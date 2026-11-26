"""R-1176 (R-855): a Bash call that runs the WHOLE suite serially gets one advice line naming the pool runner; never a refusal.

Replayed through the real hook (hook.py in a subprocess) with the temp BUDGIT_HOME and temp HOME of StudioTestCase/GuardCase.
Before: 3140 s serial for 5948 tests (ledger R-1176); the pool runner takes about 645 s.
"""
import os
import tempfile
import unittest

import studiolib as sl
import test_guard_trust as tg

POOL = "python3 tools/budgit_test.py --full"
SERIAL = "python3 -m unittest discover -s tests"


class SerialFullSuiteFunctionTest(unittest.TestCase):
    def test_the_serial_forms_are_full(self):
        for cmd in (SERIAL, "python3 -m unittest discover", "python3 -m unittest discover -v -s tests",
                    "python3 -m unittest discover -s tests -p 'test*.py'", "python3 -m unittest discover -s tests -p 'test_*.py'",
                    "cd /tmp/repo && python3 -m unittest discover -s tests",
                    "env PYTHONPATH=plugin/scripts python3 -W ignore -m unittest discover -s tests -v",
                    "lockf -k /tmp/suite.lock python3 -m unittest discover -s tests", "python3 -m unittest"):
            self.assertTrue(sl.serial_full_suite(cmd), cmd)

    def test_selective_gate_and_pool_forms_are_not(self):
        for cmd in ("python3 -m unittest test_r1176_serial_warn", "python3 -m unittest tests.test_a tests.test_b",
                    "python3 -m unittest discover -s tests -p 'test_r1176_serial_warn.py'",
                    "python3 -m unittest discover -s tests -k serial", "python3 -m unittest discover -s tools/brote/float/tests",
                    "python3 -I -W ignore /work/repo/tools/gate_test.py test_a test_b",
                    POOL, "python3 tools/budgit_test.py", "echo python3 -m unittest discover"):
            self.assertFalse(sl.serial_full_suite(cmd), cmd)


def _with_runner(folder):
    os.makedirs(os.path.join(folder, "tools"), exist_ok=True)
    with open(os.path.join(folder, "tools", "budgit_test.py"), "w") as f:
        f.write("# pool runner stand-in\n")


class SerialSuiteHookTest(tg.HookCase2):
    def setUp(self):
        super().setUp()
        _with_runner(self.project)               # the project cwd of these rows has the pool runner

    def reply(self, cmd, **extra):
        out = self.fire("pre-tool-use", dict(hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": cmd}), **extra)
        return out.get("hookSpecificOutput") or {}

    def warned(self, cmd, **extra):
        return "tools/budgit_test.py --full" in self.reply(cmd, **extra).get("additionalContext", "")

    def test_f1_a_cwd_with_the_pool_runner_warns(self):
        with tempfile.TemporaryDirectory() as d:
            _with_runner(d)
            self.assertTrue(self.warned(SERIAL, cwd=d))

    def test_f1_b_a_cwd_without_the_pool_runner_gives_no_advice(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(self.warned(SERIAL, cwd=d))

    def test_f1_c_a_cd_to_a_folder_without_the_runner_gives_no_advice(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(self.warned("cd %s && %s" % (d, SERIAL)))
            _with_runner(d)
            self.assertTrue(self.warned("cd %s && %s" % (d, SERIAL), cwd=self.tmp))

    def test_the_serial_full_discover_warns_and_names_the_pool_runner(self):
        self.assertTrue(self.warned(SERIAL))

    def test_the_same_with_v_with_p_and_with_a_cd_prefix_warns(self):
        self.assertTrue(self.warned("python3 -m unittest discover -v -s tests"))
        self.assertTrue(self.warned("python3 -m unittest discover -s tests -p 'test*.py'"))
        self.assertTrue(self.warned("cd %s && %s" % (self.project, SERIAL)))

    def test_one_module_the_gate_and_the_pool_runner_do_not_warn(self):
        self.assertFalse(self.warned("python3 -m unittest test_r1176_serial_warn"))
        self.assertFalse(self.warned("python3 -I -W ignore /work/repo/tools/gate_test.py test_a"))
        self.assertFalse(self.warned("python3 -m unittest discover -s tools/brote/float/tests"))
        self.assertFalse(self.warned(POOL))

    def test_the_warning_never_turns_the_call_into_a_refusal(self):
        warned, plain = self.reply(SERIAL), self.reply("python3 -m unittest test_r1176_serial_warn")
        self.assertIn("tools/budgit_test.py --full", warned.get("additionalContext", ""))
        self.assertEqual(warned.get("permissionDecision"), plain.get("permissionDecision"))
        self.assertNotIn(self.bash(SERIAL), ("deny", "ask"))


if __name__ == "__main__":
    unittest.main()
