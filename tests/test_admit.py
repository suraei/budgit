"""R-381: `budgit admit` -- go / wait / refuse from load, memory and busy sessions. Injected readings; nothing real is measured
except where a test says so."""
import json
import os
import subprocess
import sys
import unittest

from helpers import BudgitTestCase, ROOT
import admit
import hook

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")


def reading(load=1.0, free=8000, busy=1, cores=8):
    return {"cpu_load": load, "cores": cores, "free_mb": free, "busy": busy}


class DecideTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        os.environ["BUDGIT_LANG"] = "en"                                      # the base class restores it
        os.environ.pop("BUDGIT_SUITE_LOAD_LIMIT", None)

    def verdict(self, m, kind="lane", config=None):
        return admit.decide(m, kind, config if config is not None else {})["verdict"]

    def test_a_quiet_machine_is_go_with_the_reading_as_the_reason(self):
        got = admit.decide(reading(), "lane", {})
        self.assertEqual((got["verdict"], got["exit"]), ("go", 0))
        self.assertIn("CPU", got["reason"])
        self.assertIn("8000 MB", got["reason"])
        self.assertNotIn("load", got["reason"])

    def test_load_above_the_cores_waits_and_above_twice_the_cores_refuses(self):
        self.assertEqual(self.verdict(reading(load=6.8)), "go")                    # 85 % of 8 cores: the limit itself is fine
        self.assertEqual(self.verdict(reading(load=6.9)), "wait")
        self.assertEqual(admit.decide(reading(load=6.9), "lane", {})["exit"], 1)
        self.assertEqual(self.verdict(reading(load=16.1)), "refuse")
        self.assertEqual(admit.decide(reading(load=16.1), "lane", {})["exit"], 2)
        self.assertEqual(self.verdict(reading(load=9, cores=4)), "refuse")        # the cores are the machine's, not a constant

    def test_busy_sessions_refuse_a_lane_but_never_a_suite_and_low_memory_refuses_both(self):
        self.assertEqual(self.verdict(reading(free=1000)), "refuse")
        self.assertIn("1000 MB", admit.decide(reading(free=1000), "lane", {})["reason"])
        self.assertEqual(self.verdict(reading(busy=6)), "refuse")
        self.assertEqual(self.verdict(reading(busy=5)), "go")
        self.assertEqual(self.verdict(reading(busy=9), "suite"), "go")
        self.assertEqual(self.verdict(reading(free=1000, busy=9), "suite"), "refuse")      # memory refuses every kind

    def test_a_refusal_is_reported_before_a_wait(self):
        got = admit.decide(reading(load=9, free=500), "lane", {})
        self.assertEqual(got["verdict"], "refuse")
        self.assertIn("500 MB", got["reason"])

    def test_a_reading_that_cannot_be_taken_is_skipped_not_guessed(self):
        self.assertEqual(self.verdict(reading(load=None, free=None, busy=None)), "go")
        self.assertEqual(self.verdict(reading(free=None, busy=None, load=9)), "wait")

    def test_the_thresholds_come_from_the_config_and_bad_values_fall_back(self):
        cfg = {"admit": {"wait_load": 3, "refuse_load": 5, "min_free_mb": 4000, "max_busy": 2}}
        self.assertEqual(self.verdict(reading(load=4), config=cfg), "wait")
        self.assertEqual(self.verdict(reading(load=5.5), config=cfg), "refuse")
        self.assertEqual(self.verdict(reading(free=3000), config=cfg), "go")          # R-889b: min_free_mb is gone
        self.assertEqual(self.verdict(reading(free=500), config=cfg), "refuse")       # truly free under the floor, no pressure reading
        self.assertEqual(self.verdict(reading(busy=2), config=cfg), "refuse")
        junk = {"admit": {"wait_load": "high", "refuse_load": -1, "min_free_mb": True, "max_busy": None}}
        self.assertEqual(admit.thresholds(reading(), junk), {"wait_load": 6.8, "refuse_load": 16.0, "max_busy": 6})

    def test_r889b_a_lane_is_refused_on_memory_pressure_and_never_on_free_plus_inactive(self):
        got = admit.decide(dict(reading(free=432), mem={"pressure": 2, "free_true_mb": 432}), "lane", {})
        self.assertEqual(got["verdict"], "refuse")
        self.assertIn("memory pressure warn", got["reason"])
        calm = admit.decide(dict(reading(free=432), mem={"pressure": 1, "free_true_mb": 432}), "lane", {})
        self.assertNotEqual(calm["verdict"], "refuse")                                 # her calm Mac at 16:49
        self.assertEqual(admit.thresholds(reading(), {"admit": "oops"})["wait_load"], 6.8)
        self.assertGreaterEqual(admit.thresholds(reading(), {"admit": {"wait_load": 10, "refuse_load": 2}})["refuse_load"], 10)

    def test_the_suite_override_sets_both_load_thresholds(self):
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "3.5"
        self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOAD_LIMIT", None)
        got = admit.thresholds(reading())
        self.assertEqual((got["wait_load"], got["refuse_load"]), (3.5, 3.5))


class MeasureTest(BudgitTestCase):
    def test_r889b_admit_keeps_no_memory_reader_copy_and_measure_uses_the_shared_one(self):
        import admission
        from unittest import mock
        self.assertFalse(hasattr(admit, "free_mb"))
        pic = {"pressure": 1, "free_true_mb": 432, "unknown": []}
        with mock.patch.object(admission, "mem_reading", lambda: pic), mock.patch.object(admit, "cpu_load", lambda **k: 1.0), \
                mock.patch.object(admit, "busy_sessions", lambda: 0):
            got = admit.measure()
        self.assertEqual((got["free_mb"], got["mem"]), (432, pic))

    def test_busy_sessions_count_only_verified_busy_rows_and_an_unreadable_folder_is_none(self):
        import livework
        real = livework.live_sessions
        self.addCleanup(setattr, livework, "live_sessions", real)
        rows = [{"status": "busy", "verified": True}, {"status": "busy", "verified": False}, {"status": "idle", "verified": True},
                {"status": "busy", "verified": True}]
        livework.live_sessions = lambda: {"status": "ok", "sessions": rows}
        self.assertEqual(admit.busy_sessions(), 2)
        livework.live_sessions = lambda: {"status": "unreadable", "sessions": []}
        self.assertIsNone(admit.busy_sessions())

        def boom():
            raise RuntimeError("x")
        livework.live_sessions = boom
        self.assertIsNone(admit.busy_sessions())


class BusyRuleTest(BudgitTestCase):
    """R-841 and R-848: one busy rule (admission.busy_reason); admit's decision never reads the load average."""

    def test_no_decision_reads_the_load_average(self):
        real = os.getloadavg

        def boom():
            raise AssertionError("os.getloadavg must not be read")
        os.getloadavg = boom
        self.addCleanup(setattr, os, "getloadavg", real)
        self.assertFalse(hasattr(admit, "load1"))
        got = admit.cpu_load(sampler=lambda: 50.0, sleep=lambda s: None)
        self.assertAlmostEqual(got, 0.5 * admit.cores())
        self.assertEqual(admit.decide({"cpu_load": got, "cores": admit.cores()}, "suite", {})["verdict"], "go")
        self.assertIsNone(admit.cpu_load(sampler=lambda: None, sleep=lambda s: None))

    def test_admit_and_admission_share_one_rule_and_one_set_of_thresholds(self):
        import admission
        self.assertFalse(hasattr(admit, "BUSY_CPU_PCT") or hasattr(admit, "BUSY_FREE_MB"))
        for free, cpu in ((500, 10), (8000, 90), (8000, 85), (1024, 85), (None, None)):
            sampler = (lambda c=cpu: c)
            busy, why = admit.is_busy(sampler=sampler, sleep=lambda s: None, window=10, free=lambda f=free: f)
            self.assertEqual(busy, bool(admission.busy_reason(free, cpu)), (free, cpu))
            slots, _ = admission.budget({"cores": 8, "free_mb": free, "battery": False, "cpu_pct": cpu}, 300)
            if busy:
                self.assertEqual(slots, 1)
        self.assertEqual(admit.thresholds({"cores": 4}, {})["wait_load"], admission.busy_cpu_cores(4))


class AdviceTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        os.environ["BUDGIT_LANG"] = "en"
        os.environ.pop("BUDGIT_ADMIT_ADVICE", None)                           # the base class switches it off
        self.addCleanup(setattr, admit, "measure", admit.measure)

    def test_no_advice_when_the_answer_is_go_and_never_an_error(self):
        admit.measure = lambda: reading()
        self.assertEqual(admit.advice(), "")
        admit.measure = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertEqual(admit.advice(), "")

    def test_the_agent_hook_adds_one_line_of_advice_and_blocks_nothing(self):
        admit.measure = lambda: reading(load=12)
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use({"tool_name": "Agent", "tool_input": {"model": "sonnet", "subagent_type": "studio:builder"},
                              "session_id": "s1"}, self.tmp)
        out =hook._OUT["hookSpecificOutput"]
        self.assertIn("budgit admit: wait", out["additionalContext"])
        self.assertNotIn("permissionDecision", out)

    def test_the_advice_can_be_switched_off(self):
        admit.measure = lambda: reading(load=12)
        os.environ["BUDGIT_ADMIT_ADVICE"] = "off"
        self.assertEqual(admit.advice(), "")

    def test_a_go_adds_nothing_to_the_agent_call(self):
        admit.measure = lambda: reading()
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use({"tool_name": "Agent", "tool_input": {"model": "sonnet", "subagent_type": "studio:builder"},
                              "session_id": "s1"}, self.tmp)
        self.assertNotIn("admit", json.dumps(hook._OUT))


class CliTest(BudgitTestCase):
    def run_cli(self, *args, limit=None):
        env = dict(os.environ, BUDGIT_LANG="en", BUDGIT_TEST_MEM='{"pressure": 1, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100}')
        if limit is not None:
            env["BUDGIT_SUITE_LOAD_LIMIT"] = limit
        return subprocess.run([sys.executable, BUDGIT, "admit", *args], capture_output=True, text=True, env=env)

    def test_exit_codes_and_one_line(self):
        r = self.run_cli("suite", limit="100000")
        self.assertEqual((r.returncode, len(r.stdout.strip().splitlines())), (0, 1), r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("go:"))
        r = self.run_cli("suite", limit="0.0001")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stdout.startswith("refuse:"))

    def test_a_suite_goes_at_pressure_warn_with_calm_swap_and_compressor_through_the_cli(self):
        env = dict(os.environ, BUDGIT_LANG="en", BUDGIT_SUITE_LOAD_LIMIT="100000",
                   BUDGIT_TEST_MEM='{"pressure": 2, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100}')
        r = subprocess.run([sys.executable, BUDGIT, "admit", "suite"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("go:"), r.stdout)

    def test_a_wrong_word_is_not_mistaken_for_a_refusal(self):
        self.assertEqual(self.run_cli("bogus").returncode, 64)

    def test_a_nan_or_negative_override_is_ignored(self):
        for bad in ("nan", "-3"):
            os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = bad
            self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOAD_LIMIT", None)
            self.assertEqual(admit.thresholds(reading())["wait_load"], 6.8)

    def test_json_carries_the_readings_and_the_limits(self):
        r = self.run_cli("suite", "--json", limit="100000")
        got = json.loads(r.stdout)
        self.assertEqual(got["kind"], "suite")
        self.assertIn("cpu_load", got["measured"])
        self.assertNotIn("load1", got["measured"])
        self.assertEqual(got["limits"]["wait_load"], 100000.0)


class R864MessageTest(BudgitTestCase):
    """R-864: admit judges by busy CPU and free memory (the shared admission rule), never by load average, and says so."""

    def setUp(self):
        super().setUp()
        os.environ["BUDGIT_LANG"] = "en"
        os.environ.pop("BUDGIT_SUITE_LOAD_LIMIT", None)

    def test_high_load_average_with_low_cpu_is_not_busy(self):
        real = os.getloadavg
        os.getloadavg = lambda: (23.4, 20.0, 15.0)
        self.addCleanup(setattr, os, "getloadavg", real)
        got = admit.decide({"cpu_load": admit.cpu_load(sampler=lambda: 10.0, sleep=lambda s: None), "cores": 8, "free_mb": 9000, "busy": 0}, "suite", {})
        self.assertEqual(got["verdict"], "go")
        self.assertNotIn("23.4", got["reason"])

    def test_cpu_saturation_is_busy_and_the_message_names_cpu_percent_and_free_memory(self):
        got = admit.decide({"cpu_load": 7.6, "cores": 8, "free_mb": 4321, "busy": 0}, "suite", {})
        self.assertEqual(got["verdict"], "wait")
        self.assertIn("CPU 95 %", got["reason"])
        self.assertIn("4321 MB free", got["reason"])
        self.assertNotIn("load", got["reason"])

    def test_low_memory_is_busy_for_the_shared_rule_and_a_lane_is_refused_with_the_free_figure(self):
        import admission
        self.assertTrue(admit.is_busy(sampler=lambda: 5.0, sleep=lambda s: None, window=5, free=lambda: 500)[0])
        self.assertIsNotNone(admission.busy_reason(500, None))
        got = admit.decide({"cpu_load": 1.0, "cores": 8, "free_mb": 500, "busy": 0}, "lane", {})
        self.assertEqual(got["verdict"], "refuse")
        self.assertIn("500 MB", got["reason"])


if __name__ == "__main__":
    unittest.main()
