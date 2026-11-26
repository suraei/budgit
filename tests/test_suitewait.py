"""R-569 and R-869: `budgit suite --wait [--rc] -- CMD` and the keep-awake. Fakes only: no real suite, no real sleep."""
import os
import sys
import tempfile
import threading
import unittest

from helpers import ROOT
import suitewait


class FakeProc:
    def __init__(self, args):
        self.args, self.pid, self.terminated = args, 4242, False

    def terminate(self):
        self.terminated = True

    def wait(self):
        return 0


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = {"BUDGIT_SUITE_LOCK_DIR": self.dir}

    def test_second_call_waits_without_a_busy_loop_and_the_order_is_kept(self):
        ran, first_in, second_naps = [], threading.Event(), []
        release_first = threading.Event()

        def runner_a(cmd, cwd):
            ran.append("a")
            first_in.set()
            release_first.wait(10)
            return 0

        def runner_b(cmd, cwd):
            ran.append("b")
            return 0

        def nap_b(secs):                                   # the second caller's sleep: it must be a real pause, a few times at most
            second_naps.append(secs)
            if len(second_naps) >= 3:
                release_first.set()
            threading.Event().wait(0.01)

        out_a, out_b = [], []
        ta = threading.Thread(target=lambda: suitewait.run(["x"], "proj", env=self.env, busy_fn=lambda: (False, "free"), sleep=lambda s: None, out=out_a.append, runner=runner_a))
        ta.start()
        self.assertTrue(first_in.wait(10))
        tb = threading.Thread(target=lambda: suitewait.run(["x"], "proj", env=self.env, busy_fn=lambda: (False, "free"), sleep=nap_b, out=out_b.append, runner=runner_b))
        tb.start()
        tb.join(10)
        ta.join(10)
        self.assertEqual(ran, ["a", "b"])
        self.assertTrue(second_naps and all(s >= suitewait.POLL for s in second_naps))
        self.assertTrue(any("place" in line for line in out_b))
        self.assertTrue(any("lock acquired" in line for line in out_b))

    def test_order_is_priority_then_rc_then_oldest(self):
        base = self.dir
        suitewait.join(base, "t1", 1, False, "p", 10)
        suitewait.join(base, "t2", 1, True, "p", 30)
        suitewait.join(base, "t3", 0, False, "p", 40)
        suitewait.join(base, "t4", 1, False, "p", 5)
        self.assertEqual(suitewait.waiting(base), ["t3", "t2", "t4", "t1"])

    def test_busy_is_checked_before_any_lock_and_no_lock_is_held_while_waiting(self):
        readings = iter([(True, "median CPU 95 %"), (True, "median CPU 95 %"), (False, "free")])
        seen = []

        def busy():
            seen.append(os.path.exists(suitewait.lock_paths("proj", env=self.env)[0]))
            return next(readings)
        code = suitewait.run(["x"], "proj", env=self.env, busy_fn=busy, sleep=lambda s: None, out=lambda s: None, runner=lambda c, d: 7)
        self.assertEqual(code, 7)
        self.assertEqual(seen, [False, False, False])       # no lock file even existed while it waited for not busy

    def test_replay_1356_another_projects_suite_runs_and_this_one_is_admitted_at_once(self):
        other = suitewait.try_lock(suitewait.lock_paths("other", env=self.env)[0])      # another project's suite is running
        self.assertIsNotNone(other)
        naps = []
        try:
            code = suitewait.run(["x"], "proj", env=self.env, busy_fn=lambda: (False, "free"), sleep=naps.append,
                                 out=lambda s: None, runner=lambda c, d: 3)
        finally:
            suitewait.unlock(other)
        self.assertEqual(code, 3)
        self.assertEqual(naps, [])                          # no machine-wide lock exists: it did not wait at all
        self.assertEqual(len(suitewait.lock_paths("proj", env=self.env)), 1)

    def test_waiting_prints_the_holder_with_pid_command_age_and_eta(self):
        proj = tempfile.mkdtemp()
        os.makedirs(os.path.join(proj, "tests"))
        with open(os.path.join(proj, "tests", ".durations.json"), "w") as f:
            f.write('{"a": 30.0, "b": 90.0}')
        path = suitewait.lock_paths("proj", env=self.env)[0]
        suitewait.write_holder(path, ["python3", "-m", "unittest", "discover", "-s", "tests"], 1000)
        line = suitewait.holder_line(path, 1042, project_dir=proj, cpu_count=lambda: 5)
        self.assertIn("pid %d" % os.getpid(), line)
        self.assertIn("python3 -m unittest discover", line)
        self.assertIn("running 42 s", line)
        self.assertIn("ETA about 30 s", line)                # 120 s of tests / (5 - 1) workers
        suitewait.write_holder(path, ["python3", "-m", "unittest", "discover", "-p", "test_x.py"], 1000)
        self.assertIn("ETA unknown", suitewait.holder_line(path, 1001, project_dir=proj))
        self.assertIn("holder unknown", suitewait.holder_line(path + "-none", 1001))

    # ---- R-569 round 3: the state folder and its files are private, never followed, never shared

    def test_normal_success_path_creates_a_private_dir_and_runs(self):
        fresh = os.path.join(self.dir, "state")
        env = {"BUDGIT_SUITE_LOCK_DIR": fresh}
        code = suitewait.run(["x"], "proj", env=env, busy_fn=lambda: (False, "free"), sleep=lambda s: None, out=lambda s: None, runner=lambda c, d: 0)
        self.assertEqual(code, 0)
        self.assertEqual(os.stat(fresh).st_mode & 0o777, 0o700)
        holder = suitewait.read_json(os.path.join(fresh, "budgit-suite-proj-full.lock.holder"))
        self.assertEqual(holder["pid"], os.getpid())
        self.assertNotIn("cwd", holder)

    def test_a_planted_symlink_is_refused_at_the_holder_lock_and_ticket_paths_and_the_target_is_untouched(self):
        victim = os.path.join(self.dir, "victim.txt")
        with open(victim, "w") as f:
            f.write("keep")
        lock = suitewait.lock_paths("proj", env=self.env)[0]
        os.symlink(victim, lock)                                              # the lock path
        with self.assertRaises(suitewait.StateDirError):
            suitewait.try_lock(lock)
        os.remove(lock)
        os.symlink(victim, lock + ".holder")                                  # the holder path: replaced, never written through
        suitewait.write_holder(lock, ["x"], 1)
        self.assertFalse(os.path.islink(lock + ".holder"))
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")
        base = os.path.join(self.dir, "q")
        suitewait.join(base, "t0", 1, False, "p", 1)
        os.symlink(victim, os.path.join(base, "budgit-suite-queue", "t1"))     # a ticket path
        with self.assertRaises(suitewait.StateDirError):
            suitewait.read_json(os.path.join(base, "budgit-suite-queue", "t1"))
        self.assertEqual(suitewait.waiting(base), ["t0"])                     # the planted ticket never counts
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")
        elsewhere = os.path.join(self.dir, "elsewhere")
        os.makedirs(elsewhere)
        link = os.path.join(self.dir, "linkdir")
        os.symlink(elsewhere, link)
        with self.assertRaises(suitewait.StateDirError):                      # a symlinked state folder
            suitewait.ensure_private_dir(link)

    @unittest.skipUnless(hasattr(os, "getuid"), "no uid on this platform")
    def test_a_dir_owned_by_someone_else_or_open_to_others_is_refused(self):
        loose = os.path.join(self.dir, "loose")
        os.makedirs(loose)
        os.chmod(loose, 0o755)
        with self.assertRaises(suitewait.StateDirError):
            suitewait.ensure_private_dir(loose)
        real = os.getuid
        os.getuid = lambda: real() + 1                                        # a different user: the dir is now foreign
        self.addCleanup(setattr, os, "getuid", real)
        with self.assertRaises(suitewait.StateDirError):
            suitewait.ensure_private_dir(self.dir)

    def test_corrupt_json_is_skipped_with_one_notice(self):
        notes = []
        real = suitewait._note
        suitewait._note = notes.append
        self.addCleanup(setattr, suitewait, "_note", real)
        base = os.path.join(self.dir, "q2")
        suitewait.join(base, "good", 1, False, "p", 1)
        with open(os.path.join(base, "budgit-suite-queue", "bad"), "w") as f:
            f.write("{not json")
        self.assertEqual(suitewait.waiting(base), ["good"])
        self.assertEqual(len(notes), 1)
        self.assertIn("unreadable", notes[0])

    def test_a_holder_cwd_pointing_elsewhere_is_ignored_for_the_eta(self):
        mine, other = tempfile.mkdtemp(), tempfile.mkdtemp()
        for d, secs in ((mine, '{"a": 40.0}'), (other, '{"a": 4000.0}')):
            os.makedirs(os.path.join(d, "tests"))
            with open(os.path.join(d, "tests", ".durations.json"), "w") as f:
                f.write(secs)
        path = suitewait.lock_paths("proj", env=self.env)[0]
        suitewait.write_atomic(path + ".holder", {"pid": os.getpid(), "cmd": "python3 -m unittest discover", "cwd": other, "since": 1})
        line = suitewait.holder_line(path, 2, project_dir=mine, cpu_count=lambda: 2)
        self.assertIn("ETA about 40 s", line)                                 # this project's file, not the planted path

    def test_the_command_never_runs_with_shell_true(self):
        seen = []

        class P:
            pid = 1

            def wait(self):
                return 0
        for plat in ("darwin", "linux", "win32"):
            suitewait.run_awake("echo hi", shell=True, plat=plat, which=lambda n: None, popen=lambda a, **k: seen.append((a, k)) or P(), out=lambda s: None)
        self.assertTrue(all(isinstance(a, list) and not k.get("shell") for a, k in seen))
        self.assertEqual(seen[2][0][:2], ["cmd", "/c"])

    def test_the_suite_command_parses_wait_and_the_double_dash(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("budgit_cli", os.path.join(ROOT, "plugin", "bin", "budgit"))
        spec = importlib.util.spec_from_loader("budgit_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        args = mod.build_parser().parse_args(["suite", "--wait", "--rc", "--", "python3", "-m", "unittest", "-v"])
        self.assertEqual((args.wait, args.rc, args.command), (True, True, ["python3", "-m", "unittest", "-v"]))
        plain = mod.build_parser().parse_args(["suite", "--wait"])
        self.assertEqual(plain.command, [])
        # cmd_suite names the lock from the git common dir (the main project), never from a worktree folder
        import subprocess
        common = subprocess.run(["git", "-C", ROOT, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
        if not common:
            self.skipTest("not a git checkout")
        common = os.path.realpath(common)
        expected = os.path.basename(os.path.dirname(common)) if os.path.basename(common) == ".git" else os.path.basename(common)
        seen = {}
        real = suitewait.run
        suitewait.run = lambda argv, name, **kw: seen.update(argv=argv, name=name, **kw) or 0
        self.addCleanup(setattr, suitewait, "run", real)
        args.project = ROOT
        self.assertEqual(mod.cmd_suite(args), 0)
        self.assertEqual(seen["name"], expected)
        self.assertEqual(seen["argv"], ["python3", "-m", "unittest", "-v"])

    def test_the_launch_busy_window_is_15_seconds_and_is_what_run_asks_for(self):
        self.assertEqual(suitewait.BUSY_WINDOW, 15)
        import admit
        seen = []
        real = admit.is_busy
        admit.is_busy = lambda **kw: seen.append(kw) or (False, "free")
        self.addCleanup(setattr, admit, "is_busy", real)
        suitewait.run(["x"], "proj", env=self.env, sleep=lambda s: None, out=lambda s: None, runner=lambda c, d: 0)
        self.assertEqual(seen, [{"window": 15}])

    def test_nice_unless_top_priority(self):
        which = lambda n: "/usr/bin/nice"
        self.assertEqual(suitewait.niced(["t"], 1, "darwin", which), ["/usr/bin/nice", "-n", "10", "t"])
        self.assertEqual(suitewait.niced(["t"], 0, "darwin", which), ["t"])
        self.assertEqual(suitewait.niced(["t"], 1, "win32", which), ["t"])

    def test_windows_lock_branch_uses_msvcrt(self):
        calls = []

        class FakeMs:
            LK_NBLCK, LK_UNLCK = 2, 0

            @staticmethod
            def locking(fd, mode, n):
                calls.append(mode)
        path = os.path.join(self.dir, "w.lock")
        fd = suitewait.try_lock(path, "win32", msvcrt_mod=FakeMs)
        self.assertIsNotNone(fd)
        suitewait.unlock(fd, "win32", msvcrt_mod=FakeMs)
        self.assertEqual(calls, [2, 0])

    def test_lock_dir_per_platform(self):
        self.assertEqual(suitewait.lock_dir("linux", {"BUDGIT_SUITE_LOCK_DIR": self.dir}), self.dir)
        uid = os.getuid() if hasattr(os, "getuid") else (os.environ.get("USERNAME") or "user")
        self.assertEqual(suitewait.lock_dir("darwin", {"TMPDIR": self.dir}), os.path.join(self.dir, "budgit-%s" % uid))
        self.assertEqual(suitewait.lock_dir("linux", {"TMPDIR": self.dir}), os.path.join(tempfile.gettempdir(), "budgit-%s" % uid))


class KeepAwakeTest(unittest.TestCase):
    def test_caffeinate_gets_the_child_pid_on_macos(self):
        started = []
        ka = suitewait.KeepAwake("darwin", which=lambda n: "/usr/bin/" + n, popen=lambda a: started.append(a) or FakeProc(a))
        ka.start(777)
        self.assertEqual(started, [["caffeinate", "-i", "-w", "777"]])
        self.assertIsNone(ka.notice)
        ka.stop()

    def test_a_missing_inhibitor_gives_one_line_and_the_command_still_runs(self):
        lines = []
        code = suitewait.run_awake(["tool"], plat="darwin", which=lambda n: None, popen=lambda *a, **k: FakeProc(a), out=lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 1)
        self.assertIn("no keep-awake tool", lines[0])

    def test_linux_wraps_the_command_in_systemd_inhibit(self):
        ka = suitewait.KeepAwake("linux", which=lambda n: "/usr/bin/" + n)
        argv = ka.wrap(["tool", "-x"])
        self.assertEqual(argv[0], "systemd-inhibit")
        self.assertEqual(argv[-2:], ["tool", "-x"])
        ka.start(1)
        self.assertIsNone(ka.notice)
        bare = suitewait.KeepAwake("linux", which=lambda n: None)
        self.assertEqual(bare.wrap(["tool"]), ["tool"])
        bare.start(1)
        self.assertIn("no keep-awake tool", bare.notice)

    def test_windows_sets_and_clears_the_execution_state(self):
        calls = []

        class Api:
            @staticmethod
            def SetThreadExecutionState(flags):
                calls.append(flags)
        ka = suitewait.KeepAwake("win32", win_api=Api)
        ka.start(1)
        ka.stop()
        self.assertEqual(calls, [suitewait.ES_CONTINUOUS | suitewait.ES_SYSTEM_REQUIRED, suitewait.ES_CONTINUOUS])
        self.assertIsNone(ka.notice)


if __name__ == "__main__":
    unittest.main()
