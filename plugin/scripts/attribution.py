"""R-940 / I-1526 / R-1037: no Claude attribution in commits or PRs when the user's setting says so (default on). Pure
function of the call, the setting and the message files the call names. Denies a Bash call whose `git commit` message or
`gh pr create|edit` body carries a Co-Authored-By trailer naming Claude or anthropic.com, or a "Generated with Claude
Code" line. A message that merely mentions Claude passes. The setting is `no_claude_attribution` in config.json
(studiolib.no_claude_attribution); no key or an unreadable config counts as on.

R-1037 (allowlist): every `git commit` is found by studiolib._git_invocations, the guard's one reader of a command
(env, sudo, command, nice, time, nohup, an absolute or relative path to git, `git -C p` / `git -c k=v`, bash -c, eval).
The message is judged only when it is readable as text: -m literals (the command text), -F/--file or -t/--template with
a regular file (read), -F - when stdin is a literal in the command (heredoc, printf/echo of a literal) or a literal file
(`cat f |`, `< f`). Any other -F/-t source ASKS, it never passes: /dev/fd/N, /dev/stdin from a pipe the guard cannot
read, a process substitution <(...), a path built by $ or a backtick, a pipe or redirect it cannot read, -t with
--cleanup=whitespace or verbatim (the template becomes the message as is), a file that is not a regular file. A commit
with no -m, -F or -t opens the editor and is not judged (as before)."""
import os
import re

REASON = "R-940: no Claude attribution in commits or PRs (core rule); remove the trailer."
ASK_REASON = ("R-1037: the guard cannot read this commit message (%s). Door: write the message to an existing file first and "
              "pass it with -F <file>. Rule: no Claude attribution in commits.")      # hook.py cuts a reason at 200 chars: cause and door first
_TRAILER = re.compile(r"co-authored-by[ \t]*:[^\n]*(claude|anthropic\.com)", re.I)
_GENERATED = re.compile(r"generated\s+with\s+(\[[^\]\n]{0,40}\]|[^\n]{0,40}?)\s*claude\s+code", re.I)
_GH_PR = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
_HD_SUBST = re.compile(r"""^\s*\$\(\s*cat\s*<<-?\s*(['"])(\w+)\1[ \t]*\n(?:(?![ \t]*\2[ \t]*\n)[^\n]*\n)*[ \t]*\2[ \t]*\n\s*\)\s*$""")
_MAX_READ = 200000
_STDIN_NAMES = ("-", "/dev/stdin", "/dev/fd/0")
_BAD_CLEANUP = ("whitespace", "verbatim")
_LONG_VALUE = {"--message", "--author", "--date", "--reedit-message", "--reuse-message", "--fixup", "--squash",
               "--trailer", "--pathspec-from-file"}


# R-1042 r2/r3: the Co-Authored-By pattern anchored at a line start (a leading indent allowed; the separator may have spaces
# before the colon, as git's own trailer parser allows). The git hooks read a whole message file, where a trailer is a line of its
# own, so a message that only quotes the phrase mid-sentence passes. The guard reads a command line, where the message sits after a
# quote or a flag, so it keeps the loose match (anchored=False) over the very same pattern text. The "Generated with Claude Code"
# phrase is banned anywhere in a message (r3), so it is never anchored.
_TRAILER_LINE = re.compile(r"^[ \t]*" + _TRAILER.pattern, re.I | re.M)
_FOLD = re.compile(r"\r?\n[ \t]+")


def unfold(text):
    """A line that starts with whitespace continues the previous one (a folded trailer value): join them with one space."""
    return _FOLD.sub(" ", text)


def first_match(text, anchored=False):
    """R-1042: the first offending line of `text` (trimmed), or None. The one pattern source: the guard below, the git
    commit-msg hook and the git pre-push hook (scripts/githook_check.py) all ask this function, so they cannot drift.
    `anchored` (the hooks) = a Co-Authored-By trailer must start its line, read as written AND with folded lines unfolded;
    False = anywhere (the guard)."""
    for view in ((text, unfold(text)) if anchored else (text,)):
        m = (_TRAILER_LINE.search(view) if anchored else _TRAILER.search(view)) or _GENERATED.search(view)
        if m:
            start = view.rfind("\n", 0, m.start()) + 1
            end = view.find("\n", m.end())
            return view[start:end if end >= 0 else len(view)].strip()
    return None


def has_attribution(text):
    return first_match(text) is not None


def _setting_on():
    """The user's setting; any failure to read it counts as on (fail closed)."""
    try:
        import studiolib
        return studiolib.no_claude_attribution() is not False
    except Exception:
        return True


def _dynamic(word):
    """A word the shell builds at run time ($VAR, $(...), backtick): the guard cannot read its value."""
    return "$" in word or "`" in word or ">(" in word or "<(" in word


READ_VERBS = frozenset(("log", "show", "grep", "rev-list", "shortlog", "blame", "diff", "describe", "for-each-ref", "cat-file"))
_MSG_VERBS = frozenset(("commit", "commit-tree", "merge", "tag", "notes", "rebase", "cherry-pick", "revert", "am"))


def _dyn_arg(arg):
    """An argument the shell builds at run time ($, a backtick, <( , >( ; `$@` and `$*` hold a $). The one exemption is a
    `$(cat <<'EOF' ... EOF)` heredoc whose text sits in the command, scanned whole (alone or behind -m / --message=)."""
    if not _dynamic(arg):
        return False
    return not _HD_SUBST.match(re.sub(r"^(--message=|-m)", "", arg))


def _msg_verb_asks(args, cfg, asks):
    """R-1037 r5, the single rule: a git verb that can write a message asks when ANY argv token is dynamic, when it adds
    a trailer through -c trailer.*, or through --trailer (an abbreviation of 4 or more characters counts)."""
    if any(_dyn_arg(a) for a in args):
        asks.append("an argument built at run time ($, a backtick, <( or >()")
    if any(a.startswith("--") and _abbrev(a.partition("=")[0], "--trailer", 4) for a in args):
        asks.append("a trailer added by --trailer")
    if any(k.lower().startswith("trailer.") for k in cfg):
        asks.append("a trailer added by -c trailer.*")


def _punct(sl, t):
    return bool(t) and not isinstance(t, sl._Lit) and all(c in sl._PUNCT for c in t)


def _walk(command):
    """Segments of the command in order: [{"sep", "toks", "redir": [(op, word)], "hd"}], and whether anything runs before
    the last segment or writes a file (`;`, `&&`, a newline, `>`), the only way a file that is missing now can exist when
    git reads it. Same tokenizer and heredoc cut as studiolib._git_invocations."""
    import studiolib as sl
    text, _ = sl._extract_heredocs(command.strip())
    toks = sl._shell_tokens(re.sub(r"\\\r?\n", " ", text))
    segs, sequenced, i = [], False, 0
    cur = {"sep": ";", "toks": [], "redir": [], "hd": False}
    while i < len(toks):
        t = toks[i]
        if sl._HD_MARK.match(t):
            cur["hd"] = True
        elif _punct(sl, t):
            if t in ("<", "<<<"):
                if t == "<" and cur["toks"] and cur["toks"][-1].isdigit() and cur["toks"][-1] != "0":
                    i += 2                                  # `3< f`: another descriptor, not stdin
                    continue
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt is not None and not _punct(sl, nxt):
                    cur["redir"].append((t, nxt))
                    i += 2
                else:
                    cur["redir"].append((t, None))
                    i += 1
                continue
            if t == "<(":                                   # process substitution: its text is not the commit's
                cur["redir"].append(("<(", None))
                depth, i = 1, i + 1
                while i < len(toks) and depth > 0:
                    if _punct(sl, toks[i]):
                        depth += toks[i].count("(") - toks[i].count(")")
                    i += 1
                continue
            segs.append(cur)
            cur = {"sep": t, "toks": [], "redir": [], "hd": False}
            if t in (";", "&&", "||", "&", "\n") or ">" in t:
                sequenced = True
        else:
            cur["toks"].append(t)
        i += 1
    segs.append(cur)
    return segs, sequenced


def _is_commit_seg(seg):
    names = [os.path.basename(t) for t in seg["toks"]]
    return "git" in names and "commit" in names[names.index("git") + 1:]


def _is_gh_seg(seg):
    names = [os.path.basename(t) for t in seg["toks"]]
    return "gh" in names and "pr" in names[names.index("gh") + 1:]


def _reads_stdin(seg):
    return any(t in _STDIN_NAMES or t in ("-F-", "--file=-", "--file=/dev/stdin", "--file=/dev/fd/0", "--body-file=-")
               for t in seg["toks"])


def _unreadable_path(word):
    return word is None or word == "" or _dynamic(word) or word.startswith(("/dev/", "/proc/"))


def _producer(seg):
    """(files, literal, unreadable) for the segment on the left of a pipe."""
    toks = seg["toks"]
    if not toks:
        return [], False, True                              # `$(...) |`, `(a; b) |`: not a plain command
    head = os.path.basename(toks[0])
    args = toks[1:]
    if head in ("echo", "printf"):
        return [], True, any(_dynamic(a) for a in args)
    if head == "cat":
        names = [a for a in args if not a.startswith("-") or a == "-"]
        if seg["hd"] and names in ([], ["-"]):
            return [], True, False                          # `cat <<EOF | git ...`: the text is in the command, scanned whole
        if not names or "-" in names or any(_unreadable_path(n) for n in names):
            return [], False, True
        return names, False, False
    return [], False, True


def _stdin_feed(segs, locate):
    """(files that feed stdin, unreadable) for every located git/gh segment that reads stdin."""
    files, unreadable, seen = [], False, False
    for k, seg in enumerate(segs):
        if not locate(seg) or not _reads_stdin(seg):
            continue
        seen, found = True, False
        for op, word in seg["redir"]:
            found = True
            if op == "<":
                if _unreadable_path(word):
                    unreadable = True
                else:
                    files.append(word)
            elif op == "<<<":
                unreadable = unreadable or word is None or _dynamic(word)
            else:
                unreadable = True
        if seg["hd"]:
            found = True                                    # the heredoc text sits in the command, scanned whole
        if k > 0 and "|" in seg["sep"] and "||" not in seg["sep"]:
            found = True
            got, literal, bad = _producer(segs[k - 1])
            files.extend(got)
            unreadable = unreadable or bad
        if not found:
            unreadable = True                               # stdin comes from nowhere the guard can see
    return files, unreadable or not seen


def _abbrev(name, full, least):
    return len(name) >= least and full.startswith(name)


def _commit_sources(args):
    """([("F" or "t", value or None)], --cleanup value) for the options of one `git commit`."""
    srcs, cleanup, i = [], None, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            is_file, is_tpl = _abbrev(name, "--file", 5), _abbrev(name, "--template", 4)
            if is_file or is_tpl or _abbrev(name, "--cleanup", 4) or name in _LONG_VALUE or _abbrev(name, "--trailer", 4):
                if not eq:
                    val = args[i] if i < len(args) else None
                    i += 1
                if is_file or is_tpl:
                    srcs.append(("F" if is_file else "t", val))
                elif _abbrev(name, "--message", 4):
                    srcs.append(("m", val))
                elif _abbrev(name, "--trailer", 4):
                    srcs.append(("T", val))
                elif _abbrev(name, "--cleanup", 4):
                    cleanup = val
        elif a.startswith("-") and len(a) > 1:
            for j in range(1, len(a)):
                ch = a[j]
                if ch in "Ft" or ch in "mcC":
                    val = a[j + 1:]
                    if not val:
                        val = args[i] if i < len(args) else None
                        i += 1
                    if ch in "Ftm":
                        srcs.append((ch, val))
                    break
                if ch in "Su":                              # value only when attached: -S<key>, -uno
                    break
    return srcs, cleanup


def _read(full):
    """(text, state): state "ok", "missing" (does not exist) or "unreadable" (exists but is not a readable regular file,
    or is larger than _MAX_READ: a cut-off read is never judged as clean)."""
    if not os.path.exists(full):
        return None, "missing"
    if not os.path.isfile(full):
        return None, "unreadable"
    try:
        with open(full, "r", errors="replace") as fh:
            text = fh.read(_MAX_READ + 1)
    except OSError:
        return None, "unreadable"
    return (None, "unreadable") if len(text) > _MAX_READ else (text, "ok")


_GIT_VERBS = frozenset("""add am annotate apply archive bisect blame branch bundle cat-file check-attr check-ignore check-mailmap
check-ref-format checkout checkout-index cherry cherry-pick citool clean clone column commit commit-graph commit-tree config
count-objects credential describe diff diff-files diff-index diff-tree difftool fast-export fast-import fetch fetch-pack
filter-branch fmt-merge-msg for-each-ref for-each-repo format-patch fsck gc get-tar-commit-id grep gui hash-object help
hook index-pack init interpret-trailers log ls-files ls-remote ls-tree maintenance mailinfo mailsplit merge merge-base
merge-file merge-index merge-tree mergetool mktag mktree multi-pack-index mv name-rev notes pack-objects pack-redundant
pack-refs patch-id prune prune-packed pull push range-diff read-tree rebase receive-pack reflog remote repack replace
request-pull rerere reset restore rev-list rev-parse revert rm send-email shortlog show show-branch show-index show-ref
sparse-checkout stash status stripspace submodule switch symbolic-ref tag unpack-file unpack-objects update-index update-ref
update-server-info var verify-commit verify-pack verify-tag version whatchanged worktree write-tree""".split())


def _msg_flags(args):
    """True when an argument looks like a message option (-m, -F, -t, a cluster holding one, --message/--file/--template)."""
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            name = a.partition("=")[0]
            if any(len(name) >= 4 and full.startswith(name) for full in ("--message", "--file", "--template")):
                return True
        elif a.startswith("-") and len(a) > 1 and any(ch in "mFt" for ch in a[1:]):
            return True
    return False


def _unresolved_git(segs):
    """True when a command word is built at run time (a $VAR, a backtick, $(...)) and is followed by `commit` or another
    built word and a message option: git by another name, which the guard cannot read."""
    import studiolib as sl
    for seg in segs:
        toks = list(seg["toks"])
        while toks and (sl._ASSIGN.match(toks[0]) or os.path.basename(toks[0]) in sl._ENV_WRAPPERS or toks[0].startswith("-")):
            toks.pop(0)
        if len(toks) > 1 and _msg_flags(toks[1:]) and ((_dynamic(toks[0]) and (toks[1] == "commit" or _dynamic(toks[1])))
                                                       or toks[0] == "commit"):
            return True
    return False


def _full(path, base):
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.join(base or "", path)


def _judge(command, cwd):
    """(texts to scan, why-unreadable list) for the message sources of the command's commits and PR bodies."""
    import studiolib as sl
    texts, asks, paths, stdin_files = [], [], [], []
    stdin_for_git = stdin_for_gh = False
    segs, walked = None, False
    calls = sl._git_invocations_once(command) if ("git" in command or "commit" in command) else []
    # `git config commit.template X` / `commit.cleanup Y` in the same command sets them like -c does
    cfg_all = {}
    for verb, args, cfg, _env in calls:
        cfg_all.update({k: v for k, v in cfg.items() if k.startswith("commit.")})
        if verb == "config":
            plain = [a for a in args if not a.startswith("-")]
            for k, key in enumerate(plain[:-1]):
                if key.lower() in ("commit.template", "commit.cleanup"):
                    cfg_all[key.lower()] = plain[k + 1]
    templates = []
    for verb, args, cfg, _env in calls:
        if verb == "@tangled":
            asks.append("too much nesting to read")
        elif verb != "commit":
            if verb in _MSG_VERBS:
                _msg_verb_asks(args, cfg, asks)
            if verb not in _GIT_VERBS and (("alias." + verb) in cfg or _msg_flags(args) or _dynamic(verb)):
                asks.append("git %s is not a built-in verb (an alias?) and takes a message option" % verb[:30])
            continue
        else:
            srcs, cleanup = _commit_sources(args)
            cleanup = cleanup or cfg_all.get("commit.cleanup")
            base = _full(cfg["@C"], cwd) if cfg.get("@C") else cwd
            _msg_verb_asks(args, cfg, asks)
            if any(k == "T" for k, _v in srcs):
                asks.append("a trailer added by --trailer")
            if cfg_all.get("commit.template"):
                srcs.append(("t", cfg_all["commit.template"]))
            for kind, val in srcs:
                if kind == "T":
                    continue
                if kind == "m":
                    continue
                if val in _STDIN_NAMES:
                    stdin_for_git = True
                elif _unreadable_path(val):
                    asks.append("-%s %s" % (kind, val if val else "without a readable value"))
                else:
                    paths.append(_full(val, base))
                    if kind == "t" and cleanup in _BAD_CLEANUP:
                        asks.append("a template with --cleanup=%s" % cleanup)
    if calls or "commit" in command:
        segs, _ = _walk(command)
        walked = True
        if _unresolved_git(segs):
            asks.append("a git command word built at run time")
    if _GH_PR.search(command):
        try:
            words = sl._shell_tokens(command)
        except Exception:
            words = command.split()
        for k, w in enumerate(words):
            if w in ("-F", "--body-file") and k + 1 < len(words):
                if words[k + 1] in _STDIN_NAMES:
                    stdin_for_gh = True
                else:
                    paths.append(_full(words[k + 1], cwd))
            elif w.startswith("--body-file="):
                paths.append(_full(w.split("=", 1)[1], cwd))
    if (paths or stdin_for_git or stdin_for_gh) and not walked:
        segs, _ = _walk(command)
    if stdin_for_git or stdin_for_gh:
        if stdin_for_git:
            files, bad = _stdin_feed(segs, _is_commit_seg)
            stdin_files.extend(files)
            if bad:
                asks.append("-F - fed by a pipe or redirect the guard cannot read")
        if stdin_for_gh:
            stdin_files.extend(_stdin_feed(segs, _is_gh_seg)[0])
    for full in paths + [_full(f, cwd) for f in stdin_files]:
        text, state = _read(full)
        if state == "ok":
            texts.append(text)
        else:                                               # missing now is unreadable now: an earlier part may create it
            if state == "missing":
                asks.append("%s is missing" % os.path.basename(full))
            else:
                asks.append("%s is not a file the guard can read whole" % os.path.basename(full))
    return texts, asks


def decision(tool, tool_input, cwd=None, enabled=None):
    """("deny", reason) for a commit or PR text that carries a Claude attribution while the setting is on, ("ask", reason)
    when a commit message comes from a source the guard cannot read, else None. `enabled` None reads the user's setting."""
    if tool != "Bash" or not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    if not ("git" in command or "commit" in command or _GH_PR.search(command)):
        return None
    if enabled is None:
        enabled = _setting_on()
    if not enabled:
        return None
    texts, asks = _judge(command, cwd)
    if _GH_PR.search(command) or _writes_calls(command) or asks:
        if has_attribution(command):
            return ("deny", REASON)
    for text in texts:
        if has_attribution(text):
            return ("deny", REASON)
    if asks:
        return ("ask", ASK_REASON % "; ".join(dict.fromkeys(asks)))
    return None


def _writes_calls(command):
    """The git calls whose literal argv can become a message: all of them, unless the WHOLE command has the one read-only
    shape (R-1061). A literal pattern in such a command is a search term, not a message. Anything else fails closed."""
    calls = _commit_calls(command)
    if calls and _read_only_shape(command):
        return []
    if not calls and re.match(r"\s*(?:\d?>>?|&>>?)\s*\S+\s+(?:\S*/)?git\s", command):
        return [("@unseen", [], {}, {})]            # `>m git log ...`: the redirect hides the git call from the parser
    return calls


_PIPE_READERS = frozenset(("grep", "egrep", "fgrep", "head", "tail", "wc", "cat", "cut", "tr", "nl", "column"))
_ALLOWED_REDIRECTS = re.compile(r"(?<!\S)(?:2>&1|[2]?>\s*/dev/null)(?!\S)")


def _read_only_shape(command):
    """True only for `git [-C path] [--no-pager] READ_VERB args [2>&1 | >/dev/null | 2>/dev/null] [| reader args]...`:
    one pipeline, no substitution or heredoc, no env prefix, no other global option, no other redirect, readers only."""
    import studiolib as sl
    if any(x in command for x in ("\n", "\r", ";", "`", "$(", "<(", ">(", "<<", "&&", "||")):
        return False
    text = _ALLOWED_REDIRECTS.sub(" ", command)
    if "&" in text:
        return False
    try:
        toks = sl._shell_tokens(text)
    except Exception:
        return False
    segs, cur = [], []
    for t in toks:
        if not isinstance(t, sl._Lit) and (t == "|" or _punct(sl, t) or "<" in t or ">" in t):
            if t != "|":
                return False
            segs.append(cur)
            cur = []
        else:
            cur.append(t)
    segs.append(cur)
    if any(c in t for seg in segs for t in seg for c in "${}*?[~\\"):
        return False                                # the shell expands these after the guard has read the words
    head = segs[0]
    if not head or head[0] != "git":
        return False
    i = 1
    while i < len(head) and head[i] in ("-C", "--no-pager"):
        if head[i] == "-C":
            if i + 1 >= len(head) or _dynamic(head[i + 1]):
                return False
            i += 2
        else:
            i += 1
    if i >= len(head) or head[i] not in READ_VERBS or _read_verb_acts(head[i], head[i + 1:]):
        return False
    for seg in segs[1:]:
        if not seg or seg[0] not in _PIPE_READERS:
            return False
    return True


def _read_verb_acts(verb, args):
    """True when a read verb is told to write a file or run a command: any abbreviation of --output, --ext-diff, --textconv
    or --open-files-in-pager (4 or more characters), and grep's -O in any short cluster."""
    for a in args:
        name = a.split("=", 1)[0]
        if name.startswith("--") and len(name) >= 4 and any(f.startswith(name) or name.startswith(f)
                                                             for f in ("--output", "--ext-diff", "--textconv", "--open-files-in-pager")):
            return True
        if verb == "grep" and a.startswith("-") and not a.startswith("--") and "O" in a:
            return True
    return False


def _commit_calls(command):
    import studiolib as sl
    return sl._git_invocations_once(command)
