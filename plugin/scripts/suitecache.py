"""R-382: a green full suite is never run twice on the same tree.

A suite result is keyed by what decides it: the git tree object (`HEAD^{tree}`, the files and nothing else: a merge commit
whose tree equals one already tested has the same key), the folder it ran in and the test command. Only a green run is
recorded, and only when the working tree was clean before the run and still the same after it (a run that changed files
under it proves nothing). A dirty tree never reads or writes the cache: untracked files under `.studio/` and `.budgit/`
(project state, not code) do not count as dirty.

`budgit suite --rerun` and `budgit main-check --rerun` bypass it; BUDGIT_SUITE_CACHE=off turns it off. The store is one JSON
file under the Budgit home, never in a repo; a corrupt or unwritable store costs a rerun, never a failure."""
import hashlib
import json
import os
import subprocess

import studiolib as sl

FILE = "suite-green.json"
KEEP = 200
STATE_DIRS = (".studio/", ".budgit/")


def enabled():
    return (sl.env("SUITE_CACHE") or "").lower() != "off"


def _git(top, *args):
    r = subprocess.run(["git", "-C", top, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def tree_of(top, rev="HEAD"):
    rc, out = _git(top, "rev-parse", "--verify", "-q", rev + "^{tree}")
    return out if not rc and out else None


def clean_tree(top):
    """-> the tree of HEAD when the checkout has no change against it, else None."""
    r = subprocess.run(["git", "-C", top, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True)
    if r.returncode:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("?? ") and line[3:].startswith(STATE_DIRS):
            continue
        return None
    return tree_of(top)


def key(top, tree, command):
    return hashlib.sha256("\0".join([os.path.realpath(top), tree, command]).encode("utf-8")).hexdigest()[:32]


def _path():
    return os.path.join(sl.home_state(), FILE)


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            return {"entries": {}}
        return {"entries": {k: v for k, v in data["entries"].items() if isinstance(v, dict)}}      # a junk entry is no entry
    except (OSError, ValueError):
        return {"entries": {}}


def lookup(k):
    """The recorded green entry for this key, or None."""
    if not enabled() or not k:
        return None
    entry = _load()["entries"].get(k)
    return entry if isinstance(entry, dict) and entry.get("checked_at") else None


def forget(k):
    """A red run on a tree that was recorded green: the record no longer stands (the suite is not deterministic there)."""
    if not enabled() or not k:
        return
    try:
        data = _load()
        if data["entries"].pop(k, None) is not None:
            sl.atomic_write(_path(), json.dumps(data, indent=1, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError, sl.StudioError):
        pass


def record(k, top, tree, command, seconds, ran=None):
    if not enabled() or not k:
        return
    try:
        data = _load()
        data["entries"][k] = {"checked_at": sl.now(), "tree": tree, "top": os.path.realpath(top), "command": command,
                              "seconds": round(seconds, 1), "ran": ran}
        for old in sorted(data["entries"], key=lambda e: str(data["entries"][e].get("checked_at")))[:-KEEP]:
            del data["entries"][old]
        os.makedirs(os.path.dirname(_path()), mode=0o700, exist_ok=True)
        sl.atomic_write(_path(), json.dumps(data, indent=1, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError, sl.StudioError):
        pass
