"""R-399: Budgit carries the team organization. Role detection (TM / PM / lane), the SessionStart rules block, and
`budgit reorganize`. One source of truth: the portfolio's `tm` and `pm` fields (written when a session binds, or by
`reorganize`, or by hand with `budgit portfolio tm|pm`). roles.json is only the snapshot that `--undo` restores."""
import json
import os
import re
import sys

import studiolib as sl

MAX_BLOCK_LINES = 18    # R-849: was 60; the block is a pointer, the rules file holds the rest
LINE_CAP = 110
MAP_NAME = "roles.json"
MARKER_NAME = "roles-applied"
RULES_MARKER = re.compile(r"^#{1,6}\s*team rules\b", re.I | re.M)
_BAD = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029`#<>\"]")


def safe_name(text, limit=40):
    """An untrusted folder or project name as one quoted token: no control characters, newlines, backticks, '#', '<', '>'."""
    s = _BAD.sub(" ", str(text or ""))
    s = " ".join(s.split())[:limit].strip()
    return '"%s"' % (s or "?")


def is_budgit_project(path):
    """A folder with Budgit project state: a data folder (.budgit, or the legacy .studio) holding a ledger. A bare .git is not."""
    try:
        return os.path.isfile(sl._ledger_path(path))
    except (OSError, sl.StudioError):
        return False


def classify(session_id, tm, entries, project, parent=None):
    """Pure. tm: the TM session id or None. entries: portfolio rows [{path, name, pm}]. project: the bound project path or None.
    parent: the parent session id when the pointer knows one. -> {"role": "TM"|"PM"|"lane", "name", "path"} or None."""
    if not session_id:
        return None
    # R-932: the role comes from the session's own registration (tm / pm ids); a parent id only tells an UNREGISTERED session apart.
    if tm and tm == session_id:
        return {"role": "TM", "name": "hub", "path": None}
    for e in entries:
        if e.get("pm") == session_id:
            return {"role": "PM", "name": e.get("name") or os.path.basename(e["path"]), "path": e["path"]}
    if not project:
        return None
    row = next((e for e in entries if os.path.realpath(e["path"]) == os.path.realpath(project)), None)
    name = (row or {}).get("name") or os.path.basename(os.path.realpath(project))
    if parent and parent != session_id and parent == tm and not (row or {}).get("pm"):
        return {"role": "PM", "name": name, "path": project}      # R-1137: the TM started it, and the project has no PM yet
    if (parent and parent != session_id) or (row and row.get("pm") and row["pm"] != session_id):
        return {"role": "lane", "name": name, "path": project}
    return {"role": "PM", "name": name, "path": project}


def _entries():
    try:
        import portfolio
        data = portfolio.load() or {}
    except Exception:
        return None, []
    return data.get("tm"), data.get("projects") or []


def _within(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def _hub():
    hub = sl.load_config().get("hub")
    return os.path.realpath(os.path.expanduser(hub)) if hub else None


def _parent_of(session_id):
    try:
        parent = (sl.read_pointer(session_id) or {}).get("parent")
    except sl.StudioError:
        return None
    return parent if isinstance(parent, str) and parent and parent != session_id else None


def protected_side_session(session_id, project):
    """R-932: a one-line warning when `project` is in a configured protected folder and the session is a side session (its
    pointer names a parent) of the TM or of the PM of an unprotected project: they would share that coordinator's notes
    folder. The list is the config's `protected_dirs`; no name is built in. -> text or None."""
    parent = _parent_of(session_id)
    if not parent or not project or not sl.protected_dir(project):
        return None
    tm, entries = _entries()
    if parent == tm or any(e.get("pm") == parent and not sl.protected_dir(e["path"]) for e in entries):
        return ("budgit WARNING: this session is in a protected project but was started from a Budgit coordinator session "
                "(%s); it would share that coordinator's notes folder. It was NOT registered as the project's PM. Start the "
                "protected project's PM as its own top-level session." % parent[:8])
    return None


def _liveness_known():
    """R-1343: whether this machine can tell a live session from an ended one. livework trusts a session file only with
    pidDomain darwin and the kernel's start time (livework._verified), so elsewhere every PM would read as ended."""
    return sys.platform == "darwin"


def manual_line(done):
    """The SessionStart notice when a takeover was withheld because liveness is unknown here (assign's result list), or None."""
    old = next((d[len("pm-manual:"):] for d in done if d.startswith("pm-manual:")), None)
    if not old:
        return None
    import i18n
    return i18n.t("roles.pm_manual", old=old[:8])


def _pm_ended(old):
    """R-1343 F2: True only when no listed record names the recorded PM session `old`. live_sessions lists a record only when its
    pid is alive; verified or not does not matter here, so a reused pid blocks the takeover (the safe direction), and a
    running PM with no pidDomain or an off procStart is never read as ended. With no readable sessions folder nothing is known."""
    try:
        import livework
        found = livework.live_sessions(verify=False)
        if found.get("status") != "ok":
            return False
        return not any(s["session_id"] == old for s in found["sessions"])
    except Exception:
        sl.log_error("roles: pm liveness")
        return False


def _is_main_folder(cwd, row, project):
    """R-1343 F1: the session's folder AND its bound project are literally the recorded project folder (real paths). Never
    livework.canon(): it maps a worktree, or any folder holding a pointer file that names the repo, to the main repo."""
    if not (isinstance(cwd, str) and os.path.isabs(cwd) and isinstance(project, str) and project and row and row.get("path")):
        return False
    here = os.path.realpath(row["path"])
    return os.path.realpath(cwd) == here and os.path.realpath(project) == here


def takeover_line(done):
    """The SessionStart notice for a takeover that assign() recorded (its result list), or None."""
    old = next((d[len("pm-from:"):] for d in done if d.startswith("pm-from:")), None)
    if not old:
        return None
    import i18n
    return i18n.t("roles.pm_takeover", old=old[:8])


def assign(session_id, project, cwd=None, lock_timeout=1.0, interactive=True):
    """Binding time (SessionStart): a project with no recorded pm records this session as its pm; a session started in the hub
    folder records itself as tm when none is recorded. Never overwrites a recorded tm. A recorded pm is replaced only by
    a takeover (R-1343): the recorded pm is not live, this session is interactive, top level (no parent pointer) and sits in the
    project's own folder; the old id stays in the row as previous_pm, and the first session under the lock wins. A session whose pointer names a
    parent (a lane or nested session) is never recorded. The read-modify-write holds the portfolio lock; if it stays taken for
    `lock_timeout` seconds nothing is recorded (the hook never waits). -> list of what was written."""
    if not sl.valid_session(session_id):
        return []
    parent = _parent_of(session_id)
    child = bool(parent)
    import portfolio
    import livework
    hub = _hub()
    wants_tm = bool(hub and not child and isinstance(cwd, str) and os.path.isabs(cwd) and _within(cwd, hub))   # R-932: a detached TM is still the TM; R-939: a session with a parent never takes TM by itself
    wants_pm = bool(project and is_budgit_project(project))          # R-1137: a child of the TM may take a PM-less project (checked under the lock)
    if wants_pm and protected_side_session(session_id, project):
        return []                                                # R-932: never shares a Budgit coordinator's notes folder
    if not (wants_tm or wants_pm):
        return []
    with sl.home_try_lock(portfolio.LOCK_NAME, lock_timeout) as got:
        if not got:
            return []
        data = portfolio.registry()
        done, hint = [], []
        if wants_tm and not data.get("tm"):
            data["tm"] = session_id
            done.append("tm")
        if child and data.get("tm") != parent:
            wants_pm = False                                   # R-1137: only the TM's own child can be a PM; a PM's child is a lane
        if wants_pm and data.get("tm") != session_id:
            canon = livework.canon(os.path.realpath(project))
            row = next((e for e in data["projects"] if e["path"] == canon), None)
            if row is None:
                row = portfolio._entry(canon)
                data["projects"].append(row)
            if not row.get("pm"):
                row["pm"] = session_id
                done.append("pm")
            elif row["pm"] != session_id and interactive and not child and _is_main_folder(cwd, row, project):
                if not _liveness_known():                      # R-1343: no way to tell a live PM from an ended one here: never take over, say how
                    hint = ["pm-manual:" + row["pm"]]
                elif _pm_ended(row["pm"]):                     # the PM chat was renewed or resumed under a new id; the old one is gone
                    old = row["pm"]
                    row["previous_pm"], row["previous_pm_at"] = old, sl.now()
                    row["pm"] = session_id
                    done.extend(["pm", "pm-from:" + old])
        if done:
            portfolio.save(data)
        return done + hint


def take_over_now(inp, project):
    """R-1343, UserPromptSubmit: a chat that began before the takeover existed (or whose start was skipped) gets the same
    chance on its prompt. Cheap for a true lane: a read-only precheck first, the portfolio lock only when it may win.
    -> the takeover notice, or None."""
    sid = inp.get("session_id")
    if not sl.valid_session(sid) or not project or not sl.interactive_session(inp) or _parent_of(sid):
        return None
    tm, entries = _entries()
    row = next((e for e in entries if os.path.realpath(e["path"]) == os.path.realpath(project)), None)
    if not row or not row.get("pm") or row["pm"] == sid or tm == sid:
        return None
    if not _liveness_known() or not _is_main_folder(inp.get("cwd"), row, project) or not _pm_ended(row["pm"]):
        return None                                  # unknown liveness: the hint is printed at SessionStart only, never on every prompt
    return takeover_line(assign(sid, project, inp.get("cwd")))


def detect(session_id, project):
    tm, entries = _entries()
    parent = None
    try:
        parent = (sl.read_pointer(session_id) or {}).get("parent")
    except sl.StudioError:
        pass
    return classify(session_id, tm, entries, project, parent if isinstance(parent, str) else None)


def rules_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference", "team-rules.md")


def _sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur and line.startswith("- "):
            out[cur].append(line)
    return out


HARD_SAFETY = "Hard safety"        # the rules-file section whose lines every role card keeps in full (R-1137)


def block_parts(info, rules_text=None):
    """R-1137: the role card as two pieces -> (fixed, rest) lists of lines, or None. fixed: the role line, the hard-safety
    lines and the rules-file pointer (never shortened). rest: the role's own rules, then the common ones, each at most
    LINE_CAP characters, so that fixed + rest is at most MAX_BLOCK_LINES lines (the full text sits behind the pointer)."""
    if not info:
        return None
    if rules_text is None:
        try:
            rules_text = sl.read_text(rules_path())
        except OSError:
            rules_text = ""
    secs = _sections(rules_text)
    key = {"TM": "TM", "PM": "PM", "lane": "Lane"}[info["role"]]
    head = ["budgit team role: %s" % info["role"]]
    if info["role"] == "TM":
        head.append("You coordinate the hub and all projects.")
    else:
        head.append("%s of project %s." % ("PM" if info["role"] == "PM" else "Lane (technician)", safe_name(info["name"])))
    cap = lambda l: l if len(l) <= LINE_CAP else l[:LINE_CAP - 1].rstrip() + "…"
    fixed = head + [cap(l) for l in secs.get(HARD_SAFETY, [])] + ["full rules: %s" % rules_path()]
    own = secs.get(key, [])
    body = own + secs.get("Common", [])
    room = max(MAX_BLOCK_LINES - len(fixed), 0)
    body = [cap(l) for l in body[:room]]                          # R-849: full text sits behind the pointer
    n = min(len(own), len(body))
    return fixed, body[:n], body[n:]                              # the role's own lines outrank the common ones


def block(info, rules_text=None):
    """The SessionStart text for one role: at most MAX_BLOCK_LINES lines."""
    parts = block_parts(info, rules_text)
    if not parts:
        return None
    fixed, own, common = parts
    return "\n".join(fixed[:-1] + own + common + fixed[-1:])


def claude_md_has_rules():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        return bool(RULES_MARKER.search(sl.read_text(os.path.join(base, "CLAUDE.md"))))
    except (OSError, ValueError):
        return False


def session_start_parts(inp, project):
    """R-1137: -> (role or None, [(tag, text)]) for startbudget. Records the first session of a project as its PM (or a session
    the TM started, when the project has no PM yet) and the first hub session as the TM (never overwriting). Never edits
    CLAUDE.md. Tags: "role" (the fixed card and a protected-folder warning), "role.rules", "notice"."""
    done = []
    try:
        done = assign(inp.get("session_id"), project, inp.get("cwd"), interactive=sl.interactive_session(inp))
    except (sl.StudioError, OSError):
        sl.log_error("roles: assign")
    info = detect(inp.get("session_id"), project)
    warn = protected_side_session(inp.get("session_id"), project)
    parts = block_parts(info)
    out = []
    if parts:
        out.append(("role", "\n".join(parts[0][:-1])))
        if parts[1]:
            out.append(("role.rules", "\n".join(parts[1])))
        if parts[2]:
            out.append(("role.common", "\n".join(parts[2])))
        out.append(("role", parts[0][-1]))
    if warn:
        out.append(("role", warn))
    took = takeover_line(done)
    if took:
        out.append(("notice", took))
    manual = manual_line(done)
    if manual:
        out.append(("notice", manual))
    if parts and claude_md_has_rules():
        out.append(("notice", "budgit: both rule sources are active (Budgit team rules and your CLAUDE.md team rules); CLAUDE.md wins on conflict."))
    return (info or {}).get("role"), out


def session_start_texts(inp, project):
    """-> list of context texts (empty when the role is unknown); the same text as session_start_parts, with the role card whole."""
    role, parts = session_start_parts(inp, project)
    if not role:
        return [t for _, t in parts]
    card = "\n".join(t for tag, t in parts if tag in ("role", "role.rules", "role.common") and not t.startswith("budgit WARNING"))
    return [card] + [t for tag, t in parts if t.startswith("budgit WARNING") or tag == "notice"]


# ---------------------------------------------------------------- budgit reorganize

def _map_path():
    return os.path.join(sl.home_state(), MAP_NAME)


def _pointers():
    """The binding records: [(ts, session id, real project path)] oldest first."""
    folder = os.path.join(sl.home_state(), "sessions")
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            rec = json.loads(sl.read_text(os.path.join(folder, n)))
        except (OSError, ValueError):
            continue
        sid = n[:-5]
        if isinstance(rec, dict) and isinstance(rec.get("project"), str) and sl.valid_session(sid):
            out.append((str(rec.get("ts") or ""), sid, os.path.realpath(rec["project"])))
    return sorted(out)


def _plan(data):
    """Changes `data` (a portfolio dict) in place from the binding records. -> (before, stats). Only Budgit projects count."""
    import livework
    import portfolio
    before = {"tm": data.get("tm"), "pm": {e["path"]: e.get("pm") for e in data["projects"]}, "added": []}
    pointers = _pointers()
    known = {e["path"] for e in data["projects"]}
    root = sl.load_config().get("projects_root")
    if root:
        root = os.path.realpath(os.path.expanduser(root))
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []
        for n in names:
            p = os.path.join(root, n)
            if n.startswith(".") or os.path.islink(p) or not os.path.isdir(p) or not is_budgit_project(p):
                continue
            c = livework.canon(p)
            if c not in known:
                known.add(c)
                data["projects"].append(portfolio._entry(c))
                before["added"].append(c)
    stats = {"tm": "set" if data.get("tm") else "none", "projects": 0, "with_pm": 0, "set_now": 0, "waiting": 0}
    hub = _hub()
    if not data.get("tm") and hub:
        first = next((sid for _, sid, proj in pointers if _within(proj, hub) and not _parent_of(sid)), None)   # R-939: a session with a parent never takes TM by itself
        if first:
            data["tm"] = first
            stats["tm"] = "set now"
    for e in data["projects"]:
        if not is_budgit_project(e["path"]):
            continue
        stats["projects"] += 1
        if not e.get("pm"):
            first = next((sid for _, sid, proj in pointers
                          if proj == os.path.realpath(e["path"]) and sid != data.get("tm") and not _parent_of(sid)), None)   # R-939 r2: a session with a parent never takes PM by itself
            if first:
                e["pm"] = first
                stats["set_now"] += 1
        if e.get("pm"):
            stats["with_pm"] += 1
        else:
            stats["waiting"] += 1
    return before, stats


def _text(stats):
    tm = {"set": "TM already set", "set now": "TM set",
          "none": "no TM yet (the first session started in the hub folder becomes it)"}[stats["tm"]]
    return ("reorganize: %s; %d project(s), %d with a PM (%d set now), %d waiting for its first session"
            % (tm, stats["projects"], stats["with_pm"], stats["set_now"], stats["waiting"]))


def reorganize(dry_run=False):
    import portfolio
    if dry_run:
        return _reorganize(True)
    with sl.home_lock(portfolio.LOCK_NAME):          # waits for a concurrent tm / pm record, like set_tm and set_pm
        return _reorganize(False)


def _reorganize(dry_run):
    import portfolio
    data = portfolio.load() if dry_run else portfolio.registry()
    if data is None:
        data = {"version": portfolio.VERSION, "projects": []}
    before, stats = _plan(data)
    text = _text(stats)
    if dry_run:
        return "(dry run, nothing written) " + text
    os.makedirs(sl.home_state(), exist_ok=True)
    sl.atomic_write(_map_path() + ".prev", json.dumps(before, indent=2, ensure_ascii=False) + "\n")
    portfolio.save(data)
    snap = {"version": 2, "tm": data.get("tm"), "projects": [{"path": e["path"], "pm": e.get("pm")} for e in data["projects"]]}
    sl.atomic_write(_map_path(), json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    return text + ". Written: the portfolio's tm and pm fields only."


def undo():
    import portfolio
    prev = _map_path() + ".prev"
    try:
        before = json.loads(sl.read_text(prev))
    except (OSError, ValueError):
        raise sl.StudioError("nothing to undo: no previous reorganize")
    with sl.home_lock(portfolio.LOCK_NAME):
        data = portfolio.registry()
        data["tm"] = before.get("tm")
        for e in data["projects"]:
            if e["path"] in before["pm"]:
                e["pm"] = before["pm"][e["path"]]
            elif e["path"] in before.get("added", []):
                e["pm"] = None
        portfolio.save(data)
        os.remove(prev)
    return "reorganize undone: the portfolio's tm and pm fields are back as before."


def apply_once(version=None):
    """The update path: reorganize once per version. -> the summary, or None when already applied or not set up yet."""
    version = version or sl._running_version() or "unknown"
    marker = os.path.join(sl.home_state(), MARKER_NAME)
    if not os.path.exists(os.path.join(sl.home_state(), "config.json")):
        return None                                  # not set up: retry on a later start, no marker
    try:
        if sl.read_text(marker).strip() == version:
            return None
    except OSError:
        pass
    text = reorganize()
    sl.atomic_write(marker, version + "\n")
    return text
