"""R-1254: `budgit ext-review --tool openrouter`, a generic OpenAI-compatible adapter, free models only.

Only a fake HTTP server on 127.0.0.1 (port 0, in a thread) is ever called; the live model list is never fetched and the real
`security` command is never run (a fake `security` script sits first on PATH; the key is only ever read from it)."""
import contextlib
import http.server
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest

from helpers import StudioTestCase, patch_env
import extreview as ev

KEY = "sk-or-v1-SECRETKEYVALUE0123456789abcdef"
FREE = "deepseek/deepseek-chat-v3-0324:free"
DIFF = "diff --git a/x b/x\n+hello\n"
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")
LISTING = ["openai/gpt-4o", "anthropic/claude-3-haiku:free", "qwen/qwen3-coder:free", "someone/Claude-clone:free",
           "mistralai/mistral-small:free", "other/model-a:free", "other/model-b:free"]


class FakeServer:
    def __init__(self, mode="ok", listing=None):
        self.mode, self.requests, self.gets = mode, [], []
        self.listing = LISTING if listing is None else listing
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.gets.append({"path": self.path, "auth": self.headers.get("Authorization")})
                out = json.dumps({"data": [{"id": i} for i in outer.listing]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                if outer.mode in ("429", "500", "503", "401") or (outer.mode == "429-first" and len(outer.requests) == 1):
                    self.send_response(429 if outer.mode == "429-first" else int(outer.mode))
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"nope"}}')
                    return
                if outer.mode == "slow":
                    threading.Event().wait(3)
                content = "VERDICT: looks fine"
                if outer.mode == "echo":      # a hostile server that sends the caller's own key back
                    content = "you sent " + (self.headers.get("Authorization") or "")
                out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(out)
                except OSError:
                    pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/api/v1" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class OpenRouterBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.diff = os.path.join(self.project, "d.diff")
        with open(self.diff, "w") as f:
            f.write(DIFF)
        self.out = os.path.join(self.tmp, "verdict.txt")
        self.srv = None
        self.bindir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bindir)
        self.fake_sec = os.path.join(self.bindir, "security")
        self.script(self.fake_sec, "#!/bin/sh\nexit 44\n")                 # the keychain item is not there
        patch_env(self, OPENROUTER_API_KEY=None, OPENROUTER_BASE_URL=None, PATH=self.bindir + os.pathsep + os.environ["PATH"])

    def tearDown(self):
        if self.srv:
            self.srv.close()
        super().tearDown()

    @staticmethod
    def script(path, body):
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    def set_key(self, key=KEY):
        self.script(self.fake_sec, '#!/bin/sh\n[ "$1 $2 $3 $4" = "find-generic-password -s budgit-openrouter -w" ] && echo %s || exit 44\n' % key)

    def serve(self, mode="ok", listing=None):
        self.srv = FakeServer(mode, listing)
        os.environ["OPENROUTER_BASE_URL"] = self.srv.url
        return self.srv

    def run_main(self, *extra, tool="openrouter"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ev.main(["--tool", tool, "--diff", self.diff, "--prompt", "review", "--out", self.out] + list(extra),
                         cwd=self.project)
        return rc, out.getvalue(), err.getvalue()

    def run_review(self, tool="auto", timeout=20, now=None, which=None, **kw):
        lines = []
        rc = ev.review(tool, self.diff, "review", self.out, timeout, which=which or self.which, grace=0.5, say=lines.append,
                       now=now, cwd=self.project, **kw)
        return rc, lines

    def which(self, name):
        p = os.path.join(self.bindir, name)
        return p if os.path.exists(p) else None

    def protect(self):
        path = os.path.join(self.home, "config.json")
        os.makedirs(self.home, exist_ok=True)
        cfg = {}
        if os.path.exists(path):
            with open(path) as f:
                cfg = json.load(f)
        cfg["protected_dirs"] = [self.project]
        with open(path, "w") as f:
            json.dump(cfg, f)


class OpenRouterTest(OpenRouterBase):
    def test_success_writes_the_verdict_file_and_names_tool_and_model(self):
        srv = self.serve()
        self.set_key()
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, 0, out + err)
        with open(self.out) as f:
            self.assertEqual(f.read(), "VERDICT: looks fine")
        self.assertIn("openrouter", out)
        self.assertIn(FREE, out)
        self.assertEqual(srv.requests[0]["path"], "/api/v1/chat/completions")
        self.assertEqual(srv.requests[0]["auth"], "Bearer " + KEY)
        self.assertEqual(srv.requests[0]["body"]["model"], FREE)
        self.assertIn(DIFF, srv.requests[0]["body"]["messages"][-1]["content"])

    def test_a_paid_model_id_is_refused_with_one_line_and_no_call(self):
        srv = self.serve()
        self.set_key()
        rc, out, err = self.run_main("--model", "openai/gpt-4o")
        self.assertEqual(rc, ev.EXIT_USAGE)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn(":free", out)
        self.assertEqual(srv.requests + srv.gets, [])
        self.assertFalse(os.path.exists(self.out))

    def test_an_anthropic_or_claude_id_is_refused_even_with_the_free_suffix(self):
        srv = self.serve()
        self.set_key()
        for bad in ("anthropic/claude-3-haiku:free", "someone/Claude-clone:free", "ANTHROPIC/x:free"):
            rc, out, err = self.run_main("--model", bad)
            self.assertEqual(rc, ev.EXIT_USAGE, bad)
            self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertEqual(srv.requests, [])

    def test_a_missing_key_is_one_spanish_line_a_nonzero_exit_and_no_traceback(self):
        srv = self.serve()
        rc, out, err = self.run_main("--model", FREE)
        self.assertNotEqual(rc, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("clave", out)
        self.assertNotIn("Traceback", out + err)
        self.assertEqual(srv.requests + srv.gets, [])

    def test_the_key_is_never_read_from_the_environment(self):
        srv = self.serve()
        os.environ["OPENROUTER_API_KEY"] = KEY                    # an env key must be ignored: only the keychain counts
        rc, out, err = self.run_main("--model", FREE)
        self.assertNotEqual(rc, 0)
        self.assertIn("clave", out)
        self.assertEqual(srv.requests, [])

    def test_a_locked_keychain_is_reported_and_nothing_is_sent(self):
        srv = self.serve()
        self.script(self.fake_sec, "#!/bin/sh\nexit 36\n")        # security: the keychain is locked / needs interaction
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, ev.EXIT_NO_TOOL)
        self.assertIn("bloqueado", out)
        self.assertEqual(srv.requests, [])

    def test_a_429_is_unavailable_with_a_clear_line(self):
        self.serve("429")
        self.set_key()
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, ev.EXIT_UNAVAILABLE)
        self.assertIn("429", out)
        self.assertNotIn("Traceback", out + err)
        self.assertFalse(os.path.exists(self.out))

    def test_a_5xx_is_unavailable_with_a_clear_line(self):
        self.serve("503")
        self.set_key()
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, ev.EXIT_UNAVAILABLE)
        self.assertIn("503", out)

    def test_a_rejected_key_is_a_failure_that_does_not_show_the_key(self):
        self.serve("401")
        self.set_key()
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, ev.EXIT_FAILED)
        self.assertIn("401", out)
        self.assertNotIn(KEY, out + err)

    def test_a_protected_project_is_refused_and_nothing_is_sent(self):
        srv = self.serve()
        self.set_key()
        self.protect()
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, ev.EXIT_PROTECTED)
        self.assertIn("protected project", out)
        self.assertEqual(srv.requests + srv.gets, [])

    def test_a_protected_project_is_refused_before_any_request_is_built(self):
        srv = self.serve()
        self.set_key()
        self.protect()
        built = []
        real = ev.http_call
        ev.http_call = lambda *a, **k: built.append(a) or real(*a, **k)
        self.addCleanup(setattr, ev, "http_call", real)
        lines = []
        rc = ev.review_http("openrouter", self.diff, "p", self.out, 5, None, lines.append, None, cwd=self.project)
        self.assertEqual(rc, ev.EXIT_PROTECTED)
        self.assertEqual((built, srv.requests, srv.gets), ([], [], []))
        self.assertIn("protected project", lines[0])

    def test_a_diff_with_personal_data_is_refused_and_nothing_is_sent(self):
        srv = self.serve()
        self.set_key()
        with open(self.diff, "w") as f:
            f.write(DIFF + "+owner: maria.lopez@gmail.com\n")
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, ev.EXIT_PROTECTED)
        self.assertIn("email", out)
        self.assertNotIn("maria.lopez", out)
        self.assertEqual(srv.requests, [])

    def test_a_diff_over_the_size_limit_is_refused(self):
        srv = self.serve()
        self.set_key()
        with open(self.diff, "w") as f:
            f.write("+x\n" * ev.PROVIDERS["openrouter"]["max_diff_bytes"])
        rc, out, err = self.run_main("--model", FREE)
        self.assertEqual(rc, ev.EXIT_USAGE)
        self.assertEqual(srv.requests, [])

    def test_the_key_never_appears_in_stdout_stderr_or_the_output_file(self):
        self.serve("echo")
        self.set_key()
        r = subprocess.run([BIN, "ext-review", "--tool", "openrouter", "--model", FREE, "--diff", self.diff,
                            "--prompt", "p", "--out", self.out], cwd=self.project, env=dict(os.environ), capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(self.out) as f:
            written = f.read()
        self.assertIn("you sent", written)
        for text in (r.stdout, r.stderr, written):
            self.assertNotIn(KEY, text)
            self.assertNotIn("SECRETKEYVALUE", text)

    def test_the_real_cli_accepts_a_slash_and_colon_model_and_reports_a_missing_key(self):
        self.serve()
        r = subprocess.run([BIN, "ext-review", "--tool", "openrouter", "--model", FREE, "--diff", self.diff,
                            "--prompt", "p", "--out", self.out], cwd=self.project, env=dict(os.environ), capture_output=True,
                           text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("clave", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_other_tools_still_reject_a_slash_in_the_model_name(self):
        rc, out, err = self.run_main("--model", "a/b:free", tool="gemini")
        self.assertEqual(rc, ev.EXIT_USAGE)
        self.assertIn("bad --model", out)


class LiveModelsTest(OpenRouterBase):
    """Item 2: the free ids are read from the listing at run time and cached for the day under the project's .budgit/."""

    def cache_file(self):
        return os.path.join(self.project, ".budgit", "openrouter-free-models.json")

    def test_no_model_id_is_hard_coded_in_the_provider_table(self):
        self.assertNotIn("models", ev.PROVIDERS["openrouter"])

    def test_the_default_model_is_the_first_allowed_free_id_of_the_live_listing(self):
        srv = self.serve()
        self.set_key()
        rc, out, err = self.run_main()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(srv.requests[0]["body"]["model"], "qwen/qwen3-coder:free")      # paid, claude and anthropic ids skipped
        self.assertEqual(srv.gets[0]["path"], "/api/v1/models")
        self.assertIsNone(srv.gets[0]["auth"])                                              # the listing never carries the key
        self.assertIn("qwen/qwen3-coder:free", out)

    def test_a_different_listing_gives_a_different_model(self):
        srv = self.serve(listing=["zed/new-model:free"])
        self.set_key()
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(srv.requests[0]["body"]["model"], "zed/new-model:free")

    def test_the_list_is_cached_for_the_day_in_the_project_budgit_folder(self):
        srv = self.serve()
        self.set_key()
        t = time.mktime((2026, 11, 20, 10, 0, 0, 0, 0, -1))
        self.assertEqual(self.run_review("openrouter", now=t)[0], 0)
        self.assertEqual(self.run_review("openrouter", now=t + 3600)[0], 0)
        self.assertEqual(len(srv.gets), 1)
        with open(self.cache_file()) as f:
            cached = json.load(f)
        self.assertEqual(cached["day"], "2026-11-20")
        self.assertIn("qwen/qwen3-coder:free", cached["models"])
        self.assertNotIn(KEY, json.dumps(cached))
        self.assertFalse(os.path.exists(os.path.join(self.home, "openrouter-free-models.json")))
        self.assertEqual(self.run_review("openrouter", now=t + 86400)[0], 0)              # the next day: read again
        self.assertEqual(len(srv.gets), 2)

    def test_a_429_on_one_free_model_tries_the_next_one_of_the_listing(self):
        srv = self.serve("429-first")
        self.set_key()
        rc, out, err = self.run_main()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual([r["body"]["model"] for r in srv.requests], ["qwen/qwen3-coder:free", "mistralai/mistral-small:free"])

    def test_an_empty_listing_is_unavailable_and_sends_nothing(self):
        srv = self.serve(listing=["openai/gpt-4o"])
        self.set_key()
        rc, out, err = self.run_main()
        self.assertEqual(rc, ev.EXIT_UNAVAILABLE)
        self.assertEqual(srv.requests, [])


class AutoOrderTest(OpenRouterBase):
    """Item 1: auto tries gemini, then codex, then openrouter; a reviewer not set up, out of quota or locked is skipped."""

    def fake(self, name, body):
        self.script(os.path.join(self.bindir, name), "#!/bin/sh\n" + body)

    def test_the_order_is_gemini_then_codex_then_openrouter(self):
        self.assertEqual(ev.AUTO_ORDER, ("gemini", "codex", "openrouter"))

    def test_gemini_runs_first_when_all_three_are_set_up_and_the_line_says_which_ran(self):
        srv = self.serve()
        self.set_key()
        self.fake("gemini", "echo GEMINI VERDICT\n")
        self.fake("codex", "echo CODEX VERDICT\n")
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_OK)
        self.assertEqual(open(self.out).read().strip(), "GEMINI VERDICT")
        self.assertTrue(any("ran: gemini" in l for l in lines), lines)
        self.assertEqual(srv.requests + srv.gets, [])

    def test_a_gemini_out_of_quota_falls_to_codex(self):
        self.fake("gemini", 'echo "Error: quota exceeded (429)" >&2\nexit 1\n')
        self.fake("codex", 'while [ "$1" != "-o" ]; do shift; done\necho "CODEX VERDICT" > "$2"\n')
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_OK)
        self.assertEqual(open(self.out).read().strip(), "CODEX VERDICT")
        self.assertTrue(any("ran: codex" in l and "gemini" in l for l in lines), lines)

    def test_both_command_line_tools_out_of_quota_falls_to_openrouter(self):
        srv = self.serve()
        self.set_key()
        self.fake("gemini", 'echo "Error: quota exceeded (429)" >&2\nexit 1\n')
        self.fake("codex", 'echo "Error: quota exceeded (429)" >&2\nexit 1\n')
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_OK, lines)
        self.assertEqual(open(self.out).read(), "VERDICT: looks fine")
        self.assertTrue(any("ran: openrouter" in l for l in lines), lines)
        self.assertEqual(len(srv.requests), 1)

    def test_a_tool_that_is_not_installed_is_skipped_and_openrouter_runs_alone(self):
        self.serve()
        self.set_key()
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_OK, lines)
        self.assertTrue(any("ran: openrouter" in l and "gemini" in l and "codex" in l for l in lines), lines)

    def test_with_no_key_openrouter_is_skipped_and_the_result_is_the_same_as_before(self):
        srv = self.serve()
        self.fake("gemini", 'echo "503 Service Unavailable" >&2\nexit 1\n')
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_UNAVAILABLE)
        self.assertEqual(srv.requests + srv.gets, [])
        self.assertTrue(any("openrouter skipped" in l for l in lines), lines)

    def test_with_nothing_set_up_there_is_the_no_reviewer_line_first_and_the_no_tool_code(self):
        srv = self.serve()
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_NO_TOOL)
        self.assertIn("no external reviewer available", lines[0])
        self.assertIn("openrouter", lines[0])
        self.assertEqual(srv.requests + srv.gets, [])

    def test_a_locked_keychain_skips_openrouter_in_auto(self):
        self.serve()
        self.script(self.fake_sec, "#!/bin/sh\nexit 36\n")
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_NO_TOOL)
        self.assertTrue(any("bloqueado" in l or "locked" in l for l in lines), lines)

    def test_a_protected_project_in_auto_is_refused_before_any_reviewer_starts(self):
        srv = self.serve()
        self.set_key()
        self.fake("gemini", "echo SHOULD NOT RUN\n")
        self.protect()
        rc, lines = self.run_review("auto")
        self.assertEqual(rc, ev.EXIT_PROTECTED)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(srv.requests + srv.gets, [])


class TimeoutTest(OpenRouterBase):
    """Item 3: a wall-clock deadline on the HTTP call; on timeout it fails fast with the reason and auto goes on."""

    def test_a_slow_answer_stops_at_the_deadline_with_the_reason_and_no_file(self):
        self.serve("slow")
        self.set_key()
        t0 = time.monotonic()
        rc, out, err = self.run_main("--model", FREE, "--timeout", "1")
        self.assertEqual(rc, ev.EXIT_TIMEOUT)
        self.assertLess(time.monotonic() - t0, 2.5)
        self.assertIn("timed out after 1 s", out)
        self.assertNotIn("Traceback", out + err)
        self.assertFalse(os.path.exists(self.out))

    def test_the_deadline_covers_the_whole_call_across_the_model_listing_and_every_try(self):
        self.serve("slow")
        self.set_key()
        t0 = time.monotonic()
        rc, lines = self.run_review("openrouter", timeout=1)
        self.assertEqual(rc, ev.EXIT_TIMEOUT)
        self.assertLess(time.monotonic() - t0, 2.5)           # not 3 s per model: the shared clock ran out

    def test_the_http_default_deadline_is_a_product_value_below_the_cli_default(self):
        self.assertEqual(ev.http_deadline(None), ev.HTTP_DEADLINE_S)
        self.assertLess(ev.HTTP_DEADLINE_S, ev.DEFAULT_TIMEOUT_S)
        self.assertEqual(ev.http_deadline(30), 30)

    def test_a_timed_out_reviewer_in_auto_falls_through_to_the_next_one(self):
        self.serve()
        self.set_key()
        AutoOrderTest.fake(self, "gemini", "sleep 300\n")
        AutoOrderTest.fake(self, "codex", 'while [ "$1" != "-o" ]; do shift; done\necho "CODEX VERDICT" > "$2"\n')
        rc, lines = self.run_review("auto", timeout=1)
        self.assertEqual(rc, ev.EXIT_OK, lines)
        self.assertEqual(open(self.out).read().strip(), "CODEX VERDICT")
        self.assertTrue(any("timed out" in l for l in lines), lines)

    def test_a_slow_openrouter_in_auto_ends_with_the_timeout_code(self):
        self.serve("slow")
        self.set_key()
        rc, lines = self.run_review("auto", timeout=1)
        self.assertEqual(rc, ev.EXIT_TIMEOUT)
        self.assertTrue(any("timed out after 1 s" in l for l in lines), lines)


if __name__ == "__main__":
    unittest.main()
