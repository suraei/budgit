"""R-1286: the guard allows exactly `python3 -I <checkout>/tools/runtests.py` and `... --changed` (hash-pinned), so a builder
can earn the green-run receipt without her hands. Any other form is refused as before.
R-1282 (I-2041): and `python3 -I <main checkout>/tools/runtests.py --record`, the bare name python3 only, from the main checkout
(its .git is a folder) and never from a worktree; every other form stays refused."""
import os
import shutil
import unittest
from unittest import mock

from test_guard_trust import GuardCase
import studiolib as sl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "tools", "runtests.py")


class PinnedRunnerAllow(GuardCase):
    def verdict(self, cmd, subagent=True):
        got = sl.home_write_check("Bash", {"command": cmd}, self.root, None, subagent=subagent)[0]
        return got[0] if got else None

    def ok(self, cmd, cwd=None):
        w = cmd.split()
        return sl._is_test_runner(w, w, self.root if cwd is None else cwd, os.path.expanduser("~"))

    def fake(self, name, extra=b""):
        wt = os.path.join(os.path.expanduser("~"), "studio-wt", name)
        os.makedirs(os.path.join(wt, "tools"))
        os.makedirs(os.path.join(wt, "tests"))
        with open(RUNNER, "rb") as fh:
            data = fh.read()
        with open(os.path.join(wt, "tools", "runtests.py"), "wb") as fh:
            fh.write(data + extra)
        return wt

    def use_root(self, plugin_dir):
        """The guard reads `own` from studio_root(): point it at a plugin folder (a checkout's plugin/ or a cache-like plain folder)."""
        os.makedirs(plugin_dir, exist_ok=True)
        patcher = mock.patch.object(sl, "studio_root", lambda: plugin_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def main_checkout(self, name="studio-main", own=True):
        """A main checkout under the temp home: .git is a folder, tests/ and the real runner bytes. own=True makes it the guard's own."""
        base = os.path.join(os.path.expanduser("~"), "Dev", name)
        for sub in ("tools", "tests", ".git"):
            os.makedirs(os.path.join(base, sub))
        shutil.copyfile(RUNNER, os.path.join(base, "tools", "runtests.py"))
        base = os.path.realpath(base)
        if own:
            self.use_root(os.path.join(base, "plugin"))
        return base

    def worktree_of(self, main, name):
        """A folder under ~/studio-wt whose .git FILE names <main>/.git/worktrees/<name>."""
        wt = os.path.realpath(self.fake(name))
        with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as fh:
            fh.write("gitdir: %s\n" % os.path.join(main, ".git", "worktrees", name))
        return wt

    def test_the_exact_form_is_allowed_with_a_matching_pin(self):
        for who in (True, False):
            self.assertIsNone(self.verdict("python3 -I %s" % RUNNER, who))
        self.assertTrue(self.ok("python3 -I %s" % RUNNER))

    def test_changed_is_allowed(self):
        for who in (True, False):
            self.assertIsNone(self.verdict("python3 -I %s --changed" % RUNNER, who))

    def test_a_changed_runner_is_refused(self):
        wt = self.fake("pin-edited", b"\n")
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py" % wt))
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py --changed" % wt))

    def test_extra_flags_or_module_args_are_refused(self):
        for bad in ("--workers 4", "test_a", "--changed test_a", "--changed --record", "--out /x", "../x"):
            self.assertFalse(self.ok("python3 -I %s %s" % (RUNNER, bad)), bad)
        self.assertFalse(self.ok("python3 -I -B %s" % RUNNER))
        self.assertFalse(self.ok("python3 -B -I %s" % RUNNER))

    def test_a_chained_command_is_refused(self):
        for tail in ("; touch x", " && touch x", " | cat", " > f", " --changed; touch x"):
            self.assertNotEqual(self.verdict("python3 -I %s%s" % (RUNNER, tail)), None, tail)

    def test_an_env_prefix_is_refused(self):
        for pre in ("env PYTHONPATH=x ", "BUDGIT_TEST_CACHE=%s/c " % sl.home_state(), "PYTHONPATH=x "):
            self.assertFalse(self.ok("%spython3 -I %s" % (pre, RUNNER)), pre)
            self.assertNotEqual(self.verdict("%spython3 -I %s" % (pre, RUNNER)), None, pre)

    def test_a_symlinked_runner_is_refused(self):
        wt = self.fake("pin-link")
        real = os.path.join(wt, "tools", "runtests.py")
        os.replace(real, os.path.join(wt, "tools", "real.py"))
        os.symlink(os.path.join(wt, "tools", "real.py"), real)
        self.assertFalse(self.ok("python3 -I %s" % real))
        link = os.path.join(self.tmp, "linkroot")
        os.symlink(ROOT, link)
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py" % link))

    def test_a_copy_outside_a_checkout_of_this_project_is_refused(self):
        wt = self.fake("pin-plain")
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py" % wt))

    def test_record_alone_is_allowed_from_the_main_checkout(self):
        main = self.main_checkout()
        cmd = "python3 -I %s/tools/runtests.py --record" % main
        self.assertTrue(self.ok(cmd))
        for who in (True, False):
            self.assertIsNone(self.verdict(cmd, who))

    def test_record_from_a_worktree_root_is_not_allowed(self):
        main = self.main_checkout()
        wt = self.worktree_of(main, "pin-record-wt")
        self.assertTrue(self.ok("python3 -I %s/tools/runtests.py" % wt))              # control: the plain form is still fine there
        self.assertTrue(self.ok("python3 -I %s/tools/runtests.py --changed" % wt))
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py --record" % wt))
        self.assertNotEqual(self.verdict("python3 -I %s/tools/runtests.py --record" % wt), None)

    def test_record_from_the_main_checkout_works_only_where_the_plain_form_does(self):
        # her install: the guard's own folder is a plain cache folder with no .git, so no checkout reads as this repo
        main = self.main_checkout(own=False)
        self.use_root(os.path.join(self.tmp, "cache", "studio", "0.1"))
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py" % main))
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py --record" % main))
        # and a plain folder that is not a main checkout is not one either, even as the guard's own
        plain = self.main_checkout("studio-plain", own=False)
        shutil.rmtree(os.path.join(plain, ".git"))
        self.use_root(os.path.join(plain, "plugin"))
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py --record" % plain))

    def rec(self, cmd, cwd=None, project=None):
        """_is_test_runner as the guard calls it, with the session's BOUND project root (a `cd` cannot move it)."""
        w = cmd.split()
        kw = {} if project is None else {"project": project}
        return sl._is_test_runner(w, w, self.root if cwd is None else cwd, os.path.expanduser("~"), **kw)

    def test_record_in_the_cache_copy_takes_the_main_checkout_from_the_bound_project(self):
        main = self.main_checkout(own=False)
        self.use_root(os.path.join(self.tmp, "cache", "studio", "0.1"))                # her install: no .git next to the guard
        cmd = "python3 -I %s/tools/runtests.py --record" % main
        for cwd in (main, self.tmp, "/", ".", "", os.path.join(main, "tools")):        # the cwd is anywhere, even none or relative
            self.assertTrue(self.rec(cmd, cwd, project=main), cwd)
        for project in ("relative/path", ".", "/", self.tmp):                          # a relative project, or one that is not a main checkout
            self.assertFalse(self.rec(cmd, main, project=project), project)
        self.assertFalse(self.rec(cmd, main))                                          # no bound project at all
        # the plain and --changed forms behave exactly as before: still refused with a cache-like own folder
        for tail in ("", " --changed"):
            self.assertFalse(self.rec("python3 -I %s/tools/runtests.py%s" % (main, tail), main, project=main), tail)

    def test_record_in_the_cache_copy_is_not_moved_by_a_cd_into_a_copied_repo(self):
        main = self.main_checkout(own=False)
        self.use_root(os.path.join(self.tmp, "cache", "studio", "0.1"))
        evil = self.main_checkout("evil", own=False)                                   # git init + a byte copy of the runner and tests/
        ecmd = "python3 -I %s/tools/runtests.py --record" % evil
        for cwd in (evil, ".", os.path.join(evil, "tools")):
            self.assertFalse(self.rec(ecmd, cwd), cwd)                                 # no bound project: the cwd decides nothing
            self.assertFalse(self.rec(ecmd, cwd, project=main), cwd)                   # bound to the real main checkout: the copy is not it
        self.assertFalse(self.rec("python3 -I %s/tools/runtests.py --record" % main, evil))

    def test_record_in_the_cache_copy_refuses_a_worktree_runner(self):
        main = self.main_checkout(own=False)
        self.use_root(os.path.join(self.tmp, "cache", "studio", "0.1"))
        wt = self.worktree_of(main, "pin-cache-wt")
        wcmd = "python3 -I %s/tools/runtests.py --record" % wt
        for cwd in (main, wt):
            self.assertFalse(self.rec(wcmd, cwd, project=main), cwd)
        self.assertFalse(self.rec("python3 -I %s/tools/runtests.py --record" % main, wt, project=wt))   # a worktree is not the main working tree

    def test_the_guard_passes_the_bound_project_to_the_runner_check(self):
        main = self.main_checkout(own=False)
        self.use_root(os.path.join(self.tmp, "cache", "studio", "0.1"))
        cmd = "python3 -I %s/tools/runtests.py --record" % main
        self.assertIsNone(sl.home_write_check("Bash", {"command": cmd}, "/", None, project=main)[0])
        self.assertIsNotNone(sl.home_write_check("Bash", {"command": cmd}, main, None)[0])      # no bound project: refused, cwd or not

    def test_record_needs_the_bare_name_python3(self):
        main = self.main_checkout()
        tail = "-I %s/tools/runtests.py --record" % main
        self.assertTrue(self.ok("python3 " + tail))                                   # control
        for name in ("python3.12", "python", "python3.9.9", "python.", "python3..", "python" + chr(0x663), "python3" + chr(0x663),
                     "/usr/bin/python3", "/opt/homebrew/bin/python3", "python3.", "pythonX"):
            self.assertFalse(self.ok("%s %s" % (name, tail)), name)

    def test_record_with_a_redirect_or_chain_is_refused(self):
        main = self.main_checkout()
        base = "python3 -I %s/tools/runtests.py --record" % main
        self.assertIsNone(self.verdict(base))                                         # control
        for tail in (" > f", " >> f", "; touch x", " && touch x", " | cat", " 2>&1 | tee f"):
            self.assertNotEqual(self.verdict(base + tail), None, tail)
            self.assertNotEqual(self.verdict(base + tail, False), None, tail)

    def test_record_with_an_edited_runner_is_refused(self):
        main = self.main_checkout()
        with open(os.path.join(main, "tools", "runtests.py"), "ab") as fh:
            fh.write(b"\n")
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py --record" % main))
        self.assertNotEqual(self.verdict("python3 -I %s/tools/runtests.py --record" % main), None)

    def test_record_with_anything_else_in_the_isolated_form_is_refused(self):
        main = self.main_checkout()
        base = "python3 -I %s/tools/runtests.py" % main
        self.assertTrue(self.ok(base + " --record"))                                  # control
        for bad in ("--record --changed", "--changed --record", "--record test_x", "--record --workers 2", "--workers 2 --record",
                    "--record --record", "--record --out /x", "--record ../x"):
            self.assertFalse(self.ok("%s %s" % (base, bad)), bad)
            self.assertNotEqual(self.verdict("%s %s" % (base, bad)), None, bad)

    def test_record_through_a_symlinked_runner_is_refused(self):
        main = self.main_checkout()
        link = os.path.join(self.tmp, "linkroot")
        os.symlink(main, link)
        self.assertFalse(self.ok("python3 -I %s/tools/runtests.py --record" % link))
        real = os.path.join(main, "tools", "runtests.py")
        os.replace(real, os.path.join(main, "tools", "real.py"))
        os.symlink(os.path.join(main, "tools", "real.py"), real)
        self.assertFalse(self.ok("python3 -I %s --record" % real))

    def test_a_plain_write_into_the_budgit_home_is_still_refused(self):
        main = self.main_checkout()
        for cmd in ("touch %s/x" % sl.home_state(), "python3 -I %s/tools/runtests.py --record > %s/x" % (main, sl.home_state())):
            for who in (True, False):
                self.assertNotEqual(self.verdict(cmd, who), None, cmd)

    def test_the_plan_critic_has_the_routine_gate_lens(self):
        with open(os.path.join(ROOT, "plugin", "agents", "plan-critic.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("who passes this gate in routine work?", text)


if __name__ == "__main__":
    unittest.main()
