"""R-940: no Claude attribution in commits or PRs; `ledger add` needs a size; a whole read of a big ledger gets a hint."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import StudioTestCase, ROOT
import attribution
import readhint
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
TRAILER = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
GENERATED = "Generated with [Claude Code](https://claude.com/claude-code)"


def _missing_appetite(line):
    """Count real `ledger add --phase ...` examples on a line that carry no --appetite.
    Prose that only names the command (`ledger add --text`, `ledger add --src I-n`) is not an example."""
    return sum(1 for part in line.split("ledger add")[1:] if part.startswith(" --phase")
               and ("--appetite" not in part or "--area" not in part))      # R-1157: the size and the area, both


class LedgerAddExamplesTest(unittest.TestCase):
    def test_every_shipped_ledger_add_example_carries_appetite(self):
        # self-check: the narrowed rule still fails a real example with neither --appetite nor --src
        self.assertEqual(_missing_appetite('budgit ledger add --phase mvp --text "x"'), 1)
        bad = []
        for top in ("plugin", "share", "skills", os.path.join("tools", "release")):
            for dirpath, _dirs, files in os.walk(os.path.join(ROOT, top)):
                for name in files:
                    if name.endswith((".pyc", ".png", ".jpg", ".ico", ".md")) and name != "ledger.md":
                        continue
                    path = os.path.join(dirpath, name)
                    try:
                        with open(path, encoding="utf-8") as f:
                            lines = f.read().split("\n")
                    except (UnicodeDecodeError, OSError):
                        continue
                    for n, line in enumerate(lines, 1):
                        if _missing_appetite(line):
                            bad.append("%s:%d" % (path, n))
        self.assertEqual(bad, [], "ledger add examples without --appetite")

    def test_first_run_script_ledger_add_carries_appetite(self):
        with open(os.path.join(ROOT, "tools", "release", "first_run.sh"), encoding="utf-8") as f:
            lines = [l for l in f if "ledger add" in l]
        self.assertTrue(lines)
        for l in lines:
            self.assertIn("--appetite", l)


def bash(command):
    return attribution.decision("Bash", {"command": command}, "/tmp")


class AttributionTest(unittest.TestCase):
    def test_commit_m_with_the_trailer_is_denied(self):
        got = bash("git commit -m 'fix a bug\n\n%s'" % TRAILER)
        self.assertEqual(got, ("deny", "R-940: no Claude attribution in commits or PRs (core rule); remove the trailer."))

    def test_commit_F_file_with_the_trailer_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            msg = os.path.join(d, "msg.txt")
            with open(msg, "w") as fh:
                fh.write("fix a bug\n\nco-authored-by: claude <x@anthropic.com>\n")
            self.assertEqual(bash("git commit -F %s" % msg)[0], "deny")
            self.assertEqual(bash("git commit --file=%s" % msg)[0], "deny")

    def test_gh_pr_create_body_with_the_generated_line_is_denied(self):
        self.assertEqual(bash("gh pr create --title t --body 'text\n\n%s'" % GENERATED)[0], "deny")
        self.assertEqual(bash("gh pr edit 3 --body 'Generated with Claude Code'")[0], "deny")

    def test_gh_pr_body_file_with_the_generated_line_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            body = os.path.join(d, "body.md")
            with open(body, "w") as fh:
                fh.write(GENERATED + "\n")
            self.assertEqual(bash("gh pr create --body-file %s" % body)[0], "deny")

    def test_a_normal_commit_passes(self):
        self.assertIsNone(bash("git commit -m 'fix a bug in the parser'"))
        self.assertIsNone(bash("gh pr create --title t --body 'a plain description'"))

    def test_a_commit_that_mentions_claude_without_a_trailer_passes(self):
        self.assertIsNone(bash("git commit -m 'rename the Claude hook and the claude code docs'"))

    def test_the_hook_denies_a_commit_with_the_trailer(self):
        payload = {"tool_name": "Bash", "session_id": "s1", "cwd": "/tmp",
                   "tool_input": {"command": "git commit -m 'x\n\n%s'" % TRAILER}}
        with tempfile.TemporaryDirectory() as home:
            out = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), capture_output=True,
                                 text=True, env=dict(os.environ, HOME=home, BUDGIT_HOME=home))
        self.assertIn("R-940: no Claude attribution", out.stdout)
        self.assertIn('"deny"', out.stdout)


class LedgerAddAppetiteTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, "ledger", "add", "--area", "main", *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def test_add_without_appetite_is_refused(self):
        r = self.cli("--phase", "mvp", "--text", "build a thing")
        self.assertEqual(r.returncode, 2)
        self.assertIn("R-940: give --appetite small|medium|large (dates come from size)", r.stderr)
        self.assertEqual(sl.load_ledger(self.project)["items"], [])

    def test_add_with_appetite_is_accepted_and_stores_the_size(self):
        r = self.cli("--phase", "mvp", "--appetite", "medium", "--text", "build a thing")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["appetite"], "medium")

    def test_legacy_source_needs_no_appetite(self):
        r = self.cli("--src", "legacy", "--phase", "mvp", "--text", "old item")
        self.assertEqual(r.returncode, 0, r.stderr)


class ReadHintTest(unittest.TestCase):
    def make(self, d, size):
        path = os.path.join(d, "ledger.md")
        with open(path, "w") as fh:
            fh.write("x" * size)
        return path

    def test_a_whole_read_of_a_big_ledger_gets_the_hint(self):
        with tempfile.TemporaryDirectory() as d:
            got = readhint.hint("Read", {"file_path": self.make(d, 60000)})
        self.assertEqual(got, "R-940: the ledger is large; use `budgit ledger show R-n` or grep for the id instead of reading it whole.")

    def test_a_read_with_a_limit_gets_no_hint(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.make(d, 60000)
            self.assertIsNone(readhint.hint("Read", {"file_path": path, "limit": 100}))
            self.assertIsNone(readhint.hint("Read", {"file_path": path, "offset": 10}))

    def test_a_small_ledger_gets_no_hint(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(readhint.hint("Read", {"file_path": self.make(d, 2000)}))

    def test_the_hook_adds_the_hint_and_does_not_deny(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            payload = {"tool_name": "Read", "session_id": "s1", "cwd": d, "tool_input": {"file_path": self.make(d, 60000)}}
            out = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), capture_output=True,
                                 text=True, env=dict(os.environ, HOME=home, BUDGIT_HOME=home))
        self.assertIn("R-940: the ledger is large", out.stdout)
        self.assertNotIn('"deny"', out.stdout)


if __name__ == "__main__":
    unittest.main()
