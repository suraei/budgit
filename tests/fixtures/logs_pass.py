"""Fixture for test_runtests_failed_logs: one module with one test that passes (not collected by the suite: no test_ prefix)."""
import unittest


class LogsPass(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(sum(range(100)) > 0)
