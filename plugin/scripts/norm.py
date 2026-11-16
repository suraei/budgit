"""R-988: the user's rule "never rm; Trash or archive only" as an opt-in PreToolUse check. Off unless config.json says
`"refuse_rm": true` (studiolib.refuse_rm). Denies a Bash call that runs rm, unlink, rmdir, srm or shred, `find -delete`,
`find -exec rm`, or `xargs rm`, also inside loops, `&&`/`;`/`|` chains, `bash -c` strings, `$(...)` and sudo/env prefixes,
and names the Trash move instead. It reads commands with the guard's own tokenizer (studiolib._shell_tokens), so the
word rm inside a quoted message, `git rm`, `grep rm` and a path that merely contains rm pass."""
import os
import sys

REMOVERS = {"rm", "unlink", "rmdir", "srm", "shred"}
_FIND_EXEC = ("-exec", "-execdir", "-ok", "-okdir")
# A wrapper (sudo, env, xargs ...) is followed by options and their values; the real command is the first word we know
# is not a remover-or-wrapper argument. These end the scan: the command is a different, harmless one.
_STOP = {"git", "echo", "printf", "cat", "ls", "grep", "egrep", "fgrep", "rg", "head", "tail", "wc", "mv", "cp",
         "trash", "trash-put", "touch", "mkdir", "python", "python3", "node", "make", "open", "diff", "stat", "file"}
_MAX_DEPTH = 4
# KNOWN GAP: interpreters (perl -e unlink, python3 -c os.remove, node -e fs.rmSync, ruby -e File.delete) delete files
# without naming a remover; they are out of scope here and are not refused.
_APPLET_BOXES = {"busybox", "toybox"}
_EXTRA_WRAPPERS = {"chrt", "taskset"}
# Options that take a value, per wrapper: the value is skipped so a value like `git` in `sudo -u git rm f` is not
# mistaken for the command. A wrapper not listed here keeps the plain scan.
_VALUE_OPTS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"},
    "env": {"-u", "-C", "-S"},
    "nice": {"-n"},
    "timeout": {"-s", "-k"},
    "ionice": {"-c", "-n", "-p", "-P", "-u"},
    "chrt": {"-p"},
    "taskset": {"-c", "-p"},
}
_POSITIONAL = {"timeout": 1, "chrt": 1, "taskset": 1}     # DURATION / PRIORITY / CPU MASK before the command
_LONG_FLAGS = {"--preserve-status", "--foreground", "--non-interactive", "--login", "--reset-home", "--background",
               "--stdin", "--askpass", "--preserve-env", "--ignore-environment", "--null"}


def _git_clean_forces(toks):
    """`git [opts] clean` with -f/--force and no dry run (-n/--dry-run): it deletes untracked files."""
    k = 1
    while k < len(toks) and toks[k].startswith("-"):
        k += 2 if toks[k] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    if k >= len(toks) or toks[k] != "clean":
        return False
    args = toks[k + 1:]
    short = [a for a in args if a.startswith("-") and not a.startswith("--")]
    if "--dry-run" in args or any("n" in a for a in short):
        return False
    return "--force" in args or any("f" in a for a in short)


def trash_hint():
    """The platform's Trash, read at run time: macOS ~/.Trash/, Linux ~/.local/share/Trash/files/, else None."""
    if sys.platform == "darwin":
        return "~/.Trash/"
    if sys.platform.startswith("linux"):
        return "~/.local/share/Trash/files/"
    return None


def reason():
    import i18n
    path = trash_hint()
    if path is None:
        return i18n.t("norm.refuse_other")
    return i18n.t("norm.refuse", trash=path)


def _setting_on():
    """Opt-in, so fail OPEN: any failure to read the setting counts as off (unlike the attribution rule, which fails
    closed). A broken config must never stop the user's ordinary commands."""
    try:
        import studiolib
        return studiolib.refuse_rm() is True
    except Exception:
        return False


def _segments(command):
    import studiolib as sl
    command, _ = sl._extract_heredocs(command)          # heredoc bodies are text, not commands
    segs, cur = [], []
    for tok in sl._shell_tokens(command.replace("\\\n", " ")):
        if tok and not isinstance(tok, sl._Lit) and all(c in sl._PUNCT for c in tok):
            if cur:
                segs.append(cur)
            cur = []
        elif not sl._HD_MARK.match(tok):
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def _has_remover(command, depth=0):
    import studiolib as sl
    if depth > _MAX_DEPTH:
        return False
    for inner in sl._SUBSHELL.findall(command):            # $(...) and `...` run even inside words
        if _has_remover(inner[0] or inner[1], depth + 1):
            return True
    for toks in _segments(command):
        if _segment_has_remover(toks, depth):
            return True
    return False


def _segment_has_remover(toks, depth):
    import studiolib as sl
    toks = list(toks)
    while toks and (sl._ASSIGN.match(toks[0]) or toks[0] in sl._KEYWORDS):
        toks = toks[1:]
    if not toks:
        return False
    head = os.path.basename(toks[0])
    if head in REMOVERS:
        return True
    if head in _APPLET_BOXES:                              # busybox rm x, toybox rm x
        return len(toks) > 1 and _segment_has_remover(toks[1:], depth + 1)
    if head == "git":
        return _git_clean_forces(toks)
    if head in sl._ENV_WRAPPERS or head in _EXTRA_WRAPPERS:   # sudo [-u x] rm, env A=1 rm, xargs -n1 rm, nice -n 10 rm
        valued = _VALUE_OPTS.get(head)
        positional = _POSITIONAL.get(head, 0)
        k = 1
        while k < len(toks):
            tok = toks[k]
            base = os.path.basename(tok)
            if base in REMOVERS:
                return True
            if base in sl._SHELLS or base in ("find", "eval", "git") or base in sl._ENV_WRAPPERS or base in _EXTRA_WRAPPERS \
                    or base in _APPLET_BOXES:
                return _segment_has_remover(toks[k:], depth)
            if sl._env_split_string(toks, k) is not None and head == "env" and _has_remover(sl._env_split_string(toks, k), depth + 1):
                return True
            if valued is not None:                          # skip this wrapper's options AND their values
                if tok == "--":
                    k += 1
                    continue
                if tok.startswith("--"):
                    if "=" in tok or tok in _LONG_FLAGS:
                        k += 1
                        continue
                    # A long option we cannot classify may take a value, so the command word is unknown: refuse only
                    # when a remover (rm, busybox rm, git clean -f, bash -c 'rm') starts any later suffix.
                    return any(_segment_has_remover(toks[j:], depth + 1) for j in range(k + 1, len(toks)))
                if tok.startswith("-") and len(tok) > 1:
                    k += 2 if tok in valued else 1
                    continue
                if head == "env" and sl._ASSIGN.match(tok):
                    k += 1
                    continue
                if positional > 0:                          # timeout DURATION, chrt PRIORITY, taskset MASK
                    positional -= 1
                    k += 1
                    continue
            if base in _STOP:
                return False
            k += 1
        return False
    if head == "find":
        for k, tok in enumerate(toks):
            if tok == "-delete":
                return True
            if tok in _FIND_EXEC and k + 1 < len(toks) and _segment_has_remover(toks[k + 1:], depth + 1):
                return True
        return False
    if head in sl._SHELLS:                                  # bash -c / -lc 'script'
        for k, tok in enumerate(toks[1:], 1):
            if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
                return k + 1 < len(toks) and _has_remover(toks[k + 1], depth + 1)
        return False
    if head == "eval":
        return _has_remover(" ".join(toks[1:]), depth + 1)
    return False


def decision(tool, tool_input, cwd=None, enabled=None):
    """("deny", reason) for a Bash command that removes files while the user's `refuse_rm` setting is on, else None.
    `enabled` None reads the setting. Off costs one config read."""
    if tool != "Bash" or not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return None
    if enabled is None:
        enabled = _setting_on()
    if not enabled:
        return None
    if _has_remover(command):
        return ("deny", reason())
    return None
