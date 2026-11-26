"""R-1309 round 3: the desktop mod feeds `[{kind, percentUsed, resetsAt}]` into the weekly window ("host limits"). The app said
all-models weekly 19 %, the Fable weekly 7 %, same reset, while the feed said 83 for the weekly."""
import json, os, subprocess, sys, time
from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class FeedTest(StudioTestCase):
    def run_cli(self, *args):
        env = dict(os.environ, BUDGIT_HOME=self.home, CLAUDE_CODE_SESSION_ID="sess-1")
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.project, env=env, capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        self.run_cli("activate", self.project)
        self.resets = int(time.time()) + 86400

    def feed(self, rows):
        r = self.run_cli("status", "--line", "--colour", "off", "--limits-json", json.dumps(rows))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def row(self, kind, used, resets=None):
        return {"kind": kind, "percentUsed": used, "resetsAt": (resets or self.resets) * 1000}

    def test_r1309_a_host_limits_83_after_a_typed_19_in_the_same_window_keeps_19_in_the_file_and_the_line(self):
        import budget
        now = time.time()
        budget.record(seven_day=(19, self.resets), now=now, authoritative=True, source="typed")
        self.feed([self.row("seven_day", 83), self.row("seven_day_fable", 7)])
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 19.0)
        reason = budget.current()["reason"]
        self.assertIn("weekly 19%", reason)
        self.assertIn("may be stale", reason)
        self.assertIn("host limits", reason)
        self.assertIn("83%", reason)

    def test_r1309_a_model_weekly_under_a_looser_name_cannot_overwrite_the_weekly_window(self):
        import budget
        self.feed([self.row("seven_day", 19), self.row("weekly", 83)])
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 19.0)
        self.feed([self.row("weekly", 83), self.row("seven_day", 19)])      # whatever the order of the rows
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 19.0)

    def test_r1309_two_looser_weekly_names_and_no_plain_kind_feed_no_weekly_figure(self):
        import budget
        self.feed([self.row("weekly", 83), self.row("week", 7)])
        self.assertNotIn("seven_day", budget.load().get("limits", {}))

    def test_r1309_a_single_looser_weekly_name_still_feeds_the_weekly_when_there_is_no_plain_kind(self):
        import budget
        self.feed([self.row("weekly", 19)])
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 19.0)

    def test_r1309_the_raw_feed_is_kept_privately_overwritten_each_time_and_never_printed(self):
        path = os.path.join(sl.home_state(), "budget-feed-raw.json")
        junk = {"kind": "seven_day", "percentUsed": 19, "resetsAt": self.resets * 1000, "label": "Weekly all models", "secret": "x"}
        r = self.feed([junk, self.row("seven_day_fable", 7)])
        rows = json.load(open(path))
        self.assertEqual([sorted(x) for x in rows], [["kind", "percentUsed", "resetsAt"]] * 2)
        self.assertEqual((rows[0]["kind"], rows[0]["percentUsed"], rows[1]["percentUsed"]), ("seven_day", 19, 7))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), oct(0o600))
        self.assertNotIn("budget-feed-raw", r.stdout + r.stderr)
        self.feed([self.row("five_hour", 3)])
        self.assertEqual([x["kind"] for x in json.load(open(path))], ["five_hour"])
