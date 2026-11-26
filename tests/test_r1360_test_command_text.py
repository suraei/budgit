"""R-1360 C3: the commit guard's refusal names the project's own registered test command, never Budgit's runner outside the Budgit
checkout; the allow/deny decision is the same in all three cases."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import commitguard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNCHAINED = {"command": "echo hi ; git commit -m x"}
CHAINED = {"command": "python3 -m unittest test_x && git commit -m x"}
PLAIN = {"command": "git commit -m x"}


def _project(command=None):
    d = tempfile.mkdtemp(prefix="r1360c3-")
    os.makedirs(os.path.join(d, ".studio"))
    if command:
        with open(os.path.join(d, ".studio", "project.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_command": command}, fh)
    return d


class TestCommandText(unittest.TestCase):
    def judge(self, tool_input, cwd):
        return commitguard.judge("Bash", tool_input, "agent-1", cwd=cwd)

    def test_project_with_its_own_command_gets_it_in_the_refusal(self):
        d = _project("make verify-all")
        for text in (self.judge(UNCHAINED, d)[1], commitguard.refusal_text(d, commitguard.FIX_LINE)):
            self.assertIn("make verify-all", text[:200])
            self.assertNotIn("runtests", text)
            self.assertNotIn("gate_test", text)

    def test_project_without_a_command_gets_the_no_command_text(self):
        d = _project()
        text = self.judge(UNCHAINED, d)[1]
        self.assertIn("no registered test command", text[:200])
        self.assertIn("test_command", text[:200])
        self.assertNotIn("check:", text)
        self.assertNotIn("runtests", text)
        self.assertNotIn("gate_test", text)

    def test_receipt_path_in_a_git_project_uses_the_project_text(self):
        d = _project("npm run verify")
        subprocess.run(["git", "init", "-q", d], check=True, capture_output=True)
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": tempfile.mkdtemp(prefix="r1360c3-home-")}):
            verdict = self.judge(PLAIN, d)
        self.assertEqual(verdict[0], "deny")
        self.assertIn("npm run verify", verdict[1][:200])
        self.assertNotIn("runtests", verdict[1])

    def test_plugin_checkout_gets_its_runner(self):
        self.assertEqual(self.judge(UNCHAINED, ROOT), ("deny", commitguard.REASON))
        self.assertEqual(commitguard.refusal_text(ROOT, commitguard.FIX_LINE), commitguard.FIX_LINE)
        self.assertIn("tools/runtests.py", commitguard.FIX_LINE)

    def test_decision_is_unchanged_in_all_three(self):
        for d in (_project("make verify-all"), _project(), ROOT):
            self.assertEqual(self.judge(UNCHAINED, d)[0], "deny")
            self.assertEqual(self.judge(CHAINED, d), ("pass", None))
            self.assertIsNone(commitguard.judge("Bash", UNCHAINED, None, cwd=d))     # the coordinator is never affected


if __name__ == "__main__":
    unittest.main()
