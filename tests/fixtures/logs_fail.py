"""Fixture for test_runtests_failed_logs: one module with one test that fails on one assert (not collected by the suite: no test_ prefix)."""
import unittest


class LogsFail(unittest.TestCase):
    def test_one_assert_fails(self):
        self.assertEqual(1, 2, "fixture failure marker")
