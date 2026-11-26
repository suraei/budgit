"""R-353: the bar's decision is traced to <BUDGIT_HOME>/brote-trace.jsonl, one checked JSON line per change, the last 200 kept. A line holds the
time, a hash of the session id, the surfaces the host listed, band yes/no, footer set yes/no and its length, and the plugin version. Never the
text of the line, a path or a project name."""
import json
import os
import stat
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT

sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import brotetrace  # noqa: E402

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class RecordTest(unittest.TestCase):
    def test_a_line_holds_only_the_checked_fields_and_a_hashed_session(self):
        got = brotetrace.record("chat-7", "terminal,desktop", "yes", "yes", "0", "0.13.2", "no")
        self.assertEqual(sorted(got), ["band", "drawn", "footer_at_draw", "footer_len", "footer_set", "session", "surfaces", "tail_len", "ts", "version"])
        self.assertEqual((got["surfaces"], got["band"], got["drawn"], got["footer_set"], got["footer_len"], got["footer_at_draw"]),
                         (["terminal", "desktop"], True, True, False, 0, False))
        self.assertEqual(len(got["session"]), 12)
        self.assertNotIn("chat-7", json.dumps(got))

    def test_a_footer_length_is_a_number_never_text(self):
        got = brotetrace.record("s", "terminal", "no", "no", "92", "0.13.2")
        self.assertEqual((got["footer_set"], got["footer_len"]), (True, 92))
        self.assertNotIn("footer_at_draw", got)

    def test_the_tail_length_is_a_number_never_text(self):
        self.assertEqual(brotetrace.record("s", "terminal", "no", "no", "0", "1.0", None, "87")["tail_len"], 87)
        self.assertEqual(brotetrace.record("s", "terminal", "no", "no", "0", "1.0")["tail_len"], 0)
        for bad in ("abc", "-1", "9999999", "1\n"):
            self.assertIsNone(brotetrace.record("s", "terminal", "no", "no", "0", "1.0", None, bad), bad)

    def test_a_host_that_could_not_say_is_null(self):
        self.assertIsNone(brotetrace.record("s", "-", "no", "no", "0", "-")["surfaces"])
        self.assertEqual(brotetrace.record("s", "-", "no", "no", "0", "-")["version"], "")

    def test_anything_that_is_not_what_the_mod_sends_is_refused(self):
        bad = [
            ("s", "/Users/me/secret", "no", "no", "0", "1.0"),           # a path as a surface
            ("s", "terminal", "maybe", "no", "0", "1.0"),
            ("s", "terminal", "no", "no", "abc", "1.0"),
            ("s", "terminal", "no", "no", "-5", "1.0"),
            ("s", "terminal", "no", "no", "9999999", "1.0"),
            ("s", "Terminal Desktop", "no", "no", "0", "1.0"),
            ("s", "terminal\n", "no", "no", "0", "1.0"),               # a trailing newline is not a surface
            ("s", "terminal", "no", "no", "1_0", "1.0"),
            ("s", "terminal", "no", "no", "\u0661\u0662", "1.0"),     # Arabic digits
        ]
        for args in bad:
            self.assertIsNone(brotetrace.record(*args), args)
        self.assertEqual(brotetrace.record("s", "terminal", "no", "no", "0", "see /etc/passwd")["version"], "")
        self.assertEqual(brotetrace.record("s", "terminal", "no", "no", "0", "1.0\n")["version"], "")


class AppendTest(StudioTestCase):
    def test_the_last_200_lines_are_kept(self):
        for i in range(230):
            brotetrace.append(self.home, {"n": i})
        with open(brotetrace.trace_path(self.home), encoding="utf-8") as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual((len(rows), rows[0]["n"], rows[-1]["n"]), (200, 30, 229))

    def test_the_folder_is_private_and_a_symlink_is_not_followed(self):
        fresh = os.path.join(self.tmp, "fresh")
        self.assertTrue(brotetrace.append(fresh, {"n": 1}))
        self.assertEqual(stat.S_IMODE(os.stat(fresh).st_mode), 0o700)
        target = os.path.join(self.tmp, "elsewhere.txt")
        open(target, "w").close()
        os.remove(brotetrace.trace_path(fresh))
        os.symlink(target, brotetrace.trace_path(fresh))
        self.assertFalse(brotetrace.append(fresh, {"n": 2}))
        self.assertEqual(os.path.getsize(target), 0)

    def test_a_folder_that_cannot_be_written_does_not_raise(self):
        blocker = os.path.join(self.tmp, "file")
        open(blocker, "w").close()
        self.assertFalse(brotetrace.append(os.path.join(blocker, "sub"), {"n": 1}))


class CliTest(StudioTestCase):
    def trace(self, *args):
        env = dict(os.environ, BUDGIT_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, "trace"] + list(args), cwd=self.tmp, env=env, capture_output=True, text=True)

    def rows(self):
        with open(brotetrace.trace_path(self.home), encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def test_the_cli_writes_one_line_and_prints_nothing_and_ignores_the_project(self):
        r = self.trace("--sid", "chat-7", "--surfaces", "terminal,desktop", "--band", "yes", "--drawn", "yes", "--footer", "0",
                       "--version", "0.13.2", "--footer-at-draw", "no", "--project", "/Users/someone/Dev/quiet-lab")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["band"], rows[0]["footer_set"], rows[0]["surfaces"]), (True, False, ["terminal", "desktop"]))
        with open(brotetrace.trace_path(self.home), encoding="utf-8") as f:
            raw = f.read()
        for leak in ("quiet-lab", "/Users", "chat-7"):
            self.assertNotIn(leak, raw)

    def test_a_bad_line_writes_nothing_and_still_exits_clean(self):
        r = self.trace("--sid", "s", "--surfaces", "/etc", "--band", "yes", "--drawn", "no", "--footer", "0", "--version", "1.0")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(brotetrace.trace_path(self.home)))


if __name__ == "__main__":
    unittest.main()
