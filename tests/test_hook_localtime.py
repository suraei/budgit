"""R-1159: every prompt and session start carries one line with the machine's real local time."""
import json, os, re, subprocess, sys, time
from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
LINE = re.compile(r"budgit: (?:local time|hora local) (\d\d):(\d\d) (\w+) (\d+) (\w+) (\d{4}) \(([^)]+)\)")


class LocalTimeTest(StudioTestCase):
    def run_hook(self, event, payload, **extra):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.update(extra)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def prompt(self, **extra):
        sl.activate(self.project, "s1")
        return self.run_hook("user-prompt-submit", {"session_id": "s1", "prompt": "anade login"}, **extra)

    def test_prompt_gets_current_time_within_a_minute(self):
        before = time.time()
        text = self.prompt(BUDGIT_LANG="en")
        after = time.time()
        m = LINE.search(text)
        self.assertTrue(m, text)
        got = int(m.group(1)) * 60 + int(m.group(2))
        ok = False
        for stamp in (before - 60, before, after, after + 60):
            tm = time.localtime(stamp)
            if got == tm.tm_hour * 60 + tm.tm_min:
                ok = True
        self.assertTrue(ok, text)

    def test_line_is_first_and_appears_once_with_other_context(self):
        text = self.prompt(BUDGIT_LANG="en")      # the untracked-prompt notice is also in the context
        self.assertIn("I-1", text)
        self.assertTrue(text.startswith("budgit: local time "), text)
        self.assertEqual(len(LINE.findall(text)), 1)

    def test_english_and_spanish_text(self):
        en = self.prompt(BUDGIT_LANG="en")
        es = self.prompt(BUDGIT_LANG="es")
        self.assertIn("budgit: local time ", en)
        self.assertNotIn("hora local", en)
        self.assertIn("budgit: hora local ", es)
        self.assertNotIn("local time", es)

    def test_tz_env_shows_that_zones_hour(self):
        text = self.prompt(BUDGIT_LANG="en", TZ="Asia/Tokyo")
        m = LINE.search(text)
        self.assertEqual(m.group(7), "JST")
        tokyo = int(time.strftime("%H", time.gmtime(time.time() + 9 * 3600)))
        self.assertIn(int(m.group(1)), (tokyo, (tokyo + 23) % 24, (tokyo + 1) % 24))
        utc = self.prompt(BUDGIT_LANG="en", TZ="UTC")
        self.assertNotEqual(LINE.search(utc).group(7), "JST")

    def test_session_with_no_project_gets_the_line_once(self):
        text = self.run_hook("user-prompt-submit", {"session_id": "nobody", "prompt": "hola"}, BUDGIT_LANG="en")
        self.assertEqual(len(LINE.findall(text)), 1, text)

    def test_busy_lock_still_gets_the_line_once(self):
        sl.activate(self.project, "s1")
        with sl.locked(self.project):          # held by this process while the hook subprocess runs
            text = self.run_hook("user-prompt-submit", {"session_id": "s1", "prompt": "hola"}, BUDGIT_LANG="en")
        self.assertEqual(len(LINE.findall(text)), 1, text)

    def test_session_start_gets_the_line_too(self):
        sl.activate(self.project, "s1")
        text = self.run_hook("session-start", {"session_id": "s1", "source": "startup", "cwd": self.project}, BUDGIT_LANG="es")
        self.assertEqual(len(LINE.findall(text)), 1, text)
        self.assertLess(text.index("budgit: hora local "), text.index("BEGIN UNTRUSTED PROJECT DATA"))   # before the project summary
