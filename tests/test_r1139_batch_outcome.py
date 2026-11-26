"""R-1139: a batch `ledger done --file` takes an optional third column, id<TAB>evidence<TAB>outcome. The column is the
outcome for that id (the one-step close); a line without one on an item with no outcome is refused alone with the
one-step form, and the other lines still close. The text of a line (evidence or outcome) is never echoed. Real door: the
CLI in a subprocess, with a temp HOME and BUDGIT_HOME."""
import os
import subprocess
import sys
import tempfile

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
EVID_1 = "abcdef1 test_LINEONE_EVIDENCE"
EVID_2 = "abcdef2 test_LINETWO_EVIDENCE"
EVID_3 = "abcdef3 test_LINETHREE_EVIDENCE"
OUTCOME_1 = "You see LINEONE_OUTCOME at nine"
HAD_OUTCOME = "You already see the old line"


class BatchOutcomeColumn(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-batchout01")
        self.cli_home = tempfile.mkdtemp(prefix="r1139-home-")
        self.budgit_home = tempfile.mkdtemp(prefix="r1139-bh-")
        data = sl.load_ledger(self.project)
        for text in ("one, no outcome", "two, no outcome"):
            sl.add_item(data, text, ["legacy"])                       # an old item: no outcome line
        sl.add_item(data, "three, has an outcome", ["legacy"], outcome=HAD_OUTCOME)
        sl.save_ledger(self.project, data)

    def cli(self, *args):
        env = dict(os.environ, HOME=self.cli_home, BUDGIT_HOME=self.budgit_home, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def batch_file(self):
        path = os.path.join(self.project, "batch.tsv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("R-1\t%s\t%s\n" % (EVID_1, OUTCOME_1))
            f.write("R-2\t%s\n" % EVID_2)
            f.write("R-3\t%s\n" % EVID_3)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_third_column_closes_its_line_and_a_line_without_one_is_refused_alone(self):
        r = self.cli("ledger", "done", "--file", self.batch_file())
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("R-1: ok, done", r.stdout)
        self.assertIn("R-2: refused", r.stdout)
        self.assertIn("R-3: ok, done", r.stdout)
        self.assertIn("2 ok, 1 refused", r.stdout)
        self.assertIn("ledger done R-2 --outcome", r.stdout)           # the one-step form
        one, two, three = self.item("R-1"), self.item("R-2"), self.item("R-3")
        self.assertEqual((one["status"], one["outcome"]), ("done", OUTCOME_1))
        self.assertIn("LINEONE_EVIDENCE", one["evidence"])
        self.assertEqual(two["status"], "open")
        self.assertNotIn("outcome", two)
        self.assertEqual((three["status"], three["outcome"]), ("done", HAD_OUTCOME))
        for secret in ("LINEONE_OUTCOME", "LINEONE_EVIDENCE", "LINETWO_EVIDENCE", "LINETHREE_EVIDENCE"):
            self.assertNotIn(secret, out)                              # no line's text is echoed

    def test_the_single_id_one_step_form_still_works(self):
        r = self.cli("ledger", "done", "R-2", "--outcome", "You see two at ten", "--evidence", EVID_2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.item("R-2")["status"], self.item("R-2")["outcome"]), ("done", "You see two at ten"))

    def test_outcome_flag_with_several_ids_is_refused_and_changes_nothing(self):
        r = self.cli("ledger", "done", "R-1", "R-2", "--outcome", "one line", "--evidence", EVID_1)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.item("R-1")["status"], self.item("R-2")["status"]), ("open", "open"))
