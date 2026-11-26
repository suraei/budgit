"""R-1149: a test run imports and executes only its own checkout's code, whatever the current folder is."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import safepath  # noqa: E402
import gate_test  # noqa: E402
import budgit_test  # noqa: E402
import suitewait  # noqa: E402

PROBE = (
    "import unittest, studiolib\n"
    "class P(unittest.TestCase):\n"
    "    def test_file(self):\n"
    "        print('IMPORTED=' + studiolib.__file__)\n"
)


class SafePathTest(unittest.TestCase):
    def test_worktree_module_is_imported_when_run_starts_from_another_folder(self):
        # A same-named module sits in the folder the run starts from; the gate must still import this checkout's.
        with tempfile.TemporaryDirectory() as other:
            with open(os.path.join(other, "studiolib.py"), "w") as f:
                f.write("MARK = 'OTHER'\n")
            with open(os.path.join(other, "test_probe_r1149.py"), "w") as f:
                f.write(PROBE)
            plain = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "test_probe_r1149"], cwd=other,
                                   capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=""))
            self.assertIn(other, plain.stdout)          # the leak is real for a bare -m unittest from there
            env = dict(os.environ, PYTHONPATH=other)
            code = ("import sys; sys.path.insert(0, %r); import gate_test, os; os.chdir(%r); "
                    "sys.path[:] = gate_test.prepare(%r, [%r, ''] + sys.path); import studiolib; print('IMPORTED=' + studiolib.__file__)"
                    % (os.path.join(ROOT, "tools"), other, ROOT, other))
            got = subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=other, capture_output=True,
                                 text=True, env=env)
            self.assertIn(os.path.join(ROOT, "plugin", "scripts", "studiolib.py"), got.stdout, got.stderr)

    def test_prepare_changes_into_its_root_and_puts_own_dirs_first(self):
        old = os.getcwd()
        try:
            path = gate_test.prepare(ROOT, ["", "/x", "."])
            self.assertEqual(os.path.realpath(os.getcwd()), os.path.realpath(ROOT))
            self.assertEqual(path[:2], safepath.own_dirs(ROOT))
            self.assertNotIn("", path)
        finally:
            os.chdir(old)

    def test_budgit_test_runner_passes_its_root_as_cwd(self):
        with mock.patch.object(budgit_test, "check", lambda r, c: (None, None)), \
                mock.patch.object(budgit_test.subprocess, "call", return_value=0) as call:
            budgit_test.main(["--files", "x"], count=lambda *a: 0, lock=False)
        self.assertEqual(call.call_args.kwargs.get("cwd"), budgit_test.ROOT)

    def test_suitewait_hands_the_project_cwd_to_the_runner(self):
        seen = []
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, XDG_STATE_HOME=home)
            suitewait.run(["x"], "proj", env=env, busy_fn=lambda: (False, "free"), sleep=lambda s: None,
                          out=lambda s: None, cwd="/some/project", runner=lambda c, d: seen.append(d) or 0)
        self.assertEqual(seen, ["/some/project"])

    def test_the_budgit_cli_passes_the_project_as_cwd_to_the_suite_runner(self):
        with open(os.path.join(ROOT, "plugin", "bin", "budgit")) as f:
            self.assertIn("cwd=project", f.read())

    def test_dash_p_only_on_python_311_or_newer(self):
        self.assertEqual(safepath.flags((3, 9, 6)), [])
        self.assertEqual(safepath.flags((3, 10, 14)), [])
        self.assertEqual(safepath.flags((3, 11, 0)), ["-P"])
        self.assertEqual(safepath.flags((3, 15, 0)), ["-P"])
        self.assertEqual(safepath.flags(), ["-P"] if sys.version_info >= (3, 11) else [])

    def test_own_pythonpath_puts_tests_and_scripts_first(self):
        parts = safepath.own_pythonpath(ROOT, "/else").split(os.pathsep)
        self.assertEqual(parts, safepath.own_dirs(ROOT) + ["/else"])


def _main_checkout():
    """The main checkout, from the git common dir (file reads through the gate's own helper), or None."""
    common = gate_test.relocate.common_git_dir(ROOT)
    return os.path.dirname(os.path.realpath(common)) if common else None


MAIN_CHECKOUT = _main_checkout()
SEVEN_STEPS = ("with_visible_checks", "with_pin_checks", "with_cli_table_check", "with_guard_checks",
               "with_slow_tier", "with_landing_set", "with_full_suite")
# The child runs the real script file from the main checkout with all seven escalation steps patched out (mock.patch.object
# on the script's own namespace via mock.patch.dict), so nothing in gate_test changes and no full suite nests. run_name is not "__main__" because
# that would run the entry block before the patches are in place; main() is then called the way the entry block calls it.
CHILD = (
    "import runpy, sys\n"
    "from unittest import mock\n"
    "ns = runpy.run_path(%(script)r, run_name='gate_test_entry')\n"
    "fake = lambda mods, *a: (mods, '')\n"
    "sys.argv = ['gate_test.py'] + %(mods)r\n"
    "with mock.patch.dict(ns['main'].__globals__, {n: fake for n in %(steps)r}):\n"
    "    sys.exit(ns['main'](sys.argv[1:]))\n"
)


def run_main_checkout_child():
    code = CHILD % {"script": os.path.join(ROOT, "tools", "gate_test.py"), "steps": SEVEN_STEPS,
                    "mods": ["test_guard_names_cwd", "test_r1054_ext_review_cwd"]}
    return subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=MAIN_CHECKOUT, capture_output=True,
                          text=True, timeout=300)


class GateThroughRealHookTest(unittest.TestCase):
    def hook(self, command):
        import json
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, STUDIO_HOME=os.path.join(home, "s"), BUDGIT_LANG="en")
            for k in ("BUDGIT_HOME", "CLAUDE_CODE_SESSION_ID"):
                env.pop(k, None)
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                       "agent_id": "a1", "agent_type": "studio:builder", "session_id": "s1", "cwd": ROOT}
            r = subprocess.run([sys.executable, os.path.join(ROOT, "plugin", "scripts", "hook.py"), "pre-tool-use"],
                               input=json.dumps(payload), env=env, capture_output=True, text=True, timeout=60)
        return r

    def test_gate_test_then_commit_is_allowed_by_the_commit_chain_guard(self):
        cmd = ("python3 -W ignore %s/tools/gate_test.py test_x && git -C %s commit -m \"x\"" % (ROOT, ROOT))
        r = self.hook(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("commit only chained", r.stdout + r.stderr)
        self.assertNotIn('"deny"', r.stdout)

    def test_a_bare_commit_is_still_refused_by_the_same_hook(self):
        r = self.hook("git -C %s commit -m \"x\"" % ROOT)
        self.assertIn("deny", r.stdout)


@unittest.skipUnless(MAIN_CHECKOUT and os.path.isdir(MAIN_CHECKOUT) and os.path.realpath(MAIN_CHECKOUT) != os.path.realpath(ROOT),
                     "no separate main checkout found through the git common dir: the real gate_test script started from the "
                     "main checkout was NOT tested (cwd and sys.path proof skipped)")
class CwdReadingTestsFromMainFolderTest(unittest.TestCase):
    def test_cwd_reading_tests_are_green_through_gate_test_started_in_the_main_checkout(self):
        r = run_main_checkout_child()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
