"""R-1358: the use log of the outside models (codex, gemini, openrouter, codex images) and its weekly stand-up line.

One JSON line per run, appended under the project lock to `extuse.jsonl` in the project data folder: ts, project (the folder's
base name only), tool, purpose, outcome, seconds. Never the prompt, the code, the output, a path or a key. The log is a record:
a failure to write it never changes the run's result."""
import json
import os
import re
import time

import studiolib as sl

LOG_NAME = "extuse.jsonl"
WEEK_S = 7 * 86400
OUTCOMES = ("ok", "quota", "timeout", "login", "failed")
LOCK_WAIT_S = 5
_PURPOSE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,29}\Z")


def failure_class(status, detail=""):
    """The outcome word of a run_tool status (ok | timeout | unavailable | failed) and its detail line."""
    if status == "ok":
        return "ok"
    if status == "timeout":
        return "timeout"
    if status == "unavailable":
        return "quota"
    return "login" if "sign-in" in (detail or "") else "failed"


def log(project, tool, purpose, outcome, seconds, now=None):
    """Append one line. -> True when written. Never raises."""
    try:
        purpose = purpose if _PURPOSE_RE.match(purpose or "") else "review"
        row = {"ts": int(time.time() if now is None else now), "project": os.path.basename(os.path.abspath(project)) or "project",
               "tool": tool, "purpose": purpose, "outcome": outcome if outcome in OUTCOMES else "failed",
               "seconds": round(max(0.0, float(seconds)), 1)}
        with sl.locked(project, timeout=LOCK_WAIT_S):
            sl.append_text(os.path.join(sl.ensure_studio_dir(project), LOG_NAME), json.dumps(row) + "\n", mode=0o600)
        return True
    except Exception:
        return False


def rows(project, since):
    out = []
    try:
        with open(os.path.join(sl.studio_dir(project), LOG_NAME), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("ts"), (int, float)) and r["ts"] >= since and isinstance(r.get("tool"), str):
                    out.append(r)
    except OSError:
        pass
    return out


def week_line(projects, now=None):
    """One line: per tool the runs, the failures by class and the seconds of the last 7 days. "" when nothing was logged."""
    now = time.time() if now is None else now
    per = {}
    for p in projects:
        for r in rows(p, now - WEEK_S):
            t = per.setdefault(r["tool"], {"runs": 0, "secs": 0.0, "fail": {}})
            t["runs"] += 1
            t["secs"] += float(r.get("seconds") or 0)
            if r.get("outcome") != "ok":
                c = str(r.get("outcome") or "failed")
                t["fail"][c] = t["fail"].get(c, 0) + 1
    if not per:
        return ""
    parts = []
    for tool in sorted(per):
        t = per[tool]
        fails = ", ".join("%s %d" % kv for kv in sorted(t["fail"].items()))
        parts.append("%s %d run%s, %s, %d s" % (tool, t["runs"], "" if t["runs"] == 1 else "s",
                                                "%d failed (%s)" % (sum(t["fail"].values()), fails) if fails else "no failures",
                                                round(t["secs"])))
    return "Outside models, last 7 days: " + "; ".join(parts)
