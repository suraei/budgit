"""R-1058 (with R-1059): the Budgit-home guard refuses a command for what it can write, never because a subcommand is missing
from a list or because a test runner's source names BUDGIT_HOME."""
import hashlib
import os
import shutil
import unittest
from unittest import mock

from test_guard_trust import GuardCase, CLI
from test_r901_guard_writes import load_cli, leaves
import studiolib as sl

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "runtests.py")
MODULES = "test_export test_export_public test_public_voice test_release_evidence test_rename test_security_review test_setup test_update_daily"


class HomeGuardReadsTest(GuardCase):
    def verdict(self, cmd, subagent=True):
        got = sl.home_write_check("Bash", {"command": cmd}, self.root, None, subagent=subagent)[0]
        return got[0] if got else None

    def test_a_lint_tracked_of_the_own_cli_is_not_refused_for_a_subagent(self):
        # cause A: the table row was the string "home" ("not proven"), and a subagent is denied every "home" row
        self.assertEqual(sl._cli_table()["lint"][0], "project")
        self.assertIsNone(self.verdict(CLI + " lint --tracked"))
        self.assertIsNone(self.verdict(CLI + " lint --tracked", subagent=False))

    def test_b_the_test_runner_with_modules_is_not_refused_for_a_subagent_or_a_terminal(self):
        # cause B: not a CLI call; the script-reading rule (_file_names_home) refused tools/runtests.py, whose source names BUDGIT_HOME and writes
        self.assertTrue(sl._file_names_home(RUNNER, self.root, [sl._fold(x) for x in sl._home_candidates() if x]))
        for who in (True, False):
            self.assertIsNone(self.verdict("python3 %s %s" % (RUNNER, MODULES), who))

    def fake_checkout(self, name, git):
        """A folder with tools/runtests.py (the real bytes) and tests/; git = None (plain), "worktree" or "foreign"."""
        wt = os.path.join(os.path.expanduser("~"), "studio-wt", name)
        os.makedirs(os.path.join(wt, "tools"))
        os.makedirs(os.path.join(wt, "tests"))
        shutil.copy(RUNNER, os.path.join(wt, "tools", "runtests.py"))
        if git:
            common = sl._git_common_dir(os.path.realpath(os.path.dirname(sl.studio_root()))) if git == "worktree" else os.path.join(self.tmp, "other.git")
            with open(os.path.join(wt, ".git"), "w") as fh:
                fh.write("gitdir: %s\n" % os.path.join(common, "worktrees", name))
        return wt, self._split("python3 %s/tools/runtests.py test_a" % wt)

    def test_the_checkout_must_be_this_repo_or_a_git_worktree_of_it(self):
        self.assertIsNotNone(sl._git_common_dir(os.path.realpath(os.path.dirname(sl.studio_root()))))
        self.assertTrue(sl._is_test_runner(*self.fake_checkout("wt1", "worktree")[1]))     # a worktree of this repo
        self.assertFalse(sl._is_test_runner(*self.fake_checkout("plain", None)[1]))        # a plain folder with a copied runner
        self.assertFalse(sl._is_test_runner(*self.fake_checkout("foreign", "foreign")[1]))    # a worktree of another repo
        wt = self.fake_checkout("copy", None)[0]
        self.assertEqual(self.verdict("python3 %s/tools/runtests.py %s" % (wt, MODULES)), "deny")    # the old script-reading rule refuses it

    def test_a_test_cache_folder_inside_the_budgit_home_turns_the_runner_allow_off(self):
        args = self._split("python3 %s test_a" % RUNNER)
        self.assertTrue(sl._is_test_runner(*args))
        with mock.patch.dict(os.environ, {"BUDGIT_TEST_CACHE": os.path.join(sl.home_state(), "cache")}):
            self.assertFalse(sl._is_test_runner(*args))
        with mock.patch.dict(os.environ, {"BUDGIT_TEST_CACHE": os.path.join(self.tmp, "cache")}):
            self.assertTrue(sl._is_test_runner(*args))
        prefixed = ["BUDGIT_TEST_CACHE=%s/c" % sl.home_state(), "python3", RUNNER, "test_a"]
        self.assertFalse(sl._is_test_runner(prefixed, prefixed, self.root, os.path.expanduser("~")))

    def test_project_rows_judge_the_cwd_lint_and_brain_index_in_the_budgit_home_are_not_allowed(self):
        home = sl.home_state()
        os.makedirs(home, exist_ok=True)
        for cmd in (CLI + " lint", CLI + " brain index", CLI + " lint --tracked"):
            for who in (True, False):
                got = sl.home_write_check("Bash", {"command": cmd}, home, None, subagent=who)[0]
                self.assertIsNotNone(got, (cmd, who))
                self.assertIn(got[0], ("deny", "ask"), (cmd, who))

    def test_the_runner_shape_is_narrow(self):
        for bad in ("python3 %s test_a; touch x", "python3 %s test_a --out /x", "python3 %s ../x", "python3 %s test_a > f"):
            self.assertFalse(sl._is_test_runner(*self._split(bad % RUNNER)), bad)

    def test_the_pin_equals_this_checkouts_runner(self):
        with open(RUNNER, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), sl.RUNTESTS_SHA256, "runtests.py changed: update RUNTESTS_SHA256 on purpose")

    def test_a_changed_runner_is_refused(self):
        wt = os.path.join(os.path.expanduser("~"), "studio-wt", "edited")
        os.makedirs(os.path.join(wt, "tools"))
        os.makedirs(os.path.join(wt, "tests"))
        with open(RUNNER, "rb") as fh:
            data = fh.read()
        with open(os.path.join(wt, "tools", "runtests.py"), "wb") as fh:
            fh.write(data + b"\n")
        cmd = "python3 %s/tools/runtests.py test_a" % wt
        self.assertFalse(sl._is_test_runner(*self._split(cmd)))

    def _split(self, cmd):
        w = cmd.split()
        return w, w, self.root, os.path.expanduser("~")

    def test_real_writes_still_deny(self):
        home = sl.home_state().rstrip("/")
        self.assertEqual(self.verdict("touch %s/x" % home), "deny")
        self.assertEqual(self.verdict("cp f %s/" % home), "deny")
        self.assertEqual(self.verdict(CLI + " prepare --approve"), "deny")
        self.assertEqual(self.verdict(CLI + " no-such-subcommand"), "deny")

    def test_every_parser_subcommand_has_a_row_and_an_unknown_one_stays_home(self):
        table = sl._cli_table()
        self.assertEqual([k for k in leaves(load_cli().build_parser()) if k not in table], [])
        self.assertNotIn("zzz", table)


if __name__ == "__main__":
    unittest.main()
