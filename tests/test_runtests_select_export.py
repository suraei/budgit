"""R-883 D: a changed path under a shipped tree always selects the leak, manifest and export tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import runtests as rt  # noqa: E402

GUARDS = {"test_brote_export_leak", "test_export", "test_export_public", "test_float_export", "test_leak"}


class ShippedEdgeTest(unittest.TestCase):
    def test_a_change_under_plugin_float_src_selects_the_leak_test(self):
        sel, why = rt.select(["plugin/float/src/brote.swift"])
        # Before the edge this fell back to the full suite (unknown non-doc file); the edge only adds, so it still does.
        # A full suite (None) contains test_brote_export_leak; a narrowed list must hold the Brote tests and the guards.
        if sel is not None:
            self.assertTrue(GUARDS | {"test_brote_float", "test_brote_scenario", "test_float_build"} <= set(sel), sorted(sel))
        else:
            self.assertIn("full suite", why)

    def test_the_edge_never_narrows_a_full_suite_fallback(self):
        sel, _ = rt.select(["plugin/float/src/brote.swift"])
        self.assertIsNone(sel)

    def test_every_shipped_tree_pulls_the_export_set(self):
        for f in ("plugin/scripts/zzz_new.py", "skills/budgit-kickoff/SKILL.md", "share/README.md"):
            sel, why = rt.select([f])
            self.assertIsNotNone(sel, f)
            self.assertTrue(GUARDS <= set(sel), f)

    def test_a_tests_only_change_does_not_pull_the_export_set(self):
        sel, _ = rt.select(["tests/test_runtests_home.py"])
        self.assertEqual(sel, ["test_runtests_home"])

    def test_the_guards_are_found_by_name_and_the_shipped_trees_are_read_from_the_exporter(self):
        self.assertTrue(GUARDS <= rt.export_tests(rt.discover([])))
        trees = rt.shipped_trees()
        for t in ("plugin/", "share/"):
            self.assertIn(t, trees)
        self.assertTrue(any(t.startswith("skills/") for t in trees))


if __name__ == "__main__":
    unittest.main()
