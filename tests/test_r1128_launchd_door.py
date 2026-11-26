"""R-1128 / I-1713: a scheduled job runs EXACTLY as launchd runs it: /bin/sh <Budgit home>/bin/budgit-job job run NAME, cwd "/",
an environment of HOME and the short launchd PATH only (the real plist carries no BUDGIT_HOME, no PYTHONPATH, no WorkingDirectory).
The older door test runs the CLI directly from the user's folder with the whole test environment, so it never saw launchd's form.
Only test hooks are added (launchctl off, a LaunchAgents folder of the test's own), and `claude` is a stub in a folder Budgit looks in."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import housekeeping

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin")
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class LaunchdDoor(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="r1128-ld-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.state = os.path.join(self.home, ".budgit")                 # what HOME alone gives the Budgit home
        os.makedirs(os.path.join(self.state, "bin"))
        root = os.path.join(self.tmp, "Projects")
        proj = os.path.join(root, "p1")
        os.makedirs(os.path.join(proj, ".budgit"))
        with open(os.path.join(proj, ".budgit", "ledger.json"), "w", encoding="utf-8") as f:
            json.dump({"items": [], "acks": {}}, f)
        subprocess.run(["git", "-C", proj, "init", "-q"], check=True, capture_output=True)
        with open(os.path.join(self.state, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"projects_root": root}, f)
        claude_dir = os.path.join(self.home, ".local", "bin")           # a folder Budgit looks in: launchd's PATH has no claude
        os.makedirs(claude_dir)
        self.rec = os.path.join(self.tmp, "stub.log")
        stub = os.path.join(claude_dir, "claude")
        with open(stub, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho \"$(pwd -P)\" >> %s\nexit 0\n" % self.rec)
        os.chmod(stub, 0o755)
        self.agents = os.path.join(self.tmp, "LaunchAgents")
        os.makedirs(self.agents)
        self.env = {"HOME": self.home, "PATH": LAUNCHD_PATH,
                    "BUDGIT_LAUNCH_AGENTS_DIR": self.agents, "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off"}
        self.launcher = os.path.join(self.state, "bin", housekeeping.LAUNCHER)
        for job in housekeeping.JOBS.values():                           # R-1141: these tests play launchd running the jobs, so the agents are on
            with open(os.path.join(self.agents, job["label"] + ".plist"), "w", encoding="utf-8") as f:
                f.write("<plist/>\n")

    def write_launcher(self, root=PLUGIN, python=None, text=None):
        text = text or housekeeping.launcher_text(root, python or sys.executable)
        with open(self.launcher, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(self.launcher, 0o700)

    def launchd(self, *args):
        """The launch agent's command line: /bin/sh LAUNCHER args, from "/", with the minimal environment."""
        return subprocess.run(["/bin/sh", self.launcher] + list(args), capture_output=True, text=True, timeout=120, env=self.env, cwd="/",
                              stdin=subprocess.DEVNULL)

    def health(self):
        r = subprocess.run([sys.executable, "-I", "-S", os.path.join(PLUGIN, "bin", "budgit"), "job", "health"],
                           capture_output=True, text=True, timeout=120, env=self.env, cwd="/")
        return r.stdout

    def test_EveryScheduledJobRunsFromSlashWithTheMinimalEnvironment(self):
        pythons = [sys.executable] + [p for p in housekeeping.PYTHON_FALLBACKS if os.path.isfile(p)]     # the interpreter the launcher may pick
        for name, python in [(n, p) for n in ("keepalive", "standup") for p in pythons]:
            with self.subTest(job=name, python=python):
                self.write_launcher(python=python)
                r = self.launchd("job", "run", name)
                self.assertIn(r.returncode, (0, 75), "exit %s\nstdout: %s\nstderr: %s" % (r.returncode, r.stdout, r.stderr))
                health = self.health()
                self.assertRegex(health, r"%s: (last run \d{4}-\d\d-\d\d \d\d:\d\d, exit (0|75)|skipped: )" % name, health)

    def test_CleanupRunsFromSlashWithTheMinimalEnvironment(self):
        self.write_launcher()
        r = self.launchd(*housekeeping.JOBS["cleanup"]["args"])
        self.assertIn(r.returncode, (0, 11, 75),     # 11 is the cleanup's own recorded "machine busy" skip
                      "exit %s\nstdout: %s\nstderr: %s" % (r.returncode, r.stdout, r.stderr))
        self.assertRegex(self.health(), r"cleanup: last run \d{4}-\d\d-\d\d \d\d:\d\d, exit")

    def _assert_failure_shown(self, code):
        health = self.health()
        self.assertRegex(health, r"keepalive: last run \d{4}-\d\d-\d\d \d\d:\d\d, exit %d" % code, health)   # never "keepalive: never"
        self.assertNotIn("keepalive: never", health)

    def test_AFailureBeforeAnyPythonJobCodeShowsInJobHealth(self):
        """python missing (launcher exit 75), the plugin missing (75), budgit failing in its own startup (2): each is a failed run in `job health`."""
        text = housekeeping.launcher_text(PLUGIN, "/nonexistent/python3")
        for fallback in housekeeping.PYTHON_FALLBACKS:
            text = text.replace(fallback, "/nonexistent/python3")
        self.write_launcher(text=text)
        r = self.launchd("job", "run", "keepalive")
        self.assertEqual(r.returncode, 75, r.stderr)
        self._assert_failure_shown(75)
        self.write_launcher(root=os.path.join(self.tmp, "no-such-plugin"))
        self.assertEqual(self.launchd("job", "run", "keepalive").returncode, 75)
        self._assert_failure_shown(75)
        broken = os.path.join(self.tmp, "broken")
        os.makedirs(os.path.join(broken, "bin"))
        with open(os.path.join(broken, "bin", "budgit"), "w", encoding="utf-8") as f:
            f.write("import sys\nsys.stderr.write('usage: budgit\\n')\nsys.exit(2)\n")
        self.write_launcher(root=broken)
        r = self.launchd("job", "run", "keepalive")
        self.assertEqual(r.returncode, 2, r.stderr)
        self._assert_failure_shown(2)

    def test_ALaterGoodRunReplacesTheLauncherFailure(self):
        self.write_launcher(root=os.path.join(self.tmp, "no-such-plugin"))
        self.launchd("job", "run", "keepalive")
        self._assert_failure_shown(75)
        self.write_launcher()
        r = self.launchd("job", "run", "keepalive")
        self.assertIn(r.returncode, (0, 75), r.stderr)
        self.assertRegex(self.health(), r"keepalive: (last run \d{4}-\d\d-\d\d \d\d:\d\d, exit (0|75)|skipped: )")

    def test_ALauncherFailureFileThatIsALinkIsNeverFollowed(self):
        target = os.path.join(self.tmp, "victim")
        with open(target, "w", encoding="utf-8") as f:
            f.write("keep\n")
        os.symlink(target, os.path.join(self.state, "launcher-fail-job_run_keepalive"))
        self.write_launcher(root=os.path.join(self.tmp, "no-such-plugin"))
        self.launchd("job", "run", "keepalive")
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep\n")
        self.assertIn("keepalive: never", self.health())          # a link is no record

    # ---- the macOS privacy (TCC) refusal: "Operation not permitted" ----
    def fake_python(self, stderr_text):
        path = os.path.join(self.tmp, "fake-python3")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho %s >&2\nexit 2\n" % ('"' + stderr_text + '"'))
        os.chmod(path, 0o755)
        return path

    def fail_file(self):
        with open(os.path.join(self.state, "launcher-fail-job_run_keepalive"), encoding="utf-8") as f:
            return f.read()

    def in_process(self, lang):
        import unittest.mock
        return unittest.mock.patch.dict(os.environ, {"BUDGIT_HOME": self.state, "BUDGIT_LAUNCH_AGENTS_DIR": self.agents,
                                                     "BUDGIT_LANG": lang, "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off"})

    TCC = "/usr/bin/python3: can't open file '/Users/x/Documents/Dev/studio/plugin/bin/budgit': [Errno 1] Operation not permitted"

    def test_ATccRefusalIsKeptAsBlockedAndShowsAPlainLineInHealthAndTheDigestInEnglishAndSpanish(self):
        import jobs
        self.write_launcher(python=self.fake_python(self.TCC))
        r = self.launchd("job", "run", "keepalive")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Operation not permitted", r.stderr)                      # still copied on to the log
        self.assertRegex(self.fail_file(), r"^\d+ 2 blocked\n$")                # only numbers and one word, never the stderr text or a path
        self.env["BUDGIT_LANG"] = "en"
        health = self.health()
        self.assertIn("jobs needing attention: keepalive (cannot run: macOS blocks access to the folder where Budgit or the project sits)", health)
        self.assertNotIn("last exit 2", health)
        for lang, first, last in (("en", "macOS blocks access to the folder where Budgit or the project sits", "Full Disk Access"),
                                  ("es", "macOS bloquea el acceso a la carpeta donde están Budgit o el proyecto", "Acceso total al disco")):
            marker = os.path.join(self.state, "job-blocked-shown")
            if os.path.exists(marker):
                os.unlink(marker)
            with self.in_process(lang):
                lines = jobs.digest_lines()
                self.assertEqual(jobs.digest_lines(), [], "once a day")
            text = "\n".join(lines)
            self.assertIn(first, lines[0])
            order = [text.index(x) for x in ("(1)", "budgit job off keepalive", "(3)", last)]
            self.assertEqual(order, sorted(order), text)                         # move first, switch off second, Full Disk Access last
            self.assertRegex(text, r"(last option, and risky|última opción, y con riesgo)")

    def test_AnOrdinaryFailureStaysAFailureNotBlocked(self):
        self.write_launcher(python=self.fake_python("usage: budgit: error: argument op: invalid choice"))
        self.assertEqual(self.launchd("job", "run", "keepalive").returncode, 2)
        self.assertRegex(self.fail_file(), r"^\d+ 2\n$")
        self.env["BUDGIT_LANG"] = "en"
        health = self.health()
        self.assertIn("keepalive (last exit 2)", health)
        self.assertNotIn("macOS blocks", health)

    def test_TheFolderWarningNamesAProjectOrPluginUnderDocumentsAndNothingElse(self):
        import jobs
        home = os.path.join(self.tmp, "fakehome")
        budgit = os.path.join(PLUGIN, "bin", "budgit")
        for folder, expect in (("Documents/Dev/p", True), ("Desktop/p", True), ("Downloads", True), ("Library/Mobile Documents/com~apple~CloudDocs/p", True),
                               ("Dev/p", False), ("Documentsish/p", False), ("Library/Application Support/p", False)):
            cwd = os.path.join(home, folder)
            os.makedirs(cwd, exist_ok=True)
            text = jobs.blocked_folder_warning("keepalive", budgit, cwd=cwd, home=home, lang="en")
            self.assertEqual(bool(text), expect, (folder, text))
        plugin_in_docs = os.path.join(home, "Documents", "studio", "plugin", "bin", "budgit")
        os.makedirs(os.path.dirname(plugin_in_docs))
        self.assertIn("Full Disk Access", jobs.blocked_folder_warning("standup", plugin_in_docs, cwd=os.path.join(home, "Dev"), home=home, lang="en"))
        self.assertIn("Acceso total al disco", jobs.blocked_folder_warning("standup", plugin_in_docs, cwd="/", home=home, lang="es"))

    # ---- R-1146 for jobs: with a kernel pressure level, the compressor and swap-in limits are no skip reason ----
    def precheck_with(self, pressure, swapin_bytes=0):
        import unittest.mock
        import jobs
        first = {"pressure": pressure, "compressor_mb": 5222.0, "total_mb": 16384.0, "swapins_bytes": 0, "t": 0.0, "page": 4096}
        last = dict(first, swapins_bytes=swapin_bytes, t=10.0)
        claude = os.path.join(self.tmp, "claude-stub")
        open(claude, "w").close()
        with unittest.mock.patch("admission.mem_reading", side_effect=[first, last]), unittest.mock.patch("admit.cpu_median", return_value=5.0):
            return jobs.precheck("keepalive", level="none", claude=claude, todo=lambda name: False)

    def test_AJobRunsAtKernelLevelNormalWhateverTheCompressorOrSwapinSay(self):
        self.assertEqual(self.precheck_with(1), (True, "ok"))                              # compressor 5.1 of 16 GB, over its 30 % limit
        self.assertEqual(self.precheck_with(1, swapin_bytes=100 * 10 ** 9), (True, "ok"))  # and a swap-in rate over its limit
        self.assertEqual(self.precheck_with(2), (True, "ok"))                              # warn is no reason either

    def test_AJobSkipsAtCriticalAndTheLineShowsTheLevelAndTheCompressor(self):
        ok, why = self.precheck_with(4)
        self.assertFalse(ok)
        self.assertEqual(why, "machine busy: macOS memory pressure critical (compressor 5.1 GB of 16 GB)")

    def test_AJobWithNoKernelLevelKeepsTheCompressorLimit(self):
        ok, why = self.precheck_with(None)
        self.assertFalse(ok)
        self.assertIn("machine busy: compressor 5.1 GB of 16 GB RAM (limit 30 %)", why)

    def test_OtherCallersKeepTheirLimitsAtKernelLevelNormal(self):
        import unittest.mock
        import admit
        # R-1186 raised the limit to 50 % at a calm kernel level (TM ruling, 17 Nov)
        first = {"pressure": 1, "compressor_mb": 12000.0, "total_mb": 16384.0, "swapins_bytes": 0, "t": 0.0, "page": 4096}
        with unittest.mock.patch("admission.mem_reading", side_effect=[first, first]), unittest.mock.patch("admit.cpu_median", return_value=5.0):
            busy, why = admit.is_busy(window=1)
        self.assertTrue(busy)
        self.assertIn("compressor", why)

    def test_JobOnPrintsTheWarningOnceAndDoesNotRefuse(self):
        import unittest.mock
        import jobs
        home = os.path.join(self.tmp, "fakehome2")
        cwd = os.path.join(home, "Documents", "proj")
        os.makedirs(cwd)
        with unittest.mock.patch.object(jobs, "recorded_yes", return_value=True), \
                unittest.mock.patch("housekeeping.schedule", return_value=(0, "hk.sched_on", {"path": "x"})), \
                unittest.mock.patch.dict(os.environ, {"HOME": home, "BUDGIT_LANG": "en", "BUDGIT_HOME": self.state}), unittest.mock.patch("os.getcwd", return_value=cwd):
            code, text = jobs.turn_on("keepalive", os.path.join(PLUGIN, "bin", "budgit"))
        self.assertEqual(code, 0)
        self.assertEqual(text.count("Warning:"), 1, text)


if __name__ == "__main__":
    unittest.main()
