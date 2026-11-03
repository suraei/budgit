"""R-352: stop the line when main is red.

`budgit main-check` runs the project's full test suite on a clean checkout of main's HEAD and records the result keyed by
that commit sha (a result that exists for that sha is reused, so a long suite never runs twice for nothing). A red result sets
`red_since`; it stays set until a later check goes green. `budgit main-gate on` installs two git hooks that refuse a merge
onto main while it is red, unless the branch being merged is marked as the fix (`--fix-for <branch>` or a name ending in
`-green`):
  pre-merge-commit       `git merge` that creates a merge commit
  reference-transaction  a fast-forward `git merge` (no commit, so pre-merge-commit never runs) and `git commit` that
                         concludes a merge (a refused one, or --no-commit: git runs no pre-merge-commit there)
The hooks are plain POSIX sh and read one file in the git common dir (`budgit-main-red`, key=value lines) that this module
writes: they need neither python nor budgit on the PATH, and they are the user's to remove (`budgit main-gate off`). Same
safety rules as the watermark hook: never core.hooksPath, never an existing hook that is not ours.

Not gated (listed as follow-ups): `git push` onto main, `git merge --squash` + commit, `git pull` (a sync is not a merge of a
branch), `git push . x:main`; the PreToolUse deny for `git merge` / `git push` belongs to hook.py (R-335 owns that area)."""
import datetime
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import studiolib as sl
import suitecache
import suitequeue

STATE = "budgit-main-check.json"      # in the git common dir: per clone, shared by its worktrees, never tracked or reset
LOG = "main-check.log"
LOCK = "budgit-main-check.lock"
FLAG = "budgit-main-red"
MARK = "# budgit-main-gate"
HOOKS = ("pre-merge-commit", "reference-transaction")
FIX_PATTERN = "*-green"
KEEP_RESULTS = 20
KEEP_EPISODES = 50
FAILING_CAP = 50
TIMEOUT = 3600
_MODE = re.compile(r"^%s$" % re.escape(MARK), re.M)


class SuiteUnavailable(sl.StudioError):
    pass


# ---------------------------------------------------------------- git

def _git(path, *args):
    r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def repo_top(project):
    rc, top = _git(os.path.realpath(project), "rev-parse", "--show-toplevel")
    if rc or not top:
        raise sl.StudioError("%s is not a git repository" % project)
    return top


def common_dir(top):
    rc, out = _git(top, "rev-parse", "--git-common-dir")
    if rc or not out:
        raise sl.StudioError("cannot find the git directory of %s" % top)
    return out if os.path.isabs(out) else os.path.normpath(os.path.join(top, out))


def main_branch(project, top=None):
    """project.json `main_branch`, else `main`, else `master`."""
    top = top or repo_top(project)
    named = sl.load_project_config(project).get("main_branch")
    for name in ([named] if named else []) + ["main", "master"]:
        if _git(top, "rev-parse", "-q", "--verify", "refs/heads/" + name)[0] == 0:
            return name
    raise sl.StudioError("no main branch found in %s (set `main_branch` in project.json)" % top)


def main_sha(top, branch):
    rc, sha = _git(top, "rev-parse", "-q", "--verify", "refs/heads/" + branch)
    if rc:
        raise sl.StudioError("branch %s not found" % branch)
    return sha


# ---------------------------------------------------------------- state

def _state_path(project):
    return os.path.join(common_dir(repo_top(project)), STATE)


def load_state(project):
    try:
        with open(_state_path(project), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("results", {})
    data.setdefault("red_since", None)
    data.setdefault("red_sha", None)
    data.setdefault("fix_branches", [])
    data.setdefault("episodes", [])
    return data


def save_state(project, state):
    sl.atomic_write(_state_path(project), json.dumps(state, indent=2) + "\n")


def _parse_time(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)


def hours_between(since, until):
    return max(0.0, (_parse_time(until) - _parse_time(since)).total_seconds() / 3600.0)


def red_info(project, at=None):
    """For the daily retro and the gate: {red, red_since, red_sha, hours (while red now), episodes (closed ones), fix_branches}."""
    state = load_state(project)
    at = at or sl.now()
    red = bool(state["red_since"])
    return {"red": red, "red_since": state["red_since"], "red_sha": state["red_sha"],
            "hours": round(hours_between(state["red_since"], at), 2) if red else 0.0,
            "episodes": list(state["episodes"]), "fix_branches": list(state["fix_branches"])}


def is_fix_branch(project, branch):
    return fnmatch.fnmatchcase(branch, FIX_PATTERN) or branch in load_state(project)["fix_branches"]


def merge_allowed(project, branches):
    """(allowed, reason). Same rule as the sh hooks: main is not red, or one of the branches being merged is a marked fix."""
    state = load_state(project)
    if not state["red_since"]:
        return True, "main is not red"
    for b in branches:
        if fnmatch.fnmatchcase(b, FIX_PATTERN) or b in state["fix_branches"]:
            return True, "%s is marked as the fix" % b
    return False, "main is red since %s" % state["red_since"]


def _apply(state, sha, result, at):
    """Fold one result into the state; `at` is when the state change is dated (the check time for a fresh result)."""
    state["results"][sha] = result
    for old in sorted(state["results"], key=lambda k: state["results"][k]["checked_at"])[:-KEEP_RESULTS]:
        if old != sha:
            del state["results"][old]
    if result["state"] == "red":
        if not state["red_since"]:
            state["red_since"], state["red_sha"] = at, sha
    elif state["red_since"]:
        state["episodes"].append({"since": state["red_since"], "until": at, "sha": state["red_sha"],
                                  "hours": round(hours_between(state["red_since"], at), 2)})
        del state["episodes"][:-KEEP_EPISODES]
        state["red_since"], state["red_sha"], state["fix_branches"] = None, None, []


def write_flag(top, branch, state):
    """The file the sh hooks read: present only while main is red."""
    path = os.path.join(common_dir(top), FLAG)
    if not state["red_since"]:
        if os.path.lexists(path):
            os.remove(path)
        return
    lines = ["main=" + branch, "red_since=" + state["red_since"], "red_sha=" + (state["red_sha"] or "")]
    lines += ["fix=" + b for b in state["fix_branches"] if re.fullmatch(r"[A-Za-z0-9._/-]+", b)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- the suite

def suite_command(project):
    cfg = sl.load_project_config(project)
    if cfg.get("test_command"):
        command = str(cfg["test_command"])
        if not sl.test_command_approved(project, command):          # it comes from a repo file and runs as shell
            raise SuiteUnavailable("`test_command` in %s runs as shell and nobody approved it. Show the user this command, then "
                                   "give the user this command in a fenced bash block to run in a terminal (never run it "
                                   "yourself):\n  %s\n\n  %s" % (os.path.join(sl.studio_dir(project), "project.json"),
                                                                 sl._show_safe(command), sl.approve_command(project)))
        return command
    top = repo_top(project)
    if os.path.isdir(os.path.join(top, "tests")):
        return shlex.join([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    raise SuiteUnavailable("no test command: set `test_command` in %s" % os.path.join(sl.studio_dir(project), "project.json"))


_UT_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
_UT_FAIL = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", re.M)
_UT_TAIL = re.compile(r"^(?:OK|FAILED)(?: \(([^)]*)\))?\s*$", re.M)
_PT_FAIL = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)
_PT_SUM = re.compile(r"^=+ (.*?) in [\d.]+s", re.M)


def parse_output(text):
    """Counts and failing test names from unittest or pytest output; the exit code stays the judge of red or green."""
    out = {"ran": 0, "failed": 0, "errors": 0, "skipped": 0, "failing": []}
    m = _UT_RAN.search(text)
    if m:
        out["ran"] = int(m.group(1))
        tail = _UT_TAIL.findall(text)
        for part in (tail[-1].split(",") if tail and tail[-1] else []):
            k, _, v = part.strip().partition("=")
            if k in ("failures", "errors", "skipped") and v.isdigit():
                out[{"failures": "failed"}.get(k, k)] += int(v)
        out["failing"] = [c.strip() if c.strip().endswith("." + n) else "%s.%s" % (c.strip(), n)
                          for n, c in _UT_FAIL.findall(text)]       # Python 3.11+ already ends the parens with the method
    s = _PT_SUM.findall(text)
    if s and not m:
        for n, word in re.findall(r"(\d+) (failed|passed|skipped|error|errors)", s[-1]):
            n = int(n)
            out["ran"] += n if word != "skipped" else 0
            if word == "failed":
                out["failed"] += n
            elif word.startswith("error"):
                out["errors"] += n
            elif word == "skipped":
                out["skipped"] += n
        out["failing"] = _PT_FAIL.findall(text)
    seen, uniq = set(), []
    for name in out["failing"]:
        if name not in seen:
            seen.add(name)
            uniq.append(name)
    out["failing"] = uniq[:FAILING_CAP]
    return out


def suite_timeout(project):
    try:
        return max(1, int(sl.load_project_config(project).get("test_timeout") or TIMEOUT))
    except (TypeError, ValueError):
        return TIMEOUT


def run_suite(project, top, sha, command, timeout=None):
    """Run `command` in a clean detached checkout of `sha` (a temp worktree outside the repo, removed afterwards)."""
    tmp = tempfile.mkdtemp(prefix="budgit-main-")
    checkout = os.path.join(tmp, "main")
    rc, out = _git(top, "worktree", "add", "-q", "--detach", checkout, sha)
    if rc:
        shutil.rmtree(tmp, True)
        raise sl.StudioError("cannot make a clean checkout of main: git worktree add failed")
    timeout = timeout or suite_timeout(project)
    started = time.time()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    timed_out = False
    try:
        proc = subprocess.Popen(suitequeue.lower_priority(command), shell=True, cwd=checkout, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace", start_new_session=True)
        try:
            out, err = proc.communicate(timeout=timeout)
            code, text = proc.returncode, out + "\n" + err
        except subprocess.TimeoutExpired:
            timed_out, code = True, 124
            try:
                os.killpg(proc.pid, signal.SIGKILL)         # the shell and every test process it started, not the shell alone
            except OSError:
                pass
            out, err = proc.communicate()
            text = "%s\n%s" % (out or "", err or "")
    finally:
        _git(top, "worktree", "remove", "--force", checkout)
        shutil.rmtree(tmp, True)
        _git(top, "worktree", "prune")
    try:
        sl.ensure_studio_dir(project)
        sl.atomic_write(os.path.join(sl.studio_dir(project), LOG), text)
    except (OSError, sl.StudioError):
        pass
    result = parse_output(text)
    result.update({"state": "green" if code == 0 else "red", "exit": code, "seconds": round(time.time() - started, 1),
                   "checked_at": sl.now(), "command": command})
    if timed_out:
        result["timed_out"] = True
        result["failing"] = ["(timed out after %ds)" % timeout]
    elif code and not result["failing"]:
        result["tail"] = [l[:200] for l in text.strip().splitlines()[-10:]]
    return result


class _Lock:
    def __init__(self, project):
        self.path = os.path.join(common_dir(repo_top(project)), LOCK)

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    with open(self.path) as f:
                        pid = int(f.read().strip() or 0)
                    os.kill(pid, 0)
                except (OSError, ValueError):
                    try:
                        os.remove(self.path)             # a dead run left it behind
                    except OSError:
                        pass
                    continue
                raise sl.StudioError("a main-check is already running here (pid %d)" % pid)
        raise sl.StudioError("cannot take the main-check lock")

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def check(project, rerun=False, runner=None, notify=None, wait_load=False, load_notice=None, announce=None, release=False):
    """-> (branch, sha, result, cached). Reuses the recorded result for main's current sha unless `rerun`.
    `runner(project, top, sha, command)` is the test seam; the real one runs the suite on a clean checkout. A run waits its
    turn behind any other full suite on this machine (suitequeue) and is refused while the machine is overloaded
    (`wait_load` waits instead); `notify(waited, holder)` and `load_notice(load, limit)` hear about the waits, and
    `announce()` is called once, just before a suite really runs (not for a reused result).
    R-382: a sha with no result of its own still reuses a green run of the same tree (a merge commit that changed nothing
    against the branch tested before it), and a green run is recorded by tree for `budgit suite` and later checks."""
    top = repo_top(project)
    branch = main_branch(project, top)
    sha = main_sha(top, branch)
    with _Lock(project):
        state = load_state(project)
        cached = state["results"].get(sha)
        if cached and not rerun:
            _apply(state, sha, cached, sl.now())
            result = cached
            save_state(project, state)
            write_flag(top, branch, state)
            return branch, sha, result, True
        command = suite_command(project)
        tree = suitecache.tree_of(top, sha)
        ckey = suitecache.key(top, tree, command) if tree else None
        hit = None if rerun else suitecache.lookup(ckey)
        if hit:
            result = {"ran": hit.get("ran") or 0, "failed": 0, "errors": 0, "skipped": 0, "failing": [], "state": "green",
                      "exit": 0, "seconds": hit.get("seconds", 0), "checked_at": hit["checked_at"], "command": command,
                      "same_tree": True}
            _apply(state, sha, result, sl.now())
        else:
            if announce:
                announce()
            with suitequeue.SuiteSlot("main-check %s" % os.path.basename(top), notify=notify, wait_load=wait_load,
                                      load_notice=load_notice, project=top, release=release or None) as slot:
                result = (runner or run_suite)(project, top, sha, command)
            result["waited"], result["load_start"] = slot.waited, slot.load
            _apply(state, sha, result, result["checked_at"])
            if result["state"] == "green" and not result.get("timed_out") and ckey:
                suitecache.record(ckey, top, tree, command, result.get("seconds", 0), result.get("ran"))
            elif ckey:
                suitecache.forget(ckey)
        save_state(project, state)
        write_flag(top, branch, state)
    return branch, sha, result, bool(result.get("same_tree"))


def run_plain_suite(project, notify=None, started=None, wait_load=False, load_notice=None, rerun=False, release=False):
    """`budgit suite`: the project's own suite in its checkout (not a clean copy), queued like main-check, at low priority
    -> (exit code, slot, hit). R-382: when this exact tree (clean checkout) already ran green with this command, nothing runs
    and `hit` is that recorded entry (slot is None); it is also checked again after the queue, since the suite ahead of us
    may have been this very tree."""
    top = repo_top(project)
    command = suite_command(project)
    label = "suite %s" % os.path.basename(top)

    def lookup():
        tree = suitecache.clean_tree(top)
        k = suitecache.key(top, tree, command) if tree else None
        return k, tree, (None if rerun else suitecache.lookup(k))
    ckey, tree, hit = lookup()
    if hit:
        return 0, None, hit
    with suitequeue.SuiteSlot(label, notify=notify, wait_load=wait_load, load_notice=load_notice, project=top,
                              release=release or None) as slot:
        ckey, tree, hit = lookup()
        if hit:
            return 0, slot, hit
        if started:
            started(slot)
        began = time.time()
        code = subprocess.run(suitequeue.lower_priority(command), shell=True, cwd=top, stdin=subprocess.DEVNULL).returncode
        if code == 0 and ckey and suitecache.clean_tree(top) == tree:        # the same clean tree before and after
            suitecache.record(ckey, top, tree, command, time.time() - began)
        elif code != 0 and ckey:
            suitecache.forget(ckey)
    return code, slot, None


def mark_fix(project, branch):
    """Declare `branch` the fix for the red main -> True when recorded, False when main is not red."""
    top = repo_top(project)
    if _git(top, "rev-parse", "-q", "--verify", "refs/heads/" + branch)[0]:
        raise sl.StudioError("branch %s not found" % branch)
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise sl.StudioError("branch name %r has characters the hook cannot read" % branch)
    state = load_state(project)
    if not state["red_since"]:
        return False
    if branch not in state["fix_branches"]:
        state["fix_branches"].append(branch)
    save_state(project, state)
    write_flag(top, main_branch(project, top), state)
    return True


# ---------------------------------------------------------------- the git hooks

_DECIDE = r'''
# the whole cost of an ordinary git command: one test of one file (the path is fixed when the hook is installed)
[ -f "$flag" ] || exit 0
main=$(sed -n 's/^main=//p' "$flag" | head -n 1)
[ -n "$main" ] || exit 0
red_since=$(sed -n 's/^red_since=//p' "$flag" | head -n 1)
# $1 = the commit being merged: allowed when a branch pointing at it is the fix (or main's own remote copy: a sync)
fix_ok() {
  for r in $(git for-each-ref --points-at "$1" --format='%%(refname)' refs/heads refs/remotes); do
    case "$r" in
      refs/heads/*) b=${r#refs/heads/}; own=0 ;;
      refs/remotes/*) b=${r#refs/remotes/}; b=${b#*/}; own=1 ;;
      *) continue ;;
    esac
    case "$b" in %(pattern)s) return 0 ;; esac
    grep -qxF "fix=$b" "$flag" && return 0
    [ "$own" = 1 ] && [ "$b" = "$main" ] && return 0
  done
  return 1
}
refuse() {
  echo "budgit: main is red since $red_since: only the fix may merge onto it." >&2
  echo "budgit: run \`budgit main-check\` on main, or mark the fix with \`budgit main-check --fix-for <branch>\`" >&2
  echo "budgit: (or name it *-green). Remove this gate with \`budgit main-gate off\`." >&2
  [ -n "$abort_hint" ] && echo "budgit: git left the merge open: undo it with \`git merge --abort\`." >&2
  exit 1
}
'''

_PRE_MERGE = r'''
cur=$(git symbolic-ref --short -q HEAD) || exit 0
[ "$cur" = "$main" ] || exit 0
abort_hint=1
# git has not written MERGE_HEAD yet when `git merge` runs this hook and exports the merged commits as GITHEAD_<sha>=<name>;
# `git commit` that concludes a merge (a refused one, or --no-commit) has MERGE_HEAD and no GITHEAD_*
heads=$(env | sed -n 's/^GITHEAD_\([0-9a-f]\{40,64\}\)=.*/\1/p')
[ -n "$heads" ] || heads=$(cat "$(git rev-parse --git-path MERGE_HEAD)" 2>/dev/null)
[ -n "$heads" ] || exit 0
for h in $heads; do fix_ok "$h" || refuse; done
exit 0
'''

_REF_TX = r'''
[ "$1" = prepared ] || exit 0
mh="$(git rev-parse --git-path MERGE_HEAD)"
zero=0000000000000000000000000000000000000000
# `git merge` fast-forwards (no commit, so pre-merge-commit never runs) and `git commit` that concludes a merge
# (it runs no pre-merge-commit either, and its reflog action is empty) end here; a merge commit made by `git merge`
# itself was decided by pre-merge-commit.
case "$GIT_REFLOG_ACTION" in
  merge*) [ -f "$mh" ] && exit 0; conclude= ;;
  *) [ -f "$mh" ] || exit 0; conclude=1 ;;
esac
while read old new ref; do
  [ "$ref" = "refs/heads/$main" ] || continue
  case "$old" in $zero*) continue ;; esac
  [ "$old" = "$new" ] && continue                      # `git merge --abort` rewrites the ref to itself: never block the way out
  if [ -n "$conclude" ]; then
    abort_hint=1
    for h in $(cat "$mh"); do fix_ok "$h" || refuse; done
  else
    fix_ok "$new" || refuse
  fi
done
exit 0
'''


def hook_text(name, flag_path):
    head = "#!/bin/sh\n%s\n# Installed by `budgit main-gate on`; remove it with `budgit main-gate off`.\n" % MARK
    head += "flag=%s\n" % shlex.quote(flag_path)
    body = _DECIDE % {"pattern": FIX_PATTERN}
    return head + body + (_PRE_MERGE if name == "pre-merge-commit" else _REF_TX)


def _hooks_dir(repo):
    top = repo_top(repo)
    rc, hooks = _git(top, "rev-parse", "--git-path", "hooks")
    hooks = hooks if os.path.isabs(hooks) else os.path.join(top, hooks)
    blocked = "core.hooksPath is set" if _git(top, "config", "--get", "core.hooksPath")[1] else None
    return hooks, blocked


def _ours(path):
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    second = text.split("\n", 2)[1] if text.count("\n") >= 2 else ""
    return text if second == MARK else None


def install(repo):
    """-> installed | already installed | refused: existing <hook> hook | refused: core.hooksPath is set. All or nothing."""
    hooks, blocked = _hooks_dir(repo)
    if blocked:
        return "refused: " + blocked
    for name in HOOKS:
        target = os.path.join(hooks, name)
        if os.path.lexists(target) and _ours(target) is None:
            return "refused: existing %s hook" % name
    changed = False
    os.makedirs(hooks, exist_ok=True)
    flag_path = os.path.join(common_dir(repo_top(repo)), FLAG)
    for name in HOOKS:
        target, text = os.path.join(hooks, name), hook_text(name, flag_path)
        if os.path.lexists(target) and _ours(target) == text:
            continue
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(target, 0o755)
        changed = True
    return "installed" if changed else "already installed"


def remove(repo):
    """-> removed | not installed | refused: not Budgit's hook."""
    hooks, _blocked = _hooks_dir(repo)
    found = [os.path.join(hooks, n) for n in HOOKS if os.path.lexists(os.path.join(hooks, n))]
    if not found:
        return "not installed"
    if any(_ours(p) is None for p in found):
        return "refused: not Budgit's hook"
    for p in found:
        os.remove(p)
    return "removed"


def status(repo):
    """-> on | off | partial | blocked: core.hooksPath is set | blocked: another <hook> hook."""
    hooks, blocked = _hooks_dir(repo)
    if blocked:
        return "blocked: " + blocked
    mine = 0
    flag_path = os.path.join(common_dir(repo_top(repo)), FLAG)
    for name in HOOKS:
        target = os.path.join(hooks, name)
        if os.path.lexists(target):
            text = _ours(target)
            if text is None:
                return "blocked: another %s hook" % name
            mine += 1 if text == hook_text(name, flag_path) else 0.5           # a moved repo leaves a stale path: repair with `on`
    return "on" if mine == len(HOOKS) else ("off" if not mine else "partial")
