"""R-1042: git itself rejects an AI attribution trailer. commit-msg and pre-push hooks (plugin/githooks/, checks in
scripts/githook_check.py on attribution.first_match), `budgit githooks install|status`, and a static deny of --no-verify and
core.hooksPath overrides. Every test runs in temp repos with a fake HOME; never a real repo."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from helpers import StudioTestCase, ROOT

import attribution
import githooks
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
_COPY = []


def plugin_copy():
    """R-1042 r2: a copy of the plugin outside any git checkout. The install never bakes a linked worktree's plugin dir (it
    refuses or maps to the main checkout), and these tests run in a worktree, so they install from this copy."""
    if not _COPY:
        import atexit
        base = tempfile.mkdtemp(prefix="r1042-plugin-")
        dst = os.path.join(base, "plugin")
        shutil.copytree(os.path.join(ROOT, "plugin"), dst, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        atexit.register(shutil.rmtree, base, True)
        _COPY.append(dst)
    return _COPY[0]


TRAILERS = {
    "claude": "Co-Authored-By: Claude <noreply@anthropic.com>",
    "claude-model": "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>",
    "lowercase": "co-authored-by: Claude Opus <noreply@anthropic.com>",
    "anthropic-address": "Co-authored-by: Helper Bot <bot@anthropic.com>",
    "generated-link": "\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)",
    "generated-plain": "Generated with Claude Code",
}


class RepoCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = dict(os.environ, HOME=self.home, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@users.noreply.github.com",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@users.noreply.github.com",
                        BUDGIT_HOME=os.path.join(self.tmp, "bhome"), STUDIO_HOME=os.path.join(self.tmp, "bhome"))
        for k in [k for k in self.env if k.startswith("GIT_") and k not in (
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")]:
            self.env.pop(k)
        patcher = unittest.mock.patch.dict(os.environ, {"HOME": self.home, "GIT_CONFIG_GLOBAL": os.devnull,
                                                        "GIT_CONFIG_SYSTEM": os.devnull})   # githooks.py runs git with THIS env: no real gitconfig
        patcher.start()
        self.addCleanup(patcher.stop)
        import warnings
        warnings.simplefilter("ignore", ResourceWarning)       # the tests read small files inline
        self.repo = self.make_repo("repo")

    def git(self, repo, *args, check=True, stdin=None):
        return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=self.env, input=stdin,
                              check=check)

    def make_repo(self, name, bare=False):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        self.git(path, "init", "-q", *(["--bare"] if bare else []))
        if not bare:
            self.git(path, "checkout", "-q", "-b", "main")
        return path

    def write(self, repo, name, text="x\n"):
        with open(os.path.join(repo, name), "w") as fh:
            fh.write(text)
        self.git(repo, "add", name)

    def commit(self, repo, message, *flags):
        self.write(repo, "f%d.txt" % len(os.listdir(repo)))
        return self.git(repo, "commit", "-q", *flags, "-m", message, check=False)

    def install(self, repo=None, **kw):
        kw.setdefault("plugin", plugin_copy())
        return githooks.install_repo(repo or self.repo, **kw)

    def cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(plugin_copy(), "bin", "budgit")] + list(args), capture_output=True, text=True, env=self.env,
                              cwd=self.repo)

    def hooks(self, repo=None):
        return os.path.join(repo or self.repo, ".git", "hooks")


class CommitMsgTest(RepoCase):
    def test_commit_msg_rejects_each_trailer_form_and_accepts_a_clean_message(self):
        self.install()
        for name, trailer in TRAILERS.items():
            r = self.commit(self.repo, "subject\n\nbody\n\n" + trailer)
            self.assertNotEqual(r.returncode, 0, name)
            self.assertIn("R-1042", r.stderr, name)
        self.assertNotEqual(self.git(self.repo, "log", "--oneline", check=False).returncode, 0)    # nothing was committed
        ok = self.commit(self.repo, "Mention Claude in prose: the Claude docs say so")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.commit(self.repo, "plain message").returncode, 0)

    def test_commit_msg_rejects_a_trailer_read_from_a_file_and_an_editor_but_ignores_a_verbose_diff(self):
        self.install()
        self.write(self.repo, "g.txt")
        msg = os.path.join(self.tmp, "msg.txt")
        with open(msg, "w") as fh:
            fh.write("subject\n\n" + TRAILERS["claude"] + "\n")
        self.assertNotEqual(self.git(self.repo, "commit", "-q", "-F", msg, check=False).returncode, 0)
        # the hook reads the final message file, so the form of the command does not matter; the -v diff below the scissors line is not the message
        with open(msg, "w") as fh:
            fh.write("subject\n\n# ------------------------ >8 ------------------------\n+" + TRAILERS["claude"] + "\n")
        self.assertEqual(subprocess.run([sys.executable, "-I", os.path.join(ROOT, "plugin", "scripts", "githook_check.py"),
                                         "commit-msg", msg], capture_output=True, text=True).returncode, 0)

    def test_the_hook_and_the_guard_use_one_pattern_source(self):
        for trailer in TRAILERS.values():
            self.assertTrue(attribution.has_attribution(trailer))
            self.assertEqual(attribution.first_match("a\n" + trailer + "\nb"), trailer.strip())
        self.assertIsNone(attribution.first_match("a note about Claude\nand Anthropic"))
        text = open(os.path.join(ROOT, "plugin", "scripts", "githook_check.py")).read()
        self.assertIn("attribution", text)
        self.assertNotIn("re.compile", text)               # no copy of the pattern in the hook check


class PrePushTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.remote = self.make_repo("remote.git", bare=True)
        self.git(self.repo, "remote", "add", "origin", self.remote)

    def test_pre_push_rejects_an_outgoing_commit_with_a_trailer_and_passes_a_clean_push(self):
        self.install()
        self.assertEqual(self.commit(self.repo, "clean one").returncode, 0)
        ok = self.git(self.repo, "push", "-q", "origin", "main", check=False)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        # the commit-msg hook would stop the trailer, so the commit is made past it, as a bypass would
        bad = self.commit(self.repo, "dirty\n\n" + TRAILERS["claude"], "--no-verify")
        self.assertEqual(bad.returncode, 0, bad.stderr)
        self.assertEqual(self.commit(self.repo, "clean two").returncode, 0)
        r = self.git(self.repo, "push", "-q", "origin", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("push rejected (R-1042)", r.stderr)
        self.assertEqual(self.git(self.remote, "rev-list", "--count", "main").stdout.strip(), "1")     # nothing new arrived
        # a new branch is checked against the remote's known refs: the dirty commit is not on the remote, so it is caught
        r = self.git(self.repo, "push", "-q", "origin", "main:other", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.git(self.repo, "update-ref", "refs/heads/main", "HEAD~2")
        self.assertEqual(self.commit(self.repo, "clean three").returncode, 0)
        self.assertEqual(self.git(self.repo, "push", "-q", "origin", "main", check=False).returncode, 0)

    def test_pre_push_checks_a_first_push_of_a_whole_history_and_lets_a_branch_delete_through(self):
        self.install()
        self.commit(self.repo, "dirty\n\n" + TRAILERS["generated-plain"], "--no-verify")
        self.assertNotEqual(self.git(self.repo, "push", "-q", "origin", "main", check=False).returncode, 0)
        self.git(self.repo, "commit", "-q", "--amend", "--no-verify", "-m", "clean now")
        self.assertEqual(self.git(self.repo, "push", "-q", "origin", "main", check=False).returncode, 0)
        self.assertEqual(self.git(self.repo, "push", "-q", "origin", "main:gone", check=False).returncode, 0)
        self.assertEqual(self.git(self.repo, "push", "-q", "origin", ":gone", check=False).returncode, 0)


class InstallTest(RepoCase):
    def test_install_chains_an_existing_hook_runs_it_first_and_is_idempotent(self):
        os.makedirs(self.hooks(), exist_ok=True)
        mine = os.path.join(self.hooks(), "commit-msg")
        marker = os.path.join(self.tmp, "ran-first")
        with open(mine, "w") as fh:
            fh.write("#!/bin/sh\necho ran > '%s'\nexit 0\n" % marker)
        os.chmod(mine, 0o755)
        first = self.install()
        self.assertEqual(first["hooks"], {"commit-msg": "chain", "pre-push": "install"})
        self.assertTrue(os.path.exists(mine + ".local"))
        self.assertIn("ran > ", open(mine + ".local").read())               # the user's hook was moved, not changed
        self.assertIn("# budgit-githook commit-msg", open(mine).read())
        self.assertEqual(self.commit(self.repo, "clean").returncode, 0)
        self.assertTrue(os.path.exists(marker))                             # the user's hook ran
        before = {n: open(os.path.join(self.hooks(), n)).read() for n in os.listdir(self.hooks())}
        second = self.install()
        self.assertEqual(second["hooks"], {"commit-msg": "unchanged", "pre-push": "unchanged"})
        self.assertEqual({n: open(os.path.join(self.hooks(), n)).read() for n in os.listdir(self.hooks())}, before)
        self.assertIn("ran > ", open(mine + ".local").read())
        # the user's hook failing wins and passes its exit code through
        with open(mine + ".local", "w") as fh:
            fh.write("#!/bin/sh\nexit 7\n")
        bad = subprocess.run([mine, os.path.join(self.tmp, "nomsg")], capture_output=True)
        self.assertEqual(bad.returncode, 7)

    def test_a_dry_run_changes_nothing_and_a_symlinked_or_double_foreign_hook_is_a_conflict(self):
        dry = self.install(dry_run=True)
        self.assertEqual(dry["hooks"], {"commit-msg": "install", "pre-push": "install"})
        self.assertFalse(os.path.exists(os.path.join(self.hooks(), "commit-msg")))
        os.makedirs(self.hooks(), exist_ok=True)
        for name in ("commit-msg", "commit-msg.local"):
            with open(os.path.join(self.hooks(), name), "w") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
        res = self.install()
        self.assertEqual(res["hooks"]["commit-msg"], "conflict")
        self.assertEqual(res["hooks"]["pre-push"], "install")

    def test_core_hooks_path_is_the_install_target_relative_and_absolute(self):
        self.git(self.repo, "config", "core.hooksPath", "tools/hooks")
        res = self.install()
        self.assertEqual(res["via"], "core.hooksPath")
        target = os.path.join(self.repo, "tools", "hooks")
        self.assertEqual(os.path.realpath(res["hooks_dir"]), os.path.realpath(target))
        self.assertTrue(os.path.isfile(os.path.join(target, "commit-msg")))
        self.assertFalse(os.path.exists(os.path.join(self.hooks(), "commit-msg")))
        self.assertNotEqual(self.commit(self.repo, "x\n\n" + TRAILERS["claude"]).returncode, 0)
        absolute = os.path.join(self.tmp, "shared-hooks")
        self.git(self.repo, "config", "core.hooksPath", absolute)
        self.assertEqual(os.path.realpath(self.install()["hooks_dir"]), os.path.realpath(absolute))

    def test_a_worktree_shares_the_main_repos_hooks(self):
        self.assertEqual(self.commit(self.repo, "first").returncode, 0)
        wt = os.path.join(self.tmp, "wt")
        self.git(self.repo, "worktree", "add", "-q", "-b", "side", wt)
        res = self.install(wt)                                              # installing from the worktree
        self.assertEqual(os.path.realpath(res["hooks_dir"]), os.path.realpath(self.hooks()))
        self.assertTrue(os.path.isfile(os.path.join(self.hooks(), "commit-msg")))
        self.assertNotEqual(self.commit(wt, "x\n\n" + TRAILERS["claude"]).returncode, 0)         # the hook fires in the worktree too
        self.assertEqual(self.commit(wt, "clean").returncode, 0)
        self.assertEqual(self.install(self.repo)["hooks"], {"commit-msg": "unchanged", "pre-push": "unchanged"})

    def test_a_missing_plugin_fails_closed_with_a_message(self):
        plugin = os.path.join(self.tmp, "plugin-copy")
        shutil.copytree(os.path.join(ROOT, "plugin", "scripts"), os.path.join(plugin, "scripts"))
        self.install(plugin=plugin)
        self.assertEqual(self.commit(self.repo, "fine while the plugin is there").returncode, 0)
        shutil.rmtree(plugin)
        r = self.commit(self.repo, "now it is gone")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("fail closed", r.stderr)
        self.assertIn("budgit githooks install", r.stderr)
        self.assertEqual(githooks.status_repo(self.repo)["stale"], True)
        self.git(self.repo, "remote", "add", "origin", self.make_repo("rem.git", bare=True))
        p = self.git(self.repo, "push", "-q", "origin", "main", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("fail closed", p.stderr)

    def test_the_attribution_setting_off_lets_a_trailer_through_like_the_guard(self):
        self.install()
        os.makedirs(self.env["BUDGIT_HOME"], exist_ok=True)
        with open(os.path.join(self.env["BUDGIT_HOME"], "config.json"), "w") as fh:
            fh.write('{"no_claude_attribution": false}')
        self.assertEqual(self.commit(self.repo, "x\n\n" + TRAILERS["claude"]).returncode, 0)


class StatusTest(RepoCase):
    def status(self, repo=None):
        return githooks.status_repo(repo or self.repo)

    def test_status_reports_missing_installed_chained_and_foreign(self):
        self.assertEqual(self.status()["state"], "missing")
        os.makedirs(self.hooks(), exist_ok=True)
        with open(os.path.join(self.hooks(), "pre-push"), "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        st = self.status()
        self.assertEqual((st["state"], st["hooks"]["pre-push"], st["hooks"]["commit-msg"]), ("foreign", "foreign", "missing"))
        self.install()
        st = self.status()
        self.assertEqual((st["state"], st["hooks"]), ("chained", {"commit-msg": "installed", "pre-push": "chained"}))
        plain = self.make_repo("plain")
        self.install(plain)
        self.assertEqual(self.status(plain)["state"], "installed")
        self.assertEqual(self.status(self.tmp)["state"], "not a git repo")

    def test_status_and_install_output_through_the_cli(self):
        r = self.cli("githooks", "status", "--project", self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("missing", r.stdout)
        self.assertIn(self.hooks(), r.stdout.replace("/private", ""))
        dry = self.cli("githooks", "install", "--dry-run", "--project", self.repo)
        self.assertIn("dry run, nothing changed", dry.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.hooks(), "commit-msg")))
        done = self.cli("githooks", "install", "--project", self.repo)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("commit-msg: install", done.stdout)
        self.assertIn("installed", self.cli("githooks", "status", "--project", self.repo).stdout)
        both = self.cli("githooks", "install", "--all", "--project", self.repo)
        self.assertNotEqual(both.returncode, 0)

    def test_all_lists_the_portfolio_with_a_protected_project_shown_by_id_only(self):
        import portfolio
        plain, secret = [os.path.realpath(self.make_repo(n)) for n in ("plain-one", "secret-one")]
        data = {"version": 1, "projects": [portfolio._entry(plain), portfolio._entry(secret)]}
        with unittest.mock.patch.object(portfolio, "load", return_value=data), \
                unittest.mock.patch("livework.is_private", side_effect=lambda p: p == secret):
            out = io.StringIO()
            githooks.run("install", None, all_projects=True, dry_run=True, out=out, plugin=plugin_copy())
        text = out.getvalue()
        self.assertIn(plain, text)
        self.assertNotIn("secret-one", text)
        self.assertIn("private~", text)
        self.assertEqual(text.count("dry run, nothing changed"), 2)
        self.assertFalse(os.path.exists(os.path.join(secret, ".git", "hooks", "commit-msg")))


class StaticGuardTest(unittest.TestCase):
    def deny(self, command):
        return sl.hook_skip_deny_decision(command)

    def test_no_verify_and_hooks_path_overrides_are_denied(self):
        for cmd in ("git commit --no-verify -m x", "git commit -n -m x", "git commit -anm x", "git commit --no-verif -m x",
                    "git push --no-verify origin main", "git merge --no-verify topic", "git rebase --no-verify main",
                    "git cherry-pick --no-verify abc", "git am --no-verify p.mbox",
                    "git -c core.hooksPath=/dev/null commit -m x", "git -C /tmp/r -c core.hooksPath=x push",
                    "git config core.hooksPath /dev/null", "git config --global core.hooksPath x",
                    "git config set core.hooksPath x", "cd /tmp && git commit -n -m x", "bash -c 'git commit --no-verify -m x'",
                    "env GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x GIT_CONFIG_COUNT=1 git commit -m x",
                    "git --config-env=core.hooksPath=HP commit -m x"):
            got = self.deny(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_ordinary_git_is_not_denied(self):
        for cmd in ("git commit -m 'about --no-verify in prose'", "git push -n origin main", "git push --dry-run",
                    "git merge -n topic", "git config core.hooksPath", "git config --unset core.hooksPath",
                    "git config --get core.hooksPath", "git config user.name x", "git commit -m x", "git log -n 3",
                    "echo git commit -n", "git status"):
            self.assertIsNone(self.deny(cmd), cmd)

    def test_the_hook_denies_it_end_to_end_for_a_coordinator_and_a_subagent(self):
        hook = os.path.join(ROOT, "plugin", "scripts", "hook.py")
        with tempfile.TemporaryDirectory() as home:
            for extra in ({}, {"agent_id": "a1", "agent_type": "general-purpose"}):
                payload = dict({"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s", "cwd": home,
                                "tool_input": {"command": "git commit --no-verify -m x"}}, **extra)
                r = subprocess.run([sys.executable, hook, "pre-tool-use"], input=json.dumps(payload), capture_output=True, text=True,
                                   env=dict(os.environ, BUDGIT_HOME=home, STUDIO_HOME=home))
                self.assertIn('"deny"', r.stdout, (extra, r.stdout, r.stderr))
                self.assertIn("R-1042", r.stdout)


if __name__ == "__main__":
    unittest.main()
