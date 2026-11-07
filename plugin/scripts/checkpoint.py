"""R-123: a mechanical checkpoint written just before a context compaction.

A shell hook cannot know "task, done, next", so it does not try. It writes what a script can know: the open and doing
ledger items, the last tool calls of the session (when a transcript is given), `git status --short` and the branch of the
project repo and of the worktrees the session touched, and the first lines of progress.md. Whatever cannot be known is
written as "not available: <reason>", never invented.

Core logic is plain Python with no Claude coupling: `build_text` and `write_checkpoint` take their inputs as arguments.
The Claude adapter is `recent_calls_from_transcript` (Claude Code's JSONL transcript) and the PreCompact entry in
hooks.json. No `ps`, `/proc`, `lockf`, or fixed `/` separators: paths go through os.path, git runs without a shell."""
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import time

KEEP = 20                 # checkpoints kept in the folder
CALLS = 15                # last tool calls listed
PROGRESS_LINES = 15
GIT_LINES = 40
_LIVE_STATUSES = ("open", "doing")
DIR_NAME = "checkpoints"
_PREFIX = "checkpoint-"
_PATH_KEYS = ("file_path", "path", "notebook_path")
_SAFE_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")      # used with fullmatch: `$` would let a trailing newline through
_SAFE_PATH = re.compile(r"[\w./\\ ~:+@%,=()\[\]-]{1,300}")   # no control characters, no backtick, no `#`-only tricks
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
KNOWN_PROGRAMS = frozenset("""git python3 python pytest node npm npx ls cat head tail grep rg find sed awk make swift xcodebuild
budgit codex claude brctl curl gh open cp mv rm mkdir touch echo wc diff tar zip unzip osascript defaults launchctl kill pkill
ps df du""".split())
_SECRETISH = re.compile(r"(?i)(token|secret|passw|apikey|api_key|bearer|authorization|\.env|id_rsa|credential)")


def _stamp(t=None):
    """UTC stamp without colons, so it is a legal file name on Windows too."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t if t is not None else time.time()))


def _iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t if t is not None else time.time()))


def _one(text, n=100):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


# ---------------------------------------------------------------- the Claude adapter: the transcript

def _call_line(name, tin):
    """One tool call as `Name target`. A shell command is reduced to its program name (a command line can carry a
    secret); a path that looks like a secrets file is left out."""
    tin = tin if isinstance(tin, dict) else {}
    if not _SAFE_NAME.fullmatch(name):
        name = "(tool)"
    if name == "Bash":
        # The command text is never shown. It is tokenized like a shell would (quotes and escapes resolved, so a
        # secret inside quotes cannot become a "program"); the first token after NAME=value assignments is shown only
        # if it is exactly a word of the closed list KNOWN_PROGRAMS. Everything else, and any parse problem, is "(other)".
        try:
            parts = shlex.split(str(tin.get("command") or ""))
        except ValueError:
            return name + " (other)", ""
        while parts and _ASSIGN.match(parts[0]):
            parts.pop(0)
        if not parts:
            return name, ""
        prog = parts[0].replace("\\", "/").rsplit("/", 1)[-1]
        return name + " " + (prog if prog in KNOWN_PROGRAMS else "(other)"), ""
    target = ""
    for key in _PATH_KEYS:                          # only these fields; never description, prompt, command, content
        if isinstance(tin.get(key), str) and tin[key]:
            target = tin[key]
            break
    if not target:
        return name, ""
    if not _SAFE_PATH.fullmatch(target) or _SECRETISH.search(target):
        return name + " (path withheld)", ""
    return "%s `%s`" % (name, target), target


def recent_calls_from_transcript(path, n=CALLS):
    """-> (calls, missing). calls: up to `n` lines, oldest first; each tuple is (line, target). missing: "" or the
    exact reason no calls could be listed."""
    if not path:
        return [], "the hook input has no transcript_path"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-4000:]
    except OSError as exc:
        return [], "transcript not readable (%s)" % exc.__class__.__name__
    picked = []                                   # (name, input) newest first; only the last n are ever looked at
    for raw in reversed(lines):
        if len(picked) >= n:
            break
        try:                                      # one bad line (bad JSON, deep nesting...) never costs the checkpoint
            rec = json.loads(raw)
            msg = rec.get("message") if isinstance(rec, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            blocks = [(str(b["name"]), b.get("input")) for b in content
                      if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")]
        except Exception:
            continue
        picked.extend(reversed(blocks))
    calls = []
    for name, tin in reversed(picked[:n]):
        try:
            calls.append(_call_line(name, tin))
        except Exception:
            calls.append(("(tool) (unreadable)", ""))
    if not calls:
        return [], "the transcript holds no tool calls"
    return calls, ""


# ---------------------------------------------------------------- git

def _git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo, "--no-optional-locks"] + list(args), capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def git_state(repo):
    """-> {"branch", "status": [lines], "more": int} or None when repo is not a git work tree."""
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return None
    status = (_git(repo, "status", "--short") or "").splitlines()
    return {"branch": branch.strip(), "status": status[:GIT_LINES], "more": max(0, len(status) - GIT_LINES)}


def worktrees(repo):
    out = _git(repo, "worktree", "list", "--porcelain")
    return [line[len("worktree "):].strip() for line in (out or "").splitlines() if line.startswith("worktree ")]


def _inside(path, root):
    try:
        p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([p, r]) == r
    except ValueError:                   # another drive on Windows
        return False


def touched_worktrees(repo, targets):
    """The worktrees (other than `repo` itself) that one of the tool-call targets lies inside."""
    base = os.path.normcase(os.path.realpath(repo))
    found = []
    for wt in worktrees(repo):
        if os.path.normcase(os.path.realpath(wt)) == base:
            continue
        if any(_inside(t, wt) for t in targets if t):
            found.append(wt)
    return found


# ---------------------------------------------------------------- the text

def _git_block(label, repo):
    st = git_state(repo)
    if st is None:
        return ["%s: %s" % (label, "not available: not a git work tree or git not usable")]
    out = ["%s: %s (branch %s)" % (label, repo, st["branch"] or "?")]
    out += ["  " + l for l in st["status"]] or ["  (clean)"]
    if st["more"]:
        out.append("  … and %d more" % st["more"])
    return out


def build_text(project, items, calls, calls_missing, progress, trigger="", session="", at=None):
    """The checkpoint as Markdown text. `items`: ledger items (dicts with id, status, text); `calls`: [(line, target)];
    `progress`: (path, head lines or None, reason when None)."""
    lines = ["# Budgit checkpoint (mechanical, written just before a compaction)", "",
             "at: %s" % _iso(at), "trigger: %s" % _safe_field(trigger), "session: %s" % _safe_field(session),
             "project: %s" % project, "",
             "This is a script's snapshot, not a summary: it has no task, done or next. Read it, then continue from the ledger.", ""]
    live = [i for i in items if i.get("status") in _LIVE_STATUSES]
    lines.append("## Ledger: open and doing items (%d)" % len(live))
    lines += ["- %s [%s] %s" % (i.get("id"), i.get("status"), _one(i.get("text", ""), 90)) for i in live] or ["- none"]
    lines += ["", "## Last tool calls of this session"]
    if calls:
        lines += ["- " + c[0] for c in calls]
    else:
        lines.append("not available: %s" % (calls_missing or "no calls given"))
    lines += ["", "## Git"]
    lines += _git_block("project", project)
    for wt in touched_worktrees(project, [c[1] for c in calls]):
        lines += _git_block("worktree", wt)
    lines += ["", "## progress.md"]
    progress_path, head, why = progress
    if head is None:
        lines.append("not available: no readable progress.md (%s)" % why)
    else:
        lines.append("path: %s" % progress_path)
        lines += ["  " + _one(l, 300) for l in head]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- files

_OWN_NAME = re.compile(r"checkpoint-\d{8}T\d{6}Z-[A-Za-z0-9]{1,9}-\d{2,}\.md")      # used with fullmatch


def store_dir(home, project):
    """Where checkpoints live: under the per-user state folder `home`, in a subfolder keyed by a hash of the project
    path. Never inside the repo: a repo cannot ship a checkpoint, and `git add -A` cannot commit one."""
    key = hashlib.sha256(os.path.normcase(os.path.realpath(project)).encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(home, DIR_NAME, key)


def _plain_file(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not os.path.islink(path)


def plain_file_in(path, folder):
    """True when `path` is a regular file, not a symlink, and resolves inside `folder` (itself not a symlink)."""
    return _plain_file(path) and not os.path.islink(folder) and _inside(path, folder)


def _open_nofollow(path, flags, mode=0o600):
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), mode)


def read_head(folder, name, n=PROGRESS_LINES):
    """-> (lines, "") for the first `n` lines of folder/name, or (None, reason). Refuses a symlink, a non-regular file
    and a file that resolves outside `folder`."""
    path = os.path.join(folder, name)
    if not os.path.lexists(path):
        return None, "no %s" % name
    if not plain_file_in(path, folder):
        return None, "%s is not a plain file inside the state folder" % name
    try:
        fd = _open_nofollow(path, os.O_RDONLY)
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None, "%s is not a regular file" % name
            return [l.rstrip("\n") for _, l in zip(range(n), f)], ""
    except OSError as exc:
        return None, "%s not readable (%s)" % (name, exc.__class__.__name__)


def newest(store, session=None):
    """The newest checkpoint in `store` whose name is exactly one this module writes and that is a plain file. A
    checkpoint of `session` is preferred when there is one. None when there is none."""
    try:
        names = sorted(n for n in os.listdir(store) if _OWN_NAME.fullmatch(n) and _plain_file(os.path.join(store, n)))
    except OSError:
        return None
    sid = re.sub(r"[^A-Za-z0-9]", "", session or "")[:9]
    mine = [n for n in names if sid and n.split("-")[2] == sid] if sid else []
    pick = (mine or names or [None])[-1]
    return os.path.join(store, pick) if pick else None


def _safe_field(value, default="unknown"):
    value = value or ""
    if not value:
        return default
    return value if _SAFE_NAME.fullmatch(value) else "(withheld)"


def write_checkpoint(project, sdir, items, calls, calls_missing, trigger="", session="", at=None, *, store):
    """Write the checkpoint into `store` (required: there is no default inside the project) and keep the newest KEEP.
    `sdir` is the project's state folder, where progress.md is read from. -> its path."""
    d = store
    if os.path.islink(d):
        raise OSError("refusing to write through a symlink: %s" % d)
    os.makedirs(d, mode=0o700, exist_ok=True)
    head, why = read_head(sdir, "progress.md")
    text = build_text(project, items, calls, calls_missing, (os.path.join(sdir, "progress.md"), head, why),
                      trigger, session, at)
    sid = re.sub(r"[^A-Za-z0-9]", "", session or "")[:9] or "nosession"
    base = "%s%s-%s" % (_PREFIX, _stamp(at), sid)
    k = 1
    path = os.path.join(d, "%s-%02d.md" % (base, k))
    while os.path.lexists(path):                 # same second, same session: never overwrite; the counter sorts after
        k += 1
        path = os.path.join(d, "%s-%02d.md" % (base, k))
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = _open_nofollow(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL)       # a planted symlink or file is never written through
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    names = sorted(n for n in os.listdir(d) if _OWN_NAME.fullmatch(n))
    for old in names[:-KEEP]:
        try:
            os.unlink(os.path.join(d, old))
        except OSError:
            pass
    return path


def run(project, sdir, items, transcript_path=None, trigger="", session="", at=None, *, store):
    """The whole job, never raising: -> (path or None, error text or "")."""
    try:
        calls, missing = recent_calls_from_transcript(transcript_path)
        return write_checkpoint(project, sdir, items, calls, missing, trigger, session, at, store=store), ""
    except Exception as exc:                      # a checkpoint must never cost the compaction
        return None, "%s: %s" % (exc.__class__.__name__, exc)
