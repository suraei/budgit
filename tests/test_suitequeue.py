"""One full suite at a time on the machine: `budgit suite` and `budgit main-check` queue behind a flock under the Budgit home.
Synthetic lock and temp repos only."""
import json
import os
import subprocess
import sys
import time
import unittest

from helpers import BudgitTestCase
from loadcal import load_factor
from test_maincheck import RepoCase
import maincheck
import suitequeue

HOLDER = ("import fcntl, os, sys, time, json\n"
          "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
          "fcntl.flock(fd, fcntl.LOCK_EX)\n"
          "os.write(fd, json.dumps({'pid': os.getpid(), 'what': 'other-project suite'}).encode())\n"
          "print('held', flush=True)\n"
          "time.sleep(float(sys.argv[2]))\n")


def slurp(path):
    with open(path) as f:
        return f.read()


class SlotTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        self.lock = os.path.join(self.home, "q", "suite.lock")
        self._old = {k: os.environ.get(k) for k in ("BUDGIT_SUITE_LOCK", "BUDGIT_SUITE_LOAD_LIMIT", "BUDGIT_SUITE_SLOTS",
                                                    "BUDGIT_SUITE_PRIORITY", suitequeue.HELD_ENV)}
        os.environ["BUDGIT_SUITE_LOCK"] = self.lock
        os.environ["BUDGIT_SUITE_SLOTS"] = "1"                                # one slot: the tests below hold it to queue behind it
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "100000"                      # the real machine's load must not decide a test
        os.environ.pop(suitequeue.HELD_ENV, None)
        import admit
        self.addCleanup(setattr, admit, "cpu_load", admit.cpu_load)
        admit.cpu_load = lambda *a, **k: 1.0                                  # R-841: a fake CPU reading, no real sampling
        self.addCleanup(setattr, suitequeue, "busy_snapshot", suitequeue.busy_snapshot)
        suitequeue.busy_snapshot = lambda: "free"                              # R-889b: no in-process test reads the live machine
        self.addCleanup(setattr, suitequeue, "read_memory", suitequeue.read_memory)
        suitequeue.read_memory = lambda: {"pressure": 1, "free_true_mb": 8000}  # a calm memory picture, never the live machine
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def hold(self, seconds):
        os.makedirs(os.path.dirname(self.lock), exist_ok=True)
        p = subprocess.Popen([sys.executable, "-c", HOLDER, self.lock, str(seconds)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_r1201_the_cpu_override_takes_only_a_finite_reading_of_0_or_more(self):
        import admit
        admit.cpu_load = lambda *a, **k: 1e9                                  # the injected live reading: far above any limit
        for name in ("BUDGIT_SUITE_TEST_CPU", "STUDIO_SUITE_TEST_CPU"):
            self.addCleanup(os.environ.pop, name, None)
            for bad in ("-1", "-1000", "nan", "inf", "-inf", "", "abc"):
                os.environ.pop("BUDGIT_SUITE_TEST_CPU", None)
                os.environ.pop("STUDIO_SUITE_TEST_CPU", None)
                os.environ[name] = bad
                self.assertIsNone(suitequeue._fixed_cpu(), (name, bad))
                with self.assertRaises(suitequeue.LoadTooHigh, msg=(name, bad)):
                    with suitequeue.SuiteSlot("x", snapshot=lambda: "free"):
                        pass
            os.environ.pop("BUDGIT_SUITE_TEST_CPU", None)
            os.environ.pop("STUDIO_SUITE_TEST_CPU", None)
            os.environ[name] = "0"
            self.assertEqual(suitequeue._fixed_cpu()(), 0.0, name)             # a finite reading of 0 is the one allowed override
            with suitequeue.SuiteSlot("x", snapshot=lambda: "free"):
                pass

    def test_free_lock_is_taken_at_once_and_load_is_recorded(self):
        with suitequeue.SuiteSlot("x", snapshot=lambda: "free") as slot:      # R-889b: the busy snapshot, injected
            self.assertEqual(slot.waited, 0)
            self.assertEqual(slot.load, "free")
            self.assertEqual(os.environ.get(suitequeue.HELD_ENV), "1")
            self.assertEqual(json.loads(slurp(self.lock))["what"], "x")
        self.assertNotIn(suitequeue.HELD_ENV, os.environ)

    def test_a_second_suite_waits_for_the_first_and_says_so(self):
        p = self.hold(2)
        heard = []
        began = time.time()
        with suitequeue.SuiteSlot("mine", notify=lambda w, h: heard.append((w, h))) as slot:
            pass
        self.assertGreaterEqual(time.time() - began, 1.0)
        self.assertGreaterEqual(slot.waited, 1)
        self.assertEqual(heard[0][1]["what"], "other-project suite")
        self.assertEqual(heard[0][1]["pid"], p.pid)
        self.assertEqual(len(heard), 1)                                     # one notice per minute, not per poll

    def test_the_default_limit_is_the_cores_and_an_env_value_replaces_it(self):
        os.environ.pop("BUDGIT_SUITE_LOAD_LIMIT", None)
        self.assertEqual(suitequeue.load_limit(), (os.cpu_count() or 1) * 85 / 100.0)      # 85 % of the cores, the busy rule
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "3.5"
        self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOAD_LIMIT", None)
        self.assertEqual(suitequeue.load_limit(), 3.5)

    def test_an_overloaded_machine_refuses_and_leaves_the_lock_free(self):
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "0.0001"
        self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOAD_LIMIT", None)
        with self.assertRaises(suitequeue.LoadTooHigh) as caught:               # the fake reading is 1.0 cores
            with suitequeue.SuiteSlot("x"):
                self.fail("must not start")
        self.assertGreater(caught.exception.load, caught.exception.limit)
        self.assertNotIn(suitequeue.HELD_ENV, os.environ)
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "100000"
        with suitequeue.SuiteSlot("again"):                                         # the refused run left nothing behind
            pass

    def test_wait_rechecks_every_30_seconds_until_the_load_drops(self):
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "4"
        self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOAD_LIMIT", None)
        import admit
        loads = iter([9.0, 6.0, 3.9])                                       # the CPU reading decides, not the load average
        sleeps, heard = [], []
        admit.cpu_load = lambda *a, **k: next(loads)
        with suitequeue.SuiteSlot("w", wait_load=True, sleep=sleeps.append, load_notice=lambda l, m: heard.append((l, m))) as slot:
            self.assertEqual(len(heard), 2)
        self.assertEqual([x for x in sleeps if x == suitequeue.LOAD_POLL], [30, 30])
        self.assertEqual(heard, [(9.0, 4.0), (6.0, 4.0)])

    def test_a_nested_run_is_never_refused_for_load(self):
        with suitequeue.SuiteSlot("outer"):
            os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "0.0001"
            self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOAD_LIMIT", None)
            with suitequeue.SuiteSlot("inner"):
                pass

    def test_a_dead_holder_frees_the_lock(self):
        p = self.hold(60)
        p.kill()
        p.wait()
        with suitequeue.SuiteSlot("after the crash") as slot:
            self.assertLess(slot.waited, 3 * load_factor())

    def test_a_nested_run_inside_the_holder_does_not_wait_for_its_parent(self):
        with suitequeue.SuiteSlot("outer"):
            began = time.time()
            with suitequeue.SuiteSlot("inner") as inner:
                self.assertEqual(inner.waited, 0)
            self.assertLess(time.time() - began, 2 * load_factor())     # real-deadline budget 2 s scaled by the same-run calibration (R-1282)
            self.assertEqual(os.environ.get(suitequeue.HELD_ENV), "1")      # the outer still holds it


class CliQueueTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.lock = os.path.join(self.home, "suite.lock")
        self.env["BUDGIT_SUITE_LOCK"] = self.lock
        self.env["BUDGIT_SUITE_SLOTS"] = "1"                        # one slot: the tests below hold it to queue behind it
        self.env.pop(suitequeue.HELD_ENV, None)                     # a suite run by `budgit suite` marks its children

    def test_budgit_suite_runs_the_suite_and_prints_wait_and_load(self):
        self.set_config(test_command="echo SUITE-RAN; exit 4")
        r = self.cli("suite", expect=4)
        self.assertIn("SUITE-RAN", r.stdout)
        self.assertIn("machine busy check at start:", r.stdout)
        self.assertNotIn("load average", r.stdout)
        self.assertIn("suite exited with code 4", r.stdout)

    def test_budgit_suite_refuses_an_injected_critical_picture_so_the_injection_keeps_the_check_on(self):
        self.env["BUDGIT_TEST_MEM"] = '{"pressure": 4, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100}'
        self.set_config(test_command="echo SUITE-RAN; exit 0")
        r = self.cli("suite")
        self.assertNotIn("SUITE-RAN", r.stdout)
        self.assertIn("memory", r.stdout + r.stderr)
        self.assertNotEqual(r.returncode, 0)

    def test_budgit_suite_queues_behind_a_running_suite(self):
        os.makedirs(os.path.dirname(self.lock), exist_ok=True)
        p = subprocess.Popen([sys.executable, "-c", HOLDER, self.lock, "2"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        p.stdout.readline()
        self.set_config(test_command="exit 0")
        r = self.cli("suite", expect=0)
        self.assertIn("waiting for 0s behind another suite (other-project suite, pid %d)" % p.pid, r.stdout)

    def test_main_check_queues_records_the_load_and_a_cached_result_never_queues(self):
        r = self.cli("main-check", expect=0)
        self.assertIn("machine busy check at start:", r.stdout)
        data = json.loads(self.cli("main-check", "--status", "--json", expect=0).stdout)
        self.assertIsInstance(data["result"]["load_start"], str)                 # R-889b: the busy snapshot, not 3 numbers
        self.assertIn("waited", data["result"])
        os.makedirs(os.path.dirname(self.lock), exist_ok=True)
        p = subprocess.Popen([sys.executable, "-c", HOLDER, self.lock, "30"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        p.stdout.readline()
        began = time.time()
        again = self.cli("main-check", expect=0).stdout
        self.assertIn("reusing", again)
        self.assertNotIn("load average", again)                              # a reused result did not queue this time
        self.assertLess(time.time() - began, 10 * load_factor())                        # a cached green does not wait for the other suite

    def test_an_overloaded_machine_makes_suite_and_main_check_exit_4_without_running(self):
        marker = os.path.join(self.home, "ran")
        self.set_config(test_command="touch '%s'" % marker)
        self.env["BUDGIT_SUITE_LOAD_LIMIT"] = "0.0001"
        for cmd in ("suite", "main-check"):
            r = self.cli(cmd, expect=4)
            self.assertIn("not starting: the busy CPU (", r.stdout)
            self.assertIn("is above the limit (0.0 cores)", r.stdout)
        self.assertFalse(os.path.exists(marker))
        self.assertIn("no result recorded", self.cli("main-check", "--status", expect=0).stdout)   # a refusal records nothing

    def test_a_reused_result_is_never_refused_and_wait_passes_when_the_load_is_fine(self):
        self.cli("main-check", expect=0)
        self.env["BUDGIT_SUITE_LOAD_LIMIT"] = "0.0001"
        self.assertIn("reusing", self.cli("main-check", expect=0).stdout)
        self.env["BUDGIT_SUITE_LOAD_LIMIT"] = "100000"
        self.set_config(test_command="exit 0")
        self.assertIn("suite exited with code 0", self.cli("suite", "--wait", expect=0).stdout)
        self.assertIn("reusing", self.cli("main-check", "--wait", expect=0).stdout)

    def test_a_timed_out_suite_takes_its_child_processes_with_it(self):
        os.makedirs(self.home, exist_ok=True)
        pidfile = os.path.join(self.home, "child.pid")
        self.set_config(test_command="sleep 300 & echo $! > '%s'; wait" % pidfile, test_timeout=2)
        _b, _s, res, _c = maincheck.check(self.project, rerun=True)
        self.assertTrue(res.get("timed_out"))
        pid = int(slurp(pidfile).strip())
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.25)
        else:
            os.kill(pid, 9)
            self.fail("the test process the suite started outlived the timeout")


if __name__ == "__main__":
    unittest.main()
