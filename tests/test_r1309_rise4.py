"""R-1309 round 7: inside the same reset window, while a typed figure is kept, a lower reading from a non-typed source never replaces the
kept figure and is never taken as a reset (only a new reset time is a reset); a sub-limit reset stamp that is not a number is dropped;
the source names "typed" and "host limits" are fixed in the code, never an option of a command."""
import os
import unittest
from helpers import StudioTestCase, ROOT

HOUR = 3600
DAY = 86400
T = 1_800_000_000.0
RESETS = T + 0.15 * 7 * DAY


class LowerReadingTest(StudioTestCase):
    def week(self):
        import budget
        return budget.load()["limits"]["seven_day"]

    def typed_10_then_55(self):
        import budget
        budget.record(seven_day=(10, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(55, RESETS), now=T + 600, source="host limits")      # held: far above the typed figure
        budget.record(seven_day=(55, RESETS), now=T + 700, source="statusline")      # the host sent it too: a rise
        self.assertEqual(self.week()["used"], 55.0)

    def test_r1309_a_stale_30_after_a_taken_55_never_replaces_it_and_sets_no_reset_marker(self):
        import budget
        self.typed_10_then_55()
        budget.record(seven_day=(30, RESETS), now=T + 800, source="statusline")
        self.assertEqual(self.week()["used"], 55.0)
        self.assertNotIn("reset_from", self.week())
        self.assertNotIn("reset_at", self.week())

    def test_r1309_a_correct_55_after_the_stale_30_is_taken(self):
        import budget
        self.typed_10_then_55()
        budget.record(seven_day=(30, RESETS), now=T + 800, source="statusline")
        budget.record(seven_day=(55, RESETS), now=T + 900, source="host limits")      # the real figure again: nothing ignores it
        self.assertEqual(self.week()["used"], 55.0)
        budget.record(seven_day=(57, RESETS), now=T + 950, source="host limits")      # and growth above it, confirmed by a second source
        budget.record(seven_day=(57, RESETS), now=T + 1000, source="statusline")
        self.assertEqual(self.week()["used"], 57.0)

    def test_r1309_a_new_reset_window_with_a_low_figure_is_a_real_reset_and_is_taken(self):
        import budget
        self.typed_10_then_55()
        budget.record(seven_day=(5, RESETS + DAY), now=T + 800, source="statusline")
        self.assertEqual(self.week()["used"], 5.0)


class SubLimitStampTest(StudioTestCase):
    def test_r1309_a_sub_limit_reset_stamp_that_is_not_a_number_is_dropped_and_a_later_read_never_raises(self):
        import budget
        for bad in ("soon", float("nan"), int("9" * 400)):
            budget.record_subcap(40, bad, label="x")
            self.assertIsNone(budget.load()["subcap"]["resets_at"])
            budget.subcap_for_line(30, now=T)


class SourceNameIsFixedTest(unittest.TestCase):
    def test_r1309_the_budget_record_command_has_no_option_to_set_the_source_name(self):
        with open(os.path.join(ROOT, "plugin", "bin", "budgit"), encoding="utf-8") as f:
            text = f.read()
        import ast
        start = text.index('bops.add_parser("record"')      # the `budget record` parser: from its line to the blank line after its options
        parser = text[start:text.index("\n\n", start)]
        self.assertNotIn("source", parser)
        found = {}
        for node in ast.walk(ast.parse(text)):      # every `budget.record(...)` call in the command: the source is a fixed value, never from the arguments
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "record" \
                    and getattr(node.func.value, "id", None) == "budget":
                kw = {k.arg: k.value for k in node.keywords}
                if "source" in kw:
                    found[ast.dump(kw["source"])] = kw["source"]
        self.assertTrue(found)
        for value in found.values():
            self.assertTrue(isinstance(value, ast.Constant) or isinstance(value, ast.Attribute), ast.dump(value))
            self.assertNotIn("args", ast.dump(value))


if __name__ == "__main__":
    unittest.main()
