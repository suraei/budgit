"""R-874: job health. A scheduled job that did not do its job must be seen, not look "succeeded".
Each run records its last exit code, its time and its expected interval in <Budgit home>/job-health.json (written atomically). A job is
unhealthy when its last exit is non-zero, or its last run is older than MISSED_FACTOR times its interval, or it is scheduled and never ran.
What varies per machine, read at run time: the Budgit home (sl.home_state), the clock (injectable `now`; a last run in the future is clock
skew and counts as healthy, never as a miss), the scheduler (a macOS launch agent is looked up by its plist; on Linux or Windows no scheduler
is queried, so "never ran" is simply not reported there: UNTESTED against systemd or Task Scheduler), a missing or corrupt state file
(read as empty). No model, no network."""
import json
import os
import stat
import time

import studiolib as sl

MISSED_FACTOR = 2
STATE = "job-health.json"
DAILY = 86400


def path():
    return os.path.join(sl.home_state(), STATE)


def launcher_failures():
    """I-1713: -> {job: {"exit", "last", "interval"}} from the files the launcher shell leaves when a run failed before the job code recorded
    itself (no python, no plugin, an argument or import error). A link, a non-file or a malformed file is no record."""
    out = {}
    try:
        import housekeeping
        for name, job in housekeeping.JOBS.items():
            p = os.path.join(sl.home_state(), housekeeping.launcher_fail_name(job["args"]))
            try:
                st = os.lstat(p)
                if not stat.S_ISREG(st.st_mode) or st.st_size > 64:
                    continue
                fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, encoding="utf-8") as f:
                    start, code, *rest = f.read().split()
                if rest not in ([], ["blocked"]):
                    continue
                out[name] = {"exit": int(code), "last": float(int(start)), "interval": interval_of(name)}
                if rest:
                    out[name]["blocked"] = True       # python's stderr said "Operation not permitted": macOS privacy (TCC)
            except (OSError, ValueError):
                continue
    except Exception:
        return {}
    return out


def load():
    """-> {job: {"exit": int, "last": float, "interval": int}}: the run records, with a launcher failure in place of a record older than
    that run (the run never reached the job code). Never "never" after a failed run."""
    data = _read()
    for name, e in launcher_failures().items():
        if name not in data or data[name]["last"] < e["last"]:
            data[name] = e
    return data


def _read():
    """The run records only (record() rewrites this, never the launcher failures)."""
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


def record(name, code, interval=None, now=None, note=None):
    """Store one run. Never raises: a read-only home must not turn a good job into a failed one. `note`: why a run was skipped."""
    try:
        data = _read()
        data[name] = {"exit": int(code), "last": float(time.time() if now is None else now),
                      "interval": int(interval or interval_of(name))}
        if note:
            data[name]["note"] = str(note)[:200]
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


def problems(now=None, state=None, sched=None, prompt_exists=None):
    """-> [(name, kind, fields)], kind in failed | missed | never | noprompt (instruction file missing). Every input is injectable for tests."""
    now = time.time() if now is None else now
    state = load() if state is None else state
    sched = scheduled() if sched is None else sched
    found = []
    for name in sorted(state):
        e = state[name]
        age = now - e["last"]
        if e["exit"] != 0 and e.get("blocked"):
            found.append((name, "blocked", {}))
        elif e["exit"] != 0:
            found.append((name, "failed", {"code": e["exit"]}))
        elif age > MISSED_FACTOR * e["interval"]:           # a negative age (clock skew) is never a miss
            found.append((name, "missed", {"age": span(age), "every": span(e["interval"])}))
    for name in sorted(sched):
        if name not in state and now - sched[name] > MISSED_FACTOR * interval_of(name):
            found.append((name, "never", {}))
    import jobs
    exists = os.path.isfile if prompt_exists is None else prompt_exists
    for name in sorted(set(state) | set(sched)):             # a task whose instruction file is gone can never work, whatever its last exit says
        if name in jobs.MODEL_JOBS and not exists(jobs.prompt_path(name)):
            found.append((name, "noprompt", {}))
    return found


def span(seconds):
    s = int(seconds)
    return "%d min" % (s // 60) if s < 3600 else "%d h" % (s // 3600) if s < 172800 else "%d days" % (s // 86400)


def last_runs(state=None):
    """R-1128: every job Budgit ships with its last real run and exit from the run record, "never" for both when it never ran.
    -> [(name, last epoch or "never", exit code or "never")] in the order of housekeeping.JOBS."""
    import housekeeping
    state = load() if state is None else state
    rows = []
    for name in housekeeping.JOBS:
        e = state.get(name)
        rows.append((name, e["last"], e["exit"]) if e else (name, "never", "never"))
    return rows


def last_runs_text(state=None):
    """-> one plain line per job: `name: last run <local time>, exit N`, or `name: never`."""
    out = []
    state = load() if state is None else state
    for name, last, code in last_runs(state):
        note = (state.get(name) or {}).get("note")
        if note and last != "never":      # R-1128: a skip shows its reason and time
            out.append("%s: skipped: %s, %s" % (name, note, time.strftime("%Y-%m-%d %H:%M", time.localtime(last))))
            continue
        out.append("%s: never" % name if last == "never"
                   else "%s: last run %s, exit %s" % (name, time.strftime("%Y-%m-%d %H:%M", time.localtime(last)), code))
    return "\n".join(out)


def report(now=None, state=None, sched=None, lang=None, prompt_exists=None):
    """R-1128: -> (exit code, text) for `budgit job health`: the last run of every job ("never" with no record), then the problem line.
    Exit 0 only when no job has a problem (a failed or missed run, or an instruction file missing)."""
    state = load() if state is None else state
    problem = line(now, state, sched, lang, prompt_exists)
    text = last_runs_text(state)
    return (1, text + "\n" + problem) if problem else (0, text)


def line(now=None, state=None, sched=None, lang=None, prompt_exists=None, kinds=None):
    """-> the one job-health line in the user's language, or None when every job is healthy (the report prints nothing then).
    `kinds`: only the problems of those kinds (the session start asks for "blocked")."""
    import i18n
    found = [p for p in problems(now, state, sched, prompt_exists) if kinds is None or p[1] in kinds]
    if not found:
        return None
    items = "; ".join(i18n.t("jh." + kind, lang, name=name, **f) for name, kind, f in found)
    steps = "".join("\n" + i18n.t("jh.blocked_steps", lang, name=name) for name, kind, _f in found if kind == "blocked")
    return i18n.t("jh.line", lang, items=items) + steps
