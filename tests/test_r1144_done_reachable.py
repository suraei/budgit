"""R-1144: `ledger done` refuses a proof sha that main (or the latest release) cannot reach. Real CLI, temp git repo."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com")


class DoneReachableTest(StudioTestCase):
    def git(self, *args):
        r = subprocess.run(["git", "-C", self.project, *args], env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, message="work"):
        self.n += 1
        with open(os.path.join(self.project, "f%d.txt" % self.n), "w") as f:
            f.write(str(self.n))
        self.git("add", "f%d.txt" % self.n)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")

    def setUp(self):
        super().setUp()
        self.n = 0
        self.init_project()
        self.git("init", "-q", "-b", "main")
        data = sl.load_ledger(self.project)
        for n in range(1, 4):
            sl.add_item(data, "item %d" % n, ["legacy"])
            sl.set_status(self.project, data, "R-%d" % n, "doing")
        sl.save_ledger(self.project, data)
        self.base = self.commit("base")

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)

    def done(self, item, sha):
        return self.cli("ledger", "done", item, "--evidence", "%s test_thing" % sha, "--outcome", "You see it work")

    def status(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)["status"]

    def side(self):
        self.git("checkout", "-q", "-b", "feature")
        sha = self.commit("side work")
        self.git("checkout", "-q", "main")
        return sha

    def test_1_a_side_branch_sha_is_refused_and_names_the_branch(self):
        sha = self.side()
        r = self.done("R-1", sha)
        self.assertNotEqual(r.returncode, 0)
        text = r.stderr + r.stdout
        self.assertIn("proof sha %s is not on main or the latest release; it is only on feature; merge it first, then close" % sha, text)
        self.assertNotIn(self.project, text)
        self.assertEqual(self.status("R-1"), "doing")

    def test_2_the_same_sha_after_merge_is_accepted(self):
        sha = self.side()
        self.git("merge", "-q", "--no-ff", "-m", "merge feature", "feature")
        r = self.done("R-1", sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status("R-1"), "done")

    def test_3_a_sha_reachable_only_from_the_latest_release_tag_is_accepted(self):
        self.git("checkout", "-q", "-b", "release-line")
        sha = self.commit("release fix")
        self.git("tag", "v1.2.0")
        self.git("checkout", "-q", "main")
        self.commit("main moves on")
        r = self.done("R-1", sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status("R-1"), "done")

    def test_4_an_unknown_sha_is_refused(self):
        r = self.done("R-1", "deadbeef")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("proof sha deadbeef is not a commit in this repo", r.stderr + r.stdout)
        self.assertEqual(self.status("R-1"), "doing")

    def trunk_repo(self):
        self.git("branch", "-m", "main", "trunk")

    def test_8_no_default_branch_and_no_tag_skips_and_says_so(self):
        self.trunk_repo()
        sha = self.commit("on trunk")
        r = self.done("R-1", sha)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("proof check skipped: no main, master or origin/HEAD in this repo", r.stdout + r.stderr)
        self.assertEqual(self.status("R-1"), "done")

    def test_9_no_default_branch_but_a_release_tag_still_checks(self):
        self.trunk_repo()
        self.git("tag", "v1.0.0")
        self.git("checkout", "-q", "-b", "feature")
        sha = self.commit("side work")
        r = self.done("R-1", sha)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not on main or the latest release", r.stderr + r.stdout)
        self.assertEqual(self.status("R-1"), "doing")

    def test_5_rule_only_evidence_closes_without_git(self):
        r = self.cli("ledger", "done", "R-1", "--evidence", "rule: rules/core.md:12 check: tests/test_x.py enforces it",
                     "--outcome", "You see it work")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status("R-1"), "done")
        sl.check_proof_reachable(os.path.join(self.tmp, "nowhere"), "deadbeef test_thing")   # not a git repo: skipped

    def test_6_batch_judges_each_line_alone(self):
        side = self.side()
        batch = os.path.join(self.project, "batch.tsv")
        with open(batch, "w") as f:
            f.write("R-1\t%s test_a\tYou see it work\nR-2\t%s test_b\tYou see it work\n" % (side, self.base))
        r = self.cli("ledger", "done", "--file", batch)
        self.assertIn("not on main or the latest release", r.stderr + r.stdout)
        self.assertEqual(self.status("R-1"), "doing")
        self.assertEqual(self.status("R-2"), "done")

    def test_7_merge_close_still_closes(self):
        self.commit("R-3: the third thing")
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        os.makedirs(vdir, exist_ok=True)
        path = os.path.join(vdir, "R-3.md")
        with open(path, "w") as f:
            f.write("PASS\nevidence\n")
        sl.write_seal(path)
        r = self.cli("merge-close", "--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.status("R-3"), ("done", "verified"))
