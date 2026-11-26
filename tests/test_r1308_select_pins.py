"""R-1308: the gate adds the tests that check a hash pin when the pinned file changes."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import gate_test as gt  # noqa: E402

PINS = ["test_r1058_home_guard_reads", "test_r1259_receipt"]


class PinChecksTest(unittest.TestCase):
    def test_a_runtests_change_adds_both_pin_tests(self):
        mods, note = gt.with_pin_checks(["test_a"], ["tools/runtests.py"])
        self.assertEqual(mods, ["test_a"] + PINS)
        self.assertIn("tools/runtests.py", note)

    def test_a_studiolib_only_change_adds_neither(self):
        mods, note = gt.with_pin_checks(["test_a"], ["plugin/scripts/studiolib.py"])
        self.assertEqual(mods, ["test_a"])
        self.assertEqual(note, "")

    def test_a_tests_only_change_adds_neither(self):
        mods, note = gt.with_pin_checks(["test_a"], ["tests/test_x.py", "tests/test_r1308_select_pins.py"])
        self.assertEqual(mods, ["test_a"])
        self.assertEqual(note, "")

    def test_an_unreadable_file_list_adds_them_and_says_so(self):
        mods, note = gt.with_pin_checks(["test_a"], None)
        self.assertEqual(mods, ["test_a"] + PINS + ["test_float_bundle", "test_float_export"])
        self.assertIn("could not read", note)

    def test_modules_already_listed_are_not_duplicated(self):
        mods, _ = gt.with_pin_checks(["test_r1259_receipt", "test_a"], ["tools/runtests.py"])
        self.assertEqual(mods, ["test_r1259_receipt", "test_a", "test_r1058_home_guard_reads"])

    def test_the_pin_tests_exist_as_modules(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for m in PINS:
            self.assertTrue(os.path.exists(os.path.join(here, m + ".py")), m)

    def test_every_mapped_test_exists_as_a_module(self):
        here = os.path.dirname(os.path.abspath(__file__))
        mapped = set()
        for tests in list(gt.PINNED_FILE_TESTS.values()) + list(gt.PINNED_PREFIX_TESTS.values()):
            mapped.update(tests)
        for m in mapped:
            self.assertTrue(os.path.exists(os.path.join(here, m + ".py")), m)

    def test_a_change_under_the_float_bundle_adds_the_float_tests(self):
        for f in ("plugin/float/MANIFEST.sha256", "plugin/float/Brote.app/Contents/MacOS/x", "plugin/float/src/a.swift"):
            mods, note = gt.with_pin_checks(["test_a"], [f])
            self.assertEqual(mods, ["test_a", "test_float_bundle", "test_float_export"], f)
            self.assertIn(f, note)

    def test_the_float_record_adds_the_float_tests(self):
        mods, _ = gt.with_pin_checks([], ["tools/brote/float/VERIFIED.json"])
        self.assertEqual(mods, ["test_float_bundle", "test_float_export"])

    def test_a_float_source_or_lookalike_path_adds_nothing(self):
        mods, note = gt.with_pin_checks(["test_a"], ["plugin/floatish/x", "tools/brote/float/build.sh"])
        self.assertEqual(mods, ["test_a"])
        self.assertEqual(note, "")

    def test_a_rename_lists_the_old_path_too(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            def g(*a):
                subprocess.run(["git", "-C", d] + list(a), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            g("init", "-b", "main")
            g("config", "user.email", "t@example.com")
            g("config", "user.name", "t")
            g("config", "diff.renames", "true")
            os.makedirs(os.path.join(d, "tools"))
            with open(os.path.join(d, "tools", "runtests.py"), "w") as f:
                f.write("x = 1\n" * 20)
            g("add", ".")
            g("commit", "-m", "base")
            with open(os.path.join(d, "other.txt"), "w") as f:
                f.write("o\n")
            g("add", ".")
            g("commit", "-m", "second")
            g("mv", "tools/runtests.py", "tools/moved.py")
            files = gt.branch_files(d)
            self.assertIn("tools/runtests.py", files)
            self.assertIn("tools/moved.py", files)
            mods, _ = gt.with_pin_checks(["test_a"], files)
            self.assertEqual(mods, ["test_a"] + PINS)

    def test_no_module_named_says_so_in_one_line(self):
        self.assertIn("no module", gt.no_module_note().lower())


class OnMainTest(unittest.TestCase):
    """Security finding 1: on main (merge-base is HEAD) a committed change must still be listed."""

    def repo(self, d):
        import subprocess

        def g(*a):
            subprocess.run(["git", "-C", d] + list(a), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def write(rel, text):
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            with open(os.path.join(d, rel), "w") as f:
                f.write(text)
        g("init", "-b", "main")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        return g, write

    def test_a_committed_runtests_change_on_main_adds_both_pin_tests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            g, write = self.repo(d)
            write("tools/runtests.py", "x = 1\n")
            g("add", ".")
            g("commit", "-m", "base")
            write("tools/runtests.py", "x = 2\n")
            g("commit", "-am", "change the runner")
            files = gt.branch_files(d)
            self.assertEqual(files, ["tools/runtests.py"])
            mods, _ = gt.with_pin_checks(["test_a"], files)
            self.assertEqual(mods, ["test_a"] + PINS)

    def test_a_committed_docs_change_on_main_adds_the_leak_tests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            g, write = self.repo(d)
            write("docs/wiki/Home.md", "a\n")
            g("add", ".")
            g("commit", "-m", "base")
            write("docs/wiki/Home.md", "b\n")
            g("commit", "-am", "change the wiki")
            files = gt.branch_files(d)
            self.assertEqual(files, ["docs/wiki/Home.md"])
            mods, _ = gt.with_visible_checks(["test_a"], files)
            self.assertEqual(mods, ["test_a", "test_export", "test_leak"])

    def test_head_changes_the_runner_and_the_work_tree_puts_it_back_still_adds_both_pin_tests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            g, write = self.repo(d)
            write("tools/runtests.py", "x = 1\n")
            g("add", ".")
            g("commit", "-m", "base")
            write("tools/runtests.py", "x = 2\n")
            g("commit", "-am", "change the runner")
            write("tools/runtests.py", "x = 1\n")          # uncommitted: the earlier content is back
            files = gt.branch_files(d)
            self.assertEqual(files, ["tools/runtests.py"])
            mods, _ = gt.with_pin_checks(["test_a"], files)
            self.assertEqual(mods, ["test_a"] + PINS)

    def test_head_adds_a_wiki_file_and_the_work_tree_deletes_it_still_adds_the_leak_tests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            g, write = self.repo(d)
            write("a.txt", "a\n")
            g("add", ".")
            g("commit", "-m", "base")
            write("docs/wiki/New.md", "n\n")
            g("add", ".")
            g("commit", "-m", "add a page")
            g("rm", "-q", "docs/wiki/New.md")
            files = gt.branch_files(d)
            self.assertEqual(files, ["docs/wiki/New.md"])
            mods, _ = gt.with_visible_checks(["test_a"], files)
            self.assertEqual(mods, ["test_a", "test_export", "test_leak"])

    def test_a_root_commit_is_unreadable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            g, write = self.repo(d)
            write("tools/runtests.py", "x = 1\n")
            g("add", ".")
            g("commit", "-m", "base")
            self.assertIsNone(gt.branch_files(d))


if __name__ == "__main__":
    unittest.main()
