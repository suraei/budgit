"""R-433 / R-304: two guards that hold in every session, with or without protected data and with or without the standing yes.

persistence_decision   a write into a place that runs code later (git hooks, shell startup files, tool config, agent
                       settings, launch agents) asks, whatever writes it: a shell redirect, tee, cp, mv, install, ln, sed -i,
                       dd, touch, chmod, rm, curl -o, tar -C, `git config`, `npm config set`, inline interpreter code, or the
                       Write / Edit tools. Symlinks are resolved before the path is judged.
destructive_git_decision  every git command that throws work away (reset --hard, checkout -f / -- <path>, restore, clean,
                       stash drop or clear, branch -D, push --force ...) asks, with or without -C / --git-dir / cd.
                       Plain reads stay silent (R-342): only the verbs and options listed here ask.

Both only ever ask; neither can loosen another guard. A command the guard cannot read asks only when it names a target."""
import glob as _glob
from itertools import islice as _islice
import os
import re

import studiolib as sl

# A path that runs code later or changes what runs: judged on the folded, slash-normalised text and on its realpath.
_PERSIST_PATH = re.compile(
    r"(?:^|/)\.git(?:/|$)|"                                                         # .git/hooks, .git/config, the .git entry itself
    r"(?:^|/)(?:\.githooks|\.husky|launchagents|launchdaemons|\.ssh|\.zshrc\.d)(?:/|$)|(?:^|/)\.mcp\.json$|"
    r"(?:^|/)\.config/git(?:/|$)|"
    r"(?:^|/)\.claude/(?:settings(?:\.local)?\.json|hooks)(?:/|$)|"
    r"(?:^|/)(?:\.envrc|\.zshrc|\.zshenv|\.zprofile|\.zlogin|\.zlogout|\.bashrc|\.bash_profile|\.bash_login|\.bash_logout|"
    r"\.profile|\.inputrc|\.gitconfig|\.npmrc|\.yarnrc|\.yarnrc\.yml|\.pypirc|\.netrc|\.pgpass)$|"
    r"(?:^|/)(?:pip\.conf|pip\.ini)$|"
    r"^/(?:private/)?etc(?:/|$)|^/library(?:/|$)|^/system(?:/|$)|^/usr/local/etc(?:/|$)")
# code that names such a place without a slash (os.path.join('.git', 'hooks', ...)) or spells it in the open
_PERSIST_CODE = re.compile(
    r"\.git\b(?![\w-])|\bhooks?\b|\.(?:zshrc|zshenv|zprofile|zlogin|bashrc|bash_profile|profile|envrc|gitconfig|npmrc|pypirc|netrc)\b|"
    r"launch(?:agents|daemons)|\.ssh\b|settings(?:\.local)?\.json|/etc/", re.I)
_UNREADABLE = re.compile(r"[$`]")

_DEST_ONLY = {"cp", "install", "rsync", "ditto", "ln"}      # only the destination is written
_ANY_OPERAND = {"mv", "rm", "rmdir", "unlink", "shred", "srm", "trash", "tee", "truncate", "touch", "mkdir", "chmod", "chown",
                "chgrp", "patch", "mkfifo", "mknod", "dd", "split", "csplit"}
_FETCHERS = {"curl", "wget"}
_EXTRACT = {"tar", "bsdtar", "gtar", "unzip", "7z", "7za", "cpio", "pax"}
_TOOL_CONFIG = {"npm", "pnpm", "yarn", "pip", "pip3", "pipx", "uv", "gem", "bundle", "cargo", "poetry"}
_GIT_RISKY_KEYS = re.compile(r"^(?:core\.(?:hookspath|sshcommand|fsmonitor|pager|editor|askpass|gitproxy)|alias\..*|"
                             r"credential\..*|diff\..*\.(?:command|textconv)|filter\..*|merge\..*\.driver|include.*|"
                             r"url\..*|protocol\..*|sequence\.editor|gpg\..*program|uploadpack\..*|receive\..*|safe\.directory)$")


def _candidates(piece, dirs):
    """The folded text of a path piece, and its resolved form under each working folder (symlinks followed)."""
    text = piece.replace("\\", "/")
    out = {sl._fold(re.sub(r"/+", "/", text))}
    expanded = os.path.expanduser(text)
    for d in dirs:
        try:
            joined = os.path.join(d, expanded)
            out.add(sl._fold(os.path.normpath(joined)))
            out.add(sl._fold(os.path.realpath(joined)))
        except (OSError, ValueError):
            out.add(sl._fold(text))
    return out


_GLOB_CAP = 500
_CMD_CAP = 4096


class _TooBig(Exception):
    """A glob or a command line past its cap: the guard asks instead of walking further."""


def _hit(piece, dirs):
    """True when this word, as it stands or resolved, names a persistence target."""
    if not piece or piece in ("-", "--"):
        return False
    pieces = [piece] + [p for p in sl._PIECES.split(piece) if p]
    for p in list(pieces):
        if p.startswith("-") and not p.startswith("--") and len(p) > 2:
            pieces.append(p[2:])
    for p in pieces:
        if p.startswith("-"):
            continue
        if set(p) & sl._GLOB_CHARS:
            for d in dirs:
                hits = list(_islice(_glob.iglob(sl._under(p, d)), _GLOB_CAP))
                if len(hits) >= _GLOB_CAP:
                    raise _TooBig()
                if any(_PERSIST_PATH.search(c) for hit in hits for c in _candidates(hit, [d])):
                    return True
        if any(_PERSIST_PATH.search(c) for c in _candidates(p, dirs)):
            return True
    return False


def _operands(tokens):
    return [t for t in tokens[1:] if not t.startswith("-") or t == "-"]


def _git_config_writes(tokens, dirs):
    """`git config` that sets, adds or unsets a key (not a read) and the key or the scope can run code or change where it
    is read from."""
    at, _, cvals = sl._git_global_scan(tokens)
    if at is None or tokens[at] != "config":
        return False
    args = tokens[at + 1:]
    if any(a in ("--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "--show-origin", "--show-scope",
                 "--name-only", "--get-color", "--get-colorbool", "get", "list") for a in args):
        return False
    for i, a in enumerate(args):                         # -f / --file <path>: a write into a persistence file asks, whatever the key
        val = a.split("=", 1)[1] if a.startswith("--file=") else (args[i + 1] if a in ("-f", "--file") and i + 1 < len(args) else None)
        if val and _hit(val, dirs):
            return True
    scoped = any(a in ("--global", "--system", "--file", "-f", "--worktree", "--blob") or a.startswith("--file=") for a in args)
    plain = [a for a in args if not a.startswith("-")]
    if plain and plain[0] == "set":
        plain = plain[1:]
    key = plain[0].lower() if plain else ""
    if any(a in ("--edit", "-e") for a in args):
        return True
    if len(plain) < 2 and not any(a in ("--unset", "--unset-all", "--add", "--replace-all", "--remove-section", "--rename-section")
                                  for a in args):
        return False                                     # `git config user.name` reads
    return bool(_GIT_RISKY_KEYS.match(key)) or (scoped and bool(key) and key.split(".")[0] in ("core", "alias", "include", "includeif"))


def _segment_asks(tokens, redirs, dirs, text_of_part, whole_text):
    """True when one simple command writes into a persistence target."""
    for target in redirs:
        if _hit(target, dirs):
            return True
    if not tokens:
        return False
    head = os.path.basename(tokens[0])
    name = re.sub(r"[\d.]+$", "", head).lower()
    rest = tokens[1:]
    ops = _operands(tokens)
    if name == "git":
        return _git_config_writes(tokens, dirs)
    if name in _TOOL_CONFIG:
        words = [t for t in rest if not t.startswith("-")]   # option values stay in: config may follow any of them
        if name == "cargo":
            return False
        if name in ("npm", "pnpm") and words[:1] == ["set"]:           # npm set a b is npm config set a b
            return True
        for i, w in enumerate(words[:-1]):
            if w == "config" and words[i + 1] in ("set", "delete", "edit", "fix", "unset"):
                return True
        if name in ("pip", "pip3") and words[:2] in (["config", "set"], ["config", "unset"], ["config", "edit"]):
            return True
        return False
    if name == "sed":
        if any(t == "--in-place" or t.startswith("--in-place=") or re.match(r"^-[A-Za-z]*i", t) for t in rest):
            return any(_hit(t, dirs) for t in ops)
        return False
    if name == "dd":
        return any(t.startswith("of=") and _hit(t[3:], dirs) for t in rest)
    if name in _DEST_ONLY:
        dest = [t for i, t in enumerate(rest) if i and rest[i - 1] in ("-t", "--target-directory")]
        dest += [t.split("=", 1)[1] for t in rest if t.startswith("--target-directory=")]
        if ops:
            dest += ops if "<" in whole_text else ops[-1:]      # `< file` leaves its file name among the words
        return any(_hit(t, dirs) for t in dest)
    if name in _ANY_OPERAND:
        return any(_hit(t, dirs) for t in ops)
    if name in _FETCHERS:
        out_opts = ("-o", "--output", "-O", "--output-document", "-D", "--dump-header", "-P", "--directory-prefix")
        vals = [t for i, t in enumerate(rest) if i and rest[i - 1] in out_opts]
        vals += [t.split("=", 1)[1] for t in rest if t.startswith(("--output=", "--output-document=", "--directory-prefix="))]
        return any(_hit(t, dirs) for t in vals)
    if name in _EXTRACT:
        vals = [t for i, t in enumerate(rest) if i and rest[i - 1] in ("-C", "--directory", "-d", "-o", "-f", "--file")]
        vals += [t.split("=", 1)[1] for t in rest if t.startswith(("--directory=", "--file="))]
        vals += [t[2:] for t in rest if t.startswith(("-C", "-o")) and len(t) > 2 and not t.startswith("--")]
        return any(_hit(t, dirs) for t in vals)
    if name in ("launchctl",):
        return bool(ops) and ops[0] in ("load", "bootstrap", "submit", "enable", "kickstart", "bootout", "unload")
    if name == "crontab":
        return not any(t == "-l" for t in rest)
    if name == "ssh-keygen":
        return any(i and rest[i - 1] == "-f" and _hit(t, dirs) for i, t in enumerate(rest))
    if name in sl._HOME_INTERP_NAMES or name in ("sqlite", "java", "groovy", "emacs", "r"):
        in_place = any(re.match(r"^-[A-Za-z]*i", t) for t in rest if not t.startswith("--"))
        if in_place and any(_hit(t, dirs) for t in ops):
            return True
        code = whole_text if ("<<" in whole_text and not any(t in sl._CODE_FLAGS for t in rest)) else text_of_part
        lits = re.findall(r"""['"]([^'"\n]{1,300})['"]""", code)
        names = any(_PERSIST_PATH.search(sl._fold(l.replace("\\", "/"))) or _hit(os.path.expanduser(l), dirs) for l in lits)
        return names and bool(_WRITEY.search(code))
    return False


_WRITEY = re.compile(r"open\s*\([^)]*['\"][wax+]|\.write|writeFile|appendFile|\bcopy|\bmove\b|rename|\.replace\(|symlink|chmod|"
                     r"os\.remove|unlink|rmtree|mkdir|\bsystem\(|popen|subprocess|File\.(?:write|open)|file_put_contents|fopen|"
                     r"\bprint\s*>|\btee\b|>>?\s*['\"]", re.I)


_KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "!", "time", "{", "(", "}", ")", "fi", "done", "esac"}
_WRAP_VALUED = {"sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t"}, "timeout": {"-s", "-k", "--signal",
                "--kill-after"}, "gtimeout": {"-s", "-k", "--signal", "--kill-after"}, "env": {"-u", "-C", "-S"}, "nice": {"-n"}, "caffeinate": {"-t", "-w"}, "exec": {"-a"},
                "xargs": set(sl._XARGS_ARG_OPTS), "arch": set(), "stdbuf": set(), "nohup": set(), "command": set(),
                "builtin": set(), "doas": {"-u"}}
_SHELL_NAMES = {"sh", "bash", "zsh", "dash", "ksh"}
_SHELL_C = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")
_EXEC_FLAGS = {"-exec", "-execdir", "-ok", "-okdir"}


class _Unreadable(Exception):
    pass


def _unwrap(tokens):
    """Drop what only prefixes a command: shell keywords (then, do ...), assignments, and the wrappers (sudo -u X, timeout N,
    env, nice, nohup, command, builtin, caffeinate, stdbuf, arch, xargs ...) with their options."""
    tokens = list(tokens)
    while tokens:
        t = tokens[0]
        base = os.path.basename(t)
        if t in _KEYWORDS or sl._ASSIGN.match(t):
            tokens.pop(0)
        elif len(t) > 1 and t[0] in "({" and not t.startswith("{}"):
            tokens[0] = t.lstrip("({")
        elif base in _WRAP_VALUED:
            tokens.pop(0)
            valued = _WRAP_VALUED[base]
            while tokens and tokens[0].startswith("-") and tokens[0] != "-":
                opt = tokens.pop(0)
                if opt == "--":
                    break
                if opt in valued and tokens:
                    tokens.pop(0)
            if base in ("timeout", "gtimeout") and tokens:
                tokens.pop(0)                            # the duration
        else:
            break
    return tokens


def _expand(tokens, redirs, dirs, depth):
    """Yield (tokens, redirs, dirs) for a simple command and for every command it runs: sh -c / -lc / -ic strings, eval,
    find -exec ... ; or +. xargs is a wrapper, so its command is the segment itself."""
    tokens = _unwrap(tokens)
    yield tokens, redirs, list(dirs)
    if not tokens:
        return
    name = re.sub(r"[\d.]+$", "", os.path.basename(tokens[0])).lower()
    if name in ("cd", "pushd", "popd"):
        args = [t for t in tokens[1:] if t not in ("--", "-L", "-P", "-e", "-@")]
        if args and args[0] != "-" and not sl._unevaluable(args[0]):
            dirs[:] = [os.path.realpath(sl._under(args[0], dirs[-1]))]
        elif not args:
            dirs[:] = [os.path.realpath(os.path.expanduser("~"))]
        return
    if depth >= 4:
        if name in _SHELL_NAMES or name == "eval" or name == "find":
            raise _TooBig()
        return
    if name in _SHELL_NAMES:
        for i, t in enumerate(tokens[1:], 1):
            if _SHELL_C.match(t) and i + 1 < len(tokens):
                yield from _walk(tokens[i + 1], dirs, depth + 1)
                break
        else:                                            # bash <<< 'cmd' reaches us as an operand with spaces in it
            for t in tokens[1:]:
                if not t.startswith("-") and re.search(r"\s", t):
                    yield from _walk(t, dirs, depth + 1)
    elif name == "eval":
        yield from _walk(" ".join(tokens[1:]), dirs, depth + 1)
    elif name == "find":
        for i, t in enumerate(tokens):
            if t in _EXEC_FLAGS:
                sub = []
                for u in tokens[i + 1:]:
                    if u in (";", "\\;", "+"):
                        break
                    sub.append(u)
                yield from _expand(sub, [], dirs, depth + 1)


def _walk(command, dirs0, depth=0):
    text = re.sub(r"\$\{HOME\}|\$HOME(?![\w])", os.path.expanduser("~"), re.sub(r"\\\r?\n", "", command))
    parts = sl._shell_parts(text)
    if parts is None:
        raise _Unreadable()
    dirs = list(dirs0)
    for n, (words, redirs) in enumerate(parts):
        yield from _expand(words, redirs, dirs, depth)
        bare = _unwrap(words)
        if n and bare and re.sub(r"[\d.]+$", "", os.path.basename(bare[0])).lower() in _SHELL_NAMES and \
                not any(_SHELL_C.match(t) for t in bare[1:]):
            for before, _ in parts[:n]:                  # echo 'cmd' | sh and echo cmd | sh: the text piped in is run too
                for t in before:
                    if re.search(r"\s", t):
                        yield from _walk(t, dirs, depth + 1)
                if len(before) > 1:
                    yield from _walk(" ".join(before[1:]), dirs, depth + 1)


def _bash_text(command, dirs0):
    """True when a bash command line writes into a persistence target."""
    try:
        items = list(_walk(command, dirs0))
    except _Unreadable:
        return bool(_PERSIST_PATH.search(sl._fold(command.replace("\\", "/"))) or _PERSIST_CODE.search(command)) and \
            bool(re.search(r">|\btee\b|\bcp\b|\bmv\b|\bsed\b|\bln\b|\binstall\b|\bdd\b|\btouch\b|\bchmod\b", command))
    return any(_segment_asks(t, r, d, " ".join(t), command) for t, r, d in items)


_ASK_PERSIST = ("budgit guard: this call writes where code is run later (git hooks, shell startup files, tool or agent "
                "settings, launch agents). Approve only if you really mean to change it.")


def persistence_decision(tool, tool_input, cwd=None):
    """('ask', reason) when the call writes into a place that runs code later; None otherwise. Fails closed."""
    try:
        tool_input = tool_input or {}
        base = os.path.realpath(cwd or os.getcwd())
        if tool in sl._WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            hit = bool(path) and _hit(str(path), [base])
        elif tool == "Bash":
            command = tool_input.get("command")
            if not isinstance(command, str):
                hit = True
            elif len(command) > _CMD_CAP:                # too long to parse: ask only if it names such a place at all
                hit = bool(_PERSIST_CODE.search(command))
            else:
                hit = _bash_text(command, [base])
        else:
            return None
    except Exception:                                    # includes _TooBig
        hit = True
    return ("ask", _ASK_PERSIST) if hit else None


# --- R-304: destructive git ------------------------------------------------------------------------------------------

_ASK_GIT = ("budgit guard: this git command throws work away or rewrites history (%s). Approve only if you really mean it, "
            "or take a backup (a branch or a stash) first.")


def _short_cluster(token):
    return token[1:] if token.startswith("-") and not token.startswith("--") else ""


def _destructive_why(verb, args):
    """A short reason when `git <verb> <args>` throws work away, else None."""
    flags = [a for a in args if a.startswith("-")]
    longs = {a.split("=", 1)[0] for a in flags if a.startswith("--")}
    shorts = "".join(_short_cluster(a) for a in flags)
    plain = [a for a in args if not a.startswith("-")]
    if verb == "reset":
        if longs & {"--hard", "--merge", "--keep"}:
            return "reset " + sorted(longs & {"--hard", "--merge", "--keep"})[0]
    elif verb == "checkout":
        if "f" in shorts or "B" in shorts or longs & {"--force", "--discard-changes"} or "--" in args or "." in plain:
            return "checkout that overwrites files"
    elif verb == "switch":
        if "f" in shorts or "C" in shorts or longs & {"--force", "--discard-changes", "--force-create"}:
            return "switch that discards changes"
    elif verb == "restore":
        if (longs & {"--staged"} or "S" in shorts) and not (longs & {"--worktree"} or "W" in shorts):
            return None
        return "restore overwrites files"
    elif verb == "clean":
        if not (longs & {"--dry-run"} or "n" in shorts):
            return "clean deletes untracked files"
    elif verb == "stash":
        if plain[:1] in (["drop"], ["clear"]):
            return "stash " + plain[0]
    elif verb == "branch":
        if "D" in shorts or "M" in shorts or "C" in shorts or "f" in shorts or longs & {"--force"}:
            return "branch delete or force"
    elif verb == "tag":
        if "d" in shorts or "f" in shorts or longs & {"--delete", "--force"}:
            return "tag delete or force"
    elif verb == "push":
        if "f" in shorts or "d" in shorts or longs & {"--force", "--force-with-lease", "--force-if-includes", "--delete",
                                                      "--mirror", "--prune"}:
            return "push --force or delete"
        if any(a.startswith(("+", ":")) and len(a) > 1 for a in plain):
            return "push --force or delete"
    elif verb == "reflog":
        if plain[:1] in (["expire"], ["delete"]):
            return "reflog " + plain[0]
    elif verb == "update-ref":
        if "d" in shorts:
            return "update-ref -d"
    elif verb in ("prune", "filter-branch", "filter-repo", "replace"):
        return verb
    elif verb == "gc":
        if any(a.startswith("--prune") for a in args):
            return "gc --prune"
    elif verb == "worktree":
        if plain[:1] in (["remove"], ["prune"]) and ("f" in shorts or longs & {"--force"} or plain[:1] == ["prune"]):
            return "worktree " + plain[0] + " (forced)"
    elif verb == "checkout-index":
        if "f" in shorts or "--force" in longs:
            return "checkout-index -f"
    elif verb == "read-tree":
        if "--reset" in longs:
            return "read-tree --reset"
    return None


def _git_segment(tokens):
    at, _, _ = sl._git_global_scan(tokens)
    if at is None:
        return None
    return _destructive_why(tokens[at], tokens[at + 1:])


_COARSE_GIT = re.compile(
    r"\bgit\b[^;&|\n]*?\b(?:reset\b[^;&|\n]*--(?:hard|merge|keep)|clean\b|restore\b|stash\s+(?:drop|clear)|"
    r"branch\b[^;&|\n]*\s-[A-Za-z]*[DMCf]|push\b[^;&|\n]*(?:\s-[A-Za-z]*[fd]\b|--force|--delete|--mirror|\s\+|\s:)|"
    r"checkout\b[^;&|\n]*(?:\s-[A-Za-z]*[fB]\b|--force|\s--\s|\s\.(?:\s|$))|switch\b[^;&|\n]*(?:\s-f|--force|--discard)|"
    r"reflog\s+(?:expire|delete)|update-ref\s+-d|filter-branch|prune\b)")


def _git_text(command):
    text = re.sub(r"\\\r?\n", "", command)
    if sl._SUBST.search(text) and _COARSE_GIT.search(text):
        return "a git command this guard cannot read exactly"
    try:
        items = list(_walk(text, [os.getcwd()]))
    except _Unreadable:
        return "a git command this guard cannot read exactly" if _COARSE_GIT.search(text) else None
    for tokens, _, _ in items:
        if tokens and os.path.basename(tokens[0]).lower() == "git":   # GIT and Git run git on a case-blind disk
            why = _git_segment(tokens)
            if why:
                return why
    return None


def destructive_git_decision(tool, tool_input):
    """('ask', reason) for a Bash call that runs a git command that throws work away; None otherwise. Fails closed."""
    if tool != "Bash":
        return None
    try:
        command = (tool_input or {}).get("command")
        if not isinstance(command, str):
            why = "a git command this guard could not read"
        elif "git" not in re.sub(r"[\"'\\\\]", "", command).lower():   # g""it, g\it and GIT are still git
            return None
        elif len(command) > _CMD_CAP:
            why = "a very long command that names git"
        else:
            why = _git_text(command)
    except Exception:
        why = "a git command this guard could not read"
    return ("ask", _ASK_GIT % why) if why else None
