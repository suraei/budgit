"""R-874: job health. A scheduled job that did not do its job must be seen, not look "succeeded".
Each run records its last exit code, its time and its expected interval in <Budgit home>/job-health.json (written atomically). A job is
unhealthy when its last exit is non-zero, or its last run is older than MISSED_FACTOR times its interval, or it is scheduled and never ran.
What varies per machine, read at run time: the Budgit home (sl.home_state), the clock (injectable `now`; a last run in the future is clock
skew and counts as healthy, never as a miss), the scheduler (a macOS launch agent is looked up by its plist; on Linux or Windows no scheduler
is queried, so "never ran" is simply not reported there: UNTESTED against systemd or Task Scheduler), a missing or corrupt state file
(read as empty). No model, no network."""
import json
import os
import time

import studiolib as sl

MISSED_FACTOR = 2
STATE = "job-health.json"
DAILY = 86400


def path():
    return os.path.join(sl.home_state(), STATE)


def load():
    """-> {job: {"exit": int, "last": float, "interval": int}}; empty on a missing, unreadable or corrupt file. Bad entries are dropped."""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for name, e in data.items():
        if (isinstance(e, dict) and type(e.get("exit")) is int and isinstance(e.get("last"), (int, float))
                and not isinstance(e.get("last"), bool) and type(e.get("interval")) is int and e["interval"] > 0):
            out[name] = e
    return out


def interval_of(name):
    """The expected seconds between runs: the job's StartInterval, or a day for a calendar job."""
    try:
        import housekeeping
        job = housekeeping.JOBS.get(name) or {}
        return int(job["interval"]) if "interval" in job else DAILY
    except Exception:
        return DAILY


def record(name, code, interval=None, now=None):
    """Store one run. Never raises: a read-only home must not turn a good job into a failed one."""
    try:
        data = load()
        data[name] = {"exit": int(code), "last": float(time.time() if now is None else now),
                      "interval": int(interval or interval_of(name))}
        os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
        sl.atomic_write(path(), json.dumps(data, indent=2, sort_keys=True) + "\n")
        return True
    except (OSError, ValueError, TypeError):
        return False


def scheduled():
    """-> {job: epoch it was scheduled} for the jobs that have a launch agent here. macOS only; elsewhere {} (no crash)."""
    out = {}
    try:
        import housekeeping
        for name, job in housekeeping.JOBS.items():
            p = os.path.join(housekeeping.agents_dir(), job["label"] + ".plist")
            if os.path.exists(p):
                out[name] = os.path.getmtime(p)
    except Exception:
        return {}
    return out


def problems(now=None, state=None, sched=None):
    """-> [(name, kind, fields)], kind in failed | missed | never. Every input is injectable for tests."""
    now = time.time() if now is None else now
    state = load() if state is None else state
    sched = scheduled() if sched is None else sched
    found = []
    for name in sorted(state):
        e = state[name]
        age = now - e["last"]
        if e["exit"] != 0:
            found.append((name, "failed", {"code": e["exit"]}))
        elif age > MISSED_FACTOR * e["interval"]:           # a negative age (clock skew) is never a miss
            found.append((name, "missed", {"age": span(age), "every": span(e["interval"])}))
    for name in sorted(sched):
        if name not in state and now - sched[name] > MISSED_FACTOR * interval_of(name):
            found.append((name, "never", {}))
    return found


def span(seconds):
    s = int(seconds)
    return "%d min" % (s // 60) if s < 3600 else "%d h" % (s // 3600) if s < 172800 else "%d days" % (s // 86400)


def line(now=None, state=None, sched=None, lang=None):
    """-> the one job-health line in the user's language, or None when every job is healthy (the report prints nothing then)."""
    import i18n
    found = problems(now, state, sched)
    if not found:
        return None
    items = "; ".join(i18n.t("jh." + kind if kind != "never" else "jh.never", lang, name=name, **f) for name, kind, f in found)
    return i18n.t("jh.line", lang, items=items)
