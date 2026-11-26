"""R-1358 lane B: the outside-model use log, the weekly stand-up line, `ext-review --check` and the auto fallback.

Only fake `codex`, `gemini` and `security` scripts in a tmp dir are ever run; the real CLIs are never started."""
import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from unittest import mock

import extreview as ev
import extuse
import timelines

FAKE_KEY = "FAKEKEY-Zq81xLmN4pWv-do-not-print"
SECRET_PROMPT = "SECRETPROMPT-please-review-the-vault"
SECRET_DIFF = "+SECRETDIFFLINE = 1"
SECRET_ANSWER = "SECRETANSWER looks fine"


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        self.project = os.path.join(self.d, "my-project")
        os.makedirs(self.project)
        self._old = {k: os.environ.get(k) for k in ("PATH", "STUDIO_HOME", "BUDGIT_HOME", "GEMINI_API_KEY")}
        os.environ.pop("BUDGIT_HOME", None)     # BUDGIT_HOME wins over STUDIO_HOME (studiolib.env): left set it sends the codex-limit record to a shared home
        os.environ["STUDIO_HOME"] = os.path.join(self.d, "home")
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        os.environ["PATH"] = self.bin + os.pathsep + os.environ.get("PATH", "")
        self.diff = os.path.join(self.d, "d.diff")
        with open(self.diff, "w") as f:
            f.write("diff --git a/x b/x\n" + SECRET_DIFF + "\n")
        self.out = os.path.join(self.d, "verdict.txt")
        self.lines = []

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._t.cleanup()

    def which(self, name):          # only the fakes: never the real CLIs or the real keychain
        return shutil.which(name, path=self.bin)

    def fake(self, tool, body):
        p = os.path.join(self.bin, tool)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def review(self, tool="auto", timeout=None):
        return ev.review(tool, self.diff, SECRET_PROMPT, self.out, timeout, which=self.which, grace=0.3,
                         say=self.lines.append, cwd=self.project)

    def log_rows(self):
        import studiolib as sl
        path = os.path.join(sl.studio_dir(self.project), extuse.LOG_NAME)
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]


GEMINI_OK = 'echo "%s"\n' % SECRET_ANSWER
GEMINI_QUOTA = 'echo "Quota exceeded for this key" >&2\nexit 1\n'
CODEX_OK = 'while [ "$1" != "-o" ]; do shift; done\necho "%s" > "$2"\n' % SECRET_ANSWER
CODEX_LIMIT = 'echo "You\'ve hit your usage limit. Try again later." >&2\nexit 1\n'
HANG = "exec sleep 30\n"


class UseLogTest(Base):
    def test_log_line_has_only_the_named_fields_and_no_content(self):
        self.fake("gemini", GEMINI_OK)
        self.assertEqual(self.review("gemini"), ev.EXIT_OK)
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"ts", "project", "tool", "purpose", "outcome", "seconds"})
        self.assertEqual(rows[0]["project"], "my-project")
        self.assertEqual((rows[0]["tool"], rows[0]["outcome"]), ("gemini", "ok"))
        self.assertIsInstance(rows[0]["seconds"], (int, float))
        import studiolib as sl
        with open(os.path.join(sl.studio_dir(self.project), extuse.LOG_NAME)) as f:
            raw = f.read()
        for secret in (SECRET_PROMPT, SECRET_DIFF, SECRET_ANSWER, FAKE_KEY, self.d):
            self.assertNotIn(secret, raw)

    def test_a_failed_run_is_logged_with_its_class(self):
        self.fake("gemini", GEMINI_QUOTA)
        self.review("gemini")
        self.assertEqual([(r["tool"], r["outcome"]) for r in self.log_rows()], [("gemini", "quota")])


class NoLeakTest(Base):
    def test_a_run_without_cwd_leaves_the_cwd_project_untouched(self):
        import extuse_isolation
        import studiolib as sl
        real_cwd = os.path.join(self.d, "cwd-project")
        os.makedirs(real_cwd)
        sink = extuse_isolation.isolate(self)
        self.fake("gemini", GEMINI_OK)
        old = os.getcwd()
        os.chdir(real_cwd)
        try:
            rc = ev.review("gemini", self.diff, SECRET_PROMPT, self.out, None, which=self.which, grace=0.3, say=self.lines.append)
        finally:
            os.chdir(old)
        self.assertEqual(rc, ev.EXIT_OK)
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(real_cwd), extuse.LOG_NAME)))
        self.assertTrue(os.path.exists(os.path.join(sl.studio_dir(sink), extuse.LOG_NAME)))


class StandupLineTest(Base):
    NOW = 1_800_000_000

    def write(self, rows):
        import studiolib as sl
        path = os.path.join(sl.ensure_studio_dir(self.project), extuse.LOG_NAME)
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def row(self, tool, outcome, secs, days_ago):
        return {"ts": self.NOW - days_ago * 86400, "project": "my-project", "tool": tool, "purpose": "review",
                "outcome": outcome, "seconds": secs}

    def test_standup_block_counts_the_last_seven_days_per_tool(self):
        self.write([self.row("codex", "ok", 100, 1), self.row("codex", "quota", 5, 2), self.row("codex", "quota", 5, 3),
                    self.row("gemini", "ok", 40, 0.5), self.row("gemini", "ok", 999, 9)])      # the last one is too old
        text = timelines.standup_block(projects=[self.project], now=self.NOW)
        line = [l for l in text.splitlines() if "codex" in l][0]
        self.assertIn("codex 3 runs", line)
        self.assertIn("quota 2", line)
        self.assertIn("110 s", line)
        self.assertIn("gemini 1 run", line)
        self.assertNotIn("999", line)
        self.assertEqual(len(extuse.week_line([self.project], self.NOW).splitlines()), 1)

    def test_standup_is_silent_when_the_log_is_empty(self):
        self.assertEqual(extuse.week_line([self.project], self.NOW), "")
        self.write([])
        self.assertEqual(extuse.week_line([self.project], self.NOW), "")
        self.assertNotIn("codex", timelines.standup_block(projects=[self.project], now=self.NOW))


class CheckTest(Base):
    def lines_for(self, timeout=1):
        return ev.check(which=self.which, environ=dict(os.environ), timeout=timeout)

    def find(self, lines, tool):
        return [l for l in lines if l.startswith(tool)][0]

    def test_logged_in(self):
        self.fake("codex", "exit 0\n")
        self.fake("gemini", GEMINI_OK)
        lines = self.lines_for()
        self.assertIn("installed", self.find(lines, "codex"))
        for tool in ("codex", "gemini"):
            self.assertIn(", logged in,", self.find(lines, tool))
            self.assertNotIn("not logged in", self.find(lines, tool))
        self.assertIn("quota ok", self.find(lines, "gemini"))

    def test_not_installed(self):
        lines = self.lines_for()
        self.assertIn("not installed", self.find(lines, "codex"))
        self.assertIn("not installed", self.find(lines, "gemini"))

    def test_logged_out(self):
        self.fake("codex", 'echo "Not logged in" >&2\nexit 1\n')
        self.fake("gemini", 'echo "Please set an auth method" >&2\nexit 1\n')
        lines = self.lines_for()
        self.assertIn("not logged in", self.find(lines, "codex"))
        self.assertIn("not logged in", self.find(lines, "gemini"))

    def test_quota_reached(self):
        self.fake("codex", 'echo "You\'ve hit your usage limit." >&2\nexit 1\n')
        self.fake("gemini", GEMINI_QUOTA)
        lines = self.lines_for()
        self.assertIn("quota reached", self.find(lines, "codex"))
        self.assertIn("quota reached", self.find(lines, "gemini"))

    def test_hung_probe_is_a_waiting_prompt(self):
        self.fake("codex", HANG)
        self.fake("gemini", HANG)
        t0 = time.monotonic()
        lines = self.lines_for(timeout=1)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertIn("a prompt is waiting / hung", self.find(lines, "gemini"))
        self.assertIn("a prompt is waiting / hung", self.find(lines, "codex"))

    def test_openrouter_key_states(self):
        self.fake("security", "exit 44\n")
        self.assertIn("not logged in", self.find(self.lines_for(), "openrouter"))
        self.fake("security", 'echo "%s"\n' % FAKE_KEY)
        line = self.find(self.lines_for(), "openrouter")
        self.assertIn("logged in", line)
        self.assertNotIn(FAKE_KEY, line)

    def test_no_key_or_probe_text_in_the_output(self):
        self.fake("codex", 'echo "token=%s" >&2\necho "$GEMINI_API_KEY" >&2\nexit 1\n' % FAKE_KEY)
        self.fake("gemini", 'echo "auth failed key=$GEMINI_API_KEY" >&2\necho "$GEMINI_API_KEY"\nexit 1\n')
        self.fake("security", 'echo "%s"\n' % FAKE_KEY)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev.print_check(which=self.which, environ=dict(os.environ), timeout=1, say=print)
        self.assertNotIn(FAKE_KEY, buf.getvalue())
        self.assertNotIn("Zq81xLmN4pWv", buf.getvalue())
        self.assertTrue(buf.getvalue().strip())


class HardeningTest(Base):
    def test_ignore_template_and_repo_ignore_list_hold_the_use_log(self):
        import studiolib as sl
        self.assertIn("extuse.jsonl", sl._GITIGNORE_FILES)
        self.assertTrue(any("extuse.jsonl" in l for l in sl.gitignore_lines(".budgit")))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".gitignore")) as f:
            self.assertIn(".studio/extuse.jsonl", f.read().split())

    def test_the_probe_env_blocks_a_browser(self):
        rec = os.path.join(self.d, "env.txt")
        self.fake("gemini", 'echo "$NO_BROWSER $BROWSER" > "%s"\nexit 0\n' % rec)
        ev.check(which=self.which, environ=dict(os.environ), timeout=2)
        with open(rec) as f:
            self.assertEqual(f.read().split(), ["1", "true"])


TIMEOUT = ("timeout", "", "timed out after 1 s, no verdict")
QUOTA = ("unavailable", "", "no verdict: quota exceeded")
OK = ("ok", SECRET_ANSWER, "")


class AutoFallbackTest(Base):
    """The tool outcomes are scripted (run_tool is replaced): no real wait, no process start, so load cannot change them.
    The real timeout and quota detection of run_tool is covered by test_extreview and test_r1051_ext_review."""

    def script(self, **outcomes):
        self.started = []

        def run_tool(tool, path, *a, **k):
            self.started.append(tool)
            return outcomes[tool]
        patcher = mock.patch.object(ev, "run_tool", run_tool)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_usage_limit_moves_to_the_next_tool_and_each_attempt_is_logged(self):
        self.fake("gemini", "")
        self.fake("codex", "")
        self.script(gemini=QUOTA, codex=OK)
        self.assertEqual(self.review("auto"), ev.EXIT_OK)
        self.assertEqual(self.started, ["gemini", "codex"])
        self.assertEqual([(r["tool"], r["outcome"]) for r in self.log_rows()], [("gemini", "quota"), ("codex", "ok")])
        with open(self.out) as f:
            self.assertIn("SECRETANSWER", f.read())

    def test_codex_usage_limit_after_gemini_timeout(self):
        self.fake("gemini", "")
        self.fake("codex", "")
        self.script(gemini=TIMEOUT, codex=QUOTA)
        self.assertEqual(self.review("auto", timeout=1), ev.EXIT_UNAVAILABLE)
        self.assertEqual([(r["tool"], r["outcome"]) for r in self.log_rows()], [("gemini", "timeout"), ("codex", "quota")])

    def test_timeout_moves_to_the_next_tool(self):
        self.fake("gemini", "")
        self.fake("codex", "")
        self.script(gemini=TIMEOUT, codex=OK)
        self.assertEqual(self.review("auto", timeout=1), ev.EXIT_OK)
        self.assertEqual(self.started, ["gemini", "codex"])
        self.assertEqual([(r["tool"], r["outcome"]) for r in self.log_rows()], [("gemini", "timeout"), ("codex", "ok")])

    def test_a_codex_limit_left_in_the_home_never_reaches_these_tests(self):
        import codexlimit
        self.assertIsNone(codexlimit.blocked_until())
        self.assertTrue(codexlimit.state_path().startswith(self.d))


if __name__ == "__main__":
    unittest.main()
