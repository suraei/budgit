"""`budgit merge-close`: work that landed on main closes its item.

A commit names an item when its subject starts with `R-n` (or `R-n, R-m:`) or its body has a `Closes R-n` line; a
mention in the middle of a text names nothing. A named item that is still open, doing or done closes as verified, with the
newest commit that names it as the evidence, when it has a PASS report sealed by budgit:verifier. The others are listed with
the reason. Nothing is written without apply."""
import hashlib
import os
import re
import subprocess
import sys
import tempfile

import studiolib as sl

SUBJECT = re.compile(r"^\s*(R-\d+(?:\s*(?:,|&|\+|and)\s*R-\d+)*)\b")
BODY = re.compile(r"(?im)^\s*(?:closes|fixes|item)\s*:?\s+((?:R-\d+[\s,]*)+)$")
ID = re.compile(r"R-\d+")
OPEN = ("open", "doing", "done")


def _log(project, ref, since, limit):
    if str(ref).startswith("-") or str(since or "").startswith("-"):
        raise sl.StudioError("git log: a ref or --since that starts with '-' would be read as an option")
    spec = "%s..%s" % (since, ref) if since else ref
    try:
        r = subprocess.run(["git", "-C", project, "log", "-n", str(limit), "--format=%H%x1f%h%x1f%s%x1f%b%x1e", "--end-of-options", spec],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise sl.StudioError("git log failed: %s" % exc)
    if r.returncode != 0:
        raise sl.StudioError("git log %s failed: %s" % (spec, r.stderr.strip()[:200]))
    commits = []
    for chunk in r.stdout.split("\x1e"):
        parts = chunk.strip("\n").split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[1], "subject": parts[2], "body": parts[3]})
    return commits


def named(commit):
    ids = []
    m = SUBJECT.match(commit["subject"])
    if m:
        ids += ID.findall(m.group(1))
    for m in BODY.finditer(commit["body"]):
        ids += ID.findall(m.group(1))
    return list(dict.fromkeys(ids))


def _number(item_id):
    return int(item_id.split("-")[1])


def _worktrees(project):
    """Other checkouts of the same repository (a verifier session writes its report in its own worktree)."""
    try:
        r = subprocess.run(["git", "-C", project, "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    here = os.path.realpath(project)
    paths = [line[len("worktree "):] for line in r.stdout.splitlines() if line.startswith("worktree ")]
    return [p for p in paths if os.path.realpath(p) != here and os.path.isdir(p)]


def _first_line(data):
    return (data.decode("utf-8", "replace").splitlines() or [""])[0].strip()


def _read_sealed(path, root, own=False):
    """(bytes, seal) of a report the verifier sealed, or None. `path` is read through directory handles below `root`,
    each opened without following a link, so nothing between the folder and the file can be a link or be swapped
    for one while it is read; a listed worktree must itself not be a link (`own`: the project's own data folder is
    trusted as it is). The seal is looked up under the path that was read and must be the latest one for it and
    carry the sha256 of the bytes that were read, not of a file that may have changed since."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        root_real = os.path.realpath(root)
        if not own and root_real != os.path.join(os.path.realpath(os.path.dirname(root)), os.path.basename(root)):
            return None                                     # the worktree path is a link, or sits under one that moved
        parts = os.path.relpath(path, root).split(os.sep)
        if parts[0] == os.pardir:
            return None
        dirfd = os.open(root_real, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow, dir_fd=dirfd)
                os.close(dirfd)
                dirfd = nxt
            fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=dirfd)
        finally:
            os.close(dirfd)
        with os.fdopen(fd, "rb") as f:
            data = f.read()
        read_path = os.path.join(root_real, *parts)
    except (OSError, ValueError):
        return None
    seal = sl.latest_seal(read_path, resolved=True)
    if not seal or seal.get("sha256") != hashlib.sha256(data).hexdigest():
        return None
    return data, seal


def _own_state(project, item_id):
    """(passing, first line or None, seal ts or None) of the project's own report."""
    sdir = sl.studio_dir(project)
    own = os.path.join(sdir, "verify", item_id + ".md")
    got = _read_sealed(own, sdir, own=True)
    if got:
        return _first_line(got[0]).startswith("PASS"), _first_line(got[0]), got[1].get("ts")
    try:
        with open(own, "rb") as f:
            return False, _first_line(f.read()), None
    except OSError:
        return False, None, None


def _borrowed(project, item_id, own_ts):
    """(source path, sha256) of the newest PASS report sealed in another worktree of this repository whose seal is
    later than the seal of the project's own report (a sealed FAIL after it stays); None when there is none."""
    best = None
    for wt in _worktrees(project):
        path = os.path.join(sl.studio_dir(wt), "verify", item_id + ".md")
        got = _read_sealed(path, wt)
        if not got or not _first_line(got[0]).startswith("PASS"):
            continue
        ts = str(got[1].get("ts") or "")
        if best is None or ts > best[0]:
            best = (ts, path, got[1].get("sha256"))
    if best is None or (own_ts and best[0] <= str(own_ts)):
        return None
    return best[1], best[2]


def _why_not(project, item_id):
    """(reason, source): reason is None when the item can close as verified; source is {path, sha256} of the worktree
    report to copy in when the project has no passing sealed report of its own, else None."""
    passing, first, own_ts = _own_state(project, item_id)
    source = None
    if not passing:
        found = _borrowed(project, item_id, own_ts)
        if found is None:
            if first is None:
                return "no verify report", None
            if not first.startswith("PASS"):
                return "the verify report is not PASS", None
            return "the PASS report was not written by budgit:verifier on this machine", None
        source = {"path": found[0], "sha256": found[1]}
    try:
        sl._check_prepared(project)
    except sl.StudioError as exc:
        return str(exc), None
    return None, source


def _copy_in(project, row):
    """Copy the sealed worktree report byte for byte and seal the copy: the bytes are read once, checked against the
    seal that names them and written through a fresh temp file; the seal names the sha256 of those bytes, so a file
    swapped in afterwards is simply not sealed. A linked data folder or verify folder is refused."""
    src = row["source"]
    wt = os.path.dirname(os.path.dirname(os.path.dirname(src["path"])))
    got = _read_sealed(src["path"], wt)
    if not got or got[1].get("sha256") != src["sha256"] or not _first_line(got[0]).startswith("PASS"):
        raise sl.StudioError("%s: the report in the worktree changed or lost its seal since the plan; run merge-close again"
                             % row["id"])
    vdir = os.path.join(sl.ensure_studio_dir(project), "verify")
    if os.path.islink(vdir):
        raise sl.StudioError("%s is a symlink; refusing to write through it" % vdir)
    os.makedirs(vdir, exist_ok=True)
    own = os.path.join(vdir, row["id"] + ".md")
    try:
        with open(own, "rb") as f:
            before = f.read()
    except OSError:
        before = None
    _put(vdir, own, got[0])
    try:
        sl.write_seal(own, data=got[0])
    except BaseException:
        try:                                            # no unsealed copy stays: put back what was there, or remove it
            if before is None:
                os.unlink(own)
            else:
                _put(vdir, own, before)
        except OSError:
            pass
        raise
    return os.path.basename(wt)


def _put(vdir, own, data):
    """Write `data` to `own` through a fresh temp file in `vdir` (no predictable name to plant a link on)."""
    fd, tmp = tempfile.mkstemp(dir=vdir, prefix=os.path.basename(own) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, own)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def plan(project, data, ref="main", since=None, limit=300, only=None):
    """{'close': [{id, commit, subject}], 'cannot': [{id, commit, subject, reason}]}, oldest item first."""
    newest = {}
    for c in _log(project, ref, since, limit):          # newest first: the first commit that names an item is its evidence
        for item_id in named(c):
            newest.setdefault(item_id, c)
    items = {i["id"]: i for i in data["items"]}
    out = {"close": [], "cannot": []}
    for item_id in sorted(newest, key=_number):
        item = items.get(item_id)
        if item is None or item["status"] not in OPEN or (only and item_id != only):
            continue
        c = newest[item_id]
        row = {"id": item_id, "commit": c["sha"], "subject": c["subject"]}
        reason, source = _why_not(project, item_id)
        if reason:
            out["cannot"].append(dict(row, reason=reason))
        else:
            out["close"].append(dict(row, source=source) if source else row)
    return out


def apply(project, data, rows, ref="main"):
    """Close the planned rows in `data` (the caller saves it). A report sealed in another worktree is copied into the
    project's verify folder and sealed again there, so the closed item points at a report that exists. Returns the
    rows that were refused (a copy that failed): the item stays as it was and the others go on."""
    refused = []
    for row in rows:
        note = ""
        if row.get("source"):
            try:
                note = " (copied from the sealed report in the worktree %s)" % _copy_in(project, row)
            except (sl.StudioError, OSError) as exc:
                reason = str(exc) if isinstance(exc, sl.StudioError) else "%s: the report could not be copied (%s)" % (
                    row["id"], getattr(exc, "strerror", None) or type(exc).__name__)
                print("budgit: %s" % reason, file=sys.stderr)
                refused.append(dict(row, reason=reason))
                continue
        evidence = "merged to %s in %s: %s; verify report %s/verify/%s.md%s" % (
            ref, row["commit"], row["subject"][:80], os.path.basename(sl.studio_dir(project)), row["id"], note)
        sl.set_status(project, data, row["id"], "verified", evidence=evidence)
        sl.audit(project, "merge-close %s: closed by %s on %s" % (row["id"], row["commit"], ref))
    return refused


def demote(out, refused):
    """Move the refused rows from `close` to `cannot`, so the printed result says what really happened."""
    ids = {r["id"] for r in refused}
    out["close"] = [r for r in out["close"] if r["id"] not in ids]
    out["cannot"] += [{k: v for k, v in r.items() if k != "source"} for r in refused]


def report(out, applied):
    lines = []
    verb = "closed" if applied else "would close"
    lines.append("%s %d:" % (verb, len(out["close"])))
    lines += ["  %s  %s  %s%s" % (r["id"], r["commit"], r["subject"][:70], "  [sealed report from a worktree]" if r.get("source") else "")
              for r in out["close"]]
    lines.append("cannot close %d:" % len(out["cannot"]))
    lines += ["  %s  %s  %s" % (r["id"], r["commit"], r["reason"]) for r in out["cannot"]]
    if not applied and out["close"]:
        lines.append("Nothing was written. Run again with --apply to close them.")
    return "\n".join(lines)
