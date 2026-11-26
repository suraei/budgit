"""R-1136: outcome lines. add --outcome, draft from linked words, done refused without an outcome (fold and park are
not), verifier prompt rules, the renderer shows "outcome missing"."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
PROOF = "commit 1e2084f3 test_outcome_gate"


class OutcomeCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-outcome01")

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def item(self, **kw):
        data = sl.load_ledger(self.project)
        it = sl.add_item(data, "the stand-up runs every morning", ["legacy"], **kw)
        sl.save_ledger(self.project, data)
        return it["id"]

    def test_add_without_outcome_has_none(self):
        data = sl.load_ledger(self.project)
        self.assertNotIn("outcome", sl.add_item(data, "x thing", ["legacy"]))

    def test_add_with_outcome_stores_it(self):
        data = sl.load_ledger(self.project)
        it = sl.add_item(data, "x thing", ["legacy"], outcome="You see the report at 9")
        self.assertEqual(it["outcome"], "You see the report at 9")

    def test_cli_add_proposes_draft_from_linked_words(self):
        inbox = sl.append_inbox(self.project, "Quiero ver el resumen cada manana. Y mas cosas.", "s-outcome01", "manual")
        r = self.cli("ledger", "add", "--src", inbox, "--phase", "mvp", "--appetite", "small", "--area", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Quiero ver el resumen cada manana.", r.stdout)
        self.assertNotIn("outcome", sl.load_ledger(self.project)["items"][0])

    def test_done_refused_then_allowed_after_outcome(self):
        rid = self.item()
        r = self.cli("ledger", "done", rid, "--evidence", PROOF)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("budgit ledger outcome %s" % rid, r.stdout + r.stderr)
        r = self.cli("ledger", "outcome", rid, "You see the stand-up message each morning")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.cli("ledger", "done", rid, "--evidence", PROOF)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_fold_and_park_are_not_blocked(self):
        a, b = self.item(), self.item()
        r = self.cli("ledger", "phase", "later", a, "--reason", "parked for now")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.cli("ledger", "fold", b, "--into", a, "--reason", "same work")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_verifier_prompt_carries_both_rules(self):
        with open(os.path.join(ROOT, "plugin", "agents", "verifier.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("first evidence row of the report is the OUTCOME row", text)
        for tag in ("OUTCOME", "CAUSE", "UNIT-INJECTED"):
            self.assertIn(tag, text)
        self.assertIn("cannot write PASS when the only evidence for the outcome is", text)

    def test_renderer_shows_outcome_missing_and_proof(self):
        a = self.item()
        b = self.item(outcome="You see the message")
        data = sl.load_ledger(self.project)
        sl.set_outcome(data, a, "x")
        data["items"][0].pop("outcome")
        sl.set_status(self.project, data, b, "done", evidence=PROOF)
        data["items"][0]["status"] = "done"
        data["items"][0]["evidence"] = PROOF
        md = sl.render_md(data)
        self.assertIn("%s: outcome missing" % a, md)
        self.assertIn("%s: You see the message (proof: 1e2084f3, test_outcome_gate)" % b, md)

    def test_no_protected_names_in_new_text(self):
        # the protected-name scan itself is test_protected_names; here: no raw U+00A0 in the files this item touched
        for rel in (("plugin", "agents", "verifier.md"), ("tests", "test_r1136_outcome.py")):
            with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
                self.assertNotIn(chr(0xA0), f.read())
