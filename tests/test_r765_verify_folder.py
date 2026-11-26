"""R-765: `ledger verify` reads the verify report from the folder the guard seals against non-verifier writes, in the MAIN
project root, even when the ledger lives in .studio/ and the verifier wrote to .budgit/verify/."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
EVIDENCE = "1e2084f3 test_r765_verify_folder ok"


class VerifyFolderCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()                      # ledger in .studio/
        sl.activate(self.project, "s-r765001")
        data = sl.load_ledger(self.project)
        sl.add_item(data, "item one", ["legacy"])
        sl.save_ledger(self.project, data)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def report(self, *parts, text="PASS\nall checks hold\n"):
        path = os.path.join(self.project, *parts, "R-1.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        sl.write_seal(path)                      # what the hook does after budgit:verifier writes it
        return path

    def status(self):
        return sl.find_item(sl.load_ledger(self.project), "R-1")["status"]

    def test_pass_report_in_guard_protected_folder_is_accepted(self):
        self.assertTrue(os.path.isdir(os.path.join(self.project, ".studio")))
        self.report(".budgit", "verify")
        r = self.cli("ledger", "verify", "R-1", "--evidence", EVIDENCE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.status(), "verified")

    def test_pass_report_in_old_protected_folder_is_accepted(self):
        self.report(".studio", "verify")
        r = self.cli("ledger", "verify", "R-1", "--evidence", EVIDENCE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_pass_report_in_unprotected_folder_is_refused(self):
        for parts in (("verify",), (".claude", "verify"), ("docs", "verify"), ("reports",)):
            self.report(*parts)
        r = self.cli("ledger", "verify", "R-1", "--evidence", EVIDENCE)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("first line starts with PASS", r.stdout + r.stderr)
        self.assertNotEqual(self.status(), "verified")

    def test_worktree_resolves_report_to_main_root(self):
        main = os.path.join(self.tmp, "main")
        gitdir = os.path.join(main, ".git", "worktrees", "w")
        wt = os.path.join(self.tmp, "wt")
        os.makedirs(gitdir)
        os.makedirs(wt)
        with open(os.path.join(gitdir, "commondir"), "w") as f:
            f.write("../..\n")
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % gitdir)
        sub = os.path.join(wt, "pkg")
        os.makedirs(sub)
        with open(os.path.join(gitdir, "gitdir"), "w") as f:
            f.write(os.path.join(wt, ".git") + "\n")
        self.assertEqual(sl.main_project_root(wt), os.path.realpath(main))
        self.assertEqual(sl.main_project_root(sub), os.path.realpath(main))
        self.assertEqual(sl.verify_report_path(wt, "R-1"),
                         os.path.join(os.path.realpath(main), sl.DATA_NAME, "verify", "R-1.md"))
        self.assertEqual(set(sl.verify_dirs(wt)),
                         {os.path.join(os.path.realpath(main), n, "verify") for n in sl.DATA_NAMES})

    def forged(self, commondir, backlink):
        main = os.path.join(self.tmp, "victim")
        gitdir = os.path.join(main, ".git", "worktrees", "w")
        wt = os.path.join(self.tmp, "evil")
        os.makedirs(gitdir)
        os.makedirs(wt)
        with open(os.path.join(gitdir, "commondir"), "w") as f:
            f.write(commondir + "\n")
        with open(os.path.join(gitdir, "gitdir"), "w") as f:
            f.write(backlink(wt) + "\n")
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % gitdir)
        return wt

    def test_forged_backlink_resolves_to_folder_itself(self):
        wt = self.forged("../..", lambda w: os.path.join(self.tmp, "somewhere-else", ".git"))
        self.assertEqual(sl.main_project_root(wt), os.path.realpath(wt))

    def test_forged_commondir_dotdot_resolves_to_folder_itself(self):
        wt = self.forged("../../../..", lambda w: os.path.join(w, ".git"))
        self.assertEqual(sl.main_project_root(wt), os.path.realpath(wt))

    def test_pass_and_fail_in_both_folders_is_refused(self):
        a = self.report(".budgit", "verify")
        b = self.report(".studio", "verify", text="FAIL\nbroken\n")
        r = self.cli("ledger", "verify", "R-1", "--evidence", EVIDENCE)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        out = r.stdout + r.stderr
        self.assertIn(a, out)
        self.assertIn(b, out)
        self.assertNotEqual(self.status(), "verified")

    def test_pass_in_both_folders_is_accepted(self):
        self.report(".budgit", "verify")
        self.report(".studio", "verify")
        r = self.cli("ledger", "verify", "R-1", "--evidence", EVIDENCE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.status(), "verified")


if __name__ == "__main__":
    import unittest
    unittest.main()
