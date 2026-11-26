"""R-618 round 4: only set-up failures excuse ids; an incomplete iCloud walk warns and still runs."""
import contextlib
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import budgit_test as bt  # noqa: E402
import runtests as rt  # noqa: E402
import test_runtests_risks as risks  # noqa: E402

risks.FIXTURES["test_fx_teardown"] = ("import unittest\n\nclass T(unittest.TestCase):\n    @classmethod\n"
                                      "    def tearDownClass(cls):\n        raise RuntimeError('class tear-down broke')\n"
                                      "    def test_a(self): pass\n")


class Holder:
    def __init__(self, description):
        self.description = description


Holder.__name__ = "_ErrorHolder"


class OnlySetUpExcusesTest(unittest.TestCase):
    def test_setup_holders_map_but_teardown_holders_do_not(self):
        self.assertEqual(rt.holder_target(Holder("setUpClass (test_x.C)")), "test_x.C")
        self.assertEqual(rt.holder_target(Holder("setUpModule (test_x)")), "test_x")
        self.assertIsNone(rt.holder_target(Holder("tearDownClass (test_x.C)")))
        self.assertIsNone(rt.holder_target(Holder("tearDownModule (test_x)")))

    def test_a_lost_id_next_to_a_teardown_error_is_still_named(self):
        # tearDownClass failed (nothing excused) and one id of the same class never ran: ID CHECK must name it
        ids = ["m.C.a", "m.C.b"]
        got = rt.check_ids(ids, ids, ["m.C.a"], [rt.holder_target(Holder("tearDownClass (m.C)"))] if rt.holder_target(Holder("tearDownClass (m.C)")) else [])
        self.assertIn("collected but not run", " | ".join(got))

    def test_a_real_teardownclass_error_is_a_named_error_exit_1_with_no_id_check_line(self):
        code, lines = risks.run_main("--workers", "1", "test_fx_teardown")
        self.assertEqual(code, 1, lines)
        self.assertTrue(any(l.startswith("ERROR: tearDownClass (test_fx_teardown.T)") for l in lines), lines)
        self.assertFalse(any("ID CHECK" in l for l in lines), lines)


class IncompleteWalkWarnsTest(unittest.TestCase):
    def test_an_incomplete_walk_prints_one_warning_names_the_fix_and_still_runs(self):
        launched = []
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = bt.main(["--full"], count=lambda p: (0, False), runner=lambda c: launched.append(c) or 0, repo="/x/repo",
                           busy=lambda: (False, ""), lock=False)
        lines = [l for l in buf.getvalue().splitlines() if "warning" in l]
        self.assertEqual(code, 0)
        self.assertEqual(len(launched), 1)
        self.assertEqual(len(lines), 1, buf.getvalue())
        self.assertIn("brctl download /x/repo", lines[0])
        self.assertIn("could not look at every file", lines[0])

    def test_a_complete_clean_walk_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bt.main(["--full"], count=lambda p: (0, True), runner=lambda c: 0, repo="/x/repo",
                    busy=lambda: (False, ""), lock=False)
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
