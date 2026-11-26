"""R-311: `budgit ledger repair-done-at`. Synthetic ledgers in throwaway git repos only; no real project is opened.

A ledger whose old closures were all stamped `done_at` = "that day" (a bulk stamp) inflates the week bar and the "Hecho"
list. The repair moves such a `done_at` only EARLIER, and only to a time the git history of the ledger proves; what git
cannot prove is marked `done_at_unproven` and the panel skips it. Nothing ever guesses "now"."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT, patch_env
import donerepair
import livework
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


class RepairCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.t = int(time.time())
        self.items = []

    # ---- fixtures
    def git(self, *args, when=None):
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        if when:
            env.update(GIT_AUTHOR_DATE="%d +0000" % when, GIT_COMMITTER_DATE="%d +0000" % when)
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                            "-C", self.project, *args], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def repo(self):
        self.git("init", "-q")

    def item(self, n, status="done", done_at=None, updated="2026-09-01T00:00:00.000Z", **extra):
        it = {"id": "R-%d" % n, "src": [], "text": "texto sintetico %d" % n, "title": "Titulo sintetico %d" % n,
              "status": status, "evidence": "e" if status in ("done", "verified") else "", "phase": "build",
              "created": "2026-09-01T00:00:00.000Z", "updated": updated}
        if done_at:
            it["done_at"] = done_at
        it.update(extra)
        return it

    def write(self, items):
        os.makedirs(os.path.join(self.project, self.DATA), exist_ok=True)
        data = sl.empty_ledger()
        data["items"] = items
        sl.save_ledger(self.project, data)

    def commit(self, when, message="ledger"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, when=when)

    def burst(self, first, count, at):
        """`count` closed items stamped within a minute of `at` (the bulk-stamp signature)."""
        return [self.item(first + k, done_at=iso(at + k * 2)) for k in range(count)]

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI, "ledger", "repair-done-at", "--project", self.project, *args],
                              capture_output=True, text=True, env=dict(os.environ))

    def ledger(self):
        return {i["id"]: i for i in sl.load_ledger(self.project)["items"]}

    def raw(self):
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "rb") as f:
            return f.read()

    def legacy_history(self, count=12):
        """The first commit holds `count` closed items (legacy: no done_at); later a bulk stamp writes done_at = a few minutes ago."""
        self.repo()
        self.first = self.t - 10 * 86400
        self.write([self.item(n) for n in range(1, count + 1)])
        self.commit(self.first, "first")
        self.stamp = self.t - 300
        self.write(self.burst(1, count, self.stamp))
        self.commit(self.t - 100, "bulk stamp")


class DryRunTest(RepairCase):
    def test_dry_run_prints_counts_only_and_writes_nothing(self):
        self.legacy_history()
        self.write(self.burst(1, 12, self.stamp) + [self.item(40, done_at=iso(self.t - 5 * 86400)),
                                                    self.item(41, status="open")])
        self.commit(self.t - 90, "more")
        names = lambda: sorted(f for f in os.listdir(sl.studio_dir(self.project)) if f != ".lock")
        before, files = self.raw(), names()
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.raw(), before)
        self.assertEqual(names(), files)                              # no backup, no marker file
        for text in ("Titulo sintetico", "R-1", "R-40", "texto sintetico", self.project):
            self.assertNotIn(text, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"(?i)dry run")
        self.assertRegex(r.stdout, r"would move\D*12\b")
        self.assertRegex(r.stdout, r"unproven\D*0\b")
        self.assertRegex(r.stdout, r"already fine\D*1\b")
        self.assertIn("--apply", r.stdout)

    def test_counts_for_the_three_outcomes_together(self):
        self.legacy_history()                                        # R-1..R-12 stamped in a burst, git proves an old close
        self.write(self.burst(1, 12, self.stamp) + [self.item(30 + k, done_at=iso(self.t - 3000 + k)) for k in range(11)])   # R-30..40: a burst git never saw
        self.commit(self.t - 80, "burst nobody committed before")
        # the 11 new ones were first seen closed at t-80, after their stamp: git cannot prove their time
        out = donerepair.plan(self.project)
        self.assertEqual((out["move"], out["unproven"], out["fine"]), (12, 11, 0))


class MoveTest(RepairCase):
    def test_apply_moves_done_at_to_the_commit_time_git_proves(self):
        self.legacy_history()
        r = self.run_cli("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        items = self.ledger()
        for n in range(1, 13):
            self.assertEqual(items["R-%d" % n]["done_at"], iso(self.first), "R-%d" % n)
            self.assertNotIn("done_at_unproven", items["R-%d" % n])

    def test_a_done_at_is_only_ever_moved_earlier(self):
        self.repo()
        self.write([self.item(1), self.item(2)])                      # closed (no done_at) at the first commit
        self.commit(self.t - 10 * 86400)
        # R-1: done_at already EARLIER than the proof (a hand-set time) must stay; R-2: later than the proof moves
        early = iso(self.t - 20 * 86400)
        self.write([self.item(1, done_at=early), self.item(2, done_at=iso(self.t - 86400))])
        self.commit(self.t - 60)
        self.run_cli("--apply")
        items = self.ledger()
        self.assertEqual(items["R-1"]["done_at"], early)
        self.assertEqual(items["R-2"]["done_at"], iso(self.t - 10 * 86400))

    def test_an_item_closed_for_real_between_two_commits_is_left_alone(self):
        self.repo()
        self.write([self.item(n, status="doing") for n in range(1, 13)])       # seen open at the first commit
        self.commit(self.t - 3600)
        real = self.t - 1800                                                    # closed, in a burst, before the next commit
        self.write(self.burst(1, 12, real))
        self.commit(self.t - 600)
        before = self.raw()
        r = self.run_cli("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ledger(), {i["id"]: i for i in json.loads(before)["items"]})
        for it in self.ledger().values():
            self.assertNotIn("done_at_unproven", it)

    def test_a_ledger_migrated_between_data_folder_names_is_read_across_both(self):
        self.repo()
        old = os.path.join(self.project, ".studio")
        os.makedirs(old)
        data = sl.empty_ledger()
        data["items"] = [self.item(n) for n in range(1, 13)]
        with open(os.path.join(old, "ledger.json"), "w") as f:
            json.dump(data, f)
        self.commit(self.t - 10 * 86400)
        self.git("mv", ".studio", ".budgit")
        data["items"] = self.burst(1, 12, self.t - 300)
        with open(os.path.join(self.project, ".budgit", "ledger.json"), "w") as f:
            json.dump(data, f)
        self.commit(self.t - 100)
        self.assertEqual(os.path.basename(sl.studio_dir(self.project)), ".budgit")
        self.run_cli("--apply")
        self.assertEqual(self.ledger()["R-3"]["done_at"], iso(self.t - 10 * 86400))


class UnprovenTest(RepairCase):
    def test_a_burst_git_cannot_prove_is_marked_and_keeps_its_time(self):
        self.repo()
        self.write(self.burst(1, 12, self.t - 300) + [self.item(40, done_at=iso(self.t - 6 * 86400))])
        self.commit(self.t - 100)                                     # first seen closed AFTER the stamp: nothing earlier to prove
        r = self.run_cli("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        items = self.ledger()
        for n in range(1, 13):
            self.assertTrue(items["R-%d" % n]["done_at_unproven"])
            self.assertEqual(items["R-%d" % n]["done_at"], iso(self.t - 300 + (n - 1) * 2))   # never guessed, never touched
        self.assertNotIn("done_at_unproven", items["R-40"])           # a lone closure outside any burst is left alone

    def test_without_git_a_burst_is_unproven_and_nothing_else_changes(self):
        self.write(self.burst(1, 12, self.t - 300) + [self.item(40, done_at=iso(self.t - 6 * 86400))])
        self.assertFalse(os.path.isdir(os.path.join(self.project, ".git")))
        out = donerepair.plan(self.project)
        self.assertEqual((out["move"], out["unproven"], out["fine"]), (0, 12, 1))
        self.assertEqual(out["history"], 0)

    def test_an_untracked_ledger_in_a_repo_counts_as_no_history(self):
        self.repo()
        self.write(self.burst(1, 12, self.t - 300))
        self.assertEqual(donerepair.plan(self.project)["unproven"], 12)

    def test_fewer_than_ten_closures_close_together_are_not_a_burst(self):
        self.write(self.burst(1, 9, self.t - 300))
        out = donerepair.plan(self.project)
        self.assertEqual((out["move"], out["unproven"], out["fine"]), (0, 0, 9))

    def test_an_item_with_only_updated_is_judged_by_updated(self):
        self.write([self.item(n, updated=iso(self.t - 200 + n)) for n in range(1, 13)])      # no done_at: the feed uses `updated`
        out = donerepair.plan(self.project)
        self.assertEqual(out["unproven"], 12)
        self.run_cli("--apply")
        it = self.ledger()["R-1"]
        self.assertTrue(it["done_at_unproven"])
        self.assertNotIn("done_at", it)                               # no time is invented for it


class ApplyAndRollbackTest(RepairCase):
    def test_apply_keeps_a_backup_prints_one_rollback_line_and_keeps_the_ledger_consistent(self):
        self.legacy_history()
        before = self.raw()
        r = self.run_cli("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        backups = [f for f in os.listdir(sl.studio_dir(self.project)) if f.startswith("ledger.json.bak-done-at-")]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(sl.studio_dir(self.project), backups[0]), "rb") as f:
            self.assertEqual(f.read(), before)
        rollback = [l for l in r.stdout.splitlines() if "--rollback" in l]
        self.assertEqual(len(rollback), 1)
        self.assertIn(backups[0], rollback[0])
        self.assertFalse(sl.ledger_tampered(self.project))
        self.assertTrue(os.path.exists(os.path.join(sl.studio_dir(self.project), "ledger.md")))
        for text in ("Titulo sintetico", "texto sintetico"):
            self.assertNotIn(text, r.stdout)

    def test_a_second_apply_changes_nothing(self):
        self.legacy_history()
        self.run_cli("--apply")
        after = self.raw()
        r = self.run_cli("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.raw(), after)
        self.assertRegex(r.stdout, r"would move\D*0\b|moved\D*0\b")
        self.assertEqual(len([f for f in os.listdir(sl.studio_dir(self.project)) if f.startswith("ledger.json.bak-done-at-")]), 1)

    def test_rollback_restores_the_times_and_keeps_changes_made_since(self):
        self.legacy_history()
        before = self.ledger()
        out = self.run_cli("--apply").stdout
        name = [f for f in os.listdir(sl.studio_dir(self.project)) if f.startswith("ledger.json.bak-done-at-")][0]
        data = sl.load_ledger(self.project)                             # a change made after the repair
        data["items"].append(self.item(99, status="open"))
        data["items"][0]["title"] = "Titulo editado despues"
        sl.save_ledger(self.project, data)
        r = self.run_cli("--rollback", os.path.join(sl.studio_dir(self.project), name))
        self.assertEqual(r.returncode, 0, r.stderr)
        now = self.ledger()
        for n in range(1, 13):
            self.assertEqual(now["R-%d" % n]["done_at"], before["R-%d" % n]["done_at"])
        self.assertIn("R-99", now)
        self.assertEqual(now["R-1"]["title"], "Titulo editado despues")
        self.assertFalse(sl.ledger_tampered(self.project))

    def test_rollback_refuses_a_file_that_is_not_one_of_its_own_backups(self):
        self.legacy_history()
        stray = os.path.join(self.tmp, "ledger.json.bak-done-at-20260101T000000Z")
        with open(stray, "w") as f:
            f.write(self.raw().decode())
        r = self.run_cli("--rollback", stray)
        self.assertNotEqual(r.returncode, 0)
        r = self.run_cli("--rollback", os.path.join(sl.studio_dir(self.project), "ledger.json"))
        self.assertNotEqual(r.returncode, 0)

    def test_no_time_equal_to_now_is_ever_written(self):
        self.legacy_history()
        self.write(self.burst(1, 12, self.stamp) + self.burst(30, 11, self.t - 250))
        self.commit(self.t - 70)
        stamp_before = {i["id"]: i.get("done_at") for i in sl.load_ledger(self.project)["items"]}
        self.run_cli("--apply")
        for i in sl.load_ledger(self.project)["items"]:
            if i.get("done_at") != stamp_before[i["id"]]:
                self.assertLess(livework.epoch(i["done_at"]), self.t - 86400)        # moved to a commit time, days back


class FeedSkipsUnprovenTest(RepairCase):
    def setUp(self):
        super().setUp()
        self.uhome = os.path.join(self.tmp, "uhome")
        os.makedirs(os.path.join(self.uhome, ".claude", "sessions"))
        patch_env(self, HOME=self.uhome, CLAUDE_CONFIG_DIR=os.path.join(self.uhome, ".claude"))

    def feed(self, private=False):
        if private:
            with open(os.path.join(sl.studio_dir(self.project), "project.json"), "w") as f:
                json.dump({"private": True}, f)
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"hub": self.project}, f)
        r = subprocess.run([sys.executable, CLI, "status", "--brote", "--live", "--project", self.project],
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def mixed(self):
        now = self.t - 60
        self.write([self.item(1, done_at=iso(now), done_at_unproven=True, title="No probado"),
                    self.item(2, done_at=iso(now - 30), title="Probado"),
                    self.item(3, status="verified", updated=iso(now - 40), done_at_unproven=True, title="Sin hora")])

    def test_unproven_items_leave_the_week_bar_and_the_hecho_list_but_stay_in_the_counts(self):
        self.mixed()
        out = self.feed()
        self.assertEqual([r["item"] for r in out["recent_done"]], ["R-2"])
        self.assertEqual(sum(out["week"]), 1)
        self.assertEqual(out["projects"][0]["counts"]["done"], 3)

    def test_the_same_holds_for_a_private_project(self):
        self.mixed()
        out = self.feed(private=True)
        self.assertEqual(out["recent_done"], [])
        self.assertEqual(sum(out["week"]), 1)
        self.assertEqual(out["projects"][0]["counts"]["done"], 3)


class CliShapeTest(RepairCase):
    def test_apply_and_rollback_cannot_be_combined_and_unknown_flags_fail(self):
        self.legacy_history()
        self.assertNotEqual(self.run_cli("--apply", "--rollback", "x").returncode, 0)
        self.assertNotEqual(self.run_cli("--nope").returncode, 0)

    def test_an_unreadable_history_commit_is_skipped_not_fatal(self):
        self.repo()
        self.write([self.item(n) for n in range(1, 13)])
        self.commit(self.t - 10 * 86400)
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "w") as f:
            f.write("{ not json")
        self.commit(self.t - 5 * 86400, "broken")
        self.write(self.burst(1, 12, self.t - 300))
        self.commit(self.t - 100)
        out = donerepair.plan(self.project)
        self.assertEqual(out["move"], 12)

    def test_a_history_blob_nested_past_the_recursion_limit_is_skipped_not_fatal(self):
        self.repo()
        os.makedirs(sl.studio_dir(self.project), exist_ok=True)
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "w") as f:
            f.write("[" * 1100 + "]" * 1100)
        self.commit(self.t - 10 * 86400)
        self.write(self.burst(1, 12, self.t - 300))
        self.commit(self.t - 100)
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(donerepair.plan(self.project)["unproven"], 12)

    def test_a_backup_nested_past_the_recursion_limit_is_refused_cleanly(self):
        self.legacy_history()
        path = os.path.join(sl.studio_dir(self.project), "ledger.json.bak-done-at-20260101T000000Z")
        with open(path, "w") as f:
            f.write("[" * 1100 + "]" * 1100)
        r = self.run_cli("--rollback", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_backup_with_malformed_items_is_refused_cleanly(self):
        self.legacy_history()
        path = os.path.join(sl.studio_dir(self.project), "ledger.json.bak-done-at-20260101T000001Z")
        with open(path, "w") as f:
            json.dump({"items": [1], "acks": {}}, f)
        r = self.run_cli("--rollback", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    import unittest
    unittest.main()
