"""
Unit & regression tests for SafeExprEvaluator (exa/evaluator.py).

Primary focus — GitHub issue #1:
    Boolean 'and' / 'or' operators in SafeExprEvaluator must short-circuit.
    The right-hand side must never be evaluated when the left-hand side
    already decides the result (e.g. `false and <erroring>` must return
    False instead of raising, `true or <erroring>` must return True).

Run from the project root:
    python -m unittest test_evaluator -v
    # or
    python test_evaluator.py
"""

import ast
import unittest

from exa import ExaRuntimeError, run_exa
from exa.evaluator import SafeExprEvaluator


def ev(expr, env=None):
    """Evaluate an expression string with an optional env dict."""
    return SafeExprEvaluator.evaluate(expr, env or {})


class TestAndShortCircuit(unittest.TestCase):
    """'and' must stop at the first falsy operand (issue #1)."""

    def test_issue_repro_empty_list_indexing(self):
        # Exact expression from the issue: RHS indexes an empty list,
        # which must never be evaluated because the LHS is already False.
        self.assertFalse(ev("len($IN) > 0 and $IN[0] == 1", {"$IN": []}))

    def test_false_lhs_skips_division_by_zero_rhs(self):
        self.assertFalse(ev("false and (1 / 0 > 0)"))

    def test_zero_lhs_skips_erroring_rhs(self):
        self.assertFalse(ev("0 and $IN[0] == 1", {"$IN": []}))

    def test_false_lhs_skips_unknown_variable_rhs(self):
        self.assertFalse(ev("false and no_such_var > 0"))

    def test_false_lhs_skips_disallowed_call_rhs(self):
        self.assertFalse(ev("false and open('/etc/passwd')"))

    def test_falsy_string_lhs_skips_erroring_rhs(self):
        self.assertEqual(ev("'' and $IN[0] == 1", {"$IN": []}), "")

    def test_truthy_lhs_still_evaluates_rhs_and_raises(self):
        # When the LHS does NOT decide the result, the RHS must still run
        # and its error must propagate to the caller.
        with self.assertRaises(ExaRuntimeError):
            ev("true and $IN[0] == 1", {"$IN": []})
        with self.assertRaises(ExaRuntimeError):
            ev("1 and (1 / 0 > 0)")


class TestOrShortCircuit(unittest.TestCase):
    """'or' must stop at the first truthy operand (issue #1)."""

    def test_true_lhs_skips_empty_list_indexing_rhs(self):
        self.assertTrue(ev("true or $IN[0] == 1", {"$IN": []}))

    def test_true_lhs_skips_division_by_zero_rhs(self):
        self.assertTrue(ev("true or (1 / 0 > 0)"))

    def test_nonzero_lhs_skips_erroring_rhs(self):
        self.assertTrue(ev("5 or $IN[0] == 1", {"$IN": []}))

    def test_true_lhs_skips_unknown_variable_rhs(self):
        self.assertTrue(ev("true or no_such_var > 0"))

    def test_true_lhs_skips_disallowed_call_rhs(self):
        self.assertTrue(ev("true or open('/etc/passwd')"))

    def test_falsy_lhs_still_evaluates_rhs_and_raises(self):
        with self.assertRaises(ExaRuntimeError):
            ev("false or $IN[0] == 1", {"$IN": []})
        with self.assertRaises(ExaRuntimeError):
            ev("0 or (1 / 0 > 0)")


class TestBoolOpTruthTables(unittest.TestCase):
    """Full truth tables for two- and three-operand chains."""

    def test_and_two_operands(self):
        for a in (True, False):
            for b in (True, False):
                expr = "%s and %s" % (str(a).lower(), str(b).lower())
                self.assertIs(ev(expr), a and b, expr)

    def test_or_two_operands(self):
        for a in (True, False):
            for b in (True, False):
                expr = "%s or %s" % (str(a).lower(), str(b).lower())
                self.assertIs(ev(expr), a or b, expr)

    def test_and_three_operands(self):
        combos = [
            (True, True, True),
            (True, True, False),
            (True, False, True),
            (False, True, True),
            (False, False, False),
        ]
        for bits in combos:
            expr = " and ".join(str(b).lower() for b in bits)
            self.assertIs(ev(expr), all(bits), expr)

    def test_or_three_operands(self):
        combos = [
            (False, False, False),
            (False, False, True),
            (False, True, False),
            (True, False, False),
            (True, True, True),
        ]
        for bits in combos:
            expr = " or ".join(str(b).lower() for b in bits)
            self.assertIs(ev(expr), any(bits), expr)

    def test_mixed_precedence_and_binds_tighter_than_or(self):
        self.assertTrue(ev("true or false and false"))  # true or (false and false)
        self.assertFalse(ev("(true or false) and false"))

    def test_comparisons_inside_boolops(self):
        self.assertTrue(ev("$IN > 1 and $IN < 10", {"$IN": 5}))
        self.assertFalse(ev("$IN > 1 and $IN < 10", {"$IN": 50}))
        self.assertTrue(ev("$IN < 0 or $IN > 100", {"$IN": 500}))
        self.assertFalse(ev("$IN < 0 or $IN > 100", {"$IN": 50}))

    def test_chained_comparison_inside_boolop(self):
        self.assertTrue(ev("0 < $IN < 10 and $IN != 5", {"$IN": 3}))
        self.assertFalse(ev("0 < $IN < 10 and $IN != 5", {"$IN": 5}))

    def test_not_operator_combined_with_boolops(self):
        self.assertTrue(ev("not false and true"))
        self.assertFalse(ev("not (true or true)"))
        self.assertTrue(ev("not false or $IN[0] == 1", {"$IN": []}))
        self.assertFalse(ev("not true and $IN[0] == 1", {"$IN": []}))


class TestNonBooleanOperands(unittest.TestCase):
    """Python semantics: 'and'/'or' yield an operand, not a coerced bool."""

    def test_and_returns_last_operand_when_all_truthy(self):
        self.assertEqual(ev("1 and 2"), 2)
        self.assertEqual(ev("'a' and 'b'"), "b")
        self.assertEqual(ev("[1] and [2]"), [2])

    def test_and_returns_first_falsy_operand(self):
        self.assertEqual(ev("0 and 99"), 0)
        self.assertEqual(ev("'' and 'x'"), "")
        self.assertEqual(ev("3 and 0 and $IN[0]", {"$IN": []}), 0)

    def test_or_returns_first_truthy_operand(self):
        self.assertEqual(ev("0 or 5"), 5)
        self.assertEqual(ev("'' or 'fallback'"), "fallback")
        self.assertEqual(ev("0 or 0 or 7"), 7)

    def test_or_returns_last_operand_when_all_falsy(self):
        self.assertEqual(ev("0 or '' or 0.0"), 0.0)

    def test_short_circuit_with_non_boolean_lhs(self):
        self.assertEqual(ev("0 and $IN[0]", {"$IN": []}), 0)
        self.assertEqual(ev("'yes' or $IN[0]", {"$IN": []}), "yes")

    def test_bool_helper_still_works_on_results(self):
        self.assertIs(ev("bool(0 or 5)"), True)
        self.assertIs(ev("bool(0 and 5)"), False)


class TestNestedBoolOps(unittest.TestCase):
    """Short-circuiting must work through parentheses and nesting."""

    def test_parenthesized_nesting(self):
        self.assertTrue(ev("(true or false) and (false or true)"))
        self.assertFalse(ev("(true and false) or (false and true)"))
        self.assertTrue(ev("true and (false or true)"))
        self.assertFalse(ev("false or (true and false)"))

    def test_short_circuit_inside_nested_rhs(self):
        # Outer 'and' has a falsy LHS → the whole nested RHS (which would
        # raise) must never be evaluated.
        self.assertFalse(ev("false and (true or $IN[0] == 1)", {"$IN": []}))
        self.assertTrue(ev("true or (false and $IN[0] == 1)", {"$IN": []}))

    def test_deeply_nested_short_circuit(self):
        self.assertFalse(ev("false and true and (1 / 0 > 0 or $IN[9])", {"$IN": []}))
        self.assertTrue(ev("true or false or (1 / 0 > 0 and $IN[9])", {"$IN": []}))

    def test_boolop_inside_ternary(self):
        self.assertEqual(ev("'A' if false and $IN[0] else 'B'", {"$IN": []}), "B")
        self.assertEqual(ev("'A' if true or $IN[0] else 'B'", {"$IN": []}), "A")

    def test_boolop_result_feeds_safe_calls(self):
        self.assertEqual(ev("len([]) == 0 and 'empty' or 'nonempty'"), "empty")
        self.assertEqual(ev("len([1]) == 0 and 'empty' or 'nonempty'"), "nonempty")
        self.assertTrue(ev("len([1, 2]) == 2 and bool(1) or false"))


class TestEvaluationCount(unittest.TestCase):
    """Prove via a spy that short-circuited RHS nodes are never evaluated."""

    def _eval_counting_names(self, expr, env=None):
        seen = []
        original = SafeExprEvaluator._eval_node  # bound classmethod

        def spy(cls, node, env_):
            if isinstance(node, ast.Name):
                seen.append(node.id)
            return original(node, env_)

        SafeExprEvaluator._eval_node = classmethod(spy)
        try:
            result = SafeExprEvaluator.evaluate(expr, env or {})
        finally:
            SafeExprEvaluator._eval_node = original
        return result, seen

    def test_and_with_falsy_lhs_never_evaluates_rhs(self):
        result, seen = self._eval_counting_names("false and $IN == 1", {"$IN": []})
        self.assertFalse(result)
        self.assertNotIn("__HS_IN__", seen)

    def test_and_with_truthy_lhs_evaluates_rhs(self):
        result, seen = self._eval_counting_names("true and $IN == 1", {"$IN": 1})
        self.assertTrue(result)
        self.assertIn("__HS_IN__", seen)

    def test_or_with_truthy_lhs_never_evaluates_rhs(self):
        result, seen = self._eval_counting_names("true or $IN == 1", {"$IN": []})
        self.assertTrue(result)
        self.assertNotIn("__HS_IN__", seen)

    def test_or_with_falsy_lhs_evaluates_rhs(self):
        result, seen = self._eval_counting_names("false or $IN == 1", {"$IN": 1})
        self.assertTrue(result)
        self.assertIn("__HS_IN__", seen)

    def test_three_operand_and_stops_at_first_falsy(self):
        result, seen = self._eval_counting_names(
            "true and false and $IN == 1", {"$IN": []}
        )
        self.assertFalse(result)
        self.assertNotIn("__HS_IN__", seen)


class TestIssueEndToEnd(unittest.TestCase):
    """End-to-end pipelines through the interpreter (the issue's repro)."""

    def _run(self, src):
        outputs = []
        run_exa(src, output_fn=outputs.append)
        return outputs

    def test_issue_filter_repro_filters_empty_list_cleanly(self):
        # Exact pipeline from the issue: must not crash, item is filtered out.
        out = self._run(
            'EXEC = (CALC) > ([]) >> (FILTER) > ("len($IN) > 0 and $IN[0] == 1") >> (EMIT)/.'
        )
        self.assertEqual(out, [])

    def test_filter_keeps_matching_list(self):
        out = self._run(
            'EXEC = (CALC) > ([1]) >> (FILTER) > ("len($IN) > 0 and $IN[0] == 1") >> (EMIT)/.'
        )
        self.assertEqual(out, ["[1]"])

    def test_filter_or_short_circuit(self):
        out = self._run(
            'EXEC = (CALC) > ([]) >> (FILTER) > ("len($IN) == 0 or $IN[0] == 1") >> (EMIT)/.'
        )
        self.assertEqual(out, ["[]"])

    def test_filter_over_stream_with_and(self):
        out = self._run(
            'EXEC = (SEQ) > (1, 5) >> (FILTER) > ("$IN % 2 == 0 and $IN > 2") >> (EMIT)/.'
        )
        self.assertEqual(out, ["4"])

    def test_branch_with_and_short_circuit(self):
        out = self._run(
            'EXEC = (CALC) > ([]) >> (BRANCH) > ("len($IN) > 0 and $IN[0] == 1", "yes", "no") >> (EMIT)/.'
        )
        self.assertEqual(out, ["no"])

    def test_branch_with_or_short_circuit(self):
        out = self._run(
            'EXEC = (CALC) > ([]) >> (BRANCH) > ("len($IN) == 0 or $IN[0] == 1", "yes", "no") >> (EMIT)/.'
        )
        self.assertEqual(out, ["yes"])

    def test_check_with_and_short_circuit(self):
        # CHECK evaluates the condition per item; for small ints the RHS
        # `$IN[0]` would raise if eagerly evaluated.
        out = self._run(
            'EXEC = (CALC) > ([1, 2]) >> (CHECK) > ("all", "$IN > 5 and $IN[0] == 1") >> (EMIT)/.'
        )
        self.assertEqual(out, ["false"])

    def test_check_all_with_truthy_and(self):
        out = self._run(
            'EXEC = (CALC) > ([1, 2]) >> (CHECK) > ("all", "$IN > 0 and $IN < 10") >> (EMIT)/.'
        )
        self.assertEqual(out, ["true"])

    def test_while_loop_with_and_short_circuit(self):
        # At the final iteration CALC yields [], so the LHS `len($IN) > 0`
        # is False and the erroring RHS `$IN[0] < 6` must be skipped —
        # under the eager evaluator this crashes with "list index out of
        # range" and the loop never terminates cleanly.
        out = self._run(
            'EXEC = ((CALC) > ("[] if $STEP >= 3 else [$STEP * 2]") '
            '<< doloop.WHILE:"len($IN) > 0 and $IN[0] < 6") >> (EMIT)/.'
        )
        self.assertEqual(out, ["[2]", "[4]"])

    def test_calc_returns_operand_for_non_boolean_and_or(self):
        self.assertEqual(self._run('EXEC = (CALC) > ("1 and 2") >> (EMIT)/.'), ["2"])
        self.assertEqual(self._run('EXEC = (CALC) > ("0 or 5") >> (EMIT)/.'), ["5"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
