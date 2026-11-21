"""
Automated regression & feature test suite for Exa v0.4.
Covers:
  - v0.3 -> v0.4 bug fixes (STREAM read + over + handle close, INSTALL/$STEP on composites, FLIP bool, Class DIVE)
  - Extension Door 1: Pure .exa libraries (local, libs/*.exa, nested & circular includes, recovery)
  - Extension Door 2: Native Python plugins (EXA_MODULE, register(runtime), @exa_tool, EXA_PATH)
  - Programmatic Runtime registration (register_tool, register_module, aggregator/prepend_in/passthrough/needs_env/doc)
  - Interactive REPL (--repl) & relative base_dir CLI execution
Run from project root:
  python test_v04.py
"""

import os
import subprocess
import sys
import tempfile
import unittest

from exa import (
    ExaError,
    ExaRuntimeError,
    Interpreter,
    Lexer,
    Parser,
    Runtime,
    run_exa,
)


class TestExaV04(unittest.TestCase):
    def _run(self, src: str, base_dir=None):
        outputs = []
        rt = run_exa(src, output_fn=outputs.append, base_dir=base_dir)
        return outputs, rt

    def test_stream_read_error_recovery_and_deterministic_close(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = os.path.join(tmpdir, "does_not_exist.txt").replace("\\", "/")
            out, _ = self._run(f"""
            .METHOD_FB = (EMIT) > ("Recovered: $ERR")/.
            EXEC = (STREAM) > ("{missing}", "read") >> (EMIT) over (.METHOD_FB)/.
            """)
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].startswith("Recovered: Tool 'STREAM' failed"))

            bad_utf8 = os.path.join(tmpdir, "bad_utf8.txt")
            with open(bad_utf8, "wb") as bf:
                bf.write(b"ok\n\xff\xfeinvalid\n")
            bad_path = bad_utf8.replace("\\", "/")
            out2, _ = self._run(f"""
            .METHOD_FB = (EMIT) > ("Stream err: $ERR")/.
            EXEC = (STREAM) > ("{bad_path}", "read") >> (EMIT) over (.METHOD_FB)/.
            """)
            self.assertTrue(
                any(line.startswith("Stream err: Tool 'STREAM' failed") for line in out2)
            )

            data_file = os.path.join(tmpdir, "lines.txt")
            with open(data_file, "w", encoding="utf-8") as df:
                df.write("L1\nL2\nL3\nL4\n")
            data_path = data_file.replace("\\", "/")
            out3, _ = self._run(f"""
            EXEC = (STREAM) > ("{data_path}", "read") >> ((EMIT) << doloop.C:2)/.
            """)
            self.assertEqual(out3, ["L1", "L2"])
            os.remove(data_file)

    def test_composite_install_step_and_class_dive(self):
        out, _ = self._run("""
        .METHOD_COMP = (CALC) > ("$IN * 2") >> (FORMAT) > ("num={}")/.
        EXEC = (INSTALL) > (.METHOD_COMP, {"prefix": "[TAG]"})/.
        EXEC = (CALC) > ("7") >> (.METHOD_COMP) >> (EMIT)/.

        Class::Bot {
            .METHOD_PIPE = (CALC) > ("$IN * 2") >> (FORMAT) > ("Val={}")/.
            .METHOD_STEP = (CALC) > ("$STEP") >> (FORMAT) > ("c={}")/.
        };
        EXEC = (DIVE) > (Bot.PIPE)/.
        EXEC = (INSTALL) > (Bot.PIPE, {"prefix": "[BOT]"})/.
        EXEC = (Bot.PIPE) > (5) >> (EMIT)/.
        EXEC = (Bot.STEP << doloop.C:3) >> (EMIT)/.

        .METHOD_STEP_COMP = (CALC) > ("$STEP") >> (FORMAT) > ("s={}")/.
        EXEC = (.METHOD_STEP_COMP << doloop.C:3) >> (EMIT)/.
        """)
        self.assertEqual(out[0], "[TAG] num=14")
        self.assertIn('"tool": "CALC >> FORMAT"', out[1])
        self.assertEqual(out[2], "[BOT] Val=10")
        self.assertEqual(out[3:6], ["c=1", "c=2", "c=3"])
        self.assertEqual(out[6:9], ["s=1", "s=2", "s=3"])

    def test_flip_bool_independent_semantics(self):
        out, _ = self._run("""
        EXEC = (FLIP) > ("false", "bool") >> (EMIT)/.
        EXEC = (FLIP) > ("FALSE", "bool") >> (EMIT)/.
        EXEC = (FLIP) > ("0", "bool") >> (EMIT)/.
        EXEC = (FLIP) > ("", "bool") >> (EMIT)/.
        EXEC = (FLIP) > ("true", "bool") >> (EMIT)/.
        EXEC = (FLIP) > ("hello", "bool") >> (EMIT)/.
        """)
        self.assertEqual(out, ["false", "false", "false", "false", "true", "true"])

    def test_pure_exa_libraries_and_circular_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            libs_dir = os.path.join(tmpdir, "libs")
            os.makedirs(libs_dir)
            with open(os.path.join(tmpdir, "local_math.exa"), "w", encoding="utf-8") as f:
                f.write("""
                .METHOD_SQ = (CALC) > ("$IN * $IN")/.
                Class::Fmt {
                    .METHOD_BOX = (FORMAT) > ("[{}]")/.
                };
                """)
            with open(os.path.join(libs_dir, "a.exa"), "w", encoding="utf-8") as f:
                f.write('Include "b.exa"/.\n.METHOD_A = (CALC) > ("$IN + 100")/.')
            with open(os.path.join(libs_dir, "b.exa"), "w", encoding="utf-8") as f:
                f.write('Include "a.exa"/.\n.METHOD_B = (CALC) > ("$IN * 2")/.')

            out, _ = self._run("""
            #HYMN_CODE: SHURELIA_ANSUL_TOWER-01/.
            Include sys/.
            Include local_math/.
            Include "libs/a.exa"/.
            EXEC = (CALC) > ("5") >> (.METHOD_SQ) >> (Fmt.BOX) >> (EMIT)/.
            EXEC = (CALC) > ("4") >> (.METHOD_B) >> (.METHOD_A) >> (EMIT)/.
            EXEC = (sys.hymn_code) >> (EMIT)/.
            """, base_dir=tmpdir)
            self.assertEqual(out[0], "[25]")
            self.assertEqual(out[1], "108")
            self.assertIn('"rank": "ANSUL"', out[2])

    def test_broken_exa_library_can_be_fixed_and_reloaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_path = os.path.join(tmpdir, "fixable.exa")
            with open(lib_path, "w", encoding="utf-8") as f:
                f.write("EXEC = (EMIT) > (1")
            out = []
            rt = Runtime(output_fn=out.append, base_dir=tmpdir)
            with self.assertRaises(ExaError):
                Interpreter(rt).run(Parser(Lexer('Include "fixable.exa"/.').tokenize()).parse())
            with open(lib_path, "w", encoding="utf-8") as f:
                f.write('.METHOD_OK = (EMIT) > ("reloaded!")/.')
            Interpreter(rt).run(
                Parser(Lexer('Include "fixable.exa"/. EXEC = (.METHOD_OK)/.').tokenize()).parse()
            )
            self.assertEqual(out, ["reloaded!"])

    def test_python_plugins_exa_path_and_full_capabilities(self):
        with tempfile.TemporaryDirectory() as proj_dir, tempfile.TemporaryDirectory() as ext_dir:
            os.makedirs(os.path.join(ext_dir, "plugins"))
            os.makedirs(os.path.join(ext_dir, "libs"))

            with open(os.path.join(ext_dir, "libs", "ext_helpers.exa"), "w", encoding="utf-8") as f:
                f.write('.METHOD_DBL = (CALC) > ("$IN * 2")/.')

            with open(os.path.join(ext_dir, "plugins", "supertools.py"), "w", encoding="utf-8") as f:
                f.write("""
from exa import exa_tool

AUDIT_LOG = []

@exa_tool(aggregator=True, doc="supertools.avg(items) - Average of numeric stream.")
def avg(items):
    return sum(items) / len(items)

@exa_tool(prepend_in=True, doc="supertools.wrap(val, left, right) - Wrap text.")
def wrap(val, left="[", right="]"):
    return f"{left}{val}{right}"

@exa_tool(passthrough=True, prepend_in=True)
def audit(val, label="LOG"):
    AUDIT_LOG.append(f"{label}:{val}")

EXA_MODULE = {
    "avg": avg,
    "wrap": wrap,
    "audit": audit,
    "get_audit": lambda: list(AUDIT_LOG),
}

def register(runtime):
    runtime.register_tool(
        "AVG",
        lambda items: sum(items) / len(items),
        aggregator=True,
        doc="AVG(items) - Global average aggregator.",
    )
    runtime.register_tool(
        "REPLACE_TXT",
        lambda val, old, new: str(val).replace(str(old), str(new)),
        prepend_in=True,
    )
    runtime.register_tool(
        "TEE_VAL",
        lambda val: runtime.memory_vars.__setitem__("last_tee", val),
        passthrough=True,
    )
    runtime.register_tool(
        "ENV_INFO",
        lambda label, _env=None: f"{label}:in={_env.get('$IN')},step={_env.get('$STEP')}",
        needs_env=True,
    )
""")

            prev_exa_path = os.environ.get("EXA_PATH")
            os.environ["EXA_PATH"] = ext_dir
            try:
                out, rt = self._run("""
                Include ext_helpers/.
                includes.Ex_supertools/.

                EXEC = (SEQ) > (1, 5) >> (AVG) >> (EMIT)/.
                EXEC = (SEQ) > (10, 30, 10) >> (supertools.avg) >> (EMIT)/.
                EXEC = (CALC) > ("'hello world'") >> (REPLACE_TXT) > ("world", "exa") >> (TEE_VAL) >> (supertools.wrap) > ("<", ">") >> (supertools.audit) > ("CHK") >> (.METHOD_DBL) >> (EMIT)/.
                EXEC = (supertools.get_audit) >> (EMIT)/.
                EXEC = (SEQ) > (10, 11) >> (ENV_INFO) > ("IT") >> (EMIT)/.
                EXEC = (LORE) > ("AVG")/.
                EXEC = (LORE) > ("supertools.wrap")/.
                """, base_dir=proj_dir)
                self.assertEqual(out[0], "3.0")
                self.assertEqual(out[1], "20.0")
                self.assertEqual(out[2], "<hello exa><hello exa>")
                self.assertEqual(out[3], '["CHK:<hello exa>"]')
                self.assertEqual(out[4:6], ["IT:in=10,step=1", "IT:in=11,step=2"])
                self.assertEqual(out[6], "[LORE] AVG(items) - Global average aggregator.")
                self.assertEqual(out[7], "[LORE] supertools.wrap(val, left, right) - Wrap text.")
                self.assertEqual(rt.memory_vars.get("last_tee"), "hello exa")
            finally:
                if prev_exa_path is None:
                    os.environ.pop("EXA_PATH", None)
                else:
                    os.environ["EXA_PATH"] = prev_exa_path

    def test_cli_subdir_base_dir_and_repl_session(self):
        repo_root = os.path.dirname(os.path.abspath(__file__))
        run_py = os.path.join(repo_root, "run.py")
        if not os.path.isfile(run_py):
            self.skipTest("run.py not found in test root")

        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "sub")
            os.makedirs(sub)
            with open(os.path.join(sub, "sublib.exa"), "w", encoding="utf-8") as f:
                f.write('.METHOD_GREET = (EMIT) > ("Subdir OK:", $IN)/.')
            app = os.path.join(sub, "app.exa")
            with open(app, "w", encoding="utf-8") as f:
                f.write('Include "sublib.exa"/.\nEXEC = (CALC) > ("77") >> (.METHOD_GREET)/.')

            p_cli = subprocess.run(
                [sys.executable, run_py, app],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(p_cli.returncode, 0, p_cli.stderr)
            self.assertEqual(p_cli.stdout.strip(), "Subdir OK: 77")

        repl_script = "\n".join([
            "help",
            "invalid_starter",
            "Include math/.",
            "Class::Aria {",
            '    .METHOD_SING = (CALC) > ("$IN * 2")',
            '        >> (FORMAT) > ("R={}")/.',
            '    .METHOD_SHOW = (EMIT) > ("Aria:", $IN)/.',
            "};",
            "EXEC = (Aria.SING) > (21)",
            "    >> (Aria.SHOW)/.",
            'EXEC = (CALC) > ("99") >> (STORE) > ("hp")/.',
            "EXEC = (UNKNOWN_TOOL)/.",
            'EXEC = (RECALL) > ("hp") >> (EMIT)/.',
            ":methods",
            ":classes",
            ":vars",
            ":modules",
            ":reset",
            ":vars",
            ":exit",
        ]) + "\n"

        p_repl = subprocess.run(
            [sys.executable, run_py, "--repl"],
            cwd=repo_root,
            input=repl_script,
            capture_output=True,
            text=True,
        )
        self.assertEqual(p_repl.returncode, 0, p_repl.stderr)
        self.assertIn("[Hint] Use ':help' for REPL commands", p_repl.stdout)
        self.assertIn("Unexpected token 'invalid_starter'", p_repl.stdout)
        self.assertIn("Aria: R=42", p_repl.stdout)
        self.assertIn("[Exa Error] Unknown built-in tool 'UNKNOWN_TOOL'", p_repl.stdout)
        self.assertIn("99", p_repl.stdout)
        self.assertIn("Class::Aria -> [.METHOD_SING, .METHOD_SHOW]", p_repl.stdout)
        self.assertIn("[REPL] Runtime and Interpreter state reset.", p_repl.stdout)
        self.assertIn("(no stored variables)", p_repl.stdout)
        self.assertIn("Exiting Exa REPL.", p_repl.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)