"""R-283 S2: the portfolio — one private list of every project (`<budgit home>/portfolio.json`, never in a repo) and one
read-only view of all of them (`budgit portfolio`, which is R-221's `status --all`). A protected project (livework's
`is_private`: a `protected_dirs` folder of the config, or `"private": true` in its project.json) shows counts,
versions and ids only: no task text, no path, no file, branch or chat name, in the text and in `--json`."""
import json
import os
import subprocess

import livework
import studiolib as sl

VERSION = 1
NO_CAP = 10 ** 6
GIT_TIMEOUT_S = 5
PRIVATE = "private"
LOCK_NAME = "portfolio"          # the Budgit home lock for read-modify-write of portfolio.json (tm / pm records)
CHAT_STATE = {"busy": "working", "waiting": "waiting", "idle": "resting"}


def list_path():
    return os.path.join(sl.home_state(), "portfolio.json")


def load():
    try:
        with open(list_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return None
    data["projects"] = [e for e in data["projects"] if isinstance(e, dict) and isinstance(e.get("path"), str)]
    return data


def save(data):
    os.makedirs(sl.home_state(), exist_ok=True)
    sl.atomic_write(list_path(), json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _entry(path):
    return {"path": path, "name": None, "pm": None, "channel": "current", "reason": None}


def _root_folders():
    root = sl.load_config().get("projects_root")
    if not root:
        return []
    root = os.path.realpath(os.path.expanduser(root))
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [os.path.join(root, n) for n in names if not n.startswith(".") and os.path.isdir(os.path.join(root, n))]


def _found(sessions=None):
    """Every project the registry finds now (no cap), with its live-chat attribution."""
    return livework.all_projects(sessions, cap=NO_CAP)


def seed(reg=None):
    """First run: every project the registry finds, plus the projects_root folders that have no ledger yet."""
    reg = reg or _found()
    paths = [r["path"] for r in reg["projects"]]
    for folder in _root_folders():
        c = livework.canon(folder)
        if c not in paths:
            paths.append(c)
    return {"version": VERSION, "projects": [_entry(p) for p in paths]}


def registry(reg=None):
    data = load()
    if data is None:
        data = seed(reg)
        save(data)
    return data


def _project_name(path):
    try:
        name = sl.load_project_config(path).get("name") if livework.has_ledger(path) else None
    except Exception:
        name = None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _printable(text):
    """A lone surrogate (a `\\ud800` escape in a name, a file name that is not UTF-8) cannot be printed or written: it shows as `?`."""
    return text.encode("utf-8", "replace").decode("utf-8")


def display_name(path, entry=None, private=None):
    """The project's real name: project.json `name`, then the list's `name`, then the folder. A protected project
    shows only the name the user gave it in the (private) list, else "private", as in the live feed. Always printable."""
    listed = (entry or {}).get("name")
    listed = listed.strip() if isinstance(listed, str) and listed.strip() else None
    if livework.is_private(path) if private is None else private:
        return _printable(listed or PRIVATE)
    return _printable(_project_name(path) or listed or os.path.basename(path.rstrip(os.sep)) or path)


def entries_by_path():
    """The saved list as {path: entry}; never seeds or writes (the status line and the feed read it on every call)."""
    data = load()
    return {e["path"]: e for e in data["projects"]} if data else {}


def own_name(path, entries=None):
    """The project's name inside its own chats (status line): a protected project shows its own name there."""
    path = livework.canon(path)
    return display_name(path, (entries if entries is not None else entries_by_path()).get(path), private=False)


def project_id(path):
    """The id a project has in the portfolio view and in the card queue (R-288): `private~hash` for a protected
    project, the folder name otherwise (`~hash` on a clash with an earlier entry of the list)."""
    path = livework.canon(os.path.expanduser(path))
    entries = list((load() or {}).get("projects") or [])
    if path not in {e["path"] for e in entries}:
        entries.append({"path": path})
    return _ids(entries, {e["path"] for e in entries if livework.is_private(e["path"])})[path]


def _ids(entries, private):
    """Folder name, `~hash` on a clash; a protected project is `private~hash`, its folder name unsaid."""
    out, taken = {}, set()
    for e in entries:
        base = PRIVATE if e["path"] in private else os.path.basename(e["path"].rstrip(os.sep)) or e["path"]
        pid = base if base not in taken and e["path"] not in private else "%s~%s" % (base, livework._short_id(e["path"]))
        taken.add(pid)
        out[e["path"]] = pid
    return out


def find(data, key):
    """The entry named by id, real name, folder name or path (what the user types; never echoed for a protected
    project); StudioError when none matches."""
    ids = _ids(data["projects"], {e["path"] for e in data["projects"] if livework.is_private(e["path"])})
    real = os.path.realpath(os.path.expanduser(key)) if (os.sep in key or key.startswith("~")) else None
    for e in data["projects"]:
        if key in (ids[e["path"]], e["path"], os.path.basename(e["path"].rstrip(os.sep))) or (real and livework.canon(real) == e["path"]) \
                or key.casefold() == display_name(e["path"], e).casefold():
            return e
    raise sl.StudioError("no project %r in the portfolio list (`budgit portfolio --by-name` lists them)" % key)


def _data_kind(path):
    new = os.path.isdir(os.path.join(path, sl.DATA_NAME))
    old = os.path.isdir(os.path.join(path, sl.OLD_DATA_NAME))
    return "both" if new and old else sl.DATA_NAME if new else sl.OLD_DATA_NAME if old else "none"


def _dirty(path):
    """Uncommitted paths (untracked folders count once), the data folders never; None outside git."""
    if not os.path.exists(os.path.join(path, ".git")):
        return None
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-C", path, "status", "--porcelain"], capture_output=True, text=True,
                             timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    skip = (sl.DATA_NAME + "/", sl.OLD_DATA_NAME + "/")
    return sum(1 for line in out.stdout.splitlines() if line[3:].strip('"') and not line[3:].strip('"').startswith(skip))


def _tasks(path, protected=False):
    if not livework.has_ledger(path):
        return None, 0
    if protected:                                        # R-283 S3: the card holds counts only; the ledger is not opened
        import card
        view = card.counts_view(path)
        stale = (card.read(path) or {}).get("stuck")
        stale = stale if isinstance(stale, int) and not isinstance(stale, bool) and stale > 0 else 0
        return (view or {"doing": 0, "next": 0, "later": 0, "done": 0}), stale
    try:
        items = sl.load_ledger(path)["items"]
    except Exception:
        return None, 0
    later = sum(1 for i in items if i["status"] == "open" and i.get("phase") == "later")
    tasks = {"doing": sum(1 for i in items if i["status"] == "doing"),
             "next": sum(1 for i in items if i["status"] == "open") - later, "later": later,
             "done": sum(1 for i in items if i["status"] in ("done", "verified"))}
    return tasks, len(sl.stale_doing({"items": items}))


def view(by_name=False):
    """The portfolio as data: {'version', 'rows', 'footer'}. Seeds the list on first run."""
    found = livework.live_sessions()
    sessions = [s for s in found["sessions"] if s.get("verified")]
    reg = _found(found["sessions"])
    data = registry(reg)
    hub = None
    try:
        hub = sl.hub_path()
    except sl.StudioError:
        pass
    private = {e["path"] for e in data["projects"] if livework.is_private(e["path"])}
    ids = _ids(data["projects"], private)
    chats = {}
    no_project = 0
    by_sid = {s["session_id"]: s for s in sessions}
    for s in sessions:
        proj = reg["attrib"].get(s["session_id"])
        state = CHAT_STATE.get(s["status"])
        if not proj:
            no_project += 1
            continue
        if state:
            row = chats.setdefault(proj, {"working": 0, "waiting": 0, "resting": 0})
            row[state] += 1
    rows = []
    for e in data["projects"]:
        path = e["path"]
        is_priv = path in private
        tasks, stale = _tasks(path, is_priv)
        pm = None
        if e.get("pm"):
            live = by_sid.get(e["pm"])
            pm = {"session": e["pm"][:8], "title": None if is_priv or not live else live.get("name") or None,
                  "live": bool(live)}
        row = {"id": ids[path], "name": display_name(path, e, is_priv), "hub": bool(hub and path == hub),
               "managed": tasks is not None, "protected": is_priv, "channel": e.get("channel") or "current",
               "reason": e.get("reason"), "pm": pm, "data": _data_kind(path), "tasks": tasks, "stale": stale,
               "chats": chats.get(path, {"working": 0, "waiting": 0, "resting": 0}), "dirty": _dirty(path)}
        if tasks is not None:
            import card
            kind, age = card.state(path)
            row["card"] = {"state": kind, "age_days": age}
        if not is_priv:
            row["path"] = path
        rows.append(row)
    unnamed = [r for r in rows if r["protected"] and r["name"] == PRIVATE]
    if len(unnamed) > 1:                                 # list order, so the numbers stay put from run to run
        for n, r in enumerate(unnamed, 1):
            r["private_n"] = n
    if by_name:
        rows.sort(key=lambda r: r["name"].casefold())
    else:
        rows.sort(key=lambda r: (-r["chats"]["waiting"], -(r["stale"] or 0), not r["hub"], -r["chats"]["working"],
                                 not r["managed"], r["name"].casefold()))
    listed = {e["path"] for e in data["projects"]}
    new = [r for r in reg["projects"] if r["path"] not in listed]
    footer = {"total": len(rows), "cut": 0, "unmanaged": sum(1 for r in rows if not r["managed"]),
              "no_project_chats": no_project,
              "found": [r["name"] for r in new if not livework.is_private(r["path"])],
              "found_protected": sum(1 for r in new if livework.is_private(r["path"])),
              "tm_unrouted": sum(1 for r in load_tm() if not r.get("routed"))}
    footer.update(_stale_chats(sessions, reg["attrib"]))
    return {"version": VERSION, "rows": rows, "footer": footer}


def _stale_chats(sessions, attrib):
    """R-301: live chats whose pointer says they run an older Budgit than the installed one. A chat in a private
    folder, or with no title, is counted and never named."""
    import updatelib
    installed = updatelib.installed_version()
    titles, hidden = [], 0
    for s in sessions if installed else ():
        rec = sl.read_pointer(s["session_id"]) or {}
        if not updatelib.older(rec.get("plugin_version"), installed):
            continue
        where = attrib.get(s["session_id"]) or s.get("cwd") or ""
        if s.get("name") and where and not livework.is_private(where):
            titles.append(s["name"])
        else:
            hidden += 1
    return {"stale_chats": len(titles) + hidden, "stale_titles": titles, "stale_hidden": hidden}


# ---------------------------------------------------------------- changes to the list

def add(path, name=None):
    real = livework.canon(os.path.expanduser(path))
    if not os.path.isdir(real):
        raise sl.StudioError("not a folder: %s" % path)
    with sl.home_lock(LOCK_NAME):
        data = registry()
        entry = next((e for e in data["projects"] if e["path"] == real), None)
        if entry is None:
            entry = _entry(real)
            data["projects"].append(entry)
        if name is not None:
            entry["name"] = name.strip() or None
        save(data)
    return entry


def set_pm(key, session_id):
    if not sl.valid_session(session_id):
        raise sl.StudioError("not a session id: %r" % session_id)
    with sl.home_lock(LOCK_NAME):
        data = registry()
        find(data, key)["pm"] = session_id
        save(data)


def set_channel(key, on, reason=None):
    if not on and not (reason or "").strip():
        raise sl.StudioError("`off` needs --reason: a project is never taken out silently")
    with sl.home_lock(LOCK_NAME):
        data = registry()
        entry = find(data, key)
        entry["channel"], entry["reason"] = ("current", None) if on else ("off", reason.strip())
        save(data)


PRIORITIES = ("high", "normal", "low")


def project_priority(path):
    """R-286 (plan fix I5): the project's weight, kept in its portfolio entry; missing or unknown is normal."""
    value = (entries_by_path().get(livework.canon(os.path.expanduser(path))) or {}).get("priority")
    return value if value in PRIORITIES else "normal"


def set_priority(key, value):
    if value not in PRIORITIES:
        raise sl.StudioError("unknown priority %r (use %s)" % (value, ", ".join(PRIORITIES)))
    with sl.home_lock(LOCK_NAME):
        data = registry()
        find(data, key)["priority"] = value
        save(data)


def protect(key):
    """Add the project's folder to the config's protected_dirs: the one list the live feed and this view both read."""
    entry = find(registry(), key)
    path = os.path.join(sl.home_state(), "config.json")
    with sl.home_lock("config", strict=True):                         # a lost write here would drop a protected folder
        try:
            with open(path, encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                raise ValueError
        except OSError:
            config = {}
        except ValueError:
            raise sl.StudioError("%s is not valid JSON; fix it before protecting a project" % path)
        dirs = [d for d in (config.get("protected_dirs") or []) if isinstance(d, str)]
        if entry["path"] not in [os.path.realpath(os.path.expanduser(d)) for d in dirs]:
            dirs.append(entry["path"])
        config["protected_dirs"] = dirs
        os.makedirs(sl.home_state(), exist_ok=True)
        sl.atomic_write(path, json.dumps(config, indent=2) + "\n")


# ---------------------------------------------------------------- the Team Manager's inbox (S10)

def tm_path():
    return os.path.join(sl.home_state(), "tm-inbox.jsonl")


def tm_session():
    return (load() or {}).get("tm")


def set_tm(session_id):
    """Record which chat is the Team Manager: its prompts go to the TM inbox, never into a project's inbox."""
    if not sl.valid_session(session_id):
        raise sl.StudioError("not a session id: %r" % session_id)
    with sl.home_lock(LOCK_NAME):
        data = registry()
        data["tm"] = session_id
        save(data)


def is_tm(session_id):
    return bool(session_id) and tm_session() == session_id


def load_tm():
    try:
        text = sl.read_text(tm_path())
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("id"), str):
            out.append(rec)
    return out


def append_tm(text, session):
    """One TM prompt as T-n; harness text and empty prompts are skipped. Returns the id or None."""
    if not isinstance(text, str) or not text.strip() or sl.is_harness(text):
        return None
    os.makedirs(sl.home_state(), exist_ok=True)
    with sl.home_lock("tm-inbox", strict=True):                       # the next T-n is read and used by one writer at a time
        nums = [int(r["id"][2:]) for r in load_tm() if r["id"].startswith("T-") and r["id"][2:].isdigit()]
        tm_id = "T-%d" % (max(nums) + 1 if nums else 1)
        sl.append_text(tm_path(), json.dumps({"id": tm_id, "ts": sl.now(), "session": session, "text": text,
                                              "routed": None}, ensure_ascii=False) + "\n")
    return tm_id


def route(tm_id, key, item=None):
    """Copy a TM message into one project's inbox (source `tm`, ref T-n) and, with `item`, link it to that item.
    The TM record keeps the project's portfolio id (`private~hash` for a protected one), never its path."""
    os.makedirs(sl.home_state(), exist_ok=True)
    with sl.home_lock("tm-inbox", strict=True):                       # the whole file is rewritten: one router at a time, or a mark is lost
        recs = load_tm()
        rec = next((r for r in recs if r["id"] == tm_id), None)
        if rec is None:
            raise sl.StudioError("%s is not in the TM inbox (`budgit portfolio inbox --all` lists them)" % tm_id)
        if rec.get("routed"):
            raise sl.StudioError("%s was already routed to %s" % (tm_id, rec["routed"]["project"]))
        path = find(registry(), key)["path"]
        if not livework.has_ledger(path):
            raise sl.StudioError("that project has no ledger yet; adopt it first")
        with sl.locked(path):
            data = sl.load_ledger(path)
            if item:
                sl.find_item(data, item)                 # an unknown item changes nothing
            input_id = sl.append_inbox(path, rec["text"], rec.get("session"), "tm", ref=tm_id) or \
                next(e["id"] for e in sl.load_inbox(path) if e.get("ref") == tm_id)
            if item:
                sl.link(data, item, [input_id])
                sl.save_ledger(path, data)
        rec["routed"] = {"project": project_id(path), "input": input_id, "item": item, "at": sl.now()}
        sl.atomic_write(tm_path(), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
    return rec


# ---------------------------------------------------------------- text

NAME_W, PM_W, LINE_W = 21, 16, 80     # the first line fits LINE_W columns


def _cell(n):
    return "-" if n is None else str(n)


def _cut(text, width):
    return text if len(text) <= width else text[:width - 1] + "…"


def _found_line(names, t, key="portfolio.foot.found", hidden=0, **kw):
    """As many names as fit LINE_W, then "N more" (the unnamed ones counted in it); a single name too long for the line
    is cut. `hidden` items are never named: "N private" while every name fits, else part of "N more"."""
    private = [t("portfolio.foot.protected_n", n=hidden)] if hidden else []
    for keep in range(len(names), 0, -1):
        shown = names + private if keep == len(names) else \
            names[:keep] + [t("portfolio.foot.more", n=len(names) - keep + hidden)]
        line = t(key, names=", ".join(shown), **kw)
        if len(line) <= LINE_W:
            return line
    if not names:
        return t(key, names=", ".join(private), **kw)
    room = LINE_W - len(t(key, names="", **kw))
    rest = ", " + t("portfolio.foot.more", n=len(names) - 1 + hidden) if len(names) + hidden > 1 else ""
    return t(key, names=_cut(names[0], max(room - len(rest), 4)) + rest, **kw)


def render(out, lang=None):
    """One line per project (name, PM, private, data, uncommitted, stuck) and its tasks and chats on the line under
    it, so the table fits a normal window; the two legends say which unit each count is."""
    import i18n
    t = lambda key, **kw: i18n.t(key, lang, **kw)
    head = [t("portfolio.col.project"), t("portfolio.col.pm"), t("portfolio.col.private"), t("portfolio.col.data"),
            t("portfolio.col.dirty"), t("portfolio.col.stale")]
    table, under = [head], [None]
    for r in out["rows"]:
        name = r["name"]
        if r["protected"] and name == PRIVATE:
            name = t("portfolio.protected") + (" %d" % r["private_n"] if r.get("private_n") else "")
        pm = t("portfolio.no_pm")
        if r["pm"]:                                          # a readable name, never the session id
            label = r["pm"]["title"] or ("PM · " + name if r["protected"] else t("portfolio.pm_unnamed"))
            pm = _cut(label, PM_W - 2) + ("" if r["pm"]["live"] else " †")
        table.append([_cut(name, NAME_W), pm, t("portfolio.yes") if r["protected"] else t("portfolio.no"),
                      "-" if r["data"] == "none" else r["data"], _cell(r["dirty"]),
                      _cell(r["stale"] if r["managed"] else None)])
        tasks = t("portfolio.unmanaged") if not r["managed"] else \
            "%d · %d · %d" % (r["tasks"]["doing"], r["tasks"]["next"], r["tasks"]["later"])
        c = r["chats"]
        rest = "%s %s    %s %d · %d · %d" % (t("portfolio.tasks"), tasks, t("portfolio.chats"),
                                              c["working"], c["waiting"], c["resting"])
        card = r.get("card")
        if card and card["state"] != "ok":               # no card, or the ledger moved on after it: say so
            rest += "    " + (t("portfolio.card.missing") if card["state"] == "missing" else
                              t("portfolio.card.old", days=card["age_days"]))
        off = ""
        if r["channel"] == "off":                            # the reason is cut so the line keeps 80 columns
            room = LINE_W - 4 - len(rest) - len(" · ") - len(t("portfolio.off", reason=""))
            off = t("portfolio.off", reason=_cut(r["reason"] or "", max(room, 8))) + " · "
        under.append("    " + off + rest)
    widths = [max(len(row[i]) for row in table) for i in range(len(head))]
    lines = [t("portfolio.title", n=out["footer"]["total"]), t("portfolio.head.tasks") + ".",
             t("portfolio.head.chats") + ".", ""]
    for row, sub in zip(table, under):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if sub:
            lines.append(sub)
    f = out["footer"]
    lines.append("")
    if f["unmanaged"]:
        lines.append(t("portfolio.foot.unmanaged", n=f["unmanaged"]))
    if f["no_project_chats"]:
        lines.append(t("portfolio.foot.no_project", n=f["no_project_chats"]))
    if f.get("tm_unrouted"):
        lines.append(t("portfolio.foot.tm_unrouted_" + ("one" if f["tm_unrouted"] == 1 else "many"), n=f["tm_unrouted"]))
    if f["found"] or f["found_protected"]:
        lines.append(_found_line(f["found"], t, hidden=f["found_protected"]))
        lines.append(t("portfolio.foot.found_how"))
    if f.get("stale_chats"):
        lines.append(_found_line(f["stale_titles"], t, "portfolio.foot.stale_" + ("one" if f["stale_chats"] == 1 else
                                 "many"), hidden=f.get("stale_hidden", 0), n=f["stale_chats"]))
        lines.append(t("portfolio.foot.stale_how"))
    return "\n".join(lines).rstrip() + "\n"
