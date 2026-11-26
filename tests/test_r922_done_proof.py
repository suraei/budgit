"""R-922 part 3: `ledger done` and `ledger verify` refuse to close an item unless --evidence names a commit sha and a
test name; `ledger set <id> done|verified` is refused too. The sha is not looked up in git (part 2)."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
MSG = "done needs a commit sha and a test name in --evidence"


class DoneProofCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-proof001")
        data = sl.load_ledger(self.project)
        sl.add_item(data, "item one", ["legacy"])
        sl.save_ledger(self.project, data)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def ledger_bytes(self):
        with open(sl._ledger_path(self.project), "rb") as f:
            return f.read()

    def refused(self, *args, text=MSG):
        before = self.ledger_bytes()
        r = self.cli(*args)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(text, r.stdout + r.stderr)
        self.assertEqual(self.ledger_bytes(), before)

    def test_sha_only_is_refused(self):
        self.refused("ledger", "done", "R-1", "--evidence", "commit 1e2084f3 merged")
        self.refused("ledger", "verify", "R-1", "--evidence", "commit 1e2084f3 merged")

    def test_test_name_only_is_refused(self):
        self.refused("ledger", "done", "R-1", "--evidence", "test_something passes")
        self.refused("ledger", "done", "R-1", "--evidence", "no proof at all")

    def test_sha_and_test_name_is_accepted(self):
        self.cli("ledger", "outcome", "R-1", "You see it closed")
        r = self.cli("ledger", "done", "R-1", "--evidence", "1e2084f3 test_r922_done_proof ok")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), "R-1")["status"], "done")

    def test_other_test_name_shapes_are_accepted(self):
        for n, ev in enumerate(("abcdef1 FooTest.bar", "abcdef1234 tests/test_x.py::check_it"), 1):
            data = sl.load_ledger(self.project)
            sl.add_item(data, "more %d" % n, ["legacy"])
            sl.save_ledger(self.project, data)
            self.cli("ledger", "outcome", "R-%d" % (n + 1), "You see it closed")
            r = self.cli("ledger", "done", "R-%d" % (n + 1), "--evidence", ev)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_short_sha_is_refused(self):
        self.refused("ledger", "done", "R-1", "--evidence", "abc123 test_x")

    def test_set_done_and_verified_are_refused(self):
        self.refused("ledger", "set", "R-1", "done", text="ledger done")
        self.refused("ledger", "set", "R-1", "verified", text="ledger done")

    def test_set_other_status_unchanged(self):
        r = self.cli("ledger", "set", "R-1", "blocked")
        self.assertNotIn("ledger done", r.stdout + r.stderr)
