"""R-1050: a test class that leaves HOME, BUDGIT_HOME or STUDIO_HOME changed fails (StudioTestCase.tearDownClass), and
patch_env puts the whole environment back after its test."""
import io
import os
import unittest

from helpers import StudioTestCase, patch_env


def run_inner(case):
    """Run one throwaway test class on its own and return the result; the real runner never sees it."""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
    return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)


class GuardTest(StudioTestCase):
    def test_a_class_that_leaves_home_changed_fails_the_check_and_the_value_is_put_back(self):
        real = os.environ.get("HOME")

        class Leaks(StudioTestCase):
            def setUp(self):                                   # no super().setUp(): nothing registers a restore
                os.environ["HOME"] = "/tmp/r1050-leaked-home"

            def tearDown(self):
                pass

            def test_it(self):
                self.assertEqual(os.environ["HOME"], "/tmp/r1050-leaked-home")

        try:
            result = run_inner(Leaks)
            self.assertEqual(len(result.errors), 1, result.errors)
            self.assertIn("left the environment changed", result.errors[0][1])
            self.assertIn("HOME", result.errors[0][1])
            self.assertEqual(os.environ.get("HOME"), real)     # the guard put it back, so no later class pays for it
        finally:
            if real is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = real                      # and this test restores HOME itself, whatever happened

    def test_a_class_that_leaves_the_studio_home_changed_fails_too(self):
        real = os.environ.get("STUDIO_HOME")

        class LeaksStudio(StudioTestCase):
            def setUp(self):
                os.environ["STUDIO_HOME"] = "/tmp/r1050-leaked-studio"

            def tearDown(self):
                pass

            def test_it(self):
                pass

        try:
            result = run_inner(LeaksStudio)
            self.assertEqual(len(result.errors), 1, result.errors)
            self.assertIn("STUDIO_HOME", result.errors[0][1])
            self.assertEqual(os.environ.get("STUDIO_HOME"), real)
        finally:
            if real is None:
                os.environ.pop("STUDIO_HOME", None)
            else:
                os.environ["STUDIO_HOME"] = real

    def test_a_class_that_restores_everything_passes(self):
        class Clean(StudioTestCase):
            def test_it(self):
                patch_env(self, HOME="/tmp/r1050-scoped-home")
                self.assertEqual(os.environ["HOME"], "/tmp/r1050-scoped-home")

        result = run_inner(Clean)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)


class PatchEnvTest(StudioTestCase):
    def test_patch_env_puts_the_environment_back_with_the_variables_that_were_not_set(self):
        real = os.environ.get("HOME")

        class Scoped(unittest.TestCase):
            def test_it(self):
                patch_env(self, HOME="/tmp/r1050-x", R1050_NEW_VARIABLE="1")
                self.assertEqual(os.environ["R1050_NEW_VARIABLE"], "1")

        result = run_inner(Scoped)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(os.environ.get("HOME"), real)
        self.assertNotIn("R1050_NEW_VARIABLE", os.environ)


if __name__ == "__main__":
    unittest.main()
