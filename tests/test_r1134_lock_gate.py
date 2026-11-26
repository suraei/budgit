"""R-1134: wait log, `--waits`, selective runs take no lock, commitguard accepts a lockf-less selective chain."""
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT
import commitguard
import suitewait

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")


class WaitLogTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.log = os.path.join(self.home, "suite-waits.log")

    def _run(self, name="/secret/place/proj"):
        clock = iter(range(1000, 1100, 5))
        env = {"BUDGIT_SUITE_LOCK_DIR": tempfile.mkdtemp()}
        os.chmod(env["BUDGIT_SUITE_LOCK_DIR"], 0o700)
        return suitewait.run(["x"], name, rc=True, env=env, busy_fn=lambda: (False, "free"), sleep=lambda s: None,
                             out=lambda s: None, runner=lambda c, w: 0, now=lambda: next(clock), home=self.home)

    def test_the_wait_line_is_written_with_no_path_in_it(self):
        self.assertEqual(self._run(), 0)
        lines = open(self.log).read().splitlines()
        self.assertEqual(len(lines), 1)
        p = lines[0].split()
        self.assertEqual(len(p), 4)
        self.assertEqual(p[2], "proj")
        self.assertEqual(p[3], "1")
        self.assertNotIn("/", lines[0])
        self.assertNotIn("secret", lines[0])

    def test_waits_sums_per_checkout(self):
        with open(self.log, "w") as f:
            f.write("100 160 a 0\n200 500 a 1\n300 330 b 0\nbad line\n")
        rows = suitewait.read_waits(home=self.home)
        self.assertEqual(rows["a"], [2, 360, 300])
        self.assertEqual(rows["b"], [1, 30, 30])
        text = suitewait.waits_report(home=self.home)
        self.assertIn("a  2  6.0  5.0", text)
        self.assertEqual(suitewait.read_waits(since_hours=1, home=self.home, now=lambda: 100000), {})

    def test_a_link_or_fifo_as_the_log_is_skipped_never_written_through(self):
        target = os.path.join(self.home, "target")
        open(target, "w").write("keep\n")
        os.symlink(target, self.log)
        self.assertFalse(suitewait.log_wait("p", 1, 2, False, self.home))
        self.assertEqual(open(target).read(), "keep\n")
        os.remove(self.log)
        os.mkfifo(self.log)
        self.assertFalse(suitewait.log_wait("p", 1, 2, False, self.home))
        self.assertEqual(suitewait.read_waits(home=self.home), {})


def _project():
    p = tempfile.mkdtemp()
    os.makedirs(os.path.join(p, ".budgit"))
    open(os.path.join(p, ".budgit", "ledger.json"), "w").write("{}")
    return p


class SelectiveTest(unittest.TestCase):
    def test_selective_budgit_test_takes_no_lock(self):
        lockdir = tempfile.mkdtemp()
        os.chmod(lockdir, 0o700)
        proj = _project()
        os.makedirs(os.path.join(proj, "tools"))
        with open(os.path.join(proj, "tools", "budgit_test.py"), "w") as f:
            f.write("import sys\nprint('ARGS', sys.argv[1:])\n")
        env = dict(os.environ, BUDGIT_SUITE_LOCK_DIR=lockdir)
        r = subprocess.run([sys.executable, BUDGIT, "test", "--project", proj], env=env, capture_output=True, text=True, timeout=60)
        self.assertIn("ARGS []", r.stdout, r.stderr)
        self.assertEqual(os.listdir(lockdir), [])
        # no pool entry: the old fallback, the queued full suite, after one plain line
        bare = _project()
        env2 = dict(env, PYTHONUNBUFFERED="1")
        try:
            out = subprocess.run([sys.executable, BUDGIT, "test", "--project", bare], env=env2, capture_output=True, text=True, timeout=30).stdout
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        self.assertIn("no selective map in this project: running the full suite (queued)", out)

    def test_a_gate_command_through_suite_wait_takes_no_lock(self):
        lockdir = tempfile.mkdtemp()
        os.chmod(lockdir, 0o700)
        ran = []
        suitewait.run(["python3", "-m", "unittest", "test_x"], "p", env={"BUDGIT_SUITE_LOCK_DIR": lockdir}, runner=lambda c, w: ran.append(c) or 0)
        self.assertTrue(ran)
        self.assertEqual(os.listdir(lockdir), [])


class CommitGuardTest(unittest.TestCase):
    def test_commitguard_accepts_the_lockf_less_selective_chain(self):
        cmd = ("env PYTHONPATH=/a/tests:/a/plugin/scripts python3 -W ignore -m unittest test_a test_b "
               "&& git -C /a commit -m 'R-1134: x'")
        self.assertIsNone(commitguard.decision("Bash", {"command": cmd}, "agent-1"))
        self.assertEqual(commitguard.decision("Bash", {"command": "git -C /a commit -m x"}, "agent-1")[0], "deny")


if __name__ == "__main__":
    unittest.main()
