"""R-889b: `budgit busy` reads kernel CPU counters, memory pressure, swap-ins, the compressor and truly free memory.

Replay of 9 Nov 2026 16:25 on her Mac (8 cores, 16 GB): CPU 91-95 % used, 158 MB unused, compressor 5.6 GB, swap-ins active,
load 17-23, and the old rule said "not busy" twice. Every reading here is a fake reader output; no test reads the live machine
except the darwin-only smoke at the end, which only checks that the real readers return sane values without crashing."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import sys
import types
import unittest
from unittest import mock

import admission
import admit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
PAGE = 16384                                  # Apple Silicon page size, as her vm_stat prints it


def mac(free_true, compressor, swapins_pages, t, pressure=1, total=16384):
    """One macOS mem_reading picture as admission.mem_reading returns it."""
    return {"pressure": pressure, "psi": None, "winload": None, "free_true_mb": free_true, "total_mb": total,
            "compressor_mb": compressor, "swapins_bytes": swapins_pages * PAGE, "page": PAGE, "t": t, "unknown": []}


# 16:25: the swap-in rate was not measured ("active"); 120 pages/s of 16 KB (1.9 MB/s) over the window is the fixture.
# The pressure level was not measured either: level 1 (normal) is the worst case for detection, so it is used.
BUSY_1625 = (mac(158, 5734, 12_000_000, 0.0), mac(158, 5734, 12_000_000 + 120 * 60, 60.0))
CPU_1625 = 93.0
RECLAIMABLE_1625 = 3000                       # free + inactive: above 1 GB, which is why the old rule said "not busy"
# 16:49 the same day, live on her Mac (vm_stat, sysctl, top): calm, 21-34 % CPU, pressure normal, 432 MB truly free.
CALM_1649 = (mac(432, 1425, 12_667_972, 0.0), mac(432, 1425, 12_667_972, 60.0))
VM_STAT_1649 = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    27652.
Pages active:                                 368136.
Pages inactive:                               362917.
Pages speculative:                              5433.
Pages occupied by compressor:                  91261.
Swapins:                                    12667972.
"""


def load_cli():
    loader = importlib.machinery.SourceFileLoader("budgit_cli_r889b", BUDGIT)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli_r889b", loader))
    loader.exec_module(mod)
    return mod


class FakeCPU:
    """Stands in for admission.SystemCPU: always the given % of all cores, read from kernel counters."""
    value = 0.0
    source = "kernel"

    def __init__(self, **kw):
        pass

    def __call__(self):
        return FakeCPU.value


def run_busy(readings, cpu, reclaimable):
    """`budgit busy --window 1` with every reader faked; -> (exit code, printed line). `reclaimable` (free + inactive) is
    never read by the busy check any more (R-889b round 2); the argument stays to show the replay's figure."""
    mod = load_cli()
    seq = iter(readings)
    FakeCPU.value = cpu
    out = io.StringIO()
    self_check = reclaimable                       # documented figure only
    with mock.patch.object(admission, "mem_reading", lambda: next(seq)), mock.patch.object(admission, "SystemCPU", FakeCPU), \
            contextlib.redirect_stdout(out):
        code = mod.cmd_busy(types.SimpleNamespace(window=1))
    return code, out.getvalue().strip()


def reason(cpu=None, mem=None, free=None):
    return admission.busy_reason(free, cpu, mem)


class ReplayOf1625(unittest.TestCase):
    def test_replay_1625_budgit_busy_exits_10(self):
        code, line = run_busy(BUSY_1625, CPU_1625, RECLAIMABLE_1625)
        self.assertEqual(code, 10, line)
        self.assertTrue(line.startswith("busy: "), line)

    def test_replay_1625_each_signal_fires_alone(self):
        """Literal 16:25 figures, one signal at a time: a mutated threshold on any of them turns one of these red."""
        signals = admission.mem_signals(*BUSY_1625)
        self.assertIn("median CPU 93 %", reason(cpu=93.0))
        self.assertIn("swap-in 120 pages/s", admission.memory_reason({k: signals[k] for k in ("swapin_mb_s", "swapin_pages_s")}))
        self.assertIn("compressor 5.6 GB of 16 GB RAM", reason(mem={"compressor_mb": 5734, "total_mb": 16384}))
        self.assertIn("truly free memory 158 MB", reason(mem={"free_true_mb": 158, "total_mb": 16384}))

    def test_old_rule_missed_it_free_plus_inactive_above_1_gb(self):
        """Root cause, memory half: the reclaimable figure alone (free + inactive) is above 1 GB at 16:25."""
        self.assertIsNone(admission.busy_reason(RECLAIMABLE_1625, None))


class CalmMachines(unittest.TestCase):
    def test_her_calm_mac_1649_exits_0(self):
        code, line = run_busy(CALM_1649, 28.0, 5000)
        self.assertEqual((code, line), (0, "not busy"))

    def test_truly_free_is_skipped_where_the_kernel_reports_pressure(self):
        """The one design choice (FREE_TRUE_ONLY_WITHOUT_PRESSURE): 432 MB free at pressure normal is a calm Mac."""
        self.assertIsNone(reason(mem={"pressure": 1, "free_true_mb": 432, "total_mb": 16384}))
        self.assertIsNotNone(reason(mem={"pressure": None, "free_true_mb": 432, "total_mb": 16384}))

    def test_64_gb_idle_machine_exits_0(self):
        idle = ({"pressure": 1, "free_true_mb": 40000, "total_mb": 65536, "compressor_mb": 0, "swapins_bytes": 0, "page": PAGE,
                 "t": 0.0, "unknown": []},)
        idle = (idle[0], dict(idle[0], t=60.0))
        self.assertEqual(run_busy(idle, 3.0, 50000)[0], 0)

    def test_truly_free_floor_scales_with_ram_and_keeps_1_gb_minimum(self):
        self.assertEqual(admission.free_true_limit(8192), 1024)
        self.assertEqual(admission.free_true_limit(None), 1024)
        self.assertEqual(admission.free_true_limit(65536), 1966)
        self.assertIsNotNone(reason(mem={"free_true_mb": 1500, "total_mb": 65536}))      # no pressure reading: 1.5 GB of 64 is low
        self.assertIsNone(reason(mem={"free_true_mb": 1500, "total_mb": 8192}))


class CompressorLimitFollowsPressureLevel(unittest.TestCase):
    """R-1186: 50 % of RAM while the kernel gives a calm level, 30 % when it gives none."""
    def test_pressure_1_compressor_34_percent_no_swapin_is_not_busy(self):
        mem = {"pressure": 1, "compressor_mb": 5570, "total_mb": 16384, "swapin_mb_s": 0.0, "free_true_mb": 200}
        self.assertIsNone(admission.memory_reason(mem))

    def test_pressure_1_compressor_73_percent_is_busy_and_says_50(self):
        text = admission.memory_reason({"pressure": 1, "compressor_mb": 11960, "total_mb": 16384, "swapin_mb_s": 0.0})
        self.assertIn("limit 50 %", text)

    def test_9_nov_replay_is_still_busy(self):
        code, line = run_busy(BUSY_1625, CPU_1625, RECLAIMABLE_1625)
        self.assertEqual(code, 10, line)

    def test_no_pressure_level_compressor_34_percent_is_busy_and_says_30(self):
        text = admission.memory_reason({"pressure": None, "compressor_mb": 5570, "total_mb": 16384, "swapin_mb_s": 0.0})
        self.assertIn("limit 30 %", text)


class TwoCpuContainer(unittest.TestCase):
    def ticks(self, usages, nows, quota=2):
        files, clock = iter(usages), iter(nows)
        return lambda: admission.cpu_ticks("linux", readfile=lambda p: "usage_usec %d\n" % next(files), quota=quota,
                                           now=lambda: next(clock))

    def test_cpu_is_judged_on_the_container_quota_not_the_host(self):
        busy = admission.SystemCPU(ticks=self.ticks([0, 950000], [100.0, 100.5]), sleep=lambda s: None)
        self.assertAlmostEqual(busy(), 95.0, places=3)
        half = admission.SystemCPU(ticks=self.ticks([0, 500000], [100.0, 100.5]), sleep=lambda s: None)
        self.assertAlmostEqual(half(), 50.0, places=3)
        self.assertIsNotNone(reason(cpu=95.0))
        self.assertIsNone(reason(cpu=50.0))

    def test_host_proc_stat_counts_idle_and_iowait_as_not_busy(self):
        stats = iter(["cpu 100 0 100 700 100 0 0 0 0 0\n", "cpu 190 0 190 710 110 0 0 0 0 0\n"])
        cpu = admission.SystemCPU(ticks=lambda: admission.cpu_ticks("linux", readfile=lambda p: next(stats)), sleep=lambda s: None)
        self.assertAlmostEqual(cpu(), 90.0, places=3)                    # 180 busy of 200

    def linux_mem(self, psi, memfree_kb, pswpin):
        files = {"/proc/pressure/memory": "some avg10=%.2f avg60=1.00 avg300=0.50 total=1\nfull avg10=0.00 avg60=0 avg300=0 total=0\n" % psi,
                 "/proc/meminfo": "MemTotal:        2097152 kB\nMemFree:          %d kB\nMemAvailable:   1500000 kB\n" % memfree_kb,
                 "/proc/vmstat": "pgpgin 1\npswpin %d\npswpout 3\n" % pswpin}

        def rd(path):
            if path not in files:
                raise OSError(path)
            return files[path]
        return admission.mem_reading("linux", readfile=rd, now=lambda: 0.0, page=4096)

    def test_container_memory_psi_decides_and_true_free_is_skipped_while_psi_is_readable(self):
        calm = self.linux_mem(2.0, 300 * 1024, 5)
        self.assertEqual((calm["psi"], calm["free_true_mb"], calm["total_mb"], calm["unknown"]), (2.0, 1464, 2048, []))      # MemAvailable, not MemFree
        self.assertIsNone(admission.memory_reason(calm))
        self.assertEqual(admission.memory_reason(self.linux_mem(25.0, 300 * 1024, 5)), "memory pressure: some avg10 25.0 % (limit 10 %)")

    def test_no_psi_falls_back_to_truly_free_and_says_pressure_unknown(self):
        m = admission.mem_reading("linux", readfile=lambda p: (_ for _ in ()).throw(OSError(p)), now=lambda: 0.0, page=4096)
        self.assertIn("psi", m["unknown"])
        self.assertIn("memory pressure", admission.calm_reason(m))


class BusyWindowsPC(unittest.TestCase):
    def test_memory_load_fires_and_names_itself(self):
        self.assertEqual(reason(mem={"winload": 95, "total_mb": 16384}), "memory load 95 % (limit 90 %)")
        self.assertIsNone(reason(mem={"winload": 60, "total_mb": 16384}))

    def test_cpu_from_system_times_at_90_percent_is_busy(self):
        seq = iter([(0, 0, None), (900, 100, None)])
        self.assertIsNotNone(reason(cpu=admission.SystemCPU(ticks=lambda: next(seq), sleep=lambda s: None)()))

    def test_windows_readers_are_inert_off_windows_or_without_ctypes(self):
        self.assertIsNone(admission.cpu_ticks("win32"))
        m = admission.mem_reading("win32")
        self.assertEqual(m["winload"], None)
        self.assertIn("swapins_bytes", m["unknown"])
        self.assertIn("unknown: ", admission.calm_reason(m))


class ReadersAndReasons(unittest.TestCase):
    def test_mac_readers_parse_sysctl_and_vm_stat(self):
        out = {"kern.memorystatus_vm_pressure_level": "1\n", "hw.memsize": "17179869184\n"}
        m = admission.mem_reading("darwin", run=lambda c: VM_STAT_1649 if c == ["vm_stat"] else out[c[-1]], now=lambda: 0.0)
        self.assertEqual((m["pressure"], m["total_mb"], m["free_true_mb"], m["compressor_mb"], m["page"], m["unknown"]),
                         (1, 16384, 432, 1425, PAGE, []))
        self.assertEqual(m["swapins_bytes"], 12667972 * PAGE)

    def test_failed_readers_are_unknown_never_a_crash(self):
        m = admission.mem_reading("darwin", run=lambda c: None, now=lambda: 0.0)
        self.assertEqual(m["pressure"], None)
        self.assertIn("pressure", m["unknown"])
        self.assertIsNone(admission.memory_reason(m))
        self.assertIn("unknown: ", admission.calm_reason(m))

    def test_the_reason_names_the_signal_that_fired(self):
        self.assertEqual(reason(mem={"pressure": 2}), "memory pressure warn")
        self.assertEqual(reason(mem={"pressure": 4}), "memory pressure critical")
        self.assertIsNone(reason(mem={"pressure": 1}))

    def test_swap_in_rate_is_the_average_over_the_whole_window(self):
        s = admission.mem_signals(mac(500, 0, 1000, 0.0), mac(500, 0, 1000 + 30 * 60, 60.0))
        self.assertAlmostEqual(s["swapin_pages_s"], 30.0)
        self.assertIsNone(admission.memory_reason(dict(s, pressure=1)))           # 0.5 MB/s: under 1 MB/s
        self.assertNotIn("swapin_mb_s", admission.mem_signals(mac(500, 0, 1000, 0.0), dict(mac(500, 0, 0, 60.0), swapins_bytes=None)))

    def test_system_cpu_counts_kernel_time_and_survives_counter_wrap(self):
        seq = iter([(2 ** 32 - 50, 100, 2 ** 32), (50, 110, 2 ** 32)])            # busy wrapped: +100 busy, +10 idle
        self.assertAlmostEqual(admission.SystemCPU(ticks=lambda: next(seq), sleep=lambda s: None)(), 100.0 * 100 / 110)

    def test_no_kernel_counters_falls_back_to_ps_and_the_calm_reason_says_so(self):
        cpu = admission.SystemCPU(ticks=lambda: None, sleep=lambda s: None, fallback=lambda: 12.0)
        self.assertEqual(cpu(), 12.0)
        self.assertEqual(admission.calm_reason({}, cpu), "free; unknown: kernel CPU counters (ps sum used)")

    def test_suite_gate_reads_kernel_counters_not_the_ps_sum(self):
        """admit.cpu_load is NOT faked: the suite gate (suitequeue) must take the injected kernel counters (90 % busy)."""
        import suitequeue
        state = {"b": 0, "i": 0}

        def ticks():
            state["b"] += 90
            state["i"] += 10
            return state["b"], state["i"], None

        def ps_sum():
            raise AssertionError("the ps sum was read")
        slot = suitequeue.SuiteSlot("x", snapshot=lambda: "free", mem=lambda: {"pressure": 1, "free_true_mb": 8000})
        with mock.patch.object(admission, "cpu_ticks", lambda *a, **k: ticks()), mock.patch.object(admit, "cpu_sample", ps_sum), \
                mock.patch.dict(os.environ, {"BUDGIT_SUITE_LOAD_LIMIT": "0.0001"}):
            with self.assertRaises(suitequeue.LoadTooHigh) as caught:
                slot._admit()
        self.assertAlmostEqual(caught.exception.load, 0.9 * admit.cores(), places=3)

    def test_cpu_load_fallback_to_ps_is_named_in_the_reason(self):
        sampler = admission.SystemCPU(ticks=lambda: None, sleep=lambda s: None, fallback=lambda: 40.0)
        self.assertAlmostEqual(admit.cpu_load(sampler=sampler, sleep=lambda s: None), 0.4 * admit.cores())
        self.assertIn("ps sum used", admission.calm_reason({}, sampler))
        with mock.patch.object(admission, "cpu_ticks", lambda *a, **k: None), mock.patch.object(admit, "cpu_sample", lambda: 40.0):
            self.assertAlmostEqual(admit.cpu_load(sleep=lambda s: None), 0.4 * admit.cores())

    def test_linux_without_psi_uses_memavailable_and_memfree_only_when_absent(self):
        def read(text):
            def rd(path):
                if path == "/proc/meminfo":
                    return text
                raise OSError(path)
            return rd
        both = "MemTotal: 8388608 kB\nMemFree: 204800 kB\nMemAvailable: 5242880 kB\n"
        only_free = "MemTotal: 8388608 kB\nMemFree: 204800 kB\n"
        self.assertEqual(admission.mem_reading("linux", readfile=read(both), now=lambda: 0.0, page=4096)["free_true_mb"], 5120)
        self.assertEqual(admission.mem_reading("linux", readfile=read(only_free), now=lambda: 0.0, page=4096)["free_true_mb"], 200)

    def test_is_busy_judges_instant_memory_before_waiting_on_cpu(self):
        calls = []
        busy, why = admit.is_busy(sampler=lambda: calls.append(1) or 5.0, sleep=lambda s: None, window=10, free=lambda: 9000,
                                  mem=lambda: {"pressure": 4})
        self.assertEqual((busy, why, calls), (True, "memory pressure critical", []))


@unittest.skipUnless(sys.platform == "darwin", "live macOS readers")
class LiveMacSmoke(unittest.TestCase):
    def test_live_readers_return_sane_values(self):
        t = admission.cpu_ticks()
        self.assertIsNotNone(t)
        v = admission.SystemCPU(sleep=lambda s: __import__("time").sleep(0.3))()
        self.assertTrue(0.0 <= v <= 100.0, v)
        m = admission.mem_reading()
        self.assertIn(m["pressure"], (1, 2, 4))
        self.assertGreater(m["total_mb"], 0)


if __name__ == "__main__":
    unittest.main()
