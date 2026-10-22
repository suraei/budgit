"""budgit vendor — copy the Budgit runtime into a project so Claude Code cloud sessions (claude.ai/code) get the guards.

Cloud sessions load no plugins (code.claude.com/docs/en/cloud-environments#what-carries-over-from-your-setup) but DO
load the repo's committed .claude/settings.json hooks, .claude/agents/, .claude/skills/ and CLAUDE.md. So this writes:
  .claude/budgit/{scripts,bin,VERSION}   the runtime hook.py and the CLI need (no tests, nothing from .budgit/)
  .claude/agents/budgit-<name>.md        the plugin agents; in-repo agents take their identity from `name:`
  .claude/skills/budgit-kickoff/         the workflow skill (agent names rewritten to budgit-<name>)
  .claude/settings.json                  our hooks merged in, marked by their command path; each one is a no-op unless
                                         CLAUDE_CODE_REMOTE=true, so locally (plugin installed) nothing fires twice
Stdlib only. Everything is staged in a temp dir and scanned with `budgit secrets` before the project is touched.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile

import studiolib as sl

MARK = ".claude/budgit/scripts/hook.py"           # a hook command containing this is ours
OLD_MARKS = (".claude/studio/scripts/hook.py",)    # R-120: copies vendored before 0.12 (replaced on re-vendor)
OLD_RUNTIME, OLD_SKILL = "studio", "studio-kickoff"
GUARD = '[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0; '
RUN = 'env -u PYTHONDONTWRITEBYTECODE python3 "$CLAUDE_PROJECT_DIR/' + MARK + '" '
SKILL = "budgit-kickoff"
_PLUGIN_ARG = re.compile(r'/scripts/hook\.py"\s+(\S+)\s*$')
_TEXT_EXTS = {".md", ".txt", ".json"}
CLOUD_NOTE = ("> Claude Code cloud (claude.ai/code): this is the vendored copy. The Budgit agents are named "
              "`budgit-verifier`, `budgit-scout-reader`, `budgit-researcher`, `budgit-plan-critic`, "
              "`budgit-security-reviewer`, `budgit-persona-sim` (dash, not `budgit:X`); the CLI is "
              "`.claude/budgit/bin/budgit`.\n")


def plugin_root():
    return sl.studio_root()


def plugin_version():
    path = os.path.join(plugin_root(), ".claude-plugin", "plugin.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f)["version"])
    except (OSError, ValueError, KeyError):
        raise sl.StudioError("cannot read the plugin version (%s): run `budgit vendor` from the Budgit plugin, "
                             "not from a vendored copy" % path)


def _git(path, *args):
    r = subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _is_studio_repo(project):
    top = _git(plugin_root(), "rev-parse", "--show-toplevel")
    if top and os.path.realpath(top) == project:
        return True
    try:
        with open(os.path.join(project, "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            return json.load(f).get("name") in ("budgit", "studio")
    except (OSError, ValueError, AttributeError):
        return False


def _check_target(project):
    if not os.path.isdir(project):
        raise sl.StudioError("%s is not a directory" % project)
    top = _git(project, "rev-parse", "--show-toplevel")
    if not top:
        raise sl.StudioError("%s is not a git repository (cloud sessions only load committed files)" % project)
    if os.path.realpath(top) != project:
        raise sl.StudioError("%s is inside the git repo %s: vendor at the repo root (cloud sessions read "
                             ".claude/ there)" % (project, top))
    if _is_studio_repo(project):
        raise sl.StudioError("%s is the Budgit repo itself: it runs the plugin, never a vendored copy" % project)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _rename_agents(text):
    return sl.dash_names(text)


def _with_note(text):
    """Insert CLOUD_NOTE right after the YAML frontmatter (or at the top when there is none)."""
    m = re.match(r"---\n.*?\n---\n", text, re.S)
    cut = m.end() if m else 0
    return text[:cut] + "\n" + CLOUD_NOTE + text[cut:]


def _agent_doc(text, name):
    head, sep, body = text.partition("\n---\n")
    if not text.startswith("---\n") or not sep:
        raise sl.StudioError("agent %s has no frontmatter" % name)
    head, n = re.subn(r"(?m)^name:\s*%s\s*$" % re.escape(name), "name: budgit-" + name, head, count=1)
    if n != 1:
        raise sl.StudioError("agent %s: frontmatter name is not %r" % (name, name))
    return _with_note(_rename_agents(head + sep + body))


def _skill_source():
    root = plugin_root()
    for base in (os.path.join(root, "skills"), os.path.join(os.path.dirname(root), "skills"),
                 os.path.expanduser("~/.claude/skills")):
        if os.path.isfile(os.path.join(base, SKILL, "SKILL.md")):
            return os.path.join(base, SKILL)
    return None


def _copy(src, dst, mode=None):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode if mode is not None else 0o644)


def _write(dst, text):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def stage(stage_dir, version):
    """Build the whole .claude/ payload under stage_dir. Returns (files, warnings)."""
    root = plugin_root()
    warnings = []
    runtime = os.path.join(stage_dir, "budgit")
    scripts = os.path.join(root, "scripts")
    for name in sorted(os.listdir(scripts)):
        if name.endswith(".py") and os.path.isfile(os.path.join(scripts, name)):
            _copy(os.path.join(scripts, name), os.path.join(runtime, "scripts", name))
    _copy(os.path.join(root, "bin", "budgit"), os.path.join(runtime, "bin", "budgit"), 0o755)
    _write(os.path.join(runtime, "VERSION"), version + "\n")
    _write(os.path.join(runtime, ".gitignore"), "__pycache__/\n*.pyc\n")     # hooks run with bytecode writing on
    agents = os.path.join(root, "agents")
    for fname in sorted(os.listdir(agents)):
        if fname.endswith(".md"):
            name = fname[:-3]
            _write(os.path.join(stage_dir, "agents", "budgit-%s.md" % name), _agent_doc(_read(os.path.join(agents, fname)), name))
    skill = _skill_source()
    if skill is None:
        warnings.append("the %s skill was not found (looked next to the plugin and in ~/.claude/skills): skipped" % SKILL)
    else:
        for d, dirs, files in os.walk(skill):
            dirs[:] = sorted(x for x in dirs if x not in (".git", "__pycache__"))
            for f in sorted(files):
                src, rel = os.path.join(d, f), os.path.relpath(os.path.join(d, f), skill)
                dst = os.path.join(stage_dir, "skills", SKILL, rel)
                if os.path.splitext(f)[1] in _TEXT_EXTS:
                    text = _rename_agents(_read(src))
                    _write(dst, _with_note(text) if rel == "SKILL.md" else text)
                else:
                    _copy(src, dst)
    files = []
    for d, _, names in os.walk(stage_dir):
        files += [os.path.relpath(os.path.join(d, n), stage_dir) for n in names]
    return sorted(files), warnings


def _owner_config(stage_dir, project):
    """R-52 N2: the cloud copy gets ONLY the origin owner's noreply entry (the owner's accounts are never listed side by
    side in a project repo); an owner outside the user's map gets no config file."""
    owner = (sl._GITHUB_OWNER.search(_git(project, "remote", "get-url", "origin") or "") or [None, None])[1]
    entry = (sl.load_config().get("github_noreply") or {}).get(owner)
    if not entry:
        return []
    _write(os.path.join(stage_dir, "budgit", "config.json"),
           json.dumps({"github_noreply": {owner: entry}, "default_github_account": owner}, indent=2) + "\n")
    return [os.path.join("budgit", "config.json")]


def scan_staged(stage_dir):
    """`budgit secrets` over what is about to be written; any finding or tool failure aborts."""
    import secretscan
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = secretscan.scan(stage_dir)
    if rc:
        tail = "\n".join(buf.getvalue().strip().splitlines()[-12:])
        raise sl.StudioError("secret scan of the files to vendor failed (exit %s) — nothing was written:\n%s" % (rc, tail))
    return buf.getvalue()


def hook_groups():
    """The plugin's hooks.json re-expressed as cloud-only commands: {event: [group, ...]}."""
    path = os.path.join(plugin_root(), "hooks", "hooks.json")
    try:
        config = json.loads(_read(path))["hooks"]
    except (OSError, ValueError, KeyError):
        raise sl.StudioError("cannot read %s" % path)
    out = {}
    for event, groups in config.items():
        for group in groups:
            new = {k: v for k, v in group.items() if k != "hooks"}
            new["hooks"] = []
            for h in group["hooks"]:
                m = _PLUGIN_ARG.search(h.get("command", ""))
                if not m:
                    raise sl.StudioError("hooks.json %s: cannot vendor command %r" % (event, h.get("command")))
                new["hooks"].append(dict(h, command=GUARD + RUN + m.group(1)))
            out.setdefault(event, []).append(new)
    return out


def _is_ours(group):
    return any(m in (h.get("command") or "") for h in group.get("hooks") or [] if isinstance(h, dict)
               for m in (MARK,) + OLD_MARKS)


def merge_settings(settings, groups):
    """Our hook groups into settings["hooks"]; every other key and hook stays. Re-runs replace only ours, in place."""
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
        raise sl.StudioError(".claude/settings.json is not the expected shape (an object with an object `hooks`)")
    hooks = settings.setdefault("hooks", {})
    for event in list(hooks):
        if not isinstance(hooks[event], list):
            raise sl.StudioError(".claude/settings.json: hooks.%s is not a list" % event)
        mine = [g for g in hooks[event] if isinstance(g, dict) and _is_ours(g)]
        if mine and event not in groups:                   # an event the plugin no longer hooks
            hooks[event] = [g for g in hooks[event] if g not in mine]
            if not hooks[event]:
                del hooks[event]
    for event, new in groups.items():
        current = hooks.get(event, [])
        keep, placed = [], False
        for g in current:
            if isinstance(g, dict) and _is_ours(g):
                if not placed:
                    keep.extend(new)
                    placed = True
            else:
                keep.append(g)
        hooks[event] = keep if placed else keep + new
    return settings


def check(project):
    """(0, '') when the vendored VERSION equals the plugin's, else (1, one line)."""
    project = os.path.realpath(project)
    want = plugin_version()
    have = None
    for runtime in ("budgit", OLD_RUNTIME):
        try:
            have = _read(os.path.join(project, ".claude", runtime, "VERSION")).strip()
            break
        except OSError:
            continue
    if have is None:
        return 1, "budgit vendor: %s is not vendored — re-vendor: budgit vendor %s" % (project, project)
    if have == want:
        return 0, ""
    return 1, "budgit vendor: vendored v%s but the plugin is v%s — re-vendor: budgit vendor %s" % (have, want, project)


def vendor(project):
    """Write the vendored runtime into project/.claude. Returns the summary text."""
    project = os.path.realpath(project)
    _check_target(project)
    version = plugin_version()
    claude = os.path.join(project, ".claude")
    settings_path = os.path.join(claude, "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        try:
            settings = json.loads(_read(settings_path))
        except ValueError as exc:
            raise sl.StudioError("%s is not valid JSON (%s): left untouched" % (settings_path, exc))
    merged = merge_settings(settings, hook_groups())
    stage_dir = tempfile.mkdtemp(prefix="budgit-vendor-")
    try:
        files, warnings = stage(stage_dir, version)
        files += _owner_config(stage_dir, project)
        scan_staged(stage_dir)
        for runtime in ("budgit", OLD_RUNTIME):                                  # wholly ours: replaced, never merged
            shutil.rmtree(os.path.join(claude, runtime), ignore_errors=True)
        for skill_dir in (SKILL, OLD_SKILL):
            shutil.rmtree(os.path.join(claude, "skills", skill_dir), ignore_errors=True)
        for agent in sl.AGENT_NAMES:                                              # the pre-0.12 `studio-X` copies
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(claude, "agents", "studio-%s.md" % agent))
        for rel in files:
            dst = os.path.join(claude, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(stage_dir, rel), dst)
            os.chmod(dst, os.stat(os.path.join(stage_dir, rel)).st_mode & 0o777)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    _write(settings_path, json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    ignored = subprocess.run(["git", "-C", project, "check-ignore", "-q", ".claude/budgit/VERSION"]).returncode == 0
    lines = ["budgit vendor: v%s -> %s" % (version, claude),
             "  runtime  .claude/budgit/ (%d scripts + CLI + VERSION)" % sum(1 for f in files if f.startswith("budgit/scripts/")),
             "  agents   %s" % ", ".join(sorted(os.path.basename(f)[:-3] for f in files if f.startswith("agents/"))),
             "  skill    .claude/skills/%s/ (%d files)" % (SKILL, sum(1 for f in files if f.startswith("skills/"))),
             "  hooks    %d merged into .claude/settings.json (run only when CLAUDE_CODE_REMOTE=true)"
             % sum(len(g["hooks"]) for gs in hook_groups().values() for g in gs)]
    lines += ["  warning  " + w for w in warnings]
    if ignored:
        lines.append("  warning  .claude/budgit is git-ignored here: the cloud will not see it (fix .gitignore)")
    lines.append("Now commit and push it (cloud sessions only see pushed commits): "
                 "git add .claude && git commit -m 'Vendor Budgit runtime' && git push")
    return "\n".join(lines)
