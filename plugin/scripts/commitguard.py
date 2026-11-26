"""R-900: a subagent commits only chained after a test run: `<tests> && git commit ...`. Pure function of the call.
The coordinator (no agent_id) is never affected. A test run is recognised by an ALLOW LIST of runner heads, read per segment
after the wrappers (env, nice, lockf, timeout) are stripped. Conservative: a segment that merely mentions `git commit` needs the chain too.
Not built: a per-project `test_commands` list (the project config has no place for it yet)."""
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time

_COMMIT = re.compile(r"\bgit\b(?:\s+(?:-C\s+\S+|-c\s+\S+|--\S+|-\S+))*\s+commit(?![\w-])")
REASON = ("budgit guard: commit only chained after a green test run: <tests> && git commit ... "
          "(a test runner such as python3 -m unittest, pytest, a runtests script, npm test, cargo test, go test, make test "
          "or budgit test, joined with &&; not ; or ||). R-1360 allowed form: python3 -I <worktree>/tools/gate_test.py <modules> "
          "&& git -C <worktree> commit -F <file>, with nothing after the commit.")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SUBST = re.compile(r"\$\(|`").search          # R-1326: command substitution inside a VAR=value word
_SET_WRAPPERS = ("eval", "command", "builtin", "source", ".", "exec")        # may run a `set +o pipefail` we cannot see: fail closed
_NODE_NOT_A_RUN = ("--version", "-v", "--help", "-h", "--check", "-c")      # node prints or checks and exits: --test after them runs nothing
_NODE_EXIT_EARLY = _NODE_NOT_A_RUN + ("--v8-options", "--completion-bash", "--build-snapshot", "-e", "--eval", "-p", "--print", "-i",
                                      "--interactive", "--prof-process")
_NODE_OK_BEFORE = ("--no-warnings", "--no-deprecation", "--trace-warnings", "--enable-source-maps", "--no-experimental-strip-types",
                   "--experimental-test-coverage", "--experimental-test-isolation", "--experimental-strip-types",
                   "--experimental-transform-types", "--experimental-vm-modules", "--experimental-test-module-mocks",
                   "--experimental-require-module")                          # the only flags that may precede --test (plus --test-*)
_PYTHON = re.compile(r"python(\d+(\.\d+)*)?")
_PY_ARG_OPTS = ("-W", "-X")                 # python options that take the next token as their value


def split_chain(cmd):
    """[(segment, separator_before)] split on && || ; | & and newlines outside quotes."""
    out, buf, sep, quote, i = [], [], "", None, 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            buf.append(c)
        else:
            two = cmd[i:i + 2]
            if two in ("&&", "||"):
                out.append(("".join(buf), sep))
                buf, sep, i = [], two, i + 1
            elif c == "&" and (cmd[i - 1:i] == ">" or cmd[i + 1:i + 2] == ">"):
                buf.append(c)                   # R-1360: the `&` of `2>&1`, `>&` and `&>` is part of a redirect, not a separator
            elif c in ";|&\n":
                out.append(("".join(buf), sep))
                buf, sep = [], c
            else:
                buf.append(c)
        i += 1
    out.append(("".join(buf), sep))
    return out


def _strip_wrappers(t):
    """Drop leading VAR=x, env, nice [-n N], lockf [-k|-s|-t N] <lock> and timeout [opts] N."""
    while t:
        w = t[0]
        if _ASSIGN.match(w):
            if _SUBST(w):                   # R-1326: a command substitution in the value runs code first: not a plain prefix
                return []
            t = t[1:]
        elif w == "env":
            t = t[1:]
            while t and (t[0].startswith("-") or _ASSIGN.match(t[0])):
                if _SUBST(t[0]):
                    return []
                t = t[1:]
        elif w == "nice":
            t = t[1:]
            if t and t[0] in ("-n", "--adjustment") and len(t) > 1:
                t = t[2:]
            elif t and re.fullmatch(r"-+\d+|--adjustment=\d+", t[0]):
                t = t[1:]
        elif w == "lockf":
            t = t[1:]
            while t and t[0].startswith("-"):
                t = t[2:] if t[0] == "-t" else t[1:]
            t = t[1:]                       # the lock file
        elif w == "timeout":
            t = t[1:]
            while t and t[0].startswith("-"):
                t = t[1:]
            t = t[1:]                       # the duration
        else:
            break
    return t


_PKG_VALUE_OPTS = {"--prefix", "-C", "--dir", "--cwd", "--workspace", "-w", "--filter"}


_PKG_BOOL_OPTS = {"--silent", "-s", "--quiet", "-q", "--if-present", "--recursive", "-r",
                  "--frozen-lockfile", "--no-color", "--color"}


def is_test_run(segment):
    """True when the segment's head (after wrappers) is a known test runner. echo, printf, true and : never are."""
    try:
        t = _strip_wrappers(shlex.split(segment))
    except ValueError:
        return False
    if not t:
        return False
    head, args = os.path.basename(t[0]), t[1:]       # /bin/test and /usr/bin/[ are the builtins too
    if head in ("test", "[", "[["):                  # they test a file, not the code
        return False
    if _PYTHON.fullmatch(head):
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-m":
                return i + 1 < len(args) and args[i + 1] in ("unittest", "pytest")
            if a == "-c":                            # inline code is no test script, whatever it prints
                return False
            if a in _PY_ARG_OPTS:
                i += 2
            elif a.startswith("-"):
                i += 1
            else:
                return "test" in os.path.basename(a)
        return False
    if head in ("pytest", "py.test"):
        return True
    if head == "node":                               # R-1326: `node --test [paths]`; a --test after the script is the script's own arg
        seen = False                                 # only the listed flags may precede --test; an exit-early flag anywhere kills the run
        for a in args:
            if a in _NODE_EXIT_EARLY:
                return False
            if not seen:
                if a == "--test":
                    seen = True
                elif not (a in _NODE_OK_BEFORE or a.startswith("--test-")):
                    return False
        return seen
    if head in ("bash", "sh", "zsh") and args:
        return "test" in os.path.basename(args[0]) and not args[0].startswith("-")
    if head in ("npm", "pnpm", "yarn"):
        i = 0
        while i < len(args):
            a = args[i]
            if a in _PKG_VALUE_OPTS:                 # the option's value is a path or name, not the script
                i += 2
            elif a == "run" or a in _PKG_BOOL_OPTS or (a.startswith("--") and "=" in a):
                i += 1
            elif a.startswith("-"):                  # unknown option: it may take a value, so fail closed
                return False
            else:
                return a == "test"                   # the first word must be the script; what follows is its own args
        return False
    if head in ("cargo", "go", "budgit"):
        return bool(args) and args[0] == "test"
    if head == "make":
        return bool(args) and args[0] in ("test", "check")
    return "test" in head and head not in ("echo", "printf", "true", ":")      # runtests.py, run_tests.sh


def _set_effect(segment):
    """"on" / "off" when the segment is a `set` that turns pipefail on (-o pipefail, -eo pipefail) or off (+o pipefail), else None.
    Only a segment whose first word (after VAR=x) is `set` counts as "on". Fail closed: eval, command, builtin, source, `.` and exec, or
    any other non-read segment that mentions pipefail (`{ set +o pipefail`, `env set +o pipefail`), may have cancelled it: "off"."""
    try:
        t = shlex.split(segment)
    except ValueError:
        return "off" if "pipefail" in segment else None
    while t and _ASSIGN.match(t[0]):
        t = t[1:]
    if not t:
        return None
    if t[0] != "set":
        if t[0] in _SET_WRAPPERS or "pipefail" in " ".join(t):
            return "off"
        return None
    effect, i = None, 1
    while i < len(t):
        a = t[i]
        if a == "--":
            break
        if re.fullmatch(r"[-+][A-Za-z]*o", a) and i + 1 < len(t):
            if t[i + 1] == "pipefail":
                effect = "on" if a[0] == "-" else "off"
            i += 2
        else:
            i += 1
    return effect


def _pipefail_before(segs, s):
    """R-1326: True when pipefail is surely on for the pipeline that starts at segs[s]. set -o / +o are walked in order; a later +o
    cancels. A `set -o pipefail` counts only when it runs unconditionally: no && || or | before it in its own link, and not itself
    piped or backgrounded (`false && set -o pipefail`, `true | set -o pipefail`, `set -o pipefail &`). The pipeline itself must not be
    the right side of || (`true || tests` skips the tests)."""
    on = False
    for k in range(s):
        eff = _set_effect(segs[k][0])
        if eff is None:
            continue
        nxt = segs[k + 1][1] if k + 1 < len(segs) else ""
        if eff == "off":
            on = False                               # a conditional +o may still have run: fail closed
        elif segs[k][1] in ("", ";", "\n", "&") and nxt not in ("|", "&"):
            on = True
    return on and segs[s][1] != "||"


# R-1326: there is NO read exemption. Every segment where the commit words match (the anywhere-match baseline, _COMMIT) needs the test
# chain, a read such as `grep -n "git commit" f` included, as before R-1326. A safe exemption needs a quote scanner that agrees with
# the shell (split_chain ignores backslash escapes), so it is a separate item.


# ---- R-1259: a green-run receipt lets a plain `git commit` pass ----
# tools/runtests.py, run alone and green on its own selection (no module or file argument), writes
# <Budgit home>/test-receipts/<hash of the git dir>.json (studiolib.receipt_path): tree = `git write-tree` of the index, this git dir,
# the runner's own hash, the modules it ran, `need` (the test modules its own select() picks for the staged files, worked out inside
# the runner's process) and the time. A plain commit passes when that receipt is for the tree about to be committed and need is a
# subset of modules. This guard judges data only: it never imports or runs checkout code.
# Honest limits: there is no secret key; the home guard (not this file) keeps an agent's write tool, redirect, tee, cp, mv, ln,
# sed -i and inline python away from the receipt, but a script file that builds the path at run time is not seen, and a program that
# runs as the user can always write it. That is no weaker than the chain `python3 -m unittest <trivial> && git commit`. The accept line
# lists the modules for the coordinator to audit. select() reads the working tree's test files, which an agent can edit. The check
# and the commit are not atomic and a receipt can be replayed for MAX_AGE. Pre-commit hooks that change the tree are out of scope.
MAX_AGE = 30 * 60                       # policy: seconds a receipt may be old
MAX_BYTES = 64 * 1024                   # policy: a receipt larger than this is refused unread
GIT_TIMEOUT = 5                         # policy: seconds for each git call
FIX_LINE = ("budgit guard: no fresh green receipt for this tree: run python3 <worktree>/tools/runtests.py --changed alone "
            "(no module or file argument; the Budgit home must exist and be writable), then commit (or chain <tests> && git commit ...).")
UNREADABLE = "budgit guard: could not read the tree, run again (git missing, too slow or the index is unmerged)."
_KEYS = {"v", "tree", "gitdir", "runner_sha256", "modules", "need", "head", "time"}      # head: `git rev-parse HEAD` at the run, "none" without one
_HEX40 = re.compile(r"[0-9a-f]{40}")
_VALUE_OPTS = {"-m", "--message", "-F", "--file", "--author"}
_FLAG_OPTS = {"-q", "--quiet", "--no-edit"}


class _Unreadable(Exception):
    pass


def _git(directory, *args):
    """stdout of one git call, with no GIT_* variable, no fsmonitor and no hooks path from the repo's config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", directory] + list(args),
                           capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError):
        raise _Unreadable()
    if r.returncode != 0:
        raise _Unreadable()
    return r.stdout.strip()


def _same_path(a, b):
    return os.path.realpath(a).casefold() == os.path.realpath(b).casefold()      # macOS volumes ignore case


def receipt_ok(commit_dir, now=None):
    """(True, "", modules) when the receipt of commit_dir's git dir is for the tree `git commit` would make now; else
    (False, "unreadable" | "refused", [])."""
    now = time.time() if now is None else now
    try:
        gitdir = _git(commit_dir, "rev-parse", "--absolute-git-dir")
        tree = _git(commit_dir, "write-tree")
    except _Unreadable:
        return False, "unreadable", []
    import studiolib
    data = _read_receipt(studiolib.receipt_path(gitdir))
    try:
        head = _git(commit_dir, "rev-parse", "--verify", "-q", "HEAD")
    except _Unreadable:
        head = "none"                                   # no HEAD yet (or unreadable: a receipt for a real HEAD then fails the match)
    if not isinstance(data, dict) or set(data) != _KEYS or data["v"] != 1:
        return False, "refused", []
    mods, stamp, need = data["modules"], data["time"], data["need"]
    if not (isinstance(need, list) and all(isinstance(m, str) for m in need) and isinstance(data["head"], str)):
        return False, "refused", []
    if not (isinstance(data["tree"], str) and _HEX40.fullmatch(data["tree"]) and isinstance(data["gitdir"], str)
            and isinstance(data["runner_sha256"], str) and isinstance(mods, list) and mods
            and all(isinstance(m, str) and m for m in mods) and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)):
        return False, "refused", []
    if data["tree"] != tree or data["head"] != head or not _same_path(data["gitdir"], gitdir) or data["runner_sha256"] != studiolib.RUNTESTS_SHA256:
        return False, "refused", []
    if not 0 <= now - stamp <= MAX_AGE:
        return False, "refused", []
    if not set(need) <= set(mods):                  # the run did not cover what its own selection needed for the staged files
        return False, "refused", []
    return True, "", mods


def _read_receipt(path):
    """The receipt as parsed JSON, or None: a regular file only (never a FIFO or device: O_NONBLOCK, no symlink), at most MAX_BYTES."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return None
        raw = os.read(fd, MAX_BYTES + 1)
        return json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def _clean(text):
    """A module name for one advice line: printable characters only (no newline, escape or other control character)."""
    return "".join(c for c in text if c.isprintable())


def _plain_commit_dir(seg, cwd):
    """The directory of a plain `git [-C dir] commit [-m V | -F V | --author V | -q | --no-edit]...` segment, else None. Everything
    the shell could rewrite or that changes what gets committed is refused here (such a command needs the chain)."""
    text = seg.strip()
    if any(c in text for c in "$`\\"):
        return None
    quote, outside = None, []
    for c in text:                                # < > * ? [ { ~ outside quotes: redirects, <( >(, globs and ~ expand before git runs
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        else:
            outside.append(c)
    if quote or any(c in "<>*?[{~" for c in outside):
        return None
    try:
        t = shlex.split(text)
    except ValueError:
        return None
    if not t or t[0] != "git":
        return None
    i, directory = 1, cwd
    if len(t) > 2 and t[1] == "-C":
        directory = t[2] if os.path.isabs(t[2]) else os.path.join(cwd, t[2])
        i = 3
    if i >= len(t) or t[i] != "commit":
        return None
    i += 1
    while i < len(t):
        a = t[i]
        if a in _FLAG_OPTS:
            i += 1
        elif a in _VALUE_OPTS and i + 1 < len(t):
            i += 2
        elif a.startswith(("--message=", "--file=", "--author=")):
            i += 1
        else:
            return None
    if directory != cwd:                          # -C must stay in the cwd's own repository (same common git dir)
        try:
            if not _same_path(os.path.join(directory, _git(directory, "rev-parse", "--git-common-dir")),
                              os.path.join(cwd, _git(cwd, "rev-parse", "--git-common-dir"))):
                return None
        except _Unreadable:
            return None
    return directory


def judge(tool, tool_input, agent_id, cwd=None, now=None):
    """One verdict for a call: None (not a subagent commit), ("pass", None) (chained after a test run), ("accept", note) (a plain
    commit on a fresh receipt) or ("deny", reason). decision and accept_note read it; the hook asks once and hands it to both."""
    if tool != "Bash" or not agent_id:
        return None
    cmd = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str) or "commit" not in cmd:
        return None
    segs = split_chain(cmd)
    for i, (seg, _) in enumerate(segs):
        if not _COMMIT.search(seg):             # R-1326: no read exemption, the anywhere-match alone decides
            continue
        ok, j = False, i
        while j > 0 and segs[j][1] == "&&":     # walk back through the contiguous && chain
            j -= 1                              # j is the last member of the pipeline before this &&
            s = j
            while s > 0 and segs[s][1] == "|":  # s is the first member of that pipeline
                s -= 1
            # R-1326, a deliberate decision: a test piped into another command (`tests | grep OK && git commit`) leaves the exit
            # status of the LAST command, so a failing test would still commit: it is NOT a test run. Only when `set -o pipefail`
            # came earlier in the same line does the pipeline keep the test's status (_pipefail_before walks set -o / +o as state);
            # then any member of it may be the test.
            if is_test_run(segs[j][0]) or (any(is_test_run(x) for x, _ in segs[s:j]) and _pipefail_before(segs, s)):
                ok = True
                break
            j = s
        if ok:
            continue
        directory = _plain_commit_dir(seg, cwd) if len(segs) == 1 and cwd else None
        if directory is None:
            return ("deny", refusal_text(cwd, REASON))
        good, why, mods = receipt_ok(directory, now)
        if good:
            shown = " ".join(_clean(m) for m in mods)
            return ("accept", "budgit: commit accepted on a green run of %d modules: %s" % (
                len(mods), shown if len(shown) <= 300 else shown[:297] + "..."))
        return ("deny", UNREADABLE if why == "unreadable" else refusal_text(cwd, FIX_LINE))
    return ("pass", None)


# R-1360 C3: Budgit's own runner (tools/runtests.py) exists only in the Budgit checkout. In any other project the refusal names that
# project's registered `test_command` (its .studio/project.json), or says plainly it has none. Text only: no decision depends on it.
NO_COMMAND = ("budgit guard: this project has no registered test command: set \"test_command\" in its .studio/project.json, then chain "
              "it before the commit: <test_command> && git commit ... (joined with &&; not ; or ||).")


def _project_root(cwd):
    try:
        import studiolib
        return studiolib.suite_repo_root(cwd) or os.path.realpath(cwd)
    except Exception:
        return os.path.realpath(cwd)


def _is_budgit_checkout(root):
    return (os.path.isfile(os.path.join(root, "tools", "runtests.py"))
            and os.path.isfile(os.path.join(root, "plugin", "scripts", "commitguard.py")))


def registered_test_command(cwd):
    """The project's `test_command` from its project.json (a shown, cleaned, short copy), else None."""
    try:
        import studiolib
        command = studiolib.load_project_config(_project_root(cwd)).get("test_command")
    except Exception:
        return None
    command = _clean(str(command)).strip() if command else ""
    return (command if len(command) <= 120 else command[:117] + "...") or None


def refusal_text(cwd, budgit_text):
    """`budgit_text` (REASON or FIX_LINE, which name Budgit's runner) in the Budgit checkout or without a cwd; else the project's
    own command first in the text, or NO_COMMAND."""
    if not cwd:
        return budgit_text
    if _is_budgit_checkout(_project_root(cwd)):
        return budgit_text
    command = registered_test_command(cwd)
    if command:
        return "budgit guard: chain this project's registered test command before the commit: %s && git commit ... (joined with &&; not ; or ||)." % command
    return NO_COMMAND


def decision(tool, tool_input, agent_id, cwd=None, now=None, verdict=None):
    """("deny", reason) for a subagent's git commit that is neither chained with && after a test run nor plain on a fresh receipt,
    else None. `verdict` is a judge() result the caller already has."""
    v = verdict if verdict is not None else judge(tool, tool_input, agent_id, cwd, now)
    return v if v and v[0] == "deny" else None


def accept_note(tool, tool_input, agent_id, cwd=None, now=None, verdict=None):
    """The one advice line for a commit accepted on a receipt (modules listed), else None. Advice only: it never denies or asks."""
    v = verdict if verdict is not None else judge(tool, tool_input, agent_id, cwd, now)
    return v[1] if v and v[0] == "accept" else None
