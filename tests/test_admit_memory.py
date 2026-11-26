"""`budgit admit` reads the memory signal for every kind of run, not only a lane. Injected readings; the live machine is never read."""
import os

from helpers import BudgitTestCase
import admit
import suitequeue


def reading(load=1.0, free=8000, mem=None, cores=8):
    got = {"cpu_load": load, "cores": cores, "free_mb": free, "busy": 1}
    if mem is not None:
        got["mem"] = mem
    return got


CALM = {"pressure": 1, "compressor_mb": 100, "free_true_mb": 8000}
PRESSURE = {"pressure": 4, "compressor_mb": 100, "free_true_mb": 8000}    # critical: a suite refuses here
WARN = {"pressure": 2, "compressor_mb": 100, "free_true_mb": 8000}        # warn: a laptop reads this all day; a lane refuses, a suite goes


class SuiteQueueMemoryTest(BudgitTestCase):
    """The suite queue asks admit with a memory picture too (injected here; the live machine is never read)."""
    def setUp(self):
        super().setUp()
        os.environ["BUDGIT_LANG"] = "en"
        self._old = {k: os.environ.get(k) for k in ("BUDGIT_SUITE_LOCK", "BUDGIT_SUITE_SLOTS", "BUDGIT_SUITE_LOAD_LIMIT", suitequeue.HELD_ENV)}
        self.addCleanup(self._restore)
        os.environ["BUDGIT_SUITE_LOCK"] = os.path.join(self.home, "q", "suite.lock")
        os.environ["BUDGIT_SUITE_SLOTS"] = "1"
        os.environ["BUDGIT_SUITE_LOAD_LIMIT"] = "100000"
        os.environ.pop(suitequeue.HELD_ENV, None)

    def _restore(self):
        for k, v in self._old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def slot(self, mem):
        return suitequeue.SuiteSlot("x", snapshot=lambda: "free", cpu=lambda: 0.5, mem=lambda: mem)

    def test_the_queue_refuses_a_suite_on_a_memory_signal_with_idle_cpu(self):
        with self.assertRaises(suitequeue.MemoryTooLow) as ctx:
            with self.slot(PRESSURE):
                pass
        self.assertIn("memory pressure", str(ctx.exception))

    def test_the_queue_starts_a_suite_when_memory_is_fine(self):
        with self.slot(CALM):
            pass

    def test_the_queue_does_not_refuse_a_suite_on_a_blind_memory_picture(self):
        with self.slot({}):
            pass


class AdmitMemoryKindsTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        os.environ["BUDGIT_LANG"] = "en"
        os.environ.pop("BUDGIT_SUITE_LOAD_LIMIT", None)

    def decide(self, m, kind, continuing=False):
        return admit.decide(m, kind, {}, continuing=continuing)

    def test_a_suite_on_memory_pressure_with_idle_cpu_is_refused_and_names_memory(self):
        got = self.decide(reading(mem=PRESSURE), "suite")
        self.assertEqual((got["verdict"], got["exit"]), ("refuse", 2))
        self.assertIn("memory pressure", got["reason"])

    def test_a_suite_with_memory_fine_and_idle_cpu_is_go(self):
        self.assertEqual(self.decide(reading(mem=CALM), "suite")["verdict"], "go")

    def test_a_suite_with_a_blind_memory_picture_is_never_refused_on_memory(self):
        got = self.decide(reading(free=None, mem={}), "suite")
        self.assertEqual(got["verdict"], "go")
        self.assertIn("could not be read", got["reason"])

    def test_an_urgent_continuing_suite_is_refused_on_memory_pressure(self):
        got = self.decide(reading(mem=PRESSURE), "suite", continuing=True)
        self.assertEqual(got["verdict"], "refuse")
        self.assertIn("memory pressure", got["reason"])

    def test_an_urgent_continuing_suite_on_cpu_alone_goes_with_the_lower_cap(self):
        got = self.decide(reading(load=7.5, mem=CALM), "suite", continuing=True)
        self.assertEqual(got["verdict"], "go")
        self.assertEqual(got["worker_cap"], 4)

    def test_one_memory_signal_reading_pressure_is_enough_even_when_the_others_read_fine(self):
        for signal in ({"pressure": 1, "compressor_mb": 14000, "total_mb": 16000, "free_true_mb": 8000},
                       {"pressure": 4, "compressor_mb": 100, "total_mb": 16000, "free_true_mb": 8000}):
            got = self.decide(reading(free=signal["free_true_mb"], mem=signal), "suite")
            self.assertEqual(got["verdict"], "refuse", signal)

    def test_a_lane_is_still_refused_on_memory_pressure(self):
        self.assertEqual(self.decide(reading(mem=PRESSURE), "lane")["verdict"], "refuse")

    def test_a_lane_at_warn_is_refused_unchanged(self):
        self.assertEqual(self.decide(reading(mem=WARN), "lane")["verdict"], "refuse")

    def test_a_suite_at_warn_with_calm_swap_and_compressor_goes(self):
        self.assertEqual(self.decide(reading(mem=WARN), "suite")["verdict"], "go")

    def test_a_suite_at_critical_is_refused(self):
        self.assertEqual(self.decide(reading(mem=PRESSURE), "suite")["verdict"], "refuse")

    def test_a_suite_at_warn_with_a_high_swap_in_rate_is_refused(self):
        got = self.decide(reading(mem=dict(WARN, swapin_mb_s=500.0)), "suite")
        self.assertEqual(got["verdict"], "refuse")
        self.assertIn("swap-in", got["reason"])

    def test_a_continuing_suite_follows_the_suite_rule_for_memory(self):
        self.assertEqual(self.decide(reading(mem=WARN), "suite", continuing=True)["verdict"], "go")
        self.assertEqual(self.decide(reading(mem=PRESSURE), "suite", continuing=True)["verdict"], "refuse")
        self.assertEqual(self.decide(reading(mem=dict(WARN, swapin_mb_s=500.0)), "suite", continuing=True)["verdict"], "refuse")

    def test_the_refusal_names_memory_in_spanish_without_a_ledger_id(self):
        os.environ["BUDGIT_LANG"] = "es"
        got = self.decide(reading(mem=PRESSURE), "suite")
        self.assertEqual(got["verdict"], "refuse")
        self.assertNotRegex(got["reason"], r"R-\d+")
        self.assertNotIn("unreadable", got["reason"])
