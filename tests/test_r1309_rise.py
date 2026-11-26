"""R-1309 round 4: inside one reset window usage only rises, so a non-typed reading at or above the kept figure and within a
plausible step (RISE_STEP points, or the same figure the host last sent) is taken at once and moves the typed protection up with it;
only a bigger jump (83 against about 27) is held under the 6 h bound and two-source rules."""
import unittest
from helpers import StudioTestCase

HOUR = 3600
DAY = 86400
T = 1_800_000_000.0
RESETS = T + 0.15 * 7 * DAY


class RiseTest(StudioTestCase):
    def stored(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def reason(self, at):
        import budget
        return budget.current(now=at)["reason"]

    def typed(self, used, at=T):
        import budget
        budget.record(seven_day=(used, RESETS), now=at, authoritative=True, source="typed")

    def test_r1309_a_typed_26_then_a_host_31_is_taken_at_once_with_no_stale_line(self):
        import budget
        self.typed(26)
        budget.record(seven_day=(31, RESETS), now=T + 600, source="host limits")
        self.assertEqual(self.stored(), 31.0)
        self.assertNotIn("may be stale", self.reason(T + 610))
        self.assertNotIn("typed reading", self.reason(T + 610))

    def test_r1309_a_typed_27_then_a_host_83_is_held_and_marked_stale(self):
        import budget
        self.typed(27)
        budget.record(seven_day=(83, RESETS), now=T + 600, source="host limits")
        self.assertEqual(self.stored(), 27.0)
        self.assertIn("may be stale", self.reason(T + 610))

    def test_r1309_a_typed_17_then_40_is_23_points_up_and_is_held(self):
        import budget
        self.typed(17)
        budget.record(seven_day=(40, RESETS), now=T + 600, source="host limits")
        self.assertEqual(self.stored(), 17.0)
        self.assertIn("may be stale", self.reason(T + 610))

    def test_r1309_a_chain_26_31_38_45_is_taken_step_by_step(self):
        import budget
        self.typed(26)
        for i, used in enumerate((31, 38, 45), 1):
            budget.record(seven_day=(used, RESETS), now=T + i * 1200, source="host limits")      # round 6: 20 minutes apart, the weekly rate is 5 an hour
            self.assertEqual(self.stored(), float(used))
        self.assertNotIn("may be stale", self.reason(T + 3700))

    def test_r1309_a_figure_equal_to_the_one_the_host_last_sent_is_taken_even_far_above_the_typed_one(self):
        import budget
        self.typed(20)
        budget.record(seven_day=(40, RESETS), now=T + 600, source="host limits")      # 20 up: held
        self.assertEqual(self.stored(), 20.0)
        budget.record(seven_day=(40.5, RESETS), now=T + 700, source="statusline")      # within 1 point of the host figure
        self.assertEqual(self.stored(), 40.5)
        reason = self.reason(T + 710)
        self.assertNotIn("may be stale", reason)
        self.assertNotIn("taken after a typed reading", reason)      # a plain rise, not a release
        row = budget.load()["readings"]["seven_day"]
        self.assertEqual(row["auth_used"], 20.0)      # round 5: the baseline stays the typed figure
        self.assertEqual(row["auth_last"], 40.5)

    def test_r1309_a_host_figure_older_than_30_minutes_is_no_reference(self):
        import budget
        self.typed(20)
        budget.record(seven_day=(60, RESETS), now=T + 600, source="host limits")      # round 5: 40 is inside 20 + 15 + 10 per hour by then
        budget.record(seven_day=(60, RESETS), now=T + 600 + 1801, source="statusline")
        self.assertEqual(self.stored(), 20.0)

    def test_r1309_the_host_repeating_its_own_far_figure_does_not_confirm_itself(self):
        import budget
        self.typed(20)
        budget.record(seven_day=(40, RESETS), now=T + 600, source="host limits")
        budget.record(seven_day=(40, RESETS), now=T + 700, source="host limits")
        self.assertEqual(self.stored(), 20.0)

    def test_r1309_after_a_taken_rise_a_later_outlier_is_measured_from_the_new_figure(self):
        import budget
        self.typed(26)
        budget.record(seven_day=(40, RESETS), now=T + 600, source="host limits")
        budget.record(seven_day=(60, RESETS), now=T + 700, source="host limits")      # 20 above 40
        self.assertEqual(self.stored(), 40.0)
        self.assertIn("may be stale", self.reason(T + 710))
        row = budget.load()["readings"]["seven_day"]
        self.assertEqual(row["auth_used"], 26.0)      # round 5: the baseline stays the typed figure, the bound grows with time
        self.assertEqual(row["auth_last"], 40.0)

    def test_r1309_a_taken_rise_keeps_the_typed_protection_of_the_window(self):
        import budget
        self.typed(26)
        budget.record(seven_day=(31, RESETS), now=T + 600, source="host limits")
        row = budget.load()["readings"]["seven_day"]
        self.assertEqual(row["auth_used"], 26.0)      # round 5: the typed figure; the taken one is auth_last
        self.assertEqual(row["auth_last"], 31.0)
        self.assertEqual(row["auth_resets"], RESETS)
        self.assertEqual(row["auth_at"], T)      # the 6 h bound still counts from the typed reading
        budget.record(seven_day=(83, RESETS), now=T + 700, source="statusline")
        self.assertEqual(self.stored(), 31.0)

    def test_r1309_a_reading_in_a_new_reset_window_follows_today_s_rules(self):
        import budget
        self.typed(26)
        budget.record(seven_day=(83, RESETS + DAY), now=T + 600, source="host limits")
        self.assertEqual(self.stored(), 83.0)

    def test_r1309_a_lower_reading_inside_the_window_keeps_the_highest_figure(self):
        import budget
        self.typed(26)
        budget.record(seven_day=(31, RESETS), now=T + 600, source="host limits")
        budget.record(seven_day=(28, RESETS), now=T + 700, source="statusline")
        self.assertEqual(self.stored(), 31.0)

    def test_r1309_a_rise_is_taken_for_a_reading_with_no_reset_time_too(self):
        import budget
        self.typed(26)
        budget.record(seven_day=(31, None), now=T + 600, source="host limits")
        self.assertEqual(self.stored(), 31.0)


if __name__ == "__main__":
    unittest.main()
