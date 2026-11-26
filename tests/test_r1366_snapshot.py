"""R-1366: `budgit ci snapshot` makes one clean commit of main's committed tree on its own snapshot chain (parentless root,
never a remote-tracking ref as parent). Made-up name only; temp repos; never the real home."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from helpers import ROOT  # noqa: F401

import cisnapshot
import leak

NAME = "zebracorp"
ACCOUNT = "zebra-user"
NOREPLY = "12345+zebra-user@users.noreply.github.com"
CONFIG = {"protected_dirs": ["/nonexistent-r1366/" + NAME],
          "github_noreply": {ACCOUNT: NOREPLY}, "default_github_account": ACCOUNT}
GIT = ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null"]


def git(repo, *args):
    r = subprocess.run(GIT + ["-C", repo] + list(args), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = os.path.join(self.tmp.name, "remote.git")
        self.repo = os.path.join(self.tmp.name, "myproj-r1366")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "remote", "add", "origin", self.remote)
        self.put("base.txt", "base\n")
        self.commit("base")
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "remote", "set-url", "origin", "https://github.com/zebra-user/myproj.git")

    def put(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)

    def snap(self, config=CONFIG, **kw):
        return cisnapshot.snapshot(self.repo, config=config, **kw)

    def ref(self, name):
        r = subprocess.run(GIT + ["-C", self.repo, "rev-parse", "--verify", "-q", name], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def test_first_snapshot_has_no_parent_and_old_name_never_reaches_it(self):
        self.put("old.txt", "about %s\n" % NAME)
        self.commit("add old")
        git(self.repo, "rm", "-q", "old.txt")
        self.put("new.txt", "clean\n")
        self.commit("remove old")
        text, code = self.snap()
        self.assertEqual(code, 0, text)
        new = self.ref("refs/heads/ci-snapshot")
        self.assertTrue(new)
        self.assertEqual(git(self.repo, "rev-list", "--parents", "-n", "1", new), new)
        self.assertEqual(list(leak.push_check(self.repo, CONFIG, rev=new, exclude=[])), [])
        for sha in git(self.repo, "rev-list", "--objects", new).split("\n"):
            if sha:
                self.assertNotIn(NAME, git(self.repo, "cat-file", "-p", sha.split()[0]))
        self.assertIn("push origin refs/heads/ci-snapshot:refs/heads/ci-snapshot", text)

    def test_second_snapshot_takes_the_first_as_parent_and_chain_check_passes(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        self.assertEqual(self.snap()[1], 0)
        first = self.ref("refs/heads/ci-snapshot")
        self.assertTrue(cisnapshot._chain_ok(self.repo, "ci-snapshot"))
        self.put("b.txt", "b\n")
        self.commit("b")
        self.assertEqual(self.snap()[1], 0)
        self.assertEqual(self.ref("refs/heads/ci-snapshot^"), first)
        self.assertTrue(cisnapshot._chain_ok(self.repo, "ci-snapshot"))

    def test_stale_remote_tracking_ref_with_a_name_is_never_used(self):
        git(self.repo, "checkout", "-q", "-b", "side")
        self.put("leak.txt", "about %s\n" % NAME)
        self.commit("leaky")
        bad = self.ref("HEAD")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "update-ref", "refs/remotes/origin/main", bad)
        git(self.repo, "update-ref", "refs/remotes/origin/ci-snapshot", bad)
        self.put("a.txt", "a\n")
        self.commit("a")
        text, code = self.snap()
        self.assertEqual(code, 0, text)
        new = self.ref("refs/heads/ci-snapshot")
        self.assertNotEqual(self.ref(new + "^"), bad)
        for sha in git(self.repo, "rev-list", "--objects", new).split("\n"):
            if sha:
                self.assertNotIn(NAME, git(self.repo, "cat-file", "-p", sha.split()[0]))

    def test_existing_branch_that_is_not_a_snapshot_chain_is_refused(self):
        git(self.repo, "branch", "work")
        before = self.ref("refs/heads/work")
        text, code = self.snap(branch="work")
        self.assertEqual(code, 1)
        self.assertIn("not a snapshot chain", text)
        self.assertEqual(self.ref("refs/heads/work"), before)

    def test_main_and_master_are_refused_and_main_is_not_moved(self):
        before = self.ref("refs/heads/main")
        for branch in ("main", "master"):
            text, code = self.snap(branch=branch)
            self.assertEqual(code, 1, branch)
        self.assertEqual(self.ref("refs/heads/main"), before)
        self.assertIsNone(self.ref("refs/heads/master"))

    def test_author_and_committer_are_the_noreply_identity(self):
        git(self.repo, "config", "user.email", "other@example.invalid")
        git(self.repo, "config", "user.name", "Other")
        self.put("a.txt", "a\n")
        self.commit("a")
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "env@example.invalid", "GIT_COMMITTER_EMAIL": "env@example.invalid",
                                          "GIT_AUTHOR_NAME": "Env", "GIT_COMMITTER_NAME": "Env"}):
            text, code = self.snap()
        self.assertEqual(code, 0, text)
        out = git(self.repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>", "refs/heads/ci-snapshot")
        self.assertEqual(out, "%s <%s>|%s <%s>" % (ACCOUNT, NOREPLY, ACCOUNT, NOREPLY))

    def test_no_noreply_identity_is_refused(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        cfg = {"protected_dirs": CONFIG["protected_dirs"]}
        text, code = self.snap(config=cfg)
        self.assertEqual(code, 1)
        self.assertIn("no noreply identity", text)
        self.assertIsNone(self.ref("refs/heads/ci-snapshot"))
        cfg = dict(CONFIG, github_noreply={ACCOUNT: "me@example.invalid"})
        self.assertEqual(self.snap(config=cfg)[1], 1)

    def test_bad_remote_name_is_refused(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        for remote in ("-x", "bad..name", "a b"):
            text, code = self.snap(remote=remote)
            self.assertEqual(code, 1, remote)
        self.assertIsNone(self.ref("refs/heads/ci-snapshot"))

    def test_printed_command_quotes_the_path(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        text, code = self.snap()
        self.assertEqual(code, 0, text)
        self.assertIn("push origin refs/heads/ci-snapshot:refs/heads/ci-snapshot", text)
        self.assertNotIn("--force", text)

    def test_path_with_space_and_quote_is_shell_quoted_and_never_forced(self):
        import shlex
        repo = os.path.join(self.tmp.name, "my dir's", "proj")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "main")
        with open(os.path.join(repo, "a.txt"), "w") as f:
            f.write("a\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "a")
        git(repo, "remote", "add", "origin", "https://github.com/zebra-user/proj.git")
        text, code = cisnapshot.snapshot(repo, config=CONFIG)
        self.assertEqual(code, 0, text)
        self.assertIn("git -C %s push" % shlex.quote(os.path.abspath(repo)), text)
        self.assertNotIn("--force", text)
        self.assertNotIn(" +", text)

    def test_name_in_main_tip_is_refused_by_the_full_scan_and_no_ref_written(self):
        self.put("tip.txt", "about %s\n" % NAME)
        self.commit("tip")
        text, code = self.snap()
        self.assertEqual(code, 1)
        self.assertNotIn(NAME, text)
        self.assertIsNone(self.ref("refs/heads/ci-snapshot"))

    def test_identical_tree_reports_nothing_new(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        self.assertEqual(self.snap()[1], 0)
        text, code = self.snap()
        self.assertEqual(code, 0)
        self.assertIn("nothing new", text)

    def test_branch_name_with_the_name_or_the_project_folder_is_refused(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        for branch in ("ci-%s" % NAME, "x-myproj-r1366"):
            text, code = self.snap(branch=branch)
            self.assertEqual(code, 1, branch)
            self.assertIsNone(self.ref("refs/heads/" + branch))

    def test_dirty_working_tree_is_never_included(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        self.put("dirty.txt", "uncommitted\n")
        self.put("base.txt", "changed\n")
        self.assertEqual(self.snap()[1], 0)
        files = git(self.repo, "ls-tree", "-r", "--name-only", "refs/heads/ci-snapshot").split("\n")
        self.assertNotIn("dirty.txt", files)
        self.assertEqual(git(self.repo, "show", "refs/heads/ci-snapshot:base.txt"), "base")

    def refused_remote(self, label):
        self.put("a.txt", "a\n")
        self.commit("a")
        text, code = self.snap()
        self.assertEqual(code, 1, label + ": " + text)
        self.assertIn("not in your GitHub account", text, label)
        self.assertIsNone(self.ref("refs/heads/ci-snapshot"), label)

    def test_s3_1_pushurl_to_another_owner_is_refused(self):
        git(self.repo, "config", "remote.origin.pushurl", "https://github.com/other-user/myproj.git")
        self.refused_remote("pushurl")

    def test_s3_1_pushinsteadof_to_another_host_is_refused(self):
        git(self.repo, "config", "url.https://evil.example/zebra-user/.pushInsteadOf", "https://github.com/zebra-user/")
        self.refused_remote("pushInsteadOf")

    def test_s3_1_second_url_line_elsewhere_is_refused(self):
        git(self.repo, "config", "--add", "remote.origin.url", "https://github.com/other-user/myproj.git")
        self.refused_remote("second url")

    def test_s3_1_plain_remote_of_her_account_passes_and_prints_the_push_url(self):
        self.put("a.txt", "a\n")
        self.commit("a")
        text, code = self.snap()
        self.assertEqual(code, 0, text)
        self.assertIn("pushes to https://github.com/zebra-user/myproj.git", text)

    def test_s3_2_url_bypass_forms_are_refused(self):
        for url in ("https://github.com@evil.example/zebra-user/x", "https://github.com:443/zebra-user/x",
                    "https://GITHUB.COM/zebra-user/x", "ext::sh -c x", "file:///tmp/x",
                    "https://zebra-user:pw@github.com/zebra-user/x", "https://user@github.com/zebra-user/x"):
            self.assertIsNone(cisnapshot._url_owner(url), url)
            git(self.repo, "remote", "set-url", "origin", url)
            text, code = self.snap()
            self.assertEqual(code, 1, url)
            self.assertIsNone(self.ref("refs/heads/ci-snapshot"), url)


if __name__ == "__main__":
    unittest.main()
