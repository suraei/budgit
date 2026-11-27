"""R-1360 F41: the commit guard accepts a chained segment that equals the project's registered test_command (after the wrappers
is_test_run already strips), and its no-command refusal names the data folder the project really uses. Temp projects only."""
import json
import os
import tempfile
import unittest
from unittest import mock

import commitguard
import studiolib

GATE = "env PYTHONPATH=.:tests /venv/bin/python -P tests/tools/run_parallel.py --fast"
COMMIT = " && git commit -m x"


def _project(command=None, folder=".studio", raw=None):
    d = tempfile.mkdtemp(prefix="r1360f41-")
    os.makedirs(os.path.join(d, folder))
    if folder == ".budgit":
        with open(os.path.join(d, folder, "ledger.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")                      # a valid migrated data folder
    if raw is not None:
        with open(os.path.join(d, folder, "project.json"), "w", encoding="utf-8") as fh:
            fh.write(raw)
    elif command is not None:
        with open(os.path.join(d, folder, "project.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_command": command}, fh)
        if isinstance(command, str) and command.strip():
            studiolib._write_approval(d, [], command)     # F43: a registered command counts only once approved (temp HOME, see setUp)
    return d


def _judge(cmd, cwd):
    return commitguard.judge("Bash", {"command": cmd}, "agent-1", cwd=cwd)


class RegisteredCommand(unittest.TestCase):
    def setUp(self):
        home = tempfile.mkdtemp(prefix="r1360f41-home-")
        patcher = mock.patch.dict(os.environ, {"BUDGIT_HOME": home, "STUDIO_HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_registered_command_then_commit_is_allowed(self):
        for folder in (".studio", ".budgit"):
            d = _project(GATE, folder)
            self.assertEqual(_judge(GATE + COMMIT, d), ("pass", None), folder)

    def test_extra_trailing_command_in_the_segment_is_denied(self):
        d = _project(GATE)
        for cmd in (GATE + " ; rm x" + COMMIT,
                    GATE + " extra" + COMMIT,
                    GATE + " ; git commit -m x",
                    GATE + " || git commit -m x",
                    GATE + " | cat" + COMMIT,
                    GATE + " $(rm x)" + COMMIT):
            self.assertEqual(_judge(cmd, d)[0], "deny", cmd)

    def test_an_unregistered_runner_is_denied(self):
        d = _project("make verify-all")
        self.assertEqual(_judge(GATE + COMMIT, d)[0], "deny")
        d = _project()
        self.assertEqual(_judge(GATE + COMMIT, d)[0], "deny")
        other = "env PYTHONPATH=.:tests /venv/bin/python -P tests/tools/run_parallel.py --slow"
        self.assertEqual(_judge(other + COMMIT, _project(GATE))[0], "deny")

    def test_wrapper_variations_are_allowed(self):
        d = _project(GATE)
        bare = "/venv/bin/python -P tests/tools/run_parallel.py --fast"
        for cmd in (bare,
                    "PYTHONPATH=x " + bare,
                    "env A=1 B=2 " + bare,
                    "nice -n 5 " + GATE,
                    "timeout 600 " + bare,
                    "lockf -k /tmp/lock " + bare,
                    GATE):
            self.assertEqual(_judge(cmd + COMMIT, d), ("pass", None), cmd)

    def test_a_wrapper_with_command_substitution_is_denied(self):
        d = _project(GATE)
        self.assertEqual(_judge("A=$(rm x) /venv/bin/python -P tests/tools/run_parallel.py --fast" + COMMIT, d)[0], "deny")

    def test_a_migrated_project_refusal_names_budgit_project_json(self):
        d = _project(folder=".budgit")
        text = _judge("echo hi ; git commit -m x", d)[1]
        self.assertIn(".budgit/project.json", text)
        self.assertNotIn(".studio/project.json", text)

    def test_an_old_project_refusal_still_names_studio_project_json(self):
        d = _project(folder=".studio")
        text = _judge("echo hi ; git commit -m x", d)[1]
        self.assertIn(".studio/project.json", text)
        self.assertNotIn(".budgit/project.json", text)

    def test_no_test_command_changes_nothing(self):
        for d in (_project(), _project(raw='{"test_command": ""}'), _project(raw='{"test_command": 5}'),
                  _project(raw='{"test_command": ["a"]}'), _project(raw='{"test_command": "   "}'), _project(raw="not json")):
            self.assertEqual(_judge(GATE + COMMIT, d)[0], "deny")
            self.assertEqual(_judge("echo hi ; git commit -m x", d)[0], "deny")
            self.assertEqual(_judge("python3 -m unittest test_x" + COMMIT, d), ("pass", None))
            self.assertEqual(_judge("echo ok" + COMMIT, d)[0], "deny")
            self.assertEqual(_judge("git commit -m x", d)[0], "deny")

    def test_a_registered_command_that_is_only_wrappers_matches_nothing(self):
        d = _project("env A=1")
        self.assertEqual(_judge("env A=1 && git commit -m x", d)[0], "deny")
        self.assertEqual(_judge("echo ok" + COMMIT, d)[0], "deny")

    def test_is_test_run_without_a_registered_command_is_unchanged(self):
        self.assertFalse(commitguard.is_test_run(GATE))
        self.assertTrue(commitguard.is_test_run("python3 -m unittest x"))
        self.assertTrue(commitguard.is_test_run(GATE, commitguard.registered_tokens(_project(GATE))))


if __name__ == "__main__":
    unittest.main()
