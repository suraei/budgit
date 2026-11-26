"""R-1042 r3: trailers read through git's own parser and with folded lines unfolded; the "Generated with Claude Code" phrase is
matched anywhere in a message. Temp repos only, fake HOME."""
import os
import subprocess
import sys
import unittest

from helpers import ROOT

import attribution
import githook_check
from test_r1042_githooks import RepoCase

CHECK = os.path.join(ROOT, "plugin", "scripts", "githook_check.py")
FOLDED = {
    "folded-space": "Co-Authored-By:\n Claude <noreply@anthropic.com>",
    "folded-tab": "Co-Authored-By:\n\tClaude <noreply@anthropic.com>",
    "folded-two-lines": "Co-Authored-By: someone\n   and Claude\n   <noreply@anthropic.com>",
    "space-before-colon": "Co-Authored-By : Claude <noreply@anthropic.com>",
    "tab-before-colon": "co-authored-by\t: Claude <noreply@anthropic.com>",
    "folded-and-spaced": "Co-Authored-By :\n Claude <noreply@anthropic.com>",
}
PHRASE = {
    "note-prefix": "Note: Generated with Claude Code",
    "mid-sentence": "This change was Generated with Claude Code, then reviewed",
    "plain-line": "Generated with Claude Code",
}


class ReadingTest(RepoCase):
    def test_commit_msg_rejects_folded_and_spaced_trailers_and_the_phrase_anywhere(self):
        self.install()
        for name, text in list(FOLDED.items()) + list(PHRASE.items()):
            r = self.commit(self.repo, "subject\n\nbody text\n\n" + text)
            self.assertNotEqual(r.returncode, 0, name)
            self.assertIn("R-1042", r.stderr, name)
        self.assertNotEqual(self.git(self.repo, "log", "--oneline", check=False).returncode, 0)     # nothing got through
        ok = self.commit(self.repo, 'Docs: the "Co-Authored-By: Claude" trailer is banned in prose')   # quoted mid-line stays fine
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_pre_push_rejects_folded_and_spaced_trailers_and_the_phrase_anywhere(self):
        self.install()
        remote = self.make_repo("r.git", bare=True)
        self.git(self.repo, "remote", "add", "origin", remote)
        self.assertEqual(self.commit(self.repo, "clean base").returncode, 0)
        self.assertEqual(self.git(self.repo, "push", "-q", "origin", "main", check=False).returncode, 0)
        for name, text in list(FOLDED.items()) + list(PHRASE.items()):
            self.assertEqual(self.commit(self.repo, "dirty\n\n" + text, "--no-verify").returncode, 0, name)   # past commit-msg, as a bypass would
            r = self.git(self.repo, "push", "-q", "origin", "main", check=False)
            self.assertNotEqual(r.returncode, 0, name)
            self.assertIn("push rejected (R-1042)", r.stderr, name)
            self.git(self.repo, "update-ref", "refs/heads/main", "HEAD~1")                              # drop it again, keep the index
        self.assertEqual(self.git(remote, "rev-list", "--count", "main").stdout.strip(), "1")          # nothing dirty ever arrived

    def test_the_parser_view_and_the_unfolded_view_both_read_the_message(self):
        text = "subject\n\nbody\n\nCo-Authored-By :\n Claude <noreply@anthropic.com>\n"
        parsed = githook_check._trailers(text)
        self.assertIn("Claude", parsed)                                    # git's own parser reads it as a trailer, unfolded
        self.assertTrue(attribution.first_match(parsed, anchored=True))
        self.assertTrue(attribution.first_match(text, anchored=True))      # and so does the unfolded view of the shared pattern
        self.assertTrue(attribution.first_match("subject\n   Co-Authored-By: Claude <a@anthropic.com>\n", anchored=True))
        self.assertIsNone(attribution.first_match('Docs: say "Co-Authored-By: Claude" in quotes', anchored=True))

    def test_a_missing_git_fails_closed_for_a_message_that_needs_the_parser(self):
        msg = os.path.join(self.tmp, "msg.txt")
        with open(msg, "w") as fh:
            fh.write('Docs: the "Co-Authored-By: Claude" trailer is banned in prose\n')
        env = dict(self.env, PATH=os.path.join(self.tmp, "no-such-bin"))
        r = subprocess.run([sys.executable, "-I", CHECK, "commit-msg", msg], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fail closed", r.stderr)
        with open(msg, "w") as fh:
            fh.write("a plain message\n")
        r = subprocess.run([sys.executable, "-I", CHECK, "commit-msg", msg], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)                        # no parser needed, no git call


class GuardTest(unittest.TestCase):
    def test_the_guard_catches_a_space_before_the_colon_in_a_commit_command(self):
        for cmd in ('git commit -m "x" -m "Co-Authored-By : Claude <noreply@anthropic.com>"',
                    "git commit -m 'x\n\nCo-Authored-By\t: Claude <noreply@anthropic.com>'"):
            got = attribution.decision("Bash", {"command": cmd}, "/tmp", enabled=True)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)
        self.assertIsNone(attribution.decision("Bash", {"command": 'git commit -m "mention Claude and co-authors"'}, "/tmp", enabled=True))


if __name__ == "__main__":
    unittest.main()
