"""Two guards.
Part 1 (mergeguard): an ALLOW LIST for the plugin's own checkout. A git command that reaches, or may reach, it passes only in an allowed
shape (read verbs, fetch, add, commit, worktree add / prune, `merge --ff-only <ref>`); everything else is refused. Commands that reach only
other folders are not judged. A folder that cannot be read counts as "maybe the live checkout".
Part 2 (studiolib.verify_forgery_decision): a non-verifier WRITE into the verify reports folder is refused; a read or a mention passes."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from helpers import ROOT, StudioTestCase
from test_guard_trust import GuardCase
import mergeguard
import studiolib as sl

REAL_CHECKOUT_ROOT = mergeguard.checkout_root      # the unmocked function, kept before any test patches it
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")       # this plugin's own CLI: a bare `budgit` off PATH is not recognised as it

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def git(repo, *args):
    done = subprocess.run(["git", "-C", repo] + GIT_ID + list(args), capture_output=True, text=True)
    assert done.returncode == 0, (args, done.stdout, done.stderr)
    return done.stdout


def make_repo():
    """A small repo with a main branch and an `other` branch; the guard never runs git, so this only has to be a checkout."""
    repo = os.path.realpath(tempfile.mkdtemp(prefix="r1199-"))
    git(repo, "init", "-q", "-b", "main")
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as f:
        f.write("base\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "branch", "other")
    return repo


class MergeGuardTest(unittest.TestCase):
    def setUp(self):
        self.plugin = make_repo()
        self.elsewhere = make_repo()
        self.wt = os.path.join(os.path.realpath(tempfile.mkdtemp(prefix="r1199wt-")), "wt")
        git(self.plugin, "worktree", "add", "-q", "-b", "side", self.wt, "main")      # a worktree of the plugin repo: its own toplevel
        for folder in (self.plugin, self.elsewhere, os.path.dirname(self.wt)):          # R-1199: leave no r1199-* folder behind
            self.addCleanup(shutil.rmtree, folder, True)
        patcher = mock.patch.object(mergeguard, "checkout_root", return_value=self.plugin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def d(self, command, cwd=None):
        return mergeguard.decision("Bash", {"command": command}, cwd or self.plugin)

    def assertRefused(self, command, cwd=None):
        got = self.d(command, cwd)
        self.assertIsNotNone(got, (command, cwd))
        self.assertEqual(got[0], "deny", command)
        return got[1]

    def assertPasses(self, command, cwd=None):
        self.assertIsNone(self.d(command, cwd), (command, cwd))

    def contexts(self, rest):
        """The same git words reaching the live checkout three ways: cwd inside it; cwd outside with -C into it; cwd outside after an
        unreadable `cd "$D"` (the folder may be the live checkout)."""
        return [("git " + rest, self.plugin), ("git -C %s %s" % (self.plugin, rest), self.elsewhere),
                ('cd "$D" && git ' + rest, self.elsewhere), ('cd "$D"; git ' + rest, self.plugin)]

    def assertRefusedEverywhere(self, rest):
        for cmd, cwd in self.contexts(rest):
            self.assertRefused(cmd, cwd)

    def assertPassesEverywhere(self, rest):
        for cmd, cwd in self.contexts(rest):
            self.assertPasses(cmd, cwd)

    # --- the allowed shapes pass ---
    def test_read_verbs_pass_in_the_live_checkout(self):
        for rest in ("status", "status -sb", "log --oneline", "show HEAD", "diff", "diff --stat HEAD", "rev-parse HEAD", "rev-list HEAD",
                     "ls-files", "ls-tree HEAD", "cat-file -p HEAD", "merge-base main other",
                     "describe --always", "blame a.txt", "grep base", "shortlog -s", "for-each-ref", "show-ref",
                     "config --get user.name", "config --list", "branch", "branch --list", "branch -v", "branch --show-current",
                     "branch --contains HEAD", "branch --merged main", "branch --list 'x*'", "worktree list", "stash list",
                     "remote -v", "--version", "--help", "-h", "--no-pager log"):
            self.assertPassesEverywhere(rest)

    def test_fetch_add_commit_and_worktree_add_and_prune_pass(self):
        for rest in ("fetch", "fetch origin", "add a.txt", "add -A", "commit -m x", "commit -m x -- a.txt", "worktree add /tmp/x -b y main",
                     "worktree prune"):
            self.assertPassesEverywhere(rest)

    # --- round A2: flag allow lists, GIT_ env prefixes, exact git head, word rule ---
    def line_contexts(self, line):
        """A whole line (git is not its first word) that may reach the live checkout: cwd inside it; cwd outside with the live path in
        the line; cwd outside after an unreadable cd."""
        return [(line, self.plugin), ("cd %s && %s" % (self.plugin, line), self.elsewhere), ('cd "$D" && ' + line, self.elsewhere)]

    def assertLineRefused(self, line):
        for cmd, cwd in self.line_contexts(line):
            self.assertRefused(cmd, cwd)

    def test_fetch_with_upload_pack_or_any_unlisted_flag_is_refused(self):
        for rest in ("fetch --upload-pack='git merge x' /tmp/r", "fetch --upload-pack=x /tmp/r", "fetch --upload-p=x /tmp/r", "fetch --upload-p x",
                     "fetch -u", "fetch --depth=1", "fetch --recurse-submodules", "fetch ext::sh", "fetch --all --force", "fetch --"):
            self.assertRefusedEverywhere(rest)
        for rest in ("fetch -q --prune origin", "fetch --all --tags", "fetch origin main:other"):
            self.assertPassesEverywhere(rest)

    def test_commit_with_edit_file_amend_or_no_verify_is_refused(self):
        for rest in ("commit -e", "commit -F msg.txt", "commit --file=msg.txt", "commit --amend -m x", "commit --fixup HEAD", "commit --squash HEAD",
                     "commit -C HEAD", "commit -c HEAD", "commit --reuse-message=HEAD", "commit --no-verify -m x", "commit -n -m x",
                     "commit -a -m x", "commit -qm x", "commit --edit -m x", "commit --am -m x", "commit -m"):
            self.assertRefusedEverywhere(rest)

    def test_commit_with_a_message_and_quiet_passes(self):
        for rest in ("commit -m msg", "commit -m msg -q", "commit --quiet --message msg", "commit -mmsg", "commit --message=msg",
                     "commit -m msg --author='A <a@b.c>'", "commit -m msg -- a.txt b.txt"):
            self.assertPassesEverywhere(rest)

    def test_add_with_an_unlisted_flag_is_refused(self):
        for rest in ("add -f a.txt", "add --force a.txt", "add -p", "add -e", "add --chmod=+x a.txt", "add --inte", "add --pathspec-from-file=f"):
            self.assertRefusedEverywhere(rest)
        for rest in ("add -A", "add --all", "add -u -v", "add -- a.txt", "add a.txt b.txt"):
            self.assertPassesEverywhere(rest)

    def test_stash_list_with_any_other_token_is_refused(self):
        for rest in ("stash list --output=x", "stash list -p", "stash list stash@{0}", "stash list --out=x"):
            self.assertRefusedEverywhere(rest)
        self.assertPassesEverywhere("stash list")

    def test_worktree_add_with_force_or_big_b_or_a_path_inside_the_root_is_refused(self):
        for rest in ("worktree add -f -f -B main /tmp/x old", "worktree add -B main /tmp/x", "worktree add -f /tmp/x", "worktree add --force /tmp/x",
                     "worktree add %s/sub main" % self.plugin, "worktree add %s main" % self.plugin, "worktree add ./sub main",
                     "worktree add $D main", "worktree add /tmp/x a b", "worktree add", "worktree add --lock /tmp/x",
                     "worktree list -v", "worktree prune --expire=now", "worktree move a b", "worktree remove x"):
            self.assertRefusedEverywhere(rest)

    def test_worktree_add_detach_and_small_b_outside_the_root_passes(self):
        outside = os.path.join(self.elsewhere, "newwt")
        for rest in ("worktree add --detach %s main" % outside, "worktree add -b feature %s main" % outside, "worktree add %s" % outside,
                     "worktree list --porcelain", "worktree prune -n", "worktree prune --dry-run -v"):
            self.assertPassesEverywhere(rest)

    def test_a_git_environment_prefix_other_than_dir_and_work_tree_is_refused(self):
        for name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_EXTERNAL_DIFF", "GIT_EDITOR", "GIT_SSH_COMMAND", "GIT_PAGER",
                     "GIT_CONFIG_KEY_0", "GIT_SEQUENCE_EDITOR"):
            self.assertLineRefused("%s=x git status" % name)
            self.assertLineRefused("export %s=x; git status" % name)
        self.assertPasses("GIT_DIR=%s/.git git status" % self.plugin, cwd=self.elsewhere)

    def test_a_git_dash_binary_head_is_refused(self):
        for line in ("git-merge other", "/usr/lib/git-core/git-merge other", "git-pull", "GIT-MERGE other", "git-upload-pack x"):
            self.assertLineRefused(line)
        self.assertPasses("/usr/bin/git status", cwd=self.plugin)

    def test_a_brace_or_backslash_newline_is_refused(self):
        self.assertLineRefused("g\\\nit merge other")
        self.assertLineRefused("{g,}it merge other")
        self.assertLineRefused("git status\\\n --short")

    def test_a_brace_group_does_not_hide_a_verb_from_the_allow_list(self):
        for line in ("{ git reset --hard; }", "{ git push; }", "{ git status; git push; }"):
            self.assertRefused(line, cwd=self.plugin)
        self.assertPasses("{ git status; }", cwd=self.plugin)

    def test_the_word_rule_is_judged_against_every_reach_member_with_cwd_kept(self):
        self.assertRefused('git -C %s log "$(git merge other)"' % self.wt, cwd=self.plugin)
        self.assertPasses('git commit -m "fix git"', cwd=self.wt)
        self.assertPasses('git -C %s commit -m "fix git"' % self.wt, cwd=self.wt)
        self.assertRefused('git -C %s commit -m "fix git"' % self.plugin, cwd=self.wt)

    def test_substitutions_and_wrappers_with_options_count_as_reaching_the_live_checkout(self):
        live, wt = self.plugin, self.wt
        for line in ('echo "$(git -C %s merge other)"' % live, "echo `git -C %s merge other`" % live,
                     'git -C %s log "$(git -C %s merge x)"' % (wt, live),
                     "env -S 'git -C %s merge x'" % live, "env -P /usr/bin git -C %s merge x" % live,
                     "arch -arch arm64 git -C %s merge x" % live, "stdbuf -o L git -C %s merge x" % live):
            self.assertRefused(line, cwd=wt)
        gate = 'nice -n 10 python3 %s/tools/runtests.py test_a test_b && git -C %s commit -am "R-1 msg"' % (wt, wt)
        self.assertPasses(gate, cwd=wt)
        self.assertPasses("nice -n 10 python3 %s/tools/runtests.py test_a" % wt, cwd=wt)

    def test_the_leak_scanner_git_subcommand_with_read_only_options_passes_and_other_git_words_stay_refused(self):
        live = self.plugin
        out = os.path.join(os.path.realpath(tempfile.gettempdir()), "r1199-report.json")
        for line in ("gitleaks git --redact --no-banner --source %s --report-path %s" % (live, out),
                     "gitleaks git --redact --log-opts=--all --no-banner --source=%s --report-path=%s" % (live, out),
                     "gitleaks git --redact --log-opts '--all --since=2024-01-01' --source %s" % live):
            self.assertPasses(line, cwd=live)
        for line in ("git -C %s status && gitleaks git --redact --no-banner --source %s" % (live, live),
                     "gitleaks git --no-banner --source %s" % live,
                     "gitleaks git --redact --no-banner; echo x", "gitleaks git --redact --no-banner && echo x",
                     "gitleaks git --redact --no-banner || echo x", "gitleaks git --redact --no-banner | head -1",
                     "gitleaks git --no-banner --report-path %s/r.json" % live, "gitleaks git --report-path r.json",
                     "gitleaks git --config x", "gitleaks git --log-opts '--output=x'", "gitleaks git merge x", "gitleaks git --no-banner; git merge x",
                     "gitleaks git --source $X", "env gitleaks git --no-banner", "echo gitleaks git --no-banner", "xargs git merge x",
                     "gitleaks detect --source %s && env git merge x" % live):
            self.assertRefused(line, cwd=live)

    def test_r1217_the_scanner_form_refuses_any_redirect_and_any_subshell_and_stands_alone(self):
        live = self.plugin
        out = os.path.join(os.path.realpath(tempfile.gettempdir()), "r1217-report.json")
        self.assertPasses("gitleaks git --redact --report-path %s" % out, cwd=live)
        self.assertPasses("gitleaks git --redact --no-banner", cwd=live)
        for line in ("gitleaks git --redact --report-path %s > %s/out.txt" % (out, live),
                     "gitleaks git --redact --report-path %s 2> %s/err.txt" % (out, live),
                     "(gitleaks git --redact --no-banner)", "gitleaks git --redact --no-banner >> %s/o.txt" % live,
                     "gitleaks git --redact --no-banner &> %s/o.txt" % live, "gitleaks git --redact --no-banner < /etc/passwd",
                     "(gitleaks git --redact --no-banner) > %s/o.txt" % live):
            self.assertRefused(line, cwd=live)

    def test_f1_an_output_process_substitution_counts_as_reaching_the_live_checkout(self):
        live, wt = self.plugin, self.wt
        self.assertRefused("echo >(git merge x)", cwd=live)
        self.assertRefused("tee >(git -C %s merge x)" % live, cwd=wt)
        self.assertRefused("echo foo > >(git -C %s merge x)" % live, cwd=wt)
        self.assertRefused("echo foo | tee >(git -C %s merge x) > /dev/null" % live, cwd=wt)

    def test_a_brace_wholly_inside_quotes_passes_and_every_unquoted_or_spanning_brace_is_refused(self):
        live = self.plugin
        for line in ("grep -E 'a{0,12}' a.txt", 'grep -E "a{0,12}" a.txt', "ps ax | grep -E 'x{2}'"):
            self.assertPasses(line, cwd=live)
        for line in ("git grep -E 'a{0,12}'", "git commit -m 'fix {a,b} and {1..3}'",      # round 6: accepted friction, git heads are judged raw
                     "bash -c '{env,git} merge x'", "eval '{env,git} merge x'", 'sh -c "{g,}it merge x"', "zsh -c '{g..g}it merge x'",
                     'eval "{env,git} merge x"', "bash -c '{g,}it merge x'", "printf %s '{a,b}' | xargs -I{} git merge {}",
                     "{g,}it merge x", "{g..g}it merge x", 'exec -a {"}",git} merge x', "git commit -m ${a,,}", "git -C %s {m,}erge x" % live,
                     "echo '{a,b}' {g,}it merge x", 'echo "{a,b}" {g..g}it merge x', 'echo "$(echo {g,}it) {a}"',
                     "echo '{a,b}'\\\ngit merge x"):
            self.assertRefused(line, cwd=live)

    def test_awk_and_sed_lose_the_quote_masking_so_a_quoted_brace_in_their_program_is_refused(self):
        live = self.plugin
        for line in ("awk 'BEGIN { printf \"%d\\n\", 1 }' a.txt", "ps -axo pid,rss,comm | awk '{printf \"%s %s\\n\", $1, $2}'",
                     "sed 's/a{1,2}/b/' a.txt"):
            self.assertRefused(line, cwd=live)

    def test_quote_masking_is_dropped_for_ansi_c_quoting_a_comment_or_a_newline(self):
        live = self.plugin
        nl = "\n"
        for line in ("$'\\'' 2>/dev/null; {env,git} merge x # '",
                     "echo a # it's" + nl + "{env,git} merge x" + nl + "# '",
                     "echo a # it's" + nl + "{g..g}it merge x" + nl + "# '"):
            self.assertRefused(line, cwd=live)
        for line in ("grep -E 'a{0,12}' a.txt", 'grep -E "a{0,12}" a.txt'):
            self.assertPasses(line, cwd=live)

    def test_a_substitution_on_the_line_drops_the_quote_masking(self):
        live = self.plugin
        self.assertRefused("echo \"$(echo a)\" '{a}' {env,git} merge x", cwd=live)
        self.assertRefused("echo `echo a` '{a}' {env,git} merge x", cwd=live)
        self.assertPasses("grep -E 'a{0,12}' a.txt", cwd=live)

    def test_scanner_report_path_must_sit_under_a_system_temp_folder_outside_the_live_root(self):
        live = self.plugin
        home_path = os.path.join(os.path.expanduser("~"), "r1199-report.json")
        swapped = live.swapcase()
        base = "gitleaks git --redact --no-banner --source %s --report-path " % live
        for path in (home_path, swapped + "/r.json", "r.json", "./r.json", live + "/r.json"):
            self.assertRefused(base + path, cwd=live)
        tmp_ok = os.path.join(os.path.realpath(tempfile.gettempdir()), "r1199-ok.json")
        self.assertPasses(base + tmp_ok, cwd=live)
        self.assertPasses(base + "/tmp/r1199-ok.json", cwd=live)

    def test_process_substitutions_in_all_three_spellings_count_as_reaching_the_live_checkout(self):
        live, wt = self.plugin, self.wt
        for line in ("cat <(git merge x)", "echo =(git merge x)", "diff <(git log) =(git merge x)"):
            self.assertRefused(line, cwd=live)
        for line in ("cat <(git -C %s merge x)" % live, "echo =(git -C %s merge x)" % live):
            self.assertRefused(line, cwd=wt)

    def test_a_quote_or_backslash_inside_braces_is_refused(self):
        for line in ('exec -a {"}",git} merge other', 'env -u {"}",git} merge other', 'echo ${x:-"a"}'):
            self.assertRefused(line, cwd=self.plugin)
        for line in ('echo "${X}"', "echo ${x:--}"):
            self.assertPasses(line, cwd=self.plugin)

    def test_plain_parameter_braces_pass_and_comma_or_range_braces_are_refused(self):
        # R-1300 security MED-B: the guard cannot prove quoting after lexing; a `$` message goes through -F
        self.assertRefused('git commit -m "Cla${X}ude"', cwd=self.plugin)
        self.assertRefused('git commit -m "a${x:--}b"', cwd=self.plugin)
        for line in ("{g..g}it merge other", "{g,{x}}it merge other", "${a,,}it merge other", "git commit -m ${a,,}"):
            self.assertRefused(line, cwd=self.plugin)

    def test_the_word_git_behind_a_wrapper_is_refused(self):
        for line in ("env -S 'git merge other'", "env -P /usr/bin git merge other", "env FOO=1 git merge other", "arch -arch arm64 git merge other",
                     "nice git merge other", "nice -n 5 git merge other", "xargs git merge", "echo other | xargs git merge", "stdbuf -o L git merge other",
                     "xcrun git merge other", "setsid git merge other", "sh -c 'git merge other'", "bash -lc \"git merge other\"",
                     "eval \"git merge other\"", "command git merge other", "time git merge other", "sudo git merge other",
                     "timeout 5 git merge other", "env /usr/bin/git merge other", "nohup git merge other", "env git-merge other"):
            self.assertLineRefused(line)

    def test_a_failed_import_refuses_any_line_with_a_dollar_backtick_or_backslash(self):
        import hook
        for command, refused in (("echo $HOME", True), ("echo `date`", True), ("echo a\\ b", True), ("git status", True), ("ls -la", False),
                                 ("echo hello", False)):
            hook._OUT.clear()
            with mock.patch.dict("sys.modules", {"mergeguard": None}):
                hook.on_pre_tool_use({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.elsewhere}, None)
            denied = (hook._OUT.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
            self.assertEqual(denied, refused, command)
        hook._OUT.clear()

    def test_our_normal_lines_still_pass(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        outside = os.path.join(self.elsewhere, "detached")
        for line in ('env PYTHONPATH=%s/tests:%s/plugin/scripts python3 -W ignore -m unittest test_x && git -C %s commit -m "msg"'
                     % (self.wt, self.wt, self.wt),
                     "nice -n 10 python3 %s/tools/runtests.py" % self.plugin, "lockf -t 0 %s/x.lock python3 %s/s.py" % (self.elsewhere, self.elsewhere),
                     "budgit status", "git -C %s merge --ff-only %s" % (self.plugin, sha),
                     "git -C %s worktree add --detach %s %s" % (self.plugin, outside, sha)):
            self.assertPasses(line, cwd=self.plugin)
            self.assertPasses(line, cwd=self.wt)

    def test_a_merge_with_ff_only_and_one_ref_passes(self):
        for rest in ("merge --ff-only other", "merge -q --ff-only other", "merge --quiet --ff-only other", "merge --ff-only -q other"):
            self.assertPassesEverywhere(rest)

    def test_merge_quit_alone_passes_in_the_live_checkout(self):
        self.assertPassesEverywhere("merge --quit")
        self.assertIn("git merge --quit", self.assertRefused("git merge other"))

    def test_a_failed_import_refuses_an_unreadable_command_word(self):
        import hook
        for command, refused in (('g""it merge other', True), ("$G merge other", True), ("g\\it merge other", True), ("git merge other", True),
                                 ("ls -la", False), ("echo hello", False)):
            hook._OUT.clear()
            with mock.patch.dict("sys.modules", {"mergeguard": None}):
                hook.on_pre_tool_use({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.elsewhere}, None)
            denied = (hook._OUT.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
            self.assertEqual(denied, refused, command)
        hook._OUT.clear()

    def test_an_upper_case_verb_and_a_double_dash_are_refused_in_the_live_checkout(self):
        for rest in ("MERGE other", "merge --ff-only -- other", "Merge --ff-only other"):
            self.assertRefusedEverywhere(rest)

    def test_merge_abort_and_continue_stay_refused_in_the_live_checkout(self):
        for rest in ("merge --abort", "merge --continue", "merge --quit --abort", "merge --quit x", "merge --abort --quit",
                     "merge -q --quit", "rebase --quit", "cherry-pick --quit"):
            self.assertRefusedEverywhere(rest)

    # --- everything else is refused, from inside, from outside with -C, and through an unreadable cd ---
    def test_every_other_shape_is_refused_in_the_live_checkout(self):
        for rest in ("merge other", "merge --no-ff other", "merge -m x other", "merge --ff-only", "merge --ff-only other main",
                     "merge --ff-only --no-ff other", "merge --ff-only -s ours other", "merge --abort",
                     "merge --continue", "pull", "pull --ff-only", "pull -q --ff-only origin main", "rebase other", "rebase --quit",
                     "cherry-pick other", "am x.patch", "revert HEAD", "stash pop", "stash apply", "stash", "checkout main",
                     "checkout -m main", "switch main", "reset --hard", "restore a.txt", "apply --3way x.patch", "branch newone",
                     "branch -D other", "worktree remove x", "remote add x y", "config user.name x", "diff --output=x",
                     "grep -Ocat base", "push", "clean -fd", "tag v1"):
            self.assertRefusedEverywhere(rest)

    def test_the_refusal_names_the_allowed_shapes(self):
        reason = self.assertRefused("git merge other")
        self.assertIn("git worktree add", reason)
        self.assertIn("git merge --ff-only <branch>", reason)

    # --- earlier bypasses, kept as refused cases ---
    def test_a_verb_spelled_with_quotes_or_a_variable_is_refused(self):
        for rest in ('mer""ge other', "'merge' other", "$V other", "`echo merge` other", '"$V" other'):
            self.assertRefusedEverywhere(rest)
        for cmd, cwd in (('g""it merge other', self.plugin), ("V=merge; git $V other", self.plugin), ("G=git; $G merge other", self.plugin),
                         ("$G -C %s merge other" % self.plugin, self.elsewhere), ("$G -C %s $V other" % self.plugin, self.elsewhere),
                         ('cd "$D" && $G merge other', self.elsewhere), ("cd %s && $G merge other" % self.plugin, self.elsewhere)):
            self.assertRefused(cmd, cwd)

    def test_any_dash_c_key_and_an_unknown_global_option_are_refused(self):
        for rest in ("-c alias.m=merge m other", "-c ALIAS.m=merge m other", "-calias.m=merge m other", "--config-env=alias.m=X m",
                     "--unknown-global merge other", "-c user.name=x commit -m x", "-c pull.rebase=true pull --ff-only",
                     "-c merge.ff=false merge --ff-only other", "-c core.pager=x status"):
            self.assertRefusedEverywhere(rest)

    def test_short_clusters_and_abbreviations_cannot_hide_a_ref(self):
        for rest in ("merge -qm --abort other", "merge -vm --ff-only other", "merge -qm --quit other", "merge -qm --continue other",
                     "pull -qr", "pull --reb", "pull --ff-only --reb", "pull -q --ff-only -rq", "pull --strategy=ours --ff-only",
                     "merge -m --ff-only other", "merge -s --ff-only other", "merge --ff-only --no-ff other", "pull --ff-only --rebase"):
            self.assertRefusedEverywhere(rest)

    def test_git_dir_and_work_tree_are_resolved_together(self):
        other_git = os.path.join(self.elsewhere, ".git")
        self.assertRefused("git --work-tree=%s --git-dir=%s merge other" % (self.plugin, other_git), cwd=self.elsewhere)
        self.assertRefused("git --git-dir=%s --work-tree=%s merge other" % (other_git, self.plugin), cwd=self.elsewhere)
        self.assertRefused("git --git-dir %s merge other" % os.path.join(self.plugin, ".git"), cwd=self.elsewhere)
        self.assertRefused("git --git-dir=$G merge other", cwd=self.elsewhere)
        self.assertPasses("git --git-dir=%s --work-tree=%s merge other" % (other_git, self.elsewhere), cwd=self.elsewhere)

    def test_the_git_dir_and_git_work_tree_prefixes_are_honoured(self):
        self.assertRefused("GIT_WORK_TREE=%s GIT_DIR=%s/.git git merge other" % (self.plugin, self.elsewhere), cwd=self.elsewhere)
        self.assertRefused("GIT_DIR=%s/.git git merge other" % self.plugin, cwd=self.elsewhere)
        self.assertRefused("GIT_DIR=$X git merge other", cwd=self.elsewhere)
        self.assertPasses("GIT_DIR=%s/.git git merge other" % self.elsewhere, cwd=self.elsewhere)

    def test_a_cd_into_or_maybe_into_the_live_checkout_counts(self):
        self.assertRefused("cd %s && git merge other" % self.plugin, cwd=self.elsewhere)
        self.assertRefused("pushd %s; git merge other" % self.plugin, cwd=self.elsewhere)
        self.assertRefused('cd "$D" && git merge other', cwd=self.elsewhere)
        self.assertRefused("cd - && git merge other", cwd=self.elsewhere)
        self.assertRefused("popd; git merge other", cwd=self.elsewhere)
        self.assertRefused("sh -c 'cd %s; git merge other'" % self.plugin, cwd=self.elsewhere)
        self.assertRefused("cd %s && git merge other" % self.elsewhere, cwd=self.plugin)   # the old folder stays in the set: a cd may be a subshell's

    # a partial check must not count as the whole
    def test_an_allowed_command_does_not_cover_a_refused_one_behind_it(self):
        self.assertRefused("git merge --ff-only other && git merge other")
        self.assertRefused("git status; git merge other")
        self.assertRefused("git status || git merge other")
        self.assertRefused("git status | cat; git merge other")
        self.assertRefused("git commit -m x && git pull")
        self.assertRefused("sh -c 'git merge --ff-only other; git merge other'")
        self.assertRefused("git fetch; git -C %s merge other" % self.plugin, cwd=self.elsewhere)

    def test_an_unreadable_line_that_may_reach_the_live_checkout_is_refused(self):
        self.assertRefused("git merge 'unbalanced other")
        self.assertRefused("git merge other $(", cwd=self.plugin)

    # --- commands that reach only other folders are not judged ---
    def test_the_same_commands_in_another_repo_or_a_worktree_pass(self):
        for rest in ("merge other", "pull", "rebase other", "cherry-pick other", "revert HEAD", "stash pop", "reset --hard", "-c a.b=c status"):
            self.assertPasses("git " + rest, cwd=self.elsewhere)
            self.assertPasses("git -C %s %s" % (self.elsewhere, rest), cwd=self.plugin)
            self.assertPasses("git -C %s %s" % (self.wt, rest), cwd=self.elsewhere)
            self.assertPasses("git -C %s %s" % (self.wt, rest), cwd=self.plugin)
            self.assertPasses("git " + rest, cwd=self.wt)

    def test_an_unreadable_folder_with_a_plain_verb_passes_everywhere(self):
        for cwd in (self.elsewhere, self.plugin):
            for cmd in ('git -C "$WT" status', 'git -C "$WT" log', 'git -C "$WT" commit -m x', "git -C $WT diff", "git --git-dir=$G status",
                        'git -C "$WT" fetch', 'git -C "$WT" add -A', 'git -C "$WT" merge --ff-only other'):
                self.assertPasses(cmd, cwd=cwd)

    def test_an_unreadable_folder_with_a_merge_is_refused(self):
        self.assertRefused('git -C "$WT" merge other', cwd=self.elsewhere)       # it may be the live checkout
        self.assertRefused('git -C "$WT" pull', cwd=self.plugin)

    def test_a_copy_with_no_checkout_above_the_module_guards_nothing(self):
        """An installed copy has no .git above it, so there is no checkout to break: nothing is refused. checkout_root is not mocked."""
        bare = os.path.realpath(tempfile.mkdtemp(prefix="r1199-installed-"))
        with mock.patch.object(mergeguard, "checkout_root", REAL_CHECKOUT_ROOT), \
                mock.patch.object(mergeguard, "__file__", os.path.join(bare, "mergeguard.py")):
            self.assertIsNone(mergeguard.checkout_root())
            self.assertIsNone(mergeguard.decision("Bash", {"command": "git merge other"}, self.elsewhere))

    def test_the_real_checkout_root_is_the_checkout_this_module_sits_in(self):
        self.assertEqual(REAL_CHECKOUT_ROOT(), os.path.realpath(ROOT))

    def test_other_tools_and_other_commands_are_not_judged(self):
        self.assertPasses("echo git merge other", cwd=self.elsewhere)        # in the live checkout the word rule refuses it, by design
        self.assertRefused("echo git merge other", cwd=self.plugin)
        self.assertPasses("ls -la")
        self.assertPasses("printf '%s' merge")
        self.assertIsNone(mergeguard.decision("Write", {"file_path": "x"}, self.plugin))

    def test_the_guard_runs_no_git_itself(self):
        self.assertFalse(hasattr(mergeguard, "subprocess"))


class VerifyReportsWritesTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.project, ".budgit", "verify"), exist_ok=True)
        os.makedirs(os.path.join(self.project, "sub"), exist_ok=True)
        self.report = ".budgit/verify/R-1.md"
        self.folder = ".budgit/verify"

    def d(self, command, agent="general-purpose"):
        return sl.verify_forgery_decision("Bash", {"command": command}, agent, self.project)

    def assertDenied(self, command):
        got = self.d(command)
        self.assertIsNotNone(got, command)
        self.assertEqual(got[0], "deny", command)

    def assertPasses(self, command):
        self.assertIsNone(self.d(command), command)

    def test_the_write_tools_are_refused_for_a_non_verifier(self):
        for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("NotebookEdit", "notebook_path")):
            got = sl.verify_forgery_decision(tool, {key: os.path.join(self.project, self.report)}, "general-purpose", self.project)
            self.assertIsNotNone(got, tool)
            self.assertEqual(got[0], "deny", tool)

    def test_every_bash_write_form_is_refused_for_a_non_verifier(self):
        r, f = self.report, self.folder
        for cmd in ("cat x > %s" % r, "echo x >> %s" % r, "echo x | tee %s" % r, "echo x | tee -a %s" % r, "cp a %s" % r,
                    "mv a %s" % r, "ln -s a %s" % r, "install a %s" % r, "rsync a %s" % r, "sed -i s/a/b/ %s" % r,
                    "perl -pi -e s/a/b/ %s" % r, "perl -i -pe 1 %s" % r, "python3 make.py %s" % r, "python3 make.py --out %s" % r,
                    "bash make.sh %s" % r, "sh make.sh --output=%s" % r, "cp a %s/" % f, "cp -t %s a" % f):
            self.assertDenied(cmd)

    def test_the_verifier_agent_can_still_write(self):
        self.assertIsNone(self.d("cat x > %s" % self.report, agent="budgit:verifier"))
        self.assertIsNone(sl.verify_forgery_decision("Write", {"file_path": os.path.join(self.project, self.report)},
                                                     "budgit:verifier", self.project))

    def test_a_read_or_a_mention_passes(self):
        r, f = self.report, self.folder
        refused = []
        for cmd in ('grep -rn "/verify" .', 'grep "/verify" notes.txt', "grep -rn '/verify' sub", 'grep -n "%s" notes.txt' % f,
                    "cat %s" % r, "head -n 5 %s" % r, "ls %s" % f, "rg PASS %s" % f, 'rg "/verify" sub',
                    "git show HEAD:%s" % r, "git log --oneline -- %s" % f,
                    "%s ledger evidence R-1 --report %s" % (CLI, r), "%s ledger evidence --report %s" % (CLI, r),
                    "%s ledger show R-1" % CLI):
            if self.d(cmd) is not None:
                refused.append(cmd)
        self.assertEqual(refused, [])

    def test_the_report_exemption_is_limited_to_evidence_and_show(self):
        r = self.report
        for cmd in ("%s ledger done R-1 --report %s" % (CLI, r), "%s ledger link R-1 --src I-2 --report %s" % (CLI, r),
                    "%s ledger done R-1 --report=%s" % (CLI, r)):
            self.assertDenied(cmd)

    def prose(self):
        v = os.path.join(self.project, ".budgit", "verify")
        return v, "the folder %s/R-1.md, the words verify and bin/budgit, and git reset --hard are named here" % v

    def test_prose_that_names_the_verify_folder_passes_in_a_budgit_text_flag(self):
        v, text = self.prose()
        for flag in ("--directive", "--why", "--change", "--reason", "-m"):
            self.assertPasses('%s ledger link R-1 --src TM %s "%s"' % (CLI, flag, text))
        self.assertPasses("%s ledger link R-1 --src TM --directive '%s'" % (CLI, text))

    def test_prose_appended_to_a_progress_file_passes(self):
        v, text = self.prose()
        self.assertPasses('printf \'%%s\\n\' "%s" >> .studio/progress-tm.md' % text)
        self.assertPasses('printf \'%%s\\n\' "verify and bin/budgit" >> .studio/progress-tm.md')

    def test_a_sed_to_a_new_scratch_file_and_a_mv_pass_when_the_brief_text_names_the_folder(self):
        v, text = self.prose()
        for name in ("brief.md", "verify-brief.md", "verifier.md"):
            brief = os.path.join(self.project, "sub", name)
            with open(brief, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            self.assertPasses("sed -e s/A/B/ %s > %s.new && mv %s.new %s" % (brief, brief, brief, brief))

    def test_a_ledger_evidence_call_that_points_at_a_report_passes(self):
        v, text = self.prose()
        self.assertPasses("%s ledger evidence R-1 --sha dffe8038 --report %s/R-1.md" % (CLI, v))

    def test_ledger_done_file_takes_one_plain_report_path_as_a_read(self):
        v, text = self.prose()
        report = v + "/R-1.md"
        for cmd in ("%s ledger done R-1 --file %s" % (CLI, report), "%s ledger done R-1 --file=%s" % (CLI, report),
                    '%s ledger done R-1 --file "%s"' % (CLI, report), "%s ledger done R-1 --file '%s'" % (CLI, report),
                    "%s ledger done R-1 --file .budgit/verify/R-1.md" % CLI):
            self.assertPasses(cmd)
        for path in ("%s/R$X.md" % v, "%s/a;b.md" % v, "%s/R 1.md" % v):
            for cmd in ('%s ledger done R-1 --file "%s"' % (CLI, path), '%s ledger done R-1 --file="%s"' % (CLI, path)):
                self.assertDenied(cmd)
        self.assertDenied("%s ledger done R-1 --file %s && echo x > %s" % (CLI, report, report))

    def test_file_on_any_other_budgit_verb_keeps_its_behaviour(self):
        v, text = self.prose()
        report = v + "/R-1.md"
        for verb in ("ledger link R-1 --src I-2", "ledger evidence R-1", "ledger show R-1", "ledger add"):
            self.assertDenied("%s %s --file %s" % (CLI, verb, report))
            self.assertDenied("%s %s --file=%s" % (CLI, verb, report))

    def test_a_substitution_or_a_redirect_around_a_prose_value_is_still_judged(self):
        v, text = self.prose()
        base = "%s ledger link R-1 --src TM" % CLI
        for flag in ("--why", "--directive", "--change", "-m"):
            self.assertDenied('%s %s "a $(touch %s/R-1.md) b"' % (base, flag, v))
            self.assertDenied('%s %s "a `touch %s/R-1.md` b"' % (base, flag, v))
            for op in (">", ">>", "&>", ">|"):
                self.assertDenied('%s %s "a b" %s %s/R-1.md' % (base, flag, op, v))
                self.assertDenied('%s %s "%s" %s %s/R-1.md' % (base, flag, text, op, v))
        self.assertDenied('%s --why "a b" | tee %s/R-1.md' % (base, v))

    def test_real_write_targets_in_the_verify_folder_stay_refused(self):
        v, text = self.prose()
        for cmd in ("echo x > %s/R-1.md" % v, "tee %s/R-1.md" % v, "cp a %s/R-1.md" % v, 'printf x >> "%s/R 1.md"' % v,
                    'printf \'%%s\\n\' "%s" >> %s/R-1.md' % (text, v),
                    '%s ledger link R-1 --src TM --directive "%s" --out %s/R-1.md' % (CLI, text, v),
                    'echo "%s" | tee %s/R-1.md' % (text, v), 'cp "%s" %s/R-1.md' % (text, v)):
            self.assertDenied(cmd)

    # a partial check must not count as the whole
    def test_a_write_hidden_behind_an_allowed_read_is_still_refused(self):
        r = self.report
        for cmd in ('grep "/verify" x; cat x > %s' % r, 'grep "/verify" x && cat x > %s' % r, 'grep "/verify" x | tee %s' % r,
                    'grep "/verify" x || cp a %s' % r, "%s ledger evidence --report %s && cat x > %s" % (CLI, r, r),
                    "%s ledger evidence --report %s; cp a %s" % (CLI, r, r), "cat %s; echo x >> %s" % (r, r),
                    "rg --pre 'tee %s' x ." % r, "rg -z x %s" % self.folder, "rg -0z x %s" % self.folder, "rg -iz0 x %s" % self.folder, "%s ledger evidence --report %s --out %s" % (CLI, r, r),
                    "%s ledger evidence R-1 --report sub/x.md --note a --out %s" % (CLI, r)):
            self.assertDenied(cmd)


class SubagentCliRulesTest(StudioTestCase):
    def h(self, cmd, sub=True):
        got, _keys = sl.home_write_check("Bash", {"command": cmd}, self.project, "general-purpose", session="s1", subagent=sub)
        return got

    def test_ledger_reads_pass_for_a_subagent(self):
        for rest in ("show R-1", "list", "top"):
            self.assertIsNone(self.h("%s ledger %s" % (CLI, rest)), rest)

    def test_admit_without_slots_passes_for_a_subagent_and_admit_slots_stays_refused(self):
        for rest in ("admit", "admit suite", "admit lane --json", "admit --json"):
            self.assertIsNone(self.h("%s %s" % (CLI, rest)), rest)
        for rest in ("admit --slots 2 -- echo hi", "admit suite --slots 1 -- echo hi", "admit --slot 2 -- echo hi", "admit --kind x", "admit suite lane",
                     "admit suite extra"):
            got = self.h("%s %s" % (CLI, rest))
            self.assertIsNotNone(got, rest)
            self.assertEqual(got[0], "deny", rest)

    def test_a_read_loop_refuses_reassignment_of_the_loop_variable_source_and_other_variables(self):
        self.assertIsNone(self.h("for i in R-1 R-2; do %s ledger show $i | head -5; done" % CLI))
        self.assertIsNone(self.h("for i in R-1 R-2; do %s ledger list --limit 5 $i | head -5; done" % CLI))
        for extra in ("i[0]=--project=/tmp/x", "printf -vi x", "printf -v i x", "(( i = -1 ))", "(( i-- ))", "getopts p i",
                      "source /tmp/f", ". /tmp/f", "eval x", "read i", "declare i=1", "let i=2", "mapfile i", "local i"):
            line = "for i in R-1 R-2; do %s; %s ledger show $i | head -5; done" % (extra, CLI)
            got = self.h(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "deny", line)
        for line in ("for i in R-1; do %s ledger show $HOME; done" % CLI, "for i in R-1; do %s ledger show $i $HOME; done" % CLI):
            got = self.h(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "deny", line)

    def test_r1217_a_read_loop_refuses_any_redirect_tee_second_use_and_use_after_done(self):
        self.assertIsNone(self.h("for i in R-1 R-2; do %s ledger show $i | head -5; done" % CLI))
        self.assertIsNone(self.h("for i in R-1; do %s ledger show $i; done | head -5" % CLI))
        for tail in ("; done < /etc/passwd", " > /var/o/out.md; done", " $i; done", "; done > ~/x.md",
                     "; done | tee /var/o/x.md", "; done; %s ledger show $i" % CLI, "; done > /var/o/proj/x.md",
                     "; done >> /var/o/x.md", "; done 2> /var/o/x.md", "; done &> /var/o/x.md",
                     "; done | head -5 > /var/o/x.md", "; done | head -5 /var/o/x.md"):
            line = "for i in R-1; do %s ledger show $i%s" % (CLI, tail)
            if tail == " $i; done":
                line = "for i in R-1; do %s ledger show $i $i; done" % CLI
            elif tail.startswith(" >"):
                line = "for i in R-1; do %s ledger show $i%s" % (CLI, tail)
            got = self.h(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "deny", line)

    def test_r1217_r2_a_read_loop_reader_step_takes_an_explicit_flag_list_without_backtracking_or_newline(self):
        import time
        base = "for i in R-1; do %s ledger show $i; done" % CLI
        for tail in (" | head -5", " | head -n 5", " | wc -l", " | grep -c x", " | sort -n", " | sort -k 2,3 -r", " | tail -c 9"):
            self.assertIsNone(self.h(base + tail), tail)
        started = time.process_time()                                    # CPU time, not wall (R-1282)
        got = self.h(base + " | head" + " --a" * 40 + "; x")
        self.assertLess(time.process_time() - started, 1.0)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "deny")
        for tail in (" | sort -o x", " | sort -ox", " | sort --output=x", " | sort --compress-program=x", " | grep -f x", " | grep -r x",
                     " | grep --include=x y", " | grep -o x", " | head --lines=5", " | head -5 /var/o/x", " | sort -n /var/o/x",
                     " | grep\nmake", " | head\n5", " | head -5\r", "\n", " | grep -c", " | wc -l /var/o/x"):
            line = base + tail
            got = self.h(line)
            self.assertIsNotNone(got, repr(line))
            self.assertEqual(got[0], "deny", repr(line))

    def test_a_read_loop_with_a_variable_in_argument_position_passes_for_a_subagent(self):
        self.assertIsNone(self.h("for i in R-1 R-2; do %s ledger show $i | head -5; done" % CLI))
        self.assertIsNone(self.h("for i in R-1 R-2; do %s ledger list $i | head -5; done" % CLI))
        self.assertIsNone(self.h("for i in R-1 R-2; do %s ledger show $i | head -5; done" % CLI))
        for line in ("for i in R-1; do %s ledger show $i $X; done" % CLI,
                     "for i in R-1; do %s ledger show $i; done; i=--project=/tmp/x; %s ledger show $i" % (CLI, CLI),
                     "i=--project=/tmp/x; %s ledger show $i; for i in R-1; do :; done" % CLI,
                     "for i in R-1; do :; done; read i; %s ledger show $i" % CLI,
                     "export i=--project=/tmp/x; for i in R-1; do echo; done; %s ledger show $i" % CLI,
                     "for i in R-1; do %s ledger show $i; done; for i in --project=/x; do :; done" % CLI,
                     "for i in --project=/tmp/x R-1; do %s ledger show $i | head -5; done" % CLI,
                     "for i in R-1; do %s ledger show $j; done" % CLI,
                     "for i in a b; do %s --project $i ledger show R-1; done" % CLI,"for c in show add; do %s ledger $c R-1; done" % CLI,
                     "for b in %s; do $b ledger show R-1; done" % CLI, "for c in add; do %s ledger $c --text x | head -1; done" % CLI,
                     "for i in a; do %s $i; done" % CLI, "for s in show; do %s ledger $s R-1; done" % CLI,
                     "for i in a; do %s $i ledger show R-1; done" % CLI, "for i in a; do %s ledger show --project $i; done" % CLI,
                     'for i in a; do %s ledger show "$(echo $i)"; done' % CLI, "for i in a; do %s ledger add --text $i; done" % CLI,
                     "for i in a; do %s ledger show ${i}; done" % CLI, "for i in a; do %s ledger show $i; echo $(rm x); done" % CLI):
            got = self.h(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "deny", line)


class RunnerSiblingWorktreeTest(GuardCase):
    def test_a_runner_on_a_sibling_worktree_outside_the_project_does_not_ask(self):
        wt = tempfile.mkdtemp(prefix="r1199-sib-", dir=os.path.dirname(self.root))
        self.addCleanup(shutil.rmtree, wt, True)
        for cmd in ("npm --prefix %s test -- tests/unit" % wt, "npm --prefix %s exec -- playwright test e2e/a.spec.ts" % wt):
            self.assertIsNone(self.d(cmd), cmd)


class SuiteCpuOverrideTest(unittest.TestCase):
    def d(self, command, tool="Bash"):
        return sl.suite_cpu_override_decision(tool, {"command": command})

    def test_a_line_that_sets_the_suite_cpu_variable_is_refused_by_assignment_export_and_env(self):
        for name in ("BUDGIT_SUITE_TEST_CPU", "STUDIO_SUITE_TEST_CPU"):
            for line in ("%s=0 python3 tools/runtests.py test_a" % name, "export %s=0; python3 tools/runtests.py test_a" % name,
                         "env %s=0 python3 tools/runtests.py test_a" % name, "echo hi && %s=5 nice python3 x.py" % name,
                         'env "%s=0" python3 x.py' % name, "export %s+=1" % name):
                got = self.d(line)
                self.assertIsNotNone(got, line)
                self.assertEqual(got[0], "deny", line)

    def test_a_line_that_only_names_the_suite_cpu_variable_in_a_message_passes(self):
        for line in ('echo "do not set BUDGIT_SUITE_TEST_CPU or STUDIO_SUITE_TEST_CPU"', "grep -rn STUDIO_SUITE_TEST_CPU tests",
                     "echo OTHER_BUDGIT_SUITE_TEST_CPU_X=1", "python3 tools/runtests.py test_a"):
            self.assertIsNone(self.d(line), line)
        self.assertIsNone(self.d("BUDGIT_SUITE_TEST_CPU=0 x", tool="Write"))


if __name__ == "__main__":
    unittest.main()
