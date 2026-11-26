"""R-1313: a lane's worktree goes once its branch has landed on main and the tree is clean, so worktrees stop piling up.

Reuses housekeeping's git runner, worktree list, base-branch and merged-branch readers (one copy of each). Removal is `git worktree
remove`, then `git branch -d`, then `git worktree prune`: never a force flag, never -D. Kept, each with a one-line reason: the main
checkout (never listed), the worktree the process runs in, a locked one, a detached one, one whose branch is not an ancestor of main,
one whose branch has no commit of its own yet (just started), a dirty one (a modified tracked file), one with an untracked file that is
not evidence, and any worktree of a protected project (config `protected_dirs`, no name here).
Evidence = .budgit/verify/*, .studio/reports/*, .budgit/reports/*, and *.md at the root, a lane's .progress-*.md included (ignored or not): it is
copied into the main checkout at the same relative path first (a .progress file goes to .studio/archive/lane-progress/<folder>/), never
over a different file (that keeps the worktree). Once the copy is
byte-equal the lane's copy is dropped so the plain remove can go through. Any other untracked file: keep and say so, never delete."""
import fnmatch
import os
import re
import shutil
import stat

import housekeeping as hk
import studiolib as sl

EVIDENCE = (".budgit/verify/*", ".studio/reports/*", ".budgit/reports/*")


PROGRESS = ".progress-*.md"
ARCHIVE = os.path.join(".studio", "archive", "lane-progress")


def is_evidence(rel):
    if any(fnmatch.fnmatchcase(rel, p) for p in EVIDENCE):
        return True
    return "/" not in rel and rel.endswith(".md")


def dest_rel(worktree, rel):
    """Where evidence goes in the main checkout: the same relative path, except a lane's root progress file, which is archived under
    .studio/archive/lane-progress/<worktree folder name>/ (every lane has one; they must not collide)."""
    if "/" not in rel and fnmatch.fnmatchcase(rel, PROGRESS):
        return os.path.join(ARCHIVE, os.path.basename(os.path.realpath(worktree)), rel)
    return rel


def _same(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def untracked(path):
    """-> (dirty, [relative file paths of untracked and ignored files, regenerable caches left out]). Unreadable status = dirty."""
    code, status, _ = hk.git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored")
    if code != 0:
        return True, []
    files = []
    for entry in status.split("\0"):
        if len(entry) < 4:
            continue
        mark, rel = entry[:2], entry[3:]
        if mark not in ("??", "!!"):
            return True, []
        parts = rel.rstrip("/").split("/")
        if any(hk._regenerable(p) for p in parts):
            continue
        full = os.path.join(path, *parts)
        if rel.endswith("/") and os.path.isdir(full):
            for root, dirs, names in os.walk(full):
                dirs[:] = [d for d in dirs if not hk._regenerable(d)]
                linked = [d for d in dirs if os.path.islink(os.path.join(root, d))]      # os.walk never follows them; they are listed, so judged
                files += [os.path.relpath(os.path.join(root, n), path) for n in names + linked if not hk._regenerable(n)]
        else:
            files.append(os.path.join(*parts))
    return False, files


def started(project, branch):
    """True when the branch has moved since it was created (a commit of its own); an unreadable reflog counts as not started."""
    code, out, _ = hk.git(project, "reflog", "show", "--format=%H", "refs/heads/" + branch)
    shas = [s for s in out.split() if s]
    return code == 0 and len(shas) > 1 and shas[0] != shas[-1]


def _regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def has_special(path):
    """True when the tree holds a FIFO, socket or device. Git never lists those as untracked, and `worktree remove` would delete them
    unseen, so the lane is looked at here (links are judged by `untracked`; regenerable caches are left out as there)."""
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if d != ".git" and not hk._regenerable(d)]
        for n in names:
            try:
                mode = os.lstat(os.path.join(root, n)).st_mode
            except OSError:
                return True
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                return True
    return False


def judge(project, e, base, merged, cwd, main_real, config):
    """-> (reason or None, evidence files to copy)."""
    real = os.path.realpath(e["path"])
    if e["locked"]:
        return "locked", []
    if not e["branch"]:
        return "detached", []
    if e["branch"] == base:
        return "base", []
    if real == main_real:
        return "main", []
    if cwd and hk._inside(os.path.realpath(cwd), real):
        return "current", []
    if e["branch"] not in merged:
        return "unmerged", []
    if not started(project, e["branch"]):
        return "notstarted", []
    if config is None:
        return "config unreadable", []                                 # protected_dirs cannot be read: fail closed
    if sl.protected_dir(real, config):
        return "protected", []
    dirty, files = untracked(e["path"])
    if dirty:
        return "dirty", []
    if any(not is_evidence(f) or not _regular(os.path.join(e["path"], f)) for f in files) or has_special(e["path"]):
        return "untracked", []                                         # a link, FIFO or device is never evidence, whatever its name
    for f in files:
        dest = os.path.join(main_real, dest_rel(e["path"], f))
        if os.path.lexists(dest) and not _same(os.path.join(e["path"], f), dest):
            return "evidence-conflict", []
    return None, files


def is_lane_of(e, numbers):
    """True when the worktree folder name or its branch name holds at least one r<n> token (case-insensitive, the next character not a
    digit, the one before not a letter or digit) and EVERY such token belongs to `numbers`: with r1313 closed, r1313-land-prune and
    claude/r1313a-x are its lanes, r13130 and r5-r1313-batch (a token of another open item) are not."""
    tokens = set()
    for name in (os.path.basename(e["path"]), e["branch"] or ""):
        tokens |= {int(t) for t in re.findall(r"(?<![a-z0-9])r(\d+)(?!\d)", name, re.IGNORECASE)}
    return bool(tokens) and tokens <= {int(n) for n in numbers}


CLOSED = re.compile(r"^R-(\d+)(?: -> done|: ok, done)\s*$", re.M)


def closed_numbers(result):
    """The item numbers `ledger done` closed, from its own result lines ("R-n -> done" single, "R-n: ok, done" batch). R- ids only:
    an I-n line, or a refused one, is never a closed item."""
    return CLOSED.findall(result)


def run(project, apply=False, cwd=None, config=None, items=None):
    """-> {would_remove, removed, kept: [{path, branch, reason}], copied: [path], failed: [{what, why}], skipped}. `apply` False
    changes nothing. A protected project, or one with no main branch, is skipped whole. `items` (item numbers) limits the pass to
    those items' own lanes (is_lane_of); other worktrees are neither touched nor listed. None = every landed lane."""
    out = {"would_remove": [], "removed": [], "kept": [], "copied": [], "failed": [], "skipped": None}
    try:
        config = config if config is not None else sl.load_config()
        # load_config never raises: a bad file reads as defaults (protected_dirs empty), so the files and the list are checked too
        if sl.unreadable_config_paths() or not sl.protected_dirs_readable(config):
            config = None                                              # every lane is then kept as "config unreadable"
    except Exception:
        config = None
    if config is not None and sl.protected_dir(project, config):
        out["skipped"] = "protected"
        return out
    base = hk.base_branch(project)
    entries = hk.worktrees(project)
    if not entries or base is None:
        out["skipped"] = "nobase"
        return out
    main_real = os.path.realpath(entries[0]["path"])                   # the owning checkout counts too, not only `project`
    if config is not None and sl.protected_dir(main_real, config):
        out["skipped"] = "protected"
        return out
    import livework
    if livework.is_private(project) or livework.is_private(main_real):    # project.json "private": true, as housekeeping reads it
        out["skipped"] = "private"
        return out
    merged = hk.merged_branches(project, base)
    for e in entries[1:]:
        if items is not None and not is_lane_of(e, items):
            continue
        if e["prunable"] or not os.path.isdir(e["path"]):
            continue                                                   # the final `worktree prune` clears a missing folder
        reason, files = judge(project, e, base, merged, cwd, main_real, config)
        if reason:
            out["kept"].append({"path": e["path"], "branch": e["branch"], "reason": reason})
            continue
        out["would_remove"].append(e["path"])
        if not apply:
            continue
        failed = _land(project, e, files, main_real, out)
        if failed:
            out["would_remove"].remove(e["path"])
            out["failed"].append({"what": e["path"], "why": failed})
    if apply:
        hk.git(project, "worktree", "prune")
    return out


def _copy_regular(src, dest):
    """Copy a regular file without ever following a link: a link swapped in after judge() fails with ELOOP (O_NOFOLLOW)."""
    fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))      # Windows has no O_NOFOLLOW; the fstat below still checks the type
    with os.fdopen(fd, "rb") as fin:
        if not stat.S_ISREG(os.fstat(fin.fileno()).st_mode):
            raise OSError(0, "not a regular file")
        with open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)


def _land(project, e, files, main_real, out):
    """Copy the evidence, drop the lane's copies, plain-remove the worktree, `branch -d`. -> a reason string on failure, else None."""
    copies = []
    for f in files:
        src, dest = os.path.join(e["path"], f), os.path.join(main_real, dest_rel(e["path"], f))
        if not os.path.lexists(dest):
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                _copy_regular(src, dest)
            except Exception as exc:                                   # any failure of the copy keeps the lane, its own copy untouched
                return "evidence not copied: %s" % (getattr(exc, "strerror", None) or type(exc).__name__)
        if not _same(src, dest):
            return "evidence not copied: the copy differs"
        copies.append(src)
    for src in copies:                                                 # byte-equal copy is in main: the lane's own may go
        try:
            os.unlink(src)
        except OSError as exc:
            return "evidence copy left in the lane: %s" % (exc.strerror or type(exc).__name__)
    code, _, err = hk.git(project, "worktree", "remove", e["path"])
    if code != 0:
        return hk._first_line(err) or "worktree remove failed"
    out["removed"].append(e["path"])
    out["copied"] += [os.path.join(main_real, dest_rel(e["path"], f)) for f in files]
    code, _, err = hk.git(project, "branch", "-d", e["branch"])
    if code != 0:
        out["failed"].append({"what": e["branch"], "why": hk._first_line(err)})
    return None


def report(out, applied):
    """One line per worktree: what went (or would), what stayed and why."""
    lines = []
    if out["skipped"]:
        return "land-prune: skipped (%s)" % out["skipped"]
    verb = "removed" if applied else "would remove"
    lines += ["land-prune: %s %s" % (verb, p) for p in (out["removed"] if applied else out["would_remove"])]
    lines += ["land-prune: kept %s (%s): %s" % (k["path"], k["branch"] or "-", k["reason"]) for k in out["kept"]]
    lines += ["land-prune: failed %s: %s" % (f["what"], f["why"]) for f in out["failed"]]
    return "\n".join(lines)
