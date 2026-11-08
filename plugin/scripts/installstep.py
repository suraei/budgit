"""R-712: the per-version install step (the design calls it upgrade.py; that name is taken by `budgit upgrade`, R-283 S4).
Run from the SessionStart hook; after a plugin update it repairs what Budgit itself owns outside the plugin folder (today: the
housekeeping launch agent), with nothing typed by the user.

The record `<home state>/install.json` = {"root", "version", "python", "steps": {step id: version done}, "at"}. Fast path (nothing
changed): one small read and two checks, no write, no subprocess, no launchctl. Slow path: a non-blocking lock, the record is
updated (the root only moves forward, and only to a folder Claude Code registered), then each step not yet done at this version runs.
A failed step shows one line, stays not-done and is retried at the next session start. A success is one line in hooks.log."""
import json
import os
import sys

import studiolib as sl

RECORD = "install.json"
LOCK = "upgrade.lock"
MAX_RECORD = 64 * 1024


class StepError(Exception):
    pass


def record_path(home=None):
    return os.path.join(home or sl.home_state(), RECORD)


def record_read(home=None):
    try:
        text = sl.read_text(record_path(home))
        data = json.loads(text[:MAX_RECORD])
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record_write(home, rec):
    sl.atomic_write(record_path(home), json.dumps(rec, indent=1, sort_keys=True) + "\n")


def _inside_plugin(path, root):
    return path.startswith(os.path.join(root, "")) or "/plugins/cache/" in path or "/plugins/marketplaces/" in path


def interpreter():
    """The interpreter a scheduled job may use: the resolved base interpreter, not a venv's first python3 on PATH."""
    return os.path.realpath(getattr(sys, "base_executable", None) or sys.executable)


def safe_interpreter(path):
    """A file owned by root or the current user and writable by neither group nor others (after resolving links)."""
    try:
        real = os.path.realpath(path)
        st = os.stat(real)
    except (OSError, TypeError, ValueError):
        return False
    import stat
    return stat.S_ISREG(st.st_mode) and st.st_uid in (0, os.getuid()) and not st.st_mode & 0o022


def _python(python, root, rec):
    if python and os.path.isfile(python) and not _inside_plugin(python, root) and safe_interpreter(python):
        return python
    old = rec.get("python")
    return old if isinstance(old, str) and old and os.path.isfile(old) and safe_interpreter(old) else ""


def _forward(version, rec):
    """The running root may take over the record: it is missing, its root is gone or no longer registered, or the running version is not older."""
    root = rec.get("root")
    if not isinstance(root, str) or not os.path.isfile(os.path.join(root, "bin", "budgit")):
        return True
    import updatelib
    if updatelib.registered_root(root) is None:
        return True                                      # R-712: a recorded root Claude Code no longer vouches for loses the record
    import housekeeping
    if not housekeeping.test_mode():
        uh = housekeeping.user_home()
        plugins = os.path.join(uh, ".claude", "plugins") if uh else ""
        if not (plugins and housekeeping.under_any(os.path.dirname(root), [plugins])):
            return True                                  # a recorded root outside the user's own plugin folder is not kept
    old = rec.get("version")
    if not (updatelib.valid_version(version) and updatelib.valid_version(old)):
        return False
    return updatelib._key(version) >= updatelib._key(old)


def record_update(root, version, python, trusted):
    """Make the record name this root (when it may) and write it if anything changed. -> the record now, or None when this root
    must not act (not trusted, or an older chat while the newer root still exists)."""
    home = sl.home_state()
    rec = record_read(home) or {}
    if not trusted or not _forward(version, rec):
        return None
    new = dict(rec)
    steps = rec.get("steps") if isinstance(rec.get("steps"), dict) else {}
    new.update(root=root, version=version, python=_python(python, root, rec), steps=steps)
    if {k: v for k, v in new.items() if k != "at"} != {k: v for k, v in rec.items() if k != "at"}:
        new["at"] = sl.now()
        _record_write(home, new)
    else:
        new["at"] = rec.get("at")
    return new


def _log(home, text):
    try:
        if os.path.islink(home):
            return                                       # never write through a linked home
        sl.append_text(os.path.join(home, "hooks.log"), "%s upgrade: %s\n" % (sl.now(), text))
    except Exception:
        pass


def owned_jobs():
    """Every plist label Budgit owns outside the plugin folder. Later jobs join this list."""
    import housekeeping
    return [housekeeping.JOB]


def step_jobs_v1(root, python):
    """Move every existing job of ours onto the fixed launcher. -> the labels that changed. Creates no plist."""
    if sys.platform != "darwin" and not sl.env("LAUNCH_AGENTS_DIR"):
        return []
    import housekeeping
    return [job["label"] for job in owned_jobs() if housekeeping.repair_job(job, root, python) == "changed"]


def step_checks_v1(root, python):
    """R-785: an update leaves existing projects with an empty checks list. Write a proposal (read-only detection) for
    each; the next session in that project asks one card. Creates nothing outside each project's .budgit folder."""
    import checksdetect
    checksdetect.scan_all()
    return []


STEPS = [("jobs-v1", step_jobs_v1), ("checks-v1", step_checks_v1)]


def _fast(rec, version, root):
    if not rec or rec.get("version") != version or rec.get("root") != root:
        return False
    python = rec.get("python")
    if not isinstance(python, str) or (python and not os.path.exists(python)):
        return False
    steps = rec.get("steps")
    return isinstance(steps, dict) and all(steps.get(sid) == version for sid, _fn in STEPS)


def run(root, version, python):
    """-> None (nothing to say) or the one failure line for the user."""
    if not version:
        return None
    try:
        import fcntl                                     # not on Windows: scheduled jobs are macOS-only, so this is a silent no-op there
    except ImportError:
        return None
    home = sl.home_state()
    import housekeeping
    if not housekeeping.test_mode() and housekeeping.real_agents_dir() and not housekeeping.state_home_is_default(home):
        return None                                      # a project-set $HOME or BUDGIT_HOME never gets a record or launcher
    if _fast(record_read(home), version, root):
        return None
    if sl.vendored():
        return None
    import updatelib
    if updatelib.registered_root(root) is None:
        return None
    try:
        if os.path.islink(home):
            raise StepError("a symlink where the home goes")
        os.makedirs(home, exist_ok=True)
        fd = os.open(os.path.join(home, LOCK), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except (OSError, StepError) as exc:
        return _failed(home, "record", str(exc))
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None                                  # another session is doing the work
        return _slow(home, root, version, python)
    finally:
        os.close(fd)


def _failed(home, step, why):
    _log(home, "step %s failed: %s" % (step, why))
    import i18n
    return i18n.t("upgrade.failed", why=why)


def _slow(home, root, version, python):
    before = record_read(home) or {}
    try:
        rec = record_update(root, version, python, True)
    except OSError as exc:
        return _failed(home, "record", str(exc))
    if rec is None:
        return None
    moved = (before.get("root"), before.get("python")) != (rec.get("root"), rec.get("python"))
    steps = dict(rec.get("steps") or {})
    failures, repaired = [], []
    for sid, fn in STEPS:
        if steps.get(sid) == version and not moved:
            continue
        try:
            repaired += fn(rec["root"], rec["python"])
            steps[sid] = version
        except Exception as exc:                          # a step never blocks the others
            why = str(exc) or exc.__class__.__name__
            failures.append(why)
            _log(home, "step %s failed: %s" % (sid, why))
    if steps != (rec.get("steps") or {}):
        rec["steps"] = steps
        rec["at"] = sl.now()
        try:
            _record_write(home, rec)
        except OSError as exc:
            failures.append(str(exc))
    if repaired:
        _log(home, "jobs-v1 re-registered %s for %s" % (", ".join(repaired), version))
    if failures:
        import i18n
        return i18n.t("upgrade.failed", why="; ".join(failures))
    return None
