"""R-1378 (absorbs R-1374): the app's account figure referees the weekly. Every account fixture is derived from the sanitized real
get_usage payload in tests/fixtures/get-usage-sample.json, never typed by hand. Temp HOME only (StudioTestCase)."""
import copy
import json
import os
import stat
import subprocess
import sys
import unittest
from helpers import StudioTestCase

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "fixtures", "get-usage-sample.json")
BUDGIT = os.path.join(os.path.dirname(HERE), "plugin", "bin", "budgit")
HOUR = 3600
DAY = 86400
WEEK = 7 * DAY


class AccountHelpers:
    """A mixin: the fixtures and helpers both test classes share (so no test runs twice)."""

    def setUp(self):
        super().setUp()
        import budget
        self.b = budget
        with open(SAMPLE, encoding="utf-8") as f:
            self.sample = json.load(f)
        self.resets = budget._iso_epoch(self.sample["plan"]["windows"][0]["resetsAt"])
        self.t = self.resets - (5 * DAY + 18 * HOUR) + 600      # ten minutes after the payload's derived read time

    def payload(self, used=31, mutate=None):
        p = copy.deepcopy(self.sample)
        p["plan"]["windows"][0]["percentUsed"] = used
        if mutate:
            mutate(p)
        return p

    def acct(self, used=31, at=None, mutate=None):
        return self.b.account(self.payload(used, mutate), now=at or self.t)

    def host(self, used, at, resets=None, src="host limits"):
        self.b.record(seven_day=(used, resets or self.resets), now=at, source=src)

    def typed(self, used, at, mine=False):
        self.b.record(seven_day=(used, self.resets), now=at, authoritative=True, source="typed", mine=mine)

    def stored(self):
        return self.b.load()["limits"]["seven_day"]["used"]

    def notes(self, at):
        return " | ".join(self.b.account_notes("es", now=at))



class AccountFloorTest(AccountHelpers, StudioTestCase):
    # --- the floor ---
    def test_row01_account_31_then_host_6_and_29_alternating_two_hours_stays_31(self):
        self.acct(31)
        for i in range(8):
            self.host(6 if i % 2 == 0 else 29, self.t + 60 + i * 900)
            self.assertEqual(self.stored(), 31.0)
        self.assertIn("weekly 31%", self.b.current(now=self.t + 2 * HOUR)["reason"])

    def test_row02_account_17_host_83_and_statusline_83_from_one_session_stay_17_with_the_line(self):
        self.acct(17)
        self.host(83, self.t + 60)
        self.host(83, self.t + 120, src="statusline")
        self.assertEqual(self.stored(), 17.0)
        self.assertIn("el sistema marca 83 %, sin confirmar", self.notes(self.t + 130))

    def test_row03a_account_17_then_host_83_ten_and_a_half_hours_later_is_held(self):
        self.acct(17)
        self.host(83, self.t + int(10.5 * HOUR))
        self.assertEqual(self.stored(), 17.0)

    def test_row03b_account_17_then_host_19_is_a_small_rise_and_taken(self):
        self.acct(17)
        self.host(19, self.t + 60)
        self.assertEqual(self.stored(), 19.0)

    def test_row04a_account_83_then_host_5_with_a_later_reset_time_releases_the_floor(self):
        self.acct(83)
        self.host(5, self.t + 60, resets=self.resets + DAY)
        self.assertEqual(self.stored(), 5.0)

    def test_row04b_account_83_then_host_5_with_an_earlier_reset_time_releases_the_floor(self):
        self.acct(83)
        self.host(5, self.t + 60, resets=self.resets - 2 * DAY)
        self.assertEqual(self.stored(), 5.0)

    def test_row04c_account_83_then_the_window_ends_and_a_new_week_figure_is_taken(self):
        self.acct(83)
        after = self.resets + HOUR
        self.host(5, after, resets=self.resets + WEEK)
        self.assertEqual(self.stored(), 5.0)

    # --- refusals ---
    def refused(self, mutate=None, at=None, text=None):
        import studiolib as sl
        with self.assertRaises(sl.StudioError) as ctx:
            self.acct(31, at=at, mutate=mutate)
        self.assertNotIn("account", self.b.load())
        if text:
            self.assertIn(text, str(ctx.exception))

    def test_row05a_no_resetsAt_is_refused(self):
        self.refused(lambda p: p["plan"]["windows"][0].pop("resetsAt"))

    def test_row05b_status_not_ok_is_refused(self):
        self.refused(lambda p: p["plan"].__setitem__("status", "error"))

    def test_row05c_only_a_per_model_row_is_refused(self):
        self.refused(lambda p: p["plan"].__setitem__("windows", p["plan"]["windows"][1:]), text="Weekly")

    def test_row05d_two_all_models_rows_are_refused(self):
        self.refused(lambda p: p["plan"]["windows"].append(copy.deepcopy(p["plan"]["windows"][0])))

    def test_row05e_a_label_not_in_the_table_is_refused_and_listed(self):
        self.refused(lambda p: p["plan"]["windows"][0].__setitem__("label", "Semanal · todos los modelos"), text="Semanal")

    def test_row05f_a_derived_read_time_three_hours_old_is_refused(self):
        self.refused(at=self.t + 3 * HOUR)

    # --- typed is hers, and it cannot hide the app ---
    def test_row06a_typed_19_under_account_31_keeps_31_and_line_1_shows_both(self):
        self.acct(31)
        self.typed(19, self.t + 60)
        self.assertEqual(self.stored(), 31.0)
        self.assertIn("escrito 19 · app 31 (", self.notes(self.t + 70))

    def test_row06b_mine_19_lowers_the_level_below_the_floor(self):
        self.acct(31)
        self.typed(19, self.t + 60, mine=True)
        self.assertEqual(self.stored(), 19.0)
        self.host(6, self.t + 120)
        self.assertLessEqual(self.stored(), 19.0)

    def test_row06c_typed_19_then_a_newer_account_31_is_31(self):
        self.acct(31)
        self.typed(19, self.t + 60)
        self.acct(31, at=self.t + 300)
        self.assertEqual(self.stored(), 31.0)
        self.assertNotIn("escrito", self.notes(self.t + 310))

    def test_row06d_mine_19_then_a_newer_account_31_is_31(self):
        self.acct(31)
        self.typed(19, self.t + 60, mine=True)
        self.acct(31, at=self.t + 300)
        self.assertEqual(self.stored(), 31.0)

    def test_row06e_typed_at_or_above_the_floor_rules(self):
        self.acct(31)
        self.typed(35, self.t + 60)
        self.assertEqual(self.stored(), 35.0)

    def test_row06f_mine_without_a_terminal_is_refused_and_the_level_is_unchanged(self):
        self.acct(31)
        r = subprocess.run([sys.executable, "-I", BUDGIT, "budget", "record", "--seven-day", "19", "--seven-day-resets",
                            str(self.resets), "--mine"], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 1)
        self.assertIn("--mine", r.stderr)
        self.assertEqual(self.stored(), 31.0)

    # --- account figures among themselves ---
    def test_row07a_account_30_then_31_is_31(self):
        self.acct(30)
        self.acct(31, at=self.t + 60)
        self.assertEqual(self.stored(), 31.0)

    def test_row07b_a_newer_lower_account_figure_keeps_the_higher_with_a_mismatch_note(self):
        self.acct(31)
        self.acct(28, at=self.t + 60)
        self.assertEqual(self.stored(), 31.0)
        self.assertIn("28 %", self.notes(self.t + 70))

    def test_row08_an_account_figure_of_the_previous_week_is_ignored(self):
        self.acct(31)
        after = self.resets + HOUR
        self.assertIsNone(self.b._floor(self.b.load(), after))
        self.host(6, after, resets=self.resets + WEEK)
        self.assertEqual(self.stored(), 6.0)

    def test_row09_an_account_read_13_hours_ago_still_holds_and_is_named(self):
        self.acct(31)
        self.host(6, self.t + 13 * HOUR)
        self.assertEqual(self.stored(), 31.0)
        self.assertIn("app leída hace 13 h", self.notes(self.t + 13 * HOUR))

    # --- no account figure: today's rules, display line only ---
    # Known limit (TM decision (a), filed as an R-1378 finding for option A): with NO account figure, RELEASE_AGREE (R-1309 follow-up, pinned
    # by tests/test_r1309_*) still takes host 83 + statusline 83 after a typed reading. The page row "host 83 + statusline 83 -> not taken"
    # without an account figure is dropped; what holds there is a same-source figure and the display-only line.
    def test_row10_no_account_figure_a_same_source_83_after_typed_17_is_held_with_the_display_line(self):
        self.typed(17, self.t)
        self.host(83, self.t + 60)
        self.host(83, self.t + 120)
        self.assertEqual(self.stored(), 17.0)
        self.assertIn("el sistema marca 83 %, sin confirmar", self.notes(self.t + 130))

    def test_row11_budget_json_with_a_dead_account_key_behaves_as_without_it(self):
        self.b.save({"limits": {"seven_day": {"used": 20.0, "resets_at": self.resets}},
                     "account": {"seven_day": {"used": 83.0, "resets_at": 1.0, "at": 1.0}}})
        self.host(25, self.t)
        self.assertEqual(self.stored(), 25.0)
        self.assertEqual(self.b.load()["account"]["seven_day"]["used"], 83.0)

    # --- the per-session log ---
    def test_row12_log_appends_only_on_change_with_seven_keys_hashed_session_and_mode_0600(self):
        import studiolib as sl
        rows = [("seven_day", 31, "2026-11-30T04:00:00.103Z")]
        self.assertEqual(self.b.note_session_feed("sess-raw-id-1", "model-x", rows, "host limits", now=self.t), 1)
        self.assertEqual(self.b.note_session_feed("sess-raw-id-1", "model-x", rows, "host limits", now=self.t + 60), 0)
        self.assertEqual(self.b.note_session_feed("sess-raw-id-1", "model-x", [("seven_day", 32, "2026-11-30T04:00:00.103Z")],
                                                  "host limits", now=self.t + 120), 1)
        self.assertEqual(self.b.note_session_feed(None, "model-x", rows, "host limits"), 0)
        path = os.path.join(sl.home_state(), self.b.FEED_LOG)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        lines = [json.loads(l) for l in text.splitlines()]
        self.assertEqual(len(lines), 2)
        for row in lines:
            self.assertEqual(set(row), {"at", "source", "session", "model", "kind", "percentUsed", "resetsAt"})
        self.assertNotIn("sess-raw-id-1", text)
        self.assertNotIn(os.sep + "Users", text)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_row13_log_rotates_and_keeps_one_old_file(self):
        import studiolib as sl
        self.b.FEED_MAX, old = 400, self.b.FEED_MAX
        self.addCleanup(setattr, self.b, "FEED_MAX", old)
        for i in range(12):
            self.b.note_session_feed("sess-raw-id-2", "model-x", [("seven_day", i, None)], "host limits", now=self.t + i)
        path = os.path.join(sl.home_state(), self.b.FEED_LOG)
        self.assertTrue(os.path.exists(path + ".1"))
        self.assertLess(os.path.getsize(path), 400 + 300)


class WiringTest(AccountHelpers, StudioTestCase):
    """The CLI verb, the flags, the status line floor, the statusline script and the export skip (R-1378 round 2)."""

    def cli(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("budgit_cli", BUDGIT)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli", loader))
        loader.exec_module(mod)
        return mod

    def payload_file(self, used=31):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "usage.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.payload(used), f)
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", BUDGIT, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              env=dict(os.environ))

    def feed_path(self):
        import studiolib as sl
        return os.path.join(sl.home_state(), self.b.FEED_LOG)

    def test_row14a_cli_account_verb_stores_the_figure_from_a_file_with_a_valid_clock(self):
        import argparse
        import contextlib
        import io
        from unittest import mock
        mod, out = self.cli(), io.StringIO()
        with mock.patch("time.time", return_value=self.t), contextlib.redirect_stdout(out):
            mod.cmd_budget(argparse.Namespace(op="account", json=self.payload_file(31)))
        self.assertEqual(self.stored(), 31.0)
        self.assertEqual(self.b.load()["account"]["seven_day"]["used"], 31.0)

    def test_row14b_cli_account_verb_refuses_a_payload_whose_read_time_is_three_hours_old(self):
        import argparse
        import studiolib as sl
        from unittest import mock
        mod = self.cli()
        with mock.patch("time.time", return_value=self.t + 3 * HOUR), self.assertRaises(sl.StudioError):
            mod.cmd_budget(argparse.Namespace(op="account", json=self.payload_file(31)))
        self.assertNotIn("account", self.b.load())

    def test_row15_the_status_line_weekly_is_floored_too(self):
        import argparse
        from unittest import mock
        self.acct(31)
        data = self.b.load()
        data["limits"]["seven_day"]["used"] = 6.0
        self.b.save(data)
        with mock.patch("time.time", return_value=self.t + 60):
            limits, fresh = self.cli()._live_snapshot(argparse.Namespace(limits_json=None))
        self.assertTrue(fresh)
        self.assertEqual(limits["seven_day"]["used"], 31.0)

    def test_row16_the_cli_accepts_the_limits_run_with_and_without_the_new_flags(self):
        import time
        rows = json.dumps([{"kind": "seven_day", "percentUsed": 5, "resetsAt": time.time() + 3 * DAY}])
        plain = self.run_cli("status", "--line", "--limits-json", rows)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertFalse(os.path.exists(self.feed_path()))
        flagged = self.run_cli("status", "--line", "--limits-json", rows, "--session-id", "sess-cli-1", "--model", "model-x")
        self.assertEqual(flagged.returncode, 0, flagged.stderr)
        with open(self.feed_path(), encoding="utf-8") as f:
            text = f.read()
        row = json.loads(text.splitlines()[0])
        self.assertEqual((row["source"], row["model"], row["kind"]), ("host limits", "model-x", "seven_day"))
        self.assertNotIn("sess-cli-1", text)

    def test_row17_the_statusline_script_logs_with_the_keys_and_runs_without_them(self):
        import time
        script = os.path.join(os.path.dirname(BUDGIT), "..", "scripts", "statusline.py")
        base = {"rate_limits": {"seven_day": {"used_percentage": 12, "resets_at": time.time() + 3 * DAY}}}
        bare = subprocess.run([sys.executable, "-I", script], input=json.dumps(base), capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(bare.returncode, 0)
        self.assertFalse(os.path.exists(self.feed_path()))
        keyed = dict(base, session_id="sess-sl-1", model={"id": "model-y"})
        subprocess.run([sys.executable, "-I", script], input=json.dumps(keyed), capture_output=True, text=True, env=dict(os.environ))
        with open(self.feed_path(), encoding="utf-8") as f:
            text = f.read()
        row = json.loads(text.splitlines()[0])
        self.assertEqual((row["source"], row["model"], row["kind"]), ("statusline", "model-y", "seven_day"))
        self.assertNotIn("sess-sl-1", text)

    def test_row18_no_export_retro_or_report_path_names_or_walks_the_home_state_log(self):
        # export.py and retromine.py never call home_state() (grep, round 2), so the home-state log cannot be copied by them
        scripts = os.path.join(os.path.dirname(BUDGIT), "..", "scripts")
        for name in ("export.py", "retromine.py"):
            with open(os.path.join(scripts, name), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("home_state(", text, name)
            self.assertNotIn("session-usage", text, name)


if __name__ == "__main__":
    unittest.main()

