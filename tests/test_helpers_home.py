"""R-428: a BUDGIT_HOME exported in the shell wins over STUDIO_HOME (studiolib.env), so a fixture that only sets STUDIO_HOME would
write into the real Budgit home. The helpers take it out for the test. Proof: a child test run with a bogus BUDGIT_HOME and STUDIO_HOME exported
sees the temp home, writes there, and leaves the bogus folder uncreated; the variable is back afterwards."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import StudioTestCase, ROOT

CHILD = r'''
import os, sys, unittest
sys.path.insert(0, os.path.join(sys.argv[1], "tests"))
from helpers import StudioTestCase
import studiolib as sl

class Probe(StudioTestCase):
    def test_home_is_the_temp_one(self):
        self.assertNotIn("BUDGIT_HOME", os.environ)
        self.assertEqual(os.path.realpath(sl.home_state()), os.path.realpath(self.home))
        sl.activate(self.project, bind=False)
        sl.audit(self.project, "probe")
        os.makedirs(sl.home_state(), exist_ok=True)
        sl.atomic_write(os.path.join(sl.home_state(), "marker"), "x")

if __name__ == "__main__":
    unittest.main(argv=["probe"])
'''


class HelperHomeTest(StudioTestCase):
    def test_an_exported_budgit_home_is_never_reached(self):
        scratch = tempfile.mkdtemp()
        bogus = os.path.join(scratch, "bogus-budgit-home")
        bogus_studio = os.path.join(scratch, "bogus-studio-home")
        env = {k: v for k, v in os.environ.items() if k not in ("STUDIO_HOME", "BUDGIT_HOME", "BUDGIT_SUITE_QUEUE_HELD")}
        env["BUDGIT_HOME"] = bogus
        env["STUDIO_HOME"] = bogus_studio
        try:
            r = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", CHILD, ROOT], env=env, capture_output=True, text=True,
                               cwd=os.path.join(ROOT, "tests"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(os.listdir(scratch), [], "the test run created an exported home")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_the_variable_is_put_back_after_the_test(self):
        class Inner(StudioTestCase):
            def runTest(self):
                self.seen = os.environ.get("BUDGIT_HOME")

        old = os.environ.get("BUDGIT_HOME")
        os.environ["BUDGIT_HOME"] = "/nonexistent/bogus-home"
        try:
            t = Inner()
            t.setUp()
            t.runTest()
            t.tearDown()
            t.doCleanups()                  # R-1050: the environment comes back in a cleanup, as the runner calls it after tearDown
            self.assertIsNone(t.seen)
            self.assertEqual(os.environ.get("BUDGIT_HOME"), "/nonexistent/bogus-home")
        finally:
            if old is None:
                os.environ.pop("BUDGIT_HOME", None)
            else:
                os.environ["BUDGIT_HOME"] = old


if __name__ == "__main__":
    unittest.main()
