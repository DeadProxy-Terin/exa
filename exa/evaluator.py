"""
Safe AST-based expression evaluator for CALC, FILTER, CHECK, BRANCH, and doloop.WHILE.
v0.3 additions:
  - Dict/List indexing and slicing ($IN["hp"], $IN[0], $IN[1:3])
  - Ternary conditional expressions ('HIGH' if $IN > 50 else 'LOW')
  - Safe pure helper functions (len, int, str, float, bool, abs, round, min, max)
"""

import ast
import re
from typing import Any, Dict
from .errors import ExaRuntimeError


class SafeExprEvaluator:
    """Evaluates mathematical and boolean expressions using $IN, $STEP, and $ERR."""

    BIN_OPS = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a ** b,
    }

    CMP_OPS = {
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }

    UNARY_OPS = {
        ast.UAdd: lambda a: +a,
        ast.USub: lambda a: -a,
        ast.Not: lambda a: not a,
    }

    SAFE_FUNCS = {
        "len": len,
        "int": int,
        "str": str,
        "float": float,
        "bool": bool,
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
    }

    @classmethod
    def evaluate(cls, expr: str, env: Dict[str, Any]) -> Any:
        normalized = re.sub(r"\$(IN|STEP|ERR)\b", r"__HS_\1__", expr)
        local_env = {
            "__HS_IN__": env.get("$IN"),
            "__HS_STEP__": env.get("$STEP"),
            "__HS_ERR__": env.get("$ERR"),
            "true": True,
            "false": False,
        }
        try:
            tree = ast.parse(normalized, mode="eval")
            return cls._eval_node(tree.body, local_env)
        except ExaRuntimeError:
            raise
        except Exception as exc:
            raise ExaRuntimeError(f"Evaluation error in {expr!r}: {exc}") from exc

    @classmethod
    def _eval_node(cls, node: ast.AST, env: Dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            raise ExaRuntimeError(f"Unknown variable {node.id!r} in expression")
        if isinstance(node, ast.List):
            return [cls._eval_node(el, env) for el in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(cls._eval_node(el, env) for el in node.elts)
        if isinstance(node, ast.Dict):
            return {
                cls._eval_node(k, env): cls._eval_node(v, env)
                for k, v in zip(node.keys, node.values)
                if k is not None
            }
        if isinstance(node, ast.Subscript):
            target = cls._eval_node(node.value, env)
            key = cls._eval_node(node.slice, env)
            return target[key]
        if isinstance(node, ast.Slice):
            lower = cls._eval_node(node.lower, env) if node.lower is not None else None
            upper = cls._eval_node(node.upper, env) if node.upper is not None else None
            step = cls._eval_node(node.step, env) if node.step is not None else None
            return slice(lower, upper, step)
        if isinstance(node, ast.IfExp):
            cond = cls._eval_node(node.test, env)
            return cls._eval_node(node.body if cond else node.orelse, env)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in cls.SAFE_FUNCS and not node.keywords:
                fn = cls.SAFE_FUNCS[node.func.id]
                args = [cls._eval_node(a, env) for a in node.args]
                return fn(*args)
            raise ExaRuntimeError(
                "Only safe built-in helper calls (len, int, str, float, bool, abs, round, min, max) are allowed in expressions"
            )
        if isinstance(node, ast.BinOp):
            op_fn = cls.BIN_OPS.get(type(node.op))
            if not op_fn:
                raise ExaRuntimeError(f"Unsupported binary operator: {type(node.op).__name__}")
            return op_fn(cls._eval_node(node.left, env), cls._eval_node(node.right, env))
        if isinstance(node, ast.UnaryOp):
            op_fn = cls.UNARY_OPS.get(type(node.op))
            if not op_fn:
                raise ExaRuntimeError(f"Unsupported unary operator: {type(node.op).__name__}")
            return op_fn(cls._eval_node(node.operand, env))
        if isinstance(node, ast.BoolOp):
            # Short-circuit like Python: and stops at the first false value and
            # or at the first true one, so later operands that would raise
            # (for example $IN[0] on an empty list) are never evaluated.
            if isinstance(node.op, ast.And):
                for v in node.values:
                    if not cls._eval_node(v, env):
                        return False
                return True
            for v in node.values:
                if cls._eval_node(v, env):
                    return True
            return False
        if isinstance(node, ast.Compare):
            left = cls._eval_node(node.left, env)
            for op, right_node in zip(node.ops, node.comparators):
                cmp_fn = cls.CMP_OPS.get(type(op))
                if not cmp_fn:
                    raise ExaRuntimeError(f"Unsupported comparison operator: {type(op).__name__}")
                right = cls._eval_node(right_node, env)
                if not cmp_fn(left, right):
                    return False
                left = right
            return True
        raise ExaRuntimeError(f"Unsupported expression syntax: {type(node).__name__}")