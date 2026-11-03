"""R-332: move every project to <root>/<name> (default ~/Dev), one project at a time, reversibly.

`budgit relocate plan` is a dry run and the default: it lists what would move, the Budgit state it would rewrite and the
holders it cannot rewrite, as counts. `apply --project X` moves one project; `undo --project X` reverses that apply.

Rules the code keeps:
- One atomic rename on the same volume. Never a copy, never a force: a project with a live process, uncommitted work, a
  taken target or another volume is skipped and said so.
- Budgit's own paths are rewritten and recorded in a manifest (home/relocate/): session pointers, hub cards, and the home
  config (`hub`, `projects_root`, `protected_dirs`). ~/.claude is never written; what it needs is printed or put in a script.
- A protected project is an opaque folder: no file inside it is read or listed except git's own worktree bookkeeping
  (paths of linked worktrees), no `git status`, and in any output it is `protected #<hash>`. Its commands go to a 0600
  script file, not to the terminal. Its new path joins `protected_dirs` BEFORE the rename, so it is never unprotected.
- The manifest holds paths and counts, never project contents."""
import hashlib
import json
import os
import re
import subprocess
import unicodedata
import time

import studiolib as sl

FOLDER = "relocate"
HUB_NAME = "budgit"
SMALL_FILES = (".claude/settings.local.json", ".claude/launch.json", "CLAUDE.md", ".budgit/project.json", ".studio/project.json")


class RelocateError(sl.StudioError):
    pass


# ---------------------------------------------------------------- small helpers

def default_root():
    return os.path.join(os.path.expanduser("~"), "Dev")


def folder():
    return os.path.join(sl.home_state(), FOLDER)


def claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def claude_name(path):
    """Claude Code's per-path folder name under ~/.claude/projects: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def same_key(path):
    """One key for one folder, whatever its spelling: Unicode NFC and case-folded (APFS keeps either form and folds case)."""
    return unicodedata.normalize("NFC", path).casefold()


def label_of(path):
    return "#" + hashlib.sha256(same_key(path).encode("utf-8")).hexdigest()[:6]


def _real(path):
    return os.path.realpath(os.path.expanduser(path))


def _within(path, base):
    """Inside `base` or equal to it, whatever the spelling of either (same_key), so a process or a pointer that names the
    other spelling of a folder still counts."""
    a, b = same_key(path), same_key(base).rstrip(os.sep)
    return a == b or a.startswith(b + os.sep)


def _swap(path, old, new):
    if not _within(path, old):
        return path
    n = len([c for c in old.split(os.sep) if c])                # the same number of components, whatever the spelling
    rest = [c for c in path.split(os.sep) if c][n:]
    return os.path.join(new, *rest) if rest else new


def _git(path, *args):
    r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    sl.atomic_write(path, json.dumps(data, indent=2) + "\n")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _config_path():
    return os.path.join(sl.home_state(), "config.json")


def _load_home_config():
    try:
        data = _read_json(_config_path())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- discovery

def _card_paths(config):
    """{slug: (card file, path)} from the hub's project cards (front matter `path:`)."""
    out = {}
    try:
        base = os.path.join(sl.hub_path(config), "projects")
        names = sorted(os.listdir(base))
    except (OSError, sl.StudioError):
        return out
    for name in names:
        if not name.endswith(".md"):
            continue
        try:
            text = sl.read_text(os.path.join(base, name))
        except OSError:
            continue
        m = re.search(r"^path:\s*(.+?)\s*$", text.split("\n---", 2)[0], re.M)
        if m:
            out[name[:-3]] = (os.path.join(base, name), m.group(1))
    return out


def discover(root=None, include=(), exclude=()):
    """Every project the Budgit config knows plus the folders under projects_root that look like projects.
    -> list of dicts {name, src, dst, kind (hub|project), protected}. Loose files and unknown folders are not listed."""
    config = sl.load_config()
    root = _real(root or default_root())
    found = {}

    def add(path, name=None, kind="project", disk=False):
        real = _real(path)
        if not os.path.isdir(real):
            return
        if same_key(real) in found:
            if disk:                                         # the spelling on the disk is the one a process or lsof reports
                cur = found[same_key(real)]
                cur["src"] = real
                if cur["kind"] != "hub":                     # the hub keeps its Budgit name
                    cur["name"] = name or os.path.basename(real)
            return
        found[same_key(real)] = {"src": real, "name": name or os.path.basename(real), "kind": kind}

    hub = None
    try:
        hub = sl.hub_path(config)
    except sl.StudioError:
        pass
    if hub:
        add(hub, HUB_NAME, "hub")
    for _slug, (_card, p) in _card_paths(config).items():
        add(p)
    for p in config.get("protected_dirs") or ():
        add(p)
    pr = config.get("projects_root")
    if pr and os.path.isdir(_real(pr)):
        for child in sorted(os.listdir(_real(pr))):
            full = os.path.join(_real(pr), child)
            if os.path.isdir(full) and (os.path.exists(os.path.join(full, ".git")) or any(
                    os.path.isdir(os.path.join(full, d)) for d in sl.DATA_NAMES)):
                add(full, disk=True)
    for name in include:
        if pr:
            add(os.path.join(_real(pr), name))
    out = []
    for item in found.values():
        item["protected"] = bool(sl.protected_dir(item["src"], config))
        item["dst"] = os.path.join(root, item["name"])
        if exclude and item["name"] in exclude:
            continue
        out.append(item)
    out.sort(key=lambda i: (i["kind"] != "project", i["name"]))     # the hub last
    return out


def _left(root=None, include=(), private=()):
    """The folders of projects_root that are not clearly projects, sorted from metadata (sortdirs), and how many loose files
    sit there: ([{label, kind, ... , name (None for a private folder)}], loose count)."""
    import sortdirs
    config = sl.load_config()
    pr = config.get("projects_root")
    known = {same_key(i["src"]) for i in discover(root, include)}
    out, loose = [], 0
    try:
        children = sorted(os.listdir(_real(pr))) if pr else []
    except OSError:
        children = []
    for child in children:
        full = os.path.join(_real(pr), child)
        if child.startswith("."):
            continue                                         # tooling folders (.claude, .vscode ...) are never projects
        if os.path.islink(full) or not os.path.isdir(full):
            loose += 1                                       # a link is not a folder of this root, whatever it points to
        elif same_key(_real(full)) not in known and not sl.protected_dir(full, config):
            c = sortdirs.classify(full, config, private, _real(pr))
            c["name"] = None if c["private"] else child
            c["label"] = i18n_private(full) if c["private"] else child
            out.append(c)
    return out, loose


def i18n_private(path):
    import i18n
    import sortdirs
    return i18n.t("rc.private_folder", label=sortdirs.label(path))


def left_in_place(root=None, include=(), private=()):
    """Folders of projects_root that are not clearly projects, and how many loose files sit there: (names, loose count).
    The user names which of them are projects (`--include NAME`). A private folder is only its label."""
    entries, loose = _left(root, include, private)
    return [e["label"] for e in entries], loose


# ---------------------------------------------------------------- what holds a path

def _ancestors():
    pids, pid = set(), os.getpid()
    while pid and pid not in pids and len(pids) < 40:
        pids.add(pid)
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
        try:
            pid = int(r.stdout.strip() or 0)
        except ValueError:
            break
    return pids


def process_paths():
    """{pid: set of paths} every other process has open (cwd included), or None when lsof cannot say.
    Paths only: nothing is read from any file."""
    try:
        r = subprocess.run(["lsof", "-n", "-w", "-F", "pn"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode not in (0, 1) or not r.stdout:
        return None
    skip, out, pid = _ancestors(), {}, None
    for line in r.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and pid and pid not in skip:
            out.setdefault(pid, set()).add(line[1:])
    return out


def busy_count(src, procs):
    if procs is None:
        return None
    return sum(1 for paths in procs.values() if any(_within(p, src) for p in paths))


def live_sessions(src):
    """Session pointers bound to this project that are not ended (counts only)."""
    n = 0
    base = os.path.join(sl.home_state(), "sessions")
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    for name in names:
        try:
            rec = _read_json(os.path.join(base, name))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("ended") or not _within(_real(str(rec.get("project") or "")), src):
            continue
        seen = sl._epoch(rec.get("last_seen") or rec.get("ts"))
        if seen is None or time.time() - seen < sl.CLAIM_IDLE_S:
            n += 1
    return n


def linked_worktrees(src):
    """Paths of the linked worktrees of the repository at src, from git's own bookkeeping (.git/worktrees/*/gitdir):
    paths only, nothing of the project's contents."""
    out = []
    admin = os.path.join(src, ".git", "worktrees")
    try:
        names = sorted(os.listdir(admin))
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(admin, name, "gitdir"), encoding="utf-8") as f:
                gitdir = f.read().strip()
        except OSError:
            continue
        out.append(os.path.dirname(gitdir) if gitdir.endswith("/.git") else gitdir)
    return out


def _small_hits(src):
    """Non-protected only: how many of a few known small files name the old path (to edit by hand)."""
    n = 0
    for rel in SMALL_FILES:
        try:
            if src in sl.read_text(os.path.join(src, rel)):
                n += 1
        except OSError:
            pass
    return n


def _state_edits(src, dst):
    """(session pointer files, hub card files) that name a path inside src."""
    sessions, cards = [], []
    base = os.path.join(sl.home_state(), "sessions")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(base, name)
        try:
            rec = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and isinstance(rec.get("project"), str) and _within(rec["project"], src):
            sessions.append(path)
    for _slug, (card, p) in _card_paths(sl.load_config()).items():
        if _within(p, src):
            cards.append(card)
    return sessions, cards


def claude_folders(src, worktrees):
    """Existing ~/.claude/projects folders for the project path and each linked worktree: [(old name, new-name fn input path)]"""
    base = os.path.join(claude_dir(), "projects")
    try:
        have = set(os.listdir(base))
    except OSError:
        return []
    out, seen = [], set()
    for p in [src] + list(worktrees):                        # Claude Code may have named it with either spelling
        for v in (p, unicodedata.normalize("NFC", p), unicodedata.normalize("NFD", p)):
            if claude_name(v) in have and claude_name(v) not in seen:
                seen.add(claude_name(v))
                out.append(v)
    return out


# ---------------------------------------------------------------- the plan

SF_DATALESS = 0x40000000          # macOS: the file's content lives in iCloud and is not on this disk (optimized storage)
ICLOUD_WALK_CAP = 1000000


def dataless_count(path, cap=ICLOUD_WALK_CAP):
    """How many files under `path` are evicted to iCloud (dataless), from the stat flags alone: no content is read, so
    nothing is downloaded, and no name is kept. -> (count, complete). Not complete when the walk hit the cap or something
    could not be read (an evicted file may be in it). Symlinks are not followed. Where the platform has no such flag the count is 0."""
    found, seen, complete = 0, 0, True
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for e in entries:
                    seen += 1
                    if seen > cap:
                        return found, False
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        complete = False
                        continue
                    if getattr(st, "st_flags", 0) & SF_DATALESS:
                        found += 1
                    elif e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
        except OSError:
            complete = False
    return found, complete


def analyse(item, root, procs):
    """Fill the counts and the verdict of one discovered project. Reads nothing inside a protected project."""
    src, dst = item["src"], item["dst"]
    prot = item["protected"]
    item["label"] = label_of(src) if prot else item["name"]
    git_path = os.path.join(src, ".git")
    wts = linked_worktrees(src) if os.path.isdir(git_path) else []
    item["worktrees"] = wts
    try:
        item["entries"] = len(os.listdir(src))
    except OSError:
        item["entries"] = 0
    item["sessions"], item["cards"] = _state_edits(src, dst)
    item["live_sessions"] = live_sessions(src)
    item["claude"] = claude_folders(src, wts)
    item["busy"] = busy_count(src, procs)
    item["dataless"], item["dataless_complete"] = dataless_count(src)
    clear = not item["dataless"] and item["dataless_complete"]       # a read of an evicted file would download it: look only when none is
    item["hand_edits"] = None if prot or not clear else _small_hits(src)
    item["dirty"] = None
    item["git_state"] = "none"
    if not prot and os.path.lexists(git_path):
        item["git_state"] = "file" if os.path.isfile(git_path) else "ok"
        rc, out = _git(src, "status", "--porcelain")
        if rc:
            item["git_state"] = "failed"
        else:
            item["dirty"] = len(out.splitlines())
    if os.path.dirname(src) == root:
        verdict = ("under_root", 0)
    elif os.path.lexists(dst):
        verdict = ("target_exists", 0)
    elif item["busy"] is None:
        verdict = ("no_lsof", 0)
    elif item["busy"]:
        verdict = ("busy", item["busy"])
    elif item["live_sessions"]:
        verdict = ("sessions", item["live_sessions"])
    elif item["git_state"] == "file":
        verdict = ("git_file", 0)
    elif item["git_state"] == "failed":
        verdict = ("git_failed", 0)
    elif item["dirty"]:
        verdict = ("dirty", item["dirty"])
    elif item["dataless"]:
        verdict = ("icloud", item["dataless"])
    elif not item["dataless_complete"]:
        verdict = ("icloud_unknown", 0)
    elif _different_volume(src, root):
        verdict = ("volume", 0)
    else:
        verdict = ("ready", 0)
    item["verdict"] = verdict[0]
    item["verdict_n"] = verdict[1]
    return item


def _different_volume(src, root):
    parent = root
    while not os.path.exists(parent) and os.path.dirname(parent) != parent:
        parent = os.path.dirname(parent)
    try:
        return os.stat(src).st_dev != os.stat(parent).st_dev
    except OSError:
        return True


def plan(root=None, include=(), exclude=(), private=()):
    started = time.time()
    root = _real(root or default_root())
    procs = process_paths()
    items = [analyse(i, root, procs) for i in discover(root, include, exclude)]
    taken = set()
    for it in items:                                                  # two projects with one folder name cannot share a target
        if it["dst"] in taken and it["verdict"] == "ready":
            it["verdict"], it["verdict_n"] = "duplicate", 0
        taken.add(it["dst"])
    entries, loose = _left(root, include, private)
    return {"root": root, "projects": items, "seconds": round(time.time() - started, 1), "lsof": procs is not None,
            "left": [e["label"] for e in entries], "loose": loose, "sorted": [{k: v for k, v in e.items() if k != "name"} for e in entries]}


def sort_line(e):
    """One line for a folder that stays: what it is, size, how long it sat untouched, and what the plan proposes."""
    import i18n
    import sortdirs
    if e["private"]:
        return i18n.t("rc.sort_private", label=e["label"])
    idle = "-" if e["idle_days"] is None else str(e["idle_days"])
    size = sortdirs.size_text(e["bytes"]) + ("+" if not e["complete"] else "")
    prop = "rc.sort_trash" if e["trash_ok"] else ("rc.sort_ask" if e["kind"] in ("unknown", "orphan_worktree") else "rc.sort_keep")
    return i18n.t("rc.sort_line", label=e["label"], kind=i18n.t("rc.sort_k_" + e["kind"], owner=e.get("owner") or "?"),
                  size=size, idle=idle, files=e["files"], prop=i18n.t(prop))


def verdict_text(item):
    import i18n
    return i18n.t("rc.v_" + item["verdict"], n=item.get("verdict_n", 0))


def render(result):
    """The dry-run text: counts, labels and verdicts; protected projects only as `protected #hash`."""
    import i18n
    lines = [i18n.t("rc.plan_head", root=result["root"], n=len(result["projects"]), secs=result["seconds"]), ""]
    for i in result["projects"]:
        where = i18n.t("rc.protected", label=i["label"]) if i["protected"] else "%s  %s -> %s" % (i["label"], i["src"], i["dst"])
        lines.append("%-8s %s" % (i18n.t("rc.kind_" + i["kind"]), where))
        lines.append("    " + verdict_text(i))
        line = i18n.t("rc.counts", entries=i["entries"], wts=len(i["worktrees"]), sessions=len(i["sessions"]),
                      cards=len(i["cards"]), claude=len(i["claude"]), live=i["live_sessions"])
        if i["hand_edits"] is not None:
            line += i18n.t("rc.hand_edits", n=i["hand_edits"])
        lines.append("    " + line)
    if result.get("left") or result.get("loose"):
        lines += ["", i18n.t("rc.left", n=len(result["left"]), names=", ".join(result["left"]) or "-", loose=result["loose"])]
    if result.get("sorted"):
        lines += ["", i18n.t("rc.sort_head")] + ["    " + sort_line(e) for e in result["sorted"]]
    ready = sum(1 for i in result["projects"] if i["verdict"] == "ready")
    lines += ["", i18n.t("rc.plan_tail", ready=ready, skipped=len(result["projects"]) - ready)]
    return "\n".join(lines)


# ---------------------------------------------------------------- leftovers to the Trash

def trash_dir():
    return os.environ.get("BUDGIT_TRASH_DIR") or os.path.join(os.path.expanduser("~"), ".Trash")


def trash(name, root=None, private=(), dry=True):
    """Move a leftover folder of projects_root (sortdirs says empty, or a plugin copy idle for days) into the Trash, where it
    stays recoverable; nothing is ever deleted. Re-sorted at the moment it runs: a private, unknown, project or recently
    touched folder, or one something has open, is refused. With dry=True only says what it would do.
    -> {name, kind, bytes, from, to}."""
    import i18n
    import sortdirs
    config = sl.load_config()
    pr = config.get("projects_root")
    if not pr:
        raise RelocateError(i18n.t("rc.trash_noroot"))
    base = _real(pr)
    full = os.path.join(base, name)
    if not name or os.path.basename(name) != name or name.startswith("."):
        raise RelocateError(i18n.t("rc.trash_missing", name="?"))
    if sortdirs.is_private(full, config, private, base):              # first: a private name is never echoed
        raise RelocateError(i18n.t("rc.trash_private", label=sortdirs.label(full)))
    if os.path.islink(full) or not os.path.isdir(full):
        raise RelocateError(i18n.t("rc.trash_missing", name=name))
    c = sortdirs.classify(full, config, private, base)
    if not c["trash_ok"]:
        raise RelocateError(i18n.t("rc.trash_refused", name=name, kind=i18n.t("rc.sort_k_" + c["kind"], owner=c.get("owner") or "?")))
    procs = process_paths()
    if procs is None or busy_count(_real(full), procs) or live_sessions(_real(full)):
        raise RelocateError(i18n.t("rc.trash_open", name=name))
    dest = os.path.join(trash_dir(), name)
    if os.path.lexists(dest):
        dest = "%s %s" % (dest, time.strftime("%H.%M.%S"))
    result = {"name": name, "kind": c["kind"], "bytes": c["bytes"], "from": _real(full), "to": dest}
    if dry:
        return result
    try:
        os.makedirs(trash_dir(), exist_ok=True)
        os.rename(_real(full), dest)
    except OSError as exc:
        raise RelocateError(i18n.t("rc.trash_failed", name=name, why=exc.strerror or type(exc).__name__))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    _write_json(os.path.join(folder(), "trash-%s-%s.json" % (stamp, re.sub(r"[^A-Za-z0-9_.-]", "_", name))),
                dict(result, version=2, stamp=stamp))           # version 2: `manifests` (version 1) never reads it as a move
    return result


# ---------------------------------------------------------------- apply / undo

def _manifest_path(name, stamp):
    base = os.path.join(folder(), "%s-%s" % (stamp, re.sub(r"[^A-Za-z0-9_.-]", "_", name)))
    path, n = base + ".json", 1
    while os.path.exists(path):                                       # two moves in the same millisecond never share a record
        n += 1
        path = "%s-%d.json" % (base, n)
    return path


def _seq(path):
    """The collision number of a manifest file (`...-2.json` is the second record of its millisecond)."""
    found = re.search(r"-(\d+)\.json$", path)
    return int(found.group(1)) if found else 1


def manifests(name=None):
    try:
        names = sorted(os.listdir(folder()))
    except OSError:
        return []
    out = []
    for f in names:
        if not f.endswith(".json"):
            continue
        try:
            m = _read_json(os.path.join(folder(), f))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("version") == 1 and (name is None or name in (m.get("name"), m.get("label"))):
            m["_file"] = os.path.join(folder(), f)
            out.append(m)
    out.sort(key=lambda m: (m.get("stamp", ""), _seq(m["_file"])))
    return out


def _save(m):
    path = m.pop("_file", None) or _manifest_path(m["name"], m["stamp"])
    _write_json(path, m)
    m["_file"] = path


def _set_config(mutator):
    """Atomic edit of the home config.json; returns (old, new) values of the touched keys."""
    with sl.home_lock("config", strict=True):
        cfg = _load_home_config()
        before = json.loads(json.dumps(cfg))
        mutator(cfg)
        if cfg != before:
            sl.atomic_write(_config_path(), json.dumps(cfg, indent=2) + "\n")
    return before, cfg


def _repair(dst, worktrees, src):
    paths = [_swap(w, src, dst) for w in worktrees]
    paths = [p for p in paths if os.path.isdir(p)]
    rc, out = _git(dst, "worktree", "repair", *paths)
    return rc == 0


def _rewrite_state(m, src, dst, sessions, cards):
    """Session pointers and hub cards (found before the rename, while the hub could still be read) that name a path inside src
    now name dst. Every edit is recorded as it is made, so a crash half way still leaves a manifest that can be undone."""
    m["edits"] = []
    for path in sessions:
        rec = _read_json(path)
        new = dict(rec, project=_swap(rec["project"], src, dst))
        m["edits"].append({"file": path, "kind": "session", "old": rec["project"], "new": new["project"]})
        _save(m)
        sl.atomic_write(path, json.dumps(new) + "\n")
    for path in cards:
        if not os.path.exists(path) and _within(path, src):            # a card inside the moved folder is at the new path now
            path = _swap(path, src, dst)
        text = sl.read_text(path)
        m2 = re.search(r"^path:\s*(.+?)\s*$", text, re.M)
        if m2 and _within(m2.group(1), src):
            new_value = _swap(m2.group(1), src, dst)
            m["edits"].append({"file": path, "kind": "card", "old": m2.group(1), "new": new_value})
            _save(m)
            sl.atomic_write(path, text[:m2.start(1)] + new_value + text[m2.end(1):])
    _save(m)


def _file_of(e, src=None, dst=None):
    """Where an edited file is now: a card inside the moved folder lives at the new path after the rename and at the old one
    again after it moves back."""
    if os.path.exists(e["file"]) or not (src and dst):
        return e["file"]
    for a, b in ((dst, src), (src, dst)):
        if _within(e["file"], a) and os.path.exists(_swap(e["file"], a, b)):
            return _swap(e["file"], a, b)
    return e["file"]


def _held(e, src=None, dst=None):
    """The value an edited file holds now, or None."""
    try:
        path = _file_of(e, src, dst)
        if e["kind"] == "session":
            return _read_json(path).get("project")
        m = re.search(r"^path:\s*(.+?)\s*$", sl.read_text(path), re.M)
        return m.group(1) if m else None
    except (OSError, ValueError, AttributeError):
        return None


def _edits_changed(edits, src=None, dst=None):
    """How many recorded edits no longer hold the value this tool wrote (an edit not yet written still holds the old one)."""
    return sum(1 for e in edits if _held(e, src, dst) not in (e["new"], e["old"]))


def _undo_state(edits, src=None, dst=None):
    """Put every recorded value back, only where the file still holds the value this tool wrote."""
    for e in reversed(edits):
        try:
            if _held(e, src, dst) != e["new"]:
                continue
            path = _file_of(e, src, dst)
            if e["kind"] == "session":
                sl.atomic_write(path, json.dumps(dict(_read_json(path), project=e["old"])) + "\n")
            else:
                text = sl.read_text(path)
                m = re.search(r"^path:\s*(.+?)\s*$", text, re.M)
                sl.atomic_write(path, text[:m.start(1)] + e["old"] + text[m.end(1):])
        except (OSError, ValueError, AttributeError):
            pass


# ---------------------------------------------------------------- config changes, as reversible operations

def _same(a, b):
    return bool(a) and bool(b) and _real(a) == _real(b)


def _config_ops(src, dst, kind, protected):
    """(before the rename, after the rename). A protected project's new path joins protected_dirs first (both are protected
    during the move) and the old one leaves last; the hub path follows after. projects_root is finish()'s, not a move's."""
    before, after = [], []
    if protected:
        before.append({"key": "protected_dirs", "op": "add", "path": dst})
        after.append({"key": "protected_dirs", "op": "remove", "path": src})
    if kind == "hub":
        after.append({"key": "hub", "op": "set", "old": src, "new": dst})
    return before, after


def _run_ops(ops):
    """Apply operations to the home config in one atomic write. -> the operations that really changed something (their exact
    inverse is what undo runs, so a key a move never touched is never rewritten)."""
    done = []

    def mut(cfg):
        for o in ops:
            key = o["key"]
            if o["op"] == "add":
                dirs = list(cfg.get(key) or [])
                if not any(_same(d, o["path"]) for d in dirs):
                    cfg[key] = dirs + [o["path"]]
                    done.append(o)
            elif o["op"] == "remove":
                dirs = list(cfg.get(key) or [])
                kept = [d for d in dirs if not _same(d, o["path"])]
                if len(kept) != len(dirs):
                    cfg[key] = kept
                    done.append(o)
            elif o["op"] == "put" or _same(cfg.get(key), o["old"]):          # "set" only replaces the value it expects
                cfg[key] = o["new"]
                done.append(dict(o, op="set"))
    _set_config(mut)
    return done


def _invert(ops):
    flip = {"add": "remove", "remove": "add"}
    return [dict(o, op=flip[o["op"]]) if o["op"] in flip else dict(o, old=o["new"], new=o["old"]) for o in reversed(ops)]


def _config_conflicts(ops):
    """How many of these operations expect a value the config no longer holds."""
    cfg = _load_home_config()
    return sum(1 for o in ops if o["op"] == "set" and not any(
        cfg.get(o["key"]) == v or _same(cfg.get(o["key"]), v) for v in (o["old"], o["new"])))


def _text(key, **kw):
    import i18n
    return i18n.t(key, **kw)


def apply(name, root=None, include=(), exclude=()):
    """Move one project. -> manifest. Raises RelocateError (nothing changed) when it must be skipped."""
    root = _real(root or default_root())
    procs = process_paths()
    cands = [i for i in discover(root, include, exclude) if i["name"] == name or label_of(i["src"]) == name]
    if len(cands) != 1:
        raise RelocateError(_text("rc.err_none", name=name))
    item = analyse(cands[0], root, procs)
    if [m for m in manifests(item["name"]) if m.get("status") in ("started", "moved")]:
        raise RelocateError(_text("rc.err_unfinished", label=item["label"]))
    if item["verdict"] != "ready":
        raise RelocateError("%s: %s" % (item["label"], verdict_text(item)))
    src, dst, prot = item["src"], item["dst"], item["protected"]
    stamp = time.strftime("%Y%m%dT%H%M%S") + ".%03d" % (int(time.time() * 1000) % 1000)
    m = {"version": 1, "name": item["name"], "label": item["label"], "stamp": stamp, "src": src, "dst": dst, "root": root,
         "kind": item["kind"], "protected": prot, "status": "started", "worktrees": item["worktrees"], "edits": [],
         "ops": {"before": [], "after": []}, "plan_ops": {"before": [], "after": []}, "claude": [claude_name(p) for p in item["claude"]], "counts": {
             "entries": item["entries"], "worktrees": len(item["worktrees"]), "sessions": len(item["sessions"]),
             "cards": len(item["cards"])}}
    os.makedirs(folder(), mode=0o700, exist_ok=True)
    _save(m)                                                          # the manifest exists before anything is touched
    os.makedirs(root, exist_ok=True)
    before, after = _config_ops(src, dst, item["kind"], prot)
    m["plan_ops"] = {"before": before, "after": after}                  # written ahead: a cut-off move is undone from this
    done = []
    try:
        _save(m)
        m["ops"]["before"] = _run_ops(before)
        _save(m)
        done.append("config")
        os.rename(src, dst)
        done.append("rename")
        m["status"] = "moved"; _save(m)
        _repair(dst, item["worktrees"], src)
        _rewrite_state(m, src, dst, item["sessions"], item["cards"])
        done.append("state")
        m["ops"]["after"] = _run_ops(after)
        m["status"] = "applied"
        m["finished"] = sl.now()
        _save(m)
    except BaseException as exc:
        _rollback(m, done)
        m["status"] = "failed"
        m["error"] = type(exc).__name__ if prot else str(exc)          # a protected project's path never reaches any text
        _save(m)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise RelocateError(_text("rc.err_failed", label=item["label"], done=", ".join(done) or "-", why=m["error"]))
    return m


def _rollback(m, done):
    src, dst = m["src"], m["dst"]
    _undo_state(m.get("edits") or [], src, dst)
    _run_ops(_invert(m["ops"]["after"]))                               # protected again at the old path before it moves back
    if "rename" in done and not os.path.lexists(src) and os.path.isdir(dst):
        os.rename(dst, src)
        _repair(src, m["worktrees"], src)
    _undo_state(m.get("edits") or [], src, dst)
    _run_ops(_invert(m["ops"]["before"]))


ROOT_NAME = "_root"


def finish(root=None):
    """After the moves: Budgit's projects folder (`projects_root`) becomes the root. Reversible with undo("_root")."""
    root = _real(root or default_root())
    old = _load_home_config().get("projects_root")
    ops = _run_ops([{"key": "projects_root", "op": "put", "old": old, "new": root}])
    m = {"version": 1, "name": ROOT_NAME, "label": ROOT_NAME, "stamp": time.strftime("%Y%m%dT%H%M%S") + ".%03d" % (int(time.time() * 1000) % 1000),
         "kind": "root", "root": root, "protected": False, "status": "applied", "edits": [], "worktrees": [], "claude": [],
         "ops": {"before": [], "after": ops}, "finished": sl.now()}
    os.makedirs(folder(), mode=0o700, exist_ok=True)
    _save(m)
    return m


def undo(name):
    """Reverse the last applied (or half-done) move of `name`. Changes nothing and says why when the machine no longer matches."""
    found = [m for m in manifests(name) if m.get("status") in ("applied", "moved", "started")]
    if not found:
        raise RelocateError(_text("rc.err_nothing_undo", name=name))
    m = found[-1]
    ops = m.get("ops") or {"before": [], "after": []}
    if m.get("status") != "applied":                                    # cut off: what the move meant to change is what to reverse
        planned = m.get("plan_ops") or {"before": [], "after": []}
        ops = {"before": ops["before"] or planned["before"], "after": ops["after"] or planned["after"]}
    if m.get("kind") == "root":
        if _config_conflicts(_invert(ops["after"])):
            raise RelocateError(_text("rc.err_changed", label=m["label"], n=_config_conflicts(_invert(ops["after"]))))
        _run_ops(_invert(ops["after"]))
        m["status"], m["undone"] = "undone", sl.now()
        _save(m)
        return m
    src, dst = m["src"], m["dst"]
    moved = os.path.isdir(dst)
    if not moved and not os.path.isdir(src):
        raise RelocateError(_text("rc.err_gone", label=m["label"]))
    if moved:
        if os.path.lexists(src):
            raise RelocateError(_text("rc.err_taken", label=m["label"]))
        procs = process_paths()
        if procs is None or busy_count(dst, procs):
            raise RelocateError(_text("rc.err_open", label=m["label"]))
    changed = _edits_changed(m.get("edits") or [], src, dst) + _config_conflicts(_invert(ops["after"]))
    if changed:
        raise RelocateError(_text("rc.err_changed", label=m["label"], n=changed))
    _run_ops(_invert(ops["after"]))                                    # a protected project is protected at the old path first
    if moved:
        try:
            os.rename(dst, src)
        except OSError:
            _run_ops(ops["after"])
            raise RelocateError(_text("rc.err_open", label=m["label"]))
        _repair(src, m["worktrees"], src)
    _undo_state(m.get("edits") or [], src, dst)
    _run_ops(_invert(ops["before"]))
    m["status"] = "undone"
    m["undone"] = sl.now()
    _save(m)
    return m


# ---------------------------------------------------------------- what is left to the user

def hand_steps(result_or_manifest_list):
    """The steps this tool never does: the ~/.claude folder renames (a script per project, 0600) and the marketplace path.
    -> list of text lines; protected projects appear only as a label and a script path."""
    lines = []
    for m in result_or_manifest_list:
        names = m.get("claude") or []
        if not names:
            continue
        script = os.path.join(folder(), "%s-%s-claude-folders.sh" % (m["stamp"], re.sub(r"[^A-Za-z0-9_.-]", "_", m["label"])))
        body = ["#!/bin/sh", "# ~/.claude/projects: %s" % m["label"]]
        base = os.path.join(claude_dir(), "projects")
        for old_name in names:
            old_path = _desanitise(old_name, m)
            new_name = claude_name(_swap(old_path, m["src"], m["dst"]))
            body.append('[ -d "%s/%s" ] && [ ! -e "%s/%s" ] && mv "%s/%s" "%s/%s"' % (base, old_name, base, new_name, base, old_name, base, new_name))
        os.makedirs(folder(), mode=0o700, exist_ok=True)
        sl.atomic_write(script, "\n".join(body) + "\n")
        os.chmod(script, 0o600)
        lines.append(_text("rc.hand_claude", label=m["label"], n=len(names), script=script))
    return lines


def _desanitise(old_name, m):
    for p in [m["src"]] + list(m.get("worktrees") or []):
        if claude_name(p) == old_name:
            return p
    return m["src"]


def marketplace_note(m):
    if m.get("kind") != "hub":
        return None
    if m.get("protected"):
        return _text("rc.hand_market_protected", label=m["label"])
    return _text("rc.hand_market", old=m["src"], new=m["dst"])
