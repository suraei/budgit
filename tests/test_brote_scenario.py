"""Brote's scenario runner (tools/brote/float/scenario): the trace checks and the test-window helper. Nothing here opens a window.

Run from the repo root:  python3 -m unittest discover -s tests -p 'test_brote_scenario.py'
The checks are fed recorded traces, a passing and a failing one per step; the helper is only compiled.
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brotecache

ROOT = Path(__file__).resolve().parents[1]
SCEN = ROOT / "tools" / "brote" / "float" / "scenario"
SWIFTC = shutil.which("swiftc")
MAC = sys.platform == "darwin" and SWIFTC is not None

spec = importlib.util.spec_from_file_location("scenario", SCEN / "scenario.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

A, B, C = 101, 202, 303


def look(t, x, y, host, support="stay", plats=2, mood="contento", at=None):
    e = {"t": t, "ev": "look", "pos": [x, y], "host": host, "support": support, "plats": plats, "mood": mood}
    if at:
        e["hostAt"] = list(at)
    return e


class TraceChecks(unittest.TestCase):
    def test_lands_on_the_last_landing_counts(self):
        tr = [{"t": 1, "ev": "land", "on": A, "pos": [0, 500]}, {"t": 2, "ev": "land", "on": "floor", "pos": [0, 0]}]
        self.assertTrue(sc.lands_on(tr, "floor"))
        self.assertFalse(sc.lands_on(tr, A))
        self.assertFalse(sc.lands_on([], A))

    def test_rides_with_the_same_delta_and_no_fall(self):
        tr = [look(0, 300, 500, A, at=(250, 500)), look(1, 420, 540, A, at=(370, 540))]
        self.assertTrue(sc.rides(tr, A, 120, 40))
        self.assertFalse(sc.rides([tr[0], look(1, 300, 500, A, at=(370, 540))], A, 120, 40), "the window moved, he did not")
        self.assertFalse(sc.rides([tr[0], look(1, 300, 500, A, at=(250, 500))], A, 120, 40), "the window never moved")
        drawn_small = [look(0, 300, 500, A, at=(250, 500)), look(1, 408, 536, A, at=(358, 536))]
        self.assertTrue(sc.rides(drawn_small, A, 120, 40), "a window tool drew the window at 0.9: its own move is the measure")
        self.assertFalse(sc.rides(tr + [{"t": 1.5, "ev": "fall", "cause": "slid", "pos": [420, 540]}], A, 120, 40))

    def test_cover_hop_and_fly(self):
        hop = [look(0, 300, 500, A), {"t": 0.6, "ev": "route", "why": "cover-hop", "legs": []}, {"t": 1.2, "ev": "land", "on": B, "pos": [300, 560]}]
        self.assertTrue(sc.STEPS[3]["check"](hop, {"A": A, "B": B}))
        fell = [look(0, 300, 500, A, "covered:%d" % B), {"t": 0.1, "ev": "fall", "cause": "covered", "pos": [300, 500]}, {"t": 1.4, "ev": "land", "on": "floor", "pos": [300, 0]}]
        self.assertFalse(sc.STEPS[3]["check"](fell, {"A": A, "B": B}), "a happy Brote under a low window hops, he does not fall")
        self.assertTrue(sc.STEPS[4]["check"](fell, {"A": A, "B": B}), "a so-so Brote falls")
        self.assertTrue(sc.STEPS[5]["check"](fell, {"A": A, "B": B}), "a happy Brote under a high window falls")
        fly = [look(0, 300, 500, A), {"t": 0.6, "ev": "route", "why": "cover-fly", "legs": []}, {"t": 2, "ev": "land", "on": B, "pos": [300, 750]}]
        self.assertTrue(sc.STEPS[6]["check"](fly, {"A": A, "B": B}))

    def test_a_fall_never_lands_on_the_spot_it_left(self):
        # her hand test, 2026-10-25 15:15:11: covered, the window moved away during the pause, he "landed" where he stood
        bad = [look(0, 723, 834, A), {"t": 0.1, "ev": "fall", "cause": "covered", "pos": [723, 834]}, {"t": 0.63, "ev": "land", "on": A, "pos": [723, 834]}]
        self.assertFalse(sc.no_false_landing(bad))
        good = [look(0, 723, 834, A), {"t": 0.1, "ev": "fall", "cause": "covered", "pos": [723, 834]}, {"t": 0.6, "ev": "cancel", "cause": "covered"}]
        self.assertTrue(sc.no_false_landing(good))
        real = [look(0, 723, 834, A), {"t": 0.1, "ev": "fall", "cause": "covered", "pos": [723, 834]}, {"t": 1.5, "ev": "land", "on": "floor", "pos": [723, 93]}]
        self.assertTrue(sc.no_false_landing(real))

    def test_dizzy_starts_before_the_landing(self):
        self.assertTrue(sc.dizzy_on_first_hit([{"t": 0.4, "ev": "act", "name": "dizzy"}, {"t": 1.6, "ev": "land", "on": "floor", "pos": [0, 0]}]))
        self.assertFalse(sc.dizzy_on_first_hit([{"t": 1.6, "ev": "land", "on": "floor", "pos": [0, 0]}, {"t": 1.7, "ev": "act", "name": "dizzy"}]))

    def test_walks_the_whole_ledge(self):
        self.assertTrue(sc.walks_ledge([look(i, x, 500, A) for i, x in enumerate([250, 400, 600, 760])], A, 249, 769))
        self.assertFalse(sc.walks_ledge([look(i, x, 500, A) for i, x in enumerate([400, 420, 440])], A, 249, 769))
        self.assertFalse(sc.walks_ledge([look(0, 100, 500, A), look(1, 760, 500, A)], A, 249, 769), "he left the ledge")
        self.assertTrue(sc.walks_ledge([look(0, 1352, 93, None)] + [look(i + 1, x, 500, A) for i, x in enumerate([250, 760])], A, 249, 769),
                        "where he stood before he was put on the window does not count")
        self.assertFalse(sc.walks_ledge([look(0, 1352, 500, None)], A, 249, 769), "never on the window: no walk")

    def test_pacing_checks(self):
        act = lambda t, n: {"t": t, "ev": "act", "name": n}
        self.assertTrue(sc.calm_gaps([act(0, "am_tea"), act(0.4, "hearts"), act(0.5, "am_rope"), act(0.9, "am_code")], 0.25), "click reactions are not idle animations")
        self.assertFalse(sc.calm_gaps([act(0, "am_tea"), act(0.1, "am_rope")], 0.25))
        self.assertTrue(sc.screens_hold([act(0, "pc_out"), act(0.05, "pc_think"), act(0.45, "pc_type"), act(0.9, "pc_read"), act(1.3, "pc_run")], 0.35),
                        "taking the laptop out is not a screen")
        self.assertFalse(sc.screens_hold([act(0, "pc_type"), act(0.1, "pc_read")], 0.35))
        self.assertTrue(sc.pokes_vary([act(i, n) for i, n in enumerate(["hearts", "hop", "wave", "hop", "spin"])]))
        self.assertFalse(sc.pokes_vary([act(i, n) for i, n in enumerate(["hearts", "hearts", "wave", "hop", "spin"])]))

    def test_trip_landed_waits_for_the_landing(self):
        trip = {"t": 4, "ev": "route", "why": "trip", "to": B, "legs": []}
        self.assertFalse(sc.trip_landed([{"t": 1, "ev": "land", "on": A, "pos": [0, 500]}, trip]), "a landing before the trip does not count")
        self.assertTrue(sc.trip_landed([trip, {"t": 5, "ev": "land", "on": B, "pos": [0, 500]}]))

    def test_faces_claude_with_a_readable_sign(self):
        self.assertTrue(sc.faces_claude([{"t": 1, "ev": "frame", "flip": True, "call": "ask", "glyph_ok": True}], claude_left=True))
        self.assertFalse(sc.faces_claude([{"t": 1, "ev": "frame", "flip": True, "call": "ask", "glyph_ok": False}], claude_left=True))

    def test_window_names_become_numbers(self):
        self.assertEqual(sc.resolve("place A 200", {"A": 9549}), "place 9549 200")
        self.assertEqual(sc.resolve("claude C", {"C": 77}), "claude 77")
        self.assertEqual(sc.resolve("drop 500 700", {"A": 9549}), "drop 500 700")

    def test_every_step_is_described_and_checked(self):
        ids = [s["id"] for s in sc.STEPS]
        self.assertEqual(len(ids), len(set(ids)))
        for want in ["1", "2", "3", "4a", "4b", "5a", "5b", "5c", "6", "7", "8", "9", "10", "11", "12", "13", "14"]:
            self.assertIn(want, ids)
        for s in sc.STEPS:
            self.assertTrue(s["words"].endswith("."), s["id"])
            self.assertNotRegex(s["words"], r"\bV\d+\b", "plain words, never a test code")
            self.assertTrue(callable(s["check"]))


class Hygiene(unittest.TestCase):
    def test_no_permission_and_no_open(self):
        for f in ("winhelper.swift", "scenario.py"):
            text = (SCEN / f).read_text()
            for banned in ["CGWindowListCreateImage", "SCShareableContent", "kCGWindowName", "kCGWindowOwnerName", "AXIsProcessTrusted",
                           "CGRequestScreenCaptureAccess", "osascript", "NSAppleScript", "screencapture", "['open'", "/usr/bin/open", "NSWorkspace.shared.open"]:
                self.assertNotIn(banned, text, f"{f}: {banned}")

    def test_runner_refuses_while_another_brote_runs(self):
        self.assertIn("other_brote_running()", (SCEN / "scenario.py").read_text())

    @unittest.skipUnless(MAC, "needs macOS and swiftc")
    @brotecache.skip_unless_changed("test_brote_scenario.test_helper_compiles", [SCEN / "winhelper.swift", Path(__file__).resolve()])
    def test_helper_compiles(self):
        # R-870: compiled once per (source, swiftc version, flags), shared through tests/brotecache.py
        r = brotecache.cached_swiftc(["-O", "winhelper.swift", "-framework", "AppKit"], [SCEN / "winhelper.swift"], SCEN, swiftc=SWIFTC, out_name="winhelper")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
