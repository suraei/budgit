"""R-1037 (I-1538 verdicts A3, A4, A8): the no-attribution guard judges every `git commit` after env/sudo/path wrappers and
ASKS when the message comes from a source it cannot read. One test per probe form, plus the forms that pass today."""
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts plugin/scripts on sys.path before the plugin import)
import attribution

TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


class FormsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        with open(os.path.join(self.d, "config.json"), "w") as fh:
            fh.write(json.dumps({"no_claude_attribution": True}))
        self.write("attrib.txt", "msg\n\n%s\n" % TRAILER)
        self.write("clean.txt", "msg\n\nMentions Claude in prose.\n")
        os.makedirs(os.path.join(self.d, "adir"))

    def write(self, name, text):
        with open(os.path.join(self.d, name), "w") as fh:
            fh.write(text)

    def decide(self, command, enabled=None):
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": self.d}):
            return attribution.decision("Bash", {"command": command}, self.d, enabled)

    def kind(self, command):
        got = self.decide(command)
        return got[0] if got else None


class ProbeA4WrappedGitTest(FormsCase):
    """A4: cat f | env git commit -F - (also /usr/bin/git, sudo git) was not read."""

    def test_wrappers_and_paths_with_trailer_denied(self):
        for git in ("env git", "/usr/bin/git", "sudo git", "command git", "nice -n 10 git", "nohup git", "time git",
                    "./git", "env -i A=1 git", "A=1 git", "sudo -u root env git", "git -C . ", "git -c user.name=x"):
            self.assertEqual(self.kind("cat attrib.txt | %s commit -F -" % git), "deny", git)

    def test_wrapped_redirect_and_file_forms_denied(self):
        self.assertEqual(self.kind("env git commit -F - < attrib.txt"), "deny")
        self.assertEqual(self.kind("sudo /usr/bin/git commit -F attrib.txt"), "deny")
        self.assertEqual(self.kind("git -C . commit -F attrib.txt"), "deny")
        self.assertEqual(self.kind("bash -c 'git commit -F attrib.txt'"), "deny")

    def test_wrapped_clean_forms_pass(self):
        for git in ("env git", "/usr/bin/git", "sudo git"):
            self.assertIsNone(self.decide("cat clean.txt | %s commit -F -" % git), git)
        self.assertIsNone(self.decide("sudo /usr/bin/git commit -F clean.txt"))

    def test_wrapped_literal_trailer_in_dash_m_denied(self):
        self.assertEqual(self.kind("env git commit -m 'x\n\n%s'" % TRAILER), "deny")


class ProbeA8UnreadableSourceTest(FormsCase):
    """A8: -F /dev/fd/3 3< f and -F <(cat f) read a file the hook process cannot see: ask."""

    def test_fd3_asks(self):
        self.assertEqual(self.kind("git commit -F /dev/fd/3 3< attrib.txt"), "ask")
        self.assertEqual(self.kind("git commit -F /dev/fd/3 3< clean.txt"), "ask")

    def test_process_substitution_asks(self):
        self.assertEqual(self.kind("git commit -F <(cat attrib.txt)"), "ask")
        self.assertEqual(self.kind("git commit -F - < <(cat attrib.txt)"), "ask")
        self.assertEqual(self.kind("git commit --file=<(cat attrib.txt)"), "ask")
        self.assertEqual(self.kind("git commit -t <(cat attrib.txt)"), "ask")

    def test_other_unreadable_sources_ask(self):
        for cmd in ("git commit -F /proc/self/fd/3 3< clean.txt",
                    "git commit -F \"$MSGFILE\"",
                    "git commit -F `ls m*`",
                    "git commit -F \"$(pwd)/clean.txt\"",
                    "git commit -t \"$T\"",
                    "git commit -F adir",
                    "git commit -F",
                    "echo $(cat clean.txt) | git commit -F -",
                    "cat $(ls clean.txt) | git commit -F -",
                    "cat clean.txt | grep msg | git commit -F -",
                    "git commit -F - <<< \"$MSG\"",
                    "git commit -F -",
                    "git commit -F /dev/stdin",
                    "cat - | git commit -F -",
                    "env git commit -F /dev/fd/3 3< clean.txt",
                    "sudo git commit -F <(cat clean.txt)"):
            self.assertEqual(self.kind(cmd), "ask", cmd)

    def test_deny_beats_ask_when_the_trailer_is_visible(self):
        self.assertEqual(self.kind("git commit -F attrib.txt -F <(cat clean.txt)"), "deny")

    def test_dash_f_stdin_from_a_literal_or_file_does_not_ask(self):
        self.assertIsNone(self.decide("git commit -F - <<'EOF'\nfix thing\nEOF"))
        self.assertIsNone(self.decide("printf 'fix thing\\n' | git commit -F -"))
        self.assertIsNone(self.decide("echo 'fix thing' | env git commit -F -"))
        self.assertIsNone(self.decide("git commit -F - < clean.txt"))
        self.assertIsNone(self.decide("git commit -F /dev/stdin < clean.txt"))
        self.assertIsNone(self.decide("git commit -F - <<< 'fix thing'"))


class ProbeA3TemplateTest(FormsCase):
    """A3: git commit -t FILE (--cleanup=whitespace commits the template text with no editor)."""

    def test_template_with_trailer_denied(self):
        for cmd in ("git commit -t attrib.txt", "git commit --template=attrib.txt", "git commit --template attrib.txt",
                    "git commit -tattrib.txt", "git commit -at attrib.txt", "git commit -t attrib.txt --cleanup=whitespace",
                    "git commit --cleanup=whitespace -t attrib.txt", "git commit --cleanup verbatim -t attrib.txt",
                    "env git commit -t attrib.txt --cleanup=whitespace"):
            self.assertEqual(self.kind(cmd), "deny", cmd)

    def test_clean_template_with_whitespace_or_verbatim_cleanup_asks(self):
        for cmd in ("git commit -t clean.txt --cleanup=whitespace", "git commit --template=clean.txt --cleanup=verbatim",
                    "git commit --cleanup verbatim -t clean.txt", "git -c commit.cleanup=whitespace commit -t clean.txt"):
            self.assertEqual(self.kind(cmd), "ask", cmd)

    def test_clean_template_passes(self):
        self.assertIsNone(self.decide("git commit -t clean.txt"))
        self.assertIsNone(self.decide("git commit -t clean.txt --cleanup=strip"))


class PassingFormsTest(FormsCase):
    """What passes today still passes: plain -m, -F with a real file, heredoc literal, the editor, the switch off."""

    def test_plain_dash_m(self):
        self.assertIsNone(self.decide("git commit -m 'fix thing'"))
        self.assertIsNone(self.decide("git commit -am 'fix thing'"))
        self.assertIsNone(self.decide("git commit -m 'mentions Claude in prose'"))
        self.assertEqual(self.kind("git commit -m 'x\n\n%s'" % TRAILER), "deny")

    def test_dash_f_with_a_real_file(self):
        self.assertIsNone(self.decide("git commit -F clean.txt"))
        self.assertIsNone(self.decide("git commit --file=clean.txt"))
        self.assertIsNone(self.decide("git commit --file clean.txt"))
        self.assertIsNone(self.decide("git commit -F %s" % os.path.join(self.d, "clean.txt")))
        self.assertEqual(self.kind("git commit -F attrib.txt"), "deny")
        self.assertEqual(self.kind("git commit -aF attrib.txt"), "deny")

    def test_missing_file_asks(self):
        """r4: a -F/-t file that does not exist when the guard reads the command is unreadable now."""
        self.assertEqual(self.kind("git commit -F missing.txt"), "ask")
        self.assertEqual(self.kind("git commit -t missing.txt"), "ask")
        self.assertEqual(self.kind("printf 'Co-Authored-By: Cla%s\\n' ude > m && git commit -F m"), "ask")

    def test_heredoc_literal(self):
        self.assertIsNone(self.decide("git commit -F - <<'EOF'\nfix thing\nEOF"))
        self.assertEqual(self.kind("git commit -F - <<'EOF'\nfix\n\n%s\nEOF" % TRAILER), "deny")
        self.assertIsNone(self.decide("git commit -m \"$(cat <<'EOF'\nfix thing\nEOF\n)\""))

    def test_editor_commit_and_other_git_calls_are_not_judged(self):
        for cmd in ("git commit", "git commit -a", "git commit --amend", "git status", "git log -F x",
                    "echo 'git commit -F /dev/fd/3'", "ls"):
            self.assertIsNone(self.decide(cmd), cmd)

    def test_setting_off_passes_everything(self):
        self.assertIsNone(self.decide("git commit -F /dev/fd/3 3< attrib.txt", enabled=False))
        self.assertIsNone(self.decide("cat attrib.txt | env git commit -F -", enabled=False))

    def test_gh_pr_body_file_still_judged(self):
        self.assertEqual(self.kind("gh pr create --body-file attrib.txt"), "deny")
        self.assertEqual(self.kind("cat attrib.txt | gh pr create --body-file -"), "deny")
        self.assertIsNone(self.decide("gh pr create --body-file clean.txt"))


class R2UnresolvedTest(FormsCase):
    """r2 (verifier forms): anything the guard cannot resolve asks."""

    def test_alias_asks(self):
        self.assertEqual(self.kind("git -c alias.ci=commit ci -q -F /dev/fd/3 3< attrib.txt"), "ask")
        self.assertEqual(self.kind("git config alias.ci2 commit && git ci2 -q -F /dev/fd/3 3< attrib.txt"), "ask")
        self.assertEqual(self.kind("git ci -m 'a global alias'"), "ask")
        self.assertEqual(self.kind("git -c alias.ci=commit ci"), "ask")

    def test_builtin_and_option_free_verbs_do_not_ask(self):
        for cmd in ("git ci", "git status", "git lfs pull", "git add -A && git commit -m x", "git tag -a v1 -m x",
                    "git merge -m x topic", "git log --oneline", "git remote add o u"):
            self.assertIsNone(self.decide(cmd), cmd)

    def test_variable_built_git_asks(self):
        for cmd in ("g=git; $g commit -q -F /dev/fd/3 3< attrib.txt", "g=git; $g commit -m x", "git $v -F clean.txt",
                    "$(echo git) commit -F clean.txt", "`echo git` commit -F clean.txt", "env $g commit -m x"):
            self.assertEqual(self.kind(cmd), "ask", cmd)

    def test_other_dollar_commands_do_not_ask(self):
        self.assertIsNone(self.decide("$HOME/bin/tool -m x"))
        self.assertIsNone(self.decide("x=1; $x -m y"))

    def test_message_built_by_the_shell_asks(self):
        """r3: a -m, -F, -t or --message= argument holding $( , a backtick or >( is unreadable."""
        for cmd in ('git commit -m "$(cat attrib.txt)"', "git commit -m \"fix `cat attrib.txt`\"", 'git commit --message="$(cat f)"',
                    'git commit -F "$(echo clean.txt)"', "git commit -F >(cat attrib.txt)", 'git commit -t "$(echo t)"',
                    'git commit -am "x $(date)"'):
            self.assertEqual(self.kind(cmd), "ask", cmd)
        self.assertEqual(self.kind('git commit -m "$(cat attrib.txt)" -m "%s"' % TRAILER), "deny")

    def test_heredoc_message_substitution_does_not_ask(self):
        self.assertIsNone(self.decide("git commit -m \"$(cat <<'EOF'\nfix: thing\nEOF\n)\""))
        self.assertEqual(self.kind("git commit -m \"$(cat <<'EOF'\nfix\n\n%s\nEOF\n)\"" % TRAILER), "deny")

    def test_any_dollar_in_a_message_value_asks(self):
        for cmd in ('git commit -m "Co-Authored-By: Cla${X}ude <n@example.org>"', 'git commit -m "x $V"',
                    'git commit --message="a${B}c"', 'git commit -F "$F"', 'git commit -t "${T}"', 'git commit -m "$(echo x)"'):
            self.assertEqual(self.kind(cmd), "ask", cmd)

    def test_one_rule_any_dynamic_argv_token_of_a_message_verb_asks(self):
        for cmd in ('git commit-tree -m "$X" HEAD', 'git merge -m "$X" topic', 'git tag -m "$X" v1', 'git notes add -m "$X"',
                    'git commit -m x $ARGS', 'git commit -m x "$@"', 'git commit -m x $*', 'git commit -m"$X"',
                    'git commit --message "$X"', 'git commit -F-', 'git rebase --exec "$C" main', 'git cherry-pick $R',
                    'git revert -m 1 $R', 'git am $P', 'git commit -m x --amend "$(echo a)"',
                    'git commit --trai "$T"', 'git commit --trail=Co-x', 'git commit -m x --tr=a', 'git merge --trailer=a topic'):
            self.assertEqual(self.kind(cmd), "ask", cmd)

    def test_one_rule_stays_silent_where_it_must(self):
        for cmd in ("git tag -m 'release one'", "git log --grep x", 'git log --grep "$X"', 'git status "$D"',
                    'git commit -m "fix: x"', 'git -C %s commit -m "x"' % self.d):
            self.assertIsNone(self.decide(cmd), cmd)
        self.assertEqual(self.kind('git merge -m "%s $X" topic' % TRAILER), "deny")

    def test_trailer_option_asks(self):
        for cmd in ('git commit -m x --trailer "Co-Authored-By: Cla$X"', 'git commit -m x --trailer "Reviewed-by: a"',
                    'git -c trailer.co.key=Co-Authored-By commit -m x', "git -c trailer.ifexists=add commit -m x"):
            self.assertEqual(self.kind(cmd), "ask", cmd)

    def test_the_hook_asks_on_a_shell_built_message(self):
        import subprocess
        import sys
        hook = os.path.join(helpers.ROOT, "plugin", "scripts", "hook.py")
        payload = {"tool_name": "Bash", "session_id": "s1", "cwd": "/tmp",
                   "tool_input": {"command": 'git commit -m "Cla${X}ude"'}}
        out = subprocess.run([sys.executable, hook, "pre-tool-use"], input=json.dumps(payload), capture_output=True,
                             text=True, env=dict(os.environ, HOME=self.d, BUDGIT_HOME=self.d))
        self.assertIn('"ask"', out.stdout)

    def test_heredoc_with_a_second_delimiter_after_the_first_asks(self):
        for body in ("cat attrib.txt\nEOF\n)\"", "cat attrib.txt; echo\nEOF\n)\""):
            cmd = "git commit -m \"$(cat <<'EOF'\nfix\nEOF\n" + body
            self.assertEqual(self.kind(cmd), "ask", cmd)

    def test_double_delimiter_heredoc_asks(self):
        cmd = "git commit -m \"$(cat <<'EOF'\nhi\nEOF\nprintf 'Co-Authored-By: Cla%%s' ude >> m\nEOF\n)\""
        self.assertEqual(self.kind(cmd), "ask")
        self.assertIsNone(self.decide("git commit -m \"$(cat <<'EOF'\nfix: $5 and more\n\nbody\nEOF\n)\""))
        self.assertEqual(self.kind("git commit -m \"$(cat <<'EOF'\nfix\n\n%s\nEOF\n)\"" % TRAILER), "deny")

    def test_every_git_verb_with_a_literal_trailer_is_denied(self):
        for cmd in ('git merge -m "x\n\n%s" topic' % TRAILER, 'git tag -a v1 -m "%s"' % TRAILER,
                    'git commit-tree HEAD^{tree} -m "%s"' % TRAILER, 'git notes add -m "%s"' % TRAILER,
                    'git cherry-pick x && git log --grep "%s"' % TRAILER,
                    'git rebase --exec \'git commit --amend -m "%s"\' main' % TRAILER):
            self.assertEqual(self.kind(cmd), "deny", cmd)
        self.assertIsNone(self.decide("git tag -a v1 -m 'release one'"))

    def test_plain_forms_pass_silently(self):
        self.write("msg.txt", "fix: x\n")
        p = os.path.join(self.d, "msg.txt")
        for cmd in ('git commit -m "fix: x"', 'git -C %s commit -m "x"' % self.d, "git commit -F %s" % p,
                    'python3 -m unittest t && git -C %s commit -m "x"' % self.d,
                    "python3 -m unittest t && git -C %s commit -F %s" % (self.d, p),
                    "cat clean.txt > out.txt; git commit -F clean.txt", "git add -A && git commit -F clean.txt"):
            self.assertIsNone(self.decide(cmd), cmd)

    def test_oversize_file_asks(self):
        self.write("big.txt", "x" * (attribution._MAX_READ + 50) + "\n\n%s\n" % TRAILER)
        self.write("edge.txt", "y" * attribution._MAX_READ)
        self.assertEqual(self.kind("git commit -F big.txt"), "ask")
        self.assertEqual(self.kind("cat big.txt | git commit -F -"), "ask")
        self.assertEqual(self.kind("git commit -t big.txt"), "ask")
        self.assertIsNone(self.decide("git commit -F edge.txt"))

    def test_template_from_config_follows_the_dash_t_rules(self):
        self.assertEqual(self.kind("git -c commit.template=attrib.txt -c commit.cleanup=whitespace commit -q"), "deny")
        self.assertEqual(self.kind("git -c commit.template=attrib.txt commit --cleanup=whitespace"), "deny")
        self.assertEqual(self.kind("git -c commit.template=attrib.txt commit"), "deny")
        self.assertEqual(self.kind("git -c commit.template=clean.txt -c commit.cleanup=whitespace commit -q"), "ask")
        self.assertEqual(self.kind("git -c commit.template=clean.txt commit --cleanup=verbatim"), "ask")
        self.assertEqual(self.kind("git -c commit.template=\"$T\" commit"), "ask")
        self.assertIsNone(self.decide("git -c commit.template=clean.txt commit"))

    def test_template_set_by_a_config_call_in_the_same_command(self):
        self.assertEqual(self.kind("git config commit.template attrib.txt && git commit -q"), "deny")
        self.assertEqual(self.kind("git config commit.template clean.txt && git config commit.cleanup verbatim && git commit"), "ask")
        self.assertIsNone(self.decide("git config commit.template clean.txt && git commit"))

    def test_cat_heredoc_pipe_is_a_literal_again(self):
        self.assertIsNone(self.decide("cat <<EOF | git commit -F -\nfix thing\nEOF"))
        self.assertIsNone(self.decide("cat <<'EOF' | env git commit -F -\nfix thing\nEOF"))
        self.assertEqual(self.kind("cat <<EOF | git commit -F -\nfix\n\n%s\nEOF" % TRAILER), "deny")


if __name__ == "__main__":
    unittest.main()
