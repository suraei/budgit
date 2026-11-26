"""R-618 round 3: the two open risks of the pool runner (a module skipped at import, a failed setUpClass), and the
`budgit test --full` entry with its iCloud preflight."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import budgit_test as bt  # noqa: E402
import runtests as rt  # noqa: E402

FIXTURES = {
    "test_fx_ok": "import unittest\n\nclass T(unittest.TestCase):\n    def test_one(self): pass\n",
    # Risk A: skipped at import AND with `def test_` lines, so the old regex count came out short
    "test_fx_skipdef": "import unittest\nraise unittest.SkipTest('not here')\n\nclass T(unittest.TestCase):\n"
                       "    def test_a(self): pass\n    def test_b(self): pass\n",
    # Risk B: setUpClass fails, so its two tests never start
    "test_fx_setup": "import unittest\n\nclass T(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n"
                     "        raise RuntimeError('class set-up broke')\n    def test_a(self): pass\n    def test_b(self): pass\n",
}


def run_main(*argv):
    with tempfile.TemporaryDirectory() as d:
        for name, text in FIXTURES.items():
            with open(os.path.join(d, name + ".py"), "w") as f:
                f.write(text)
        old = (rt.TESTS, rt.DURATIONS)
        rt.TESTS, rt.DURATIONS = d, os.path.join(d, ".durations.json")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = rt.main(["runtests.py"] + list(argv))
        finally:
            rt.TESTS, rt.DURATIONS = old
    return code, buf.getvalue().splitlines()


class RiskATest(unittest.TestCase):
    def test_a_module_skipped_at_import_with_test_methods_passes_and_is_reported_skipped(self):
        code, lines = run_main("--workers", "1", "test_fx_ok", "test_fx_skipdef")
        self.assertEqual(code, 0, lines)
        self.assertIn("OK (1 NOT RUN)", lines)
        self.assertFalse(any(l.startswith("ABORT") for l in lines), lines)
        self.assertTrue(any(l.startswith("Ran 2 tests") for l in lines), lines)       # 1 real + 1 skipped placeholder


class RiskBTest(unittest.TestCase):
    def test_a_failed_setupclass_is_a_named_error_with_exit_1_not_an_id_mismatch(self):
        code, lines = run_main("--workers", "1", "test_fx_setup", "test_fx_ok")
        self.assertEqual(code, 1, lines)
        self.assertTrue(any(l.startswith("ERROR: setUpClass (test_fx_setup.T)") for l in lines), lines)
        self.assertFalse(any("ID CHECK" in l for l in lines), lines)
        self.assertTrue(any(l.startswith("FAILED") for l in lines), lines)

    def test_excused_ids_are_only_those_under_the_failed_class(self):
        got = rt.check_ids(["m.C.a", "m.C.b", "m.D.c"], ["m.C.a", "m.C.b", "m.D.c"], ["m.D.c"], ["m.C"])
        self.assertEqual(got, [])
        got = rt.check_ids(["m.C.a", "m.D.c", "m.D.d"], ["m.C.a", "m.D.c", "m.D.d"], ["m.C.a", "m.D.c"], ["m.C"])
        self.assertIn("collected but not run", " | ".join(got))                  # a genuinely lost id still fails

    def test_holder_description_maps_to_the_class_or_module(self):
        class _ErrorHolder:
            description = "setUpClass (test_x.Cls)"
        self.assertEqual(rt.holder_target(_ErrorHolder()), "test_x.Cls")
        self.assertIsNone(rt.holder_target(unittest.TestCase("run")))


class BudgitTestEntryTest(unittest.TestCase):
    def test_dataless_files_stop_the_run_and_name_the_fix(self):
        launched = []
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = bt.main(["--full"], count=lambda p: (3, True), runner=launched.append, repo="/x/repo")
        self.assertEqual(code, bt.EXIT_DATALESS)
        self.assertEqual(launched, [])
        self.assertIn("brctl download /x/repo", buf.getvalue())
        self.assertIn("3 file(s)", buf.getvalue())

    def test_a_clean_repo_runs_the_pool_with_no_flags_typed(self):
        launched = []
        code = bt.main(["--full"], count=lambda p: (0, True), runner=lambda c: launched.append(c) or 0, repo="/x/repo",
                       busy=lambda: (False, ""), lock=False)
        self.assertEqual(code, 0)
        self.assertTrue(launched[0][1].endswith(os.path.join("tools", "runtests.py")))
        self.assertEqual(launched[0][2:], [])

    def test_without_full_it_runs_only_what_changed(self):
        launched = []
        bt.main([], count=lambda p: (0, True), runner=lambda c: launched.append(c) or 0, repo="/x/repo")
        self.assertEqual(launched[0][2:], ["--changed"])

    def test_the_project_test_command_is_the_pool_entry(self):
        with open(os.path.join(ROOT, ".studio", "project.json")) as f:
            self.assertEqual(json.load(f)["test_command"], "python3 tools/budgit_test.py --full")


if __name__ == "__main__":
    unittest.main()
