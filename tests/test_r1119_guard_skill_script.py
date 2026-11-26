"""R-1119: the Bash home-write guard denied a plain read-only run of a script under the project's own folder
(`python3 -I <project>/skills/.../legalcheck.py filing --help`). The decision function the PreToolUse hook uses
(`home_write_check`) is called directly; the refused command itself is never run."""
import os
import shutil
import subprocess
import time
import unittest
from unittest import mock

from test_guard_trust import GuardCase

import studiolib as sl

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "skills", "legal-eu-es-fr", "scripts", "legalcheck.py")


class SkillScriptRunTest(GuardCase):
    def setUp(self):
        super().setUp()
        # the checkout holding the plugin and the shipped skill script, like the real one
        self.studio = os.path.dirname(HERE)
        self.script = SCRIPT
        self.user = os.path.realpath(os.environ["HOME"])
        self.cmd = "python3 -I %s filing --help" % self.script

    def check(self, cmd, **kw):
        return sl.home_write_check("Bash", {"command": cmd}, self.studio, **kw)[0]

    def test_r1119_plain_run_of_a_project_script_is_not_denied(self):
        for kw in ({}, {"agent_type": "verifier", "subagent": True}, {"subagent": True}):
            got = self.check(self.cmd, **kw)
            self.assertIsNone(got, (kw, got))

    def test_r1119_a_copy_outside_the_shipped_skills_is_still_judged(self):
        other = os.path.join(self.tmp, "copy")
        os.makedirs(other)
        shutil.copy(SCRIPT, os.path.join(other, "legalcheck.py"))
        got = self.check("python3 -I %s filing --help" % os.path.join(other, "legalcheck.py"))
        self.assertEqual(got and got[0], "deny")

    def test_r1119_only_a_script_under_the_own_skills_folder_is_exempt(self):
        self.assertFalse(sl._own_skill_script(os.path.join(self.tmp, "skills", "x", "scripts", "a.py")))
        self.assertTrue(sl._own_skill_script(SCRIPT))

    def test_r1119_write_into_the_home_stays_denied(self):
        for cmd in ("echo x > %s/.studio/trust.json" % self.user,
                    "echo x > $HOME/.studio/trust.json",
                    "echo x > ~/.studio/trust.json",
                    "cp /tmp/p %s/.budgit/trust.json" % self.user):
            got = self.check(cmd)
            self.assertEqual(got and got[0], "deny", cmd)

    def test_r1119_running_the_hook_or_library_by_hand_stays_denied(self):
        for cmd in ("python3 %s/plugin/scripts/hook.py pre-tool-use" % self.studio,
                    "python3 %s/plugin/scripts/studiolib.py" % self.studio):
            got = self.check(cmd)
            self.assertEqual(got and got[0], "deny", cmd)

    def test_r1119_the_same_run_with_a_home_write_stays_denied(self):
        for tail in (" > %s/.studio/trust.json" % self.user, " > $HOME/.studio/x", " | tee ~/.studio/x"):
            got = self.check(self.cmd + tail)
            self.assertEqual(got and got[0], "deny", tail)


SKILL_TEXT = "import os\nhome = os.environ.get('BUDGIT_HOME')\nprint('help')\nopen('out.txt', 'w').write('x')\n"
WRITER_TEXT = SKILL_TEXT + "open(os.environ['BUDGIT_HOME'] + '/ledger.json', 'w').write('x')\n"


class SkillScriptMatchesHeadTest(GuardCase):
    """Round 2: the exemption needs the script to be tracked and equal to HEAD in its own checkout."""

    def setUp(self):
        super().setUp()
        self.repo = os.path.realpath(os.path.join(self.tmp, "checkout"))
        self.dir = os.path.join(self.repo, "skills", "a", "scripts")
        os.makedirs(self.dir)
        os.makedirs(os.path.join(self.repo, "plugin"))
        self.script = os.path.join(self.dir, "x.py")
        open(self.script, "w").write(SKILL_TEXT)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        patcher = mock.patch.object(sl, "studio_root", lambda: os.path.join(self.repo, "plugin"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run_cmd(self, script=None):
        cmd = "python3 -I %s --help" % (script or self.script)
        return sl.home_write_check("Bash", {"command": cmd}, self.repo)[0]

    def test_r1119_clean_tracked_skill_script_passes(self):
        self.assertIsNone(self.run_cmd())

    def test_r1119_modified_tracked_script_that_writes_through_budgit_home_is_denied(self):
        open(self.script, "w").write(WRITER_TEXT)
        got = self.run_cmd()
        self.assertEqual(got and got[0], "deny")

    def test_r1119_new_untracked_script_is_denied(self):
        new = os.path.join(self.dir, "new.py")
        open(new, "w").write(WRITER_TEXT)
        got = self.run_cmd(new)
        self.assertEqual(got and got[0], "deny")

    def test_r1119_staged_but_uncommitted_script_is_denied(self):
        new = os.path.join(self.dir, "staged.py")
        open(new, "w").write(WRITER_TEXT)
        self.git("add", "-A")
        got = self.run_cmd(new)
        self.assertEqual(got and got[0], "deny")

    def test_r1119_non_git_folder_is_denied(self):
        plain = os.path.realpath(os.path.join(self.tmp, "vendored"))
        os.makedirs(os.path.join(plain, "plugin"))
        os.makedirs(os.path.join(plain, "skills", "a", "scripts"))
        script = os.path.join(plain, "skills", "a", "scripts", "x.py")
        open(script, "w").write(WRITER_TEXT)
        with mock.patch.object(sl, "studio_root", lambda: os.path.join(plain, "plugin")):
            got = sl.home_write_check("Bash", {"command": "python3 -I %s --help" % script}, plain)[0]
        self.assertEqual(got and got[0], "deny")

    def commit(self, where, *args):
        base = ["git", "-C", where, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(base + list(args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_r1119_nested_repo_with_a_committed_writer_is_denied(self):
        self.commit(self.dir, "init", "-q")
        open(self.script, "w").write(WRITER_TEXT)
        self.commit(self.dir, "add", "-A")
        self.commit(self.dir, "commit", "-q", "-m", "x")
        got = self.run_cmd()
        self.assertEqual(got and got[0], "deny")

    def test_r1119_compound_same_command_edit_is_denied(self):
        for cmd in ("echo 'x' >> %s; python3 -I %s --help" % (self.script, self.script),
                    "python3 -I %s --help && echo done" % self.script,
                    "python3 -I %s --help | cat" % self.script,
                    "python3 -I %s --help > %s/o.txt" % (self.script, self.repo),
                    "python3 -I %s --help\necho hi" % self.script,
                    "(python3 -I %s --help)" % self.script):
            got = sl.home_write_check("Bash", {"command": cmd}, self.repo)[0]
            self.assertEqual(got and got[0], "deny", cmd)

    def test_r1119_assume_unchanged_flag_is_denied(self):
        self.git("update-index", "--assume-unchanged", "--", self.script)
        open(self.script, "w").write(WRITER_TEXT)
        got = self.run_cmd()
        self.assertEqual(got and got[0], "deny")

    def test_r1119_skip_worktree_flag_is_denied(self):
        self.git("update-index", "--skip-worktree", "--", self.script)
        self.assertFalse(sl._tracked_and_clean(self.script))

    def test_r1119_git_timeout_or_error_means_no_exemption(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 1)):
            self.assertFalse(sl._tracked_and_clean(self.script))
        with mock.patch("subprocess.run", side_effect=OSError("no git")):
            self.assertFalse(sl._tracked_and_clean(self.script))
        self.assertTrue(sl._tracked_and_clean(self.script))

    def test_r1119_git_cost_is_measured(self):
        start = time.perf_counter()
        for _ in range(5):
            self.assertTrue(sl._tracked_and_clean(self.script))
        per = (time.perf_counter() - start) / 5
        print("R-1119 _tracked_and_clean (5 git calls): %.0f ms" % (per * 1000))
        self.assertLess(per, 2.0)


if __name__ == "__main__":
    unittest.main()
