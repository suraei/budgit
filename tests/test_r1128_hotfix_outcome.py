"""R-1128 hotfix 1: a friend's old ledger has items with no outcome line. `done --outcome` closes one in a single step
(real door: the CLI), the refusal names that one-step form, and an internal closer passes a derived outcome and is
never refused (and never replaces a line the user wrote)."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
PROOF = "commit 1e2084f3 test_old_ledger_one_step"


class OldLedgerCloses(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-oldledger1")

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def old_item(self):
        data = sl.load_ledger(self.project)
        it = sl.add_item(data, "the stand-up runs every morning", ["legacy"])     # no outcome line, as in an old ledger
        sl.save_ledger(self.project, data)
        self.assertNotIn("outcome", it)
        return it["id"]

    def test_old_item_closes_in_one_step_with_done_outcome(self):
        rid = self.old_item()
        r = self.cli("ledger", "done", rid, "--outcome", "You see the stand-up message each morning", "--evidence", PROOF)
        self.assertEqual(r.returncode, 0, r.stderr)
        item = sl.find_item(sl.load_ledger(self.project), rid)
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["outcome"], "You see the stand-up message each morning")

    def test_refusal_names_the_one_step_form(self):
        rid = self.old_item()
        r = self.cli("ledger", "done", rid, "--evidence", PROOF)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ledger done %s --outcome" % rid, r.stdout + r.stderr)
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), rid)["status"], "open")

    def test_internal_closer_is_never_refused_and_keeps_the_users_line(self):
        rid = self.old_item()
        data = sl.load_ledger(self.project)
        sl.set_status(self.project, data, rid, "done", evidence=PROOF, derived_outcome="closed by merge abc123")
        self.assertEqual(sl.find_item(data, rid)["outcome"], "closed by merge abc123")
        other = self.old_item()
        data = sl.load_ledger(self.project)
        sl.set_outcome(data, other, "You see it at nine")
        sl.set_status(self.project, data, other, "done", evidence=PROOF, derived_outcome="closed by merge abc123")
        self.assertEqual(sl.find_item(data, other)["outcome"], "You see it at nine")
