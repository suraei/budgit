"""R-1309 follow-up round 3: an unknown or future age of the typed reading holds (the newest higher figure is the one that was
wrong) and restamps the time; the clock is read under the lock; a huge integer in the data never stops record() from saving."""
import unittest
from helpers import StudioTestCase

HOUR = 3600
DAY = 86400
T = 1_800_000_000.0
RESETS = T + 0.15 * 7 * DAY
FAR_FUTURE = 253402300799.0      # year 9999
HUGE = int("9" * 400)


class FutureAgeHoldsTest(StudioTestCase):
    def stored(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def typed_17_with_stamp(self, stamp):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        data = budget.load()
        data["readings"]["seven_day"]["auth_at"] = stamp
        budget.save(data)

    def test_r1309_a_future_auth_at_then_a_higher_host_figure_stays_held_and_is_marked_stale(self):
        import budget
        self.typed_17_with_stamp(FAR_FUTURE)
        budget.record(seven_day=(40, RESETS), now=T + HOUR, source="host limits")
        self.assertEqual(self.stored(), 17.0)
        reason = budget.current(now=T + HOUR + 10)["reason"]
        self.assertIn("may be stale", reason)
        self.assertNotIn("unknown", reason)
        self.assertNotIn("older than 6 h", reason)

    def test_r1309_a_future_auth_at_is_restamped_to_now_and_can_never_hold_longer_than_six_hours(self):
        import budget
        self.typed_17_with_stamp(FAR_FUTURE)
        budget.record(seven_day=(95, RESETS), now=T + HOUR, source="host limits")      # round 5: 95 is above 17 + 15 + 10 per hour for the whole 6 h
        self.assertEqual(budget.load()["readings"]["seven_day"]["auth_at"], T + HOUR)
        budget.record(seven_day=(95, RESETS), now=T + HOUR + 6 * HOUR - 60, source="host limits")
        self.assertEqual(self.stored(), 17.0)
        budget.record(seven_day=(95, RESETS), now=T + HOUR + 6 * HOUR + 1, source="host limits")
        self.assertEqual(self.stored(), 95.0)

    def test_r1309_a_typed_reading_stamped_a_few_milliseconds_after_a_non_typed_clock_does_not_release(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=T + 0.005, authoritative=True, source="typed")
        budget.record(seven_day=(40, RESETS), now=T, source="host limits")
        self.assertEqual(self.stored(), 17.0)

    def test_r1309_the_clock_is_read_under_the_lock_unless_a_caller_passes_one(self):
        import budget, time
        taken = []
        real_time, real_locked = time.time, budget._locked
        import contextlib

        @contextlib.contextmanager
        def slow_lock():
            with real_locked():
                taken.append("lock")
                yield

        def clock():
            taken.append("clock")
            return T

        budget._locked, time.time = slow_lock, clock
        try:
            budget.record(seven_day=(17, RESETS), source="typed")
        finally:
            budget._locked, time.time = real_locked, real_time
        self.assertEqual(taken[:2], ["lock", "clock"])


class HugeIntegerTest(StudioTestCase):
    def test_r1309_a_400_digit_integer_in_auth_at_never_stops_record_from_saving(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=T, authoritative=True, source="typed")
        data = budget.load()
        data["readings"]["seven_day"]["auth_at"] = HUGE
        budget.save(data)
        budget.record(seven_day=(40, RESETS), now=T + 60, source="host limits")
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 17.0)
        self.assertEqual(budget.load()["readings"]["seven_day"]["auth_at"], T + 60)

    def test_r1309_a_400_digit_integer_as_a_reading_is_dropped_and_the_rest_is_saved(self):
        import budget
        budget.record(seven_day=(HUGE, RESETS), five_hour=(12, T + 3600), now=T, source="host limits")
        data = budget.load()
        self.assertNotIn("seven_day", data.get("limits", {}))
        self.assertEqual(data["limits"]["five_hour"]["used"], 12.0)

    def test_r1309_a_400_digit_integer_stored_as_used_is_replaced_by_the_next_reading(self):
        import budget
        budget.record(seven_day=(20, RESETS), now=T, source="host limits")
        data = budget.load()
        data["limits"]["seven_day"]["used"] = HUGE
        budget.save(data)
        budget.record(seven_day=(21, RESETS), now=T + 60, source="host limits")
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 21.0)

    def test_r1309_num_gives_none_for_a_huge_integer(self):
        import budget
        self.assertIsNone(budget._num(HUGE))
        self.assertEqual(budget._num(5), 5.0)


if __name__ == "__main__":
    unittest.main()
