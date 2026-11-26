"""R-399 scope gate: a release refuses while an item of its own phase that carries the user's words is neither built nor
deferred. Replay of 0.13.5: R-399 open in phase 0.13.5, release request listing only other items."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class ScopeGateTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "built thing", ["legacy"], phase="0.13.5", item_id="R-1")
        sl.add_item(data, "team organization", ["I-707"], phase="0.13.5", item_id="R-399")
        sl.save_ledger(self.project, data)
        data = sl.load_ledger(self.project)
        sl.find_item(data, "R-1").update(status="done", evidence="test_x")
        sl.save_ledger(self.project, data)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def test_open_item_with_her_words_blocks_the_release(self):
        r = self.cli("release-check", "--items", "R-1", "--version", "0.13.5")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("R-399", r.stdout)

    def test_deferred_to_a_later_version_lets_it_pass(self):
        data = sl.load_ledger(self.project)
        sl.find_item(data, "R-399")["phase"] = "0.13.7"
        sl.save_ledger(self.project, data)
        r = self.cli("release-check", "--items", "R-1", "--version", "0.13.5")
        self.assertNotIn("R-399", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unit_gate_shapes(self):
        data = sl.load_ledger(self.project)
        self.assertEqual([i["id"] for i in sl.scope_gate(data, "0.13.5")], ["R-399"])
        sl.find_item(data, "R-399")["deferred"] = "0.13.7"
        self.assertEqual(sl.scope_gate(data, "0.13.5"), [])
        sl.find_item(data, "R-399")["deferred"] = "0.13.5"          # deferred to itself is not deferred
        self.assertEqual(len(sl.scope_gate(data, "0.13.5")), 1)
        self.assertEqual(sl.scope_gate(data, None), [])
        sl.find_item(data, "R-399")["src"] = ["legacy"]             # no words of the user: not this gate's business
        sl.find_item(data, "R-399").pop("deferred")
        self.assertEqual(sl.scope_gate(data, "0.13.5"), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
