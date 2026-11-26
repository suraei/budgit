"""R-1360 task C: a refusal for a program word inside free text names the door that works. Text only: the allow and deny
decision is the same as before (the existing guard modules are run unchanged). Part 1 covers the plugin-merge guard texts;
the helper read text (guard.subagent_ask_read) and the R-1037 text are still to do (see .progress-r1360b.md)."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import mergeguard  # noqa: E402
import hook  # noqa: E402
import i18n  # noqa: E402
import studiolib as sl  # noqa: E402
import readdoor  # noqa: E402
import test_r1119_read_route as rr  # noqa: E402                 # its fixture and run_hook are reused; the class is not imported by name, so its tests do not run twice
from test_guard_trust import GuardCase  # noqa: E402

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
WORD = "g" + "it"                                    # the program word, kept out of this file's own command lines


class DoorTexts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r1360c-")
        self.msg = os.path.join(self.tmp, "msg.txt")
        with open(self.msg, "w") as fh:
            fh.write("a note that mentions %s status\n" % WORD)

        self.wt = os.path.join(self.tmp, "other-worktree")      # a separate folder with its own .git entry: not the plugin's checkout
        os.makedirs(self.wt)
        with open(os.path.join(self.wt, ".git"), "w") as fh:
            fh.write("gitdir: %s\n" % os.path.join(self.tmp, "nowhere"))

    def judge(self, command, cwd=None):
        return mergeguard.decision("Bash", {"command": command}, cwd or self.tmp)

    def test_an_own_budgit_text_verb_with_a_program_word_in_quotes_names_the_text_file_door(self):
        d = self.judge("%s ledger finding add R-1 --sev LOW --class bug --text 'the %s status said x'" % (BUDGIT, WORD), cwd=ROOT)
        self.assertEqual(d[0], "deny", d)
        self.assertIn("put the text in a file and pass --text-file", d[1])

    def test_a_commit_message_with_a_program_word_in_quotes_names_the_dash_f_door(self):
        d = self.judge("%s -C %s commit -m 'ignore %s-ignored files'" % (WORD, ROOT, WORD))
        self.assertEqual(d[0], "deny", d)
        self.assertIn("pass the message with -F <file>", d[1])

    def test_the_text_file_door_form_is_allowed(self):
        self.assertIsNone(self.judge("%s ledger finding add R-1 --sev LOW --class bug --text-file %s" % (BUDGIT, self.msg), cwd=ROOT))

    def test_the_commit_dash_f_door_form_is_allowed(self):
        self.assertIsNone(self.judge("%s -C %s commit -F %s" % (WORD, self.wt, self.msg), cwd=self.wt))


class HelperReadDoor(GuardCase):
    """C2: a helper whose cat, wc, awk, sed or head read is refused is told the repo-read lines door; two joined reasons put the door first."""
    run_hook = rr.ReadRouteTest.run_hook

    def setUp(self):
        GuardCase.setUp(self)
        for rel in ("facts.md", "progress.md"):
            with open(os.path.join(self.root, rel), "w") as fh:
                fh.write("one\ntwo\n")
        self.sub = os.path.join(self.root, "docs")
        sl.write_pointer("s-r1119", self.root)        # the hook finds the project through the session's pointer

    def test_a_helper_cat_and_wc_refusal_names_repo_read_lines(self):
        for cmd in (rr.INCIDENT,):                          # the form test_r1119_read_route shows as refused for a helper: a cat and a wc after a lost cd
            for lang in ("en", "es"):
                decision, text = self.run_hook("Bash", {"command": cmd}, self.sub, lang=lang)
                self.assertEqual(decision, "deny", (cmd, text))
                self.assertIn("budgit repo-read lines <path> <start> <end>", text, (cmd, lang))
                self.assertIn("lines --count", text, (cmd, lang))
                self.assertIn("grep -e", text, (cmd, lang))
                self.assertIn(i18n.t("guard.subagent_ask_read", lang=lang), text)

    def test_when_two_reasons_join_the_door_reason_comes_first(self):
        door = readdoor.PREFIX + "lines: the path is outside the project"
        other = "budgit guard: a long reason that is not about a door " + "x" * 300
        for first, second in ((other, door), (door, other)):
            kind, reason = hook._combine([("ask", first), ("ask", second)])
            self.assertTrue(reason.startswith(door), reason[:80])
            self.assertEqual(kind, "ask")
        kind, reason = hook._combine([("ask", other), ("deny", readdoor.DOOR + "x")])      # the decision is unchanged: deny still beats ask
        self.assertEqual(kind, "deny")
        self.assertTrue(reason.startswith(readdoor.DOOR), reason[:80])
        kind, reason = hook._combine([("ask", other), ("ask", "pass the message with -F <file>")])      # a reason that names a door also goes first
        self.assertTrue(reason.startswith("pass the message"), reason[:80])
        plain = "budgit: a plain refusal that only mentions repo-read in passing"       # a bare word is not a door reason: the order stays
        kind, reason = hook._combine([("ask", other), ("ask", plain)])
        self.assertTrue(reason.startswith(other[:40]), reason[:80])
        kind, reason = hook._combine([("ask", other), ("ask", "put the text in a file and pass --text-file <file>")])
        self.assertTrue(reason.startswith("put the text in a file"), reason[:80])
        self.assertEqual(hook._combine([("ask", "a"), ("ask", "b")]), ("ask", "a | b" + hook.sl._ASK_TAIL))     # no door: the order is kept

    def test_the_helper_form_repo_read_lines_is_allowed(self):
        self.assertEqual(self.run_hook("Bash", {"command": "%s repo-read lines docs/a.md 1 50" % BUDGIT}, self.sub), (None, ""))


if __name__ == "__main__":
    unittest.main()
