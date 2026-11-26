"""R-292 plan B: plugin/scripts/float-uninstall.sh removes the floating Brote with nothing but /bin/sh.

It is what the README tells someone to run when the plugin is already gone, so it must not need the plugin, python or a manifest.
It removes exactly the files this feature wrote, never follows a link out of its folders and never touches anything else.
"""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugin" / "scripts" / "float-uninstall.sh"
MAC = sys.platform == "darwin"

HOLD = '''#!/usr/bin/python3
import fcntl, os, time
lock = os.open(os.path.join(os.environ["HOME"], "Library/Application Support/BroteWindow/brote.lock"), os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(lock, fcntl.LOCK_EX)
os.ftruncate(lock, 0)
os.write(lock, (str(os.getpid()) + "\\n").encode())
time.sleep(120)
'''


def run(home, *args, env=None):
    e = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "en_US.UTF-8", **(env or {})}
    return subprocess.run(["/bin/sh", str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=60)


class Uninstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="float-unsh-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.bw = self.home / "Library/Application Support/BroteWindow"
        self.bw.mkdir(parents=True, mode=0o700)
        self.procs = []
        self.addCleanup(lambda: [p.kill() for p in self.procs])

    def make(self, *paths):
        for p in paths:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")

    def test_it_exists_runs_under_sh_and_says_what_it_does_without_arguments_needed(self):
        self.assertTrue(SCRIPT.is_file())
        r = run(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_home_that_never_had_the_window_still_gets_the_off_note_so_he_never_starts_by_himself(self):
        shutil.rmtree(self.bw)
        r = run(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((self.bw / "float.json").read_text())["float"], "off")
        self.assertEqual(os.listdir(self.bw), ["float.json"])

    def test_removes_exactly_its_own_files_and_the_folder_when_empty(self):
        mine = [self.bw / n for n in ("float.json", "brote.lock", "ensure.lock", "ensure.log", "events.log", "probe.log", "Brote.app.sha256",
                                      "Brote.app.sha256.new", "Brote.app/Contents/MacOS/Brote", "Brote.app.prev/x", ".stage-abc/y", "sessions/123.json")]
        mine += [self.home / "Library/Logs/BroteWindow/probe.log", self.home / "Library/Preferences/es.budgit.brote.float.plist",
                 self.home / "Library/Saved Application State/es.budgit.brote.float.savedState/data"]
        keep = [self.home / "Library/Logs/OtherApp/keep.log", self.home / "Library/Preferences/com.other.plist"]
        self.make(*mine, *keep)
        r = run(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for p in mine:
            if p.name != "float.json" or p.parent != self.bw:
                self.assertFalse(p.exists(), str(p))
        self.assertEqual(os.listdir(self.bw), ["float.json"], "the one file left remembers that he is off, so an update does not bring him back")
        self.assertIn('"float": "off"', (self.bw / "float.json").read_text())
        self.assertEqual(stat.S_IMODE((self.bw / "float.json").stat().st_mode), 0o600)
        for p in keep:
            self.assertEqual(p.read_text(), "x")
        self.assertEqual(run(self.home).returncode, 0, "again is fine")

    def test_a_file_that_is_not_its_own_keeps_the_folder(self):
        self.make(self.bw / "float.json", self.bw / "precious.txt")
        run(self.home)
        self.assertIn('"float": "off"', (self.bw / "float.json").read_text())
        self.assertEqual((self.bw / "precious.txt").read_text(), "x")

    def test_it_never_follows_a_link_out(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "important.txt").write_text("keep")
        os.symlink(outside, self.bw / "Brote.app.prev")
        os.symlink(outside, self.bw / "sessions")
        (self.home / "Library/Logs").mkdir(parents=True)
        os.symlink(outside, self.home / "Library/Logs/BroteWindow")
        run(self.home)
        self.assertEqual((outside / "important.txt").read_text(), "keep")
        self.assertFalse((self.bw / "Brote.app.prev").is_symlink())

    def test_a_linked_support_folder_is_left_alone(self):
        shutil.rmtree(self.bw)
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "float.json").write_text("keep")
        os.symlink(outside, self.bw)
        r = run(self.home)
        self.assertEqual((outside / "float.json").read_text(), "keep")
        self.assertTrue(self.bw.is_symlink())
        self.assertIn("link", (r.stdout + r.stderr).lower())

    @unittest.skipUnless(MAC, "ps output shape is macOS's")
    def test_the_running_app_is_quit_by_its_lock_and_a_bystander_is_not(self):
        exe = self.bw / "Brote.app/Contents/MacOS/Brote"
        exe.parent.mkdir(parents=True)
        exe.write_text(HOLD)
        exe.chmod(0o755)
        app = subprocess.Popen([str(exe)], env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
        self.procs.append(app)
        by = subprocess.Popen(["sleep", "60"])
        self.procs.append(by)
        end = time.time() + 5
        while time.time() < end and not (self.bw / "brote.lock").exists():
            time.sleep(0.05)
        time.sleep(0.4)
        r = run(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        time.sleep(0.5)
        self.assertIsNotNone(app.poll(), "the app was quit")
        self.assertIsNone(by.poll())

    @unittest.skipUnless(MAC, "ps and lsof output shape is macOS's")
    def test_a_process_that_only_names_the_app_in_its_arguments_is_not_quit(self):
        exe = self.bw / "Brote.app/Contents/MacOS/Brote"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        by = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(exe)])
        self.procs.append(by)
        (self.bw / "brote.lock").write_text(f"{by.pid}\n")             # nobody holds the lock: the app is not running
        time.sleep(0.3)
        r = run(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        time.sleep(0.4)
        self.assertIsNone(by.poll(), "a reused pid in a stale lock file must never be signalled")

    def test_the_words_follow_the_language(self):
        self.assertNotEqual(run(self.home, env={"LANG": "es_ES.UTF-8"}).stdout, run(self.home, env={"LANG": "en_US.UTF-8"}).stdout)

    def test_a_bad_home_is_refused(self):
        r = run("relative/home")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
