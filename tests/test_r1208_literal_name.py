"""R-1208: a project whose folder has the same last name as its protected data folder is not refused on its own path.
Synthetic fixture only: project <tmp>/Dev/acmebook, protected path ~/Library/Application Support/Acmebook (HOME is a temp
folder), a proven code worktree <project>/.claude/worktrees/agent-x made with `git worktree add`. Nothing reads the live machine.

Two levels are checked. `base_asks` is the name scan itself (`_base_segments`): a bare word keeps it asking. `verdict` is the whole
guard: its later layer drops a "name appears" ask for a harmless command (R-493), so a harmless bare word is judged at the scan."""
import json
import os
import shlex
import unittest
from unittest import mock

from test_guard_trust import GuardCase, git
import studiolib as sl

PATTERNS = ["~/Library/Application Support/Acmebook"]


class LiteralNameOwnPathTest(GuardCase):
    def setUp(self):
        super().setUp()
        repo = os.path.join(self.tmp, "Dev", "acmebook")
        os.makedirs(repo)
        git(repo, "init", "-q")
        sl.activate(repo, bind=False)
        pj = os.path.join(sl.studio_dir(repo), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = list(PATTERNS)
        json.dump(cfg, open(pj, "w"))
        open(os.path.join(repo, "f.txt"), "w").write("1")
        git(repo, "add", "-f", "-A")
        git(repo, "commit", "-q", "-m", "init")
        wt = os.path.join(repo, ".claude", "worktrees", "agent-x")
        os.makedirs(os.path.dirname(wt))
        git(repo, "worktree", "add", "-q", "-b", "agent-x", wt)
        self.home = os.path.realpath(os.path.expanduser("~"))
        self.data = os.path.join(self.home, "Library", "Application Support", "Acmebook")
        os.makedirs(self.data)
        open(os.path.join(self.data, "y"), "w").write("secret")
        self.repo, self.wt = os.path.realpath(repo), os.path.realpath(wt)

    def verdict(self, cmd, cwd=None):
        got = sl.real_data_check("Bash", {"command": cmd}, self.repo, cwd=cwd or self.repo)[0]
        return got[0] if got else None

    def base_asks(self, cmd, cwd=None):
        return bool(sl._base_segments(cmd, self.repo, PATTERNS, cwd or self.repo, "%s %s"))

    def old_verdict(self, cmd, cwd=None):
        """What 03b0e1f3 did: the whole segment is tested for the protected name."""
        with mock.patch.object(sl, "_literal_outside_own_path", lambda seg, project, pats, base: sl._mentions_literal(seg, pats)):
            return self.verdict(cmd, cwd)

    # --- the false positive is gone
    def test_prefix_into_the_worktree_passes_in_every_option_form(self):
        self.assertEqual(sl._code_worktree_of(self.wt, self.repo), self.wt)
        for cmd in ("npm --prefix %s test -- src/lib/x" % self.wt,
                    "npm --prefix=%s test -- src/lib/x" % self.wt,
                    "npm -C %s test -- src/lib/x" % self.wt,
                    "npm --prefix %s/src test" % self.repo):
            for cwd in (self.repo, self.wt):
                self.assertFalse(self.base_asks(cmd, cwd), cmd)
                self.assertIsNone(self.verdict(cmd, cwd), cmd)

    def test_a_task_runner_given_the_project_itself_is_not_asked_for_its_name(self):
        for cmd in ("npm --prefix %s test" % self.repo, "pip install -e %s" % self.repo):
            self.assertIsNone(self.verdict(cmd), cmd)

    def test_the_same_commands_were_asked_before_the_fix(self):
        for cmd in ("npm --prefix %s test -- src/lib/x" % self.wt, "npm --prefix %s test" % self.repo):
            self.assertEqual(self.old_verdict(cmd), "ask", cmd)

    def test_npm_test_with_cwd_in_the_worktree_passes(self):
        for cmd in ("npm test", "npm test -- src/lib/x"):
            self.assertFalse(self.base_asks(cmd, self.wt), cmd)
            self.assertIsNone(self.verdict(cmd, self.wt), cmd)

    # --- every other mention keeps asking
    def test_a_copy_into_the_real_data_folder_still_asks(self):
        cmd = "cp x %s" % shlex.quote(self.data + "/y")
        for cwd in (self.repo, self.wt):
            self.assertTrue(self.base_asks(cmd, cwd))
            self.assertEqual(self.verdict(cmd, cwd), "ask")

    def test_a_bare_word_still_asks(self):
        for cwd in (self.repo, self.wt, self.home):
            for cmd in ("echo Acmebook > f", "echo acmebook", "touch ACMEBOOK"):
                self.assertTrue(self.base_asks(cmd, cwd), (cmd, cwd))

    def test_the_name_inside_the_project_is_not_the_projects_own_path(self):
        for cmd in ("npm --prefix %s/Acmebook test" % self.repo,
                    "npm --prefix=%s/sub/acmebook test" % self.wt,
                    "echo %s/x Acmebook" % self.repo,
                    "echo foo:%s/x" % self.repo):
            self.assertTrue(self.base_asks(cmd), cmd)

    def test_a_relative_word_is_read_from_the_folder_the_command_works_in(self):
        """`-C DIR` / `--directory=DIR` move where a relative word points: `../Acmebook/y` is then the real data."""
        beside = os.path.join(os.path.dirname(self.data), "Other")
        os.makedirs(beside)
        q = shlex.quote(beside)
        for cmd in ("tar -C %s -cf - ../Acmebook/y" % q,
                    "tar --directory=%s -cf - ../Acmebook/y" % q,
                    "make -C %s -f ../Acmebook/Makefile" % q,
                    "git -C %s add ../Acmebook/y" % q,
                    "cp ../Acmebook/y /tmp/o"):
            self.assertTrue(self.base_asks(cmd), cmd)
            self.assertTrue(sl._literal_outside_own_path(cmd, self.repo, PATTERNS, self.repo), cmd)
        self.assertEqual(self.verdict("make -C %s -f ../Acmebook/Makefile" % q), "ask")
        self.assertEqual(self.verdict("git -C %s add ../Acmebook/y" % q), "ask")
        # `..` inside an absolute word leaves the project: it is never cut
        self.assertTrue(sl._literal_outside_own_path("tar -cf - %s/../x/../acmebook/y" % self.repo, self.repo, PATTERNS, self.repo))

    def test_a_line_that_makes_a_link_keeps_the_name_scan_on_every_segment(self):
        """A link made earlier on the line may point inside the project at the data; a later segment reads through it."""
        cmd = 'X=Acme; ln -s "%s/${X}book" %s/l; cp %s/l/y /tmp/o' % (os.path.dirname(self.data), self.repo, self.repo)
        self.assertTrue(self.base_asks(cmd))
        self.assertTrue(self.base_asks("cp %s/l/y /tmp/o; ln -s a b" % self.repo))
        self.assertFalse(self.base_asks("cp %s/l/y /tmp/o" % self.repo))      # without a link on the line the project's own path passes

    def test_a_path_that_leaves_the_project_through_dotdot_and_reaches_the_data_still_asks(self):
        up = os.path.relpath(self.data, self.repo)
        self.assertTrue(up.startswith(".."), up)
        for cmd in ("npm --prefix %s/%s test" % (self.repo, shlex.quote(up)),
                    "npm --prefix=%s test" % shlex.quote(self.wt + "/" + os.path.relpath(self.data, self.wt)),
                    "npm --prefix %s test" % shlex.quote(self.repo + "/sub/../" + up)):
            self.assertTrue(self.base_asks(cmd), cmd)
            self.assertEqual(self.verdict(cmd), "ask", cmd)

    def test_a_glob_that_names_the_data_folders_parent_still_asks(self):
        parent = os.path.dirname(self.data)
        for cmd in ("touch %s/*" % shlex.quote(parent),
                    "touch %s/Appl*/Acme*" % shlex.quote(os.path.dirname(parent)),
                    "npm --prefix %s/src/* test" % self.repo):      # a glob next to the project's name fails closed
            self.assertTrue(self.base_asks(cmd), cmd)
        self.assertEqual(self.verdict("ls %s/*" % shlex.quote(parent)), "ask")

    def test_a_word_that_cannot_be_read_or_resolved_still_asks(self):
        for cmd in ("npm --prefix '%s/x test" % self.repo,                  # a parse error
                    'npm --prefix "%s/$X" test' % self.repo,                 # a variable
                    "npm --prefix $(echo %s) test" % self.repo,              # a substitution
                    "npm --prefix `echo %s` test" % self.repo,
                    "npm --prefix=%s/{a,b} test" % self.repo):
            self.assertTrue(self.base_asks(cmd), cmd)

    # --- the shared function itself
    def test_the_function_judges_the_name_not_the_path(self):
        f = lambda seg: sl._literal_outside_own_path(seg, self.repo, PATTERNS, self.repo)
        self.assertFalse(f("npm --prefix %s test" % self.wt))
        self.assertFalse(f("npm test"))
        self.assertTrue(f("echo Acmebook"))
        self.assertTrue(f("cat %s" % shlex.quote(self.data + "/y")))
        self.assertTrue(f("echo 'unterminated %s" % self.repo))

if __name__ == "__main__":
    unittest.main()
