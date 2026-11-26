"""Fixture for test_runtests_speed_slice1: one module with a test that always passes and takes a little time."""
import time
import unittest


class SpeedProbe(unittest.TestCase):
    def test_passes_after_a_short_wait(self):
        time.sleep(0.05)
        self.assertTrue(sum(range(1000)) > 0)
