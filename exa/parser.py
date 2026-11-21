"""
Recursive-descent parser for Exa v0.4.
v0.3/v0.4 features:
  - Composite multi-stage .METHOD_X definitions (.METHOD_A = (stage1) >> (stage2)/.)
  - Chaining '>>' after grouped pipelines (((SEQ) > (1, 3) >> (SUM)) >> (EMIT)/.)
  - Class.Method identifier references inside tool argument lists (e.g. chmod(..., Hero.HI))
  - String path support in Include statements (e.g. Include "libs/utils.exa"/.)
"""

from typing import Any, List, Optional, Tuple
from .ast_nodes import (
    ASTNode,
    ClassDefNode,
    DictNode,
    ExecNode,
    HymnCodeNode,
    IncludeNode,
    ListNode,
    LiteralNode,
    LoopSpec,
    MethodDefNode,
    PipelineExpr,
    ProgramNode,
    StageNode,
    Token,
    VarNode,
)
from .errors import ParserError


class Parser:
    """Builds an Exa AST from tokenized source code."""

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def _current(self) -> Token:
        return self.tokens[self.pos]

    def _peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else self.tokens[-1]

    def _match(self, kind: str, value: Any = None) -> bool:
        tok = self._current()
        if tok.kind != kind:
            return False
        if value is not None and tok.value != value:
            return False
        return True

    def _consume(self, kind: str, value: Any = None, err_msg: Optional[str] = None) -> Token:
        tok = self._current()
        if not self._match(kind, value):
            expected = f"{kind}({value})" if value is not None else kind
            msg = err_msg or f"Expected {expected}, got {tok.kind}({tok.value!r})"
            raise ParserError(f"[Line {tok.line}, Col {tok.col}] {msg}")
        self.pos += 1
        return tok

    def parse(self) -> ProgramNode:
        statements: List[ASTNode] = []
        while not self._match("EOF"):
            statements.append(self._parse_statement())
        return ProgramNode(statements=statements, line=1)

    def _parse_statement(self) -> ASTNode:
        tok = self._current()

        if self._match("HASH"):
            stmt = self._parse_hymn_code()
            self._consume("TERM", "/.", "Missing statement terminator '/.' after #HYMN_CODE")
            return stmt

        if self._match("KEYWORD", "Class"):
            return self._parse_class_def()

        if self._match("KEYWORD", "Include") or self._match("KEYWORD", "includes"):
            stmt = self._parse_include()
        elif self._match("DOT"):
            stmt = self._parse_method_def()
        elif (
            self._match("KEYWORD", "EXEC")
            or self._match("KEYWORD", "EXEC_with")
            or self._match("KEYWORD", "DIVE")
        ):
            stmt = self._parse_exec()
        else:
            raise ParserError(
                f"[Line {tok.line}, Col {tok.col}] Unexpected token {tok.value!r}. "
                f"Expected '#HYMN_CODE', 'Include', 'Class', '.METHOD...', 'EXEC', 'EXEC_with', or 'DIVE'."
            )

        self._consume("TERM", "/.", "Missing statement terminator '/.'")
        return stmt

    def _parse_hymn_code(self) -> HymnCodeNode:
        h = self._consume("HASH")
        directive = self._consume("IDENT", err_msg="Expected 'HYMN_CODE' after '#'")
        if directive.value != "HYMN_CODE":
            raise ParserError(f"[Line {directive.line}] Unknown directive '#{directive.value}'")
        self._consume("COLON", ":", "Expected ':' after '#HYMN_CODE'")
        code_tok = self._current()
        if code_tok.kind in ("IDENT", "STRING"):
            self._consume(code_tok.kind)
            raw = str(code_tok.value)
        else:
            raise ParserError(f"[Line {code_tok.line}] Expected Hymn Code identifier or string")

        parts = raw.split("_")
        user = parts[0] if len(parts) >= 1 else "GUEST"
        rank = parts[1] if len(parts) >= 2 else "USER"
        server = "_".join(parts[2:]) if len(parts) >= 3 else "LOCAL"
        return HymnCodeNode(raw_code=raw, user=user, rank=rank, server=server, line=h.line)

    def _parse_include(self) -> IncludeNode:
        if self._match("KEYWORD", "Include"):
            kw = self._consume("KEYWORD", "Include")
            tok = self._current()
            if tok.kind in ("IDENT", "STRING"):
                self._consume(tok.kind)
                mod_name = str(tok.value)
            else:
                raise ParserError(
                    f"[Line {tok.line}, Col {tok.col}] Expected module name or '.exa' path string after 'Include'"
                )
            if not mod_name.strip():
                raise ParserError(f"[Line {tok.line}, Col {tok.col}] Include target cannot be empty")
            return IncludeNode(module_name=mod_name, line=kw.line)

        kw = self._consume("KEYWORD", "includes")
        self._consume("DOT", ".", "Expected '.' after 'includes'")
        mod_tok = self._consume("IDENT", err_msg="Expected 'Ex_<module>' after 'includes.'")
        name = mod_tok.value
        if name.startswith("Ex_"):
            name = name[3:]
        return IncludeNode(module_name=name, line=kw.line)

    def _parse_method_name(self) -> Tuple[str, int]:
        dot = self._consume("DOT")
        ident = self._consume("IDENT", err_msg="Expected method identifier after '.'")
        if not (ident.value == "METHOD" or ident.value.startswith("METHOD_")):
            raise ParserError(
                f"[Line {ident.line}, Col {ident.col}] Method name must be '.METHOD' or start with '.METHOD_'"
            )
        return "." + ident.value, dot.line

    def _parse_tool_parts(self) -> List[str]:
        if self._match("KEYWORD", "DIVE"):
            parts = [self._consume("KEYWORD", "DIVE").value]
        else:
            parts = [self._consume("IDENT", err_msg="Expected tool or module name").value]
        while self._match("DOT"):
            self._consume("DOT")
            parts.append(self._consume("IDENT", err_msg="Expected function name after '.'").value)
        return parts

    def _parse_arg_list(self) -> List[ASTNode]:
        args: List[ASTNode] = []
        self._consume("LPAREN")
        if not self._match("RPAREN"):
            args.append(self._parse_expr())
            while self._match("COMMA"):
                self._consume("COMMA")
                args.append(self._parse_expr())
        self._consume("RPAREN")
        return args

    def _parse_expr(self) -> ASTNode:
        tok = self._current()
        if tok.kind in ("STRING", "NUMBER", "BOOL"):
            self._consume(tok.kind)
            return LiteralNode(value=tok.value, line=tok.line)
        if tok.kind == "VAR":
            self._consume("VAR")
            return VarNode(name=tok.value, line=tok.line)
        if tok.kind == "DOT":
            method_name, line = self._parse_method_name()
            return LiteralNode(value=method_name, line=line)
        # Support bare Class.Method references inside argument lists (e.g. chmod(..., Hero.HI))
        if tok.kind == "IDENT" and self._peek(1).kind == "DOT":
            cls_tok = self._consume("IDENT")
            self._consume("DOT")
            meth_tok = self._consume("IDENT", err_msg="Expected method name after '.'")
            return LiteralNode(value=f"{cls_tok.value}.{meth_tok.value}", line=cls_tok.line)
        if tok.kind == "LBRACK":
            self._consume("LBRACK")
            elems: List[ASTNode] = []
            if not self._match("RBRACK"):
                elems.append(self._parse_expr())
                while self._match("COMMA"):
                    self._consume("COMMA")
                    elems.append(self._parse_expr())
            self._consume("RBRACK")
            return ListNode(elements=elems, line=tok.line)
        if tok.kind == "LBRACE":
            self._consume("LBRACE")
            pairs: List[Tuple[str, ASTNode]] = []
            if not self._match("RBRACE"):
                pairs.append(self._parse_dict_pair())
                while self._match("COMMA"):
                    self._consume("COMMA")
                    pairs.append(self._parse_dict_pair())
            self._consume("RBRACE")
            return DictNode(pairs=pairs, line=tok.line)
        raise ParserError(f"[Line {tok.line}, Col {tok.col}] Invalid argument expression: {tok.value!r}")

    def _parse_dict_pair(self) -> Tuple[str, ASTNode]:
        key_tok = self._current()
        if key_tok.kind in ("STRING", "IDENT"):
            self._consume(key_tok.kind)
            key = str(key_tok.value)
        else:
            raise ParserError(f"[Line {key_tok.line}] Expected string or identifier dict key")
        self._consume("COLON", ":", "Expected ':' after dictionary key")
        val_node = self._parse_expr()
        return key, val_node

    def _parse_method_def(self) -> MethodDefNode:
        name, line = self._parse_method_name()
        self._consume("EQ", "=", "Expected '=' after method name")
        pipeline = self._parse_pipeline_expr()
        first = pipeline.stages[0]
        return MethodDefNode(
            name=name,
            tool_parts=first.inline_tool_parts or [],
            args=first.inline_args or [],
            stages=pipeline.stages,
            line=line,
        )

    def _parse_class_def(self) -> ClassDefNode:
        kw = self._consume("KEYWORD", "Class")
        self._consume("DOUBLE_COLON", "::", "Expected '::' after 'Class'")
        name_tok = self._consume("IDENT", err_msg="Expected class name after 'Class::'")

        base_class: Optional[str] = None
        if self._match("KEYWORD", "extends"):
            self._consume("KEYWORD", "extends")
            self._consume("DOT", ".", "Expected '.' after 'extends'")
            base_class = self._consume("IDENT", err_msg="Expected base class name after 'extends.'").value

        self._consume("LBRACE", "{", "Expected '{' to start Class body")
        methods: List[MethodDefNode] = []
        while not self._match("RBRACE"):
            if self._match("EOF"):
                raise ParserError(f"[Line {kw.line}] Unterminated Class::{name_tok.value} block")
            m = self._parse_method_def()
            self._consume("TERM", "/.", "Expected '/.' after method inside Class body")
            methods.append(m)
        self._consume("RBRACE", "}", "Expected '}' to close Class body")
        self._consume("SEMI", ";", "Expected ';' after Class::{...} block")

        return ClassDefNode(name=name_tok.value, base_class=base_class, methods=methods, line=kw.line)

    def _parse_loop_spec(self) -> LoopSpec:
        if self._match("KEYWORD", "REPLEKIA"):
            kw = self._consume("KEYWORD", "REPLEKIA")
            self._consume("COLON", ":", "Expected ':' followed by worker count after 'REPLEKIA'")
            count_tok = self._consume("NUMBER", err_msg="Expected integer worker count")
            if not isinstance(count_tok.value, int) or count_tok.value < 1:
                raise ParserError(f"[Line {kw.line}] REPLEKIA worker count must be >= 1")
            return LoopSpec(mode="REPLEKIA", count=count_tok.value)

        self._consume("KEYWORD", "doloop")
        self._consume("DOT", ".", "Expected '.' after 'doloop'")
        mode_tok = self._consume("IDENT", err_msg="Expected loop mode (C, UT, UTE, or WHILE)")
        mode = mode_tok.value
        if mode == "C":
            self._consume("COLON", ":", "Expected ':' followed by loop count after 'doloop.C'")
            count_tok = self._consume("NUMBER", err_msg="Expected integer loop count")
            if not isinstance(count_tok.value, int) or count_tok.value < 0:
                raise ParserError(f"[Line {count_tok.line}] Loop count must be a non-negative integer")
            return LoopSpec(mode="C", count=count_tok.value)
        if mode == "WHILE":
            self._consume("COLON", ":", "Expected ':' followed by condition string after 'doloop.WHILE'")
            cond_tok = self._consume("STRING", err_msg="Expected condition string after 'doloop.WHILE:'")
            return LoopSpec(mode="WHILE", condition=cond_tok.value)
        if mode in ("UT", "UTE"):
            return LoopSpec(mode=mode)
        raise ParserError(f"[Line {mode_tok.line}] Unknown doloop mode {mode!r}. Use C:<n>, UT, UTE, or WHILE:<cond>.")

    def _is_grouped_pipeline(self) -> bool:
        if not self._match("LPAREN"):
            return False
        depth = 0
        idx = self.pos
        while idx < len(self.tokens):
            tok = self.tokens[idx]
            if tok.kind == "LPAREN":
                depth += 1
            elif tok.kind == "RPAREN":
                depth -= 1
                if depth == 0:
                    return False
            elif tok.kind == "PIPE" and depth == 1:
                return True
            elif tok.kind in ("TERM", "EOF"):
                return False
            idx += 1
        return False

    def _parse_stage(self) -> StageNode:
        tok = self._current()

        if self._match("LPAREN"):
            self._consume("LPAREN")

            if self._match("DOT"):
                method_name, line = self._parse_method_name()
                loop = None
                if self._match("BIND"):
                    self._consume("BIND")
                    loop = self._parse_loop_spec()
                self._consume("RPAREN")
                return StageNode(method_name=method_name, loop=loop, line=line)

            if self._match("LPAREN"):
                self._consume("LPAREN")
                tool_parts = self._parse_tool_parts()
                self._consume("RPAREN")
                args: List[ASTNode] = []
                if self._match("FEED"):
                    self._consume("FEED")
                    args = self._parse_arg_list()
                loop = None
                if self._match("BIND"):
                    self._consume("BIND")
                    loop = self._parse_loop_spec()
                self._consume("RPAREN")
                return StageNode(inline_tool_parts=tool_parts, inline_args=args, loop=loop, line=tok.line)

            tool_parts = self._parse_tool_parts()
            if self._match("BIND"):
                self._consume("BIND")
                loop = self._parse_loop_spec()
                self._consume("RPAREN")
                return StageNode(inline_tool_parts=tool_parts, inline_args=[], loop=loop, line=tok.line)

            self._consume("RPAREN")
            args = []
            if self._match("FEED"):
                self._consume("FEED")
                args = self._parse_arg_list()
            loop = None
            if self._match("BIND"):
                self._consume("BIND")
                loop = self._parse_loop_spec()
            return StageNode(inline_tool_parts=tool_parts, inline_args=args, loop=loop, line=tok.line)

        if self._match("DOT"):
            method_name, line = self._parse_method_name()
            loop = None
            if self._match("BIND"):
                self._consume("BIND")
                loop = self._parse_loop_spec()
            return StageNode(method_name=method_name, loop=loop, line=line)

        if self._match("IDENT") or self._match("KEYWORD", "DIVE"):
            tool_parts = self._parse_tool_parts()
            args = []
            if self._match("LPAREN"):
                args = self._parse_arg_list()
            elif self._match("FEED"):
                self._consume("FEED")
                args = self._parse_arg_list()
            loop = None
            if self._match("BIND"):
                self._consume("BIND")
                loop = self._parse_loop_spec()
            return StageNode(inline_tool_parts=tool_parts, inline_args=args, loop=loop, line=tok.line)

        raise ParserError(f"[Line {tok.line}, Col {tok.col}] Expected method or tool stage in EXEC statement")

    def _parse_pipeline_expr(self) -> PipelineExpr:
        stages: List[StageNode] = []

        def _parse_item() -> None:
            if self._is_grouped_pipeline():
                self._consume("LPAREN")
                inner = self._parse_pipeline_expr()
                self._consume("RPAREN", ")", "Expected closing ')' for grouped pipeline")
                stages.extend(inner.stages)
            else:
                stages.append(self._parse_stage())

        _parse_item()
        while self._match("PIPE"):
            self._consume("PIPE")
            _parse_item()
        return PipelineExpr(stages=stages)

    def _parse_exec(self) -> ExecNode:
        kw = self._consume("KEYWORD")
        parallel = kw.value == "EXEC_with"
        dive_mode = kw.value == "DIVE"

        self._consume("EQ", "=", f"Expected '=' after '{kw.value}'")
        pipelines = [self._parse_pipeline_expr()]

        if parallel:
            while self._match("AMP"):
                self._consume("AMP")
                pipelines.append(self._parse_pipeline_expr())
        elif self._match("AMP"):
            tok = self._current()
            raise ParserError(f"[Line {tok.line}] '&' parallel operator requires 'EXEC_with =' instead of '{kw.value} ='")

        fallback_method: Optional[str] = None
        if self._match("KEYWORD", "over"):
            self._consume("KEYWORD", "over")
            self._consume("LPAREN", "(", "Expected '(' around fallback method after 'over'")
            fallback_method, _ = self._parse_method_name()
            self._consume("RPAREN", ")", "Expected ')' after fallback method")

        return ExecNode(
            pipelines=pipelines,
            parallel=parallel,
            dive_mode=dive_mode,
            fallback_method=fallback_method,
            line=kw.line,
        )