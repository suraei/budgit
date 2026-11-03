"""R-283 S6: `budgit sweep [--all] [--json]`, the read-only hygiene list (plan 5.3).

It lists, and never changes anything: no worktree is removed, no process is signalled, no session is archived. Per
project: `doing` claims with no ledger change for a week (the ledger's own rule), worktrees that are removable (branch
merged into the default branch, clean, not young, no live chat in it) or must be asked about, the dirty count and a stray
second data folder. Once for the machine: pointers of ended sessions (and which of them hold no claim, so their lane
can be archived by the PM, who checks the app side), and stray dev servers / test runs / headless browsers that sit in a
registered project or one of its worktrees.

A protected project shows counts only, in every field: no name, branch, worktree folder, file name or error text. A
process row never carries its command line or its folder, only its kind, pid, age, ports and the owning project.
Every git, ps and lsof call has a timeout; a section that cannot be read says `unknown`, never an empty "all clear".
"""
import os
import re
import subprocess
import time

import livework
import portfolio
import studiolib as sl

VERSION = 1
TIMEOUT_S = 6
BUDGET_S = 30                    # one deadline for the whole sweep: a hung git or lsof costs seconds, not seconds per project
_deadline = None
YOUNG_S = 86400                  # a worktree whose HEAD moved less than a day ago may be a lane that just started
KINDS = (("http.server", r"(?<![\w.-])http\.server(?![\w.-])"),
         ("vite", r"(?<![\w.-])vite(?![\w-])"),
         ("next", r"(?<![\w.-])next\s+dev(?![\w-])|(?<![\w.-])next-server(?![\w-])"),
         ("dev-server", r"(?<![\w.-])(?:npm|yarn|pnpm)\s+(?:run\s+)?dev(?![\w-])|(?<![\w.-])webpack-dev-server(?![\w-])"),
         ("test-run", r"(?<![\w.-])(?:pytest|unittest)(?![\w-])"),
         ("headless-browser", r"--headless"))
_KINDS = tuple((k, re.compile(rx)) for k, rx in KINDS)


def _run(argv, timeout=TIMEOUT_S):
    """(returncode, stdout) or None when the program is missing, times out or cannot run. Never writes."""
    if _deadline is not None:
        left = _deadline - time.monotonic()
        if left <= 0:
            return None
        timeout = min(timeout, left)
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return out.returncode, out.stdout


def _git(path, *args):
    return _run(["git", "--no-optional-locks", "-C", path, *args])


def _dirty(path):
    """Uncommitted paths like the portfolio view counts them, but inside the sweep's one time budget."""
    if not os.path.exists(os.path.join(path, ".git")):
        return None
    res = _git(path, "status", "--porcelain")
    if res is None or res[0]:
        return None
    skip = (sl.DATA_NAME + "/", sl.OLD_DATA_NAME + "/")
    names = (line[3:].strip('"') for line in res[1].splitlines())
    return sum(1 for name in names if name and not name.startswith(skip))


def _real(path):
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


def _inside(path, base):
    path, base = _real(path), _real(base)
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


# ---------------------------------------------------------------- worktrees

def _base_ref(project):
    """The default branch other branches are measured against: origin/HEAD, then local main, then master."""
    got = _git(project, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if got and got[0] == 0 and got[1].strip():
        ref = got[1].strip()
        ok = _git(project, "rev-parse", "--verify", "-q", ref + "^{commit}")
        if ok and ok[0] == 0:
            return ref
    for name in ("main", "master"):
        got = _git(project, "rev-parse", "--verify", "-q", "refs/heads/%s^{commit}" % name)
        if got and got[0] == 0:
            return "refs/heads/" + name
    return None


def _listed(project):
    """[(path, branch ref or None)] for every linked worktree (the main one is not listed); None when git failed."""
    got = _git(project, "worktree", "list", "--porcelain")
    if not got or got[0] != 0:
        return None
    blocks, cur = [], {}
    for line in got[1].splitlines() + [""]:
        if not line.strip():
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        cur[key] = val
    return [(b["worktree"], b.get("branch")) for b in blocks[1:] if "worktree" in b]


def _age_days(ts, now):
    return max(0, int((now - ts) // 86400))


def _private_files(path):
    """Files in the worktree's own private data folder that git ignores and removal would lose (the card is rebuilt)."""
    for data in (sl.DATA_NAME, sl.OLD_DATA_NAME):
        for _, _, files in os.walk(os.path.join(path, data, "private")):
            if any(f != "card.json" for f in files):
                return True
    return False


def _worktree_row(project, path, branch, base, live_cwds, now):
    """One linked worktree: removable only when nothing could be lost. Every doubt is `ask`."""
    row = {"name": os.path.basename(path.rstrip(os.sep)), "state": "ask", "reason": "unknown", "age_days": None}
    if not os.path.isdir(path):
        row["reason"] = "gone"
        return row
    stamp = _git(path, "log", "-1", "--format=%ct")
    if stamp and stamp[0] == 0 and stamp[1].strip().isdigit():
        row["age_days"] = _age_days(int(stamp[1].strip()), now)
    if any(_inside(c, path) for c in live_cwds):
        row["reason"] = "live"
        return row
    status = _git(path, "status", "--porcelain")              # no data-folder exclusion: a private ledger counts
    if not status or status[0] != 0:
        return row
    if status[1].strip() or _private_files(path):
        row["reason"] = "dirty"
        return row
    if not branch or not base:
        row["reason"] = "unknown"
        return row
    merged = _git(path, "merge-base", "--is-ancestor", branch, base)
    if not merged or merged[0] not in (0, 1):
        return row
    if merged[0] == 1:
        row["reason"] = "unmerged"
        return row
    gitdir = _git(path, "rev-parse", "--absolute-git-dir")
    head = os.path.join(gitdir[1].strip(), "HEAD") if gitdir and gitdir[0] == 0 else None
    try:
        moved = os.path.getmtime(head) if head else None
    except OSError:
        moved = None
    if moved is None:
        row["reason"] = "unknown"
        return row
    if now - moved < YOUNG_S:
        row["reason"] = "young"
        return row
    row.update(state="removable", reason="merged")
    return row


def worktrees(project, live_cwds, now):
    """(status, rows, folders): status `ok`, or `unknown` when git could not list them."""
    listed = _listed(project)
    if listed is None:
        return "unknown", [], []
    base = _base_ref(project)
    return "ok", [_worktree_row(project, p, b, base, live_cwds, now) for p, b in listed], [p for p, _ in listed]


# ---------------------------------------------------------------- per project

def _project(entry, ident, name, is_priv, live_cwds, now):
    path = entry["path"]
    out = {"id": ident, "name": name, "protected": is_priv, "status": "ok"}
    if not os.path.isdir(path):
        out["status"] = "missing"
        return out, []
    tasks, stuck = portfolio._tasks(path, is_priv)
    claims = None
    if tasks is not None:
        if is_priv:
            import card
            claims = {"stuck": stuck} if card.state(path)[0] != "missing" else None
        else:
            try:
                items = sl.stale_doing(sl.load_ledger(path), now)
                claims = [{"id": i["id"], "age_days": _age_days(sl._epoch(i.get("updated") or i.get("created")) or now, now)}
                          for i in items]
            except Exception:
                claims = None
    wt = worktrees(path, live_cwds, now)
    out["worktrees_status"] = wt[0]
    rows = wt[1]
    if is_priv:
        out["worktrees"] = {"removable": sum(1 for r in rows if r["state"] == "removable"),
                            "ask": sum(1 for r in rows if r["state"] == "ask")}
    else:
        out["worktrees"] = rows
    out["claims"] = claims
    out["claims_status"] = "unknown" if livework.has_ledger(path) and claims is None else "ok"
    out["dirty"] = _dirty(path)
    kind = portfolio._data_kind(path)
    out["stray_data"] = kind == "both"
    return out, wt[2]


# ---------------------------------------------------------------- the machine: pointers and processes

def _pointers(sessions_status, live_ids, owners, now):
    """Pointers of sessions that are not alive. Needs a readable live list: otherwise `unknown`, never "all ended"."""
    out = {"status": "ok", "ended": [], "ended_protected": 0, "unverified": 0}
    if sessions_status != "ok":
        out["status"] = "unknown"
        return out
    folder = os.path.join(sl.home_state(), "sessions")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except FileNotFoundError:
        return out
    except OSError:
        out["status"] = "unknown"
        return out
    for name in names:
        sid = name[:-5]
        if not sl.valid_session(sid) or sid in live_ids:
            continue
        rec = sl.read_pointer(sid)
        if not isinstance(rec, dict) or not isinstance(rec.get("project"), str):
            continue
        if not rec.get("ended"):
            out["unverified"] += 1                          # no live chat, but it never said it ended: not archivable
            continue
        project = livework.canon(rec["project"])
        if livework.is_private(project):
            out["ended_protected"] += 1
            continue
        archivable = False
        try:
            items = sl.load_ledger(project)["items"]
            archivable = not any(i.get("status") == "doing" and i.get("doing_by") == sid for i in items)
        except Exception:
            pass
        out["ended"].append({"session": sid, "project": owners.get(_real(project)) or os.path.basename(project.rstrip(os.sep)),
                             "archivable": archivable})
    return out


def _etime_s(text):
    """ps etime `[[dd-]hh:]mm:ss` in seconds, or None."""
    m = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text.strip())
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _ps():
    """[(pid, age_s, command)] for every process, or None."""
    got = _run(["ps", "-axo", "pid=,etime=,command="])
    if not got or got[0] != 0:
        return None
    rows = []
    for line in got[1].splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit():
            age = _etime_s(parts[1])
            if age is not None:
                rows.append((int(parts[0]), age, parts[2]))
    return rows


def _lsof_map(args, pids):
    """{pid: [value, ...]} from an lsof -F run limited to `pids`; {} when lsof is unavailable."""
    if not pids:
        return {}
    got = _run(["lsof", "-nP", *args, "-F", "pn", "-p", ",".join(str(p) for p in pids)])
    out, cur = {}, None
    if not got:
        return out
    for line in got[1].splitlines():
        if line.startswith("p") and line[1:].isdigit():
            cur = int(line[1:])
        elif line.startswith("n") and cur is not None:
            out.setdefault(cur, []).append(line[1:])
    return out


def _cwds(pids):
    return {p: v[0] for p, v in _lsof_map(["-a", "-d", "cwd"], pids).items() if v}


def _ports(pids):
    ports = {}
    for p, names in _lsof_map(["-a", "-iTCP", "-sTCP:LISTEN"], pids).items():
        found = sorted({int(m.group(1)) for n in names for m in [re.search(r":(\d+)$", n)] if m})
        ports[p] = found
    return ports


def _kind(command):
    for kind, rx in _KINDS:
        if rx.search(command):
            return kind
    return None


def processes(places, live_sessions):
    """Stray dev servers, test runs and headless browsers living in a registered project or one of its worktrees.
    `places` is [(base folder, project name, protected)]. Rows hold the kind, pid, age, ports, owner and session."""
    out = {"status": "ok", "items": [], "protected": 0}
    allps = _ps()
    if allps is None:
        out["status"] = "unknown"
        return out
    mine = {os.getpid(), os.getppid()}
    cand = [(pid, age, _kind(cmd)) for pid, age, cmd in allps if pid not in mine]
    cand = [c for c in cand if c[2]]
    if not cand:
        return out
    cwds = _cwds([c[0] for c in cand])
    ports = _ports([c[0] for c in cand])
    for pid, age, kind in cand:
        cwd = cwds.get(pid)
        if not cwd:
            continue                                    # no folder known: not listed, never guessed
        owner = next((p for p in places if _inside(cwd, p[0])), None)
        if owner is None:
            continue
        if owner[2]:
            out["protected"] += 1
            continue
        session = next((s["session_id"] for s in live_sessions if s.get("cwd") and _inside(s["cwd"], owner[0])), None)
        out["items"].append({"pid": pid, "kind": kind, "age_s": age, "ports": ports.get(pid, []),
                             "project": owner[1], "session": session})
    out["items"].sort(key=lambda r: (r["project"], r["pid"]))
    return out


# ---------------------------------------------------------------- the whole sweep

def collect(project=None, all_projects=False, now=None):
    """The sweep as data. `project` (a folder) when not `all_projects`; with neither, only the machine sections."""
    global _deadline
    _deadline = time.monotonic() + BUDGET_S
    try:
        return _collect(project, all_projects, now)
    finally:
        _deadline = None


def _collect(project, all_projects, now):
    now = now if now is not None else time.time()
    found = livework.live_sessions()
    live = found["sessions"]
    reg = portfolio._found(live)
    data = portfolio.load() or portfolio.seed(reg)          # read-only: a cold home is seeded in memory, never saved
    entries = list(data["projects"])
    private = {e["path"] for e in entries if livework.is_private(e["path"])}
    ids = portfolio._ids(entries, private)
    if not all_projects:
        real = livework.canon(project) if project else None      # a worktree is its repo's project, never its own
        entries = [e for e in entries if _real(e["path"]) == real] or ([{"path": real}] if real else [])
        if entries and entries[0]["path"] not in ids:
            if livework.is_private(real):
                private.add(real)
                ids[real] = portfolio.PRIVATE
            else:
                ids[real] = os.path.basename(real)
    live_cwds = [s["cwd"] for s in live if isinstance(s.get("cwd"), str)]
    projects, places, owners = [], [], {}
    for e in entries:
        path = e["path"]
        is_priv = path in private
        name = portfolio.display_name(path, e, is_priv)
        row, extra = _project(e, ids.get(path, name), name, is_priv, live_cwds, now)
        if is_priv and name == portfolio.PRIVATE:
            row["name"] = name
        if not is_priv:
            owners[_real(path)] = name
        projects.append(row)
        for base in [path] + extra:
            places.append((base, name, is_priv))
    out = {"version": VERSION, "scope": "all" if all_projects else "project", "no_project": not all_projects and not projects,
           "projects": projects,
           "pointers": _pointers(found["status"], {s["session_id"] for s in live}, owners, now),
           "processes": processes(places, live)}
    out["archivable"] = [p["session"] for p in out["pointers"]["ended"] if p["archivable"]]
    return out


def render(out, lang=None):
    import i18n
    t = lambda key, **kw: i18n.t(key, lang, **kw)
    lines = [t("sweep.title")]
    if out.get("no_project"):
        lines.append(t("sweep.no_project"))
    for p in out["projects"]:
        name = p["name"] if not (p["protected"] and p["name"] == portfolio.PRIVATE) else t("portfolio.protected")
        if p["status"] == "missing":
            lines.append("%s: %s" % (name, t("sweep.missing")))
            continue
        wt = p.get("worktrees")
        if isinstance(wt, dict):
            removable, ask = wt["removable"], wt["ask"]
        else:
            removable = sum(1 for r in wt or [] if r["state"] == "removable")
            ask = sum(1 for r in wt or [] if r["state"] == "ask")
        claims = p.get("claims")
        stuck = claims["stuck"] if isinstance(claims, dict) else len(claims or [])
        stuck = t("sweep.stuck_unknown") if p.get("claims_status") == "unknown" else t("sweep.stuck", n=stuck)
        dirty = "-" if p.get("dirty") is None else p["dirty"]
        lines.append("%s: %s · %s · %s · %s%s" % (name, stuck, t("sweep.removable", n=removable),
                                                   t("sweep.ask", n=ask), t("sweep.dirty", n=dirty),
                                                   " · " + t("sweep.stray_data") if p.get("stray_data") else ""))
        if p.get("worktrees_status") == "unknown":
            lines.append("  " + t("sweep.worktrees_unknown"))
        if not p["protected"]:
            for c in claims or []:
                lines.append("  " + t("sweep.claim", id=c["id"], days=c["age_days"]))
            for r in wt or []:
                lines.append("  " + t("sweep.worktree." + r["state"], name=r["name"], reason=t("sweep.reason." + r["reason"]),
                                       days="-" if r["age_days"] is None else r["age_days"]))
    ptr = out["pointers"]
    if ptr["status"] == "unknown":
        lines.append(t("sweep.pointers_unknown"))
    else:
        lines.append(t("sweep.pointers", ended=len(ptr["ended"]) + ptr["ended_protected"], arch=len(out["archivable"]),
                       unverified=ptr["unverified"]))
        for p in ptr["ended"]:
            lines.append("  %s · %s%s" % (p["session"][:8], p["project"], " · " + t("sweep.archivable") if p["archivable"] else ""))
    pr = out["processes"]
    if pr["status"] == "unknown":
        lines.append(t("sweep.processes_unknown"))
    else:
        lines.append(t("sweep.processes", n=len(pr["items"]) + pr["protected"]))
        for r in pr["items"]:
            lines.append("  %s %d · %s · %s%s" % (r["kind"], r["pid"], r["project"], t("sweep.age_min", n=r["age_s"] // 60),
                                                  (" · :" + ",".join(str(x) for x in r["ports"])) if r["ports"] else ""))
    return "\n".join(lines) + "\n"
