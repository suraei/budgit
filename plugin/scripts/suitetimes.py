"""R-961: how long the suites take, recorded and checked by code.

Every `budgit suite` run (tools/runtests.py under it) appends ONE JSON line to <project>/.budgit/suite-times.jsonl:
{"kind": "gate"|"full", "modules": n, "tests": n, "wall": seconds, "at": ISO time, "slowest": [{"id", "secs"} x5]}.
A "gate" run is one that was given modules (or --changed/--files); a "full" run discovers every module.
`budgit suite --report` prints the last gate and full durations and exits 1 when the last gate run took over GATE_LIMIT s.
The daily stand-up shows the same line (slow_line) when the last gate run was over the limit. One reader, one wording.
Standard library only; the clock is a parameter."""
import json
import os
import time

GATE_LIMIT = 60
TOP = 5
FILE = os.path.join(".budgit", "suite-times.jsonl")


def path(project):
    return os.path.join(project, FILE)


def stamp(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() if now is None else now))


def top_slowest(timings, n=TOP):
    """[(test id, seconds)] -> the n slowest as [{"id", "secs"}], slowest first."""
    ranked = sorted(timings, key=lambda kv: -kv[1])[:n]
    return [{"id": i, "secs": round(s, 2)} for i, s in ranked]


def append(project, kind, modules, tests, wall, slowest, now=None):
    """Append one line. Returns the record. A folder that cannot be written is not a test failure: the record is dropped."""
    rec = {"kind": kind, "modules": int(modules), "tests": int(tests), "wall": round(float(wall), 1),
           "at": stamp(now), "slowest": list(slowest)[:TOP]}
    try:
        os.makedirs(os.path.dirname(path(project)), exist_ok=True)
        with open(path(project), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError:
        pass
    return rec


def records(project):
    out = []
    try:
        with open(path(project), errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("kind") in ("gate", "full") and isinstance(r.get("wall"), (int, float)):
                    out.append(r)
    except OSError:
        pass
    return out


def last(project, kind):
    got = [r for r in records(project) if r["kind"] == kind]
    return got[-1] if got else None


def names(rec):
    return ", ".join(str(s.get("id", "?")) for s in (rec.get("slowest") or [])[:TOP]) or "-"


def line(rec, limit=GATE_LIMIT):
    return "gate suite took %d s (limit %d s); slowest: %s" % (round(rec["wall"]), limit, names(rec))


def slow_gate(project, limit=GATE_LIMIT):
    """The last gate record when it took over `limit` s, else None."""
    rec = last(project, "gate")
    return rec if rec and rec["wall"] > limit else None


def report(project, out=print, limit=GATE_LIMIT):
    """Print the last gate and full durations; -> 1 when the last gate run was over `limit`, else 0."""
    gate, full = last(project, "gate"), last(project, "full")
    out("last gate run: %s" % ("none recorded" if not gate else "%d s, %d tests in %d modules, %s" % (
        round(gate["wall"]), gate.get("tests", 0), gate.get("modules", 0), gate.get("at", "?"))))
    out("last full run: %s" % ("none recorded" if not full else "%d s, %d tests in %d modules, %s" % (
        round(full["wall"]), full.get("tests", 0), full.get("modules", 0), full.get("at", "?"))))
    if gate and gate["wall"] > limit:
        out(line(gate, limit))
        for s in (gate.get("slowest") or [])[:TOP]:
            out("  %6.1f s  %s" % (s.get("secs", 0), s.get("id", "?")))
        return 1
    return 0
