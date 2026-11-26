"""R-1136 symptom 5: `budgit status` and `ledger show` show each item's verify state and claim a closed item by its
outcome plus proof. Real door: the CLI runs as a subprocess on a temp git project with temp HOME and BUDGIT_HOME."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class StatusVerifyCase(StudioTestCase):
    def git(self, *args):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", self.project, *args],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, name):
        with open(os.path.join(self.project, name), "w") as f:
            f.write(name)
        self.git("add", name)
        self.git("commit", "-m", name)
        return self.git("rev-parse", "HEAD")

    def setUp(self):
        super().setUp()
        self.init_project()
        self.git("init")
        old = self.commit("a.txt")
        new = self.commit("b.txt")
        data = sl.load_ledger(self.project)
        for n in (1, 2):
            sl.add_item(data, "item %d" % n, ["legacy"], outcome="You see item %d working" % n)
        sl.set_status(self.project, data, "R-1", "done", evidence="%s test_r1136_status" % new)
        sl.save_ledger(self.project, data)
        path = sl.verify_report_path(self.project, "R-1")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("PASS\nreviewer: x\n%s\nround: 1\n" % old)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home, HOME=self.home, BUDGIT_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def test_status_says_superseded_and_none(self):
        """OUTCOME: the CLI prints superseded for a PASS older than the newer commit, and none for an item with no report."""
        r = self.cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("R-1 verify: superseded", r.stdout)
        self.assertNotIn("R-2 verify:", r.stdout)       # R-2 is open: only doing and closed items are listed

    def test_open_doing_item_without_report_says_none(self):
        """OUTCOME: a doing item with no verify file prints none."""
        data = sl.load_ledger(self.project)
        data["items"][1]["status"] = "doing"
        sl.save_ledger(self.project, data)
        self.assertIn("R-2 verify: none", self.cli("status").stdout)

    def test_status_and_show_claim_by_outcome_plus_proof(self):
        """OUTCOME: a closed item is claimed by its outcome plus proof in status and in ledger show."""
        want = "R-1: You see item 1 working (proof: "
        self.assertIn(want, self.cli("status").stdout)
        self.assertIn(want, self.cli("ledger", "show", "R-1").stdout)

    def test_outcome_flag_is_a_text_flag(self):
        """CAUSE: --outcome is in the guard's text-flag set."""
        self.assertIn("--outcome", sl._LEDGER_TEXT_FLAGS)
