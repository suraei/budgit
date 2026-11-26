"""R-292 plan B, step 2: `budgit float` (on / off / stop / status / ensure / uninstall) with a fake bundle and a temp HOME.

Contract under test (written before the code):
  - A made-up Brote.app (a script, ad-hoc signed, listed in a MANIFEST.sha256 written by plugin/scripts/floatmanifest.py) sits in
    <plugin root>/float/. The plugin root is a copy of plugin/ under <config>/plugins/cache/<marketplace>/<name>/<version>/ and is
    registered in <config>/plugins/installed_plugins.json, because only the installed version is ever trusted.
  - Everything the command writes is under $HOME/Library (BroteWindow, Logs, Preferences, Saved Application State); the tests give it
    a HOME of its own, so nothing here can touch a real install.
  - Exit codes: 0 done, 3 "needs your yes" (nothing written), 4 refused (the reason is printed). `ensure` is a SessionStart hook:
    it prints NOTHING on stdout (a hook's stdout would enter the chat), always exits 0, and writes why it did nothing to ensure.log.
  - The app is launched by its own binary, never `open`, with an empty environment but PATH, HOME and CLAUDE_CONFIG_DIR, stdio on
    /dev/null and no other descriptor open.
Mac only: the command refuses elsewhere and the tests are skipped.
"""
import json
import os
import resource
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
FLOAT = ROOT / "tools" / "brote" / "float"
MAC = sys.platform == "darwin" and shutil.which("codesign") is not None
sys.path.insert(0, str(FLOAT))
sys.path.insert(0, str(ROOT / "plugin" / "scripts"))

FAKE = r'''#!/usr/bin/python3
import fcntl, json, os, sys, time
home = os.environ["HOME"]
null = os.stat("/dev/null").st_rdev
rec = {"argv": sys.argv, "env": dict(os.environ), "pid": os.getpid(),
       "fds": sorted(int(x) for x in os.listdir("/dev/fd") if x.isdigit()),
       "stdio_null": [os.fstat(i).st_rdev == null for i in (0, 1, 2)]}
with open(os.path.join(home, "launches.jsonl"), "a") as f:
    f.write(json.dumps(rec) + "\n")
if os.path.exists(os.path.join(home, "fake-hold")):
    lock = os.open(os.path.join(home, "Library/Application Support/BroteWindow/brote.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(lock, 0)
    os.write(lock, (str(os.getpid()) + "\n").encode())
    time.sleep(120)
'''


def cpu_since(c0):
    """CPU seconds the finished child processes (and the ps/lsof they ran) used since c0: the code's own work, not how busy the machine was.
    On a loaded machine the same run takes several times the wall time and about the same CPU time (R-1255 follow-up)."""
    c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (c1.ru_utime - c0.ru_utime) + (c1.ru_stime - c0.ru_stime)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class Setup:
    """One fake machine: HOME, config folder, the installed plugin copy with a fake signed bundle."""

    def __init__(self, tc, build_id="build-one", name="studio@studio-hub", version="0.13.2"):
        self.tc = tc
        self.tmp = Path(tempfile.mkdtemp(prefix="float-cli-")).resolve()
        tc.addCleanup(self.cleanup)
        self.home = self.tmp / "home"
        self.cfg = self.tmp / "cfg"
        self.home.mkdir()
        (self.home / "Library").mkdir()
        self.name, self.version = name, version
        mkt, plug = name.split("@")[1], name.split("@")[0]
        self.root = self.cfg / "plugins" / "cache" / mkt / plug / version
        self.root.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "plugin", self.root, ignore=shutil.ignore_patterns("float", "__pycache__"))
        self.make_bundle(build_id)
        self.register(self.root)
        self.bw = self.home / "Library/Application Support/BroteWindow"
        self.pids = []

    def cleanup(self):
        for pid in self.pids + self.launch_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        # a launch still starting up has not written launches.jsonl yet: also stop whatever runs from this fake machine's folder (R-318)
        for _ in range(20):
            mine = [int(l.split(None, 1)[0]) for l in sh(["ps", "-axo", "pid=,args="]).stdout.splitlines() if str(self.tmp) in l]
            if not mine:
                break
            for pid in mine:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            time.sleep(0.05)
        shutil.rmtree(self.tmp, True)

    def launch_pids(self):
        f = self.home / "launches.jsonl"
        return [json.loads(l)["pid"] for l in f.read_text().splitlines()] if f.exists() else []

    def launches(self):
        f = self.home / "launches.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def make_bundle(self, build_id, into=None):
        import floatmanifest as manifest
        fd = Path(into) if into else self.root / "float"
        shutil.rmtree(fd, True)
        exe = fd / "Brote.app/Contents/MacOS/Brote"
        exe.parent.mkdir(parents=True)
        exe.write_text(FAKE + f"# {build_id}\n")
        exe.chmod(0o755)
        (fd / "Brote.app/Contents/Resources").mkdir()
        (fd / "Brote.app/Contents/Resources/build.txt").write_text(build_id + "\n")
        shutil.copy(FLOAT / "Info.plist", fd / "Brote.app/Contents/Info.plist")
        (fd / "BUILD-INFO.txt").write_text("fake\n")
        r = sh(["codesign", "--force", "--sign", "-", "--timestamp=none", str(fd / "Brote.app")])
        assert r.returncode == 0, r.stderr
        (fd / "src").mkdir()
        for n in manifest.SRC_FILES:
            (fd / "src" / n).write_text(n)
        manifest.write(str(fd))
        return fd

    def register(self, root, version=None, name=None):
        p = self.cfg / "plugins" / "installed_plugins.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {"scope": "user", "installPath": str(root), "version": version or self.version}
        p.write_text(json.dumps({"version": 2, "plugins": {name or self.name: [entry]}}))

    def budgit(self, *args, root=None, extra_env=None, timeout=60):
        env = {"PATH": "/usr/bin:/bin:/usr/sbin", "HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.cfg), **(extra_env or {})}
        t0 = time.time()
        c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        r = sh([sys.executable, str(Path(root or self.root) / "scripts/floatctl.py"), *args], env=env, timeout=timeout)
        r.elapsed = time.time() - t0
        r.cpu = cpu_since(c0)
        return r

    def installed_exe(self):
        return self.bw / "Brote.app/Contents/MacOS/Brote"

    def wait_launches(self, n, secs=8):
        end = time.time() + secs
        while time.time() < end and len(self.launches()) < n:
            time.sleep(0.05)
        return self.launches()


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class On(unittest.TestCase):
    def test_on_needs_no_yes_any_more_because_turning_on_is_the_default(self):
        s = Setup(self)
        r = s.budgit("on")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "on")
        self.assertEqual(len(s.wait_launches(1)), 1)

    def test_with_a_yes_it_installs_launches_once_and_returns_fast(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(r.elapsed, 5)
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "on")
        self.assertEqual(stat.S_IMODE((s.bw / "float.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(s.bw.stat().st_mode), 0o700)
        self.assertEqual(s.installed_exe().read_bytes(), (s.root / "float/Brote.app/Contents/MacOS/Brote").read_bytes())
        (rec,) = s.wait_launches(1)
        self.assertEqual(rec["argv"], [str(s.installed_exe()), "--plugin-root", str(s.root)], "launched by its own binary, never `open`")
        shim = {"LC_CTYPE", "SDKROOT", "CPATH", "LIBRARY_PATH", "MANPATH", "__CF_USER_TEXT_ENCODING"}   # added by /usr/bin/python3 itself, which runs the fake
        self.assertEqual(set(rec["env"]) - shim, {"PATH", "HOME", "CLAUDE_CONFIG_DIR"})
        self.assertEqual((rec["env"]["PATH"], rec["env"]["HOME"], rec["env"]["CLAUDE_CONFIG_DIR"]), ("/usr/bin:/bin", str(s.home), str(s.cfg)))
        self.assertTrue(all(rec["stdio_null"]), "stdin, stdout and stderr are /dev/null")
        self.assertEqual([fd for fd in rec["fds"] if fd > 3], [], "no other descriptor leaks into the app")

    def test_a_quarantined_bundle_is_refused_and_the_attribute_is_left_alone(self):
        s = Setup(self)
        app = s.root / "float/Brote.app"
        self.assertEqual(sh(["xattr", "-w", "com.apple.quarantine", "0081;00000000;Safari;", str(app)]).returncode, 0)
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("reinstall", (r.stdout + r.stderr).lower())
        self.assertIn("com.apple.quarantine", sh(["xattr", str(app)]).stdout, "never stripped")
        self.assertFalse(s.bw.exists())
        self.assertEqual(s.launches(), [])

    def test_a_tampered_bundle_is_refused(self):
        s = Setup(self)
        exe = s.root / "float/Brote.app/Contents/MacOS/Brote"
        exe.write_text(exe.read_text() + "# one more byte\n")
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("MISMATCH", r.stdout + r.stderr)
        self.assertFalse(s.bw.exists())

    def test_only_the_installed_version_is_trusted(self):
        s = Setup(self)
        other = s.cfg / "plugins" / "marketplaces" / "studio-hub" / "plugin"
        other.parent.mkdir(parents=True)
        shutil.copytree(s.root, other)
        r = s.budgit("on", "--yes", root=other)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("installed", (r.stdout + r.stderr).lower())
        self.assertFalse(s.bw.exists())
        self.assertEqual(s.launches(), [])

    def test_an_older_version_than_the_installed_one_is_refused(self):
        s = Setup(self)
        newer = s.cfg / "plugins/cache/studio-hub/studio/0.13.3"
        shutil.copytree(s.root, newer)
        s.register(newer, version="0.13.3")
        r = s.budgit("on", "--yes", root=s.root)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertFalse(s.bw.exists())

    def test_the_public_plugin_name_works_too(self):
        s = Setup(self, name="budgit@budgit-hub")
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_symlinked_support_folder_is_refused_and_the_target_untouched(self):
        s = Setup(self)
        target = s.tmp / "elsewhere"
        target.mkdir()
        (s.bw.parent).mkdir(parents=True)
        os.symlink(target, s.bw)
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(list(target.iterdir()), [])


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class Ensure(unittest.TestCase):
    def on(self, s):
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s.wait_launches(1)
        return r

    def test_it_does_nothing_and_says_nothing_while_float_is_off(self):
        s = Setup(self)
        s.budgit("off")
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(sorted(x for x in os.listdir(s.bw) if x != "ensure.lock"), ["float.json"])
        self.assertEqual(s.launches(), [])

    def test_when_on_it_launches_silently_and_quickly(self):
        s = Setup(self)
        self.on(s)
        shutil.rmtree(s.home / "launches.jsonl", True)
        (s.home / "launches.jsonl").unlink(missing_ok=True)
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertLess(r.elapsed, 3)
        self.assertEqual(len(s.wait_launches(1)), 1)

    def test_a_running_app_is_never_started_twice(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        self.on(s)
        time.sleep(0.5)                        # the fake now holds brote.lock
        for _ in range(3):
            r = s.budgit("ensure")
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        time.sleep(0.5)
        self.assertEqual(len(s.launches()), 1)

    def test_a_new_plugin_build_replaces_the_app_when_it_is_not_running_and_keeps_the_old_one(self):
        s = Setup(self, build_id="build-one")
        self.on(s)
        old = s.installed_exe().read_bytes()
        s.make_bundle("build-two")
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(s.installed_exe().read_bytes(), (s.root / "float/Brote.app/Contents/MacOS/Brote").read_bytes())
        self.assertEqual((s.bw / "Brote.app.prev/Contents/MacOS/Brote").read_bytes(), old)
        self.assertEqual((s.bw / "Brote.app/Contents/Resources/build.txt").read_text().strip(), "build-two")

    def test_a_running_older_app_is_left_alone(self):
        s = Setup(self, build_id="build-one")
        (s.home / "fake-hold").write_text("")
        self.on(s)
        time.sleep(0.5)
        old = s.installed_exe().read_bytes()
        s.make_bundle("build-two")
        s.budgit("ensure")
        self.assertEqual(s.installed_exe().read_bytes(), old)
        self.assertFalse((s.bw / "Brote.app.prev").exists())

    def test_a_tampered_plugin_bundle_changes_nothing_and_is_logged(self):
        s = Setup(self, build_id="build-one")
        self.on(s)
        old = s.installed_exe().read_bytes()
        s.make_bundle("build-two")
        exe = s.root / "float/Brote.app/Contents/MacOS/Brote"
        exe.write_text(exe.read_text() + "# tampered\n")
        n = len(s.launches())
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(s.installed_exe().read_bytes(), old)
        self.assertEqual(len(s.launches()), n, "a tampered bundle is never launched")
        self.assertIn("MISMATCH", (s.bw / "ensure.log").read_text())
        self.assertEqual(stat.S_IMODE((s.bw / "ensure.log").stat().st_mode), 0o600)

    def test_a_tampered_installed_copy_is_not_launched(self):
        s = Setup(self)
        self.on(s)
        s.installed_exe().write_text(s.installed_exe().read_text() + "# tampered\n")
        n = len(s.launches())
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(len(s.launches()), n)
        self.assertIn("MISMATCH", (s.bw / "ensure.log").read_text())

    def test_a_crash_between_the_two_renames_is_repaired(self):
        s = Setup(self)
        self.on(s)
        os.rename(s.bw / "Brote.app", s.bw / "Brote.app.prev")      # what a crash mid-swap leaves
        (s.home / "launches.jsonl").unlink()
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertTrue(s.installed_exe().exists())
        self.assertEqual(len(s.wait_launches(1)), 1)

    def test_a_quarantined_plugin_bundle_is_logged_and_not_installed(self):
        s = Setup(self)
        sh(["xattr", "-w", "com.apple.quarantine", "0081;00000000;Safari;", str(s.root / "float/Brote.app")])
        s.bw.mkdir(parents=True, mode=0o700)
        (s.bw / "float.json").write_text('{"float": "on"}')
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("quarantine", (s.bw / "ensure.log").read_text().lower())
        self.assertFalse(s.installed_exe().exists())
        self.assertEqual(s.launches(), [])

    def test_a_dev_copy_outside_the_plugin_cache_is_not_trusted(self):
        s = Setup(self)
        self.on(s)
        dev = s.tmp / "dev-plugin"
        shutil.copytree(s.root, dev)
        (s.home / "launches.jsonl").unlink()
        r = s.budgit("ensure", root=dev)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(s.launches(), [])
        self.assertIn("installed", (s.bw / "ensure.log").read_text().lower())


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class Sessions(unittest.TestCase):
    """A live Claude session is a file sessions/<pid>.json {pid, start (process start, epoch s), sid}; the app counts the live ones."""

    def proc(self):
        p = subprocess.Popen(["sleep", "60"])
        self.addCleanup(p.kill)
        return p

    def hook(self, s, pid, sid="abcdef0123456789xyz", *args):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg)}
        r = subprocess.run([sys.executable, str(s.root / "scripts/floatctl.py"), *args], input=json.dumps({"session_id": sid}),
                           capture_output=True, text=True, env=env, timeout=30)
        return r

    def test_ensure_registers_the_session_with_its_process_start_time(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        p = self.proc()
        r = self.hook(s, p.pid, "abcdef0123456789xyz", "ensure", "--claude-pid", str(p.pid))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        f = s.bw / "sessions" / f"{p.pid}.json"
        d = json.loads(f.read_text())
        self.assertEqual((d["pid"], d["sid"]), (p.pid, "abcdef012345"))
        out = sh(["ps", "-o", "lstart=", "-p", str(p.pid)], env={"LC_ALL": "C"}).stdout.strip()
        import calendar
        self.assertAlmostEqual(d["start"], time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")), delta=2)
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode), 0o700)

    def test_turning_it_on_from_inside_a_session_registers_that_session_so_he_stays_up(self):
        s = Setup(self)
        p = self.proc()
        r = s.budgit("on", "--yes", "--claude-pid", str(p.pid), extra_env={"CLAUDE_CODE_SESSION_ID": "0123456789abcdef"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads((s.bw / "sessions" / f"{p.pid}.json").read_text())
        self.assertEqual((d["pid"], d["sid"]), (p.pid, "0123456789ab"))

    def named(self, s):
        """the session files a caller named (not the on-<pid> ones made for the chats that happen to be open on this machine)"""
        d = s.bw / "sessions"
        return [n for n in (os.listdir(d) if d.exists() else []) if not json.loads((d / n).read_text())["sid"].startswith("on-")]

    def test_turning_it_on_from_a_plain_terminal_registers_nothing_it_was_not_told_about(self):
        s = Setup(self)
        p = self.proc()
        s.budgit("on", "--yes", "--claude-pid", str(p.pid))
        self.assertEqual(self.named(s), [])

    def hook_in(self, s, pid, cwd, sid="abcdef0123456789xyz"):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg)}
        return subprocess.run([sys.executable, str(s.root / "scripts/floatctl.py"), "ensure", "--claude-pid", str(pid)],
                              input=json.dumps({"session_id": sid, "cwd": str(cwd)}), capture_output=True, text=True, env=env, timeout=30)

    def test_the_session_file_names_the_budgit_project_the_session_works_in(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        proj = s.tmp / "work" / "app"
        (proj / ".budgit").mkdir(parents=True)
        (proj / ".budgit" / "ledger.json").write_text("{}")
        old = s.tmp / "work" / "old"
        (old / ".studio").mkdir(parents=True)
        (old / ".studio" / "ledger.json").write_text("{}")
        (proj / "src" / "deep").mkdir(parents=True)
        a, b, c = self.proc(), self.proc(), self.proc()
        self.hook_in(s, a.pid, proj / "src" / "deep")
        self.hook_in(s, b.pid, old)
        self.hook_in(s, c.pid, s.tmp / "work")
        read = lambda p: json.loads((s.bw / "sessions" / f"{p.pid}.json").read_text())
        self.assertEqual(read(a).get("project"), str(proj), "found from a folder inside it")
        self.assertEqual(read(b).get("project"), str(old), "the older .studio folder name counts too")
        self.assertNotIn("project", read(c), "no ledger here: no project")

    def test_the_home_folder_and_what_is_above_it_are_never_a_project(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        (s.home / ".budgit").mkdir()
        (s.home / ".budgit" / "ledger.json").write_text("{}")
        a = self.proc()
        self.hook_in(s, a.pid, s.home)
        self.assertNotIn("project", json.loads((s.bw / "sessions" / f"{a.pid}.json").read_text()))

    def test_a_session_end_removes_only_its_own_file(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        a, b = self.proc(), self.proc()
        for p in (a, b):
            self.hook(s, p.pid, "abcdef0123456789xyz", "ensure", "--claude-pid", str(p.pid))
        r = self.hook(s, a.pid, "abcdef0123456789xyz", "session-end", "--claude-pid", str(a.pid))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertFalse((s.bw / "sessions" / f"{a.pid}.json").exists())
        self.assertTrue((s.bw / "sessions" / f"{b.pid}.json").exists())

    def test_nothing_is_registered_while_float_is_off(self):
        s = Setup(self)
        s.budgit("off")
        p = self.proc()
        self.hook(s, p.pid, "abcdef0123456789xyz", "ensure", "--claude-pid", str(p.pid))
        self.assertEqual(sorted(x for x in os.listdir(s.bw) if x != "ensure.lock"), ["float.json"])

    def test_the_session_process_is_found_by_walking_up_past_shells_and_interpreters(self):
        import floatctl as fp
        self.assertEqual(fp.pick_session_pid([("zsh", 5), ("python3", 4), ("2.1.308", 3), ("Claude", 2)]), 3)
        self.assertEqual(fp.pick_session_pid([("sh", 5), ("claude", 4)]), 4)
        self.assertIsNone(fp.pick_session_pid([("sh", 5), ("zsh", 4)]))

    def test_a_bad_session_id_or_pid_writes_nothing(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        p = self.proc()
        for sid in ("../../etc", "", "a b"):
            self.hook(s, p.pid, sid, "ensure", "--claude-pid", str(p.pid))
        self.hook(s, p.pid, "abcdef", "ensure", "--claude-pid", "1")
        self.hook(s, p.pid, "abcdef", "ensure", "--claude-pid", "../x")
        self.assertEqual(self.named(s), [])


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class OffStopStatusUninstall(unittest.TestCase):
    def test_off_stops_ensure_but_keeps_the_files(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        self.assertEqual(s.budgit("off").returncode, 0)
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "off")
        (s.home / "launches.jsonl").unlink()
        s.budgit("ensure")
        self.assertEqual(s.launches(), [])
        self.assertTrue(s.installed_exe().exists())

    def test_off_also_closes_the_window_that_is_open(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        s.budgit("on")
        (rec,) = s.wait_launches(1)
        time.sleep(0.6)
        r = s.budgit("off")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(rec["pid"], 0)
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "off")

    def test_budgit_brote_without_a_word_shows_the_status(self):
        s = Setup(self)
        r = subprocess.run([sys.executable, str(s.root / "bin/budgit"), "brote"], capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg)}, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("off", r.stdout.lower())

    def test_stop_ends_the_running_app_by_its_lock_and_only_that_process(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        s.budgit("on", "--yes")
        (rec,) = s.wait_launches(1)
        time.sleep(0.6)
        bystander = subprocess.Popen(["sleep", "60"])
        self.addCleanup(bystander.kill)
        r = s.budgit("stop")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(rec["pid"], 0)
        self.assertIsNone(bystander.poll())

    def test_status_says_state_build_and_running(self):
        s = Setup(self)
        self.assertIn("off", s.budgit("status").stdout.lower())
        (s.home / "fake-hold").write_text("")
        s.budgit("on", "--yes")
        s.wait_launches(1)
        time.sleep(0.6)
        out = s.budgit("status").stdout.lower()
        for must in ("on", "build-one", "running"):
            self.assertIn(must, out)

    def test_uninstall_removes_exactly_its_own_files(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        s.budgit("on", "--yes")
        (rec,) = s.wait_launches(1)
        time.sleep(0.6)
        mine = [s.bw / "events.log", s.bw / "probe.log", s.bw / "Brote.app.prev", s.bw / ".stage-abc",
                s.home / "Library/Logs/BroteWindow/x.log", s.home / "Library/Preferences/es.budgit.brote.float.plist",
                s.home / "Library/Saved Application State/es.budgit.brote.float.savedState/data"]
        for p in mine:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        not_mine = [s.bw / "precious.txt", s.home / "Library/Logs/OtherApp/keep.log", s.home / "Library/Preferences/com.other.plist"]
        for p in not_mine:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("keep")
        r = s.budgit("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(rec["pid"], 0)
        for p in [s.bw / "Brote.app", s.bw / "brote.lock", *mine]:
            self.assertFalse(p.exists(), str(p))
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "off", "the one file left remembers that he is off")
        for p in not_mine:
            self.assertEqual(p.read_text(), "keep", str(p))
        self.assertEqual(s.budgit("uninstall").returncode, 0, "again is fine")

    def test_uninstall_leaves_only_the_off_marker_when_nothing_else_is_in_the_support_folder(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        time.sleep(0.4)
        s.budgit("uninstall")
        self.assertEqual(sorted(x for x in os.listdir(s.bw) if x != "ensure.lock"), ["float.json"])

    def test_uninstall_never_follows_a_symlink_out_of_the_support_folder(self):
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        target = s.tmp / "outside"
        target.mkdir()
        (target / "important.txt").write_text("keep")
        os.symlink(target, s.bw / "Brote.app.prev")
        s.budgit("uninstall")
        self.assertEqual((target / "important.txt").read_text(), "keep")


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class SecurityReview(unittest.TestCase):
    """The first security review of the lifecycle (2026-10-26): each finding has its test."""

    def on(self, s):
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s.wait_launches(1)

    def test_an_automatic_update_cannot_be_rolled_back_by_an_older_plugin_build(self):
        s = Setup(self, build_id="build-one")
        self.on(s)
        newer = s.cfg / "plugins/cache/studio-hub/studio/0.13.3"
        shutil.copytree(s.root, newer)
        s.make_bundle("build-two", into=newer / "float")
        s.register(newer, version="0.13.3")
        r = s.budgit("ensure", root=newer)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual((s.bw / "Brote.app/Contents/Resources/build.txt").read_text().strip(), "build-two")
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["version"], "0.13.3", "the update records the version it installed")
        s.register(s.root)
        r = s.budgit("ensure", root=s.root)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual((s.bw / "Brote.app/Contents/Resources/build.txt").read_text().strip(), "build-two", "an older build is never put back")
        self.assertIn("older", (s.bw / "ensure.log").read_text())

    def test_a_reused_pid_in_a_dead_lock_is_never_killed(self):
        s = Setup(self)
        self.on(s)
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(s.installed_exe())])
        self.addCleanup(bystander.kill)
        (s.bw / "brote.lock").write_text(f"{bystander.pid}\n")         # nobody holds the lock: the app is not running
        for cmd in ("stop", "uninstall"):
            s.budgit(cmd)
            time.sleep(0.3)
            self.assertIsNone(bystander.poll(), f"`{cmd}` must not signal a process that merely has the app's path in its arguments")
            (s.bw / "brote.lock").parent.mkdir(parents=True, exist_ok=True)
            (s.bw / "brote.lock").write_text(f"{bystander.pid}\n")

    def test_a_busy_lock_never_makes_a_session_start_wait(self):
        import fcntl
        s = Setup(self)
        self.on(s)
        fd = os.open(s.bw / "ensure.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertLess(r.elapsed, 2.5, "the hook gives up quickly when another one is working")

    def test_the_plugin_bundle_is_not_rechecked_when_the_installed_copy_is_current(self):
        s = Setup(self)
        self.on(s)
        shutil.rmtree(s.root / "float/Brote.app")                       # unreadable now: only an install or `on` needs it
        (s.home / "launches.jsonl").unlink()
        r = s.budgit("ensure")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(len(s.wait_launches(1)), 1, "the current installed copy still starts")

    def test_a_pipe_in_place_of_a_state_file_cannot_hang_the_hook(self):
        s = Setup(self)
        self.on(s)
        os.mkfifo(s.bw / "Brote.app.sha256.new")
        r = s.budgit("ensure", timeout=15)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_a_pipe_in_place_of_the_lock_file_cannot_hang_the_hook(self):
        s = Setup(self)
        self.on(s)
        (s.bw / "ensure.lock").unlink()
        os.mkfifo(s.bw / "ensure.lock")
        r = s.budgit("ensure", timeout=15)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_a_pipe_in_place_of_the_plugin_manifest_cannot_hang_the_hook(self):
        s = Setup(self)
        self.on(s)
        (s.root / "float/MANIFEST.sha256").unlink()
        os.mkfifo(s.root / "float/MANIFEST.sha256")
        r = s.budgit("ensure", timeout=15)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_turning_it_on_again_never_lowers_the_recorded_version(self):
        s = Setup(self)
        self.on(s)
        state = json.loads((s.bw / "float.json").read_text())
        state["version"] = "0.13.9"
        (s.bw / "float.json").write_text(json.dumps(state))
        r = s.budgit("on", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["version"], "0.13.9")

    def test_a_remote_login_does_not_open_a_window(self):
        s = Setup(self)
        self.on(s)
        (s.home / "launches.jsonl").unlink()
        r = s.budgit("ensure", extra_env={"SSH_CONNECTION": "10.0.0.1 22 10.0.0.2 50000"})
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        time.sleep(0.5)
        self.assertEqual(s.launches(), [])
        self.assertIn("remote", (s.bw / "ensure.log").read_text())

    def test_a_failing_attribute_check_counts_as_quarantined(self):
        import floatctl
        with self.assertRaises(floatctl.Refuse):
            floatctl.quarantined("/nonexistent-brote-path")

    def test_the_log_keeps_paths_out(self):
        import floatctl
        s = Setup(self)
        self.on(s)
        old = dict(os.environ)
        os.environ.update({"HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg)})
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        ctx = floatctl.Ctx()
        floatctl.log(ctx, f"probe {s.home}/Library/x and {s.cfg}/plugins/y")
        text = (s.bw / "ensure.log").read_text()
        self.assertNotIn(str(s.home), text)
        self.assertNotIn(str(s.cfg), text)


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class AutoOn(unittest.TestCase):
    """R-315: after an install or update the first session start turns the window on by itself; `off` is remembered and never undone."""

    def hook(self, s, **kw):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg), **kw.pop("env", {})}
        t0, c0 = time.time(), resource.getrusage(resource.RUSAGE_CHILDREN)
        r = subprocess.run([sys.executable, str(kw.pop("root", s.root) / "scripts/floatctl.py"), "ensure"], input=json.dumps({"session_id": "auto0123456789"}),
                           capture_output=True, text=True, env=env, timeout=30)
        r.elapsed = time.time() - t0
        r.cpu = cpu_since(c0)
        return r

    def test_the_first_session_start_turns_it_on_installs_launches_and_says_so_once(self):
        s = Setup(self)
        r = self.hook(s)
        self.assertEqual(r.returncode, 0, r.stderr)
        msg = json.loads(r.stdout)["systemMessage"]
        self.assertIn("/budgit-brote off", msg)
        self.assertEqual(len(s.wait_launches(1)), 1)
        state = json.loads((s.bw / "float.json").read_text())
        self.assertEqual(state["float"], "on")
        self.assertEqual(s.installed_exe().read_bytes(), (s.root / "float/Brote.app/Contents/MacOS/Brote").read_bytes())
        again = self.hook(s)
        self.assertEqual((again.returncode, again.stdout), (0, ""), "the notice is shown the first time only")
        self.assertEqual(len(s.launches()), 1)

    def test_the_first_start_is_quick(self):
        s = Setup(self)
        r = self.hook(s)
        # measured 0.69 s of CPU (wall 0.98 s on a quiet machine); budget 3 times that, rounded up to 0.05 s. Wall time is not asserted: it follows the load.
        self.assertLess(r.cpu, 2.10, f"cpu {r.cpu:.2f} s (wall {r.elapsed:.2f} s)")

    def test_the_notice_follows_the_language(self):
        s = Setup(self)
        r = self.hook(s, env={"LANG": "es_ES.UTF-8", "LC_ALL": "es_ES.UTF-8"})
        self.assertIn("/budgit-brote off", json.loads(r.stdout)["systemMessage"])
        self.assertIn("Brote", json.loads(r.stdout)["systemMessage"])

    def test_the_chats_already_open_are_registered_so_he_does_not_leave_when_this_one_ends(self):
        s = Setup(self)
        p = subprocess.Popen(["sleep", "60"])
        self.addCleanup(p.kill)
        r = self.hook(s)
        self.assertEqual(r.returncode, 0)
        files = [f for f in os.listdir(s.bw / "sessions")]
        self.assertTrue(files, "the starting session is registered")

    def test_off_is_remembered_and_never_turned_on_again(self):
        s = Setup(self)
        self.hook(s)
        s.wait_launches(1)
        self.assertEqual(s.budgit("off").returncode, 0)
        (s.home / "launches.jsonl").unlink()
        for _ in range(3):
            r = self.hook(s)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(s.launches(), [])
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "off")

    def test_off_before_the_first_start_is_remembered_too(self):
        s = Setup(self)
        r = s.budgit("off")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "off")
        self.assertEqual(sorted(x for x in os.listdir(s.bw) if x != "ensure.lock"), ["float.json"], "nothing is installed by saying off")
        self.assertEqual(self.hook(s).stdout, "")
        self.assertEqual(s.launches(), [])
        self.assertFalse((s.bw / "Brote.app").exists())

    def test_on_after_off_turns_it_back_on(self):
        s = Setup(self)
        s.budgit("off")
        self.assertEqual(s.budgit("on", "--yes").returncode, 0)
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "on")

    def test_a_damaged_state_file_counts_as_off_not_as_never_decided(self):
        s = Setup(self)
        s.bw.mkdir(parents=True, mode=0o700)
        (s.bw / "float.json").write_text("{not json")
        r = self.hook(s)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(s.launches(), [])

    def test_uninstall_is_remembered_as_off_and_he_does_not_come_back_by_himself(self):
        s = Setup(self)
        self.hook(s)
        s.wait_launches(1)
        time.sleep(0.4)
        self.assertEqual(s.budgit("uninstall").returncode, 0)
        self.assertEqual(sorted(x for x in os.listdir(s.bw) if x != "ensure.lock"), ["float.json"])
        self.assertEqual(json.loads((s.bw / "float.json").read_text())["float"], "off")
        (s.home / "launches.jsonl").unlink()
        self.assertEqual(self.hook(s).stdout, "")
        self.assertEqual(s.launches(), [])
        self.assertEqual(s.budgit("on", "--yes").returncode, 0, "on brings him back")

    def test_it_stays_quiet_where_there_is_no_screen_or_nobody_asked(self):
        for name, env in (("ci", {"CI": "true"}), ("ssh", {"SSH_CONNECTION": "10.0.0.1 22 10.0.0.2 50000"}), ("switch", {"BUDGIT_FLOAT_AUTO": "off"})):
            with self.subTest(name):
                s = Setup(self)
                r = self.hook(s, env=env)
                self.assertEqual((r.returncode, r.stdout), (0, ""))
                self.assertEqual(s.launches(), [])
                self.assertFalse((s.bw / "float.json").exists(), "nothing is decided while it cannot run here")

    def test_no_graphical_session_means_no_automatic_start(self):
        sys.path.insert(0, str(ROOT / "plugin" / "scripts"))
        import floatctl
        real = os.stat
        class St:
            st_uid = os.getuid() + 1
        floatctl_stat = floatctl.os.stat
        try:
            floatctl.os.stat = lambda path, *a, **k: St() if path == "/dev/console" else real(path, *a, **k)
            self.assertFalse(floatctl.gui_session())
        finally:
            floatctl.os.stat = floatctl_stat
        self.assertEqual(floatctl.gui_session(), os.stat("/dev/console").st_uid == os.getuid())

    def test_a_bad_bundle_or_an_untrusted_plugin_writes_nothing(self):
        s = Setup(self)
        (s.root / "float/Brote.app/Contents/MacOS/Brote").write_text("tampered")
        r = self.hook(s)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse(s.bw.exists())
        s2 = Setup(self)
        dev = s2.tmp / "dev-plugin"
        shutil.copytree(s2.root, dev)                                       # a copy nobody registered
        r = self.hook(s2, root=dev)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse(s2.bw.exists())
        self.assertEqual(s2.launches(), [])

    def test_a_plugin_folder_registered_by_claude_code_outside_the_cache_never_turns_him_on_by_itself(self):
        s = Setup(self)
        dev = s.tmp / "dev-plugin"
        shutil.copytree(s.root, dev)
        s.register(dev)
        r = self.hook(s, root=dev)
        self.assertEqual((r.returncode, r.stdout), (0, ""), "a developer folder is not the install the user chose; only `on` turns it on")
        self.assertFalse(s.bw.exists())
        self.assertEqual(s.launches(), [])

    def test_a_plugin_folder_registered_by_claude_code_works_outside_the_cache_once_the_user_typed_on(self):
        s = Setup(self)
        dev = s.tmp / "dev-plugin"
        shutil.copytree(s.root, dev)
        s.register(dev)
        r = s.budgit("on", root=dev)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (rec,) = s.wait_launches(1)
        self.assertEqual(rec["argv"][1:], ["--plugin-root", str(dev)])

    def test_a_registered_folder_that_others_can_write_is_refused_and_so_is_one_under_marketplaces(self):
        s = Setup(self)
        dev = s.tmp / "dev-plugin"
        shutil.copytree(s.root, dev)
        s.register(dev)
        dev.chmod(0o775)
        self.assertNotEqual(s.budgit("on", root=dev).returncode, 0)
        self.assertFalse(s.bw.exists())
        dev.chmod(0o755)
        mk = s.cfg / "plugins" / "marketplaces" / "x" / "plugin"
        mk.parent.mkdir(parents=True)
        shutil.copytree(s.root, mk)
        s.register(mk)
        self.assertEqual(self.hook(s, root=mk).stdout, "")
        self.assertFalse(s.bw.exists())

    def test_a_dev_brote_that_is_already_running_is_left_alone_and_nothing_second_starts(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        dev_exe = s.installed_exe()                                         # the developer's own build, in the installed place, with no sidecar
        shutil.copytree(s.root / "float/Brote.app", s.bw / "Brote.app")
        os.chmod(s.bw, 0o700)
        (s.bw / "Brote.app/Contents/MacOS/Brote").write_text(FAKE + "# dev build\n")
        os.chmod(dev_exe, 0o755)
        subprocess.Popen([str(dev_exe), "--hold"], env={"HOME": str(s.home), "PATH": "/usr/bin:/bin"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid
        (rec,) = s.wait_launches(1)
        before = dev_exe.read_bytes()
        self.hook(s)
        time.sleep(0.5)
        self.assertEqual(len(s.launches()), 1, "no second instance")
        self.assertEqual(dev_exe.read_bytes(), before, "the running build is not replaced")


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class EveryPrompt(unittest.TestCase):
    """R-316: the UserPromptSubmit hook runs `ensure --prompt`: a chat that was open before the update, or one he left while it was open,
    brings him up at the next message. A cheap check when he is already there; off is respected."""

    def run_hook(self, s, sid="promptsid0123456", pid=None, prompt=True, env=None):
        e = {"PATH": "/usr/bin:/bin", "HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg), **(env or {})}
        args = ["ensure"] + (["--prompt"] if prompt else []) + (["--claude-pid", str(pid)] if pid else [])
        t0, c0 = time.time(), resource.getrusage(resource.RUSAGE_CHILDREN)
        r = subprocess.run([sys.executable, str(s.root / "scripts/floatctl.py"), *args], input=json.dumps({"session_id": sid, "cwd": "/tmp"}),
                           capture_output=True, text=True, env=e, timeout=30)
        r.elapsed = time.time() - t0
        r.cpu = cpu_since(c0)
        return r

    def chat(self):
        p = subprocess.Popen(["sleep", "120"])
        self.addCleanup(p.kill)
        return p

    def test_a_chat_that_was_open_before_brote_existed_brings_him_up_at_its_next_message(self):
        s = Setup(self)
        c = self.chat()
        r = self.run_hook(s, pid=c.pid)
        self.assertIn("/budgit-brote off", json.loads(r.stdout)["systemMessage"])
        self.assertEqual(len(s.wait_launches(1)), 1)
        self.assertTrue((s.bw / "sessions" / f"{c.pid}.json").exists(), "this chat now counts as a live session")

    def test_when_he_has_left_the_next_message_brings_him_back(self):
        s = Setup(self)
        c = self.chat()
        self.run_hook(s, pid=c.pid)
        (rec,) = s.wait_launches(1)
        try:
            os.kill(rec["pid"], signal.SIGKILL)                               # he went away (the fake may already have ended on its own)
        except ProcessLookupError:
            pass
        time.sleep(0.3)
        r = self.run_hook(s, pid=c.pid)
        self.assertEqual(r.stdout, "", "no second notice")
        self.assertEqual(len(s.wait_launches(2)), 2)

    def test_when_he_is_up_and_the_chat_is_registered_it_is_a_cheap_check_and_launches_nothing(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        c = self.chat()
        self.run_hook(s, pid=c.pid)
        s.wait_launches(1)
        time.sleep(0.6)
        r = self.run_hook(s, pid=c.pid)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        # measured 0.052 s of CPU (wall 0.06 s); budget 3 times that, rounded up to 0.05 s. Wall time is not asserted: it follows the load.
        self.assertLess(r.cpu, 0.20, f"cpu {r.cpu:.2f} s (wall {r.elapsed:.2f} s)")
        time.sleep(0.4)
        self.assertEqual(len(s.launches()), 1)

    def test_a_second_chat_that_is_not_registered_yet_is_registered_without_a_second_window(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        a, b = self.chat(), self.chat()
        self.run_hook(s, sid="firstchatsid0123", pid=a.pid)
        s.wait_launches(1)
        time.sleep(0.6)
        self.run_hook(s, sid="secondchatsid012", pid=b.pid)
        self.assertTrue((s.bw / "sessions" / f"{b.pid}.json").exists())
        time.sleep(0.4)
        self.assertEqual(len(s.launches()), 1)

    def test_off_is_respected_at_every_message(self):
        s = Setup(self)
        s.budgit("off")
        c = self.chat()
        for _ in range(3):
            r = self.run_hook(s, pid=c.pid)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(s.launches(), [])
        self.assertEqual(sorted(x for x in os.listdir(s.bw) if x != "ensure.lock"), ["float.json"])

    def test_the_cheap_check_when_it_is_off_is_fast(self):
        s = Setup(self)
        s.budgit("off")
        self.assertLess(self.run_hook(s).elapsed, 0.2)

    def test_nothing_starts_where_automatic_start_is_not_allowed(self):
        for env in ({"CI": "1"}, {"SSH_TTY": "/dev/ttys001"}, {"BUDGIT_FLOAT_AUTO": "off"}):
            s = Setup(self)
            r = self.run_hook(s, env=env)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            self.assertEqual(s.launches(), [])
            self.assertFalse(s.bw.exists())

    def test_the_hook_is_wired_with_a_short_timeout(self):
        hooks = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())["hooks"]["UserPromptSubmit"]
        ours = [h for g in hooks for h in g["hooks"] if "floatctl.py" in h["command"]]
        self.assertEqual(len(ours), 1)
        self.assertTrue(ours[0]["command"].rstrip().endswith("ensure --prompt"), ours[0]["command"])
        self.assertIn('"${CLAUDE_PLUGIN_ROOT}/scripts/floatctl.py"', ours[0]["command"])
        self.assertLessEqual(ours[0]["timeout"], 5)

    def test_resumed_chats_run_the_session_start_hook_too(self):
        hooks = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())["hooks"]["SessionStart"]
        (g,) = [g for g in hooks if any("floatctl.py" in h["command"] for h in g["hooks"])]
        self.assertIn("resume", g["matcher"].split("|"))


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class DirectoryMarketplace(unittest.TestCase):
    """R-317: a developer setup. The registry lists the plugin at an old cache folder, but Claude reads the live folder because the marketplace is a
    "directory" one: known_marketplaces.json studio-hub -> installLocation <hub>, and <hub>/.claude-plugin/marketplace.json says source ./plugin."""

    def hub(self, s, source="./plugin", mname="studio-hub", kind="directory", loc=None, plugin_name="studio", enabled=None):
        hub = s.tmp / "hub"
        shutil.copytree(s.root, hub / "plugin")
        (hub / ".claude-plugin").mkdir()
        (hub / ".claude-plugin/marketplace.json").write_text(json.dumps({"name": mname, "plugins": [{"name": plugin_name, "source": source}]}))
        where = str(loc or hub)
        (s.cfg / "plugins/known_marketplaces.json").write_text(json.dumps({"studio-hub": {"source": {"source": kind, "path": str(hub)}, "installLocation": where}}))
        s.register(s.cfg / "plugins/cache/studio-hub/studio/0.11.36", version="0.11.36")       # the stale entry her registry really has
        if enabled is not None:
            (s.cfg / "settings.json").write_text(json.dumps({"enabledPlugins": {"studio@studio-hub": enabled}}))
        return hub / "plugin"

    def test_on_works_from_the_live_folder_of_a_directory_marketplace_and_the_app_gets_that_root(self):
        s = Setup(self)
        root = self.hub(s, enabled=True)
        r = s.budgit("on", "--yes", root=root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (rec,) = s.wait_launches(1)
        self.assertEqual(rec["argv"][1:], ["--plugin-root", str(root.resolve())])
        state = json.loads((s.bw / "float.json").read_text())
        self.assertEqual(state["version"], json.loads((root / ".claude-plugin/plugin.json").read_text())["version"], "the live plugin.json, not the stale 0.11.36")

    def test_a_session_start_there_keeps_it_up_but_never_turns_it_on_first(self):
        s = Setup(self)
        root = self.hub(s)
        r = s.budgit("ensure", root=root)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse(s.bw.exists(), "auto-on stays cache-only")
        self.assertEqual(s.budgit("on", root=root).returncode, 0)
        s.wait_launches(1)
        (s.home / "launches.jsonl").unlink()
        s.budgit("ensure", root=root)
        self.assertEqual(len(s.wait_launches(1)), 1, "after `on`, a session start brings him up from the live folder")

    def test_refused_when_the_marketplace_is_not_a_directory_one_or_names_another_folder_or_another_plugin(self):
        for kw in ({"kind": "github"}, {"source": "./other"}, {"mname": "someone-else"}, {"plugin_name": "not-us"}, {"source": "../escape"}, {"source": "/abs"}):
            with self.subTest(kw=kw):
                s = Setup(self)
                root = self.hub(s, **kw)
                if kw.get("source") == "./other":
                    shutil.copytree(root, root.parent / "other")
                r = s.budgit("on", root=root)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertFalse(s.bw.exists())

    def test_refused_when_the_install_location_is_not_the_folder_that_holds_the_plugin(self):
        s = Setup(self)
        root = self.hub(s, loc=s.tmp / "elsewhere")
        (s.tmp / "elsewhere").mkdir()
        self.assertNotEqual(s.budgit("on", root=root).returncode, 0)

    def test_refused_when_disabled_or_writable_by_others_or_the_marketplace_file_is_not_ours_to_trust(self):
        s = Setup(self)
        root = self.hub(s, enabled=False)
        self.assertNotEqual(s.budgit("on", root=root).returncode, 0, "switched off in settings.json")
        (s.cfg / "settings.json").unlink()
        root.chmod(0o775)
        self.assertNotEqual(s.budgit("on", root=root).returncode, 0)
        root.chmod(0o755)
        (root / "float").chmod(0o777)
        self.assertNotEqual(s.budgit("on", root=root).returncode, 0)
        (root / "float").chmod(0o755)
        mj = root.parent / ".claude-plugin/marketplace.json"
        mj.unlink()
        mj.symlink_to(s.tmp / "somewhere")
        self.assertNotEqual(s.budgit("on", root=root).returncode, 0, "a linked marketplace.json is never read")

    def test_refused_when_settings_json_is_there_but_unreadable_or_the_source_path_is_relative(self):
        s = Setup(self)
        root = self.hub(s)
        (s.cfg / "settings.json").write_text("{ not json")
        self.assertNotEqual(s.budgit("on", root=root).returncode, 0, "present but unreadable: not treated as enabled")
        (s.cfg / "settings.json").unlink()
        km = s.cfg / "plugins/known_marketplaces.json"
        d = json.loads(km.read_text())
        d["studio-hub"]["source"]["path"] = "hub"
        km.write_text(json.dumps(d))
        self.assertNotEqual(s.budgit("on", root=root).returncode, 0, "a relative source path means nothing")

    def test_the_manifest_still_has_to_match(self):
        s = Setup(self)
        root = self.hub(s)
        (root / "float/Brote.app/Contents/MacOS/Brote").write_text("tampered")
        r = s.budgit("on", root=root)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(s.launches(), [])


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class OnFromTheDesktop(unittest.TestCase):
    """`/budgit-float on` from the desktop app runs outside any session: the chats that are already open must still keep him up."""

    def test_the_claude_processes_are_read_from_the_process_list(self):
        import floatctl
        text = ("  501   501 /Applications/Claude.app/Contents/Helpers/disclaimer\n"
                "  502   501 /Users/x/Library/Application Support/Claude/claude-code/2.1/claude.app/Contents/MacOS/claude\n"
                "  503   777 /Users/x/Library/Application Support/Claude/claude-code/2.1/claude.app/Contents/MacOS/claude\n"
                "  504   501 /usr/bin/claudette\n"
                "  505   501 /opt/homebrew/bin/claude\n")
        self.assertEqual(floatctl.live_claude_pids(text, 501), [502, 505])

    def test_open_chats_are_registered_when_no_session_is_known(self):
        import floatctl
        s = Setup(self)
        s.budgit("on", "--yes")
        s.wait_launches(1)
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(other.kill)
        old = dict(os.environ)
        os.environ.update({"HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg)})
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        ctx = floatctl.Ctx()
        floatctl.register_open_chats(ctx, ps_text=f"  {other.pid}   {os.getuid()} /x/claude.app/Contents/MacOS/claude\n")
        entry = json.loads((s.bw / "sessions" / f"{other.pid}.json").read_text())
        self.assertEqual(entry["pid"], other.pid)
        self.assertRegex(entry["sid"], r"^on-[0-9]+$")
        before = (s.bw / "sessions" / f"{other.pid}.json").read_text()
        (s.bw / "sessions" / f"{other.pid}.json").write_text(before.replace(entry["sid"], "realsid"))
        floatctl.register_open_chats(ctx, ps_text=f"  {other.pid}   {os.getuid()} /x/claude\n")
        self.assertEqual(json.loads((s.bw / "sessions" / f"{other.pid}.json").read_text())["sid"], "realsid", "a session the hook registered is not overwritten")


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class Wiring(unittest.TestCase):
    def test_budgit_float_is_the_same_command(self):
        s = Setup(self)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg)}
        a = sh([sys.executable, str(s.root / "bin/budgit"), "float", "status"], env=env)
        b = sh([sys.executable, str(s.root / "scripts/floatctl.py"), "status"], env=env)
        self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout), a.stderr)
        self.assertIn("off", a.stdout.lower())
        c = sh([sys.executable, str(s.root / "bin/budgit"), "brote", "status"], env=env)
        self.assertEqual((c.returncode, c.stdout), (a.returncode, a.stdout), "`budgit brote` is the name people are told; `float` stays as the old name")
        r = sh([sys.executable, str(s.root / "bin/budgit"), "brote", "on"], env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


@unittest.skipUnless(MAC, "needs macOS (codesign)")
class OldInstance(unittest.TestCase):
    """R-318: a Brote that an older build or a hand start left running from BroteWindow (no --plugin-root, not holding brote.lock) is closed
    before the new one starts, so there are never two. Only that process: the lock holder, other programs and other folders' Brotes stay."""

    def old(self, exe):
        """A process whose command line is exactly this path (what ps shows for an app started by its path), holding no lock."""
        p = subprocess.Popen(["/bin/bash", "-c", 'exec -a "$0" sleep 120', str(exe)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(p.kill)
        time.sleep(0.4)
        return p

    def ensure(self, s):
        c = subprocess.Popen(["sleep", "120"])
        self.addCleanup(c.kill)
        e = {"PATH": "/usr/bin:/bin", "HOME": str(s.home), "CLAUDE_CONFIG_DIR": str(s.cfg)}
        return subprocess.run([sys.executable, str(s.root / "scripts/floatctl.py"), "ensure", "--claude-pid", str(c.pid)], input=json.dumps({"session_id": "oldinst012345678", "cwd": "/tmp"}),
                              capture_output=True, text=True, env=e, timeout=30)

    def test_an_old_instance_is_closed_and_the_new_one_starts(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        s.make_bundle("build-one")
        self.ensure(s)
        s.wait_launches(1)                     # installs the app into BroteWindow
        for pid in s.launch_pids():
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.3)
        exe = s.bw / "Brote.app/Contents/MacOS/Brote"
        o = self.old(exe)
        self.ensure(s)
        time.sleep(0.5)
        self.assertIsNotNone(o.poll(), "the old instance was closed")
        self.assertEqual(len(s.wait_launches(2)), 2, "and the new one started")

    def test_the_one_that_holds_the_lock_and_other_programs_stay(self):
        s = Setup(self)
        (s.home / "fake-hold").write_text("")
        self.ensure(s)
        (rec,) = s.wait_launches(1)
        other = s.tmp / "elsewhere" / "Brote"
        other.parent.mkdir()
        o_other = self.old(other)
        o_sleep = subprocess.Popen(["sleep", "120"])
        self.addCleanup(o_sleep.kill)
        self.ensure(s)
        time.sleep(0.5)
        os.kill(rec["pid"], 0)                 # the lock holder is still there
        self.assertIsNone(o_other.poll(), "a Brote from another folder is not ours to close")
        self.assertIsNone(o_sleep.poll())
        self.assertEqual(len(s.launches()), 1, "nothing new was started")


class NotMac(unittest.TestCase):
    @unittest.skipIf(sys.platform == "darwin", "the refusal is for other systems")
    def test_other_systems_are_refused(self):
        r = sh([sys.executable, str(ROOT / "plugin/bin/budgit"), "float", "status"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("macOS", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
