"""R-1360 G2a: the lane worktree move door. `worktree move <src> <dst>` passes only for the coordinator, with two absolute plain paths and
no flag, when <src> is registered in the common dir (`worktrees/<id>/gitdir` holds the realpath of `<src>/.git`, read with plain open),
<src> and <dst> are outside the checkout, and <dst> does not exist. Each rule is broken alone below. The fixture is a real temp repo with a
real `worktree add` (test code may run the version tool; the guard never does)."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import mergeguard  # noqa: E402

G = "g" + "it"


def run(*args, cwd):
    subprocess.run((G,) + args, cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid"))


class MoveDoor(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="r1360g2a-"))
        self.main = os.path.join(self.tmp, "main")
        os.makedirs(self.main)
        run("init", "-q", cwd=self.main)
        run("commit", "-q", "--allow-empty", "-m", "x", cwd=self.main)
        self.wt = os.path.join(self.tmp, "lane")
        run("worktree", "add", "-q", "--detach", self.wt, cwd=self.main)
        self.inner = os.path.join(self.main, "inner")
        run("worktree", "add", "-q", "--detach", self.inner, cwd=self.main)
        self.dst = os.path.join(self.tmp, "moved")
        patch = mock.patch.object(mergeguard, "checkout_root", return_value=self.main)
        patch.start()
        self.addCleanup(patch.stop)

    def judge(self, tail, agent_id=None):
        return mergeguard.decision("Bash", {"command": "%s worktree %s" % (G, tail)}, self.main, agent_id=agent_id)

    def refused(self, tail, agent_id=None):
        d = self.judge(tail, agent_id)
        self.assertEqual(d[0] if d else None, "deny", (tail, d))

    def test_allow_a_registered_worktree_moved_outside_by_the_coordinator(self):
        self.assertIsNone(self.judge("move %s %s" % (self.wt, self.dst)))

    def test_rule_a_a_subagent_is_refused(self):
        self.refused("move %s %s" % (self.wt, self.dst), agent_id="agent-1")

    def test_rule_b_any_flag_or_a_wrong_word_count_is_refused(self):
        for tail in ("move -f %s %s", "move --force %s %s", "move --lock %s %s", "move %s %s -f", "move %s %s --force"):
            with self.subTest(tail=tail):
                self.refused(tail % (self.wt, self.dst))
        self.refused("move %s" % self.wt)
        self.refused("move %s %s %s" % (self.wt, self.dst, self.dst + "2"))

    def test_rule_c_a_path_that_is_not_absolute_and_plain_is_refused(self):
        for src, dst in (("lane", self.dst), (self.wt, "moved"), (self.wt, self.tmp + "/x/../moved"), (self.wt, "$HOME/moved"),
                         (self.wt, self.tmp + "/mo*"), (self.wt, self.tmp + "/mo?ved"), (self.wt, "`x`"), (self.wt, self.tmp + "/m[a]")):
            with self.subTest(src=src, dst=dst):
                self.refused("move %s %s" % (src, dst))

    def test_rule_d_an_unregistered_source_the_main_worktree_or_an_unreadable_file_is_refused(self):
        other = os.path.join(self.tmp, "plain")
        os.makedirs(other)
        self.refused("move %s %s" % (other, self.dst))
        self.refused("move %s %s" % (self.main, self.dst))
        gfile = os.path.join(self.main, ".git", "worktrees", "lane", "gitdir")
        os.chmod(gfile, 0)
        self.addCleanup(os.chmod, gfile, 0o644)
        self.refused("move %s %s" % (self.wt, self.dst))

    def test_rule_e_a_source_or_destination_inside_the_checkout_is_refused(self):
        self.refused("move %s %s" % (self.inner, self.dst))
        self.refused("move %s %s" % (self.wt, os.path.join(self.main, "in")))
        self.refused("move %s %s" % (self.wt, self.main))

    def test_rule_f_an_existing_destination_is_refused(self):
        os.makedirs(self.dst)
        self.refused("move %s %s" % (self.wt, self.dst))

    def test_worktree_remove_stays_refused(self):
        self.refused("remove %s" % self.wt)
        self.refused("remove -f %s" % self.wt)


if __name__ == "__main__":
    unittest.main()
