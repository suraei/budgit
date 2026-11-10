"""R-569 and R-869: `budgit suite --wait [--rc] -- <cmd>`, and a keep-awake while a suite runs.

Agents used to wait for the suite lock by hand, spending a tool call per look. This module is ONE blocking process: it
checks busy first (the shared rule, admit.is_busy over a 15 s window; no lock is held while it waits for the machine to calm
down), takes a place in the project's ticket queue (order: rank, then release candidates, then oldest), prints its place
once, waits with a sleep between looks (never a busy loop) printing the holder (pid, command, age, ETA from
tests/.durations.json), takes the PER-PROJECT lock (named from the git common dir) and nothing else: the machine-wide
full-suite lock is retired (§6), another project's suite never blocks this one. It prints one line when it has the lock, runs
<cmd> and releases on exit. A project that is not top priority (rank 0) runs <cmd> under `nice -n 10`. `--rc` only
puts the suite ahead of ordinary ones in its project's queue.

Locks are try-locks on files (fcntl.flock on POSIX, msvcrt.locking on Windows), so a dead holder frees them. Lock folder:
BUDGIT_SUITE_LOCK_DIR, else /private/tmp on macOS (where the by-hand lockf files live), else the temp folder.

R-869: while <cmd> runs the machine stays awake: `caffeinate -i -w <pid>` (macOS), `systemd-inhibit` wrapped around the command
(Linux), SetThreadExecutionState (Windows). With none of them available the command runs anyway and one line says so.
Everything that varies (platform, tools, clock, sleep, readings) is a parameter, so tests use fakes."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

NICE = 10
POLL = 1.0
LOAD_POLL = 30
BUSY_WINDOW = 15            # seconds of CPU sampling at launch (budgit busy defaults to 60; a suite should not idle a minute)
HOLDER_EVERY = 60
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


class StateDirError(Exception):
    """The state folder (or a file in it) is not safe to use: refuse, never follow or share."""


def _uid():
    return os.getuid() if hasattr(os, "getuid") else None


def _note(text):
    sys.stderr.write("budgit suite: %s\n" % text)


def ensure_private_dir(path):
    """Create `path` with mode 0700 when missing, then require: a real directory (not a symlink), owned by this user, no group or
    other access. Anything else raises StateDirError. Windows has no uid: its per-user temp folder is the protection."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    import stat
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise StateDirError("%s is not a plain folder" % path)
    uid = _uid()
    if uid is not None and (st.st_uid != uid or st.st_mode & 0o077):
        raise StateDirError("%s is not private to this user (owner %s, mode %o)" % (path, st.st_uid, st.st_mode & 0o777))
    return path


def lock_dir(plat=None, env=None):
    """The per-user state folder: BUDGIT_SUITE_LOCK_DIR (tests, odd machines), else <TMPDIR>/budgit-<uid> on macOS, else
    <temp folder>/budgit-<uid>. Created 0700 and checked on every call."""
    env = os.environ if env is None else env
    plat = plat or sys.platform
    explicit = env.get("BUDGIT_SUITE_LOCK_DIR")
    if explicit:
        return ensure_private_dir(explicit)
    base = env.get("TMPDIR") if plat == "darwin" and env.get("TMPDIR") else tempfile.gettempdir()
    uid = _uid()
    return ensure_private_dir(os.path.join(base, "budgit-%s" % (uid if uid is not None else (os.environ.get("USERNAME") or "user"))))


def open_nofollow(path, flags, mode=0o600):
    """os.open that refuses a symlink (O_NOFOLLOW, where the OS has it) and a file owned by someone else."""
    try:
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), mode)
    except OSError as exc:
        if os.path.islink(path):
            raise StateDirError("%s is a symlink: refused" % path)
        raise exc
    uid = _uid()
    if uid is not None and os.fstat(fd).st_uid != uid:
        os.close(fd)
        raise StateDirError("%s belongs to another user: refused" % path)
    return fd


def write_atomic(path, obj):
    """JSON to a fresh temp file (O_EXCL, no symlink followed), then os.replace: a planted symlink at `path` is replaced, never written through."""
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), os.urandom(3).hex())
    fd = open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def read_json(path):
    """-> the parsed file, or None (one notice) when it is corrupt; a symlink or foreign file raises StateDirError; missing -> None."""
    try:
        fd = open_nofollow(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as f:
        try:
            return json.load(f)
        except ValueError:
            _note("skipping unreadable state file %s" % os.path.basename(path))
            return None


def lock_name(name):
    """The project lock's file name: one function, used by `budgit suite --wait` and by tools/budgit_test.py (both front doors)."""
    return "budgit-suite-%s.lock" % name


def lock_paths(name, plat=None, env=None):
    """[the project lock]: the only lock there is."""
    return [os.path.join(lock_dir(plat, env), lock_name(name))]


def try_lock(path, plat=None, fcntl_mod=None, msvcrt_mod=None):
    """-> an open handle holding the lock, or None when someone else holds it."""
    plat = plat or sys.platform
    ensure_private_dir(os.path.dirname(path))
    fd = open_nofollow(path, os.O_RDWR | os.O_CREAT)
    try:
        if plat.startswith("win"):
            m = msvcrt_mod or __import__("msvcrt")
            os.lseek(fd, 0, 0)
            m.locking(fd, m.LK_NBLCK, 1)
        else:
            f = fcntl_mod or __import__("fcntl")
            f.flock(fd, f.LOCK_EX | f.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def unlock(fd, plat=None, fcntl_mod=None, msvcrt_mod=None):
    plat = plat or sys.platform
    try:
        if plat.startswith("win"):
            m = msvcrt_mod or __import__("msvcrt")
            os.lseek(fd, 0, 0)
            m.locking(fd, m.LK_UNLCK, 1)
        else:
            f = fcntl_mod or __import__("fcntl")
            f.flock(fd, f.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(fd)


# ---------------------------------------------------------------- the ticket queue

def _queue_dir(base):
    return os.path.join(base, "budgit-suite-queue")


def _alive(pid):
    import admission
    return admission.pid_alive(int(pid))


def join(base, ticket, rank, rc, name, now):
    ensure_private_dir(base)
    ensure_private_dir(_queue_dir(base))
    write_atomic(os.path.join(_queue_dir(base), ticket), {"pid": os.getpid(), "rank": rank, "rc": bool(rc), "since": now, "name": name})


def leave(base, ticket):
    try:
        os.remove(os.path.join(_queue_dir(base), ticket))
    except OSError:
        pass


def waiting(base):
    """Live tickets in service order: (rank, release candidates first, oldest first). Dead holders are dropped."""
    out = []
    try:
        names = os.listdir(_queue_dir(base))
    except OSError:
        return out
    for n in names:
        path = os.path.join(_queue_dir(base), n)
        if n.endswith(".tmp"):
            continue
        try:
            t = read_json(path)
            if t is None:
                continue
            if not _alive(t["pid"]):
                os.remove(path)
                continue
            out.append((t["rank"], 0 if t["rc"] else 1, t["since"], n))
        except (OSError, ValueError, KeyError, TypeError, StateDirError):
            continue                                  # a planted or foreign ticket never counts
    return [x[3] for x in sorted(out)]


# ---------------------------------------------------------------- keep awake (R-869)

class KeepAwake:
    """Hold the machine awake while a command runs. `wrap(argv)` first (Linux wraps the command), `start(pid)` after launch,
    `stop()` on exit. `notice` is the one line to print when no tool exists."""

    def __init__(self, plat=None, which=shutil.which, popen=subprocess.Popen, win_api=None):
        self.plat, self.which, self.popen, self.win_api = plat or sys.platform, which, popen, win_api
        self.proc, self.notice, self.covered = None, None, False

    def wrap(self, argv):
        if self.plat.startswith("linux") and self.which("systemd-inhibit"):
            self.covered = True
            return ["systemd-inhibit", "--what=idle:sleep", "--who=budgit", "--why=full suite running", "--"] + list(argv)
        return list(argv)

    def _windows(self):
        if self.win_api is not None:
            return self.win_api
        try:
            import ctypes
            return ctypes.windll.kernel32
        except (ImportError, AttributeError, OSError):
            return None

    def start(self, pid):
        if self.covered:
            return
        if self.plat == "darwin" and self.which("caffeinate"):
            try:
                self.proc = self.popen(["caffeinate", "-i", "-w", str(pid)])
                return
            except OSError:
                pass
        elif self.plat.startswith("win"):
            api = self._windows()
            if api is not None:
                api.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                self.covered = True
                return
        self.notice = "no keep-awake tool found (caffeinate, systemd-inhibit or SetThreadExecutionState): running anyway, the machine may sleep"

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except OSError:
                pass
            self.proc = None
        if self.plat.startswith("win") and self.covered:
            api = self._windows()
            if api is not None:
                api.SetThreadExecutionState(ES_CONTINUOUS)


def run_awake(command, shell=False, cwd=None, plat=None, which=shutil.which, popen=subprocess.Popen, awake=None, out=print):
    """Run `command` (an argv list; a string only when shell=True, which still runs as an argv list through the platform's
    interpreter: never Popen(shell=True)) with the machine kept awake -> exit code."""
    plat = plat or sys.platform
    awake = awake or KeepAwake(plat, which)
    if shell:
        command = ["cmd", "/c", command] if plat.startswith("win") else ["/bin/sh", "-c", command]
    child = popen(awake.wrap(command), cwd=cwd, stdin=subprocess.DEVNULL)
    awake.start(child.pid)
    if awake.notice:
        out(awake.notice)
    try:
        return child.wait()
    finally:
        awake.stop()


# ---------------------------------------------------------------- the command

def niced(argv, rank, plat=None, which=shutil.which):
    """`nice -n 10` in front unless this is the top-priority project (rank 0). No nice on Windows: the caller sets the class."""
    plat = plat or sys.platform
    if rank == 0 or plat.startswith("win"):
        return list(argv)
    nice = which("nice")
    return [nice, "-n", str(NICE)] + list(argv) if nice else list(argv)


def write_holder(lock_path, argv, now):
    """Who holds the project lock, in a side file (a Windows byte-range lock would hide the lock file itself). Written
    atomically in the private state folder; no path is stored, so nobody can point the reader elsewhere."""
    try:
        write_atomic(lock_path + ".holder", {"pid": os.getpid(), "cmd": " ".join(argv)[:200], "since": now})
    except OSError:
        pass


def is_full_suite(cmd):
    return ("discover" in cmd and " -p " not in cmd) or "--full" in cmd or "budgit_test" in cmd


def eta_text(holder, project_dir, cpu_count=os.cpu_count):
    """Sum of THIS project's tests/.durations.json (`project_dir` is the waiting caller's repo, never a path read from the
    holder file) divided by the workers (cores - 1, read now) when the holder runs the full suite, else unknown."""
    try:
        if not project_dir or not is_full_suite(str(holder.get("cmd", ""))):
            return "unknown"
        data = read_json(os.path.join(project_dir, "tests", ".durations.json"))
        if not isinstance(data, dict):
            return "unknown"
        total = sum(v for v in data.values() if isinstance(v, (int, float)) and not isinstance(v, bool))
        workers = max(1, (cpu_count() or 1) - 1)
        return "about %d s" % round(total / workers)
    except (OSError, StateDirError, TypeError, AttributeError):
        return "unknown"


def holder_line(lock_path, now, project_dir=None, cpu_count=os.cpu_count):
    try:
        h = read_json(lock_path + ".holder")
        if not isinstance(h, dict):
            return "waiting for the lock: holder unknown"
        cmd = "".join(c if c.isprintable() else "?" for c in str(h["cmd"]))[:60]
        return "waiting for the lock held by pid %s, command %s, running %d s, ETA %s" % (
            int(h["pid"]), cmd, max(0, int(now - h["since"])), eta_text(h, project_dir, cpu_count))
    except (OSError, StateDirError, KeyError, TypeError, ValueError):
        return "waiting for the lock: holder unknown"


def wait_not_busy(busy_fn, sleep, out):
    """Wait, holding NO lock, until the machine is not busy -> True when it was busy at the first reading."""
    first = None
    while True:
        busy, why = busy_fn()
        if first is None:
            first = busy
        if not busy:
            return first
        out("machine busy (%s): waiting, no lock held, rechecking in %ds" % (why, LOAD_POLL))
        sleep(LOAD_POLL)


def run(argv, name, rank=1, rc=False, plat=None, env=None, busy_fn=None, sleep=time.sleep, out=print, cwd=None,
        runner=None, now=time.time, which=shutil.which):
    """Busy check (no lock held), then the PER-PROJECT lock only (there is no machine-wide lock: §6 retired it), run `argv`,
    release -> the command's exit code. While it waits it prints the holder: pid, command, age and an ETA."""
    plat = plat or sys.platform
    if busy_fn is None:
        import admit
        busy_fn = lambda: admit.is_busy(window=BUSY_WINDOW)
    base = os.path.join(lock_dir(plat, env), "budgit-suite-queue-%s" % name)       # tickets queue per project too
    wait_not_busy(busy_fn, sleep, out)
    path = lock_paths(name, plat, env)[0]
    ticket = "%d-%d-%s" % (os.getpid(), int(now() * 1000), os.urandom(3).hex())
    join(base, ticket, rank, rc, name, now())
    began, held, last = now(), [], None
    try:
        line = waiting(base)
        out("suite queue: place %d of %d (rank %d%s)" % (line.index(ticket) + 1 if ticket in line else 1, len(line), rank, ", RC" if rc else ""))
        while True:
            line = waiting(base)
            if not line or line[0] == ticket:
                fd = try_lock(path, plat)
                if fd is not None:
                    held = [fd]
                    break
            if last is None or now() - last >= HOLDER_EVERY:
                out(holder_line(path, now(), project_dir=cwd))
                last = now()
            sleep(POLL)
        write_holder(path, argv, now())
        leave(base, ticket)
        out("suite lock acquired after %ds: running" % int(now() - began))
        cmd = niced(argv, rank, plat, which)
        if runner:
            return runner(cmd, cwd)
        return run_awake(cmd, cwd=cwd, plat=plat, which=which, out=out)
    finally:
        leave(base, ticket)
        for fd in held:
            unlock(fd, plat)
