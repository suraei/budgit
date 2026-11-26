"""Faster suites, slice 1: per-module times on every run, and a partial flag that suppresses the receipt.
Fixtures come from the real writers: run_pool for the times file, write_receipt (through main) for receipts."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtests as rt  # noqa: E402
import suitetimes  # noqa: E402
from test_r1259_receipt import RepoCase  # noqa: E402

FIXTURE = "fixtures.speed_probe"


class ModuleTimesTest(unittest.TestCase):
    def test_module_times_written_by_real_run_pool(self):
        shown = io.StringIO()
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as out, \
                mock.patch.object(rt, "record_times"), contextlib.redirect_stdout(shown):
            path = os.path.join(out, "times.json")
            rc = rt.run_pool(rt.discover([FIXTURE]), 1, 1, False, base, [], None, "gate", (), times_out=path)
            files = [os.path.join(d, f) for d, _, fs in os.walk(base) for f in fs if f.startswith("rec")]
            dump = [(f, open(f).read()[:600]) for f in files]
            self.assertEqual(rc, 0, shown.getvalue() + repr(dump))
            with open(path) as f:
                data = json.load(f)
        self.assertEqual(data["workers"], 1)
        row = data["modules"][FIXTURE]
        self.assertGreater(row["wall"], 0)
        self.assertGreaterEqual(row["cpu_s"], 0)
        self.assertIsInstance(row["cpu_s"], float)


class PartialFlagTest(RepoCase):
    def run_main(self, partial=False, args=("--workers", "1"), seen=None):
        def fake(run):
            if seen is not None:
                with mock.patch.object(rt, "run_pool", side_effect=lambda *a, **k: seen.append(a[7]) or 0):
                    run("b", [])
            return 0
        out = io.StringIO()
        with mock.patch.object(rt, "ROOT", self.repo), mock.patch.object(rt, "discover", return_value=["test_zorb"]), \
                mock.patch.object(rt, "choose_workers", return_value=(1, "test")), mock.patch.object(rt, "supervise", side_effect=fake), \
                mock.patch.object(rt, "PARTIAL", partial), contextlib.redirect_stdout(out):
            self.assertEqual(rt.main(["runtests.py"] + list(args)), 0)
        return out.getvalue()

    def test_partial_run_writes_no_receipt(self):
        out = self.run_main(partial=True)
        self.assertFalse(os.path.exists(self.receipt_file()))
        self.assertIn("receipt not written", out)

    def test_full_uncached_run_still_writes_receipt(self):
        self.run_main(partial=False)
        with open(self.receipt_file()) as f:
            self.assertEqual(json.load(f)["modules"], ["test_zorb"])

    def test_partial_run_recorded_as_gate_not_full(self):
        seen = []
        self.run_main(partial=True, seen=seen)
        self.assertEqual(seen, ["gate"])
        with tempfile.TemporaryDirectory() as proj:
            self.assertTrue(rt.record_times(seen[0], ["a"], 1, 1.0, [], project=proj))
            self.assertEqual([r["kind"] for r in suitetimes.records(proj)], ["gate"])

    def test_first_line_workers_unchanged_when_partial(self):
        for partial in (False, True):
            out = self.run_main(partial=partial)
            self.assertTrue(out.splitlines()[0].startswith("workers 1: "), out)


if __name__ == "__main__":
    unittest.main()
