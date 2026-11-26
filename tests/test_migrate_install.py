"""R-241 / O1: `budgit migrate-install` moves a live-folder install (a directory marketplace) to the public marketplace.
It shows the commands and runs them only when the person at a terminal answers yes; the old plugin is disabled, never deleted."""
import json
import os
import pty
import select
import stat
import subprocess
import sys
import time
import unittest

from helpers import StudioTestCase, ROOT

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
MANIFEST = os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json")


class MigrateInstallBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.config = os.environ["CLAUDE_CONFIG_DIR"]
        self.name = json.load(open(MANIFEST))["name"]
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.log = os.path.join(self.tmp, "claude-calls.txt")
        self.fail_on = os.path.join(self.tmp, "fail-on.txt")
        script = os.path.join(self.bin, "claude")
        with open(script, "w") as f:                       # records argv, one call per line; fails when asked
            f.write('#!/bin/sh\necho "$@" >> "%s"\n[ -f "%s" ] && grep -q "$(cat "%s")" "%s" && exit 1\nexit 0\n' % (self.log, self.fail_on, self.fail_on, self.log))
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        self.env = dict(os.environ, BUDGIT_HOME=self.home, PATH=self.bin + os.pathsep + os.environ["PATH"], BUDGIT_LANG="en")
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)

    def live_install(self, market="studio-hub", source="directory"):
        os.makedirs(os.path.join(self.config, "plugins"), exist_ok=True)
        with open(os.path.join(self.config, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"version": 2, "plugins": {"%s@%s" % (self.name, market): [{"scope": "user", "version": "0.13.0"}]}}, f)
        with open(os.path.join(self.config, "plugins", "known_marketplaces.json"), "w") as f:
            json.dump({market: {"source": {"source": source, "path": "/x"}, "installLocation": "/x"}}, f)

    def calls(self):
        return open(self.log).read().splitlines() if os.path.exists(self.log) else []

    def piped(self, *args):
        return subprocess.run([sys.executable, CLI, "migrate-install"] + list(args), env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, cwd=self.tmp)

    def on_terminal(self, answer, *args):
        master, slave = pty.openpty()
        proc = subprocess.Popen([sys.executable, CLI, "migrate-install"] + list(args), env=self.env, stdin=slave, stdout=slave,
                                stderr=slave, close_fds=True, cwd=self.tmp)
        os.close(slave)
        out, deadline, sent = b"", time.time() + 20, False
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                if not sent and b"[y/N]" in out:
                    os.write(master, answer.encode() + b"\n")
                    sent = True
            elif proc.poll() is not None:
                break
        proc.wait(timeout=10)
        os.close(master)
        return proc.returncode, out.decode("utf-8", "replace")


class PlanTest(MigrateInstallBase):
    def test_without_a_terminal_it_only_prints_the_commands_and_runs_nothing(self):
        self.live_install()
        r = self.piped("--repo", "owner/name")
        self.assertEqual(self.calls(), [])
        self.assertIn("claude plugin marketplace add owner/name", r.stdout)
        self.assertIn("claude plugin install budgit@budgit-hub", r.stdout)
        self.assertIn("claude plugin disable %s@studio-hub" % self.name, r.stdout)
        self.assertNotEqual(r.returncode, 0)                               # it did not migrate; the person must run it at a terminal

    def test_install_comes_before_disable_so_a_failed_install_changes_nothing(self):
        self.live_install()
        out = self.piped("--repo", "owner/name").stdout
        self.assertLess(out.index("plugin install"), out.index("plugin disable"))

    def test_nothing_to_do_when_there_is_no_directory_install(self):
        r = self.piped("--repo", "owner/name")
        self.assertIn("nothing to migrate", r.stdout.lower())
        self.assertEqual(r.returncode, 0)
        self.live_install(market="budgit-hub", source="github")             # already on a public marketplace
        self.assertIn("nothing to migrate", self.piped("--repo", "owner/name").stdout.lower())

    def test_a_missing_or_odd_repo_is_refused(self):
        self.live_install()
        for repo in (None, "no-slash", "a/b; touch x", "a/b c", "--help", "../x/y"):
            args = ("--repo", repo) if repo else ()
            r = self.piped(*args)
            self.assertNotEqual(r.returncode, 0, repo)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(self.calls(), [])

    def test_hostile_registry_names_never_reach_the_commands(self):
        self.live_install(market="bad hub; touch x")
        out = self.piped("--repo", "owner/name").stdout
        self.assertNotIn("touch x", out)


class RunTest(MigrateInstallBase):
    def test_a_yes_at_a_terminal_runs_the_three_commands_in_order(self):
        self.live_install()
        code, out = self.on_terminal("y", "--repo", "owner/name")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls(), ["plugin marketplace add owner/name", "plugin install budgit@budgit-hub",
                                        "plugin disable %s@studio-hub" % self.name])

    def test_anything_but_yes_runs_nothing(self):
        self.live_install()
        for answer in ("", "n", "no", "yess"):
            code, out = self.on_terminal(answer, "--repo", "owner/name")
            self.assertNotEqual(code, 0, answer)
            self.assertEqual(self.calls(), [], answer)

    def test_a_failing_step_stops_there_and_says_how_to_undo(self):
        self.live_install()
        open(self.fail_on, "w").write("plugin install")
        code, out = self.on_terminal("y", "--repo", "owner/name")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.calls(), ["plugin marketplace add owner/name", "plugin install budgit@budgit-hub"])     # disable never ran
        self.assertIn("still enabled", out.lower())

    def test_without_the_claude_cli_it_says_so(self):
        self.live_install()
        self.env["PATH"] = "/usr/bin:/bin"
        os.remove(os.path.join(self.bin, "claude"))
        code, out = self.on_terminal("y", "--repo", "owner/name")
        self.assertNotEqual(code, 0)
        self.assertNotIn("Traceback", out)

    def test_the_approval_needs_the_person_not_a_flag(self):
        self.live_install()
        r = self.piped("--repo", "owner/name", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
