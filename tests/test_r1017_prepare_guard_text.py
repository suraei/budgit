"""R-1017 r2: an allowlist in front of the word guard. The word guard (any line naming the approval words asks) is unchanged;
one relax layer lets through only a reader (grep cat head tail wc ls), `git [-C p] commit [-q] -m <literal>` and
`budgit ledger <verb> ... --text|--title|--evidence|--reason|--note <literal>`, split at ; && ||, a pipeline being one unit.
The refused and passing phrases live here only."""
import os
import unittest

from helpers import StudioTestCase
import studiolib as sl

FILE = "approved" + "_prepare"
SRC = "plugin/scripts/studiolib.py"
HOME_FILE = "~/.budgit/" + FILE + ".json"
CLI = os.path.join(os.path.dirname(os.path.abspath(sl.__file__)), "..", "bin", "budgit")
APPROVE = "budgit prepare --approve"


class Base(StudioTestCase):
    def asked(self, command):
        got = sl.approval_decision(command, self.project)
        if got is not None:
            self.assertEqual(got[0], "ask", command)
        return got is not None

    def all_asked(self, cmds):
        for cmd in cmds:
            self.assertTrue(self.asked(cmd), cmd)


class StillRefusedTest(Base):
    def test_cli_approve_call_is_refused_in_every_spelling(self):
        self.all_asked([APPROVE, "/opt/homebrew/bin/" + APPROVE, CLI + " prepare --approve",
                        "env BUDGIT_HOME=/tmp/x " + APPROVE, "FOO=1 budgit --project /tmp/p prepare --approve",
                        "true && " + APPROVE, "true ; " + APPROVE, "budgit prepare --appr",
                        "python3 " + CLI + " prepare --approve", "echo --approve | xargs budgit prepare",
                        "bash -c '" + APPROVE + "'"])

    def test_any_write_onto_the_approvals_file_is_refused(self):
        self.all_asked(["echo x > " + HOME_FILE, "echo x >> " + HOME_FILE, "echo x | tee " + HOME_FILE,
                        "cp /tmp/x " + HOME_FILE, "mv /tmp/x " + HOME_FILE, "sed -i s/a/b/ " + HOME_FILE,
                        "python3 -c \"open('" + FILE + ".json','w')\"", "echo x > $HOME/.budgit/" + FILE + ".json"])

    def test_the_echo_into_a_scratch_file_has_a_redirect_so_it_goes_to_the_word_guard(self):
        self.assertTrue(self.asked("echo '" + APPROVE + " and " + FILE + "' > /private/tmp/scratch.txt"))

    def test_plain_reads_of_the_answers_file_in_a_path_stay_asked(self):
        self.all_asked(["cat " + HOME_FILE, "head -3 " + HOME_FILE, "ls -la " + HOME_FILE, "wc -l " + FILE + ".json",
                        "sed -n 1,3p " + HOME_FILE, "cat " + HOME_FILE + " | head"])


class ReaderResidualsTest(Base):
    def test_grep_with_a_combined_pattern_flag_asks(self):
        self.all_asked(["grep -en " + FILE + " /dev/null", "grep -ne " + FILE + " /dev/null", "grep -e " + FILE + " x",
                        "grep -rne " + FILE + " plugin"])

    def test_a_reader_with_a_path_in_the_approvals_folder_asks_in_any_spelling(self):
        home = os.path.expanduser("~")
        self.all_asked(["grep -r " + FILE + " ~/.budgit", "grep -r " + FILE + " " + home + "/.budgit",
                        "grep -r " + FILE + " .budgit", "grep -r " + FILE + " ../.budgit/x",
                        "grep -rn " + FILE + " " + home + "/.budgit | head"])


class NowPassesTest(Base):
    def test_grep_with_the_file_name_as_pattern_over_the_source_passes(self):
        self.assertFalse(self.asked("grep -n '%s' %s" % (FILE, SRC)))
        self.assertFalse(self.asked("grep -nE \"approve_prepare|%s\" %s" % (FILE, SRC)))
        self.assertFalse(self.asked("grep -rn %s plugin | head" % FILE))

    def test_git_commit_message_naming_the_subcommand_and_flag_passes(self):
        self.assertFalse(self.asked('true && git commit -m "R-1: the guard let budgit prepare --approve text pass"'))
        self.assertFalse(self.asked("git -C /tmp/x commit -q -m 'budgit prepare --approve in a message'"))

    def test_ledger_text_values_naming_the_subcommand_and_flag_pass(self):
        for flag in ("--text", "--title", "--evidence", "--reason", "--note"):
            self.assertFalse(self.asked("budgit ledger add %s 'never run %s'" % (flag, APPROVE)), flag)
        self.assertFalse(self.asked("%s ledger note R-1 --text 'file %s'" % (CLI, FILE)))


class AllowlistShapeTest(Base):
    def test_only_the_three_shapes_pass_other_programs_with_the_same_text_ask(self):
        for other in ["somecmd", "printf %s", "mytool --note", "rg", "sed -n 1p", "find . -name"]:
            self.assertTrue(self.asked(other + " '" + APPROVE + "'"), other)

    def test_shapes_with_an_extra_option_or_wrapper_fall_to_the_word_guard(self):
        self.all_asked(["git -c user.name=x commit -m '" + APPROVE + "'", "git commit -F x -m '" + APPROVE + "'",   # R-1027: -a now passes
                        "git commit --amend -m '" + APPROVE + "'", "git commit -m " + FILE,
                        "grep -f x '" + APPROVE + "'", "grep -n --include=x '" + APPROVE + "' .",
                        "budgit ledger add --text x prepare --approve",
                        "budgit prepare --text '" + APPROVE + "'", "env budgit ledger add --text '" + APPROVE + "'",
                        "budgit ledger frob --text '" + APPROVE + "'", "grep prepare x | sort | " + APPROVE])

    def test_a_pipeline_that_names_the_words_passes_only_if_every_member_is_a_reader(self):
        self.assertFalse(self.asked("grep -rn prepare plugin | head"))
        self.assertTrue(self.asked("grep -rn prepare plugin | xargs " + APPROVE))
        self.assertTrue(self.asked("echo prepare | xargs"))

    def test_a_line_with_a_substitution_backtick_redirect_or_subshell_goes_to_the_word_guard(self):
        self.all_asked(["git commit -m \"$(echo " + APPROVE + ")\"", "echo `" + APPROVE + "`", "( " + APPROVE + " )",
                        "grep '" + APPROVE + "' x > /tmp/o", "cat <<EOF\n" + APPROVE + "\nEOF"])


class VerifierBypassTest(Base):
    def test_every_round1_bypass_is_asked(self):
        self.all_asked([
            "sed -n 'w " + HOME_FILE + "' /tmp/x", "ed -s " + HOME_FILE, "ex -sc wq " + HOME_FILE,
            "git -c alias.ci='!" + APPROVE + "' ci", "find . -exec " + APPROVE + " ;", "find . -exec " + APPROVE + " {} +",
            "sqlite3 /tmp/db '.output " + HOME_FILE + "'",
            "env -u X " + APPROVE, "env -S '" + APPROVE + "'", "timeout -s KILL 5 " + APPROVE, "sudo " + APPROVE,
            "stdbuf -o0 " + APPROVE, "lockf /tmp/l " + APPROVE, "caffeinate " + APPROVE, "arch -arm64 " + APPROVE,
            "script -q /dev/null " + APPROVE, "setsid " + APPROVE, "watch " + APPROVE, "parallel ::: " + APPROVE,
            "tmux new '" + APPROVE + "'",
            "if true; then " + APPROVE + "; fi", "for i in 1; do " + APPROVE + "; done",
            "if false; then true; else " + APPROVE + "; fi", "! " + APPROVE])

    def test_cat_of_the_file_then_another_command_is_judged_per_part_and_the_cat_part_asks(self):
        self.assertTrue(self.asked("cat " + HOME_FILE + "; python3 x.py"))

    def test_regression_prepare_word_next_to_xargs_asks(self):
        self.assertTrue(self.asked("echo prepare | xargs"))


if __name__ == "__main__":
    unittest.main()
