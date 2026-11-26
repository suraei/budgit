"""R-1282: after a red run the runner keeps each failed module's worker log in the checkout, writes the launch order, prints the first
failure block of each failed module, and starts every worker from an allowlist of the caller's environment (no suite may depend on
who launched it). Fixtures come from the real writers: the real run_pool on two tiny fixture modules, the real worker_env."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtests as rt  # noqa: E402

FAIL = "fixtures.logs_fail"
LONG = "fixtures.logs_fail_long"
PASS = "fixtures.logs_pass"
STAMP = re.compile(r"^\d{8}-\d{6}-\d{6}$")


def run_pool(mods, logs_root, workers=2):
    """-> (exit code, printed text); the real pool, the times file and the run record in scratch folders."""
    shown = io.StringIO()
    with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as out, \
            mock.patch.object(rt, "record_times"), contextlib.redirect_stdout(shown):
        rc = rt.run_pool(rt.discover(mods), workers, workers, False, base, [], None, "gate", (),
                         times_out=os.path.join(out, "times.json"), failed_logs=logs_root)
    return rc, shown.getvalue()


def folders(root):
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))) if os.path.isdir(root) else []


class FailedLogsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "failed-logs")

    def red_run(self, mods=(FAIL, PASS)):
        rc, text = run_pool(list(mods), self.root)
        self.assertEqual(rc, 1, text)
        found = [d for d in folders(self.root) if STAMP.match(d)]
        self.assertTrue(found, text)
        return os.path.join(self.root, max(found)), text        # the newest stamp: the run just made

    def test_default_folder_is_in_the_checkout(self):
        self.assertEqual(rt.FAILED_LOGS, os.path.join(rt.ROOT, ".budgit", "failed-logs"))

    def test_failed_module_log_kept_in_checkout(self):
        folder, text = self.red_run()
        self.assertRegex(os.path.basename(folder), STAMP)
        with open(os.path.join(folder, FAIL + ".log")) as f:
            log = f.read()
        self.assertIn("fixture failure marker", log)
        self.assertFalse(os.path.exists(os.path.join(folder, PASS + ".log")), "a passing module keeps no log")
        self.assertIn(folder, text.splitlines()[-1], "the last line names the folder")

    def test_green_run_writes_no_failed_logs(self):
        rc, text = run_pool([PASS], self.root)
        self.assertEqual(rc, 0, text)
        self.assertEqual(folders(self.root), [])
        self.assertFalse(os.path.exists(self.root))
        m = re.search(r"^(\d+) variables from the caller were left out of the tests$", text, re.M)
        self.assertTrue(m, text)
        self.assertEqual(int(m.group(1)), len(rt.dropped_env_names()))

    def test_order_file_lists_every_launched_module(self):
        folder, _ = self.red_run()
        with open(os.path.join(folder, "order.json")) as f:
            rows = json.load(f)
        by = {r["module"]: r for r in rows}
        self.assertEqual(sorted(by), sorted([FAIL, PASS]))
        self.assertEqual(sorted(r["launch"] for r in rows), [0, 1])
        for r in rows:
            self.assertLessEqual(r["start"], r["end"])
        self.assertEqual((by[FAIL]["result"], by[FAIL]["exit"]), ("fail", 1))
        self.assertEqual((by[PASS]["result"], by[PASS]["exit"]), ("ok", 0))

    def test_env_names_written_without_values(self):
        secret = "hunter2-fake-value-not-for-files"
        with mock.patch.dict(os.environ, {"CLAUDE_FAKE_X": secret, "BUDGIT_FAKE_X": secret}):
            folder, text = self.red_run()
        with open(os.path.join(folder, "env-names.txt")) as f:
            names = f.read().split()
        self.assertIn("CLAUDE_FAKE_X", names)
        self.assertIn("BUDGIT_FAKE_X", names)
        self.assertNotIn("PATH", names)                      # kept, so not dropped
        self.assertNotIn("HOME", names)                      # replaced by the runner's own, so not dropped
        for d, _, fs in os.walk(folder):
            for name in fs:
                with open(os.path.join(d, name), errors="replace") as f:
                    self.assertNotIn(secret, f.read(), name)
        self.assertNotIn(secret, text)

    def test_failure_block_printed_and_capped(self):
        folder, text = self.red_run((LONG, FAIL, PASS))
        lines = text.splitlines()
        start = [i for i, ln in enumerate(lines) if ln.startswith("FAIL: test_long_failure")]
        self.assertTrue(start, text)
        self.assertIn("fixture failure marker", text)
        block = lines[start[-1]:]
        shown = [ln for ln in block if ln.startswith("long failure line")]
        self.assertTrue(0 < len(shown) < 40, len(shown))
        with open(os.path.join(folder, LONG + ".log")) as f:
            log = f.read()
        report = rt.failure_blocks([(LONG, log)])
        self.assertLessEqual(len(report), 40)
        many = rt.failure_blocks([("m%d" % i, log) for i in range(9)])
        self.assertLessEqual(len(many), 200)
        self.assertGreater(len(many), 160)                    # the cap is on the total, each module still gets its share

    def test_only_five_newest_run_folders_kept(self):
        old = ["20200101-00000%d-000001" % i for i in range(1, 8)]
        for name in old + ["keep-me-by-hand", "2020-not-a-stamp"]:
            os.makedirs(os.path.join(self.root, name))
        folder, _ = self.red_run()
        left = folders(self.root)
        self.assertEqual(sorted(d for d in left if STAMP.match(d)), sorted(old[-4:] + [os.path.basename(folder)]))
        self.assertIn("keep-me-by-hand", left)
        self.assertIn("2020-not-a-stamp", left)


class WorkerEnvTest(unittest.TestCase):
    def worker_env(self, rc=False):
        with tempfile.TemporaryDirectory() as base:
            return rt.worker_env(base, 0, rc)[0]

    def test_build_toolchain_names_match_the_build_cache_key(self):
        import brotecache
        self.assertEqual(set(brotecache.KEY_ENV) - set(rt.ENV_KEEP), set(), "a build-cache key variable the workers would not get")
        for name in brotecache.KEY_ENV:
            with mock.patch.dict(os.environ, {name: "/caller/value"}):
                self.assertEqual(self.worker_env().get(name), "/caller/value", name)

    def test_worker_env_drops_caller_variables(self):
        extra = {"CLAUDE_X": "1", "BUDGIT_X": "1", "CLAUDE_CODE_EXECPATH": "/x", "PYTHONDONTWRITEBYTECODE": "1",
                 "BUDGIT_SUITE_LOCK": "/x/lock", "SSH_AUTH_SOCK": "/x", "SOME_TOKEN": "t"}
        with mock.patch.dict(os.environ, extra):
            env = self.worker_env()
        for name in extra:
            self.assertFalse(name in env, name + " reached the worker")      # names only: a failing assertNotIn would print the whole environment

    def test_worker_env_keeps_allowlist_and_runner_variables(self):
        keep = {"PATH": "/p/bin", "LANG": "xx_XX.UTF-8", "LC_ALL": "xx_XX.UTF-8", "LC_CTYPE": "xx", "TERM": "dumb", "USER": "nobody",
                "LOGNAME": "nobody", "SHELL": "/bin/sh", "BUDGIT_TEST_REAL_HOME": "/real/home"}
        with mock.patch.dict(os.environ, keep):
            env = self.worker_env(rc=True)
        for name, value in keep.items():
            self.assertEqual(env.get(name), value, name)
        for name in ("TMPDIR", "HOME", "BUDGIT_HOME", "BUDGIT_LANG", "BUDGIT_ADMIT_ADVICE", "BUDGIT_HOUSEKEEPING_LAUNCHCTL",
                     "PYTHONHASHSEED", "PYTHONPYCACHEPREFIX", "PYTHONPATH", "BUDGIT_TEST_RC"):
            self.assertIn(name, env, name)
        self.assertEqual((env["BUDGIT_LANG"], env["BUDGIT_ADMIT_ADVICE"], env["BUDGIT_TEST_RC"]), ("es", "off", "1"))
        with mock.patch.dict(os.environ, {"HOME": "/caller/home"}):
            os.environ.pop("BUDGIT_TEST_REAL_HOME", None)
            self.assertEqual(self.worker_env()["BUDGIT_TEST_REAL_HOME"], "/caller/home")     # read as today: the caller's real home
        self.assertFalse("BUDGIT_TEST_RC" in self.worker_env(rc=False))


if __name__ == "__main__":
    unittest.main()
