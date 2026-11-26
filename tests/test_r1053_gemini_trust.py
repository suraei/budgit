"""R-1053: gemini is told to trust its empty review folder for the run (--skip-trust); codex is not.

Only a fake gemini placed first on PATH is run."""
import os
import stat
import tempfile
import unittest

import extreview as ev
from loadcal import load_factor


class GeminiTrustTest(unittest.TestCase):
    def test_gemini_argv_has_skip_trust_and_plan_mode(self):
        argv = ev.command("gemini", "p", "/tmp/o")
        self.assertIn("--skip-trust", argv)
        i = argv.index("--approval-mode")
        self.assertEqual(argv[i + 1], "plan")

    def test_codex_argv_has_no_skip_trust(self):
        self.assertNotIn("--skip-trust", ev.command("codex", "p", "/tmp/o"))

    def test_the_run_with_the_flag_is_the_one_in_the_empty_work_folder(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            bin_ = os.path.join(d, "bin")
            os.makedirs(bin_)
            caller = os.path.join(d, "caller")
            os.makedirs(caller)
            rec = os.path.join(d, "rec.txt")
            p = os.path.join(bin_, "gemini")
            with open(p, "w") as f:
                f.write('#!/bin/sh\npwd -P > "%s"\nprintf "%%s\\n" "$@" >> "%s"\n'
                        'cat > /dev/null\necho ok\n' % (rec, rec))
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            diff = os.path.join(d, "d.diff")
            with open(diff, "w") as f:
                f.write("+x\n")
            old = {k: os.environ.get(k) for k in ("PATH", "STUDIO_HOME")}
            os.environ["PATH"] = bin_ + os.pathsep + os.environ.get("PATH", "")
            os.environ["STUDIO_HOME"] = os.path.join(d, "home")
            try:
                code = ev.review("gemini", diff, "p", os.path.join(d, "v.txt"), 30, grace=0.5,
                                 say=lambda s: None, cwd=caller)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(code, ev.EXIT_OK)
            lines = open(rec).read().split("\n")
            self.assertTrue(lines[0].endswith(os.sep + "work"), lines[0])
            self.assertNotEqual(lines[0], caller)
            self.assertIn("--skip-trust", lines[1:])


FRAME = "    at async X (file:///a.js:1:2)\n"


class StderrTailTest(unittest.TestCase):
    def test_a_timeout_message_carries_the_error_line_not_the_frames(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            bin_ = os.path.join(d, "bin")
            os.makedirs(bin_)
            p = os.path.join(bin_, "gemini")
            with open(p, "w") as f:
                f.write("#!/bin/sh\nprintf 'Error: some cause 503\\n' >&2\n"
                        + "printf '    at async X (file:///a.js:1:2)\\n' >&2\n" * 8 + "sleep 30\n")
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            diff = os.path.join(d, "d.diff")
            with open(diff, "w") as f:
                f.write("+x\n")
            old = {k: os.environ.get(k) for k in ("PATH", "STUDIO_HOME")}
            os.environ["PATH"] = bin_ + os.pathsep + os.environ.get("PATH", "")
            os.environ["STUDIO_HOME"] = os.path.join(d, "home")
            lines = []
            try:
                ev.review("gemini", diff, "p", os.path.join(d, "v.txt"), 2, grace=0.5, say=lines.append, cwd=d)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            msg = "\n".join(lines)
            self.assertIn("some cause 503", msg)
            self.assertNotIn("at async", msg)

    def test_only_frames_still_gives_five_lines(self):
        out = ev.tail_of(FRAME * 8)
        self.assertEqual(len(out.split(" | ")), 5)

    def test_redaction_applies_to_a_secret_on_the_error_line(self):
        out = ev.tail_of("Error: Bearer abcdef1234567890SECRET failed\n" + FRAME * 8)
        self.assertNotIn("abcdef1234567890SECRET", out)
        self.assertIn("Error", out)


class DebugFlagTest(unittest.TestCase):
    def test_debug_goes_to_gemini_only_and_is_off_by_default(self):
        self.assertIn("--debug", ev.command("gemini", "p", "/tmp/o", True))
        self.assertNotIn("--debug", ev.command("codex", "p", "/tmp/o", True))
        self.assertNotIn("--debug", ev.command("gemini", "p", "/tmp/o"))
        self.assertNotIn("--debug", ev.command("gemini", "p", "/tmp/o", False))

    def run_hang(self, debug):
        secret_a = "Bearer " + "q7Zk" * 6
        secret_b = "key=" + "m3Xv" * 6
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            bin_ = os.path.join(d, "bin")
            os.makedirs(bin_)
            body = "#!/bin/sh\n"
            for i in range(20):
                body += "printf 'debug line %d\\n' >&2\n" % i
            body += "printf 'Authorization: %s\\n' >&2\n" % secret_a
            body += "printf 'GET x?%s\\n' >&2\n" % secret_b
            body += "sleep 30\n"
            p = os.path.join(bin_, "gemini")
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            diff = os.path.join(d, "d.diff")
            with open(diff, "w") as f:
                f.write("+x\n")
            old = {k: os.environ.get(k) for k in ("PATH", "STUDIO_HOME")}
            os.environ["PATH"] = bin_ + os.pathsep + os.environ.get("PATH", "")
            os.environ["STUDIO_HOME"] = os.path.join(d, "home")
            lines = []
            try:
                ev.review("gemini", diff, "p", os.path.join(d, "v.txt"), 2, grace=0.5, say=lines.append, cwd=d, debug=debug)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        return "\n".join(lines), secret_a, secret_b

    def test_debug_message_shows_up_to_15_lines_redacted(self):
        msg, a, b = self.run_hang(True)
        self.assertEqual(msg.split("last stderr: ")[1].count(" | "), 14)
        self.assertNotIn(a.split(" ")[1], msg)
        self.assertNotIn(b.split("=")[1], msg)

    def test_without_debug_the_message_keeps_5_lines(self):
        msg, a, b = self.run_hang(False)
        self.assertEqual(msg.split("last stderr: ")[1].count(" | "), 4)
        self.assertNotIn(a.split(" ")[1], msg)


class CliParserTest(unittest.TestCase):
    """The real budgit CLI parser must accept every extreview option (--debug)."""

    def run_cli(self, extra):
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            bin_ = os.path.join(d, "bin")
            os.makedirs(bin_)
            argv_file = os.path.join(d, "argv.txt")
            p = os.path.join(bin_, "gemini")
            with open(p, "w") as f:
                f.write("#!/bin/sh\nprintf '%%s\\n' \"$@\" > %s\ncat > /dev/null\necho verdict ok\n" % argv_file)
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            diff = os.path.join(d, "d.diff")
            with open(diff, "w") as f:
                f.write("+x\n")
            env = dict(os.environ)
            env["PATH"] = bin_ + os.pathsep + env.get("PATH", "")
            env["STUDIO_HOME"] = os.path.join(d, "home")
            r = subprocess.run([sys.executable, os.path.join(root, "plugin", "bin", "budgit"), "ext-review",
                                "--tool", "gemini", "--diff", diff, "--prompt", "p",
                                "--out", os.path.join(d, "v.txt"), "--timeout", "30"] + extra,
                               env=env, capture_output=True, text=True, timeout=60)
            argv = open(argv_file).read().split("\n") if os.path.exists(argv_file) else []
        return r, argv

    def test_cli_accepts_debug_and_gemini_receives_it(self):
        r, argv = self.run_cli(["--debug"])
        self.assertNotIn("unrecognized arguments", r.stderr)
        self.assertIn("--debug", argv)

    def test_cli_without_debug_gemini_gets_none(self):
        r, argv = self.run_cli([])
        self.assertNotIn("--debug", argv)
        self.assertIn("--skip-trust", argv)

    def test_model_option_puts_dash_m_in_gemini_argv(self):
        r, argv = self.run_cli(["--model", "gemini-2.5-flash"])
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gemini-2.5-flash")


class ModelOptionTest(unittest.TestCase):
    BAD = ["-x", "--yolo", "a\n", "a b", "a" * 65]

    def test_option_like_or_multiline_model_is_refused(self):
        for bad in self.BAD:
            self.assertEqual(ev.main(["--diff", __file__, "--prompt", "p", "--out", "/tmp/x-r1053", "--model=" + bad]),
                             ev.EXIT_USAGE, repr(bad))

    def test_good_model_matches(self):
        self.assertTrue(ev.MODEL_RE.match("gemini-3.8-flash"))

    def test_cli_refuses_yolo_model(self):
        for bad in ("--yolo", "a\n"):
            r, argv = CliParserTest("test_model_option_puts_dash_m_in_gemini_argv").run_cli(["--model=" + bad])
            self.assertEqual(r.returncode, ev.EXIT_USAGE, repr(bad))
            self.assertEqual(argv, [])

    def test_bad_name_is_refused(self):
        self.assertEqual(ev.main(["--diff", __file__, "--prompt", "p", "--out", "/tmp/x-r1053", "--model", "x;rm"]), ev.EXIT_USAGE)

    def test_no_model_adds_nothing_and_codex_gets_dash_m(self):
        self.assertNotIn("-m", ev.command("gemini", "p", "/tmp/o"))
        self.assertEqual(ev.command("codex", "p", "/tmp/o", model="m1")[5:7], ["-m", "m1"])


class FallbackLoopTest(unittest.TestCase):
    def go(self, n, tail):
        import time
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            bin_ = os.path.join(d, "bin")
            os.makedirs(bin_)
            p = os.path.join(bin_, "gemini")
            with open(p, "w") as f:
                f.write("#!/bin/sh\ncat > /dev/null\nfor i in $(seq %d); do\n"
                        "echo 'Model fallback activated: switching from gemini-3-flash-preview to gemini-3.1-pro-preview-customtools' >&2\n"
                        "sleep 0.3\ndone\n%s\n" % (n, tail))
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            diff = os.path.join(d, "d.diff")
            with open(diff, "w") as f:
                f.write("+x\n")
            old = {k: os.environ.get(k) for k in ("PATH", "STUDIO_HOME")}
            os.environ["PATH"] = bin_ + os.pathsep + os.environ.get("PATH", "")
            os.environ["STUDIO_HOME"] = os.path.join(d, "home")
            lines = []
            t0 = time.monotonic()
            try:
                code = ev.review("gemini", diff, "p", os.path.join(d, "v.txt"), 30, grace=0.5, say=lines.append, cwd=d)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            return code, " ".join(lines), time.monotonic() - t0

    def test_three_fallbacks_stop_with_exit_5(self):
        code, msg, took = self.go(3, "sleep 20")
        self.assertEqual(code, ev.EXIT_UNAVAILABLE)
        self.assertLess(took, 8 * load_factor())     # real-deadline budget 8 s scaled by the same-run calibration (R-1282)
        self.assertIn("gemini keeps switching models (gemini-3-flash-preview -> gemini-3.1-pro-preview-customtools)", msg)
        self.assertIn("try --model", msg)

    def test_two_fallbacks_then_a_verdict_still_give_the_verdict(self):
        code, msg, _ = self.go(2, "echo the verdict")
        self.assertEqual(code, ev.EXIT_OK)


class OverloadTest(FallbackLoopTest):
    def go(self, n, tail, same=None):
        import time
        num = "$i" if same is None else str(same)
        with tempfile.TemporaryDirectory() as t:
            d = os.path.realpath(t)
            bin_ = os.path.join(d, "bin")
            os.makedirs(bin_)
            p = os.path.join(bin_, "gemini")
            with open(p, "w") as f:
                f.write(("#!/bin/sh\ncat > /dev/null\nfor i in $(seq %d); do\n"
                         "echo \"Attempt " + num + " failed with status 503. Retrying with backoff... _ApiError: {\\\"code\\\": 503, \\\"status\\\": \\\"UNAVAILABLE\\\"}\" >&2\n"
                         "echo 'status: 503' >&2\nsleep 0.3\ndone\n%s\n") % (n, tail))
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            diff = os.path.join(d, "d.diff")
            with open(diff, "w") as f:
                f.write("+x\n")
            old = {k: os.environ.get(k) for k in ("PATH", "STUDIO_HOME")}
            os.environ["PATH"] = bin_ + os.pathsep + os.environ.get("PATH", "")
            os.environ["STUDIO_HOME"] = os.path.join(d, "home")
            lines = []
            t0 = time.monotonic()
            try:
                code = ev.review("gemini", diff, "p", os.path.join(d, "v.txt"), 30, grace=0.5, say=lines.append, cwd=d)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            return code, " ".join(lines), time.monotonic() - t0

    def test_three_fallbacks_stop_with_exit_5(self):
        code, msg, took = self.go(3, "sleep 20")
        self.assertEqual(code, ev.EXIT_UNAVAILABLE)
        self.assertLess(took, 8 * load_factor())     # real-deadline budget 8 s scaled by the same-run calibration (R-1282)
        self.assertIn("503", msg)
        self.assertIn("after 3 tries", msg)

    def test_same_attempt_line_echoed_three_times_still_gives_the_verdict(self):
        code, msg, _ = self.go(3, "echo the verdict", same=2)
        self.assertEqual(code, ev.EXIT_OK)

    def test_two_fallbacks_then_a_verdict_still_give_the_verdict(self):
        code, msg, _ = self.go(2, "echo the verdict")
        self.assertEqual(code, ev.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
