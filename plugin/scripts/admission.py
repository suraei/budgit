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
BUSY_FREE_MB = 1024                                # ... or memory pressure/swap-ins/compressor; truly free (never inactive) under 1024 MB only where the OS gives no pressure reading
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


# ---------------------------------------------------------------- R-889b: kernel CPU counters and memory pressure
# Why: on 9 Nov 16:25 a Mac was at 91-95 % CPU with 158 MB free, a 5.6 GB compressor and swap-ins, and busy said "not busy".
# The ps sum misses kernel_task (compressor and swap time) and processes that already exited; free + inactive hides pressure.

BUSY_PRESSURE_LEVEL = 2                # macOS kern.memorystatus_vm_pressure_level 1 normal, 2 warn, 4 critical: warn is busy
BUSY_PSI_SOME_AVG10 = 10.0             # Linux PSI: some task stalled on memory 10 % of the last 10 s is lag a user feels
BUSY_WIN_MEMLOAD_PCT = 90              # Windows dwMemoryLoad: under 10 % of RAM available (standby counted) means paging
BUSY_SWAPIN_MB_S = 1.0                 # swap-ins averaged over the whole window: the working set no longer fits in RAM
BUSY_COMPRESSOR_RAM_FRACTION = 0.30    # macOS compressor holding 30 % of RAM: the kernel is already squeezing live memory
BUSY_FREE_RAM_FRACTION = 0.03          # the truly-free floor scales with RAM (3 %), never under BUSY_FREE_MB
FREE_TRUE_ONLY_WITHOUT_PRESSURE = True # free pages stay low by design beside a file cache (a calm Mac, 9 Nov 16:49: 432 MB
                                       # free, pressure normal, 21-34 % CPU), so where the kernel reports pressure it decides
PRESSURE_NAMES = {1: "normal", 2: "warn", 4: "critical"}
PRIME_S = 0.5                          # the first CPU sample reads the counters twice this far apart
MB = 1024 * 1024


def _mac_ticks():
    import ctypes
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    lib.mach_host_self.restype = ctypes.c_uint32
    if not _HOST:
        _HOST.append(lib.mach_host_self())             # one send right, kept for the process
    info, count = (ctypes.c_uint32 * 4)(), ctypes.c_uint32(4)
    if lib.host_statistics(_HOST[0], 3, info, ctypes.byref(count)) != 0:     # 3 = HOST_CPU_LOAD_INFO
        return None
    user, system, idle, nice = info
    return user + system + nice, idle, 2 ** 32        # uint32 tick counters wrap; deltas are taken modulo 2**32


_HOST = []


def _win_ticks():
    import ctypes
    from ctypes import wintypes
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None

    def n(t):
        return (t.dwHighDateTime << 32) | t.dwLowDateTime
    return n(kernel) + n(user) - n(idle), n(idle), None   # kernel time includes idle time


def cpu_ticks(platform=sys.platform, readfile=None, quota=None, now=time.monotonic):
    """Cumulative CPU counters of the whole machine, or of this container when it has a quota: (busy, idle, wrap) or None.
    macOS host_statistics, Linux cgroup cpu.stat (quota) else /proc/stat, Windows GetSystemTimes. Never raises."""
    rd = readfile or _read
    try:
        if platform == "darwin":
            return _mac_ticks()
        if platform.startswith("linux"):
            if quota:
                try:
                    for line in rd("/sys/fs/cgroup/cpu.stat").splitlines():
                        if line.startswith("usage_usec "):
                            used = int(line.split()[1])
                            return used, now() * 1e6 * quota - used, None
                except OSError:
                    pass
            f = [int(x) for x in rd("/proc/stat").splitlines()[0].split()[1:9]]
            f += [0] * (8 - len(f))
            return f[0] + f[1] + f[2] + f[5] + f[6] + f[7], f[3] + f[4], None     # idle + iowait are not busy
        if platform.startswith("win"):
            return _win_ticks()
    except Exception:                                  # no ctypes, no file, odd text: unknown, never a crash
        return None
    return None


class SystemCPU:
    """Sampler for cpu_median: % of all (usable) cores busy since the previous call, from kernel counters, so kernel time
    and processes that already exited count. The first call primes over PRIME_S. No counters: `fallback` (the old ps sum)
    and .source becomes "ps". One instance per check, so the median covers the whole window."""

    def __init__(self, ticks=None, sleep=time.sleep, fallback=None):
        if ticks is None:
            q = cgroup_quota() if sys.platform.startswith("linux") else None
            ticks = lambda: cpu_ticks(quota=q)
        self.ticks, self.sleep, self.fallback, self.last, self.source = ticks, sleep, fallback, None, "kernel"

    def __call__(self):
        now = self.ticks()
        if now is not None and self.last is None:
            self.last = now
            self.sleep(PRIME_S)
            now = self.ticks()
        if now is None:
            self.source = "ps"
            return self.fallback() if self.fallback else None
        a, self.last = self.last, now
        db, di = now[0] - a[0], now[1] - a[1]
        if now[2]:
            db, di = db % now[2], di % now[2]
        total = db + di
        return None if total <= 0 else max(0.0, min(100.0, 100.0 * db / total))


def _num(text):
    try:
        return int(str(text).strip().rstrip("."))
    except (ValueError, TypeError):
        return None


def _kv(text):
    """'Key: 123 kB' or 'key 123' lines -> {key: 123}."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2:
            n = _num(parts[1])
            if n is not None:
                out[parts[0]] = n
    return out


def vm_stat_pages(text):
    """vm_stat text -> {counter name: pages} plus "page" (bytes per page)."""
    out = {"page": 4096}
    for line in (text or "").splitlines():
        if "page size of" in line:
            out["page"] = _num(line.split("page size of")[1].split()[0]) or 4096
        elif ":" in line:
            k, v = line.split(":", 1)
            n = _num(v)
            if n is not None:
                out[k.strip().strip('"')] = n
    return out


def psi_some_avg10(text):
    """PSI text (/proc/pressure/memory or a cgroup memory.pressure) -> the `some avg10` percent, or None."""
    for line in (text or "").splitlines():
        if line.startswith("some "):
            for tok in line.split():
                if tok.startswith("avg10="):
                    try:
                        return float(tok[6:])
                    except ValueError:
                        return None
    return None


def _win_mem(m):
    import ctypes
    u64 = ctypes.c_ulonglong

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", u64),
                    ("ullAvailPhys", u64), ("ullTotalPageFile", u64), ("ullAvailPageFile", u64), ("ullTotalVirtual", u64),
                    ("ullAvailVirtual", u64), ("ullAvailExtendedVirtual", u64)]
    s = Status()
    s.dwLength = ctypes.sizeof(Status)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
        m["winload"], m["total_mb"] = int(s.dwMemoryLoad), int(s.ullTotalPhys) // MB


MEM_KEYS = {"darwin": ("pressure", "free_true_mb", "compressor_mb", "swapins_bytes", "total_mb"),
            "linux": ("psi", "free_true_mb", "swapins_bytes", "total_mb"), "win": ("winload", "total_mb")}


def mem_reading(platform=sys.platform, run=_run, readfile=None, now=time.monotonic, page=None):
    """One instant memory picture read from the kernel. Keys may be None (unknown, that signal is skipped): pressure (macOS
    level), psi (Linux some avg10), winload (Windows %), free_true_mb (free pages only, never inactive), total_mb,
    compressor_mb, swapins_bytes (cumulative), page; "unknown" lists the signals this OS should give and did not. Never raises."""
    m = dict.fromkeys(("pressure", "psi", "winload", "free_true_mb", "total_mb", "compressor_mb", "swapins_bytes"))
    m["t"], m["page"] = now(), 4096
    kind = "darwin" if platform == "darwin" else "linux" if platform.startswith("linux") else "win" if platform.startswith("win") else None
    try:
        if kind == "darwin":
            m["pressure"] = _num(run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]))
            total = _num(run(["sysctl", "-n", "hw.memsize"]))
            m["total_mb"] = total // MB if total else None
            v = vm_stat_pages(run(["vm_stat"]))
            m["page"] = v["page"]
            for key, name in (("free_true_mb", "Pages free"), ("compressor_mb", "Pages occupied by compressor")):
                if name in v:
                    m[key] = v[name] * v["page"] // MB
            if "Swapins" in v:
                m["swapins_bytes"] = v["Swapins"] * v["page"]
        elif kind == "linux":
            rd = readfile or _read

            def get(path):
                try:
                    return rd(path)
                except OSError:
                    return None
            for path in ("/sys/fs/cgroup/memory.pressure", "/proc/pressure/memory"):    # the container's own first
                m["psi"] = psi_some_avg10(get(path))
                if m["psi"] is not None:
                    break
            info = _kv(get("/proc/meminfo"))
            avail = info.get("MemAvailable", info.get("MemFree"))      # MemFree stays low next to the page cache: MemAvailable first
            m["free_true_mb"] = avail // 1024 if avail is not None else None
            m["total_mb"] = info["MemTotal"] // 1024 if "MemTotal" in info else None
            m["page"] = page or (os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096)
            vm = _kv(get("/proc/vmstat"))
            if "pswpin" in vm:
                m["swapins_bytes"] = vm["pswpin"] * m["page"]
        elif kind == "win":
            _win_mem(m)
    except Exception:                                  # no ctypes, odd text: what was read stays, the rest is unknown
        pass
    m["unknown"] = [k for k in MEM_KEYS.get(kind, ()) if m.get(k) is None]
    if kind == "win":
        m["unknown"].append("swapins_bytes")           # Windows gives no swap-in counter here
    return m


def mem_signals(first, last):
    """Two mem_readings (start and end of the CPU window) -> the last picture plus swapin_mb_s and swapin_pages_s, the
    average over the whole window (sustained by construction), or None when either counter is unknown."""
    sig = dict(last or {})
    a, b = (first or {}).get("swapins_bytes"), sig.get("swapins_bytes")
    dt = (sig.get("t") or 0) - ((first or {}).get("t") or 0)
    if a is not None and b is not None and dt > 0:
        rate = max(0, b - a) / dt
        sig["swapin_mb_s"], sig["swapin_pages_s"] = rate / MB, rate / (sig.get("page") or 4096)
    return sig


def free_true_limit(total_mb):
    """The truly-free floor: BUSY_FREE_RAM_FRACTION of RAM, never under BUSY_FREE_MB."""
    return max(BUSY_FREE_MB, int((total_mb or 0) * BUSY_FREE_RAM_FRACTION))


def memory_reason(mem):
    """Busy text from a memory picture (mem_reading or mem_signals), else None. Unknown readings are skipped."""
    if not mem:
        return None
    level = mem.get("pressure")
    if level is not None and level >= BUSY_PRESSURE_LEVEL:
        return "memory pressure %s" % PRESSURE_NAMES.get(level, level)
    psi = mem.get("psi")
    if psi is not None and psi >= BUSY_PSI_SOME_AVG10:
        return "memory pressure: some avg10 %.1f %% (limit %.0f %%)" % (psi, BUSY_PSI_SOME_AVG10)
    load = mem.get("winload")
    if load is not None and load >= BUSY_WIN_MEMLOAD_PCT:
        return "memory load %d %% (limit %d %%)" % (load, BUSY_WIN_MEMLOAD_PCT)
    rate = mem.get("swapin_mb_s")
    if rate is not None and rate >= BUSY_SWAPIN_MB_S:
        return "swap-in %.0f pages/s (%.1f MB/s, limit %.1f MB/s)" % (mem.get("swapin_pages_s") or 0, rate, BUSY_SWAPIN_MB_S)
    comp, total = mem.get("compressor_mb"), mem.get("total_mb")
    if comp is not None and total and comp >= total * BUSY_COMPRESSOR_RAM_FRACTION:
        return "compressor %.1f GB of %.0f GB RAM (limit %.0f %%)" % (comp / 1024.0, total / 1024.0, BUSY_COMPRESSOR_RAM_FRACTION * 100)
    known = any(mem.get(k) is not None for k in ("pressure", "psi", "winload"))
    free = mem.get("free_true_mb")
    if free is not None and not (known and FREE_TRUE_ONLY_WITHOUT_PRESSURE) and free < free_true_limit(total):
        return "truly free memory %d MB is under %d MB" % (free, free_true_limit(total))
    return None


def calm_reason(mem=None, sampler=None):
    """The not-busy text: "free", plus the signals that could not be read (they were skipped, never guessed)."""
    names = {"pressure": "memory pressure", "psi": "memory pressure", "winload": "memory load", "free_true_mb": "free memory",
             "compressor_mb": "compressor", "swapins_bytes": "swap-in", "total_mb": "RAM size"}
    unknown = sorted(set(names[k] for k in (mem or {}).get("unknown", ()) if k in names))
    if getattr(sampler, "source", None) == "ps":
        unknown.append("kernel CPU counters (ps sum used)")
    return "free" + ("; unknown: " + ", ".join(unknown) if unknown else "")


def read_machine(per_worker_mb=None, sample=None, sampler=None, sleep=time.sleep):
    """-> dict of the readings now. `sample` replaces the CPU reading; else the median of 3 short `sampler` readings
    (default: kernel counters, SystemCPU, with the ps sum as fallback)."""
    cores_all = os.cpu_count() or 1
    q = cgroup_quota() if sys.platform.startswith("linux") else None
    cores = usable_cores(quota=q)
    if sample is None:
        sample = cpu_median(sampler or SystemCPU(sleep=sleep, fallback=lambda: cpu_pct(cores=cores_all)), sleep)
    return {"cores": cores, "quota": q, "free_mb": free_mb(), "battery": on_battery(), "cpu_pct": sample, "mem": mem_reading()}


# ---------------------------------------------------------------- the one busy rule (admit.py calls these too)

def busy_reason(free_true, cpu, mem=None):
    """The busy rule, defined ONCE (R-532, R-889b). free_true: TRULY free MB (free pages, never inactive) or None, judged
    like mem's free_true_mb (only where no kernel pressure reading exists); cpu: median CPU as % of all usable cores or None;
    mem: a mem_reading or mem_signals picture or None. Free plus inactive memory is never a busy signal (R-889b round 2).
    -> the reason text naming the signal that fired, else None. A reading that is None is skipped. Memory is judged first."""
    if free_true is not None and (mem or {}).get("free_true_mb") is None:
        mem = dict(mem or {}, free_true_mb=free_true)
    why = memory_reason(mem)
    if why:
        return why
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
    if busy_reason(None, m.get("cpu_pct"), m.get("mem")):  # free_mb here is reclaimable: it sizes slots, never decides busy
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
