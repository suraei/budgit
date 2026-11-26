"""R-853 (and R-881): Codex's usage limit is recorded once and nothing asks for Codex until it returns.
Fake codex and gemini scripts only; the clock is the `now` parameter, and every named date is computed from the real clock."""
import datetime, os, shutil, stat, sys, tempfile, time, unittest
from helpers import StudioTestCase, codex_on
import codexlane
import codexlimit as cl
import extreview as ev

DIFF = "diff --git a/x b/x\n+hello\n"


def ordinal(n):
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def message(day):
    """The real usage-limit text with a retry date for local day `day` (a datetime.date)."""
    return ("You've hit your usage limit. Upgrade to Plus to continue using Codex, or try again at %s %s, %d."
            % (day.strftime("%b"), ordinal(day.day), day.year))


def midnight(day):
    return datetime.datetime(day.year, day.month, day.day).timestamp()


class LimitBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.d = os.path.join(self.tmp, "tools")
        os.makedirs(self.d)
        self.diff = os.path.join(self.d, "d.diff")
        with open(self.diff, "w") as f:
            f.write(DIFF)
        self.out = os.path.join(self.d, "verdict.txt")
        self.tools = {}
        self.lines = []
        self.now = time.time()
        self.day = datetime.date.fromtimestamp(self.now) + datetime.timedelta(days=30)

    def fake(self, name, body):
        p = os.path.join(self.d, "fake-" + name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\necho x >> %s\n%s" % (self.marker(name), body))
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        self.tools[name] = p

    def marker(self, name):
        return os.path.join(self.d, "ran-" + name)

    def runs(self, name):
        try:
            with open(self.marker(name)) as f:
                return len(f.read().split())
        except OSError:
            return 0

    def codex_limited(self, text=None):
        self.fake("codex", "echo %r >&2\nexit 1\n" % (text or message(self.day)))

    def gemini_ok(self):
        self.fake("gemini", "echo GEMINI VERDICT\n")

    def auto(self, now=None):
        return ev.review("auto", self.diff, "review", self.out, 20, which=lambda t: self.tools.get(t), grace=0.5,
                         say=self.lines.append, now=self.now if now is None else now)


class CodexLimitTest(LimitBase):
    # R-1254: auto tries gemini first, so Codex is only reached when gemini is out of quota (gemini_down).
    def gemini_down(self):
        self.fake("gemini", 'echo "503 Service Unavailable" >&2\nexit 1\n')

    def test_codex_is_not_started_when_gemini_answers_first(self):
        self.codex_limited()
        self.gemini_ok()
        self.assertEqual(self.auto(), ev.EXIT_OK)
        self.assertIn("GEMINI VERDICT", open(self.out).read())
        self.assertEqual(self.runs("codex"), 0)

    def test_codex_at_its_limit_after_gemini_is_down_ends_unavailable_in_the_same_call(self):
        self.codex_limited()
        self.gemini_down()
        self.assertEqual(self.auto(), ev.EXIT_UNAVAILABLE)
        self.assertEqual(self.runs("gemini"), 1)
        self.assertEqual(self.runs("codex"), 1)

    def test_the_retry_date_is_recorded_and_auto_never_starts_codex_before_it(self):
        self.codex_limited()
        self.gemini_down()
        self.auto()
        self.assertEqual(cl.blocked_until(self.now), midnight(self.day))
        self.assertEqual(self.auto(self.now + 3600), ev.EXIT_UNAVAILABLE)
        self.assertEqual(self.auto(midnight(self.day) - 1), ev.EXIT_UNAVAILABLE)
        self.assertEqual(self.runs("codex"), 1)                # only the first call reached Codex
        self.assertEqual(self.runs("gemini"), 3)

    def test_after_the_date_auto_tries_codex_again(self):
        self.codex_limited()
        self.gemini_down()
        self.auto()
        self.assertIsNone(cl.blocked_until(midnight(self.day) + 1))
        self.auto(midnight(self.day) + 1)
        self.assertEqual(self.runs("codex"), 2)

    def test_a_message_with_no_date_blocks_for_24_hours_from_now(self):
        until = cl.record("You've hit your usage limit.", self.now)
        self.assertEqual(until, self.now + 24 * 3600)
        self.assertEqual(cl.blocked_until(self.now + 24 * 3600 - 1), until)
        self.assertIsNone(cl.blocked_until(self.now + 24 * 3600 + 1))

    def test_a_named_date_already_past_falls_back_to_24_hours(self):
        yesterday = datetime.date.fromtimestamp(self.now) - datetime.timedelta(days=3)
        self.assertEqual(cl.record(message(yesterday), self.now), self.now + 24 * 3600)

    def test_every_named_date_follows_the_real_clock_so_none_can_expire(self):
        for ahead in (1, 40, 400, 4000):          # also past any fixed year someone might have typed
            day = datetime.date.fromtimestamp(time.time()) + datetime.timedelta(days=ahead)
            self.assertEqual(cl.parse_retry(message(day)), midnight(day))
            t = time.time()
            self.assertEqual(cl.record(message(day), t), min(midnight(day), t + cl.MAX_AHEAD_S))
        self.assertEqual(cl.parse_retry("try again at Nov 25th, 2026 3:30 PM"),
                         datetime.datetime(2026, 11, 25, 15, 30).timestamp())

    def test_no_fallback_tool_exits_non_zero_and_names_the_section_12_fallback(self):
        self.codex_limited()
        rc = self.auto()
        self.assertNotEqual(rc, ev.EXIT_OK)
        self.assertEqual(rc, 5)
        self.assertIn("fresh-context reviewer on another tier", self.lines[-1])
        self.assertIn("weaker", self.lines[-1])
        self.assertFalse(os.path.exists(self.out))

    def test_blocked_and_no_other_tool_exits_no_tool_with_the_fallback_line_and_never_starts_codex(self):
        cl.record(message(self.day), self.now)
        self.codex_limited()
        rc = self.auto()
        self.assertEqual(rc, 3)
        self.assertEqual(self.runs("codex"), 0)
        self.assertIn("fresh-context reviewer on another tier", self.lines[-1])

    def test_an_explicit_codex_request_still_runs_codex(self):
        cl.record(message(self.day), self.now)
        self.fake("codex", "while [ \"$1\" != \"-o\" ]; do shift; done\necho CODEX VERDICT > \"$2\"\n")
        rc = ev.review("codex", self.diff, "r", self.out, 20, which=lambda t: self.tools.get(t), grace=0.5,
                       say=self.lines.append, now=self.now)
        self.assertEqual(rc, ev.EXIT_OK)
        self.assertEqual(self.runs("codex"), 1)


class FailClosedTest(LimitBase):
    """A damaged or odd codex-limit.json reads as "no limit": the hook note and ext-review behave as if none was recorded."""

    def put(self, data, mode="w"):
        os.makedirs(os.path.dirname(cl.state_path()), exist_ok=True)
        with open(cl.state_path(), mode) as f:
            f.write(data)

    def assert_no_limit(self):
        self.assertIsNone(cl.blocked_until(self.now))
        self.init_project()
        codex_on(self.home)
        note = codexlane.review_first_note(self.project, "budgit:verifier", now=self.now)
        self.assertIn("`budgit codex review`", note)
        self.fake("codex", "while [ \"$1\" != \"-o\" ]; do shift; done\necho CODEX VERDICT > \"$2\"\n")
        self.assertEqual(self.auto(), 0)
        self.assertEqual(self.runs("codex"), 1)

    def test_corrupt_empty_list_null_nan_json_all_read_as_no_limit(self):
        for text in ("{not json", "", "[1, 2]", "null", '{"until": NaN}', '{"until": "x"}', '{"until": null}', '{"until": [1]}'):
            self.put(text)
            self.assertIsNone(cl.blocked_until(self.now), text)
        self.put(b"\xff\xfe\x00bad", "wb")
        self.assertIsNone(cl.blocked_until(self.now))
        self.assert_no_limit()

    def test_a_missing_file_reads_as_no_limit(self):
        self.assert_no_limit()

    def test_a_folder_in_its_place_reads_as_no_limit(self):
        os.makedirs(cl.state_path())
        self.assert_no_limit()

    def test_a_symlink_in_its_place_reads_as_no_limit(self):
        target = os.path.join(self.d, "elsewhere.json")
        with open(target, "w") as f:
            f.write('{"until": %r}' % (self.now + 3600))
        os.makedirs(os.path.dirname(cl.state_path()), exist_ok=True)
        os.symlink(target, cl.state_path())
        self.assert_no_limit()

    def test_until_must_be_finite_and_is_capped_at_90_days(self):
        for text in ('{"until": Infinity}', '{"until": -Infinity}'):
            self.put(text)
            self.assertIsNone(cl.blocked_until(self.now))
        self.put('{"until": 1e18}')
        self.assertEqual(cl.blocked_until(self.now), self.now + 90 * 24 * 3600)
        self.assertEqual(cl.record("You've hit your usage limit. try again at Jan 1st, 9999", self.now), self.now + 90 * 24 * 3600)


class EchoedPromptTest(LimitBase):
    def test_a_prompt_echoed_on_stderr_that_mentions_usage_limit_records_nothing(self):
        self.fake("codex", "echo 'Review this change: if the usage limit is reached, the job retries. You have a usage limit.' >&2\n"
                           "echo 'the usage limit message looks like: You have hit your usage limit' >&2\nexit 1\n")
        self.auto()
        self.assertIsNone(cl.blocked_until(self.now))

    def test_the_real_line_with_either_apostrophe_records_2026_11_25_14_52(self):
        want = datetime.datetime(2026, 11, 25, 14, 52).timestamp()
        for apos in ("'", "’"):
            line = "You%sve hit your usage limit. Upgrade to Plus, or try again at Nov 25th, 2026 2:52 PM." % apos
            self.assertTrue(cl.is_limit(line))
            self.assertTrue(cl.is_limit("ERROR: " + line))
            self.assertEqual(cl.parse_retry(line), want)
        self.assertFalse(cl.is_limit("please explain: You've hit your usage limit means what?"))


class ProtectedProjectTest(LimitBase):
    def setUp(self):
        super().setUp()
        self.init_project()
        codex_on(self.home)
        cl.record(message(self.day), self.now)

    def protect(self):
        import json
        path = os.path.join(self.home, "config.json")
        with open(path) as f:
            cfg = json.load(f)
        cfg["protected_dirs"] = [self.project]
        with open(path, "w") as f:
            json.dump(cfg, f)

    def test_a_protected_project_gets_no_ext_review_hint(self):
        self.protect()
        note = codexlane.review_first_note(self.project, "budgit:verifier", now=self.now)
        self.assertNotIn("ext-review --tool auto", note)
        self.assertIn("budgit codex review --clean-copy", note)
        self.assertIn("fresh-context reviewer", note)

    def test_ext_review_refuses_inside_a_protected_project(self):
        self.protect()
        diff = os.path.join(self.project, "d.diff")
        with open(diff, "w") as f:
            f.write(DIFF)
        self.assertEqual(ev.main(["--diff", diff, "--prompt", "p", "--out", self.out], cwd=self.project), 7)
        self.assertEqual(self.runs("gemini"), 0)

    def test_the_real_cli_in_a_protected_folder_exits_7_and_runs_no_tool(self):
        import subprocess
        self.protect()
        stubs = os.path.join(self.tmp, "stubs")
        os.makedirs(stubs)
        marker = os.path.join(self.tmp, "stub-ran")
        for name in ("codex", "gemini"):
            p = os.path.join(stubs, name)
            with open(p, "w") as f:
                f.write("#!/bin/sh\necho %s >> %s\necho VERDICT\n" % (name, marker))
            os.chmod(p, 0o755)
        diff = os.path.join(self.project, "d.diff")
        with open(diff, "w") as f:
            f.write(DIFF)
        env = {k: v for k, v in os.environ.items() if k != "BUDGIT_HOME"}
        env["PATH"] = stubs + os.pathsep + env.get("PATH", "")
        bin_ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")
        r = subprocess.run([bin_, "ext-review", "--tool", "auto", "--diff", diff, "--prompt", "p", "--out", self.out],
                           cwd=self.project, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 7, r.stdout + r.stderr)
        self.assertIn("protected project", r.stdout)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.exists(self.out))

    def test_an_unreadable_protected_list_counts_as_protected_through_review(self):
        import json
        path = os.path.join(self.home, "config.json")
        with open(path) as f:
            cfg = json.load(f)
        cfg["protected_dirs"] = "not-a-list"
        with open(path, "w") as f:
            json.dump(cfg, f)
        self.gemini_ok()
        self.assertEqual(self.auto(), 7)
        self.assertEqual(self.runs("gemini"), 0)

    def test_a_normal_project_is_unchanged(self):
        note = codexlane.review_first_note(self.project, "budgit:verifier", now=self.now)
        self.assertIn("budgit ext-review --tool auto", note)
        self.assertIsNone(ev.refuse_protected(self.diff, self.d))


class HookNoteTest(LimitBase):
    def setUp(self):
        super().setUp()
        self.init_project()
        codex_on(self.home)

    def test_the_codex_ask_is_gone_before_the_date_and_back_after_it(self):
        cl.record(message(self.day), self.now)
        before = codexlane.review_first_note(self.project, "budgit:verifier", now=self.now)
        self.assertNotIn("run it first", before)
        self.assertNotIn("`budgit codex review`", before)
        self.assertIn("budgit ext-review --tool auto", before)
        self.assertIn(cl.when(midnight(self.day)), before)
        self.assertIn("fresh-context reviewer", before)
        after = codexlane.review_first_note(self.project, "budgit:verifier", now=midnight(self.day) + 1)
        self.assertIn("`budgit codex review`", after)
        self.assertNotIn("R-853", after)

    def test_without_a_recorded_limit_the_note_is_todays_text_without_the_overlap_remark(self):
        note = codexlane.review_first_note(self.project, "budgit:verifier", now=self.now)
        self.assertIn("run it first", note)
        self.assertNotIn("R-853", note)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("sandbox-exec"), "the Codex lane runs inside sandbox-exec (macOS)")
class CodexReviewLaneTest(LimitBase):
    def setUp(self):
        super().setUp()
        self.init_project()
        codex_on(self.home)
        self.fake("codex", "echo %r >&2\nexit 1\n" % message(self.day))
        self._old = {k: os.environ.get(k) for k in ("STUDIO_CODEX_BIN", "HOME")}
        os.environ["STUDIO_CODEX_BIN"] = self.tools["codex"]
        os.environ["HOME"] = os.path.join(self.tmp, "realhome")
        os.makedirs(os.environ["HOME"])

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_codex_review_records_the_limit_names_ext_review_and_then_does_not_start_codex(self):
        import studiolib as sl
        with self.assertRaises(sl.StudioError) as c:
            codexlane.run(self.project, "review", DIFF, now=self.now)
        self.assertIn("ext-review --tool auto", str(c.exception))
        self.assertEqual(cl.blocked_until(self.now), midnight(self.day))
        self.assertEqual(self.runs("codex"), 1)
        with self.assertRaises(sl.StudioError):
            codexlane.run(self.project, "review", DIFF, now=self.now)
        self.assertEqual(self.runs("codex"), 1)


if __name__ == "__main__":
    unittest.main()
