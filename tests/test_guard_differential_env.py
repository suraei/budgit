"""R-1021: the differential verdict is the same inside and outside a claude session (a terminal shell sets PAGER)."""
import os
import unittest

import test_guard_differential as td
import studiolib as sl


class DifferentialEnvIsolationTest(td.DifferentialGuardTest):
    """The same differential tests, run with the culprit variables set before setUp."""

    def setUp(self):
        saved = {k: os.environ.get(k) for k in ("PAGER", "LESSOPEN", "GIT_PAGER")}
        for k in saved:
            os.environ[k] = "less"
        for k, v in saved.items():
            self.addCleanup(os.environ.pop, k, None) if v is None else self.addCleanup(os.environ.__setitem__, k, v)
        super().setUp()

    def test_the_culprit_env_is_set_before_the_test_body_but_not_seen_by_the_guard(self):
        # setUp cleared them for the guard's duration
        self.assertTrue(sl._git_env_clean())


if __name__ == "__main__":
    unittest.main()
