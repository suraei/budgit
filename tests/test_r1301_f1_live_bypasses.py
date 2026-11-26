"""Two live bypasses of the guards, each shown by plan review.

Bypass 1: the interim lexer check only fired when the raw line held the program's name as a plain substring, so a hidden name
(`g''it`, a backslash, a dollar-expansion, a dollar-single-quote escape) under a hidden-comment wrapper reached the shell but not the live check.
Bypass 2: the destructive-command guard exited early unless the name appeared, so a name built by an expansion or a brace was never judged.
The program's name is built with chr() here so this file never holds it."""
import shutil
import tempfile
import unittest
from unittest import mock

from test_r1199_guards import make_repo
import mergeguard
import writeguard

G, I, T = chr(103), chr(105), chr(116)
D, Q, DQ, BS, NL = chr(36), chr(39), chr(34), chr(92), chr(10)
NAME = G + I + T

# the backslash-newline row is a characterization row: the wrapper form was already denied before the fix
CHARACTERIZATION = "backslash newline (characterization row, already denied before the fix)"

DISGUISES = {
    "empty single quotes": G + Q + Q + I + T,
    "backslash": G + BS + I + T,
    CHARACTERIZATION: G + BS + NL + I + T,
    "positional parameter": G + D + "9" + I + T,
    "unset braced parameter": G + D + "{UNSET}" + I + T,
    "ansi-c hex name": D + Q + BS + "x67" + I + T + Q,
    "ansi-c short hex": D + Q + G + BS + "x69" + T + Q,
    "ansi-c octal": D + Q + BS + "147" + I + T + Q,
    "ansi-c three octal": D + Q + BS + "147" + BS + "151" + BS + "164" + Q,
    "ansi-c unicode": D + Q + BS + "u0067" + I + T + Q,
    "ansi-c short unicode": D + Q + BS + "u67" + I + T + Q,
    "ansi-c short long-unicode": D + Q + BS + "U67" + I + T + Q,
    "nul escape hex": D + Q + G + BS + "x00" + I + T + Q,
    "nul escape one-digit octal": D + Q + G + BS + "0" + I + T + Q,
    "nul escape three-digit octal": D + Q + G + BS + "000" + I + T + Q,
}


class LiveBypassCase(unittest.TestCase):
    def setUp(self):
        self.live = make_repo()
        self.addCleanup(shutil.rmtree, self.live, True)
        patcher = mock.patch.object(mergeguard, "checkout_root", return_value=self.live)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.other, True)


class HiddenNameUnderCommentWrapperTest(LiveBypassCase):
    def test_every_disguised_name_under_the_comment_wrapper_reaches_the_live_check(self):
        for label, name in DISGUISES.items():
            with self.subTest(label):
                line = "echo hi # it's" + NL + name + " -C " + self.live + " merge x" + NL + "# won't"
                got = mergeguard.decision("Bash", {"command": line}, self.other)
                self.assertIsNotNone(got, label)
                self.assertEqual(got[0], "deny", label)


class DecoyNameCountTest(LiveBypassCase):
    """Security review finding 1: a plain `$name` decoy balanced the count of name words, so a real disguise on another line slipped."""

    def deny(self, line):
        got = mergeguard.decision("Bash", {"command": line}, self.other)
        self.assertIsNotNone(got, line)
        self.assertEqual(got[0], "deny", line)

    def test_a_dollar_name_decoy_does_not_hide_a_disguised_name_on_the_next_line(self):
        disguised = "env " + G + Q + Q + I + T + " -C " + self.live + " reset --hard"
        self.deny("echo hi # it's" + NL + disguised)                      # the control, no decoy
        self.deny("echo " + D + NAME + " # it's" + NL + disguised)        # the decoy
        self.deny("echo " + D + NAME + " " + D + NAME + " # it's" + NL + disguised)
        head = G + Q + Q + I + T + " -C " + self.live + " merge x"        # the disguise at the head of its segment
        self.deny("echo " + D + NAME + " # it's" + NL + head)

    def test_the_disguise_denies_as_a_pipe_target_and_behind_exec_command_env_and_nice(self):
        for prefix in ("echo x | ", "exec ", "command ", "env ", "nice "):
            with self.subTest(prefix):
                self.deny("echo hi # it's" + NL + prefix + G + Q + Q + I + T + " -C " + self.live + " reset --hard")


class NulWordInLiveCheckTest(LiveBypassCase):
    def test_l1_a_nul_cut_name_word_is_denied(self):
        got = mergeguard.decision("Bash", {"command": "env " + D + Q + NAME + BS + "0x" + Q + " -C " + self.live + " reset --hard"}, self.other)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "deny")


class DestructiveBehindExpansionTest(unittest.TestCase):
    def ask(self, line):
        return writeguard.destructive_git_decision("Bash", {"command": line})

    def asks(self, *lines):
        for line in lines:
            with self.subTest(line):
                got = self.ask(line)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "ask", line)

    def test_m1_a_brace_command_word_behind_a_wrapper_asks(self):
        b = "{" + NAME + ",reset,--hard}"
        c = "{" + NAME + ",reset}"
        self.asks("sudo " + b, "nohup " + c + " --hard", "env {" + NAME + ",push} --force", "xargs " + c + " --hard",
                  "eval '" + b + "'", "sh -c '" + c + " --hard'")

    def test_m1_guard_rows_for_other_wrappers_and_a_here_string_ask(self):
        c = "{" + NAME + ",reset}"
        self.asks("bash <<< '" + c + " --hard'", "time " + D + Q + G + BS + "x69" + T + Q + " reset --hard",
                  "builtin " + D + Q + G + BS + "x69" + T + Q + " reset --hard", "doas -u x " + c + " --hard")

    def test_m2_a_disguised_name_before_an_unreadable_line_asks(self):
        self.asks(G + Q + Q + I + T + " reset --hard" + NL + "echo " + DQ)

    def test_l1_a_nul_in_a_dollar_single_quote_word_asks(self):
        self.asks("env " + D + Q + NAME + BS + "0x" + Q + " reset --hard", D + Q + NAME + BS + "0x" + Q + " reset --hard")

    def test_r4_adjacent_brace_groups_cross_into_the_program_name_behind_a_wrapper(self):
        self.asks("sudo {g,x}{i,y}{t,z} reset --hard", "env {g,x}{i,y}{t,z} reset --hard")

    def test_r4_a_long_brace_line_with_the_name_letters_asks_at_once_and_fast(self):
        import time
        for n in (1000, 2000, 8000):
            line = "sudo " + NAME + " " + "{a,b}" * n + " reset --hard"
            began = time.monotonic()
            got = writeguard._git_text(line)        # the text check itself, below the whole-command size cap
            elapsed = time.monotonic() - began
            self.assertIsNotNone(got, n)
            self.assertLess(elapsed, 0.5, n)

    def test_r4_a_five_thousand_character_brace_line_with_a_hard_reset_asks(self):
        line = "sudo " + NAME + " " + "{a,b}" * 1000 + " reset --hard"
        self.assertIsNotNone(writeguard._git_text(line))
        self.asks(line)

    def test_r4_many_expanded_words_over_the_cap_ask_without_expanding(self):
        self.asks("echo " + NAME + " {a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}{o,p}{q,r} ok")

    def test_everyday_brace_and_wrapper_lines_do_not_ask(self):
        for line in ("echo x{1,2}", "cp f{,.bak}", "sudo ls", "env A=1 make", "mkdir -p src/{a,b}", NAME + " log --format=%H",
                     NAME + " status"):
            with self.subTest(line):
                self.assertIsNone(self.ask(line), line)

    def test_an_ansi_c_name_with_a_hard_reset_asks(self):
        self.asks(D + Q + BS + "x67" + I + T + Q + " reset --hard")

    def test_short_unicode_escapes_spelling_the_name_with_a_hard_reset_ask(self):
        self.asks(D + Q + BS + "u67" + I + T + Q + " reset --hard", D + Q + BS + "U67" + I + T + Q + " reset --hard")

    def test_a_name_built_from_a_variable_with_a_hard_reset_asks(self):
        self.asks("G=" + G + I + "; " + D + "G" + DQ + T + DQ + " reset --hard")

    def test_a_brace_command_word_that_spells_the_name_and_a_destructive_verb_asks(self):
        self.asks("{" + NAME + ",reset,--hard}",
                  "{" + NAME + ",echo} reset --hard",
                  "{" + NAME + ",} reset --hard",
                  "{" + NAME + ",echo} clean -fd",
                  "echo x; {" + NAME + ",echo} reset --hard",
                  "{echo," + NAME + "} reset --hard",
                  "{" + G + ",}" + I + T + " reset --hard",
                  G + "{,}" + I + T + " reset --hard")

    def test_a_range_or_multi_group_or_nested_brace_command_word_with_a_hard_reset_asks(self):
        # security review finding 2: a brace in the command word with no comma group was not judged
        self.asks(G + "{" + I + ".." + I + "}" + T + " reset --hard",
                  "{" + G + ",}{" + I + ",}" + T + " reset --hard",
                  "{{" + G + ",}" + I + T + ",x} reset --hard")

    def test_a_quote_disguised_name_with_a_hard_reset_asks_as_a_pipe_target_and_behind_wrappers(self):
        for prefix in ("echo x | ", "exec ", "command ", "env ", "nice "):
            self.asks(prefix + G + Q + Q + I + T + " reset --hard")

    def test_controls_that_only_look_like_expansions_do_not_ask(self):
        for line in ("echo hello", "ls -la", D + "HOME/bin/tool run", DQ + D + "X" + DQ + " status", D + "EDITOR notes.md",
                     D + "PYTHON setup.py clean", D + "{VENV}/bin/x push origin", D + "EDITOR branch-notes.md",
                     "ls {a,b}.txt", "{a,b}.txt"):
            with self.subTest(line):
                self.assertIsNone(self.ask(line), line)

    def test_asks_by_design_a_variable_command_word_with_a_destructive_verb(self):
        # the guard cannot tell these from a disguised program name, so it asks; the cost is pinned here on purpose
        self.asks(D + "MAKE clean", D + "BIN branch -D x", D + "{DOCKER} restore", D + "COMPOSE push --force")


if __name__ == "__main__":
    unittest.main()
