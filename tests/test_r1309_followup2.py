"""R-1309 follow-up round 2: rows written by the 0.13.9.34 build have auth_used and no auth_at (the 6 h start is the first
reading after the upgrade); a future auth_at or held_at never extends the hold; a second source with no reset time still releases."""
import unittest
from helpers import StudioTestCase

HOUR = 3600
DAY = 86400
T = 1_800_000_000.0
RESETS = T + 0.15 * 7 * DAY
FAR_FUTURE = 253402300799.0      # year 9999


class OldRowTest(StudioTestCase):
    def build_old(self, typed):
        """The exact shape the 0.13.9.34 build left: no auth_at on the row, whether the last taken reading was typed or not."""
        import budget
        row = {"src": "typed" if typed else "host limits", "at": T, "auth": typed, "auth_used": 28.0, "auth_resets": RESETS}
        budget.save({"limits": {"seven_day": {"used": 28.0, "resets_at": RESETS}}, "seen": {"seven_day": T}, "seen_at": T,
                     "readings": {"seven_day": row}, "held": {}})

    def stored(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def test_r1309_an_old_row_without_auth_at_does_not_release_on_the_first_higher_figure(self):
        import budget
        for typed in (False, True):
            self.build_old(typed)
            budget.record(seven_day=(83, RESETS), now=T + 60, source="host limits")
            self.assertEqual(self.stored(), 28.0, typed)
            reason = budget.current(now=T + 70)["reason"]
            self.assertIn("may be stale", reason)
            self.assertNotIn("older than 6 h", reason)

    def test_r1309_the_six_hours_start_at_the_first_reading_after_the_upgrade_and_are_stored_once(self):
        import budget
        self.build_old(False)
        budget.record(seven_day=(99, RESETS), now=T + 60, source="host limits")      # round 5: 99 is above 28 + 15 + 10 per hour for 5 h
        self.assertEqual(budget.load()["readings"]["seven_day"]["auth_at"], T + 60)
        budget.record(seven_day=(99, RESETS), now=T + 60 + 5 * HOUR, source="host limits")
        self.assertEqual(self.stored(), 28.0)
        self.assertEqual(budget.load()["readings"]["seven_day"]["auth_at"], T + 60)      # not moved by later readings
        budget.record(seven_day=(99, RESETS), now=T + 60 + 6 * HOUR + 60, source="host limits")
        self.assertEqual(self.stored(), 99.0)

    def test_r1309_typed_28_then_a_taken_host_28_then_host_83_stays_28_marked_stale(self):
        import budget
        budget.record(seven_day=(28, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(28, RESETS), now=T + 600, source="host limits")
        budget.record(seven_day=(83, RESETS), now=T + 1200, source="host limits")
        self.assertEqual(self.stored(), 28.0)
        self.assertIn("may be stale", budget.current(now=T + 1210)["reason"])


class FutureTimeTest(StudioTestCase):
    def stored(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def test_r1309_an_auth_at_in_the_far_future_holds_and_never_says_older_than_6_h(self):      # round 3: unknown age holds
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        data = budget.load()
        data["readings"]["seven_day"]["auth_at"] = FAR_FUTURE
        budget.save(data)
        budget.record(seven_day=(40, RESETS), now=T + HOUR, source="host limits")
        self.assertEqual(self.stored(), 17.0)
        reason = budget.current(now=T + HOUR + 10)["reason"]
        self.assertIn("may be stale", reason)
        self.assertNotIn("older than 6 h", reason)

    def test_r1309_a_held_mark_stamped_in_the_future_never_counts_as_a_second_source(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(40, RESETS), now=T + 600, source="host limits")
        data = budget.load()
        data["held"]["seven_day"]["at"] = FAR_FUTURE
        budget.save(data)
        budget.record(seven_day=(44, RESETS), now=T + 900, source="statusline")      # round 4: 41 sat within a point of the host's 40
        self.assertEqual(self.stored(), 17.0)

    def test_r1309_a_held_mark_stamped_in_the_future_does_not_show_the_stale_line_forever(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(40, RESETS), now=T + 600, source="host limits")
        data = budget.load()
        data["held"]["seven_day"]["at"] = FAR_FUTURE
        budget.save(data)
        self.assertNotIn("may be stale", budget.current(now=T + 900)["reason"])


class SecondSourceNoResetTimeTest(StudioTestCase):
    def test_r1309_the_second_source_sends_a_higher_reading_with_no_reset_time_and_releases(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(40, RESETS), now=T + 600, source="host limits")
        budget.record(seven_day=(44, None), now=T + 900, source="statusline")      # round 4: 41 sat within a point of the host's 40
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 44.0)
        self.assertIn("two sources", budget.current(now=T + 910)["reason"])


if __name__ == "__main__":
    unittest.main()
