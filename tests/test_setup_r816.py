"""R-816: setup --answer structure honours --project, budgit --version, budgit setup --pending."""
import json
import os
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT
import portfolio
import postupdate

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
S = "11111111-0000-4000-8000-000000000000"


class SetupR816(StudioTestCase):
    def run_budgit(self, *args, session=None, cwd=None):
        env = dict(os.environ, HOME=os.path.join(self.tmp, "uh"), BUDGIT_HOME=self.home, BUDGIT_LANG="en")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([sys.executable, BUDGIT, *args], capture_output=True, text=True, env=env, cwd=cwd)

    def plain_folder(self, name="plain"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        return d

    def test_project_flag_names_the_folder_for_the_structure_answer(self):
        self.init_project()
        r = self.run_budgit("setup", "--answer", "structure=yes", "--project", self.project, session=S, cwd=self.plain_folder())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], S)
        self.assertIsNone(portfolio.tm_session())
        self.assertIn(os.path.realpath(self.project), r.stdout)

    def test_project_flag_with_a_plain_folder_gives_the_lead_line(self):
        self.init_project()
        r = self.run_budgit("setup", "--answer", "structure=yes", "--project", self.plain_folder("other"), session=S, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), S)

    def test_no_flag_keeps_the_current_folder(self):
        self.init_project()
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], S)

    def test_version_prints_the_plugin_json_version(self):
        with open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            version = json.load(f)["version"]
        r = self.run_budgit("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "budgit " + version)

    def test_pending_lists_unanswered_keys_then_nothing(self):
        r = self.run_budgit("setup", "--pending")
        self.assertEqual(r.returncode, 0, r.stderr)
        listed = r.stdout.split()
        self.assertEqual(listed, postupdate.unanswered())
        self.assertTrue(listed)
        for key in listed:
            self.assertEqual(self.run_budgit("setup", "--answer", key + "=no", session=S, cwd=self.plain_folder()).returncode, 0)
        r = self.run_budgit("setup", "--pending")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
