"""R-400: `budgit housekeeping` -- the flow count and the removal of merged, clean, idle worktrees and branches. Temp repos only;
the clock is moved forward instead of ageing files, so nothing here waits and nothing depends on the machine's load."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from unittest import mock

from helpers import BudgitTestCase, ROOT
import housekeeping
import studiolib as sl

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
DAY = 86400


class HkCase(BudgitTestCase):
    def setUp(self):
        super().setUp()
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        GIT_CONFIG_NOSYSTEM="1")
        for key, value in self.env.items():
            if key.startswith("GIT_"):
                self.addCleanup(lambda k=key, o=os.environ.get(key): os.environ.pop(k, None) if o is None else os.environ.update({k: o}))
                os.environ[key] = value
        self.git("init", "-q", "-b", "main")
        self.init_project()
        with open(os.path.join(self.project, ".git", "info", "exclude"), "a") as f:
            f.write(".budgit/\n.studio/\n")
        self.write_file("a.txt", "a\n")
        self.commit("start")
        self.config(projects_root=self.tmp)
        self.later = time.time() + 3 * DAY                                  # "three days from now": everything made now is idle

    def config(self, **values):
        os.makedirs(self.home, exist_ok=True)
        path = os.path.join(self.home, "config.json")
        data = {}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        data.update(values)
        with open(path, "w") as f:
            json.dump(data, f)

    def git(self, *args, cwd=None, check=True):
        r = subprocess.run(["git", "-C", cwd or self.project] + list(args), capture_output=True, text=True, env=self.env)
        if check and r.returncode:
            raise AssertionError("git %s: %s%s" % (" ".join(args), r.stdout, r.stderr))
        return r

    def write_file(self, rel, text, root=None):
        path = os.path.join(root or self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def commit(self, msg, cwd=None):
        self.git("add", "-A", cwd=cwd)
        self.git("commit", "-q", "-m", msg, cwd=cwd)

    def lane(self, name, merged=True, dirty=False, commit=True):
        """A worktree next to the project on branch `name`; merged into main when `merged`."""
        path = os.path.join(self.tmp, "wt-" + name.replace("/", "-"))
        self.git("worktree", "add", "-q", "-b", name, path, "main")
        if commit:
            self.write_file(name.replace("/", "_") + ".txt", "x\n", root=path)
            self.commit("work on " + name, cwd=path)
        if merged and commit:
            self.git("merge", "-q", "--ff-only", name)
        if dirty:
            self.write_file("scratch.txt", "wip\n", root=path)
        return path

    def run_hk(self, apply=False, now=None, **kw):
        return housekeeping.run(apply_changes=apply, now=now or self.later, busy=(False, "free"), live=kw.pop("live", []), **kw)

    def branches(self):
        return set(self.git("branch", "--format=%(refname:short)").stdout.split())

    def cli(self, *args, expect=0, **env):
        r = subprocess.run([sys.executable, BUDGIT, "housekeeping", *args], capture_output=True, text=True,
                           env=dict(self.env, STUDIO_HOME=self.home, BUDGIT_LANG="en", BUDGIT_BUSY_WINDOW="1", **env))
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r


class FlowTest(HkCase):
    def test_the_count_of_the_last_day_and_the_next_version(self):
        data = sl.load_ledger(self.project)
        old = "2020-01-01T00:00:00.000Z"
        for text, status, phase, created, done in (("one", "open", "0.1", None, None), ("two", "open", "0.1", None, None),
                                                   ("three", "doing", "0.2", None, None), ("four", "done", "0.1", None, "now"),
                                                   ("five", "done", "0.1", old, old), ("six", "dropped", "0.1", None, "now")):
            sl.add_item(data, text, "user", phase=phase)
            item = data["items"][-1]
            item["status"] = status
            if created:
                item["created"] = created
            if done:
                item["done_at"] = sl.now() if done == "now" else done
        sl.save_ledger(self.project, data)
        f = housekeeping.flow(self.project, time.time())
        self.assertEqual((f["made"], f["shut"], f["pending"], f["next"], f["next_count"]), (5, 2, 3, "0.1", 2))

    def test_a_ledger_that_cannot_be_read_is_a_line_not_a_crash(self):
        with open(os.path.join(self.project, self.DATA, "ledger.json"), "w") as f:
            f.write("{nope")
        report = self.run_hk()
        text = housekeeping.render(report, "en")
        self.assertIn("flow count could not be read", text)
        self.assertEqual(report["projects"][0]["flow"], None)

    def test_the_rule_line_appears_only_when_more_came_in_than_went_out(self):
        text = housekeeping.render(self.run_hk(now=time.time()), "en")
        self.assertNotIn("flow rule", text)
        data = sl.load_ledger(self.project)
        sl.add_item(data, "new", "user", phase="0.1")
        sl.save_ledger(self.project, data)
        text = housekeeping.render(self.run_hk(now=time.time()), "en")
        self.assertIn("flow rule: in proj", text)


class TidyTest(HkCase):
    def test_a_merged_clean_idle_worktree_goes_with_its_branch_and_nothing_else(self):
        gone = self.lane("done-lane")
        report = self.run_hk(apply=True)
        self.assertFalse(os.path.exists(gone))
        self.assertNotIn("done-lane", self.branches())
        self.assertIn("main", self.branches())
        self.assertEqual(report["totals"]["removed"], 1)
        self.assertEqual(report["totals"]["deleted"], 1)

    def test_without_apply_it_only_says_what_it_would_do(self):
        path = self.lane("done-lane")
        report = self.run_hk(apply=False)
        self.assertTrue(os.path.isdir(path))
        self.assertIn("done-lane", self.branches())
        text = housekeeping.render(report, "en")
        self.assertIn("would remove worktree", text)
        self.assertIn("dry run", text)

    def test_unmerged_dirty_locked_and_live_ones_stay(self):
        unmerged = self.lane("unmerged-lane", merged=False)
        dirty = self.lane("dirty-lane", dirty=True)
        locked = self.lane("locked-lane")
        self.git("worktree", "lock", locked)
        done = self.lane("done-lane")
        live = self.lane("live-lane")
        report = self.run_hk(apply=True, live=[os.path.realpath(live)])
        reasons = {os.path.basename(k["path"]): k["reason"] for k in report["projects"][0]["git"]["kept"]}
        self.assertEqual(reasons["wt-unmerged-lane"], "unmerged")
        self.assertEqual(reasons["wt-dirty-lane"], "dirty")
        self.assertEqual(reasons["wt-locked-lane"], "locked")
        self.assertEqual(reasons["wt-live-lane"], "live")
        for path in (unmerged, dirty, locked, live):
            self.assertTrue(os.path.isdir(path), path)
        self.assertFalse(os.path.exists(done))

    def test_when_the_live_sessions_cannot_be_read_every_worktree_stays(self):
        path = self.lane("done-lane")
        real = housekeeping.live_cwds
        housekeeping.live_cwds = lambda: None
        self.addCleanup(lambda: setattr(housekeeping, "live_cwds", real))
        report = self.run_hk(apply=True, live=None)
        self.assertTrue(os.path.isdir(path))
        self.assertIn("done-lane", self.branches())
        kept = report["projects"][0]["git"]["kept"]
        self.assertEqual([(os.path.basename(k["path"]), k["reason"]) for k in kept], [("wt-done-lane", "unknownlive")])
        self.assertEqual(report["totals"]["removed"], 0)
        self.assertIn("live sessions", housekeeping.render(report, "en"))

    def test_a_worktree_with_an_ignored_file_stays_because_removing_it_would_delete_that_file(self):
        with open(os.path.join(self.project, ".git", "info", "exclude"), "a") as f:
            f.write(".env\n")
        path = self.lane("env-lane")
        self.write_file(".env", "TOKEN=local\n", root=path)
        report = self.run_hk(apply=True)
        self.assertTrue(os.path.isfile(os.path.join(path, ".env")))
        kept = report["projects"][0]["git"]["kept"][0]
        self.assertEqual((kept["reason"], kept["candidate"]), ("ignored", False))
        self.assertIn("env-lane", self.branches())

    def test_an_ignored_file_in_a_folder_with_an_odd_name_still_keeps_the_worktree(self):
        with open(os.path.join(self.project, ".git", "info", "exclude"), "a") as f:
            f.write(".env\n")
        for n, folder in enumerate(("notas-\u00e9", "with space", "tab\there", "new\nline")):
            path = self.lane("odd-lane-%d" % n)
            try:
                self.write_file(os.path.join(folder, ".env"), "TOKEN=local\n", root=path)
            except OSError:
                continue                                         # this filesystem does not take that name
            self.assertTrue(housekeeping.has_ignored_files(path), repr(folder))
            self.run_hk(apply=True)
            self.assertTrue(os.path.isfile(os.path.join(path, folder, ".env")), repr(folder))

    def test_a_status_that_fails_or_names_a_missing_entry_keeps_the_worktree(self):
        path = self.lane("fail-lane")
        real = housekeeping.git
        housekeeping.git = lambda p, *a: (128, "", "boom") if "--ignored" in a else real(p, *a)
        self.addCleanup(lambda: setattr(housekeeping, "git", real))
        self.assertTrue(housekeeping.has_ignored_files(path))
        housekeeping.git = lambda p, *a: (0, "!! ghost/\0", "") if "--ignored" in a else real(p, *a)
        self.assertTrue(housekeeping.has_ignored_files(path))

    def test_only_regenerable_ignored_files_do_not_keep_a_worktree(self):
        with open(os.path.join(self.project, ".git", "info", "exclude"), "a") as f:
            f.write("__pycache__/\n*.pyc\n.DS_Store\n")
        path = self.lane("cache-lane")
        self.write_file("pkg/__pycache__/m.cpython-312.pyc", "x", root=path)
        self.write_file(".DS_Store", "x", root=path)
        os.utime(os.path.join(path, "pkg", "__pycache__", "m.cpython-312.pyc"), (1, 1))
        for rel in ("pkg/__pycache__", "pkg", ".DS_Store", "cache-lane.txt", ""):
            os.utime(os.path.join(path, rel) if rel else path, (1, 1))
        self.run_hk(apply=True)
        self.assertFalse(os.path.exists(path))

    def test_a_worktree_touched_within_the_window_stays(self):
        path = self.lane("fresh-lane")
        report = self.run_hk(apply=True, now=time.time() + 3600)
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(report["projects"][0]["git"]["kept"][0]["reason"], "recent")
        self.assertIn("fresh-lane", self.branches())

    def test_old_dirty_and_unmerged_ones_are_drop_candidates_not_removals(self):
        self.lane("dirty-lane", dirty=True)
        report = self.run_hk(apply=True, now=time.time() + 5 * DAY)
        kept = report["projects"][0]["git"]["kept"][0]
        self.assertEqual((kept["reason"], kept["candidate"]), ("dirty", True))
        self.assertIn("drop-list candidates", housekeeping.render(report, "en"))

    def test_other_merged_branches_go_but_main_backup_and_checked_out_ones_stay(self):
        for name in ("old-merged", "backup/keep-me"):
            self.git("branch", name, "main")
        self.git("checkout", "-q", "-b", "main-checkout-branch")
        self.run_hk(apply=True)
        left = self.branches()
        self.assertEqual(left, {"main", "backup/keep-me", "main-checkout-branch"})

    def test_a_second_checkout_of_main_is_kept_and_main_is_never_deleted(self):
        self.git("checkout", "-q", "-b", "side")                           # the primary checkout leaves main
        other = os.path.join(self.tmp, "wt-main")
        self.git("worktree", "add", "-q", other, "main")
        report = self.run_hk(apply=True)
        self.assertTrue(os.path.isdir(other))
        self.assertIn("main", self.branches())
        self.assertEqual(report["projects"][0]["git"]["kept"][0]["reason"], "base")
        self.assertEqual(report["totals"]["removed"], 0)

    def test_a_brand_new_branch_is_not_deleted_for_being_merged(self):
        self.git("branch", "just-made", "main")
        self.run_hk(apply=True, now=time.time() + 3600)
        self.assertIn("just-made", self.branches())

    def test_it_never_uses_force_and_a_refusal_is_listed_not_hidden(self):
        path = self.lane("done-lane")
        real = housekeeping.git

        def refuse(p, *args):
            if args[:2] == ("worktree", "remove"):
                self.assertNotIn("--force", args)
                return 1, "", "fatal: refused for the test"
            return real(p, *args)
        housekeeping.git = refuse
        self.addCleanup(lambda: setattr(housekeeping, "git", real))
        report = self.run_hk(apply=True)
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(report["totals"]["failed"], 1)
        self.assertIn("could not remove", housekeeping.render(report, "en"))

    def test_a_busy_machine_gets_the_flow_count_and_no_git_changes(self):
        path = self.lane("done-lane")
        report = housekeeping.run(apply_changes=True, now=self.later, busy=(True, "median CPU 95 % is above 85 %"), live=[])
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(report["git_skipped"])
        self.assertEqual(housekeeping.exit_code(report), housekeeping.EXIT_SKIPPED)
        self.assertIn("machine busy (median CPU 95 %", housekeeping.skip_reason(report))
        dry = housekeeping.run(apply_changes=False, now=self.later, busy=(True, "x"), live=[])
        self.assertEqual(housekeeping.exit_code(dry), 0)                     # a dry run stays 0
        self.assertIsNotNone(report["projects"][0]["flow"])
        self.assertIn("only the flow count ran", housekeeping.render(report, "en"))

    def test_i1395_a_high_load_average_with_idle_cpu_is_not_busy_and_the_stale_lane_is_removed(self):
        path = self.lane("done-lane")
        real = os.getloadavg
        os.getloadavg = lambda: (13.1, 12.0, 11.0)
        self.addCleanup(setattr, os, "getloadavg", real)
        import admit
        busy = admit.is_busy(sampler=lambda: 5.0, sleep=lambda s: None, window=5, free=lambda: 9000)
        self.assertEqual(busy[0], False)
        report = housekeeping.run(apply_changes=True, now=self.later, busy=busy, live=[])
        self.assertFalse(os.path.isdir(path))                                 # the normal path: the planted stale item is gone
        self.assertEqual(housekeeping.exit_code(report), 0)
        self.assertIsNone(housekeeping.skip_reason(report))

    def test_i1395_busy_follows_the_shared_reading_cpu_or_memory(self):
        import admit
        self.assertTrue(admit.is_busy(sampler=lambda: 95.0, sleep=lambda s: None, window=5, free=lambda: 9000)[0])
        self.assertTrue(admit.is_busy(sampler=lambda: 5.0, sleep=lambda s: None, window=5, free=lambda: 500)[0])
        real = housekeeping.busy_now
        self.assertIs(real.__module__, "housekeeping")
        housekeeping.busy_now = lambda: (True, "free memory 500 MB is under 1024 MB")
        self.addCleanup(setattr, housekeeping, "busy_now", real)
        self.assertTrue(housekeeping.run(now=self.later, live=[])["git_skipped"])    # no injection: run asks the one shared reading

    def test_i1395_apply_with_nothing_to_clean_exits_with_the_named_code_and_a_reason(self):
        report = housekeeping.run(apply_changes=True, now=self.later, busy=(False, "free"), live=[])
        self.assertEqual(housekeeping.exit_code(report), 0)                  # nothing to clean is success; only a busy skip is 11
        self.assertEqual(housekeeping.skip_reason(report), "nothing to clean")

    def test_a_repo_with_no_main_or_master_is_left_alone(self):
        self.git("branch", "-m", "main", "trunk")
        report = self.run_hk(apply=True)
        self.assertEqual(report["projects"][0]["git"].get("skip"), "nobase")


class ProjectsTest(HkCase):
    def test_protected_and_private_projects_are_counted_and_never_named(self):
        secret = os.path.join(self.tmp, "hidden-project")
        os.makedirs(os.path.join(secret, self.DATA))
        sl.save_ledger(secret, sl.empty_ledger())
        subprocess.run(["git", "-C", secret, "init", "-q", "-b", "main"], env=self.env, check=True)
        self.config(protected_dirs=[secret])
        report = self.run_hk()
        names = [r["name"] for r in report["projects"]]
        self.assertEqual(names, ["proj"])
        self.assertGreaterEqual(report["left_alone"], 1)
        text = housekeeping.render(report, "en")
        self.assertNotIn("hidden-project", text)
        self.assertNotIn("hidden-project", json.dumps(report))

    def test_a_project_marked_private_in_its_own_file_is_left_alone(self):
        private = os.path.join(self.tmp, "second")
        os.makedirs(os.path.join(private, self.DATA))
        sl.save_ledger(private, sl.empty_ledger())
        with open(os.path.join(private, self.DATA, "project.json"), "w") as f:
            json.dump({"private": True}, f)
        subprocess.run(["git", "-C", private, "init", "-q", "-b", "main"], env=self.env, check=True)
        names = [r["name"] for r in self.run_hk()["projects"]]
        self.assertNotIn("second", names)
        self.assertNotIn("private", names)

    def test_the_source_names_no_folder(self):
        with open(os.path.join(ROOT, "plugin", "scripts", "housekeeping.py"), encoding="utf-8") as f:
            source = f.read().lower()
        self.assertNotIn("/users/", source)

    def test_the_source_names_no_configured_protected_folder(self):
        """The real names are read from the private config at run time; none of them is listed in this repo."""
        import leak
        try:
            names = leak.protected_names()
        except ValueError:
            names = set()
        if not names:
            self.skipTest("no protected_dirs configured on this machine, nothing to check")
        with open(os.path.join(ROOT, "plugin", "scripts", "housekeeping.py"), encoding="utf-8") as f:
            source = f.read().lower()
        for word in names:
            self.assertNotIn(word, source)


class LeftoverTest(HkCase):
    """R-404: the folders next to the projects are counted from metadata; nothing is opened, named, moved or deleted."""
    def folder(self, name, files=(), manifest=False, age_days=0):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        for rel in files:
            self.write_file(rel, "x", root=path)
        if manifest:
            self.write_file(".claude-plugin/plugin.json", "{}", root=path)
        old = time.time() - age_days * DAY
        for base, dirs, fs in os.walk(path):
            for n in dirs + fs:
                os.utime(os.path.join(base, n), (old, old))
        return path

    def test_empty_and_idle_plugin_copies_are_counted_unknown_ones_asked_and_private_ones_left_alone(self):
        self.folder("blank")
        self.folder("copy-old", ["a.txt"], manifest=True, age_days=9)
        self.folder("copy-new", ["a.txt"], manifest=True)
        self.folder("mystery-dir", ["inner-secret-name.txt"], age_days=30)
        hidden = self.folder("keep-out", ["inner-secret-name.txt"], age_days=30)
        self.config(protected_dirs=[hidden])
        report = self.run_hk()
        lo = report["leftovers"]
        self.assertEqual((lo["trash"], lo["private"]), (2, 1))                  # blank + copy-old (the home and the project do not count as trash)
        self.assertGreaterEqual(lo["unknown"], 1)                               # mystery-dir (and the temp home)
        text = housekeeping.render(report, "en")
        self.assertIn("look like leftovers", text)
        for secret in ("keep-out", "inner-secret-name", "mystery-dir", "copy-old", "blank"):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, json.dumps(report))
        for name in ("blank", "copy-old", "copy-new", "mystery-dir", "keep-out"):
            self.assertTrue(os.path.isdir(os.path.join(self.tmp, name)))        # nothing moved, with or without --apply
        self.run_hk(apply=True)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "blank")))

    def test_a_folder_that_could_not_be_read_is_not_counted_as_a_leftover(self):
        path = self.folder("shut")
        os.chmod(path, 0)
        try:
            if os.access(path, os.R_OK):
                self.skipTest("running as a user that reads anything")
            lo = self.run_hk()["leftovers"]
            self.assertEqual(lo["trash"], 0)
            self.assertGreaterEqual(lo["unknown"], 1)
        finally:
            os.chmod(path, 0o700)

    def test_a_busy_machine_skips_the_count(self):
        self.folder("blank")
        self.assertIsNone(housekeeping.run(now=self.later, busy=(True, "x"), live=[])["leftovers"])


class CliTest(HkCase):
    def test_the_command_prints_the_report_and_saves_the_applied_one(self):
        self.lane("done-lane")
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        import types
        loader = importlib.machinery.SourceFileLoader("budgit_cli_hk2", BUDGIT)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli_hk2", loader))
        loader.exec_module(mod)
        base = dict(schedule=None, last=False, only=None, apply=False, hours=housekeeping.IDLE_HOURS, json=False, quiet=False, at=None)
        out, last = io.StringIO(), io.StringIO()
        # in-process so the busy reading is injected (housekeeping.busy_now): the live machine never decides this test
        with mock.patch.dict(os.environ, {"STUDIO_HOME": self.home, "BUDGIT_LANG": "en"}), mock.patch.object(housekeeping, "busy_now", return_value=(False, "free")), \
                mock.patch.dict(sys.modules, {"procclose": mock.Mock(sweep_orphans=lambda apply: [])}):
            with contextlib.redirect_stdout(out):
                mod.cmd_housekeeping(types.SimpleNamespace(**base))
            with contextlib.redirect_stdout(last):
                code = mod.cmd_housekeeping(types.SimpleNamespace(**dict(base, last=True)))
        self.assertIn("proj:", out.getvalue())
        self.assertIn("dry run", out.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("no housekeeping report", last.getvalue())

    def test_apply_with_hours_zero_removes_and_last_shows_it(self):
        path = self.lane("done-lane")
        time.sleep(1.1)
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        import types
        loader = importlib.machinery.SourceFileLoader("budgit_cli_hk", BUDGIT)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli_hk", loader))
        loader.exec_module(mod)
        args = types.SimpleNamespace(schedule=None, last=False, only=None, apply=True, hours=0.0001, json=False, quiet=False, at=None)
        out = io.StringIO()
        # the busy reading is injected through housekeeping.busy_now (the one shared reading), so the live machine never decides this test
        with mock.patch.dict(os.environ, {"STUDIO_HOME": self.home, "BUDGIT_LANG": "en"}), mock.patch.object(housekeeping, "busy_now", return_value=(False, "free")), \
                mock.patch.dict(sys.modules, {"procclose": mock.Mock(sweep_orphans=lambda apply: [])}), contextlib.redirect_stdout(out):
            code = mod.cmd_housekeeping(args)
            last = housekeeping.last()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(path))
        self.assertIn("removed 1 worktrees, deleted 1 branches", out.getvalue())
        self.assertIn("removed 1 worktrees", last)

    def test_apply_does_not_read_the_live_machine_under_a_fake_88_percent_cpu(self):
        """Proves the isolation: with admit's own sampler reporting 88 % CPU the injected reading still decides."""
        import admit
        path = self.lane("done-lane")
        time.sleep(1.1)
        with mock.patch.object(housekeeping, "busy_now", return_value=(False, "free")), \
                mock.patch.object(admit, "is_busy", return_value=(True, "median CPU 88 % is above 85 %")):
            report = housekeeping.run(apply_changes=True, hours=0.0001)
        self.assertFalse(report["git_skipped"])
        self.assertFalse(os.path.exists(path))

    def test_quiet_prints_nothing_and_json_is_json(self):
        self.assertEqual(self.cli("--quiet").stdout, "")
        data = json.loads(self.cli("--json").stdout)
        self.assertEqual(data["projects"][0]["name"], "proj")


class JobHealthRecordTest(HkCase):
    """R-569 exit code + R-874 job health: --apply records 0 for nothing to clean, 11 for a busy skip, 1 for a failed removal."""

    def apply_recorded(self, **extra):
        import importlib.machinery
        import importlib.util
        import types
        import jobhealth
        report = {"projects": [], "left_alone": [], "busy_reason": None, "requested_apply": True,
                  "totals": dict({"removed": 0, "deleted": 0, "failed": 0}, **{k: v for k, v in extra.items() if k != "busy_reason"})}
        report["busy_reason"] = extra.get("busy_reason")
        loader = importlib.machinery.SourceFileLoader("budgit_cli_r874", BUDGIT)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli_r874", loader))
        loader.exec_module(mod)
        args = types.SimpleNamespace(schedule=None, last=False, only=None, apply=True, hours=None, json=False, quiet=True, at=None)
        with mock.patch.dict(os.environ, {"STUDIO_HOME": self.home}), mock.patch.object(housekeeping, "run", return_value=report), \
                mock.patch.object(housekeeping, "save_last"), mock.patch.object(housekeeping, "render", return_value=""), mock.patch.dict(sys.modules, {"procclose": mock.Mock(sweep_orphans=lambda apply: [])}):
            code = mod.cmd_housekeeping(args)
        return code, jobhealth.load()["cleanup"]["exit"]

    def test_nothing_to_clean_records_healthy(self):
        code, recorded = self.apply_recorded()
        self.assertEqual((code, recorded), (0, 0))

    def test_a_busy_skip_records_eleven(self):
        code, recorded = self.apply_recorded(busy_reason="median CPU 90 % is above 85 %")
        self.assertEqual((code, recorded), (11, 11))

    def test_a_failed_removal_records_one(self):
        code, recorded = self.apply_recorded(failed=1)
        self.assertEqual((code, recorded), (1, 1))


class JobsTableTest(HkCase):
    """R-788 E6/E7: each job keeps its own schedule through repeated repairs; `job on` needs a recorded yes."""

    def setUp(self):
        super().setUp()
        self.agents = os.path.join(self.tmp, "agents2")
        os.makedirs(self.agents)
        self.env = dict(BUDGIT_LAUNCH_AGENTS_DIR=self.agents, BUDGIT_HOUSEKEEPING_LAUNCHCTL="off")
        os.environ.update(self.env)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in self.env])

    def test_RepairThriceKeepsSchedule(self):
        root = os.path.join(ROOT, "plugin")
        kept = {"cleanup": {"StartCalendarInterval": [{"Hour": 7, "Minute": 5}, {"Hour": 19, "Minute": 0}]},
                "keepalive": {"StartInterval": 5400},
                "standup": {"StartCalendarInterval": {"Hour": 6, "Minute": 45}}}
        for name, job in housekeeping.JOBS.items():
            launcher = housekeeping.launcher_path(sl.home_state())
            current = housekeeping.desired_plist(job, {}, launcher, sl.home_state())
            current.update(kept[name])
            current.pop("StartInterval" if "StartCalendarInterval" in kept[name] else "StartCalendarInterval", None)
            path = os.path.join(self.agents, job["label"] + ".plist")
            with open(path, "wb") as f:
                plistlib.dump(current, f)
            with mock.patch.object(housekeeping, "_plist_home", lambda cur: sl.home_state()):
                for _ in range(3):
                    housekeeping.repair_job(job, root, sys.executable)
                    with open(path, "rb") as f:
                        again = plistlib.load(f)
                    for key, value in kept[name].items():
                        self.assertEqual(again[key], value, name)
        defaults = {n: housekeeping.desired_plist(j, {}, "/x", "/h") for n, j in housekeeping.JOBS.items()}
        self.assertEqual(defaults["keepalive"]["StartInterval"], 7200)
        self.assertEqual(defaults["standup"]["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        self.assertEqual(defaults["cleanup"]["StartCalendarInterval"], {"Hour": 9, "Minute": 30})
        self.assertEqual(housekeeping.desired_plist(housekeeping.JOBS["keepalive"], {"StartInterval": 60}, "/x", "/h")["StartInterval"], 7200)

    def test_JobOnNeedsYes(self):
        import jobs
        import postupdate
        for answer in (None, "no", "later"):
            if answer:
                postupdate.answer("keepalive", answer)
            code, text = jobs.turn_on("keepalive", os.path.join(ROOT, "plugin", "bin", "budgit"))
            self.assertEqual(code, 1, text)
            self.assertEqual(os.listdir(self.agents), [])
            self.assertFalse(os.path.exists(jobs.path()))


class ScheduleTest(HkCase):
    def setUp(self):
        super().setUp()
        self.agents = os.path.join(self.tmp, "agents")
        self.cfg = os.path.join(self.tmp, "claude-cfg")
        os.makedirs(self.cfg)
        self.sched = dict(BUDGIT_LAUNCH_AGENTS_DIR=self.agents, BUDGIT_HOUSEKEEPING_LAUNCHCTL="off", CLAUDE_CONFIG_DIR=self.cfg)
        self.root = self.register_plugin("0.13.6")

    def register_plugin(self, version):
        """A copy of the plugin that Claude Code's registry names, the way tests/test_install_step.py does it."""
        root = os.path.join(self.cfg, "plugins", "cache", "mkt", "budgit", version)
        shutil.copytree(os.path.join(ROOT, "plugin"), root, symlinks=True)
        manifest = os.path.join(root, ".claude-plugin", "plugin.json")
        with open(manifest) as f:
            data = json.load(f)
        data["version"] = version
        with open(manifest, "w") as f:
            json.dump(data, f)
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"plugins": {"budgit@mkt": [{"installPath": root, "version": version}]}}, f)
        return root

    def cli(self, *args, expect=0, **env):
        r = subprocess.run([sys.executable, os.path.join(self.root, "bin", "budgit"), "housekeeping", *args], capture_output=True, text=True,
                           env=dict(self.env, STUDIO_HOME=self.home, BUDGIT_LANG="en", BUDGIT_BUSY_WINDOW="1", **env),
                           cwd=self.tmp)       # R-1141: the cwd is part of the privacy-folder check; never the caller's (the repo may sit in Documents)
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r

    def test_an_unregistered_copy_gets_no_job_even_in_test_mode(self):
        self.root = os.path.join(self.tmp, "loose")
        shutil.copytree(os.path.join(ROOT, "plugin"), self.root, symlinks=True)
        out = self.cli("--schedule", "on", expect=1, **self.sched).stdout
        self.assertIn("not the install", out)
        self.assertFalse(os.path.exists(self.agents))
        self.assertFalse(os.path.exists(os.path.join(self.home, "install.json")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "bin", "budgit-job")))

    def test_a_vendored_copy_gets_no_job(self):
        with open(os.path.join(self.root, "VERSION"), "w") as f:
            f.write("0.13.6\n")
        self.cli("--schedule", "on", expect=1, **self.sched)
        self.assertFalse(os.path.exists(os.path.join(self.home, "install.json")))

    def test_on_writes_one_launch_agent_status_sees_it_and_off_removes_it(self):
        self.assertIn("not set up", self.cli("--schedule", "status", **self.sched).stdout)
        self.cli("--schedule", "on", "--at", "07:05", **self.sched)
        path = os.path.join(self.agents, housekeeping.LABEL + ".plist")
        with open(path, "rb") as f:
            plist = plistlib.load(f)
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 7, "Minute": 5})
        self.assertEqual(plist["ProgramArguments"][-3:], ["housekeeping", "--apply", "--quiet"])
        self.assertEqual(plist["Nice"], 10)
        self.assertIn("is set up", self.cli("--schedule", "status", **self.sched).stdout)
        self.cli("--schedule", "off", **self.sched)
        self.assertFalse(os.path.exists(path))
        self.cli("--schedule", "off", **self.sched)                          # twice is fine

    def test_a_bad_time_is_refused_and_writes_nothing(self):
        self.cli("--schedule", "on", "--at", "25:00", expect=64, **self.sched)
        self.assertFalse(os.path.exists(self.agents))

    def test_a_plist_whose_program_is_gone_is_called_stale(self):
        os.makedirs(self.agents)
        with open(os.path.join(self.agents, housekeeping.LABEL + ".plist"), "wb") as f:
            plistlib.dump({"Label": housekeeping.LABEL, "ProgramArguments": [sys.executable, "/nowhere/budgit"]}, f)
        out = self.cli("--schedule", "status", expect=1, **self.sched).stdout
        self.assertIn("is gone", out)

    def launcher_plist(self):
        self.cli("--schedule", "on", "--at", "07:05", **self.sched)
        with open(os.path.join(self.agents, housekeeping.LABEL + ".plist"), "rb") as f:
            return plistlib.load(f)

    def test_on_writes_the_launcher_layout(self):
        program = self.launcher_plist()["ProgramArguments"]
        self.assertEqual(program[0], "/bin/sh")
        self.assertTrue(program[1].endswith(os.path.join("bin", "budgit-job")))
        self.assertTrue(os.path.isfile(program[1]))
        self.assertEqual(os.stat(program[1]).st_mode & 0o777, 0o700)
        self.assertEqual(program[2:], ["housekeeping", "--apply", "--quiet"])
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(program[1])), "install.json")))

    def test_status_follows_the_launcher(self):
        program = self.launcher_plist()["ProgramArguments"]
        self.assertIn("is set up", self.cli("--schedule", "status", **self.sched).stdout)
        record = os.path.join(os.path.dirname(os.path.dirname(program[1])), "install.json")
        with open(record, "w") as f:
            json.dump({"root": "/nowhere/plugin", "version": "0.0.1", "python": "", "steps": {}}, f)
        out = self.cli("--schedule", "status", expect=1, **self.sched).stdout
        self.assertIn("launcher", out)
