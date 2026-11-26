"""R-882 synthetic fixture: a sibling that really runs one test (see maskb)."""
import unittest


class MaskA(unittest.TestCase):
    def test_runs(self):
        self.assertTrue(True)
