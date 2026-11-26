"""R-618 round 6: the pool does not read its own workers as load, and a single odd read does not resize it."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import runtests as rt  # noqa: E402

# a runner (pid 10) with two workers (11, 12) and a grandchild (13); pid 99 is someone else
ROWS = [(1, 0, 0.0), (10, 1, 5.0), (11, 10, 100.0), (12, 10, 100.0), (13, 12, 50.0), (99, 1, 30.0)]


class OwnTreeTest(unittest.TestCase):
    def test_own_tree_counts_pid_and_descendants_only(self):
        self.assertAlmostEqual(rt.own_tree_pct(ROWS, 10, cores=1), 255.0)
        self.assertAlmostEqual(rt.own_tree_pct(ROWS, 10, cores=4), 63.75)

    def test_unknown_pid_is_zero(self):
        self.assertEqual(rt.own_tree_pct(ROWS, 4242, cores=2), 0.0)


class HysteresisTest(unittest.TestCase):
    def feed(self, readings, initial):
        it = iter(readings)
        pick = rt.damped(lambda rest: (next(it), "r"), initial)
        return [pick([])[0] for _ in readings]

    def test_one_busy_read_does_not_shrink(self):
        self.assertEqual(self.feed([4, 1, 4, 4], 4), [4, 4, 4, 4])

    def test_two_busy_reads_shrink(self):
        self.assertEqual(self.feed([1, 1, 1], 4), [4, 1, 1])

    def test_two_quiet_reads_grow_and_one_does_not(self):
        self.assertEqual(self.feed([4, 1, 1, 4, 1, 4, 4], 1)[:1], [1])
        self.assertEqual(self.feed([4, 1, 4, 4], 1), [1, 1, 1, 4])

    def test_alternating_reads_never_flap(self):
        self.assertEqual(self.feed([1, 4, 1, 4, 1, 4], 4), [4] * 6)


class ReversedDirectionTest(unittest.TestCase):
    def test_a_reversal_restarts_the_streak(self):
        it = iter([1, 6, 6])
        pick = rt.damped(lambda rest: (next(it), "r"), 4)
        self.assertEqual([pick([])[0] for _ in range(3)], [4, 4, 6])     # down once, up once: no change; up twice: grow


def idle_machine_table(workers, cores=8, runner=10):
    """ONE ps snapshot of an idle machine whose only load is the runner and its `workers` workers (100 % CPU, 300 MB each)."""
    rows = [(1, 0, 0.0, 1000), (runner, 1, 0.0, 30000)]
    rows += [(100 + i, runner, 100.0, 300 * 1024) for i in range(workers)]
    return rows


def read_idle(workers, free, cores=8):
    return rt.read_load(table=lambda: idle_machine_table(workers, cores), pid=10, free=lambda: free,
                        battery=lambda: None, away=lambda: False, sleep=lambda s: None, cores=cores)


class ReadLoadItselfTest(unittest.TestCase):
    """R-618 round 7: read_load is the tested unit (the verifier removed its own-tree subtraction and nothing failed)."""

    def test_own_workers_are_not_load(self):
        ld = read_idle(7, 4000)
        self.assertEqual(ld.cpu_pct, 0.0)              # 7 workers = 87.5 % of 8 cores, all of it ours
        self.assertEqual(rt.decide_workers(8, 8, None, ld.cpu_pct, ld.free_mb, 300)[0], 7)

    def test_other_load_still_counts(self):
        rows = idle_machine_table(7) + [(999, 1, 200.0, 1000)]
        ld = rt.read_load(table=lambda: rows, pid=10, free=lambda: 9000, battery=lambda: None, away=lambda: False,
                          sleep=lambda s: None, cores=8)
        self.assertAlmostEqual(ld.cpu_pct, 25.0)

    def test_one_ps_snapshot_per_sample_feeds_total_and_own_tree(self):
        calls = []

        def table():
            calls.append(1)
            return idle_machine_table(3)
        rt.read_load(table=table, pid=10, free=lambda: 9000, battery=lambda: None, away=lambda: False,
                     sleep=lambda s: None, cores=8, samples=3)
        self.assertEqual(len(calls), 3)               # three samples, one table call each: no second ps for the tree

    def test_the_cpu_figure_is_a_median_of_samples_not_one_read(self):
        seq = iter([[(1, 0, 400.0, 0)], [(1, 0, 40.0, 0)], [(1, 0, 40.0, 0)]])
        ld = rt.read_load(table=lambda: next(seq), pid=10, free=lambda: 9000, battery=lambda: None,
                          away=lambda: False, sleep=lambda s: None, cores=4, samples=3)
        self.assertAlmostEqual(ld.cpu_pct, 10.0)      # median of 100, 10, 10 percent of 4 cores

    def test_unreadable_table_is_none(self):
        ld = rt.read_load(table=lambda: [], pid=10, free=lambda: 9000, battery=lambda: None, away=lambda: False,
                          sleep=lambda s: None, cores=8)
        self.assertIsNone(ld.cpu_pct)


class OwnMemoryTest(unittest.TestCase):
    def test_seven_workers_do_not_drop_because_their_own_memory(self):
        # 4096 MB free before the pool starts; 7 workers of 300 MB each hold 2100 MB, so the machine reads 1996 MB free
        before = read_idle(0, 4096)
        n0 = rt.decide_workers(8, 8, None, 0.0, before.free_mb, 300)[0]
        running = read_idle(7, 4096 - 2100)
        self.assertGreaterEqual(running.free_mb, 4096 - 1)        # own 2100 MB added back
        n1 = rt.decide_workers(8, 8, None, running.cpu_pct, running.free_mb, 300)[0]
        self.assertEqual((n0, n1), (7, 7))

    def test_without_the_add_back_the_pool_would_have_shrunk(self):
        self.assertLess(rt.decide_workers(8, 8, None, 0.0, 4096 - 2100, 300)[0], 7)


class BatteryAndUnreadableTest(unittest.TestCase):
    def test_battery_halves_and_unknown_does_not(self):
        base = rt.decide_workers(10, 10, None, 0.0, 32000, 300)[0]
        self.assertEqual(rt.decide_workers(10, 10, None, 0.0, 32000, 300, battery=True)[0], base // 2)
        self.assertEqual(rt.decide_workers(10, 10, None, 0.0, 32000, 300, battery=None)[0], base)
        self.assertEqual(rt.decide_workers(10, 10, None, 0.0, 32000, 300, battery=False)[0], base)

    def test_battery_is_read_through_admission(self):
        ld = rt.read_load(table=lambda: idle_machine_table(0), pid=10, free=lambda: 9000, battery=lambda: True,
                          away=lambda: False, sleep=lambda s: None, cores=8, charge=lambda: 25, low_power=lambda: False)
        self.assertTrue(ld.battery)
        self.assertEqual(rt.choose_workers(read=lambda: ld)[1].count("battery 25 %: halved"), 1)

    def _load(self, on_batt, charge, low, away=False):
        return rt.read_load(table=lambda: idle_machine_table(0), pid=10, free=lambda: 9000, battery=lambda: on_batt,
                            away=lambda: away, sleep=lambda s: None, cores=8, charge=lambda: charge, low_power=lambda: low)

    def test_76_percent_discharging_is_not_halved(self):
        ld = self._load(True, 76, False)
        full = rt.choose_workers(read=lambda: self._load(False, None, None))
        got = rt.choose_workers(read=lambda: ld)
        self.assertEqual(got[0], full[0])
        self.assertIn("battery 76 %: full", got[1])

    def test_25_percent_discharging_is_halved(self):
        got = rt.choose_workers(read=lambda: self._load(True, 25, False))
        self.assertIn("battery 25 %: halved", got[1])
        self.assertLess(got[0], rt.choose_workers(read=lambda: self._load(False, None, None))[0])

    def test_low_power_mode_is_halved_even_when_charged(self):
        got = rt.choose_workers(read=lambda: self._load(True, 90, True))
        self.assertIn("Low Power Mode: halved", got[1])

    def test_an_unknown_charge_is_no_change(self):
        ld = self._load(True, None, None)
        self.assertFalse(ld.battery)
        self.assertEqual(rt.choose_workers(read=lambda: ld)[0], rt.choose_workers(read=lambda: self._load(False, None, None))[0])

    def test_away_still_halves_on_a_full_battery(self):
        got = rt.choose_workers(read=lambda: self._load(False, None, None, away=True))
        self.assertIn("away: halved", got[1])

    def test_the_readers_parse_and_never_raise(self):
        self.assertEqual(rt.battery_charge("darwin", run=lambda c: " -InternalBattery-0 (id=1)\t76%; discharging; 5:00"), 76)
        self.assertIsNone(rt.battery_charge("darwin", run=lambda c: None))
        self.assertEqual(rt.battery_charge("linux", listdir=lambda d: ["AC", "BAT0"], readfile=lambda p: "42\n"), 42)
        self.assertIsNone(rt.battery_charge("linux", listdir=lambda d: (_ for _ in ()).throw(OSError())))
        self.assertIsNone(rt.battery_charge("win32"))
        self.assertTrue(rt.low_power_mode("darwin", run=lambda c: " lowpowermode         1"))
        self.assertFalse(rt.low_power_mode("darwin", run=lambda c: " lowpowermode         0"))
        self.assertTrue(rt.low_power_mode("linux", readfile=lambda p: "low-power\n"))
        self.assertIsNone(rt.low_power_mode("win32"))

    def test_unreadable_cpu_is_said_and_does_not_shrink_the_pool(self):
        n, why = rt.decide_workers(8, 8, None, None, 32000, 300)
        self.assertEqual(n, 7)
        self.assertIn("load unreadable", why)


class NoSelfLoadFlapTest(unittest.TestCase):
    """The old behaviour: the sampler counted the runner's own workers as load, so the count shrank, then grew."""

    def test_own_worker_load_does_not_shrink_the_pool(self):
        cores = 8
        state = {"n": 7}               # an idle 8-core machine: 8 cores, one stays free

        def total_cpu_pct():            # what ps -A reports: other load 0, plus 100% per running worker
            return state["n"] * 100.0 / cores

        def own_rows():
            return [(10, 1, 0.0)] + [(100 + i, 10, 100.0) for i in range(state["n"])]

        def read():
            raw = total_cpu_pct()
            return max(0.0, raw - rt.own_tree_pct(own_rows(), 10, cores=cores)), 16000, False

        def choose(rest):
            return rt.decide_workers(cores, cores, None, *read()[:2], None)

        sizes = []
        pick = rt.damped(choose, 7)
        for _ in range(8):
            n, _why = pick([])
            state["n"] = n
            sizes.append(n)
        self.assertEqual(set(sizes), {7}, sizes)

    def test_old_reading_no_longer_drops_to_one(self):
        # the same sampler without the subtraction: 7 workers read as 87.5% load; the count still holds
        n, _ = rt.decide_workers(8, 8, None, 87.5, 16000, None)
        self.assertEqual(n, 7)


if __name__ == "__main__":
    unittest.main()
