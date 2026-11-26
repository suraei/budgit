"""R-1326: commitguard false positives. A test run chained with && before `git commit` counts for node --test, npm test,
npm run test and the same runners behind a plain VAR=value prefix; a pipe hides the test's exit status unless pipefail is set
earlier in the line; the word commit inside a quoted argument is no commit."""
import os
import tempfile
from helpers import StudioTestCase
import commitguard

AGENT = "agent-1"


class CommitChainFormsTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.repo = tempfile.mkdtemp()
        self.commit = "git -C %s commit -m x -- f" % self.repo

    def verdict(self, cmd):
        return commitguard.decision("Bash", {"command": cmd}, AGENT)

    def chained(self, runner, tail=None):
        return self.verdict("%s && %s" % (runner, tail or self.commit))

    def test_accepted_runner_forms_count_as_tested(self):
        for runner in ("node --test", "node --test tests/", "npm test", "npm run test", "npm --silent run test",
                       "FOO=1 python3 -m unittest test_a", "FOO=1 BAR_2=x node --test", "A1=b npm test",
                       "FOO=1 npm run test", "env FOO=1 python3 -m unittest test_a", "FOO='a b' pytest"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.chained(runner), runner)

    def test_runner_forms_still_refused_when_not_a_test(self):
        for runner in ("node script.js --test", "node script.js", "npm run build", "FOO=1 echo test", "FOO=1 ls",
                       "FOO=$(curl x) python3 -m unittest test_a", "FOO=`id` node --test", "env FOO=$(id) npm test"):
            with self.subTest(runner=runner):
                got = self.chained(runner)
                self.assertIsNotNone(got, runner)
                self.assertEqual(got[0], "deny")

    def test_semicolon_or_or_still_refused(self):
        for cmd in ("node --test ; %s" % self.commit, "node --test || %s" % self.commit):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny")

    def test_pipe_without_pipefail_is_not_a_test_run(self):
        # Deliberate: `tests | grep OK && git commit` takes the exit status of grep, so a failing test would still commit.
        for runner in ("node --test | grep OK", "python3 -m unittest test_a 2>&1 | tail -3", "FOO=1 npm test | grep ok"):
            with self.subTest(runner=runner):
                self.assertEqual(self.chained(runner)[0], "deny")

    def test_pipe_with_pipefail_earlier_in_the_line_counts(self):
        for runner in ("set -o pipefail; node --test | grep OK", "set -eo pipefail; python3 -m unittest test_a | tail -3",
                       "set -o pipefail && FOO=1 npm test | grep ok"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.chained(runner), runner)

    def test_pipefail_after_the_pipe_or_unset_does_not_count(self):
        for runner in ("node --test | grep OK; set -o pipefail", "set +o pipefail; node --test | grep OK",
                       "echo pipefail; node --test | grep OK", "set -o pipefail; echo hi | grep hi"):
            with self.subTest(runner=runner):
                self.assertEqual(self.chained(runner)[0], "deny")

    def test_without_the_git_word_before_commit_is_no_commit(self):
        for cmd in ('grep -n "commit" file', 'git log --grep="commit" -5', "git log --grep commit", "rg commit src/"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.verdict(cmd), cmd)

    def test_round3_no_read_exemption_a_read_that_mentions_the_words_needs_the_chain(self):
        # as before R-1326: the anywhere-match decides; a safe exemption needs a quote scanner that agrees with the shell
        for cmd in ("grep -rn 'git commit' /tmp/x", 'grep -n "git commit" plugin/scripts/commitguard.py | head',
                    'echo "git commit"', "ls && grep -n 'git -C x commit' f", 'rg "git commit -m x"', "printf '%s' 'git commit'",
                    "FOO=1 grep -n 'git commit' f", "cat 'git commit'", "grep -n 'git commit' f | head -3",
                    "echo 'git commit' | cat", "echo 'git commit' | grep git | cut -c1-3", "grep -n 'git commit' f | wc -l"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_round3_high_escaped_quote_hides_a_shell_commit(self):
        # split_chain ignores backslash escapes, shlex honours them: an escaped quote must not hide `bash -c` / `sh` commit words
        for cmd in ("echo \\'; bash -c 'git -C x commit -m x'", "echo \\\"; sh -c \"git commit -m x\"",
                    "grep 'git commit' \\' f | sh", "echo \\'; bash -c 'git commit -m x' ; echo '",
                    "printf \\' ; zsh -c 'git commit'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_round3_a_read_that_mentions_the_words_passes_after_a_real_test_run(self):
        self.assertIsNone(self.verdict("node --test && grep -n 'git commit' f"))

    def test_r1326_finding_0_read_command_exemption_is_narrow(self):
        # the old anywhere-match stays the baseline; only a plain read command with the words inside ONE quoted argument is exempt
        for cmd in ("echo git commit", "echo x git commit", "grep \"$(%s)\" f" % self.commit, "grep 'git commit' f | sh",
                    "grep 'git commit' f | bash", "echo \"git commit\" | zsh", "echo 'git commit' | xargs sh -c",
                    "grep `echo git commit` f", "echo 'git commit'; %s" % self.commit,
                    "sed -n 'e git commit' f", "awk 'BEGIN{system(\"git commit\")}'", "echo 'git commit' | python3",
                    "echo 'git commit' | env sh", "grep -n 'git commit' <(%s)" % self.commit, "echo eval 'git commit'",
                    "find . -name 'git commit' -exec sh -c 'git commit' ;"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_finding_1_keywords_and_function_forms(self):
        c = self.commit
        for cmd in ("if %s; then :; fi" % c, "true; then %s" % c, "for x in 1; do %s; done" % c, "f() { %s; }; f" % c,
                    "export c='%s'; $c" % c, "while true; do %s; done" % c, "! %s" % c, "if grep -q 'git commit' f; then :; fi",
                    "until false; do echo 'git commit'; done", "else echo 'git commit'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_finding_2_unquoted_eval(self):
        for cmd in ("eval %s" % self.commit, "eval git commit -m x", "eval git -C x commit"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_finding_3_substitution_and_pipe_into_a_shell(self):
        c = self.commit
        for cmd in ("echo $(%s)" % c, 'echo "$(%s)"' % c, "cat <(%s)" % c, "echo '%s' | sh" % c, "echo `%s`" % c,
                    "echo '%s' | bash" % c, "printf '%%s' '%s' | zsh" % c):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_finding_4_wrapper_options_with_a_value(self):
        c = self.commit
        for cmd in ("env -u FOO %s" % c, "sudo -u root %s" % c, "xargs -n1 %s" % c, "command -p %s" % c, "nohup -- %s" % c,
                    "find . -exec %s ;" % c, "find . -execdir %s \\;" % c, "env -i -u A %s" % c, "time -p %s" % c):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_finding_5_pipefail_is_tracked_as_state(self):
        for runner in ("set -o pipefail; set +o pipefail; node --test | grep OK",
                       "set -o pipefail; set +eo pipefail; node --test | grep OK",
                       "false && set -o pipefail; node --test | grep OK",
                       "true | set -o pipefail; node --test | grep OK",
                       "set -o pipefail | cat; node --test | grep OK",
                       "set -o pipefail & node --test | grep OK",
                       "true || set -o pipefail; node --test | grep OK",
                       "true && set -o pipefail; node --test | grep OK",
                       "set -o pipefail; false || node --test | grep OK",
                       "(set -o pipefail; node --test) | grep OK",
                       "echo 'set -o pipefail'; node --test | grep OK"):
            with self.subTest(runner=runner):
                self.assertEqual(self.chained(runner)[0], "deny", runner)

    def test_r1326_finding_5_pipefail_kept_forms_still_count(self):
        for runner in ("set -o pipefail; node --test | grep OK", "set +o pipefail; set -o pipefail; node --test | grep OK",
                       "set -o pipefail; set -o pipefail; node --test | grep OK", "set -o pipefail && node --test | grep OK",
                       "set -euo pipefail; python3 -m unittest test_a | tail -3", "set -o pipefail\nnode --test | grep OK",
                       "node --test"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.chained(runner), runner)

    def test_r1326_finding_6_node_flags_that_do_not_run_tests(self):
        for runner in ("node --version --test", "node --help --test", "node --check --test x.js", "node -v --test",
                       "node --test --version", "node x.js --test", "node -e 'x' --test", "node --inspect"):
            with self.subTest(runner=runner):
                self.assertEqual(self.chained(runner)[0], "deny", runner)
        for runner in ("node --test", "node --no-warnings --test tests/", "node --test --test-reporter=spec"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.chained(runner), runner)

    # ---- round 2: the read exemption is an ALLOW list (heads grep, rg without --pre, echo, printf, cat; never awk or sed) ----
    def test_r1326_round2_A_pipe_and_and_background_into_a_shell(self):
        for cmd in ("echo 'git commit' |& sh", "echo 'git commit' |& bash", "echo 'git commit' & sh", "echo 'git commit' |& cat",
                    "echo '%s' |& zsh" % self.commit):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_round2_B_sinks_outside_the_allow_list(self):
        for sink in ("fish", "csh", "tcsh", "php", "lua", "ssh host", "parallel", "osascript", "tclsh", "awk '{system($0)}'",
                     "sed e", "sed -n e", "mystery", "perl", "ruby", "dash", "ksh", "tee /tmp/x", "xargs", "python3 -", "node"):
            cmd = "echo 'git commit' | %s" % sink
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_round2_C_executing_options_of_a_read_head(self):
        for cmd in ("rg --pre 'git commit' f", "rg --pre='git commit' f", "rg --pre-glob x --pre 'git commit' f",
                    "rg --hostname-bin 'git commit' f", "awk -v c='git commit' 'BEGIN{system(c)}'",
                    "awk 'BEGIN{system(ARGV[1])}' 'git commit'", "awk '{print}' 'git commit'", "sed -n p 'git commit'",
                    "printf -v c 'git commit'; $c", "sed -n '/git commit/p' f"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_round2_D_write_then_run(self):
        for cmd in ("printf 'git commit' > /tmp/x.sh; sh /tmp/x.sh", "echo 'git commit' | tee /tmp/x.sh; . /tmp/x.sh",
                    "echo 'git commit' >> f", "echo 'git commit' > f", "cat 'git commit' < f", "echo 'git commit' 2> f",
                    "echo 'git commit' | tee f", "grep 'git commit' f | tee g", "echo 'git commit' >| f"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_r1326_round2_E_pipefail_cancelled_through_a_wrapper(self):
        for cancel in ("command set +o pipefail", "builtin set +o pipefail", "eval 'set +o pipefail'", "eval set +o pipefail",
                       "FOO=1 set +o pipefail", "{ set +o pipefail; }", "source ./x.sh", ". ./x.sh", "command eval x",
                       "exec true", "(set +o pipefail)", "env set +o pipefail"):
            runner = "set -o pipefail; %s; node --test | grep OK" % cancel
            with self.subTest(runner=runner):
                self.assertEqual(self.chained(runner)[0], "deny", runner)
        self.assertIsNone(self.chained("FOO=1 set -o pipefail; node --test | grep OK"))

    def test_r1326_round2_F_node_flags_before_test(self):
        for runner in ("node --v8-options --test", "node --completion-bash --test", "node --build-snapshot --test",
                       "node --test --v8-options", "node --prof-process --test", "node --import x --test", "node --require x --test",
                       "node --inspect-brk --test", "node --interactive --test", "node --print --test", "node --eval --test",
                       "node --test --build-snapshot", "node --test --completion-bash"):
            with self.subTest(runner=runner):
                self.assertEqual(self.chained(runner)[0], "deny", runner)
        for runner in ("node --test", "node --no-warnings --test tests/", "node --experimental-test-coverage --test",
                       "node --test-reporter=spec --test", "node --enable-source-maps --test"):
            with self.subTest(runner=runner):
                self.assertIsNone(self.chained(runner), runner)

    def test_real_commits_at_a_segment_head_still_need_the_chain(self):
        for cmd in (self.commit, "ls; %s" % self.commit, "ls && %s" % self.commit, "true || %s" % self.commit,
                    "FOO=1 %s" % self.commit, "env FOO=1 %s" % self.commit, "(%s)" % self.commit, "{ %s; }" % self.commit,
                    "ls\n%s" % self.commit, "bash -c '%s'" % self.commit, "sh -c \"git commit -m x\"", "eval 'git commit -m x'",
                    "xargs git commit -m x", "command git commit -m x", "git commit -m x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd)[0], "deny", cmd)

    def test_coordinator_is_never_affected(self):
        self.assertIsNone(commitguard.decision("Bash", {"command": self.commit}, None))
