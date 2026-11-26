"""R-1360 F14 shape 4 (I-2209): a subagent's read-only archive listings and stat-only git diffs pass with no ask.
The five corpus commands (unzip -p, unzip -Z, 7z l, 7z t, git -C <repo> diff --stat) were asked by the real-data guard (an archive tool
is read as an extraction into the cwd; a flagless work-tree diff needs --ignore-submodules=all) and the hook turned the ask into a deny.
Every write or exec form of the same tools stays out of the new rule (`not_cleared`); the forms the hook asked about before, and tar
with a program option, still come back as a deny for a subagent. Paths are fake (/Users/x/proj) or temporary."""
import json
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

from test_guard_trust import GuardCase, HOOK, git
import hook
import mergeguard
import studiolib as sl

PY = os.path.realpath(sys.executable)
ZIP, SZ, TAR = "/Users/x/proj/a.zip", "/Users/x/proj/a.7z", "/Users/x/proj/a.tar"

# The tar exec rows, one constant per group. The refusal tests and the in-memory mutation proofs (TarMutationProofs) read the SAME constants, so
# a row can never be in the refusal test and missing from the proof. `%s` stands for the archive TAR.
TAR_PROGRAM_OPTION_ROWS = (        # an option that runs a program, spelled so that no -I/-F joined-value check is needed
    "tar -tf %s -I /Users/x/prog", "tar -tf %s --use-compress-program=/Users/x/prog", "tar -tf %s --use-compress-prog /Users/x/p",
    "tar -tf %s --to-command=/Users/x/prog", "tar -tf %s --checkpoint=1 --checkpoint-action=exec=/Users/x/prog",
    "tar -tvf %s -F /Users/x/prog", "tar -tf %s --info-script=/Users/x/prog", "tar tIf /Users/x/prog %s",
    "tar -tf %s --new-volume-script=/Users/x/prog")
TAR_GLUED_ROWS = (                 # -I / -F with the value joined to the letter or inside a cluster: only the joined-value check (_TAR_SHORT_EXEC.match) sees these
    "tar -tf %s -I/Users/x/prog", "tar -tf %s -F/Users/x/prog", "tar -tI/Users/x/prog -f %s", "tar -tvf %s -I.x", "tar -tf %s -I_x",
    "tar -tIf /Users/x/prog %s")
TAR_RMT_ROWS = ("tar -tf %s --rmt-command=/Users/x/prog", "tar -tf %s --rmt-command /Users/x/prog", "tar -tf %s --rsh-command=/Users/x/prog")
TAR_REMOTE_ROWS = (                # an archive host:path: tar starts rsh/ssh; only the host:path check (_TAR_REMOTE) sees these
    "tar -tf host:/Users/x/a.tar", "tar -t -f host:/Users/x/a.tar", "tar -tfhost:/Users/x/a.tar", "tar --file=host:/Users/x/a.tar -t",
    "tar tf user@host:/Users/x/a.tar")
TAR_EXEC_REASON = "budgit guard (R-1360): tar with -I, -F"       # the start of the reason text hook._tar_exec_decision gives


def tar_row(row):
    return row.replace("%s", TAR)


class ArchiveAndGitReads(GuardCase):
    def setUp(self):
        super().setUp()
        sl.write_pointer("s-r1360-4", self.root)
        self.repo = os.path.join(self.tmp, "other-repo")           # a second repository outside the project, no nested .git
        os.makedirs(os.path.join(self.repo, "src"))
        with open(os.path.join(self.repo, "src", "f.txt"), "w") as fh:
            fh.write("a\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        with open(os.path.join(self.repo, "src", "f.txt"), "w") as fh:
            fh.write("b\n")

    def hook(self, command, agent=True, cwd=None):
        cwd = cwd or self.root
        env = dict(os.environ, HOME=os.path.join(self.tmp, "fake-user-home"), STUDIO_HOME=self.home, PWD=cwd)
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s-r1360-4",
                   "cwd": cwd}
        if agent:
            payload.update(agent_id="agent-1", agent_type="general-purpose")
        r = subprocess.run([PY, HOOK, "pre-tool-use"], input=json.dumps(payload), env=env, capture_output=True, text=True, cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
        return out.get("permissionDecision"), out.get("permissionDecisionReason", "")

    def allowed(self, command, **kw):
        self.assertEqual(self.hook(command, **kw), (None, ""), command)

    def refused(self, command, **kw):
        decision, text = self.hook(command, **kw)
        self.assertIn(decision, ("deny", "ask"), (command, decision, text))

    def tar_refused(self, command, **kw):
        """A tar exec form is refused BY THE TAR-EXEC GUARD: the predicate says so and the hook outcome carries the tar-exec reason, so a deny
        from another guard (or a weakened tar matcher) cannot satisfy it."""
        self.assertTrue(mergeguard.tar_runs_program(command), command)
        decision, text = self.hook(command, **kw)
        self.assertIn(decision, ("deny", "ask"), (command, decision, text))
        self.assertIn("budgit guard (R-1360): tar with -I, -F", text, (command, decision, text))

    def not_cleared(self, command):
        """The new rule never lets this command through (the real-data ask, where there was one, stays)."""
        self.assertFalse(hook._read_form_clear("Bash", {"command": command}, self.root, self.root, ["real-data"]), command)

    # --- the five corpus shapes, verbatim (each was a deny on f158cc68)
    def test_shape_unzip_p_member(self):
        self.allowed("unzip -p %s notes/readme.txt" % ZIP)

    def test_shape_unzip_zipinfo_mode(self):
        self.allowed("unzip -Z %s" % ZIP)

    def test_shape_7z_list(self):
        self.allowed("7z l %s" % SZ)

    def test_shape_7z_test(self):
        self.allowed("7z t %s" % SZ)

    def test_shape_git_C_diff_stat(self):
        self.allowed("git -C %s diff --stat" % self.repo)

    # --- the wider allowed set
    def test_unzip_list_test_verbose_and_zipinfo(self):
        for cmd in ("unzip -l %s", "unzip -t %s", "unzip -v %s", "unzip -lq %s", "unzip -Z -1 %s", "unzip -Zl %s", "zipinfo %s"):
            with self.subTest(cmd=cmd):
                self.allowed(cmd % ZIP)

    def test_tar_list_forms(self):
        for cmd in ("tar -t -f %s", "tar -tf %s", "tar -tvf %s"):
            with self.subTest(cmd=cmd):
                self.allowed(cmd % TAR)

    def test_git_C_stat_only_diffs(self):
        for flags in ("--stat", "--name-only", "--name-status", "--numstat", "--stat --cached", "--name-only -- src"):
            with self.subTest(flags=flags):
                self.allowed("git -C %s diff %s" % (self.repo, flags))

    def test_git_C_read_verbs_already_passed(self):
        for verb in ("log", "show", "rev-parse HEAD", "ls-files"):
            with self.subTest(verb=verb):
                self.allowed("git -C %s %s" % (self.repo, verb))

    # --- every write or exec form stays out
    def test_refused_unzip_extract_forms(self):
        for cmd in ("unzip %s", "unzip -o %s", "unzip -d /Users/x/out %s", "unzip -l -d /Users/x/out %s", "unzip -lo %s", "unzip -x %s",
                    "unzip -pT %s", "unzip -j %s", "unzip -n %s", "unzip -f %s", "unzip -u %s", "unzip -lM %s", "unzip -l %s -d /Users/x/out"):
            with self.subTest(cmd=cmd):
                self.not_cleared(cmd % ZIP)
        self.refused("unzip %s" % os.path.join(self.root, "a.zip"))              # an extraction into the project root is still a deny
        # the real-data guard does not ask about an extraction into an unprotected folder inside the project (behaviour from before this
        # change), so the hook decision is not asserted here; what matters is that the NEW rule does not clear it
        self.not_cleared("unzip -d %s %s" % (os.path.join(self.root, "docs"), ZIP))

    def test_refused_7z_write_forms(self):
        for cmd in ("7z x %s", "7z e %s", "7z a %s", "7z u %s", "7z d %s", "7z l -o/Users/x/out %s", "7z l -so %s", "7z t -p123 %s"):
            with self.subTest(cmd=cmd):
                self.not_cleared(cmd % SZ)
        self.refused("7z x %s" % os.path.join(self.root, "a.7z"))

    def test_refused_tar_write_forms_stay_out_of_the_rule(self):
        for cmd in ("tar -xf %s", "tar -cf %s docs", "tar -rf %s docs", "tar -uf %s docs"):
            with self.subTest(cmd=cmd):
                self.not_cleared(cmd % TAR)
        self.refused("tar -xf %s" % os.path.join(self.root, "a.tar"))

    def test_refused_tar_program_options(self):
        """tar -t passed with no ask on f158cc68 even with an option that runs a program; the new tar-exec guard asks for each."""
        for cmd in TAR_PROGRAM_OPTION_ROWS:
            with self.subTest(cmd=cmd):
                self.tar_refused(tar_row(cmd))

    def test_refused_redirect_chain_pipe_and_env_prefix(self):
        for cmd in ("unzip -p %s m.txt > /Users/x/out.txt" % ZIP, "unzip -l %s; touch /Users/x/y" % ZIP, "unzip -l %s && unzip %s" % (ZIP, ZIP),
                    "unzip -p %s m.txt | tee /Users/x/out.txt" % ZIP, "FOO=1 unzip -l %s" % ZIP, "7z l %s > /Users/x/o.txt" % SZ,
                    "7z l %s | tee /Users/x/o.txt" % SZ, "env unzip -l %s" % ZIP, "unzip -l $(echo %s)" % ZIP,
                    "git -C %s diff --stat > /Users/x/o.txt" % self.repo, "FOO=1 git -C %s diff --stat" % self.repo,
                    "git -C %s diff --stat | tee /Users/x/o.txt" % self.repo):
            with self.subTest(cmd=cmd):
                self.not_cleared(cmd)

    def test_refused_git_C_forms(self):
        r = self.repo
        for cmd in ("git -C src diff --stat", "git -C %s -c core.pager=x diff --stat" % r, "git -c diff.external=x -C %s diff --stat" % r,
                    "git -C %s diff --ext-diff --stat" % r, "git -C %s diff --textconv --stat" % r, "git -C %s diff --output=/Users/x/o --stat" % r,
                    "git -C %s diff" % r, "git -C %s diff -p" % r, "git -C %s status" % r, "git -C %s checkout --stat" % r,
                    "git -C %s reset --hard" % r, "git -C %s diff --stat --ignore-submodules=none" % r):
            with self.subTest(cmd=cmd):
                self.not_cleared(cmd)
                self.assertNotEqual(self.hook(cmd), (None, ""), cmd) if cmd.startswith(("git -C src", "git -c", "git -C %s -c" % r)) else None

    def test_refused_git_C_with_nested_repo_or_external_diff_config(self):
        nested = os.path.join(self.repo, "vendor", "lib")
        os.makedirs(nested)
        git(nested, "init", "-q")
        self.refused("git -C %s diff --stat" % self.repo)
        repo2 = os.path.join(self.tmp, "cfg-repo")
        os.makedirs(repo2)
        git(repo2, "init", "-q")
        with open(os.path.join(repo2, ".git", "config"), "a") as fh:
            fh.write("[diff]\n\texternal = /Users/x/prog\n")
        self.refused("git -C %s diff --stat" % repo2)

    def test_refused_tar_glued_value_and_remote_forms(self):
        """Round 3 M1 / L1: -I/-F with the value glued on (no space), --rmt-command, and an archive host:path (tar starts rsh/ssh) run a program.
        Each of these passed with no ask on 7ba03783."""
        for cmd in TAR_GLUED_ROWS + TAR_RMT_ROWS + TAR_REMOTE_ROWS:
            with self.subTest(cmd=cmd):
                self.tar_refused(tar_row(cmd))

    def test_unzip_zipinfo_one_per_line(self):
        """Round 3 M2: `unzip -Z1 <zip>` is one of the six corpus commands (names only, one per line), with or without a member."""
        self.allowed("unzip -Z1 %s" % ZIP)
        self.allowed("unzip -Z1 %s notes/readme.txt" % ZIP)

    def test_git_log_plain_repo_passes_without_the_new_rule(self):
        """Round 3 M3: a subagent's `git -C <abs repo> log` and a plain `git log` in the project pass with no ask on a plain repository, and the new
        shape-4 rule is not what clears them (it clears only stat-only diffs), so they passed on f158cc68 as well: the live refusal of `git log`
        comes from a repository or user condition (the tests below), not from the command form."""
        log = "git -C %s log" % self.repo
        self.allowed(log)
        self.allowed("git log")
        self.allowed("git log --oneline -5", cwd=self.root)
        self.assertFalse(hook._read_form_clear("Bash", {"command": log}, self.root, self.root, ["real-data"]))

    def test_git_log_refused_by_the_real_data_guard_on_a_risky_repo(self):
        """Round 3 M3: the repo conditions that still refuse `git -C <repo> log` for a subagent; the asker is the real-data guard (the reason text
        names it), not the plugin-merge guard."""
        # an out-of-project repository is not judged at all for `log` (no protected path can be named), so the condition is shown on the project repo
        with open(os.path.join(self.root, ".git", "config"), "a") as fh:
            fh.write('[filter "x"]\n\tclean = /Users/x/prog\n')
        # a plain `git log` names no path, so no guard judges it (it passes even here); `-C <project root>` names the protected level and is judged
        self.allowed("git log")
        cmd = "git -C %s log" % self.root
        decision, text = self.hook(cmd)
        self.assertIn(decision, ("deny", "ask"), (cmd, text))
        self.assertIn("touches protected real data", text, (cmd, text))      # the real-data guard's own text, not the plugin-merge guard's

    def test_user_gitconfig_with_a_filter_refuses_git_log(self):
        """Round 3 L2: a clean filter in the user's own ~/.gitconfig runs on a work-tree read, so the config scan refuses it (it only matched
        `command|include|...` before)."""
        with open(os.path.join(self.tmp, "fake-user-home", ".gitconfig"), "w") as fh:
            fh.write('[filter "x"]\n\tclean = /Users/x/prog\n')
        self.refused("git -C %s log" % self.root)
        self.refused("git -C %s diff --stat" % self.repo)

    def test_user_gitconfig_without_risk_keeps_git_log_open(self):
        with open(os.path.join(self.tmp, "fake-user-home", ".gitconfig"), "w") as fh:
            fh.write('[user]\n\tname = x\n')
        self.allowed("git -C %s log" % self.repo)
        self.allowed("git -C %s log" % self.root)         # the project repo itself: judged (protected level named), cleared by the config scan
        self.allowed("git log")

    def test_the_user_session_still_asks(self):
        decision, _text = self.hook("unzip -l %s" % ZIP, agent=False)
        self.assertEqual(decision, "ask")

    def test_a_protected_archive_is_judged_like_a_cat_of_it(self):
        for path in (os.path.join(self.root, "data", "real", "x.zip"), os.path.join(self.root, "data.json")):
            self.assertEqual(self.hook("unzip -l %s" % path)[0], self.hook("cat %s" % path)[0], path)


class _FullMatch:
    """Stands in for a compiled pattern whose `.match` was weakened to `.fullmatch` (the verifier's mutation (a))."""
    def __init__(self, pattern):
        self.pattern = pattern

    def match(self, text):
        return self.pattern.fullmatch(text)


class _NoMatch:
    """Stands in for a compiled pattern that never matches (the host:path check taken out, mutation (b))."""
    def match(self, text):
        return None


class TarMutationProofs(unittest.TestCase):
    """R-1360 verifier item 6, made durable: the tar rows are judged by the check each row names, not by some other guard.
    The two mutations are applied IN MEMORY with unittest.mock.patch.object on the mergeguard module attribute (no file edit, no copy). They are
    asserted IN-PROCESS on mergeguard.tar_runs_program and hook._tar_exec_decision (the hook's tar-exec reason), not through the hook subprocess,
    because a subprocess cannot see an in-memory patch. The subprocess outcome of the same rows is asserted in ArchiveAndGitReads.tar_refused."""

    def judged(self, row):
        cmd = tar_row(row)
        decision = hook._tar_exec_decision(cmd)
        if decision is not None:
            self.assertEqual(decision[0], "ask", cmd)
            self.assertTrue(decision[1].startswith(TAR_EXEC_REASON), (cmd, decision))
        return mergeguard.tar_runs_program(cmd), decision is not None

    def assert_judged(self, row, why):
        self.assertEqual(self.judged(row), (True, True), (row, why))

    def assert_not_judged(self, row, why):
        self.assertEqual(self.judged(row), (False, False), (row, why))

    def test_glued_rows_depend_on_the_joined_value_check(self):
        """(a) _TAR_SHORT_EXEC behaving as fullmatch: every glued row stops being judged as tar-exec; the rows with a separate value still are."""
        for row in TAR_GLUED_ROWS:
            with self.subTest(row=row):
                self.assert_judged(row, "unpatched")
        with mock.patch.object(mergeguard, "_TAR_SHORT_EXEC", _FullMatch(mergeguard._TAR_SHORT_EXEC)):
            for row in TAR_GLUED_ROWS:
                with self.subTest(row=row, patched="fullmatch"):
                    self.assert_not_judged(row, "_TAR_SHORT_EXEC as fullmatch")
            for row in TAR_PROGRAM_OPTION_ROWS + TAR_RMT_ROWS + TAR_REMOTE_ROWS:
                with self.subTest(row=row, patched="fullmatch, other group"):
                    self.assert_judged(row, "the patch must touch the glued rows only")

    def test_remote_rows_depend_on_the_host_path_check(self):
        """(b) the host:path check (_TAR_REMOTE) off: every remote row stops being judged as tar-exec; the other groups still are."""
        for row in TAR_REMOTE_ROWS:
            with self.subTest(row=row):
                self.assert_judged(row, "unpatched")
        with mock.patch.object(mergeguard, "_TAR_REMOTE", _NoMatch()):
            for row in TAR_REMOTE_ROWS:
                with self.subTest(row=row, patched="no host:path check"):
                    self.assert_not_judged(row, "_TAR_REMOTE never matches")
            for row in TAR_PROGRAM_OPTION_ROWS + TAR_GLUED_ROWS + TAR_RMT_ROWS:
                with self.subTest(row=row, patched="no host:path check, other group"):
                    self.assert_judged(row, "the patch must touch the remote rows only")

    def test_the_patches_are_undone_after_the_proof(self):
        self.assertIsInstance(mergeguard._TAR_SHORT_EXEC, re.Pattern)
        self.assertIsInstance(mergeguard._TAR_REMOTE, re.Pattern)
