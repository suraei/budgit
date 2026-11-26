"""R-618: the pool runner's dynamic worker count and its collection (count) guard. Every input is mocked or injected."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import runtests as rt  # noqa: E402


class UsableCpusTest(unittest.TestCase):
    def test_process_cpu_count_wins(self):
        self.assertEqual(rt.usable_cpus(lambda: 6, lambda p: {0, 1}, lambda: 16), 6)

    def test_affinity_when_no_process_cpu_count(self):
        self.assertEqual(rt.usable_cpus(None, lambda p: {0, 1, 2}, lambda: 16), 3)

    def test_windows_has_neither_so_cpu_count(self):
        self.assertEqual(rt.usable_cpus(None, None, lambda: 12), 12)

    def test_unknown_count_is_one(self):
        self.assertEqual(rt.usable_cpus(None, None, lambda: None), 1)


class CgroupTest(unittest.TestCase):
    def files(self, d):
        return lambda path: d.get(path)

    def test_v2_quota(self):
        self.assertEqual(rt.cgroup_cpu_limit(self.files({"/sys/fs/cgroup/cpu.max": "200000 100000"})), 2.0)

    def test_v2_max_means_no_limit(self):
        self.assertIsNone(rt.cgroup_cpu_limit(self.files({"/sys/fs/cgroup/cpu.max": "max 100000"})))

    def test_v1_quota(self):
        d = {"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "150000", "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000"}
        self.assertEqual(rt.cgroup_cpu_limit(self.files(d)), 1.5)

    def test_v1_minus_one_means_no_limit(self):
        d = {"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1", "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000"}
        self.assertIsNone(rt.cgroup_cpu_limit(self.files(d)))

    def test_macos_has_no_cgroup(self):
        self.assertIsNone(rt.cgroup_cpu_limit(self.files({})))


class DecideWorkersTest(unittest.TestCase):
    def test_idle_eight_cores_leave_one_free_and_have_no_fixed_cap(self):
        n, why = rt.decide_workers(8, 8, cpu_pct=0, free_mb=16000, rss_mb=300)
        self.assertEqual(n, 7)
        self.assertIn("1 core free", why)
        n, _ = rt.decide_workers(64, 64, cpu_pct=0, free_mb=64000, rss_mb=300)
        self.assertEqual(n, 63)

    def test_other_load_does_not_shrink_the_pool(self):
        n, why = rt.decide_workers(8, 8, cpu_pct=50, free_mb=16000, rss_mb=300)
        self.assertEqual(n, 7)
        self.assertIn("50% busy", why)

    def test_decide_workers_never_returns_one_without_an_override(self):
        for cpus, cpu_pct, free in ((1, 0, 16000), (8, 0, 16000), (8, 100, 16000), (64, 100, 16000), (8, None, 16000),
                                    (8, 0, 700), (8, 0, 1100), (8, 100, 1100), (8, 0, 0)):
            n, why = rt.decide_workers(cpus, cpus, cpu_pct=cpu_pct, free_mb=free, rss_mb=500)
            self.assertGreaterEqual(n, 2, (cpus, cpu_pct, free, why))
        n, _ = rt.decide_workers(8, 8, cpu_pct=0, free_mb=16000, rss_mb=500, away=True, mem={"pressure": 4})
        self.assertGreaterEqual(n, 2)
        self.assertEqual(rt.decide_workers(8, 8, override=1)[0], 1)         # by hand, 1 still works

    def test_cgroup_quota_caps_the_cpus(self):
        n, why = rt.decide_workers(32, 32, quota=4.0, cpu_pct=0, free_mb=64000, rss_mb=300)
        self.assertEqual(n, 3)
        self.assertIn("cgroup quota", why)

    def test_affinity_style_small_machine(self):
        self.assertEqual(rt.decide_workers(3, 16, cpu_pct=0, free_mb=16000, rss_mb=300)[0], 2)

    def test_low_memory_caps_it(self):
        n, why = rt.decide_workers(16, 16, cpu_pct=0, free_mb=1024 + 3 * 500 + 100, rss_mb=500)
        self.assertEqual(n, 3)
        self.assertIn("memory", why)

    def test_memory_sizing_5024_free_500_peak_16_cpus_gives_8(self):
        self.assertEqual(rt.decide_workers(16, 16, cpu_pct=0, free_mb=5024, rss_mb=500)[0], 8)

    def test_memory_for_two_but_not_more_gives_two(self):
        self.assertEqual(rt.decide_workers(16, 16, cpu_pct=90, free_mb=2100, rss_mb=500)[0], 2)

    def test_a_fake_no_free_memory_machine_does_not_change_these_results(self):
        # every load read goes through an injected reader; the live readers answer "no memory" here and nothing moves
        from unittest import mock
        adm = rt._admission()
        before = (rt.decide_workers(16, 16, cpu_pct=0, free_mb=5024, rss_mb=500),
                  rt.choose_workers(None, {}, read=lambda: rt.Load(0.0, 9000, False, 0.0, None, None, None)))
        with mock.patch.object(adm, "free_mb", lambda: 0), mock.patch.object(adm, "mem_reading", lambda: {"pressure": 4}), \
                mock.patch.object(adm, "on_battery", lambda: True), mock.patch.object(rt, "process_table", lambda: []):
            after = (rt.decide_workers(16, 16, cpu_pct=0, free_mb=5024, rss_mb=500),
                     rt.choose_workers(None, {}, read=lambda: rt.Load(0.0, 9000, False, 0.0, None, None, None)))
        self.assertEqual(before, after)

    def test_memory_that_cannot_fit_two_waits_then_reports_and_never_runs_one(self):
        said, slept, now = [], [], [0.0]

        def sleep(s):
            slept.append(s)
            now[0] += s
        ok, why = rt.await_memory(lambda: 600, 500, timeout=30, poll=5, sleep=sleep, clock=lambda: now[0], say=said.append)
        self.assertFalse(ok)
        self.assertIn("600 MB free", why)
        self.assertEqual(len(said), 1)                          # the reason is printed once, up front
        self.assertIn("waiting for memory", said[0])
        self.assertEqual(sum(slept), 30)                        # bounded
        self.assertEqual(rt.MIN_WORKERS, 2)

    def test_memory_that_frees_up_while_waiting_lets_the_run_start(self):
        reads, now = iter([600, 700, 2100]), [0.0]

        def sleep(s):
            now[0] += s
        ok, _ = rt.await_memory(lambda: next(reads), 500, timeout=60, poll=5, sleep=sleep, clock=lambda: now[0], say=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(now[0], 10)

    def test_main_with_too_little_memory_exits_4_and_runs_no_worker(self):
        import contextlib
        import io
        from unittest import mock
        buf = io.StringIO()
        with mock.patch.object(rt, "read_load", return_value=rt.Load(0.0, 100, False, 0.0, None, None, None)), \
                mock.patch.object(rt, "MEM_WAIT_SECS", 0.0), mock.patch.object(rt, "supervise") as sup, \
                mock.patch.object(rt, "discover", return_value=["test_a", "test_b", "test_c"]), \
                mock.patch.dict(os.environ, {"BUDGIT_TEST_WORKERS": ""}), contextlib.redirect_stdout(buf):
            code = rt.main(["runtests.py", "test_a", "test_b", "test_c"])
        self.assertEqual(code, 4)
        self.assertIn("ABORT: memory", buf.getvalue())
        sup.assert_not_called()

    def test_the_default_rss_is_labelled(self):
        _, why = rt.decide_workers(8, 8, cpu_pct=0, free_mb=16000, rss_mb=None)
        self.assertIn("(default)", why)
        _, why = rt.decide_workers(8, 8, cpu_pct=0, free_mb=16000, rss_mb=250)
        self.assertNotIn("(default)", why)

    def test_away_halves(self):
        self.assertEqual(rt.decide_workers(10, 10, cpu_pct=0, free_mb=32000, rss_mb=300, away=True)[0], 4)

    def test_unreadable_load_is_said(self):
        self.assertIn("load unreadable", rt.decide_workers(8, 8, free_mb=16000, rss_mb=300)[1])

    def test_override_wins_and_minimum_is_one(self):
        self.assertEqual(rt.decide_workers(8, 8, override=12), (12, "override"))
        self.assertEqual(rt.decide_workers(8, 8, override=0)[0], 1)

    def test_a_heavy_modules_child_is_reserved_once_not_per_worker(self):
        # 8000 free - 1024 = 6976; per-worker 400: 17 workers by memory. Reserving 1779 once leaves 5197 // 400 = 12.
        self.assertEqual(rt.decide_workers(64, 64, cpu_pct=0, free_mb=8000, rss_mb=400)[0], 17)
        n, why = rt.decide_workers(64, 64, cpu_pct=0, free_mb=8000, rss_mb=400, heavy_mb=1779)
        self.assertEqual(n, 12)
        self.assertIn("1779 MB reserved once", why)

    def test_heavy_reserve_only_counts_modules_in_the_set_and_over_the_limit(self):
        by = {"test_float_build": 1779.0, "test_small": 500.0, "test_other_heavy": 1500.0}
        self.assertEqual(rt.heavy_reserve_mb({"test_float_build", "test_small"}, by), 1779.0)
        self.assertEqual(rt.heavy_reserve_mb({"test_small"}, by), 0)
        self.assertEqual(rt.heavy_reserve_mb({"test_other_heavy", "test_small"}, by), 1500.0)

    def test_env_override(self):
        self.assertEqual(rt.choose_workers(None, {"BUDGIT_TEST_WORKERS": "3"}), (3, "override"))
        self.assertEqual(rt.choose_workers(2, {"BUDGIT_TEST_WORKERS": "3"}), (2, "override"))


class RssUnitsTest(unittest.TestCase):
    def test_macos_reports_bytes_and_linux_kilobytes(self):
        self.assertEqual(rt.rss_to_mb(300 * 1024 * 1024, "darwin"), 300.0)
        self.assertEqual(rt.rss_to_mb(300 * 1024, "linux"), 300.0)
        self.assertEqual(rt.rss_to_mb(300 * 1024, "freebsd13"), 300.0)

    def test_the_worker_peak_is_its_own_not_the_childrens(self):
        import resource
        mine = rt.self_peak_mb()
        self.assertEqual(mine, rt.rss_to_mb(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        self.assertGreater(mine, 0)

    def test_margin_is_named(self):
        self.assertEqual(rt.RSS_MARGIN, 1.25)


class WorkerEnvTest(unittest.TestCase):
    def test_no_suite_lock_var_even_when_inherited_and_state_is_per_worker(self):
        old = os.environ.get("BUDGIT_SUITE_LOCK")
        os.environ["BUDGIT_SUITE_LOCK"] = "/x/outer.lock"
        try:
            with tempfile.TemporaryDirectory() as d:
                env, tmp = rt.worker_env(d, 0)
        finally:
            if old is None:
                os.environ.pop("BUDGIT_SUITE_LOCK", None)
            else:
                os.environ["BUDGIT_SUITE_LOCK"] = old
        # Key names only: a failing assertNotIn would print the whole environment, tokens included.
        self.assertFalse("BUDGIT_SUITE_LOCK" in env, "BUDGIT_SUITE_LOCK is set in the worker env")
        for key in ("TMPDIR", "HOME", "BUDGIT_HOME"):
            self.assertTrue(env[key].startswith(d), "%s is not under the worker folder" % key)
        self.assertFalse("PYTHONDONTWRITEBYTECODE" in env, "PYTHONDONTWRITEBYTECODE is set in the worker env")
        self.assertFalse("GIT_CONFIG_GLOBAL" in env, "GIT_CONFIG_GLOBAL is set in the worker env")


class CountGuardTest(unittest.TestCase):
    def test_short_count_aborts(self):
        self.assertIn("fewer than", rt.check_collection(90, 100, []))

    def test_full_count_passes(self):
        self.assertIsNone(rt.check_collection(100, 100, []))
        self.assertIsNone(rt.check_collection(120, 100, []))

    def test_an_import_error_aborts(self):
        self.assertIn("broke", rt.check_collection(100, 100, ["test_x"]))

    def test_a_fixture_module_with_a_missing_import_is_reported_by_a_worker(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_fix_broken.py"), "w") as f:
                f.write("import no_such_module_for_r618\n")
            out = os.path.join(d, "rec.json")
            env = dict(os.environ, PYTHONPATH=d)
            subprocess.run([sys.executable, os.path.join(ROOT, "tools", "runtests.py"), "--worker", out, "test_fix_broken"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=d)
            with open(out + ".collected") as f:
                got = json.load(f)
            self.assertEqual(got["count"], 0)
            self.assertEqual(got["broken"], ["test_fix_broken"])
            self.assertIn("broke", rt.check_collection(got["count"], 0, got["broken"]))


class IdEqualityTest(unittest.TestCase):
    """check_ids with mocked id lists: every kind of disagreement fails with its own named message."""

    def test_agreement_is_silent(self):
        self.assertEqual(rt.check_ids(["a", "b"], ["b", "a"], ["a", "b"]), [])

    def test_a_lost_id_is_named(self):
        got = " | ".join(rt.check_ids(["a", "b", "c"], ["a", "b"]))
        self.assertIn("never collected (lost id)", got)
        self.assertIn("c", got)

    def test_a_lost_id_at_run_time_is_named(self):
        got = " | ".join(rt.check_ids(["a", "b"], ["a", "b"], ["a"]))
        self.assertIn("collected but not run", got)

    def test_a_duplicated_id_is_named_for_collection_and_for_run(self):
        self.assertIn("duplicate id collected more than once", " | ".join(rt.check_ids(["a", "b"], ["a", "a", "b"])))
        self.assertIn("duplicate id run more than once", " | ".join(rt.check_ids(["a", "b"], ["a", "b"], ["a", "b", "b"])))

    def test_a_swapped_id_fails_in_both_directions(self):
        got = rt.check_ids(["a", "b"], ["a", "b"], ["a", "c"])
        text = " | ".join(got)
        self.assertEqual(len(got), 2)
        self.assertIn("collected but not run (lost or swapped id) (1): b", text)
        self.assertIn("run but not collected (extra or swapped id) (1): c", text)

    def test_an_extra_id_fails_too(self):
        self.assertIn("collected but not discovered (extra id)", " | ".join(rt.check_ids(["a", "b"], ["a", "b", "x"])))
        self.assertIn("run but not collected", " | ".join(rt.check_ids(["a"], ["a"], ["a", "x"])))


FIXTURES = {
    # load_tests: its ids must read the same at collection and at run time (Python 3.8 and later)
    "test_fx_ok": "import unittest\n\nclass T(unittest.TestCase):\n    def test_one(self): pass\n    def test_two(self): pass\n\n"
                  "def load_tests(loader, tests, pattern):\n    return tests\n",
    # skips at import: collected and run as one skipped placeholder, never a false FAIL
    "test_fx_skip": "import unittest\nraise unittest.SkipTest('not on this machine')\n",
    # a parameterised class name
    "test_fx_param": "import unittest\n\nfor n in ('a', 'b'):\n    globals()['Case_' + n] = type('Case_' + n, (unittest.TestCase,), "
                     "{'test_x': lambda self: None})\n",
    "test_fx_broken": "import no_such_module_for_r618\n",
    # a worker that ends its own process mid-run with exit 0: no result, so the run must not read as a pass
    "test_fx_die": "import os, unittest\n\nclass T(unittest.TestCase):\n    def test_die(self):\n        os._exit(0)\n",
    "test_fx_skiptest": "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n"
                        "    @unittest.skip('needs swiftc')\n    def test_compile(self): pass\n",
    # its test name carries the process id, so the loader's two passes disagree
    "test_fx_pid": "import os, unittest\n\nclass T(unittest.TestCase):\n    pass\n\nsetattr(T, 'test_p%d' % os.getpid(), lambda self: None)\n",
}


class MainPathsTest(unittest.TestCase):
    """rt.main end to end on fixture modules (the runner's tests dir and durations file are patched in-process)."""

    def run_main(self, *argv):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            for name, text in FIXTURES.items():
                with open(os.path.join(d, name + ".py"), "w") as f:
                    f.write(text)
            old = (rt.TESTS, rt.DURATIONS)
            rt.TESTS, rt.DURATIONS = d, os.path.join(d, ".durations.json")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = rt.main(["runtests.py"] + list(argv))
            finally:
                rt.TESTS, rt.DURATIONS = old
        return code, buf.getvalue().splitlines()

    def test_skip_at_import_load_tests_and_a_parameterised_class_all_pass(self):
        code, lines = self.run_main("--workers", "2", "test_fx_ok", "test_fx_skip", "test_fx_param")
        self.assertEqual(lines[0], "workers 2: override")
        self.assertIn("OK (1 NOT RUN)", lines)                       # the skipped placeholder is not a pass
        self.assertNotIn("OK", lines)
        self.assertEqual(code, 0)
        self.assertTrue(any(l.startswith("Ran 5 tests") for l in lines), lines)       # 2 + 1 skipped + 2

    def test_a_skipped_test_is_counted_named_and_not_passed(self):
        code, lines = self.run_main("--workers", "2", "test_fx_skiptest")
        self.assertEqual(code, 0)
        row = [l for l in lines if l.startswith("passed ")]
        self.assertEqual(len(row), 1, lines)
        self.assertIn("passed 1, skipped 1 (NOT RUN)", row[0])
        self.assertIn("test_fx_skiptest.T.test_compile", row[0])
        self.assertIn("OK (1 NOT RUN)", lines)
        self.assertNotIn("OK", lines)

    def test_more_than_twenty_skipped_names_end_in_plus_m_more(self):
        body = "import unittest\n\nclass T(unittest.TestCase):\n" + "".join(
            "    @unittest.skip('x')\n    def test_s%02d(self): pass\n" % i for i in range(23))
        FIXTURES["test_fx_many"] = body
        try:
            code, lines = self.run_main("--workers", "2", "test_fx_many")
        finally:
            del FIXTURES["test_fx_many"]
        row = [l for l in lines if l.startswith("passed ")][0]
        self.assertIn("skipped 23 (NOT RUN)", row)
        self.assertTrue(row.endswith("+3 more"), row)
        self.assertIn("OK (23 NOT RUN)", lines)

    def test_a_worker_that_ends_its_own_process_mid_run_is_a_fail_never_ok(self):
        code, lines = self.run_main("--workers", "2", "test_fx_ok", "test_fx_die")
        self.assertNotEqual(code, 0)
        self.assertFalse(any(l.startswith("OK") for l in lines), lines)

    def test_longest_first_order_uses_the_recorded_durations_and_unknown_modules_go_first(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("test_fx_ok", "test_fx_skip", "test_fx_param", "test_fx_new"):
                with open(os.path.join(d, name + ".py"), "w") as f:
                    f.write(FIXTURES.get(name, "x = 1\n" * 50))
            with open(os.path.join(d, ".durations.json"), "w") as f:
                json.dump({"test_fx_ok": 5.0, "test_fx_skip": 90.0, "test_fx_param": 20.0, "_peak_rss_mb": 300}, f)
            old = (rt.TESTS, rt.DURATIONS)
            rt.TESTS, rt.DURATIONS = d, os.path.join(d, ".durations.json")
            try:
                order = rt.schedule(["test_fx_ok", "test_fx_param", "test_fx_new", "test_fx_skip"])
            finally:
                rt.TESTS, rt.DURATIONS = old
        self.assertEqual(order, ["test_fx_new", "test_fx_skip", "test_fx_param", "test_fx_ok"])

    def test_a_module_never_scheduled_is_a_fail_never_ok_even_when_every_reported_module_passed(self):
        real = rt.run_queue

        def one_only(queue, launch, choose, initial, limit, **kw):
            first = list(queue)[0]                       # the other modules are never launched
            return 1, [first], [launch(first)]
        rt.run_queue = one_only
        try:
            code, lines = self.run_main("--workers", "2", "test_fx_ok", "test_fx_param")
        finally:
            rt.run_queue = real
        self.assertNotEqual(code, 0)
        self.assertNotIn("OK", lines)

    def test_a_broken_import_aborts_with_exit_3(self):
        code, lines = self.run_main("--workers", "1", "test_fx_ok", "test_fx_broken")
        self.assertEqual(code, 3)
        self.assertTrue(any(l.startswith("ABORT:") for l in lines), lines)

    def test_ids_that_differ_between_the_loaders_two_passes_exit_3_with_the_diff(self):
        code, lines = self.run_main("--workers", "1", "test_fx_pid")
        self.assertEqual(code, 3)
        self.assertTrue(any(l.startswith("ABORT:") for l in lines), lines)
        self.assertTrue(any("collected but not discovered (extra id)" in l for l in lines), lines)
        self.assertTrue(any("never collected (lost id)" in l for l in lines), lines)

    def test_workers_is_the_first_line_with_files_too_and_selected_comes_after(self):
        code, lines = self.run_main("--workers", "1", "--files", "tests/test_fx_ok.py")
        self.assertTrue(lines[0].startswith("workers 1:"), lines[:2])
        self.assertTrue(lines[1].startswith("selected 1 of "), lines[:2])
        self.assertEqual(code, 0)

    def test_workers_is_first_even_when_nothing_is_selected(self):
        code, lines = self.run_main("--workers", "1", "--files", "tools/zzz_no_test_names_this.py")
        self.assertTrue(lines[0].startswith("workers "), lines[:2])
        self.assertTrue(lines[1].startswith("selected 0 of "), lines[:2])
        self.assertEqual(code, 0)


class NoEnvDumpTest(unittest.TestCase):
    def test_no_runner_test_asserts_membership_on_a_whole_environment(self):
        with open(os.path.abspath(__file__)) as f:
            text = f.read()
        self.assertFalse(re.search(r"assert(Not)?In\(\"[A-Z_]+\", *env", text), "an assertIn/NotIn(.., env) would dump the environment")


if __name__ == "__main__":
    unittest.main()
