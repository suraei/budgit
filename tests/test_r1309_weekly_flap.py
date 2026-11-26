"""R-1309 case 2: the weekly figure printed 62 %, 10 %, 62 % within half an hour with the same share of the week elapsed.
Her app said 17 % used: the low readings were right and a stale writer kept sending the high ones.
One test per invariant; every view reads the figure through budget.current(), so the tests read it there too."""
import io, json, sys, time, unittest
from helpers import StudioTestCase

DAY = 86400
NOW = 1_800_000_000.0
RESETS = NOW + 0.15 * 7 * DAY        # 85 % of the week elapsed


class WeeklyFlapTest(StudioTestCase):
    def stored(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def test_r1309_a_stale_writer_sending_62_again_cannot_flap_the_figure_back_up(self):
        import budget
        shown, levels = [], []
        for i in range(12):
            used = 62 if i % 2 == 0 else 10
            budget.record(seven_day=(used, RESETS), now=NOW + 60 * i, source="s%d" % (i % 2))
            shown.append(self.stored())
            levels.append(budget.current(now=NOW + 60 * i)["level"])
        self.assertEqual(shown, [62.0] + [10.0] * 11)
        self.assertEqual(len(set(levels[1:])), 1, levels)

    def test_r1309_a_source_with_no_reset_time_cannot_flap_the_figure_either(self):
        # the mechanism: a reading with no reset time used to be taken whatever it said, and the next full reading took the max back
        import budget
        shown, levels = [], []
        for i in range(12):
            if i % 2 == 0:
                budget.record(seven_day=(62, RESETS), now=NOW + 60 * i, source="statusline")
            else:
                budget.record(seven_day=(10, None), now=NOW + 60 * i, source="mod")
            shown.append(self.stored())
            levels.append(budget.current(now=NOW + 60 * i)["level"])
        self.assertEqual(shown, [62.0] + [10.0] * 11, shown)
        self.assertEqual(len(set(levels[1:])), 1, levels)

    def test_r1309_62_then_17_then_83_from_a_stale_writer_stays_17_and_the_line_names_it(self):
        import budget
        budget.record(seven_day=(62, RESETS), now=NOW, source="statusline")
        budget.record(seven_day=(17, RESETS), now=NOW + 60, source="mod")
        budget.record(seven_day=(83, RESETS), now=NOW + 120, source="old session")
        self.assertEqual(self.stored(), 17.0)
        reason = budget.current(now=NOW + 150)["reason"]
        self.assertIn("may be stale", reason)
        self.assertIn("old session", reason)
        self.assertIn("83%", reason)

    def test_r1309_a_lower_reading_from_a_non_authoritative_source_in_the_same_window_is_taken(self):
        import budget
        budget.record(seven_day=(62, RESETS), now=NOW, source="statusline")
        budget.record(seven_day=(17, RESETS), now=NOW + 60, source="mod")
        self.assertEqual(self.stored(), 17.0)
        self.assertNotIn("may be stale", budget.current(now=NOW + 90)["reason"])

    def test_r1309_a_typed_17_then_a_statusline_83_in_the_same_window_stays_17_with_the_stale_line(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=NOW, authoritative=True, source="typed")
        budget.record(seven_day=(83, RESETS), now=NOW + 60, source="statusline")
        budget.record(seven_day=(83, None), now=NOW + 120, source="statusline")      # one source repeating itself is not agreement
        self.assertEqual(self.stored(), 17.0)
        reason = budget.current(now=NOW + 150)["reason"]
        self.assertIn("may be stale", reason)
        self.assertIn("statusline", reason)

    def test_r1309_a_higher_reading_stays_held_until_she_types_again_even_after_hours(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=NOW, authoritative=True, source="typed")
        budget.record(seven_day=(83, RESETS), now=NOW + 5 * 3600, source="statusline")
        self.assertEqual(self.stored(), 17.0)
        budget.record(seven_day=(40, RESETS), now=NOW + 5 * 3600 + 60, authoritative=True, source="typed")
        self.assertEqual(self.stored(), 40.0)
        self.assertNotIn("may be stale", budget.current(now=NOW + 5 * 3600 + 90)["reason"])
        budget.record(seven_day=(70, RESETS), now=NOW + 5 * 3600 + 120, source="statusline")      # round 4: 45 is a plain rise now; 70 is 30 above the new typed figure: held
        self.assertEqual(self.stored(), 40.0)

    def test_r1309_a_lower_figure_after_a_typed_one_is_taken_and_a_later_higher_one_is_held(self):
        import budget
        budget.record(seven_day=(60, RESETS), now=NOW, authoritative=True, source="typed")
        budget.record(seven_day=(30, RESETS), now=NOW + 60, source="statusline")
        self.assertEqual(self.stored(), 30.0)
        budget.record(seven_day=(83, RESETS), now=NOW + 120, source="old session")
        self.assertEqual(self.stored(), 30.0)

    def test_r1309_a_higher_reading_with_a_new_reset_time_is_taken(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=NOW, authoritative=True, source="typed")
        budget.record(seven_day=(83, RESETS + 2 * 3600), now=NOW + 60, source="statusline")
        self.assertEqual(self.stored(), 83.0)

    def test_r1309_a_drop_from_the_authoritative_source_is_taken(self):
        import budget
        budget.record(seven_day=(62, RESETS), now=NOW, source="statusline")
        budget.record(seven_day=(10, RESETS), now=NOW + 60, authoritative=True, source="typed")
        self.assertEqual(self.stored(), 10.0)
        self.assertNotIn("may be stale", budget.current(now=NOW + 90)["reason"])

    def test_r1309_a_drop_with_a_new_reset_time_is_taken(self):
        import budget
        budget.record(seven_day=(62, RESETS), now=NOW, source="statusline")
        budget.record(seven_day=(10, RESETS + 2 * 3600), now=NOW + 60, source="statusline")
        self.assertEqual(self.stored(), 10.0)

    def test_r1309_a_free_reset_with_the_same_reset_date_is_taken_with_or_without_a_reset_time(self):
        # a free reset drops the use and leaves the date alone
        import budget
        budget.record(seven_day=(83, RESETS), now=NOW, source="statusline")
        budget.record(seven_day=(2, RESETS), now=NOW + 60, source="statusline")
        self.assertEqual(self.stored(), 2.0)
        budget.record(seven_day=(83, RESETS), now=NOW + 120, source="statusline")      # a session still on the old figure
        self.assertEqual(self.stored(), 2.0)
        budget.record(seven_day=(83, RESETS), now=NOW + 180, authoritative=True, source="typed")
        budget.record(seven_day=(1, None), now=NOW + 240, source="mod")
        self.assertEqual(self.stored(), 1.0)

    def test_r1309_a_drop_taken_from_a_reading_with_no_reset_time_sets_the_marker(self):
        import budget
        budget.record(seven_day=(83, RESETS), now=NOW, source="statusline")
        budget.record(seven_day=(3, None), now=NOW + 60, source="mod")
        win = budget.load()["limits"]["seven_day"]
        self.assertEqual((win["used"], win["reset_from"], win["reset_at"]), (3.0, 83.0, NOW + 60))
        budget.record(seven_day=(80, RESETS), now=NOW + 120, source="statusline")
        self.assertEqual(self.stored(), 3.0)

    def test_r1309_each_stored_weekly_reading_records_its_source_and_when_it_was_seen(self):
        import budget
        budget.record(seven_day=(40, RESETS), now=NOW, source="statusline")
        row = budget.load()["readings"]["seven_day"]
        self.assertEqual((row["src"], row["at"], row["auth"]), ("statusline", NOW, False))
        budget.record(seven_day=(55, None), now=NOW + 30, source="mod")
        budget.record(seven_day=(10, None), now=NOW + 60, source="mod")                # taken: a drop of 45
        self.assertEqual(budget.load()["readings"]["seven_day"]["at"], NOW + 60)
        budget.record(seven_day=(45, RESETS), now=NOW + 120, authoritative=True, source="typed")
        row = budget.load()["readings"]["seven_day"]
        self.assertEqual((row["src"], row["at"], row["auth"]), ("typed", NOW + 120, True))

    def test_r1309_a_small_lag_drop_is_still_held_by_the_max_without_a_stale_claim(self):
        import budget
        budget.record(seven_day=(45, RESETS), now=NOW, source="statusline")
        budget.record(seven_day=(34, None), now=NOW + 60, source="mod")
        self.assertEqual(self.stored(), 45.0)
        self.assertNotIn("may be stale", budget.current(now=NOW + 90)["reason"])

    def test_r1309_the_status_line_passes_its_source_name(self):
        import budget, statusline
        payload = {"rate_limits": {"seven_day": {"used_percentage": 37, "resets_at": time.time() + 3 * DAY}}}
        real = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            statusline.main()
        finally:
            sys.stdin = real
        self.assertEqual(budget.load()["readings"]["seven_day"]["src"], "statusline")


if __name__ == "__main__":
    unittest.main()
