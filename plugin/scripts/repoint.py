"""R-332: point Budgit's launch agents at a new project root (the folder moved). `budgit jobs repoint --root <abs> [--dry-run]`.

ONE parameter, the root, flows into the launcher text (housekeeping.launcher_text) and the plists (housekeeping.desired_plist /
plist_text); nothing here names a path. Every input of the work (state home, LaunchAgents folder, launchctl, uid, platform, python)
is a parameter of `repoint`, so tests use temp folders and a fake launchctl and never read an environment variable.
Each file is written atomically; a replaced file is first copied next to itself as <name>.bak-<stamp> (never deleted)."""
import difflib
import json
import os
import plistlib
import shutil
import time

import studiolib as sl


class PlanError(Exception):
    pass


def preflight(items, uid):
    """R-332: every refusal over ALL files, before the first write. -> the first problem as text, or None."""
    for it in items:
        path = it["path"]
        if os.path.islink(path):
            return "%s is a symlink" % path
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            return "%s cannot be checked (%s)" % (path, exc.__class__.__name__)
        if st.st_uid not in (0, uid):
            return "%s is owned by another user" % path
    return None


def plugin_root(root, exists=os.path.isfile):
    """-> (the folder that holds bin/budgit, found). `root` is that folder, or the project folder that holds plugin/bin/budgit.
    When neither exists yet (a dry run before the move) -> (root/plugin, False)."""
    if exists(os.path.join(root, "bin", "budgit")):
        return root, True
    inner = os.path.join(root, "plugin")
    return inner, exists(os.path.join(inner, "bin", "budgit"))


def _read(path):
    try:
        return sl.read_text(path)
    except OSError:
        return None


def build_plan(root, state_home, agents, python, record=None):
    """-> list of dicts (path, old, new, mode, label). `label` is set for a launch agent. Only files whose text changes are listed,
    except a plist that exists keeps an entry with old == new so it is still reloaded."""
    import housekeeping
    launcher = housekeeping.launcher_path(state_home)
    items = [{"path": launcher, "old": _read(launcher), "new": housekeeping.launcher_text(root, python), "mode": 0o700, "label": None}]
    for name, job in housekeeping.JOBS.items():
        path = os.path.join(agents, job["label"] + ".plist")
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    current = plistlib.load(f)
            except (OSError, ValueError) as exc:            # InvalidFileException and ExpatError are ValueError / Exception subclasses
                raise PlanError("%s cannot be read as a plist (%s)" % (path, exc.__class__.__name__))
            except Exception as exc:
                raise PlanError("%s cannot be read as a plist (%s)" % (path, exc.__class__.__name__))
            if not isinstance(current, dict):
                raise PlanError("%s is not a plist dictionary" % path)
            new = plistlib.dumps(housekeeping.desired_plist(job, current, launcher, state_home)).decode("utf-8")
        elif name == "cleanup":                                    # R-871: the deterministic daily housekeeping job always exists
            new = housekeeping.plist_text(job, launcher, None, state_home)
        else:
            continue                                               # keepalive and stand-up are opt-in: never created here
        items.append({"path": path, "old": _read(path), "new": new, "mode": None, "label": job["label"]})
    if record is not None:
        rec_path = os.path.join(state_home, "install.json")
        text = json.dumps(dict(record, root=root), indent=1, sort_keys=True) + "\n"
        items.append({"path": rec_path, "old": _read(rec_path), "new": text, "mode": None, "label": None})
    return items


def render_diff(items):
    out = []
    for it in items:
        if it["old"] == it["new"]:
            out.append("= %s (no change)" % it["path"])
            continue
        old = (it["old"] or "").splitlines(keepends=True)
        new = it["new"].splitlines(keepends=True)
        tag = "(new file)" if it["old"] is None else ""
        out.append("~ %s %s" % (it["path"], tag))
        out.extend(line.rstrip("\n") for line in difflib.unified_diff(old, new, "old", "new", n=1))
    return out


def _backup(path, stamp):
    target = "%s.bak-%s" % (path, stamp)
    n = 1
    while os.path.lexists(target):
        n += 1
        target = "%s.bak-%s-%d" % (path, stamp, n)
    shutil.copy2(path, target)
    return target


def repoint(root, state_home, agents, python, launchctl, uid, platform, dry_run=False, now=None, record=None, registered=None):
    """-> (exit code, text). launchctl is a callable (*args) -> return code. 64 off macOS, 1 on a missing root or a failed reload."""
    if platform != "darwin":
        return 64, ("budgit jobs repoint: only macOS launchd is supported here. Linux (systemd user timer or cron) and "
                    "Windows (schtasks) are not built yet; nothing was changed.")
    if not isinstance(root, str) or not os.path.isabs(root) or any(c in root for c in "\n\r\x00"):
        return 64, "budgit jobs repoint: --root must be an absolute path on one line"
    root = os.path.normpath(root)
    plug, found = plugin_root(root)
    if not found and not dry_run:
        return 1, "budgit jobs repoint: no bin/budgit under %s or %s/plugin; nothing was changed" % (root, root)
    try:
        items = build_plan(plug, state_home, agents, python, record)
    except PlanError as exc:
        return 1, "budgit jobs repoint: %s; nothing was changed" % exc
    problem = preflight(items, uid)
    if problem:
        return 1, "budgit jobs repoint: %s; nothing was changed" % problem
    lines = ["root: %s" % plug] + ([] if found else ["warning: bin/budgit is not there yet (apply refuses until it is)"])
    mismatch = registered is not None and not registered(plug)
    if mismatch:
        lines.append("warning: Claude Code has not registered %s as the plugin folder (the registry points elsewhere). "
                     "Fix: in Claude Code run /plugin and re-add or update the budgit plugin from %s, then run `budgit jobs repoint --root %s` again. "
                     "This tool does not change the registry." % (plug, plug, root))
    lines += render_diff(items)
    labels = [it["label"] for it in items if it["label"]]
    if dry_run:
        for label in labels:
            lines.append("would: launchctl bootout gui/%d/%s, then bootstrap" % (uid, label))
        lines.append("dry run: nothing was written")
        return 0, "\n".join(lines)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now if now is not None else time.time()))
    for it in items:
        if it["old"] == it["new"]:
            continue
        os.makedirs(os.path.dirname(it["path"]), exist_ok=True)
        if it["old"] is not None:
            lines.append("backup: " + _backup(it["path"], stamp))
        sl.atomic_write(it["path"], it["new"], it["mode"])
        lines.append("written: " + it["path"])
    failed = []
    for it in items:
        if not it["label"]:
            continue
        launchctl("bootout", "gui/%d/%s" % (uid, it["label"]))
        if launchctl("bootstrap", "gui/%d" % uid, it["path"]) != 0:
            failed.append(it["label"])
        else:
            lines.append("loaded: " + it["label"])
    if failed:
        lines.append("failed to load: " + ", ".join(failed))
        return 1, "\n".join(lines)
    return (2 if mismatch else 0), "\n".join(lines)


def run_real(root, dry_run):
    """The CLI entry: reads the real machine here and nowhere else, then calls repoint."""
    import sys
    import housekeeping
    import installstep
    if sl.env("HOME"):
        return 1, "budgit jobs repoint: BUDGIT_HOME is set; a job runs only from the default home in your user folder"
    rec = installstep.record_read(sl.home_state()) or {}
    python = rec.get("python") or ""
    return repoint(root, sl.home_state(), housekeeping.agents_dir(), python, _real_launchctl, os.getuid(), sys.platform,
                   dry_run, record=rec or None, registered=_real_registered)


def _real_registered(plug):
    """The same predicate `job on` uses (housekeeping.schedule): the user's Claude registry, never $HOME's."""
    import housekeeping
    import updatelib
    uh = housekeeping.user_home()
    return bool(uh) and updatelib.registered_root(plug, os.path.join(uh, ".claude")) is not None


def _real_launchctl(*args):
    import housekeeping
    return housekeeping._launchctl(*args, timeout=housekeeping.LAUNCHCTL_TIMEOUT)
