"""
Exa v0.4 - An Ar tonelico-inspired pipeline scripting language.
"""

from pprint import pprint
from typing import Callable, Optional

from .errors import (
    ExaRuntimeError,
    ExaError,
    LexerError,
    MethodSuspendedError,
    ParserError,
    PermissionDeniedError,
)
from .interpreter import Interpreter
from .lexer import Lexer
from .parser import Parser
from .runtime import Runtime, exa_tool


def run_exa(
    source: str,
    output_fn: Callable[[str], None] = print,
    input_fn: Callable[[str], str] = input,
    dump_ast: bool = False,
    base_dir: Optional[str] = None,
) -> Runtime:
    """Lex, parse, and execute an Exa source string."""
    tokens = Lexer(source).tokenize()
    ast_tree = Parser(tokens).parse()
    if dump_ast:
        pprint(ast_tree)
    rt = Runtime(output_fn=output_fn, input_fn=input_fn, base_dir=base_dir)
    Interpreter(rt).run(ast_tree)
    return rt


__all__ = [
    "Lexer",
    "Parser",
    "Runtime",
    "Interpreter",
    "run_exa",
    "exa_tool",
    "ExaError",
    "LexerError",
    "ParserError",
    "ExaRuntimeError",
    "PermissionDeniedError",
    "MethodSuspendedError",
]