"""R-618 round 7: the equal-to-serial comparison (tools/pooleq.py) tells equal sets from different ones."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pooleq  # noqa: E402

A = [("m.T.a", "ok"), ("m.T.b", "fail"), ("n.U.c", "skip")]


class CompareTest(unittest.TestCase):
    def test_same_pairs_in_another_order_are_equal(self):
        self.assertEqual(pooleq.compare(A, list(reversed(A))), [])

    def test_a_different_outcome_is_named(self):
        got = pooleq.compare(A, [("m.T.a", "ok"), ("m.T.b", "ok"), ("n.U.c", "skip")])
        self.assertEqual(got, ["outcome differs: m.T.b serial=fail pool=ok"])

    def test_a_missing_and_an_extra_id_are_named(self):
        got = pooleq.compare(A, [("m.T.a", "ok"), ("m.T.b", "fail"), ("n.U.z", "ok")])
        self.assertIn("only in the serial run: n.U.c (skip)", got)
        self.assertIn("only in the pool run: n.U.z (ok)", got)

    def test_a_duplicated_id_is_a_difference(self):
        self.assertTrue(pooleq.compare(A, A + [("m.T.a", "ok")]))

    def test_main_without_modules_is_a_usage_error(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pooleq.main([]), 2)


if __name__ == "__main__":
    unittest.main()
