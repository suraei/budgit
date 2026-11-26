"""R-1277: gate_test.prepare must export PYTHONPATH so child processes (runtests.py) find safepath."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import gate_test  # noqa: E402
import safepath  # noqa: E402


class GatePathTest(unittest.TestCase):
    def test_gate_test_sets_pythonpath(self):
        cwd = os.getcwd()
        try:
            with mock.patch.dict(os.environ, {"PYTHONPATH": "/old/one"}):
                gate_test.prepare(ROOT)
                got = os.environ["PYTHONPATH"].split(os.pathsep)
        finally:
            os.chdir(cwd)
        own = safepath.own_dirs(ROOT)
        self.assertEqual(got[:len(own)], own)
        self.assertIn(os.path.join(ROOT, "plugin", "scripts"), own)
        self.assertEqual(got[-1], "/old/one")


if __name__ == "__main__":
    unittest.main()
