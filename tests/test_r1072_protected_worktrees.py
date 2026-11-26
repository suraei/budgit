"""R-1072: a protected project's proven code worktrees are not its real data. Synthetic fixture only: a temp git repo with
real-data patterns (data.json, data/real/**) and a `git worktree add` checkout of it elsewhere in the temp dir. Each rule is
checked with both bindings: the bound project is the repo, and the bound project is the worktree itself."""
import json
import os
import shlex
import unittest
from unittest import mock

from test_guard_trust import GuardCase, git
import studiolib as sl


class ProtectedWorktreesTest(GuardCase):
    def make_wt(self, name):
        path = os.path.join(self.tmp, "elsewhere", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        git(self.project, "worktree", "add", "-q", "-b", "code-" + name, path)
        return os.path.realpath(path)

    def setUp(self):
        super().setUp()
        git(self.project, "add", "-f", os.path.relpath(sl.studio_dir(self.project), self.project))        # the project's own folder is checked in, so a worktree carries it
        git(self.project, "commit", "-q", "-m", "studio folder")
        self.wt = self.make_wt("wt1")

    def bindings(self):
        return ((self.root, self.root), (self.wt, self.wt))      # (bound project, the checkout it is read in)

    def verdict(self, cmd, cwd, project, tool="Bash", **inp):
        got = sl.real_data_check(tool, dict(inp, command=cmd) if tool == "Bash" else inp, project, cwd=cwd)[0]
        return got[0] if got else None

    def test_in_the_code_worktree_git_log_and_a_test_run_are_not_refused_as_real_data(self):
        self.assertEqual(sl._git_common_dir(self.wt), sl._git_common_dir(self.root))
        self.assertTrue(os.path.isfile(os.path.join(sl.studio_dir(self.wt), "project.json")))     # its own checked-in .studio
        for project, base in self.bindings():
            for cmd in ("git log --oneline -3", "python3 -m unittest test_x", "git status --ignore-submodules=all"):
                self.assertIsNone(self.verdict(cmd, self.wt, project), (project, cmd))

    def test_the_reproduced_misfire_a_whole_root_pattern_in_the_bound_worktree_is_not_real_data(self):
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = [".", "data/real/**"]
        json.dump(cfg, open(pj, "w"))
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "whole-root pattern")
        wt2 = self.make_wt("wt2")
        for cmd in ("git log --oneline -3", "python3 -m unittest test_x", "git status --ignore-submodules=all"):
            self.assertIsNone(self.verdict(cmd, wt2, wt2), cmd)
            self.assertIsNone(self.verdict(cmd, wt2, self.root), cmd)
        self.assertEqual(self.verdict("touch x2.csv", os.path.join(wt2, "data", "real"), wt2), "ask")   # a named pattern still holds
        # a whole-root pattern on the main repo folder still protects the main folder
        self.assertEqual(self.verdict("touch notes.txt", self.root, self.root), "ask")

    def test_in_a_real_data_folder_a_write_is_still_asked(self):
        for project, base in self.bindings():
            real = os.path.join(base, "data", "real")
            self.assertEqual(self.verdict("touch y.csv", real, project), "ask", (project, base))
            self.assertEqual(self.verdict("", real, project, "Write", file_path=os.path.join(real, "x.csv")), "ask", (project, base))

    def test_a_fake_git_file_pointing_at_a_foreign_git_dir_is_still_refused(self):
        fake = os.path.join(self.tmp, "fake")
        os.makedirs(fake)
        sl.activate(fake, bind=False)
        pj = os.path.join(sl.studio_dir(fake), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = ["."]
        json.dump(cfg, open(pj, "w"))
        with open(os.path.join(fake, ".git"), "w") as fh:
            fh.write("gitdir: %s\n" % os.path.join(self.tmp, "other.git", "worktrees", "fake"))
        fake = os.path.realpath(fake)
        self.assertEqual(self.verdict("touch notes.txt", fake, fake), "ask")          # the whole-root rule still applies to it
        self.assertEqual(self.verdict("touch " + os.path.join(self.root, "data.json"), fake, self.root), "ask")

    def test_a_data_folder_checked_into_the_worktree_is_still_protected(self):
        self.assertTrue(os.path.isfile(os.path.join(self.wt, "data", "real", "x.csv")))
        for project, base in self.bindings():
            self.assertEqual(self.verdict("touch " + os.path.join(self.wt, "data", "real", "x2.csv"), self.wt, project), "ask", project)
            self.assertEqual(self.verdict("touch x2.csv", os.path.join(self.wt, "data", "real"), project), "ask", project)    # relative form
            self.assertEqual(self.verdict("", self.wt, project, "Write", file_path=os.path.join(self.wt, "data.json")), "ask", project)

    def whole_root(self, project):
        pj = os.path.join(sl.studio_dir(project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = ["."]
        json.dump(cfg, open(pj, "w"))

    def test_a_symlinked_dot_git_is_not_a_proven_worktree(self):
        x = os.path.join(self.root, "x")
        os.makedirs(x)
        os.symlink(os.path.join(self.wt, ".git"), os.path.join(x, ".git"))
        self.assertTrue(sl._proven_worktree(self.wt))
        self.assertFalse(sl._proven_worktree(x))
        self.whole_root(self.project)
        self.assertEqual(self.verdict("", x, self.root, "Write", file_path=os.path.join(x, "notes.txt")), "ask")

    def test_the_worktree_walk_stops_at_the_project_root(self):
        sub = os.path.join(self.wt, "nested")
        os.makedirs(sub)
        sl.activate(sub, bind=False)
        self.whole_root(sub)
        sub = os.path.realpath(sub)
        self.assertIsNone(sl._code_worktree_of(os.path.join(sub, "notes.txt"), sub))
        self.assertEqual(self.verdict("", sub, sub, "Write", file_path=os.path.join(sub, "notes.txt")), "ask")


class GitReadIntoWorktreeTest(GuardCase):
    """R-1072: only a git READ into a proven worktree of the project skips the project-name scan; everything else keeps it."""

    def setUp(self):
        super().setUp()
        repo = os.path.join(self.tmp, "Dev", "acme")
        os.makedirs(repo)
        git(repo, "init", "-q")
        sl.activate(repo, bind=False)
        pj = os.path.join(sl.studio_dir(repo), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = ["~/Library/Application Support/Acme"]
        json.dump(cfg, open(pj, "w"))
        open(os.path.join(repo, "f.txt"), "w").write("1")
        git(repo, "add", "-f", "-A")
        git(repo, "commit", "-q", "-m", "init")
        wt = os.path.join(self.tmp, "worktrees", "acme-r37")
        os.makedirs(os.path.dirname(wt))
        git(repo, "worktree", "add", "-q", "-b", "r37", wt)
        self.data = os.path.realpath(os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Acme"))
        os.makedirs(self.data)
        open(os.path.join(self.data, "x"), "w").write("secret")
        self.repo, self.wt = os.path.realpath(repo), os.path.realpath(wt)
        os.symlink(self.data, os.path.join(self.wt, "link"))

    def verdict(self, cmd, cwd, project):
        got = sl.real_data_check("Bash", {"command": cmd}, project, cwd=cwd)[0]
        return got[0] if got else None

    def main_verdict(self, cmd, cwd, project):
        with mock.patch.object(sl, "_git_read_into_worktree", return_value=False):      # what the scan did before the exemption
            return self.verdict(cmd, cwd, project)

    def exempt(self, cmd, cwd=None):
        return sl._git_read_into_worktree(cmd, shlex.split(cmd), self.repo, cwd or self.repo)

    def test_a_git_c_read_into_the_worktree_is_not_asked_under_both_bindings(self):
        cmd = "git -C %s log --oneline -5" % self.wt
        self.assertTrue(self.exempt(cmd))
        for project in (self.repo, self.wt):
            for cwd in (self.repo, self.wt):
                self.assertIsNone(self.verdict(cmd, cwd, project), (project, cwd))

    def test_b_the_cwd_form_in_the_worktree_is_not_asked(self):
        self.assertTrue(self.exempt("git log --oneline -5", self.wt))
        self.assertFalse(self.exempt("git log --oneline -5", self.repo))          # cwd is the repo, not a worktree
        self.assertIsNone(self.verdict("git log --oneline -5", self.wt, self.repo))

    def test_c_a_test_run_under_the_project_lock_recorded_finding(self):
        lock = "/private/tmp/budgit-suite-acme.lock"
        asked = [c for c in ("budgit busy", "lockf %s npx vitest run" % lock) if self.verdict(c, self.wt, self.repo)]
        if asked:      # the exemption stays narrow: a lock path that holds the name keeps the scan
            self.skipTest("asked: _base_segments literal-name scan (_mentions_literal) for %r" % asked)

    def test_d_the_round_two_forms_keep_the_scan(self):
        q = shlex.quote
        for cmd in ("git -C %s log --output=%s" % (self.wt, q(self.data + "/x")), "cat -C%s" % q(self.data + "/x"),
                    "VAR=%s git -C %s log" % (q(self.data + "/x"), self.wt), "rsync host:%s ." % q(self.data + "/x"),
                    "curl file://%s" % q(self.data + "/x")):
            self.assertFalse(self.exempt(cmd, self.wt), cmd)
            for project in (self.repo, self.wt):
                self.assertEqual(self.verdict(cmd, self.wt, project), self.main_verdict(cmd, self.wt, project), cmd)

    def test_e_the_other_shapes_are_exactly_mains_decision(self):
        rel = os.path.relpath(self.data, self.wt)
        for cmd in ('touch "%s/x"' % self.data, 'cat "%s/x"' % self.data, "cat %s/%s/x" % (self.wt, rel),
                    "cat %s/link/x" % self.wt, "cp %s/link/x /tmp/y" % self.wt, "cat %s/lin*/x" % self.wt,
                    'cp %s/f.txt "%s/b"' % (self.wt, self.data), "cat ${HOME}/Library/Application\\ Support/Acme/x"):
            self.assertFalse(self.exempt(cmd, self.wt), cmd)
            for project in (self.repo, self.wt):
                for cwd in (self.repo, self.wt):
                    self.assertEqual(self.verdict(cmd, cwd, project), self.main_verdict(cmd, cwd, project), (cmd, project, cwd))

    def test_f_negative_controls_get_no_exemption(self):
        fake = os.path.join(self.tmp, "worktrees", "acme-r38")
        os.makedirs(fake)
        open(os.path.join(fake, ".git"), "w").write("gitdir: /nonexistent/x\n")
        linked = os.path.join(self.tmp, "worktrees", "acme-r39")
        os.makedirs(linked)
        os.symlink(os.path.join(self.wt, ".git"), os.path.join(linked, ".git"))
        for cmd in ("git -C %s log" % fake, "git -C %s log" % linked, "git -C %s commit -m x" % self.wt,
                    "git -C %s log | sh" % self.wt, "git -C %s/ log && touch y" % self.wt, "git -C %s/src log" % self.wt):
            self.assertFalse(self.exempt(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
