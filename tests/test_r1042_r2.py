"""R-1042 r2: anchored trailer pattern, already-published commits skipped at push, more bypass forms denied or asked, no worktree
plugin path baked into a hook, and the pre-push time with many refs. Temp repos only, fake HOME, never a real repo."""
import os
import subprocess
import sys
import time
import unittest
import unittest.mock

from helpers import ROOT

import attribution
import githooks
import studiolib as sl
from test_r1042_githooks import RepoCase, TRAILERS, plugin_copy

CHECK = os.path.join(ROOT, "plugin", "scripts", "githook_check.py")


class AnchorTest(RepoCase):
    def test_a_quoted_phrase_mid_sentence_passes_and_a_real_trailer_line_is_rejected(self):
        self.install()
        for msg in ('Policy: the "Co-Authored-By: Claude" trailer is banned here',      # r3: the Generated-with phrase is banned anywhere, so it is not in this list
                    "Docs: explain Co-Authored-By: Claude <noreply@anthropic.com> lines"):
            r = self.commit(self.repo, msg)
            self.assertEqual(r.returncode, 0, (msg, r.stderr))
        for trailer in list(TRAILERS.values()) + ["   Co-Authored-By: Claude <noreply@anthropic.com>",
                                                  "\t\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)"]:
            r = self.commit(self.repo, "subject\n\nbody text\n\n" + trailer)
            self.assertNotEqual(r.returncode, 0, trailer)
            self.assertIn("R-1042", r.stderr, trailer)

    def test_anchored_matching_is_the_hooks_and_the_guard_keeps_its_loose_match_on_command_text(self):
        self.assertIsNone(attribution.first_match('x "Co-Authored-By: Claude <a@anthropic.com>" y', anchored=True))
        self.assertTrue(attribution.first_match("see Generated with Claude Code here", anchored=True))     # r3: banned anywhere
        self.assertTrue(attribution.first_match("x\n  Co-Authored-By: Claude <a@anthropic.com>\n", anchored=True))
        self.assertTrue(attribution.first_match("x\n\U0001F916 Generated with [Claude Code](u)\n", anchored=True))
        # a command line carries the message after a quote or a flag: the guard still reads it loosely, as before
        self.assertTrue(attribution.has_attribution('git commit -m "x" -m "Co-Authored-By: Claude <a@anthropic.com>"'))
        self.assertEqual(attribution.decision("Bash", {"command": 'git commit -m "x" -m "Co-Authored-By: Claude <a@anthropic.com>"'},
                                              "/tmp", enabled=True)[0], "deny")


class AllRemotesPushTest(RepoCase):
    def test_a_push_to_a_new_remote_skips_commits_any_remote_already_has(self):
        self.install()
        a, b = self.make_repo("a.git", bare=True), self.make_repo("b.git", bare=True)
        self.git(self.repo, "remote", "add", "a", a)
        self.git(self.repo, "remote", "add", "b", b)
        self.assertEqual(self.commit(self.repo, "old\n\n" + TRAILERS["claude"], "--no-verify").returncode, 0)
        self.assertEqual(self.git(self.repo, "push", "-q", "--no-verify", "a", "main", check=False).returncode, 0)   # published long ago
        self.assertEqual(self.commit(self.repo, "clean new").returncode, 0)
        r = self.git(self.repo, "push", "-q", "b", "main", check=False)         # b is new: only the clean commit is not on a
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.commit(self.repo, "fresh\n\n" + TRAILERS["generated-plain"], "--no-verify").returncode, 0)
        r = self.git(self.repo, "push", "-q", "b", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("push rejected (R-1042)", r.stderr)
        r = self.git(self.repo, "push", "-q", "b", "main:other", check=False)   # a new branch: the fresh trailer commit is on no remote
        self.assertNotEqual(r.returncode, 0)

    def test_a_repo_with_no_remote_checks_the_whole_history_when_pushing_to_a_url(self):
        self.install()
        url = self.make_repo("url.git", bare=True)
        self.assertEqual(self.commit(self.repo, "old\n\n" + TRAILERS["claude"], "--no-verify").returncode, 0)
        r = self.git(self.repo, "push", "-q", url, "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("push rejected (R-1042)", r.stderr)


class BypassFormsTest(unittest.TestCase):
    def test_config_env_and_alias_forms_that_skip_hooks_are_denied(self):
        for cmd in ("GIT_CONFIG_GLOBAL=/dev/null git commit -m x", "GIT_CONFIG_SYSTEM=/dev/null git push origin main",
                    "env GIT_CONFIG_GLOBAL=/x git commit -m x", "export GIT_CONFIG_SYSTEM=/x; git commit -m x",
                    "git -c alias.ci='commit -n' ci -m x", "git -c alias.ci='commit --no-verify' ci",
                    "git -c alias.ps='!git push --no-verify' ps", "git config alias.ci 'commit -n'",
                    "git config --global alias.ci commit -n", "git config alias.ps 'push --no-verify'",
                    "git config alias.ci '!git commit -a --no-verify'"):
            got = sl.hook_skip_deny_decision(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_harmless_forms_stay_untouched_by_the_deny(self):
        for cmd in ("HOME=x ls", "git log", "git config alias.st status", "git -c alias.st=status st", "git config alias.ci 'commit -m'",
                    "git config --get alias.ci", "XDG_CONFIG_HOME=/x ls", "HOME=x git commit -m x", "GIT_DIR=/x git push"):
            self.assertIsNone(sl.hook_skip_deny_decision(cmd), cmd)

    def test_home_xdg_and_git_dir_on_a_commit_or_push_ask_and_other_commands_do_not(self):
        for cmd in ("HOME=/tmp/x git commit -m m", "XDG_CONFIG_HOME=/x git push origin main", "GIT_DIR=/x git commit -m m",
                    "env HOME=x git push origin main"):
            got = sl.hook_bypass_decision(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
        for cmd in ("HOME=x ls", "git log", "HOME=x git log", "XDG_CONFIG_HOME=/x ls", "GIT_DIR=/x git status", "git commit -m x"):
            self.assertIsNone(sl.hook_bypass_decision(cmd), cmd)


class WorktreePluginTest(RepoCase):
    def main_with_worktree(self, name, with_checker):
        main = os.path.realpath(self.make_repo(name))
        scripts = os.path.join(main, "plugin", "scripts")
        os.makedirs(scripts)
        with open(os.path.join(scripts, "githook_check.py" if with_checker else "other.py"), "w") as fh:
            fh.write("# stub\n")
        self.git(main, "add", "-A")
        self.git(main, "commit", "-q", "-m", "init")
        wt = os.path.join(self.tmp, name + "-wt")
        self.git(main, "worktree", "add", "-q", "-b", "side", wt)
        return main, os.path.realpath(wt)

    def test_a_plugin_dir_inside_a_linked_worktree_maps_to_the_main_checkout(self):
        main, wt = self.main_with_worktree("main1", True)
        self.assertEqual(githooks.stable_plugin(os.path.join(wt, "plugin")), os.path.join(main, "plugin"))
        self.assertEqual(githooks.stable_plugin(os.path.join(main, "plugin")), os.path.join(main, "plugin"))
        with unittest.mock.patch.object(githooks, "PLUGIN", os.path.join(wt, "plugin")):
            res = githooks.install_repo(self.repo)
        self.assertEqual(res["hooks"]["commit-msg"], "install")
        text = open(os.path.join(self.hooks(), "commit-msg")).read()
        self.assertIn("BUDGIT_PLUGIN='%s'" % os.path.join(main, "plugin"), text)
        self.assertNotIn(wt, text)

    def test_the_install_is_refused_with_a_clear_message_when_the_main_checkout_has_no_plugin(self):
        main, wt = self.main_with_worktree("main2", False)
        with self.assertRaises(githooks.Refused) as ctx:
            githooks.stable_plugin(os.path.join(wt, "plugin"))
        self.assertIn("worktree", str(ctx.exception))
        self.assertIn("main checkout", str(ctx.exception))
        with unittest.mock.patch.object(githooks, "PLUGIN", os.path.join(wt, "plugin")):
            with self.assertRaises(githooks.Refused):
                githooks.install_repo(self.repo)
            with self.assertRaises(githooks.Refused):
                githooks.install_repo(self.repo, dry_run=True)
        self.assertFalse(os.path.exists(os.path.join(self.hooks(), "commit-msg")))

    def test_a_plain_folder_outside_git_is_used_as_it_is(self):
        self.assertEqual(githooks.stable_plugin(plugin_copy()), os.path.realpath(plugin_copy()))


class TimingTest(RepoCase):
    def test_pre_push_with_200_tags_takes_under_two_seconds_and_still_reads_a_tag_message(self):
        self.assertEqual(self.commit(self.repo, "base").returncode, 0)
        for i in range(200):
            self.git(self.repo, "tag", "-a", "-m", "release %d" % i, "t%d" % i)
        refs = self.git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/tags").stdout.split("\n")
        lines = "".join("%s %s %s %s\n" % (r.split()[0], r.split()[1], r.split()[0], "0" * 40) for r in refs if r.strip())
        self.assertEqual(lines.count("\n"), 200)
        import resource                                  # CPU time of the child (RUSAGE_CHILDREN delta), not wall seconds (R-1282)
        def child_cpu():
            u = resource.getrusage(resource.RUSAGE_CHILDREN)
            return u.ru_utime + u.ru_stime
        c0 = child_cpu()
        r = subprocess.run([sys.executable, "-I", CHECK, "pre-push", "origin"], input=lines, capture_output=True, text=True,
                           cwd=self.repo, env=self.env)
        took = child_cpu() - c0
        sys.stderr.write("\nPRE-PUSH 200 tags: %.2fs\n" % took)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(took, 2.0)
        self.git(self.repo, "tag", "-a", "-m", TRAILERS["claude"], "bad")
        sha = self.git(self.repo, "rev-parse", "refs/tags/bad").stdout.strip()
        r = subprocess.run([sys.executable, "-I", CHECK, "pre-push", "origin"], capture_output=True, text=True, cwd=self.repo, env=self.env,
                           input=lines + "refs/tags/bad %s refs/tags/bad %s\n" % (sha, "0" * 40))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tag", r.stderr)


if __name__ == "__main__":
    unittest.main()
