"""I-1820 (a): the known-flake rerun in the test runner, `admit --continuing`, and the bisect of a red merge group.
Every reading, runner and VCS call is injected: no test reads the live machine or creates a real worktree."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import runtests as rt  # noqa: E402
import admit  # noqa: E402

FLAKE = "test_raw_store.AssignmentAndDataTest.test_d39_echo_of_20k_words_is_fast_and_allowed"
LISTED = [{"test": FLAKE, "item": "R-1210", "why": "wall bound"}]
FAIL_LINE = "FAIL: test_d39_echo_of_20k_words_is_fast_and_allowed (test_raw_store.AssignmentAndDataTest)"
FAIL_LINE_311 = "FAIL: test_d39_echo_of_20k_words_is_fast_and_allowed (%s)" % FLAKE
OTHER_LINE = "FAIL: test_other (test_x.Case)"


class Reruns:
    def __init__(self, results):
        self.results, self.asked = list(results), []

    def __call__(self, test_id):
        self.asked.append(test_id)
        got = self.results.pop(0)
        if isinstance(got, BaseException):
            raise got
        return got


class FlakeRerunTest(unittest.TestCase):
    def test_a_listed_flake_that_fails_then_passes_is_a_flake_line_and_not_a_failure(self):
        rerun = Reruns([True])
        left, lines, n = rt.settle_flakes([FAIL_LINE], LISTED, rerun)
        self.assertEqual(left, [])
        self.assertEqual(lines, ["flake: %s (R-1210) passed on rerun" % FLAKE])
        self.assertEqual((n, rerun.asked), (1, [FLAKE]))

    def test_the_newer_unittest_line_shape_names_the_same_test(self):
        left, lines, n = rt.settle_flakes([FAIL_LINE_311], LISTED, Reruns([True]))
        self.assertEqual((left, n, len(lines)), ([], 1, 1))

    def test_the_same_test_failing_twice_counts(self):
        left, lines, n = rt.settle_flakes([FAIL_LINE], LISTED, Reruns([False]))
        self.assertEqual((left, lines, n), ([FAIL_LINE], [], 1))

    def test_a_red_test_not_on_the_list_is_never_rerun_and_counts(self):
        rerun = Reruns([])
        left, lines, n = rt.settle_flakes([OTHER_LINE], LISTED, rerun)
        self.assertEqual((left, lines, n, rerun.asked), ([OTHER_LINE], [], 0, []))

    def test_a_whole_module_on_the_list_never_matches_one_of_its_tests(self):
        with self.assertRaises(rt.FlakeListError):
            rt.parse_flakes([{"test": "test_raw_store", "item": "R-1210", "why": "x"}])

    def test_a_mixed_run_keeps_the_unlisted_failure(self):
        left, lines, n = rt.settle_flakes([FAIL_LINE, OTHER_LINE], LISTED, Reruns([True]))
        self.assertEqual((left, len(lines), n), ([OTHER_LINE], 1, 1))

    def test_a_rerun_that_was_interrupted_is_a_failure_never_a_flake_pass(self):
        for got in (None, OSError("killed"), RuntimeError("timeout")):
            left, lines, n = rt.settle_flakes([FAIL_LINE], LISTED, Reruns([got]))
            self.assertEqual((left, lines), ([FAIL_LINE], []), got)

    def test_a_keyboard_interrupt_during_the_rerun_reports_no_flake_pass(self):
        with self.assertRaises(KeyboardInterrupt):
            rt.settle_flakes([FAIL_LINE], LISTED, Reruns([KeyboardInterrupt()]))

    def test_the_summary_line_carries_the_number_of_flake_reruns(self):
        self.assertEqual(rt.flake_summary(2), "flake reruns: 2")


class FlakeListStartTest(unittest.TestCase):
    def write(self, data):
        d = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, d)
        p = os.path.join(d, "known_flakes.json")
        with open(p, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        self.addCleanup(os.remove, p)
        return p

    def test_the_shipped_list_is_valid_and_no_longer_names_the_d39_flake(self):
        # R-1282: the d39 bound reads CPU time now (helpers.fastest), so R-1210 left the list; the shipped file stays a valid list
        got = rt.load_flakes(os.path.join(ROOT, "tests", "known_flakes.json"))
        self.assertEqual(got, [])
        self.assertNotIn(FLAKE, [g["test"] for g in got])

    def test_a_malformed_entry_fails_closed(self):
        bad = [{"test": FLAKE, "item": "1210", "why": "x"}, {"test": FLAKE, "why": "x"}, {"test": FLAKE, "item": "R-1", "why": ""},
               {"test": "no_dots", "item": "R-1", "why": "x"}, "text", {"item": "R-1", "why": "x"}]
        for entry in bad:
            with self.assertRaises(rt.FlakeListError, msg=entry):
                rt.load_flakes(self.write([entry]))
        with self.assertRaises(rt.FlakeListError):
            rt.load_flakes(self.write("{not json"))
        with self.assertRaises(rt.FlakeListError):
            rt.load_flakes(self.write({"test": FLAKE}))

    def test_main_stops_with_a_nonzero_exit_before_running_when_the_list_is_malformed(self):
        old, rt.FLAKES = rt.FLAKES, self.write([{"test": FLAKE, "item": "oops", "why": "x"}])
        self.addCleanup(setattr, rt, "FLAKES", old)
        called = []
        old_sup, rt.supervise = rt.supervise, lambda *a, **k: called.append(1) or 0
        self.addCleanup(setattr, rt, "supervise", old_sup)
        self.assertNotEqual(rt.main(["runtests.py", "--workers", "1", "test_admit"]), 0)
        self.assertEqual(called, [])


class FakeLedger:
    def __init__(self, ids):
        self.ids = ids

    def __call__(self, project):
        return {"items": [{"id": i} for i in self.ids]}


def reading(load=1.0, free=8000, busy=1, cores=8, mem=None):
    m = {"cpu_load": load, "cores": cores, "free_mb": free, "busy": busy}
    if mem is not None:
        m["mem"] = mem
    return m


class ContinuingTest(unittest.TestCase):
    def setUp(self):
        self._lang = os.environ.get("BUDGIT_LANG")
        os.environ["BUDGIT_LANG"] = "en"
        os.environ.pop("BUDGIT_SUITE_LOAD_LIMIT", None)
        self.addCleanup(self.restore)

    def restore(self):
        if self._lang is None:
            os.environ.pop("BUDGIT_LANG", None)
        else:
            os.environ["BUDGIT_LANG"] = self._lang

    def test_continuing_on_a_cpu_busy_machine_is_go_with_a_lower_cap(self):
        for kind in ("suite", "lane"):
            got = admit.decide(reading(load=7.5), kind, {}, continuing=True)
            self.assertEqual((got["verdict"], got["exit"]), ("go", 0), kind)
            self.assertEqual(got["worker_cap"], 4, kind)
            self.assertIn("4", got["reason"])

    def test_continuing_on_a_machine_far_over_the_refusal_limit_still_goes_with_the_cap(self):
        got = admit.decide(reading(load=20.0), "suite", {}, continuing=True)
        self.assertEqual((got["verdict"], got["worker_cap"]), ("go", 4))

    def test_continuing_on_a_calm_machine_has_no_cap(self):
        got = admit.decide(reading(load=1.0), "suite", {}, continuing=True)
        self.assertEqual((got["verdict"], got["worker_cap"]), ("go", None))

    def test_continuing_under_memory_pressure_still_refuses_for_a_suite_too(self):
        for kind in ("suite", "lane"):                    # critical refuses both; warn refuses a lane only (a suite follows the scheduled-job level)
            got = admit.decide(reading(load=7.5, mem={"pressure": 4}), kind, {}, continuing=True)
            self.assertEqual((got["verdict"], got["exit"]), ("refuse", 2), kind)
            self.assertIsNone(got["worker_cap"])
        got = admit.decide(reading(load=7.5, mem={"pressure": 2}), "lane", {}, continuing=True)
        self.assertEqual(got["verdict"], "refuse")

    def test_plain_admit_on_a_cpu_busy_machine_waits_as_before(self):
        got = admit.decide(reading(load=7.5), "suite", {})
        self.assertEqual((got["verdict"], got["exit"]), ("wait", 1))
        self.assertNotIn("worker_cap", got)

    def test_a_bad_id_is_refused(self):
        for bad in ("", "go", "R-", "r-12", "R-12x", "R-1 ", "R-12; ls", "12"):
            self.assertIsNotNone(admit.continuing_refusal(bad, "p", FakeLedger(["R-12"])), bad)

    def test_an_id_the_ledger_does_not_hold_is_refused(self):
        self.assertIsNotNone(admit.continuing_refusal("R-99", "p", FakeLedger(["R-12"])))

    def test_an_unreadable_ledger_or_no_project_is_refused(self):
        def broken(project):
            raise OSError("no ledger")
        self.assertIsNotNone(admit.continuing_refusal("R-12", "p", broken))
        self.assertIsNotNone(admit.continuing_refusal("R-12", None, FakeLedger(["R-12"])))

    def test_a_listed_id_is_accepted(self):
        self.assertIsNone(admit.continuing_refusal("R-12", "p", FakeLedger(["R-12"])))

    def test_check_refuses_a_bad_id_without_measuring_the_machine(self):
        old = admit.measure
        admit.measure = lambda: self.fail("measured")
        self.addCleanup(setattr, admit, "measure", old)
        got = admit.check("suite", {}, "p", continuing="nope", ledger=FakeLedger(["R-12"]))
        self.assertEqual(got["verdict"], "refuse")
        self.assertNotEqual(got["exit"], 0)

    def test_the_messages_exist_in_english_and_spanish_and_name_no_item_id(self):
        import i18n
        for lang in ("en", "es"):
            os.environ["BUDGIT_LANG"] = lang
            for key in ("ad.r_continue", "ad.r_continue_bad", "ad.r_continue_unknown"):
                text = i18n.t(key, cap=3, cpu=90, mem=100)
                self.assertNotEqual(text, key)
                self.assertNotRegex(text, r"R-\d")


class FakeVcs:
    """A history of `n` commits c1..cn; modules are red from commit `red_from` on (1-based), or never."""

    def __init__(self, n, red_from, broken_at=None):
        self.commits = ["c%d" % i for i in range(1, n + 1)]
        self.red_from, self.broken_at = red_from, broken_at
        self.calls, self.at, self.removed, self.ran = [], None, False, []

    def rev_list(self, rng):
        self.calls.append(("rev_list", rng))
        return list(self.commits)

    def add(self, path, sha):
        self.calls.append(("add", sha))
        self.at = sha

    def checkout(self, path, sha):
        self.calls.append(("checkout", sha))
        self.at = sha

    def remove(self, path):
        self.calls.append(("remove", path))
        self.removed = True

    def run(self, path, modules):
        self.ran.append(self.at)
        if self.at == self.broken_at:
            return None
        return not (self.red_from and int(self.at[1:]) >= self.red_from)


class BisectTest(unittest.TestCase):
    def go(self, vcs):
        out = []
        code = rt.bisect("a..b", ["test_x"], vcs, "/tmp/wt-fake", say=out.append)
        return code, out

    def test_five_commits_red_from_the_fourth_prints_the_fourth(self):
        vcs = FakeVcs(5, 4)
        code, out = self.go(vcs)
        self.assertEqual(code, 0)
        self.assertEqual(out, ["first red commit: c4"])
        self.assertTrue(vcs.removed)
        self.assertLessEqual(len(vcs.ran), 4)               # a bisect, not a walk of all five

    def test_a_green_tip_prints_no_red_commit(self):
        vcs = FakeVcs(5, None)
        code, out = self.go(vcs)
        self.assertEqual((code, out), (0, ["no red commit"]))
        self.assertEqual(vcs.ran, ["c5"])
        self.assertTrue(vcs.removed)

    def test_a_run_that_could_not_finish_is_an_error_never_a_verdict(self):
        vcs = FakeVcs(5, 4, broken_at="c3")
        code, out = self.go(vcs)
        self.assertNotEqual(code, 0)
        self.assertFalse(any(o.startswith("first red") or o == "no red commit" for o in out))
        self.assertTrue(vcs.removed)

    def test_the_worktree_is_removed_even_when_the_runner_raises(self):
        vcs = FakeVcs(5, 4)
        vcs.run = lambda path, modules: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            rt.bisect("a..b", ["test_x"], vcs, "/tmp/wt-fake", say=lambda s: None)
        self.assertTrue(vcs.removed)

    def test_a_failing_cleanup_keeps_the_result_and_names_the_leftover_worktree(self):
        vcs = FakeVcs(5, 4)

        def broken(path):
            raise RuntimeError("git worktree failed")
        vcs.remove = broken
        code, out = self.go(vcs)
        self.assertEqual(code, 0)                                   # the bisect had its verdict
        self.assertEqual(out[0], "first red commit: c4")           # the result first
        self.assertIn("/tmp/wt-fake", out[1])                       # then the cleanup error on its own line
        self.assertIn("cleanup failed", out[1])

    def test_a_failing_cleanup_after_no_verdict_still_exits_non_zero(self):
        vcs = FakeVcs(5, 4, broken_at="c3")

        def broken(path):
            raise RuntimeError("x")
        vcs.remove = broken
        code, out = self.go(vcs)
        self.assertNotEqual(code, 0)

    def test_an_empty_range_is_reported_not_guessed(self):
        vcs = FakeVcs(0, None)
        code, out = self.go(vcs)
        self.assertNotEqual(code, 0)
        self.assertEqual(vcs.ran, [])

    def test_the_real_vcs_uses_a_detached_worktree_and_worktree_remove(self):
        calls = []
        v = rt.GitVcs(run=lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0, "stdout": "a\nb\n"})())
        v.add("/tmp/wt-fake", "abc")
        v.remove("/tmp/wt-fake")
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("worktree add --detach /tmp/wt-fake abc" in c for c in flat), flat)
        self.assertTrue(any("worktree remove" in c and "/tmp/wt-fake" in c for c in flat), flat)
        self.assertFalse(any(c[0] == "rm" for c in calls))


if __name__ == "__main__":
    unittest.main()
