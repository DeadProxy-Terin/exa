"""
Runtime environment, Core Built-ins (including STORE/RECALL/BRANCH),
and Standard Library Modules ('math', 'console', 'sys', 'net', 'time') for Exa v0.4.
"""

import base64
from datetime import datetime
import hashlib
import importlib.util
import json
import math
import os
import platform
import random
import re
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple
import urllib.request

from .ast_nodes import (
    ASTNode,
    ClassDefNode,
    DictNode,
    HymnCodeNode,
    ListNode,
    LiteralNode,
    LoopSpec,
    MethodDefNode,
    StageNode,
    VarNode,
)
from .errors import (
    ExaRuntimeError,
    MethodSuspendedError,
    PermissionDeniedError,
    SkipItem,
)
from .evaluator import SafeExprEvaluator


LORE_DOCS: Dict[str, str] = {
    "FLIP": "FLIP(val, target) - Convert values (int, float, str, bool, bin, hex, oct, list, dict, json, reverse).",
    "CODEC": "CODEC(val, mode) - Encode/decode (to_ascii, from_ascii, to_bytes, from_bytes, to_base64, from_base64).",
    "FORMAT": "FORMAT(val_or_fmt, *args) - String/number formatting (upper, lower, trim, .2f, or '{}'.format).",
    "EMIT": "EMIT(*args) - Print values to output (acts as tee-style pass-through in pipelines).",
    "LISTEN": "LISTEN(prompt) - Read a line of user input from stdin.",
    "STREAM": "STREAM(path, mode, content) - Read/write files lazily (read, read_all, write, append).",
    "CALC": "CALC(expr) - Safely evaluate math/boolean expressions using $IN, $STEP, $ERR.",
    "ABS": "ABS(val) - Absolute value.",
    "ROUND": "ROUND(val, digits=0) - Round floating-point numbers.",
    "SUM": "SUM(items) - Sum a list or incoming pipeline stream.",
    "MIN": "MIN(items) - Minimum value in a list or stream.",
    "MAX": "MAX(items) - Maximum value in a list or stream.",
    "SEQ": "SEQ(start, end, step=1) - Lazy sequence generator.",
    "LEN": "LEN(items) - Length of string, list, dict, or stream.",
    "FILTER": "FILTER(condition) - Filter stream items matching condition expression.",
    "SORT": "SORT(items, direction='asc') - Sort items ascending or descending.",
    "SLICE": "SLICE(items, start, end) - Slice lists, strings, or streams.",
    "CHECK": "CHECK(items, mode='all', expr=None) - Check 'all' or 'any' truthiness.",
    "GRATHMELD": "GRATHMELD(a, b) - Synthesize/merge dicts, zip/concat lists, or combine values.",
    "TYPE": "TYPE(val) - Inspect runtime data type.",
    "HASH": "HASH(val, algo='sha256') - Compute cryptographic hash (sha256, md5, sha1).",
    "DIVE": "DIVE(target) - Inspect method internals/plugins or run pipelines in step-by-step trace mode.",
    "INSTALL": "INSTALL(method, config_dict) - Attach middleware plugins (retry, delay, prefix) to a method.",
    "LORE": "LORE(topic) - Display built-in documentation.",
    "chmod": "chmod(binary_perm, method) - Set 9-bit binary permissions like 'b111000000'.",
    "SUSPEND": "SUSPEND(seconds) or SUSPEND(method) - Sleep or pause a method until RE=NATION.",
    "RE=NATION": "RE=NATION(method) - Wake up a suspended method.",
    "HIBERNATION": "HIBERNATION('save'|'load', path) - Snapshot/restore full runtime state (including Class::) to disk.",
    "PURGER": "PURGER(*methods) - Purge methods, permissions, and plugins from memory.",
    "STORE": "STORE(key, val) - Store a value into persistent runtime memory_vars (pass-through).",
    "RECALL": "RECALL(key, default=None) - Retrieve a stored value from runtime memory_vars.",
    "BRANCH": "BRANCH(cond, true_val, false_val) - Conditional branching expression/routing inside pipelines.",
    "REPLEKIA": "EXEC = (.METHOD << REPLEKIA:N)/. - Execute across N parallel worker threads.",
    "doloop": "doloop.C:<n>, doloop.UT, doloop.UTE, doloop.WHILE:<cond> - Loop controllers.",
    "with/over": "EXEC_with = (A) & (B)/. for parallel execution; '... over (.FALLBACK)/.' for error hijacking.",
}


def _ast_to_json(node: ASTNode) -> Dict[str, Any]:
    if isinstance(node, LiteralNode):
        return {"kind": "Literal", "value": node.value, "line": node.line}
    if isinstance(node, VarNode):
        return {"kind": "Var", "name": node.name, "line": node.line}
    if isinstance(node, ListNode):
        return {"kind": "List", "elements": [_ast_to_json(e) for e in node.elements], "line": node.line}
    if isinstance(node, DictNode):
        return {"kind": "Dict", "pairs": [[k, _ast_to_json(v)] for k, v in node.pairs], "line": node.line}
    raise ExaRuntimeError(f"Cannot serialize AST node: {type(node).__name__}")


def _json_to_ast(data: Dict[str, Any]) -> ASTNode:
    k = data["kind"]
    ln = data.get("line", 0)
    if k == "Literal":
        return LiteralNode(value=data["value"], line=ln)
    if k == "Var":
        return VarNode(name=data["name"], line=ln)
    if k == "List":
        return ListNode(elements=[_json_to_ast(e) for e in data["elements"]], line=ln)
    if k == "Dict":
        return DictNode(pairs=[(p[0], _json_to_ast(p[1])) for p in data["pairs"]], line=ln)
    raise ExaRuntimeError(f"Unknown serialized AST node kind: {k!r}")


def _stage_to_json(st: StageNode) -> Dict[str, Any]:
    return {
        "method_name": st.method_name,
        "inline_tool_parts": st.inline_tool_parts,
        "inline_args": [_ast_to_json(a) for a in st.inline_args] if st.inline_args is not None else None,
        "loop": {
            "mode": st.loop.mode,
            "count": st.loop.count,
            "condition": st.loop.condition,
        } if st.loop else None,
        "line": st.line,
    }


def _json_to_stage(d: Dict[str, Any]) -> StageNode:
    lp = d.get("loop")
    loop = LoopSpec(mode=lp["mode"], count=lp.get("count"), condition=lp.get("condition")) if lp else None
    ia = [_json_to_ast(a) for a in d["inline_args"]] if d.get("inline_args") is not None else None
    return StageNode(
        method_name=d.get("method_name"),
        inline_tool_parts=d.get("inline_tool_parts"),
        inline_args=ia,
        loop=loop,
        line=d.get("line", 0),
    )


def _method_to_json(meth: MethodDefNode) -> Dict[str, Any]:
    return {
        "name": meth.name,
        "tool_parts": meth.tool_parts,
        "args": [_ast_to_json(a) for a in meth.args],
        "stages": [_stage_to_json(s) for s in meth.stages],
        "line": meth.line,
    }


def _json_to_method(mdata: Dict[str, Any]) -> MethodDefNode:
    return MethodDefNode(
        name=mdata["name"],
        tool_parts=mdata.get("tool_parts", []),
        args=[_json_to_ast(a) for a in mdata.get("args", [])],
        stages=[_json_to_stage(s) for s in mdata.get("stages", [])],
        line=mdata.get("line", 0),
    )


def exa_tool(
    *,
    aggregator: bool = False,
    prepend_in: bool = False,
    passthrough: bool = False,
    needs_env: bool = False,
    doc: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Optional decorator for plugin functions to declare pipeline behavior and LORE docs:
      - aggregator=True  : Collects upstream stream into a list before invoking
      - prepend_in=True  : Auto-prepends $IN when called in a pipeline with option args
      - passthrough=True : Tee-style output; passes upstream $IN down the pipeline
      - needs_env=True   : Receives '_env' dict containing $IN, $STEP, $ERR
      - doc="..."        : Registers built-in documentation for LORE(...)
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, "_exa_meta", {
            "aggregator": aggregator,
            "prepend_in": prepend_in,
            "passthrough": passthrough,
            "needs_env": needs_env,
            "doc": doc,
        })
        return fn
    return decorator


class Runtime:
    """
    Thread-safe execution environment holding methods, classes, permissions,
    middleware plugins, persistent memory_vars, built-ins, and Standard Library modules.
    """

    STREAM_AGGREGATORS = {"SUM", "MIN", "MAX", "LEN", "SORT", "CHECK", "SLICE"}
    AUTO_PREPEND_IN_TOOLS = {"FLIP", "CODEC", "HASH", "ROUND", "FORMAT", "GRATHMELD"}
    DEFAULT_OUTPUT_PASSTHROUGH_TOOLS = {
        ("EMIT",),
        ("console", "log"),
        ("console", "warn"),
        ("console", "error"),
    }
    DEFAULT_ENV_TOOLS = {("CALC",), ("math", "count"), ("FILTER",)}

    def __init__(
        self,
        output_fn: Callable[[str], None] = print,
        input_fn: Callable[[str], str] = input,
        base_dir: Optional[str] = None,
    ):
        self.output_fn = output_fn
        self.input_fn = input_fn
        self.base_dir: str = os.path.abspath(base_dir) if base_dir else os.getcwd()
        self._io_lock = threading.Lock()
        self._state_lock = threading.RLock()

        self.hymn_code: HymnCodeNode = HymnCodeNode()
        self.included_modules: set = set()
        self.loaded_files: set = set()
        self._include_dir_stack: List[str] = []
        self.extra_search_paths: List[str] = []

        # Instance-level extensible capability registries (configurable by plugins)
        self.stream_aggregators: set = {(name,) for name in self.STREAM_AGGREGATORS}
        self.auto_prepend_in_tools: set = {(name,) for name in self.AUTO_PREPEND_IN_TOOLS}
        self.custom_prepend_in_tools: set = set()
        self.passthrough_tools: set = set(self.DEFAULT_OUTPUT_PASSTHROUGH_TOOLS)
        self.env_tools: set = set(self.DEFAULT_ENV_TOOLS)
        self.lore_docs: Dict[str, str] = dict(LORE_DOCS)
        self.methods: Dict[str, MethodDefNode] = {}
        self.classes: Dict[str, ClassDefNode] = {}
        self.permissions: Dict[str, str] = {}
        self.suspended: set = set()
        self.plugins: Dict[str, Dict[str, Any]] = {}
        self.memory_vars: Dict[str, Any] = {}

        self.builtins: Dict[str, Callable[..., Any]] = {
            # Group 1: Conversion & Formatting
            "FLIP": self._tool_flip,
            "CODEC": self._tool_codec,
            "FORMAT": self._tool_format,
            # Group 2: Basic I/O
            "EMIT": self._tool_emit,
            "LISTEN": self._tool_listen,
            "STREAM": self._tool_stream,
            # Group 3: Math & Sequences
            "CALC": self._tool_calc,
            "ABS": self._tool_abs,
            "ROUND": self._tool_round,
            "SUM": self._tool_sum,
            "MIN": self._tool_min,
            "MAX": self._tool_max,
            "SEQ": self._tool_seq,
            # Group 4: Streams & Collections
            "LEN": self._tool_len,
            "FILTER": self._tool_filter,
            "SORT": self._tool_sort,
            "SLICE": self._tool_slice,
            "CHECK": self._tool_check,
            "GRATHMELD": self._tool_grathmeld,
            # Group 5: Inspection, Security & Meta
            "TYPE": self._tool_type,
            "HASH": self._tool_hash,
            "DIVE": self._tool_dive,
            "INSTALL": self._tool_install,
            "LORE": self._tool_lore,
            "chmod": self._tool_chmod,
            # Group 6: Lifecycle, Memory & Routing
            "SUSPEND": self._tool_suspend,
            "RE=NATION": self._tool_renation,
            "HIBERNATION": self._tool_hibernation,
            "PURGER": self._tool_purger,
            "STORE": self._tool_store,
            "RECALL": self._tool_recall,
            "BRANCH": self._tool_branch,
        }

        self.std_modules: Dict[str, Dict[str, Callable[..., Any]]] = {
            "math": {
                "count": self._tool_calc,
                "sqrt": lambda x: math.sqrt(float(x)),
                "pow": lambda a, b: math.pow(float(a), float(b)),
                "sin": lambda x: math.sin(float(x)),
                "cos": lambda x: math.cos(float(x)),
                "pi": lambda: math.pi,
                "random": lambda a=0, b=1: random.randint(int(a), int(b)),
            },
            "console": {
                "log": self._tool_emit,
                "warn": lambda *args: self._tool_emit("[WARN]", *args),
                "error": lambda *args: self._tool_emit("[ERROR]", *args),
                "clear": lambda: self._emit_raw("\033[2J\033[H"),
            },
            "sys": {
                "chmod": self._tool_chmod,
                "info": lambda: {
                    "os": platform.system(),
                    "release": platform.release(),
                    "python": platform.python_version(),
                    "user": self.hymn_code.user,
                    "rank": self.hymn_code.rank,
                    "server": self.hymn_code.server,
                },
                "hymn_code": lambda: {
                    "raw": self.hymn_code.raw_code,
                    "user": self.hymn_code.user,
                    "rank": self.hymn_code.rank,
                    "server": self.hymn_code.server,
                },
                "env": lambda key, default="": os.environ.get(str(key), default),
                "sleep": lambda secs: self._tool_suspend(float(secs)),
            },
            "net": {
                "request_access": self._net_request_access,
                "ping": self._net_ping,
                "http_get": self._net_http_get,
            },
            "time": {
                "now": lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": lambda: time.time(),
                "sleep": lambda secs: self._tool_suspend(float(secs)),
            },
        }

    # --- Thread-safe State & Class Helpers ---

    def _emit_raw(self, text: str) -> None:
        with self._io_lock:
            self.output_fn(text)

    @staticmethod
    def format_val(val: Any) -> str:
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        return str(val)

    def add_search_path(self, path: str) -> None:
        """Adds an extra search directory for local '.exa' libraries and 'plugins/*.py'."""
        if path and isinstance(path, str):
            abs_p = os.path.abspath(path)
            with self._state_lock:
                if abs_p not in self.extra_search_paths:
                    self.extra_search_paths.append(abs_p)

    def _apply_tool_metadata(
        self,
        tool_key: Tuple[str, ...],
        lore_key: str,
        fn: Callable[..., Any],
        aggregator: Optional[bool] = None,
        prepend_in: Optional[bool] = None,
        passthrough: Optional[bool] = None,
        needs_env: Optional[bool] = None,
        doc: Optional[str] = None,
    ) -> None:
        meta = getattr(fn, "_exa_meta", {}) or {}
        is_agg = meta.get("aggregator", False) if aggregator is None else aggregator
        is_prep = meta.get("prepend_in", False) if prepend_in is None else prepend_in
        is_pass = meta.get("passthrough", False) if passthrough is None else passthrough
        is_env = meta.get("needs_env", False) if needs_env is None else needs_env
        doc_str = doc if doc is not None else meta.get("doc")

        if is_agg:
            self.stream_aggregators.add(tool_key)
        if is_prep:
            self.custom_prepend_in_tools.add(tool_key)
        if is_pass:
            self.passthrough_tools.add(tool_key)
        if is_env:
            self.env_tools.add(tool_key)
        if doc_str:
            self.lore_docs[lore_key] = str(doc_str)

    def register_tool(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        aggregator: Optional[bool] = None,
        prepend_in: Optional[bool] = None,
        passthrough: Optional[bool] = None,
        needs_env: Optional[bool] = None,
        doc: Optional[str] = None,
    ) -> None:
        """
        Registers a custom built-in tool callable into this Runtime.
        Optional flags allow plugins to match full built-in pipeline semantics
        (stream aggregation, auto-$IN prepending, tee passthrough, _env access, LORE docs).
        """
        if not isinstance(name, str) or not name.strip():
            raise ExaRuntimeError("Tool name must be a non-empty string")
        if not callable(fn):
            raise ExaRuntimeError(f"Tool '{name}' must be callable")
        key_name = name.strip()
        with self._state_lock:
            self.builtins[key_name] = fn
            self._apply_tool_metadata(
                (key_name,),
                key_name,
                fn,
                aggregator=aggregator,
                prepend_in=prepend_in,
                passthrough=passthrough,
                needs_env=needs_env,
                doc=doc,
            )

    def register_module(
        self,
        module_name: str,
        functions_dict: Dict[str, Callable[..., Any]],
        auto_include: bool = False,
        docs: Optional[Dict[str, str]] = None,
    ) -> None:
        """Registers a dictionary of functions as an Exa module."""
        if not isinstance(module_name, str) or not module_name.strip():
            raise ExaRuntimeError("Module name must be a non-empty string")
        if not isinstance(functions_dict, dict):
            raise ExaRuntimeError(f"Module '{module_name}' functions must be provided as a dict")
        clean_mod: Dict[str, Callable[..., Any]] = {}
        for fn_name, fn in functions_dict.items():
            if not isinstance(fn_name, str) or not fn_name.strip():
                raise ExaRuntimeError(f"Invalid function name {fn_name!r} in module '{module_name}'")
            if not callable(fn):
                raise ExaRuntimeError(f"Member '{module_name}.{fn_name}' must be callable")
            clean_mod[fn_name.strip()] = fn
        mod_key = module_name.strip()
        doc_map = docs or {}
        with self._state_lock:
            existing = self.std_modules.setdefault(mod_key, {})
            existing.update(clean_mod)
            for fn_name, fn in clean_mod.items():
                self._apply_tool_metadata(
                    (mod_key, fn_name),
                    f"{mod_key}.{fn_name}",
                    fn,
                    doc=doc_map.get(fn_name),
                )
            if auto_include:
                self.included_modules.add(mod_key)

    def _search_dirs(self) -> List[str]:
        dirs: List[str] = []
        if self._include_dir_stack:
            dirs.append(self._include_dir_stack[-1])
        dirs.append(self.base_dir)
        dirs.append(os.getcwd())
        with self._state_lock:
            dirs.extend(self.extra_search_paths)
        env_paths = os.environ.get("EXA_PATH", "")
        if env_paths:
            for p in env_paths.split(os.pathsep):
                p_clean = p.strip()
                if p_clean:
                    dirs.append(p_clean)
        seen = set()
        unique: List[str] = []
        for d in dirs:
            norm = os.path.normcase(os.path.abspath(d))
            if norm not in seen:
                seen.add(norm)
                unique.append(os.path.abspath(d))
        return unique

    def _resolve_exa_library(self, target: str) -> Optional[str]:
        if target.lower().endswith(".py"):
            return None
        has_path_sep = ("/" in target) or ("\\" in target) or target.lower().endswith(".exa")
        if os.path.isabs(target):
            candidates = [target]
            if not target.lower().endswith(".exa"):
                candidates.append(target + ".exa")
            for cand in candidates:
                if os.path.isfile(cand):
                    return cand
            return None

        for base in self._search_dirs():
            if has_path_sep:
                cand = os.path.join(base, target)
                if os.path.isfile(cand):
                    return cand
                if not target.lower().endswith(".exa") and os.path.isfile(cand + ".exa"):
                    return cand + ".exa"
            else:
                for rel in (f"{target}.exa", os.path.join("libs", f"{target}.exa")):
                    cand = os.path.join(base, rel)
                    if os.path.isfile(cand):
                        return cand
        return None

    def _resolve_python_plugin(self, target: str) -> Optional[Tuple[str, str]]:
        if target.lower().endswith(".exa"):
            return None
        if target.lower().endswith(".py"):
            plugin_name = os.path.splitext(os.path.basename(target))[0]
            if os.path.isabs(target):
                return (plugin_name, target) if os.path.isfile(target) else None
            for base in self._search_dirs():
                cand = os.path.join(base, target)
                if os.path.isfile(cand):
                    return plugin_name, cand
            return None
        if "/" in target or "\\" in target:
            return None
        for base in self._search_dirs():
            cand = os.path.join(base, "plugins", f"{target}.py")
            if os.path.isfile(cand):
                return target, cand
        return None

    def _load_exa_library(self, file_path: str) -> None:
        canon = os.path.normcase(os.path.realpath(os.path.abspath(file_path)))
        with self._state_lock:
            if canon in self.loaded_files:
                return
            self.loaded_files.add(canon)

        from .lexer import Lexer
        from .parser import Parser
        from .interpreter import Interpreter

        prev_hymn = self.hymn_code
        pushed_dir = False
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tokens = Lexer(source).tokenize()
            ast_tree = Parser(tokens).parse()
            self._include_dir_stack.append(os.path.dirname(os.path.abspath(file_path)))
            pushed_dir = True
            Interpreter(self).run(ast_tree)
        except Exception as exc:
            with self._state_lock:
                self.loaded_files.discard(canon)
            if isinstance(exc, ExaRuntimeError):
                raise
            raise ExaRuntimeError(f"Failed to load Exa library '{file_path}': {exc}") from exc
        finally:
            if pushed_dir:
                self._include_dir_stack.pop()
            if prev_hymn.raw_code:
                with self._state_lock:
                    self.hymn_code = prev_hymn

    def _load_python_plugin(self, plugin_name: str, file_path: str) -> None:
        canon = os.path.normcase(os.path.realpath(os.path.abspath(file_path)))
        with self._state_lock:
            if canon in self.loaded_files:
                if plugin_name in self.std_modules:
                    self.included_modules.add(plugin_name)
                return
            self.loaded_files.add(canon)

        try:
            safe_ident = re.sub(r"[^0-9A-Za-z_]", "_", plugin_name)
            spec = importlib.util.spec_from_file_location(f"exa_plugin_{safe_ident}", file_path)
            if spec is None or spec.loader is None:
                raise ExaRuntimeError(f"Could not create module spec for plugin '{file_path}'")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            loaded_anything = False
            if hasattr(module, "EXA_MODULE"):
                mod_dict = getattr(module, "EXA_MODULE")
                mod_docs = getattr(module, "EXA_DOCS", None)
                self.register_module(plugin_name, mod_dict, auto_include=True, docs=mod_docs)
                loaded_anything = True

            if hasattr(module, "register") and callable(module.register):
                with self._state_lock:
                    before_mods = set(self.std_modules.keys())
                module.register(self)
                with self._state_lock:
                    after_mods = set(self.std_modules.keys())
                    for new_mod in (after_mods - before_mods):
                        self.included_modules.add(new_mod)
                    if plugin_name in self.std_modules:
                        self.included_modules.add(plugin_name)
                loaded_anything = True

            if not loaded_anything:
                raise ExaRuntimeError(
                    f"Plugin '{file_path}' must define an 'EXA_MODULE' dict or 'register(runtime)' function"
                )
        except Exception as exc:
            with self._state_lock:
                self.loaded_files.discard(canon)
            if isinstance(exc, ExaRuntimeError):
                raise
            raise ExaRuntimeError(f"Failed to load Python plugin '{file_path}': {exc}") from exc

    def include_module(self, module_name: str) -> None:
        # 1. Built-in or programmatically registered module
        with self._state_lock:
            if module_name in self.std_modules:
                self.included_modules.add(module_name)
                return

        # 2. Pure Exa library (.exa)
        exa_path = self._resolve_exa_library(module_name)
        if exa_path is not None:
            self._load_exa_library(exa_path)
            return

        # 3. Native Python plugin (plugins/<name>.py or explicit .py path)
        py_match = self._resolve_python_plugin(module_name)
        if py_match is not None:
            plugin_name, py_path = py_match
            self._load_python_plugin(plugin_name, py_path)
            return

        available = ", ".join(sorted(self.std_modules.keys()))
        raise ExaRuntimeError(
            f"Module or library '{module_name}' not found (checked std modules [{available}], "
            f"local '.exa' files, libs/*.exa, and plugins/*.py)."
        )

    def _canonical_target(self, target_name: str) -> str:
        """
        Resolves either a standard method ('.METHOD_FOO') or a class method
        ('Hero.HI' / 'Hero.METHOD_HI') into its canonical state key, raising if undefined.
        """
        with self._state_lock:
            if target_name in self.methods:
                return target_name
            if "." in target_name and not target_name.startswith("."):
                cls_name, meth_short = target_name.split(".", 1)
                resolved = self.resolve_class_method_with_owner(cls_name, meth_short)
                if resolved is not None:
                    _, cls_meth = resolved
                    return f"{cls_name}::{cls_meth.name}"
        raise ExaRuntimeError(f"Undefined method or class method '{target_name}'")

    def check_method_executable(self, method_key: str) -> None:
        with self._state_lock:
            if method_key in self.suspended:
                raise MethodSuspendedError(
                    f"Method '{method_key}' is currently SUSPENDED. Wake it with RE=NATION({method_key})."
                )
            perm = self.permissions.get(method_key)
            rank = self.hymn_code.rank.upper()

        if perm is not None:
            if perm == "000000000":
                raise PermissionDeniedError(
                    f"Permission denied on '{method_key}' (chmod b000000000: locked for all)."
                )
            owner_exec = perm[2] == "1"
            is_admin = rank == "ANSUL"
            if not owner_exec and not is_admin:
                raise PermissionDeniedError(
                    f"Permission denied on '{method_key}' (chmod b{perm}). Requires ANSUL rank or execute bit."
                )

    def resolve_class_method_with_owner(
        self, class_name: str, method_short: str
    ) -> Optional[Tuple[str, MethodDefNode]]:
        with self._state_lock:
            cur_name: Optional[str] = class_name
            visited = set()
            while cur_name and cur_name in self.classes and cur_name not in visited:
                visited.add(cur_name)
                cls = self.classes[cur_name]
                target_full = method_short if method_short.startswith(".METHOD") else f".METHOD_{method_short}"
                for m in cls.methods:
                    if m.name == target_full or m.name == f".{method_short}":
                        return cur_name, m
                cur_name = cls.base_class
        return None

    def resolve_class_method(self, class_name: str, method_short: str) -> Optional[MethodDefNode]:
        res = self.resolve_class_method_with_owner(class_name, method_short)
        return res[1] if res is not None else None

    def resolve_tool(self, parts: List[str]) -> Callable[..., Any]:
        if len(parts) == 1:
            name = parts[0]
            if name in self.builtins:
                return self.builtins[name]
            raise ExaRuntimeError(f"Unknown built-in tool '{name}'")

        if len(parts) == 2:
            mod, fn = parts
            if mod in self.std_modules:
                with self._state_lock:
                    included = mod in self.included_modules
                if not included:
                    raise ExaRuntimeError(
                        f"Module '{mod}' is not included. Add 'Include {mod}/.' before using '{mod}.{fn}'."
                    )
                if fn not in self.std_modules[mod]:
                    raise ExaRuntimeError(f"Module '{mod}' has no function '{fn}'")
                return self.std_modules[mod][fn]
            raise ExaRuntimeError(f"Unknown module or class '{mod}'")

        raise ExaRuntimeError(f"Invalid tool path: {'.'.join(parts)}")

    # --- Group 1: Conversion & Formatting ---

    def _tool_flip(self, value: Any, target: str) -> Any:
        mode = str(target).lower()
        if mode == "int":
            return int(value)
        if mode == "float":
            return float(value)
        if mode == "str":
            return str(value)
        if mode == "bool":
            if isinstance(value, str):
                norm = value.strip().lower()
                if norm in ("", "0", "false", "no", "off"):
                    return False
                if norm in ("1", "true", "yes", "on"):
                    return True
            return bool(value)
        if mode == "bin":
            return bin(int(value))
        if mode == "hex":
            return hex(int(value))
        if mode == "oct":
            return oct(int(value))
        if mode == "list":
            return list(value)
        if mode == "dict":
            return json.loads(value) if isinstance(value, str) else dict(value)
        if mode == "json":
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return json.dumps({"value": value})
            return json.dumps({"value": value} if not isinstance(value, (dict, list)) else value)
        if mode == "reverse":
            if isinstance(value, (str, list)):
                return value[::-1]
            raise ExaRuntimeError(f"FLIP reverse does not support type {type(value).__name__}")
        raise ExaRuntimeError(f"Unknown FLIP target {target!r}")

    def _tool_codec(self, value: Any, mode: str) -> Any:
        m = str(mode).lower()
        if m == "to_ascii":
            s = str(value)
            return ord(s) if len(s) == 1 else [ord(c) for c in s]
        if m == "from_ascii":
            if isinstance(value, list):
                return "".join(chr(int(x)) for x in value)
            return chr(int(value))
        if m == "to_bytes":
            return list(str(value).encode("utf-8"))
        if m == "from_bytes":
            return bytes(value).decode("utf-8")
        if m == "to_base64":
            return base64.b64encode(str(value).encode("utf-8")).decode("ascii")
        if m == "from_base64":
            return base64.b64decode(str(value).encode("ascii")).decode("utf-8")
        raise ExaRuntimeError(f"Unknown CODEC mode {mode!r}")

    def _tool_format(self, value: Any, *args: Any) -> str:
        if not args:
            return str(value)
        if len(args) == 1:
            p = str(args[0])
            if p == "upper":
                return str(value).upper()
            if p == "lower":
                return str(value).lower()
            if p == "trim":
                return str(value).strip()
            if "{}" in p and "{}" not in str(value):
                return p.format(value)
            if "{}" in str(value):
                return str(value).format(args[0])
            if p.startswith(".") and p.endswith("f"):
                return format(float(value), p)
            return format(value, p) if p else str(value)
        return str(value).format(*args)

    # --- Group 2: Basic I/O ---

    def _tool_emit(self, *args: Any) -> Any:
        text = " ".join(self.format_val(a) for a in args)
        self._emit_raw(text)
        return args[-1] if len(args) == 1 else text

    def _tool_listen(self, prompt: str = "") -> str:
        with self._io_lock:
            return self.input_fn(str(prompt))

    def _tool_stream(self, path: str, mode: str = "read", content: Any = None) -> Any:
        m = str(mode).lower()
        p = str(path)
        if m == "read":
            f = open(p, "r", encoding="utf-8")

            def line_gen() -> Generator[str, None, None]:
                try:
                    for line in f:
                        yield line.rstrip("\r\n")
                except Exception as exc:
                    raise ExaRuntimeError(f"Tool 'STREAM' failed while reading '{p}': {exc}") from exc
                finally:
                    f.close()

            return line_gen()
        if m == "read_all":
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
        if m == "write":
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.format_val(content))
            return f"WROTE:{p}"
        if m == "append":
            with open(p, "a", encoding="utf-8") as f:
                f.write(self.format_val(content) + "\n")
            return f"APPENDED:{p}"
        raise ExaRuntimeError(f"Unknown STREAM mode {mode!r}. Use read, read_all, write, or append.")

    # --- Group 3: Math & Sequences ---

    def _tool_calc(self, expr: Any, _env: Optional[Dict[str, Any]] = None) -> Any:
        if not isinstance(expr, str):
            return expr
        return SafeExprEvaluator.evaluate(expr, _env or {})

    def _tool_abs(self, val: Any) -> Any:
        return abs(val)

    def _tool_round(self, val: Any, digits: int = 0) -> Any:
        d = int(digits)
        return round(float(val), d) if d > 0 else round(float(val))

    def _tool_sum(self, items: Any) -> Any:
        return sum(items)

    def _tool_min(self, items: Any) -> Any:
        return min(items)

    def _tool_max(self, items: Any) -> Any:
        return max(items)

    def _tool_seq(self, start: int, end: int, step: int = 1) -> Generator[int, None, None]:
        if step == 0:
            raise ExaRuntimeError("SEQ step cannot be 0")
        cur, end_i, step_i = int(start), int(end), int(step)
        if step_i > 0:
            while cur <= end_i:
                yield cur
                cur += step_i
        else:
            while cur >= end_i:
                yield cur
                cur += step_i

    # --- Group 4: Streams & Collections ---

    def _tool_len(self, items: Any) -> int:
        return len(items)

    def _tool_filter(self, condition: str, _env: Optional[Dict[str, Any]] = None) -> Any:
        env = _env or {}
        if not SafeExprEvaluator.evaluate(str(condition), env):
            raise SkipItem()
        return env.get("$IN")

    def _tool_sort(self, items: Any, direction: str = "asc") -> List[Any]:
        return sorted(items, reverse=(str(direction).lower() == "desc"))

    def _tool_slice(self, items: Any, start: int, end: Optional[int] = None) -> Any:
        return items[int(start) : (int(end) if end is not None else None)]

    def _tool_check(self, items: Any, mode: str = "all", condition: Optional[str] = None) -> bool:
        m = str(mode).lower()
        if condition is not None:
            results = [bool(SafeExprEvaluator.evaluate(condition, {"$IN": x})) for x in items]
        else:
            results = [bool(x) for x in items]
        if m == "all":
            return all(results)
        if m == "any":
            return any(results)
        raise ExaRuntimeError(f"CHECK mode must be 'all' or 'any', got {mode!r}")

    def _tool_grathmeld(self, a: Any, b: Any) -> Any:
        if isinstance(a, dict) and isinstance(b, dict):
            merged = dict(a)
            merged.update(b)
            return merged
        if isinstance(a, list) and isinstance(b, list):
            return [list(pair) for pair in zip(a, b)]
        return f"{self.format_val(a)}{self.format_val(b)}"

    # --- Group 5: Inspection, Security & Meta ---

    def _tool_type(self, val: Any) -> str:
        mapping = {int: "int", float: "float", str: "str", bool: "bool", list: "list", dict: "dict"}
        return mapping.get(type(val), type(val).__name__)

    def _tool_hash(self, val: Any, algo: str = "sha256") -> str:
        a = str(algo).lower()
        raw = self.format_val(val).encode("utf-8")
        if a == "sha256":
            return hashlib.sha256(raw).hexdigest()
        if a == "md5":
            return hashlib.md5(raw).hexdigest()
        if a == "sha1":
            return hashlib.sha1(raw).hexdigest()
        raise ExaRuntimeError(f"Unsupported HASH algorithm {algo!r}")

    @staticmethod
    def _describe_method_tool(m: Optional[MethodDefNode]) -> str:
        if m is None:
            return "?"
        if len(m.stages) > 1:
            return " >> ".join(
                ".".join(s.inline_tool_parts or [s.method_name or "?"])
                for s in m.stages
            )
        return ".".join(m.tool_parts)

    def _tool_dive(self, target: Any) -> Dict[str, Any]:
        with self._state_lock:
            if isinstance(target, str):
                try:
                    key = self._canonical_target(target)
                except ExaRuntimeError:
                    key = None
                if key is not None:
                    if key in self.methods:
                        m = self.methods[key]
                    else:
                        cls_name, meth_name = key.split("::", 1)
                        m = self.resolve_class_method(cls_name, meth_name)
                    tool_desc = self._describe_method_tool(m)
                    info = {
                        "method": target,
                        "tool": tool_desc,
                        "permission": "b" + self.permissions.get(key, "111111111"),
                        "suspended": key in self.suspended,
                        "plugins": dict(self.plugins.get(key, {})),
                    }
                    self._emit_raw(f"[DIVE] {json.dumps(info)}")
                    return info
        info = {"value": target, "type": self._tool_type(target)}
        self._emit_raw(f"[DIVE] {json.dumps(info)}")
        return info

    def _tool_install(self, target_name: str, config: Dict[str, Any]) -> str:
        if not isinstance(config, dict):
            raise ExaRuntimeError("INSTALL expects a dictionary config, e.g. {'retry': 2}")
        key = self._canonical_target(str(target_name))
        with self._state_lock:
            existing = self.plugins.setdefault(key, {})
            existing.update(config)
            snap = dict(existing)
        return f"INSTALLED on {target_name}: {json.dumps(snap)}"

    def _tool_lore(self, topic: Optional[str] = None) -> str:
        with self._state_lock:
            docs = dict(self.lore_docs)
        if topic is None:
            keys = ", ".join(sorted(docs.keys()))
            msg = f"Exa LORE topics: {keys}"
            self._emit_raw(msg)
            return msg
        t = str(topic)
        doc = docs.get(t) or docs.get(t.upper())
        if not doc:
            t_low = t.lower()
            for k, v in docs.items():
                if k.lower() == t_low:
                    doc = v
                    break
        if not doc:
            raise ExaRuntimeError(f"No LORE entry for {topic!r}")
        self._emit_raw(f"[LORE] {doc}")
        return doc

    def _tool_chmod(self, perm: str, target_name: str) -> str:
        raw = str(perm).strip()
        if raw.startswith("b"):
            raw = raw[1:]
        if len(raw) != 9 or any(c not in "01" for c in raw):
            raise ExaRuntimeError(
                f"Invalid binary chmod format {perm!r}. Expected 9 bits like 'b111000000'."
            )
        key = self._canonical_target(str(target_name))
        with self._state_lock:
            self.permissions[key] = raw
        return f"chmod b{raw} -> {target_name}"

    # --- Group 6: Lifecycle, Persistent Memory & Routing ---

    def _tool_suspend(self, target_or_secs: Any, secs: Optional[float] = None) -> str:
        if isinstance(target_or_secs, (int, float)):
            time.sleep(float(target_or_secs))
            return f"SUSPENDED:{target_or_secs}s"
        target_str = str(target_or_secs)
        key = self._canonical_target(target_str)
        if secs is not None:
            time.sleep(float(secs))
        with self._state_lock:
            self.suspended.add(key)
        return f"SUSPENDED:{target_str}"

    def _tool_renation(self, target_name: str) -> str:
        target_str = str(target_name)
        key = self._canonical_target(target_str)
        with self._state_lock:
            self.suspended.discard(key)
        return f"RE=NATION:{target_str} (ACTIVE)"

    def _tool_store(self, key: str, value: Any) -> Any:
        with self._state_lock:
            self.memory_vars[str(key)] = value
        return value

    def _tool_recall(self, key: str, default: Any = None) -> Any:
        with self._state_lock:
            return self.memory_vars.get(str(key), default)

    def _tool_branch(
        self,
        condition: str,
        true_val: Any,
        false_val: Any,
        _env: Optional[Dict[str, Any]] = None,
    ) -> Any:
        passed = bool(SafeExprEvaluator.evaluate(str(condition), _env or {}))
        return true_val if passed else false_val

    def _tool_hibernation(self, mode: str, path: str = "hymn_state.json") -> str:
        m = str(mode).lower()
        if m == "save":
            with self._state_lock:
                serialized_methods = {
                    name: _method_to_json(meth)
                    for name, meth in self.methods.items()
                }
                serialized_classes = {
                    cname: {
                        "name": cls.name,
                        "base_class": cls.base_class,
                        "methods": [_method_to_json(m) for m in cls.methods],
                        "line": cls.line,
                    }
                    for cname, cls in self.classes.items()
                }
                snapshot = {
                    "hymn_code": self.hymn_code.raw_code,
                    "included_modules": sorted(self.included_modules),
                    "permissions": dict(self.permissions),
                    "suspended": sorted(self.suspended),
                    "plugins": dict(self.plugins),
                    "memory_vars": dict(self.memory_vars),
                    "methods": serialized_methods,
                    "classes": serialized_classes,
                }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
            return f"HIBERNATION_SAVED:{path}"

        if m == "load":
            with open(path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
            with self._state_lock:
                raw_hc = snapshot.get("hymn_code")
                if raw_hc:
                    parts = raw_hc.split("_")
                    self.hymn_code = HymnCodeNode(
                        raw_code=raw_hc,
                        user=parts[0] if len(parts) >= 1 else "GUEST",
                        rank=parts[1] if len(parts) >= 2 else "USER",
                        server="_".join(parts[2:]) if len(parts) >= 3 else "LOCAL",
                    )
                for mod in snapshot.get("included_modules", []):
                    if mod in self.std_modules:
                        self.included_modules.add(mod)
                for name, mdata in snapshot.get("methods", {}).items():
                    self.methods[name] = _json_to_method(mdata)
                for cname, cdata in snapshot.get("classes", {}).items():
                    self.classes[cname] = ClassDefNode(
                        name=cdata["name"],
                        base_class=cdata.get("base_class"),
                        methods=[_json_to_method(m) for m in cdata.get("methods", [])],
                        line=cdata.get("line", 0),
                    )
                self.permissions.update(snapshot.get("permissions", {}))
                self.suspended = set(snapshot.get("suspended", []))
                self.plugins.update(snapshot.get("plugins", {}))
                self.memory_vars.update(snapshot.get("memory_vars", {}))
            return f"HIBERNATION_LOADED:{path}"

        raise ExaRuntimeError(f"HIBERNATION mode must be 'save' or 'load', got {mode!r}")

    def _tool_purger(self, *target_names: str) -> str:
        purged = []
        with self._state_lock:
            for name in target_names:
                if name in self.methods:
                    del self.methods[name]
                    self.permissions.pop(name, None)
                    self.plugins.pop(name, None)
                    self.suspended.discard(name)
                    purged.append(name)
                elif name in self.classes:
                    del self.classes[name]
                    purged.append(name)
        return f"PURGED: {', '.join(purged) if purged else 'none'}"

    # --- Standard Module: net ---

    def _net_request_access(self, scope: str = "internet") -> str:
        return f"NET_ACCESS_GRANTED:{scope} (rank={self.hymn_code.rank})"

    def _net_ping(self, host: str, count: int = 1) -> str:
        param = "-n" if platform.system().lower().startswith("win") else "-c"
        cmd = ["ping", param, str(int(count)), str(host)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except Exception as exc:
            raise ExaRuntimeError(f"Ping failed for {host}: {exc}") from exc

        if proc.returncode != 0:
            err_detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
            raise ExaRuntimeError(f"Host {host} unreachable ({err_detail})")

        m = re.search(r"time[=<]\s*([0-9.]+\s*ms)", proc.stdout, re.IGNORECASE)
        latency = m.group(1) if m else "ok"
        return f"PING {host}: {latency}"

    def _net_http_get(self, url: str) -> str:
        try:
            with urllib.request.urlopen(str(url), timeout=5) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise ExaRuntimeError(f"HTTP GET failed for {url}: {exc}") from exc