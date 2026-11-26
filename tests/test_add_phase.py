"""`ledger add` needs --phase: no item is added without one, and the error lists the valid phases (mvp, later, a
release like 0.13.2, and the phases this ledger already uses). No silent default."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class AddPhaseTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, "ledger", "add", "--appetite", "small", "--area", "main", *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def items(self):
        return sl.load_ledger(self.project)["items"]

    def test_no_phase_adds_nothing_and_lists_the_valid_ones(self):
        self.assertEqual(self.cli("--text", "x", "--phase", "0.14").returncode, 0)
        for extra in ((), ("--phase", ""), ("--phase", "  "), ("--phase", "someday")):
            r = self.cli("--text", "new thing", *extra)
            self.assertNotEqual(r.returncode, 0, extra)
            for word in ("--phase", "mvp", "later", "0.14"):
                self.assertIn(word, r.stderr, extra)
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(len(sl.load_inbox(self.project)), 1)            # a refused add records no input either

    def test_valid_phases_are_kept(self):
        for phase in ("mvp", "later", "0.13.2", "1.0", "validate", "qa"):
            r = self.cli("--text", "item " + phase, "--phase", phase)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([i["phase"] for i in self.items()], ["mvp", "later", "0.13.2", "1.0", "validate", "qa"])
        data = sl.load_ledger(self.project)
        sl.add_item(data, "hand phase", ["legacy"], phase="beta")
        sl.save_ledger(self.project, data)
        self.assertEqual(self.cli("--text", "y", "--phase", "beta").returncode, 0)     # one already in use
