"""R-856: Budgit's own read-only release checks (`budgit export <scratch dir> [--repo <public repo> | --check]` and
`budgit secrets <dir under ~/studio-wt>`) are not refused by the budgit-home guard. Root cause: run as
`python3 <plugin>/bin/budgit ...`, the script-reading rule (`_file_names_home`) found BUDGIT_HOME in the CLI's docstring plus a
write call and denied the CLI itself. The allow line pins this checkout's CLI and a system interpreter, both by absolute path
and realpath; near-misses stay refused. Git calls of the release gates run with no fsmonitor, no hooks and a clean env.
(`export --source` moved to R-861.)"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import leak
import studiolib as sl
import vendor

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(sl.__file__)))
CLI = os.path.join(PLUGIN, "bin", "budgit")
PY = os.path.realpath(sys.executable)           # an absolute interpreter the pin accepts
REPO = "sur" + "aei/budgit"


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r856-")
        self.home = os.path.join(self.tmp, "home")
        self.wt = os.path.join(self.home, "studio-wt", "r856")
        os.makedirs(self.wt)
        self.out = os.path.join(tempfile.mkdtemp(prefix="r856-out-", dir="/tmp"), "out")
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def verdict(self, cmd):
        got = sl.home_write_check("Bash", {"command": cmd}, self.tmp)[0]
        return got[0] if got else None

    # the real commands (audit ids 3c5dacb1, 2b234923), with an absolute interpreter and CLI
    def test_export_repo_passes(self):
        self.assertIsNone(self.verdict("%s %s export %s --repo %s" % (PY, CLI, self.out, REPO)))

    def test_export_check_passes(self):
        self.assertIsNone(self.verdict("%s %s export %s --check" % (PY, CLI, self.out)))

    def test_secrets_passes(self):
        self.assertIsNone(self.verdict("%s %s secrets %s" % (PY, CLI, self.wt)))

    def test_plain_export_passes_and_the_direct_cli_form_is_not_allowed_by_the_line(self):
        self.assertIsNone(self.verdict("%s %s export %s" % (PY, CLI, self.out)))
        # the shebang `#!/usr/bin/env python3` lets the shell's PATH pick the interpreter: the allow line covers only `<abs python3> <abs CLI>`
        for direct in ("%s export %s --check" % (CLI, self.out), "%s secrets %s" % (CLI, self.wt)):
            words = direct.split()
            self.assertFalse(sl._is_release_check(words, list(words), self.tmp, self.home), direct)

    # near-misses stay refused
    def test_near_misses_refused(self):
        h = sl.home_state()
        near = [
            "%s %s export %s --repo %s" % (PY, CLI, os.path.join(h, "out"), REPO),            # out dir inside the Budgit home
            "%s %s export %s --repo other/repo" % (PY, CLI, self.out),                        # another repo
            "%s %s export %s --check --repo %s" % (PY, CLI, self.out, REPO),                  # extra args
            "%s %s export %s --source %s" % (PY, CLI, self.out, self.wt),                     # --source moved to R-861
            "%s %s export %s --repo %s; echo x > %s/x" % (PY, CLI, self.out, REPO, h),
            "%s %s export %s --check && touch %s/x" % (PY, CLI, self.out, h),
            "%s %s export $(echo %s) --check" % (PY, CLI, h),
            "%s %s export %s --check > %s/log" % (PY, CLI, self.out, h),
            "%s %s export /Users/someone/out --check" % (PY, CLI),                            # not a scratch folder
            "%s %s export %s/../x --check" % (PY, CLI, self.out),
            "%s %s secrets %s" % (PY, CLI, h),                                                # not under studio-wt
            "%s %s secrets %s/other" % (PY, CLI, self.home),
            "%s %s secrets %s %s" % (PY, CLI, self.wt, self.wt),
            "%s -u %s secrets %s" % (PY, CLI, self.wt),                                       # a flag before the script
            "FOO=1 %s %s secrets %s" % (PY, CLI, self.wt),                                    # env prefix
            "%s %s secrets %s | tee %s/x" % (PY, CLI, self.wt, h),
        ]
        for cmd in near:
            self.assertIsNotNone(self.verdict(cmd), cmd)         # a deny or an ask, never silent

    # security review: the allow line trusts only THIS checkout's CLI and a system interpreter, by absolute path
    def _fake_checkout(self, with_cli_copy=False):
        root = tempfile.mkdtemp(prefix="r856-fake-", dir="/tmp")
        os.makedirs(os.path.join(root, "bin"))
        os.makedirs(os.path.join(root, "scripts"))
        for name in ("studiolib.py", "hook.py"):
            open(os.path.join(root, "scripts", name), "w").close()
        cli = os.path.join(root, "bin", "budgit")
        with open(cli, "w") as fh:
            if with_cli_copy:
                with open(CLI) as src:
                    fh.write(src.read())
            else:
                fh.write("import os\nopen(%r, 'w').write('x')\n" % os.path.join(sl.home_state(), "trust.json"))
        os.chmod(cli, 0o755)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root, cli

    def test_a_stub_cli_named_budgit_with_stub_scripts_is_refused(self):
        _, cli = self._fake_checkout()
        for sub in ("export %s" % self.out, "export %s --check" % self.out, "secrets %s" % self.wt):
            self.assertIsNotNone(self.verdict("%s %s %s" % (PY, cli, sub)), sub)

    def test_an_edited_or_other_checkout_cli_is_refused(self):
        _, cli = self._fake_checkout(with_cli_copy=True)
        self.assertIsNotNone(self.verdict("%s %s export %s --check" % (PY, cli, self.out)))

    def test_a_fake_python3_is_refused(self):
        root, _ = self._fake_checkout()
        py = os.path.join(root, "python3")
        with open(py, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(py, 0o755)
        self.assertIsNotNone(self.verdict("%s %s export %s --check" % (py, CLI, self.out)))

    def test_a_fake_python3_in_the_users_local_bin_is_refused(self):
        local = os.path.join(self.home, ".local", "bin")
        os.makedirs(local)
        py = os.path.join(local, "python3")
        with open(py, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(py, 0o755)
        with mock.patch.dict(os.environ, {"PATH": local + os.pathsep + os.environ.get("PATH", "")}):
            self.assertIsNotNone(self.verdict("%s %s export %s --check" % (py, CLI, self.out)))
            self.assertIsNotNone(self.verdict("python3 %s export %s --check" % (CLI, self.out)))
            for direct in ("%s export %s --check" % (CLI, self.out), "%s secrets %s" % (CLI, self.wt)):
                words = direct.split()
                self.assertFalse(sl._is_release_check(words, list(words), self.tmp, self.home), direct)

    def test_a_bare_python3_or_budgit_name_is_never_pinned(self):
        # the hook's PATH may differ from the shell's: only absolute paths are judged
        self.assertFalse(sl._pinned_python("python3"))
        self.assertFalse(sl._pinned_cli("budgit"))
        self.assertTrue(sl._pinned_python(PY))
        self.assertTrue(sl._pinned_cli(CLI))
        self.assertIsNotNone(self.verdict("python3 %s export %s --check" % (CLI, self.out)))
        words = ["budgit", "export", self.out]
        self.assertFalse(sl._is_release_check(words, list(words), self.tmp, self.home))

    def test_other_subcommands_refused(self):
        for sub in ("ledger add --text x", "trust work", "relocate %s" % self.out, "status"):
            self.assertIsNotNone(self.verdict("%s %s %s" % (PY, CLI, sub)), sub)

    def test_normal_refusals_unchanged(self):
        h = sl.home_state()
        for cmd in ("echo x > %s/trust.json" % h, "cp /tmp/p %s/trust.json" % h, "python3 %s/plugin/scripts/hook.py stop" % PLUGIN):
            self.assertEqual(self.verdict(cmd), "deny", cmd)


class GitHardening(unittest.TestCase):
    """The git calls of release_check, _check_out and leak.py never run a command the scanned tree's own config names."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="r856-repo-", dir="/tmp")
        self.addCleanup(lambda: shutil.rmtree(self.repo, ignore_errors=True))
        self.marker = os.path.realpath(os.path.join(self.repo, "..", "r856-marker-%d" % os.getpid()))
        self.addCleanup(lambda: os.path.exists(self.marker) and os.remove(self.marker))
        for args in (["init", "-q"], ["config", "user.email", "a@b.c"], ["config", "user.name", "t"],
                     ["config", "core.fsmonitor", "touch %s; echo" % self.marker]):
            subprocess.run(["git", "-C", self.repo] + args, check=True, capture_output=True)
        with open(os.path.join(self.repo, "f.txt"), "w") as fh:
            fh.write("x\n")
        subprocess.run(["git", "-C", self.repo, "add", "f.txt"], capture_output=True)
        if os.path.exists(self.marker):
            os.remove(self.marker)

    def test_the_control_plain_git_runs_the_fsmonitor_command(self):
        subprocess.run(["git", "-C", self.repo, "status", "--porcelain"], capture_output=True)
        if not os.path.exists(self.marker):
            self.skipTest("this git does not run core.fsmonitor commands: nothing to guard against")

    def test_fsmonitor_in_the_scanned_tree_never_runs(self):
        vendor._git(self.repo, "status", "--porcelain", "--", ".")
        vendor._git(self.repo, "rev-parse", "--show-toplevel")
        leak._origin_repo(self.repo)
        leak._common_dir(self.repo)
        self.assertFalse(os.path.exists(self.marker), "a core.fsmonitor command of the scanned tree ran")

    def test_the_argv_and_env_are_hardened(self):
        argv = sl.safe_git_argv("/x", "status")
        self.assertEqual(argv[:5], ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"])
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere", "GIT_WORK_TREE": "/else"}):
            env = sl.safe_git_env()
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_WORK_TREE", env)


if __name__ == "__main__":
    unittest.main()
