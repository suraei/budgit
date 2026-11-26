"""R-1141: each Budgit job (keepalive, cleanup, standup) has exactly ONE scheduler. When a Claude app scheduled task covers a job
(a folder under <Claude config>/scheduled-tasks), Budgit installs no launch agent for it; health and status tell the truth: a job that is
off or covered reads "off", is no failure, and a stale log of a launch agent that is gone does not count.
Every case runs the CLI as a subprocess with a temp HOME, fake app-task folders, a LaunchAgents folder of the test's own and launchctl
switched off. The real ~/.claude, ~/.budgit and ~/Library are never read. R1141_PLUGIN points the run at another plugin tree (the
red-first check against the code before the change)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import housekeeping
import jobs

PLUGIN = os.environ.get("R1141_PLUGIN") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin")
BIN = os.path.join(PLUGIN, "bin", "budgit")
TASKS = {"keepalive": "budgit-keep-alive", "cleanup": "budgit-housekeeping", "standup": "budgit-daily-standup"}


class OneScheduler(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="r1141-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.state = os.path.join(self.home, ".budgit")
        self.agents = os.path.join(self.tmp, "LaunchAgents")
        self.proj = os.path.join(self.tmp, "proj")
        for d in (self.state, self.agents, self.proj):
            os.makedirs(d)
        self.env = {"HOME": self.home, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "BUDGIT_LAUNCH_AGENTS_DIR": self.agents,
                    "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off", "BUDGIT_LANG": "es"}

    # ---- fixtures
    def app_task(self, job):
        os.makedirs(os.path.join(self.home, ".claude", "scheduled-tasks", TASKS[job]))

    def plist(self, job):
        with open(os.path.join(self.agents, housekeeping.JOBS[job]["label"] + ".plist"), "w", encoding="utf-8") as f:
            f.write("<plist/>\n")

    def blocked_failure(self, job):
        """What the launcher leaves when macOS blocked a run: `<start> 2 blocked`."""
        name = housekeeping.launcher_fail_name(housekeeping.JOBS[job]["args"])
        with open(os.path.join(self.state, name), "w", encoding="utf-8") as f:
            f.write("%d 2 blocked\n" % int(time.time()))

    def yes(self, *names):
        with open(os.path.join(self.state, "setup-answers.json"), "w", encoding="utf-8") as f:
            json.dump({"rules": {"enabled": True, "off": [], "told": ""}, "answers": {n: "yes" for n in names}}, f)

    def cli(self, *args, lang="es", cwd=None):
        env = dict(self.env, BUDGIT_LANG=lang)
        return subprocess.run([sys.executable, BIN] + list(args), capture_output=True, text=True, timeout=120, env=env,
                              cwd=cwd or self.proj, stdin=subprocess.DEVNULL)

    def no_launchd_files(self):
        self.assertEqual(os.listdir(self.agents), [], "a launch agent was installed")

    # ---- (a) health
    def test_a_AJobThatIsOffReadsApagadaAndIsNoFailureEvenWithAStaleBlockedLog(self):
        self.blocked_failure("keepalive")                 # the log of a launch agent that was removed
        r = self.cli("job", "health")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for job in TASKS:
            self.assertIn("%s: apagada" % job, r.stdout)
        self.assertNotIn("bloquea", r.stdout)
        self.assertNotIn("never", r.stdout)
        self.assertEqual(self.cli("job", "health", lang="en").stdout.splitlines()[0], "cleanup: off")

    def test_a_AJobCoveredByAnAppTaskReadsCoveredAndHealthyInEnglishAndSpanish(self):
        for job in TASKS:
            self.app_task(job)
        self.blocked_failure("keepalive")
        es = self.cli("job", "health")
        en = self.cli("job", "health", lang="en")
        self.assertEqual((es.returncode, en.returncode), (0, 0), es.stdout + en.stdout)
        for job, task in TASKS.items():
            self.assertIn("%s: apagada (la cubre la tarea de la app %s)" % (job, task), es.stdout)
            self.assertIn("%s: off (covered by the app task %s)" % (job, task), en.stdout)

    def test_a_ARealFailureOfTheActiveLaunchAgentStillExitsOne(self):
        self.plist("keepalive")
        self.blocked_failure("keepalive")
        r = self.cli("job", "health", lang="en")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("keepalive (cannot run: macOS blocks access", r.stdout)

    def test_a_ALaunchAgentAndAnAppTaskForTheSameJobIsReportedAsRunningTwice(self):
        self.plist("keepalive")
        self.app_task("keepalive")
        r = self.cli("job", "health", lang="en")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("keepalive (runs twice: a launch agent and the app task budgit-keep-alive", r.stdout)

    # ---- (b) status
    def test_b_EachJobStatusPrintsItsOwnLineNeverTheCleanupLine(self):
        for job in ("keepalive", "standup"):
            r = self.cli("job", "status", job, lang="en")
            self.assertEqual(r.stdout.strip(), "%s: off (`budgit job on %s` switches it on)" % (job, job))
            self.assertNotIn("housekeeping", r.stdout)
            self.assertEqual(self.cli("job", "status", job).stdout.strip(), "%s: apagada (`budgit job on %s` la enciende)" % (job, job))
        self.assertIn("la limpieza diaria no está programada", self.cli("job", "status", "cleanup").stdout)

    def test_b_StatusOfACoveredJobNamesTheAppTask(self):
        self.app_task("standup")
        self.assertEqual(self.cli("job", "status", "standup", lang="en").stdout.strip(),
                         "standup: the app task budgit-daily-standup runs it, so Budgit installs no launch agent for it")

    # ---- (c) one scheduler: job on and setup never install launchd when an app task covers the job
    def test_c_JobOnInstallsNoLaunchAgentWhenAnAppTaskCoversTheJob(self):
        for job in TASKS:
            self.app_task(job)
        self.yes(*TASKS)
        for job, task in TASKS.items():
            with self.subTest(job=job):
                r = self.cli("job", "on", job, lang="en")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("the app task %s runs it, so Budgit installs no launch agent" % task, r.stdout)
                self.no_launchd_files()
        self.assertFalse(os.path.exists(os.path.join(self.state, "jobs.json")))

    def test_c_HousekeepingScheduleOnAlsoInstallsNothingWhenTheAppTaskCoversCleanup(self):
        self.app_task("cleanup")
        r = self.cli("housekeeping", "--schedule", "on", lang="en")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cleanup: the app task budgit-housekeeping runs it", r.stdout)
        self.no_launchd_files()

    def test_c_AnUncoveredJobIsNeverReportedAsCovered(self):
        self.app_task("standup")                          # another job's task does not cover keepalive
        self.yes("keepalive")
        r = self.cli("job", "on", "keepalive", lang="en")
        self.assertNotIn("app task", r.stdout)

    def test_c_TheSessionStartDefaultKeepAliveLeavesItToTheAppTask(self):
        import hook
        import postupdate
        self.app_task("keepalive")
        with mock.patch.dict(os.environ, {"HOME": self.home, "BUDGIT_HOME": "", "STUDIO_HOME": "", "BUDGIT_LAUNCH_AGENTS_DIR": self.agents,
                                          "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off", "BUDGIT_LANG": "en"}):
            os.environ.pop("BUDGIT_HOME"), os.environ.pop("STUDIO_HOME")
            with mock.patch.object(hook, "_context") as said:
                hook._default_keepalive(postupdate)
                self.assertEqual(postupdate.load()["answers"].get("keepalive"), "yes")
                hook._default_keepalive(postupdate)                   # the next session: nothing more is said
            self.assertEqual(said.call_count, 1)                      # one line, once
            self.assertIn("the app task budgit-keep-alive runs it", said.call_args[0][1])
        self.no_launchd_files()

    def test_c_TheSessionStartSaysAPrivacyFolderRefusalOnceAndInstallsNothing(self):
        import hook
        import postupdate
        docs = os.path.join(self.home, "Documents", "proj")
        os.makedirs(docs)
        with mock.patch.dict(os.environ, {"HOME": self.home, "BUDGIT_HOME": "", "STUDIO_HOME": "", "BUDGIT_LAUNCH_AGENTS_DIR": self.agents,
                                          "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off", "BUDGIT_LANG": "en"}), \
                mock.patch.object(os, "getcwd", return_value=docs):
            os.environ.pop("BUDGIT_HOME"), os.environ.pop("STUDIO_HOME")
            with mock.patch.object(hook, "_context") as said:
                hook._default_keepalive(postupdate)
                hook._default_keepalive(postupdate)                   # retried next session, but not said again
            self.assertEqual(said.call_count, 1)
            self.assertIn("macOS stops a scheduled launch agent from reading those folders", said.call_args[0][1])
            self.assertEqual(postupdate.load()["answers"].get("keepalive"), "later")
        self.no_launchd_files()

    def test_c_AMovedCoveringTaskFolderIsFoundUnderTheClaudeConfigFolder(self):
        cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(os.path.join(cfg, "scheduled-tasks", "budgit-keep-alive"))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            self.assertEqual(jobs.covering_task("keepalive"), "budgit-keep-alive")
            self.assertIsNone(jobs.covering_task("standup"))

    # ---- (c) a launch agent in a macOS privacy folder is refused, not installed to fail with exit 2
    def test_c_JobOnRefusesWhenTheProjectSitsInDocumentsInEnglishAndSpanish(self):
        docs = os.path.join(self.home, "Documents", "proj")
        os.makedirs(docs)
        self.yes("keepalive")
        en = self.cli("job", "on", "keepalive", lang="en", cwd=docs)
        es = self.cli("job", "on", "keepalive", lang="es", cwd=docs)
        self.assertEqual((en.returncode, es.returncode), (1, 1), en.stdout + es.stdout)
        self.assertIn("macOS stops a scheduled launch agent from reading those folders", en.stdout)
        self.assertIn("(1) move Budgit or the project out of that folder", en.stdout)
        self.assertIn("macOS impide que un agente programado lea esas carpetas", es.stdout)
        self.assertIn("(1) saca Budgit o el proyecto de esa carpeta", es.stdout)
        self.no_launchd_files()
        self.assertFalse(os.path.exists(os.path.join(self.state, "jobs.json")))

    def test_c_ScheduleRefusesWhenThePluginSitsInDownloads(self):
        plugin = os.path.join(self.home, "Downloads", "x", "plugin", "bin", "budgit")
        with mock.patch.dict(os.environ, {"HOME": self.home, "BUDGIT_LAUNCH_AGENTS_DIR": self.agents, "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off"}):
            code, key, fields = housekeeping.schedule("on", plugin, None, "standup")
        self.assertEqual((code, key), (1, "job.tcc_refused"))
        self.assertIn("Budgit (", fields["where"])
        self.no_launchd_files()


if __name__ == "__main__":
    unittest.main()
