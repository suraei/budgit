"""R-199: what is happening now, across every project (the data behind `budgit status --brote --live`).

Sources, none of them needing a new permission:
  - the ledgers of every project (claims: items with status doing),
  - Budgit's session pointers (`<home_state>/sessions/<id>.json`: which project a session was activated on),
  - Claude Code's live-session files (`$CLAUDE_CONFIG_DIR/sessions/<pid>.json`, an undocumented format).
Only an allow-list of the session fields is read; everything else in those files (sockets, bridge ids, peer data) is
never copied into any output. Stdlib only. Times are epoch seconds (UTC)."""
import calendar
import datetime
import hashlib
import json
import os
import re
import sys
import time

import studiolib as sl

MAX_PROJECTS = 12
MAX_WORKING = 20
MAX_PAUSED = 10
MAX_WATCH = 64
QUEUE_OTHER = 3
QUEUE_HUB = 8
RECENT_DONE = 6
TITLE_MAX = 90
BUSY_FALLBACK_S = 12 * 3600       # no kernel answer: a "busy" older than this cannot be trusted
START_TOLERANCE_S = 2             # procStart has whole-second resolution
STATUSES = ("busy", "idle", "waiting")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
PROTECTED_HOME = ("Desktop", "Downloads", os.path.join("Library", "Mobile Documents"))
# the only keys of ~/.claude/sessions/*.json that are ever read (a test pins this list)
SESSION_FIELDS = ("pid", "sessionId", "cwd", "name", "status", "statusUpdatedAt", "procStart", "kind", "waitingFor",
                  "pidDomain")


def epoch(stamp):
    return sl._epoch(stamp)


def _home():
    return os.path.realpath(os.path.expanduser("~"))


def _under(path, base):
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _line(text, limit=TITLE_MAX):
    return " ".join(str(text).split())[:limit]


# ---------------------------------------------------------------- Claude Code's live sessions

def sessions_dir():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(os.path.expanduser(base), "sessions")


_libc = []


def kernel_start(pid):
    """The kernel's start time of `pid` (epoch seconds, UTC) from sysctl KERN_PROC_PID through ctypes, or None when
    it cannot be read (not macOS, process gone, any error). About 0.3 ms per pid."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util
        import struct
        if not _libc:
            _libc.append(ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib", use_errno=True))
        libc = _libc[0]
        mib = (ctypes.c_int * 4)(1, 14, 1, int(pid))       # CTL_KERN, KERN_PROC, KERN_PROC_PID, pid
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 4, None, ctypes.byref(size), None, 0) != 0 or size.value < 16:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0 or size.value < 16:
            return None
        sec, _usec = struct.unpack_from("@qi", buf.raw, 0)  # kp_proc.p_un.__p_starttime (a struct timeval)
        return int(sec) or None
    except Exception:
        return None


def parse_proc_start(text):
    """'Sun Oct 25 10:27:13 2026' (UTC, ctime layout) -> epoch seconds, or None."""
    try:
        _dow, mon, day, clock, year = str(text).split()
        hh, mm, ss = (int(x) for x in clock.split(":"))
        return calendar.timegm((int(year), MONTHS.index(mon) + 1, int(day), hh, mm, ss))
    except (ValueError, TypeError):
        return None


def _alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _verified(rec, status, since, now):
    """True only when this file describes the live process: pid domain darwin, and procStart equals the kernel's start
    time of that pid. Without a kernel answer (spike fallback) a `busy` older than 12 h is not trusted."""
    if rec.get("pidDomain") != "darwin":
        return False
    declared = parse_proc_start(rec.get("procStart"))
    actual = kernel_start(rec["pid"]) if declared is not None else None
    if actual is not None:
        return abs(actual - declared) <= START_TOLERANCE_S
    return not (status == "busy" and now - since > BUSY_FALLBACK_S)


def _waiting_for(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    low = raw.lower()
    if "permission" in low:
        return "permission"
    if "input" in low or "question" in low:
        return "question"
    return "other"


def live_sessions(now=None):
    """{'status': ok|absent|unreadable, 'dir', 'sessions': [...]}. A session is listed when its pid is alive;
    `verified` says whether the file can be trusted as a live row. Dead pids are dropped."""
    now = now if now is not None else time.time()
    folder = sessions_dir()
    out = {"status": "ok", "dir": folder, "sessions": []}
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except FileNotFoundError:
        out["status"] = "absent"
        return out
    except OSError:
        out["status"] = "unreadable"
        return out
    for name in names:
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            rec = {k: raw.get(k) for k in SESSION_FIELDS}
            mtime = os.stat(path).st_mtime
        except (OSError, ValueError, AttributeError):
            continue
        if not sl.valid_session(rec["sessionId"]) or not _alive(rec["pid"]) or not isinstance(rec["cwd"], str):
            continue
        status = rec["status"] if rec["status"] in STATUSES else "idle"
        ms = rec["statusUpdatedAt"]
        since = int(ms / 1000) if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0 else int(mtime)
        out["sessions"].append({
            "pid": rec["pid"], "session_id": rec["sessionId"], "cwd": rec["cwd"], "status": status, "since": since,
            "name": _line(rec["name"]) if isinstance(rec["name"], str) else "",
            "kind": rec["kind"] if isinstance(rec["kind"], str) else "",
            "waiting_for": _waiting_for(rec["waitingFor"]) if status == "waiting" else None,
            "verified": _verified(rec, status, since, now), "file": path})
    return out


# ---------------------------------------------------------------- the project registry

def is_protected(path, home=None):
    home = home or _home()
    low = path.lower()
    if _under(low, "/volumes"):
        return True
    return any(_under(low, os.path.join(home, sub).lower()) for sub in PROTECTED_HOME)


def _config_dir_strings(home):
    """The `protected_dirs` strings of every Budgit config this user may have: the live one and both home-folder
    names (.budgit, .studio). Strings only: nothing here touches the folders they name."""
    raw = []
    try:
        raw += list(sl.load_config().get("protected_dirs") or ())
    except Exception:
        pass
    for name in (".budgit", ".studio"):
        try:
            with open(os.path.join(home, name, "config.json"), encoding="utf-8") as f:
                raw += list(json.load(f).get("protected_dirs") or ())
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return [d for d in raw if isinstance(d, str) and d.strip()]


def _config_private_dirs(home):
    """`protected_dirs` as real paths, `~` expanded, case-folded (APFS folds case)."""
    out = []
    for d in _config_dir_strings(home):
        real = os.path.realpath(os.path.expanduser(d)).casefold()
        if real not in out:
            out.append(real)
    return out


def is_private(path, home=None):
    """A project (or a folder) whose contents must reach no feed field, only counts: it sits under a `protected_dirs`
    folder of the config, or its own project.json says `"private": true` (that travels with the project when its
    folder moves, which the config list does not). `protected_paths` is the guard's real-data list, not privacy."""
    home = home or _home()
    low = os.path.realpath(path).casefold()
    if any(_under(low, d) for d in _config_private_dirs(home)):
        return True
    try:
        return bool(has_ledger(path) and sl.load_project_config(path).get("private") is True)
    except Exception:
        return False


# folder names too common to say "this text is about a protected project" (a `protected_dirs` entry of `~/Dev` hides
# the folder, not every title with the word dev)
GENERIC_DIRS = {"documents", "dev", "desktop", "downloads", "library", "home", "users", "volumes", "projects", "code",
                "work", "src", "tmp", "repos", "github", "data", "private"}


def _norm(text):
    """Case-folded, every run of punctuation or space one space: `V.A.C.A`, `v-a-c-a` and `V A C A` read alike."""
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def private_names(home=None, paths=()):
    """Normalised folder names of the `protected_dirs` entries and of the given private project paths, read from the
    strings alone (the folders are never opened). Names under 3 characters and generic folder names are left out."""
    home = home or _home()
    skip = GENERIC_DIRS | {_norm(os.path.basename(home.rstrip(os.sep)))}
    out = []
    for raw in list(_config_dir_strings(home)) + list(paths):
        name = _norm(os.path.basename(os.path.expanduser(raw).rstrip(os.sep)))
        if len(name) >= 3 and name not in skip and name not in out:
            out.append(name)
    return out


def shield(text, names):
    """"private" for a text that names a protected project, else the text. A name of 4+ characters matches anywhere
    (`mynotes` hides for `notes`), a 3-letter one only as a whole word."""
    if not isinstance(text, str) or not names:
        return text
    n = _norm(text)
    padded = " %s " % n
    for name in names:
        if (name in n) if len(name) >= 4 else (" %s " % name in padded):
            return "private"
    return text


def shield_v2(out, names):
    """The v2 title lists of `status --brote`: a title or text that names a protected project reads "private"."""
    for key in ("queue_recent", "done_recent", "doing", "queue", "later"):
        for i in out.get(key) or ():
            if isinstance(i, dict):
                for field in ("title", "text"):
                    if field in i:
                        i[field] = shield(i[field], names)
    return out


def canon(path):
    """The real path of a project folder; a git worktree (`.git` is a file pointing into <repo>/.git/worktrees/) is
    the project at <repo>, never a project of its own."""
    real = os.path.realpath(path)
    dotgit = os.path.join(real, ".git")
    try:
        if os.path.isfile(dotgit):
            with open(dotgit, encoding="utf-8") as f:
                first = (f.read(1024).splitlines() or [""])[0]
            if first.startswith("gitdir:"):
                gitdir = first[len("gitdir:"):].strip()
                gitdir = os.path.realpath(gitdir if os.path.isabs(gitdir) else os.path.join(real, gitdir))
                worktrees = os.path.dirname(gitdir)
                if os.path.basename(worktrees) == "worktrees" and os.path.basename(os.path.dirname(worktrees)) == ".git":
                    return os.path.dirname(os.path.dirname(worktrees))
    except OSError:
        pass
    return real


def _ledger_file(project):
    return os.path.join(sl.studio_dir(project), "ledger.json")


def has_ledger(project):
    return os.path.isfile(_ledger_file(project))


def _walk_up(cwd, home):
    """The project that holds `cwd`: climbs to (never into) the home folder, never enters the Budgit home state."""
    p = os.path.realpath(cwd)
    state = os.path.realpath(sl.home_state())
    while p != home and _under(p, home) and not _under(p, state):
        c = canon(p)
        if has_ledger(c):
            return c
        p = os.path.dirname(p)
    return None


def _pointer_projects():
    folder = os.path.join(sl.home_state(), "sessions")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                rec = json.load(f)
            if isinstance(rec, dict) and isinstance(rec.get("project"), str):
                yield rec["project"]
        except (OSError, ValueError):
            continue


def _short_id(path):
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:4]


def all_projects(sessions=None, extra=(), cap=MAX_PROJECTS):
    """Every project that has a ledger, merged from session pointers, the hub, the children of projects_root and the
    walk-up of each live session's cwd. De-duplicated by real path, worktrees folded into their repo, protected
    places skipped (counted in `skipped`), at most `cap` kept (hub, then live ones, then newest ledger).
    Returns {'projects': [{id, name, path, hub, live, mtime, private}], 'skipped', 'capped', 'attrib': {session_id: path}}."""
    home = _home()
    cfg = sl.load_config()
    try:
        hub = sl.hub_path(cfg)
    except sl.StudioError:
        hub = None
    root = os.path.realpath(os.path.expanduser(cfg["projects_root"])) if cfg.get("projects_root") else None
    if sessions is None:
        sessions = live_sessions()["sessions"]
    cands = [hub] if hub else []
    cands += list(_pointer_projects())
    cands += [e for e in extra if e]
    if root:
        try:
            cands += [os.path.join(root, n) for n in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, n))]
        except OSError:
            pass
    walked = {}
    for s in sessions:
        walked[s["session_id"]] = _walk_up(s["cwd"], home)
        cands.append(walked[s["session_id"]])
    valid, skipped = {}, set()
    for raw in cands:
        if not raw or not isinstance(raw, str):
            continue
        c = canon(os.path.expanduser(raw))
        if c in valid or c in skipped or not has_ledger(c):
            continue
        if is_protected(c, home) and not (hub and _under(c, hub)) and not (root and _under(c, root)):
            skipped.add(c)
            continue
        valid[c] = True
    attrib = {}
    for s in sessions:
        proj = None
        try:
            rec = sl.read_pointer(s["session_id"])
        except sl.StudioError:
            rec = None
        if rec and isinstance(rec.get("project"), str):
            guess = canon(os.path.expanduser(rec["project"]))
            proj = guess if guess in valid else None
        if proj is None and walked.get(s["session_id"]) in valid:
            proj = walked[s["session_id"]]
        attrib[s["session_id"]] = proj
    live = {}
    for proj in attrib.values():
        if proj:
            live[proj] = live.get(proj, 0) + 1
    rows = []
    for path in valid:
        try:
            mtime = int(os.stat(_ledger_file(path)).st_mtime)
        except OSError:
            mtime = 0
        rows.append({"path": path, "hub": path == hub, "live": live.get(path, 0), "mtime": mtime,
                     "private": is_private(path, home)})
    rows.sort(key=lambda r: (not r["hub"], -r["live"], -r["mtime"], r["path"]))
    capped = len(rows) > cap
    rows = rows[:cap]
    taken = set()
    for r in rows:                     # the first by that order keeps the plain basename, later ones get ~hash
        name = "private" if r["private"] else os.path.basename(r["path"]) or r["path"]      # a private folder name stays unsaid
        r["name"] = name
        r["id"] = name if name not in taken and not r["private"] else "%s~%s" % (name, _short_id(r["path"]))
        taken.add(r["id"])
    kept = {r["path"] for r in rows}
    return {"projects": rows, "skipped": len(skipped), "capped": capped,
            "attrib": {sid: (p if p in kept else None) for sid, p in attrib.items()}}


# ---------------------------------------------------------------- rows

def _claim_row(c, sess):
    return {"kind": "claim", "project": c["project"], "item": c["item"], "title": c["title"],
            "session": c["doing_by"][:8], "session_name": sess["name"] if sess else None,
            "session_status": sess["status"] if sess else None, "waiting_for": sess["waiting_for"] if sess else None,
            "since": c["since"] or (sess["since"] if sess else 0)}


def working_rows(claims, sessions, attrib, ids, sessions_ok, hidden=()):
    """(working, paused). claims: [{project, item, title, doing_by, since, raw}] from every ledger.
    Under a sound session source a claim is live only while its claimant is a verified live session; otherwise it is
    paused (`claimant_gone` | `idle`). Without session data the 12 h rule of `claim_state` decides."""
    by_id = {s["session_id"]: s for s in sessions}
    working, paused, held = [], [], set()
    for c in claims:
        owner = c["doing_by"]
        sess = by_id.get(owner) if owner else None
        if sessions_ok:      # a claim is live while its claimant works or waits for the user; an idle one is on the paused line
            live = bool(sess and sess["verified"] and sess["status"] in ("busy", "waiting"))
            reason = None if live else ("claimant_gone" if not sess and owner else "idle")
        else:
            state = sl.claim_state(c["raw"])
            live = state == "live"
            reason = None if live else ("claimant_gone" if state == "dead" else "idle")
            sess = None
        if live:
            held.add(owner)
            working.append(_claim_row(c, sess))
        else:
            paused.append({"project": c["project"], "item": c["item"], "title": c["title"],
                           "session": owner[:8] if owner else None, "reason": reason, "since": c["since"]})
    for s in sessions:
        if s["session_id"] in hidden:           # a session in a private project: its title is never shown
            continue
        if s["verified"] and s["status"] in ("busy", "waiting") and s["session_id"] not in held:
            proj = attrib.get(s["session_id"])
            title = s["name"] or (os.path.basename(s["cwd"].rstrip(os.sep)) or "?")
            working.append({"kind": "session", "project": ids.get(proj), "title": title, "session": s["session_id"][:8],
                            "session_name": s["name"], "session_status": s["status"], "waiting_for": s["waiting_for"],
                            "since": s["since"]})
    group = lambda r: 0 if r["session_status"] == "waiting" else 1 if r["kind"] == "claim" else 2
    working.sort(key=lambda r: (group(r), -(r["since"] or 0)))
    paused.sort(key=lambda r: -(r["since"] or 0))
    return working[:MAX_WORKING], paused[:MAX_PAUSED]


def quiet_sessions(sessions):
    """Live sessions that are not a verified busy/waiting one: idle sessions and unverified ones."""
    return sum(1 for s in sessions if not (s["verified"] and s["status"] in ("busy", "waiting")))


def watch_paths(ledgers, folder, sessions, home=None):
    """What the app stats while the Libreta is open: the ledgers, the sessions folder and each live <pid>.json.
    Absolute real paths under the home folder, no duplicates, at most 64. Pointer files are never listed."""
    home = home or _home()
    out = []
    for path in list(ledgers) + [folder] + [s["file"] for s in sessions]:
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError):
            continue
        if real != home and _under(real, home) and real not in out and os.path.exists(real):
            out.append(real)
        if len(out) >= MAX_WATCH:
            break
    return out


def week_counts(rows, now=None):
    """Done items per day of the current week, Monday first, local time. rows: (project, item, title, epoch)."""
    today = datetime.date.fromtimestamp(now if now is not None else time.time())
    monday = today - datetime.timedelta(days=today.weekday())
    days = [0] * 7
    for _p, _i, _t, ts in rows:
        idx = (datetime.date.fromtimestamp(ts) - monday).days
        if 0 <= idx < 7:
            days[idx] += 1
    return days


def _iso(ts):
    """The panel reads `done_at` as a UTC ISO string (the v3.1 spec), never a number."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def scrub_v2(out):
    """`status --brote` of a private project: the counts stay, every title and text list is emptied."""
    for key in ("queue_recent", "done_recent", "doing", "queue", "later"):
        out[key] = []
    return out


def _newest(items):
    def num(i):
        try:
            return int(i["id"].split("-", 1)[1])
        except (IndexError, ValueError):
            return -1
    return sorted(items, key=num, reverse=True)


def live_feed(current=None, now=None, v2=None):
    """The v3/v3.1 keys of `status --brote --live`. Never raises: a broken part becomes an `unreadable` source.
    `v2`: the v2 dict of the same call, whose title lists get the same name shield."""
    now = now if now is not None else time.time()
    empty = {"working": [], "paused": [], "quiet_sessions": 0, "projects": [], "week": [0] * 7, "recent_done": [],
             "watch": {"files": [], "check_s": 2},
             "sources": {"claims": "unreadable", "sessions": "unreadable", "projects": 0, "skipped": 0, "capped": False}}
    try:
        found = live_sessions(now)
        reg = all_projects(found["sessions"], extra=[current] if current else ())
        ids = {r["path"]: r["id"] for r in reg["projects"]}
        private = {r["path"] for r in reg["projects"] if r["private"]}
        hidden = {sid for sid, proj in reg["attrib"].items() if proj in private}
        # a session in a private folder, or in one of the protected places (Desktop, Downloads, iCloud, /Volumes) whether or
        # not the config lists it, shows no name and no folder
        hidden |= {s["session_id"] for s in found["sessions"]
                   if is_private(s["cwd"]) or is_protected(os.path.realpath(s["cwd"]))}
        names = private_names(paths=[r["path"] for r in reg["projects"] if r["private"]])
        hide = lambda text: shield(text, names)             # a title that names a protected project reads "private"
        if v2 is not None:
            shield_v2(v2, names)
        projects, claims, done_rows, week_rows, ledgers, loaded = [], [], [], [], [], 0
        for r in reg["projects"]:
            if r["path"] not in private:
                ledgers.append(_ledger_file(r["path"]))
            row = {"id": r["id"], "name": r["name"], "hub": r["hub"], "live": r["live"], "error": None, "counts": None,
                   "queue_recent": [], "last_change": r["mtime"]}
            try:
                data = sl.load_ledger(r["path"])
            except Exception:
                row["error"] = "ledger unreadable"
                projects.append(row)
                continue
            loaded += 1
            items = data["items"]
            later = sum(1 for i in items if i["status"] == "open" and i.get("phase") == "later")
            queue = [i for i in items if i["status"] == "open" and i.get("phase") != "later"]
            closed = [i for i in items if i["status"] in ("done", "verified")]
            row["counts"] = {"doing": sum(1 for i in items if i["status"] == "doing"),
                             "next": sum(1 for i in items if i["status"] == "open") - later, "later": later,
                             "done": len(closed)}
            if r["path"] in private:                 # counts only: no title, no id, no path leaves this function
                row["private"] = True
                projects.append(row)
                for i in closed:
                    ts = None if i.get("done_at_unproven") else epoch(i.get("done_at")) or epoch(i.get("updated"))
                    if ts:
                        week_rows.append(("", "", "", ts))
                continue
            row["queue_recent"] = [{"id": i["id"], "title": hide(_line(sl.item_title(i)))}
                                   for i in _newest(queue)[:QUEUE_HUB if r["hub"] else QUEUE_OTHER]]
            projects.append(row)
            for i in items:
                if i["status"] == "doing":
                    claims.append({"project": r["id"], "item": i["id"], "title": hide(_line(sl.item_title(i))),
                                   "doing_by": i.get("doing_by") or "", "since": epoch(i.get("doing_since")) or 0, "raw": i})
            for i in closed:
                ts = None if i.get("done_at_unproven") else epoch(i.get("done_at")) or epoch(i.get("updated"))
                if ts:
                    done_rows.append((r["id"], i["id"], hide(_line(sl.item_title(i))), ts))
        sessions_ok = found["status"] == "ok"
        working, paused = working_rows(claims, found["sessions"], reg["attrib"],
                                       {p: i for p, i in ids.items()}, sessions_ok, hidden)
        for r in working:                       # a session title is the user's to write: it may name a protected project too
            r["title"] = hide(r["title"])
            r["session_name"] = hide(r["session_name"])
        done_rows.sort(key=lambda d: -d[3])
        return {"working": working, "paused": paused, "quiet_sessions": quiet_sessions(found["sessions"]),
                "projects": projects, "week": week_counts(done_rows + week_rows, now),
                "recent_done": [{"project": p, "item": i, "title": t, "done_at": _iso(ts)}
                                for p, i, t, ts in done_rows[:RECENT_DONE]],
                "watch": {"files": watch_paths(ledgers, found["dir"], found["sessions"]), "check_s": 2},
                "sources": {"claims": "ok" if loaded else ("absent" if not reg["projects"] else "unreadable"),
                            "sessions": found["status"], "projects": len(projects), "skipped": reg["skipped"],
                            "capped": reg["capped"]}}
    except Exception:
        if v2 is not None:
            scrub_v2(v2)                         # the private projects' names are not known: no titles at all
        return empty
