"""R-882 synthetic fixture: a module whose verdict depends on where its cache dir is resolved from HOME,
XDG_CACHE_HOME, ~/Library/Caches (and LOCALAPPDATA on Windows). Not collected by the suite (no test_ prefix); run
through tools/pooleq.py as `fixtures.homecache_probe`, serially and pooled, and both must give the same verdicts."""
import os
import sys
import unittest


def cache_dir(environ=None, platform=None):
    """The cache dir a HOME-keyed tool would pick."""
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    home = environ.get("HOME") or environ.get("USERPROFILE") or ""
    if environ.get("XDG_CACHE_HOME"):
        return environ["XDG_CACHE_HOME"]
    if platform == "darwin":
        return os.path.join(home, "Library", "Caches")
    if platform.startswith("win") and environ.get("LOCALAPPDATA"):
        return environ["LOCALAPPDATA"]
    return os.path.join(home, ".cache")


class HomeCacheProbe(unittest.TestCase):
    def test_the_runner_sets_no_scratch_cache_and_passes_the_real_home(self):
        self.assertNotIn("runtests-", os.environ.get("BUDGIT_TEST_CACHE", ""))
        self.assertTrue(os.environ.get("BUDGIT_TEST_REAL_HOME"))

    def test_the_home_keyed_cache_resolves_and_can_be_created(self):
        d = os.path.join(cache_dir(), "budgit-probe")
        os.makedirs(d, exist_ok=True)
        self.assertTrue(os.path.isdir(d))

    def test_the_real_home_is_not_the_worker_home(self):
        self.assertNotEqual(os.path.realpath(os.environ["BUDGIT_TEST_REAL_HOME"]), os.path.realpath(os.environ["HOME"]))


if __name__ == "__main__":
    unittest.main()
