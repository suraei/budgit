"""R-1128 hotfix 1: a bug inside a guard never lets a destructive command through, never freezes ordinary work, and is never silent.
The real exported hook.py runs as a subprocess with real hook JSON on stdin. A sitecustomize on PYTHONPATH makes one named guard
function (rawguard.raw_reason) raise. Tests that inject the crash are tagged UNIT-INJECTED; the rest are OUTCOME."""
import os
import unittest

from outcome_harness import OutcomeCase


class FailClosed(OutcomeCase):
    def setUp(self):
        super().setUp()
        self.crashdir = os.path.join(self.tmp, "crash")
        os.makedirs(self.crashdir)
        with open(os.path.join(self.crashdir, "sitecustomize.py"), "w", encoding="utf-8") as f:
            f.write("import rawguard\n"
                    "def raw_reason(*a, **k):\n"
                    "    raise RuntimeError('injected guard crash')\n"
                    "rawguard.raw_reason = raw_reason\n")

    def crash(self, event, payload):
        scripts = os.path.join(self.export, "plugin", "scripts")
        return self.run_hook(event, payload, extra_env={"PYTHONPATH": self.crashdir + os.pathsep + scripts})

    def bash(self, command, crashed=True):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        return self.crash("pre-tool-use", payload) if crashed else self.run_hook("pre-tool-use", payload)

    def log(self):
        path = os.path.join(self.bhome, "hooks.log")
        return open(path, encoding="utf-8").read() if os.path.exists(path) else ""

    def test_force_push_is_denied_when_a_guard_crashes(self):
        """UNIT-INJECTED"""
        out = self.bash("git push --force origin main")
        self.assertEqual(self.decision(out), "deny", (out, self.last_hook.stderr))

    def test_reset_hard_is_denied_when_a_guard_crashes(self):
        """UNIT-INJECTED"""
        out = self.bash("git reset --hard")
        self.assertEqual(self.decision(out), "deny", (out, self.last_hook.stderr))

    def test_write_into_budgit_home_is_denied_when_a_guard_crashes(self):
        """UNIT-INJECTED"""
        target = os.path.join(self.bhome, "config.json")
        out = self.crash("pre-tool-use", {"tool_name": "Write", "tool_input": {"file_path": target, "content": "{}"}})
        self.assertEqual(self.decision(out), "deny", (out, self.last_hook.stderr))

    def test_a_read_and_git_status_are_allowed_when_a_guard_crashes(self):
        """UNIT-INJECTED"""
        out = self.crash("pre-tool-use", {"tool_name": "Read", "tool_input": {"file_path": os.path.join(self.project, "README.md")}})
        self.assertNotIn(self.decision(out), ("deny", "ask"), (out, self.last_hook.stderr))
        out = self.bash("git status")
        self.assertNotIn(self.decision(out), ("deny", "ask"), (out, self.last_hook.stderr))

    def test_each_fallback_run_prints_the_user_line_and_logs_the_guard_and_the_error(self):
        """UNIT-INJECTED"""
        for command in ("git status", "git push --force origin main"):
            out = self.bash(command)
            line = out.get("systemMessage", "")
            self.assertIn("raw_reason", line, (command, out))
            self.assertIn("only the basic protections", line, (command, out))
        log = self.log()
        self.assertIn("raw_reason", log)
        self.assertIn("injected guard crash", log)

    def test_without_a_crash_the_normal_path_still_denies_the_destructive_command(self):
        """OUTCOME (control)"""
        out = self.bash("git push --force origin main", crashed=False)       # the normal path asks the user; it never allows
        self.assertIn(self.decision(out), ("ask", "deny"), (out, self.last_hook.stderr))
        self.assertNotIn("only the basic protections", out.get("systemMessage", ""))

    def test_without_a_crash_a_write_into_budgit_home_is_still_denied(self):
        """OUTCOME (control)"""
        target = os.path.join(self.bhome, "config.json")
        out = self.run_hook("pre-tool-use", {"tool_name": "Write", "tool_input": {"file_path": target, "content": "{}"}})
        self.assertEqual(self.decision(out), "deny", (out, self.last_hook.stderr))

    def test_an_unparsed_payload_stays_fail_open_but_is_not_silent(self):
        """UNIT-INJECTED: the payload cannot be parsed, so there is nothing to decide on"""
        import subprocess
        import sys
        r = subprocess.run([sys.executable, os.path.join(self.export, "plugin", "scripts", "hook.py"), "pre-tool-use"],
                           input="not json", capture_output=True, text=True, env=self.env(), timeout=120)
        self.assertEqual(r.returncode, 0)
        self.assertIn("enforcement off", r.stdout)


class LibraryFailureLog(OutcomeCase):
    """R-1128 hotfix 1: when Budgit's own library cannot load, the fallback appends ONE line to hooks.log (time, event, cause, error
    type), never the command, and a destructive Bash is still denied. studiolib is made unimportable by a sitecustomize."""

    def test_a_library_that_cannot_load_is_logged_without_the_command_and_still_denies(self):
        """UNIT-INJECTED"""
        crashdir = os.path.join(self.tmp, "nolib")
        os.makedirs(crashdir)
        with open(os.path.join(crashdir, "sitecustomize.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nsys.modules['studiolib'] = None\n")
        scripts = os.path.join(self.export, "plugin", "scripts")
        command = "git push --force origin SECRETBRANCHNAME"
        out = self.run_hook("pre-tool-use", {"tool_name": "Bash", "tool_input": {"command": command}},
                            extra_env={"PYTHONPATH": crashdir + os.pathsep + scripts})
        self.assertEqual(self.decision(out), "deny", (out, self.last_hook.stderr))
        with open(os.path.join(self.bhome, "hooks.log"), encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ pre-tool-use: .*library cannot load.*\(ModuleNotFoundError\)")
        self.assertNotIn("SECRETBRANCHNAME", lines[0])
        self.assertNotIn("git push", lines[0])


class JobsIgnoreCpu(unittest.TestCase):
    """R-1128 hotfix 1: a scheduled job never skips on CPU; only critical memory pressure or the swap limits skip it."""

    @staticmethod
    def real_check(pressure):
        import admit
        real = admit.is_busy

        def check(**kw):
            return real(sampler=lambda: 95.0, sleep=lambda s: None, window=1, mem=lambda: {"pressure": pressure}, **kw)
        return check

    def test_the_standup_runs_with_cpu_at_95_percent_and_warn_memory(self):
        import sys
        from unittest import mock
        import admit
        import jobs
        with mock.patch.object(admit, "is_busy", self.real_check(2)):
            ok, why = jobs.precheck("standup", level="normal", claude=sys.executable, todo=lambda n: False)   # todo True = "nothing to do"
        self.assertTrue(ok, why)

    def test_critical_memory_still_skips_a_job(self):
        import sys
        from unittest import mock
        import admit
        import jobs
        with mock.patch.object(admit, "is_busy", self.real_check(4)):
            ok, why = jobs.precheck("standup", level="normal", claude=sys.executable, todo=lambda n: False)
        self.assertFalse(ok)
        self.assertIn("machine busy", why)
        self.assertIn("memory", why.lower())          # the skip is for memory pressure, never for CPU or another reason
        self.assertNotIn("CPU", why)

    def test_other_callers_keep_cpu_as_a_busy_reason(self):
        import admit
        busy, why = admit.is_busy(sampler=lambda: 95.0, sleep=lambda s: None, window=1, mem=lambda: {"pressure": 1})
        self.assertTrue(busy, why)


if __name__ == "__main__":
    unittest.main()
