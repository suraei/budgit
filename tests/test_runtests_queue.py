"""R-618 round 5: workers pull from one queue and the count is re-read during the run; the entry waits outside the locks
while the machine is busy. Load readings, the clock and every interval are injected: no test reads real load or sleeps."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import budgit_test as bt  # noqa: E402
import runtests as rt  # noqa: E402
import test_runtests_risks as risks  # noqa: E402


class Sim:
    """A fake clock whose sleep advances it, and fake workers that finish `dur` seconds after they start."""

    def __init__(self, dur=40.0):
        self.t, self.dur = 0.0, dur
        self.handles, self.launched, self.alive_at_launch, self.said, self.asked = [], [], [], [], []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def launch(self, module):
        self.alive_at_launch.append(sum(1 for h in self.handles if h.poll() is None))
        h = Handle(self, self.dur)
        self.handles.append(h)
        self.launched.append(module)
        return h

    def run(self, queue, choose, initial, interval=15.0):
        def asking(rest):
            self.asked.append(self.t)
            return choose(rest)
        return rt.run_queue(queue, self.launch, asking, initial, 99, clock=self.clock, sleep=self.sleep,
                            interval=interval, poll=1.0, say=self.said.append)


class Handle:
    def __init__(self, sim, dur):
        self.sim, self.end, self.killed = sim, sim.t + dur, False

    def poll(self):
        return 0 if self.sim.t >= self.end else None

    def terminate(self):
        self.killed = True

    kill = terminate


MODS = ["m%d" % i for i in range(8)]


class QueueGrowthTest(unittest.TestCase):
    def test_the_pool_grows_from_1_to_4_when_the_load_drops_mid_run(self):
        s = Sim()
        peak, launched, _ = s.run(MODS, lambda rest: (4, "calm") if s.t >= 30 else (1, "busy"), 1)
        self.assertEqual(peak, 4)
        self.assertEqual(s.said, ["workers 1 -> 4: calm"])

    def test_no_growth_while_the_machine_stays_busy(self):
        s = Sim()
        peak, _, _ = s.run(MODS, lambda rest: (1, "busy"), 1)
        self.assertEqual(peak, 1)
        self.assertEqual(s.said, [])

    def test_a_rising_load_starts_no_new_worker_and_kills_none(self):
        s = Sim()
        peak, _, _ = s.run(MODS, lambda rest: (4, "calm") if s.t < 20 else (1, "busy"), 4)
        self.assertEqual(peak, 4)
        self.assertEqual(s.said, ["workers 4 -> 1: busy"])
        self.assertFalse(any(h.killed for h in s.handles))
        self.assertTrue(all(h.poll() == 0 for h in s.handles))          # every running worker was left to finish
        # after the drop to 1, a worker starts only when none is alive
        self.assertEqual(s.alive_at_launch[:4], [0, 1, 2, 3])
        self.assertTrue(all(a == 0 for a in s.alive_at_launch[4:]), s.alive_at_launch)

    def test_every_queued_module_is_launched_exactly_once(self):
        s = Sim()
        s.run(MODS, lambda rest: (4, "calm") if s.t >= 30 else (2, "busy"), 2)
        self.assertEqual(sorted(s.launched), sorted(MODS))

    def test_the_count_is_re_read_only_on_the_interval(self):
        s = Sim()
        s.run(MODS, lambda rest: (1, "busy"), 1, interval=15.0)
        self.assertTrue(s.asked and s.asked[0] >= 15.0, s.asked)
        self.assertTrue(all(b - a >= 15.0 for a, b in zip(s.asked, s.asked[1:])), s.asked)

    def test_the_production_default_interval_is_15_seconds(self):
        s = Sim()
        rt.run_queue(MODS, s.launch, lambda rest: s.asked.append(s.t) or (1, "busy"), 1, 99, clock=s.clock,
                     sleep=s.sleep, poll=1.0, say=s.said.append)         # no interval passed: the default is what runs
        self.assertEqual(s.asked[0], 15.0)
        self.assertEqual(rt.QUEUE_INTERVAL, 15.0)

    def test_the_decided_count_is_capped_by_the_module_count(self):
        s = Sim()
        peak, _, _ = rt.run_queue(MODS[:2], s.launch, lambda rest: (50, "huge"), 1, 2, clock=s.clock, sleep=s.sleep,
                                  interval=5.0, poll=1.0, say=s.said.append)
        self.assertEqual(peak, 2)


def rows_for(total_pct_of_cores, cores):
    """One ps snapshot whose summed %CPU is `total_pct_of_cores` of `cores` (nothing of it is the runner's own)."""
    return [(1, 0, total_pct_of_cores * cores, 1024)]


def reading(pct, cores, free):
    """A Load as read_load builds it on a `cores`-CPU host at `pct` % CPU with `free` MB free: host-independent."""
    return rt.read_load(table=lambda: rows_for(pct, cores), pid=424242, free=lambda: free, battery=lambda: None,
                        away=lambda: False, sleep=lambda s: None, cores=cores)


class BusyNowTest(unittest.TestCase):
    """R-618 round 7: the busy rule is the owner's (admission.busy_reason): 85 % of all cores, 1 GB. Every reading is
    injected, so these pass on a 2-CPU host."""

    def test_8_cpus_at_70_percent_are_not_busy(self):
        self.assertFalse(rt.busy_now(env={}, read=lambda: reading(70, 8, 8000))[0])

    def test_4_cpus_at_30_percent_are_not_busy(self):
        self.assertFalse(rt.busy_now(env={}, read=lambda: reading(30, 4, 8000))[0])

    def test_1700_mb_free_at_zero_cpu_is_not_busy(self):
        self.assertFalse(rt.busy_now(env={}, read=lambda: reading(0, 8, 1700))[0])

    def test_r889b_900_mb_free_plus_inactive_is_not_busy_but_memory_pressure_is(self):
        self.assertFalse(rt.busy_now(env={}, read=lambda: reading(0, 8, 900))[0])
        load = rt.read_load(table=lambda: rows_for(0, 8), pid=424242, free=lambda: 9000, battery=lambda: None,
                            away=lambda: False, sleep=lambda s: None, cores=8, mem=lambda: {"pressure": 2})
        self.assertEqual(rt.busy_now(env={}, read=lambda: load), (True, "memory pressure warn"))

    def test_r889b_the_machine_total_comes_from_kernel_counters_minus_the_own_tree(self):
        rows = [(1, 0, 100.0, 1024), (424242, 0, 200.0, 2048)]          # ps: own tree 200 % of 8 cores = 25 %
        load = rt.read_load(table=lambda: rows, pid=424242, free=lambda: 9000, battery=lambda: None, away=lambda: False,
                            sleep=lambda s: None, cores=8, system=lambda: 95.0, samples=1)
        self.assertAlmostEqual(load.cpu_pct, 70.0)                     # kernel 95 % minus own 25 %, not ps 37.5 % - 25 %

    def test_cpu_85_percent_is_the_edge(self):
        self.assertTrue(rt.busy_now(env={}, read=lambda: reading(86, 8, 8000))[0])
        self.assertFalse(rt.busy_now(env={}, read=lambda: reading(84, 8, 8000))[0])

    def test_the_rule_is_admissions_not_a_copy(self):
        import admission
        self.assertEqual(admission.BUSY_CPU_PCT, 85)
        self.assertEqual(admission.BUSY_FREE_MB, 1024)
        for pct, free in ((86, 8000), (84, 8000), (0, 900), (0, 1700)):
            busy, _ = rt.busy_now(env={}, read=lambda: reading(pct, 8, free))
            self.assertEqual(busy, admission.busy_reason(None, pct) is not None)        # free+inactive never decides

    def test_an_override_is_never_busy(self):
        self.assertFalse(rt.busy_now(env={"BUDGIT_TEST_WORKERS": "1"}, read=lambda: reading(99, 8, 100))[0])

    def test_a_load_tuple_with_only_the_leading_fields_still_works(self):
        self.assertTrue(rt.busy_now(env={}, read=lambda: (90.0, 4000, False))[0])
        self.assertFalse(rt.busy_now(env={}, read=lambda: (5.0, 8000, False))[0])


class MemoryBusyRuleTest(unittest.TestCase):
    """R-889b: free plus inactive memory only sizes the pool; memory signals (pressure, swap-ins) make it busy."""

    def test_r889b_low_reclaimable_memory_sizes_the_pool_but_is_not_called_busy(self):
        n, why = rt.decide_workers(8, 8, free_mb=500)
        self.assertEqual(n, 2)                   # the floor; the runner waits for memory before it gets here
        self.assertNotIn("busy", why)

    def test_r889b_memory_pressure_is_said_but_does_not_force_one_worker(self):
        n, why = rt.decide_workers(8, 8, free_mb=9000, mem={"pressure": 4})
        self.assertEqual(n, 7)
        self.assertIn("memory busy: memory pressure critical", why)

    def test_r889b_low_reclaimable_memory_alone_is_not_busy(self):
        self.assertFalse(rt.busy_now(env={}, read=lambda: (5.0, 900, False))[0])

    def test_r889b_the_memory_figure_comes_from_admissions_one_reader(self):
        sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
        import admit
        import admission
        self.assertFalse(hasattr(admit, "free_mb"))                    # admit keeps no copy (R-889b round 2)
        out = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:      1000.\n"
               "Pages active:    9999.\nPages inactive:  2000.\nPages speculative: 500.\n")
        self.assertEqual(admission.free_mb("darwin", run=lambda c: out), 3500 * 16384 // (1024 * 1024))
        data = "MemTotal: 8000000 kB\nMemAvailable: 2097152 kB\n"
        self.assertEqual(admission.free_mb("linux", readfile=lambda p: data), 2048)


class WaitOutsideTheLockTest(unittest.TestCase):
    def setUp(self):
        self.events, self.t, self.out = [], [0.0], []

    def clock(self):
        return self.t[0]

    def sleep(self, s):
        self.events.append("sleep")
        self.t[0] += s

    def entry(self, busy, limit=600.0):
        orig = bt.locked

        def spy(cmd, project, **k):
            self.events.append("lock")
            return orig(cmd, project, **k)
        bt.locked = spy
        try:
            return bt.main(["--full"], count=lambda p: (0, True), repo="/x/repo", busy=busy, sleep=self.sleep,
                           clock=self.clock, poll=30.0, limit=limit,
                           runner=lambda c: self.events.append("run") or self.out.append(c) or 0)
        finally:
            bt.locked = orig

    def test_the_locks_are_taken_after_the_wait_not_during_it(self):
        readings = iter([(True, "90% busy"), (True, "90% busy"), (False, "calm")])
        self.assertEqual(self.entry(lambda: next(readings)), 0)
        self.assertEqual(self.events, ["sleep", "sleep", "lock", "run"])
        cmd = self.out[0]
        if cmd[0] == "lockf":                      # lockf exists on this machine: only the project lock wraps the runner
            self.assertEqual(cmd.count("lockf"), 1)
            self.assertTrue(cmd[4].endswith("budgit-suite-repo-full.lock"), cmd)

    def test_only_the_project_lock_is_taken_and_it_is_the_one_suite_wait_takes(self):
        import suitewait
        cmd = bt.locked(["run"], "proj", tmp="/t", which=lambda n: "/usr/bin/lockf")
        self.assertEqual(cmd, ["lockf", "-k", "-t", "60", "/t/" + suitewait.lock_name("proj"), "run"])
        self.assertNotIn("full-suite", " ".join(cmd))
        self.assertEqual(suitewait.lock_name("proj"), "budgit-suite-proj-full.lock")
        self.assertEqual(bt.LOCK_WAIT, 60)
        self.assertEqual(bt.EX_TEMPFAIL, 75)

    def test_a_lock_timeout_goes_back_to_the_outside_the_lock_wait(self):
        import contextlib
        import io
        codes = iter([bt.EX_TEMPFAIL, 0])
        order = []

        def runner(c):
            order.append("run")
            return next(codes) if c[0] == "lockf" else 0

        def busy():
            order.append("busy")
            return (False, "calm")
        orig = bt.locked
        bt.locked = lambda cmd, project, **k: ["lockf"] + cmd           # lockf present on every host for this test
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = bt.main(["--full"], count=lambda p: (0, True), repo="/x/repo", busy=busy, sleep=self.sleep,
                             clock=self.clock, runner=runner)
        finally:
            bt.locked = orig
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["busy", "run", "busy", "run"])      # timed out, waited outside again, then ran

    def test_the_wait_defaults_are_30_s_and_10_min(self):
        sleeps = []
        self.t[0] = 0.0
        bt.wait_calm(lambda: (True, "x"), lambda s: sleeps.append(s) or self.sleep(s), self.clock, say=lambda s: None)
        self.assertEqual(sleeps, [30.0] * 20)
        self.assertEqual((bt.WAIT_POLL, bt.WAIT_LIMIT), (30.0, 600.0))

    def test_a_calm_machine_does_not_wait(self):
        self.entry(lambda: (False, "calm"))
        self.assertEqual(self.events, ["lock", "run"])

    def test_after_10_minutes_it_runs_anyway_and_says_why(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self.entry(lambda: (True, "92% busy")), 0)
        self.assertEqual(self.events.count("sleep"), 20)                   # 600 s at one look per 30 s
        self.assertEqual(self.events[-2:], ["lock", "run"])
        self.assertIn("still busy after 10 min; running anyway", buf.getvalue())
        self.assertIn("92% busy", buf.getvalue())

    def test_wait_calm_reports_whether_it_ended_calm(self):
        self.assertTrue(bt.wait_calm(lambda: (False, ""), self.sleep, self.clock, say=lambda s: None))
        self.assertFalse(bt.wait_calm(lambda: (True, "x"), self.sleep, self.clock, poll=30.0, limit=60.0, say=lambda s: None))

    def test_without_lockf_the_command_is_unchanged(self):
        self.assertEqual(bt.locked(["a"], "p", which=lambda n: None), ["a"])


class PoolEndToEndTest(unittest.TestCase):
    def test_the_summary_prints_duration_and_peak_workers_and_every_id_runs_once(self):
        code, lines = risks.run_main("--workers", "2", "test_fx_ok", "test_fx_skipdef")
        self.assertEqual(code, 0, lines)
        self.assertTrue(any(l.startswith("Ran 2 tests") for l in lines), lines)
        last = [l for l in lines if l.startswith("duration ")]
        self.assertEqual(len(last), 1, lines)
        self.assertRegex(last[0], r"^duration \d+\.\d s, peak workers [12]$")
        self.assertFalse(any("ID CHECK" in l for l in lines), lines)
        self.assertTrue(lines[0].startswith("workers 2:"), lines[:1])


if __name__ == "__main__":
    unittest.main()
