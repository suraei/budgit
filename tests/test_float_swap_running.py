"""R-1255: a running Brote never took a newer build, so users who update kept the old Brote for days.

Rule: when the installed build is strictly newer than the running one, a session start replaces the running Brote once for that
version, keeps its window position and state, and never leaves the user with no Brote.

Everything runs on a fake machine under a temp folder (see test_float_cli.Setup): a fake app that holds brote.lock like the real one and
writes a "saved" file when it is closed with SIGTERM (the real app saves its window position in its own defaults on that signal).
"""
import json
import os
import signal
import sys
import time
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_float_cli as base  # noqa: E402

FAKE_TERM = base.FAKE.replace(
    "null = os.stat",
    "import signal\n"
    "def _term(*a):\n"
    "    open(os.path.join(home, 'saved-' + str(os.getpid())), 'w').write('window kept')\n"
    "    sys.exit(0)\n"
    "signal.signal(signal.SIGTERM, _term)\n"
    "null = os.stat", 1)
FAKE_BROKEN = "#!/nonexistent-interpreter-for-the-test\n"


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@unittest.skipUnless(base.MAC, "needs macOS (codesign)")
class SwapRunning(unittest.TestCase):
    def machine(self, version="0.13.2"):
        """A fake machine whose Brote is up and holding the lock, built by the plugin at `version`."""
        old = base.FAKE
        base.FAKE = FAKE_TERM
        self.addCleanup(setattr, base, "FAKE", old)
        s = base.Setup(self, build_id="build-one", version=version)
        (s.home / "fake-hold").write_text("x")
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s.wait_launches(1)
        self.old_pid = self.wait_holder(s)
        (s.bw / "window.pos").write_text("123,456\n")       # stands for what the app keeps about its window
        return s

    def wait_holder(self, s, secs=8):
        end = time.time() + secs
        while time.time() < end:
            try:
                t = (s.bw / "brote.lock").read_text().strip()
            except OSError:
                t = ""
            if t.isdigit() and alive(int(t)):
                return int(t)
            time.sleep(0.05)
        self.fail("the fake Brote never took the lock")

    def newer(self, s, build_id="build-two", version="0.13.3", fake=None):
        root = s.cfg / "plugins/cache/studio-hub/studio" / version
        shutil.copytree(s.root, root)
        if fake is not None:
            saved = base.FAKE
            base.FAKE = fake
        try:
            s.make_bundle(build_id, into=root / "float")
        finally:
            if fake is not None:
                base.FAKE = saved
        s.register(root, version=version)
        return root

    def build(self, s):
        return (s.bw / "Brote.app/Contents/Resources/build.txt").read_text().strip()

    def state(self, s):
        return json.loads((s.bw / "float.json").read_text())

    def saved_files(self, s):
        return sorted(p.name for p in s.home.glob("saved-*"))

    def test_a_running_older_build_is_replaced_and_relaunched_with_its_window_kept(self):
        s = self.machine()
        root = self.newer(s)
        r = s.budgit("ensure", root=root)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.build(s), "build-two", "the new build is installed")
        self.assertEqual(len(s.wait_launches(2)), 2, "and started")
        self.assertEqual(s.launches()[1]["argv"][0], str(s.installed_exe()))
        new_pid = self.wait_holder(s)
        self.assertNotEqual(new_pid, self.old_pid)
        self.assertFalse(alive(self.old_pid), "the old one is gone")
        self.assertEqual(self.saved_files(s), ["saved-%d" % self.old_pid], "it was closed with SIGTERM, so it could save its window")
        self.assertEqual((s.bw / "window.pos").read_text(), "123,456\n", "its saved state is untouched")
        st = self.state(s)
        self.assertEqual((st["float"], st["version"], st["replaced_for"]), ("on", "0.13.3", "0.13.3"))

    def test_a_running_equal_or_newer_build_is_left_alone(self):
        for recorded in ("0.13.3", "0.13.9"):
            with self.subTest(recorded=recorded):
                s = self.machine()
                st = self.state(s)
                st["version"] = recorded
                (s.bw / "float.json").write_text(json.dumps(st))
                root = self.newer(s)
                r = s.budgit("ensure", root=root)
                self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
                self.assertEqual(self.build(s), "build-one")
                self.assertEqual(len(s.launches()), 1, "nothing new was started")
                self.assertTrue(alive(self.old_pid), "the running one was not touched")
                self.assertEqual(self.saved_files(s), [])

    def test_a_failed_relaunch_puts_the_old_copy_back_running_and_logs_it(self):
        s = self.machine()
        root = self.newer(s, fake=FAKE_BROKEN)
        r = s.budgit("ensure", root=root)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.build(s), "build-one", "the old copy is back")
        self.assertEqual(self.state(s)["version"], "0.13.2", "and recorded as the installed one")
        launches = s.wait_launches(2)
        self.assertEqual(len(launches), 2, "the old copy was started again")
        self.assertEqual(launches[1]["argv"][0], str(s.installed_exe()))
        new_pid = self.wait_holder(s)
        self.assertNotEqual(new_pid, self.old_pid)
        self.assertIn("not replaced", (s.bw / "ensure.log").read_text())
        self.assertEqual(s.budgit("status").returncode, 0)

    def test_a_version_that_failed_once_is_not_tried_again_on_the_next_session_start(self):
        s = self.machine()
        root = self.newer(s, fake=FAKE_BROKEN)
        s.budgit("ensure", root=root)
        s.wait_launches(2)
        pid = self.wait_holder(s)
        saved = self.saved_files(s)
        r = s.budgit("ensure", root=root)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.build(s), "build-one")
        self.assertEqual(len(s.launches()), 2, "no third start")
        self.assertTrue(alive(pid), "the restored Brote was left running")
        self.assertEqual(self.saved_files(s), saved)

    def test_turning_brote_on_again_keeps_the_failed_version_and_it_is_not_retried(self):
        s = self.machine()
        root = self.newer(s, fake=FAKE_BROKEN)
        s.budgit("ensure", root=root)
        s.wait_launches(2)
        self.wait_holder(s)
        self.assertEqual(self.state(s)["replaced_for"], "0.13.3")
        r = s.budgit("on", "--yes", root=root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state(s).get("replaced_for"), "0.13.3", "`on` keeps what a failed swap recorded")
        n = len(s.launches())
        r = s.budgit("ensure", root=root)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.build(s), "build-one", "that version is not tried again")
        self.assertEqual(len(s.launches()), n)

    def test_a_failed_swap_without_our_prev_does_not_start_the_copy_that_failed(self):
        import tempfile
        from types import SimpleNamespace
        from unittest import mock
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugin/scripts"))
        import floatctl
        with tempfile.TemporaryDirectory() as d:
            ctx = SimpleNamespace(bw=d, root=d)
            logs, launched = [], []
            with mock.patch.object(floatctl, "install"), \
                 mock.patch.object(floatctl, "write_private"), \
                 mock.patch.object(floatctl, "running_pid", return_value=None), \
                 mock.patch.object(floatctl, "installed_problems", return_value=["MISSING Brote.app.sha256"]), \
                 mock.patch.object(floatctl, "launch", side_effect=lambda *a, **k: launched.append(a)), \
                 mock.patch.object(floatctl, "log", side_effect=lambda c, t: logs.append(t)):
                ok = floatctl.swap_running(ctx, "0.13.3", "want", "have", {"version": "0.13.2"})
        self.assertFalse(ok)
        self.assertEqual(launched, [], "the broken new copy is not started")
        self.assertTrue(any("the old Brote did not start again" in t and "MISSING Brote.app.sha256" in t for t in logs), logs)

    def test_with_the_float_choice_off_nothing_is_replaced(self):
        s = self.machine()
        st = self.state(s)
        st["float"] = "off"
        (s.bw / "float.json").write_text(json.dumps(st))
        root = self.newer(s)
        r = s.budgit("ensure", root=root)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.build(s), "build-one")
        self.assertEqual(len(s.launches()), 1)
        self.assertTrue(alive(self.old_pid))
        self.assertEqual(self.saved_files(s), [])


if __name__ == "__main__":
    unittest.main()
