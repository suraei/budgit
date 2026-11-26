"""R-1032: agent dumps (YYYYMMDD-HHMMSS-<agent>-<id>.md) go to <state>/archive/agent-dumps/<date>/ with a manifest line each;
`budgit reports sweep` moves the old ones out of reports/ (moves only); curated reports and codex answers are never touched."""
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")

OLD_DUMP = "20260101-120000-budgit-builder-abc12345.md"
OLD_DUMP_2 = "20260102-080000-Explore-ab99.md"
CURATED = ("2026-01-01-R-1022-second-brain.md", "2026-01-01-R-9-plan.md")
# hand-named files that look a little like a dump (a stamp in front) must never move: the id is not a hex agent id
HAND_NAMED = ("20261001-093000-R-1032-plan.md", "20261001-093000-security-review-r6.md", "20261025-emilkowalski-skills-security.md")
OTHER = ("20260101-notes.md", "20260101-120000.md", "20260101-120000-codex-review-default.md", "plain.md", "20260101-120000-x-y.txt") + HAND_NAMED


class AgentDumpsTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "b1")
        self.env = dict(os.environ, STUDIO_HOME=self.home)
        self.reports = os.path.join(sl.studio_dir(self.project), "reports")
        self.arch = os.path.join(sl.studio_dir(self.project), "archive", "agent-dumps")

    def put(self, *names):
        os.makedirs(self.reports, exist_ok=True)
        for n in names:
            with open(os.path.join(self.reports, n), "w") as fh:
                fh.write("body of " + n)

    def manifest(self, day=None):
        """The manifest lines of one day folder, or of every day folder (a manifest per day)."""
        days = [day] if day else sorted(d for d in os.listdir(self.arch) if os.path.isdir(os.path.join(self.arch, d)))
        rows = []
        for d in days:
            if not os.path.exists(os.path.join(self.arch, d, "manifest.jsonl")):
                continue                                 # a day folder made for a move that did not happen has no manifest
            with open(os.path.join(self.arch, d, "manifest.jsonl")) as fh:
                rows += [json.loads(line) for line in fh if line.strip()]
        return rows

    def tree(self):
        return sorted(os.path.join(b, f) for b, _d, fs in os.walk(sl.studio_dir(self.project)) for f in fs)

    # --- writer ---
    def test_the_writer_puts_a_dump_in_the_dated_archive_not_in_reports(self):
        path = sl.save_report(self.project, "budgit:builder", "acc3a08fe0", "all green")
        day = time.strftime("%Y-%m-%d")
        self.assertEqual(os.path.dirname(path), os.path.join(self.arch, day))
        self.assertRegex(os.path.basename(path), r"^\d{8}-\d{6}-budgit-builder-acc3a08f\.md$")
        self.assertFalse(os.path.exists(self.reports))

    def test_the_codex_answer_stays_in_reports(self):
        path = sl.save_report(self.project, "codex-review", "default", "verdict", keep=True)
        self.assertEqual(os.path.dirname(path), self.reports)
        self.assertFalse(os.path.exists(self.arch))                     # no archive folder, no manifest line

    # --- exact names: the writer's own format is the one the sweeper accepts ---
    def test_every_name_the_writer_makes_is_a_name_the_sweeper_accepts(self):
        for atype, aid in (("budgit:builder", "acc3a08fe0"), ("general-purpose", "0123456789abcdef"), (None, ""), ("../../evil", "abc")):
            path = sl.save_report(self.project, atype, aid, "x")
            self.assertIsNotNone(sl._AGENT_DUMP.match(os.path.basename(path)), path)
        self.assertIsNotNone(sl._AGENT_DUMP.match(sl._dump_name("20261111-120000", "budgit-builder", "abc12345")))
        self.assertIsNotNone(sl._AGENT_DUMP.match(sl._dump_name("20261111-120000", "budgit-builder", "abc12345", 3)))
        self.assertIsNone(sl._AGENT_DUMP.match(sl._dump_name("20261111-120000", "codex-review", "default")))

    def test_a_hand_named_file_with_a_stamp_never_moves(self):
        self.put(*HAND_NAMED)
        self.assertEqual(sl.sweep_reports(self.project, days=0), ([], []))
        self.assertEqual(sorted(os.listdir(self.reports)), sorted(HAND_NAMED))

    # --- collision ---
    def test_two_dumps_in_the_same_second_both_survive_with_one_manifest_line_each(self):
        real = time.strftime
        with mock.patch.object(sl._time, "strftime", side_effect=lambda f, *a: "20261111-120000" if f == sl._DUMP_STAMP else real(f, *a)):
            paths = [sl.save_report(self.project, "budgit:builder", "abc12345", "text %d" % i) for i in range(3)]
        self.assertEqual([os.path.basename(p) for p in paths],
                         ["20261111-120000-budgit-builder-abc12345.md", "20261111-120000-budgit-builder-abc12345-2.md",
                          "20261111-120000-budgit-builder-abc12345-3.md"])
        for i, p in enumerate(paths):
            with open(p) as fh:
                self.assertIn("text %d" % i, fh.read())
        rows = self.manifest("2026-11-11")
        self.assertEqual([r["file"] for r in rows], [os.path.basename(p) for p in paths])
        self.assertTrue(all(sl._AGENT_DUMP.match(os.path.basename(p)) for p in paths))

    # --- manifest ---
    def test_the_manifest_gets_one_line_per_dump_with_time_type_id_and_file(self):
        p1 = sl.save_report(self.project, "budgit:builder", "acc3a08x", "one")
        sl.save_report(self.project, "Explore", "zz99", "two")
        rows = self.manifest()
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), {"time", "agent_type", "agent_id", "file"})
        self.assertEqual((rows[0]["agent_type"], rows[0]["agent_id"]), ("budgit-builder", "acc3a08x"))
        self.assertEqual(rows[0]["file"], os.path.basename(p1))
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(p1), "manifest.jsonl")))      # the manifest sits in the day folder
        self.assertEqual(rows[1]["agent_type"], "Explore")
        self.assertTrue(rows[0]["time"])

    # --- sweeper ---
    def test_the_sweeper_moves_only_auto_named_files_older_than_n_days(self):
        fresh = time.strftime("%Y%m%d-%H%M%S") + "-budgit-builder-fff00000.md"
        self.put(OLD_DUMP, OLD_DUMP_2, fresh, *CURATED, *OTHER)
        moved, skipped = sl.sweep_reports(self.project, days=3)
        self.assertEqual(sorted(moved), sorted([OLD_DUMP, OLD_DUMP_2]))
        self.assertEqual(skipped, [])
        self.assertEqual(sorted(os.listdir(self.reports)), sorted([fresh, *CURATED, *OTHER]))
        self.assertTrue(os.path.isfile(os.path.join(self.arch, "2026-01-01", OLD_DUMP)))
        self.assertTrue(os.path.isfile(os.path.join(self.arch, "2026-01-02", OLD_DUMP_2)))
        with open(os.path.join(self.arch, "2026-01-01", OLD_DUMP)) as fh:
            self.assertEqual(fh.read(), "body of " + OLD_DUMP)               # moved, content intact
        rows = {r["file"]: r for r in self.manifest()}
        row = rows[OLD_DUMP]
        self.assertEqual((row["agent_type"], row["agent_id"], row["moved_from"]), ("budgit-builder", "abc12345", "reports/" + OLD_DUMP))
        self.assertTrue(row["time"].startswith("2026-01-01T12:00:00"))
        self.assertEqual([r["file"] for r in self.manifest("2026-01-02")], [OLD_DUMP_2])      # each day has its own manifest

    def test_a_file_that_vanishes_mid_sweep_is_skipped_and_the_rest_still_moves(self):
        self.put(OLD_DUMP, OLD_DUMP_2)
        real = os.rename

        def flaky(src, dst):
            if os.path.basename(src) == OLD_DUMP:
                raise FileNotFoundError(src)
            return real(src, dst)
        with mock.patch.object(os, "rename", side_effect=flaky):
            moved, skipped = sl.sweep_reports(self.project, days=3)
        self.assertEqual((moved, skipped), ([OLD_DUMP_2], [OLD_DUMP]))
        self.assertTrue(os.path.isfile(os.path.join(self.arch, "2026-01-02", OLD_DUMP_2)))
        self.assertEqual([r["file"] for r in self.manifest()], [OLD_DUMP_2])                  # no manifest line for the file that was not moved

    # --- automatic sweep: never in a protected folder, never without the lock ---
    def end_session(self):
        r = subprocess.run([sys.executable, HOOK, "session-end"], input=json.dumps({"session_id": "b1", "reason": "other"}),
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def set_protected(self, value):
        with open(os.path.join(self.home, "config.json"), "w") as fh:
            json.dump({"protected_dirs": value}, fh)

    def test_the_automatic_sweep_skips_a_protected_project_but_the_manual_one_works(self):
        self.set_protected([self.project])
        self.put(OLD_DUMP)
        self.end_session()
        self.assertTrue(os.path.exists(os.path.join(self.reports, OLD_DUMP)))                 # session end left it alone
        r = subprocess.run([sys.executable, CLI, "--project", self.project, "reports", "sweep"], env=self.env, capture_output=True,
                           text=True, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.reports, OLD_DUMP)))                # a manual sweep works everywhere

    def test_the_automatic_sweep_fails_closed_when_the_protected_list_is_unreadable(self):
        self.set_protected("not a list")
        self.put(OLD_DUMP)
        self.end_session()
        self.assertTrue(os.path.exists(os.path.join(self.reports, OLD_DUMP)))

    def test_the_automatic_sweep_runs_in_a_project_that_is_not_protected(self):
        self.set_protected([os.path.join(self.tmp, "some-other-folder")])
        self.put(OLD_DUMP)
        self.end_session()
        self.assertTrue(os.path.exists(os.path.join(self.arch, "2026-01-01", OLD_DUMP)))

    def test_the_busy_lock_path_never_sweeps(self):
        import hook
        self.put(OLD_DUMP)
        hook.on_session_end({"session_id": "b1", "reason": "other"}, self.project, held=False)    # when_busy: no project lock
        self.assertTrue(os.path.exists(os.path.join(self.reports, OLD_DUMP)))

    def test_the_sweeper_threshold_is_the_day_count(self):
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - 2 * 86400)) + "-x-aa11.md"
        self.put(stamp)
        self.assertEqual(sl.sweep_reports(self.project, days=3, dry_run=True)[0], [])
        self.assertEqual(sl.sweep_reports(self.project, days=1, dry_run=True)[0], [stamp])

    def test_dry_run_moves_nothing_and_writes_nothing(self):
        self.put(OLD_DUMP, OLD_DUMP_2, *CURATED)
        before = self.tree()
        moved, _ = sl.sweep_reports(self.project, days=3, dry_run=True)
        self.assertEqual(sorted(moved), sorted([OLD_DUMP, OLD_DUMP_2]))
        self.assertEqual(self.tree(), before)
        self.assertFalse(os.path.exists(self.arch))

    def test_a_curated_report_is_never_moved_even_at_zero_days(self):
        self.put(*CURATED, *OTHER)
        before = sorted(os.listdir(self.reports))
        self.assertEqual(sl.sweep_reports(self.project, days=0), ([], []))
        self.assertEqual(sorted(os.listdir(self.reports)), before)

    def test_a_dump_whose_target_exists_is_left_in_place_not_overwritten(self):
        self.put(OLD_DUMP)
        os.makedirs(os.path.join(self.arch, "2026-01-01"))
        with open(os.path.join(self.arch, "2026-01-01", OLD_DUMP), "w") as fh:
            fh.write("already there")
        moved, skipped = sl.sweep_reports(self.project, days=3)
        self.assertEqual((moved, skipped), ([], [OLD_DUMP]))
        self.assertTrue(os.path.exists(os.path.join(self.reports, OLD_DUMP)))
        with open(os.path.join(self.arch, "2026-01-01", OLD_DUMP)) as fh:
            self.assertEqual(fh.read(), "already there")

    def test_a_symlinked_reports_folder_is_not_swept(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, OLD_DUMP), "w") as fh:
            fh.write("x")
        os.symlink(outside, self.reports)
        with self.assertRaises(sl.StudioError):
            sl.sweep_reports(self.project, days=0)
        self.assertEqual(os.listdir(outside), [OLD_DUMP])

    # --- readers ---
    def test_readers_find_a_fresh_dump_and_a_swept_dump_in_the_new_place(self):
        path = sl.save_report(self.project, "budgit:researcher", "cccc3333", "finding")
        self.put(OLD_DUMP, *CURATED)
        sl.sweep_reports(self.project, days=3)
        names = sl.recent_reports(self.project, n=10000)
        self.assertIn("archive/agent-dumps/%s/%s" % (time.strftime("%Y-%m-%d"), os.path.basename(path)), names)
        self.assertIn("archive/agent-dumps/2026-01-01/" + OLD_DUMP, names)
        self.assertTrue(set(CURATED) <= set(names))
        self.assertTrue(any("cccc3333" in n for n in names))                      # what comply.research_unsaved looks for
        newest = sl.recent_reports(self.project, n=1)
        self.assertEqual(len(newest), 1)

    def test_the_session_start_summary_lists_the_dumps_of_the_archive(self):
        sl.save_report(self.project, "budgit:verifier", "q1q1q1q1", "PASS")
        summary = sl.session_summary(self.project, "b1", full=True)
        self.assertIn("verifier", summary)
        self.assertIn("archive/agent-dumps", summary)

    # --- the command and the session-end hook ---
    def test_the_command_dry_run_then_real_sweep(self):
        self.put(OLD_DUMP, *CURATED)
        base = [sys.executable, CLI, "--project", self.project, "reports", "sweep"]
        r = subprocess.run(base + ["--dry-run"], env=self.env, capture_output=True, text=True, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would move 1", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.reports, OLD_DUMP)))
        r = subprocess.run(base, env=self.env, capture_output=True, text=True, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("moved 1", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.reports, OLD_DUMP)))
        self.assertTrue(os.path.exists(os.path.join(self.arch, "2026-01-01", OLD_DUMP)))
        self.assertTrue(all(os.path.exists(os.path.join(self.reports, c)) for c in CURATED))

    def test_session_end_runs_the_sweep_behind_the_same_pattern(self):
        self.put(OLD_DUMP, *CURATED, *OTHER)
        r = subprocess.run([sys.executable, HOOK, "session-end"], input=json.dumps({"session_id": "b1", "reason": "other"}),
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.arch, "2026-01-01", OLD_DUMP)))
        self.assertEqual(sorted(os.listdir(self.reports)), sorted([*CURATED, *OTHER]))


if __name__ == "__main__":
    unittest.main()
