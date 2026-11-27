"""R-1360 F43: the commit guard accepts the project's registered test_command only when the user approved that exact string at a
terminal (studiolib.test_command_approved, digest only: the standing yes does not count), and never when it is a no-op (true,
echo, :, python -c ...). The refusal names the approval step. Temp HOME and temp projects only; the approval is written through
studiolib._write_approval, never into a real home."""
import json
import os
import tempfile
import unittest
from unittest import mock

import commitguard
import studiolib

GATE = "env PYTHONPATH=.:tests /venv/bin/python -P tests/tools/run_parallel.py --fast"
COMMIT = " && git commit -m x"
NOT_CHAINED = "echo hi ; git commit -m x"          # no test before the commit: the guard refuses and shows its text


def _project(command):
    d = tempfile.mkdtemp(prefix="r1360f43-")
    os.makedirs(os.path.join(d, ".studio"))
    with open(os.path.join(d, ".studio", "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"test_command": command}, fh)
    return d


def _judge(cmd, cwd):
    return commitguard.judge("Bash", {"command": cmd}, "agent-1", cwd=cwd)


class ApprovedCommand(unittest.TestCase):
    def setUp(self):
        home = tempfile.mkdtemp(prefix="r1360f43-home-")
        patcher = mock.patch.dict(os.environ, {"BUDGIT_HOME": home, "STUDIO_HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _approved(self, command, approve=None):
        d = _project(command)
        studiolib._write_approval(d, [], approve if approve is not None else command)
        return d

    def test_an_approved_registered_command_then_commit_passes(self):
        d = self._approved(GATE)
        self.assertEqual(_judge(GATE + COMMIT, d), ("pass", None))

    def test_the_same_command_unapproved_is_refused_and_the_text_names_the_approval_step(self):
        d = _project(GATE)
        verdict = _judge(GATE + COMMIT, d)
        self.assertEqual(verdict[0], "deny")
        text = _judge(NOT_CHAINED, d)[1]
        self.assertIn(studiolib.approve_command(d), text)
        self.assertIn("not approved", text)

    def test_the_approval_of_a_different_string_is_refused(self):
        d = self._approved(GATE, approve=GATE + " --other")
        self.assertEqual(_judge(GATE + COMMIT, d)[0], "deny")
        self.assertIn(studiolib.approve_command(d), _judge(NOT_CHAINED, d)[1])

    def test_the_standing_yes_alone_does_not_count(self):
        d = _project(GATE)
        with mock.patch.object(studiolib, "trust_granted", return_value=True):
            self.assertTrue(studiolib.test_command_approved(d, GATE))          # the plain check would accept it
            self.assertEqual(_judge(GATE + COMMIT, d)[0], "deny")
            self.assertIn(studiolib.approve_command(d), _judge(NOT_CHAINED, d)[1])

    def test_an_unapproved_project_keeps_the_builtin_runners(self):
        d = _project(GATE)
        self.assertEqual(_judge("python3 -m unittest test_x" + COMMIT, d), ("pass", None))
        self.assertEqual(_judge("echo ok" + COMMIT, d)[0], "deny")

    def test_a_no_op_registered_command_is_refused_even_when_approved(self):
        for command in ("true", "echo ok", ":", "python3 -c pass", "false", "exit 0", "printf x", "test -f x", "[ -f x ]",
                        "env A=1 true", "nice -n 5 echo ok", "/usr/bin/python3 -c 'print(1)'", "python -Ic pass"):
            d = self._approved(command)
            self.assertEqual(_judge(command + COMMIT, d)[0], "deny", command)
            text = _judge(NOT_CHAINED, d)[1]
            self.assertIn("does nothing", text, command)
            self.assertNotIn("registered test command: " + command, text, command)

    def test_a_real_python_module_runner_is_not_a_no_op(self):
        d = self._approved("python3 -m unittest discover")
        self.assertEqual(_judge("python3 -m unittest discover" + COMMIT, d), ("pass", None))

    def test_a_chained_registered_command_is_refused_and_the_text_says_it_cannot_be_registered(self):
        for command in (GATE + " && echo x", GATE + " || true", GATE + " | cat", GATE + " ; true", GATE + " > out",
                        GATE + " $(rm x)", GATE + " `rm x`"):
            d = self._approved(command)
            self.assertEqual(_judge(command + COMMIT, d)[0], "deny", command)
            text = _judge(NOT_CHAINED, d)[1]
            self.assertIn("cannot be registered", text, command)
            self.assertNotIn("joined with &&", text, command)

    def test_an_operator_inside_quotes_is_not_a_chain(self):
        command = "pytest -k 'a|b'"
        d = self._approved(command)
        self.assertEqual(_judge(command + COMMIT, d), ("pass", None))

    def test_is_test_run_takes_tokens_only_for_an_approved_command(self):
        self.assertIsNone(commitguard.registered_tokens(_project(GATE)))
        self.assertTrue(commitguard.is_test_run(GATE, commitguard.registered_tokens(self._approved(GATE))))
        self.assertIsNone(commitguard.registered_tokens(self._approved("true")))


if __name__ == "__main__":
    unittest.main()
