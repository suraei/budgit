"""A bundled git pager/exec option in a reader line is refused by the real report-folder decision
(verify_forgery_decision uses _GIT_EXEC_OPTS, finding 4 of R-623's review).

R-623: the verify-forgery guard refused read-only commands that only NAMED a verify folder or only carried the word in a
message. Two shapes were false positives and are let through now, each under one accepted shape (see studiolib):
  1. `cd DIR` in a line whose every other part is a plain reader (no redirect but /dev/*, no assignment, no substitution);
  2. the body of `git commit -m "$(cat <<'DELIM' ... DELIM )"` (quoted delimiter): a commit message is text.
Everything outside those shapes keeps the old verdict, and a real write into a verify folder is still refused."""
import os
import unittest
from unittest import mock

from helpers import StudioTestCase
import studiolib as sl


class VerifyWordTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        for d in (".budgit/verify", ".studio/verify", "sub", "other"):
            os.makedirs(os.path.join(self.project, d), exist_ok=True)
        os.symlink(".budgit/verify", os.path.join(self.project, "lnk"))

    def d(self, command, agent="general-purpose"):
        return sl.verify_forgery_decision("Bash", {"command": command}, agent, self.project)

    def test_a_bundled_O_and_glued_c_git_option_is_refused_in_a_reader_line(self):
        for cmd in ("git grep -nO'touch x' PATTERN .budgit/verify",
                    "git grep -ccore.pager=x PATTERN .budgit/verify"):
            with self.subTest(cmd=cmd):
                got = self.d(cmd)
                self.assertIsNotNone(got, cmd)
                self.assertEqual(got[0], "deny", cmd)

    def test_plain_git_readers_of_the_report_folder_pass(self):
        for cmd in ("git grep -n PATTERN .budgit/verify", "git log --oneline -- .budgit/verify"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.d(cmd), cmd)

    def assertDenied(self, command):
        got = self.d(command)
        self.assertIsNotNone(got, command)
        self.assertEqual(got[0], "deny", command)

    # --- shape 1: cd + readers -------------------------------------------------------------------------------------
    CD_READS = (
        "cd /x && ls .studio/verify", "cd /x; head -5 .studio/verify/R-1.md", "cd sub && ls ../.budgit/verify | head",
        "cd /x && git log --oneline -3 && ls .studio/verify", "cd /x && git diff --stat -- .studio/verify",
        "cd /x && cat .budgit/verify/R-1.md | grep PASS", "cd /x && ls .studio/verify 2>/dev/null | tail -3",
        "cd '/x y' && ls -la .studio/verify/", 'cd "/x" && wc -l .studio/verify/*.md', "cd ~ && ls .studio/verify",
        "cd /x && git commit -m 'verify the guard' .studio/ledger.json", "cd /x && sed -n 1,5p .studio/verify/R-1.md",
    )

    def test_a_cd_before_plain_readers_of_a_verify_folder_passes(self):
        for cmd in self.CD_READS:
            self.assertIsNone(self.d(cmd), cmd)

    CD_WRITES = (
        "cd .studio && echo x > verify/R-1.md", "cd .budgit && tee verify/R-1.md", "cd .budgit; echo x >> verify/R-1.md",
        "cd .budgit && cp /x/a verify/R-1.md", "cd .budgit && mv a verify/R-1.md", "cd .budgit && install a verify/R-1.md",
        "cd .budgit && rsync a verify/", "cd .budgit && ln -s a verify/R-1.md", "cd .budgit && sed -i s/a/b/ verify/R-1.md",
        "cd .budgit && python3 -c \"open('verify/R-1.md','w')\"", "cd .budgit && cat > verify/R-1.md <<'EOF'\nx\nEOF",
        "cd .budgit && echo x > $(echo verify)/R-1.md", "cd .budgit && ls verify > verify/R-1.md",
        "cd lnk && echo x > R-1.md", "cd .budgit && echo x >! verify/R-1.md", "cd .budgit && =tee verify/R-1.md",
        "cd .budgit && ${=W} verify/R-1.md", "cd .budgit && ls verify; touch verify/R-1.md",
        "cd .budgit && ls verify && FOO=1 tee verify/R-1.md", "cd .budgit && ls verify | tee verify/R-1.md",
        "cd .budgit && ls verify | xargs tee verify/R-1.md", "cd .budgit && find verify -fprint verify/R-1.md",
        "cd $X && ls .studio/verify", "cd /x && ls $(echo .studio/verify)", "cd /x && ls .studio/verify > o.md",
        "cd /x && ls .studio/verify; cd verify && echo x > R-1.md",
    )

    def test_a_cd_before_any_writer_or_unreadable_part_stays_denied(self):
        for cmd in self.CD_WRITES:
            self.assertDenied("ls .budgit/verify; " + cmd if "verify" not in cmd else cmd)

    def test_the_cd_is_judged_only_with_a_cd_in_the_line(self):
        # the old verdict for a line with no cd: a write target in the folder is denied, a read passes
        self.assertDenied("echo x > .studio/verify/R-1.md")
        self.assertIsNone(self.d("ls .studio/verify"))

    # --- shape 2: a commit message heredoc -------------------------------------------------------------------------
    MSG = "git commit -m \"$(cat <<'EOF'\nR-623: verify word\n\nThe guard refused ls .studio/verify and head .budgit/verify/R-1.md\nEOF\n)\""

    def test_a_commit_message_that_names_a_verify_folder_passes(self):
        for cmd in (self.MSG, "git add -A && " + self.MSG, "git -C /x commit -a -m \"$(cat <<'EOF'\nfix .budgit/verify/ read\nEOF\n)\"",
                    "git commit -m \"$(cat <<'MSG'\nuse echo x > .studio/verify/R-1.md\nMSG\n)\""):
            self.assertIsNone(self.d(cmd), cmd)

    def test_a_heredoc_outside_the_commit_message_shape_keeps_the_old_verdict(self):
        for cmd in ("git commit -m \"$(cat <<'EOF'\nmsg\nEOF\necho x > .studio/verify/R-1.md\nEOF\n)\"",
                    "git commit -m \"$(cat <<EOF\n$(echo x > .studio/verify/R-1.md)\nEOF\n)\"",
                    "git commit -m \"$(cat <<'EOF'\nmsg .studio/verify\nEOF\n)\" && echo x > .studio/verify/R-1.md",
                    "git commit -m \"$(cat <<'EOF'\nmsg\nEOF\n)\"; tee .studio/verify/R-1.md <<'EOF'\nPASS\nEOF",
                    "cat > .budgit/verify/R-1.md <<'EOF'\nPASS\nEOF", "tee .studio/verify/R-1.md <<'EOF'\nPASS\nEOF",
                    "bash <<'EOF'\necho x > .studio/verify/R-1.md\nEOF",
                    "python3 - <<'EOF'\nopen('.studio/verify/R-1.md','w').write('PASS')\nEOF",
                    "echo 'git commit -m \"$(cat <<'EOF'\nEOF\nls .studio/verify > .studio/verify/R-1.md\nEOF\n)\"",
                    "cat <<'EOF' > .studio/verify/R-1.md\nPASS\nEOF"):
            self.assertDenied(cmd)

    def test_the_commit_body_is_dropped_only_in_its_shape(self):
        self.assertEqual(sl._drop_commit_message_body(self.MSG), "git commit -m \"$(cat <<'EOF'\nEOF\n)\"")
        other = "cat <<'EOF'\nls .studio/verify\nEOF\n"
        self.assertEqual(sl._drop_commit_message_body(other), other)
        unq = "git commit -m \"$(cat <<EOF\nls .studio/verify\nEOF\n)\""
        self.assertEqual(sl._drop_commit_message_body(unq), unq)

    # --- the plain word is not a write ------------------------------------------------------------------------------
    WORD_ONLY = ("git commit -m 'verify the guard'", "python3 -m pytest tests/test_verify_x.py", "grep -n verify plugin/scripts/studiolib.py",
                 "budgit ledger verify R-1", "git add .studio/ledger.json && git commit -m \"verify: the guard\"",
                 "ls tests/test_verify_x.py", "cat .studio/verify/R-1.md", "head -20 .budgit/verify/R-1.md", "ls .studio/verify",
                 "ls .studio/verify | head", "echo 'verify' >> .studio/progress.md")

    def test_the_word_alone_passes(self):
        for cmd in self.WORD_ONLY:
            self.assertIsNone(self.d(cmd), cmd)

    def test_ls_t_of_the_budgit_verify_folder_passes_and_a_write_there_does_not(self):
        # replay from a coordinator session at the project root (8 Nov 2026): a read-only `ls -t .budgit/verify`
        for cmd in ("ls -t .budgit/verify", "ls -t .budgit/verify | head -3", "ls -lt .budgit/verify/", "ls -t .studio/verify"):
            self.assertIsNone(self.d(cmd), cmd)
        for cmd in ("ls -t .budgit/verify > .budgit/verify/R-1.md", "echo PASS > .budgit/verify/R-1.md"):
            self.assertDenied(cmd)

    # --- R-623 replay cases (read-only commands that only MENTION a Budgit path) ---------------------------------------------
    def hw(self, command):
        return sl.home_write_decision("Bash", {"command": command}, self.project, "general-purpose")

    def test_replay_1_ls_t_of_verify_folder_piped_to_head(self):
        self.assertIsNone(self.d("ls -t /Users/x/proj/.budgit/verify/ | head"))
        self.assertIsNone(self.hw("ls -t /Users/x/proj/.budgit/verify/ | head"))

    def test_replay_2_ledger_done_evidence_only_mentions_the_path(self):
        # the guard trusts only THIS plugin's bin/budgit (R-515), so the test puts it first on PATH like a real session
        own_dir = os.path.dirname(sl._own_budgit())
        cmd = 'budgit ledger done R-1 --evidence "verify report .budgit/verify/R-1.md"'
        with mock.patch.dict(os.environ, {"PATH": own_dir + os.pathsep + os.environ.get("PATH", "")}):
            self.assertIsNone(self.d(cmd))
            self.assertIsNone(self.hw(cmd))
        # ...but the same word as a path argument of the CLI is still a write target
        with mock.patch.dict(os.environ, {"PATH": own_dir + os.pathsep + os.environ.get("PATH", "")}):
            self.assertDenied('budgit ledger done R-1 --out .budgit/verify/R-1.md')

    def test_replay_3_append_to_progress_is_not_a_verify_refusal(self):
        cmd = "printf 'line\\n' >> /Users/x/proj/.studio/progress.md"
        self.assertIsNone(self.d(cmd))

    def test_replay_4_sed_n_and_awk_on_a_transcript_are_reads(self):
        for cmd in ("sed -n '10,20p' /Users/x/.claude/projects/p/t.jsonl | grep foo",
                    "awk 'NR>=10' /Users/x/.claude/projects/p/t.jsonl"):
            self.assertIsNone(self.d(cmd), cmd)
            self.assertIsNone(self.hw(cmd), cmd)
        # (a write there is not this guard's business: only reads are pinned)
        self.assertIsNone(self.d("sed -n 1p /Users/x/.claude/projects/p/t.jsonl"))

    def test_replay_5_git_diff_name_only_piped_to_grep_is_a_read(self):
        cmd = 'git diff --name-only A B | grep -E "^\\.studio/(ledger|progress)|^docs/INDEX.md"'
        # a backslash anywhere makes git reads ask (security HOLD 2, desync via >| and fullwidth ;): a deliberate noise cost
        self.assertDenied(cmd)
        self.assertIsNone(self.hw(cmd))

    def test_replay_writes_to_the_verify_folder_stay_refused(self):
        for cmd in ("echo x > .budgit/verify/R-1.md", "tee .budgit/verify/R-1.md", "cp a .budgit/verify/R-1.md",
                    "sed -i s/a/b/ .budgit/verify/R-1.md", "sed -i.bak s/a/b/ .budgit/verify/R-1.md"):
            self.assertDenied(cmd)

    def test_a_bundled_git_pager_option_is_refused_in_a_reader_line(self):  # finding 4
        for cmd in ("git grep -nO'cp /tmp/f.md .budgit/verify/R-1.md ;' PATTERN", "git grep -O'cp a .budgit/verify/R-1.md;' X",
                    "git grep -inOcmd X -- .budgit/verify", "git grep --open-files-in-pager=cmd X .budgit/verify",
                    "git -ccore.pager=x grep X .budgit/verify"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)
        self.assertIsNone(self.d("git grep -n X"))
        self.assertIsNone(self.d("git grep -n X -- .budgit/verify"))

    def test_abbreviated_or_unknown_git_options_are_refused_in_a_reader_line(self):
        V = ".budgit/verify"
        for cmd in ("git grep --open=cmd X " + V, "git grep --open-file=cmd X " + V, "git log --outp=f " + V,
                    "git diff --ex " + V, "git grep -nO cmd X " + V, "git log --frobnicate " + V):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)
        for cmd in ("git grep -n X -- " + V, "git log --oneline -5 " + V, "git diff --stat " + V,
                    "git -C /some/dir status --short " + V, "git show HEAD --name-only " + V):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.d(cmd))

    def test_a_double_dash_value_or_a_leading_assignment_cannot_smuggle_a_git_option(self):
        V = ".budgit/verify"
        for cmd in ("git grep -e -- --open=cmd X " + V, "git grep -f -- --open=cmd " + V, "git log -S -- --output=f " + V,
                    "EDITOR=cmd git commit -e " + V, "VISUAL=cmd git add -e " + V):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)
        for cmd in ("git grep -n X -- " + V, "git log --oneline -- " + V):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.d(cmd))

    def test_shell_expansions_in_a_git_reader_line_are_refused(self):
        V = ".budgit/verify"
        for cmd in ("git grep X $O " + V, "git grep X `echo -e` " + V, "git grep X {--open=cmd,a} " + V, "git grep X * " + V):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)
        self.assertIsNone(self.d('git grep -n "a b" -- ' + V))
        self.assertDenied('git grep -n "a*b" -- ' + V)     # the plain character set has no `*`, even quoted: asks

    def test_a_git_read_line_with_any_character_outside_the_plain_set_is_refused(self):
        V = ".budgit/verify"
        for cmd in ("git diff \\-\\-output=F " + V, "git grep \\-Osh x " + V, "git log <(touch X) " + V,
                    "git log =(touch X) " + V, "git grep [-]Osh x " + V, "git log $'\\x2d\\x2doutput=F' " + V,
                    "git log --oneline\n-Osh " + V, "git log {a,b} " + V, "git log ~ " + V, "git log ! " + V):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_a_backslash_or_non_ascii_character_anywhere_makes_git_reads_ask(self):
        V = ".budgit/verify"
        for cmd in ("echo >| /dev/null ; git diff \\-\\-output=" + V + "/R-1.md ； true",
                    "git log --oneline " + V + " ； true", "git log --oneline " + V + " ; echo \\x",
                    "cd /x && git log --oneline -3 && ls " + V + " ； true"):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_a_caret_only_passes_directly_after_a_name(self):
        V = ".budgit/verify"
        for cmd in ("git log ^HEAD~1 " + V, "git grep ^x " + V, "git log HEAD^ ^x " + V):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)
        self.assertIsNone(self.d("git show HEAD^ " + V))

    def test_plain_git_reads_with_the_plain_character_set_still_pass(self):
        V = ".budgit/verify"
        for cmd in ("git log -n 3 " + V, "git status " + V, "git -C /tmp/x diff --stat " + V, "git show HEAD^ " + V,
                    "git log --format=%H -n 1 " + V, 'git grep -n "foo bar" -- docs ' + V):
            with self.subTest(cmd=cmd):
                self.assertIsNone(self.d(cmd), cmd)

    def test_push_fetch_and_editor_flags_are_not_plain_reads(self):
        V = ".budgit/verify"
        for cmd in ("git push origin x " + V, "git fetch " + V, "git commit -e " + V, "git add -i " + V, "git grep -p x " + V):
            with self.subTest(cmd=cmd):
                self.assertDenied(cmd)

    def test_the_verifier_itself_is_never_asked(self):
        self.assertIsNone(self.d("cd .budgit && echo x > verify/R-1.md", agent="budgit:verifier"))

    def test_real_writes_stay_denied(self):
        for cmd in ("echo x > .studio/verify/R-1.md", "echo x >> .budgit/verify/R-1.md", "echo x | tee .studio/verify/R-1.md",
                    "cp a .budgit/verify/R-1.md", "mv a .studio/verify/R-1.md", "install a .budgit/verify/R-1.md",
                    "rsync a .budgit/verify/", "ln -s a .budgit/verify/R-1.md", "sed -i s/FAIL/PASS/ .budgit/verify/R-1.md",
                    "python3 -c \"open('.budgit/verify/R-1.md','w')\"", "echo x > $(echo .budgit)/verify/R-1.md",
                    "ls .budgit/verify; echo x > lnk/R-1.md", "=tee .budgit/verify/R-1.md", "${=W} .budgit/verify/R-1.md",
                    "cat > .budgit/verify/R-1.md <<'EOF'\nx\nEOF"):
            self.assertDenied(cmd)


if __name__ == "__main__":
    unittest.main()
