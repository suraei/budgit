"""R-1313: `budgit ledger done` prunes the clean worktrees of landed lanes once the done proof passed. Real CLI, temp git repo."""
import json
import os
import subprocess
import sys

import landprune
from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


class LandPruneDoneTest(StudioTestCase):
    def git(self, *args, cwd=None):
        r = subprocess.run(["git", "-C", cwd or self.project, *args], env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        super().setUp()
        self.n = 0
        self.init_project()
        self.git("init", "-q", "-b", "main")
        data = sl.load_ledger(self.project)
        for n in range(1, 3):
            sl.add_item(data, "item %d" % n, ["legacy"])
            sl.set_status(self.project, data, "R-%d" % n, "doing")
        sl.save_ledger(self.project, data)
        self.write("base.txt", self.project)
        self.git("add", "base.txt")
        self.git("commit", "-q", "-m", "base")

    def write(self, name, root, text="x\n"):
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def lane(self, name):
        """A worktree with one commit, fast-forwarded into main like the PM's landing. -> (path, sha)."""
        path = os.path.join(self.tmp, "wt-" + name)
        self.git("worktree", "add", "-q", "-b", "claude/" + name, path, "main")
        self.write(name + ".txt", path)
        self.git("add", "-A", cwd=path)
        self.git("commit", "-q", "-m", "work " + name, cwd=path)
        self.git("merge", "-q", "--ff-only", "claude/" + name)
        return path, self.git("rev-parse", "--short", "HEAD")

    def done(self, sha):
        env = dict(os.environ, STUDIO_HOME=self.home)
        return subprocess.run([sys.executable, CLI, "ledger", "done", "R-1", "--evidence", "%s test_thing" % sha,
                               "--outcome", "You see it work", "--project", self.project], env=env, capture_output=True, text=True)

    def test_ledger_done_removes_a_landed_clean_lane(self):
        path, sha = self.lane("r1-x")
        r = self.done(sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(path))
        self.assertNotIn("claude/r1-x", self.git("branch", "--format=%(refname:short)").split())
        self.assertIn("land-prune: removed", r.stdout)

    def test_ledger_done_keeps_a_landed_clean_lane_of_another_item_untouched_and_unlisted(self):
        other, _ = self.lane("r2-y")
        path, sha = self.lane("r1-x")
        r = self.done(sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.isdir(other))
        self.assertIn("claude/r2-y", self.git("branch", "--format=%(refname:short)").split())
        self.assertNotIn("r2-y", r.stdout + r.stderr)

    def test_the_item_number_must_end_at_a_digit_boundary(self):
        longer, _ = self.lane("r10-z")                          # R-10's lane, not R-1's
        by_branch, sha = self.lane("R1a-w")                     # case-insensitive; a letter after the number is fine
        r = self.done(sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(longer))
        self.assertFalse(os.path.exists(by_branch))

    def test_ledger_done_keeps_a_dirty_lane_and_says_why(self):
        path, sha = self.lane("r1-b")
        self.write("r1-b.txt", path, "changed\n")
        r = self.done(sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(path))
        self.assertIn("kept", r.stdout)
        self.assertIn("dirty", r.stdout)

    def test_batch_with_one_refused_id_prunes_only_the_lanes_of_the_ids_that_closed(self):
        first, _ = self.lane("r1-x")
        second, sha = self.lane("r2-y")
        batch = os.path.join(self.project, "batch.tsv")
        with open(batch, "w") as f:
            f.write("R-1\tdeadbeef test_a\tYou see it work\nR-2\t%s test_b\tYou see it work\n" % sha)
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, CLI, "ledger", "done", "--file", batch, "--project", self.project], env=env,
                           capture_output=True, text=True)
        self.assertIn("R-2: ok, done", r.stdout)
        self.assertTrue(os.path.isdir(first))
        self.assertFalse(os.path.exists(second))
        self.assertNotIn("r1-x", r.stdout.split("R-2: ok, done", 1)[1])

    def test_a_lane_holding_a_token_of_another_open_item_is_kept(self):
        both, sha = self.lane("r1-r2-both")                    # R-2 is still open
        r = self.done(sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(both))

    def test_an_i_id_in_a_result_line_is_never_a_closed_item_number(self):
        result = "I-5 -> done\nI-7: ok, done\nR-3 -> done\nR-4: ok, done\nR-9: refused (no proof)\nR-10 -> open"
        self.assertEqual(["3", "4"], landprune.closed_numbers(result))     # fails if I- ids were accepted as items

    def merge_close(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        return subprocess.run([sys.executable, CLI, "merge-close", *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def worktrees(self):
        return self.git("worktree", "list", "--porcelain")

    def test_merge_close_without_apply_changes_nothing_and_lists_the_plan(self):
        clean, _ = self.lane("r1-x")
        dirty, _ = self.lane("r2-d")
        self.write("r2-d.txt", dirty, "changed\n")
        before = self.worktrees()
        r = self.merge_close()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(before, self.worktrees())
        self.assertTrue(os.path.isdir(clean))
        self.assertIn("claude/r1-x", self.git("branch", "--format=%(refname:short)").split())
        self.assertIn("land-prune: would remove", r.stdout)
        self.assertIn("r1-x", r.stdout)
        self.assertIn("land-prune: kept", r.stdout)
        self.assertIn("dirty", r.stdout)

    def test_merge_close_apply_removes_the_landed_clean_lane_and_keeps_a_dirty_one(self):
        clean, _ = self.lane("r1-x")
        dirty, _ = self.lane("r2-d")
        self.write("r2-d.txt", dirty, "changed\n")
        r = self.merge_close("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(clean))
        self.assertTrue(os.path.isdir(dirty))
        branches = self.git("branch", "--format=%(refname:short)").split()
        self.assertNotIn("claude/r1-x", branches)
        self.assertIn("claude/r2-d", branches)
        self.assertIn("land-prune: removed", r.stdout)
        self.assertIn("dirty", r.stdout)

    def test_merge_close_json_keeps_stdout_json_and_sends_the_land_prune_lines_to_stderr(self):
        clean, _ = self.lane("r1-x")
        r = self.merge_close("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        json.loads(r.stdout)                                               # stdout is still one JSON document
        self.assertNotIn("land-prune", r.stdout)
        self.assertIn("land-prune: would remove", r.stderr)
        self.assertTrue(os.path.isdir(clean))

    def test_a_done_that_fails_its_proof_prunes_nothing(self):
        path, _sha = self.lane("r1-c")
        r = self.done("deadbeef")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isdir(path))
        self.assertIn("claude/r1-c",self.git("branch", "--format=%(refname:short)").split())
        self.assertNotIn("land-prune", r.stdout + r.stderr)
