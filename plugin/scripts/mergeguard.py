"""A merge that conflicts inside the plugin's own checkout leaves conflict markers in the code the hooks run from; the hook then fails
closed and every tool of every session is blocked. This guard is an ALLOW LIST for that checkout: a git command that reaches, or may
reach, it passes only in one of the shapes below; anything else is refused. It runs no git command itself.

For each git command of a Bash line (writeguard's shell walker splits `;`, `&&`, `||`, pipes and sh -c strings, and removes quotes) the
guard works out every folder the command can act on: each working folder the line can be in after its cd / pushd / popd words, then -C,
--git-dir, --work-tree, and the GIT_DIR and GIT_WORK_TREE prefixes. A folder it cannot read counts as "maybe the live checkout". A
command that reaches only other folders (another repo, a worktree of this one) is not judged.

Allowed in the live checkout: read verbs (status, log, show, diff, rev-parse, rev-list, ls-files, ls-tree, cat-file, merge-base,
describe, blame, grep, shortlog, for-each-ref, show-ref, config --get / --list, plain branch listing, worktree list / add /
prune, stash list, remote -v), `merge-tree --write-tree [--no-messages | --name-only] <2 or 3 plain revisions>` (the coordinator only),
fetch, add, commit, `merge --ff-only <one ref>` (-q allowed; git itself refuses a non-fast-forward), and
--version / --help / -h. Anything else is refused: merge without --ff-only, pull, rebase, cherry-pick, am, revert, stash pop or apply,
checkout, switch, reset, restore, apply, any -c setting, an unknown global option, an unreadable command word or verb.
decision(tool, tool_input, cwd) -> ("deny", reason) or None; the caller turns an exception into a deny."""
import os
import re

import writeguard

_READ_VERBS = {"status", "log", "show", "diff", "rev-parse", "rev-list", "ls-files", "ls-tree", "cat-file", "merge-base",
               "describe", "blame", "grep", "shortlog", "for-each-ref", "show-ref", "check-ignore", "cherry", "diff-tree"}
_OTHER_VERBS = {"merge-tree", "merge", "pull", "rebase", "cherry-pick", "am", "revert", "stash", "checkout", "switch", "reset", "restore", "apply",
                "fetch", "add", "commit", "branch", "worktree", "remote", "config", "clean", "push", "tag", "rm", "mv", "init", "clone"}
_KNOWN_VERBS = _READ_VERBS | _OTHER_VERBS
_ENV_TARGET = re.compile(r"\bGIT_(?:DIR|WORK_TREE)=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")
_UNREADABLE_CHARS = re.compile(r"[$`\"'\\]")
_NO_VALUE_GLOBALS = {"--no-pager", "-P", "--bare", "--no-replace-objects", "--literal-pathspecs",
                     "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs", "--no-optional-locks", "--no-lazy-fetch",
                     "--no-advice"}
_SPECIAL = {"--version", "--help", "-h"}
_CD_WORDS = {"cd", "pushd", "popd", "chdir"}

_REASON = ("budgit guard: %s. The plugin's own checkout is where the hooks run from, and a conflict there leaves markers in its code and "
           "blocks every tool. Allowed there: read commands (status, log, diff, show ...), fetch, add, commit, worktree add, and "
           "`git merge --ff-only <branch>`, and `git merge --quit`. Merge in a worktree (git worktree add <dir> -b <branch> <start>), then "
           "`git merge --ff-only <branch>` here.")


def checkout_root():
    """The git checkout this plugin runs from: the nearest folder above this file that holds a .git entry, else None."""
    here = os.path.dirname(os.path.realpath(__file__))
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _toplevel(path):
    """The folder with a .git entry at or above `path` (a worktree has its own, so it differs from the main checkout), else None."""
    here = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _bad(text):
    return bool(_UNREADABLE_CHARS.search(text))


def _parse(tokens):
    """-> dict for `git [global options] <verb> args`, or None when the words are not a git command.
    ops: the (kind, value) of each -C ("C") and --git-dir / --work-tree ("D"), in order; a value that cannot be read is None.
    bad_global: a -c setting, --config-env, or a global option outside the plain list. head_bad / verb_bad: unreadable words."""
    head = tokens[0] if tokens else ""
    head_bad = _bad(head)
    if head_bad:
        if not any(t.lower() in _KNOWN_VERBS or t == "-C" or _bad(t) for t in tokens[1:]):
            return None                                  # a variable word with nothing git-like after it
    elif os.path.basename(head).lower() != "git":
        return None
    p = {"verb": None, "args": [], "ops": [], "bad_global": False, "head_bad": head_bad, "verb_bad": False, "special": False}
    i, n = 1, len(tokens)
    while i < n and tokens[i].startswith("-") and tokens[i] != "--":
        t = tokens[i]
        name, eq, value = t.partition("=")
        if t in _SPECIAL:
            p["special"] = True
            i += 1
        elif t == "-C" or (t.startswith("-C") and not t.startswith("--")) or name in ("--git-dir", "--work-tree"):
            if t == "-C":
                raw, step, kind = (tokens[i + 1] if i + 1 < n else ""), 2, "C"
            elif t.startswith("-C"):
                raw, step, kind = t[2:], 1, "C"
            else:
                raw, step, kind = ((value, 1) if eq else ((tokens[i + 1] if i + 1 < n else ""), 2)) + ("D",)
            p["ops"].append((kind, None if _bad(raw) else raw))
            i += step
        elif t in _NO_VALUE_GLOBALS:
            i += 1
        else:
            p["bad_global"] = True                       # -c anything, --config-env, an unknown option: never in the live checkout
            i += 2 if t in ("-c", "--config-env") else 1
    if i < n:
        p["verb"], p["args"] = tokens[i], tokens[i + 1:]
        p["verb_bad"] = _bad(tokens[i])
    return p


_CONFIG_SCOPES = {"--local", "--global", "--system", "--worktree"}
_CONFIG_GET = {"--get", "--get-all", "--get-regexp"}
_XCRUN_NAME = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.+-]*")


def _config_ok(args):
    """R-1294 slice 2: an optional scope flag, then ONE read flag. A --get form takes a key (no leading dash, readable) and at most one
    value pattern; --list / -l takes only --name-only, --show-origin or -z after it. Every write form, --file, -e and a bare `key value`
    is refused."""
    rest = list(args)
    if rest and rest[0] in _CONFIG_SCOPES:
        rest = rest[1:]
    if not rest:
        return False
    flag, tail = rest[0], rest[1:]
    if flag in _CONFIG_GET:
        return 1 <= len(tail) <= 2 and all(t and not t.startswith("-") and not _bad(t) for t in tail)
    if flag in ("--list", "-l"):
        return all(t in ("--name-only", "--show-origin", "-z") for t in tail)
    return False


def _xcrun_find_form(words):
    """R-1294 slice 2: `xcrun -f|--find <one bare name>` only prints a path. The word must be exactly `xcrun` (not a path), and without
    -f or --find xcrun RUNS the tool, so every other shape is refused."""
    return len(words) == 3 and words[0] == "xcrun" and words[1] in ("-f", "--find") and bool(_XCRUN_NAME.fullmatch(words[2]))


def _reader_why(verb, args):
    """R-1294 slice 2: every read verb and branch go through the ONE shared reader check (studiolib `_git_reader_ok`) with the live
    extra table. There is no fallback: a fault in it reaches decision()'s own handler and denies the call. The refusal names the word."""
    sl = writeguard.sl
    if sl._git_reader_ok(verb, args, extra=sl._LIVE_EXTRA_SPECS):
        return None
    token = sl._git_reader_refused(verb, args, extra=sl._LIVE_EXTRA_SPECS)
    shown = "".join(ch for ch in str(token) if ch.isprintable())[:80]       # no control, bidi or zero-width character is echoed
    if sl._glob_word(shown) and not sl._expansion_word(shown):
        return _glob_text(verb, shown)
    if sl._expansion_word(shown):
        why = "a shell expansion ($, backtick or ~) in a read argument cannot be judged (una expansion de shell no se puede juzgar)"
    else:
        why = "which is not on it (no figura en la lista)"
    return "the read allowlist (lista de lectura): git %s refused at '%s', %s" % (verb, shown, why)


_FETCH_FLAGS = {"-q", "--quiet", "--prune", "-p", "--tags", "--no-tags", "--all"}
_ADD_FLAGS = {"-A", "--all", "-u", "--update", "-v", "--"}
_COMMIT_FLAGS = {"-q", "--quiet"}
_PRUNE_FLAGS = {"-n", "--dry-run", "-v"}


def _expansion_why(verb, args):
    """R-1300 security MED-B: a verb argument that holds a `$`, a backtick or a tilde expansion cannot be judged (the guard cannot prove
    quoting after lexing). The rule comes first, then the token, in English and Spanish; a commit also gets the way out."""
    for a in args:
        if writeguard.sl._expansion_word(a):
            shown = "".join(ch for ch in a if ch.isprintable())[:80]
            way = ("; rephrase the message without `$`, a backtick or a leading `~` (reescribe el mensaje sin `$`, sin comilla inversa y sin `~` al "
                   "principio)") if verb == "commit" else ""
            return ("the expansion rule (regla de expansion): git %s argument '%s' holds a shell expansion ($, backtick or ~) that cannot be judged "
                    "(lleva una expansion de shell que no se puede juzgar)%s" % (verb, shown, way))
    skip = False
    for a in args:                                       # R-1300 MED-3: a word that starts with a glob character, before a real `--`
        if skip:
            skip = False
        elif a == "--":
            break
        elif a in _NEXT_VALUE_OPTS:                      # the walker's own value-taking words: their next word is a value, `--` included
            skip = True
            continue
        if writeguard.sl._glob_word(a):
            return _glob_text(verb, a)
    return None


_NEXT_VALUE_OPTS = {"-m", "--message", "-b"}            # commit's message and worktree add's branch name (see `_commit_why`, `_worktree_why`)


def _paren_why(command):
    """R-1300 security r2 MED-3b: a parenthesis outside quotes (a zsh glob qualifier such as `.*(e:...:)`, `(a|b)`, an extglob `@(`, a
    subshell) can run or build a word this guard cannot see. A quoted one is only text."""
    raw = writeguard.sl._unquoted_raw(command)
    m = re.search(r"[()]", raw)
    if not m:
        return None
    shown = "".join(ch for ch in command[m.start():m.start() + 24] if ch.isprintable())
    return ("the parenthesis rule (regla de parentesis): the line runs git and holds an unquoted parenthesis at '%s' (a zsh glob qualifier, "
            "(a|b), an extglob or a subshell) that cannot be judged (lleva un parentesis sin comillas que no se puede juzgar); quote it or "
            "rephrase (ponlo entre comillas o reescribe)" % shown)


def _glob_text(verb, token):
    shown = "".join(ch for ch in str(token) if ch.isprintable())[:80]
    return ("the glob rule (regla de glob): git %s argument '%s' starts with a glob character (* ? [) before `--` and may expand to an option "
            "(empieza con un comodin antes de `--` y puede expandirse a una opcion); put pathspecs after `--`, or start the pattern with a literal "
            "character (pon las rutas despues de `--`, o empieza el patron con un caracter literal)" % (verb, shown))


def _fetch_why(args):
    for a in args:
        if a.startswith("-"):
            if a not in _FETCH_FLAGS:
                return "git fetch with a flag outside the plain list (an abbreviation, --upload-pack ...)"
        elif "::" in a:
            return "git fetch from a remote helper address"
    return None


def _add_why(args):
    return None if all(a in _ADD_FLAGS or not a.startswith("-") for a in args) else "git add with a flag outside the plain list"


def _commit_why(args):
    """Only a message, -q, --author=, `--` and paths: -e, -F, --amend, --fixup, -C, -c, --no-verify and the rest are refused."""
    skip, dd = False, False
    for a in args:
        if skip:
            skip = False
        elif dd or not a.startswith("-"):
            continue
        elif a == "--":
            dd = True
        elif a in ("-m", "--message"):
            skip = True
        elif a in _COMMIT_FLAGS or a.startswith("--message=") or a.startswith("--author=") or (a.startswith("-m") and not a.startswith("--")):
            continue
        else:
            return "git commit with a flag outside the plain list"
    return "git commit that ends after -m" if skip else None


def _common_dir(live):
    """The checkout's common git dir, read from files only: `<live>/.git` is the folder itself, or a `gitdir: <path>` file whose folder may
    hold a `commondir` file (a linked worktree). None when it cannot be read."""
    dot = os.path.join(live, ".git")
    try:
        if os.path.isdir(dot):
            return os.path.realpath(dot)
        with open(dot, encoding="utf-8") as fh:
            text = fh.read().strip()
        if not text.startswith("gitdir:"):
            return None
        gdir = os.path.realpath(os.path.join(live, text[len("gitdir:"):].strip()))
        with open(os.path.join(gdir, "commondir"), encoding="utf-8") as fh:
            return os.path.realpath(os.path.join(gdir, fh.read().strip()))
    except (OSError, UnicodeDecodeError):
        return None


def _registered(src, live):
    """True when `<common>/worktrees/<id>/gitdir` holds exactly the realpath of `<src>/.git`. Plain open and read; an unreadable file is
    not a registration, and the main worktree (which has no such file) is never registered."""
    common = _common_dir(live)
    if not common:
        return False
    want = os.path.realpath(os.path.join(src, ".git"))
    try:
        ids = os.listdir(os.path.join(common, "worktrees"))
    except OSError:
        return False
    for ident in ids:
        try:
            with open(os.path.join(common, "worktrees", ident, "gitdir"), encoding="utf-8") as fh:
                if fh.read().strip() == want:
                    return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _outside(path, live):
    """R-1360 r2 M-B, L-1: the real path of the path itself (a symlink is followed), compared with the real live root by normcase and, for
    every existing ancestor, by samefile (a case-variant spelling on APFS is the same folder)."""
    cur, root = os.path.realpath(path), os.path.realpath(live)
    while True:
        if os.path.normcase(cur) == os.path.normcase(root):
            return False
        try:
            if os.path.lexists(cur) and os.path.samefile(cur, root):
                return False
        except OSError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            return True
        cur = parent


def _move_why(rest, live, subagent):
    """R-1360 G2a: `worktree move <src> <dst>` for the coordinator only: two absolute plain paths and no flag; <src> registered in the common
    dir; both outside the checkout; <dst> absent."""
    if subagent:
        return "git worktree move from a helper or subagent"
    if len(rest) != 2 or any(a.startswith("-") for a in rest):
        return "git worktree move other than `<src> <dst>` with no flag"
    for a in rest:
        if not os.path.isabs(a) or ".." in a or re.search(r"[$`*?\[\"'\\]", a):
            return "git worktree move with a path that is not absolute and plain"
    src, dst = rest
    if not _registered(src, live):
        return "git worktree move of a folder that is not a registered linked worktree"
    if not (_outside(src, live) and _outside(dst, live)):
        return "git worktree move that touches the live checkout"
    if os.path.lexists(dst):
        return "git worktree move onto a path that already exists"
    return None


def _worktree_why(args, targets, live, subagent=False, command=""):
    sub, rest = args[:1], args[1:]
    if sub == ["move"]:
        if re.search(r"[$`]", command):      # the walker rewrites $HOME before the words are split, so the raw line is checked too
            return "git worktree move on a line with a $ or a backtick"
        return _move_why(rest, live, subagent)
    if sub == ["list"]:
        return None if all(a == "--porcelain" for a in rest) else "git worktree list with another flag"
    if sub == ["prune"]:
        return None if all(a in _PRUNE_FLAGS for a in rest) else "git worktree prune with another flag"
    if sub != ["add"]:
        return "git worktree other than list, add or prune"
    positional, k = [], 0
    while k < len(rest):
        a = rest[k]
        if a == "--detach":
            pass
        elif a == "-b" and k + 1 < len(rest) and not rest[k + 1].startswith("-"):
            if _bad(rest[k + 1]):
                return "git worktree add with an unreadable branch name"
            k += 1
        elif a.startswith("-"):
            return "git worktree add with a flag outside --detach and -b <name>"
        else:
            positional.append(a)
        k += 1
    if not positional or len(positional) > 2 or any(_bad(a) for a in positional):
        return "git worktree add without a readable path and at most one commit-ish"
    path = os.path.expanduser(positional[0])
    for t in (targets if not os.path.isabs(path) else [os.sep]):      # an absolute path does not depend on the folder the command runs in
        if t is None:
            return "git worktree add whose target folder cannot be read"
        where = os.path.realpath(os.path.join(t, path))
        if where == live or where.startswith(live + os.sep):
            return "git worktree add into the live checkout"
    return None


def _allowed(p, targets=(), live="", command="", subagent=False):
    """None when the shape is on the allow list, else a short description of what is refused."""
    verb, args = p["verb"], p["args"]
    if p["head_bad"] or p["verb_bad"]:
        return "a git command whose command word or verb cannot be read"
    if p["bad_global"]:
        return "a git command with a -c setting, an alias or a global option outside the plain list"
    if p["special"] or verb is None:
        return None
    if verb in _READ_VERBS or verb == "branch":
        return _reader_why(verb, args)
    why = _expansion_why(verb, args)                     # R-1300: the same word test as the read verbs, for every other verb
    if why:
        return why
    if verb == "merge-tree":
        return _merge_tree_why(args, command, subagent)
    if verb == "fetch":
        return _fetch_why(args)
    if verb == "add":
        return _add_why(args)
    if verb == "commit":
        return _commit_why(args)
    if verb == "config":
        return None if _config_ok(args) else "git config that is not one plain read (scope flag, then --get / --get-all / --get-regexp <key> [<pattern>] or --list)"
    if verb == "worktree":
        return _worktree_why(args, targets, live, subagent, command)
    if verb == "stash":
        return None if args == ["list"] else "git stash other than a bare `stash list`"
    if verb == "remote":
        return None if args in ([], ["-v"], ["--verbose"]) else "git remote other than -v"
    if verb == "merge":
        if args == ["--quit"]:
            return None                                  # the recovery verb: it only forgets a half-done merge, with nothing else beside it
        flags =[a for a in args if a.startswith("-")]
        refs = [a for a in args if not a.startswith("-")]
        if "--ff-only" in flags and set(flags) <= {"--ff-only", "-q", "--quiet"} and len(refs) == 1:
            return None
        return "git merge that is not `--ff-only <one ref>`"
    return "git %s" % verb


def _from_home(raw, risky):
    """R-1300 MED-1: on a risky line (studiolib `_home_expansion_risky`) a folder word that is a tilde word, or holds the real home the
    walker rewrote a HOME expansion to, may name anything once the line has changed HOME: it is unknown."""
    if raw is None:
        return False
    if writeguard.sl._dir_tilde(raw):
        return True                                      # ~+ ~- ~N: unknown on any line
    if not risky:
        return False
    home = os.path.expanduser("~")
    return raw.startswith("~") or "=~" in raw or ":~" in raw or bool(home and home != os.sep and home in raw)


def _targets(p, cwds, env_raws, risky=False):
    """Every folder the command can act on: for each possible working folder, where -C led, each --git-dir / --work-tree, and the
    GIT_DIR / GIT_WORK_TREE values. None stands for a folder that cannot be read."""
    out = []
    for c in cwds:
        here, extra = c, []
        for kind, raw in p["ops"]:
            f = None if (raw is None or here is None or _from_home(raw, risky)) else os.path.join(here, os.path.expanduser(raw))
            if kind == "C":
                here = f
            else:
                extra.append(f)
        out += [here] + extra
        out += [None if (raw is None or c is None or _from_home(raw, risky)) else os.path.join(c, os.path.expanduser(raw)) for raw in env_raws]
    return out


def _reaches(targets, live):
    for f in targets:
        if f is None:
            return True                                  # unknown: maybe the live checkout
        if os.path.exists(f) and _toplevel(f) == live:
            return True
    return False


def _cdpath_unknown(name, args, cdpath):
    """R-1300 security r2 MED-2: CDPATH can send a relative `cd` anywhere; the shell ignores it for `/`, `.`, `..`, `./` and `../` only."""
    if not cdpath or name not in ("cd", "chdir", "pushd") or not args:
        return False
    t = args[0]
    return not (os.path.isabs(t) or t in (".", "..", "-") or t.startswith(("./", "../", "~")))


def _runs_vcs(items):
    """True when a segment of the walked line is the program's own command (a parse that fails counts: fail closed)."""
    for tokens, _redirs, _dirs in items:
        if tokens and os.path.basename(tokens[0]) not in _CD_WORDS:
            try:
                if _parse(tokens) is not None:
                    return True
            except Exception:
                return True
    return False


def _after_cd(tokens, cwds, risky=False, cdpath=False):
    """The possible working folders after a cd / pushd / popd word: the old ones stay (a subshell or a branch may not run it)."""
    args = [t for t in tokens[1:] if not t.startswith("-") or t == "-"]
    name = os.path.basename(tokens[0])
    if name == "popd" or (name in ("cd", "chdir", "pushd") and args and (args[0] == "-" or _bad(args[0]))) or (name == "pushd" and not args) or \
            _cdpath_unknown(name, args, cdpath) or \
            (name in ("cd", "chdir", "pushd") and risky and _from_home(args[0] if args else "~", risky)):
        new = [None]
    else:
        target = args[0] if args else "~"
        new = [os.path.realpath(os.path.join(c, os.path.expanduser(target))) for c in cwds if c is not None]
    return cwds + [x for x in new if x not in cwds]


_DOOR_WHY = ("the word git", "a line with", "a program name", "a git-* program")
_COMMIT_TEXT = re.compile(r"\bcommit\b.*(?<!\S)(?:-m|--message)")
_OWN_BUDGIT = re.compile(r"(?:^|[\s/;&|(])budgit(?:\s|$)")


def _door(command, why):
    """R-1360 task C: a refusal for a program word inside free text names the door that works. Text only: the decision is the same.
    A commit message goes through `commit -F <file>`; the free text of an own budgit verb goes through `--text-file <file>`."""
    if not why.startswith(_DOOR_WHY):
        return ""
    if _COMMIT_TEXT.search(command):
        return "; a commit message with a program word: write it to a file and pass the message with -F <file>"
    if _OWN_BUDGIT.search(command):
        return "; free text with a program word: put the text in a file and pass --text-file <file>"
    return ""


def decision(tool, tool_input, cwd=None, agent_id=None):
    """('deny', reason) for a Bash call that runs a git command that reaches, or may reach, the plugin's own checkout outside the allowed
    shapes; None otherwise."""
    if tool != "Bash":
        return None
    subagent = bool(agent_id)                # R-1360 G1: the hook passes the call's agent_id; a helper or subagent gets no merge-tree door
    command = (tool_input or {}).get("command")
    if not isinstance(command, str):
        return ("deny", _REASON % "a command this guard could not read")
    root = checkout_root()
    if not root:
        return None                  # no .git above this module: an installed copy, with no checkout to break, so nothing to guard
    live = os.path.realpath(root)
    bare = re.sub(r"[\"'\\]", "", command).lower()
    if "git" not in bare and "$" not in bare and "`" not in bare and "{" not in bare and "\\\n" not in command:
        return None                  # no git word and nothing that could build one: the cheap exit for most Bash calls
    base = os.path.realpath(cwd or os.getcwd())
    try:
        items = list(writeguard._walk(command, [base]))
    except (writeguard._Unreadable, writeguard._TooBig):
        maybe = _toplevel(base) == live or live in command or re.search(r"\bcd\b|\bpushd\b|-C|GIT_|--git-dir|--work-tree|\$|`", bare)
        return ("deny", _REASON % "a git command line this guard could not read") if maybe else None
    env_raws = [None if re.search(r"[$`]", m.group(1)) else m.group(1).strip("\"'") for m in _ENV_TARGET.finditer(command)]
    risky = writeguard.sl._home_expansion_risky(command)          # R-1300 MED-1: a HOME or tilde folder word is unknown on such a line
    cdpath = _runs_vcs(items)                                     # R-1300 r2 MED-2: CDPATH matters only when the line runs the program
    cwds = [base]
    for tokens, _redirs, _dirs in items:
        if tokens and os.path.basename(tokens[0]) in _CD_WORDS:
            cwds = _after_cd(tokens, cwds, risky, cdpath)
    may = any(c is None or _toplevel(c) == live for c in cwds) or live in command or bool(
        re.search(r"[$`]|GIT_|--git-dir|--work-tree|(?<!\S)-C", command))
    reach = list(cwds)                       # for the word rule the working folders always count; -C and the like only add members
    walked = [base]
    for tokens, _redirs, _dirs in items:
        if tokens and os.path.basename(tokens[0]) in _CD_WORDS:
            walked = _after_cd(tokens, walked, risky, cdpath)
            continue
        try:
            p = _parse(tokens)
            if p is not None:
                reach += _targets(p, walked, env_raws, risky)
        except Exception:
            reach.append(None)
    if re.search(r"\$\(|`", command) or _PROC_SUBST.search(command) or _wrapper_with_options(command) or writeguard.sl._lexer_unsafe(command):
        reach.append(None)                   # a substitution or a wrapper with options can run a git command this walk cannot see
    try:
        why = _line_why(command, bare, _reaches(reach, live), live) if may else None
    except Exception as exc:
        why = "a line this guard could not judge (%s)" % exc.__class__.__name__
    if why:
        return ("deny", _REASON % (why + _door(command, why)))
    cwds = [base]
    for tokens, _redirs, _dirs in items:
        if tokens and os.path.basename(tokens[0]) in _CD_WORDS:
            cwds = _after_cd(tokens, cwds, risky, cdpath)
            continue
        try:
            p = _parse(tokens)
            if p is None:
                continue
            targets = _targets(p, cwds, env_raws, risky)
            if not _reaches(targets, live):
                continue
            why = _paren_why(command) or _allowed(p, [t for t in targets], live, command, subagent) or _home_why(p, command)
        except Exception as exc:
            return ("deny", _REASON % ("a git command this guard could not judge (%s)" % exc.__class__.__name__))
        if why:
            return ("deny", _REASON % why)
    return None


_ALLOWED_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE")
_SAFE_ENV = ("GIT_EDITOR=true", "GIT_EDITOR=:", "GIT_OPTIONAL_LOCKS=0")     # R-1360 G1: exact whole-token prefix doors, nothing else
_REV_WORD = re.compile(r"[A-Za-z0-9/._-]+")
_GIT_ASSIGN = re.compile(r"(?<![A-Za-z0-9_])GIT_[A-Za-z0-9_]*=")


def _merge_tree_why(args, command, subagent):
    """R-1360 G1: `merge-tree --write-tree [--no-messages | --name-only] <2 or 3 plain revision words>`, for the coordinator only, with no
    GIT_ prefix. A revision word is letters, digits and / . _ - with no leading dash and no `..`."""
    if subagent:
        return "git merge-tree from a helper or subagent"
    if _GIT_ASSIGN.search(command):
        return "git merge-tree with a GIT_ prefix"
    if not args or args[0] != "--write-tree":
        return "git merge-tree other than `--write-tree [--no-messages | --name-only] <2 or 3 revisions>`"
    rest = args[1:]
    if rest and rest[0] in ("--no-messages", "--name-only"):
        rest = rest[1:]
    if not 2 <= len(rest) <= 3 or not all(_REV_WORD.fullmatch(a) and not a.startswith("-") and ".." not in a for a in rest):
        return "git merge-tree with other than 2 or 3 plain revision words"
    return None
_WRAPPERS = {"env", "exec", "command", "arch", "stdbuf", "nice", "nohup", "time", "timeout", "xargs", "sudo"}


def _wrapper_with_options(command):
    """True when a segment's head is a wrapper that carries an option, other than `nice -n <number>` or an env assignment."""
    parts = writeguard.sl._shell_parts(re.sub(r"\\\r?\n", "", command))
    for words, _redirs in (parts or []):
        i = 0
        while i < len(words) and writeguard.sl._ASSIGN.match(words[i]):
            i += 1
        if i >= len(words) or os.path.basename(words[i]).lower() not in _WRAPPERS:
            continue
        rest = [w for w in words[i + 1:] if not writeguard.sl._ASSIGN.match(w)]
        if rest and rest[0].startswith("-"):
            if os.path.basename(words[i]).lower() == "nice" and len(rest) >= 2 and rest[0] == "-n" and re.fullmatch(r"[0-9]+", rest[1]):
                continue
            return True
    return False


def _home_why(p, command):
    """R-1294 slice 2: the walker rewrites $HOME to the real home before lexing, so the `$` scan cannot see it. A read verb or branch
    argument that came from it (it holds the home path) is refused when the line can change HOME first (studiolib
    `_home_expansion_risky`). A `-C $HOME/x` before the verb is not an argument and stays admitted."""
    if p["verb"] is None:
        return None                                      # R-1300: every verb, not only the read ones
    if not writeguard.sl._home_expansion_risky(command):
        return None
    home = os.path.expanduser("~")
    for a in p["args"]:
        if home and home != os.sep and home in a:
            shown = "".join(ch for ch in a if ch.isprintable())[:80]
            return ("the HOME rule (regla HOME de la lista de lectura): git %s argument '%s' came from $HOME on a line that can change HOME "
                    "first (viene de $HOME en una linea que puede cambiar HOME antes)" % (p["verb"], shown))
    return None


_PROC_SUBST = re.compile(r"[<>]\(|(?:^|(?<=[\s;&|(<>]))=\(")      # <( and >( ; zsh =( at the start of a word


def _unquoted_view(command):
    """The line with the text inside single quotes, and inside double quotes that hold no `$(` or backtick, replaced by Q (the quote
    marks stay). A `{...}` seen only inside such quotes is therefore gone; a brace outside them, or one spanning a quote mark, stays.
    An unbalanced quote leaves the rest of the line as it is."""
    out, i, n = [], 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n:
            out.append(command[i:i + 2])
            i += 2
        elif c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                out.append(command[i:])
                break
            out.append("'Q'")
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                j += 2 if command[j] == "\\" else 1
            if j >= n:
                out.append(command[i:])
                break
            body = command[i + 1:j]
            out.append(command[i:j + 1] if ("$(" in body or "`" in body) else '"Q"')
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


_MASK_HEADS = frozenset(("grep", "egrep", "fgrep", "rg", "ps", "head", "tail", "cat", "wc", "sort", "cut", "tr", "nl", "diff",
                         "jq", "shasum"))      # readers that never execute their argument (uniq is out: `uniq IN OUT` writes OUT)
_MASK_EXEC_FLAGS = ("--pre", "--hostname-bin", "--filter", "--pager", "--view", "--format-open", "--config", "--save-config")
_SHELLS = frozenset(("sh", "bash", "zsh", "dash", "ksh", "eval", "source", "."))


_NAME_IN_TEXT = re.compile(r"(?i)(?<![a-z0-9])git(?![a-z])")


def _name_hidden_by_quotes(command):
    """True when the program word (not inside a longer word such as `digit`) sits in text that `_unquoted_view` would mask."""
    return len(_NAME_IN_TEXT.findall(command)) != len(_NAME_IN_TEXT.findall(_unquoted_view(command)))


def _sort_runs_or_writes(words):
    """sort: any long option that is a prefix of --compress-program or --output (so an unambiguous abbreviation too), `-o`, or a short
    cluster holding o (`-no`, `-ofile`). The value may be joined with `=` or in the next word: the option itself is enough to refuse."""
    for w in words[1:]:                      # F15 F1: every word, past `--` too (`-T --` takes the `--` as its value)
        if w == "--":
            continue
        if w.startswith("--"):
            name = w[2:].partition("=")[0]
            if name and ("compress-program".startswith(name) or "output".startswith(name)):
                return True
        elif re.fullmatch(r"-[A-Za-z0-9]*o[A-Za-z0-9,.=/_-]*", w):
            return True
    return False


def _masking_allowed(command):
    """Round 6: quote masking applies only when every segment's head word (after assignments) is a pattern reader and no wrapper or
    shell word sits anywhere on the line; any other line is judged raw, so a quoted brace handed to a shell that parses it again is seen."""
    parts = writeguard.sl._shell_parts(re.sub(r"\\\r?\n", "", command))
    if not parts:
        return False
    # R-1360 F14 shape 1: the heads in _MASK_HEADS run no part of their argument (awk, sed, perl, python, node, ruby, env, xargs and every
    # other head keep the raw judgement: they can run code). No quoted text may hold the program word; the quotes must be balanced
    # (`_shell_parts` gives no parts for an open quote).
    if _NAME_IN_TEXT.search(command) and _name_hidden_by_quotes(command):
        return False
    for words, _redirs in parts:
        if not words or writeguard.sl._ASSIGN.match(words[0]):
            return False                     # F15 F5: VAR=value before the head (PERL5OPT, RIPGREP_CONFIG_PATH, PATH, DYLD_*) changes what runs
        head = words[0]
        if head not in _MASK_HEADS:          # F15 F4: a bare name only, never a path (./cat, /tmp/grep, bin/head)
            return False
        if any(w.startswith(_MASK_EXEC_FLAGS) or re.fullmatch(r"-[A-Za-z0-9]*Q[A-Za-z0-9]*", w) for w in words):
            return False                     # rg --pre*, --hostname-bin and the ugrep exec flags run a program
        if head == "rg" and any(re.fullmatch(r"-[A-Za-z0-9]*z[A-Za-z0-9]*", w) or (w.startswith("--") and len(w) > 2 and
                                "search-zip".startswith(w[2:].partition("=")[0])) for w in words[1:]):
            return False                     # rg -z / --search-zip (any cluster, abbreviation or = form) runs external decompressors
        if head == "sort" and _sort_runs_or_writes(words):
            return False                     # sort -o / --output writes and --compress-program runs one
        if head == "diff" and any((w.startswith("--") and len(w) > 2 and "paginate".startswith(w[2:].partition("=")[0])) or re.fullmatch(r"-[A-Za-z0-9]*l[A-Za-z0-9]*", w) for w in words[1:]):
            return False                     # diff -l and --paginate run pr
        if any(os.path.basename(w).lower() in _WRAPPERS or os.path.basename(w).lower() in _SHELLS for w in words):
            return False
    return True


_SCANNER = "gitleaks"
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_./@+-]+")
_LOG_OPTS_OK = re.compile(r"[A-Za-z0-9_./@^~:=, -]+")
_LOG_OPTS_BAD = re.compile(r"(?i)output|exec|config|textconv|ext-diff|(?:^|\s)-c(?:\s|$)")


def _scanner_read_form(words, live):
    """R-1199: `<scanner> git [--log-opts V] [--no-banner] [--source P] [--report-path P]` and nothing else. The scanner's own subcommand
    is the VCS name; with only these options it reads history. --report-path must be an absolute path outside the live checkout (it is
    written); --log-opts takes plain revision words (no output, exec, config or -c); every other word or option refuses the form."""
    head = words[0] if words else ""
    literal = head == _SCANNER or (os.path.isabs(head) and os.path.basename(head) == _SCANNER
                                   and os.path.dirname(head) in writeguard.sl._TRUSTED_BIN)      # never a relative path or a basename match
    if len(words) < 2 or not literal or words[1] != "git":
        return False
    args, j = words[2:], 0
    if "--redact" not in args:
        return False                                     # round 5: without --redact the scanner prints secrets
    while j < len(args):
        name, eq, val = args[j].partition("=")
        if name in ("--no-banner", "--redact") and not eq:
            j += 1
            continue
        if name not in ("--log-opts", "--source", "--report-path"):
            return False
        if eq:
            j += 1
        elif j + 1 < len(args):
            val, j = args[j + 1], j + 2
        else:
            return False
        if name == "--log-opts":
            if not _LOG_OPTS_OK.fullmatch(val) or _LOG_OPTS_BAD.search(val):
                return False
        elif not _PLAIN_VALUE.fullmatch(val):
            return False
        elif name == "--report-path":
            if not _report_path_ok(val, live):
                return False
    return True


def _temp_roots():
    roots = ["/tmp", "/private/tmp"]
    if os.environ.get("TMPDIR"):
        roots.append(os.environ["TMPDIR"])
    return [os.path.realpath(r).casefold().rstrip(os.sep) for r in roots]


def _report_path_ok(val, live):
    """--report-path is written: absolute, under a system temp folder (after realpath, casefolded), and never inside the live root by identity."""
    if not os.path.isabs(val) or not live:
        return False
    real = os.path.realpath(val)
    folded = real.casefold()
    if not any(folded.startswith(r + os.sep) for r in _temp_roots() if r):
        return False
    probe = real
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    while True:
        try:
            if os.path.samefile(probe, live):
                return False
        except OSError:
            return False
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return True


def _name_words(text):
    """How many words of `text` are the program's name or a `<name>-*` program (a disguised name is counted only after the text is normalised)."""
    count = 0
    for piece in re.split(r"[\s;&|`$()]+", text):
        base = os.path.basename(piece).lower() if piece else ""
        if base == "git" or base.startswith("git-"):
            count += 1
    return count


_RO_FLAGS = frozenset(("--hidden", "--no-ignore"))
# R-1360 fp2: letters that only change what grep or rg prints or how it matches (never e or f, which give a pattern, never a letter that takes a
# value or runs a program). Short clusters of these keep the pattern slot. grep alone adds z, Z and T (rg -z runs a decompressor, rg -T takes a value).
_RO_CLUSTER = r"-[abchHiIlLnNoPqrRsSuUvVwxyEFG]+"
_RO_CLUSTER_GREP = r"-[abchHiIlLnNoPqrRsSuUvVwxyEFGzZT]+"
_RO_VALUED = frozenset(("-g", "--glob", "-t", "-A", "-B", "-C", "-m"))
_RG_EXEC = re.compile(r"^--(?:pre|pre-glob|hostname-bin)(?:=|$)")


def _rg_runs_program(words):
    """R-1360 r2 M-A: ripgrep runs a program through --pre, --pre-glob or --hostname-bin, whatever the program is: refuse the segment."""
    return any(os.path.basename(w) == "rg" for w in words) and any(_RG_EXEC.match(w) for w in words)


# R-1360 F14 shape 4: the archive and git reads a subagent may run with no ask. Same style as _RO_CLUSTER: letters that only change what is
# listed, tested or printed, never one that takes a value, extracts, updates, touches the archive (unzip -T) or runs a program (unzip -M).
_UNZIP_LETTERS = frozenset("lvptq")                  # list, verbose list, print to stdout, test, quiet
_UNZIP_MODES = frozenset("lvpt")                     # at least one must be there, else unzip extracts
_ZIPINFO_FIRST = re.compile(r"-Z[12hmsztTvl]*")      # zipinfo mode must be the first option; its letters only choose a listing format
_ZIPINFO_LETTERS = frozenset("Z12hmsztTvl")
_SEVENZ_FLAGS = frozenset(("-slt", "-ba", "-bd"))    # 7z l / t only: technical listing, bare output, no progress
_ARCHIVE_PATH = re.compile(r"/[A-Za-z0-9_./+@=,:-]*")
_ARCHIVE_MEMBER = re.compile(r"[A-Za-z0-9_./+@=,:-]+")
_GIT_STAT_FLAGS = frozenset(("--stat", "--name-only", "--name-status", "--numstat"))   # formats that never run an external diff
_GIT_DIFF_OPTS = _GIT_STAT_FLAGS | {"--cached", "--staged", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames"}


def _unzip_archive(args):
    """The archive of `unzip` words that only list, test or print (-l -v -p -t, or zipinfo mode -Z first), else None. Any other option
    (-d, -o, -x, -j, -n, -f, -u, -P, -T, -M ...) or a form with none of the mode letters extracts, so it is not this form."""
    zipinfo = bool(args) and bool(_ZIPINFO_FIRST.fullmatch(args[0]))
    letters = _ZIPINFO_LETTERS if zipinfo else _UNZIP_LETTERS
    mode, operands = zipinfo, []
    for w in args:
        if w.startswith("-"):
            if not re.fullmatch(r"-[A-Za-z0-9]+", w) or not set(w[1:]) <= letters:
                return None
            mode = mode or bool(set(w[1:]) & _UNZIP_MODES)
        else:
            operands.append(w)
    if not mode or not operands or not _ARCHIVE_PATH.fullmatch(operands[0]) or not all(_ARCHIVE_MEMBER.fullmatch(m) for m in operands[1:]):
        return None
    return operands[0]


def _sevenz_archive(args):
    """The archive of `7z l <archive>` / `7z t <archive>` (listing or testing only; x, e, a, u, d and the rest write), else None."""
    if not args or args[0] not in ("l", "t"):
        return None
    flags, operands = [], []
    for w in args[1:]:
        (flags if w.startswith("-") else operands).append(w)
    if not set(flags) <= _SEVENZ_FLAGS or len(operands) != 1 or not _ARCHIVE_PATH.fullmatch(operands[0]):
        return None
    return operands[0]


def archive_read(command):
    """R-1360 F14 shape 4: the absolute archive path when `command` is ONE plain simple `unzip` (-l -v -p -t -Z) or `7z` (l, t) command with
    nothing else on the line (no redirect, chain, pipe, env prefix, substitution or wrapper), else None. The caller judges the path as a read."""
    toks = writeguard.sl._sole_simple(command)
    if not toks:
        return None
    if toks[0] == "unzip":
        return _unzip_archive(toks[1:])
    if toks[0] == "7z":
        return _sevenz_archive(toks[1:])
    return None


_TAR_NAMES = ("tar", "bsdtar", "gtar")
_TAR_EXEC_LONG = ("use-compress-program", "to-command", "checkpoint-action", "info-script", "new-volume-script", "rsh-command", "rmt-command")
_TAR_SHORT_EXEC = re.compile(r"-[A-Za-z0-9]*[IF]")                  # -I / -F in a cluster, with or without a glued value (re.match: no tail anchor)
_TAR_REMOTE = re.compile(r"(?:--file=|-[A-Za-z]*f)?[^/:\s=-][^/:]*:")   # host:path (rsh/ssh) as the archive: tar starts a remote program for it


def tar_runs_program(command):
    """R-1360 F14 shape 4: a tar segment with an option that runs a program, in any mode (`tar -t` included): -I or -F (alone, in a short cluster
    or in the old-style first word), --use-compress-program, --to-command, --checkpoint-action, --info-script, --new-volume-script,
    --rsh-command (any abbreviation of them, with = or a separate value). A line the walker cannot split counts when it holds a tar word
    and such an option anywhere."""
    parts = writeguard.sl._shell_parts(re.sub(r"\\\r?\n", "", command))
    if parts is None:
        return bool(re.search(r"(?:^|[\s/;&|(])(?:tar|bsdtar|gtar)\s", command)
                    and re.search(r"-[A-Za-z]*[IF]|--(?:u|t|c|i|n|r)[a-z-]*", command))
    for words, _redirs in parts:
        i = 0
        while i < len(words) and writeguard.sl._ASSIGN.match(words[i]):
            i += 1
        if i >= len(words) or os.path.basename(words[i]) not in _TAR_NAMES:
            continue
        for k, w in enumerate(words[i + 1:]):
            if w.startswith("--"):
                name = w[2:].partition("=")[0]
                if name and any(n.startswith(name) for n in _TAR_EXEC_LONG):
                    return True
                if name and "=" in w and "file".startswith(name) and _TAR_REMOTE.match("--file=" + w.partition("=")[2]):
                    return True
            elif w.startswith("-"):
                if _TAR_SHORT_EXEC.match(w) or _TAR_REMOTE.match(w):    # M1: a glued value (-I/p, -F/p, -tI/p) is no reason to pass
                    return True
            elif k == 0 and re.fullmatch(r"[A-Za-z]*[IF][A-Za-z]*", w):
                return True
            elif _TAR_REMOTE.match(w):                                   # L1: an archive operand host:path starts rsh/ssh
                return True
    return False


def _no_nested_git(top, cap=20000):
    """True when no folder below `top` holds a `.git` (a nested repository or a submodule checkout): a work-tree diff looks inside one and
    would run its own config. An unreadable or oversized tree is not provably clean."""
    seen = 0
    for here, dirs, files in os.walk(top, followlinks=False):
        if here == top:
            dirs[:] = [d for d in dirs if d != ".git"]
        elif ".git" in dirs or ".git" in files:
            return False
        seen += len(dirs) + len(files)
        if seen > cap:
            return False
    return True


def git_diff_stat_read(command, project, patterns, cwd):
    """R-1360 F14 shape 4: ONE plain `git [-C <absolute path>]* diff <--stat|--name-only|--name-status|--numstat> [...]`. Those formats never
    run an external diff (a repo's diff.external / textconv are also refused by the shared config scan below). The work-tree diff is
    cleared only for a repository with no nested `.git` below it: then the `--ignore-submodules=all` the shared exception asks for
    changes nothing, so it is added to the words judged. Every other check is the shared `_git_exception`: -C chain, config scan,
    the read allowlist (no -c, --ext-diff, --textconv, --output)."""
    sl = writeguard.sl
    toks = sl._sole_simple(command)
    if not toks or toks[0] not in sl._GIT_BINS:
        return False
    i, chain = 1, []
    while i < len(toks):
        if toks[i] == "-C" and i + 1 < len(toks):
            chain.append(toks[i + 1])
            i += 2
        elif toks[i] in ("--no-pager", "--no-optional-locks"):
            i += 1
        else:
            break
    if i >= len(toks) or toks[i] != "diff" or not all(_ARCHIVE_PATH.fullmatch(c) for c in chain):    # -C must be absolute and plain
        return False
    args = toks[i + 1:]
    opts = [a.partition("=")[0] for a in (args[:args.index("--")] if "--" in args else args) if a.startswith("-")]
    if not (set(opts) & _GIT_STAT_FLAGS) or not set(opts) <= _GIT_DIFF_OPTS:
        return False
    workdir = os.path.realpath(cwd)
    for c in chain:
        workdir = sl._norm_candidate(c, workdir)
    top = _toplevel(workdir) if os.path.isdir(workdir) else None
    if top is None or not _no_nested_git(top):
        return False
    judged = toks[:i + 1] + ["--ignore-submodules=all"] + toks[i + 1:]
    return bool(sl._git_exception(judged, " ".join(judged), project, patterns, cwd))


def _grep_pattern_slots(words, i):
    """R-1360 G2b: the indexes of the pattern words of a plain `grep` or `rg` segment (no assignment prefix, no wrapper): every value of
    -e or --regexp, else the first non-option word. A pattern source file (-f, --file), a short option cluster holding e or f, or a
    pattern with `$` or a backtick gives no slot, so the word rule judges the whole segment as before."""
    if i != 0 or not words or words[0] not in ("grep", "rg"):
        return set()
    slots, found_e, k, pos = set(), False, 1, None
    while k < len(words):
        w = words[k]
        if w == "--":
            if pos is None and k + 1 < len(words):
                pos = k + 1
            break
        if w in ("-e", "--regexp"):
            found_e = True
            if k + 1 >= len(words):
                return set()
            slots.add(k + 1)
            k += 2
            continue
        if w.startswith("--regexp="):
            found_e = True
            slots.add(k)
            k += 1
            continue
        if w == "-f" or w.startswith("--file"):
            return set()
        if w in _RO_FLAGS or re.fullmatch(_RO_CLUSTER_GREP if words[0] == "grep" else _RO_CLUSTER, w):
            k += 1
            continue
        if w in _RO_VALUED:
            if k + 1 >= len(words):
                return set()
            k += 2
            continue
        if w.startswith("-"):
            return set()                     # R-1360 r2 M-A: any option outside the read-only list (--pre, --hostname-bin, an unknown long one) gives no slot
        if pos is None:
            pos = k
        k += 1
    if not found_e and pos is not None:
        slots.add(pos)
    if any(re.search(r"[$`]", words[s]) for s in slots):
        return set()
    return slots


def _line_why(command, bare, word_rule=True, live=None):
    """Word rules for a line that may reach the live checkout. No wrapper is walked: a word that is `git` or `git-*` anywhere but at the
    head of its segment (env, arch, nice, xargs, sh -c '...', eval ...), a head that is `git-*`, a GIT_ assignment other than GIT_DIR and
    GIT_WORK_TREE, a backslash-newline, or a brace holding a comma or a range (which can spell the word git), is refused."""
    view = _unquoted_view(command)           # a brace wholly inside quotes is never expanded by the shell (R-1199)
    if "$'" in command or "\n" in command or "#" in view or "$(" in command or "`" in command or not _masking_allowed(command):
        view = command                       # ANSI-C quoting, a comment, a newline or a command that may run its argument: judge the raw line
    if "\\\n" in command or re.search(r"\{[^}]*(?:,|\.\.)[^}]*\}", view) or re.search(r"\{[^}]*[\"'\\]", view):
        return "a line with a brace expansion or a backslash-newline, which can spell the word git"
    if word_rule and _PROC_SUBST.search(command):        # R-1199 F-1: the splitter reads `>(git ...)` as a redirect target, so no `git` word is seen
        return "a line with a process substitution (>(, <( or =(), which runs a command this guard cannot see"
    if word_rule and writeguard.sl._lexer_unsafe(command) and _name_words(writeguard.sl._normal_text(command, True)) > _name_words(command):
        return "a program name written in disguise (quotes, a backslash, an expansion or an escape) on a line this guard cannot read exactly"
    parts = writeguard.sl._shell_parts(re.sub(r"\\\r?\n", "", command))
    if parts is None:
        return "a line this guard cannot split into words"
    plain = {t: len(re.findall(r"(?<!\S)%s(?=\s)" % re.escape(t), command)) for t in _SAFE_ENV}   # R-1360 G1: the raw text must hold the token bare
    for words, _redirs in parts:
        i = 0
        while i < len(words) and writeguard.sl._ASSIGN.match(words[i]):
            i += 1
        for k, w in enumerate(words):
            m = re.match(r"^(GIT_[A-Za-z0-9_]*)=", w)
            if m and m.group(1) in _ALLOWED_GIT_ENV:
                continue
            if m and k < i and w in _SAFE_ENV and plain[w] > 0:
                plain[w] -= 1                # one raw plain token covers one word: a quoted twin has no raw token left
                continue
            if m:
                return "a %s assignment (only GIT_DIR, GIT_WORK_TREE, GIT_EDITOR=true, GIT_EDITOR=: and GIT_OPTIONAL_LOCKS=0 are read)" % m.group(1)
        if _rg_runs_program(words):
            return "rg with --pre, --pre-glob or --hostname-bin, which runs a program this guard cannot read"
        if i < len(words) and os.path.basename(words[i]).lower().startswith("git-"):
            return "a git-* program as the command word"
        if word_rule and i == 0 and words and len(parts) == 1 and not _redirs and not re.search(r"[<>()]", command) \
                and _scanner_read_form(words, live):        # no assignment prefix: PATH= or DYLD_ ... would change what runs
            continue                         # R-1199: the leak scanner's own `git` subcommand with read-only options only reads history
        if word_rule and i == 0 and len(words) == 3 and len(parts) == 1 and not _redirs and not re.search(r"[<>()]", command) \
                and _xcrun_find_form(words):
            continue                         # R-1294: `xcrun -f <name>` resolves a path; the only segment, no prefix, no redirect
        data = _grep_pattern_slots(words, i)                # R-1360 G2b: a grep or rg pattern is data
        for n, w in enumerate(words[i + 1:] if word_rule else (), i + 1):      # the word rule only judges a line whose reach set holds the live root or an unreadable folder
            if n in data:
                continue
            for text in (w, writeguard.sl._normal_text(w, True)):     # also the word as the shell reads it (g\it, g''it, g$9it, $'\x67it')
                for piece in re.split(r"[\s;&|`$()]+", text):
                    base = os.path.basename(piece).lower() if piece else ""
                    if base == "git" or base.startswith("git-"):
                        return "the word git behind another command (env, arch, xargs, sh -c, eval ...)"
    return None
