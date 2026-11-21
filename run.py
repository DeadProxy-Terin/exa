"""
CLI runner and Interactive REPL for Exa v0.4.
Usage:
  python run.py                  # Run built-in verification demo
  python run.py script.exa       # Execute an Exa file
  python run.py --ast file.exa   # Print parsed AST before executing
  python run.py --repl           # Start interactive REPL session
"""

import json
import os
from pprint import pprint
import sys
from typing import Tuple

from exa import (
    ExaError,
    Interpreter,
    Lexer,
    LexerError,
    Parser,
    Runtime,
    run_exa,
)

DEMO_V04 = """
#HYMN_CODE: SHURELIA_USER_ARCIEL/.

includes.Ex_console/.
Include math/.
Include sys/.
Include time/.

// 1. Class:: inheritance (Ar nosurge style)
Class::REYVATEIL {
    .METHOD_SING = (EMIT) > ("Hymmnos Resonance ->", $IN)/.
};

Class::IPD extends.REYVATEIL {
    .METHOD_AMPLIFY = (CALC) > ("$IN * 10")/.
};

EXEC = (IPD.AMPLIFY) > (7) >> (IPD.SING)/.

// 2. CODEC, FORMAT, HASH, SLICE, GRATHMELD
EXEC = (CODEC) > ("Hymmnos", "to_base64") >> (EMIT) > ("Base64:", $IN) >> (CODEC) > ($IN, "from_base64") >> (EMIT) > ("Decoded:", $IN)/.
EXEC = (FORMAT) > (3.14159, ".2f") >> (EMIT) > ("Pi formatted:", $IN)/.
EXEC = (HASH) > ("Ar Ciel") >> (SLICE) > ($IN, 0, 12) >> (EMIT) > ("Hash12:", $IN)/.
EXEC = (GRATHMELD) > ([1, 2, 3], ["A", "B", "C"]) >> (EMIT) > ("Grathmeld:", $IN)/.

// 3. Binary chmod + over error recovery
.METHOD_LOCKED = (EMIT) > ("Secret tower access!")/.
.METHOD_DENIED = (console.warn) > ("Access blocked -> $ERR")/.
EXEC = (sys.chmod) > ("b110000000", .METHOD_LOCKED)/.
EXEC = (.METHOD_LOCKED) over (.METHOD_DENIED)/.

// 4. SUSPEND & RE=NATION
.METHOD_PING_SIM = (EMIT) > ("Ping #", $STEP, "OK")/.
EXEC = (SUSPEND) > (.METHOD_PING_SIM)/.
EXEC = (.METHOD_PING_SIM) over (.METHOD_DENIED)/.
EXEC = (RE=NATION) > (.METHOD_PING_SIM)/.
EXEC = (.METHOD_PING_SIM << doloop.C:2)/.

// 5. doloop.WHILE
EXEC = (SEQ) > (1, 10) >> ((EMIT) > ("While <4:", $IN) << doloop.WHILE:"$IN < 4")/.

// 6. EXEC_with (parallel pipelines) & REPLEKIA (multi-worker)
.METHOD_TASK_A = (EMIT) > ("Parallel A step", $STEP)/.
.METHOD_TASK_B = (EMIT) > ("Parallel B step", $STEP)/.
EXEC_with = (.METHOD_TASK_A << doloop.C:2) & (.METHOD_TASK_B << doloop.C:2)/.

.METHOD_WORKER = (CALC) > ("$STEP * $STEP")/.
EXEC = (.METHOD_WORKER << REPLEKIA:4) >> (SUM) >> (EMIT) > ("REPLEKIA:4 sum of squares:", $IN)/.

// 7. DIVE sandbox step-by-step trace
DIVE = (CALC) > ("5 + 5") >> (CALC) > ("$IN * 3")/.
"""


REPL_HELP = """
Exa REPL Meta-Commands:
  :help           Show this help message
  :methods        List defined .METHOD_* slots and permissions/plugins
  :classes        List defined Class:: definitions and their methods
  :vars           Show stored variables (STORE / RECALL)
  :modules        List included modules, available std/registered modules, and loaded files
  :clear          Clear the terminal screen (use ':clear state' or ':reset' to reset session)
  :reset          Reset Runtime & Interpreter state for a fresh session
  :cancel         Cancel the current incomplete multi-line buffer
  :exit / :quit   Exit the REPL
""".strip()


VALID_STATEMENT_STARTERS = {"Include", "includes", "Class", "EXEC", "EXEC_with", "DIVE"}


def _is_buffer_complete(source: str) -> Tuple[bool, bool]:
    """
    Determines whether the accumulated REPL buffer is ready to parse.
    Returns (is_complete, is_empty_or_comments_only).
    """
    try:
        tokens = Lexer(source).tokenize()
    except LexerError as exc:
        if "Unterminated block comment" in str(exc):
            return False, False
        # Report invalid tokens / unterminated single-line strings immediately
        return True, False

    non_eof = [t for t in tokens if t.kind != "EOF"]
    if not non_eof:
        return True, True

    first = non_eof[0]
    is_valid_start = (
        first.kind in ("HASH", "DOT")
        or (first.kind == "KEYWORD" and first.value in VALID_STATEMENT_STARTERS)
    )
    if not is_valid_start:
        # Fail fast instead of trapping invalid input in multi-line continuation
        return True, False

    brace_depth = 0
    for tok in non_eof:
        if tok.kind == "LBRACE":
            brace_depth += 1
        elif tok.kind == "RBRACE":
            brace_depth = max(0, brace_depth - 1)

    if brace_depth > 0:
        return False, False

    last_tok = non_eof[-1]
    if last_tok.kind in ("TERM", "SEMI"):
        return True, False
    return False, False


def _print_repl_meta(
    cmd: str, rt: Runtime, interp: Interpreter
) -> Tuple[bool, Runtime, Interpreter]:
    """
    Executes a REPL meta-command starting with ':'.
    Returns (should_continue, runtime, interpreter).
    """
    if cmd in (":exit", ":quit"):
        print("Exiting Exa REPL.")
        return False, rt, interp

    if cmd == ":help":
        print(REPL_HELP)
        return True, rt, interp

    if cmd == ":methods":
        with rt._state_lock:
            if not rt.methods:
                print("(no methods defined)")
            else:
                for name, m in rt.methods.items():
                    desc = rt._describe_method_tool(m)
                    perm = "b" + rt.permissions.get(name, "111111111")
                    susp = " [SUSPENDED]" if name in rt.suspended else ""
                    plug = f" plugins={json.dumps(rt.plugins[name])}" if name in rt.plugins else ""
                    print(f"  {name} = {desc} ({perm}){susp}{plug}")
        return True, rt, interp

    if cmd == ":classes":
        with rt._state_lock:
            if not rt.classes:
                print("(no classes defined)")
            else:
                for cname, cls in rt.classes.items():
                    ext = f" extends.{cls.base_class}" if cls.base_class else ""
                    mnames = ", ".join(m.name for m in cls.methods) or "(none)"
                    print(f"  Class::{cname}{ext} -> [{mnames}]")
        return True, rt, interp

    if cmd == ":vars":
        with rt._state_lock:
            if not rt.memory_vars:
                print("(no stored variables)")
            else:
                print(json.dumps(rt.memory_vars, indent=2, default=str))
        return True, rt, interp

    if cmd == ":modules":
        with rt._state_lock:
            inc = sorted(rt.included_modules)
            avail = sorted(rt.std_modules.keys())
            files = sorted(rt.loaded_files)
        print(f"  Included modules : {', '.join(inc) if inc else '(none)'}")
        print(f"  Available modules: {', '.join(avail)}")
        print(f"  Loaded files     : {', '.join(files) if files else '(none)'}")
        return True, rt, interp

    if cmd == ":clear":
        if sys.stdout.isatty():
            os.system("cls" if os.name == "nt" else "clear")
        else:
            print("\033[2J\033[H", end="")
        return True, rt, interp

    if cmd in (":reset", ":clear state"):
        new_rt = Runtime()
        new_interp = Interpreter(new_rt)
        print("[REPL] Runtime and Interpreter state reset.")
        return True, new_rt, new_interp

    print(f"Unknown REPL command {cmd!r}. Type ':help' for available commands.")
    return True, rt, interp


def run_repl(show_ast: bool = False) -> None:
    """Starts an interactive, multi-line Exa REPL session."""
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    print("Exa v0.4 Interactive REPL")
    print("End statements with '/.' (or '};' for Class::). Type ':help' or ':exit'.")

    rt = Runtime()
    interp = Interpreter(rt)
    buffer_lines = []

    while True:
        prompt = "... > " if buffer_lines else "exa> "
        try:
            line = input(prompt)
        except EOFError:
            print("\nExiting Exa REPL.")
            break
        except KeyboardInterrupt:
            print()
            if buffer_lines:
                buffer_lines.clear()
                print("(multi-line buffer cleared)")
                continue
            print("Type ':exit' or ':quit' to exit.")
            continue

        stripped = line.strip()

        if not buffer_lines and stripped.lower() in ("help", "exit", "quit"):
            print(f"[Hint] Use ':{stripped.lower()}' for REPL commands (Exa statements end with '/.').")
            continue

        # Handle meta-commands when starting a command, or :cancel/:help/:exit/:quit mid-buffer
        if stripped.startswith(":"):
            low = stripped.lower()
            if low == ":cancel":
                buffer_lines.clear()
                print("(multi-line buffer cleared)")
                continue
            if low == ":help" and buffer_lines:
                print(REPL_HELP)
                continue
            if not buffer_lines or low in (":exit", ":quit"):
                should_continue, rt, interp = _print_repl_meta(low, rt, interp)
                buffer_lines.clear()
                if not should_continue:
                    break
                continue

        buffer_lines.append(line)
        source = "\n".join(buffer_lines)
        complete, empty_only = _is_buffer_complete(source)

        if not complete:
            continue

        buffer_lines.clear()
        if empty_only:
            continue

        try:
            tokens = Lexer(source).tokenize()
            ast_tree = Parser(tokens).parse()
            if show_ast:
                pprint(ast_tree)
            interp.run(ast_tree)
        except ExaError as exc:
            print(f"[Exa Error] {exc}")
        except Exception as exc:
            print(f"[Internal Error] {type(exc).__name__}: {exc}")


def main() -> None:
    args = sys.argv[1:]
    show_ast = "--ast" in args
    repl_mode = "--repl" in args
    file_args = [a for a in args if a not in ("--ast", "--repl")]

    if repl_mode:
        run_repl(show_ast=show_ast)
        return

    if file_args:
        script_path = os.path.abspath(file_args[0])
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                source = f.read()
            run_exa(
                source,
                dump_ast=show_ast,
                base_dir=os.path.dirname(script_path),
            )
        except FileNotFoundError:
            print(f"[CLI Error] File not found: {file_args[0]}", file=sys.stderr)
            sys.exit(1)
        except ExaError as exc:
            print(f"[Exa Error] {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        print("=== Exa v0.4 Built-in Demo ===")
        run_exa(DEMO_V04, dump_ast=show_ast)


if __name__ == "__main__":
    main()