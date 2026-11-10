"""R-848: ONE machine-wide cap on test and build workers, counted across all lanes and all projects.

Slots come from one budget read at run time (usable cores, memory, battery, current busy). Each holder writes one lease file
(pid, slots, start) in a state dir; a lock file serialises every acquire; leases of dead pids are reclaimed on every acquire.
A holder is never refused outright: when nothing is free it gets ONE crawl slot (over budget, flagged), so work still moves.

    admission.acquire(want, kind)  -> granted        admission.release()        with admission.admitted(want) as n: ...
    python admission.py --slots N [--kind K] [--timeout S] -- <cmd>     (child gets BUDGIT_ADMIT_SLOTS=<granted>)

Every reading is a parameter, so tests inject fakes; production code reads no test env var. Python 3.9+."""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time

PER_WORKER_MB = {"test": 300, "build": 1500}      # estimates per kind; a caller may pass its own per_worker_mb
BUSY_CPU_PCT = 85                                  # R-532 busy rule: median CPU over 85 % of cores ...
BUSY_FREE_MB = 1024                                # ... or reclaimable memory (free plus inactive) below 1024 MB
RESERVE_MB = 1024                                  # memory always left for the OS
ENV_SLOTS = "BUDGIT_ADMIT_SLOTS"


# ---------------------------------------------------------------- readings (each fails to None, never raises)

def usable_cores(cpu_count=os.cpu_count, affinity=getattr(os, "sched_getaffinity", None), quota=None):
    """CPUs this process may use: the affinity mask (Linux) else cpu_count, then the container quota (cores) if lower."""
    n = 0
    if affinity:
        try:
            n = len(affinity(0))
        except OSError:
            n = 0
    n = n or cpu_count() or 1
    if quota is not None and quota < n:
        n = max(1, int(quota + 0.999))             # a 1.5 quota may use 2 cores in bursts
    return n


def cgroup_quota(read=None):
    """Linux container CPU limit as cores, or None. cgroup v2 cpu.max, then v1 cfs quota/period."""
    def rd(path):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return None
    read = read or rd
    v2 = read("/sys/fs/cgroup/cpu.max")
    try:
        if v2:
            a, b = v2.split()[:2]
            return None if a == "max" else int(a) / float(b)
        q, p = read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q and p and int(q) > 0 and int(p) > 0:
            return int(q) / float(p)
    except (ValueError, ZeroDivisionError):
        pass
    return None


def meminfo_free_mb(text):
    """MemAvailable from /proc/meminfo text, in MB, or None."""
    for line in (text or "").splitlines():
        if line.startswith("MemAvailable:"):
            try:
                return int(line.split()[1]) // 1024
            except (ValueError, IndexError):
                return None
    return None


def vm_stat_free_mb(text):
    """free + inactive + speculative pages from macOS vm_stat text, in MB, or None."""
    page, pages = 4096, 0
    try:
        for line in (text or "").splitlines():
            if "page size of" in line:
                page = int(line.split("page size of")[1].split()[0])
            elif line.split(":")[0] in ("Pages free", "Pages inactive", "Pages speculative"):
                pages += int(line.split(":")[1].strip().rstrip("."))
    except (ValueError, IndexError):
        return None
    return pages * page // (1024 * 1024) if pages else None


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _read(path):
    with open(path) as f:
        return f.read()


def free_mb(platform=sys.platform, run=_run, readfile=None):
    """Memory a new process could use now. Linux /proc/meminfo, macOS vm_stat; Windows and anything else: None (unknown)."""
    if platform.startswith("linux"):
        try:
            return meminfo_free_mb((readfile or _read)("/proc/meminfo"))
        except OSError:
            return None
    if platform == "darwin":
        return vm_stat_free_mb(run(["vm_stat"]))
    return None


def on_battery(platform=sys.platform, run=_run, listdir=os.listdir, readfile=None):
    """True on battery, False on mains, None when unknown (Windows, desktops, no reading)."""
    if platform == "darwin":
        out = run(["pmset", "-g", "batt"])
        if not out:
            return None
        if "Battery Power" in out:
            return True
        return False if "AC Power" in out else None
    if platform.startswith("linux"):
        base = "/sys/class/power_supply"
        try:
            names = [n for n in listdir(base) if n.startswith("BAT")]
        except OSError:
            return None
        for n in names:
            try:
                s = (readfile or _read)(os.path.join(base, n, "status")).strip()
            except OSError:
                continue
            return s == "Discharging"
        return None
    return None


def cpu_pct(run=_run, cores=1, platform=sys.platform):
    """Summed %CPU of all processes as a share of all cores (ps, macOS and Linux); None elsewhere or on failure."""
    if platform.startswith("win"):
        return None
    out = run(["ps", "-A", "-o", "%cpu="])
    try:
        return sum(float(x) for x in out.split()) / max(1, cores) if out else None
    except ValueError:
        return None


def cpu_median(sampler, sleep=time.sleep, samples=3, step=0.5):
    """Median of a few short samples (admit.cpu_median is the 60 s R-532 window, too slow for every acquire). Unreadable
    samples are skipped; None when none was read. Sampler and sleep are injected."""
    import statistics
    got = []
    for i in range(max(1, samples)):
        v = sampler()
        if v is not None:
            got.append(v)
        if i < samples - 1:
            sleep(step)
    return statistics.median(got) if got else None


def read_machine(per_worker_mb=None, sample=None, sampler=None, sleep=time.sleep):
    """-> dict of the readings now. `sample` replaces the CPU reading; else the median of 3 short `sampler` readings."""
    cores_all = os.cpu_count() or 1
    q = cgroup_quota() if sys.platform.startswith("linux") else None
    cores = usable_cores(quota=q)
    if sample is None:
        sample = cpu_median(sampler or (lambda: cpu_pct(cores=cores_all)), sleep)
    return {"cores": cores, "quota": q, "free_mb": free_mb(), "battery": on_battery(), "cpu_pct": sample}


# ---------------------------------------------------------------- the one busy rule (admit.py calls these too)

def busy_reason(free, cpu):
    """The R-532 busy rule, defined ONCE. free: free+inactive MB or None; cpu: median CPU as % of all cores or None.
    -> the reason text when busy, else None. A reading that is None is skipped. Memory is judged first."""
    if free is not None and free < BUSY_FREE_MB:
        return "free memory %d MB is under %d MB" % (free, BUSY_FREE_MB)
    if cpu is not None and cpu > BUSY_CPU_PCT:
        return "median CPU %.0f %% is above %d %%" % (cpu, BUSY_CPU_PCT)
    return None


def busy_cpu_cores(cores):
    """The busy CPU threshold expressed in busy cores (85 % of this machine's cores)."""
    return max(1, cores) * BUSY_CPU_PCT / 100.0


# ---------------------------------------------------------------- the one decision

def budget(m, per_worker_mb=300):
    """-> (slots, why). Pure. m: cores (usable), free_mb or None, battery True/False/None, cpu_pct or None.
    Ceiling cores-1, floor 1. Memory limits slots; unknown memory (Windows) is treated conservatively as half the ceiling.
    Battery halves. Busy (CPU over 85 % or memory under 1 GB) gives 1."""
    cores = max(1, int(m.get("cores") or 1))
    ceiling = max(1, cores - 1)
    n, why = ceiling, ["%d usable cores" % cores]
    free = m.get("free_mb")
    if free is None:
        n = min(n, max(1, ceiling // 2))
        why.append("memory unknown: conservative")
    else:
        n = min(n, max(1, int((free - RESERVE_MB) // max(1, per_worker_mb))))
        why.append("memory %d MB / %d MB per worker" % (free, per_worker_mb))
    if m.get("battery"):
        n = max(1, n // 2)
        why.append("battery: halved")
    if busy_reason(free, m.get("cpu_pct")):
        n = 1
        why.append("busy")
    return max(1, min(n, ceiling)), ", ".join(why)


# ---------------------------------------------------------------- leases

def default_state_dir():
    uid = getattr(os, "getuid", lambda: "u")()
    return os.path.join(tempfile.gettempdir(), "budgit-admission-%s" % uid)


def pid_alive(pid):
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        out = _run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"])
        return bool(out) and str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextlib.contextmanager
def _locked(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    f = open(os.path.join(state_dir, "lock"), "a+")
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()                                  # closing drops a flock


def _leases(state_dir, alive):
    """Live leases as dicts; the files of dead pids are emptied of meaning by removal (reclaim)."""
    out = []
    for name in os.listdir(state_dir):
        if not name.endswith(".lease"):
            continue
        path = os.path.join(state_dir, name)
        try:
            with open(path) as f:
                d = json.load(f)
            ok = alive(int(d["pid"]))
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        if ok:
            d["_path"] = path
            out.append(d)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    return out


_held = []


def acquire(want, kind="test", timeout=0, machine=None, state_dir=None, pid=None, alive=pid_alive, per_worker_mb=None,
            sleep=time.sleep, now=time.time):
    """-> granted slots (int). Never more than free; at least 1. timeout 0 (default): never waits, and when nothing is free
    the 1 crawl slot is granted over budget. timeout > 0: waits up to that many seconds for a free slot, then crawls.
    `machine` is the reading dict (default: read now); budget is re-read on every try."""
    state_dir = state_dir or default_state_dir()
    pid = pid or os.getpid()
    want = max(1, int(want))
    per = per_worker_mb or PER_WORKER_MB.get(kind, 300)
    deadline = now() + max(0, timeout)
    while True:
        m = machine() if callable(machine) else (machine or read_machine())
        total, why = budget(m, per)
        with _locked(state_dir):
            used = sum(int(d.get("slots", 0)) for d in _leases(state_dir, alive) if d.get("crawl") is not True)
            free = total - used
            if free >= 1 or now() >= deadline:
                crawl = free < 1
                granted = 1 if crawl else min(want, free)
                path = os.path.join(state_dir, "%d-%d.lease" % (pid, len(_held)))
                with open(path, "w") as f:
                    json.dump({"pid": pid, "slots": granted, "kind": kind, "start": now(), "crawl": crawl,
                               "budget": total, "why": why}, f)
                _held.append(path)
                return granted
        sleep(0.2)


def release(path=None):
    """Drop this process's lease (the newest, or `path`). Safe to call twice."""
    p = path or (_held.pop() if _held else None)
    if path and path in _held:
        _held.remove(path)
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


@contextlib.contextmanager
def admitted(want, kind="test", **kw):
    granted = acquire(want, kind, **kw)
    path = _held[-1]
    try:
        yield granted
    finally:
        release(path)


# ---------------------------------------------------------------- CLI

def main(argv=None, run=subprocess.call, **kw):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: admission.py --slots N [--kind test|build] [--timeout S] -- <cmd> [args]", file=sys.stderr)
        return 2
    i = argv.index("--")
    opts, cmd = argv[:i], argv[i + 1:]
    want, kind, timeout = os.cpu_count() or 1, "test", 0
    try:
        j = 0
        while j < len(opts):
            if opts[j] == "--slots":
                want = int(opts[j + 1])
            elif opts[j] == "--kind":
                kind = opts[j + 1]
            elif opts[j] == "--timeout":
                timeout = float(opts[j + 1])
            else:
                raise ValueError(opts[j])
            j += 2
    except (ValueError, IndexError):
        print("bad options", file=sys.stderr)
        return 2
    if not cmd:
        return 2
    with admitted(want, kind, timeout=timeout, **kw) as n:
        env = dict(os.environ)
        env[ENV_SLOTS] = str(n)
        return run(cmd, env=env)


if __name__ == "__main__":
    sys.exit(main())
