"""R-1309 round 5: a taken rise may never walk the figure further from the last typed one than real use could grow since the typing
(RISE_STEP + RISE_RATE per hour); only the figure the host feed itself just sent for the same window can go beyond that. The typed
baseline (auth_used) stays the typed figure, a rise keeps the stored reset time, and a number that is not a number never stops record()."""
import unittest
from helpers import StudioTestCase

HOUR = 3600
DAY = 86400
T = 1_800_000_000.0
RESETS = T + 0.15 * 7 * DAY
HUGE = int("9" * 400)


class BoundedRiseTest(StudioTestCase):
    def stored(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def typed(self, used, at=T):
        import budget
        budget.record(seven_day=(used, RESETS), now=at, authoritative=True, source="typed")

    def test_r1309_a_stale_writer_walking_up_in_steps_of_14_stops_at_the_bound(self):
        import budget
        self.typed(17)
        for i, used in enumerate((31, 45, 59, 73), 1):      # one source, ten minutes: 17 + 15 + about 2 is the most real use can reach
            budget.record(seven_day=(used, RESETS), now=T + i * 150, source="statusline")
            self.assertEqual(self.stored(), 31.0)
        self.assertIn("may be stale", budget.current(now=T + 700)["reason"])
        self.assertEqual(budget.load()["readings"]["seven_day"]["auth_used"], 17.0)      # the baseline stays the typed figure

    def test_r1309_a_typed_26_then_a_host_31_five_minutes_later_is_taken(self):
        import budget
        self.typed(26)
        budget.record(seven_day=(31, RESETS), now=T + 300, source="host limits")
        self.assertEqual(self.stored(), 31.0)

    def test_r1309_three_hours_after_the_typing_a_rise_of_30_points_is_real_use_and_is_taken(self):
        import budget
        self.typed(20)
        budget.record(seven_day=(50, RESETS), now=T + 3 * HOUR, source="statusline")      # 20 + 15 + 10 per hour * 3 = 65
        self.assertEqual(self.stored(), 50.0)
        self.assertNotIn("may be stale", budget.current(now=T + 3 * HOUR + 10)["reason"])

    def test_r1309_three_hours_after_the_typing_a_figure_above_the_bound_is_held(self):
        import budget
        self.typed(20)
        budget.record(seven_day=(70, RESETS), now=T + 3 * HOUR, source="statusline")
        self.assertEqual(self.stored(), 20.0)

    def test_r1309_a_rise_keeps_the_stored_reset_time_so_the_window_cannot_drift_away(self):
        import budget
        self.typed(26)
        for i, (used, shift) in enumerate(((28, 300), (30, 600), (32, 300), (34, 600), (36, 300)), 1):
            budget.record(seven_day=(used, RESETS + shift), now=T + i * 60, source="statusline")
        data = budget.load()
        self.assertEqual(data["limits"]["seven_day"]["used"], 36.0)
        self.assertEqual(data["limits"]["seven_day"]["resets_at"], RESETS)
        self.assertEqual(data["readings"]["seven_day"]["auth_resets"], RESETS)
        budget.record(seven_day=(83, RESETS), now=T + 400, source="statusline")
        self.assertEqual(self.stored(), 36.0)      # the protection still applies


class NotANumberTest(StudioTestCase):
    def test_r1309_a_seen_time_that_is_not_a_number_never_stops_record(self):
        import budget
        for bad in ("soon", HUGE, float("nan")):
            data = budget.load()
            data["seen"] = {"seven_day": bad}
            budget.save(data)
            budget.record(seven_day=(30, RESETS), now=T, source="statusline")
            self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 30.0)

    def test_r1309_an_extra_figure_that_is_not_a_number_is_dropped_and_record_still_saves(self):
        import budget
        for bad in (("abc", 1), (HUGE, 1), (float("nan"), 1), (1, None)):
            budget.record(seven_day=(30, RESETS), extra=bad, now=T, source="statusline")
            self.assertNotIn("extra", budget.load())
        budget.record(extra=(5, 100), now=T, source="typed")
        self.assertEqual(budget.load()["extra"]["spent"], 5.0)

    def test_r1309_a_stored_extra_limit_that_is_not_a_number_never_stops_a_disabled_reading(self):
        import budget
        data = budget.load()
        data["extra"] = {"spent": 1, "limit": "lots", "enabled": True}
        budget.save(data)
        budget.record(extra_enabled=False, now=T, source="statusline")
        self.assertEqual(budget.load()["extra"]["spent"], 0.0)

    def test_r1309_a_sub_limit_that_is_not_a_number_is_no_sub_limit_and_never_raises(self):
        import budget
        for bad in ("abc", HUGE, float("nan")):
            budget.record_subcap(bad, None)
            self.assertNotIn("subcap", budget.load())


class HostReferenceTest(StudioTestCase):
    def test_r1309_a_host_figure_for_another_window_is_no_reference_for_this_one(self):
        import budget
        budget.record(seven_day=(20, RESETS), now=T, authoritative=True, source="typed")
        budget.record(seven_day=(40, RESETS - 2 * HOUR), now=T + 300, source="host limits")      # an older window: ignored, but the host mark is stored
        budget.record(seven_day=(40, RESETS), now=T + 400, source="statusline")
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 20.0)

    def test_r1309_a_plain_rise_clears_an_old_release_note(self):
        import budget
        budget.record(seven_day=(26, RESETS), now=T, authoritative=True, source="typed")
        data = budget.load()
        data["released"] = {"seven_day": {"why": budget.RELEASE_AGREE, "at": T + 500, "src": "statusline"}}
        budget.save(data)
        budget.record(seven_day=(31, RESETS), now=T + 600, source="host limits")
        self.assertNotIn("taken after a typed reading", budget.current(now=T + 610)["reason"])
        self.assertNotIn("seven_day", budget.load().get("released", {}))


if __name__ == "__main__":
    unittest.main()
