"""R-1157 part 1: every ledger item has a size and an area from the moment it is filed (real CLI as a subprocess)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class AreaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.tmp, "demo-shop")
        os.makedirs(self.home)
        os.makedirs(self.proj)
        self.env = dict(os.environ, HOME=self.home, BUDGIT_HOME=os.path.join(self.home, ".budgit-hub"))
        self.env.pop("CLAUDE_SESSION_ID", None)
        # --no-bind: activate binds a session by default and needs CLAUDE_CODE_SESSION_ID, which a Terminal or a
        # clean worker env lacks. This test is about the ledger, not session binding, so it asks for no binding.
        act = subprocess.run([sys.executable, "-W", "ignore", CLI, "activate", "--no-bind", self.proj],
                             capture_output=True, text=True, env=self.env, cwd=self.tmp)
        self.assertEqual(act.returncode, 0, "activate failed: " + act.stderr)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-W", "ignore", CLI, "--project", self.proj] + list(args),
                              capture_output=True, text=True, env=self.env, cwd=self.tmp)

    def add(self, *extra):
        return self.cli("ledger", "add", "--phase", "mvp", "--text", "make the cart page faster", *extra)

    def test_add_without_area_is_refused(self):
        r = self.add("--appetite", "small")
        self.assertEqual(r.returncode, 2)
        self.assertIn("R-1157: give --area WORD", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)

    def test_add_without_appetite_is_refused(self):
        r = self.add("--area", "cart")
        self.assertEqual(r.returncode, 2)
        self.assertIn("give --appetite small|medium|large", r.stderr)

    def test_add_with_both_is_accepted_and_shown(self):
        self.assertEqual(self.add("--appetite", "small", "--area", "cart").returncode, 0, self.add().stderr)
        self.assertIn("area: cart", self.cli("ledger", "show", "R-1").stdout)

    def test_bad_area_is_refused(self):
        self.assertNotEqual(self.add("--appetite", "small", "--area", "Cart Page").returncode, 0)

    def test_set_area_fills_an_old_item(self):
        self.assertEqual(self.add("--appetite", "small", "--area", "triage").returncode, 0)
        self.assertEqual(self.cli("ledger", "area", "R-1", "checkout").returncode, 0)
        self.assertIn("area: checkout", self.cli("ledger", "show", "R-1").stdout)

    def test_a_legacy_item_is_not_refused_and_has_no_area(self):
        r = self.cli("ledger", "add", "--src", "legacy", "--phase", "mvp", "--text", "an old item from a table")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("area:", self.cli("ledger", "show", "R-1").stdout)

    def test_the_denylog_caller_files_with_area_triage(self):
        import re
        with open(os.path.join(ROOT, "plugin", "scripts", "denylog.py"), encoding="utf-8") as f:
            self.assertRegex(f.read(), re.compile(r"add_item\([^\n]*area=\"triage\""))


if __name__ == "__main__":
    unittest.main()
