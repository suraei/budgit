"""R-404: sort the folders next to the projects (leftover, plugin copy, worktree, project, unknown) from metadata only.

Nothing is read: only whether a few well-known names exist at the top of the folder, and size, count and dates from
lstat (an iCloud placeholder is not downloaded by lstat). No file name is kept or shown. A folder that is protected
(`protected_dirs`), or that the caller names as private, is opaque: it is not even walked, its name never reaches any text
(it is `private folder #hash`), and it is never proposed for the Trash.

Only three kinds may be proposed for the Trash, because a copy of them exists elsewhere or they hold nothing:
  - empty: no file at all (empty folders do not count);
  - plugin_copy: has a plugin manifest folder, no git history, no Budgit data, and nobody touched it for IDLE_DAYS;
  - nothing else. An orphan worktree (its git folder is gone) or an unknown folder may hold the only copy of someone's
    work, so they are only reported; the user decides."""
import hashlib
import os
import time
import unicodedata

import studiolib as sl

WALK_CAP = 100000
IDLE_DAYS = 3
MANIFEST_DIRS = (".claude-plugin",)


def idle_days_limit():
    try:
        return float(os.environ.get("BUDGIT_LEFTOVER_DAYS", IDLE_DAYS))
    except ValueError:
        return IDLE_DAYS


def label(path):
    return "#" + hashlib.sha256(_fold(os.path.realpath(path)).encode("utf-8")).hexdigest()[:6]


def private_paths():
    """Folders the environment names as private (BUDGIT_PRIVATE_DIRS, ':'-separated), besides config `protected_dirs`."""
    return [p for p in os.environ.get("BUDGIT_PRIVATE_DIRS", "").split(os.pathsep) if p]


def _fold(name):
    return unicodedata.normalize("NFC", name).casefold()


def _same_name(a, b):
    """Case-folded and Unicode-normalised, like `protected_dir`: APFS folds case and keeps either Unicode form."""
    return _fold(a) == _fold(b)


def is_private(path, config=None, names=(), root=None):
    """True when the folder is protected by config, or its path or its name (inside `root`) is in `names`/the environment."""
    config = config if config is not None else sl.load_config()
    if sl.protected_dir(path, config):
        return True
    real = os.path.realpath(path)
    for n in list(names) + private_paths():
        n = os.path.expanduser(n)
        target = n if os.path.isabs(n) else os.path.join(root or os.path.dirname(real), n)
        if _same_name(os.path.realpath(target), real):
            return True
    return False


def _walk(path):
    """-> (files, bytes, newest mtime or None, complete). Only lstat; symlinks are counted, never followed. Anything that
    could not be read (a folder or an entry the OS refuses) makes the answer incomplete: what could not be seen is not
    known to be empty."""
    files, size, newest, seen, complete = 0, 0, None, 0, True
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    seen += 1
                    if seen > WALK_CAP:
                        return files, size, newest, False
                    try:
                        st = e.stat(follow_symlinks=False)
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        complete = False
                        continue
                    if is_dir:
                        stack.append(e.path)
                        continue
                    files += 1
                    size += st.st_size
                    newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
        except OSError:
            complete = False
    return files, size, newest, complete


def _worktree_of(path):
    """('worktree' | 'orphan', owner folder name or None) for a folder whose `.git` is a file. Reads that one pointer line,
    which is git's own bookkeeping, nothing of the project."""
    try:
        with open(os.path.join(path, ".git"), encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
    except OSError:
        return "orphan", None
    if not first.startswith("gitdir:"):
        return "orphan", None
    gitdir = first.split(":", 1)[1].strip()
    marker = os.sep + ".git" + os.sep + "worktrees" + os.sep
    owner = gitdir.split(marker)[0] if marker in gitdir else None
    if os.path.isdir(gitdir):
        return "worktree", os.path.basename(owner) if owner else None
    return "orphan", None


def classify(path, config=None, names=(), root=None, now=None):
    """-> {kind, private, files, bytes, idle_days, complete, trash_ok, owner}. kind is one of private, empty, worktree,
    orphan_worktree, project, plugin_copy, unknown."""
    now = now if now is not None else time.time()
    out = {"kind": "unknown", "private": False, "files": 0, "bytes": 0, "idle_days": None, "complete": True, "trash_ok": False,
           "owner": None}
    if is_private(path, config, names, root):
        out.update(kind="private", private=True, complete=False)
        return out
    if os.path.islink(path):                                   # where it points is not this folder: never walked, never trashable
        out["complete"] = False
        return out
    git = os.path.join(path, ".git")
    files, size, newest, complete = _walk(path)
    out.update(files=files, bytes=size, complete=complete, idle_days=None if newest is None else round((now - newest) / 86400, 1))
    if os.path.isfile(git) and not os.path.islink(git):
        kind, owner = _worktree_of(path)
        out.update(kind="worktree" if kind == "worktree" else "orphan_worktree", owner=owner)
    elif os.path.lexists(git) or any(os.path.lexists(os.path.join(path, d)) for d in sl.DATA_NAMES):
        out["kind"] = "project"
    elif complete and files == 0:
        out.update(kind="empty", trash_ok=True)
    elif any(os.path.isdir(os.path.join(path, d)) for d in MANIFEST_DIRS):
        out["kind"] = "plugin_copy"
        out["trash_ok"] = complete and out["idle_days"] is not None and out["idle_days"] >= idle_days_limit()
    return out


def size_text(n):
    for unit, step in (("B", 1024), ("K", 1024), ("M", 1024), ("G", 1024)):
        if n < step or unit == "G":
            return ("%d%s" % (n, unit)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= step
