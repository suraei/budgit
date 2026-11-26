"""R-381: `budgit admit` -- may this machine take one more suite or lane right now? go / wait / refuse with a one-line reason.

No model, no network. It reads the busy CPU (kernel counters, in cores; never the load average, R-841, R-889b), the core
count, the memory signals of admission.mem_reading (pressure, swap-ins, compressor, truly free; never free plus inactive) and
how many Claude sessions are busy this minute, and compares them with thresholds that live in the home config under `admit`
(all optional; "load" below means busy cores):

    wait_load      cpu_load above this -> wait      (default: 85 % of the cores, admission.busy_cpu_cores)
    refuse_load    cpu_load above this -> refuse    (default: twice the core count)
    max_busy       this many busy sessions or more -> refuse a lane   (default 6: a PM, the TM and four lanes)

A lane is refused when admission.busy_reason finds a memory signal (the same rule as `budgit busy`).

R-402 adds the fair share across projects when a project is named (`budgit admit lane --project DIR`, see decide and
fairshare.py). `budgit suite` asks with kind "suite" (load only); a PM or the TM asks with kind "lane" before start_session; the Agent
PreToolUse hook asks with "lane" and only adds a line when the answer is not go (advice, never a block).
Exit codes: 0 go, 1 wait, 2 refuse. A reading that cannot be taken is skipped, never guessed."""
import os
import re
import subprocess

import studiolib as sl

GO, WAIT, REFUSE = "go", "wait", "refuse"
EXIT = {GO: 0, WAIT: 1, REFUSE: 2}
DEFAULTS = {"max_busy": 6}
KINDS = ("lane", "suite")


def cores():
    return os.cpu_count() or 1


def cpu_load(sampler=None, sleep=None, window=1, step=0.5):
    """R-841, R-889b: how many cores' worth of CPU is busy now (median of short kernel-counter samples x cores / 100), or None
    when unreadable. The default sampler is admission.SystemCPU (kernel counters); the ps sum (cpu_sample) is only its named
    fallback when the counters cannot be read. No decision here depends on os.getloadavg."""
    if sampler is None:
        import admission
        import time
        sampler = admission.SystemCPU(sleep=sleep or time.sleep, fallback=cpu_sample)
    median = cpu_median(sampler, sleep, window, step)
    return None if median is None else median * cores() / 100.0


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


def is_busy(sampler=None, sleep=None, window=60, free=None, mem=None, pressure_level=None, use_cpu=True, kernel_gate=False):
    """-> (busy, reason). R-889b: memory first, instantly (kernel pressure, compressor, truly free memory); then the CPU
    median over the window from kernel counters (admission.SystemCPU, the ps sum only as fallback), with the swap-in rate
    measured across that same window. A reading that cannot be taken is skipped and the calm reason names it.
    `mem` returns one admission.mem_reading picture; `free` returns TRULY free MB. A caller that injects `free` but not
    `mem` supplies the whole memory picture itself, so no live memory reading is taken."""
    import time
    import admission
    if mem is None:
        mem = (lambda: {}) if free else admission.mem_reading
    free_now = free() if free else None
    first = mem()
    why = admission.busy_reason(free_now, None, first, pressure_level, kernel_gate)
    if why:
        return True, why
    sampler = sampler or admission.SystemCPU(sleep=sleep or time.sleep, fallback=cpu_sample)
    cpu = cpu_median(sampler, sleep, window)
    signals = admission.mem_signals(first, mem())
    # use_cpu=False (scheduled jobs, R-1128 hotfix 1): the CPU reading is never a reason; memory pressure and the swap rate still are
    why = admission.busy_reason(None, cpu if use_cpu else None, signals, pressure_level, kernel_gate)
    return (True, why) if why else (False, admission.calm_reason(signals, sampler))


def measure():
    """The reading decide() judges. Memory comes from the ONE shared reader (admission.mem_reading); free_mb is the truly
    free figure, shown in messages and judged only where the kernel gives no pressure reading."""
    import admission
    mem = admission.mem_reading()
    return {"cpu_load": cpu_load(sampler=admission.SystemCPU(fallback=cpu_sample)), "cores": cores(),
            "free_mb": mem.get("free_true_mb"), "mem": mem, "busy": busy_sessions()}


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
    return {"wait_load": wait, "refuse_load": max(refuse, wait), "max_busy": num("max_busy", DEFAULTS["max_busy"])}


def suite_pressure_level():
    """The macOS pressure level a suite refuses at: the scheduled-job level (jobs.JOB_PRESSURE_LEVEL, critical), because an
    ordinary laptop reports warn all day. The swap-in rate and compressor limits stay as they are."""
    import jobs
    return jobs.JOB_PRESSURE_LEVEL


def decide(m, kind="lane", config=None, share=None, continuing=False):
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
    picture = m.get("mem")                                # a memory picture with no readable signal says so and never refuses
    blind = isinstance(picture, dict) and not any(picture.get(k) is not None for k in ("pressure", "psi", "winload", "compressor_mb", "free_true_mb"))
    verdict, reason = GO, t("ad.r_go_blind" if blind else "ad.r_go", cpu=pct(load), mem=mem, busy=_n(m.get("busy")))
    import admission                                      # R-889b: the lane refusal reads the same memory signals as busy
    level = suite_pressure_level() if kind == "suite" else None   # a suite is a scheduled-job-sized run: critical, not warn; a lane keeps warn
    memory_why = admission.busy_reason(m.get("free_mb"), None, m.get("mem"), level)   # every kind reads memory (a suite too), one rule
    short_of_memory = bool(memory_why)
    cap, over = None, load is not None and load > limits["wait_load"]
    if continuing:                                        # urgent mode: a step of work already in flight is never held back by CPU alone
        why_c = admission.busy_reason(m.get("free_mb"), None, m.get("mem"), level)      # memory is not negotiable, for a suite too
        if why_c:
            return _answer(REFUSE, t("ad.r_memory", why=why_c), kind, m, limits, share, None, None)
        if kind == "lane" and m.get("busy") is not None and m["busy"] >= limits["max_busy"]:
            return _answer(REFUSE, t("ad.r_busy", busy=m["busy"], limit=int(limits["max_busy"])), kind, m, limits, share, None, None)
        if over:
            worker_cap = continuing_cap(m.get("cores"))
            return _answer(GO, t("ad.r_continue", cap=worker_cap, cpu=pct(load), mem=mem), kind, m, limits, share, None, worker_cap)
    if lane and mine == 0 and not short_of_memory:
        reason = t("ad.r_floor", cpu=pct(load), mem=mem)
    elif load is not None and load > limits["refuse_load"]:
        verdict, reason = REFUSE, t("ad.r_load_refuse", cpu=pct(load), limit=pct(limits["refuse_load"]), mem=mem)
    elif short_of_memory:
        verdict, reason = REFUSE, t("ad.r_memory", why=memory_why)
    elif kind == "lane" and m.get("busy") is not None and m["busy"] >= limits["max_busy"]:
        verdict, reason = REFUSE, t("ad.r_busy", busy=m["busy"], limit=int(limits["max_busy"]))
    elif load is not None and load > limits["wait_load"]:
        verdict = WAIT
        reason = (t("ad.r_yield", lanes=mine, cpu=pct(load), limit=pct(limits["wait_load"]), mem=mem) if lane and share["most"]
                  else t("ad.r_load_wait", cpu=pct(load), limit=pct(limits["wait_load"]), mem=mem))
    elif lane and mine >= 2 and share["starved"] > 0:
        verdict, reason = WAIT, t("ad.r_starved", lanes=mine, n=share["starved"])
    if lane:
        cap = lane_quota(over, True, share["starved"])
    return _answer(verdict, reason, kind, m, limits, share, cap, None, continuing)


def _answer(verdict, reason, kind, m, limits, share, lane_cap, worker_cap, continuing=True):
    got = {"verdict": verdict, "reason": reason, "exit": EXIT[verdict], "kind": kind, "measured": m, "limits": limits,
           "share": share, "lane_cap": lane_cap}
    if continuing:
        got["worker_cap"] = worker_cap               # the worker cap for the runner (BUDGIT_TEST_WORKERS), or None
    return got


def continuing_cap(cores):
    """The reduced worker count for a step already in flight on a CPU-busy machine: half the cores, at least 1."""
    return max(1, (cores or 1) // 2)


CONTINUING_ID = re.compile(r"R-[0-9]+")


def continuing_refusal(item, project, load=None):
    """None when `item` is an item id (R, a dash, digits) that the project's ledger holds, else the refusal text. No free pass by typing a word."""
    import i18n
    if not isinstance(item, str) or not CONTINUING_ID.fullmatch(item):
        return i18n.t("ad.r_continue_bad")
    try:
        if not project:
            raise OSError("no project")
        data = (load or sl.load_ledger)(project)
        known = {it.get("id") for it in data["items"]}
    except Exception:
        return i18n.t("ad.r_continue_unknown")
    return None if item in known else i18n.t("ad.r_continue_unknown")


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


def check(kind="lane", config=None, project=None, continuing=None, ledger=None):
    """The answer for this machine now. With `project` (a lane asked for by that project) the fair share is applied too.
    `continuing` (urgent mode): the id of an item already in flight; a bad or unknown id is refused (exit 64) before any reading."""
    if continuing is not None:
        bad = continuing_refusal(continuing, project, ledger)
        if bad:
            return {"verdict": REFUSE, "reason": bad, "exit": 64, "kind": kind, "measured": None, "limits": None,
                    "share": None, "lane_cap": None, "worker_cap": None}
    share = None
    if kind == "lane" and project:
        try:
            import fairshare
            share = fairshare.share(project)
        except Exception:
            share = None
    return decide(measure(), kind, config, share, continuing=continuing is not None)


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
