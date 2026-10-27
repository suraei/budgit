"""`budgit ledger repair-done-at`: put right the `done_at` of old closures that a bulk stamp set to one day.

Closing an item that was already closed (a legacy ledger, or a `verify` that touched it) used to stamp `done_at` = now,
so the week bar and the "Hecho" list counted a whole backlog as done today. This module moves such a `done_at` only
EARLIER, and only to a time the git history of the project's own ledger.json proves: the first commit that holds the
item closed. What git cannot prove is marked `done_at_unproven`, which the panel feed skips. It never guesses "now",
never reads another file, and never prints a title or an id: the report is counts only."""
import json
import os
import shutil
import subprocess
import time

import studiolib as sl

BURST_ITEMS = 10            # this many closures inside one window is the signature of a bulk stamp
BURST_WINDOW = 600          # seconds
TOLERANCE = 60              # clock slack between a stamp and the commit that holds it
MAX_COMMITS = 400           # the newest commits of the ledger's history that are read
MAX_BLOB = 20 * 1024 * 1024
BACKUP_PREFIX = "ledger.json.bak-done-at-"
CLOSED = ("done", "verified")


def _git(top, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(["git", "-C", top, *args], capture_output=True, env=env, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and len(r.stdout) <= MAX_BLOB else None


def history(project):
    """(commits read, {item id: {"first": commit time of the first commit holding it closed, "open": commit time of the
    last commit before that holding it not closed, or None}}). Nothing is provable without a git history of the ledger."""
    base = os.path.realpath(project)
    top = _git(base, "rev-parse", "--show-toplevel")
    if not top:
        return 0, {}
    top = os.path.realpath(top.decode("utf-8", "replace").strip())
    rels = []
    for name in sl.DATA_NAMES:
        rel = os.path.relpath(os.path.join(base, name, "ledger.json"), top)
        if not rel.startswith(".."):
            rels.append(rel.replace(os.sep, "/"))
    log = _git(top, "log", "--no-renames", "-n", str(MAX_COMMITS), "--format=%H %ct", "--", *rels)
    if not log:
        return 0, {}
    commits = []
    for line in log.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            commits.append((parts[0], int(parts[1])))
    seen, read = {}, 0
    for sha, ts in reversed(commits):                    # oldest first
        items = None
        for rel in rels:
            blob = _git(top, "show", "%s:%s" % (sha, rel))
            if blob is None:
                continue
            try:
                data = json.loads(blob.decode("utf-8"))
                items = data["items"]
            except (ValueError, KeyError, TypeError, RecursionError, MemoryError):   # a blob from history is data: no shape of it stops the command
                items = None
            break
        if not isinstance(items, list):
            continue
        read += 1
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                continue
            closed = it.get("status") in CLOSED
            st = seen.get(it["id"])
            if st is None:
                seen[it["id"]] = {"first": ts if closed else None, "open": None if closed else ts}
            elif st["first"] is None:
                if closed:
                    st["first"] = ts
                else:
                    st["open"] = ts
    return read, {k: v for k, v in seen.items() if v["first"] is not None}


def _burst_members(times):
    """Ids whose time sits in a window of BURST_WINDOW seconds that holds BURST_ITEMS or more closures."""
    ordered = sorted(times.items(), key=lambda kv: kv[1])
    members, lo = set(), 0
    for hi in range(len(ordered)):
        while ordered[hi][1] - ordered[lo][1] > BURST_WINDOW:
            lo += 1
        if hi - lo + 1 >= BURST_ITEMS:
            members.update(k for k, _ in ordered[lo:hi + 1])
    return members


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def plan(project, data=None):
    """What a repair would do, as counts plus the list of changes (never printed): `changes` is [(id, move-to epoch or
    None, unproven flag to set)]."""
    data = data if data is not None else sl.load_ledger(project)
    closed = [i for i in data["items"] if i["status"] in CLOSED]
    times = {}
    for i in closed:
        ts = sl._epoch(i.get("done_at")) or sl._epoch(i.get("updated"))
        if ts is not None:
            times[i["id"]] = ts
    read, proof = history(project)
    burst = _burst_members(times)
    out = {"closed": len(closed), "move": 0, "unproven": 0, "fine": 0, "history": read, "changes": []}
    for i in closed:
        cur, flagged, h = times.get(i["id"]), bool(i.get("done_at_unproven")), proof.get(i["id"])
        if cur is None:
            out["fine"] += 1
            continue
        if h and cur > h["first"] + TOLERANCE:                       # closed in git earlier than the time it carries
            out["move"] += 1
            out["changes"].append((i["id"], h["first"], False))
        elif h and abs(cur - h["first"]) <= TOLERANCE or (h and h["open"] is not None and h["open"] - TOLERANCE <= cur):
            out["fine"] += 1                                         # git agrees: at the proof, or inside the window it opened in
            if flagged:
                out["changes"].append((i["id"], None, False))
        elif i["id"] in burst or flagged:
            out["unproven"] += 1
            if not flagged:
                out["changes"].append((i["id"], None, True))
        else:
            out["fine"] += 1
    return out


def report(out, applied):
    return ("%s: %d closed items; %s %d earlier; unproven %d; already fine %d. git history: %d commits read." % (
        "applied" if applied else "dry run", out["closed"], "moved" if applied else "would move",
        out["move"], out["unproven"], out["fine"], out["history"]))


def apply(project, out):
    """Write the changes of `out` (a plan of this ledger) with a backup; returns the backup path, or None if nothing changed."""
    if not out["changes"]:
        return None
    if sl.ledger_tampered(project):
        raise sl.StudioError("ledger.json was edited by hand; run `budgit ledger accept-edit` first")
    sdir = sl.studio_dir(project)
    src = os.path.join(sdir, "ledger.json")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup, n = os.path.join(sdir, BACKUP_PREFIX + stamp), 1
    while os.path.lexists(backup):
        n += 1
        backup = os.path.join(sdir, "%s%s-%d" % (BACKUP_PREFIX, stamp, n))
    if os.path.islink(src):
        raise sl.StudioError("ledger.json is a symlink; refusing to repair through it")
    shutil.copyfile(src, backup)
    data = sl.load_ledger(project)
    by_id = {i["id"]: i for i in data["items"]}
    for item_id, ts, mark in out["changes"]:
        it = by_id[item_id]
        if ts is not None:
            it["done_at"] = _iso(ts)
        if mark:
            it["done_at_unproven"] = True
        else:
            it.pop("done_at_unproven", None)
    sl.save_ledger(project, data)
    sl.audit(project, "repair-done-at: %d moved earlier, %d marked unproven (backup %s)" % (
        out["move"], sum(1 for c in out["changes"] if c[2]), os.path.basename(backup)))
    return backup


def rollback(project, backup):
    """Put the `done_at` fields of the backup back, item by item: changes made since the repair stay."""
    sdir = os.path.realpath(sl.studio_dir(project))
    path = os.path.realpath(backup)
    if os.path.dirname(path) != sdir or not os.path.basename(path).startswith(BACKUP_PREFIX) or os.path.islink(backup):
        raise sl.StudioError("not a backup made by repair-done-at in this project's data folder")
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        sl.validate_ledger(old)
    except (OSError, ValueError, RecursionError, KeyError, TypeError, AttributeError) as exc:
        raise sl.StudioError("cannot read the backup: %s" % exc)
    if sl.ledger_tampered(project):
        raise sl.StudioError("ledger.json was edited by hand; run `budgit ledger accept-edit` first")
    was = {i["id"]: i for i in old["items"]}
    data = sl.load_ledger(project)
    restored = 0
    for it in data["items"]:
        before = was.get(it["id"])
        if before is None:
            continue
        for key in ("done_at", "done_at_unproven"):
            if before.get(key) != it.get(key):
                restored += 1
                if key in before:
                    it[key] = before[key]
                else:
                    it.pop(key, None)
    sl.save_ledger(project, data)
    sl.audit(project, "repair-done-at: rolled back from %s (%d fields)" % (os.path.basename(path), restored))
    return restored
