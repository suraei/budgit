"""R-882: the pool gives every worker ONE shared BUDGIT_TEST_CACHE, a HOME-keyed fixture gives equal verdicts serial
and pooled, and a static scan lists the modules that read HOME."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import pooleq  # noqa: E402
import runtests as rt  # noqa: E402

FIXTURE = "fixtures.homecache_probe"


class SharedCacheTest(unittest.TestCase):
    def test_a_pool_workers_env_carries_no_scratch_test_cache(self):
        with tempfile.TemporaryDirectory() as base, mock.patch.dict(os.environ, {"HOME": "/real/home"}):
            os.environ.pop("BUDGIT_TEST_CACHE", None)
            os.environ.pop("BUDGIT_TEST_REAL_HOME", None)
            envs = [rt.worker_env(base, i)[0] for i in range(3)]
            for e in envs:
                self.assertNotIn("BUDGIT_TEST_CACHE", e)
                self.assertEqual(e["BUDGIT_TEST_REAL_HOME"], "/real/home")
            self.assertEqual(len({e["HOME"] for e in envs}), 3)           # while HOME stays per worker
            self.assertFalse(os.path.exists(os.path.join(base, "cache")))

    def test_a_cache_the_caller_set_is_kept(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as mine, mock.patch.dict(os.environ):
            os.environ["BUDGIT_TEST_CACHE"] = mine
            envs = [rt.worker_env(base, i)[0] for i in range(2)]
            self.assertEqual({e["BUDGIT_TEST_CACHE"] for e in envs}, {mine})

    def test_the_rc_flag_is_set_for_a_full_run_or_a_recording_and_not_for_a_selective_run(self):
        with tempfile.TemporaryDirectory() as base, mock.patch.dict(os.environ):
            os.environ.pop("BUDGIT_TEST_RC", None)
            self.assertNotIn("BUDGIT_TEST_RC", rt.worker_env(base, 0)[0])
            self.assertEqual(rt.worker_env(base, 1, True)[0]["BUDGIT_TEST_RC"], "1")

    def test_run_pool_passes_rc_only_for_a_full_or_recorded_run(self):
        seen = []
        real = rt.worker_env

        def spy(base, i, rc=False):
            seen.append(rc)
            return real(base, i, rc)
        for kind, record, want in (("full", False, True), ("gate", False, False), ("gate", True, True)):
            seen.clear()
            with mock.patch.object(rt, "worker_env", spy), tempfile.TemporaryDirectory() as base, \
                    mock.patch.object(rt, "spawn", side_effect=RuntimeError("stop")):
                try:
                    rt.run_pool(["test_x"], 1, 1, record, base, [], None, kind)
                except RuntimeError:
                    pass
            self.assertEqual(seen[0], want, (kind, record))


class HomeParityTest(unittest.TestCase):
    def test_home_keyed_fixture_has_equal_verdicts_serial_and_pooled(self):
        with tempfile.TemporaryDirectory(prefix="r882-") as base:
            serial, src = pooleq.serial_outcomes([FIXTURE], base)
            pooled, prc = pooleq.pool_outcomes([FIXTURE], 2, base)
        self.assertEqual(len(serial), 3)
        self.assertEqual(pooleq.compare(serial, pooled), [])
        import subprocess
        shown = subprocess.run([sys.executable, os.path.join(rt.ROOT, "tools", "runtests.py"), "--workers", "2", FIXTURE],
                               capture_output=True, text=True)
        self.assertEqual((src, prc), (0, 0), shown.stdout + shown.stderr)
        self.assertEqual({o for _, o in serial}, {"ok"})


class MaskedModuleTest(unittest.TestCase):
    def test_a_dotted_sibling_that_ran_nothing_is_reported(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(rt.ROOT, "tools", "runtests.py"), "--workers", "2",
                            "fixtures.maska", "fixtures.maskb"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("no result for module fixtures.maskb", r.stdout)
        self.assertNotIn("no result for module fixtures.maska", r.stdout)


class HomeScanTest(unittest.TestCase):
    def test_the_scan_finds_a_planted_home_reader(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_planted.py"), "w") as f:
                f.write("import os\nx = os.path.expanduser('~')\n")
            with open(os.path.join(d, "test_clean.py"), "w") as f:
                f.write("y = 1\n")
            with open(os.path.join(d, "test_xdg.py"), "w") as f:
                f.write("z = os.environ.get('XDG_CACHE_HOME')\nw = 'Library/Caches'\n")
            self.assertEqual(rt.home_readers(d), [("test_planted.py", 2), ("test_xdg.py", 1), ("test_xdg.py", 2)])

    def test_the_flag_prints_the_modules(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(rt.ROOT, "tools", "runtests.py"), "--list-home-readers"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("modules read HOME", r.stdout)


if __name__ == "__main__":
    unittest.main()
