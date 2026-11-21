"""
Comprehensive automated regression & feature test suite for Exa v0.3.
Run from project root:
  python test_v03.py
"""

import os
import tempfile
import unittest
from exa import run_exa


class TestExaV03Full(unittest.TestCase):
    def _run(self, src: str):
        outputs = []
        rt = run_exa(src, output_fn=lambda msg: outputs.append(msg))
        return outputs, rt

    def test_v021_regressions(self):
        out, _ = self._run("""
        EXEC = (SEQ) > (1, 10) >> (FILTER) > ("$IN % 2 == 0") >> ((EMIT) << doloop.C:2)/.
        EXEC = (SEQ) > (1, 5) >> (SORT) > ("desc") >> (EMIT)/.
        EXEC = (SEQ) > (1, 5) >> (SLICE) > (1, 4) >> (EMIT)/.
        EXEC = (SEQ) > (1, 5) >> (CHECK) > ("all", "$IN > 0") >> (EMIT)/.
        EXEC_with = ((SEQ) > (1, 2) >> (EMIT)) & ((SEQ) > (3, 4) >> (EMIT))/.
        """)
        self.assertEqual(out[:4], ["2", "4", "[5, 4, 3, 2, 1]", "[2, 3, 4]"])
        self.assertEqual(out[4], "true")
        self.assertEqual(sorted(out[5:]), ["1", "2", "3", "4"])

    def test_bug1_format_and_grathmeld_prepend_and_varargs(self):
        out, _ = self._run("""
        EXEC = (CALC) > ("'hello'") >> (FORMAT) > ("upper") >> (EMIT)/.
        EXEC = (CALC) > ("'world'") >> (FORMAT) > ("Hello {}") >> (EMIT)/.
        EXEC = (FORMAT) > ("{} + {} = {}", 1, 2, 3) >> (EMIT)/.
        EXEC = (CALC) > ("10") >> (FORMAT) > ("{} + {} = {}", $IN, 2, 12) >> (EMIT)/.
        EXEC = (CALC) > ([1, 2]) >> (GRATHMELD) > ([3, 4]) >> (EMIT)/.
        """)
        self.assertEqual(out, ["HELLO", "Hello world", "1 + 2 = 3", "10 + 2 = 12", "[[1, 3], [2, 4]]"])

    def test_bug2_while_single_value_and_root_in(self):
        out, _ = self._run("""
        EXEC = (CALC) > ("10") >> ((EMIT) > ("Step:", $STEP, "Val:", $IN) << doloop.WHILE:"$STEP <= 3")/.
        EXEC = ((CALC) > ("$STEP * 2") << doloop.WHILE:"$IN < 6") >> (EMIT)/.
        EXEC = ((CALC) > ("$STEP * 10") << doloop.WHILE:"$STEP <= 0") >> (EMIT)/.
        """)
        self.assertEqual(out, ["Step: 1 Val: 10", "Step: 2 Val: 10", "Step: 3 Val: 10", "2", "4"])

    def test_bug3_grouped_pipeline_followed_by_pipe(self):
        out, _ = self._run('EXEC = ((SEQ) > (1, 3) >> (SUM)) >> (EMIT)/.')
        self.assertEqual(out, ["6"])

    def test_bug4_class_hibernation_and_security(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snap = os.path.join(tmpdir, "cls_snap.json").replace("\\", "/")
            out1, _ = self._run(f"""
            Class::Hero {{
                .METHOD_HI = (FORMAT) > ("hero ready", "upper")/.
            }};
            .METHOD_BLOCKED = (EMIT) > ("Blocked: $ERR")/.
            EXEC = (INSTALL) > (Hero.HI, {{"prefix": "[HERO]"}})/.
            EXEC = (chmod) > ("b110000000", Hero.HI)/.
            EXEC = (Hero.HI) over (.METHOD_BLOCKED)/.
            EXEC = (chmod) > ("b111000000", Hero.HI)/.
            EXEC = (SUSPEND) > (Hero.HI)/.
            EXEC = (DIVE) > (Hero.HI)/.
            EXEC = (HIBERNATION) > ("save", "{snap}")/.
            """)
            self.assertTrue(out1[0].startswith("Blocked: Permission denied"))
            self.assertIn('"suspended": true', out1[1])

            out2, _ = self._run(f"""
            EXEC = (HIBERNATION) > ("load", "{snap}")/.
            EXEC = (Hero.HI) over (.METHOD_BLOCKED)/.
            EXEC = (RE=NATION) > (Hero.HI)/.
            EXEC = (Hero.HI) >> (EMIT)/.
            """)
            self.assertTrue(out2[0].startswith("Blocked: Method"))
            self.assertEqual(out2[1], "[HERO] HERO READY")

    def test_bug5_anon_method_race_free_in_exec_with(self):
        for _ in range(10):
            out, rt = self._run("""
            .METHOD = (EMIT) > ("anon")/.
            EXEC_with = (.METHOD) & (.METHOD) & (.METHOD)/.
            """)
            self.assertEqual(out, ["anon", "anon", "anon"])
            self.assertNotIn(".METHOD", rt.methods)

    def test_v03_upgrades(self):
        out, rt = self._run("""
        #HYMN_CODE: SHURELIA_USER_TOWER-01/.

        // 1. Subscript + Ternary + Safe functions in CALC/FILTER
        EXEC = (GRATHMELD) > ({"hp": 80}, {"mp": 20}) >> (FILTER) > ("$IN['hp'] > 50 and len($IN) == 2") >> (CALC) > ("'STRONG' if $IN['hp'] >= 80 else 'WEAK'") >> (EMIT)/.
        EXEC = (CALC) > ("[10, 20, 30][1]") >> (EMIT)/.

        // 2. Composite multi-stage .METHOD_X (standalone + inside Class)
        .METHOD_CLEAN_SUM = (FILTER) > ("$IN > 0") >> (SUM)/.
        EXEC = (SEQ) > (-3, 5) >> (.METHOD_CLEAN_SUM) >> (EMIT)/.

        Class::MathBot {
            .METHOD_DOUBLE_FMT = (CALC) > ("$IN * 2") >> (FORMAT) > ("Val={}")/.
        };
        EXEC = (MathBot.DOUBLE_FMT) > (21) >> (EMIT)/.

        // 3. BRANCH conditional router (both literal values and .METHOD calls!)
        .METHOD_WIN  = (CALC) > ("$IN + 10")/.
        .METHOD_LOSE = (CALC) > ("$IN - 10")/.
        EXEC = (CALC) > ("90") >> (BRANCH) > ("$IN >= 75", "PASS", "FAIL") >> (EMIT)/.
        EXEC = (CALC) > ("90") >> (BRANCH) > ("$IN >= 75", .METHOD_WIN, .METHOD_LOSE) >> (EMIT)/.
        EXEC = (CALC) > ("50") >> (BRANCH) > ("$IN >= 75", .METHOD_WIN, .METHOD_LOSE) >> (EMIT)/.
        """)
        self.assertEqual(rt.hymn_code.raw_code, "SHURELIA_USER_TOWER-01")
        self.assertEqual(rt.hymn_code.server, "TOWER-01")
        self.assertEqual(out, ["STRONG", "20", "15", "Val=42", "PASS", "100", "40"])


if __name__ == "__main__":
    unittest.main(verbosity=2)