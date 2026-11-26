"""R-1309 round 2, the rest of it: the source name printed in the level line is one clean line (no control, bidi or
zero-width characters), and the status line shows and stores the weekly figure Budgit keeps, never the stdin value as it came."""
import contextlib, io, json, sys, time, unittest
from helpers import StudioTestCase

DAY = 86400
NOW = 1_800_000_000.0
RESETS = NOW + 0.15 * 7 * DAY
INVISIBLE = [chr(c) for c in (0x202E, 0x200B, 0x2066, 0xFEFF, 0x061C, 27)]


def run_statusline(payload):
    import statusline
    real, out = sys.stdin, io.StringIO()
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        with contextlib.redirect_stdout(out):
            statusline.main()
    finally:
        sys.stdin = real
    return out.getvalue()


class SourceNameTest(StudioTestCase):
    def test_r1309_the_source_name_loses_bidi_and_zero_width_characters(self):
        import budget
        a, b, c, d, e, f = INVISIBLE
        evil = "a" + a + "b" + b + "c" + c + "d" + d + "e" + e + "f" + f + "g"
        budget.record(seven_day=(62, RESETS), now=NOW, source=evil)
        self.assertEqual(budget.load()["readings"]["seven_day"]["src"], "abcdef g")
        budget.record(seven_day=(10, RESETS), now=NOW + 30, source="ok")
        budget.record(seven_day=(83, RESETS), now=NOW + 60, source=evil)
        reason = budget.current(now=NOW + 90)["reason"]
        self.assertIn("abcdef g", reason)
        for ch in INVISIBLE:
            self.assertNotIn(ch, reason)


class StatusLineTest(StudioTestCase):
    def test_r1309_a_typed_17_then_statusline_stdin_83_stores_and_prints_17(self):
        import budget
        t = time.time()
        budget.record(seven_day=(17, t + 3 * DAY), now=t, authoritative=True, source="typed")
        text = run_statusline({"rate_limits": {"seven_day": {"used_percentage": 83, "resets_at": t + 3 * DAY}}})
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 17.0)
        self.assertIn("wk 17%", text)
        self.assertNotIn("83", text)

    def test_r1309_the_status_line_stores_the_figure_as_sent_when_nothing_else_is_known(self):
        import budget
        text = run_statusline({"rate_limits": {"seven_day": {"used_percentage": 17, "resets_at": time.time() + 3 * DAY}}})
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 17)
        self.assertIn("wk 17%", text)


if __name__ == "__main__":
    unittest.main()
