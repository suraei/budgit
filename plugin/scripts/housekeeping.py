"""R-400: `budgit housekeeping` -- the daily tidy-up the TM used to do by hand (R-379, R-385, R-389).

For every Budgit project on the machine it prints the flow count of the last 24 hours (items in, items out, pending, how many
are waiting in the next version) and finds the git worktrees and branches that are finished: merged into the main branch,
clean, and idle for more than 24 hours. With --apply it removes them with `git worktree remove` and `git branch -d` only
(never --force, never -D, never a reset or an rm -rf); without it, it only says what it would do.

Projects are found, never listed here: the hub, the children of `projects_root`, the projects live sessions point at. A project
under `protected_dirs` of the config, or one whose project.json says `"private": true`, is left alone and only counted. Nothing in
this file names a folder. A worktree that a live Claude session is working in is kept. When the 1-minute load is above 8 the git
part is skipped and only the (read-only) flow count is printed. `--schedule on` sets up the daily run (a macOS launch agent);
it is never done on its own. Best effort per project: one project that fails costs a line in the report, never the run."""
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
import time

import studiolib as sl

IDLE_HOURS = 24
CANDIDATE_DAYS = 3
LOAD_LIMIT = 8.0
KEEP_PATTERNS = ("backup/*",)
GIT_TIMEOUT = 60
LABEL = "com.budgit.housekeeping"
LAST = "housekeeping-last.txt"
CLOSED = ("done", "verified", "dropped")
VERSION = re.compile(r"v?\d+(\.\d+)*")


# ---------------------------------------------------------------- git

_deadline = None                  # R-415: `budgit daily` sets one time.monotonic() deadline for all its git calls


def git(path, *args):
    """-> (exit code, stdout, stderr). A git that cannot run is exit 127, never an exception."""
    timeout = GIT_TIMEOUT
    if _deadline is not None:
        timeout = min(timeout, _deadline - time.monotonic())
        if timeout <= 0:
            return 127, "", "git could not run"
    try:
        r = subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True, errors="surrogateescape", timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError):
        return 127, "", "git could not run"


def _first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def top_level(path):
    code, out, _ = git(path, "rev-parse", "--show-toplevel")
    return os.path.realpath(out.strip()) if code == 0 and out.strip() else None


def common_dir(path):
    code, out, _ = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.realpath(out.strip()) if code == 0 and out.strip() else None


def base_branch(path):
    for name in ("main", "master"):
        if git(path, "rev-parse", "--verify", "--quiet", "refs/heads/" + name)[0] == 0:
            return name
    return None


def worktrees(path):
    """The worktrees of the repo of `path`, the main checkout first: [{path, branch, detached, locked, prunable, bare}]."""
    code, out, _ = git(path, "worktree", "list", "--porcelain")
    entries, cur = [], None
    for line in out.splitlines() if code == 0 else ():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": None, "detached": False, "locked": False,
                   "prunable": False, "bare": False}
            entries.append(cur)
        elif cur is None:
            continue
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["detached"] = True
        elif line == "bare":
            cur["bare"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
        elif line.startswith("prunable"):
            cur["prunable"] = True
    return entries


def merged_branches(path, base):
    code, out, _ = git(path, "branch", "--merged", base, "--format=%(refname:short)")
    return {b.strip() for b in out.splitlines() if b.strip()} if code == 0 else set()


def branch_touched(path, name):
    """When the branch last moved (reflog, else its tip commit), epoch seconds; None when git cannot say."""
    for args in (("reflog", "show", "--format=%ct", "refs/heads/" + name), ("log", "-1", "--format=%ct", "refs/heads/" + name)):
        code, out, _ = git(path, *args)
        line = _first_line(out)
        if code == 0 and line.isdigit():
            return int(line)
    return None


def touched_since(path, cutoff):
    """True when anything in `path` (git's own files left out) changed after `cutoff`; unreadable counts as touched."""
    try:
        if os.stat(path).st_mtime > cutoff:
            return True
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".git")]
            for name in dirs + [f for f in files if not f.startswith(".git")]:
                try:
                    if os.lstat(os.path.join(root, name)).st_mtime > cutoff:
                        return True
                except OSError:
                    continue
    except OSError:
        return True
    return False


def live_cwds():
    """The folders live Claude sessions work in, or None when they cannot be read."""
    try:
        import livework
        found = livework.live_sessions()
        if found.get("status") == "absent":
            return []                                             # no sessions folder: no session file says anything is live
        if found.get("status") != "ok":
            return None
        return [os.path.realpath(s["cwd"]) for s in found["sessions"] if s.get("cwd")]
    except Exception:
        return None


REGENERABLE = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".venv", ".DS_Store")


def has_ignored_files(path):
    """True when the worktree holds an ignored file that is not a regenerable cache (a local `.env`, a notes file): `git worktree
    remove` deletes those without a word, and `git status --porcelain` does not list them. An unreadable status counts as yes."""
    code, status, _ = git(path, "status", "--porcelain=v1", "-z", "--ignored")      # -z: names are never quoted or escaped
    if code != 0:
        return True
    for line in status.split("\0"):
        if not line.startswith("!! "):
            continue
        rel = line[3:]
        parts = rel.rstrip("/").split("/")
        if any(_regenerable(p) for p in parts):
            continue
        if not rel.endswith("/") or not os.path.lexists(os.path.join(path, *parts)):    # a file, or an entry not found on disk
            return True
        for root, dirs, files in os.walk(os.path.join(path, *parts)):    # git lists a folder of only ignored files as one entry
            dirs[:] = [d for d in dirs if not _regenerable(d)]
            if any(not _regenerable(f) for f in files):
                return True
    return False


def _regenerable(name):
    return name in REGENERABLE or name.endswith(".pyc")


def _inside(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def keep_patterns(config):
    extra = ((config.get("housekeeping") or {}).get("keep") if isinstance(config.get("housekeeping"), dict) else None) or []
    return KEEP_PATTERNS + tuple(p for p in extra if isinstance(p, str))


def plan(project, now, hours=IDLE_HOURS, live=None, config=None):
    """What housekeeping would do in one repo (`live` None = the live sessions could not be read: nothing is removed): {remove: [{path, branch}], delete: [branch], kept: [{path, branch, reason,
    candidate}]}, or {"skip": reason} when the repo cannot be judged."""
    config = config if config is not None else sl.load_config()
    entries = worktrees(project)
    base = base_branch(project)
    if not entries or base is None:
        return {"skip": "nobase" if entries else "nogit", "remove": [], "delete": [], "kept": []}
    merged = merged_branches(project, base)
    cutoff = now - hours * 3600
    old = now - CANDIDATE_DAYS * 86400
    out = {"remove": [], "delete": [], "kept": [], "base": base}
    held = {e["branch"] for e in entries if e["branch"]}
    for e in entries[1:]:
        path, branch = e["path"], e["branch"]
        if e["prunable"] or not os.path.isdir(path):
            out["kept"].append({"path": path, "branch": branch, "reason": "missing", "candidate": False})
            continue
        real = os.path.realpath(path)
        reason = None
        if e["locked"]:
            reason = "locked"
        elif not branch:
            reason = "detached"
        elif branch == base:
            reason = "base"                                       # a second checkout of main: the branch is never ours to delete
        elif branch not in merged:
            reason = "unmerged"
        elif live is None:
            reason = "unknownlive"                                # a session could be working here and we cannot tell: fail closed
        elif any(_inside(c, real) for c in live):
            reason = "live"
        else:
            code, status, _ = git(path, "status", "--porcelain")
            if code != 0 or status.strip():
                reason = "dirty"
            elif has_ignored_files(path):
                reason = "ignored"
            elif touched_since(path, cutoff):
                reason = "recent"
        if reason is None:
            out["remove"].append({"path": path, "branch": branch})
            continue
        candidate = reason in ("dirty", "unmerged") and not touched_since(path, old)
        out["kept"].append({"path": path, "branch": branch, "reason": reason, "candidate": candidate})
    patterns = keep_patterns(config)
    for name in sorted(merged):
        if name == base or name in held:                          # the worktrees about to go are in `held` and leave their branch with them
            continue
        if any(fnmatch.fnmatchcase(name, p) for p in patterns):
            continue
        stamp = branch_touched(project, name)
        if stamp is None or stamp > cutoff:
            continue
        out["delete"].append(name)
    return out


def apply(project, result):
    """Do the plan. -> {removed: [path], deleted: [branch], failed: [{what, why}]}. Only the safe forms of git."""
    done = {"removed": [], "deleted": [], "failed": []}
    for r in result["remove"]:
        if r["branch"] == result.get("base"):
            continue                                              # plan() never lists it; belt and braces
        code, _, err = git(project, "worktree", "remove", r["path"])
        if code != 0:
            done["failed"].append({"what": r["path"], "why": _first_line(err)})
            continue
        done["removed"].append(r["path"])
        code, _, err = git(project, "branch", "-d", r["branch"])
        if code == 0:
            done["deleted"].append(r["branch"])
        else:
            done["failed"].append({"what": r["branch"], "why": _first_line(err)})
    for name in result["delete"]:
        if name == result.get("base"):
            continue
        code, _, err = git(project, "branch", "-d", name)
        if code == 0:
            done["deleted"].append(name)
        else:
            done["failed"].append({"what": name, "why": _first_line(err)})
    git(project, "worktree", "prune")
    return done


# ---------------------------------------------------------------- the flow count

def _when(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def flow(project, now, hours=IDLE_HOURS):
    """The ledger's last-24-hours count: {made, shut, pending, next, next_count}. Read only."""
    data = sl.load_ledger(project)
    items = data["items"] if isinstance(data, dict) else data
    since = now - hours * 3600
    pending = [i for i in items if i.get("status") in ("open", "doing")]
    made = sum(1 for i in items if (_when(i.get("created")) or 0) > since)
    shut = sum(1 for i in items if i.get("status") in CLOSED and (_when(i.get("done_at")) or 0) > since)
    versions = sorted({str(i.get("phase")) for i in pending if VERSION.fullmatch(str(i.get("phase")))},
                      key=lambda v: [int(x) for x in v.lstrip("v").split(".")])
    nxt = versions[0] if versions else None
    return {"made": made, "shut": shut, "pending": len(pending), "next": nxt,
            "next_count": sum(1 for i in pending if str(i.get("phase")) == nxt) if nxt else 0}


# ---------------------------------------------------------------- the projects

def projects(config=None):
    """-> (paths, left_alone). One path per repo; protected and private projects are counted, never listed."""
    import livework
    config = config if config is not None else sl.load_config()
    found = livework.all_projects(sessions=[], cap=1 << 20)
    left, paths, repos = found["skipped"], [], set()
    for row in found["projects"]:
        path = row["path"]
        if row.get("private") or sl.protected_dir(path, config):
            left += 1
            continue
        if top_level(path) != os.path.realpath(path):
            continue                                              # not the root of a repo: no worktrees of its own to tidy
        repo = common_dir(path)
        if repo and repo not in repos:
            repos.add(repo)
            paths.append(path)
    return paths, left


def leftovers(config=None):
    """-> {"trash": n, "unknown": n, "private": n} for the folders of projects_root that are not projects, sorted from metadata
    only (sortdirs): `trash` are empty folders and idle plugin copies, `unknown` need a decision, `private` are opaque and
    only counted. Nothing is opened, named or moved here."""
    import sortdirs
    config = config if config is not None else sl.load_config()
    out = {"trash": 0, "unknown": 0, "private": 0}
    root = config.get("projects_root")
    root = os.path.realpath(os.path.expanduser(root)) if root else None
    try:
        children = sorted(os.listdir(root)) if root else []
    except OSError:
        return out
    for child in children:
        full = os.path.join(root, child)
        if child.startswith(".") or os.path.islink(full) or not os.path.isdir(full):
            continue
        kind = sortdirs.classify(full, config, root=root)
        if kind["trash_ok"]:
            out["trash"] += 1
        elif kind["private"]:
            out["private"] += 1
        elif kind["kind"] in ("unknown", "orphan_worktree"):
            out["unknown"] += 1
    return out


def load_now():
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return None


def run(only=None, apply_changes=False, hours=IDLE_HOURS, now=None, load=None, live=None):
    """The whole pass -> a report dict (see render). `only` limits it to those project folders (they must be projects)."""
    now = time.time() if now is None else now
    config = sl.load_config()
    found, left = projects(config)
    if only:
        wanted = {os.path.realpath(os.path.expanduser(p)) for p in only}
        found = [p for p in found if os.path.realpath(p) in wanted]
    load = load_now() if load is None else load
    limit = _load_limit()
    busy = load is not None and load > limit
    if live is None and not busy:
        live = live_cwds()
    report = {"projects": [], "left_alone": left, "load": load, "load_limit": limit, "git_skipped": busy,
              "applied": bool(apply_changes and not busy), "hours": hours,
              "totals": {"removed": 0, "deleted": 0, "kept": 0, "failed": 0}}
    for path in found:
        row = {"name": os.path.basename(path), "path": path, "flow": None, "flow_error": None, "git": None, "done": None}
        try:
            row["flow"] = flow(path, now, hours)
        except sl.StudioError as exc:
            row["flow_error"] = _first_line(str(exc))
        if not busy:
            row["git"] = plan(path, now, hours, live, config)
            if report["applied"] and not row["git"].get("skip"):
                row["done"] = apply(path, row["git"])
                report["totals"]["removed"] += len(row["done"]["removed"])
                report["totals"]["deleted"] += len(row["done"]["deleted"])
                report["totals"]["failed"] += len(row["done"]["failed"])
            report["totals"]["kept"] += len(row["git"]["kept"])
        report["projects"].append(row)
    report["leftovers"] = None if busy or only else leftovers(config)
    return report


def _load_limit():
    try:
        return float(sl.env("HOUSEKEEPING_LOAD") or LOAD_LIMIT)
    except ValueError:
        return LOAD_LIMIT


def render(report, lang=None):
    import i18n
    t = lambda key, **kw: i18n.t(key, lang, **kw)
    lines = []
    for row in report["projects"]:
        f = row["flow"]
        if f:
            lines.append(t("hk.flow", project=row["name"], made=f["made"], shut=f["shut"], pending=f["pending"],
                           next=f["next"] or "-", count=f["next_count"]))
            if f["made"] > f["shut"]:
                lines.append(t("hk.flow_rule", project=row["name"]))
        elif row["flow_error"]:
            lines.append(t("hk.flow_error", project=row["name"], why=row["flow_error"]))
    if report["left_alone"]:
        lines.append(t("hk.left_alone", n=report["left_alone"]))
    lo = report.get("leftovers")
    if lo and (lo["trash"] or lo["unknown"]):
        lines.append(t("hk.leftovers", trash=lo["trash"], unknown=lo["unknown"], private=lo["private"]))
    if report["git_skipped"]:
        lines.append(t("hk.busy", load=round(report["load"], 1), limit=round(report["load_limit"], 1)))
        return "\n".join(lines)
    done_word = "hk.removed" if report["applied"] else "hk.would_remove"
    drop_word = "hk.deleted" if report["applied"] else "hk.would_delete"
    candidates = False
    for row in report["projects"]:
        g = row["git"]
        if not g:
            continue
        if g.get("skip"):
            lines.append(t("hk.no_base", project=row["name"]))
            continue
        lines.append(t("hk.project", project=row["name"]))
        for r in g["remove"]:
            lines.append(t(done_word, path=r["path"], branch=r["branch"]))
        for name in g["delete"]:
            lines.append(t(drop_word, branch=name))
        for k in g["kept"]:
            lines.append(t("hk.kept", path=k["path"], branch=k["branch"] or "-", reason=t("hk.r_" + k["reason"], hours=report["hours"])))
            candidates = candidates or k["candidate"]
        for fail in (row["done"] or {}).get("failed", ()):
            lines.append(t("hk.failed", what=fail["what"], why=fail["why"]))
    if candidates:
        lines.append(t("hk.candidates", days=CANDIDATE_DAYS))
    if not report["applied"]:
        lines.append(t("hk.dry"))
    tot = report["totals"]
    if report["applied"]:
        lines.append(t("hk.summary", w=tot["removed"], b=tot["deleted"], k=tot["kept"]))
    return "\n".join(lines)


def save_last(text):
    try:
        os.makedirs(sl.home_state(), exist_ok=True)
        sl.atomic_write(os.path.join(sl.home_state(), LAST), text + "\n")
    except OSError:
        pass


def last():
    try:
        return sl.read_text(os.path.join(sl.home_state(), LAST))
    except OSError:
        return None


# ---------------------------------------------------------------- the daily run (macOS launch agent)

def agents_dir():
    return sl.env("LAUNCH_AGENTS_DIR") or os.path.join(user_home() or os.path.expanduser("~"), "Library", "LaunchAgents")


def plist_path():
    return os.path.join(agents_dir(), LABEL + ".plist")


# R-712 / R-788: the jobs Budgit owns. Each has its own label, args, log and default schedule: "calendar" (a dict, or a list of dicts) or
# "interval" (seconds, at least MIN_INTERVAL). Keep-alive and stand-up are opt-in (jobs.turn_on needs a recorded yes).
MIN_INTERVAL = 600
JOBS = {
    "cleanup": {"name": "cleanup", "label": LABEL, "args": ["housekeeping", "--apply", "--quiet"], "log": "housekeeping.log",
                "calendar": {"Hour": 9, "Minute": 30}},
    "keepalive": {"name": "keepalive", "label": "com.budgit.keepalive", "args": ["job", "run", "keepalive"], "log": "keepalive.log",
                  "interval": 7200},
    "standup": {"name": "standup", "label": "com.budgit.standup", "args": ["job", "run", "standup"], "log": "standup.log",
                "calendar": {"Hour": 8, "Minute": 0}},
}
LAUNCHER = "budgit-job"
NO_PLUGIN_LINE = "budgit: the plugin moved (update?); the next Claude Code session repairs this job"
PYTHON_FALLBACKS = ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3")


class JobError(Exception):
    pass


def _launchctl(*args, timeout=30):
    if (sl.env("HOUSEKEEPING_LAUNCHCTL") or "").lower() == "off":
        return 0
    try:
        return subprocess.run(["launchctl"] + list(args), capture_output=True, text=True, timeout=timeout).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


LAUNCHCTL_TIMEOUT = 2          # seconds per call; the SessionStart hook is killed at 10 s, so bootout + bootstrap + one retry stay at 6 s
RELOAD_MARK = "reload-pending"


def _reload(label, path):
    """Unload and load one launch agent. One retry of the load; raises JobError when the job is still not loaded."""
    uid = os.getuid()
    _launchctl("bootout", "gui/%d/%s" % (uid, label), timeout=LAUNCHCTL_TIMEOUT)
    for _attempt in range(2):
        if _launchctl("bootstrap", "gui/%d" % uid, path, timeout=LAUNCHCTL_TIMEOUT) == 0:
            return
    raise JobError("launchctl could not load " + label)


def user_home():
    """The real user's folder from the passwd database, never $HOME (a project can set that). None where there is no pwd (Windows)."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError, OSError):
        return None


def same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def under_any(path, roots):
    """True when `path`, or one of its parents, IS one of `roots`. Compared by file identity (device and inode), never by text,
    so case folding and firmlinks (/System/Volumes/Data/...) cannot hide a folder."""
    current = os.path.abspath(path)
    while True:
        if any(same_file(current, root) for root in roots):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def state_home_is_default(home):
    """True when `home` IS the default state home of the passwd-database user (what resolve_data_dir gives for that home), never one
    derived from $HOME or BUDGIT_HOME: a project can set both, and a real plist runs whatever launcher sits in that home."""
    uh = user_home()
    if not uh:
        return False
    want = sl.resolve_data_dir(uh)
    return same_file(home, want) or os.path.realpath(home) == os.path.realpath(want)


def test_mode():
    """The test hooks: a LaunchAgents folder of the test's own, and launchctl switched off. Only then may a job leave ~/Library.
    An allow list: the folder must sit under the system temp folder (by file identity) and must not be the user's real one."""
    import tempfile
    agents = sl.env("LAUNCH_AGENTS_DIR")
    if not agents or (sl.env("HOUSEKEEPING_LAUNCHCTL") or "").lower() != "off":
        return False
    if not under_any(agents, [os.path.realpath(tempfile.gettempdir()), "/private/tmp"]):
        return False
    home = user_home()
    if home and same_file(agents, os.path.join(home, "Library", "LaunchAgents")):
        return False
    return True


def launcher_path(home=None):
    return os.path.join(home or sl.home_state(), "bin", LAUNCHER)


def launcher_text(root, python):
    """R-712: the fixed shell file every Budgit-owned launch agent runs. Only this text follows an update, never the plist."""
    import shlex
    for value in (root, python):
        if "\n" in value or "\x00" in value or "\r" in value:
            raise JobError("a path with a line break")
    budgit = os.path.join(root, "bin", "budgit")
    return "\n".join([
        "#!/bin/sh",
        "# Budgit scheduled-job launcher. Written by Budgit; an update rewrites this text, never the launch agent.",
        "py=%s" % shlex.quote(python),
        '[ -x "$py" ] || py=""',
        'if [ -z "$py" ]; then',
        "  for c in %s; do" % " ".join(PYTHON_FALLBACKS),
        '    if [ -x "$c" ]; then py=$c; break; fi',
        "  done",
        "fi",
        'if [ -z "$py" ]; then echo "budgit: no python3 found for this job" >&2; exit 75; fi',
        "b=%s" % shlex.quote(budgit),
        'if [ ! -f "$b" ]; then echo %s >&2; exit 75; fi' % shlex.quote(NO_PLUGIN_LINE),
        'exec "$py" "$b" "$@"',
        ""])


def plist_text(job, launcher, at=None, home=None, every=None):
    """The plist of a new job. `at` (HH:MM) sets a calendar job's time, `every` (seconds) an interval job's period; None = the job's default."""
    import plistlib
    home = home or sl.home_state()
    log = os.path.join(home, job["log"])
    plist = {
        "Label": job["label"],
        "ProgramArguments": ["/bin/sh", launcher] + list(job["args"]),
        "Nice": 10, "LowPriorityIO": True,
        "StandardOutPath": log, "StandardErrorPath": log,
        "EnvironmentVariables": {"BUDGIT_HOME": home} if sl.env("HOME") else {},
    }
    if "interval" in job:
        plist["StartInterval"] = int(every) if every else job["interval"]
    elif at:
        hour, minute = (int(x) for x in at.split(":"))
        plist["StartCalendarInterval"] = {"Hour": hour, "Minute": minute}
    else:
        plist["StartCalendarInterval"] = dict(job["calendar"])
    return plistlib.dumps(plist).decode("utf-8")


def desired_plist(job, current, launcher, home):
    """The plist a job of ours should have, kept from `current` (its time, nice level, environment) except what is frozen by an update."""
    log = os.path.join(home, job["log"])
    want = {"Label": job["label"], "ProgramArguments": ["/bin/sh", launcher] + list(job["args"]),
            "StandardOutPath": log, "StandardErrorPath": log}
    # R-712: only what the design keeps, type-checked; a stale Program or an injected key (DYLD_*, WorkingDirectory...) does not survive,
    # and a bad value falls back to the job's own default (an empty calendar would run the job every minute)
    if "interval" in job:
        want["StartInterval"] = _kept_interval(current.get("StartInterval"), job["interval"])
    else:
        want["StartCalendarInterval"] = _kept_calendar(current.get("StartCalendarInterval"), job["calendar"])
    want["Nice"] = _kept_nice(current.get("Nice"), 10)
    want["LowPriorityIO"] = _kept_bool(current.get("LowPriorityIO"), True)
    env = current.get("EnvironmentVariables")
    if isinstance(env, dict):
        want["EnvironmentVariables"] = {"BUDGIT_HOME": env["BUDGIT_HOME"]} if isinstance(env.get("BUDGIT_HOME"), str) else {}
    return want


def _plist_home(current):
    env = current.get("EnvironmentVariables")
    explicit = env.get("BUDGIT_HOME") if isinstance(env, dict) else None
    return explicit if isinstance(explicit, str) and explicit else sl.resolve_data_dir(os.path.expanduser("~"))


def real_agents_dir():
    """True when the agents folder in use IS (same file) <passwd home>/Library/LaunchAgents. With no LAUNCH_AGENTS_DIR set, the default is that folder by construction."""
    uh = user_home()
    if not uh:
        return False
    if not sl.env("LAUNCH_AGENTS_DIR"):
        return True
    return same_file(agents_dir(), os.path.join(uh, "Library", "LaunchAgents"))


def _calendar_ok(value):
    return isinstance(value, dict) and bool(value) and all(isinstance(k, str) and type(v) is int for k, v in value.items())


def _kept_calendar(value, default):
    """A calendar dict, or a non-empty list of calendar dicts, is kept as it is; anything else falls back to the default."""
    if _calendar_ok(value):
        return value
    if isinstance(value, list) and value and all(_calendar_ok(v) for v in value):
        return value
    return default


def _kept_interval(value, default):
    return value if type(value) is int and value >= MIN_INTERVAL else default


def _kept_nice(value, default):
    return value if type(value) is int and 0 <= value <= 20 else default


def _kept_bool(value, default):
    return value if type(value) is bool else default


def _refuse_links(home, launcher):
    if os.path.islink(home) or os.path.islink(os.path.dirname(launcher)) or os.path.islink(launcher):
        raise JobError("a symlink where the launcher goes")


def repair_job(job, root, python):
    """R-712: bring one existing launch agent of ours onto the fixed launcher. -> "none" (not switched on), "skipped" (another
    home's job), "same", or "changed". Never creates a plist; launchctl only for a plist in ~/Library/LaunchAgents (or the test hooks)."""
    import plistlib
    path = os.path.join(agents_dir(), job["label"] + ".plist")
    if not os.path.exists(path):
        return "none"
    if not test_mode() and not real_agents_dir():
        raise JobError("a LaunchAgents folder that is not the user's")
    with open(path, "rb") as f:
        current = plistlib.load(f)
    plist_home = _plist_home(current)
    if os.path.realpath(plist_home) != os.path.realpath(sl.home_state()):
        return "skipped"                                           # a project cannot move the job to a home it controls
    home = sl.home_state()
    if not test_mode():
        uh = user_home()
        if not uh or same_file(home, uh) or not under_any(home, [uh]) or not state_home_is_default(home):
            raise JobError("a home outside your user folder")
    launcher = launcher_path(home)
    _refuse_links(home, launcher)
    text = launcher_text(root, python)
    changed = False
    try:
        old = sl.read_text(launcher)
    except OSError:
        old = None
    if old != text:
        os.makedirs(os.path.dirname(launcher), exist_ok=True)
        _refuse_links(home, launcher)
        sl.atomic_write(launcher, text, 0o700)
        changed = True
    want = desired_plist(job, current, launcher, home)
    marker = os.path.join(home, RELOAD_MARK + "-" + job["label"])
    pending = os.path.lexists(marker)                              # an earlier reload was cut short: load the job again
    if want != current or pending:
        sl.atomic_write(marker, "reload\n", 0o600)                 # stays until the job is loaded, so a killed hook retries
        if want != current:
            sl.atomic_write(path, plistlib.dumps(want).decode("utf-8"))
        _reload(job["label"], path)
        os.remove(marker)
        changed = True
    return "changed" if changed else "same"


def schedule(op, budgit_path, at=None, name="cleanup", every=None):
    """-> (exit code, message key, fields). op: on, off, status. Writes only the one plist of job `name` and talks only to launchctl.
    `at` (HH:MM) is for calendar jobs, `every` (seconds) for interval jobs; None = the job's default."""
    if sys.platform != "darwin" and not sl.env("LAUNCH_AGENTS_DIR"):
        return 64, "hk.sched_platform", {}
    job = JOBS[name]
    LABEL = job["label"]                                            # this job's label (shadows the module's cleanup label here)
    path = os.path.join(agents_dir(), LABEL + ".plist")
    uid = os.getuid()
    if op == "status":
        if not os.path.exists(path):
            return 0, "hk.sched_off", {}
        try:
            import plistlib
            with open(path, "rb") as f:
                program = plistlib.load(f)["ProgramArguments"]
        except Exception:
            program = []
        if len(program) > 1 and program[0] == "/bin/sh" and os.path.basename(program[1]) == LAUNCHER:
            import installstep as upgrade
            rec = upgrade.record_read(sl.home_state()) or {}
            target = os.path.join(rec.get("root") or "", "bin", "budgit")
            if not (os.path.exists(program[1]) and rec.get("root") and os.path.exists(target)):
                return 1, "hk.sched_stale_launcher", {"path": target if rec.get("root") else program[1]}
            return 0, "hk.sched_on", {"path": path}
        program = program[1] if len(program) > 1 else ""
        if not (program and os.path.exists(program)):
            return 1, "hk.sched_stale", {"path": program or path}
        return 0, "hk.sched_on", {"path": path}
    if op == "off":
        _launchctl("bootout", "gui/%d/%s" % (uid, LABEL))
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            return 1, "hk.sched_failed", {"why": path}
        return 0, "hk.sched_removed", {}
    if "interval" in job:
        if every is not None and not (type(every) is int and every >= MIN_INTERVAL):
            return 64, "hk.sched_time", {"at": str(every)}
    elif at is not None and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at):
        return 64, "hk.sched_time", {"at": at}
    import updatelib
    import installstep as upgrade
    home = sl.home_state()
    root = os.path.dirname(os.path.dirname(os.path.realpath(budgit_path)))
    real = not test_mode()
    uh = user_home()
    if real and not uh:
        return 1, "hk.sched_failed", {"why": "no user folder from the system"}
    if real and sl.env("HOME"):
        return 1, "hk.sched_failed", {"why": "the state home is set by BUDGIT_HOME; a job runs only from the default home in your user folder"}   # a project can set that variable
    if real and os.environ.get("CLAUDE_CONFIG_DIR"):
        return 1, "hk.sched_failed", {"why": "CLAUDE_CONFIG_DIR is set; a job is switched on only from the default Claude Code folder"}   # a project can set it
    if real and not real_agents_dir():
        return 1, "hk.sched_failed", {"why": "a LaunchAgents folder that is not the user's"}
    if real and not (under_any(home, [uh]) and not same_file(home, uh) and state_home_is_default(home)):
        return 1, "hk.sched_failed", {"why": "a home outside your user folder"}
    trusted = updatelib.registered_root(root, os.path.join(uh, ".claude") if real else None) is not None   # outside test mode: the user's registry, never $HOME's
    if sl.vendored() or not trusted:
        return 1, "hk.sched_failed", {"why": "this copy is not the install Claude Code registered"}   # R4: no job from a vendored or loose copy, in test mode too
    launcher = launcher_path(home)
    try:
        if os.path.islink(home):
            raise JobError("a symlink where the home goes")
        os.makedirs(home, exist_ok=True)
        rec = upgrade.record_update(root, updatelib.running_version(root), upgrade.interpreter(), trusted)
        if not rec:
            raise JobError("the install record could not be written")
        _refuse_links(home, launcher)
        os.makedirs(os.path.dirname(launcher), exist_ok=True)
        _refuse_links(home, launcher)
        sl.atomic_write(launcher, launcher_text(rec["root"], rec["python"]), 0o700)
    except (JobError, OSError) as exc:
        return 1, "hk.sched_failed", {"why": str(exc)}
    os.makedirs(agents_dir(), exist_ok=True)
    sl.atomic_write(path, plist_text(job, launcher, at, home, every))
    _launchctl("bootout", "gui/%d/%s" % (uid, LABEL), timeout=LAUNCHCTL_TIMEOUT)
    if _launchctl("bootstrap", "gui/%d" % uid, path, timeout=LAUNCHCTL_TIMEOUT) != 0:
        return 1, "hk.sched_failed", {"why": path}
    shown = at or "%02d:%02d" % (job["calendar"]["Hour"], job["calendar"]["Minute"]) if "calendar" in job else "every %d s" % (every or job["interval"])
    return 0, "hk.sched_set", {"at": shown, "path": path}
