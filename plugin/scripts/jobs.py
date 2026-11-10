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


def claude_dirs(home=None):
    """R-332: where `claude` is installed when launchd's short PATH does not list it. Built from the user's folder, no fixed user name."""
    home = home or os.path.expanduser("~")
    return [os.path.join(home, ".local", "bin"), os.path.join(home, ".claude", "local"), os.path.join(home, ".npm-global", "bin"),
            "/opt/homebrew/bin", "/usr/local/bin"]


def _usable_claude(candidate, exists):
    import installstep
    # the versioned Homebrew Caskroom folder changes on every update: never a launcher target (R-332)
    return bool(candidate) and "/Caskroom/" not in candidate and exists(candidate) and installstep.safe_interpreter(candidate)


def resolve_claude_runtime(stored=None, which=shutil.which, home=None, exists=os.path.isfile):
    """R-332: -> an absolute `claude` found now, or None. Order: a PATH lookup, the known install dirs, then `stored` (an old jobs.json
    value) only if it still exists and is not a versioned Caskroom path. The PATH link is kept as found (never realpath: that is the
    versioned target)."""
    found = which("claude")
    if found and _usable_claude(os.path.abspath(found), exists):
        return os.path.abspath(found)
    for folder in claude_dirs(home):
        candidate = os.path.join(folder, "claude")
        if _usable_claude(candidate, exists):
            return candidate
    if isinstance(stored, str) and os.path.isabs(stored) and _usable_claude(stored, exists):
        return stored
    return None


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
        data[name] = {"claude": claude}   # only a hint now: run time resolves again (resolve_claude_runtime)
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


# R-332 / R-871: scheduled tasks that are retired, not deleted. The model housekeeping task is replaced by the deterministic
# daily job "cleanup" (`budgit housekeeping --apply --quiet`).
RETIRED = {"budgit-housekeeping": {"kind": "model scheduled task", "replaced_by": "cleanup"}}


def listing():
    """-> rows (name, state, note) of every job: active ones from housekeeping.JOBS, then the retired ones."""
    import housekeeping
    rows = [(n, "active", " ".join(["budgit"] + j["args"])) for n, j in housekeeping.JOBS.items()]
    rows += [(n, "retired", "%s, replaced by %s" % (r["kind"], r["replaced_by"])) for n, r in RETIRED.items()]
    return rows


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
        claude = resolve_claude_runtime(load().get(name, {}).get("claude"))
    if not claude or not os.path.isfile(claude):
        return False, "`claude` not found (looked up on PATH and the known install folders)"
    if (todo or nothing_to_do)(name):
        return False, "nothing to do"
    return True, "ok"


def prompt_text(name):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", name + ".md")
    return sl.read_text(p)


SKIPPED = 75        # EX_TEMPFAIL: the job did not do its job (R-874); never 0 on a skip
FAILED = 1


def run(name, runner=subprocess.run, **precheck_args):
    """What the launch agent runs: precheck, then one headless `claude -p` run with the generic prompt.
    -> exit code (R-874): 0 only when the tool ran and exited 0; SKIPPED on a skip; the tool's own code otherwise. Every run is recorded."""
    import jobhealth
    code = _run(name, runner, **precheck_args)
    jobhealth.record(name, code)
    return code


def _run(name, runner, **precheck_args):
    ok, reason = precheck(name, **precheck_args)
    if not ok:
        print("budgit job %s: skipped: %s" % (name, reason))
        return SKIPPED
    claude = precheck_args.get("claude") or resolve_claude_runtime(load().get(name, {}).get("claude"))
    try:
        r = runner([claude, "-p", prompt_text(name)], capture_output=True, text=True, timeout=3600, cwd=sl.home_state(),
                   env=dict(os.environ, BUDGIT_JOB=name))
    except (OSError, subprocess.SubprocessError) as exc:
        print("budgit job %s: the run failed: %s" % (name, exc))
        return FAILED
    code = getattr(r, "returncode", None)
    print("budgit job %s: ran, exit %s" % (name, code if code is not None else "?"))
    return code if isinstance(code, int) else FAILED
