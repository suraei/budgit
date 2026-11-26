"""R-1309 round 8: a correct lower reading from a non-typed source always replaces a wrong higher one (only a typed figure is held
against a higher non-typed one); a new reset time is always a reset; a host mark with no time, no reset time or an old time is no
reference for a figure above the bound."""
import unittest
from helpers import StudioTestCase

HOUR = 3600
DAY = 86400
T = 1_800_000_000.0
RESETS = T + 0.15 * 7 * DAY


class SameWindowDropTest(StudioTestCase):
    def week(self):
        import budget
        return budget.load()["limits"]["seven_day"]

    def test_r1309_typed_10_then_55_then_a_non_typed_8_in_the_same_window_is_a_reset_and_is_taken(self):
        import budget
        budget.record(seven_day=(10, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(55, RESETS), now=T + 600, source="host limits")
        budget.record(seven_day=(55, RESETS), now=T + 700, source="statusline")
        self.assertEqual(self.week()["used"], 55.0)
        budget.record(seven_day=(8, RESETS), now=T + 800, source="statusline")
        self.assertEqual(self.week()["used"], 8.0)
        self.assertEqual(self.week()["reset_from"], 55.0)

    def test_r1309_typed_60_then_5_in_the_same_window_is_a_reset_and_is_taken(self):
        import budget
        budget.record(seven_day=(60, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(5, RESETS), now=T + 60, source="statusline")
        self.assertEqual(self.week()["used"], 5.0)

    def test_r1309_a_non_typed_83_then_a_correct_17_in_the_same_window_is_taken_as_17(self):
        import budget      # the guard for the user's case: no hold protects a wrong figure that was never typed
        budget.record(seven_day=(83, RESETS), now=T, source="statusline")
        budget.record(seven_day=(17, RESETS), now=T + 60, source="host limits")
        self.assertEqual(self.week()["used"], 17.0)

    def test_r1309_a_new_reset_time_is_always_a_reset(self):
        import budget
        budget.record(seven_day=(60, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(30, RESETS + DAY), now=T + 120, source="host limits")
        self.assertEqual(self.week()["used"], 30.0)


class HostMarkGapsTest(StudioTestCase):
    def check(self, mark, at=T + 600):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        data = budget.load()
        data["host"] = {"seven_day": mark}
        budget.save(data)
        budget.record(seven_day=(60, RESETS), now=at, source="statusline")      # above 17 + 15 + 5 an hour
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 17.0)

    def test_r1309_a_host_mark_with_no_time_is_no_reference(self):
        self.check({"used": 60, "resets_at": RESETS})

    def test_r1309_a_host_mark_with_no_reset_time_is_no_reference(self):
        self.check({"used": 60, "at": T + 500})

    def test_r1309_a_host_mark_older_than_30_minutes_is_no_reference(self):
        self.check({"used": 60, "at": T + 600 - 2000, "resets_at": RESETS})


if __name__ == "__main__":
    unittest.main()
