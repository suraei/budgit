"""R-1379 P1a rows 32 and 33: `budgit busy` answers within 5 s (exit 12 busy-unknown when it cannot), and the gate's full suite
runs through runtests workers, never in the gate's own process. Fakes only: no real system reading, no real sleep over 6 s."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import admission
import admit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_test as gt  # noqa: E402
import runtests as rt  # noqa: E402

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
LEAK = "plugin/scripts/leak.py"
CALM = {"pressure": 1, "psi": None, "winload": None, "free_true_mb": 9000, "total_mb": 16384, "compressor_mb": 100,
        "swapins_bytes": 0, "page": 16384, "t": 0.0, "unknown": []}
UNKNOWN_LINE = "busy-unknown: no answer after 5 s of reading (a reader or a subprocess is stuck); retry later"


def load_cli():
    loader = importlib.machinery.SourceFileLoader("budgit_cli_r1379", BUDGIT)
    spec = importlib.util.spec_from_loader("budgit_cli_r1379", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class CalmCPU:
    def __init__(self, *a, **k):
        pass

    def __call__(self):
        return 10.0


class StuckCPU:
    """A CPU sampler that sleeps 60 s (until the test's cleanup sets `release`)."""
    release = threading.Event()

    def __init__(self, *a, **k):
        pass

    def __call__(self):
        StuckCPU.release.wait(60)
        return 10.0


def run_cli(window, cpu):
    mod = load_cli()
    out = io.StringIO()
    with mock.patch.object(admission, "mem_reading", lambda: dict(CALM)), mock.patch.object(admission, "SystemCPU", cpu), \
            contextlib.redirect_stdout(out):
        code = mod.cmd_busy(types.SimpleNamespace(window=window))
    return code, out.getvalue().strip()


class BusyAnswersFast(unittest.TestCase):
    def setUp(self):
        StuckCPU.release = threading.Event()
        self.addCleanup(lambda: StuckCPU.release.set())

    def test_r1379_a_sampler_that_sleeps_60_s_gives_busy_unknown_and_exit_12_within_5_s(self):
        t0 = time.monotonic()
        code, line = run_cli(0, StuckCPU)
        took = time.monotonic() - t0
        self.assertEqual(code, 12)
        self.assertTrue(line.startswith("busy-unknown"), line)
        self.assertLess(took, 6.5)
        self.assertGreaterEqual(took, 4.5)             # the limit is the 5 s of work, not shorter

    def test_r1379_busy_check_gives_up_at_its_own_limit_and_reports_unknown(self):
        t0 = time.monotonic()
        state, why = admit.busy_check(timeout=0.3, sampler=lambda: StuckCPU.release.wait(60), sleep=lambda s: None, window=0,
                                      mem=lambda: dict(CALM))
        self.assertEqual(state, "unknown")
        self.assertTrue(why.startswith("busy-unknown"), why)
        self.assertLess(time.monotonic() - t0, 2)

    def test_r1379_the_instant_path_returns_under_2_s_with_fakes_and_takes_one_reading(self):
        t0 = time.monotonic()
        code, line = run_cli(None, CalmCPU)
        self.assertEqual((code, line), (0, "not busy"))
        self.assertLess(time.monotonic() - t0, 2)

    def test_r1379_no_window_means_window_0_and_an_explicit_window_is_passed_on(self):
        seen = []
        mod = load_cli()
        with mock.patch.object(admit, "busy_check", lambda **kw: seen.append(kw["window"]) or ("free", "calm")), \
                contextlib.redirect_stdout(io.StringIO()):
            mod.cmd_busy(types.SimpleNamespace(window=None))
            mod.cmd_busy(types.SimpleNamespace(window=60))
        self.assertEqual(seen, [0, 60])

    def test_r1379_exit_12_is_distinct_from_free_and_busy(self):
        self.assertEqual(admit.BUSY_UNKNOWN_EXIT, 12)
        self.assertNotIn(admit.BUSY_UNKNOWN_EXIT, (0, admit.BUSY_EXIT))
        self.assertEqual(admit.BUSY_EXIT, 10)
        code, _ = run_cli(0, CalmCPU)
        self.assertEqual(code, 0)


class ChildIsKilledByGroup(unittest.TestCase):
    def test_r1379_a_child_that_ignores_sigterm_is_killed_by_group_and_reaped_within_the_bound(self):
        made, groups = [], []
        real_popen, real_killpg = subprocess.Popen, os.killpg

        def spy_popen(*a, **k):
            p = real_popen(*a, **k)
            made.append((p, k.get("start_new_session")))
            return p

        def spy_killpg(pgid, sig):
            groups.append((pgid, sig))
            return real_killpg(pgid, sig)
        cmd = [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"]
        t0 = time.monotonic()
        with mock.patch.object(admission.subprocess, "Popen", spy_popen), mock.patch.object(admission.os, "killpg", spy_killpg):
            out = admission.run_bounded(cmd, timeout=0.5)
        took = time.monotonic() - t0
        self.assertIsNone(out)
        self.assertLess(took, 0.5 + 1 + 1)                       # the timeout plus the 1 s reap bound, with slack
        p, own_session = made[0]
        self.addCleanup(lambda: p.poll() is None and p.kill())
        self.assertTrue(own_session)
        self.assertEqual(groups, [(p.pid, signal.SIGKILL)])
        self.assertIsNotNone(p.poll())                           # reaped, not left as a child
        self.assertEqual(p.returncode, -signal.SIGKILL)

    def test_r1379_giving_up_kills_every_registered_child_and_starts_no_new_one(self):
        owner = admission.Owner()
        admission.CURRENT.owner = owner
        self.addCleanup(lambda: setattr(admission.CURRENT, "owner", None))
        killed = []
        child = mock.Mock(pid=999999)                 # hashable like a Popen (a SimpleNamespace is not, and children is a set)
        owner.children.add(child)
        with mock.patch.object(admission, "kill_tree", killed.append):
            owner.give_up()
        self.assertEqual(killed, [child])
        self.assertIsNone(admission.run_bounded([sys.executable, "-V"], 1))      # dead owner: nothing is started


class UnknownIsBusyWithRetryForEveryCaller(unittest.TestCase):
    """admit.is_busy maps an unknown answer to (True, 'busy-unknown: ...'); each caller's real code takes it as busy and retries."""

    def unknown(self):
        return mock.patch.object(admit, "busy_check", lambda **kw: ("unknown", UNKNOWN_LINE))

    def test_r1379_admit_is_busy_maps_unknown_to_busy(self):
        with self.unknown():
            self.assertEqual(admit.is_busy(window=1), (True, UNKNOWN_LINE))

    def test_r1379_suitewait_waits_and_rechecks_on_unknown_never_returns_free(self):
        import suitewait
        answers = iter([("unknown", UNKNOWN_LINE), ("unknown", UNKNOWN_LINE), ("free", "calm")])
        slept, said = [], []
        with mock.patch.object(admit, "busy_check", lambda **kw: next(answers)):
            first = suitewait.wait_not_busy(lambda: admit.is_busy(window=suitewait.BUSY_WINDOW), slept.append, said.append)
        self.assertTrue(first)
        self.assertEqual(len(slept), 2)
        self.assertTrue(all("busy-unknown" in s for s in said[:2]), said)

    def test_r1379_suitequeue_snapshot_says_busy_unknown(self):
        import suitequeue
        with self.unknown():
            self.assertEqual(suitequeue.busy_snapshot(), "busy: " + UNKNOWN_LINE)

    def test_r1379_housekeeping_busy_now_is_busy_with_the_unknown_reason(self):
        import housekeeping
        with self.unknown():
            self.assertEqual(housekeeping.busy_now(window=1), (True, UNKNOWN_LINE))

    def test_r1379_jobs_precheck_skips_with_a_machine_busy_reason_not_a_failure(self):
        import jobs
        with self.unknown():
            run, why = jobs.precheck("standup", level="ok", claude="/nonexistent", todo=lambda n: False)
        self.assertFalse(run)
        self.assertEqual(why, "machine busy: " + UNKNOWN_LINE)


class FullSuiteRunsThroughRuntests(unittest.TestCase):
    def gate(self, files, nested=None):
        ran, full = [], []
        env = {k: v for k, v in os.environ.items() if k != gt.NESTED_ENV}
        if nested:
            env[gt.NESTED_ENV] = "1"

        def fake_unittest(module=None, argv=None, exit=True):
            ran.extend(argv)
            return mock.Mock(result=mock.Mock(wasSuccessful=lambda: True))
        with mock.patch.object(gt, "prepare", return_value=list(sys.path)), \
                mock.patch.object(gt, "check_dataless", return_value=None), \
                mock.patch.object(gt, "branch_files", return_value=files), \
                mock.patch.object(gt.unittest, "main", fake_unittest), \
                mock.patch.object(gt, "run_full_suite", lambda mods, **k: full.append(list(mods)) or 0), \
                mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()):
            rc = gt.main(["test_a"])
        return rc, ran, full

    def test_r1379_the_gates_full_path_calls_runtests_and_not_unittest_in_process(self):
        rc, ran, full = self.gate([LEAK])
        self.assertEqual(rc, 0)
        self.assertEqual(len(full), 1)
        self.assertIn("test_livework", full[0])
        self.assertEqual(ran, [])                                 # unittest.main was not called in this process

    def test_r1379_run_full_suite_starts_the_runner_with_a_synced_free_tmpdir_and_the_nested_marker(self):
        calls = []
        with tempfile.TemporaryDirectory() as t:
            def fake_run(cmd, **kw):
                calls.append((cmd, kw))
                return types.SimpleNamespace(returncode=0)
            out = []
            rc = gt.run_full_suite(["test_a", "test_b"], runner="/x/runtests.py", run=fake_run, tmp=t, out=out.append)
            self.assertEqual(rc, 0)
            cmd, kw = calls[0]
            self.assertEqual(cmd[1:], ["/x/runtests.py", "test_a", "test_b"])
            self.assertEqual(kw["env"]["TMPDIR"], os.path.realpath(t))
            self.assertEqual(kw["env"][gt.NESTED_ENV], "1")
            self.assertEqual(gt.run_full_suite(["m"], runner="/x/r.py", run=lambda c, **k: types.SimpleNamespace(returncode=3),
                                               tmp=t, out=out.append), 1)         # an aborted run counts as red

    def test_r1379_the_full_suite_tmpdir_is_outside_documents_desktop_and_icloud(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as safe:
            for sub in ("Documents", "Desktop", os.path.join("Library", "Mobile Documents")):
                inside = os.path.join(home, sub, "scratch")
                os.makedirs(inside)
                self.assertTrue(gt.in_synced(inside, home), sub)
                self.assertEqual(gt.full_suite_tmp(tmp=inside, home=home, fallback=safe), os.path.realpath(safe), sub)
            self.assertEqual(gt.full_suite_tmp(tmp=safe, home=home, fallback="/nonexistent"), os.path.realpath(safe))
        real = gt.full_suite_tmp()
        self.assertIsNotNone(real)
        self.assertFalse(gt.in_synced(real))

    def test_r1379_the_nested_marker_stops_the_escalation_and_runs_only_the_named_modules(self):
        rc, ran, full = self.gate([LEAK], nested=True)
        self.assertEqual(rc, 0)
        self.assertEqual(full, [])
        self.assertIn("test_a", ran)
        self.assertNotIn("test_livework", ran)

    def test_r1379_runtests_workers_get_the_nested_marker(self):
        self.assertIn(gt.NESTED_ENV, rt.ENV_KEEP)

    def test_r1379_a_change_to_the_file_that_picks_or_runs_the_tests_gives_the_full_suite_reason(self):
        for changed in ("tools/gate_test.py", "tools/runtests.py"):
            why = gt.full_suite_reason(["docs/spec.md", changed])
            self.assertIn(changed, why)
            self.assertTrue(why, changed)
            mods, note = gt.with_full_suite(["test_a"], [changed])
            self.assertIn("full suite", note)
            self.assertGreater(len(mods), 1)
        self.assertEqual(gt.full_suite_reason(["docs/spec.md"]), "")         # an unrelated file stays selective
        self.assertIn("plugin/scripts/leak.py", gt.full_suite_reason(["plugin/scripts/leak.py"]))   # the first entry still triggers


class WorkersGetThePlainTempName(unittest.TestCase):
    """The gate hands the runner a resolved TMPDIR (/private/var/... on macOS); the workers' HOME and TMPDIR must still carry the plain name."""

    def test_r1379_plain_path_drops_private_only_when_it_is_the_same_folder(self):
        with tempfile.TemporaryDirectory() as t:
            real = os.path.realpath(t)
            got = rt.plain_path(real)
            self.assertFalse(got.startswith("/private/"), got)
            self.assertTrue(os.path.samefile(got, real))                         # the very same folder, only named without the prefix
        self.assertEqual(rt.plain_path("/usr/local/x"), "/usr/local/x")          # no prefix: untouched
        self.assertEqual(rt.plain_path("/private/nonexistent-r1379/x"), "/private/nonexistent-r1379/x")   # no such short name: untouched

    def test_r1379_a_resolved_tmpdir_gives_workers_a_home_that_has_no_private_prefix(self):
        with tempfile.TemporaryDirectory() as t:
            seen = {}

            def run(base, live):
                env, tmp = rt.worker_env(base, 1)
                seen.update(base=base, home=env["HOME"], tmp=tmp)
            with mock.patch.dict(os.environ, {"TMPDIR": os.path.realpath(t)}), mock.patch.object(tempfile, "tempdir", None):
                rt.supervise(run)
            self.assertFalse(seen["base"].startswith("/private/"), seen["base"])
            self.assertFalse(seen["home"].startswith("/private/"), seen["home"])
            self.assertTrue(seen["tmp"].startswith(seen["base"]))


class ModuleTimesAreWrittenAsEachModuleFinishes(unittest.TestCase):
    def test_r1379_the_times_file_gains_a_line_per_module_before_the_run_ends(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "last-module-times.jsonl")
            with open(path, "w") as f:
                f.write("stale\n")
            rt.start_module_log(path)
            self.assertEqual(open(path).read(), "")              # a new run starts a new list
            ends = {}
            for i, name in enumerate(("test_fast", "test_slow")):
                p = types.SimpleNamespace(pid=100 + i, returncode=0, wait=lambda: 0)
                rt.module_exited(p, name, time.time() - 3, ends, path=path)
                lines = [json.loads(x) for x in open(path).read().splitlines()]
                self.assertEqual([x["module"] for x in lines], ["test_fast", "test_slow"][:i + 1])   # one more line, run still going
            self.assertGreaterEqual(lines[1]["wall"], 3)
            self.assertEqual(lines[1]["exit"], 0)
            self.assertEqual(set(ends), {100, 101})

    def test_r1379_a_log_that_cannot_be_written_never_fails_the_run(self):
        self.assertFalse(rt.note_module_done("m", 1.0, 0, path="/nonexistent-dir-r1379/x.jsonl"))


if __name__ == "__main__":
    unittest.main()
