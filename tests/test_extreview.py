"""R-889a: `budgit ext-review`. Fake tool scripts only; the real codex and gemini are never called."""
import os, stat, subprocess, sys, tempfile, time, unittest
from helpers import StudioTestCase, ROOT
from loadcal import load_factor
import extreview as ev
import procclose as pc

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")


class ExtReviewTest(unittest.TestCase):
    def setUp(self):
        import extuse_isolation
        extuse_isolation.isolate(self)       # R-1358: no use-log row in the real project
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        self._old_home = os.environ.get("STUDIO_HOME")          # R-853: the Codex-limit record lives in the home, never the real one
        os.environ["STUDIO_HOME"] = self.path("home")
        self.diff = self.path("d.diff")
        with open(self.diff, "w") as f:
            f.write("diff --git a/x b/x\n+hello\n")
        self.out = self.path("verdict.txt")
        self.lines = []
        self.tools = {}

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("STUDIO_HOME", None)
        else:
            os.environ["STUDIO_HOME"] = self._old_home
        self._t.cleanup()

    def path(self, name):
        return os.path.join(self.d, name)

    def fake(self, name, body):
        p = self.path("fake-" + name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        self.tools[name] = p
        return p

    def run_review(self, tool="auto", timeout=20, grace=0.5):
        return ev.review(tool, self.diff, "review this", self.out, timeout, which=lambda t: self.tools.get(t),
                         grace=grace, say=self.lines.append)

    def alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    # -- normal finish
    def test_a_normal_finish_writes_out_with_the_text_and_the_diff_arrives_as_stdin(self):
        self.fake("gemini", 'echo "GOT: $(cat)"\n')
        self.assertEqual(self.run_review("gemini"), ev.EXIT_OK)
        text = open(self.out).read()
        self.assertIn("GOT: diff --git a/x b/x", text)
        self.assertIn("hello", text)

    def test_codex_runs_read_only_and_its_final_message_file_is_the_verdict(self):
        self.fake("codex", 'echo "$@" > %s\nwhile [ "$1" != "-o" ]; do shift; done\necho "CODEX VERDICT" > "$2"\necho noise\n' % self.path("args"))
        self.assertEqual(self.run_review("codex"), ev.EXIT_OK)
        self.assertEqual(open(self.out).read().strip(), "CODEX VERDICT")
        self.assertIn("-s read-only", open(self.path("args")).read())

    def test_gemini_runs_with_plan_approval_mode_and_text_output(self):
        self.fake("gemini", 'echo "$@" > %s\necho ok\n' % self.path("args"))
        self.run_review("gemini")
        a = open(self.path("args")).read()
        self.assertIn("--approval-mode plan", a)
        self.assertIn("-o text", a)

    # -- timeout
    def test_a_fake_that_sleeps_past_the_timeout_is_killed_with_its_grandchild_and_leaves_no_out(self):
        self.fake("gemini", 'sleep 300 &\necho $! > %s\necho $$ > %s\nwait\n' % (self.path("gc"), self.path("me")))
        t0 = time.monotonic()
        rc = self.run_review("gemini", timeout=1, grace=0.5)
        self.assertEqual(rc, ev.EXIT_TIMEOUT)
        self.assertLess(time.monotonic() - t0, 15 * load_factor())     # real-deadline budget 15 s scaled by the same-run calibration (R-1282)
        self.assertIn("timed out after 1 s, no verdict", self.lines[-1])
        self.assertFalse(os.path.exists(self.out))
        gc, me = int(open(self.path("gc")).read()), int(open(self.path("me")).read())
        time.sleep(0.3)
        self.assertFalse(self.alive(gc), "grandchild still alive")
        self.assertFalse(self.alive(me), "tool still alive")

    def test_a_tool_that_ignores_term_is_killed_after_the_grace(self):
        self.fake("gemini", 'trap "" TERM\necho $$ > %s\nwhile true; do sleep 1; done\n' % self.path("me"))
        self.assertEqual(self.run_review("gemini", timeout=1, grace=0.5), ev.EXIT_TIMEOUT)
        time.sleep(0.3)
        self.assertFalse(self.alive(int(open(self.path("me")).read())))

    def test_a_stale_out_never_survives_a_timeout(self):
        with open(self.out, "w") as f:
            f.write("old verdict")
        self.fake("gemini", "sleep 300\n")
        self.run_review("gemini", timeout=1, grace=0.5)
        self.assertFalse(os.path.exists(self.out))

    def test_a_finished_tool_that_left_a_background_grandchild_does_not_leave_it_running(self):
        self.fake("gemini", 'sleep 300 &\necho $! > %s\necho done\n' % self.path("gc"))
        self.assertEqual(self.run_review("gemini"), ev.EXIT_OK)
        time.sleep(0.3)
        self.assertFalse(self.alive(int(open(self.path("gc")).read())))

    def test_the_timeout_is_the_default_lowered_by_the_flag_and_capped(self):
        self.assertEqual(ev.effective_timeout(None), ev.DEFAULT_TIMEOUT_S)
        self.assertEqual(ev.effective_timeout(30), 30)
        self.assertEqual(ev.effective_timeout(10 ** 6), ev.HARD_CAP_S)
        self.assertEqual(ev.DEFAULT_TIMEOUT_S, 600)

    # -- quota, 5xx, fall through, nothing installed
    def test_auto_falls_through_from_a_quota_error_to_the_next_tool(self):
        self.fake("gemini", 'echo "Error: quota exceeded (429)" >&2\nexit 1\n')       # R-1254: auto tries gemini first
        self.fake("codex", "echo CODEX VERDICT\n")
        self.assertEqual(self.run_review("auto"), ev.EXIT_OK)
        self.assertEqual(open(self.path("verdict.txt")).read().strip(), "CODEX VERDICT")
        self.assertTrue(any("no verdict: gemini unavailable" in l for l in self.lines))

    def test_a_503_from_the_only_tool_gives_the_unavailable_code_and_no_out(self):
        self.fake("gemini", 'echo "503 Service Unavailable" >&2\nexit 1\n')
        self.assertEqual(self.run_review("auto"), ev.EXIT_UNAVAILABLE)
        self.assertTrue(any("no verdict: gemini unavailable" in l for l in self.lines))
        self.assertFalse(os.path.exists(self.out))

    def test_a_review_that_mentions_429_but_finished_is_a_verdict(self):
        self.fake("gemini", 'echo "line 429 has a bug; the quota code is fine"\n')
        self.assertEqual(self.run_review("gemini"), ev.EXIT_OK)

    def test_with_no_tool_installed_there_is_a_clear_line_and_a_distinct_code(self):
        rc = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_NO_TOOL)
        self.assertIn("no external reviewer available", self.lines[0])
        self.assertEqual(len({ev.EXIT_OK, ev.EXIT_USAGE, ev.EXIT_NO_TOOL, ev.EXIT_TIMEOUT, ev.EXIT_UNAVAILABLE, ev.EXIT_FAILED}), 6)

    def test_the_cli_with_an_empty_path_exits_with_the_no_reviewer_code(self):
        empty = self.path("emptybin")
        os.makedirs(empty)
        env = dict(os.environ, PATH=empty)
        r = subprocess.run([sys.executable, BUDGIT, "ext-review", "--diff", self.diff, "--prompt", "p", "--out", self.out],
                           capture_output=True, text=True, env=env, cwd=self.d)
        self.assertEqual(r.returncode, ev.EXIT_NO_TOOL, r.stdout + r.stderr)
        self.assertIn("no external reviewer available", r.stdout)

    # -- owner tag and the sweep
    def test_the_child_carries_the_owner_tag_the_sweep_reads(self):
        self.fake("gemini", 'echo "$BUDGIT_OWNER" > %s\necho ok\n' % self.path("owner"))
        self.run_review("gemini")
        tag = open(self.path("owner")).read().strip()
        self.assertTrue(tag.startswith(pc.EXT_OWNER_PREFIX), tag)
        self.assertEqual(pc.owner_from_env("BUDGIT_OWNER=%s\0" % tag), tag)

    def test_the_tag_keeps_the_session_id_when_there_is_one(self):
        self.assertEqual(ev.owner_tag({"BUDGIT_OWNER": "sess-1"}), "extreview:sess-1")



class ExtReviewHintTest(StudioTestCase):
    def test_the_sweep_recognises_and_closes_an_orphaned_tagged_child_but_keeps_a_young_untagged_one(self):
        import test_procclose as tp
        m = tp.Machine(tp.base() + [tp.row(500, ppid=1, owner="extreview:S1", age=300, command="gemini -p x"),
                                    tp.row(501, ppid=1, owner="S1", age=300, command="other session proc"),
                                    tp.row(502, ppid=tp.CLAUDE, owner="extreview:S1", age=300, command="wrapper alive"),
                                    tp.row(503, ppid=1, owner="extreview:S1", age=5, command="just started")])
        closed = pc.sweep_orphans(True, now=tp.NOW, **m.kw())
        self.assertEqual([r["pid"] for r in closed], [500])
        self.assertEqual(m.pids(), {tp.CLAUDE, tp.ME, 501, 502, 503})

    def test_the_pretooluse_hint_names_ext_review_and_the_fallback(self):
        import codexlane
        from helpers import codex_on
        self.init_project()
        codex_on(self.home)
        note = codexlane.review_first_note(self.project, "budgit:verifier")
        self.assertIn("budgit ext-review", note)
        self.assertIn("gemini", note)


if __name__ == "__main__":
    unittest.main()
