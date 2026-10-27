"""budgit skills: what Budgit has, what it may pick for a job, and why (D26-D31, D43-D45).

The registry (skills/budgit-kickoff/reference/skills-registry.json) is the selector's only input; skillselect.py is the
pure function over it. This module is the I/O around them: it finds the registry, hashes installed skill folders,
reads the environment, prints in the user's language, appends one JSON line per `why` to the selection log.

Read-only towards the user's setup: it never writes inside a skills folder, a plugin folder or a settings file. The only
files it writes are the selection log and, for the maintainer's `build`, the registry and its generated .md.
Offline: no network at all. Values of environment variables are never read into output, only their names. Stdlib only.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import time

import i18n
import skillselect as sel
import studiolib as sl

REGISTRY_ENV = "BUDGIT_SKILLS_REGISTRY"
LOG_NAME = "skills-log.jsonl"
GAPS_NAME = "skills-gaps.json"
HASH_SKIP_FILES = {"PINNED.txt", ".DS_Store"}
HASH_SKIP_DIRS = {".git", "node_modules", "__pycache__"}
SCRIPT_EXTS = (".js", ".mjs", ".cjs", ".ts", ".py", ".sh", ".json")
URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
MAX_ROWS = 40
OWN_SCOPES = ("own",)
HASHED_SCOPES = ("user", "plugin", "own")


# --- the files ---------------------------------------------------------------------------------------------
NAMESPACE_HOSTS = {"www.w3.org", "w3.org", "schemas.openxmlformats.org", "schemas.microsoft.com", "purl.org"}   # XML/SVG namespace names, never fetched


def kickoff_dir():
    import vendor
    return vendor._skill_source()


def registry_path(explicit=None):
    path = explicit or os.environ.get(REGISTRY_ENV)
    if path:
        return os.path.realpath(os.path.expanduser(path))
    base = kickoff_dir()
    if not base:
        raise sl.StudioError(i18n.t("skills.no_registry"))
    return os.path.join(base, "reference", "skills-registry.json")


def load_registry(explicit=None, overlay=None):
    """The shipped registry (public skills only) plus, when `overlay` is True or a path, this user's private overlay.
    By default the overlay joins only when no explicit registry file was named, so a fixture or a maintainer's file stays itself."""
    path = registry_path(explicit)
    data = _read_registry_file(path)
    if overlay is None:
        overlay = explicit is None
    if overlay:
        data = merge_overlay(data, load_overlay(overlay_path() if overlay is True else overlay))
    return data


def _read_registry_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise sl.StudioError(i18n.t("skills.registry_bad", path=path, why=exc.__class__.__name__))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise sl.StudioError(i18n.t("skills.registry_bad", path=path, why="entries"))
    return data


# --- the private overlay: this user's own skills never enter the shipped file ----------------------------------------
OVERLAY_NAME = "skills-registry.local.json"


def overlay_path():
    return os.path.join(sl.home_state(), OVERLAY_NAME)


def is_private(entry):
    """Private = marked so, or installed at user level from no public source (a skill the user wrote or copied in)."""
    mark = entry.get("visibility")
    if mark:
        return mark == "private"
    source, install = entry.get("source") or {}, entry.get("install") or {}
    return install.get("scope") == "user" and not source.get("repo")


def load_overlay(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"entries": []}
    rows = data.get("entries") if isinstance(data, dict) else None
    return {"entries": [e for e in rows or [] if isinstance(e, dict) and e.get("id")]}


def merge_overlay(registry, overlay):
    known = {e["id"] for e in registry["entries"]}
    extra = [dict(e, visibility="private") for e in overlay["entries"] if e["id"] not in known]
    return dict(registry, entries=list(registry["entries"]) + extra)


def _version_key(path):
    """Sort key that puts 7.0.0 after 6.4.1 and 10.0.0 after 9.0.0 (plain text order would not)."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-_]", os.path.basename(path))]


def newest(paths):
    """Oldest first, newest last: by version when every folder name is a version, else by modification time (sha-named caches)."""
    paths = list(paths)
    if all(re.match(r"^\d+(\.\d+)*$", os.path.basename(p)) for p in paths):
        return sorted(paths, key=_version_key)
    return sorted(paths, key=lambda p: (os.path.getmtime(p), p))


def claude_dir():
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def expand(path, claude=None):
    """'~/.claude/skills/x' -> the real folder (CLAUDE_CONFIG_DIR respected); other '~' as usual."""
    claude = claude or claude_dir()
    if path == "~/.claude" or path.startswith("~/.claude/"):
        path = claude + path[len("~/.claude"):]
    return os.path.expanduser(path)


def resolve_install(entry, claude=None):
    """The folder (or file) an entry is installed at, or None. Globs pick the newest match (plugin caches are versioned)."""
    inst = entry.get("install") or {}
    path = inst.get("path")
    if not path:
        return None
    if inst.get("scope") in OWN_SCOPES:
        base = kickoff_dir()
        full = os.path.join(base, path) if base else None
        return full if full and os.path.exists(full) else None
    full = expand(path, claude)
    if any(c in full for c in "*?["):
        # a versioned or sha-named cache folder: the newest match wins
        hits = glob.glob(full)
        parts = full.split(os.sep)
        wild = next(i for i, part in enumerate(parts) if any(c in part for c in "*?["))
        cache = {}
        for h in hits:
            cache[h] = os.sep.join(h.split(os.sep)[:wild + 1])
        order = newest(sorted(set(cache.values())))
        hits.sort(key=lambda h: order.index(cache[h]))
    else:
        hits = [full] if os.path.exists(full) else []
    return hits[-1] if hits else None


# --- hashing and scanning an installed folder ---------------------------------------------------------------
def tree_hash(path):
    """sha256 over the sorted relative paths and file hashes of a folder (PINNED.txt and .DS_Store left out). None if absent."""
    if not path or not os.path.isdir(path):
        return None
    h = hashlib.sha256()
    for base, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in HASH_SKIP_DIRS)
        for name in sorted(files):
            if name in HASH_SKIP_FILES:
                continue
            full = os.path.join(base, name)
            fh = hashlib.sha256()
            try:
                with open(full, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        fh.update(chunk)
            except OSError:
                continue
            h.update(("%s\0%s\n" % (os.path.relpath(full, path), fh.hexdigest())).encode("utf-8"))
    return h.hexdigest()


def file_sha256(path):
    try:
        fh = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                fh.update(chunk)
        return fh.hexdigest()
    except OSError:
        return None


def scan_flags(path):
    """What the files of an installed folder say about themselves: a hooks folder or file, an MCP config, and the web hosts
    named inside script files (documentation links in .md files are left out)."""
    flags = {"hooks": False, "mcp": False, "script_hosts": []}
    if not path or not os.path.isdir(path):
        return flags
    hosts = set()
    for base, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in HASH_SKIP_DIRS)
        if os.path.basename(base) == "hooks":
            flags["hooks"] = True
        for name in files:
            if name in ("hooks.json",):
                flags["hooks"] = True
            if name in (".mcp.json", "mcp.json"):
                flags["mcp"] = True
            if name.endswith(SCRIPT_EXTS) and name != "package-lock.json":
                full = os.path.join(base, name)
                try:
                    if os.path.getsize(full) > 2_000_000:
                        continue
                    with open(full, encoding="utf-8", errors="ignore") as f:
                        hosts.update(h.lower() for h in URL_HOST.findall(f.read()) if h.lower() not in NAMESPACE_HOSTS)
                except OSError:
                    continue
    flags["script_hosts"] = sorted(hosts)
    return flags


def frontmatter(path):
    """-> dict of the simple `key: value` lines of a SKILL.md frontmatter (description joined if folded)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(60000)
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    head = text.split("\n---", 1)[0].split("\n")[1:]
    out, key = {}, None
    for line in head:
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            key = m.group(1)
            out[key] = m.group(2).strip().strip("'\"")
            if out[key] in (">", "|", ">-", "|-"):
                out[key] = ""
        elif key and line.strip():
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


# --- what is installed -------------------------------------------------------------------------------------
def settings_files(project=None, claude=None):
    claude = claude or claude_dir()
    files = [os.path.join(claude, "settings.json")]
    if project:
        files += [os.path.join(project, ".claude", "settings.json"), os.path.join(project, ".claude", "settings.local.json")]
    return files


def read_settings(project=None, claude=None):
    """-> {'env': set(names), 'plugins': {key: bool}, 'overrides': {skill: mode}, 'env_values': {name: value}}.
    Values are kept only so a condition like `=0` can be compared; they are never printed."""
    out = {"env": set(), "plugins": {}, "overrides": {}, "env_values": {}}
    for path in settings_files(project, claude):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        env = data.get("env")
        if isinstance(env, dict):
            for name, value in env.items():
                out["env"].add(name)
                out["env_values"][name] = str(value)
        if isinstance(data.get("enabledPlugins"), dict):
            out["plugins"].update({k: bool(v) for k, v in data["enabledPlugins"].items()})
        if isinstance(data.get("skillOverrides"), dict):
            out["overrides"].update({k: v for k, v in data["skillOverrides"].items() if isinstance(v, str)})
    return out


def installed_skills(claude=None, plugins=None):
    """-> [{'id', 'path', 'scope'}]: user skill folders, and the skills of plugins enabled in the settings (newest cached version)."""
    claude = claude or claude_dir()
    found = []
    for skill_md in sorted(glob.glob(os.path.join(claude, "skills", "*", "SKILL.md"))):
        d = os.path.dirname(skill_md)
        found.append({"id": os.path.basename(d), "path": d, "scope": "user"})
    for key, on in sorted((plugins or {}).items()):
        if not on or "@" not in key:
            continue
        name, market = key.split("@", 1)
        versions = newest(glob.glob(os.path.join(claude, "plugins", "cache", market, name, "*")))
        if not versions:
            continue
        for skill_md in sorted(glob.glob(os.path.join(versions[-1], "skills", "*", "SKILL.md"))):
            d = os.path.dirname(skill_md)
            found.append({"id": "%s:%s" % (name, os.path.basename(d)), "path": d, "scope": "plugin"})
    return found


def known_ids(registry):
    """Every id the registry answers to: the entry id and its `aliases` (a plugin skill is listed as plugin:skill)."""
    out = set()
    for e in registry["entries"]:
        out.add(e["id"])
        out.update(e.get("aliases") or [])
    return out


def entry_states(registry, claude=None):
    """-> {id: 'drifted' | 'missing'} for entries whose installed copy differs from the registry or is gone."""
    out = {}
    for e in registry["entries"]:
        inst = e.get("install") or {}
        if e.get("status") not in sel.SELECTABLE or inst.get("scope") not in HASHED_SCOPES or not inst.get("path"):
            continue
        path = resolve_install(e, claude)
        if path is None:
            out[e["id"]] = "missing"
            continue
        pin = (e.get("source") or {}).get("tree_sha256")
        if pin and os.path.isdir(path) and tree_hash(path) != pin:
            out[e["id"]] = "drifted"
    return out


def read_design(project):
    """The project's design settings: `author` (an explicit field, never inferred from a folder) and optional switches."""
    if not project:
        return {}
    try:
        with open(os.path.join(sl.studio_dir(project), "design.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def node_version():
    node = shutil.which("node")
    if not node:
        return None
    try:
        import subprocess
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def chrome_present():
    return any(os.path.exists(p) for p in ("/Applications/Google Chrome.app", "/Applications/Chromium.app")) or bool(
        shutil.which("google-chrome") or shutil.which("chromium"))


def environment(registry, project=None, claude=None, detect=True):
    """What the selector may rely on here. A project's design.json can switch things off (binary, network)."""
    claude = claude or claude_dir()
    settings = read_settings(project, claude)
    design = read_design(project)
    env = {"state": entry_states(registry, claude), "author": design.get("author") or None,
           "binary_allowed": design.get("binary", True) is not False, "network": design.get("network", True) is not False,
           "project_rules": list(design.get("rules") or []) or ["no_claude_footer"],
           "keys": [], "browser_tool": design.get("browser_tool", True) is not False}
    if detect:
        env["node"], env["chrome"] = node_version(), chrome_present()
        try:
            env["codex"] = sl.load_config().get("codex") == "on" and bool(shutil.which("codex"))
        except Exception:
            env["codex"] = False
    known = known_ids(registry)
    env["unregistered"] = sorted(s["id"] for s in installed_skills(claude, settings["plugins"]) if s["id"] not in known)
    return env


def current_level(explicit=None):
    if explicit in sel.LEVELS:
        return explicit
    try:
        import budget
        return budget.current()["level"]
    except Exception:
        return "normal"


# --- check (D27 reduced, D31, D43): offline, read-only ------------------------------------------------------
def check(registry, project=None, claude=None, environ=None):
    """-> list of findings {kind, id, detail}: drifted, missing, unregistered, flags-changed, env. Offline."""
    claude = claude or claude_dir()
    environ = os.environ if environ is None else environ
    settings = read_settings(project, claude)
    out = []
    states = entry_states(registry, claude)
    for e in registry["entries"]:
        st = states.get(e["id"])
        if st:
            out.append({"kind": st, "id": e["id"], "detail": ""})
        path = resolve_install(e, claude)
        if path and os.path.isdir(path) and e.get("status") in sel.SELECTABLE and (e.get("install") or {}).get("scope") in HASHED_SCOPES:
            seen, flags = scan_flags(path), e.get("flags") or {}
            changed = [name for name in ("hooks", "mcp") if seen[name] and not flags.get(name)]
            new_hosts = [h for h in seen["script_hosts"] if h not in set(flags.get("script_hosts") or [])] if "script_hosts" in flags else []
            if changed or new_hosts:
                out.append({"kind": "flags-changed", "id": e["id"], "detail": ", ".join(changed + new_hosts[:3])})
    known = known_ids(registry)
    for s in installed_skills(claude, settings["plugins"]):
        if s["id"] not in known:
            out.append({"kind": "unregistered", "id": s["id"], "detail": ""})
    out += env_report(registry, settings, claude, environ)
    return out


def env_report(registry, settings, claude, environ):
    """D31 / D43: the read-only report. Names of variables and whether they are set or the condition holds; never a value."""
    out, seen = [], set()
    names = set(settings["env"]) | set(environ)
    for e in registry["entries"]:
        if e.get("status") not in sel.SELECTABLE and not e.get("env_checks_when_listed"):
            continue
        key = e.get("plugin_key")
        plugin_on = settings["plugins"].get(key) if key else True
        if key and not plugin_on:
            continue
        for chk in e.get("env_checks") or []:
            var = chk["var"]
            alts = chk.get("any_of") or [var]
            present = [v for v in alts if v in names]
            want = chk.get("equals")
            ok = bool(present)
            if ok and want is not None:
                value = settings["env_values"].get(present[0], environ.get(present[0]))
                ok = str(value) == str(want)
            if not ok and (key or e["id"], var) not in seen:      # one line per plugin, not one per skill it ships
                seen.add((key or e["id"], var))
                out.append({"kind": "env", "id": (key or e["id"]).split("@")[0], "detail": "/".join(alts), "condition": chk.get("condition")})
    for e in registry["entries"]:
        pin = (e.get("source") or {}).get("binary_sha256")
        rel = (e.get("install") or {}).get("binary_path")
        if pin and rel:
            base = resolve_install(e, claude)
            if base:
                got = file_sha256(os.path.join(base, rel))
                if got is not None and got != pin:
                    out.append({"kind": "binary", "id": e["id"], "detail": "sha256"})
    return out


# --- why: the dry run of the selector and the log (D29, D38) ---------------------------------------------------
def log_path(project):
    return os.path.join(sl.studio_dir(project), LOG_NAME)


def append_log(project, record):
    """One JSON line; the folder must exist already (a project that never ran Budgit has none and gets no log)."""
    folder = sl.studio_dir(project)
    if not os.path.isdir(folder) or os.path.islink(folder):
        return None
    path = log_path(project)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def note_gap(project, result, ts):
    """A gap opens (or updates) an `unresolved` row in the project's gap list, so someone looks for a skill or writes a rule."""
    gap = result.get("gap")
    folder = sl.studio_dir(project) if project else None
    if not gap or not folder or not os.path.isdir(folder) or os.path.islink(folder):
        return None
    path = os.path.join(folder, GAPS_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        rows = {}
    row = rows.get(gap["watch_row"]) or {"id": gap["watch_row"], "kind": "gap", "status": "unresolved", "first": ts, "count": 0}
    row.update(last=ts, count=int(row.get("count", 0)) + 1, job=gap["job"], surface=gap.get("surface"),
               fallback=gap["fallback"])
    rows[gap["watch_row"]] = row
    sl.atomic_write(path, json.dumps(rows, indent=1, sort_keys=True) + "\n")
    return path


def why(registry, job, traits, project=None, claude=None, level=None, log=True, env=None, now=None):
    """-> (plan dict, log record). Appends the record to the selection log when the project has a data folder."""
    traits = dict(traits or {})
    traits.setdefault("budget", current_level(level or traits.get("budget")))
    env = env or environment(registry, project, claude)
    if env.get("author") and "author" not in traits:
        traits["author"] = env["author"]
    result = sel.plan(registry, job, traits, env)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))
    record = sel.log_line(result, ts)
    if log and project:
        result["logged"] = append_log(project, record)
        note_gap(project, result, ts)
    return result, record


# --- printing ------------------------------------------------------------------------------------------------
def reason_text(code, lang=None):
    base, _, arg = code.partition(":")
    key = "skills.reason." + base
    try:
        return i18n.t(key, lang, arg=arg)
    except KeyError:
        return code


def _cap(rows, limit=MAX_ROWS):
    return rows[:limit] + (["  … +%d" % (len(rows) - limit)] if len(rows) > limit else [])


def format_why(result, lang=None):
    t = lambda k, **kw: i18n.t(k, lang, **kw)
    lines = [t("skills.why.head", job=result["job"], level=result["level"], stakes=result["traits"].get("stakes", "routine"))]
    if result["primary"]:
        lines.append(t("skills.why.primary", skill=result["primary"], why=t("skills.why.code." + (result.get("why_code") or "score"),
                       skill=result["primary"], score=result.get("why_score"), top=result.get("why_top"))))
        load = result.get("load") or "full"
        if load != "full":
            lines.append(t("skills.why.load_" + load.replace("-", "_"), id=result["primary"]))
    else:
        gap = result["gap"]
        lines.append(t("skills.why.gap", fallback=", ".join(gap["fallback"]), row=gap["watch_row"]))
    for c in result.get("checkers") or []:
        lines.append(t("skills.why.checker", job=c["job"], skill=c["skill"]))
    if result.get("provisional"):
        lines.append(t("skills.why.provisional"))
    elif result.get("bakeoff") == "needed":
        lines.append(t("skills.why.bakeoff"))
    if result.get("debate"):
        lines.append(t("skills.why.debate"))
    if result.get("persona_runs") is not None:
        lines.append(t("skills.why.runs_none") if not result["persona_runs"] else t("skills.why.runs", n=result["persona_runs"]))
    for rec in result.get("recommendations") or []:
        text = rec.get("text_es") if (lang or i18n.detect()) == "es" and rec.get("text_es") else rec["text"]
        lines.append(t("skills.why.recommend", text=text) + (" " + t("skills.why.needs_d39") if rec.get("needs_d39") else ""))
    if result["rejected"]:
        lines.append(t("skills.why.rejected_head"))
        lines += _cap(["  %s%s: %s" % (r["skill"], (" (%s)" % r["job"]) if r.get("job") else "", reason_text(r["reason"], lang))
                       for r in result["rejected"]], 12)
    if result["beat"]:
        lines.append(t("skills.why.beat_head"))
        lines += _cap(["  " + t("skills.why.beaten", skill=b["skill"], score=b.get("score"), pick=b.get("pick_score")) for b in result["beat"]], 8)
    if result["fallback"] and result["primary"]:
        lines.append(t("skills.why.fallback", chain=", ".join(result["fallback"])))
    if result.get("logged"):
        lines.append(t("skills.why.logged", path=result["logged"]))
    return "\n".join(lines)


def format_list(registry, job=None, status=None, traits=None, lang=None):
    t = lambda k, **kw: i18n.t(k, lang, **kw)
    rows = []
    traits = sel.normalise(traits)
    for e in registry["entries"]:
        if status and e.get("status") != status:
            continue
        fit = ""
        if job:
            _, row = sel.fit_row(registry, e, job, traits)
            if row is None:
                continue
            fit = str(row.get("fit", 0))
        verdict = ((e.get("security") or {}).get("verdict") or "-").split(" (")[0]
        enabled = e.get("enabled")
        rows.append("%-34s %-10s %-9s %-22s %-5s %s" % (
            e["id"][:34], e.get("status", "-"), "on" if enabled is True else ("part" if enabled else "off"), verdict[:22],
            (e.get("cost") or {}).get("class", "-"), fit))
    if not rows:
        return t("skills.none")
    head = t("skills.list.head")
    return "\n".join([t("skills.count", n=len(rows), version=registry.get("version", "?")), head] + _cap(rows, 200))


def format_show(registry, skill_id, lang=None):
    for e in registry["entries"]:
        if e["id"] == skill_id:
            shown = dict(e, sub_jobs=sorted(e.get("fits") or {}))     # D35: the jobs it competes for, read from the fit rows so the two cannot drift
            return json.dumps(shown, indent=2, ensure_ascii=False)
    raise sl.StudioError(i18n.t("skills.show_missing", lang, id=skill_id))


def format_check(findings, lang=None):
    t = lambda k, **kw: i18n.t(k, lang, **kw)
    if not findings:
        return t("skills.check.clean")
    lines = [t("skills.check.head", n=len(findings))]
    for f in findings:
        if f["kind"] == "env":
            note = ("  " + f["condition"]) if f.get("condition") else ""
            lines.append("  " + t("skills.check.env", id=f["id"], var=f["detail"]) + note)
        elif f["kind"] == "binary":
            lines.append("  " + t("skills.check.binary_" + f["detail"], id=f["id"]))
        else:
            lines.append("  " + t("skills.check." + f["kind"].replace("-", "_"), id=f["id"], detail=f["detail"]))
    return "\n".join(lines)


# --- load-plan (D44, D45): a printed plan, no file written ----------------------------------------------------------
def load_plan(registry, settings):
    """-> {'rows': [...], 'block': {skill: mode}, 'saved_chars': n}. Pure over the registry and the setting names it can see."""
    rows, block, saved = [], {}, 0
    overrides = settings.get("overrides") or {}
    for e in registry["entries"]:
        proposed = e.get("load_proposed")
        if not proposed:
            continue
        current = overrides.get(e["id"], "on" if e.get("load", "full") == "full" else e.get("load"))
        current = {"full": "on"}.get(current, current)
        unread = e.get("read") in (False, "description")
        note = []
        if unread:
            note.append("unread")
        scope = (e.get("install") or {}).get("scope")
        eligible = scope == "user"
        if not eligible:
            note.append("plugin")
        chars = int(e.get("description_chars") or 0)
        rows.append({"id": e["id"], "current": current, "proposed": proposed, "chars": chars, "why": e.get("load_reason", ""),
                     "note": note, "applies": eligible and current != proposed})
        if eligible and current != proposed:
            block[e["id"]] = proposed
            saved += max(0, chars - len(e["id"]))
    return {"rows": rows, "block": block, "saved_chars": saved}


def format_load_plan(plan, lang=None):
    t = lambda k, **kw: i18n.t(k, lang, **kw)
    if not plan["rows"]:
        return t("skills.load.none")
    lines = [t("skills.load.head", n=len(plan["block"]), chars=plan["saved_chars"], tokens=plan["saved_chars"] // 4)]
    for r in plan["rows"]:
        flags = (" [%s]" % ", ".join(r["note"])) if r["note"] else ""
        lines.append("  %-28s %s -> %s  %d chars  %s%s" % (r["id"], r["current"], r["proposed"], r["chars"], r["why"], flags))
    if plan["block"]:
        body = {"skillOverrides": dict(sorted(plan["block"].items()))}
        lines += [t("skills.load.block"), json.dumps(body, indent=2)[2:-2], t("skills.load.foot")]
    return "\n".join(lines)


# --- build (maintainer): the generated .md and the unread entries --------------------------------------------------
def render_md(registry):
    """skills-registry.md: one row per entry; never edited by hand."""
    lines = ["# Skills registry (generated)", "",
             "Generated by `budgit skills build` from `skills-registry.json` (version %s). Do not edit by hand." % registry.get("version", "?"), "",
             "| id | status | on | review | cost | pick for | avoid for |", "|---|---|---|---|---|---|---|"]
    for e in registry["entries"]:
        fits = e.get("fits") or {}
        best = sorted((k for k, v in fits.items() if int(v.get("fit", 0)) >= 2), key=lambda k: -int(fits[k]["fit"]))[:3]
        verdict = ((e.get("security") or {}).get("verdict") or "-").split(" (")[0]
        enabled = e.get("enabled")
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            cell(e["id"]), e.get("status", "-"), "on" if enabled is True else ("part" if enabled else "off"), cell(verdict),
            (e.get("cost") or {}).get("class", "-"), cell(", ".join(best) or "-"), cell(", ".join(e.get("not_for") or []) or "-")))
    return "\n".join(lines) + "\n"


def seed_unread(registry, claude=None):
    """Entries for installed skills that have none: `read: false`, listed, never a primary. -> number added."""
    claude = claude or claude_dir()
    settings = read_settings(None, claude)
    known = known_ids(registry)
    added = 0
    for s in installed_skills(claude, settings["plugins"]):
        if s["id"] in known:
            continue
        fm = frontmatter(os.path.join(s["path"], "SKILL.md"))
        desc = fm.get("description", "")
        home_rel = "~/.claude" + s["path"][len(claude):] if s["path"].startswith(claude) else s["path"]
        registry["entries"].append({
            "id": s["id"], "kind": "skill", "status": "installed", "enabled": False, "read": False, "role": [],
            "source": {"repo": None, "path": None, "pin": None, "version": fm.get("version"), "tree_sha256": None, "licence": None},
            "install": {"scope": s["scope"], "path": home_rel, "installed": None, "local_patches": []},
            "security": {"verdict": "unreviewed", "report": None, "reviewed": None, "conditions": [], "unchecked": ["not read"]},
            "flags": {}, "cost": {"class": 1, "agents": 0}, "fits": {}, "not_for": [], "fallback": [], "load": "full",
            "description": desc[:1536], "description_chars": len(desc[:1536]),
            "history": [], "uses": {"total": 0, "last": None}})
        added += 1
    return added


def build(path=None, claude=None, write=True):
    """Regenerate the .md from the public entries; add unread entries for installed skills the registry does not know to
    the user's private overlay, never to the shipped file. -> (added, md_path)."""
    base_path = registry_path(path)
    base = _read_registry_file(base_path)
    ov_path = overlay_path() if path is None else os.path.join(os.path.dirname(base_path), OVERLAY_NAME)
    overlay = load_overlay(ov_path)
    merged = merge_overlay(base, overlay)
    before = len(merged["entries"])
    added = seed_unread(merged, claude)
    md_path = os.path.join(os.path.dirname(base_path), "skills-registry.md")
    if write:
        if added:
            fresh = [dict(e, visibility="private") for e in merged["entries"][before:]]
            sl.atomic_write(ov_path, json.dumps({"entries": overlay["entries"] + fresh}, indent=1, ensure_ascii=False) + "\n")
        sl.atomic_write(md_path, render_md(base))
    return added, md_path


# --- command line --------------------------------------------------------------------------------------------------
def add_arguments(p):
    p.add_argument("action", nargs="?", default="list", help="list, show ID, why, check, build, load-plan")
    p.add_argument("rest", nargs="*")
    p.add_argument("--job", help="why/list: the job, e.g. design/build")
    p.add_argument("--traits", default="", help="why/list: key=value pairs, comma separated (surface, stakes, language, where, author)")
    p.add_argument("--status", help="list: only this status")
    p.add_argument("--registry", help="another registry file (tests, maintainers)")
    p.add_argument("--level", help="why: the budget level to plan for (ample, normal, tight, critical)")
    p.add_argument("--no-log", action="store_true", help="why: do not append to the selection log")
    p.add_argument("--json", action="store_true")


def main(argv, project=None, lang=None):
    """`budgit skills <list|show|why|check|build|load-plan>`; returns the exit code. argv is the part after `skills`."""
    import argparse
    p = argparse.ArgumentParser(prog="budgit skills", add_help=False)
    add_arguments(p)
    return run(p.parse_args(argv), project, lang)


def run(a, project=None, lang=None):
    registry = load_registry(a.registry)
    out = print
    if a.action == "list":
        out(format_list(registry, a.job, a.status, sel.parse_traits(a.traits), lang))
    elif a.action == "show":
        if not a.rest:
            raise sl.StudioError(i18n.t("skills.show_missing", lang, id=""))
        out(format_show(registry, a.rest[0], lang))
    elif a.action == "why":
        if not a.job:
            raise sl.StudioError(i18n.t("skills.why_need_job", lang))
        result, record = why(registry, a.job, sel.parse_traits(a.traits), project, level=a.level, log=not a.no_log)
        out(json.dumps(record, indent=2, ensure_ascii=False) if a.json else format_why(result, lang))
    elif a.action == "check":
        findings = check(registry, project)
        out(json.dumps(findings, indent=2) if a.json else format_check(findings, lang))
        return 1 if any(f["kind"] in ("drifted", "missing") for f in findings) else 0
    elif a.action == "build":
        added, md_path = build(a.registry)
        out(i18n.t("skills.build.done", lang, n=added, path=md_path))
    elif a.action == "load-plan":
        plan = load_plan(registry, read_settings(project))
        out(json.dumps(plan, indent=2) if a.json else format_load_plan(plan, lang))
    else:
        raise sl.StudioError(i18n.t("skills.unknown_action", lang, action=a.action))
    return 0
