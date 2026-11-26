"""R-618 round 7: the runner leaves nothing behind (process groups, temp dir) however it ends, and a collection abort
means a stalled worker, not a slow one. Temp dirs and short-lived children only; the clocks are fake."""
import os
import signal
import subprocess
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import runtests as rt  # noqa: E402
from loadcal import load_factor  # noqa: E402

# a fake worker that starts a grandchild, prints the grandchild's pid, then sleeps
WORKER = ("import subprocess, sys, time\n"
          "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
          "print(g.pid, flush=True)\n"
          "time.sleep(120)\n")
STUBBORN = "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + WORKER      # ignores SIGTERM, so does its child


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def gone(pid, within=8.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


@unittest.skipUnless(os.name == "posix", "process groups are POSIX")
class CleanupTest(unittest.TestCase):
    def start(self, live, script=WORKER):
        p = rt.spawn([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        live.append(p)
        return p, int(p.stdout.readline())

    def test_a_worker_gets_its_own_process_group(self):
        live = []
        p, grand = self.start(live)
        try:
            self.assertEqual(os.getpgid(p.pid), p.pid)
            self.assertEqual(os.getpgid(grand), p.pid)
            self.assertNotEqual(os.getpgid(p.pid), os.getpgid(os.getpid()))
        finally:
            rt.reap(live, grace=0.5)

    def test_an_exception_in_the_run_kills_the_grandchild_and_removes_the_base_dir(self):
        seen = {}

        def run(base, live):
            seen["base"] = base
            seen["grand"] = self.start(live)[1]
            self.assertTrue(os.path.isdir(base))
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            rt.supervise(run, grace=0.5)
        self.assertTrue(gone(seen["grand"]), "the grandchild is still running")
        self.assertFalse(os.path.exists(seen["base"]))

    def test_sigterm_to_the_runner_cleans_up_the_same_way(self):
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("signal handlers only in the main thread")
        seen = {}
        before = signal.getsignal(signal.SIGTERM)

        def run(base, live):
            seen["base"] = base
            seen["grand"] = self.start(live)[1]
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(10)                         # the handler raises into the finally long before this ends
        with self.assertRaises(SystemExit) as cm:
            rt.supervise(run, grace=0.5)
        self.assertEqual(cm.exception.code, 128 + signal.SIGTERM)
        self.assertTrue(gone(seen["grand"]), "the grandchild is still running")
        self.assertFalse(os.path.exists(seen["base"]))
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)         # the handler is put back

    def test_a_group_that_ignores_sigterm_is_killed_after_the_grace(self):
        live = []
        p, grand = self.start(live, STUBBORN)
        t0 = time.monotonic()
        rt.reap(live, grace=0.3)
        self.assertTrue(gone(grand), "SIGKILL did not reach the grandchild")
        self.assertIsNotNone(p.poll())
        self.assertLess(time.monotonic() - t0, 6.0 * load_factor())                        # the wait is bounded

    def test_a_normal_return_also_removes_the_base_dir_and_returns_the_value(self):
        seen = {}

        def run(base, live):
            seen["base"] = base
            return 7
        self.assertEqual(rt.supervise(run, grace=0.1), 7)
        self.assertFalse(os.path.exists(seen["base"]))

    def test_the_grace_default_is_5_seconds(self):
        self.assertEqual(rt.TERM_GRACE, 5.0)


class StallTest(unittest.TestCase):
    def watch(self, cpu_seq, window=60.0, unknown=600.0):
        t = [0.0]
        vals = iter(cpu_seq)
        w = rt.StallWatch(cpu=lambda pid: next(vals), clock=lambda: t[0], window=window, unknown_window=unknown, every=0)
        return w, t

    def test_a_worker_that_keeps_gaining_cpu_never_stalls_however_long_it_takes(self):
        w, t = self.watch([i * 0.5 for i in range(100)])
        for _ in range(100):
            t[0] += 30.0                           # 50 minutes of wall clock, far over the old 60 s
            self.assertFalse(w.stalled(1, 0.0))

    def test_a_worker_with_no_cpu_growth_stalls_after_the_window(self):
        w, t = self.watch([1.0] * 10)
        self.assertFalse(w.stalled(1, 0.0))        # first read at t=0
        t[0] = 59.0
        self.assertFalse(w.stalled(1, 0.0))
        t[0] = 61.0
        self.assertTrue(w.stalled(1, 0.0))

    def test_growth_restarts_the_window(self):
        w, t = self.watch([1.0, 1.0, 2.0, 2.0])
        w.stalled(1, 0.0)
        t[0] = 50.0
        self.assertFalse(w.stalled(1, 0.0))
        t[0] = 70.0
        self.assertFalse(w.stalled(1, 0.0))        # gained CPU at 70: window restarts there
        t[0] = 120.0
        self.assertFalse(w.stalled(1, 0.0))        # 50 s since the last gain
        self.assertEqual(rt.STALL_WINDOW, 60.0)

    def test_unreadable_cpu_falls_back_to_a_long_wall_clock(self):
        w, t = self.watch([None] * 5)
        t[0] = 599.0
        self.assertFalse(w.stalled(1, 0.0))
        t[0] = 601.0
        self.assertTrue(w.stalled(1, 0.0))

    def test_ps_time_formats(self):
        self.assertAlmostEqual(rt.parse_cputime("0:01.50"), 1.5)
        self.assertAlmostEqual(rt.parse_cputime("01:02:03"), 3723.0)
        self.assertAlmostEqual(rt.parse_cputime("2-00:00:01"), 172801.0)
        self.assertIsNone(rt.parse_cputime(""))
        self.assertIsNone(rt.parse_cputime("abc"))


if __name__ == "__main__":
    unittest.main()
