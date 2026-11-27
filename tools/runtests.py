#!/usr/bin/env python3
"""Process-pool test runner (R-618 F1). Standard library only, Python 3.8+.

Usage: runtests.py [--workers N] [--record] [module ...]
Modules are names like test_portfolio (default: all tests/test_*.py).

Each worker is a child of this file in --worker mode (it runs the modules with unittest) with its own TMPDIR, HOME,
BUDGIT_HOME, suite lock and git config, so tests cannot touch each other's state. Bytecode is cached outside the
repo in PYTHONPYCACHEPREFIX=<run root>/pycache, pre-warmed once, so children do not recompile studiolib.
Modules are packed greedy largest-first by tests/.durations.json (module -> seconds; median for unknown, file size
if the file is missing). --record writes the measured per-module walls back to it.
Output is merged unittest-shaped: `Ran N tests in S`, the FAIL/ERROR lines, then `OK` or `FAILED (...)`. Exit 1 on
any failure, on a count that differs from the per-worker sum, or on a discovered module with no result.
"""
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import namedtuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
DURATIONS = os.path.join(TESTS, ".durations.json")
TIMES_FILE = os.path.join(ROOT, ".budgit", "last-module-times.json")     # per-run wall and cpu_s per module, replaced each run
PARTIAL = False             # set by a run that tests only part of its selection: no receipt, and recorded as a gate run


def module_file(m):
    """tests/<m>.py; a dotted name (fixtures.homecache_probe, R-882) is a file under a tests sub-folder."""
    return os.path.join(TESTS, *m.split(".")) + ".py"


def discover(names):
    if names:
        return [n[:-3] if n.endswith(".py") else n for n in names]
    return sorted(f[:-3] for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py"))


def weights(mods):
    try:
        with open(DURATIONS) as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    known = {k: v for k, v in known.items() if not k.startswith("_")}      # "_peak_rss_mb" is metadata
    if known:
        med = statistics.median(known.values())
        return {m: float(known.get(m, med)) for m in mods}
    return {m: os.path.getsize(module_file(m)) / 1000.0 for m in mods}


def schedule(mods):
    """The one queue order: modules with no recorded duration first (largest file first, their cost is unknown), then
    the recorded ones longest first."""
    try:
        with open(DURATIONS) as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    known = {k: float(v) for k, v in known.items() if not k.startswith("_") and isinstance(v, (int, float))}
    def size(m):
        try:
            return os.path.getsize(module_file(m))
        except OSError:
            return 0
    unknown = sorted((m for m in mods if m not in known), key=lambda m: (-size(m), m))
    seen = sorted((m for m in mods if m in known), key=lambda m: (-known[m], m))
    return unknown + seen


def split(mods, n):
    w = weights(mods)
    buckets = [[0.0, []] for _ in range(n)]
    for m in sorted(mods, key=lambda m: -w[m]):
        b = min(buckets, key=lambda b: b[0])
        b[0] += w[m]
        b[1].append(m)
    return [b[1] for b in buckets if b[1]]


HOME_READ = re.compile(r"expanduser|os\.environ(?:\.get)?\W+[\"'](?:HOME|USERPROFILE|LOCALAPPDATA|APPDATA|XDG_\w+)[\"']"
                       r"|getenv\W+[\"'](?:HOME|USERPROFILE|LOCALAPPDATA|APPDATA|XDG_\w+)[\"']"
                       r"|Path\.home\(|Library/Caches|[\"']~[/\\\"']")


def home_readers(tests_dir=None):
    """-> sorted [(file, line number)] of tests/*.py lines that read HOME, `~`, XDG_*, LOCALAPPDATA or Library/Caches
    (a plain static scan; R-882, reused by R-883 and R-884). These are the modules whose result can depend on the
    per-worker HOME, so the pool-equals-serial proof must cover them."""
    tests_dir = tests_dir or TESTS
    found = []
    for name in sorted(os.listdir(tests_dir)):
        if not name.endswith(".py"):
            continue
        try:
            with open(os.path.join(tests_dir, name), encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if HOME_READ.search(line):
                        found.append((name, n))
        except OSError:
            continue
    return found


def list_home_readers(tests_dir=None):
    rows = home_readers(tests_dir)
    for name in sorted({r[0] for r in rows}):
        print("%s: lines %s" % (name, ",".join(str(n) for f, n in rows if f == name)))
    print("%d modules read HOME" % len({r[0] for r in rows}))
    return 0


# What a worker takes from its caller (R-1282): these and every LC_*, plus the runner's own variables below. Everything else (an app
# Terminal's CLAUDE_* and BUDGIT_*, tokens, a stale BUDGIT_HOME) stays out. A name is added here only when a test needs it.
ENV_KEEP = ("PATH", "LANG", "TERM", "USER", "LOGNAME", "SHELL", "BUDGIT_TEST_REAL_HOME",
            "SDKROOT", "MACOSX_DEPLOYMENT_TARGET", "DEVELOPER_DIR", "TOOLCHAINS", "SOURCE_DATE_EPOCH", "BROTE_FORCE_OS",   # tests/brotecache.py KEY_ENV (a test checks the lists match): they choose the compiler and feed the build-cache key, so a caller's value must reach the build
            "GATE_TEST_NESTED",         # R-1379 P1a: the marker tools/gate_test.run_full_suite sets; a gate run inside a worker never escalates again
            "BUDGIT_TEST_CACHE")        # BUDGIT_TEST_CACHE: test_runtests_home.test_a_cache_the_caller_set_is_kept needs a caller's Swift-build cache path to reach the workers
ENV_RUNNER = ("TMPDIR", "HOME", "BUDGIT_HOME", "BUDGIT_LANG", "BUDGIT_ADMIT_ADVICE", "BUDGIT_HOUSEKEEPING_LAUNCHCTL",
              "PYTHONHASHSEED", "PYTHONPYCACHEPREFIX", "PYTHONPATH", "BUDGIT_TEST_RC")
FAILED_LOGS = os.path.join(ROOT, ".budgit", "failed-logs")
STAMP = re.compile(r"^\d{8}-\d{6}-\d{6}$")          # the run folders under failed-logs: only these are ever deleted
KEEP_RUNS = 5
BLOCK_LINES, REPORT_LINES = 40, 200


def dropped_env_names():
    """Names (never values) of the caller's variables a worker does not get: not on the allowlist and not replaced by the runner."""
    return sorted(k for k in os.environ if k not in ENV_KEEP and not k.startswith("LC_") and k not in ENV_RUNNER)


def worker_env(base, i, rc=False):
    w = os.path.join(base, "w%d" % i)
    tmp = os.path.join(w, "tmp")
    home = os.path.join(w, "home")
    bhome = os.path.join(w, "budgit-home")
    for d in (tmp, home, bhome):
        os.makedirs(d)
    # The git guard asks when GIT_CONFIG_GLOBAL is set, and a test checks an unset identity: so no env var and no
    # identity; the worker's own HOME carries only the default branch.
    with open(os.path.join(home, ".gitconfig"), "w") as f:
        f.write("[init]\n\tdefaultBranch = main\n")
    # Start from an allowlist of the caller's environment, never the whole of it: no suite may depend on who launched it.
    # (Moot now, kept for the reasons: PYTHONDONTWRITEBYTECODE would stop the shared pycache; an inherited BUDGIT_SUITE_LOCK moves
    # the suite queue away from the state folder test_fairshare.QueueProcessTest reads, 3 failures at 90 s each.)
    env = {k: v for k, v in os.environ.items() if k in ENV_KEEP or k.startswith("LC_")}
    env.update({
        "TMPDIR": tmp, "HOME": home, "BUDGIT_HOME": bhome,
        "BUDGIT_LANG": "es", "BUDGIT_ADMIT_ADVICE": "off", "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off",
        "PYTHONHASHSEED": "0",
        "PYTHONPYCACHEPREFIX": os.path.join(base, "pycache"),
        "PYTHONPATH": TESTS + os.pathsep + os.path.join(ROOT, "plugin", "scripts"),
    })
    # No scratch BUDGIT_TEST_CACHE: the Swift builds live in the persistent per-machine folder, which brotecache finds
    # from the real home (the worker's own HOME is a scratch folder). A test that needs isolation sets its own cache.
    env["BUDGIT_TEST_REAL_HOME"] = os.environ.get("BUDGIT_TEST_REAL_HOME") or os.environ.get("HOME", "")
    if rc:
        env["BUDGIT_TEST_RC"] = "1"          # a full run or a recording: the compile tests run whatever changed
    return env, tmp


def walk_ids(suite):
    """-> (ids, broken): every test id the loader built, in order, and the names of modules that failed to load.
    Ids are the loader's own strings, so load_tests, skipped-at-import modules and generated class names read the same
    way here as when the test runs."""
    import unittest
    ids, broken = [], []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            elif t.id().startswith("unittest.loader._FailedTest"):
                broken.append(t.id().split(".")[-1])
            else:
                ids.append(t.id())
    walk(suite)
    return ids, broken


def load_all(mods):
    """The loader's suite for these modules. A module that raises SkipTest at import (loadTestsFromName lets that
    escape) becomes one skipped placeholder test, so it is collected and run under the same id and is not a false FAIL."""
    import unittest
    suite = unittest.TestSuite()
    for name in mods:
        try:
            suite.addTest(unittest.defaultTestLoader.loadTestsFromName(name))
        except unittest.SkipTest as e:
            reason = str(e)

            def skipped(self, reason=reason):
                self.skipTest(reason)
            case = type("ModuleSkipped", (unittest.TestCase,), {"test_module_skipped": skipped, "__module__": name})
            suite.addTest(case("test_module_skipped"))
    return suite


def discover_ids(argv):
    """--discover OUT.json module...: the loader's ids, written once up front, independent of how the pool splits them."""
    ids, broken = walk_ids(load_all(argv[1:]))
    with open(argv[0], "w") as f:
        json.dump({"ids": ids, "broken": broken}, f)
    return 0


def holder_target(test):
    """unittest reports a failed or skipped setUpClass/setUpModule through an _ErrorHolder whose description reads
    `setUpClass (module.Class)`. -> "module.Class" (or "module"), else None for an ordinary test."""
    if type(test).__name__ != "_ErrorHolder":
        return None
    desc = str(getattr(test, "description", test))
    if not desc.startswith(("setUpClass", "setUpModule")):       # tearDown* errors: the tests ran, nothing is excused
        return None
    m = re.search(r"\(([^)]*)\)", desc)
    return m.group(1) if m else None


def _dupes(ids):
    seen, dup = set(), []
    for i in ids:
        if i in seen and i not in dup:
            dup.append(i)
        seen.add(i)
    return dup


def check_ids(discovered, collected, ran=None, excused=()):
    """Test-id equality, both directions. Returns named messages (empty when everything agrees):
    duplicate id (collected or run twice), lost or extra against the loader's up-front list, and, once `ran` is given,
    collected-but-not-run (a lost or swapped id) and run-but-not-collected (an extra or swapped id)."""
    out = []

    def say(label, ids):
        if ids:
            out.append("ID CHECK: %s (%d): %s%s" % (label, len(ids), ", ".join(sorted(ids)[:10]), " ..." if len(ids) > 10 else ""))
    say("duplicate id collected more than once", _dupes(collected))
    say("discovered but never collected (lost id)", sorted(set(discovered) - set(collected)))
    say("collected but not discovered (extra id)", sorted(set(collected) - set(discovered)))
    if ran is not None:
        say("duplicate id run more than once", _dupes(ran))
        # ids under a class or module whose set-up failed never start: that is an error already counted, not a lost id
        lost = [i for i in set(collected) - set(ran) if not any(i.startswith(p + ".") for p in excused)]
        say("collected but not run (lost or swapped id)", sorted(lost))
        say("run but not collected (extra or swapped id)", sorted(set(ran) - set(collected)))
    return out


def worker(argv):
    """--worker OUT.json module...: run the modules, write {module: {wall, count}}."""
    import unittest
    out, mods = argv[0], argv[1:]
    child_by_module = {}

    class Res(unittest.TextTestResult):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.rec = {}
            self._t0 = 0.0
            self.child_top = 0.0
            self.holders = []             # "module.Class" or "module" whose set-up failed or skipped
            self.ran = []                 # [id, outcome] per test that started, in order; duplicates stay visible
            self.times = []               # (id, seconds) per test, startTest to stopTest (R-961)

        def _mark(self, outcome):
            if self.ran:
                self.ran[-1][1] = outcome

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self._mark("fail")

        def addError(self, test, err):
            super().addError(test, err)
            target = holder_target(test)
            if target:                    # setUpClass / setUpModule failed: a real error, and its tests never start
                self.holders.append(target)
            elif type(test).__name__ != "_ErrorHolder":      # a tearDown* holder is not the last test that ran
                self._mark("error")

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            target = holder_target(test)
            if target:                    # SkipTest in setUpClass: the class's tests never start either
                self.holders.append(target)
            else:
                self._mark("skip")

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            self._mark("xfail")

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            self._mark("xpass")

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                self._mark("fail")

        def startTest(self, test):
            super().startTest(test)
            self.ran.append([test.id(), "ok"])
            self._t0 = time.perf_counter()

        def stopTest(self, test):
            super().stopTest(test)
            r = self.rec.setdefault(test.id().split(".")[0], {"wall": 0.0, "count": 0})
            r["wall"] += time.perf_counter() - self._t0
            r["count"] += 1
            self.times.append((test.id(), time.perf_counter() - self._t0))
            # ru_maxrss of children only ever rises, so a module owns the value only when it raised it.
            now = child_peak_mb()
            if now > self.child_top:
                self.child_top = now
                child_by_module[test.id().split(".")[0]] = now

    suite = load_all(mods)
    # Collection report first, so the parent can abort early on a short or broken collection (count guard).
    ids, broken = walk_ids(suite)
    with open(out + ".collected", "w") as f:
        json.dump({"count": len(ids), "broken": broken, "ids": ids}, f)
    res = unittest.TextTestRunner(resultclass=Res, verbosity=0).run(suite)
    rec = dict(res.rec)
    rec["_ran"] = res.ran
    rec["_holders"] = res.holders
    rec["_slow"] = sorted(res.times, key=lambda kv: -kv[1])[:5]
    rec["_rss_mb"] = self_peak_mb()
    rec["_cpu_s"] = worker_cpu_s()
    rec["_child_mb"] = child_by_module
    with open(out, "w") as f:
        json.dump(rec, f)
    return 0 if res.wasSuccessful() else 1


SKIP_NAMES_SHOWN = 20       # skipped test names printed in the summary, then "+M more"
RSS_MARGIN = 1.25          # recorded worker peak x 1.25: later runs may hold a bit more than the one measured
HEAVY_CHILD_MB = 1024       # a module whose largest child passed this is "heavy": its child memory is reserved once


def rss_to_mb(raw, platform=None):
    """ru_maxrss to MB: bytes on macOS, kilobytes on Linux and the other Unixes."""
    platform = platform or sys.platform
    return round(raw / (1024.0 * 1024.0 if platform == "darwin" else 1024.0), 1)


def _rusage_mb(which):
    try:
        import resource
    except ImportError:                       # Windows: no `resource`, nothing measured
        return 0.0
    return rss_to_mb(resource.getrusage(getattr(resource, which)).ru_maxrss)


def worker_cpu_s():
    """CPU seconds this worker used, its own plus the children it waited for: resource.getrusage RUSAGE_SELF + RUSAGE_CHILDREN
    (user + system). One module per worker, so this is that module's cpu_s. 0.0 where `resource` is missing."""
    try:
        import resource
    except ImportError:
        return 0.0
    total = 0.0
    for which in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN):
        u = resource.getrusage(which)
        total += u.ru_utime + u.ru_stime
    return round(total, 2)


def write_module_times(path, kind, n, rows):
    """The per-run times file: {"kind", "workers", "time", "modules": {module: {"wall", "cpu_s"}}}, replaced whole each run."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump({"kind": kind, "workers": n, "time": int(time.time()), "modules": rows}, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        return False                    # a times file is a report, never a reason to fail a run
    return True


MODULE_LOG = TIMES_FILE + "l"      # last-module-times.jsonl: one line per module, appended the moment its worker ends (R-1379 P1a)


def start_module_log(path=None):
    """Empty the per-module log at the start of a run; False when it cannot be written (never a reason to fail a run)."""
    try:
        os.makedirs(os.path.dirname(path or MODULE_LOG), exist_ok=True)
        with open(path or MODULE_LOG, "w"):
            pass
    except OSError:
        return False
    return True


def note_module_done(module, wall, code, path=None):
    """Append {"module", "wall", "exit", "time"} for one finished module. A run killed after hours still names its slow modules."""
    try:
        with open(path or MODULE_LOG, "a") as f:
            f.write(json.dumps({"module": module, "wall": round(wall, 2), "exit": code, "time": int(time.time())}) + "\n")
    except OSError:
        return False
    return True


def module_exited(p, module, started, ends, path=None):
    """The end-of-worker hook: wait for `p`, record when it ended and append its line to the per-module log."""
    p.wait()
    ends[p.pid] = time.time()
    note_module_done(module, ends[p.pid] - started, p.returncode, path)


def self_peak_mb():
    """This worker process's own peak resident memory (RUSAGE_SELF), not its children's."""
    return _rusage_mb("RUSAGE_SELF")


def child_peak_mb():
    """Largest descendant this process has waited for so far (RUSAGE_CHILDREN)."""
    return _rusage_mb("RUSAGE_CHILDREN")


def check_collection(collected, discovered, broken):
    """Count guard: a message when the collection is short of the static count or an import failed, else None."""
    if broken:
        return "collection broke on import or load: %s" % ", ".join(sorted(set(broken))[:10])
    if collected < discovered:
        return "collected %d tests, fewer than the %d found by scanning the files" % (collected, discovered)
    return None


# ---- dynamic worker count (R-618, I-1255: it must follow the environment, not a fixed number) ----

DEFAULT_RSS_MB = 300        # a default, used only until a run has recorded a real per-worker peak


def usable_cpus(process_cpu_count=getattr(os, "process_cpu_count", None),
                sched_getaffinity=getattr(os, "sched_getaffinity", None), cpu_count=os.cpu_count):
    """CPUs this process may use: process_cpu_count (3.13+), else the affinity mask (Linux), else cpu_count."""
    if process_cpu_count:
        return process_cpu_count() or 1
    if sched_getaffinity:
        return len(sched_getaffinity(0)) or 1
    return cpu_count() or 1


def cgroup_cpu_limit(read=None):
    """CPU quota of a Linux container as a number of cores, or None. cgroup v2 cpu.max, then v1 cfs quota/period."""
    def rd(path):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return None
    read = read or rd
    v2 = read("/sys/fs/cgroup/cpu.max")
    if v2:
        parts = v2.split()
        try:
            if parts[0] != "max":
                return int(parts[0]) / float(parts[1])
        except (ValueError, IndexError, ZeroDivisionError):
            pass
        return None
    q, p = read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    try:
        if q and p and int(q) > 0 and int(p) > 0:
            return int(q) / float(p)
    except ValueError:
        pass
    return None


def _admission():
    """The owner's one busy rule and readers (plugin/scripts/admission.py): imported here so there is a single copy."""
    d = os.path.join(ROOT, "plugin", "scripts")
    if d not in sys.path:
        sys.path.insert(0, d)
    import admission
    return admission


def decide_workers(cpus, cores, quota=None, cpu_pct=None, free_mb=None, rss_mb=None, away=False, override=None,
                   heavy_mb=0, battery=None, mem=None, battery_note=None):
    """-> (n, reason). Pure: every input is passed in.
    cpus usable CPUs; cores all CPUs of the machine; quota cgroup core limit or None; cpu_pct the other processes' CPU as
    a percent of all cores (the owner's busy rule reads the same figure) or None; free_mb free+inactive memory or None
    (the pool's own workers already added back by read_load); rss_mb recorded per-worker peak or None (then
    DEFAULT_RSS_MB, said so); away or battery halves the count (battery None = unknown = no change). The count is
    min(usable CPUs, free_mb // per-worker peak), never below MIN_WORKERS: busy CPU or memory is said, never a reason for
    one worker. Waiting for memory is await_memory's job. Only an override can give 1. No fixed cap."""
    if override is not None:
        return max(1, int(override)), "override"
    adm = _admission()
    usable = float(cpus)
    why = ["%d usable cpu" % cpus]
    if quota is not None and quota < usable:
        usable = max(1.0, quota)
        why.append("cgroup quota %.1f" % quota)
    n = max(1, int(usable) - 1)               # one core stays free; other processes' load does not shrink the pool
    why.append("1 core free")
    if cpu_pct is not None:
        why.append("%.0f%% busy by others" % cpu_pct)
    else:
        why.append("load unreadable")
    cpu_busy = adm.busy_reason(None, cpu_pct)
    if cpu_busy:
        why.append("busy: " + cpu_busy + " (does not lower the floor of %d)" % MIN_WORKERS)
    mem_busy = adm.busy_reason(None, None, mem)  # pressure, swap-ins, compressor, truly free; never free+inactive
    if mem_busy:
        why.append("memory busy: " + mem_busy + " (does not lower the floor of %d)" % MIN_WORKERS)
    if free_mb is not None:
        per = rss_mb if rss_mb else DEFAULT_RSS_MB
        n_mem = memory_fit(free_mb, per, heavy_mb)
        if n_mem < n:
            n = n_mem
        why.append("memory %d MB free / %d MB per worker%s%s" % (free_mb, per, "" if rss_mb else " (default)",
                                                                  ", %d MB reserved once for a heavy module" % heavy_mb if heavy_mb else ""))
    if away or battery:
        n = n // 2
        why.append("away: halved" if away else (battery_note or "battery: halved"))
    elif battery_note:
        why.append(battery_note)
    return max(MIN_WORKERS, n), ", ".join(why)


MIN_WORKERS = 2             # the pool never picks fewer; only --workers by hand can ask for 1
MEM_WAIT_SECS = 300.0       # how long the runner waits for memory for MIN_WORKERS before it reports and stops
MEM_POLL_SECS = 5.0


def memory_fit(free_mb, per_mb, heavy_mb=0):
    """How many workers of `per_mb` fit in `free_mb` (a heavy child's memory is reserved once, not per worker)."""
    return int((free_mb - _admission().RESERVE_MB - heavy_mb) // max(1.0, per_mb))


def await_memory(read, rss_mb, heavy_mb=0, timeout=None, poll=None, sleep=time.sleep, clock=time.monotonic, say=print):
    """Wait, bounded, until MIN_WORKERS workers fit in free memory. `read()` -> free_mb or None (unreadable: no wait).
    -> (True, reason) once they fit, (False, reason) after `timeout` s. Never answers with a single worker."""
    per = rss_mb if rss_mb else DEFAULT_RSS_MB
    timeout = MEM_WAIT_SECS if timeout is None else timeout
    poll = MEM_POLL_SECS if poll is None else poll
    deadline = clock() + timeout
    said = False
    while True:
        free = read()
        if free is None or memory_fit(free, per, heavy_mb) >= MIN_WORKERS:
            return True, "memory fits %d workers" % MIN_WORKERS
        res = _admission().RESERVE_MB
        why = "%d MB free, %d MB needed for %d workers of %d MB (with the %d MB reserve)" % (
            free, per * MIN_WORKERS + heavy_mb + res, MIN_WORKERS, per, res)
        if clock() >= deadline:
            return False, why
        if not said:
            say("waiting for memory (up to %d s): %s" % (timeout, why))
            said = True
        sleep(poll)


# ---- battery policy (R-854, I-1067): on battery the pool halves only under LOW_BATTERY_PCT or in Low Power Mode ----
LOW_BATTERY_PCT = 30


def battery_charge(platform=sys.platform, run=None, listdir=os.listdir, readfile=None):
    """Charge in percent, or None when it cannot be read (Windows, desktops, any failure). Never raises."""
    try:
        if platform == "darwin":
            out = (run or _adm_run)(["pmset", "-g", "batt"]) or ""
            m = re.search(r"(\d+)%", out)
            return int(m.group(1)) if m else None
        if platform.startswith("linux"):
            base = "/sys/class/power_supply"
            rd = readfile or (lambda p: open(p).read())
            for n in sorted(listdir(base)):
                if n.startswith("BAT"):
                    return int(rd(base + "/" + n + "/capacity").strip())
    except Exception:
        return None
    return None


def low_power_mode(platform=sys.platform, run=None, readfile=None):
    """True when Low Power Mode is on, False when off, None when unknown. macOS `pmset -g`; Linux platform_profile."""
    try:
        if platform == "darwin":
            out = (run or _adm_run)(["pmset", "-g"]) or ""
            m = re.search(r"lowpowermode\s+(\d)", out)
            return m.group(1) == "1" if m else None
        if platform.startswith("linux"):
            rd = readfile or (lambda p: open(p).read())
            return rd("/sys/firmware/acpi/platform_profile").strip() == "low-power"
    except Exception:
        return None
    return None


def _adm_run(cmd):
    return _admission()._run(cmd)


def battery_policy(on_batt, charge=None, low_power=None):
    """-> (halve, note). Halve only on battery AND (charge under LOW_BATTERY_PCT or Low Power Mode). Unknown = no change."""
    if not on_batt:
        return False, None
    if low_power:
        return True, "battery: Low Power Mode: halved"
    if charge is None:
        return False, "battery: charge unknown: full"
    if charge < LOW_BATTERY_PCT:
        return True, "battery %d %%: halved" % charge
    return False, "battery %d %%: full" % charge


def recorded_rss():
    try:
        with open(DURATIONS) as f:
            return float(json.load(f).get("_peak_rss_mb") or 0) or None
    except (OSError, ValueError, TypeError):
        return None


def heavy_reserve_mb(mods, by_module=None):
    """The largest recorded child peak among `mods` that passed HEAVY_CHILD_MB, else 0. Reserved once."""
    if by_module is None:
        try:
            with open(DURATIONS) as f:
                by_module = json.load(f).get("_peak_child_mb_by_module") or {}
        except (OSError, ValueError, TypeError):
            by_module = {}
    return max([v for m, v in by_module.items() if m in mods and v > HEAVY_CHILD_MB] or [0])


def process_table():
    """-> [(pid, ppid, cpu_pct, rss_kb)] from ONE ps call (macOS, Linux); [] when it cannot be read."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,%cpu=,rss="], capture_output=True, text=True, timeout=10).stdout
        rows = []
        for line in out.splitlines():
            p = line.split()
            if len(p) == 4:
                rows.append((int(p[0]), int(p[1]), float(p[2]), int(p[3])))
        return rows
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def own_tree(rows, root):
    """The set of pids of `root` and all its descendants. Pure: `rows` is process_table()."""
    kids = {}
    for r in rows:
        kids.setdefault(r[1], []).append(r[0])
    tree, todo = {root}, [root]
    while todo:
        for k in kids.get(todo.pop(), ()):
            if k not in tree:
                tree.add(k)
                todo.append(k)
    return tree


def own_tree_pct(rows, root, cores=None):
    """The summed %CPU of `root` and all its descendants, as a percent of all cores."""
    tree = own_tree(rows, root)
    return sum(r[2] for r in rows if r[0] in tree) / float(cores or os.cpu_count() or 1)


def own_tree_mb(rows, root):
    """The summed resident memory of `root` and all its descendants, in MB (rows with no rss column count 0)."""
    tree = own_tree(rows, root)
    return sum(r[3] for r in rows if r[0] in tree and len(r) > 3) / 1024.0


Load = namedtuple("Load", "cpu_pct free_mb away own_mb battery mem battery_note", defaults=(False, 0.0, None, None, None))


def _is_away():
    try:
        import away as away_mod
        return bool(away_mod.is_away())
    except Exception:
        return False


def read_load(table=process_table, pid=None, free=None, battery=None, away=None, sleep=time.sleep, cores=None,
              samples=3, step=0.5, system=None, mem=None, charge=None, low_power=None):
    """-> Load(cpu_pct, free_mb, away, own_mb, battery, mem, battery_note) as the machine reads now; None where a reading is unavailable.
    R-889b: the machine total is the kernel CPU counters (admission.SystemCPU: kernel time and exited processes count);
    this runner's own tree is subtracted from ps rows (`table`), because kernel counters give no per-tree figure. The
    figure is the OTHER processes' load, the median of `samples` short samples. `mem` is admission.mem_reading (pressure,
    swap-ins, truly free); free_mb (reclaimable, own workers added back) only sizes the pool, never decides busy.
    Every reader is injectable: a caller that injects `table` without `system` takes the total from its rows, and one
    that injects `free` without `mem` supplies no memory picture, so no live reading is taken. R-854: `battery` in the
    Load is the halve decision (battery_policy) and battery_note says why; charge and low_power readers are injectable."""
    adm = _admission()
    cores = cores or os.cpu_count() or 1
    root = pid if pid is not None else os.getpid()
    own = []
    if system is None and table is process_table:
        system = adm.SystemCPU(sleep=sleep)

    def sample():
        rows = table()
        if not rows:
            return None
        own.append(own_tree_mb(rows, root))
        total = system() if system else None
        if total is None:
            total = sum(r[2] for r in rows) / float(cores)
        return max(0.0, total - own_tree_pct(rows, root, cores))

    cpu_pct = adm.cpu_median(sample, sleep, samples, step)
    own_mb = own[-1] if own else 0.0
    free_now = (free or adm.free_mb)()
    if free_now is not None:
        free_now = int(free_now + own_mb)
    live = table is process_table and not free                  # any injected reader means an injected machine: no live read
    picture = (mem or (adm.mem_reading if live else (lambda: {})))()
    on_batt = (battery or adm.on_battery)()
    if on_batt:          # the charge and Low Power readers run only on battery
        halve, note = battery_policy(True, (charge or battery_charge)(), (low_power or low_power_mode)())
    else:
        halve, note = battery_policy(on_batt)
    return Load(cpu_pct, free_now, (away or _is_away)(), own_mb, halve, picture, note)


def choose_workers(flag=None, env=None, mods=(), read=None):
    """The worker count for this machine now. Override: --workers, else BUDGIT_TEST_WORKERS. `read` returns a Load (or
    a tuple of its leading fields); injectable, so tests need no real load."""
    env = os.environ if env is None else env
    given = flag if flag is not None else env.get("BUDGIT_TEST_WORKERS")
    try:
        if given not in (None, ""):
            return decide_workers(1, 1, override=int(given))
    except ValueError:
        pass
    ld = Load(*(read or read_load)())
    return decide_workers(usable_cpus(), os.cpu_count() or 1, cgroup_cpu_limit(), ld.cpu_pct, ld.free_mb, recorded_rss(),
                          ld.away, heavy_mb=heavy_reserve_mb(mods), battery=ld.battery, mem=ld.mem,
                          battery_note=ld.battery_note)


def busy_now(env=None, mods=(), read=None):
    """-> (busy, reason). The owner's busy rule (admission.busy_reason): median CPU of the other processes above 85 % of
    all cores, or a memory signal (pressure, swap-ins, compressor, truly free); never free+inactive (R-889b). An override
    is never busy."""
    env = os.environ if env is None else env
    if env.get("BUDGIT_TEST_WORKERS") not in (None, ""):
        return False, "override"
    ld = Load(*(read or read_load)())
    why = _admission().busy_reason(None, ld.cpu_pct, ld.mem)
    return (True, why) if why else (False, "calm")


def damped(choose, initial, need=2):
    """Hysteresis around `choose(rest) -> (n, why)`: a lower count is passed on only after `need` consecutive reads
    below the current one, a higher count only after `need` consecutive reads above it; one odd read changes nothing."""
    state = {"cur": initial, "dir": 0, "streak": 0}

    def pick(rest):
        n, why = choose(rest)
        d = (n > state["cur"]) - (n < state["cur"])
        if d == 0:
            state["dir"], state["streak"] = 0, 0
            return state["cur"], why
        state["streak"] = state["streak"] + 1 if d == state["dir"] else 1
        state["dir"] = d
        if state["streak"] >= need:
            state["cur"], state["dir"], state["streak"] = n, 0, 0
        return state["cur"], why
    return pick


QUEUE_INTERVAL = 15.0       # seconds between worker-count readings while modules remain
STALL_WINDOW = 60.0         # a worker that has not reported its collection and gained no CPU time for this long has stalled
STALL_UNKNOWN_WINDOW = 600.0    # wall-clock fallback when a worker's CPU time cannot be read
STALL_EVERY = 5.0           # seconds between CPU-time readings of one worker
TERM_GRACE = 5.0            # seconds a worker's process group gets after SIGTERM before SIGKILL


def parse_cputime(text):
    """ps `time` -> seconds. Formats: M:SS.ff (macOS), [[D-]HH:]MM:SS (Linux). None when it does not parse."""
    try:
        days = 0
        text = (text or "").strip()
        if "-" in text:
            d, text = text.split("-", 1)
            days = int(d)
        secs = 0.0
        for part in text.split(":"):
            secs = secs * 60 + float(part)
        return days * 86400 + secs
    except ValueError:
        return None


def cpu_seconds(pid):
    """CPU time used so far by process `pid`, in seconds, or None (ps missing, process gone, Windows)."""
    try:
        out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_cputime(out)


class StallWatch:
    """Has a worker stalled? Only when its CPU time has not grown for `window` s, not because a wall clock ran out: a
    niced or busy machine slows a worker without stalling it. CPU time is read at most every `every` s per worker.
    When it cannot be read, a long wall-clock limit applies instead. Clock and reader are injected."""

    def __init__(self, cpu=cpu_seconds, clock=time.time, window=STALL_WINDOW, unknown_window=STALL_UNKNOWN_WINDOW,
                 every=STALL_EVERY):
        self.cpu, self.clock, self.window, self.unknown, self.every = cpu, clock, window, unknown_window, every
        self.seen = {}

    def stalled(self, key, started):
        now = self.clock()
        rec = self.seen.get(key)
        if rec is not None and now - rec["read_at"] < self.every:
            return rec["stalled"]
        c = self.cpu(key)
        if rec is None:
            rec = self.seen[key] = {"cpu": c, "moved": started, "read_at": now, "stalled": False}
        rec["read_at"] = now
        if c is None:
            rec["stalled"] = now - started > self.unknown
        elif rec["cpu"] is None or c > rec["cpu"] + 1e-9:
            rec["cpu"], rec["moved"], rec["stalled"] = c, now, False
        else:
            rec["stalled"] = now - rec["moved"] > self.window
        return rec["stalled"]


def spawn(argv, **kw):
    """Popen in its own session (POSIX), so the worker and everything it starts share one process group to clean up."""
    if os.name == "posix":
        kw.setdefault("start_new_session", True)
    return subprocess.Popen(argv, **kw)


def _signal_group(p, sig):
    """Send `sig` to p's whole process group (pgid == pid because of start_new_session); on non-POSIX only to p."""
    try:
        if os.name == "posix" and hasattr(os, "killpg"):
            os.killpg(p.pid, sig)
        elif p.poll() is None:
            p.terminate() if sig == signal.SIGTERM else p.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _group_alive(p):
    if p.poll() is None:
        return True
    if os.name == "posix" and hasattr(os, "killpg"):
        try:
            os.killpg(p.pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False
    return False


def reap(procs, grace=TERM_GRACE, sleep=time.sleep, clock=time.monotonic):
    """Stop every worker's process group: SIGTERM, wait up to `grace` s (bounded), SIGKILL what is left, then wait."""
    for p in procs:
        _signal_group(p, signal.SIGTERM)
    deadline = clock() + grace
    while clock() < deadline and any(_group_alive(p) for p in procs):
        sleep(0.05)
    for p in procs:
        if _group_alive(p):
            _signal_group(p, getattr(signal, "SIGKILL", signal.SIGTERM))
    for p in procs:
        try:
            p.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            pass


def plain_path(path):
    """R-1379 P1a: the same folder named without a macOS "/private" prefix ("/private/var/..." -> "/var/..."), when that name
    reaches the very same folder; else `path` unchanged. The gate hands the runner a resolved TMPDIR (it must be sure the folder is
    outside Documents, Desktop and iCloud), and that name is 8 characters longer and is not the name a plain TMPDIR gives: tests
    that cut a path at 80 characters (the HOME guard message) or strip "/private" from output (githooks status) were written
    against the plain name, so the workers use it."""
    if not path.startswith("/private/"):
        return path
    short = path[len("/private"):]
    try:
        return short if os.path.samefile(short, path) else path
    except OSError:
        return path


def supervise(run, grace=TERM_GRACE):
    """Run `run(base, live)` with a fresh temp base dir. `live` collects every process the run starts. However the run
    ends (return, exception, Ctrl-C, SIGTERM to this process) the workers' groups are terminated, waited for, killed,
    and only then the base dir is removed."""
    base = plain_path(tempfile.mkdtemp(prefix="runtests-"))
    live = []
    old = None
    if threading.current_thread() is threading.main_thread() and hasattr(signal, "SIGTERM"):
        def on_term(signum, frame):
            raise SystemExit(128 + signum)           # unwinds through the finally below
        try:
            old = signal.signal(signal.SIGTERM, on_term)
        except (ValueError, OSError):
            old = None
    try:
        return run(base, live)
    finally:
        try:
            reap(live, grace)
        finally:
            shutil.rmtree(base, ignore_errors=True)
            if old is not None:
                signal.signal(signal.SIGTERM, old)


def run_queue(queue, launch, choose, initial, limit, clock=time.time, sleep=time.sleep, interval=QUEUE_INTERVAL, poll=0.2,
              say=print, stop=lambda: False):
    """Workers pull from one queue. `launch(module)` starts one and returns an object with poll(); `choose(remaining)`
    -> (n, why) is re-read every `interval` s while modules remain. A bigger n starts more workers; a smaller n only
    starts none (a running worker is never killed). Each change says `workers N -> M: why`. -> (peak, finished) where
    finished is the list of launched modules in launch order. Every interval and the clock are parameters."""
    queue = list(queue)
    running = []                      # (module, handle)
    launched = []
    target, peak = initial, 0
    next_read = clock() + interval
    while queue or running:
        if stop():
            break
        now = clock()
        if queue and now >= next_read:
            next_read = now + interval
            new, why = choose(list(queue))
            new = max(1, min(new, limit))
            if new != target:
                say("workers %d -> %d: %s" % (target, new, why))
                target = new
        running = [(m, h) for m, h in running if h.poll() is None]
        while queue and len(running) < target:
            m = queue.pop(0)
            running.append((m, launch(m)))
            launched.append(m)
            peak = max(peak, len(running))
        if queue or running:
            sleep(poll)
    return peak, launched, [h for _, h in running]


# ---- I-1820: known flakes (one rerun, logged on the item) and the bisect of a red merge group ----

FLAKES = os.path.join(TESTS, "known_flakes.json")
RERUN_TIMEOUT = 300           # seconds one rerun of one test may take; past it the test counts as failed


class FlakeListError(Exception):
    pass


def parse_flakes(data):
    """The known-flake list (a list of {test, item, why}) or FlakeListError. Only a test id (module.Class.method) goes on
    it, never a whole module; the item must be an R-n id and every field is text."""
    if not isinstance(data, list):
        raise FlakeListError("known_flakes: expected a list of {test, item, why}")
    for e in data:
        if not isinstance(e, dict):
            raise FlakeListError("known_flakes: entry is not an object: %r" % (e,))
        test, item, why = e.get("test"), e.get("item"), e.get("why")
        if not (isinstance(test, str) and re.fullmatch(r"\w+(\.\w+){2,}", test)):
            raise FlakeListError("known_flakes: test must be module.Class.method, got %r" % (test,))
        if not (isinstance(item, str) and re.fullmatch(r"R-[0-9]+", item)):
            raise FlakeListError("known_flakes: %s needs an item like R-123, got %r" % (test, item))
        if not (isinstance(why, str) and why.strip()):
            raise FlakeListError("known_flakes: %s needs a one-line why" % test)
    return data


def load_flakes(path=None):
    try:
        with open(path or FLAKES) as f:
            return parse_flakes(json.load(f))
    except (OSError, ValueError) as exc:
        raise FlakeListError("known_flakes: cannot read %s: %s" % (path or FLAKES, exc))


def fail_id(line):
    """'FAIL: test_x (mod.Class)' (Python up to 3.10) or 'FAIL: test_x (mod.Class.test_x)' -> 'mod.Class.test_x', else None."""
    m = re.match(r"(?:FAIL|ERROR): (\w+) \(([\w.]+)\)", line)
    if not m:
        return None
    name, path = m.groups()
    return path if path.endswith("." + name) else path + "." + name


def settle_flakes(bad, flakes, rerun):
    """bad: FAIL/ERROR lines. -> (still_bad, flake_lines, reruns). A red test that is on the list is rerun ONCE through
    rerun(test_id) -> True only when it passed. Anything else (False, None, an exception, a test not on the list) stays bad.
    A KeyboardInterrupt is not caught: an interrupted run never reports a flake pass."""
    by_id = {f["test"]: f for f in flakes}
    verdict, still, lines, reruns = {}, [], [], 0
    for line in bad:
        tid = fail_id(line)
        if tid not in by_id:
            still.append(line)
            continue
        if tid not in verdict:
            reruns += 1
            try:
                verdict[tid] = rerun(tid) is True
            except Exception:
                verdict[tid] = False
            if verdict[tid]:
                lines.append("flake: %s (%s) passed on rerun" % (tid, by_id[tid]["item"]))
        if not verdict[tid]:
            still.append(line)
    return still, lines, reruns


def counted_red(out):
    """failures + errors from unittest's own `FAILED (failures=a, errors=b)` line, or -1 when that line holds anything else
    (unexpected successes, an unreadable line): then no exit code is ever forgiven."""
    m = re.search(r"^FAILED \((.*)\)\s*$", out, re.M)
    if not m:
        return -1
    total = 0
    for part in m.group(1).split(","):
        k, _, v = part.strip().partition("=")
        if k not in ("failures", "errors") or not v.isdigit():
            return -1
        total += int(v)
    return total


def flake_summary(n):
    return "flake reruns: %d" % n


def rerun_one(test_id, env, run=subprocess.run):
    """One test alone, same interpreter and the same kind of env as a worker. True only on exit 0 in time."""
    try:
        r = run([sys.executable, "-m", "unittest", test_id], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=RERUN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


class GitVcs:
    """The VCS calls of the bisect, in one place so tests inject a fake. Never rm: the worktree goes with `worktree remove`."""

    def __init__(self, root=None, run=subprocess.run):
        self.root, self.run_ = root or ROOT, run

    def _git(self, *a, cwd=None):
        r = self.run_(["git", "-C", cwd or self.root] + list(a), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      universal_newlines=True)
        if r.returncode != 0:
            raise RuntimeError("git %s failed" % " ".join(a[:2]))
        return r.stdout

    def rev_list(self, rng):
        return [x for x in self._git("rev-list", "--reverse", rng).split("\n") if x]

    def add(self, path, sha):
        self._git("worktree", "add", "--detach", path, sha)

    def checkout(self, path, sha):
        self._git("checkout", "--detach", sha, cwd=path)

    def remove(self, path):
        self._git("worktree", "remove", path)

    def run(self, path, modules):
        """True when the modules pass at the checkout, False when they fail, None when the run could not finish."""
        try:
            r = subprocess.run([sys.executable, os.path.join(path, "tools", "runtests.py")] + list(modules), cwd=path,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        return True if r.returncode == 0 else False if r.returncode == 1 else None


def bisect(rng, modules, vcs, path, say=print):
    """The first commit of BASE..TIP where `modules` go red (a red tip is checked first; commits are oldest first and the
    history is taken as green until one commit and red from it on). A run that could not finish (None) is an error, never
    a verdict. The temporary worktree is always removed."""
    commits = vcs.rev_list(rng)
    if not commits:
        say("no commits in " + rng)
        return 3
    vcs.add(path, commits[-1])
    lines, code = [], 3
    try:
        code = _bisect_steps(commits, modules, vcs, path, lines.append)
    finally:
        for line in lines:                           # the result first, whatever the cleanup does
            say(line)
        try:
            vcs.remove(path)
        except Exception as exc:                     # a failed cleanup never replaces the result; a sweep removes the leftover
            say("cleanup failed, leftover worktree: %s (%s)" % (path, exc))
    return code


def _bisect_steps(commits, modules, vcs, path, say):
    """The search itself -> the exit code; every line it prints goes through `say`."""
    here = [commits[-1]]

    def green(sha):
        if here[0] != sha:
            vcs.checkout(path, sha)
            here[0] = sha
        return vcs.run(path, modules)
    got = green(commits[-1])
    if got is None:
        say("ERROR: the run at the tip did not finish; no verdict")
        return 3
    if got:
        say("no red commit")
        return 0
    lo, hi = 0, len(commits) - 1                     # commits[hi] is red
    while lo < hi:
        mid = (lo + hi) // 2
        got = green(commits[mid])
        if got is None:
            say("ERROR: the run at %s did not finish; no verdict" % commits[mid][:12])
            return 3
        if got:
            lo = mid + 1
        else:
            hi = mid
    say("first red commit: " + commits[lo])
    return 0


def git(*a):
    r = subprocess.run(["git", "-C", ROOT] + list(a), stdout=subprocess.PIPE, universal_newlines=True)
    return r.stdout.split("\n") if r.returncode == 0 else []


def changed_files(base):
    """Files changed since base (default: merge-base with ac3b9f4f), committed or not, untracked included."""
    if not base:
        base = (git("merge-base", "HEAD", "ac3b9f4f") or [""])[0] or "ac3b9f4f"
    out = git("diff", "--name-only", base) + git("ls-files", "--others", "--exclude-standard")
    return sorted(set(f for f in out if f))


STATE_DIRS = (".studio/", ".budgit/", ".claude/", ".git/", "tests/red-runs/")
SHARED = ("tests/helpers.py", "tests/conftest.py", "tests/__init__.py", "tests/fixtures/", "tests/kit/")


FALLBACK_SHIPPED = ("plugin", "skills/budgit-kickoff", "share")
EXPORT_TEST_WORDS = ("leak", "manifest", "export")       # a test module whose name has one of these guards what ships


def shipped_trees():
    """The trees the exporter ships (export.SHIPPED_DIRS, read, not copied), as 'dir/' prefixes with '/' separators."""
    try:
        sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
        try:
            import export as exp
            dirs = tuple(exp.SHIPPED_DIRS)
        finally:
            sys.path.pop(0)
    except Exception:
        dirs = FALLBACK_SHIPPED
    return tuple(d.replace(os.sep, "/").rstrip("/") + "/" for d in dirs)


def export_tests(allm):
    """Every collected test module guarding the export (file-type edge, R-883 D): found by name, not by a hand list."""
    return {m for m in allm if any(w in m for w in EXPORT_TEST_WORDS)}


def voice_tests(texts):
    """R-1049: every test module that runs the exporter's owner-voice scan over the shipped tree, found by what it calls
    (its name may not say export): the tests a change to any shipped file must run."""
    return {m for m, text in texts.items() if "voice_hits" in text and "shipped_text_files" in text}


def select(files):
    """Returns (modules or None for the full suite, reason). Maps changed files to test modules."""
    allm = discover([])
    shipped = shipped_trees()
    guards = export_tests(allm)
    texts = {}
    for m in allm:
        with open(os.path.join(TESTS, m + ".py"), errors="replace") as f:
            texts[m] = f.read()
    sel, notes = set(), []
    docs = False
    for f in files:
        if f.startswith(STATE_DIRS) or f == "tests/.durations.json" or f.startswith("tests/bench_"):
            continue
        if f.startswith(SHARED):
            return None, "full suite: %s is shared test code" % f
        if f.startswith(shipped):          # any shipped path pulls in every leak, manifest and export test
            sel.update(guards)             # adds to the selection below, never narrows it (a full-suite fallback stays)
            sel.update(voice_tests(texts))     # R-1049: the real-tree owner-voice test, whatever its name
        if f == "plugin/hooks/hooks.json":
            return None, "full suite: hooks.json changed"
        if f.endswith(".md"):
            docs = True
            continue
        m = re.match(r"tests/(test_\w+)\.py$", f)
        if m:
            if m.group(1) in texts:
                sel.add(m.group(1))
            continue
        m = re.match(r"plugin/scripts/(\w+)\.py$", f)
        if m:
            x = m.group(1)
            pats = [re.compile(r"\bimport " + x + r"\b"), re.compile(r"\bfrom " + x + " ")]
            for t, text in texts.items():
                if t.startswith("test_" + x) or '"%s"' % x in text or any(p.search(text) for p in pats):
                    sel.add(t)
            continue
        if f == "plugin/bin/budgit":
            sel.update(t for t, text in texts.items() if "BUDGIT" in text or "bin/budgit" in text)
            continue
        if f.startswith("tools/") and f.endswith(".py"):
            # tools are not plugin code: only tests that name the tool file run (none today means nothing runs)
            sel.update(t for t, text in texts.items() if f in text)
            continue
        return None, "full suite: unknown non-doc file %s" % f
    if docs:
        sel.update(t for t in allm if t in ("test_release_evidence",) or "doc" in t)
    return sorted(sel), "%d changed file(s)" % len(files)


def main(argv):
    args = argv[1:]
    if args[:1] == ["--worker"]:
        return worker(args[1:])
    if args[:1] == ["--list-home-readers"]:
        return list_home_readers()
    if args[:1] == ["--discover"]:
        return discover_ids(args[1:])
    if args[:1] == ["--bisect"]:
        if len(args) < 3 or ".." not in args[1]:
            print("usage: runtests.py --bisect BASE..TIP MODULES...")
            return 2
        wt = os.path.join(tempfile.gettempdir(), "runtests-bisect-%d" % os.getpid())
        return bisect(args[1], discover(args[2:]), GitVcs(), wt)
    try:
        flakes = load_flakes()
    except FlakeListError as exc:                     # fail closed: a list that cannot be read never means "no flakes"
        print("ABORT: " + str(exc))
        return 2
    n = None
    record = False
    outcomes_out = None
    changed, files = False, None
    cbase = None
    while args and args[0].startswith("--"):
        if args[0] == "--workers":
            n = int(args[1])
            args = args[2:]
        elif args[0] == "--record":
            record = True
            args = args[1:]
        elif args[0] == "--changed":
            changed = True
            args = args[1:]
            if args and not args[0].startswith("--"):
                cbase, args = args[0], args[1:]
        elif args[0] == "--outcomes":
            outcomes_out = args[1]
            args = args[2:]
        elif args[0] == "--files":
            changed, files, args = True, args[1:], []
        else:
            print("unknown option " + args[0])
            return 2
    sel_line = None
    explicit = bool(args) or files is not None         # R-1259: a module or file named on the command line is not the default selection
    if changed:
        record = False
        if files is None:
            files = changed_files(cbase)
        sel, why = select(files)
        if sel is None:
            sel = discover([])
        sel_line = "selected %d of %d modules: %s" % (len(sel), len(discover([])), why)
        args = sel
    mods = discover(args) if (not changed or args) else []
    flag_n = n
    if flag_n is None and not os.environ.get("BUDGIT_TEST_WORKERS") and len(mods) >= MIN_WORKERS:
        fit, why_fit = await_memory(lambda: Load(*read_load()).free_mb, recorded_rss(), heavy_reserve_mb(mods),
                                    say=lambda s: print(s) or sys.stdout.flush())
        if not fit:
            print("ABORT: memory for %d workers did not free up: %s (never one worker; give --workers 1 to force it)" % (
                MIN_WORKERS, why_fit))
            return 4
    n, why_n = choose_workers(n, mods=mods)
    n = min(n, len(mods)) or 1
    print("workers %d: %s" % (n, why_n))            # always the first line, with --changed too
    if sel_line:
        print(sel_line)
    sys.stdout.flush()
    if changed and not mods:
        return 0
    kind = "gate" if (changed or args or PARTIAL) else "full"          # R-961: given modules is a gate run, discovery is a full run; a partial run is never "full"
    before = tree_state()                              # R-1259: the tree this run is about to test
    rc = supervise(lambda base, live: run_pool(mods, n, flag_n, record, base, live, outcomes_out, kind, flakes))
    if rc == 0 and mods and (explicit or PARTIAL):
        print("receipt not written: a module or file was named on the command line, or the run was partial (run with no module or file argument, or --changed)")
    elif rc == 0 and mods:                             # only a green, non-empty run leaves a receipt; red, aborted (2/3/4) and empty runs leave none
        write_receipt(mods, before)
    return rc


# ---- R-1259: the green-run receipt that plugin/scripts/commitguard.py reads (it holds the other half of this format) ----
# It lives under the Budgit home (studiolib.receipt_path: one file per checkout, named by a hash of its git dir), where the home guard
# keeps agents from writing. It is written only for the runner's own selection (the full suite or --changed; never a module or file
# named on the command line), is keyed to the INDEX tree (`git write-tree`: what a commit would contain) and only when every tracked
# file equals the index before and after the run. Untracked files do not count: this runner itself writes some, and they are not in a
# commit. The git calls use the guard's scrubbed form (no GIT_* variable, no fsmonitor, no hooks path).


def _receipt_git(*a):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", ROOT] + list(a),
                          capture_output=True, text=True, timeout=5, env=env)


def tree_state():
    """((index tree, absolute git dir, HEAD or "none"), "") when every tracked file equals the index; else (None, why)."""
    try:
        gitdir = _receipt_git("rev-parse", "--absolute-git-dir")
        tree = _receipt_git("write-tree")
        diff = _receipt_git("diff", "--quiet", "--no-ext-diff", "--no-textconv")
        head = _receipt_git("rev-parse", "--verify", "-q", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return None, "git could not be read"
    if gitdir.returncode or tree.returncode:
        return None, "git could not read the tree"
    if diff.returncode:
        return None, "tracked files differ from the index (stage what you test)"
    return (tree.stdout.strip(), gitdir.stdout.strip(), head.stdout.strip() if head.returncode == 0 else "none"), ""


def _fold(text):
    import unicodedata
    return unicodedata.normalize("NFKC", text).casefold()


def _budgit_home():
    """The same folder as studiolib.home_state() (BUDGIT_HOME, then STUDIO_HOME, else the home's .budgit / .studio by
    resolve_data_dir), written out here so this runner imports no checkout code; test_runner_and_guard_agree_on_receipt_path pins it."""
    v = os.environ.get("BUDGIT_HOME")
    v = v if v is not None else os.environ.get("STUDIO_HOME")
    if v:
        return os.path.expanduser(v)
    parent = os.path.expanduser("~")
    new, old = os.path.join(parent, ".budgit"), os.path.join(parent, ".studio")

    def real(p):
        return os.path.isdir(p) or os.path.islink(p)
    migrated = os.path.isfile(os.path.join(new, "MIGRATED"))
    if real(new) and (migrated or os.path.isfile(os.path.join(new, "ledger.json"))):
        if real(old) and not migrated and os.path.isfile(os.path.join(old, "ledger.json")):
            return old
        return new
    return old if real(old) else new


def receipt_path(gitdir):
    """<Budgit home>/test-receipts/<sha256 of the folded real git dir, 40 hex>.json: studiolib.receipt_path, spelled out."""
    import hashlib
    key = hashlib.sha256(_fold(os.path.realpath(gitdir)).encode("utf-8", "replace")).hexdigest()[:40]
    return os.path.join(_budgit_home(), "test-receipts", key + ".json")


def _staged_files():
    """Files the index differs from HEAD in (every file when there is no HEAD yet)."""
    out = _receipt_git("diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD")
    if out.returncode:
        out = _receipt_git("ls-files", "-z")
    if out.returncode:
        raise OSError("git could not list the staged files")
    return [f for f in out.stdout.split("\0") if f]


def write_receipt(mods, before):
    """After a green run: write the receipt unless the tree was not clean before the run or changed during it. One line says which."""
    def skip(why):
        print("receipt not written: " + why)
    state, why = before
    if state is None:
        return skip(why)
    after, why = tree_state()
    if after != state:
        return skip("the tree changed during the run" if after else why)
    try:                                               # the selection this runner would make for the staged files of the tree it just tested
        files = _staged_files()
        picked = select(files)[0] if files else []
        need = sorted(discover([]) if picked is None else picked)
    except Exception as exc:
        return skip("cannot work out the selection for the staged files: %s" % exc)
    import hashlib
    try:
        with open(os.path.realpath(__file__), "rb") as f:
            runner = hashlib.sha256(f.read()).hexdigest()
        path = receipt_path(state[1])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump({"v": 1, "tree": state[0], "gitdir": state[1], "runner_sha256": runner, "modules": list(mods),
                       "need": need, "head": state[2], "time": int(time.time())}, f)
        os.replace(tmp, path)
    except OSError as exc:
        return skip("cannot write it: %s" % exc)
    print("receipt written for tree %s (%d modules); commit now, within 30 minutes" % (state[0][:12], len(mods)))


def record_times(kind, mods, ran, secs, slow, project=None, now=None):
    """R-961: one line in <project>/.budgit/suite-times.jsonl for this run (suitetimes.append). A run whose count is 0
    (nothing selected) records nothing."""
    if not ran:
        return None
    import suitetimes
    return suitetimes.append(project or ROOT, kind, len(mods), ran, secs, suitetimes.top_slowest(slow), now=now)


def failure_blocks(entries, per=BLOCK_LINES, total=REPORT_LINES):
    """-> the lines to print for [(module, worker log text)]: the first FAIL or ERROR block of each log (a log with none shows its
    last lines), at most `per` lines for a module and `total` in all."""
    out = []
    for module, text in entries:
        room = min(per, total - len(out))
        if room < 2:
            break
        lines = text.splitlines()
        start = next((i for i, ln in enumerate(lines) if re.match(r"(FAIL|ERROR): ", ln)), None)
        if start is None:
            block = lines[-10:]
        else:
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("=" * 10) or lines[i].startswith("Ran ")), len(lines))
            block = lines[start:end]
            while block and (not block[-1].strip() or set(block[-1].strip()) == {"-"}):
                block.pop()
        out += ["--- %s ---" % module] + block[:room - 1]
    return out


def keep_failed_logs(root, procs, ends, results, logs_text):
    """Copy the worker log of every module that is not ok to <root>/<run stamp>/<module>.log, write order.json (launch order, times, exit
    code and result of every launched module) and env-names.txt (the NAMES of the dropped caller variables), keep the newest KEEP_RUNS
    run folders. -> the new folder."""
    import datetime
    folder = os.path.join(root, datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    os.makedirs(folder)
    order = []
    for idx, (p, tmp, rec, started, module) in enumerate(procs):
        res = results.get(module, "no result")
        if res != "ok":
            shutil.copyfile(rec + ".log", os.path.join(folder, module + ".log"))
        order.append({"module": module, "launch": idx, "start": round(started, 3), "end": round(ends.get(p.pid, time.time()), 3),
                      "exit": p.returncode, "result": res})
    with open(os.path.join(folder, "order.json"), "w") as f:
        json.dump(order, f, indent=1)
    with open(os.path.join(folder, "env-names.txt"), "w") as f:
        f.write("".join(name + "\n" for name in dropped_env_names()))
    runs = sorted(d for d in os.listdir(root) if STAMP.match(d) and os.path.isdir(os.path.join(root, d)))
    for old in runs[:-KEEP_RUNS]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    return folder


def run_pool(mods, n, flag_n, record, base, live, outcomes_out=None, kind="full", flakes=(), times_out=None, failed_logs=None):
    """The pool run. `base` is the supervised temp dir; every Popen goes into `live` so supervise can reap it.
    `times_out` is where the per-module times file goes (default: TIMES_FILE); `failed_logs` is where a red run keeps its logs
    (default: FAILED_LOGS, in the checkout)."""
    rows = {}
    ends, results, logs_text, noresult = {}, {}, {}, set()      # R-1282: when each worker ended, its verdict and its log text
    order = schedule(mods)          # one queue: unknown modules first, then the heaviest recorded first
    t0 = time.time()
    start_module_log()                # R-1379 P1a: a fresh per-module log; each worker appends its line as it ends
    warm_env = dict(os.environ)
    warm_env.pop("PYTHONDONTWRITEBYTECODE", None)
    warm_env["PYTHONPYCACHEPREFIX"] = os.path.join(base, "pycache")
    import safepath                   # R-1149: 3.11+ gets -P; the cwd is this checkout either way
    subprocess.run([sys.executable] + safepath.flags() + ["-m", "compileall", "-q", os.path.join(ROOT, "plugin", "scripts")],
                   env=warm_env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs = []                        # (process, tmp, rec, started, module) per launched worker
    counter = [0]

    def next_dir():
        counter[0] += 1
        return counter[0] - 1

    # The loader's own id list, taken once up front in a child of its own, alongside the workers.
    di = next_dir()
    rc_run = kind == "full" or bool(record)
    denv, _ = worker_env(base, di, rc_run)
    disc_out = os.path.join(base, "w%d" % di, "disc.json")
    dproc = spawn([sys.executable, os.path.abspath(__file__), "--discover", disc_out] + mods, cwd=ROOT, env=denv,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    live.append(dproc)

    def launch(module):
        i = next_dir()
        env, tmp = worker_env(base, i, rc_run)
        rec = os.path.join(base, "w%d" % i, "rec.json")
        with open(rec + ".log", "w") as log:          # a file, not a pipe: a polled worker with a full pipe would hang
            p = spawn([sys.executable, os.path.abspath(__file__), "--worker", rec, module], cwd=ROOT, env=env,
                      stdout=log, stderr=subprocess.STDOUT, text=True)
        live.append(p)
        procs.append((p, tmp, rec, time.time(), module))
        threading.Thread(target=module_exited, args=(p, module, procs[-1][3], ends), daemon=True).start()
        return p

    # Count guard, now per worker as it starts: a worker that stalls (no CPU time gained for STALL_WINDOW s) before it
    # reports its collection aborts the run, a slow one does not; an import error aborts the run.
    state = {"problem": None}
    reports = {}
    watch = StallWatch()

    def stop():
        if state["problem"]:
            return True
        for p, tmp, rec, started, module in procs:
            if rec in reports:
                continue
            try:
                with open(rec + ".collected") as f:
                    reports[rec] = json.load(f)
            except (OSError, ValueError):
                if p.poll() is not None:
                    state["problem"] = "worker for %s ended without reporting a collection" % module
                elif watch.stalled(p.pid, started):
                    state["problem"] = "worker for %s made no progress (no CPU time gained) for %d s before reporting a collection" % (
                        module, watch.window)
                continue
            # The regex count is gone from the abort decision (a module skipped at import still has `def test_` lines):
            # only a broken import aborts here, and id equality below decides the rest.
            state["problem"] = state["problem"] or check_collection(reports[rec]["count"], 0, reports[rec]["broken"])
        return bool(state["problem"])

    start_n = n
    peak, launched, live = run_queue(order, launch, damped(lambda rest: choose_workers(flag_n, mods=rest), start_n),
                                     start_n, len(mods),
                                     say=lambda s: print(s) or sys.stdout.flush(), stop=stop)
    while not stop() and any(p.poll() is None for p, _, _, _, _ in procs):      # the last workers, still watched
        time.sleep(0.2)
    problem = state["problem"]
    collected_all = [i for r in reports.values() for i in r.get("ids", [])]
    discovered, idmsgs = [], []
    if not problem:
        try:
            dproc.wait(timeout=300)
            with open(disc_out) as f:
                discovered = json.load(f)["ids"]
        except (OSError, ValueError, subprocess.TimeoutExpired):
            problem = "the up-front discovery did not produce an id list"
        else:
            idmsgs = check_ids(discovered, collected_all)
            if idmsgs:
                problem = "test ids differ from the loader's list"
    if problem:
        for p, tmp, rec, started, module in procs:
            if p.poll() is None:
                p.terminate()
        if dproc.poll() is None:
            dproc.terminate()
        print("ABORT: " + problem)
        for m in idmsgs:
            print(m)
        return 3
    bad, extra, leaks, rc, per, counts, rss, struct = [], [], [], 0, {}, [], [], []
    child_mb, ran_all, holders, ran_pairs, slow = {}, [], [], [], []
    flake_out, reruns = [], 0
    for p, tmp, rec, started, module in procs:
        p.wait()
        try:
            with open(rec + ".log", errors="replace") as f:
                out = f.read()
        except OSError:
            out = ""
        m = re.search(r"^Ran (\d+) tests? in", out, re.M)
        if m:
            counts.append(int(m.group(1)))
        elif p.returncode:
            bad.append("ERROR: worker produced no result (exit %d)" % p.returncode)
            extra.append(out[-2000:])
        wbad = re.findall(r"^((?:FAIL|ERROR): .*)$", out, re.M)
        forgive = False
        if wbad and p.returncode and m and flakes and counted_red(out) == len(wbad):
            # I-1820: a red test on the known list is rerun once alone; the worker's exit code stops counting only when
            # every one of its red tests was such a flake that then passed. Anything unclear stays a failure.
            def rerun(tid):
                renv, _ = worker_env(base, next_dir(), rc_run)
                return rerun_one(tid, renv)
            wbad, flake_lines, nre = settle_flakes(wbad, flakes, rerun)
            flake_out += flake_lines
            reruns += nre
            forgive = not wbad
        rc |= 1 if p.returncode and not forgive else 0
        bad += wbad
        logs_text[module] = out
        if not m:
            results[module] = "no result"
        elif wbad:
            results[module] = "error" if any(b.startswith("ERROR") for b in wbad) else "fail"
        else:
            results[module] = "fail" if p.returncode and not forgive else "ok"
        try:
            with open(rec) as f:
                d = json.load(f)
            rss.append(d.pop("_rss_mb", 0))
            cpu = d.pop("_cpu_s", 0.0)
            rows[module] = {"wall": round(sum(v["wall"] for k, v in d.items() if not k.startswith("_")), 2), "cpu_s": float(cpu)}
            child_mb.update(d.pop("_child_mb", {}))
            ran_got = d.pop("_ran", [])
            ran_all += [x[0] for x in ran_got]
            ran_pairs += [(x[0], x[1]) for x in ran_got]
            holders += d.pop("_holders", [])
            slow += [tuple(x) for x in d.pop("_slow", [])]
            per.update(d)
        except (OSError, ValueError):
            pass
        left = os.listdir(tmp)
        if left:
            leaks.append((tmp, left))
    ran = sum(counts)
    secs = time.time() - t0
    from_rec = sum(v["count"] for v in per.values())
    if from_rec != ran:
        struct.append("ERROR: merged count %d differs from per-worker sum %d" % (from_rec, ran))
    for m in mods:
        if "." in m:          # records are keyed by the first id segment, so a dotted module counts only when its own tests ran
            if any(i.startswith(m + ".") for i in ran_all):
                continue
        elif m in per:
            continue
        with open(module_file(m), errors="replace") as f:
            if "def test_" in f.read():
                struct.append("ERROR: no result for module %s" % m)
                results[m] = "no result"
    idmsgs = check_ids(discovered, collected_all, ran_all, holders)
    struct += idmsgs
    bad += struct
    for b in bad:
        print(b)
    for tmp, left in leaks:
        print("leftover in %s: %s" % (os.path.basename(os.path.dirname(tmp)), " ".join(sorted(left)[:40])))
    for e in extra:
        print(e)
    for fl in flake_out:
        print(fl)
    if record and not struct:        # test failures do not stop a recording: the walls are still true
        data = {m: round(per[m]["wall"], 2) for m in sorted(per)}
        if rss and max(rss):
            data["_peak_rss_mb"] = round(max(rss) * RSS_MARGIN, 1)     # the workers' own peak, plus a 25% margin
        if child_mb:
            top = sorted(child_mb.items(), key=lambda kv: -kv[1])[:5]
            data["_peak_child_mb_by_module"] = dict(top)
        with open(DURATIONS, "w") as f:
            json.dump(data, f, indent=1)
    print("Ran %d tests in %.3fs" % (ran, secs))
    skipped = [i for i, o in ran_pairs if o == "skip"]
    if skipped:         # a skipped test is counted in `Ran`, but it did not pass: say so, with the names
        names = ", ".join(sorted(skipped)[:SKIP_NAMES_SHOWN])
        more = len(skipped) - SKIP_NAMES_SHOWN
        print("passed %d, skipped %d (NOT RUN): %s%s" % (ran - len(skipped), len(skipped), names,
                                                         " +%d more" % more if more > 0 else ""))
    record_times(kind, mods, ran, secs, slow)
    write_module_times(times_out or TIMES_FILE, kind, peak, rows)
    ok = not bad and not rc
    print(("OK" + (" (%d NOT RUN)" % len(skipped) if skipped else "")) if ok else "FAILED (failures=%d)" % len(bad))
    print(flake_summary(reruns))
    print("duration %.1f s, peak workers %d" % (secs, peak))
    if ok and not idmsgs:
        print("%d variables from the caller were left out of the tests" % len(dropped_env_names()))
    else:                       # R-1282: keep what a red run needs to be read after the temp folder is gone
        try:
            folder = keep_failed_logs(failed_logs or FAILED_LOGS, procs, ends, results, logs_text)
        except OSError as e:
            folder = None
            print("failed logs not kept: %s" % e)
        failed = [(m, logs_text.get(m, "")) for _, _, _, _, m in procs if results.get(m, "no result") != "ok"]
        for line in failure_blocks(failed):
            print(line)
        if folder:
            print("failed logs: " + folder)
    if outcomes_out:
        with open(outcomes_out, "w") as f:
            json.dump(sorted(ran_pairs), f)
    if idmsgs:
        return 3                  # the id sets disagree: not a test failure, a broken run
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
