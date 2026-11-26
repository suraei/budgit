"""R-1220 (R-1199 round 7, F-1-r7-1): a subagent's Budgit read whose `--project` value starts with the home folder written as a
variable passes again; every other variable, a substitution, an env prefix and every non-read row keep their refusal."""
import os

from helpers import ROOT, StudioTestCase
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")       # this plugin's own CLI: a bare `budgit` off PATH is not recognised as it


class HomeReadsTest(StudioTestCase):
    def h(self, cmd, sub=True):
        got, _keys = sl.home_write_check("Bash", {"command": cmd}, self.project, "general-purpose", session="s1", subagent=sub)
        return got

    def assertPasses(self, line):
        self.assertIsNone(self.h(line), line)

    def assertDenied(self, line):
        got = self.h(line)
        self.assertIsNotNone(got, line)
        self.assertEqual(got[0], "deny", line)

    def test_a_read_with_a_home_variable_project_passes_bare_quoted_and_braced(self):
        for home in ("$HOME", '"$HOME"', "${HOME}", '"${HOME}"'):
            for rest in ("ledger show R-1", "ledger list", "status"):        # c1, c7, c8
                self.assertPasses("%s %s --project %s/proj" % (CLI, rest, home))
            self.assertDenied("%s ledger show R-1 --project %s/proj | head -5" % (CLI, home))     # c18; round 4: a pipe is a second step
        self.assertPasses("%s ledger show R-1 --project=$HOME/proj" % CLI)
        self.assertEqual(self.h("%s ledger show R-1 --project $HOME" % CLI),          # the home itself: the verdict of its literal path
                         self.h("%s ledger show R-1 --project %s" % (CLI, os.path.expanduser("~"))))

    def test_any_other_variable_substitution_or_default_in_the_project_value_is_refused(self):
        for value in ("$USER/p", "${HOME:-/x}/p", "$(echo ~)/p", "`pwd`/p", "$HOMEX/p", "$HOME$USER/p", "${HOME}${X}/p", "$X/$HOME/p"):
            self.assertDenied("%s ledger show R-1 --project %s" % (CLI, value))

    def test_an_env_prefix_or_a_home_reassignment_keeps_the_refusal(self):
        self.assertDenied("HOME=/x %s ledger show R-1 --project $HOME/p" % CLI)
        self.assertDenied("env HOME=/x %s ledger show R-1 --project $HOME/p" % CLI)
        self.assertDenied("export HOME=/x; %s ledger show R-1 --project $HOME/p" % CLI)

    def test_a_second_command_or_another_dollar_on_the_line_keeps_the_refusal(self):
        self.assertDenied("%s ledger show R-1 --project $HOME/p; %s ledger add --text x --project $HOME/p" % (CLI, CLI))
        self.assertDenied("%s ledger show R-1 $HOME --project $HOME/p" % CLI)
        self.assertDenied("%s ledger show R-1 --project $HOME/p --file $HOME/x" % CLI)
        self.assertDenied("echo $x\n%s ledger show R-1" % CLI)
        # round 3: a second step that is no known reader (`rm x`) keeps the refusal of `$HOME`
        self.assertDenied("%s ledger show R-1 --project $HOME/p; rm x" % CLI)

    def test_a_write_row_with_a_home_variable_project_keeps_its_verdict(self):
        for rest in ("ledger add --text x", "ledger done R-1 --evidence x", "ledger evidence R-1 --text x", "ledger start R-1"):
            self.assertDenied("%s %s --project $HOME/p" % (CLI, rest))

    def test_a_data_heredoc_with_a_dollar_does_not_taint_a_later_read_line(self):          # c9
        line = "cat <<'EOF' > /tmp/n.md\ncost $5 for $x\nEOF\n%s ledger show R-1" % CLI
        self.assertPasses(line)
        self.assertDenied(line + " --project $HOME/p")                    # round 4: a heredoc line is more than one step

    def test_k8_a_loop_whose_output_goes_to_a_file_under_home_stays_refused(self):
        for tail in ("> $HOME/k8.md", "> ~/k8.md", ">> $HOME/k8.md"):
            self.assertDenied("for i in R-1; do %s ledger show $i; done %s" % (CLI, tail))

    READ = CLI + " ledger show R-1 --project $HOME/p"

    def test_round2_every_home_reassignment_form_keeps_the_refusal_on_the_same_or_an_earlier_line(self):
        forms = ['export "HOME=/x"', 'declare "HOME=/x"', "eval 'HOME=/x'", "HOME+=/x", "read HOME <<< /x",
                 "printf -v HOME %s /x", "unset HOME", "source /tmp/f", ". /tmp/f", 'typeset "HOME=/x"',
                 "readonly HOME=/x", "mapfile HOME < /tmp/f", "getopts a HOME", "local HOME=/x", 'H"O"ME=/x',
                 "alias x=y", "set -- /x", "select HOME in /x; do break; done"]
        for form in forms:
            self.assertDenied("%s; %s" % (form, self.READ))
            self.assertDenied("%s\n%s" % (form, self.READ))
        self.assertDenied("for HOME in /x; do %s ; done" % self.READ)
        self.assertDenied("%s ; for HOME in /x; do :; done" % self.READ)
        self.assertDenied("%s --note HOME" % self.READ)

    def test_round2_an_unquoted_heredoc_body_with_a_substitution_keeps_the_refusal_of_a_read_and_a_write_row(self):
        for body in ("$(id)", "`id`", "${x}", "cost $(id) now"):
            head = "cat <<EOF > /tmp/n.md\n%s\nEOF\n" % body
            self.assertDenied(head + CLI + " ledger show R-1")
            self.assertDenied(head + CLI + " ledger show R-1 --project $HOME/p")
            self.assertDenied(head + CLI + " ledger add --text x")

    def test_round2_a_quoted_heredoc_body_with_a_dollar_does_not_taint_a_later_read_row(self):
        for delim in ("'EOF'", '"EOF"'):
            head = "cat <<%s > /tmp/n.md\ncost $5 and ${x} for $(y)\nEOF\n" % delim
            self.assertPasses(head + CLI + " ledger show R-1")
            self.assertDenied(head + self.READ)                          # round 4: more than one step

    def test_round3_a_word_that_can_change_the_home_variable_keeps_the_read_refusal(self):
        for form in ("print -v HOME /x", "wait -p HOME", "zparseopts -D -E a=b", "zformat -f x y z", "strftime -s HOME %Y 0",
                     "coproc cat", "setopt shwordsplit", "IFS=/", "print -v {H,/tmp/x}OME y"):
            for line in ("%s; %s" % (form, self.READ), "%s\n%s" % (form, self.READ), "%s ; %s" % (self.READ, form)):
                self.assertDenied(line)

    def test_round3_a_step_that_is_not_a_known_reader_keeps_the_read_refusal(self):
        for form in ("python3 /tmp/x.py", "mytool --go", "touch /tmp/x"):
            self.assertDenied("%s; %s" % (form, self.READ))

    def test_round3_a_write_target_under_a_changed_home_variable_is_denied(self):
        for line in ('export "HOME=/tmp/x"; echo x > $HOME/.budgit/f', "read HOME <<< /tmp/x; echo x > $HOME/.budgit/f",
                     'export "HOME=/tmp/x"; echo x > ${HOME}/.budgit/f', "setopt x; echo x > $HOME/.budgit/f",
                     "IFS=/; echo x > $HOME/.budgit/f"):
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round5_a_printf_with_v_keeps_the_write_denial(self):
        for line in ("printf -v HOME %s /tmp/x; echo x > $HOME/.budgit/f", "printf -vHOME /tmp/x; echo x > $HOME/.budgit/f",
                     "printf -v \"$n\" /tmp/x; echo x > $HOME/.budgit/f"):
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round5_a_printf_option_the_shell_builds_keeps_the_write_denial(self):
        """A printf counts as plain only with a literal format word; every spelling of `-v` that is no literal `-v` is a changer."""
        tail = "; echo x > $HOME/.budgit/f"
        pre = "n=HO m=ME; "
        dash, bs = chr(45), chr(92)
        for line in (pre + "printf -$'v' $n$m /tmp/x" + tail, pre + "printf $'-v' $n$m /tmp/x" + tail,
                     pre + "x=v; printf -$x $n$m /tmp/x" + tail, pre + "printf $'" + bs + "x2dv' $n$m /tmp/x" + tail,
                     pre + "printf -$'" + bs + "166' $n$m /tmp/x" + tail, pre + "x=" + dash + "v; printf \"$x\" $n$m /tmp/x" + tail,
                     pre + "builtin printf -$'v' $n$m /tmp/x" + tail, pre + "command printf $'-v' $n$m /tmp/x" + tail,
                     pre + "printf '%s' a; printf -$'v' $n$m /tmp/x" + tail,           # every printf must be plain
                     pre + "printf '" + dash + "v' $n$m /tmp/x" + tail, pre + "printf " + bs + dash + "v $n$m /tmp/x" + tail):
            self.assertTrue(sl._home_write_target_unsafe(line), line)
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round5_a_decoy_printf_cannot_cancel_a_split_printf(self):
        """The printf letters are counted on the quote-stripped text and every printf word on the raw text must be plain: a decoy
        that adds a format-bearing printf (a backslash or a quote before it) cannot pay for a quote-split `pr''intf -v`."""
        bs = chr(92)
        pre, tail = "n=HO m=ME; ", "; echo x > $HOME/.budgit/f"
        for line in (pre + "pr''intf -$'v' $n$m /tmp/x; echo x" + bs + "printf '%s'" + tail,
                     pre + "pr''intf -$'v' $n$m /tmp/x; echo \"x\"printf '%s'" + tail,
                     "pr''intf '%s' x" + tail, pre + "pr\"\"intf -$'v' $n$m /tmp/x; echo x" + bs + "printf '%s'" + tail,
                     "echo " + bs + "printf '%s' x" + tail):          # a backslash before printf: not a word, so a changer (fail-safe)
            self.assertTrue(sl._home_write_target_unsafe(line), line)
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round5_a_printf_with_a_numeric_or_positional_conversion_stays_a_changer(self):
        """zsh evaluates the argument of a numeric conversion as arithmetic, which can assign: only %s %b %c %q %% (flags, digit
        width and precision) make a printf plain."""
        pre, tail = "a=HO; b=ME; ", "; echo x > $HOME/.budgit/f"
        for line in (pre + "printf '%d' \"$a$b=0\"" + tail, "printf '%d' $'" + chr(92) + "x48OME=0'" + tail,
                     "printf '%*s' 3 x" + tail, "printf '%1$s' x" + tail, pre + "printf %x \"$a$b=0\"" + tail,
                     pre + "printf '%s %i' x \"$a$b=0\"" + tail, pre + "printf '%.*f' 1 \"$a$b=0\"" + tail,
                     "printf '%5%' x" + tail):
            self.assertTrue(sl._home_write_target_unsafe(line), line)
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round5_a_printf_with_a_literal_format_word_is_left_alone(self):
        for line in ("printf '%s' \"$HOME\"", "printf \"%s\" x > $HOME/out.txt", "printf %s x > $HOME/out.txt",
                     "printf '%s\\n' x > $HOME/out.txt", "command printf %s x > $HOME/out.txt",
                     "command printf \"%s\" x > $HOME/out.txt", "printf \"%s\\n\" x > $HOME/out.txt",
                     "printf %s\\\\n x > $HOME/out.txt"):
            self.assertFalse(sl._home_write_target_unsafe(line), line)

    def test_round5_a_format_outside_the_closed_set_of_six_stays_a_changer(self):
        """Only `'%s'`, `'%s\\n'`, `"%s"`, `"%s\\n"`, `%s`, `%s\\\\n` as a whole word make a printf plain: a glued second word and a
        decoded escape in the format (zsh turns `\\x25` and `\\045` into `%`) are changers."""
        bs = chr(92)
        pre, tail = "a=HO; b=ME; ", "; echo x > $HOME/.budgit/f"
        for line in ("printf '%s'%d x $'" + bs + "x48OME=0'" + tail, "printf '%s''%d' x \"$a$b=0\"" + tail,
                     "f=%d; printf \"%s\"$f x \"$a$b=0\"" + tail, "printf '" + bs + "x25d' $'" + bs + "x48OME=0'" + tail,
                     "printf '" + bs + "045d' $'" + bs + "x48OME=0'" + tail,
                     "printf '%5.2s' x" + tail, "printf '%s %%' x" + tail, "printf '%-8s|%b|%c|%q' a b c d" + tail,
                     "printf '%s'x y" + tail, "printf '%s" + bs + "n'x y" + tail, "printf %s" + bs + "n x" + tail):
            self.assertTrue(sl._home_write_target_unsafe(line), line)
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round3_a_literal_home_assignment_does_not_skip_the_write_check(self):
        for line in ("export HOME=/tmp/a; read HOME <<< x; echo x > $HOME/f", "HOME=/tmp/a; setopt x; echo x > ${HOME}/f"):
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round3_a_plain_write_target_is_judged_like_the_literal_home_path(self):
        home = os.path.expanduser("~")
        for tail in ("echo x > %s/f", "cp x %s/y", "echo x > %s/.budgit/f"):
            for sub in (True, False):
                self.assertEqual(self.h(tail % "$HOME", sub), self.h(tail % home, sub), tail)
                self.assertEqual(self.h(tail % "${HOME}", sub), self.h(tail % home, sub), tail)

    def test_round3_a_plain_read_still_passes_and_a_cd_then_read_now_keeps_the_refusal_round4(self):
        self.assertPasses("%s ledger show --project $HOME/p" % CLI)
        self.assertDenied("cd /tmp && %s status --project $HOME/p" % CLI)        # round 4: not one step, so the refusal stays
        self.assertDenied("cat /tmp/a | %s ledger show R-1 --project $HOME/p | head -3" % CLI)

    def test_round4_the_home_variable_expands_only_on_a_one_step_line(self):
        b = "budgit ledger show --project $HOME/p"
        for line in ("x; " + b, b + " | cat", "cat <<< rm; " + b + "; rm x", "FOO=1 " + b, "cd /tmp && budgit status --project $HOME/p"):
            self.assertFalse(sl._home_var_stays(line, ()), line)
            self.assertDenied(line.replace("budgit", CLI))
        self.assertTrue(sl._home_var_stays(b, ()))
        self.assertPasses(b.replace("budgit", CLI))

    def test_round4_a_write_target_of_any_kind_under_a_changed_home_variable_is_denied(self):
        lines = []
        for t in ("$HOME/.budgit/f", "$HOME/f"):
            lines += ['export "HOME=/tmp/x"; echo x > %s' % t, "read HOME <<< /tmp/x; echo x > %s" % t,
                      'export "HOME=/tmp/x"; echo x > ${HOME}/%s' % t[6:], "setopt x; echo x > %s" % t, "IFS=/; echo x > %s" % t]
        lines += ["read HOME <<< /tmp/x; cp x $HOME/f", "setopt x; touch $HOME/f", "HOME=/tmp/a true; echo x > $HOME/f",
                  "HOME=/tmp/a true; echo x > ${HOME}/f", "env HOME=/tmp/a true; cp x $HOME/f", "setopt x; mv x $HOME/f",
                  "setopt x; dd if=x of=$HOME/f", "HOME=/tmp/a; HOME=/tmp/b; cp x $HOME/f"]
        for line in lines:
            for sub in (True, False):
                got = self.h(line, sub)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_round4_a_plain_cp_or_mv_target_is_judged_like_the_literal_home_path(self):
        home = os.path.expanduser("~")
        for tail in ("mv x %s/y", "touch %s/f", "dd if=x of=%s/f"):
            for sub in (True, False):
                self.assertEqual(self.h(tail % "$HOME", sub), self.h(tail % home, sub), tail)


if __name__ == "__main__":
    import unittest
    unittest.main()
