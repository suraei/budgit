"""R-900: a subagent commits only chained after a test run: `<tests> && git commit ...`. Pure function of the call.
The coordinator (no agent_id) is never affected. A test run is recognised by an ALLOW LIST of runner heads, read per segment
after the wrappers (env, nice, lockf, timeout) are stripped. Conservative: a segment that merely mentions `git commit` needs the chain too.
Not built: a per-project `test_commands` list (the project config has no place for it yet)."""
import os
import re
import shlex

_COMMIT = re.compile(r"\bgit\b(?:\s+(?:-C\s+\S+|-c\s+\S+|--\S+|-\S+))*\s+commit(?![\w-])")
REASON = ("budgit guard: commit only chained after a green test run: <tests> && git commit ... "
          "(a test runner such as python3 -m unittest, pytest, a runtests script, npm test, cargo test, go test, make test "
          "or budgit test, joined with &&; not ; or ||).")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
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
            t = t[1:]
        elif w == "env":
            t = t[1:]
            while t and (t[0].startswith("-") or _ASSIGN.match(t[0])):
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
    if head in ("bash", "sh", "zsh") and args:
        return "test" in os.path.basename(args[0]) and not args[0].startswith("-")
    if head in ("npm", "pnpm", "yarn"):
        rest = [a for a in args if a != "run" and not a.startswith("-")]
        return bool(rest) and rest[0] == "test"
    if head in ("cargo", "go", "budgit"):
        return bool(args) and args[0] == "test"
    if head == "make":
        return bool(args) and args[0] in ("test", "check")
    return "test" in head and head not in ("echo", "printf", "true", ":")      # runtests.py, run_tests.sh


def decision(tool, tool_input, agent_id):
    """("deny", reason) for a subagent's git commit that is not chained with && after a test run, else None."""
    if tool != "Bash" or not agent_id:
        return None
    cmd = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str) or "commit" not in cmd:
        return None
    segs = split_chain(cmd)
    for i, (seg, _) in enumerate(segs):
        if not _COMMIT.search(seg):
            continue
        ok, j = False, i
        while j > 0 and segs[j][1] == "&&":     # walk back through the contiguous && chain
            j -= 1
            if is_test_run(segs[j][0]):
                ok = True
                break
        if not ok:
            return ("deny", REASON)
    return None
