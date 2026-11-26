"""R-1306: the selective test selector adds test_export and test_leak by itself when a change can carry visible text."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import runtests as rt  # noqa: E402

BOTH = {"test_export", "test_leak"}

# git diff --name-only 497b6455^1..7a5cec2d (the R-1288 lane-cap change)
R1288 = [
    "plugin/bin/budgit",
    "plugin/scripts/capacity.py",
    "plugin/scripts/i18n.py",
    "plugin/scripts/studiolib.py",
    "tests/test_r1288_capacity_cap.py",
    "tests/test_r1304_refusal_not_input.py",
]


class SelectLeakTest(unittest.TestCase):
    def test_the_r1288_change_selects_export_and_leak(self):
        sel, _ = rt.select(R1288)
        self.assertIsNotNone(sel)
        sel, _ = gt.with_visible_checks(sel, R1288)
        self.assertTrue(BOTH <= set(sel), sorted(sel))

    def test_a_tests_or_tools_only_change_adds_neither(self):
        files = ["tests/test_runtests_home.py", "tools/gate_test.py"]
        sel, _ = rt.select(files)
        self.assertIsNotNone(sel)
        sel, _ = gt.with_visible_checks(sel, files)
        self.assertFalse(BOTH & set(sel), sorted(sel))

    def test_a_wiki_release_notes_change_adds_both(self):
        sel, _ = rt.select(["docs/wiki/Release-notes.md"])
        self.assertIsNotNone(sel)
        sel, _ = gt.with_visible_checks(sel, ["docs/wiki/Release-notes.md"])
        self.assertTrue(BOTH <= set(sel), sorted(sel))

    def test_a_changelog_change_adds_both(self):
        sel, _ = rt.select(["CHANGELOG.md"])
        self.assertIsNotNone(sel)
        sel, _ = gt.with_visible_checks(sel, ["CHANGELOG.md"])
        self.assertTrue(BOTH <= set(sel), sorted(sel))

    def test_modules_picked_before_are_all_still_there_and_order_is_sorted(self):
        sel, _ = rt.select(R1288)
        self.assertEqual(list(sel), sorted(sel))
        for m in ("test_r1288_capacity_cap", "test_r1304_refusal_not_input"):
            self.assertIn(m, sel)
        sel_docs, _ = rt.select(["docs/wiki/Release-notes.md"])
        self.assertTrue(set(m for m in rt.discover([]) if "doc" in m) <= set(sel_docs))


import gate_test as gt  # noqa: E402


class GateAddsLeakChecksTest(unittest.TestCase):
    def test_the_r1288_files_add_both(self):
        mods, note = gt.with_visible_checks(["test_a"], R1288)
        self.assertEqual(mods, ["test_a", "test_export", "test_leak"])
        self.assertIn("test_export", note)

    def test_tests_and_tools_only_add_neither(self):
        mods, note = gt.with_visible_checks(["test_a"], ["tests/test_x.py", "tools/runtests.py"])
        self.assertEqual(mods, ["test_a"])
        self.assertEqual(note, "")

    def test_an_unreadable_file_list_adds_both_and_says_so(self):
        mods, note = gt.with_visible_checks(["test_a"], None)
        self.assertEqual(mods, ["test_a", "test_export", "test_leak"])
        self.assertIn("could not read", note)

    def test_no_git_binary_makes_the_file_list_unreadable_so_both_are_added(self):
        from unittest import mock
        with mock.patch.object(gt.subprocess, "run", side_effect=OSError("no git")):
            files = gt.branch_files()
        self.assertIsNone(files)
        mods, _ = gt.with_visible_checks(["test_a"], files)
        self.assertEqual(mods, ["test_a", "test_export", "test_leak"])

    def test_r1366_a_cli_file_change_adds_the_cli_table_test_and_says_so(self):
        mods, note = gt.with_cli_table_check(["test_a"], ["plugin/bin/budgit"])
        self.assertEqual(mods, ["test_a", "test_r901_guard_writes"])
        self.assertIn("the branch changes the CLI table", note)
        again, note2 = gt.with_cli_table_check(mods, ["plugin/bin/budgit"])
        self.assertEqual(again, mods)
        self.assertEqual(note2, "")
        other, note3 = gt.with_cli_table_check(["test_a"], ["plugin/scripts/hook.py"])
        self.assertEqual(other, ["test_a"])
        self.assertEqual(note3, "")
        self.assertIn("test_r901_guard_writes", gt.with_cli_table_check(["test_a"], None)[0])

    def test_modules_already_listed_are_not_duplicated(self):
        mods, _ = gt.with_visible_checks(["test_leak", "test_a"], R1288)
        self.assertEqual(mods, ["test_leak", "test_a", "test_export"])


if __name__ == "__main__":
    unittest.main()
