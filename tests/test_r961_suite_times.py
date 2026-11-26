"""R-961: suite durations are recorded, a gate run over 60 s is flagged, a gate run takes no full lock. No test sleeps."""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import suitetimes   # noqa: E402
import suitewait    # noqa: E402


def put(project, kind, wall, slowest=("a", "b", "c", "d", "e")):
    return suitetimes.append(project, kind, 3, 40, wall, [{"id": s, "secs": 1.0} for s in slowest], now=1700000000)


class SuiteTimesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_jsonl_line_is_written_with_its_fields(self):
        import runtests
        runtests.record_times("gate", ["m1", "m2"], 12, 3.456, [("t.a", 1.0), ("t.b", 5.0), ("t.c", 2.0), ("t.d", 0.1),
                                                                ("t.e", 0.2), ("t.f", 9.0)], project=self.p, now=1700000000)
        with open(os.path.join(self.p, ".budgit", "suite-times.jsonl")) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        for k in ("kind", "modules", "tests", "wall", "at", "slowest"):
            self.assertIn(k, rec)
        self.assertEqual((rec["kind"], rec["modules"], rec["tests"], rec["wall"]), ("gate", 2, 12, 3.5))
        self.assertEqual([s["id"] for s in rec["slowest"]], ["t.f", "t.b", "t.c", "t.a", "t.e"])
        self.assertRegex(rec["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")

    def test_report_exits_1_when_the_gate_run_took_over_60_s(self):
        put(self.p, "gate", 61.2)
        out = []
        self.assertEqual(suitetimes.report(self.p, out=out.append), 1)
        self.assertIn("gate suite took 61 s (limit 60 s); slowest: a, b, c, d, e", out)

    def test_report_exits_0_when_the_gate_run_was_under_60_s(self):
        put(self.p, "gate", 59.0)
        put(self.p, "full", 2500.0)
        out = []
        self.assertEqual(suitetimes.report(self.p, out=out.append), 0)
        self.assertTrue(any("2500 s" in o for o in out))
        self.assertEqual(suitetimes.report(tempfile.gettempdir() + "/none-r961", out=lambda s: None), 0)

    def test_a_gate_run_does_not_take_the_full_lock_and_a_full_run_does(self):
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o700)
            env = {"BUDGIT_SUITE_LOCK_DIR": d}
            held = suitewait.try_lock(suitewait.lock_paths("proj", env=env)[0])     # a full suite is running
            self.assertIsNotNone(held)
            try:
                cmd = ["python3", "tools/runtests.py", "test_cli"]
                self.assertEqual(suitewait.kind_of(cmd), "gate")
                code = suitewait.run(cmd, "proj", env=env, busy_fn=lambda: self.fail("a gate run does not wait"),
                                     sleep=lambda s: self.fail("a gate run does not wait"), out=lambda s: None,
                                     runner=lambda c, w: 5)
                self.assertEqual(code, 5)
            finally:
                suitewait.unlock(held)
            self.assertEqual(suitewait.kind_of(["python3", "tools/runtests.py"]), "full")
            self.assertTrue(suitewait.lock_name("proj").endswith("-full.lock"))

    def test_the_daily_report_includes_the_line_when_the_gate_run_took_over_60_s(self):
        import daily
        import leak
        put(self.p, "gate", 75.0)
        row = daily._empty_row("proj", False)
        row.update({"counts": {"made": 0, "shut": 0, "pending": 0, "next": 0, "later": 0}, "_next": None})
        slow = suitetimes.slow_gate(self.p)
        row["suite"] = {"secs": round(slow["wall"]), "limit": suitetimes.GATE_LIMIT, "slowest": suitetimes.names(slow)}
        text = daily.render({"projects": [row], "no_project": False, "_names": frozenset()}, lang="en")
        self.assertIn("gate suite took 75 s (limit 60 s); slowest: a, b, c, d, e", text)
        put(self.p, "gate", 10.0)
        self.assertIsNone(suitetimes.slow_gate(self.p))


if __name__ == "__main__":
    unittest.main()
