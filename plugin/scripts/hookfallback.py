#!/usr/bin/env python3
"""R-1128 hotfix 1: the hook's stdlib-only fallback. hook.py runs it when Budgit's own library (studiolib and what it pulls in)
fails to load, so the hook still refuses the destructive shapes and protected writes instead of failing open with no deny.
Imports ONLY the standard library. Never raises: any error inside it ends in a plain, quiet exit, and a pre-tool-use call it
cannot read ends in a deny, never in an allow. A refusal never prints a protected folder's name."""
import fnmatch
import glob
import json
import os
import re
import shlex
import stat
import sys
import unicodedata

REDUCED = ("budgit: enforcement reduced - Budgit's own library did not load, only the basic safety checks ran: destructive git and "
           "rm, and writes into protected folders (a Bash write is checked only for protected folders, and only the plain forms: "
           "redirects, tee, cp, mv, sed -i, dd of=) (%s). Run `budgit doctor`.")
PRE_EVENT = "pre-tool-use"                       # the spelling plugin/hooks.json passes; a test keeps the two equal
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_MAX_DEPTH = 8
_DEEP = "a command nested too deep to read"
# wrapper -> the flags that take the next word as their value (`sudo -u x`, `nice -n 5`, `timeout -s KILL`)
_WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string", "-P"},
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-r", "-t", "-D", "-R", "-U", "-T", "-c", "--user", "--group", "--host", "--prompt",
             "--chdir"},
    "doas": {"-u", "-C"},
    "command": set(), "nohup": set(), "builtin": set(), "setsid": set(), "busybox": set(), "toybox": set(),
    "exec": {"-a"}, "time": {"-f", "-o", "--format", "--output"},
    "nice": {"-n", "--adjustment"}, "ionice": {"-c", "-n", "-p"}, "caffeinate": {"-t", "-w"}, "arch": {"-arch"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "xargs": {"-I", "-J", "-n", "-P", "-L", "-s", "-d", "-E", "-a", "--max-args", "--max-procs", "--delimiter", "--arg-file"},
}
_RESERVED = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "coproc"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "csh", "tcsh", "fish"}
_INLINE = re.compile(r"(?:python[0-9.]*|perl|ruby|node|php|lua|awk|gawk|osascript|ssh|su|watch|parallel|script|tmux|screen|expect)\Z")
_SEPARATORS = {";", "&&", "||", "|", "&", "|&", "\n", "(", ")"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_REDIR = re.compile(r"&?>{1,2}[|&]?\Z")                              # >  >>  >|  >&  &>  &>>
_RAW = re.compile(r"\bgit\b[^\n]{0,80}?\b(?:push|clean|stash)\b|\bgit\b[^\n]{0,80}?\breset\b[^\n]{0,40}?--hard|reset\s+--hard"
                  r"|checkout\s+(?:-f|--force)|\brm\s+-\w*[rR]\w*f|\brm\s+-\w*f\w*[rR]|\brm\b[^\n]{0,40}--recursive")
_GIT_AND_RM = re.compile(r"\b(?:git|rm)\b")
_XARGS_GIT_OK = {"add", "commit", "diff", "log", "show", "status", "ls-files", "blame", "grep", "rev-parse", "check-ignore"}
_ALL_OPERANDS = {"tee", "mv", "touch", "truncate", "mkdir", "rmdir", "rm", "unlink", "chmod", "chown", "chflags", "shred", "mkfifo"}
_LAST_OPERAND = {"cp", "install", "ln", "rsync", "ditto", "scp"}
_UNKNOWN = object()                                                  # a cd the fallback cannot follow


def _fold(text):
    """APFS and NTFS fold case and Unicode form: compare paths the way they do (R-1128 M2). Elsewhere paths are exact."""
    if sys.platform in ("darwin", "win32"):
        return unicodedata.normalize("NFKC", text).casefold()
    return text


def home():
    for key in ("BUDGIT_HOME", "STUDIO_HOME"):
        value = os.environ.get(key)
        if value:
            return os.path.expanduser(value)
    return os.path.join(os.path.expanduser("~"), ".budgit")


def count():
    """One more fallback run on the counter file; returns nothing, never raises. The file is opened without following a symlink."""
    try:
        base = home()
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "hook-fallback.count")
        if os.path.islink(path):
            return
        nofollow = getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK
        n = 0
        try:
            fd = os.open(path, os.O_RDONLY | nofollow)
            try:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    n = int(os.read(fd, 32).decode("ascii", "ignore").strip() or 0)
            finally:
                os.close(fd)
        except (OSError, ValueError):
            n = 0
        # O_TRUNC is not given at open: a FIFO or device must be refused before anything is cut
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | nofollow, 0o600)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.ftruncate(fd, 0)
                os.write(fd, str(n + 1).encode("ascii"))
        finally:
            os.close(fd)
    except Exception:
        pass


def log_library_failure(event, error="import"):
    """R-1128 hotfix 1: Budgit's library could not load. Append ONE line to <home>/hooks.log: time, event, LIB_CAUSE and the exception's
    type name. Never the payload or the command. Stdlib only, no symlink followed, never raises."""
    try:
        import re
        import time
        base = home()
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "hooks.log")
        if os.path.islink(path):
            return
        safe = lambda s: re.sub(r"[^A-Za-z0-9_.-]", "_", str(s or ""))[:60] or "unknown"
        line = "%s %s: %s (%s)\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), safe(event), LIB_CAUSE, safe(error))
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.write(fd, line.encode("utf-8", "replace"))
        finally:
            os.close(fd)
    except Exception:
        pass


def _split_lines(command):
    """An unquoted newline ends a command like `;` does (shlex reads it as a space); a backslash-newline joins the lines."""
    out, quote, i = [], None, 0
    while i < len(command):
        c = command[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(command):
                out.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c == "\\" and i + 1 < len(command):
            if command[i + 1] == "\n":
                out.append(" ")
            else:
                out.append(c + command[i + 1])
            i += 1
        elif c in "'\"":
            quote = c
            out.append(c)
        elif c == "\n":
            out.append(" ; ")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _segments(command):
    lexer = shlex.shlex(_split_lines(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""                       # a `#` hides nothing: what follows it is still read
    segment = []
    for token in lexer:
        if token in _SEPARATORS or set(token) <= set(";&|()"):
            if segment:
                yield segment
            segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _substitutions(text):
    """The inner text of every `$(...)`, `<(...)`, `>(...)` and backtick pair. Raises ValueError when one does not close."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            j = text.find("`", i + 1)
            while j > 0 and text[j - 1] == "\\":
                j = text.find("`", j + 1)
            if j == -1:
                raise ValueError("backtick")
            out.append(text[i + 1:j].replace("\\`", "`"))
            i = j + 1
            continue
        if c in "$<>" and text[i + 1:i + 2] == "(":
            depth, j = 1, i + 2
            while j < n and depth:
                depth += (text[j] == "(") - (text[j] == ")")
                j += 1
            if depth:
                raise ValueError("paren")
            out.append(text[i + 2:j - 1])
            i = j
            continue
        i += 1
    return out


def _short_has(words, letter):
    return any(w.startswith("-") and not w.startswith("--") and letter in w[1:] for w in words)


def _git_sub(words):
    """The git subcommand and the words after it, skipping global options."""
    i = 1
    while i < len(words):
        w = words[i]
        if w in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w, words[i + 1:]
    return None, []


def _unwrap(words):
    """The words after every leading wrapper, its flags (and their values), assignment and shell keyword; and the wrappers seen."""
    seen = []
    while words:
        w = words[0]
        base = os.path.basename(w)
        if _ASSIGN.match(w) or w in _RESERVED:
            words = words[1:]
        elif base in _WRAPPERS:
            seen.append(base)
            valued = _WRAPPERS[base]
            words = words[1:]
            while words and words[0].startswith("-") and words[0] != "-":
                flag, words = words[0], words[1:]
                if flag == "--":
                    break
                if flag in valued and words:
                    words = words[1:]
            if base == "timeout" and words:
                words = words[1:]              # the duration
        else:
            break
    return words, seen


def _shell_denied(rest, depth, acc):
    """sh/bash/zsh/dash ...: recurse into the -c string; a shell that reads its commands from stdin cannot be read at all."""
    has_c = stdin = False
    ops = []
    i = 1
    while i < len(rest):
        w = rest[i]
        i += 1
        if "(" in w and re.fullmatch(r"[<>&()]+", w):
            stdin = True                        # bash <(echo ...): its commands come from another command
        elif re.fullmatch(r"[<>&]+", w) or _REDIR.match(w):
            i += 1                              # a redirect and its target
        elif re.fullmatch(r"\d+", w):
            continue
        elif w in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 1
        elif w.startswith("--"):
            continue
        elif w[:1] in "-+" and len(w) > 1:
            has_c = has_c or "c" in w[1:]
            stdin = stdin or "s" in w[1:]
        else:
            ops.append(w)
    if has_c:
        for op in ops:
            hit = bash_denied(op, depth + 1, acc)
            if hit:
                return hit
        return None
    if stdin or not ops or any(o == "-" or o.startswith("/dev/") for o in ops):
        return "a shell fed from stdin (its commands cannot be read)"
    return None


def _cd_track(rest, acc):
    head = os.path.basename(rest[0])
    if head not in ("cd", "pushd"):
        return
    ops = [a for a in rest[1:] if not a.startswith("-") or a == "-"]
    if not ops:
        acc["base"] = os.path.expanduser("~")
        return
    target = os.path.expandvars(os.path.expanduser(ops[0]))
    if "$" in target or "`" in target or target == "-" or acc["base"] is _UNKNOWN:
        acc["base"] = _UNKNOWN
    else:
        acc["base"] = os.path.normpath(target if os.path.isabs(target) else os.path.join(acc["base"], target))


def _collect_redirects(words, acc):
    for i, w in enumerate(words):
        if _REDIR.match(w) and i + 1 < len(words):
            target = words[i + 1]
            if not re.fullmatch(r"\d+|-", target):         # >&2 and >&- close or copy a descriptor; they write no file
                acc["writes"].append((target, acc["base"]))


def _collect_writes(rest, acc):
    """The paths a plain write command names (tee, cp, mv, sed -i, dd of= ...), for the protected-folder check."""
    head = os.path.basename(rest[0])
    args = rest[1:]
    ops = [a for a in args if a and not a.startswith("-")]
    for i, a in enumerate(args):
        if head in _ALL_OPERANDS or head in _LAST_OPERAND:
            if a in ("-t", "--target-directory") and i + 1 < len(args):
                acc["writes"].append((args[i + 1], acc["base"]))
            elif a.startswith("--target-directory="):
                acc["writes"].append((a.split("=", 1)[1], acc["base"]))
    if head in _ALL_OPERANDS:
        chosen = ops
    elif head in _LAST_OPERAND:
        chosen = ops[-1:]
    elif head in ("sed", "perl") and (_short_has(args, "i") or "--in-place" in args or any(a.startswith("--in-place=") for a in args)):
        chosen = ops
    elif head == "dd":
        chosen = [a[3:] for a in args if a.startswith("of=")]
    else:
        return
    for target in chosen:
        acc["writes"].append((target, acc["base"]))


def _denied_segment(words, depth=0, acc=None, scan=True):
    acc = acc if acc is not None else _new_acc(None)
    if depth > _MAX_DEPTH:
        return _DEEP
    if scan:
        _collect_redirects(words, acc)
    rest, seen = _unwrap(words)
    if scan and seen:
        # a wrapper's flag table can be out of date: read every later word as the start of the real command as well
        for i in range(1, len(words)):
            hit = _denied_segment(words[i:], depth + 1, acc, scan=False)
            if hit:
                return hit
        for w in words[1:]:
            if re.search(r"\s", w):               # env -S "git push", sudo "git push"
                hit = bash_denied(w, depth + 1, acc)
                if hit:
                    return hit
    if not rest:
        return None
    _cd_track(rest, acc)
    first = rest[0]
    if "`" in first or "$" in first or "*" in first or "?" in first or (len(first) > 1 and "[" in first and first != "[["):
        return "a command whose name the shell builds (a variable, a substitution or a glob)"
    head = os.path.basename(first)
    _collect_writes(rest, acc)
    if head in _SHELLS:
        return _shell_denied(rest, depth, acc)
    if head == "eval":
        return bash_denied(" ".join(rest[1:]), depth + 1, acc)
    if _INLINE.match(head):
        for w in rest[1:]:
            if _RAW.search(w):
                return "a destructive git or rm shape inside an inline program"
    if head == "find":
        for i, w in enumerate(rest):
            if w in ("-exec", "-execdir", "-ok", "-okdir"):
                hit = _denied_segment(rest[i + 1:], depth + 1, acc, scan=False)
                if hit:
                    return hit
    if head == "git":
        sub, after = _git_sub(rest)
        if any(w.startswith("alias.") for w in rest):
            return "git with an inline alias"
        if sub is not None and ("$" in sub or "`" in sub):
            return "git with a subcommand the shell builds"
        if "xargs" in seen and sub not in _XARGS_GIT_OK:
            return "git run by xargs"
        if sub == "push":
            return "git push"
        if sub == "reset" and "--hard" in after:
            return "git reset --hard"
        if sub == "checkout" and ("--force" in after or _short_has(after, "f")):
            return "git checkout -f"
        if sub == "clean":
            return "git clean"
        if sub == "stash":
            return "git stash"
    if head == "rm":
        if "xargs" in seen:
            return "rm run by xargs"
        recursive = "--recursive" in rest or _short_has(rest[1:], "r") or _short_has(rest[1:], "R")
        force = "--force" in rest or _short_has(rest[1:], "f")
        if recursive and force:
            return "rm -rf"
    return None


def _new_acc(cwd):
    return {"writes": [], "base": cwd if isinstance(cwd, str) and cwd else os.getcwd()}


def bash_denied(command, _depth=0, acc=None):
    """The reason a Bash command is refused, or None. Fills acc["writes"] with the (target, folder) pairs it writes."""
    if not isinstance(command, str):
        return None
    if _depth > _MAX_DEPTH:
        return _DEEP
    acc = acc if acc is not None else _new_acc(None)
    try:
        for inner in _substitutions(command):
            hit = bash_denied(inner, _depth + 1, acc)
            if hit:
                return hit
        for words in _segments(command):
            hit = _denied_segment(words, _depth, acc)
            if hit:
                return hit
    except ValueError:
        # a command that cannot be read: look for the shapes in the raw text
        if _RAW.search(command):
            return "an unreadable command with a destructive shape"
    return None


def read_regular(path, limit=1 << 20):
    """Text of `path` if it is a regular file, else None. O_NONBLOCK and O_NOFOLLOW at open, fstat S_ISREG on the fd: a planted
    FIFO or device never blocks a hook."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def _json(path):
    text = read_regular(path)
    if text is None:
        raise OSError("not a regular file: config")
    return json.loads(text)


def _realp(path):
    return os.path.realpath(os.path.expanduser(path))


def _real(path):
    return _fold(os.path.normcase(_realp(path)))


def _real_pattern(pat):
    """A protected pattern as the folded real path it stands for: a glob keeps its tail, its folder part is resolved."""
    plain = os.path.expanduser(pat)
    cut = min([plain.find(c) for c in "*?[" if c in plain] or [-1])
    if cut < 0:
        return _real(plain)
    slash = plain.rfind(os.sep, 0, cut)
    folder, tail = (plain[:slash] or os.sep, plain[slash:]) if slash >= 0 else (".", os.sep + plain)
    return _fold(os.path.normcase(os.path.join(_realp(folder), tail.lstrip(os.sep))))


def _under(path, base):
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _project_root(cwd):
    here = _realp(cwd or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(here, ".budgit")) or os.path.isdir(os.path.join(here, ".studio")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def protected_state(cwd):
    """(list of protected folders and glob patterns, readable). Unreadable config means the list cannot be trusted."""
    folders, patterns, readable = [], [], True
    cfg = os.path.join(home(), "config.json")
    if os.path.lexists(cfg):
        try:
            data = _json(cfg)
            raw = data.get("protected_dirs") or []
            if not isinstance(data, dict) or not isinstance(raw, list):
                raise ValueError("config")
            for d in raw:
                if not isinstance(d, str) or not d.strip():
                    raise ValueError("entry")
                p = os.path.expanduser(d.strip())
                folders.append(_real(p if os.path.isabs(p) else os.path.join(os.path.expanduser("~"), p)))
        except Exception:
            readable = False
    root = _project_root(cwd)
    if root:
        for name in (".budgit", ".studio"):
            pj = os.path.join(root, name, "project.json")
            if not os.path.lexists(pj):
                continue
            try:
                raw = _json(pj).get("protected_paths") or []
                if not isinstance(raw, list):
                    raise ValueError("paths")
                for pat in raw:
                    if not isinstance(pat, str):
                        raise ValueError("pattern")
                    patterns.append(_real_pattern(pat if os.path.isabs(os.path.expanduser(pat)) else os.path.join(root, pat)))
            except Exception:
                readable = False
    return folders, patterns, readable


def _path_hit(real, state, cwd):
    """Why a folded real path is refused, or None. The text never carries the path: it may sit in a protected folder."""
    folders, patterns, readable = state
    if _under(real, _real(home())):
        return "the target is inside Budgit's own folder"
    if not readable:
        if _under(real, _real(cwd)):
            return None
        return "the protected-folder list could not be read, and the target is outside the working folder"
    for base in folders:
        if _under(real, base):
            return "the target is inside a protected folder"
    for pat in patterns:
        if any(c in pat for c in "*?["):
            if fnmatch.fnmatchcase(real, pat):
                return "the target matches a protected path"
        elif _under(real, pat):
            return "the target is protected"
    return None


def write_denied(tool_input, cwd):
    if not isinstance(tool_input, dict):
        return "the write target could not be read"
    target = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if not isinstance(target, str) or not target or "\x00" in target:
        return "the write target could not be read"
    cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    full = target if os.path.isabs(os.path.expanduser(target)) else os.path.join(cwd, target)
    return _path_hit(_real(full), protected_state(cwd), cwd)


def bash_write_denied(acc, cwd):
    """The reason a Bash command's writes (redirects, tee, cp, mv, sed -i, dd of= ...) are refused, or None."""
    if not acc["writes"]:
        return None
    cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    state = protected_state(cwd)
    guarded = bool(state[0] or state[1]) or not state[2]
    for target, base in acc["writes"]:
        target = target.strip()
        if "\x00" in target:
            return "a write target that could not be read"
        if not target or (target.startswith("/dev/") and ".." not in target):
            continue
        expanded = os.path.expandvars(os.path.expanduser(target))
        if "$" in expanded or "`" in expanded:
            if guarded:
                return "a write target the shell builds, and a protected folder exists"
            continue
        if not os.path.isabs(expanded):
            if base is _UNKNOWN:
                if guarded:
                    return "a write target relative to a folder the fallback cannot follow, and a protected folder exists"
                continue
            expanded = os.path.join(base, expanded)
        paths = [expanded]
        if any(c in expanded for c in "*?["):
            paths += glob.glob(expanded)[:500]
        for path in paths:
            hit = _path_hit(_real(path), state, cwd)
            if hit:
                return hit
    return None


def _raw_hit(command):
    """The last look when the reader itself failed: the plain shapes, then any git or rm word at all."""
    if not isinstance(command, str):
        return None
    if _RAW.search(command):
        return "a command with a destructive shape"
    if _GIT_AND_RM.search(command):
        return "a command with a git or rm word that could not be read"
    return None


LIB_CAUSE = "Budgit's own library cannot load"
GUARD_CAUSE = "one of Budgit's guards has failed"    # hook.py main(), a guard raised (R-1128 hotfix 1)


def decide(event, inp, cause=LIB_CAUSE):
    """The output dict for one event, or {}. `cause` is the words the refusal gives for why only the basic checks ran."""
    reason = None
    if event == PRE_EVENT and isinstance(inp, dict):
        tool = inp.get("tool_name")
        if tool == "Bash":
            ti = inp.get("tool_input")
            command = ti.get("command") if isinstance(ti, dict) else None
            cwd = inp.get("cwd")
            acc = _new_acc(cwd)
            try:
                hit = bash_denied(command, 0, acc)
            except Exception:
                hit = _raw_hit(command)
            if hit:
                reason = "budgit fallback guard: %s is refused while %s. Stop and report it." % (hit, cause)
            else:
                try:
                    whit = bash_write_denied(acc, cwd)
                except Exception:
                    whit = "a Bash write that could not be checked" if acc["writes"] else None
                if whit:
                    reason = ("budgit fallback guard: a write is refused while %s (%s). Stop and "
                              "report it." % (cause, whit))
        elif tool in WRITE_TOOLS:
            try:
                hit = write_denied(inp.get("tool_input"), inp.get("cwd"))
            except Exception:
                hit = "the write target could not be checked"        # an error here is a deny, never an allow
            if hit:
                reason = "budgit fallback guard: a write is refused while %s (%s). Stop and report it." % (cause, hit)
    if reason:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason},
                "systemMessage": REDUCED % event}
    return {"systemMessage": REDUCED % event}


# ---- R-1128 hotfix 1, security review r3: the allow list used when a GUARD crashed (hook.py main). The deny list above misses the next
# trick, so here a call passes only when it is one of a few read-only shapes; everything else asks (denies in an unattended job).
_BASH_CAP = 4000                                                      # a longer command is not read at all (L-A)
_PLAIN_BAD = re.compile(r"[\n;&|<>`$(){}*?\[\]!~#\\\x00]")             # a separator, redirect, substitution, glob, escape or comment
_READ_GIT = ("status", "log", "diff", "show")
_GIT_BAD = ("-c", "-C", "-O", "--output", "--paginate", "--pager", "--config", "--exec", "--git-dir", "--work-tree", "--ext",
            "--textconv", "--no-index", "--open-files", "--namespace")
_SAFE_NOTE = ("budgit fallback guard: this step is not on the short list of safe ones while %s. Approve it only if you mean it; "
              "a refusal here is not a verdict on the command.")


def _inside(path, cwd, root, write=False):
    """True when `path` resolves inside the project, outside Budgit's own folder, protected folders and (for a write) the verify folder."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    full = os.path.expanduser(path)
    full = full if os.path.isabs(full) else os.path.join(cwd, full)
    real = _real(full)
    if not _under(real, _real(root)) or _path_hit(real, protected_state(cwd), cwd):
        return False
    if write:
        # R-1128 r4 M1, the general rule: a fallback write never lands in anything a tool or the harness runs or reads as config:
        # any top-level dot entry of the project (.git, .claude, .budgit, .studio, .npmrc, .mcp.json ...), a .git folder at any depth,
        # and every shape writeguard lists as persistent
        base = _real(root).rstrip(os.sep)
        parts = [p for p in real[len(base):].split(os.sep) if p]
        if _PERSIST.search(real) or (parts and parts[0].startswith(".")) or ".git" in parts:
            return False
    return True


# a copy of writeguard._PERSIST_PATH (this module imports only the standard library); a test keeps the two equal
_PERSIST = re.compile(
    r"(?:^|/)\.git(?:/|$)|"
    r"(?:^|/)(?:\.githooks|\.husky|launchagents|launchdaemons|\.ssh|\.zshrc\.d)(?:/|$)|(?:^|/)\.mcp\.json$|"
    r"(?:^|/)\.config/git(?:/|$)|"
    r"(?:^|/)\.claude/(?:settings(?:\.local)?\.json|hooks)(?:/|$)|"
    r"(?:^|/)(?:\.envrc|\.zshrc|\.zshenv|\.zprofile|\.zlogin|\.zlogout|\.bashrc|\.bash_profile|\.bash_login|\.bash_logout|"
    r"\.profile|\.inputrc|\.gitconfig|\.npmrc|\.yarnrc|\.yarnrc\.yml|\.pypirc|\.netrc|\.pgpass)$|"
    r"(?:^|/)(?:pip\.conf|pip\.ini)$|"
    r"^/(?:private/)?etc(?:/|$)|^/library(?:/|$)|^/system(?:/|$)|^/usr/local/etc(?:/|$)")


def _unsafe_root(root):
    """R-1128 r4 M2: no project found, or the 'project' is `/`, the home folder or a folder above it: no path is trusted there."""
    if not root:
        return True
    real, home_real = _real(root), _real("~")
    return real == _fold(os.sep) or _under(home_real, real)


def _plain_words(command):
    if not isinstance(command, str) or not command.strip() or len(command) > 2000 or _PLAIN_BAD.search(command):
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _bash_plain_ok(command, cwd, root, unsafe=False):
    words = _plain_words(command)
    if not words:
        return False
    head, args = words[0], words[1:]
    if head == "pwd":
        return not args
    if head == "git":
        if unsafe and args != ["status"]:
            return False                          # R-1128 r4 M2: outside a real project only `git status` and `pwd` pass
        # R-1128 r4 M3: a git shape takes no path operand outside the project (absolute, ~, .. or /dev/) and no config, dir or
        # --no-index option (`git diff --no-index` reads any file)
        if any(a.startswith(("/", "~")) or ".." in a or "/dev/" in a for a in args):
            return False
        return bool(args) and args[0] in _READ_GIT and not any(a.startswith(_GIT_BAD) for a in args)
    if unsafe:
        return False
    if head in ("ls", "cat", "head", "tail"):
        paths = []
        for i, a in enumerate(args):
            if re.fullmatch(r"-[A-Za-z0-9]+", a) or (re.fullmatch(r"\d+", a) and i and re.fullmatch(r"-[nc]", args[i - 1])):
                continue
            if a.startswith("-"):
                return False
            paths.append(a)
        if head != "ls" and not paths:
            return False                                              # cat with no file reads stdin
        return all(_inside(p, cwd, root) for p in paths)
    return False


def _strict_allows(inp):
    tool, ti = inp.get("tool_name"), inp.get("tool_input")
    ti = ti if isinstance(ti, dict) else {}
    cwd = inp.get("cwd") if isinstance(inp.get("cwd"), str) and inp.get("cwd") else os.getcwd()
    found = _project_root(cwd)
    unsafe = _unsafe_root(found)
    root = found or _realp(cwd)
    if tool == "Bash":
        return _bash_plain_ok(ti.get("command"), cwd, root, unsafe)
    if tool not in WRITE_TOOLS and tool not in ("Grep", "Glob", "Read"):
        return False                              # R-1128 r4 L1: an unknown or missing tool (MCP, WebFetch, Agent) asks
    if unsafe:
        return False                              # R-1128 r4 M2: outside a real project every path tool asks
    if tool in WRITE_TOOLS:
        return _inside(ti.get("file_path") or ti.get("notebook_path") or ti.get("path"), cwd, root, write=True)
    if tool in ("Grep", "Glob"):
        return _inside(ti.get("path") or cwd, cwd, root)
    return _inside(ti.get("file_path"), cwd, root)


def strict_decide(event, inp, cause=GUARD_CAUSE):
    """The output dict for a pre-tool-use call when a guard crashed: the deny list first, then the allow list; what is on neither asks
    (denies when BUDGIT_JOB is set: nobody can answer). Never raises."""
    try:
        if event != PRE_EVENT or not isinstance(inp, dict):
            return {}
        ti = inp.get("tool_input")
        command = ti.get("command") if isinstance(ti, dict) else None
        if inp.get("tool_name") == "Bash" and isinstance(command, str) and len(command) > _BASH_CAP:
            # R-1128 r4 L2: a command too long to read is denied (a padded `rm -rf` must not fall to an ask)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "budgit fallback guard: a command too long to read is refused "
                                                                       "while %s. Stop and report it." % cause},
                    "systemMessage": REDUCED % event}
        else:
            out = decide(event, inp, cause)
        if out.get("hookSpecificOutput"):
            return out
        try:
            ok = _strict_allows(inp)
        except Exception:
            ok = False
        if ok:
            return out
        kind = "deny" if os.environ.get("BUDGIT_JOB") else "ask"
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind,
                                       "permissionDecisionReason": _SAFE_NOTE % cause}}
    except Exception:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                       "permissionDecisionReason": _SAFE_NOTE % cause}}


def run(event, raw=None, error="import"):
    """Entry used by hook.py. Prints one JSON object; returns 0 always."""
    try:
        count()
        log_library_failure(event, error)
        try:
            inp = json.loads(raw if raw is not None else sys.stdin.read())
        except Exception:
            inp = {}
        # one allow list for both fallback paths (R-1128 hotfix 1): a library that cannot load is judged like a guard that crashed
        out = strict_decide(event, inp, LIB_CAUSE) if event == PRE_EVENT else decide(event, inp)
        out.setdefault("systemMessage", REDUCED % event)
        sys.stdout.write(json.dumps(out, ensure_ascii=True))
    except Exception:
        try:
            out = {"systemMessage": REDUCED % "unknown"}
            if event == PRE_EVENT:                 # a pre-tool-use call the fallback could not decide is refused
                out["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "budgit fallback guard: this call could not be checked "
                                                                         "while Budgit's own library cannot load. Stop and report it."}
            sys.stdout.write(json.dumps(out))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else ""))
