"""R-788 steps 6-7: the opt-in background jobs (keep-alive, stand-up). Turning one on needs a recorded yes (setup-answers.json).
The precheck uses no model tokens: it skips on a critical budget (stand-up: tight too), a busy machine, a missing `claude` or nothing to do,
logging one line and exiting 0. `claude` is resolved when the job is turned on, checked like installstep.safe_interpreter, and stored
in <Budgit home>/jobs.json (never in the plist). Waking leads and messaging need the desktop scheduler: UNTESTED, not done here."""
import json
import os
import shutil
import subprocess
import sys

import studiolib as sl

MODEL_JOBS = ("keepalive", "standup")
SKIP_LEVELS = {"keepalive": ("critical",), "standup": ("tight", "critical")}


def path():
    return os.path.join(sl.home_state(), "jobs.json")


def load():
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def save(data):
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    sl.atomic_write(path(), json.dumps(data, indent=2, sort_keys=True) + "\n")


def resolve_claude(which=shutil.which):
    """-> the absolute path of a safe `claude`, or None."""
    import installstep
    found = which("claude")
    return os.path.realpath(found) if found and installstep.safe_interpreter(found) else None


def recorded_yes(name):
    import postupdate
    return postupdate.load()["answers"].get(name) == "yes"


def turn_on(name, budgit_path, at=None, every=None, which=shutil.which):
    """-> (exit code, text). Refuses without a recorded yes (exit 1, nothing written)."""
    import housekeeping
    if name not in housekeeping.JOBS:
        return 64, "budgit job: unknown job %s (cleanup, keepalive, standup)" % name
    if not recorded_yes(name):
        return 1, "budgit job on %s: no recorded yes; record one with `budgit setup --answer %s=yes`" % (name, name)
    code, key, fields = housekeeping.schedule("on", budgit_path, at, name, every)
    import i18n
    text = i18n.t(key, **fields)
    if code == 0 and name in MODEL_JOBS:
        data = load()
        claude = resolve_claude(which)
        data[name] = {"claude": claude}
        save(data)
        if not claude:
            text += "\nbudgit job: `claude` was not found on PATH; the job will log one line and do nothing until it is."
    return code, text


def turn_off(name, budgit_path):
    import housekeeping
    import i18n
    if name not in housekeeping.JOBS:
        return 64, "budgit job: unknown job %s" % name
    code, key, fields = housekeeping.schedule("off", budgit_path, None, name)
    return code, i18n.t(key, **fields)


def status(name, budgit_path):
    import housekeeping
    import i18n
    if name not in housekeeping.JOBS:
        return 64, "budgit job: unknown job %s" % name
    code, key, fields = housekeeping.schedule("status", budgit_path, None, name)
    return code, i18n.t(key, **fields)


def nothing_to_do(name):
    """True when no registered project exists (the job has nobody to serve). A deeper per-item check is left to the run itself."""
    try:
        import housekeeping
        return not housekeeping.projects()
    except Exception:
        return False


def precheck(name, level=None, busy=None, claude=None, todo=None):
    """-> (run, reason). No model. Every input is injectable for tests: level (a budget level), busy (a callable -> (bool, reason)),
    claude (a path or None), todo (a callable -> bool)."""
    if level is None:
        import budget
        level = budget.current()["level"]
    if level in SKIP_LEVELS.get(name, ()):
        return False, "budget is %s" % level
    import admit
    is_busy, why = (busy or admit.is_busy)()
    if is_busy:
        return False, "machine busy: " + why
    if claude is None:
        claude = load().get(name, {}).get("claude")
    if not claude or not os.path.isfile(claude):
        return False, "`claude` not found (resolved when the job was turned on)"
    if (todo or nothing_to_do)(name):
        return False, "nothing to do"
    return True, "ok"


def prompt_text(name):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", name + ".md")
    return sl.read_text(p)


def run(name, runner=subprocess.run, **precheck_args):
    """What the launch agent runs: precheck, then one headless `claude -p` run with the generic prompt. -> exit code (0 on a skip)."""
    ok, reason = precheck(name, **precheck_args)
    if not ok:
        print("budgit job %s: skipped: %s" % (name, reason))
        return 0
    claude = precheck_args.get("claude") or load().get(name, {}).get("claude")
    try:
        r = runner([claude, "-p", prompt_text(name)], capture_output=True, text=True, timeout=3600, cwd=sl.home_state(),
                   env=dict(os.environ, BUDGIT_JOB=name))
    except (OSError, subprocess.SubprocessError) as exc:
        print("budgit job %s: the run failed: %s" % (name, exc))
        return 0
    print("budgit job %s: ran, exit %s" % (name, getattr(r, "returncode", "?")))
    return 0
