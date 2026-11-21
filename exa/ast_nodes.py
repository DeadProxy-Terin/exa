"""
Token and Abstract Syntax Tree (AST) node definitions for Exa v0.3.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class Token:
    kind: str
    value: Any
    line: int
    col: int


@dataclass
class ASTNode:
    line: int = 0


@dataclass
class LiteralNode(ASTNode):
    value: Any = None


@dataclass
class ListNode(ASTNode):
    elements: List[ASTNode] = field(default_factory=list)


@dataclass
class DictNode(ASTNode):
    pairs: List[tuple] = field(default_factory=list)  # List[(str, ASTNode)]


@dataclass
class VarNode(ASTNode):
    name: str = ""


@dataclass
class HymnCodeNode(ASTNode):
    """Represents #HYMN_CODE: USER_RANK_SERVER/. script header."""
    raw_code: str = ""
    user: str = "GUEST"
    rank: str = "USER"      # e.g. "USER" or "ANSUL" (Administrator)
    server: str = "LOCAL"


@dataclass
class IncludeNode(ASTNode):
    """Represents Include <module>/. or includes.Ex_<module>/."""
    module_name: str = ""


@dataclass
class LoopSpec:
    """Loop or concurrency controller attached via '<<'."""
    mode: str                 # "C", "UT", "UTE", "WHILE", "REPLEKIA"
    count: Optional[int] = None
    condition: Optional[str] = None


@dataclass
class StageNode(ASTNode):
    """A single stage inside an EXEC or composite .METHOD pipeline."""
    method_name: Optional[str] = None
    inline_tool_parts: Optional[List[str]] = None
    inline_args: Optional[List[ASTNode]] = None
    loop: Optional[LoopSpec] = None


@dataclass
class MethodDefNode(ASTNode):
    """
    Represents .METHOD_NAME = (tool) > (args)/.
    or a composite pipeline .METHOD_NAME = (stage1) >> (stage2)/.
    """
    name: str = ""
    tool_parts: List[str] = field(default_factory=list)
    args: List[ASTNode] = field(default_factory=list)
    stages: List[StageNode] = field(default_factory=list)


@dataclass
class ClassDefNode(ASTNode):
    """Represents Ar nosurge-inspired Class::NAME [extends.BASE] { ... };"""
    name: str = ""
    base_class: Optional[str] = None
    methods: List[MethodDefNode] = field(default_factory=list)


@dataclass
class PipelineExpr:
    """A sequence of connected stages (Stage1 >> Stage2 >> ...)."""
    stages: List[StageNode] = field(default_factory=list)


@dataclass
class ExecNode(ASTNode):
    """
    Represents:
      - EXEC = <pipeline> [over (.FALLBACK)]/.
      - EXEC_with = (<pipeline1>) & (<pipeline2>) [over (.FALLBACK)]/.
      - DIVE = <pipeline>/.   (step-by-step sandbox trace mode)
    """
    pipelines: List[PipelineExpr] = field(default_factory=list)
    parallel: bool = False          # True for EXEC_with
    dive_mode: bool = False         # True for DIVE = .../.
    fallback_method: Optional[str] = None


@dataclass
class ProgramNode(ASTNode):
    statements: List[ASTNode] = field(default_factory=list)