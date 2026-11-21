"""
Lexical analyzer (Tokenizer) for Exa v0.3.
Supports hyphens in #HYMN_CODE values (e.g. SHURELIA_USER_TOWER-01).
"""

from typing import List
from .ast_nodes import Token
from .errors import LexerError


class Lexer:
    KEYWORDS = {
        "Include",
        "includes",
        "EXEC",
        "EXEC_with",
        "DIVE",
        "doloop",
        "REPLEKIA",
        "over",
        "Class",
        "extends",
    }

    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.length = len(source)

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        return self.source[idx] if idx < self.length else ""

    def _advance(self, count: int = 1) -> str:
        chunk = self.source[self.pos : self.pos + count]
        for ch in chunk:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.pos += count
        return chunk

    def _skip_ws(self) -> None:
        while self.pos < self.length and self._peek().isspace():
            self._advance()

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []

        while self.pos < self.length:
            ch = self._peek()

            if ch.isspace():
                self._advance()
                continue

            # Single-line comments: // ...
            if ch == "/" and self._peek(1) == "/":
                while self.pos < self.length and self._peek() != "\n":
                    self._advance()
                continue

            # Block comments: /* ... */
            if ch == "/" and self._peek(1) == "*":
                sl, sc = self.line, self.col
                self._advance(2)
                closed = False
                while self.pos < self.length:
                    if self._peek() == "*" and self._peek(1) == "/":
                        self._advance(2)
                        closed = True
                        break
                    self._advance()
                if not closed:
                    raise LexerError(f"Unterminated block comment at line {sl}, col {sc}")
                continue

            sl, sc = self.line, self.col

            # Special directive header: #HYMN_CODE: <code-with-optional-hyphens>/.
            if ch == "#" and self.source.startswith("#HYMN_CODE", self.pos):
                tokens.append(Token("HASH", "#", sl, sc))
                self._advance()
                tokens.append(Token("IDENT", "HYMN_CODE", self.line, self.col))
                self._advance(9)
                self._skip_ws()
                if self._peek() == ":":
                    tokens.append(Token("COLON", ":", self.line, self.col))
                    self._advance()
                    self._skip_ws()
                    if self._peek() not in ('"', "'"):
                        val_line, val_col = self.line, self.col
                        raw_buf: List[str] = []
                        while self.pos < self.length:
                            if self._peek() == "/" and self._peek(1) == ".":
                                break
                            if self._peek() == "\n":
                                break
                            raw_buf.append(self._advance())
                        raw_val = "".join(raw_buf).strip()
                        if raw_val:
                            tokens.append(Token("STRING", raw_val, val_line, val_col))
                continue

            # Statement terminator: /.
            if ch == "/" and self._peek(1) == ".":
                self._advance(2)
                tokens.append(Token("TERM", "/.", sl, sc))
                continue

            # Double-character operators
            if ch == ">" and self._peek(1) == ">":
                self._advance(2)
                tokens.append(Token("PIPE", ">>", sl, sc))
                continue

            if ch == "<" and self._peek(1) == "<":
                self._advance(2)
                tokens.append(Token("BIND", "<<", sl, sc))
                continue

            if ch == ":" and self._peek(1) == ":":
                self._advance(2)
                tokens.append(Token("DOUBLE_COLON", "::", sl, sc))
                continue

            # Compound built-in: RE=NATION
            if self.source.startswith("RE=NATION", self.pos):
                next_ch = self._peek(9)
                if not (next_ch.isalnum() or next_ch == "_"):
                    self._advance(9)
                    tokens.append(Token("IDENT", "RE=NATION", sl, sc))
                    continue

            simple_tokens = {
                ">": "FEED",
                "=": "EQ",
                "&": "AMP",
                "#": "HASH",
                "(": "LPAREN",
                ")": "RPAREN",
                "[": "LBRACK",
                "]": "RBRACK",
                "{": "LBRACE",
                "}": "RBRACE",
                ",": "COMMA",
                ":": "COLON",
                ";": "SEMI",
                ".": "DOT",
            }
            if ch in simple_tokens:
                self._advance()
                tokens.append(Token(simple_tokens[ch], ch, sl, sc))
                continue

            # String literals
            if ch in ('"', "'"):
                quote = self._advance()
                buf: List[str] = []
                closed = False
                while self.pos < self.length:
                    cur = self._peek()
                    if cur == "\\":
                        self._advance()
                        esc = self._advance()
                        escape_map = {"n": "\n", "t": "\t", "\\": "\\", '"': '"', "'": "'"}
                        buf.append(escape_map.get(esc, esc))
                    elif cur == quote:
                        self._advance()
                        closed = True
                        break
                    else:
                        buf.append(self._advance())
                if not closed:
                    raise LexerError(f"Unterminated string literal at line {sl}, col {sc}")
                tokens.append(Token("STRING", "".join(buf), sl, sc))
                continue

            # Numbers (int and float)
            if ch.isdigit() or (ch == "-" and self._peek(1).isdigit()):
                num_buf = [self._advance()]
                has_dot = False
                while self.pos < self.length:
                    cur = self._peek()
                    if cur.isdigit():
                        num_buf.append(self._advance())
                    elif cur == "." and not has_dot and self._peek(1).isdigit():
                        has_dot = True
                        num_buf.append(self._advance())
                    else:
                        break
                raw = "".join(num_buf)
                val = float(raw) if has_dot else int(raw)
                tokens.append(Token("NUMBER", val, sl, sc))
                continue

            # Runtime variables: $IN, $STEP, $ERR
            if ch == "$":
                self._advance()
                ident_buf: List[str] = []
                while self.pos < self.length and (self._peek().isalnum() or self._peek() == "_"):
                    ident_buf.append(self._advance())
                if not ident_buf:
                    raise LexerError(f"Unexpected '$' at line {sl}, col {sc}")
                tokens.append(Token("VAR", "$" + "".join(ident_buf), sl, sc))
                continue

            # Identifiers, booleans, and keywords
            if ch.isalpha() or ch == "_":
                ident_buf = []
                while self.pos < self.length and (self._peek().isalnum() or self._peek() == "_"):
                    ident_buf.append(self._advance())
                word = "".join(ident_buf)
                if word in self.KEYWORDS:
                    tokens.append(Token("KEYWORD", word, sl, sc))
                elif word in ("true", "false"):
                    tokens.append(Token("BOOL", word == "true", sl, sc))
                else:
                    tokens.append(Token("IDENT", word, sl, sc))
                continue

            raise LexerError(f"Unexpected character {ch!r} at line {sl}, col {sc}")

        tokens.append(Token("EOF", None, self.line, self.col))
        return tokens