"""R-874: a job that did not do its job exits non-zero, is recorded, and shows in the report."""
import json
import os
import sys
import types
import unittest

from helpers import StudioTestCase
import jobhealth
import jobs

BUSY_NO = lambda: (False, "free")
RUN = dict(level="normal", busy=BUSY_NO, claude=sys.executable, todo=lambda n: False)


def runner(code):
    return lambda *a, **k: types.SimpleNamespace(returncode=code)


class ExitCodeTest(StudioTestCase):
    def test_NonZeroExitPassthrough(self):
        self.assertEqual(jobs.run("keepalive", runner=runner(2), **RUN), 2)
        self.assertEqual(jobhealth.load()["keepalive"]["exit"], 2)

    def test_SkipReturnsNonZero(self):
        self.assertEqual(jobs.run("keepalive", runner=runner(0), **dict(RUN, level="critical")), jobs.SKIPPED)
        self.assertNotEqual(jobs.SKIPPED, 0)
        self.assertEqual(jobhealth.load()["keepalive"]["exit"], jobs.SKIPPED)

    def test_RunnerErrorIsNonZero(self):
        def boom(*a, **k):
            raise OSError("Operation not permitted")
        self.assertEqual(jobs.run("keepalive", runner=boom, **RUN), jobs.FAILED)

    def test_HealthyRunReturnsZero(self):
        self.assertEqual(jobs.run("keepalive", runner=runner(0), **RUN), 0)
        e = jobhealth.load()["keepalive"]
        self.assertEqual((e["exit"], e["interval"]), (0, 7200))
        self.assertEqual(jobhealth.problems(now=e["last"] + 60, sched={}), [])


class MissedIntervalTest(StudioTestCase):
    def state(self, last, code=0, interval=3600):
        return {"j": {"exit": code, "last": last, "interval": interval}}

    def test_MissedIntervalFakeClock(self):
        t0 = 1_000_000.0
        self.assertEqual(jobhealth.problems(now=t0 + 2 * 3600, state=self.state(t0), sched={}), [])           # exactly 2x: still fine
        found = jobhealth.problems(now=t0 + 2 * 3600 + 1, state=self.state(t0), sched={})
        self.assertEqual([(n, k) for n, k, f in found], [("j", "missed")])

    def test_FailedExitListed(self):
        found = jobhealth.problems(now=1.0, state=self.state(0.5, code=2), sched={})
        self.assertEqual(found, [("j", "failed", {"code": 2})])

    def test_ClockSkewIsNotAMiss(self):
        self.assertEqual(jobhealth.problems(now=100.0, state=self.state(9_999_999.0), sched={}), [])

    def test_ScheduledNeverRan(self):
        found = jobhealth.problems(now=10 ** 7, state={}, sched={"keepalive": 0})
        self.assertEqual([(n, k) for n, k, f in found], [("keepalive", "never")])
        self.assertEqual(jobhealth.problems(now=100, state={}, sched={"keepalive": 0}), [])                  # just switched on

    def test_NoSchedulerHereDoesNotCrash(self):
        from helpers import patch_env
        agents = os.path.join(self.tmp, "agents")            # R-1141: a temp agents folder, never the real ~/Library/LaunchAgents
        os.makedirs(agents)
        patch_env(self, BUDGIT_LAUNCH_AGENTS_DIR=agents)
        self.assertIsInstance(jobhealth.scheduled(), dict)                                                    # Linux and Windows: {} (UNTESTED there)


class ReportLineTest(StudioTestCase):
    def test_ReportLineBothLanguages(self):
        state = {"keepalive": {"exit": 2, "last": 1000.0, "interval": 7200}}
        en = jobhealth.line(now=1100.0, state=state, sched={}, lang="en")
        es = jobhealth.line(now=1100.0, state=state, sched={}, lang="es")
        self.assertEqual(en, "jobs needing attention: keepalive (last exit 2)")
        self.assertEqual(es, "tareas programadas con problemas: keepalive (última salida 2)")

    def test_HealthyPrintsNothing(self):
        self.assertIsNone(jobhealth.line(now=1100.0, state={"k": {"exit": 0, "last": 1000.0, "interval": 7200}}, sched={}, lang="en"))


BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")


class SkipExitValueTest(StudioTestCase):
    def test_SkipPathReturnsExactly75(self):
        self.assertEqual(jobs.SKIPPED, 75)
        self.assertEqual(jobs.run("keepalive", runner=runner(0), **dict(RUN, level="critical")), 75)


class StatusLineCommandTest(StudioTestCase):
    def status(self):
        import subprocess
        self.init_project()
        agents = os.path.join(self.tmp, "agents")
        os.makedirs(agents)
        import housekeeping
        for job in housekeeping.JOBS.values():       # R-1141: launchd is on for every job, so a failed run counts
            with open(os.path.join(agents, job["label"] + ".plist"), "w", encoding="utf-8") as f:
                f.write("<plist/>\n")
        env = dict(os.environ, HOME=self.tmp, STUDIO_HOME=self.home, BUDGIT_LANG="en",
                   BUDGIT_LAUNCH_AGENTS_DIR=agents, STUDIO_LAUNCH_AGENTS_DIR=agents)
        r = subprocess.run([sys.executable, BIN, "status"], cwd=self.project, env=env, capture_output=True, text=True, timeout=60)
        return r.stdout

    def test_FailedJobPrintsLineInStatus(self):
        jobhealth.record("keepalive", 2)
        self.assertIn("jobs needing attention: keepalive (last exit 2)", self.status())

    def test_HealthyJobPrintsNothingInStatus(self):
        jobhealth.record("keepalive", 0)
        self.assertNotIn("jobs needing attention", self.status())


class HousekeepingRecordsTest(StudioTestCase):
    def load_bin(self):
        from importlib.machinery import SourceFileLoader
        return SourceFileLoader("budgit_cli_r874", BIN).load_module()

    def test_ApplyRecordsRunInJobState(self):
        from unittest import mock
        import housekeeping
        import procclose
        cli = self.load_bin()
        args = types.SimpleNamespace(schedule=None, last=False, only=None, apply=True, hours=None, json=False, quiet=True, at=None)
        report = {"totals": {"failed": 0}}
        with mock.patch.object(housekeeping, "run", return_value=report), mock.patch.object(housekeeping, "render", return_value="x\n"), \
                mock.patch.object(housekeeping, "save_last"), mock.patch.object(procclose, "sweep_orphans", return_value=[]):
            self.assertEqual(cli.cmd_housekeeping(args), 0)
        self.assertEqual(jobhealth.load()["cleanup"]["exit"], 0)


class StateFileTest(StudioTestCase):
    def test_MissingStateFile(self):
        self.assertEqual(jobhealth.load(), {})
        self.assertIsNone(jobhealth.line(sched={}, lang="en"))

    def test_CorruptStateFile(self):
        os.makedirs(os.path.dirname(jobhealth.path()), exist_ok=True)
        for text in ("{not json", "[1, 2]", json.dumps({"a": {"exit": "x", "last": 1, "interval": 5}, "b": 3})):
            with open(jobhealth.path(), "w") as f:
                f.write(text)
            self.assertEqual(jobhealth.load(), {})
        self.assertTrue(jobhealth.record("keepalive", 0))                      # a corrupt file is replaced by a good one
        self.assertIn("keepalive", jobhealth.load())

    def test_MissedFactorIsTwo(self):
        self.assertEqual(jobhealth.MISSED_FACTOR, 2)


if __name__ == "__main__":
    unittest.main()
