"""Fixture for test_runtests_failed_logs: one failing test whose failure message is 120 lines long (the printed block must be capped)."""
import unittest


class LogsFailLong(unittest.TestCase):
    def test_long_failure(self):
        self.fail("\n".join("long failure line %03d" % i for i in range(120)))
