"""R-1309 round 6: the plausible-rise bound depends on the window. The measured weekly growth is about 1.5 points an hour, so the weekly
allows 15 points plus 5 an hour (about three times that); a session window can run from 0 to 100 in 5 h, so it allows 30 plus 30 an hour."""
import unittest
from helpers import StudioTestCase

HOUR = 3600
DAY = 86400
T = 1_800_000_000.0
RESETS = T + 0.15 * 7 * DAY
RESETS_5H = T + 4 * HOUR


class PerWindowRiseTest(StudioTestCase):
    def week(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def session(self):
        import budget
        return budget.load()["limits"]["five_hour"]["used"]

    def test_r1309_seven_day_typed_17_then_48_three_hours_later_is_held(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(48, RESETS), now=T + 3 * HOUR, source="statusline")      # bound 17 + 15 + 5 * 3 = 47
        self.assertEqual(self.week(), 17.0)
        self.assertIn("may be stale", budget.current(now=T + 3 * HOUR + 10)["reason"])

    def test_r1309_seven_day_typed_17_then_47_three_hours_later_is_taken(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(47, RESETS), now=T + 3 * HOUR, source="statusline")
        self.assertEqual(self.week(), 47.0)

    def test_r1309_five_hour_typed_20_then_60_half_an_hour_later_is_taken(self):
        import budget
        budget.record(five_hour=(20, RESETS_5H), now=T, authoritative=True, source="typed")
        budget.record(five_hour=(60, RESETS_5H), now=T + HOUR // 2, source="statusline")      # bound 20 + 30 + 30 * 0.5 = 65
        self.assertEqual(self.session(), 60.0)

    def test_r1309_five_hour_typed_20_then_80_half_an_hour_later_is_held(self):
        import budget
        budget.record(five_hour=(20, RESETS_5H), now=T, authoritative=True, source="typed")
        budget.record(five_hour=(80, RESETS_5H), now=T + HOUR // 2, source="statusline")
        self.assertEqual(self.session(), 20.0)

    def test_r1309_the_bounds_are_named_per_window_in_one_place(self):
        import budget
        self.assertEqual(budget.RISE_BOUND["seven_day"], (15.0, 5.0))
        self.assertEqual(budget.RISE_BOUND["five_hour"], (30.0, 30.0))


if __name__ == "__main__":
    unittest.main()
