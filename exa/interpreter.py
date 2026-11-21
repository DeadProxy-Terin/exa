"""
Pipeline & Concurrency Interpreter for Exa v0.4.
Includes:
  - Composite multi-stage .METHOD_X pipeline expansion
  - Smart $IN prepending for FORMAT, GRATHMELD, FLIP, CODEC, HASH, ROUND, STORE & aggregators
  - doloop.WHILE support for both upstream.is_single_value and root-$IN conditions
  - Class::Method full integration with chmod, SUSPEND, RE=NATION, INSTALL, and DIVE
  - Race-free cleanup of anonymous .METHOD after EXEC_with finishes
  - BRANCH conditional routing (values or .METHOD / Class.Method execution)
  - Dynamic plugin capabilities (stream_aggregators, prepend_in, passthrough, env_tools)
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import re
import time
from typing import Any, Dict, Generator, List, Optional, Tuple

from .ast_nodes import (
    ASTNode,
    ClassDefNode,
    DictNode,
    ExecNode,
    HymnCodeNode,
    IncludeNode,
    ListNode,
    LiteralNode,
    MethodDefNode,
    PipelineExpr,
    ProgramNode,
    StageNode,
    VarNode,
)
from .errors import ExaRuntimeError, SkipItem
from .evaluator import SafeExprEvaluator
from .runtime import Runtime


@dataclass
class StreamPacket:
    """Wraps a pipeline generator along with whether its source is a single static value."""
    gen: Generator[Any, None, None]
    is_single_value: bool


class Interpreter:
    """Executes an Exa ProgramNode AST."""

    OUTPUT_PASSTHROUGH_TOOLS = Runtime.DEFAULT_OUTPUT_PASSTHROUGH_TOOLS

    def __init__(self, runtime: Optional[Runtime] = None):
        self.runtime = runtime or Runtime()

    def run(self, program: ProgramNode) -> None:
        for stmt in program.statements:
            if isinstance(stmt, HymnCodeNode):
                with self.runtime._state_lock:
                    self.runtime.hymn_code = stmt
            elif isinstance(stmt, IncludeNode):
                self.runtime.include_module(stmt.module_name)
            elif isinstance(stmt, MethodDefNode):
                with self.runtime._state_lock:
                    self.runtime.methods[stmt.name] = stmt
            elif isinstance(stmt, ClassDefNode):
                with self.runtime._state_lock:
                    if stmt.base_class and stmt.base_class not in self.runtime.classes:
                        raise ExaRuntimeError(
                            f"[Line {stmt.line}] Base class '{stmt.base_class}' not defined before '{stmt.name}'"
                        )
                    self.runtime.classes[stmt.name] = stmt
            elif isinstance(stmt, ExecNode):
                self._exec_node(stmt)

    # --- Argument & Tool Invocation ---

    def _eval_arg(self, node: ASTNode, env: Dict[str, Any]) -> Any:
        if isinstance(node, LiteralNode):
            return node.value
        if isinstance(node, ListNode):
            return [self._eval_arg(el, env) for el in node.elements]
        if isinstance(node, DictNode):
            return {k: self._eval_arg(v_node, env) for k, v_node in node.pairs}
        if isinstance(node, VarNode):
            if node.name in env:
                return env[node.name]
            raise ExaRuntimeError(f"[Line {node.line}] Unbound variable {node.name}")
        raise ExaRuntimeError(f"Unsupported argument AST node: {type(node).__name__}")

    def _interpolate_vars(self, val: Any, env: Dict[str, Any]) -> Any:
        if isinstance(val, str) and "$" in val:
            def _sub(match: re.Match) -> str:
                key = "$" + match.group(1)
                if env.get(key) is not None:
                    return self.runtime.format_val(env[key])
                return match.group(0)
            return re.sub(r"\$(IN|STEP|ERR)\b", _sub, val)
        if isinstance(val, list):
            return [self._interpolate_vars(item, env) for item in val]
        if isinstance(val, dict):
            return {k: self._interpolate_vars(v, env) for k, v in val.items()}
        return val

    @staticmethod
    def _accepts_positional_arg(fn: Any) -> bool:
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return True
        for p in sig.parameters.values():
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                return True
        return False

    @staticmethod
    def _has_explicit_in_var(arg_nodes: List[ASTNode]) -> bool:
        return any(isinstance(a, VarNode) and a.name == "$IN" for a in arg_nodes)

    def _invoke_tool(
        self,
        tool_parts: List[str],
        arg_nodes: List[ASTNode],
        env: Dict[str, Any],
        has_upstream: bool,
        method_key: Optional[str] = None,
    ) -> Any:
        # Check Class::Method invocation first (e.g. REYVATEIL.SING or Hero.NOW)
        with self.runtime._state_lock:
            is_class_call = len(tool_parts) == 2 and tool_parts[0] in self.runtime.classes

        if is_class_call:
            cls_method = self.runtime.resolve_class_method(tool_parts[0], tool_parts[1])
            if cls_method is None:
                raise ExaRuntimeError(
                    f"Class '{tool_parts[0]}' has no method '{tool_parts[1]}'"
                )
            cls_key = f"{tool_parts[0]}::{cls_method.name}"
            self.runtime.check_method_executable(cls_key)

            caller_supplied_input = has_upstream or (len(arg_nodes) > 0)
            if has_upstream and len(arg_nodes) == 0:
                cls_in = env.get("$IN")
            elif len(arg_nodes) > 0:
                passed = [self._eval_arg(a, env) for a in arg_nodes]
                cls_in = passed[0] if len(passed) == 1 else passed
            else:
                cls_in = None

            cls_env = {"$IN": cls_in, "$STEP": env.get("$STEP", 1), "$ERR": env.get("$ERR")}
            if len(cls_method.stages) > 1:
                return self._invoke_composite_with_plugins(
                    cls_method, cls_env, has_upstream=caller_supplied_input, method_key=cls_key
                )
            return self._invoke_tool(
                cls_method.tool_parts,
                cls_method.args,
                cls_env,
                has_upstream=caller_supplied_input,
                method_key=cls_key,
            )

        fn = self.runtime.resolve_tool(tool_parts)

        # Resolve arguments with smart $IN injection
        if len(arg_nodes) == 0:
            if has_upstream and self._accepts_positional_arg(fn):
                resolved_args = [env.get("$IN")]
            else:
                resolved_args = []
        else:
            resolved_args = [self._eval_arg(a, env) for a in arg_nodes]
            if has_upstream and not self._has_explicit_in_var(arg_nodes):
                tkey = tuple(tool_parts)
                with self.runtime._state_lock:
                    is_agg_tool = tkey in self.runtime.stream_aggregators
                    is_auto_prep = tkey in self.runtime.auto_prepend_in_tools
                    is_custom_prep = tkey in self.runtime.custom_prepend_in_tools
                if is_agg_tool and not isinstance(arg_nodes[0], ListNode):
                    resolved_args = [env.get("$IN")] + resolved_args
                elif is_custom_prep:
                    resolved_args = [env.get("$IN")] + resolved_args
                elif is_auto_prep and len(arg_nodes) == 1:
                    resolved_args = [env.get("$IN")] + resolved_args
                elif tkey == ("STORE",) and len(arg_nodes) == 1:
                    resolved_args = resolved_args + [env.get("$IN")]

        # Plugin middleware hooks (installed via INSTALL)
        with self.runtime._state_lock:
            plugin_cfg = dict(self.runtime.plugins.get(method_key, {})) if method_key else {}
        max_retries = int(plugin_cfg.get("retry", 0))
        delay_secs = float(plugin_cfg.get("delay", 0))
        prefix = plugin_cfg.get("prefix")

        attempt = 0
        while True:
            try:
                if delay_secs > 0:
                    time.sleep(delay_secs)

                with self.runtime._state_lock:
                    is_env_tool = tuple(tool_parts) in self.runtime.env_tools
                    is_passthrough_tool = tuple(tool_parts) in self.runtime.passthrough_tools

                if is_env_tool:
                    res = fn(*resolved_args, _env=env)
                elif tool_parts == ["BRANCH"]:
                    chosen = fn(*resolved_args, _env=env)
                    if isinstance(chosen, str):
                        with self.runtime._state_lock:
                            mdef = self.runtime.methods.get(chosen)
                            is_cls_branch = (
                                mdef is None
                                and "." in chosen
                                and not chosen.startswith(".")
                                and chosen.split(".", 1)[0] in self.runtime.classes
                            )
                        if mdef is not None:
                            self.runtime.check_method_executable(chosen)
                            if len(mdef.stages) > 1:
                                res = self._invoke_composite_with_plugins(
                                    mdef, env, has_upstream=has_upstream, method_key=chosen
                                )
                            else:
                                res = self._invoke_tool(
                                    mdef.tool_parts, mdef.args, env, has_upstream=has_upstream, method_key=chosen
                                )
                        elif is_cls_branch:
                            c_parts = chosen.split(".", 1)
                            res = self._invoke_tool(c_parts, [], env, has_upstream=has_upstream)
                        else:
                            res = chosen
                    else:
                        res = chosen
                elif tool_parts == ["CHECK"]:
                    res = fn(*resolved_args)
                else:
                    final_args = [self._interpolate_vars(v, env) for v in resolved_args]
                    res = fn(*final_args)

                if prefix and isinstance(res, str):
                    res = f"{prefix} {res}"

                if has_upstream and is_passthrough_tool:
                    return env.get("$IN")
                return res
            except (ExaRuntimeError, SkipItem):
                if attempt < max_retries:
                    attempt += 1
                    continue
                raise
            except Exception as exc:
                if attempt < max_retries:
                    attempt += 1
                    continue
                raise ExaRuntimeError(f"Tool '{'.'.join(tool_parts)}' failed: {exc}") from exc

    def _invoke_composite_with_plugins(
        self,
        method_def: MethodDefNode,
        env: Dict[str, Any],
        has_upstream: bool,
        method_key: Optional[str] = None,
    ) -> Any:
        """Evaluates a composite method with optional INSTALL middleware (retry/delay/prefix)."""
        with self.runtime._state_lock:
            plugin_cfg = dict(self.runtime.plugins.get(method_key, {})) if method_key else {}
        max_retries = int(plugin_cfg.get("retry", 0))
        delay_secs = float(plugin_cfg.get("delay", 0))
        prefix = plugin_cfg.get("prefix")

        attempt = 0
        while True:
            try:
                if delay_secs > 0:
                    time.sleep(delay_secs)
                res = self._eval_composite_method(method_def, env, has_upstream=has_upstream)
                if prefix and isinstance(res, str):
                    res = f"{prefix} {res}"
                return res
            except (ExaRuntimeError, SkipItem):
                if attempt < max_retries:
                    attempt += 1
                    continue
                raise
            except Exception as exc:
                if attempt < max_retries:
                    attempt += 1
                    continue
                label = method_key or method_def.name
                raise ExaRuntimeError(f"Composite method '{label}' failed: {exc}") from exc

    def _eval_composite_method(
        self,
        method_def: MethodDefNode,
        env: Dict[str, Any],
        has_upstream: bool,
    ) -> Any:
        """Evaluates a multi-stage composite method synchronously."""
        seed_pkt: Optional[StreamPacket] = None
        if has_upstream:
            def seed_gen() -> Generator[Any, None, None]:
                yield env.get("$IN")
            seed_pkt = StreamPacket(gen=seed_gen(), is_single_value=True)

        pkt = seed_pkt
        outer_step = int(env.get("$STEP", 1) or 1)
        for idx, st in enumerate(method_def.stages):
            pkt = self._run_stage(
                st,
                pkt,
                err_value=env.get("$ERR"),
                initial_step=(outer_step if idx == 0 else 1),
            )
        if not pkt:
            return []
        try:
            results = list(pkt.gen)
        finally:
            pkt.gen.close()
        return results[0] if len(results) == 1 else results

    def _resolve_stage_spec(
        self, stage: StageNode
    ) -> Tuple[Optional[MethodDefNode], List[str], List[ASTNode], Optional[str]]:
        if stage.method_name is not None:
            with self.runtime._state_lock:
                if stage.method_name not in self.runtime.methods:
                    raise ExaRuntimeError(f"[Line {stage.line}] Undefined method '{stage.method_name}'")
                m = self.runtime.methods[stage.method_name]
            self.runtime.check_method_executable(stage.method_name)
            return m, m.tool_parts, m.args, m.name
        assert stage.inline_tool_parts is not None
        return None, stage.inline_tool_parts, stage.inline_args or [], None

    # --- Stage Execution ---

    def _run_stage(
        self,
        stage: StageNode,
        upstream: Optional[StreamPacket],
        err_value: Optional[str] = None,
        dive_mode: bool = False,
        stage_idx: int = 1,
        initial_step: int = 1,
    ) -> StreamPacket:
        method_def, tool_parts, arg_nodes, method_name = self._resolve_stage_spec(stage)
        loop = stage.loop

        # Expand composite multi-stage .METHOD_X inline if no loop wrapper and no plugins are attached
        has_plugins = False
        if method_def is not None and method_name:
            with self.runtime._state_lock:
                has_plugins = bool(self.runtime.plugins.get(method_name))

        if method_def is not None and len(method_def.stages) > 1 and loop is None and not has_plugins:
            pkt = upstream
            for sub_idx, sub_stage in enumerate(method_def.stages):
                pkt = self._run_stage(
                    sub_stage,
                    pkt,
                    err_value=err_value,
                    dive_mode=dive_mode,
                    stage_idx=stage_idx,
                    initial_step=(initial_step if sub_idx == 0 else 1),
                )
            assert pkt is not None
            return pkt

        has_upstream = upstream is not None
        with self.runtime._state_lock:
            is_agg = tuple(tool_parts) in self.runtime.stream_aggregators
        out_is_single = (loop is None) and ((upstream is None) or is_agg or upstream.is_single_value)
        packet = StreamPacket(gen=(x for x in ()), is_single_value=out_is_single)

        def run_once(in_val: Any, step: int) -> Generator[Any, None, None]:
            env = {"$IN": in_val, "$STEP": step, "$ERR": err_value}
            if method_def is not None and len(method_def.stages) > 1:
                try:
                    res = self._invoke_composite_with_plugins(
                        method_def, env, has_upstream=has_upstream, method_key=method_name
                    )
                except SkipItem:
                    if dive_mode:
                        label = method_name or ".".join(tool_parts)
                        self.runtime._emit_raw(
                            f"[DIVE Stage {stage_idx} | {label} | step={step}] FILTERED OUT ($IN={in_val!r})"
                        )
                    return
                if dive_mode:
                    label = method_name or ".".join(tool_parts)
                    self.runtime._emit_raw(
                        f"[DIVE Stage {stage_idx} | {label} | step={step}] $IN={in_val!r} -> OUT={res!r}"
                    )
                yield res
                return

            try:
                res = self._invoke_tool(
                    tool_parts, arg_nodes, env, has_upstream=has_upstream, method_key=method_name
                )
            except SkipItem:
                if dive_mode:
                    label = method_name or ".".join(tool_parts)
                    self.runtime._emit_raw(
                        f"[DIVE Stage {stage_idx} | {label} | step={step}] FILTERED OUT ($IN={in_val!r})"
                    )
                return
            if isinstance(res, Generator):
                packet.is_single_value = False
                try:
                    for out in res:
                        if dive_mode:
                            label = method_name or ".".join(tool_parts)
                            self.runtime._emit_raw(
                                f"[DIVE Stage {stage_idx} | {label} | step={step}] $IN={in_val!r} -> OUT={out!r}"
                            )
                        yield out
                except (ExaRuntimeError, SkipItem, GeneratorExit):
                    raise
                except Exception as exc:
                    label = method_name or ".".join(tool_parts)
                    raise ExaRuntimeError(f"Stream in '{label}' failed: {exc}") from exc
                finally:
                    res.close()
            else:
                if dive_mode:
                    label = method_name or ".".join(tool_parts)
                    self.runtime._emit_raw(
                        f"[DIVE Stage {stage_idx} | {label} | step={step}] $IN={in_val!r} -> OUT={res!r}"
                    )
                yield res

        def run_once_materialized(in_val: Any, step: int) -> List[Any]:
            return list(run_once(in_val, step))

        def gen() -> Generator[Any, None, None]:
            if upstream is None:
                if loop is None:
                    yield from run_once(None, initial_step)
                elif loop.mode == "C":
                    for step in range(1, (loop.count or 0) + 1):
                        yield from run_once(None, step)
                elif loop.mode in ("UT", "UTE"):
                    step = 1
                    while True:
                        yield from run_once(None, step)
                        step += 1
                elif loop.mode == "WHILE":
                    assert loop.condition is not None
                    cond_uses_in = bool(re.search(r"\$IN\b", loop.condition))
                    step = 1
                    last_val = None
                    if cond_uses_in:
                        # Produce candidate first so $IN is bound before condition check
                        while True:
                            produced = list(run_once(last_val, step))
                            if not produced:
                                break
                            stop = False
                            for item in produced:
                                if not SafeExprEvaluator.evaluate(
                                    loop.condition, {"$IN": item, "$STEP": step, "$ERR": err_value}
                                ):
                                    stop = True
                                    break
                                last_val = item
                                yield item
                            if stop:
                                break
                            step += 1
                    else:
                        while SafeExprEvaluator.evaluate(
                            loop.condition, {"$IN": last_val, "$STEP": step, "$ERR": err_value}
                        ):
                            produced = list(run_once(last_val, step))
                            for item in produced:
                                last_val = item
                                yield item
                            step += 1
                elif loop.mode == "REPLEKIA":
                    workers = loop.count or 1
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = [
                            pool.submit(run_once_materialized, None, step)
                            for step in range(1, workers + 1)
                        ]
                        for fut in futures:
                            for item in fut.result():
                                yield item
            else:
                try:
                    if is_agg and loop is None:
                        collected = list(upstream.gen)
                        payload = (
                            collected[0]
                            if (len(collected) == 1 and (upstream.is_single_value or isinstance(collected[0], list)))
                            else collected
                        )
                        yield from run_once(payload, initial_step)
                    elif loop is None:
                        step = initial_step
                        for item in upstream.gen:
                            if not upstream.is_single_value:
                                packet.is_single_value = False
                            yield from run_once(item, step)
                            step += 1
                    elif loop.mode == "C":
                        count = loop.count or 0
                        if count <= 0:
                            return
                        try:
                            first_item = next(upstream.gen)
                        except StopIteration:
                            return
                        if upstream.is_single_value:
                            for step in range(1, count + 1):
                                yield from run_once(first_item, step)
                        else:
                            yield from run_once(first_item, 1)
                            for step in range(2, count + 1):
                                try:
                                    item = next(upstream.gen)
                                except StopIteration:
                                    break
                                yield from run_once(item, step)
                    elif loop.mode in ("UT", "UTE"):
                        try:
                            first_item = next(upstream.gen)
                        except StopIteration:
                            return
                        if upstream.is_single_value:
                            st = 1
                            while True:
                                yield from run_once(first_item, st)
                                st += 1
                        else:
                            yield from run_once(first_item, 1)
                            st = 2
                            for item in upstream.gen:
                                yield from run_once(item, st)
                                st += 1
                    elif loop.mode == "WHILE":
                        assert loop.condition is not None
                        try:
                            first_item = next(upstream.gen)
                        except StopIteration:
                            return
                        if upstream.is_single_value:
                            st = 1
                            while SafeExprEvaluator.evaluate(
                                loop.condition, {"$IN": first_item, "$STEP": st, "$ERR": err_value}
                            ):
                                yield from run_once(first_item, st)
                                st += 1
                        else:
                            if SafeExprEvaluator.evaluate(
                                loop.condition, {"$IN": first_item, "$STEP": 1, "$ERR": err_value}
                            ):
                                yield from run_once(first_item, 1)
                                st = 2
                                for item in upstream.gen:
                                    if not SafeExprEvaluator.evaluate(
                                        loop.condition, {"$IN": item, "$STEP": st, "$ERR": err_value}
                                    ):
                                        break
                                    yield from run_once(item, st)
                                    st += 1
                    elif loop.mode == "REPLEKIA":
                        workers = loop.count or 1
                        items = list(upstream.gen)
                        with ThreadPoolExecutor(max_workers=workers) as pool:
                            futures = [
                                pool.submit(run_once_materialized, item, idx)
                                for idx, item in enumerate(items, start=1)
                            ]
                            for fut in futures:
                                for res_item in fut.result():
                                    yield res_item
                finally:
                    upstream.gen.close()

        packet.gen = gen()
        return packet

    def _run_pipeline_expr(
        self,
        pipeline: PipelineExpr,
        dive_mode: bool = False,
        err_value: Optional[str] = None,
    ) -> None:
        packet: Optional[StreamPacket] = None
        for idx, stage in enumerate(pipeline.stages, start=1):
            packet = self._run_stage(
                stage,
                packet,
                err_value=err_value,
                dive_mode=dive_mode,
                stage_idx=idx,
            )
        if packet is not None:
            try:
                for _ in packet.gen:
                    pass
            finally:
                packet.gen.close()

    @staticmethod
    def _exec_uses_anon_method(exec_node: ExecNode) -> bool:
        for p in exec_node.pipelines:
            for st in p.stages:
                if st.method_name == ".METHOD":
                    return True
        return exec_node.fallback_method == ".METHOD"

    def _exec_node(self, exec_node: ExecNode) -> None:
        used_anon = self._exec_uses_anon_method(exec_node)
        try:
            try:
                if exec_node.parallel:
                    with ThreadPoolExecutor(max_workers=max(1, len(exec_node.pipelines))) as pool:
                        futures = [
                            pool.submit(self._run_pipeline_expr, p, exec_node.dive_mode)
                            for p in exec_node.pipelines
                        ]
                        for fut in futures:
                            fut.result()
                else:
                    self._run_pipeline_expr(exec_node.pipelines[0], dive_mode=exec_node.dive_mode)
            except ExaRuntimeError as exc:
                if exec_node.fallback_method is None:
                    raise
                fb_pipeline = PipelineExpr(
                    stages=[StageNode(method_name=exec_node.fallback_method, line=exec_node.line)]
                )
                self._run_pipeline_expr(fb_pipeline, dive_mode=exec_node.dive_mode, err_value=str(exc))
        finally:
            # Safe race-free cleanup of anonymous .METHOD after all parallel threads complete
            if used_anon:
                with self.runtime._state_lock:
                    self.runtime.methods.pop(".METHOD", None)