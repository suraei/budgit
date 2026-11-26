"""R-848: the machine-wide admission cap. Every reading is injected; nothing real is measured."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

from helpers import ROOT
import admission

M = dict(cores=8, free_mb=32000, battery=False, cpu_pct=10)


def m(**kw):
    d = dict(M)
    d.update(kw)
    return d


class BudgetTest(unittest.TestCase):
    def test_normal_path_is_cores_minus_one(self):
        self.assertEqual(admission.budget(m())[0], 7)

    def test_busy_cpu_gives_one(self):
        self.assertEqual(admission.budget(m(cpu_pct=90))[0], 1)

    def test_busy_memory_gives_one(self):
        self.assertEqual(admission.budget(m(free_mb=900))[0], 1)

    def test_battery_halves(self):
        self.assertEqual(admission.budget(m(battery=True))[0], 3)

    def test_floor_is_one(self):
        self.assertEqual(admission.budget(m(cores=1))[0], 1)
        self.assertEqual(admission.budget(m(cores=0, free_mb=0))[0], 1)

    def test_ceiling_is_cores_minus_one(self):
        self.assertEqual(admission.budget(m(cores=64))[0], 63)

    def test_memory_limits_slots(self):
        self.assertEqual(admission.budget(m(free_mb=1024 + 3 * 300))[0], 3)
        self.assertEqual(admission.budget(m(free_mb=1024 + 3 * 1500), 1500)[0], 3)

    def test_unknown_memory_is_conservative(self):
        self.assertEqual(admission.budget(m(cores=9, free_mb=None))[0], 4)

    def test_container_quota_of_two(self):
        cores = admission.usable_cores(cpu_count=lambda: 16, affinity=None, quota=2)
        self.assertEqual(cores, 2)
        self.assertEqual(admission.budget(m(cores=cores))[0], 1)
        cores3 = admission.usable_cores(cpu_count=lambda: 16, affinity=None, quota=2.5)
        self.assertLessEqual(admission.budget(m(cores=cores3))[0], 2)

    def test_affinity_beats_cpu_count(self):
        self.assertEqual(admission.usable_cores(cpu_count=lambda: 16, affinity=lambda p: {0, 1, 2}), 3)


class ReadingsTest(unittest.TestCase):
    def test_cgroup_v2(self):
        self.assertEqual(admission.cgroup_quota(lambda p: "200000 100000" if p.endswith("cpu.max") else None), 2.0)
        self.assertIsNone(admission.cgroup_quota(lambda p: "max 100000" if p.endswith("cpu.max") else None))

    def test_cgroup_v1(self):
        files = {"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "150000", "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000"}
        self.assertEqual(admission.cgroup_quota(files.get), 1.5)

    def test_linux_branch(self):
        text = "MemTotal: 1 kB\nMemAvailable:    4194304 kB\n"
        self.assertEqual(admission.free_mb("linux", readfile=lambda p: text), 4096)
        self.assertTrue(admission.on_battery("linux", listdir=lambda d: ["AC", "BAT0"], readfile=lambda p: "Discharging\n"))
        self.assertFalse(admission.on_battery("linux", listdir=lambda d: ["BAT0"], readfile=lambda p: "Charging\n"))
        self.assertIsNone(admission.on_battery("linux", listdir=lambda d: ["AC"]))

    def test_macos_branch(self):
        vm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:   1000.\n"
              "Pages inactive:  1000.\nPages speculative: 0.\n")
        self.assertEqual(admission.free_mb("darwin", run=lambda c: vm), 2000 * 16384 // 1048576)
        self.assertTrue(admission.on_battery("darwin", run=lambda c: "Now drawing from 'Battery Power'"))
        self.assertFalse(admission.on_battery("darwin", run=lambda c: "Now drawing from 'AC Power'"))

    def test_cpu_reading_branches_macos_linux_and_failure(self):
        ps = "10.0\n20.0\n 30.0\n"
        for plat in ("darwin", "linux"):
            self.assertEqual(admission.cpu_pct(run=lambda c: ps, cores=4, platform=plat), 15.0)
        self.assertIsNone(admission.cpu_pct(run=lambda c: None, cores=4, platform="linux"))
        self.assertIsNone(admission.cpu_pct(run=lambda c: "x y", cores=4, platform="darwin"))

    def test_the_busy_rule_is_defined_once(self):
        self.assertIsNone(admission.busy_reason(None, None))
        self.assertIsNone(admission.busy_reason(admission.BUSY_FREE_MB, admission.BUSY_CPU_PCT))
        self.assertIn("memory", admission.busy_reason(admission.BUSY_FREE_MB - 1, 0))
        self.assertIn("CPU", admission.busy_reason(9999, admission.BUSY_CPU_PCT + 1))
        self.assertEqual(admission.busy_cpu_cores(8), 6.8)

    def test_windows_branch_is_unknown_and_conservative(self):
        self.assertIsNone(admission.free_mb("win32"))
        self.assertIsNone(admission.on_battery("win32"))
        self.assertIsNone(admission.cpu_pct(platform="win32"))
        self.assertEqual(admission.budget(m(cores=8, free_mb=None, battery=None, cpu_pct=None))[0], 3)


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="admission-test-")
        del admission._held[:]

    def tearDown(self):
        del admission._held[:]

    def acq(self, want, pid, machine=None, **kw):
        return admission.acquire(want, "test", machine=machine or m(cores=16, free_mb=64000), state_dir=self.dir,
                                 pid=pid, alive=lambda p: True, **kw)

    def test_success_path_and_release(self):
        self.assertEqual(self.acq(4, 1001), 4)
        self.assertEqual(len(os.listdir(self.dir)), 2)           # lock + one lease
        admission.release()
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".lease")], [])

    def test_second_holder_gets_only_what_is_free(self):
        self.assertEqual(self.acq(10, 1001), 10)
        self.assertEqual(self.acq(10, 1002), 5)                  # budget 15
        self.assertEqual(self.acq(10, 1003), 1)                  # nothing free: one crawl slot, never 0

    def test_eight_concurrent_holders_never_exceed_budget(self):
        out, lock = [], threading.Lock()

        def hold(i):
            g = self.acq(16, 2000 + i)
            with lock:
                out.append(g)
        ts = [threading.Thread(target=hold, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        leases = []
        for n in os.listdir(self.dir):
            if n.endswith(".lease"):
                with open(os.path.join(self.dir, n)) as f:
                    leases.append(json.load(f))
        self.assertEqual(len(leases), 8)
        real = sum(d["slots"] for d in leases if not d["crawl"])
        self.assertLessEqual(real, 15)
        self.assertEqual(real, 15)                               # and the budget is used
        self.assertTrue(all(d["slots"] == 1 for d in leases if d["crawl"]))
        self.assertTrue(all(g >= 1 for g in out))

    def test_dead_pid_lease_is_reclaimed(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertEqual(admission.acquire(15, "test", machine=m(cores=16, free_mb=64000), state_dir=self.dir,
                                           pid=p.pid), 15)       # real liveness check: p.pid is gone after it fills the budget
        # a new holder now finds the dead one's slots free again
        self.assertEqual(admission.acquire(15, "test", machine=m(cores=16, free_mb=64000), state_dir=self.dir), 15)
        names = [n for n in os.listdir(self.dir) if n.endswith(".lease")]
        self.assertEqual(len(names), 1)

    def test_timeout_waits_then_crawls(self):
        self.acq(15, 1001)
        clock = [0.0]
        naps = []

        def sleep(s):
            naps.append(s)
            clock[0] += s
        got = self.acq(4, 1002, timeout=1, sleep=sleep, now=lambda: clock[0])
        self.assertEqual(got, 1)
        self.assertTrue(naps)

    def test_context_manager_releases(self):
        with admission.admitted(3, machine=m(), state_dir=self.dir) as n:
            self.assertEqual(n, 3)
        self.assertEqual([x for x in os.listdir(self.dir) if x.endswith(".lease")], [])


class CpuMedianTest(unittest.TestCase):
    def test_median_of_samples_ignores_a_spike_and_unreadable(self):
        vals = iter([10, None, 99, 12])
        naps = []
        self.assertEqual(admission.cpu_median(lambda: next(vals), naps.append, samples=4, step=0.1), 12)
        self.assertEqual(naps, [0.1, 0.1, 0.1])

    def test_all_unreadable_is_none(self):
        self.assertIsNone(admission.cpu_median(lambda: None, lambda s: None))

    def test_read_machine_uses_the_injected_sampler(self):
        got = admission.read_machine(sampler=iter([90, 91, 92]).__next__, sleep=lambda s: None)
        self.assertEqual(got["cpu_pct"], 91)


CHILD = ("import sys,admission\n"
         "g=admission.acquire(16,'test',machine={'cores':16,'free_mb':64000,'battery':False,'cpu_pct':1},state_dir=sys.argv[1])\n"
         "print(g,flush=True)\n"
         "import time; time.sleep(2)\n")


class ContentionTest(unittest.TestCase):
    def test_six_real_processes_never_exceed_budget(self):
        d = tempfile.mkdtemp(prefix="admission-mp-")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(ROOT, "plugin", "scripts")
        procs = [subprocess.Popen([sys.executable, "-c", CHILD, d], stdout=subprocess.PIPE, text=True, env=env)
                 for _ in range(6)]
        granted = [int(p.communicate(timeout=20)[0].split()[0]) for p in procs]
        # each child holds its lease for 2 s, so all six overlapped; budget is 15
        self.assertEqual(sum(g for g in granted if g > 1), 15 if max(granted) > 1 else 0)
        self.assertEqual(max(granted), 15)
        self.assertEqual(sorted(granted)[:5], [1, 1, 1, 1, 1])       # the rest crawl at 1, never 0


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
class LiveMacTest(unittest.TestCase):
    def run_cmd(self, *cmd):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout

    def test_cores_battery_memory_match_the_system(self):
        self.assertEqual(admission.usable_cores(), int(self.run_cmd("sysctl", "-n", "hw.ncpu")))
        batt = self.run_cmd("pmset", "-g", "batt")
        expected = True if "Battery Power" in batt else False if "AC Power" in batt else None
        self.assertEqual(admission.on_battery(), expected)
        a = admission.free_mb()
        b = admission.vm_stat_free_mb(self.run_cmd("vm_stat"))
        self.assertTrue(a and b)
        self.assertLess(abs(a - b), 0.10 * max(a, b))


class CliTest(unittest.TestCase):
    def test_child_gets_granted_count(self):
        seen = {}

        def fake_run(cmd, env=None):
            seen["cmd"], seen["slots"] = cmd, env[admission.ENV_SLOTS]
            return 7
        d = tempfile.mkdtemp(prefix="admission-cli-")
        rc = admission.main(["--slots", "99", "--", "echo", "hi"], run=fake_run, machine=m(cores=4, free_mb=64000),
                            state_dir=d)
        self.assertEqual((rc, seen["slots"], seen["cmd"]), (7, "3", ["echo", "hi"]))

    def test_real_child_sees_env(self):
        d = tempfile.mkdtemp(prefix="admission-cli-")
        code = "import os,sys; sys.exit(int(os.environ['BUDGIT_ADMIT_SLOTS']))"
        rc = admission.main(["--slots", "2", "--", sys.executable, "-c", code], machine=m(cores=8, free_mb=64000),
                            state_dir=d)
        self.assertEqual(rc, 2)

    def test_budgit_admit_slots_dispatch(self):
        code = "import os,sys; sys.exit(0 if int(os.environ['BUDGIT_ADMIT_SLOTS'])>=1 else 9)"
        env = dict(os.environ, BUDGIT_HOME=tempfile.mkdtemp(prefix="admission-home-"),
                   BUDGIT_TEST_MEM='{"pressure": 1, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100}')
        r = subprocess.run([sys.executable, os.path.join(ROOT, "plugin", "bin", "budgit"), "admit", "--slots", "2", "--",
                            sys.executable, "-c", code], capture_output=True, text=True, timeout=25, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_usage_errors(self):
        self.assertEqual(admission.main(["--slots", "2"]), 2)
        self.assertEqual(admission.main(["--slots", "x", "--", "true"]), 2)


if __name__ == "__main__":
    unittest.main()
