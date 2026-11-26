"""R-352: stop the line. `budgit main-check` (full suite on a clean checkout of main, recorded by commit sha, red_since) and
`budgit main-gate` (git hooks that refuse a merge onto a red main unless the branch is the fix). Temp repos only."""
import json
import os
import subprocess
import sys
import unittest

from helpers import BudgitTestCase, ROOT
import maincheck
import studiolib as sl

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
CALM_MEMORY = json.dumps({"pressure": 1, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100})
GREEN = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
RED = ("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n"
       "    def test_broken(self):\n        self.assertEqual(1, 2)\n    def test_boom(self):\n        raise RuntimeError('x')\n")


class RepoCase(BudgitTestCase):
    def setUp(self):
        super().setUp()
        old = os.environ.get("BUDGIT_SUITE_LOAD_LIMIT")                  # the real machine's load must not decide a test
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "100000"
        self.addCleanup(lambda: os.environ.pop("BUDGIT_SUITE_LOAD_LIMIT", None) if old is None else os.environ.update(BUDGIT_SUITE_LOAD_LIMIT=old))
        old_mem = os.environ.get("BUDGIT_TEST_MEM")                       # nor the real machine's memory: a calm picture, read by the CLI child
        os.environ["BUDGIT_TEST_MEM"] = CALM_MEMORY
        self.addCleanup(lambda: os.environ.pop("BUDGIT_TEST_MEM", None) if old_mem is None else os.environ.update(BUDGIT_TEST_MEM=old_mem))
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.init_project()
        with open(os.path.join(self.project, ".git", "info", "exclude"), "a") as f:
            f.write(".budgit/\n.studio/\n")
        self.write("tests/test_t.py", GREEN)
        self.commit("start")

    def git(self, *args, check=True, cwd=None):
        r = subprocess.run(["git", "-C", cwd or self.project] + list(args), capture_output=True, text=True, env=self.env)
        if check and r.returncode:
            raise AssertionError("git %s: %s%s" % (" ".join(args), r.stdout, r.stderr))
        return r

    def read(self, path):
        with open(path) as f:
            return f.read()

    def write(self, rel, text):
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def branch_commit(self, name, rel, text="x\n", base="main"):
        """A branch off `base` with one commit; leaves the checkout on `base`."""
        self.git("checkout", "-q", "-b", name, base)
        self.write(rel, text)
        self.commit("work on " + name)
        self.git("checkout", "-q", base)

    def head(self, ref="main"):
        return self.git("rev-parse", ref).stdout.strip()

    def set_config(self, **kw):
        """Write project.json and, as the user would at a terminal, approve the `test_command` it holds."""
        cfg = self.raw_config(**kw)
        if cfg.get("test_command"):
            sl._write_approval(self.project, [], test_command=cfg["test_command"])

    def raw_config(self, **kw):
        path = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = {}
        if os.path.exists(path):
            with open(path) as f:
                cfg = json.load(f)
        cfg.update(kw)
        with open(path, "w") as f:
            json.dump(cfg, f)
        return cfg

    def fake(self, state, at="2026-10-27T10:00:00.000Z", **extra):
        """A runner that answers without running anything."""
        def runner(project, top, sha, command):
            r = {"state": state, "exit": 0 if state == "green" else 1, "ran": 3, "failed": 0 if state == "green" else 1,
                 "errors": 0, "skipped": 0, "failing": [] if state == "green" else ["m.C.test_x"], "seconds": 1.0,
                 "checked_at": at, "command": command}
            r.update(extra)
            return r
        return runner

    def go_red(self, at="2026-10-27T10:00:00.000Z"):
        self.set_config(test_command="true")
        return maincheck.check(self.project, rerun=True, runner=self.fake("red", at))

    def go_green(self, at="2026-10-27T15:00:00.000Z"):
        return maincheck.check(self.project, rerun=True, runner=self.fake("green", at))

    def cli(self, *args, expect=None):
        env = dict(self.env, STUDIO_HOME=self.home, BUDGIT_LANG="en")
        r = subprocess.run([sys.executable, BUDGIT] + list(args) + ["--project", self.project], capture_output=True,
                           text=True, env=env)
        if expect is not None:
            self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r


class ParseTest(unittest.TestCase):
    def test_unittest_counts_and_names_in_both_python_formats(self):
        text = ("FAIL: test_a (pkg.mod.C)\nERROR: test_b (pkg.mod.C.test_b)\nFAIL: test_a (pkg.mod.C)\n\n"
                "Ran 12 tests in 0.5s\n\nFAILED (failures=2, errors=1, skipped=3)\n")
        r = maincheck.parse_output(text)
        self.assertEqual((r["ran"], r["failed"], r["errors"], r["skipped"]), (12, 2, 1, 3))
        self.assertEqual(r["failing"], ["pkg.mod.C.test_a", "pkg.mod.C.test_b"])        # duplicates collapse

    def test_a_green_unittest_run(self):
        r = maincheck.parse_output("Ran 5 tests in 0.1s\n\nOK (skipped=1)\n")
        self.assertEqual((r["ran"], r["failed"], r["errors"], r["skipped"], r["failing"]), (5, 0, 0, 1, []))

    def test_pytest_summary_and_names(self):
        text = "FAILED tests/test_a.py::test_one - assert 1 == 2\n=== 1 failed, 4 passed, 2 skipped in 0.3s ===\n"
        r = maincheck.parse_output(text)
        self.assertEqual((r["ran"], r["failed"], r["skipped"]), (5, 1, 2))
        self.assertEqual(r["failing"], ["tests/test_a.py::test_one"])

    def test_unknown_output_is_zero_counts(self):
        self.assertEqual(maincheck.parse_output("hello")["ran"], 0)


class CheckTest(RepoCase):
    def test_a_green_suite_on_a_clean_checkout_is_recorded_by_sha(self):
        branch, sha, res, cached = maincheck.check(self.project)
        self.assertEqual((branch, sha, cached), ("main", self.head(), False))
        self.assertEqual((res["state"], res["ran"], res["failed"], res["errors"]), ("green", 1, 0, 0))
        state = maincheck.load_state(self.project)
        self.assertEqual(state["results"][sha]["state"], "green")
        self.assertIsNone(state["red_since"])
        self.assertFalse(os.path.exists(os.path.join(self.project, ".git", maincheck.FLAG)))

    def test_a_red_suite_names_the_failing_tests_sets_red_since_and_writes_the_flag(self):
        self.write("tests/test_t.py", RED)
        self.commit("break")
        _b, sha, res, _c = maincheck.check(self.project)
        self.assertEqual(res["state"], "red")
        self.assertEqual((res["ran"], res["failed"], res["errors"]), (3, 1, 1))
        self.assertEqual(sorted(res["failing"]), ["test_t.T.test_boom", "test_t.T.test_broken"])
        state = maincheck.load_state(self.project)
        self.assertTrue(state["red_since"])
        self.assertEqual(state["red_sha"], sha)
        flag = self.read(os.path.join(self.project, ".git", maincheck.FLAG))
        self.assertIn("main=main\n", flag)
        self.assertIn("red_since=" + state["red_since"], flag)

    def test_the_suite_runs_on_main_head_not_on_the_working_copy(self):
        self.set_config(test_command="test -e tests/test_t.py && test ! -e scratch.txt && test ! -e .budgit")
        self.write("scratch.txt", "uncommitted\n")                            # untracked: a clean checkout does not have it
        self.write("tests/test_t.py", RED)                                    # modified, not committed
        _b, _s, res, _c = maincheck.check(self.project)
        self.assertEqual(res["state"], "green", res)
        self.assertEqual(self.git("worktree", "list").stdout.count("\n"), 1)   # the temp checkout is gone
        self.assertEqual(self.read(os.path.join(self.project, "scratch.txt")), "uncommitted\n")

    def test_a_recorded_result_for_the_same_sha_is_reused_and_rerun_runs_again(self):
        counter = os.path.join(self.tmp, "runs")
        self.set_config(test_command="echo x >> %s" % counter)
        maincheck.check(self.project)
        _b, _s, res, cached = maincheck.check(self.project)
        self.assertTrue(cached)
        self.assertEqual(self.read(counter).count("x"), 1)
        self.assertEqual(maincheck.check(self.project, rerun=True)[3], False)
        self.assertEqual(self.read(counter).count("x"), 2)
        self.write("more.txt", "m\n")
        self.commit("new sha")
        self.assertEqual(maincheck.check(self.project)[3], False)             # a new commit is a new key
        self.assertEqual(self.read(counter).count("x"), 3)

    def test_red_since_survives_another_red_and_hours_come_from_the_green_that_closes_it(self):
        self.go_red("2026-10-27T10:00:00.000Z")
        self.write("a.txt", "a\n")
        self.commit("still red")
        self.set_config(test_command="true")
        maincheck.check(self.project, runner=self.fake("red", "2026-10-27T12:00:00.000Z"))
        info = maincheck.red_info(self.project, at="2026-10-27T13:30:00.000Z")
        self.assertEqual((info["red"], info["red_since"], info["hours"]), (True, "2026-10-27T10:00:00.000Z", 3.5))
        self.go_green("2026-10-27T15:00:00.000Z")
        info = maincheck.red_info(self.project, at="2026-10-27T16:00:00.000Z")
        self.assertEqual((info["red"], info["red_since"], info["hours"]), (False, None, 0.0))
        self.assertEqual([(e["since"], e["until"], e["hours"]) for e in info["episodes"]],
                         [("2026-10-27T10:00:00.000Z", "2026-10-27T15:00:00.000Z", 5.0)])
        self.assertFalse(os.path.exists(os.path.join(self.project, ".git", maincheck.FLAG)))

    def test_a_timeout_is_red_and_says_so(self):
        self.set_config(test_command="sleep 5")
        top = maincheck.repo_top(self.project)
        res = maincheck.run_suite(self.project, top, self.head(), "sleep 5", timeout=1)
        self.assertEqual((res["state"], res.get("timed_out")), ("red", True))
        self.assertIn("timed out", res["failing"][0])

    def test_a_test_command_nobody_approved_is_refused_and_never_run(self):
        marker = os.path.join(self.tmp, "ran")
        self.raw_config(test_command="touch '%s'" % marker)                    # as a cloned repo would ship it
        for run in (lambda: maincheck.check(self.project, rerun=True), lambda: maincheck.run_plain_suite(self.project)):
            with self.assertRaises(maincheck.SuiteUnavailable) as ctx:
                run()
            self.assertIn("--approve", str(ctx.exception))
        self.assertFalse(os.path.exists(marker))
        r = self.cli("suite")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_an_approved_test_command_runs_and_a_changed_one_needs_a_new_approval(self):
        marker = os.path.join(self.tmp, "ran")
        self.set_config(test_command="touch '%s'" % marker)
        self.assertEqual(maincheck.run_plain_suite(self.project)[0], 0)
        self.assertTrue(os.path.exists(marker))
        os.remove(marker)
        self.raw_config(test_command="touch '%s'; true" % marker)
        with self.assertRaises(maincheck.SuiteUnavailable):
            maincheck.run_plain_suite(self.project)
        self.assertFalse(os.path.exists(marker))

    def test_the_standing_yes_of_the_user_also_lets_a_test_command_run(self):
        marker = os.path.join(self.tmp, "ran")
        self.raw_config(test_command="touch '%s'" % marker)
        real = sl.trust_granted
        sl.trust_granted = lambda project: True
        self.addCleanup(lambda: setattr(sl, "trust_granted", real))
        self.assertEqual(maincheck.run_plain_suite(self.project)[0], 0)
        self.assertTrue(os.path.exists(marker))

    def test_no_test_command_and_no_tests_folder_refuses_clearly(self):
        os.remove(os.path.join(self.project, "tests", "test_t.py"))
        self.commit("no tests")
        os.rmdir(os.path.join(self.project, "tests")) if os.path.isdir(os.path.join(self.project, "tests")) else None
        with self.assertRaises(sl.StudioError) as ctx:
            maincheck.check(self.project)
        self.assertIn("test_command", str(ctx.exception))

    def test_a_running_check_blocks_a_second_one_and_a_dead_lock_is_cleared(self):
        lock = os.path.join(self.project, ".git", maincheck.LOCK)
        with open(lock, "w") as f:
            f.write(str(os.getpid()))                                         # this process is alive
        with self.assertRaises(sl.StudioError) as ctx:
            maincheck.check(self.project)
        self.assertIn("already running", str(ctx.exception))
        with open(lock, "w") as f:
            f.write("999999")
        self.assertEqual(maincheck.check(self.project)[2]["state"], "green")
        self.assertFalse(os.path.exists(lock))

    def test_the_recorded_state_lives_in_the_git_dir_so_a_reset_or_a_commit_cannot_lose_it(self):
        self.go_red()
        self.write("x.txt", "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "everything")
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.assertTrue(maincheck.red_info(self.project)["red"])
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".git", maincheck.STATE)))
        self.assertEqual(self.git("ls-files", "--", ".budgit", ".studio").stdout.strip(), "")

    def test_a_linked_worktree_sees_the_same_state(self):
        self.go_red()
        wt = os.path.join(self.tmp, "wt2")
        self.git("worktree", "add", "-q", "-b", "side2", wt)
        self.assertTrue(maincheck.red_info(wt)["red"])

    def test_test_timeout_comes_from_project_json(self):
        self.set_config(test_command="sleep 5", test_timeout=1)
        _b, _s, res, _c = maincheck.check(self.project)
        self.assertEqual((res["state"], res.get("timed_out")), ("red", True))
        self.assertIn("1s", res["failing"][0])
        self.set_config(test_timeout="soon")
        self.assertEqual(maincheck.suite_timeout(self.project), maincheck.TIMEOUT)

    def test_master_is_found_when_there_is_no_main(self):
        self.git("branch", "-m", "main", "master")
        self.assertEqual(maincheck.check(self.project)[0], "master")


class GateInstallTest(RepoCase):
    def hook(self, name):
        return os.path.join(self.project, ".git", "hooks", name)

    def test_on_installs_both_hooks_off_removes_them_and_status_follows(self):
        self.assertEqual(maincheck.status(self.project), "off")
        self.assertEqual(maincheck.install(self.project), "installed")
        self.assertEqual(maincheck.install(self.project), "already installed")
        for name in maincheck.HOOKS:
            self.assertTrue(os.access(self.hook(name), os.X_OK), name)
            self.assertEqual(self.read(self.hook(name)).split("\n")[1], maincheck.MARK)
        self.assertEqual(maincheck.status(self.project), "on")
        self.assertEqual(maincheck.remove(self.project), "removed")
        self.assertEqual(maincheck.remove(self.project), "not installed")
        self.assertEqual(maincheck.status(self.project), "off")

    def test_a_foreign_hook_is_never_touched_and_nothing_is_installed(self):
        os.makedirs(os.path.dirname(self.hook("reference-transaction")), exist_ok=True)
        with open(self.hook("reference-transaction"), "w") as f:
            f.write("#!/bin/sh\necho mine\n")
        self.assertEqual(maincheck.install(self.project), "refused: existing reference-transaction hook")
        self.assertFalse(os.path.exists(self.hook("pre-merge-commit")))             # all or nothing
        self.assertEqual(self.read(self.hook("reference-transaction")), "#!/bin/sh\necho mine\n")
        self.assertEqual(maincheck.remove(self.project), "refused: not Budgit's hook")
        self.assertTrue(maincheck.status(self.project).startswith("blocked: another"))

    def test_core_hooks_path_refuses(self):
        self.git("config", "core.hooksPath", os.path.join(self.tmp, "elsewhere"))
        self.assertEqual(maincheck.install(self.project), "refused: core.hooksPath is set")
        self.assertEqual(maincheck.status(self.project), "blocked: core.hooksPath is set")

    def test_a_linked_worktree_shares_the_one_hooks_folder(self):
        wt = os.path.join(self.tmp, "wt")
        self.git("worktree", "add", "-q", "-b", "side", wt)
        self.assertEqual(maincheck.install(wt), "installed")
        self.assertTrue(os.path.isfile(self.hook("pre-merge-commit")))


class GateMergeTest(RepoCase):
    """Real git merges against the real hooks."""

    def setUp(self):
        super().setUp()
        maincheck.install(self.project)
        self.branch_commit("feature", "feature.txt")
        self.branch_commit("other", "other.txt")

    def merge(self, branch, *flags):
        return self.git("merge", *flags, branch, check=False)

    def refused(self, r, before):
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("main is red", r.stderr)
        self.assertEqual(self.head(), before)
        if os.path.exists(os.path.join(self.project, ".git", "MERGE_HEAD")):          # git leaves a refused merge commit open
            self.assertIn("git merge --abort", r.stderr)
            self.git("merge", "--abort")

    def diverge(self):
        self.write("main-only.txt", "m\n")
        self.commit("main moves")                   # main and the branches have now diverged: a merge makes a commit

    def test_a_fast_forward_onto_a_red_main_is_refused_and_main_does_not_move(self):
        self.go_red()
        before = self.head()
        self.refused(self.merge("feature", "--ff-only"), before)
        self.refused(self.merge("feature"), before)                                  # plain `git merge` fast-forwards too

    def test_a_merge_commit_onto_a_red_main_is_refused_and_says_how_to_abort(self):
        self.diverge()
        self.go_red()
        before = self.head()
        r = self.merge("feature", "--no-ff")
        self.assertTrue(os.path.exists(os.path.join(self.project, ".git", "MERGE_HEAD")))
        self.refused(r, before)                                                      # aborts the open merge after its checks
        self.assertEqual(self.git("status", "--porcelain").stdout.strip(), "")

    def test_a_branch_named_green_may_merge_both_ways(self):
        self.branch_commit("hotfix-green", "fix.txt")
        self.go_red()
        self.assertEqual(self.merge("hotfix-green", "--ff-only").returncode, 0)
        self.branch_commit("second-green", "fix2.txt", base="main")
        self.diverge()
        self.assertEqual(self.merge("second-green", "--no-ff").returncode, 0)

    def test_fix_for_marks_a_branch_and_only_that_branch(self):
        self.go_red()
        self.assertTrue(maincheck.mark_fix(self.project, "feature"))
        before = self.head()
        self.refused(self.merge("other", "--ff-only"), before)
        r = self.merge("feature", "--ff-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.project, "feature.txt")))

    def test_fix_for_a_merge_commit_works_too(self):
        self.diverge()
        self.go_red()
        maincheck.mark_fix(self.project, "feature")
        self.assertEqual(self.merge("feature", "--no-ff").returncode, 0)

    def test_after_the_fix_merges_main_stays_red_until_a_green_check(self):
        self.go_red()
        maincheck.mark_fix(self.project, "feature")
        self.assertEqual(self.merge("feature", "--ff-only").returncode, 0)
        before = self.head()
        self.refused(self.merge("other"), before)                                    # the record is still red
        self.go_green()
        self.assertEqual(self.merge("other").returncode, 0)

    def test_a_green_main_merges_freely_and_so_does_a_main_never_checked(self):
        self.assertEqual(self.merge("feature", "--ff-only").returncode, 0)
        self.go_red()
        self.go_green()
        self.assertEqual(self.merge("other", "--no-ff").returncode, 0)

    def test_only_merges_onto_main_are_gated(self):
        self.go_red()
        self.git("checkout", "-q", "feature")
        self.assertEqual(self.merge("other").returncode, 0)                          # a branch takes a branch: fine
        self.git("checkout", "-q", "main")
        self.write("direct.txt", "d\n")
        self.commit("a direct commit on main (this is how a fix is made)")           # not a merge: fine
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.git("branch", "-q", "scratch")
        self.assertEqual(self.git("checkout", "-q", "scratch", check=False).returncode, 0)

    def test_a_sync_of_mains_own_remote_copy_is_not_refused(self):
        origin = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", origin], check=True, env=self.env)
        self.git("remote", "add", "origin", origin)
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "-b", "elsewhere", "main")
        self.write("remote-fix.txt", "r\n")
        self.commit("someone else fixed main upstream")
        self.git("push", "-q", "origin", "elsewhere:main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "main")
        self.go_red()
        r = self.merge("origin/main", "--ff-only")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_off_removes_the_refusal(self):
        self.go_red()
        before = self.head()
        self.refused(self.merge("feature", "--ff-only"), before)
        maincheck.remove(self.project)
        self.assertEqual(self.merge("feature", "--ff-only").returncode, 0)

    def test_a_refused_merge_cannot_be_finished_with_a_bare_git_commit(self):
        self.diverge()
        self.go_red()
        before = self.head()
        r = self.merge("feature", "--no-ff")
        self.assertNotEqual(r.returncode, 0)
        c = self.git("commit", "--no-edit", check=False)                                # git itself says: use git commit
        self.assertNotEqual(c.returncode, 0, c.stdout)
        self.assertIn("main is red", c.stderr)
        self.assertEqual(self.head(), before)
        self.git("merge", "--abort")

    def test_a_no_commit_merge_cannot_be_concluded_with_git_commit_while_red(self):
        self.diverge()
        self.go_red()
        before = self.head()
        self.assertEqual(self.merge("feature", "--no-ff", "--no-commit").returncode, 0)
        c = self.git("commit", "--no-edit", check=False)
        self.assertNotEqual(c.returncode, 0, c.stdout)
        self.assertEqual(self.head(), before)
        self.git("merge", "--abort")

    def test_a_no_commit_merge_of_the_fix_concludes(self):
        self.diverge()
        self.go_red()
        maincheck.mark_fix(self.project, "feature")
        self.assertEqual(self.merge("feature", "--no-ff", "--no-commit").returncode, 0)
        self.assertEqual(self.git("commit", "--no-edit", check=False).returncode, 0)

    def test_the_fix_name_is_matched_as_text_not_as_a_pattern(self):
        self.branch_commit("zq1", "zq1.txt")
        self.branch_commit("z.1", "z1.txt")
        self.go_red()
        maincheck.mark_fix(self.project, "zq1")
        before = self.head()
        self.refused(self.merge("z.1", "--ff-only"), before)                            # "z.1" is not "zq1"
        self.assertEqual(self.merge("zq1", "--ff-only").returncode, 0)

    def test_the_hook_decides_with_one_file_test_first_and_a_moved_repo_needs_on_again(self):
        text = maincheck.hook_text("reference-transaction", "/some/where/.git/budgit-main-red")
        body = [l for l in text.splitlines() if l and not l.startswith("#")]
        self.assertEqual(body[0], "flag=/some/where/.git/budgit-main-red")
        self.assertEqual(body[1], '[ -f "$flag" ] || exit 0')
        self.assertEqual(maincheck.status(self.project), "on")
        for name in maincheck.HOOKS:
            path = os.path.join(self.project, ".git", "hooks", name)
            with open(path, "w") as f:
                f.write(maincheck.hook_text(name, "/old/place/.git/" + maincheck.FLAG))
        self.assertEqual(maincheck.status(self.project), "partial")
        self.assertEqual(maincheck.install(self.project), "installed")
        self.assertEqual(maincheck.status(self.project), "on")

    def test_other_ways_onto_main_are_not_gated_as_the_doc_says(self):
        self.go_red()
        before = self.head()
        self.assertEqual(self.git("merge", "--squash", "feature", check=False).returncode, 0)
        self.git("commit", "-q", "-m", "squashed")
        self.assertNotEqual(self.head(), before)                                       # documented: squash is not gated
        self.git("reset", "-q", "--hard", before)
        self.assertEqual(self.git("cherry-pick", "other", check=False).returncode, 0)  # documented: cherry-pick is not gated

    def test_the_python_decision_matches_the_hooks(self):
        self.assertEqual(maincheck.merge_allowed(self.project, ["feature"])[0], True)
        self.go_red()
        self.assertEqual(maincheck.merge_allowed(self.project, ["feature"])[0], False)
        self.assertEqual(maincheck.merge_allowed(self.project, ["feature", "x-green"])[0], True)
        maincheck.mark_fix(self.project, "feature")
        self.assertEqual(maincheck.merge_allowed(self.project, ["feature"])[0], True)
        self.assertEqual(maincheck.merge_allowed(self.project, ["other"])[0], False)
        self.go_green()
        self.assertEqual(maincheck.merge_allowed(self.project, ["other"])[0], True)

    def test_fix_for_when_main_is_green_records_nothing(self):
        self.assertFalse(maincheck.mark_fix(self.project, "feature"))
        self.assertEqual(maincheck.load_state(self.project)["fix_branches"], [])
        with self.assertRaises(sl.StudioError):
            maincheck.mark_fix(self.project, "nope")


class CliTest(RepoCase):
    def test_exit_codes_output_and_json(self):
        self.assertIn("no result recorded", self.cli("main-check", "--status", expect=0).stdout)
        r = self.cli("main-check", expect=0)
        self.assertIn("main is GREEN", r.stdout)
        self.assertIn("main-gate on", r.stdout)                                        # the gate is off: say how to turn it on
        self.assertIn("reusing", self.cli("main-check", expect=0).stdout)
        self.write("tests/test_t.py", RED)
        self.commit("break")
        r = self.cli("main-check", expect=3)
        self.assertIn("main is RED", r.stdout)
        self.assertIn("failing: test_t.T.test_broken", r.stdout)
        self.assertIn("red since", r.stdout)
        data = json.loads(self.cli("main-check", "--status", "--json", expect=3).stdout)
        self.assertTrue(data["red"] and data["red_since"] and data["result"]["state"] == "red")
        self.assertIn("RED since", self.cli("main-check", "--status", expect=3).stdout)

    def test_main_gate_and_fix_for_through_the_cli(self):
        self.assertIn("off", self.cli("main-gate", "status", expect=0).stdout)
        self.assertIn("installed", self.cli("main-gate", "on", expect=0).stdout)
        self.assertIn("main gate: on", self.cli("main-gate", "status", expect=0).stdout)
        self.assertIn("nothing to mark", self.cli("main-check", "--fix-for", "main", expect=0).stdout)
        self.go_red()
        self.branch_commit("repair", "repair.txt")
        self.assertIn("marked as the fix", self.cli("main-check", "--fix-for", "repair", expect=0).stdout)
        self.assertEqual(self.git("merge", "--ff-only", "repair", check=False).returncode, 0)
        self.assertIn("removed", self.cli("main-gate", "off", expect=0).stdout)

    def test_main_gate_refuses_a_foreign_hook_with_exit_one(self):
        hooks = os.path.join(self.project, ".git", "hooks")
        os.makedirs(hooks, exist_ok=True)
        with open(os.path.join(hooks, "pre-merge-commit"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        r = self.cli("main-gate", "on", expect=1)
        self.assertIn("already exists", r.stderr)


if __name__ == "__main__":
    unittest.main()
