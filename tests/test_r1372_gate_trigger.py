"""R-1372: the gate runs the full suite when the ledger save path or the leak shield changes.

R-1366 put leak.alias_data into studiolib.save_ledger; the gate ran only the modules named by hand, so test_livework and
test_r1229_retext never ran. The rule reads diff hunks for studiolib (functions on the save path, or calling the leak
module) and takes any change to leak.py whole.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import gate_test as gt  # noqa: E402

STUDIOLIB = "plugin/scripts/studiolib.py"
LEAK = "plugin/scripts/leak.py"

# studiolib before the alias-on-save change: save_ledger does not call leak
STUDIOLIB_BASE = '''import json


def validate_ledger(data):
    return data


def save_ledger(project, data):
    validate_ledger(data)
    path = project
    text = json.dumps(data)
    return path, text


def ledger_tampered(project):
    return False


def add_note(project, text):
    return text + "!"


_CLI_READS = frozenset(("ledger show", "ledger list"))


def mentions_tables():
    return "_VAR_READ_KEYS and _CLI_READS in a string"


_VAR_READ_KEYS = frozenset(("ledger show", "status"))
'''

LEAK_BASE = '''def hide(text, names):
    return text


def alias_data(value, config=None):
    names = []
    if not names:
        return value
    return value


def push_check(root):
    return []
'''


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.g("init", "-b", "main")
        self.g("config", "user.email", "t@example.com")
        self.g("config", "user.name", "t")
        self.write(STUDIOLIB, STUDIOLIB_BASE)
        self.write(LEAK, LEAK_BASE)
        for m in ("test_livework", "test_r1229_retext", "test_other"):
            self.write("tests/%s.py" % m, "")
        self.g("add", ".")
        self.g("commit", "-m", "base")
        self.branch("feature")

    def branch(self, name):
        """A branch one commit ahead of main (as a real work branch is), so the diff base is main's tip."""
        self.g("checkout", "-b", name)
        self.write("branch-%s.txt" % name, "start\n")
        self.g("add", ".")
        self.g("commit", "-m", "start " + name)

    def g(self, *a):
        subprocess.run(["git", "-C", self.d] + list(a), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, rel, text):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def edit(self, rel, old, new):
        p = os.path.join(self.d, rel)
        with open(p) as f:
            text = f.read()
        self.assertIn(old, text)
        self.write(rel, text.replace(old, new, 1))

    def files(self):
        return gt.branch_files(self.d)

    def reason(self):
        return gt.full_suite_reason(self.files(), self.d)


class FullSuiteTriggerTest(Repo):
    def test_a_a_change_to_the_ledger_save_in_studiolib_selects_the_full_suite(self):
        self.edit(STUDIOLIB, "    path = project\n", "    path = project + '/x'\n")
        self.assertIn("save_ledger", self.reason())
        mods, note = gt.with_full_suite(["test_a"], self.files(), self.d)
        self.assertEqual(mods, ["test_a", "test_livework", "test_other", "test_r1229_retext"])
        self.assertIn("full suite", note)

    def test_a_a_deletion_inside_the_ledger_save_still_selects_it(self):
        self.edit(STUDIOLIB, "    validate_ledger(data)\n", "")
        self.assertIn("save_ledger", self.reason())

    def test_a_a_change_to_the_hash_and_view_writer_selects_it(self):
        self.edit(STUDIOLIB, "def ledger_tampered(project):\n    return False\n",
                  "def _write_hash_and_view(project, data, text):\n    return text\n")
        self.assertIn("_write_hash_and_view", self.reason())

    def test_b_a_change_to_leak_alias_data_selects_the_full_suite(self):
        self.edit(LEAK, "    if not names:\n        return value\n", "    if not names:\n        return None\n")
        self.assertIn(LEAK, self.reason())
        mods, note = gt.with_full_suite([], self.files(), self.d)
        self.assertEqual(mods, ["test_livework", "test_other", "test_r1229_retext"])
        self.assertIn("full suite", note)

    def test_b_a_change_to_a_function_alias_data_calls_selects_it_too(self):
        self.edit(LEAK, "def hide(text, names):\n    return text\n", "def hide(text, names):\n    return text.strip()\n")
        self.assertIn(LEAK, self.reason())

    def test_c_an_unrelated_studiolib_function_stays_selective(self):
        self.edit(STUDIOLIB, '    return text + "!"\n', '    return text + "?"\n')
        self.assertEqual(self.reason(), "")
        mods, note = gt.with_full_suite(["test_a"], self.files(), self.d)
        self.assertEqual((mods, note), (["test_a"], ""))

    def test_c_a_new_function_after_the_save_stays_selective(self):
        with open(os.path.join(self.d, STUDIOLIB), "a") as f:
            f.write("\n\ndef another(x):\n    return x\n")
        self.assertEqual(self.reason(), "")

    def test_c_an_insert_at_the_edge_of_the_save_function_stays_selective(self):
        self.edit(STUDIOLIB, "def ledger_tampered(project):", "def helper():\n    return 1\n\n\ndef ledger_tampered(project):")
        self.assertEqual(self.reason(), "")

    def test_c_a_branch_that_changes_other_files_stays_selective(self):
        self.write("plugin/scripts/other.py", "x = 1\n")
        self.assertEqual(self.reason(), "")

    def test_d_the_r1366_shape_would_now_select_the_two_tests_that_went_red(self):
        self.edit(STUDIOLIB, "    validate_ledger(data)\n    path = project\n",
                  "    validate_ledger(data)\n    import leak\n    data = leak.alias_data(data)   # R-1366\n    path = project\n")
        self.assertIn("save_ledger", self.reason())
        mods, _ = gt.with_full_suite(["test_livework_only_named"], self.files(), self.d)
        self.assertIn("test_livework", mods)
        self.assertIn("test_r1229_retext", mods)

    def test_d_the_same_shape_in_another_writer_is_caught_by_the_leak_call(self):
        self.edit(STUDIOLIB, '    return text + "!"\n', '    import leak\n    return leak.alias_text(text) + "!"\n')
        self.assertIn("add_note", self.reason())

    def alias_already_on_main(self):
        self.g("checkout", "main")
        self.edit(STUDIOLIB, '    return text + "!"\n', '    import leak\n    return leak.alias_text(text) + "!"\n')
        self.g("commit", "-am", "alias a note")
        self.branch("feature2")

    def test_d_a_later_edit_of_a_function_that_already_calls_leak_selects_it(self):
        self.alias_already_on_main()
        self.edit(STUDIOLIB, "def add_note(project, text):\n", "def add_note(project, text):\n    text = text.strip()\n")
        self.assertIn("add_note", self.reason())

    def test_d_removing_the_leak_call_from_a_function_selects_it(self):
        self.alias_already_on_main()
        self.edit(STUDIOLIB, "    import leak\n    return leak.alias_text(text) + \"!\"\n", '    return text + "!"\n')
        self.assertIn("add_note", self.reason())

    def test_c_an_edit_of_another_function_after_the_alias_stays_selective(self):
        self.alias_already_on_main()
        self.edit(STUDIOLIB, "    return False\n", "    return True\n")
        self.assertEqual(self.reason(), "")

    def test_a_committed_change_on_main_is_still_seen(self):
        self.g("checkout", "main")
        self.edit(STUDIOLIB, "    path = project\n", "    path = project + '/x'\n")
        self.g("commit", "-am", "change the save")
        self.assertIn("save_ledger", self.reason())

    def test_an_unreadable_file_list_runs_the_full_suite(self):
        self.assertIn("could not read", gt.full_suite_reason(None, self.d))
        mods, note = gt.with_full_suite(["test_a"], None, self.d)
        self.assertEqual(mods[0], "test_a")
        self.assertIn("test_livework", mods)

    def test_an_unreadable_studiolib_diff_runs_the_full_suite(self):
        with tempfile.TemporaryDirectory() as nogit:
            self.assertIn("could not read", gt.full_suite_reason([STUDIOLIB], nogit))

    def test_a_studiolib_that_does_not_parse_runs_the_full_suite(self):
        self.write(STUDIOLIB, "def broken(:\n")
        self.assertIn("could not read", self.reason())


class RealTreeTest(unittest.TestCase):
    def test_the_two_tests_that_went_red_are_in_the_real_full_list(self):
        mods = gt.all_test_modules()
        self.assertIn("test_livework", mods)
        self.assertIn("test_r1229_retext", mods)

    def test_the_gate_main_adds_the_full_suite_for_a_leak_change(self):
        ran = []

        def fake_main(module=None, argv=None, exit=True):
            ran.extend(argv)
            return mock.Mock(result=mock.Mock(wasSuccessful=lambda: True))
        out = io.StringIO()
        with mock.patch.object(gt, "prepare", return_value=list(sys.path)), \
                mock.patch.object(gt, "check_dataless", return_value=None), \
                mock.patch.object(gt, "branch_files", return_value=[LEAK]), \
                mock.patch.object(gt.unittest, "main", fake_main), \
                mock.patch.object(gt, "run_full_suite", lambda mods, **k: ran.extend(mods) or 0), \
                mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != gt.NESTED_ENV}, clear=True), \
                redirect_stdout(out):
            rc = gt.main(["test_a"])      # R-1379 P1a: the escalated modules go to run_full_suite (runtests workers), not unittest.main
        self.assertEqual(rc, 0)
        self.assertIn("test_livework", ran)
        self.assertIn("test_r1229_retext", ran)
        self.assertIn("full suite", out.getvalue())

    def test_the_gate_main_stays_selective_for_an_unrelated_file(self):
        ran = []

        def fake_main(module=None, argv=None, exit=True):
            ran.extend(argv)
            return mock.Mock(result=mock.Mock(wasSuccessful=lambda: True))
        out = io.StringIO()
        with mock.patch.object(gt, "prepare", return_value=list(sys.path)), \
                mock.patch.object(gt, "check_dataless", return_value=None), \
                mock.patch.object(gt, "branch_files", return_value=["tests/test_x.py"]), \
                mock.patch.object(gt.unittest, "main", fake_main), redirect_stdout(out):
            gt.main(["test_a"])
        self.assertEqual(ran[:2], ["gate_test", "test_a"])
        self.assertNotIn("test_livework", ran)                  # no full suite
        for m in gt.GUARD_TESTS:
            self.assertNotIn(m, ran)

    def gate_ran(self, files):
        ran, out = [], io.StringIO()

        def fake_main(module=None, argv=None, exit=True):
            ran.extend(argv)
            return mock.Mock(result=mock.Mock(wasSuccessful=lambda: True))
        with mock.patch.object(gt, "prepare", return_value=list(sys.path)), \
                mock.patch.object(gt, "check_dataless", return_value=None), \
                mock.patch.object(gt, "branch_files", return_value=files), \
                mock.patch.object(gt.unittest, "main", fake_main), redirect_stdout(out):
            gt.main(["test_a"])
        return ran, out.getvalue()

    def test_r1280_the_gate_main_runs_the_landing_set_with_no_changed_file(self):
        ran, _ = self.gate_ran([])
        for m in ("test_leak", "test_export", "test_public_voice", "test_shipped_owner_words"):
            self.assertIn(m, ran)

    def test_r1228_the_gate_main_adds_both_guard_tests_for_the_cli_file(self):
        ran, out = self.gate_ran([gt.CLI_FILE])
        for m in gt.GUARD_TESTS:
            self.assertIn(m, ran)

    def test_r1318_the_real_slow_record_names_existing_modules(self):
        slow = gt.slow_modules()
        self.assertTrue(slow)
        for m in slow:
            self.assertTrue(os.path.exists(os.path.join(HERE, m + ".py")), m)

    def test_r1318_a_real_slow_importer_of_a_changed_file_is_added_by_the_gate_main(self):
        slow = gt.slow_modules()
        ran, out = self.gate_ran(["plugin/scripts/studiolib.py"])
        added = [m for m in slow if m in ran]
        self.assertTrue(added)
        self.assertIn("slow-tier", out)


class LandingSetTest(Repo):
    """R-1280: a fixed landing set, whatever the diff."""
    def files(self, *names):
        for m in names:
            self.write("tests/%s.py" % m, "")

    def test_r1280_the_set_is_added_whatever_the_diff(self):
        self.files("test_leak", "test_export", "test_export_public", "test_public_voice", "test_shipped_owner_words")
        mods, note = gt.with_landing_set(["test_a"], self.d)
        self.assertEqual(mods, ["test_a", "test_leak", "test_export", "test_export_public", "test_public_voice",
                                "test_shipped_owner_words"])
        self.assertEqual(note.count("does not exist"), 0)

    def test_r1280_a_missing_module_is_skipped_and_named_in_one_line(self):
        self.files("test_leak", "test_export", "test_shipped_owner_words")
        mods, note = gt.with_landing_set([], self.d)
        self.assertEqual(mods, ["test_leak", "test_export", "test_shipped_owner_words"])
        self.assertEqual([l for l in note.splitlines() if "test_public_voice" in l and "does not exist" in l].__len__(), 1)

    def test_r1280_no_export_module_is_named_once_as_a_prefix(self):
        self.files("test_leak", "test_public_voice", "test_shipped_owner_words")
        mods, note = gt.with_landing_set([], self.d)
        self.assertEqual(len([l for l in note.splitlines() if "test_export*" in l]), 1)

    def test_r1280_a_module_already_named_is_not_repeated(self):
        self.files("test_leak", "test_export", "test_public_voice", "test_shipped_owner_words")
        mods, _ = gt.with_landing_set(["test_leak", "test_a"], self.d)
        self.assertEqual(mods.count("test_leak"), 1)


class GuardTableTest(Repo):
    """R-1228 (2): the CLI file or a guard table always adds both guard tests, by file and table name."""
    BOTH = list(gt.GUARD_TESTS)

    def test_r1228_a_change_to_the_cli_file_adds_both(self):
        mods, note = gt.with_guard_checks(["test_a"], ["plugin/bin/budgit"], self.d)
        self.assertEqual(mods, ["test_a"] + self.BOTH)
        self.assertIn("plugin/bin/budgit", note)

    def test_r1228_a_change_to_the_cli_reads_table_adds_both(self):
        self.edit(STUDIOLIB, '"ledger list"))', '"ledger list", "ledger top"))')
        mods, note = gt.with_guard_checks(["test_a"], self.files(), self.d)
        self.assertEqual(mods, ["test_a"] + self.BOTH)
        self.assertIn("_CLI_READS", note)

    def test_r1228_a_change_to_the_var_read_keys_table_adds_both(self):
        self.edit(STUDIOLIB, '"ledger show", "status"', '"ledger show", "ledger list", "status"')
        mods, note = gt.with_guard_checks(["test_a"], self.files(), self.d)
        self.assertEqual(mods, ["test_a"] + self.BOTH)
        self.assertIn("_VAR_READ_KEYS", note)

    def test_r1228_an_unrelated_studiolib_change_adds_neither(self):
        self.edit(STUDIOLIB, '    return text + "!"\n', '    return text + "?"\n')
        mods, note = gt.with_guard_checks(["test_a"], self.files(), self.d)
        self.assertEqual((mods, note), (["test_a"], ""))

    def test_r1228_a_function_that_only_names_a_table_in_text_adds_neither(self):
        self.edit(STUDIOLIB, "in a string", "in a changed string")
        mods, note = gt.with_guard_checks(["test_a"], self.files(), self.d)
        self.assertEqual((mods, note), (["test_a"], ""))

    def test_r1228_an_unreadable_list_or_diff_adds_both(self):
        mods, _ = gt.with_guard_checks(["test_a"], None, self.d)
        self.assertEqual(mods, ["test_a"] + self.BOTH)
        with tempfile.TemporaryDirectory() as nogit:
            mods, note = gt.with_guard_checks(["test_a"], [STUDIOLIB], nogit)
        self.assertEqual(mods, ["test_a"] + self.BOTH)
        self.assertIn("could not read", note)

    def test_r1228_a_test_already_named_is_not_repeated(self):
        mods, _ = gt.with_guard_checks(["test_r901_guard_writes"], ["plugin/bin/budgit"], self.d)
        self.assertEqual(mods, ["test_r901_guard_writes", "test_r1058_home_guard_reads"])


class SlowTierTest(Repo):
    """R-1318: the slow-tier modules that import a changed file. Per-chain dedupe is not tested: it needs R-1334."""
    def setUp(self):
        super().setUp()
        self.write("tests/.durations.json", '{"test_slow_a": 30.0, "test_slow_b": 12.0, "test_fast": 0.2, '
                   '"test_slow_none": 50.0, "test_gone": 99.0, "test_slow_cli": 11.0}')
        self.write("tests/test_slow_a.py", "import studiolib\n")
        self.write("tests/test_slow_b.py", "import leak\n")
        self.write("tests/test_fast.py", "import studiolib\n")
        self.write("tests/test_slow_none.py", "import json\n")
        self.write("tests/test_slow_cli.py", "BIN = 'plugin/bin/budgit'\n")

    def test_r1318_a_changed_file_adds_the_slow_modules_that_import_it(self):
        mods, note = gt.with_slow_tier(["test_a"], [STUDIOLIB], self.d)
        self.assertEqual(mods, ["test_a", "test_slow_a"])
        self.assertIn("slow-tier", note)

    def test_r1318_a_fast_importer_and_a_non_importer_are_not_added(self):
        mods, _ = gt.with_slow_tier([], [LEAK], self.d)
        self.assertEqual(mods, ["test_slow_b"])

    def test_r1318_the_cli_file_adds_the_slow_modules_that_name_it(self):
        mods, _ = gt.with_slow_tier([], ["plugin/bin/budgit"], self.d)
        self.assertEqual(mods, ["test_slow_cli"])

    def test_r1318_no_changed_plugin_file_adds_nothing(self):
        self.assertEqual(gt.with_slow_tier(["test_a"], ["docs/x.md", "tests/test_x.py"], self.d), (["test_a"], ""))

    def test_r1318_an_unreadable_list_adds_every_existing_slow_module(self):
        mods, _ = gt.with_slow_tier([], None, self.d)
        self.assertEqual(mods, ["test_slow_a", "test_slow_b", "test_slow_cli", "test_slow_none"])

    def test_r1318_an_unreadable_record_says_so_and_adds_nothing(self):
        with tempfile.TemporaryDirectory() as empty:
            mods, note = gt.with_slow_tier(["test_a"], [STUDIOLIB], empty)
        self.assertEqual(mods, ["test_a"])
        self.assertIn("durations", note)

    def test_r1318_a_module_already_named_is_not_repeated(self):
        mods, _ = gt.with_slow_tier(["test_slow_a"], [STUDIOLIB], self.d)
        self.assertEqual(mods, ["test_slow_a"])


SEVEN = ("with_visible_checks", "with_pin_checks", "with_cli_table_check", "with_guard_checks",
         "with_slow_tier", "with_landing_set", "with_full_suite")


class NoBypassTest(unittest.TestCase):
    """R-1372 round 4: main() has no env, flag or file path that skips the escalation steps; tests patch the functions instead."""

    def run_main(self, patch_all):
        ran, out = [], io.StringIO()

        def fake_main(module=None, argv=None, exit=True):
            ran.extend(argv)
            return mock.Mock(result=mock.Mock(wasSuccessful=lambda: True))
        real = {n: getattr(gt, n) for n in SEVEN}
        calls = {n: [] for n in SEVEN}
        fakes = {n: (lambda mods, *a, _n=n: calls[_n].append(1) or (mods, "")) for n in SEVEN}
        spies = {n: (fakes[n] if patch_all else (lambda *a, _n=n, **k: calls[_n].append(1) or real[_n](*a, **k))) for n in SEVEN}
        with mock.patch.object(gt, "prepare", return_value=list(sys.path)), \
                mock.patch.object(gt, "check_dataless", return_value=None), \
                mock.patch.object(gt, "branch_files", return_value=[LEAK]), \
                mock.patch.object(gt.unittest, "main", fake_main), \
                mock.patch.object(gt, "run_full_suite", lambda mods, **k: ran.extend(mods) or 0), \
                mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != gt.NESTED_ENV}, clear=True), \
                redirect_stdout(out):
            patches =[mock.patch.object(gt, n, spies[n]) for n in SEVEN]
            for p in patches:
                p.start()
            try:
                gt.main(["test_a"])
            finally:
                for p in patches:
                    p.stop()
        return ran, calls

    def test_r1372_default_main_calls_all_seven_steps(self):
        ran, calls = self.run_main(False)
        for n in SEVEN:
            self.assertEqual(len(calls[n]), 1, n)
        self.assertIn("test_livework", ran)

    def test_r1372_with_all_seven_patched_none_of_the_real_steps_run(self):
        ran, calls = self.run_main(True)
        for n in SEVEN:
            self.assertEqual(len(calls[n]), 1, n)
        self.assertEqual(ran, ["gate_test", "test_a"])

    def test_r1372_main_and_the_entry_block_read_no_env_and_no_flag_for_the_step_list(self):
        import inspect
        src = inspect.getsource(gt.main)
        with open(gt.__file__) as f:
            text = f.read()
        entry = text[text.index("if __name__"):]
        for part in (src, entry):
            self.assertNotIn("environ", part)
            self.assertNotIn("getenv", part)
        self.assertIn('mods = [a for a in argv if not a.startswith("-")]', src)
        self.assertEqual(src.count("if mods"), 1)
        self.assertEqual(list(inspect.signature(gt.main).parameters), ["argv"])
        self.assertNotIn("GATE_TEST_PLAIN", text)

    def test_r1372_the_main_checkout_child_prints_no_full_suite_note(self):
        import test_r1149_safe_path as t
        if not (t.MAIN_CHECKOUT and os.path.realpath(t.MAIN_CHECKOUT) != os.path.realpath(t.ROOT)):
            self.skipTest("no separate main checkout: the test_r1149 child was NOT run, so its output was not checked")
        r = t.run_main_checkout_child()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("full suite", (r.stdout + r.stderr).lower())
        self.assertNotIn("gate: added", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
