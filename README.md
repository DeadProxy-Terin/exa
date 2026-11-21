# Exa (`.exa`)

> An *Ar tonelico / EXA_PICO*-inspired, lazy pipeline-driven scripting and task-runner language.

**Exa** is an independent programming language with its own syntax, lazy stream execution model, security/lifecycle primitives, and OOP class system. It is currently implemented as a **zero-dependency Python 3 prototype (v0.4)** while its language specification matures toward a standalone native bytecode VM.

---

## Key Features

* **Lazy Pipeline Engine (`>>` & `StreamPacket`):** Connects stages lazily using generators. Differentiates single scalar values from multi-item/infinite streams automatically, and cleanly tears down upstream infinite streams (`doloop.UTE`) when downstream bounds (`doloop.C:<n>` or `doloop.WHILE`) complete.
* **Ar tonelico / Ar nosurge-Inspired Grammar:**
  * Statement terminators `/.` (and `};` for `Class::` blocks)
  * Identity & privilege header `#HYMN_CODE: USER_RANK_SERVER/.` (rank `ANSUL` acts as administrator)
  * Reusable `.METHOD_*` slots, temporary anonymous `.METHOD`, and multi-stage composite methods
  * `Class::NAME` & `Class::CHILD extends.BASE` inheritance
* **Concurrency & Flow Control:**
  * Parallel pipelines via `EXEC_with = (A) & (B)/.`
  * Multi-worker thread execution via `<< REPLEKIA:<n>`
  * Error recovery via `over (.FALLBACK)` with `$ERR` injection
  * Loop controllers `doloop.C:<n>`, `doloop.UT`, `doloop.UTE`, `doloop.WHILE:"<cond>"`
* **Security, Middleware & State Snapshots:**
  * 9-bit binary permissions (`chmod` `"b111000000"`, `b000000000` full lock)
  * Method lifecycle control (`SUSPEND`, `RE=NATION`, `PURGER`)
  * Runtime middleware injection (`INSTALL` with `retry`, `delay`, `prefix`)
  * Step-by-step sandbox execution tracing (`DIVE = .../.` and `DIVE(method)`)
  * Full runtime state JSON snapshots (`HIBERNATION` `"save"` / `"load"`, including `Class::` and composite methods)
* **Extensible Without Editing Core (`exa/`):**
  * **Pure Exa Libraries:** `Include mylib/.` or `Include "libs/utils.exa"/.` with circular-include protection
  * **Native Python Plugins:** Auto-loaded from `plugins/<name>.py` (`EXA_MODULE`, `register(runtime)`, `@exa_tool`) and global `EXA_PATH` search paths
* **Interactive Multi-Line REPL:** Run `python run.py --repl` with bracket/block-aware multi-line continuation and meta-commands.

---

## Quick Start

### Requirements
* **Python 3.10+** (tested on **Python 3.12.0**, Windows 11)
* **Zero third-party dependencies** (standard library only)

### Installation & Running

```bash
git clone https://github.com/DeadProxy-Terin/exa.git
cd exa
```

Run the built-in verification demo:
```bash
python run.py
```

Execute an `.exa` script (or print its AST before executing):
```bash
python run.py test.exa
python run.py --ast test.exa
python run.py stress_core.exa
```

Start the interactive multi-line REPL:
```bash
python run.py --repl
```

Run the automated test suites:
```bash
python test_v03.py
python test_v04.py
```

---

## Language Examples

### 1. Lazy Stream Pipelines, Composite Methods & Infinite Fibonacci

```exa
// Composite method bundling FILTER -> CALC -> SUM
.METHOD_EVEN_SUM = (FILTER) > ("$IN % 2 == 0") >> (CALC) > ("$IN * $IN") >> (SUM)/.
EXEC = (SEQ) > (1, 10) >> (.METHOD_EVEN_SUM) >> (EMIT) > ("Sum of even squares:", $IN)/.

// Stateful infinite Fibonacci stream stopped cleanly after 8 items
EXEC = (STORE) > ("fib", [0, 1])/.
.METHOD_NEXT_FIB = (RECALL) > ("fib")
    >> (CALC) > ("[$IN[1], $IN[0] + $IN[1]]")
    >> (STORE) > ("fib")
    >> (CALC) > ("$IN[1] - $IN[0]")/.
EXEC = (.METHOD_NEXT_FIB << doloop.UTE) >> ((EMIT) << doloop.C:8)/.
```

Output:
```text
Sum of even squares: 220
0
1
1
2
3
5
8
13
```

### 2. Class Inheritance, Binary `chmod`, Error Recovery (`over`) & `DIVE`

```exa
#HYMN_CODE: SHURELIA_USER_ARCIEL/.
Include sys/.
includes.Ex_console/.

Class::REYVATEIL {
    .METHOD_SING = (EMIT) > ("Resonance ->", $IN)/.
};

Class::IPD extends.REYVATEIL {
    .METHOD_AMPLIFY = (CALC) > ("$IN * 10")/.
};

EXEC = (IPD.AMPLIFY) > (7) >> (IPD.SING)/.

.METHOD_LOCKED = (EMIT) > ("Tower Core Access")/.
.METHOD_DENIED = (console.warn) > ("Blocked -> $ERR")/.
EXEC = (sys.chmod) > ("b110000000", .METHOD_LOCKED)/.
EXEC = (.METHOD_LOCKED) over (.METHOD_DENIED)/.

DIVE = (CALC) > ("5 + 5") >> (CALC) > ("$IN * 3")/.
```

Output:
```text
Resonance -> 70
[WARN] Blocked -> Permission denied on '.METHOD_LOCKED' (chmod b110000000). Requires ANSUL rank or execute bit.
[DIVE Stage 1 | CALC | step=1] $IN=None -> OUT=10
[DIVE Stage 2 | CALC | step=1] $IN=10 -> OUT=30
```

### 3. Parallel Pipelines (`EXEC_with`) & Multi-Worker (`REPLEKIA`)

```exa
.METHOD_WORKER = (CALC) > ("$STEP * $STEP")/.
EXEC = (.METHOD_WORKER << REPLEKIA:4) >> (SUM) >> (EMIT) > ("Parallel sum of squares:", $IN)/.

.METHOD_A = (EMIT) > ("Task A step", $STEP)/.
.METHOD_B = (EMIT) > ("Task B step", $STEP)/.
EXEC_with = (.METHOD_A << doloop.C:2) & (.METHOD_B << doloop.C:2)/.
```

---

## Built-in Tools & Standard Modules

### Core Built-ins (32 Tools + 3 Control Keywords)
| Category | Tools |
| :--- | :--- |
| **Conversion & Format** | `FLIP`, `CODEC`, `FORMAT` |
| **I/O & Streams** | `EMIT`, `LISTEN`, `STREAM` (`read`, `read_all`, `write`, `append`) |
| **Math & Sequences** | `CALC`, `ABS`, `ROUND`, `SUM`, `MIN`, `MAX`, `SEQ` |
| **Collections & Filters** | `LEN`, `FILTER`, `SORT`, `SLICE`, `CHECK`, `GRATHMELD` |
| **Inspection & Security** | `TYPE`, `HASH`, `DIVE`, `INSTALL`, `LORE`, `chmod` |
| **Lifecycle, Memory & Routing** | `SUSPEND`, `RE=NATION`, `HIBERNATION`, `PURGER`, `STORE`, `RECALL`, `BRANCH` |
| **Language Controllers** | `doloop` (`C:<n>`, `UT`, `UTE`, `WHILE:<cond>`), `REPLEKIA:<n>`, `EXEC_with` / `over` |

### Standard Modules (`Include <mod>/.` or `includes.Ex_<mod>/.`)
* **`math`**: `count`, `sqrt`, `pow`, `sin`, `cos`, `pi`, `random`
* **`console`**: `log`, `warn`, `error`, `clear`
* **`sys`**: `chmod`, `info`, `hymn_code`, `env`, `sleep`
* **`net`**: `request_access`, `ping`, `http_get`
* **`time`**: `now`, `timestamp`, `sleep`

### Runtime Variables
* `$IN` — Current pipeline input item
* `$STEP` — 1-based iteration counter
* `$ERR` — Caught runtime error message inside an `over (.FALLBACK)` handler

---

## Extending Exa (Plugins & Libraries)

You can add new Exa tools and reusable libraries without modifying anything inside `exa/`.

### 1. Pure `.exa` Libraries
Place `.exa` files in your project root or `libs/` (or any directory listed in `EXA_PATH`), then import them:
```exa
Include my_utils/.
Include "libs/helpers.exa"/.
```

### 2. Python Plugins (`plugins/<name>.py`)
Plugins can define `EXA_MODULE`, `register(runtime)`, and use `@exa_tool(...)` to gain full built-in capabilities (`aggregator`, `prepend_in`, `passthrough`, `needs_env`, and `LORE` docs):

```python
# plugins/stats.py
from exa import exa_tool

@exa_tool(aggregator=True, doc="stats.avg(items) - Arithmetic mean of a stream.")
def avg(items):
    return sum(items) / len(items)

@exa_tool(prepend_in=True, doc="stats.wrap(val, left, right) - Wrap a value.")
def wrap(val, left="[", right="]"):
    return f"{left}{val}{right}"

EXA_MODULE = {
    "avg": avg,
    "wrap": wrap,
}

def register(runtime):
    runtime.register_tool(
        "AUDIT",
        lambda val: print(f"[AUDIT] {val}"),
        passthrough=True,
        doc="AUDIT(val) - Print and forward $IN.",
    )
```

---

## Interactive REPL (`--repl`)

```text
python run.py --repl
```

Supported REPL meta-commands:
* `:help` — Show help message
* `:methods` — List defined `.METHOD_*` slots, permissions, and installed plugins
* `:classes` — List defined `Class::` definitions and inheritance
* `:vars` — Inspect stored `STORE` / `RECALL` variables
* `:modules` — List included modules, available modules, and loaded files
* `:clear` — Clear terminal screen (`:reset` / `:clear state` resets the REPL runtime)
* `:cancel` — Cancel the current incomplete multi-line buffer
* `:exit` / `:quit` — Exit the REPL

---

## Project Structure

```text
.
├── exa/
│   ├── __init__.py       # Public entrypoint (run_exa, exa_tool)
│   ├── ast_nodes.py      # Token & AST dataclasses
│   ├── errors.py         # Custom Exa exceptions & SkipItem signal
│   ├── evaluator.py      # Safe AST expression evaluator
│   ├── interpreter.py    # Lazy-generator pipeline & concurrency engine
│   ├── lexer.py          # Tokenizer
│   ├── parser.py         # Recursive-descent parser
│   └── runtime.py        # Built-ins, stdlib, plugin/library loader, security
├── run.py                # CLI runner & multi-line REPL
├── test_v03.py           # v0.3 regression suite (7 tests)
├── test_v04.py           # v0.4 feature & plugin test suite (7 tests)
├── test.exa              # Basic test script
├── ping.exa              # Network ping test script
├── stress_core.exa       # Non-interactive stress test (5000-item stream, REPLEKIA:8, etc.)
└── stress_interactive.exa
```

---

## Known Issues (v0.4 — Contributions Welcome!)

This repository is an active early prototype. The following concrete issues and edge cases have been identified and are open for PRs or discussion:

1. **No short-circuit evaluation for `and` / `or` (`bug`, `good first issue`):**
   `SafeExprEvaluator` (`exa/evaluator.py`) evaluates all operands of `ast.BoolOp` eagerly, so expressions like `len($IN) > 0 and $IN[0] == 1` fail on empty lists (`[]`).
2. **`FORMAT` auto-prepend vs literal `"$IN"` & data re-interpolation (`bug`):**
   `EXEC = (SEQ) > (10, 12) >> (FORMAT) > ("step=$STEP:val=$IN") >> (EMIT)/.` fails because `_has_explicit_in_var()` only checks `VarNode`, causing `$IN` to be auto-prepended as a value into `format(10, "step=$STEP:val=$IN")`. Additionally, `_interpolate_vars()` can re-interpolate literal `"$IN"` text inside upstream values passed to `EMIT`.
3. **Single-stage method aliases and single-stage looped methods (`bug`):**
   `.METHOD_B = (.METHOD_A)/.` fails and `.METHOD_LOOP = ((EMIT) > ("hi", $STEP) << doloop.C:3)/.` ignores its loop because `exa/interpreter.py` only expands methods in pipeline mode when `len(stages) > 1`.
4. **Inherited `Class::Child` bypasses parent `chmod` / `SUSPEND` / `INSTALL` (`bug`, `security`):**
   Calling an un-overridden method via `Child.METHOD` checks `Child::.METHOD_*` rather than the owning `Parent::.METHOD_*` permissions/suspend/plugin keys.
5. **Composite streams with `INSTALL` / `Class::` lose laziness (`bug`, `performance`):**
   `_eval_composite_method()` materializes streams via `list(pkt.gen)`, which can hang on infinite `doloop.UTE` streams inside composite methods when `INSTALL` or `Class::` is used. `SafeExprEvaluator` also re-parses AST on every stream item (no `lru_cache`), and `ast.Pow` lacks an exponent guard.
6. **State isolation edge cases (`bug`):**
   * An included `.exa` library with `#HYMN_CODE` can overwrite the caller's rank if the main script omitted `#HYMN_CODE`.
   * `PURGER` on a `Class` removes `self.classes[name]` without clearing `ClassName::*` entries in `permissions`, `plugins`, and `suspended`.
   * Anonymous `.METHOD` used inside a composite `.METHOD_WRAP` is not recursively cleaned up after `EXEC`.
7. **REPL mid-buffer inspection (`good first issue`):**
   Typing `:vars` or `:methods` while in `... >` continuation mode currently appends it to the code buffer instead of executing the inspection command.

---

## Roadmap (Towards Standalone Exa v1.0)

* **v0.4.x:** Fix the known issues listed above, add `pyproject.toml` packaging (`exa` CLI command), and expand pure `.exa` conformance tests.
* **v0.5 (Language Purity):** Replace Python `ast.parse` in `SafeExprEvaluator` with Exa's own native expression parser, separate `MethodRefNode` from string literals in the AST, and enforce `r`/`w` bits in `chmod`.
* **v0.6+ (Standalone Native Engine):** Port the Lexer, Parser, Bytecode Compiler, and Lazy-Stream VM to a systems language (e.g., **Rust** or **Zig**) with C-ABI / WASM native plugins—removing Python as a runtime dependency completely.

---

## Contributing & License

Feedback, bug reports, issue discussions, and pull requests are very welcome!
Created by **Proxy** ([@DeadProxy-Terin](https://github.com/DeadProxy-Terin)).