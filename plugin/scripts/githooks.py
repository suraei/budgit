"""R-1042: `budgit githooks install|status`. Puts a commit-msg and a pre-push hook (templates in plugin/githooks/) into a repo's
real hooks folder so git itself rejects an AI attribution trailer, whatever the form of the commit. The checks live in
scripts/githook_check.py and use attribution.first_match, the same pattern source as the PreToolUse guard.

Where: `git rev-parse --git-common-dir`/hooks (worktrees share it), or core.hooksPath when it is set.
Never overwrites: an existing hook of the user's is moved to <name>.local and the stub runs it first (its exit code passes
through). A Budgit header line marks our file, so a second install changes nothing. The plugin path is written into the stub
at install time; when that folder is gone the hook fails closed with a message. Local only: nothing is sent anywhere.
Status per repo: installed, chained, missing or foreign."""
import os
import subprocess
import sys

import studiolib as sl

HERE = os.path.dirname(os.path.realpath(__file__))
PLUGIN = os.path.dirname(HERE)
TEMPLATES = os.path.join(PLUGIN, "githooks")
HOOKS = ("commit-msg", "pre-push")
MARK = "# budgit-githook "
GIT_TIMEOUT_S = 30


class Refused(sl.StudioError):
    """The install cannot pick a plugin path that will still exist later (r2): nothing is written."""


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, errors="replace",
                          timeout=GIT_TIMEOUT_S)


def stable_plugin(plugin):
    """R-1042 r2: the plugin dir to bake into a hook. A checkout that is a linked worktree (its top-level `.git` is a file) is
    deleted when the work is done, so its plugin dir is never baked: the same relative dir of the MAIN checkout is used, and
    when that has no scripts/githook_check.py the install is refused with a message. Any other folder is used as it is."""
    here = os.path.realpath(plugin)
    try:
        top = _git(here, "rev-parse", "--show-toplevel")
        if top.returncode or not top.stdout.strip():
            return here
        top = os.path.realpath(top.stdout.strip())
        if not os.path.isfile(os.path.join(top, ".git")):
            return here
        common = _git(here, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return here
    main = os.path.dirname(os.path.realpath(common)) if os.path.basename(common.rstrip(os.sep)) == ".git" else None
    candidate = os.path.join(main, os.path.relpath(here, top)) if main else None
    if not candidate or not os.path.isfile(os.path.join(candidate, "scripts", "githook_check.py")):
        raise Refused("githooks install refused: this plugin is inside a linked worktree (%s), which is removed when the work "
                      "ends, and the main checkout has no %s to point the hooks at. Merge it, or run the install from the "
                      "installed plugin." % (os.path.basename(top), os.path.join("plugin", "scripts", "githook_check.py")))
    return candidate


def hooks_dir(repo):
    """(hooks folder, how it was found) or None when `repo` is not inside a git repo."""
    try:
        r = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if r.returncode:
            r = _git(repo, "rev-parse", "--git-common-dir")
            if r.returncode:
                return None
            common = r.stdout.strip()
            common = common if os.path.isabs(common) else os.path.join(os.path.abspath(repo), common)
        else:
            common = r.stdout.strip()
        cfg = _git(repo, "config", "--type=path", "--get", "core.hooksPath")
    except (OSError, subprocess.SubprocessError):
        return None
    value = cfg.stdout.strip() if cfg.returncode == 0 else ""
    if value:
        if not os.path.isabs(value):                      # git runs hooks from the top of the work tree
            top = _git(repo, "rev-parse", "--show-toplevel")
            base = top.stdout.strip() if top.returncode == 0 and top.stdout.strip() else common
            value = os.path.join(base, value)
        return os.path.normpath(value), "core.hooksPath"
    return os.path.join(os.path.normpath(common), "hooks"), "git dir"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def render(name, plugin=None, python=None):
    """The stub text for hook `name`, with the plugin path and the python of THIS install written in."""
    template = _read(os.path.join(TEMPLATES, name))
    if template is None:
        raise OSError("hook template missing: %s" % os.path.join(TEMPLATES, name))
    quote = lambda s: s.replace("'", "'\\''")
    return template.replace("@PLUGIN@", quote(stable_plugin(plugin or PLUGIN))).replace(
        "@PYTHON@", quote(python or sys.executable or "python3"))


def classify(hooks, name):
    """-> (state, stale): state is installed, chained, missing or foreign; stale = ours but its plugin folder is gone."""
    path = os.path.join(hooks, name)
    if not os.path.lexists(path):
        return "missing", False
    text = None if os.path.islink(path) else _read(path)
    if text is None or (MARK + name) not in text[:400]:
        return "foreign", False
    stale = False
    for line in text.splitlines():
        if line.startswith("BUDGIT_PLUGIN='"):
            plugin = line[len("BUDGIT_PLUGIN='"):-1].replace("'\\''", "'")
            stale = not os.path.isfile(os.path.join(plugin, "scripts", "githook_check.py"))
    return ("chained" if os.path.lexists(path + ".local") else "installed"), stale


def status_repo(repo):
    found = hooks_dir(repo)
    if found is None:
        return {"repo": repo, "hooks_dir": None, "via": None, "state": "not a git repo", "hooks": {}, "stale": False}
    hooks, via = found
    states, stale = {}, False
    for name in HOOKS:
        states[name], s = classify(hooks, name)
        stale = stale or s
    values = list(states.values())
    state = ("foreign" if "foreign" in values else "missing" if "missing" in values
             else "chained" if "chained" in values else "installed")
    chained = None                                   # R-1046: name what commit-msg.local is, when it is the watermark hook
    local = os.path.join(hooks, "commit-msg.local")
    if states.get("commit-msg") == "chained" and not os.path.islink(local):
        head = (_read(local) or "").split("\n", 2)
        if len(head) > 1 and head[1].startswith("# budgit-watermark mode="):
            chained = "watermark"
    return {"repo": repo, "hooks_dir": hooks, "via": via, "state": state, "hooks": states, "stale": stale, "chained": chained}


def install_repo(repo, dry_run=False, plugin=None, python=None):
    """Install both hooks. -> {"repo", "hooks_dir", "via", "hooks": {name: action}} where action is one of install, chain,
    update, unchanged, conflict (a symlink, or a <name>.local that already exists: nothing is changed there)."""
    found = hooks_dir(repo)
    if found is None:
        return {"repo": repo, "hooks_dir": None, "via": None, "hooks": {}, "state": "not a git repo"}
    hooks, via = found
    actions = {}
    for name in HOOKS:
        path = os.path.join(hooks, name)
        state, _stale = classify(hooks, name)
        want = render(name, plugin, python)
        if state == "missing":
            action = "install"
        elif state == "foreign":
            action = "conflict" if os.path.islink(path) or os.path.lexists(path + ".local") else "chain"
        else:
            action = "unchanged" if _read(path) == want else "update"
        actions[name] = action
        if dry_run or action in ("unchanged", "conflict"):
            continue
        os.makedirs(hooks, exist_ok=True)
        if action == "chain":
            os.replace(path, path + ".local")             # a move, never a delete: the user's hook runs first from there
        tmp = path + ".budgit-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(want)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    return {"repo": repo, "hooks_dir": hooks, "via": via, "hooks": actions, "state": None}


def targets(project, all_projects):
    """-> [(path, protected, label)]. --all = every project of the portfolio list (the list `upgrade --all`, `sweep --all` and
    `daily --all` read), protected ones included; a protected one is labelled by its portfolio id and never by its path."""
    import livework
    import portfolio
    if not all_projects:
        return [(project, False, project)]
    data = portfolio.load() or portfolio.seed()
    out, seen = [], set()
    for entry in data["projects"]:
        path = entry["path"]
        if path in seen:
            continue
        seen.add(path)
        protected = bool(livework.is_private(path))
        out.append((path, protected, portfolio.project_id(path) if protected else path))
    return out


def run(op, project, all_projects=False, dry_run=False, out=None, plugin=None, python=None):
    """Print one line per repo; -> exit code (0, or 1 when a repo could not be handled)."""
    out = out or sys.stdout
    bad = 0
    done = set()
    for path, protected, label in targets(project, all_projects):
        try:
            res = install_repo(path, dry_run, plugin, python) if op == "install" else status_repo(path)
        except (OSError, subprocess.SubprocessError) as exc:
            out.write("%s | error: %s\n" % (label, type(exc).__name__))
            bad = 1
            continue
        key = res["hooks_dir"] or path
        if res["hooks_dir"] and key in done:
            out.write("%s | same hooks folder as an earlier repo (worktree): skipped\n" % label)
            continue
        done.add(key)
        where = "" if protected else " | hooks dir: %s (%s)" % (res["hooks_dir"], res["via"])
        if res["hooks_dir"] is None:
            out.write("%s | skipped: not a git repo\n" % label)
        elif op == "install":
            verb = "dry run, nothing changed" if dry_run else "done"
            out.write("%s%s | %s | %s\n" % (label, where, verb, ", ".join("%s: %s" % kv for kv in res["hooks"].items())))
            if "conflict" in res["hooks"].values():
                bad = 1
        else:
            note = " (plugin folder gone: re-run `budgit githooks install`)" if res["stale"] else ""
            shown = "installed, chained: %s" % res["chained"] if res.get("chained") and res["state"] == "chained" else res["state"]
            out.write("%s%s | %s (%s)%s\n" % (label, where, shown, ", ".join(
                "%s: %s" % kv for kv in res["hooks"].items()), note))
    return bad
