"""R-865 (pace against the sustainable pace, both windows) and R-862 (the extra-usage limit is not spend)."""
import unittest
from helpers import StudioTestCase

DAY = 86400
HOUR = 3600
NOW = 1_800_000_000.0
FIVE = 5 * HOUR


def week(used, days_gone):
    """A weekly reading `days_gone` days into its window."""
    return (used, NOW + 7 * DAY - days_gone * DAY)


def five(used, share_gone):
    """A 5-hour reading `share_gone` (0..1) of the way through its window."""
    return (used, NOW + FIVE * (1 - share_gone))


class LevelCase(StudioTestCase):
    def read(self, seven_day=None, five_hour=None, **kw):
        import budget
        budget.record(seven_day=seven_day, five_hour=five_hour, now=NOW, authoritative=True, **kw)
        cur = budget.current(now=NOW)
        pol = budget.policy(cur["level"])
        return cur["level"], pol["subagents"], cur, budget.brote_state(budget.load().get("limits"), now=NOW)


class WeeklyPaceTest(LevelCase):
    def test_9_nov_replay_weekly_15_percent_at_8_hours_is_not_normal(self):
        # 15 % in 8 h = 45 %/day against 14.3 %/day: 3.1 times the sustainable pace. Ultra shape: seven_day only.
        level, cap, cur, _ = self.read(seven_day=week(15, 8 / 24.0))
        self.assertNotEqual(level, "normal")
        self.assertEqual((level, cap), ("critical", 0))
        self.assertIn("%", cur["reason"])
        self.assertIn("3.1x", cur["reason"])

    def test_9_nov_replay_41_to_44_percent_a_day_against_14(self):
        for per_day in (41, 44):
            with self.subTest(per_day=per_day):
                self.assertNotEqual(self.read(seven_day=week(per_day * 2, 2))[0], "normal")

    def test_pace_thresholds_are_1_5_and_2_5_times_the_sustainable_pace(self):
        # one day in: the sustainable pace is 14.29 %, so 21 % is 1.47x, 22 % 1.54x, 35 % 2.45x, 36 % 2.52x
        got = [self.read(seven_day=week(u, 1))[0] for u in (21, 22, 35, 36)]
        self.assertEqual(got, ["normal", "tight", "tight", "critical"])

    def test_each_level_sets_the_cap_and_the_models_the_hook_enforces(self):
        import budget
        want = {"normal": (3, "opus"), "tight": (2, "sonnet"), "critical": (0, "sonnet")}
        for used, level in ((18, "normal"), (22, "tight"), (36, "critical")):
            with self.subTest(level=level):
                got, cap, _, _ = self.read(seven_day=week(used, 1))
                self.assertEqual(got, level)
                self.assertEqual(cap, want[level][0])
                self.assertEqual(budget.policy(got)["models"]["judgement"], want[level][1])
                # the pre-Agent hook lowers a dearer model to this level's role model
                self.assertEqual(budget.above_policy("verifier", got, "opus"), None if want[level][1] == "opus" else "sonnet")
                self.assertEqual(budget.current(now=NOW)["level"], got)    # hook.py reads budget.current()["level"]


class FiveHourPaceTest(LevelCase):
    LOW_WEEK = week(10, 3.5)     # weekly low: 10 % at the half

    def test_five_hour_pace_thresholds(self):
        # 20 % of the window gone: 30 % used is exactly the sustainable pace x1.5
        got = [self.read(self.LOW_WEEK, five(u, 0.2))[0] for u in (29, 49, 51)]
        self.assertEqual(got, ["ample", "tight", "critical"])      # the weekly is low, so below 1.5x it is ample, not normal

    def test_a_fast_start_is_critical_before_the_ceilings(self):
        level, cap, cur, _ = self.read(self.LOW_WEEK, five(40, 0.1))
        self.assertEqual((level, cap), ("critical", 0))
        self.assertIn("4.0x", cur["reason"])

    def test_a_tiny_sample_does_not_read_as_a_huge_ratio(self):
        self.assertEqual(self.read(self.LOW_WEEK, five(2, 0.01))[0], "ample")

    def test_missing_resets_at_uses_the_percent_alone(self):
        import budget
        for used, level in ((40, "normal"), (75, "tight"), (90, "critical"), (100, "critical")):
            with self.subTest(used=used):
                budget.record(seven_day=self.LOW_WEEK, five_hour=(used, None), now=NOW, authoritative=True)
                cur = budget.current(now=NOW)
                self.assertEqual(cur["level"], level)
                self.assertIn("no reset time", cur["reason"])


class ConstantsPinnedTest(LevelCase):
    """R-865 mutation round: each floor and the hysteresis band is pinned to its value."""
    LOW_WEEK = week(10, 3.5)

    def classify(self, prev, seven_day, five_hour=None):
        import budget
        lim = {"seven_day": {"used": seven_day[0], "resets_at": seven_day[1]}}
        if five_hour:
            lim["five_hour"] = {"used": five_hour[0], "resets_at": five_hour[1]}
        return budget.classify(lim, prev=prev, now=NOW)[0]

    def test_week_floor_is_a_quarter_of_a_day(self):
        # 0.1 day in: the rate is read over the 0.25-day floor, 5 % -> 20 % a day, 1.4x the sustainable pace
        reason = self.read(seven_day=week(5, 0.1))[2]["reason"]
        self.assertIn("20% a day so far", reason)
        self.assertIn("(1.4x)", reason)

    def test_five_hour_floor_is_ten_percent_of_the_window(self):
        # 5 % of the window gone: 10 % used is read over the 10 % floor, exactly 1.0x
        self.assertIn("(1.0x the pace", self.read(self.LOW_WEEK, five(10, 0.05))[2]["reason"])

    def test_hysteresis_band_is_five_points_on_the_ceilings(self):
        # a tight level is held down to 75 % weekly (80 - 5), released at 74
        late = lambda u: week(u, 6.3)
        self.assertEqual(self.classify("tight", late(75)), "tight")
        self.assertEqual(self.classify("tight", late(74)), "normal")

    def test_hysteresis_relaxes_ratio_thresholds_by_one_minus_band_over_25(self):
        # k = 1 - 5/25 = 0.8: tight is held while the 5-hour ratio exceeds 1.2, critical while it exceeds 2.0
        half = lambda u: five(u, 0.5)        # ratio = u / 50
        self.assertNotEqual(self.classify("tight", self.LOW_WEEK, half(59.5)), "tight")
        self.assertEqual(self.classify("tight", self.LOW_WEEK, half(60.5)), "tight")
        fifth = lambda u: five(u, 0.2)       # ratio = u / 20
        self.assertEqual(self.classify("critical", self.LOW_WEEK, fifth(40.2)), "critical")
        self.assertEqual(self.classify("critical", self.LOW_WEEK, fifth(39.7)), "tight")


class PlanShapesTest(LevelCase):
    """Pro and Max send five_hour + seven_day; Ultra sends seven_day only. At each step: level, agent cap, Brote."""

    def test_pro_max_five_hour_steps_with_the_weekly_low(self):
        low = week(10, 3.5)
        steps = ((40, "normal", 3, "radiante", 40), (75, "tight", 2, "radiante", 75),
                 (90, "critical", 0, "radiante", 90), (100, "critical", 0, "dormido", 100))
        for used, level, cap, state, h5 in steps:
            with self.subTest(five_hour=used):
                got, agents, _, brote = self.read(low, five(used, 0.5))
                self.assertEqual((got, agents), (level, cap))
                self.assertEqual((brote["state"], brote["h5"]), (state, h5))

    def test_ultra_shape_without_a_five_hour_window_is_unchanged(self):
        import budget
        self.assertEqual(self.read(week(45, 3.5))[:2], ("normal", 3))
        lim = {"seven_day": {"used": 45.0, "resets_at": NOW + 3.5 * DAY}}
        self.assertEqual(budget.classify(lim, now=NOW)[0], "normal")
        self.assertEqual(self.read(week(63, 3.43))[0], "tight")
        self.assertIn("no 5-hour window", self.read(week(63, 3.43))[2]["reason"])

    def test_ultra_brote_state_has_no_five_hour_overlay(self):
        self.assertEqual(self.read(week(10, 3.5))[3]["h5"], 0)


class ExtraUsageTest(LevelCase):
    TIGHT = week(22, 1)

    def reason(self, **kw):
        return self.read(seven_day=self.TIGHT, **kw)[2]["reason"]

    def test_disabled_with_zero_spent_and_a_limit_says_nothing_about_spend(self):
        self.assertNotIn("extra usage", self.reason(extra=(0.0, 101.27), extra_enabled=False))

    def test_unknown_flag_with_zero_spent_and_a_limit_says_nothing_about_spend(self):
        self.assertNotIn("extra usage", self.reason(extra=(0.0, 101.27)))

    def test_enabled_with_partial_spend_shows_spend_of_limit(self):
        self.assertIn("extra usage $12.50 of $101 this month", self.reason(extra=(12.5, 101.27), extra_enabled=True))

    def test_a_disabled_reading_with_no_figures_replaces_an_older_enabled_one(self):
        import budget
        self.reason(extra=(12.5, 101.27), extra_enabled=True)
        budget.record(extra_enabled=False, now=NOW, authoritative=True)
        self.assertEqual(budget.load()["extra"], {"spent": 0.0, "limit": 101.27, "enabled": False})
        self.assertNotIn("extra usage", budget.current(now=NOW)["reason"])


if __name__ == "__main__":
    unittest.main()
