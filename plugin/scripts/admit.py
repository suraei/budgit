"""R-381: `budgit admit` -- may this machine take one more suite or lane right now? go / wait / refuse with a one-line reason.

No model, no network. It reads the busy CPU (median of short %CPU samples, in cores; never the load average, R-841), the core count, free
memory and how many Claude sessions are busy this minute, and compares them with thresholds that live in the home config under
`admit` (all optional; "load" below means busy cores):

    wait_load      cpu_load above this -> wait      (default: 85 % of the cores, admission.busy_cpu_cores)
    refuse_load    cpu_load above this -> refuse    (default: twice the core count)
    min_free_mb    free memory under this -> refuse a lane   (default 1536)
    max_busy       this many busy sessions or more -> refuse a lane   (default 6: a PM, the TM and four lanes)

R-402 adds the fair share across projects when a project is named (`budgit admit lane --project DIR`, see decide and
fairshare.py). `budgit suite` asks with kind "suite" (load only); a PM or the TM asks with kind "lane" before start_session; the Agent
PreToolUse hook asks with "lane" and only adds a line when the answer is not go (advice, never a block).
Exit codes: 0 go, 1 wait, 2 refuse. A reading that cannot be taken is skipped, never guessed."""
import os
import subprocess

import studiolib as sl

GO, WAIT, REFUSE = "go", "wait", "refuse"
EXIT = {GO: 0, WAIT: 1, REFUSE: 2}
DEFAULTS = {"min_free_mb": 1536, "max_busy": 6}
KINDS = ("lane", "suite")


def cores():
    return os.cpu_count() or 1


def cpu_load(sampler=None, sleep=None, window=1, step=0.5):
    """R-841: how many cores' worth of CPU is busy now (median of short %CPU samples x cores / 100), or None when unreadable.
    This replaces the 1-minute load average: no decision here depends on os.getloadavg."""
    median = cpu_median(sampler or cpu_sample, sleep, window, step)
    return None if median is None else median * cores() / 100.0

def free_mb():
    """Memory a new process could use now, in MB: free + inactive + speculative pages on macOS, MemAvailable on Linux."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        page, pages = 4096, 0
        for line in out.splitlines():
            if "page size of" in line:
                page = int(line.split("page size of")[1].split()[0])
            elif line.split(":")[0] in ("Pages free", "Pages inactive", "Pages speculative"):
                pages += int(line.split(":")[1].strip().rstrip("."))
        return pages * page // (1024 * 1024) if pages else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def busy_sessions():
    """Claude sessions working this minute (livework's verified live rows with status busy); None when they cannot be read."""
    try:
        import livework
        found = livework.live_sessions()
    except Exception:
        return None
    if found.get("status") != "ok":
        return None
    return sum(1 for s in found["sessions"] if s["status"] == "busy" and s.get("verified"))


BUSY_EXIT = 10             # the thresholds (85 % CPU, 1 GB) live once, in admission.busy_reason


def cpu_sample():
    """Summed %CPU of all processes as a share of all cores, in percent (ps on macOS and Linux). None when it cannot be read.
    Windows (a PowerShell counter) is not built: None there, so the CPU half is skipped. UNTESTED on Windows."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "%cpu="], capture_output=True, text=True, timeout=10).stdout
        return sum(float(x) for x in out.split()) / cores()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def cpu_median(sampler=cpu_sample, sleep=None, window=60, step=5):
    """Median of the sampler over `window` seconds, one sample every `step`; unreadable samples are skipped. None when none was read.
    The sampler and the sleep are injected so tests run without waiting."""
    import statistics
    import time
    sleep = sleep or time.sleep
    count = max(1, int(window // step))
    got = []
    for i in range(count):
        value = sampler()
        if value is not None:
            got.append(value)
        if i < count - 1:
            sleep(step)
    return statistics.median(got) if got else None


def is_busy(sampler=cpu_sample, sleep=None, window=60, free=None):
    """-> (busy, reason). Memory first (cheap, no waiting); then the CPU median. A reading that cannot be taken is skipped."""
    import admission
    free_now = free() if free else free_mb()
    why = admission.busy_reason(free_now, None)
    if why:
        return True, why
    why = admission.busy_reason(None, cpu_median(sampler, sleep, window))
    return (True, why) if why else (False, "free")


def measure():
    return {"cpu_load": cpu_load(), "cores": cores(), "free_mb": free_mb(), "busy": busy_sessions()}


def thresholds(m, config=None):
    """The three numbers and the two limits in force: config `admit` over the defaults. BUDGIT_SUITE_LOAD_LIMIT (tests, odd
    machines) sets both load thresholds."""
    cfg = ((config if config is not None else sl.load_config()).get("admit")) or {}
    cfg = cfg if isinstance(cfg, dict) else {}

    def num(key, default):
        value = cfg.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else default
    import admission
    wait, refuse = num("wait_load", admission.busy_cpu_cores(m["cores"])), num("refuse_load", 2.0 * m["cores"])
    explicit = sl.env("SUITE_LOAD_LIMIT")
    try:
        value = float(explicit) if explicit else None
        if value is not None and value == value and value >= 0:           # not nan, not negative
            wait = refuse = value
    except ValueError:
        pass
    return {"wait_load": wait, "refuse_load": max(refuse, wait), "min_free_mb": num("min_free_mb", DEFAULTS["min_free_mb"]),
            "max_busy": num("max_busy", DEFAULTS["max_busy"])}


def decide(m, kind="lane", config=None, share=None):
    """-> {"verdict", "reason", "exit", "kind", "measured", "limits", "share", "lane_cap"}. Refusals are checked before waits.

    R-402, fair share across projects (`share` from fairshare.share; without it the machine alone decides, as before):
    a project that holds no lane may always start its first one, whatever the load and the busy count (only a machine out of
    memory refuses it); a second lane needs a load under the waiting limit, and above it the project holding the largest number of lanes is
    the one that yields; a third lane also needs every other project with pending work to hold a lane. `lane_cap` is how many
    lanes the project may hold now: 1 above the waiting limit, 2 while another project still has none, else None (only memory
    and the busy limit bound it): spare capacity is lent while nobody needs it and taken back when somebody does."""
    import i18n
    t = i18n.t
    limits = thresholds(m, config)
    load = m.get("cpu_load")
    lane = kind == "lane" and share is not None
    mine = share["lanes"] if lane else None
    n_cores = max(1, m.get("cores") or 1)

    def pct(cores_busy):                                  # R-864: the message names CPU % and free memory, never a load average
        return "?" if cores_busy is None else "%.0f" % (cores_busy * 100.0 / n_cores)
    mem = _n(m.get("free_mb"))
    verdict, reason = GO, t("ad.r_go", cpu=pct(load), mem=mem, busy=_n(m.get("busy")))
    short_of_memory = kind == "lane" and m.get("free_mb") is not None and m["free_mb"] < limits["min_free_mb"]
    if lane and mine == 0 and not short_of_memory:
        reason = t("ad.r_floor", cpu=pct(load), mem=mem)
    elif load is not None and load > limits["refuse_load"]:
        verdict, reason = REFUSE, t("ad.r_load_refuse", cpu=pct(load), limit=pct(limits["refuse_load"]), mem=mem)
    elif short_of_memory:
        verdict, reason = REFUSE, t("ad.r_memory", free=int(m["free_mb"]), limit=int(limits["min_free_mb"]))
    elif kind == "lane" and m.get("busy") is not None and m["busy"] >= limits["max_busy"]:
        verdict, reason = REFUSE, t("ad.r_busy", busy=m["busy"], limit=int(limits["max_busy"]))
    elif load is not None and load > limits["wait_load"]:
        verdict = WAIT
        reason = (t("ad.r_yield", lanes=mine, cpu=pct(load), limit=pct(limits["wait_load"]), mem=mem) if lane and share["most"]
                  else t("ad.r_load_wait", cpu=pct(load), limit=pct(limits["wait_load"]), mem=mem))
    elif lane and mine >= 2 and share["starved"] > 0:
        verdict, reason = WAIT, t("ad.r_starved", lanes=mine, n=share["starved"])
    cap = None
    if lane:
        cap = lane_quota(load is not None and load > limits["wait_load"], True, share["starved"])
    return {"verdict": verdict, "reason": reason, "exit": EXIT[verdict], "kind": kind, "measured": m, "limits": limits,
            "share": share, "lane_cap": cap}


LANE_FLOOR = 1


def lane_quota(busy, pending, starved):
    """R-877: lanes a project may hold. No pending work: 0 is allowed. Pending work: never under LANE_FLOOR (1), whatever the
    load. Only the 2nd and 3rd lane are gated: busy machine -> 1; not busy -> 2 while another pending project holds none,
    else None (no cap: the 3rd lane needs every pending project to hold one)."""
    if not pending:
        return 0
    if busy:
        return LANE_FLOOR
    return max(LANE_FLOOR, 2) if starved > 0 else None


def _n(value):
    if value is None:
        return "?"
    return ("%.1f" % value) if isinstance(value, float) else str(value)


def check(kind="lane", config=None, project=None):
    """The answer for this machine now. With `project` (a lane asked for by that project) the fair share is applied too."""
    share = None
    if kind == "lane" and project:
        try:
            import fairshare
            share = fairshare.share(project)
        except Exception:
            share = None
    return decide(measure(), kind, config, share)


def advice(config=None, project=None):
    """For the Agent hook: a one-line note when a lane would not be admitted, else "". Never raises.
    BUDGIT_ADMIT_ADVICE=off silences it. `project` applies the fair share (R-402)."""
    if (sl.env("ADMIT_ADVICE") or "").lower() == "off":
        return ""
    try:
        got = check("lane", config, project)
    except Exception:
        return ""
    if got["verdict"] == GO:
        return ""
    import i18n
    return i18n.t("ad.advice", verdict=got["verdict"], reason=got["reason"])
