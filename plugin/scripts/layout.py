"""R-306: one folder structure in every project, and a way to see (and fix) the documents saved where they do not belong.

Plans, research, reports and handovers live in the project's data folder (`.budgit/plans`, `.budgit/research`,
`.budgit/reports`, `.budgit/handover`), not scattered under `docs/`, `plans/`, `research/` and the like. This module only looks
at the well-known misplaced folders (never the whole tree, never `docs/` itself, which may be the product's own manual) and
only at document files (.md .txt .html .pdf). It never reads a file: names and the lstat type are enough.

  scan(project)      -> the misplaced documents and where each would go (a protected project: only the count)
  advice(project, p) -> a one-line hint when `p` is a document about to be saved in a wrong place, else None (warns, never blocks)
  move(project, apply) / undo(project) -> move them with `git mv` where tracked, remembering how to put them back

A project opts out with `"layout": "keep"` in its project.json or `"layout": "off"` in the Budgit config."""
import hashlib
import json
import os
import subprocess
import time

import studiolib as sl

DOC_EXT = (".md", ".txt", ".html", ".pdf")
# where each kind belongs inside the data folder, and the well-known folders it is found in by mistake
KINDS = {
    "plans": ("plans", "docs/plans", "doc/plans"),
    "research": ("research", "docs/research"),
    "reports": ("reports", "docs/reports"),
    "handover": ("handoffs", "handover", "docs/handoffs", "docs/handover"),
}
MAX_DEPTH = 3


def enabled(project, config=None):
    config = config if config is not None else sl.load_config()
    if str(config.get("layout", "")).lower() == "off":
        return False
    try:
        with open(os.path.join(sl.studio_dir(project), "project.json"), encoding="utf-8") as f:
            return str((json.load(f) or {}).get("layout", "")).lower() != "keep"
    except (OSError, ValueError, AttributeError):
        return True


def _symlink_free(project, rel):
    """False when `project`/`rel` or any folder on the way to it is a symlink: a move through one would leave the project."""
    cur = project
    for part in rel.split(os.sep):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return False
    return True


def _docs_under(folder):
    """Document files under `folder` as paths relative to it; symlinks and anything unreadable are skipped."""
    out = []
    base_depth = folder.rstrip(os.sep).count(os.sep)
    for dirpath, dirs, files in os.walk(folder, followlinks=False):
        if dirpath.count(os.sep) - base_depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d)))
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            if f.lower().endswith(DOC_EXT) and not f.startswith(".") and not os.path.islink(full):
                out.append(os.path.relpath(full, folder))
    return out


def scan(project, config=None):
    """-> {"protected": bool, "items": [{from, to, kind}]}. For a protected project `items` has no names, only `count`."""
    config = config if config is not None else sl.load_config()
    project = os.path.realpath(project)
    data = os.path.basename(sl.studio_dir(project))
    items = []
    if enabled(project, config):
        for kind, places in KINDS.items():
            for place in places:
                folder = os.path.join(project, place)
                if not _symlink_free(project, place) or not os.path.isdir(folder):
                    continue
                for rel in _docs_under(folder):
                    items.append({"from": os.path.join(place, rel), "to": os.path.join(data, kind, rel), "kind": kind})
    protected = bool(sl.protected_dir(project, config))
    if protected:
        return {"protected": True, "count": len(items), "items": []}
    return {"protected": False, "count": len(items), "items": items}


def advice(project, path, config=None):
    """{kind, to} when `path` is a document inside a well-known wrong folder of this project, else None."""
    config = config if config is not None else sl.load_config()
    if not path or not path.lower().endswith(DOC_EXT) or not enabled(project, config):
        return None
    project = os.path.realpath(project)
    full = os.path.realpath(path) if os.path.isabs(path) else os.path.realpath(os.path.join(project, path))
    if not full.startswith(project + os.sep):
        return None
    rel = os.path.relpath(full, project)
    data = os.path.basename(sl.studio_dir(project))
    if rel.split(os.sep)[0] == data:
        return None
    for kind, places in KINDS.items():
        for place in places:
            if rel.startswith(place + os.sep):
                return {"kind": kind, "to": os.path.join(data, kind, os.path.relpath(rel, place))}
    return None


def _record_path(project):
    return os.path.join(sl.home_state(), "layout-undo-%s.json" % hashlib.sha256(os.path.realpath(project).encode()).hexdigest()[:12])


def _tracked(project, rel):
    r = subprocess.run(["git", "-C", project, "ls-files", "--error-unmatch", "--", rel], capture_output=True, text=True)
    return r.returncode == 0


def move(project, apply=False, config=None):
    """Move the misplaced documents into the data folder. Dry run unless `apply`; a target that exists is never overwritten.
    -> {"moved": n, "kept": n, "protected": bool}. Names are in the undo record only (a file under the Budgit home)."""
    config = config if config is not None else sl.load_config()
    project = os.path.realpath(project)
    found = scan(project, config)
    if found["protected"]:
        raise sl.StudioError("protected")
    todo, kept = [], 0
    if apply and found["items"]:
        sl.ensure_studio_dir(project)                        # refuses a data folder that is a symlink: nothing moves through it
    for it in found["items"]:
        src, dst = os.path.join(project, it["from"]), os.path.join(project, it["to"])
        if os.path.lexists(dst):
            kept += 1
            continue
        if not (_symlink_free(project, it["from"]) and _symlink_free(project, os.path.dirname(it["to"]))):
            raise sl.StudioError("a folder on the way to a document or to its new place is a symlink: nothing moves through it")
        todo.append([it["from"], it["to"]])
    done = []
    try:
        if apply and todo:
            _write_record(project, todo)                     # first the whole plan: undo skips what never moved, so a crash loses nothing
        for rel_from, rel_to in (todo if apply else ()):
            src, dst = os.path.join(project, rel_from), os.path.join(project, rel_to)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            moved = False
            if os.path.lexists(os.path.join(project, ".git")) and _tracked(project, rel_from):
                moved = subprocess.run(["git", "-C", project, "mv", "--", rel_from, rel_to], capture_output=True).returncode == 0
            if not moved:
                os.rename(src, dst)
            done.append([rel_from, rel_to])
    finally:
        if apply and done:
            _write_record(project, done)                     # what really moved, also when a move failed midway
            for place in {m[0].rsplit(os.sep, 1)[0] for m in done}:                   # only folders that are now empty
                _prune_empty(project, place)
    return {"moved": len(todo), "kept": kept, "protected": False}


def _write_record(project, moves):
    os.makedirs(sl.home_state(), exist_ok=True)
    sl.atomic_write(_record_path(project), json.dumps({"project": project, "at": time.time(), "moves": moves}))


def _prune_empty(project, rel):
    path = os.path.join(project, rel)
    while rel and os.path.isdir(path) and not os.path.islink(path):
        try:
            os.rmdir(path)
        except OSError:
            return
        rel = os.path.dirname(rel)
        path = os.path.join(project, rel)


def undo(project):
    """Put back what the last `move` moved: only a document that is still at its new place and whose old place is free.
    -> number put back, or None when there is no record."""
    project = os.path.realpath(project)
    try:
        with open(_record_path(project), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    back = 0
    for src, dst in reversed(rec.get("moves") or []):
        a, b = os.path.join(project, dst), os.path.join(project, src)
        if os.path.lexists(a) and not os.path.lexists(b) and _symlink_free(project, src) and _symlink_free(project, dst):
            os.makedirs(os.path.dirname(b), exist_ok=True)
            if not (os.path.lexists(os.path.join(project, ".git")) and _tracked(project, dst)
                    and subprocess.run(["git", "-C", project, "mv", "--", dst, src], capture_output=True).returncode == 0):
                os.rename(a, b)
            back += 1
    os.unlink(_record_path(project))
    return back
