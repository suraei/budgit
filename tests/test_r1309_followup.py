"""R-1309 follow-up. M1: the hold on a higher non-typed reading after a typed one ends 6 h after the typed reading, or at once when
two different non-typed sources both report a higher figure within 30 minutes. L-c: the private raw feed copy is cleaned and capped."""
import json, os, subprocess, sys, time
from helpers import StudioTestCase, ROOT
import studiolib as sl

DAY = 86400
HOUR = 3600
NOW = 1_800_000_000.0
RESETS = NOW + 0.15 * 7 * DAY
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class TypedHoldBoundTest(StudioTestCase):
    def stored(self):
        import budget
        return budget.load()["limits"]["seven_day"]["used"]

    def typed_17(self):
        import budget
        budget.record(seven_day=(17, RESETS), now=NOW, authoritative=True, source="typed")

    def test_r1309_typed_17_then_host_40_one_hour_later_stays_17_marked_stale(self):
        import budget
        self.typed_17()
        budget.record(seven_day=(60, RESETS), now=NOW + HOUR, source="host limits")      # round 5: 40 is inside 17 + 15 + 10 per hour
        self.assertEqual(self.stored(), 17.0)
        reason = budget.current(now=NOW + HOUR + 10)["reason"]
        self.assertIn("may be stale", reason)
        self.assertIn("host limits", reason)

    def test_r1309_a_host_figure_seven_hours_after_the_typed_one_is_taken_and_the_line_says_why(self):
        import budget      # round 8: 99 is above the weekly bound (17 + 15 + 5 an hour = 67 at 7 h), so the 6 h release is the path again
        self.typed_17()
        budget.record(seven_day=(99, RESETS), now=NOW + HOUR, source="host limits")
        budget.record(seven_day=(99, RESETS), now=NOW + 7 * HOUR, source="host limits")
        self.assertEqual(self.stored(), 99.0)
        reason = budget.current(now=NOW + 7 * HOUR + 10)["reason"]
        self.assertIn("older than 6 h", reason)
        self.assertNotIn("may be stale", reason)

    def test_r1309_just_inside_six_hours_the_hold_still_stands(self):
        import budget
        self.typed_17()
        budget.record(seven_day=(95, RESETS), now=NOW + 6 * HOUR - 60, source="host limits")      # round 5: above 17 + 15 + 59.8
        self.assertEqual(self.stored(), 17.0)

    def test_r1309_two_different_sources_both_higher_within_half_an_hour_are_taken_at_once(self):
        import budget
        self.typed_17()
        budget.record(seven_day=(40, RESETS), now=NOW + 600, source="host limits")
        self.assertEqual(self.stored(), 17.0)
        budget.record(seven_day=(44, RESETS), now=NOW + 900, source="statusline")      # round 4: 41 sat within a point of the host's 40
        self.assertEqual(self.stored(), 44.0)
        reason = budget.current(now=NOW + 910)["reason"]
        self.assertIn("two sources", reason)
        self.assertNotIn("may be stale", reason)

    def test_r1309_one_source_repeating_40_stays_held_before_six_hours(self):
        import budget
        self.typed_17()
        for k in range(1, 11):
            budget.record(seven_day=(95, RESETS), now=NOW + 1800 * k, source="host limits")      # round 5: above the rate bound for 5 h
        self.assertEqual(self.stored(), 17.0)

    def test_r1309_two_sources_more_than_half_an_hour_apart_do_not_agree(self):
        import budget
        self.typed_17()
        budget.record(seven_day=(40, RESETS), now=NOW + 600, source="host limits")
        budget.record(seven_day=(41, RESETS), now=NOW + 600 + 1900, source="statusline")
        self.assertEqual(self.stored(), 17.0)

    def test_r1309_after_a_release_growth_is_taken_until_the_user_types_again(self):
        import budget
        self.typed_17()
        budget.record(seven_day=(40, RESETS), now=NOW + 600, source="host limits")
        budget.record(seven_day=(44, RESETS), now=NOW + 900, source="statusline")      # round 5: 41 sat within a point of the host's 40
        budget.record(seven_day=(48, RESETS), now=NOW + 960, source="host limits")
        self.assertEqual(self.stored(), 48.0)
        budget.record(seven_day=(20, RESETS), now=NOW + 1000, authoritative=True, source="typed")      # typed again: protected again
        budget.record(seven_day=(60, RESETS), now=NOW + 1060, source="host limits")
        self.assertEqual(self.stored(), 20.0)

    def test_r1309_a_drop_of_20_points_or_more_keeps_todays_rule(self):
        import budget
        budget.record(seven_day=(60, RESETS), now=NOW, authoritative=True, source="typed")
        budget.record(seven_day=(30, RESETS), now=NOW + 60, source="host limits")
        self.assertEqual(self.stored(), 30.0)


class RawFeedCleanTest(StudioTestCase):
    def run_cli(self, *args):
        env = dict(os.environ, BUDGIT_HOME=self.home, CLAUDE_CODE_SESSION_ID="sess-1")
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.project, env=env, capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        self.run_cli("activate", self.project)
        self.resets = int(time.time()) + DAY

    def raw(self, rows):
        r = self.run_cli("status", "--line", "--colour", "off", "--limits-json", json.dumps(rows))
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(sl.home_state(), "budget-feed-raw.json")) as f:
            return json.load(f), r

    def test_r1309_a_500_character_kind_is_cut_to_64(self):
        rows, r = self.raw([{"kind": "k" * 500, "percentUsed": 5, "resetsAt": self.resets * 1000}])
        self.assertEqual(rows[0]["kind"], "k" * 64)
        self.assertNotIn("budget-feed-raw", r.stdout + r.stderr)

    def test_r1309_a_100_row_feed_keeps_32_rows(self):
        rows, _ = self.raw([{"kind": "kind%d" % i, "percentUsed": 5, "resetsAt": None} for i in range(100)])
        self.assertEqual(len(rows), 32)
        self.assertEqual(rows[0]["kind"], "kind0")
        self.assertEqual(rows[31]["kind"], "kind31")

    def test_r1309_control_and_bidi_characters_are_removed_from_the_kind_like_the_limit_kinds_file(self):
        evil = "seven" + chr(0x202E) + "_" + chr(7) + "da" + chr(0x200B) + "y" + chr(0x2066) + " Opus"
        rows, _ = self.raw([{"kind": evil, "percentUsed": 5, "resetsAt": None}])
        self.assertEqual(rows[0]["kind"], "seven_dayopus")

    def test_r1309_a_long_string_in_the_other_fields_is_cut_too(self):
        rows, _ = self.raw([{"kind": "seven_day", "percentUsed": 5, "resetsAt": "2026-11-23T04:00:00Z" + "x" * 300}])
        self.assertLessEqual(len(str(rows[0]["resetsAt"])), 64)
        self.assertEqual(oct(os.stat(os.path.join(sl.home_state(), "budget-feed-raw.json")).st_mode & 0o777), oct(0o600))
