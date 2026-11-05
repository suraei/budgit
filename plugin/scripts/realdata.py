"""R-493: precision for the real-data guard.

The older scans of studiolib._real_data_core ask when a protected word APPEARS in a command whose head is not a short
read-only list. Nearly every command names the project root (the rule is "never cd, use absolute paths"), so the guard asked
all day. This module reads a Bash command the way a person would and answers one question: does anything in it WRITE to
protected data? It is used only to take back an ask the older scans gave; it never adds one.

A command is judged one simple command at a time (loops, pipes, `&&`, command substitutions and heredoc programs included).
Each is

  harmless    a reader, a builtin, a test or script run whose code does not write to protected literals, a git read, git in a
              worktree that is not the project, a write whose every target is resolved and is not protected data;
  a write     a writing or destructive verb (or a redirect) whose target is protected data (or, for a recursive/destructive
              verb, a folder that holds it): the protected paths are collected in `keys`;
  a doubt     a write whose target cannot be read, a script that writes to a name it builds, a shell the guard cannot follow.

`judge()` returns the context: `harmless` is True only when there is no write and no doubt. Anything else keeps the older
verdict, so doubt about a WRITE still asks. Naming the project (or a folder above it) as a cwd, an option value, a reader's
argument, a variable that is only read or a loop's list is not a write."""
import fnmatch as _fnmatch
import glob as _glob
import os
import re
import shlex

import studiolib as sl

_SIMPLE_VAR = re.compile(r"\$(?:(\w+)|\{(\w+)\})")
_BRACE = re.compile(r"\{[^{}\s]*,[^{}\s]*\}|\{[^{}\s]*\.\.[^{}\s]*\}")
_GLOB = set("*?[")
TILDE_NAME = re.compile(r"~(?![/]|$)")                       # R-558 r7: a `~` that is not `~` or `~/...`
_GLOB_IN_DIR = re.compile(r"[*?\[][^/]*/")                   # R-558 r7: a glob character before the last `/`
_SAFE_VALUE = re.compile(r"^(?!~[+-])[A-Za-z0-9_./~@:+=,%-]+$")
_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")

# heads whose arguments are only read (find, sed, awk, sort, tee ... have their own handlers below)
_READERS = set(sl._READ_ONLY_CMDS) | {
    "sort", "uniq", "cut", "tr", "nl", "column", "tac", "rev", "xxd", "od", "hexdump", "strings", "fold", "fmt", "expand",
    "unexpand", "paste", "join", "comm", "pr", "seq", "du", "tree", "df", "realpath", "readlink", "dirname", "basename",
    "pwd", "which", "type", "whoami", "id", "uname", "date", "hostname", "printenv", "nproc", "lsof", "ps", "pgrep", "sleep",
    "echo", "printf", "true", "false", ":", "test", "[", "[[", "wait", "jobs", "man", "open", "sw_vers", "shasum", "sha1sum", "sha512sum", "md5sum", "cksum", "b2sum", "numfmt", "tput", "stty", "locale", "getconf", "nl",
    "base64", "iconv", "less", "diff3", "sdiff", "ping", "dig", "host", "nslookup", "uptime", "free", "vm_stat", "whois",
    "tsort", "look", "ag", "ack", "fd", "fdfind", "exa", "eza", "lsd", "bc", "dc", "expr", "let",
    "cmp", "zcat", "gzcat", "bzcat", "xzcat", "mdls", "mdfind"}
_BUILTIN_NOOPS = {"set", "shopt", "setopt", "unsetopt", "umask", "ulimit", "hash", "bind", "enable", "history", "fg", "bg",
                  "disown", "caller", "compgen", "complete", "return", "exit", "break", "continue", "shift",
                  "getopts", "let", "local", "declare", "readonly", "typeset", "export", "unalias"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_INTERP = {"python", "python2", "python3", "node", "nodejs", "deno", "bun", "perl", "ruby", "php", "lua", "osascript",
           "rscript", "tclsh", "swift", "pwsh", "r", "groovy", "julia", "elixir", "dart", "java", "kotlin", "scala"} | _SHELLS
_LEAD = {"do", "then", "else", "elif", "if", "!", "time", "{", "}", "(", ")"}
_NOPARSE_RUNNERS = {"unittest", "pytest", "py_compile", "compileall", "json.tool", "doctest", "venv", "pip", "ensurepip"}
_CODE_ENV = {"PYTHONPATH", "PATH", "NODE_OPTIONS", "BASH_ENV", "ENV", "PYTHONSTARTUP", "RUBYOPT", "PERL5LIB", "PERL5OPT",
             "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH", "PYTHONHOME", "NODE_PATH"}
_EDITORS = {"vi", "vim", "nvim", "nano", "emacs", "ed", "ex", "pico", "micro"}
_COMPRESSORS = {"gzip", "bzip2", "xz", "zstd", "compress", "pigz", "lz4"}
_GIT_TREE_VERBS = {"checkout", "switch", "merge", "rebase", "pull", "stash", "clean", "revert", "cherry-pick", "am", "apply",
                   "read-tree", "checkout-index", "sparse-checkout", "submodule", "bisect", "filter-branch", "subtree",
                   "reset", "restore", "rm", "mv", "worktree", "add", "commit"}
_GIT_READ_EXTRA = {"show-ref", "for-each-ref", "ls-remote", "name-rev", "whatchanged", "reflog", "count-objects",
                   "diff-tree", "diff-index", "diff-files", "check-ignore", "check-attr", "var", "help", "version",
                   "verify-commit", "verify-tag", "cherry", "range-diff", "show-branch"}
_GIT_BAD_READ_OPT = re.compile(r"^(?:--out|-O|-o$|--open-files-in-pager|--ext-diff|--textconv|--exec|-c$|--paginate|-p$)")
_WRITE_WORDS = re.compile(
    r"(?:^|[^\w])(?:rm|rmdir|mv|cp|tee|dd|ln|touch|mkdir|truncate|shred|install|rsync|ditto|chmod|chown|chgrp|unlink|"
    r"patch|tar|unzip|zip|gzip|7z|curl|wget|git\s+(?:clean|checkout|restore|reset|stash|rm|mv|add|commit|merge|rebase|pull|"
    r"switch|apply)|sed\s+-[a-z]*i|perl\s+-[a-z]*i|open|write|unlink|rename)(?:$|[^\w])")


class Ctx:
    def __init__(self, project, patterns, base, session=None, trusted=False):
        self.project, self.patterns, self.base, self.session, self.trusted = project, patterns, base, session, trusted
        self.here = base
        self.known = {}
        self.pure = set()                  # R-515: variables whose value is a file name (a read-only substitution, a loop over paths)
        self.safe_subs = set()
        self.noresolve = False
        self._lost = False
        self.unsure = False                # R-558 r6: the folder was lost at some point; the base guard's ask then stands
        self.cd_mode = "lose"              # R-558 r6: how the next cd of this segment moves the folder (move, lose, skip)
        self.verb = ""                     # R-558 r6 (M1): the program being judged; hard-ask memory keys carry it
        self.rsync_rsh = bool(os.environ.get("RSYNC_RSH"))   # R-558 r6: a remote shell set for rsync by the environment
        self.above = False                 # R-562: a copy into a folder above the root has a reason to ask
        self.hard = False                  # R-558 r3: a copy verb resolved a write key, or a contents copy that cannot be trusted
        self.raw = ""                      # the whole Bash command (only the top-level context has it)
        self.loops = 0
        self.keys = []
        self.doubts = []
        self.dclass = []                   # R-558 r4 (N4): the memory class of each doubt: "", "contents" (hard) or "above" (hard)
        self.dverb = []                    # R-558 r6 (M1): the verb that raised each doubt
        self.above_targets = []            # R-558 r4 (N4): the folders above the root a hard ask would write into
        self.bodies = []
        self.depth = 0
        self.root = os.path.realpath(project)

    @property
    def lost(self):
        return self._lost

    @lost.setter
    def lost(self, value):
        self._lost = bool(value)
        if value:
            self.unsure = True

    @property
    def harmless(self):
        return not self.keys and not self.doubts

    @property
    def keyed(self):
        """Every reason to ask is a write to a resolved protected path (nothing the guard merely could not read)."""
        return bool(self.keys) and not self.doubts

    def write(self, path):
        try:
            self.keys.append(os.path.realpath(path))
        except Exception:
            self.keys.append(str(path))

    def doubt(self, why, hard=False):
        self.doubts.append(why)
        self.dclass.append("contents" if hard else "")
        self.dverb.append(self.verb or "sh")
        if hard:
            self.hard = True

    def mark_above(self, folder, since):
        """R-562 / R-558 r4 (N4): the doubts added since index `since`, and the command itself, concern a write into `folder`,
        which holds the project. A yes to this ask is remembered as `above:<folder>`, a class of its own."""
        self.above = True
        for i in range(since, len(self.dclass)):
            if self.dclass[i] == "contents":
                self.dclass[i] = "above"
        try:
            self.above_targets.append("%s:%s" % (self.verb or "sh", sl._fold(os.path.realpath(folder))))
        except Exception:
            self.above_targets.append("%s:%s" % (self.verb or "sh", sl._fold(str(folder))))


# ------------------------------------------------------------------------------------------------------------ levels

def _static_prefix(pattern):
    return sl._static_prefix(pattern)


def level(word, ctx, base=None):
    """How a path word relates to the protected data: 'exact' (a protected file or something under a protected folder),
    'ancestor' (a folder that holds protected data), 'root' (the project itself) or None. Fails closed."""
    base = base or ctx.here
    try:
        if not word or word.startswith("-"):
            return None
        if word.startswith("/dev/"):
            word = os.path.normpath(word)                 # a /dev name that climbs out of /dev is a path like any other
        froot = sl._fold(ctx.root)
        if set(word) & _GLOB:
            best = None
            for hit in _glob.glob(os.path.join(base, os.path.expanduser(word))):
                lv = level(hit, ctx, base)
                if lv == "exact":
                    return "exact"
                best = best or lv
            rel_pat = os.path.relpath(sl._fold(os.path.join(base, word)), froot)
            if any(not (set(p) & _GLOB) and _fnmatch.fnmatch(sl._fold(p), rel_pat) for p in ctx.patterns):
                return "exact"
            return best
        fpath = sl._fold(sl._norm_candidate(word, base))
        if fpath == froot:
            return "root"
        if not fpath.startswith(froot + os.sep):
            return None
        rel = fpath[len(froot) + 1:]
        if sl._is_budgit_own(rel):
            return None                                   # Budgit's own folders are not real data
        for pat in ctx.patterns:
            fp = sl._fold(pat)
            if sl._whole_root_pattern(fp, froot):
                return "exact"                            # a pattern that is the whole project protects all of it
            if _fnmatch.fnmatch(rel, fp):
                return "exact"
            if not (set(fp) & _GLOB) and rel.startswith(fp.rstrip("/") + "/"):
                return "exact"                            # a file inside a protected folder given without /**
        for pat in ctx.patterns:
            prefix = sl._fold(_static_prefix(pat))
            if prefix and prefix == rel and re.fullmatch(r"\**/?", sl._fold(pat)[len(prefix):].lstrip("/")):
                return "exact"                            # the folder IS the protected set (data/real/**)
            if prefix and (prefix == rel or prefix.startswith(rel + "/")):
                return "ancestor"
        return None
    except Exception:
        return "exact"


def _tail_matchers(patterns):
    out = set()
    for p in patterns:
        comps = [c for c in str(p).split("/") if c]
        if not comps:
            continue
        out.add(comps[-1])
        for c in comps:
            if not (set(c) & _GLOB):
                out.add(c)
    return out


def _tail_excludes(word, ctx):
    """A word with a part the guard cannot read (a variable) whose last file name cannot be a protected file, whatever the
    variable holds: `$S/out.txt` against `plans/*.handles.json`."""
    if "/" not in word and word.startswith("$"):
        return False
    if TILDE_NAME.match(word):
        return False                                      # R-558 r7: `~D/real/y.csv`
    tail = word.rsplit("/", 1)[-1]
    if not tail or tail in (".", "..") or "$" in tail or "`" in tail or set(tail) & _GLOB or "{" in tail or \
            tail.startswith(("~", "(")):
        return False
    folded = sl._fold(tail)
    for m in _tail_matchers(ctx.patterns):
        mm = sl._fold(m)
        if _fnmatch.fnmatch(folded, mm) or folded == mm:
            return False
    return True


# ---------------------------------------------------------------------------------------------------- words and vars

def val(word, ctx):
    """The word with every known variable and `~` replaced, or None when something unreadable stays in it."""
    if word is None:
        return None
    if word.startswith("~"):
        if TILDE_NAME.match(word):
            return None                                       # R-558 r7: `~NAME` is zsh's named directory; only `~` and `~/` are readable
        word = os.path.expanduser(word)

    def one(m):
        name = m.group(1) or m.group(2)
        if name == "HOME":
            return os.path.expanduser("~")
        if name == "PWD" and not ctx.lost:
            return ctx.here
        if name in ctx.known and not ctx.noresolve:
            return ctx.known[name]
        raise KeyError(name)
    try:
        out = _SIMPLE_VAR.sub(one, word)
    except KeyError:
        return None
    if "$" in out or "`" in out or "<(" in out or ">(" in out or _BRACE.search(out):
        return None
    if ctx.lost and out and not out.startswith(("/", "-")):
        return None                                       # R-558 r3: after `cd -`, popd, pushd or a cd to an unreadable name
    return out


_PURE_HEADS = {"date", "pwd", "basename", "dirname", "head", "tail", "sort", "uniq", "wc", "stat", "realpath",
               "whoami", "hostname", "uname", "id", "which", "find", "git"}
_LS_SAFE_OPT = re.compile(r"^-[1tdrS]+$")             # R-516: ordering and one-per-line options only
# R-516: a path before the first wildcard, and a wildcard after it; no braces anywhere (`{,x/}*` expands to a bare `*`)
_LS_PATH_GLOB = re.compile(r"^[^-*?\[{}][^*?\[{}]*/[^{}]*\*[^{}]*$")


def _pure_producer(body):
    """R-515: a read-only substitution body whose output is a name or a number, never a word an author chose (`echo -i`)."""
    if re.search(r"[;&<>$`()\\]", body):
        return False
    for stage in body.split("|"):
        toks = stage.split()
        if not toks:
            return False
        if os.path.basename(toks[0]) == "ls":
            # R-516: bare `ls` prints names a file could turn into a flag (`-delete`); it is pure only when EVERY operand is a
            # path-prefixed glob (`plans/*`, never `.`, `*` or `x`) and every option only orders or one-per-lines the names
            # (no `-R`, whose subdirectory listings print bare names; no `--`, after which an operand may start with a dash)
            args = toks[1:]
            if any(t.startswith("-") and not _LS_SAFE_OPT.match(t) for t in args):
                return False
            names = [t for t in args if not t.startswith("-")]
            if not names or not all(_LS_PATH_GLOB.match(t) for t in names):
                return False
        elif os.path.basename(toks[0]) not in _PURE_HEADS:
            return False
        if toks[0] == "find" and any(t in sl._FIND_WRITERS for t in toks):
            return False
        if toks[0] == "git" and (len(toks) < 2 or toks[1] not in ("rev-parse", "log", "status", "branch", "describe")):
            return False
    return True


def _set_var(name, value, ctx, persist=True):
    if value in ctx.safe_subs:
        ctx.pure.add(name)
    else:
        ctx.pure.discard(name)
    if name == "IFS":
        ctx.noresolve = True
    if name in _CODE_ENV:
        ctx.doubt("an environment variable that changes which program runs (%s)" % name)
    v = val(value, ctx) if ("$" in value or "~" in value) else value
    if persist and ctx.loops == 0 and v is not None and _SAFE_VALUE.match(v):
        ctx.known[name] = v
    else:
        ctx.known.pop(name, None)


_FOUND = "$__FOUND"                                      # what `{}` stands for inside a find -exec command


def target(word, ctx, destructive=False, what="a write"):
    """One word that a writer changes. A protected file is a write; a folder that holds protected data (or the project) is
    one only for a destructive verb; a word the guard cannot read is a doubt unless its file name cannot be protected."""
    if _FOUND in word:
        return                                            # `find S -exec chmod x {}`: the start folders are judged by find()
    v = val(word, ctx)
    if v is None:
        # R-558 r6: in a lost folder a relative name can be anything, whatever its file name says
        if ctx.lost or not _tail_excludes(word, ctx):
            ctx.doubt("%s to a name the guard cannot read (%s)" % (what, word))
        return
    if v.startswith("/dev/"):
        v = os.path.normpath(v)                           # /dev/stdout/../..<root>/data.json is not a device
    lv = level(v, ctx)
    if lv == "exact" or (destructive and lv in ("ancestor", "root")):
        ctx.write(sl._norm_candidate(v, ctx.here) if not (set(v) & _GLOB) else os.path.join(ctx.here, v))
    elif _GLOB_IN_DIR.search(v):
        ctx.doubt("%s to a name with a glob in a folder part (%s)" % (what, word))   # R-558 r7: it may name any folder


# ---------------------------------------------------------------------------------------------------------- parsing

def strip_heredocs(command):
    """-> (text without heredoc bodies, [bodies], ok). ok False when a marker sits inside a quote or a body never ends."""
    lines, out, bodies, i, ok = command.split("\n"), [], [], 0, True
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in _HEREDOC.finditer(line):
            if line[:m.start()].count("'") % 2 or line[:m.start()].count('"') % 2:
                ok = False
                continue
            end, body = m.group(3), []
            closed = False
            while i < len(lines):
                cur = lines[i]
                i += 1
                if (cur.strip() if m.group(1) or True else cur) == end:
                    closed = True
                    break
                body.append(cur)
            ok = ok and closed
            bodies.append("\n".join(body))
    return "\n".join(out), bodies, ok


def segments(text):
    """[(words, outs, op_before)] of a shell text: split at ; && || | & ( ) and newlines; the word after each > is a
    redirect target, the word after each < (and << here-document marker) is dropped. None when it cannot be lexed."""
    toks = sl._lex_raw(text)
    if toks is None:
        return None
    segs, words, outs, want, op, opaque = [], [], [], None, "", False
    for tok in toks:
        if tok and set(tok) <= sl._PUNCT_ONLY:
            if ">(" in tok:
                opaque = True
                continue
            if "<(" in tok:
                if words or outs:
                    segs.append((words, outs, op))
                words, outs, want, op = [], [], None, "("
                continue
            if ">" in tok or "<" in tok:
                if words and words[-1].isdigit() and tok.startswith(("<", ">")) and want is None:
                    words.pop()
                want = ">" if ">" in tok else "<"
                continue
            if words or outs:
                segs.append((words, outs, op))
                op = ""
            # R-558 r6: every operator since the last command is kept (`) &&`, `; }`), so a group's end is never lost
            words, outs, want, op = [], [], None, (op + " " + tok).strip()
            continue
        if want == ">":
            outs.append(tok)
        elif want == "<":
            pass
        elif tok not in ("}", "{"):
            words.append(tok)
        want = None
    if words or outs:
        segs.append((words, outs, op))
    return None if opaque else segs


def inner_substs(word):
    """The command texts of every $( ... ) inside a word (balanced), or None when one is not closed."""
    out, i = [], 0
    while True:
        i = word.find("$(", i)
        if i < 0:
            return out
        depth, j = 1, i + 2
        while j < len(word) and depth:
            depth += (word[j] == "(") - (word[j] == ")")
            j += 1
        if depth:
            return None
        out.append(word[i + 2:j - 1])
        i = j


_ASSIGN_WORD = re.compile(r"[A-Za-z_]\w*\+?=")


def hoist_assign_substs(text):
    """-> (text, bodies, ok): every $( ... ) that sits inside a NAME=value word is replaced by an unreadable variable
    ($__SUBn) and its body is returned apart, so `F=plans/$(date +%F).json` is one assignment of an unknown value and the
    body is judged as the command it is. A substitution anywhere else stays in the text. ok False when one never closes."""
    out, bodies, word, quote, i, n = [], [], "", None, 0, len(text)
    while i < n:
        c = text[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            word += text[i:i + 2]
            i += 2
            continue
        elif c == "$" and text.startswith("$(", i) and _ASSIGN_WORD.match(word):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += (text[j] == "(") - (text[j] == ")")
                j += 1
            if depth:
                return text, [], False
            out.append("$__SUB%d" % len(bodies))
            word += "$__SUB%d" % len(bodies)
            bodies.append(text[i + 2:j - 1])
            i = j
            continue
        elif quote is None and c in "'\"":
            quote = c
        elif quote == '"' and c == '"':
            quote = None
        elif quote is None and (c.isspace() or c in ";&|()<>"):
            out.append(c)
            word = ""
            i += 1
            continue
        out.append(c)
        word += c
        i += 1
    return "".join(out), bodies, True


def assign_only_subst(command, _depth=0):
    """True when every command substitution in the command is the value of a NAME=value assignment (and none of those
    bodies holds another one), so the only unreadable thing is a variable. Anything else, a here-document that
    substitutes, a ${...}, a process substitution or an inline $( ... ) argument, is False."""
    try:
        if _depth > 3:
            return False
        text, bodies, ok = strip_heredocs(command)
        if not ok or any(sl._SUBST.search(b) for b in bodies) or text.count("`") % 2:
            return False
        text = re.sub(r"`([^`]*)`", r"$(\1)", text)
        text, subs, ok = hoist_assign_substs(text)
        return ok and not sl._SUBST.search(text) and all(assign_only_subst(b, _depth + 1) for b in subs)
    except Exception:
        return False


# R-558 r7: the folder is tracked on an ALLOWLIST. Six rounds of blocklisting shell forms ('!', a function body, a case `)`,
# `~NAME`, a glob, `CDPATH+=`) each missed the next one, so the reader no longer models what a cd does. It believes the folder
# moved on exactly one shape, and every other cd-like word on the line leaves the folder lost.
_CD_NAMES = ("cd", "chdir", "pushd", "popd")
_CD_TEXT = re.compile(r"(?<![A-Za-z0-9_.-])(?:cd|chdir|pushd|popd)(?![A-Za-z0-9_.-])")
_CDPATH_TEXT = re.compile(r"cdpath", re.I)
_CD_BAD_CHARS = set("$`~*?[]{}()!#^<>|&;\\\"'")
_AFTER_CD_OK = {"&&", "|"}


def _cd_operand(args):
    """The one literal operand of a certain cd (`cd X`, `cd -- X`), or None. The word has none of `$ ` ~ * ? [ ] { } ( ) !`
    (nor `# ^ < > | & ; \\`), no leading `-`, `+` or `=`, and CDPATH in the environment cannot redirect it."""
    if len(args) == 2 and args[0] == "--":
        args = args[1:]
    if len(args) != 1:
        return None
    w = args[0]
    if not w or w[0] in "-+=" or set(w) & _CD_BAD_CHARS:
        return None
    if os.environ.get("CDPATH") and not w.startswith(("/", "./", "../")) and w not in (".", ".."):
        return None
    return w


def certain_cd(text, raw=""):
    """True when the line is `[NAME=value ...] cd [--] WORD && ...` and nothing after the cd can run without it: only `&&`,
    `|` and redirects follow, no `;`, `||`, `&` or newline, no second cd-like word anywhere, and CDPATH / cdpath is not
    mentioned anywhere. Only then did the cd surely run and succeed before every later command."""
    toks = sl._lex_raw(text)
    if not toks or _CDPATH_TEXT.search(raw or text) or _CDPATH_TEXT.search(text):
        return False
    i = 0
    while i < len(toks) and sl._ASSIGN.match(toks[i]):
        i += 1
    if i >= len(toks) or toks[i] != "cd":
        return False
    j = i + 1
    while j < len(toks) and not (toks[j] and set(toks[j]) <= sl._PUNCT_ONLY):
        j += 1
    if j >= len(toks) or toks[j] != "&&" or _cd_operand(toks[i + 1:j]) is None:
        return False
    for t in toks[j:]:
        if t and set(t) <= sl._PUNCT_ONLY and t not in _AFTER_CD_OK and ">" not in t and "<" not in t and t not in ("(", ")"):
            return False
    named = sum(1 for t in toks if os.path.basename(t) in _CD_NAMES)
    return max(named, len(_CD_TEXT.findall(text))) == 1


def cd_words(text):
    """How many cd-like words the line has (cd, chdir, pushd, popd: as a token, or spelled anywhere in the text)."""
    toks = sl._lex_raw(text) or []
    return max(sum(1 for t in toks if os.path.basename(t) in _CD_NAMES), len(_CD_TEXT.findall(text)))


def _walk(segs, ctx, certain=False):
    """R-558 r7: judge the segments in order. Only the first one can move the folder, and only when `certain` (see
    certain_cd); a cd anywhere else was already counted by run() and left the folder lost."""
    for k, (words, outs, _op) in enumerate(segs):
        _judge_segment(list(words), list(outs), ctx, "move" if (k == 0 and certain) else "lose")


def run(command, ctx):
    ctx.depth += 1
    saved = (ctx.here, ctx._lost)                         # R-558 r6 (H1): a nested run is another shell: its cd stays there
    try:
        if ctx.depth > 6:
            ctx.doubt("a command nested too deeply")
            return
        text, bodies, ok = strip_heredocs(command)
        if not ok:
            ctx.doubt("a here-document the guard cannot read")
        ctx.bodies = bodies + ctx.bodies if ctx.depth == 1 else bodies
        if text.count("`") % 2:
            ctx.doubt("an open backtick")
            return
        text = re.sub(r"`([^`]*)`", r"$(\1)", text)
        text, assigned, closed = hoist_assign_substs(text)
        if not closed:
            ctx.doubt("a command substitution that does not close")
            return
        mine = {"$__SUB%d" % k for k, b in enumerate(assigned) if _pure_producer(b)}
        for inner in assigned:
            run(inner, ctx)
        ctx.safe_subs = mine                              # the nested runs set their own
        if sl._MOVES_HOME.search(text):
            ctx.lost = True                               # R-558 r7: no folder can be trusted after it
            ctx.doubt("the line changes HOME, PWD, OLDPWD or CDPATH")
            return
        if "$'" in text or '$"' in text:
            ctx.doubt("an ANSI-C string")
        segs = segments(text)
        if segs is None:
            ctx.doubt("a shell form the guard cannot read")
            return
        certain = certain_cd(text, ctx.raw)
        if cd_words(text) > (1 if certain else 0):
            ctx.lost = True                               # R-558 r7: any cd-like word but the one certain cd loses the folder
        _walk(segs, ctx, certain)
    finally:
        ctx.depth -= 1
        if ctx.depth:
            ctx.here, ctx._lost = saved


# --------------------------------------------------------------------------------------------------------- segments

_RSH_KEEPERS = {"export", "declare", "typeset", "readonly", "local"}


def _judge_segment(words, outs, ctx, cd_mode="lose"):
    """R-558 r7 (Codex low): `RSYNC_RSH=x cmd` and `env RSYNC_RSH=x cmd` set the remote shell for that command only; an
    assignment on its own line or an `export` keeps it for the rest of the line."""
    prefix, was = any(w.startswith("RSYNC_RSH=") for w in words), ctx.rsync_rsh
    try:
        _judge_segment_inner(words, outs, ctx, cd_mode)
    finally:
        if prefix and was is False:
            rest = [w for w in words if w not in _LEAD]
            while rest and sl._ASSIGN.match(rest[0]):
                rest = rest[1:]
            if rest and rest[0] not in _RSH_KEEPERS:
                ctx.rsync_rsh = was


def _judge_segment_inner(words, outs, ctx, cd_mode="lose"):
    if any(w.startswith("RSYNC_RSH=") for w in words):
        ctx.rsync_rsh = True                              # R-558 r6 (H3): `RSYNC_RSH=env rsync`, `env RSYNC_RSH=x rsync`
    for w in words + outs:
        if "$(" in w:
            subs = inner_substs(w)
            if subs is None:
                ctx.doubt("a command substitution that does not close")
            else:
                for inner in subs:
                    run(inner, ctx)
    for t in outs:
        redirect(t, ctx)
    while words and words[0] in _LEAD:
        words = words[1:]
    if not words:
        return
    # leading NAME=value words
    i = 0
    while i < len(words) and sl._ASSIGN.match(words[i]):
        i += 1
    assigns, words = words[:i], words[i:]
    if not words:
        for a in assigns:
            m = sl._ASSIGN.match(a)
            _set_var(m.group(1), m.group(2), ctx)
        return
    env = {sl._ASSIGN.match(a).group(1): sl._ASSIGN.match(a).group(2) for a in assigns}
    head = words[0]
    # R-558 r7: only the shell's own `cd` can move the folder, and only as the certain first command (see certain_cd)
    direct = head == "cd"
    if head == "done":
        ctx.loops = max(0, ctx.loops - 1)
        return
    if head in ("for", "select"):
        ctx.loops += 1
        if len(words) > 1:
            ctx.known.pop(words[1], None)
            items = words[3:] if words[2:3] == ["in"] else []
            if items and not any(w.startswith("-") or "$" in w or "`" in w for w in items):
                ctx.pure.add(words[1])             # R-515: a loop over literal paths or a glob: a file operand, not a flag
            else:
                ctx.pure.discard(words[1])
        return
    if head in ("while", "until"):
        ctx.loops += 1
        words = words[1:]
        while words and words[0] in _LEAD:
            words = words[1:]
        if not words:
            return
        head = words[0]
    if head in ("fi", "esac", "in", ";;", "case", "function"):
        return
    split = any(os.path.basename(w) == "env" for w in words[:2]) and any(
        t == "--split-string" or t.startswith("--split-string=") or (t.startswith("-") and not t.startswith("--") and "S" in t[1:])
        for t in words[1:])
    words = unwrap(words)
    if not words:
        return
    before = len(ctx.keys)
    ctx.cd_mode = cd_mode if direct and words[0] == head else "lose"
    command(words, env, ctx)
    ctx.cd_mode = "lose"
    if split and len(ctx.keys) > before:
        ctx.hard = True                               # H5: the older scan reads `env -S '<cmd>'` as one word, whatever it runs


def redirect(tok, ctx):
    if tok and tok.startswith("/dev/"):
        tok = os.path.normpath(tok)                       # /dev/stdout/../..<root>/data.json is not a device
    if not tok or tok.isdigit() or tok == "-" or tok.startswith("/dev/") or tok.startswith("&"):
        return
    target(tok, ctx, destructive=True, what="a redirect")


_WRAP_VALUE_OPTS = {"sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-T", "-U"}, "nice": {"-n"},
                    "ionice": {"-c", "-n", "-p"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
                    "lockf": {"-t"}, "env": {"-u", "-C", "-S", "--unset", "--chdir"}, "stdbuf": {"-i", "-o", "-e"}}


def unwrap(words):
    """Strip env / sudo / nice / nohup / time / timeout / lockf / command / exec ... and return the command they run."""
    for _ in range(8):
        if not words:
            return words
        w = os.path.basename(words[0])
        if w not in {"env", "sudo", "nice", "nohup", "time", "timeout", "lockf", "command", "exec", "stdbuf", "ionice",
                     "caffeinate", "setsid", "unbuffer", "builtin", "noglob", "arch"}:
            return words
        i, valued = 1, _WRAP_VALUE_OPTS.get(w, set())
        split = None
        while i < len(words):
            t = words[i]
            if w == "env" and sl._ASSIGN.match(t):
                i += 1
            elif w == "env" and (t == "--split-string" or t.startswith("--split-string=") or
                                 (t.startswith("-") and not t.startswith("--") and "S" in t[1:])):
                # R-558 r3: `env -S 'cp x R/data.json'` splits the string into the command it runs
                if t.startswith("--split-string="):
                    split, i = t.partition("=")[2], i + 1
                elif t == "--split-string":
                    split, i = (words[i + 1] if i + 1 < len(words) else ""), i + 2
                else:
                    rest = t[t.index("S") + 1:]
                    split, i = (rest, i + 1) if rest else ((words[i + 1] if i + 1 < len(words) else ""), i + 2)
                break
            elif t == "--":
                i += 1
                break
            elif t.startswith("-") and len(t) > 1:
                i += 2 if t in valued else 1
            else:
                break
        if split is not None:
            try:
                extra = shlex.split(split)
            except ValueError:
                return ["eval", split]                    # a string the guard cannot split: judged as a built command line
            while extra and sl._ASSIGN.match(extra[0]):
                extra = extra[1:]
            words = extra + words[i:]
            continue
        if w == "timeout" and i < len(words):
            i += 1                                        # the duration
        if w == "lockf" and i < len(words):
            i += 1                                        # the lock file
        if w == "command" and len(words) > 1 and words[1] in ("-v", "-V", "-p"):
            return ["true"]
        words = words[i:]
    return words


# ----------------------------------------------------------------------------------------------------------- commands

def _flags_ops(args, valued=()):
    flags, ops, after, i = [], [], False, 0
    while i < len(args):
        a = args[i]
        i += 1
        if after or a == "-" or not a.startswith("-"):
            ops.append(a)
        elif a == "--":
            after = True
        else:
            flags.append(a)
            if a in valued and i < len(args):
                flags.append(args[i])
                i += 1
    return flags, ops


def _long(f, name, minlen):
    """The long option `name` or an abbreviation of it that cp / rsync accept (`--rec`, `--arc`), with or without =value."""
    key = f.partition("=")[0]
    return key.startswith("--") and len(key) >= minlen and name.startswith(key)


def _recursive(flags):
    return any(f in ("--recursive", "-r", "-R") or _long(f, "--recursive", 5)
               or (f.startswith("-") and not f.startswith("--") and re.search("[rR]", f)) for f in flags)


def _archive(f):
    return _long(f, "--archive", 4) or (f.startswith("-") and not f.startswith("--") and "a" in f)


def _dereference(f):
    return _long(f, "--dereference", 5) or (f.startswith("-") and not f.startswith("--") and bool(re.search("[LH]", f)))


_FLAG_WRITERS = {"sed", "sort", "find", "perl", "awk", "gawk", "mawk", "nawk", "tee", "cp", "mv", "rsync", "dd", "install", "ruby"}
_FLAGISH_WORD = re.compile(r"^(?:-[\w-]*)?(?:\$|`)")      # a word that is (or starts like) a variable or a substitution
_FIND_VALUE_PRIMARIES = {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-newer", "-type", "-size", "-mtime",
                         "-atime", "-ctime", "-mmin", "-user", "-group", "-perm", "-maxdepth", "-mindepth", "-lname", "-wholename"}


def _flag_in_variable(head, args, ctx):
    """R-515: -> args with a flag-valued variable replaced by its value; an unresolved word (`$(echo -i)`, `$UNKNOWN`) standing
    where a flag can stand in a program that can write is a doubt: the flag it hides may be -i, -o, -delete, -exec ..."""
    out, seen_operand = [], False
    for i, a in enumerate(args):
        shown = a
        if "$" in a:
            v = val(a, ctx)
            if v is not None and v.startswith("-"):
                shown = v
        out.append(shown)
        if shown != a:
            ctx.doubt("a variable holds a flag where a flag of %s can stand (%s)" % (head, a))
            continue
        if head in ("tee", "cp", "mv", "install", "rsync", "dd") and not a.startswith("-"):
            continue                                      # a bare $X here is a file operand, not a flag
        if not _FLAGISH_WORD.match(a) or val(a, ctx) is not None:
            if not shown.startswith("-"):
                seen_operand = True                       # the sed script, the first file: later words are operands
            continue
        m_name = re.fullmatch(r"\$(\w+)|\$\{(\w+)\}", a)
        if m_name and (m_name.group(1) or m_name.group(2)) in ctx.pure:
            seen_operand = True
            continue                                      # a file name, not a flag
        if head == "ruby" and i == 0:
            continue                                      # ruby's first operand is the script
        if head == "find":
            if i == 0 or args[i - 1] in _FIND_VALUE_PRIMARIES:
                continue                                  # a start folder or a primary's value
        elif seen_operand:
            continue
        ctx.doubt("a word the guard cannot read where a flag of %s can stand (%s)" % (head, a))
    return out


_COPY_ALIASES = {"gcp": "cp", "gmv": "mv", "ginstall": "install", "gln": "ln"}
_HARD_VERBS = {"cp", "mv", "install", "ditto", "rsync", "ln"}


def command(words, env, ctx):
    """Judge one simple command. R-558 r3 (H4): a write key a copy, move, install or link resolves is a hard ask for the
    hook, wherever the protected word sits among the operands (the older scan reads a protected word that is not the last as
    a source). GNU `g` prefixed names are the verbs."""
    head = os.path.basename(words[0])
    if head in _COPY_ALIASES:
        words, head = [_COPY_ALIASES[head]] + list(words[1:]), _COPY_ALIASES[head]
    ctx.verb = head
    before = len(ctx.keys)
    _command(words, env, ctx)
    if head in _HARD_VERBS and len(ctx.keys) > before:
        ctx.hard = True


def _reader_hazard(head, args):
    """L1: a reader or builtin on the lists whose options write a file or run another program."""
    low = [a.partition("=")[0] for a in args]
    if head == "rg":
        return any(a in ("--pre", "--hostname-bin") for a in low)
    if head == "file":
        return any(a in ("-C", "--compile") or (a.startswith("-") and not a.startswith("--") and "C" in a) for a in args)
    if head == "compgen":
        return any(a.startswith("-") and not a.startswith("--") and "C" in a for a in args)
    if head == "enable":
        return any(a.startswith("-") and not a.startswith("--") and "f" in a for a in args)
    if head == "hash":
        return any(a.startswith("-") and not a.startswith("--") and "p" in a for a in args)
    if head == "history":
        return any(a.startswith("-") and not a.startswith("--") and bool(set(a) & set("wanrcd")) for a in args)
    if head in ("let", "[[", "[", "test"):
        return any("$(" in a or "`" in a for a in args)
    return False


def _command(words, env, ctx):
    head = os.path.basename(words[0])
    args = words[1:]
    if _reader_hazard(head, args):
        ctx.doubt("%s with an option that writes a file or runs a program" % head)
        return
    m_plus = re.match(r"^(\w+)\+=", words[0])
    if m_plus:                                            # x+=y builds a name the guard does not follow
        ctx.known.pop(m_plus.group(1), None)
        return
    if head in _FLAG_WRITERS:
        args = _flag_in_variable(head, args, ctx)
        words = [words[0]] + args
    if head in ("open", "code"):
        ctx.doubt("%s starts a program" % head)
        return
    if head in ("install", "touch", "truncate"):
        for a in args:
            if not a.startswith("-"):
                target(a, ctx, destructive=True, what="install")
        return
    if head in _CD_NAMES:
        # R-558 r7: the folder moves only for the shell's own `cd` that run() found certain, with one literal operand;
        # pushd, popd, chdir, a wrapped cd or any other cd leaves it lost
        mode, ctx.cd_mode = ctx.cd_mode, "lose"
        operand = _cd_operand(args) if mode == "move" and head == "cd" and words[0] == "cd" else None
        if operand is None:
            ctx.lost = True
        else:
            ctx.here = sl._norm_candidate(operand, ctx.here)
        return
    if head in ("export", "declare", "typeset", "local", "readonly"):
        for a in args:
            m = sl._ASSIGN.match(a)
            if m:
                _set_var(m.group(1), m.group(2), ctx)
        return
    if head in ("read", "unset", "mapfile", "readarray"):
        for a in args:
            if not a.startswith("-"):
                ctx.known.pop(a, None)
        return
    if head == "printf" and "-v" in args:
        i = args.index("-v")
        if i + 1 < len(args):
            ctx.known.pop(args[i + 1], None)
    if head in ("eval", "source", ".", "trap", "alias", "coproc"):
        ctx.doubt("%s runs a command line built elsewhere" % head)
        return
    if head in _SHELLS:
        return shell(head, args, ctx)
    if head in _INTERP or re.sub(r"[\d.]+$", "", head) in _INTERP:
        return interpreter(re.sub(r"[\d.]+$", "", head), args, env, ctx)
    if head == "xargs":
        if not sl._xargs_runs_reader(words):
            ctx.doubt("names handed to a command that is not a reader")
        return
    if head == "git" or head in sl._GIT_BINS:
        return git(words, env, ctx)
    if head == "find":
        return find(args, ctx)
    if head == "sed":
        return sed(args, ctx)
    if head in ("awk", "gawk", "mawk", "nawk"):
        return awk(args, ctx)
    if head in ("perl", "ruby") or head == "tee" or head in sl._DELETERS or head in WRITERS:
        return writer(head, args, ctx)
    if head in _EDITORS:
        for a in args:
            if not a.startswith(("-", "+")):
                target(a, ctx, what="an edit")
        return
    if head in _COMPRESSORS:
        flags, ops = _flags_ops(args)
        if not any(f in ("-c", "--stdout", "-l", "--list", "-t", "--test") for f in flags):
            for o in ops:
                target(o, ctx, what="a compression in place")
        return
    if head in sl._TASK_RUNNERS:
        ctx.doubt("a task runner (%s) whose writes cannot be read" % head)
        if ctx.lost:
            ctx.doubt("a runner after a cd the guard cannot follow")
        elif sl._runner_ask(" ".join(shlex.quote(w) for w in words), "%s %s", ctx.project, ctx.patterns, ctx.here):
            ctx.doubt("a runner pointed at protected data or a folder above the project")
        return
    if head == "sort":
        for i, a in enumerate(args):
            if a == "-o" and i + 1 < len(args):
                target(args[i + 1], ctx, what="sort -o")
            elif a.startswith("--output="):
                target(a.partition("=")[2], ctx, what="sort -o")
            elif a.startswith("-o") and len(a) > 2 and not a.startswith("--"):
                target(a[2:], ctx, what="sort -o")
        return
    if head == "tree":
        if any(a.startswith("-") and not a.startswith("--") and "R" in a for a in args):
            ctx.doubt("tree -R writes files")
        for i, a in enumerate(args):
            if a == "-o" or a.startswith("--output"):
                ctx.doubt("tree -o writes a file")
        return
    if head in _READERS or head in _BUILTIN_NOOPS:
        return
    if _CODE_ENV & set(env):
        ctx.doubt("an environment variable that changes which program runs")
        return
    if head in ("budgit", "studio"):
        if not sl._sole_simple(getattr(ctx, "raw", "") or "x;y") or not (getattr(ctx, "raw", "") or "").lstrip().startswith(words[0]) or not sl._budgit_exception([words[0]] + [val(a, ctx) or a for a in args], ctx.project, ctx.patterns, ctx.here):
            ctx.doubt("a budgit command that is not plain bookkeeping, or not this plugin's own")
        return
    if "/" in words[0]:
        return script(words[0], args, ctx)               # a program run by its own path: read it like a script
    unknown(head, args, ctx)


def unknown(head, args, ctx):
    """A command the guard cannot class: it passes unless an argument IS protected data (the real file or something under
    a protected folder). Naming the project, a folder above it, or a file beside the data does not make it a writer.
    A program whose code cannot be read is a doubt of the `script` class: the older scans asked about it when it ran beside
    the root, and it still asks (once per session and root)."""
    ctx.doubt("a program %s whose writes cannot be read" % head)
    for a in args:
        for cand in (a, a.partition("=")[2]) if "=" in a and not a.startswith("=") else (a,):
            v = val(cand, ctx)
            if v is None or v.startswith("-"):
                continue
            if level(v, ctx) == "exact":
                ctx.write(sl._norm_candidate(v, ctx.here) if not (set(v) & _GLOB) else os.path.join(ctx.here, v))


WRITERS = {"mv", "cp", "install", "ditto", "rsync", "ln", "touch", "chmod", "chown", "chgrp", "chflags", "truncate", "mkdir",
           "mkfifo", "mknod", "dd", "patch", "tar", "bsdtar", "gtar", "unzip", "zip", "7z", "7za", "7zr", "cpio", "pax",
           "unar", "gunzip", "bunzip2", "unxz", "curl", "wget", "split", "csplit", "sponge", "scp", "sftp", "cpio", "unlink"}


def writer(head, args, ctx):
    flags, ops = _flags_ops(args, valued={"-t", "--target-directory", "-S", "-m", "-s", "-o", "-g", "-d", "-r", "-c", "-D",
                                          "-e", "-f", "-i", "-p", "-C", "-X", "-n", "-z", "-l", "-u", "-b", "-B"} if head in
                            ("install",) else ())
    if head in sl._DELETERS:
        rec = _recursive(flags) or head in ("trash", "trash-put", "srm", "mv")
        if head in ("rmdir", "unlink"):
            rec = False
        for o in ops:
            target(o, ctx, destructive=rec, what="a delete")
        return
    if head == "tee":
        for o in ops:
            target(o, ctx, destructive=True, what="tee")
        return
    if head in ("mv", "cp", "install", "ditto"):
        return copy_move(head, args, ctx)
    if head == "rsync":
        return rsync(args, ctx)
    if head == "ln":
        for o in ops:
            v = val(o, ctx)
            if v is None:
                if _FOUND not in o and not _tail_excludes(o, ctx):
                    ctx.doubt("ln with a name the guard cannot read")
            elif level(v, ctx):
                ctx.write(sl._norm_candidate(v, ctx.here))
        return
    if head in ("chmod", "chown", "chgrp", "chflags"):
        rec = _recursive(flags)
        for o in ops[1:]:
            target(o, ctx, destructive=rec, what=head)
        return
    if head == "touch":
        i, rest = 0, []
        while i < len(args):
            if args[i] in ("-t", "-r", "-d", "--date", "--reference") and i + 1 < len(args):
                i += 2
                continue
            if not args[i].startswith("-"):
                rest.append(args[i])
            i += 1
        for o in rest:
            target(o, ctx, what="touch")
        return
    if head == "truncate":
        i = 0
        while i < len(args):
            if args[i] in ("-s", "--size", "-r", "--reference") and i + 1 < len(args):
                i += 2
                continue
            if not args[i].startswith("-"):
                target(args[i], ctx, what="truncate")
            i += 1
        return
    if head in ("mkdir", "mkfifo", "mknod"):
        for o in ops:
            target(o, ctx, what=head)
        return
    if head == "dd":
        for a in args:
            if a.startswith("of="):
                target(a[3:], ctx, destructive=True, what="dd")
        return
    if head == "patch":
        files = [o for o in ops]
        if files:
            for o in files:
                target(o, ctx, what="patch")
        elif level(ctx.here, ctx):
            ctx.doubt("patch reads the files it changes from its input")
        return
    if head in ("tar", "bsdtar", "gtar", "unzip", "7z", "7za", "7zr", "cpio", "pax", "unar", "gunzip", "bunzip2", "unxz",
                "zip"):
        return archive(head, [head] + list(args), ctx)
    if head in ("curl", "wget"):
        for i, a in enumerate(args):
            if a in ("-o", "--output", "-O" if head == "wget" else "--output", "-P", "--directory-prefix") and i + 1 < len(args):
                target(args[i + 1], ctx, what=head + " -o")
            elif a.startswith(("--output=", "--directory-prefix=")):
                target(a.partition("=")[2], ctx, what=head + " -o")
        return
    if head in ("split", "csplit", "sponge", "scp", "sftp"):
        for o in ops[-1:]:
            if ":" not in o.split("/")[0]:
                target(o, ctx, what=head)
        return
    for o in ops:                                          # any other writer: every operand is a target
        target(o, ctx, what=head)


def _above_root(path, ctx):
    """R-562: is `path` a folder that holds the project root (a strict ancestor of it)? `level` is None for such a folder."""
    try:
        fp = sl._fold(sl._norm_candidate(path, ctx.here))
        froot = sl._fold(ctx.root)
        return fp != froot and froot.startswith(fp if fp.endswith(os.sep) else fp + os.sep)
    except Exception:
        return True


def _can_reach_root(path, ctx):
    """A target folder that is the root, inside it, or above it: anything else cannot lead to protected data."""
    try:
        fp = sl._fold(sl._norm_candidate(path, ctx.here))
        froot = sl._fold(ctx.root)
        return fp == froot or fp.startswith(froot + os.sep) or _above_root(path, ctx)
    except Exception:
        return True


def _contents_hit(src, target_dir, ctx, limit=3000, deref=False):
    """R-558: a copy whose source ends in `/` or `/.` (or a ditto source) writes what the folder HOLDS into the target.
    The first target path that is protected data, or None. A source that cannot be listed (a glob, missing, not a folder,
    unreadable, too big) counts as a doubt, so the command asks. With a copy that follows links (-L, -H) a symlinked
    folder inside the source is a doubt: what it holds is not listed. Target folders that cannot lead to the root are
    not entered (a copy into a folder above the project reads the source only where it can matter)."""
    top = sl._norm_candidate(src, ctx.here)
    if set(src) & _GLOB or not os.path.isdir(top):
        ctx.doubt("a copy of the contents of a folder the guard cannot list (%s)" % src)
        return None
    seen = 0
    try:
        for dirpath, dirnames, filenames in os.walk(top, onerror=_raise):
            rel_dir = os.path.relpath(dirpath, top)
            for name in dirnames + filenames:
                seen += 1
                if seen > limit:
                    ctx.doubt("a copy of the contents of a folder too big to list (%s)" % src)
                    return None
                if deref and name in dirnames and os.path.islink(os.path.join(dirpath, name)):
                    ctx.doubt("a copy that follows a symlinked folder inside its source (%s)" % src)
                    return None
                joined = os.path.normpath(os.path.join(target_dir, rel_dir, name))
                if level(joined, ctx) == "exact":
                    return joined
            dirnames[:] = [d for d in dirnames if _can_reach_root(os.path.join(target_dir, rel_dir, d), ctx)]
    except OSError:
        ctx.doubt("a copy of the contents of a folder the guard cannot read (%s)" % src)
    return None


def _raise(err):
    raise err


_CP_PLAIN_OPTION = re.compile(r"-[RrapvfnX]+")
_DITTO_PLAIN_OPTION = re.compile(r"-V")


def _sole_plain_copy(ctx):
    """R-558 r4: the listing of a contents copy is trusted only when the whole Bash command is ONE plain foreground `cp` or
    `ditto`: no `&&`, `;`, `|`, `&`, loop, subshell, substitution, `bash -c`, here-document, env wrapper or xargs, so nothing
    runs before or beside it that could fill the folder, AND every option comes from a short ALLOW-LIST: cp `-R -r -a -p -v
    -f -n -X` (and groups of those letters), ditto no option or `-V`. Any other option, any long option and any other verb
    (rsync, gcp, a path to a renamed binary) is not trusted: three rounds of denying option shapes each found new ones.
    The listing can only ADD an ask, never remove one."""
    raw = (ctx.raw or "").strip()
    toks = sl._sole_simple(raw) if raw else None
    if not toks:
        return False
    verb = toks[0]
    if verb == "cp":
        plain = _CP_PLAIN_OPTION
    elif verb == "ditto":
        plain = _DITTO_PLAIN_OPTION
    else:
        return False                                       # also /bin/cp, gcp, rsync: not the plain word
    return all(plain.fullmatch(t) for t in toks[1:] if t.startswith("-") and t != "-")


def _land(head, srcs, v, rec, deref, notarget, ctx):
    n = len(ctx.keys) + len(ctx.doubts)
    d0 = len(ctx.doubts)
    try:
        _land_inner(head, srcs, v, rec, deref, notarget, ctx)
    finally:
        if len(ctx.keys) + len(ctx.doubts) > n:
            ctx.hard = True
            for i in range(d0, len(ctx.dclass)):
                ctx.dclass[i] = ctx.dclass[i] or "contents"


def _land_inner(head, srcs, v, rec, deref, notarget, ctx):
    """Judge what the sources write into the folder `v`, which is the project root, a folder inside it that holds protected
    data, or (R-562) a folder above it. A source ending in `/` (or `/.`, `.`, `..`, a ditto source, or any folder under
    -T) writes the files it HOLDS into `v` (R-558): they are listed. A source that is not `/.` also lands under its own
    name (GNU cp copies `X/` to `v/X`), so that name is judged as well."""
    contents = 0
    for s in srcs or [""]:
        sv = val(s, ctx) if s else ""
        if sv is None:
            ctx.doubt("%s into a folder that holds protected data, from a name the guard cannot read" % head)
            continue
        dotted = sv in (".", "..") or sv.endswith(("/.", "/.."))
        if sv and (head == "ditto" or (rec and (sv.endswith("/") or dotted)) or
                   (notarget and os.path.isdir(sl._norm_candidate(sv, ctx.here)))):
            contents += 1
            if not _sole_plain_copy(ctx):
                ctx.doubt("a copy of the contents of a folder inside a command that is more than one plain copy (%s)" % sv,
                          hard=True)
            else:
                held = _contents_hit(sv, v, ctx, deref=deref)
                if held:
                    ctx.write(sl._norm_candidate(held, ctx.here))
            if head == "ditto" or dotted:
                continue
        joined = os.path.join(v, os.path.basename(sv.rstrip("/"))) if sv else v
        jl = level(joined, ctx)
        if jl is None and rec and _above_root(joined, ctx):
            jl = "ancestor"                                # R-562: a folder that holds the project, merged with the source
        if jl == "exact" or (rec and jl in ("ancestor", "root")) or (head == "ditto" and not sv):
            ctx.write(sl._norm_candidate(joined, ctx.here))
    if contents and len(srcs) > 1:
        ctx.doubt("a copy of the contents of a folder together with other sources (one may land inside another)", hard=True)


def _unknown_folders(dest, ctx):
    """R-558 r4 (N3): the folders a destination word the guard cannot resolve may stand for, judged by the word's own last
    name: the root, a folder above it, or a folder inside it that holds protected data, whose name is that last name.
    Nothing when the last name is itself unreadable (a variable, `.`, `..`, a glob): that is the older doubt, as before."""
    comps = [c for c in dest.rstrip("/").split("/") if c not in ("", ".")]
    tail = comps[-1] if comps else ""
    if not tail or tail == ".." or "$" in tail or "`" in tail or "{" in tail or set(tail) & _GLOB or tail.startswith("~"):
        return []
    cands = [ctx.root]
    for p in ctx.patterns:
        acc = ctx.root
        for c in str(p).split("/"):
            if not c or set(c) & _GLOB:
                break
            acc = os.path.join(acc, c)
            cands.append(acc)
    d = ctx.root
    while os.path.dirname(d) != d:
        d = os.path.dirname(d)
        cands.append(d)
    want = sl._fold(tail)
    return list(dict.fromkeys(f for f in cands if sl._fold(os.path.basename(f)) == want))


def copy_move(head, args, ctx):
    flags, ops, dest, notarget, i = [], [], None, False, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            ops += args[i:]
            break
        if a.startswith("--"):
            if a.partition("=")[0] in ("--suffix", "--no-preserve", "--sparse") or _long(a, "--suffix", 4):
                if "=" not in a and i < len(args):
                    i += 1                                   # R-558 r3: `--suffix .bak` takes the next word
                flags.append(a)
            elif _long(a, "--target-directory", 3):
                if "=" in a:
                    dest = a.partition("=")[2]
                elif i < len(args):
                    dest = args[i]
                    i += 1
            elif _long(a, "--no-target-directory", 6):
                notarget = True
            else:
                flags.append(a)
        elif a.startswith("-") and a != "-":
            cut = len(a)
            for j in range(1, len(a)):                       # a cluster of short flags: `-Rt <dir>`, `-Rt<dir>`, `-aT`
                if a[j] == "S" and head in ("cp", "mv"):
                    if j + 1 >= len(a) and i < len(args):
                        i += 1                               # `-S <suffix>`: the value is the next word
                    cut = j + 1                              # `-RSt`: the rest of the cluster is the suffix
                    break
                if a[j] == "T":
                    notarget = True
                elif a[j] == "t" and head != "ditto":
                    if j + 1 < len(a):
                        dest = a[j + 1:]
                    elif i < len(args):
                        dest = args[i]
                        i += 1
                    cut = j + 1
                    break
            flags.append(a[:cut])
        else:
            ops.append(a)
    if head == "install" and any(f in ("-d", "--directory") for f in flags):
        for o in ops:
            target(o, ctx, what="install -d")
        return
    srcs = ops
    if dest is None:
        if not ops:
            return
        dest, srcs = ops[-1], ops[:-1]
    if head == "mv":
        for s in srcs:
            target(s, ctx, destructive=True, what="a move")
    rec = _recursive(flags) or head == "ditto" or (head == "cp" and any(_archive(f) for f in flags))   # cp -a is -R
    deref = any(_dereference(f) for f in flags)
    parents = head == "cp" and any(_long(f, "--parents", 4) for f in flags)
    v = val(dest, ctx)
    if v is None:
        if _FOUND in dest:
            return                                           # find's own start folders are judged by find()
        if not _tail_excludes(dest, ctx):
            ctx.doubt("%s into a name the guard cannot read (%s)" % (head, dest))
        for f in _unknown_folders(dest, ctx):
            # R-558 r4 (N3): a target the guard cannot read whose own name is the root's (or a folder above it, or one that
            # holds protected data) may BE that folder: each source's name inside it is judged
            (_land_above if _above_root(f, ctx) else _land)(head, srcs, f, rec, deref, notarget, ctx)
        if parents:
            ctx.doubt("cp --parents into %s, which the guard cannot read" % dest, hard=True)
        if rec and dest.rstrip("/").rsplit("/", 1)[-1] in (".", ".."):
            # L2: a folder copy into `<unreadable>/..` or `<unreadable>/.` can land on a folder that holds the project
            ctx.doubt("%s of a folder into %s, whose folder the guard cannot read" % (head, dest), hard=True)
        return
    lv = level(v, ctx)
    if lv == "exact":
        ctx.write(sl._norm_candidate(v, ctx.here))
        return
    if lv in ("ancestor", "root"):
        if parents:                                          # N1: --parents rebuilds the source's folders under the target
            ctx.doubt("cp --parents into a folder that holds protected data", hard=True)
        _land(head, srcs, v, rec, deref, notarget, ctx)
    elif lv is None:
        if _GLOB_IN_DIR.search(v):
            ctx.doubt("%s into a name with a glob in a folder part (%s)" % (head, dest))     # R-558 r7
        for d in _above_candidates(v, ctx):                # R-562: a folder above the project is judged too
            if parents:
                since = len(ctx.doubts)
                ctx.doubt("cp --parents into a folder that holds the project", hard=True)
                ctx.mark_above(d, since)
            _land_above(head, srcs, d, rec, deref, notarget, ctx)


def _land_above(head, srcs, v, rec, deref, notarget, ctx):
    """R-562: `_land` into a folder that holds the project. The older name scan never sees such a target, so a reason to ask
    found here is marked (ctx.above) for the hook to act on by itself."""
    before, d0 = len(ctx.keys) + len(ctx.doubts), len(ctx.doubts)
    _land(head, srcs, v, rec, deref, notarget, ctx)
    if len(ctx.keys) + len(ctx.doubts) > before:
        ctx.mark_above(v, d0)


def _above_candidates(v, ctx):
    """The folders `v` can mean that hold the project root (v itself, or each glob hit)."""
    if set(v) & _GLOB:
        hits = _glob.glob(os.path.join(ctx.here, os.path.expanduser(v)))
        return [h for h in hits if _above_root(h, ctx)]
    return [v] if _above_root(v, ctx) else []


_RSYNC_VALUED = {"--exclude", "--include", "--exclude-from", "--include-from", "--filter", "--rsh", "--rsync-path",
                 "--files-from", "--backup-dir", "--suffix", "--temp-dir", "--partial-dir", "--compare-dest", "--copy-dest",
                 "--link-dest", "--log-file", "--log-file-format", "--max-size", "--min-size", "--bwlimit", "--timeout",
                 "--contimeout", "--port", "--chmod", "--chown", "--usermap", "--groupmap", "--out-format", "--info",
                 "--debug", "--compress-level", "--compress-choice", "--skip-compress", "--block-size", "--modify-window",
                 "--read-batch", "--write-batch", "--only-write-batch", "--address", "--sockopts", "--password-file",
                 "--protocol", "--iconv", "--checksum-choice", "--cc", "--checksum-seed", "--max-alloc", "--stop-after",
                 "--stop-at", "--time-limit", "--early-input", "--outbuf", "--remote-option", "--fuzzy-basis"}
_RSYNC_BOOL = {"--checksum", "--backup", "--partial", "--compress", "--verbose", "--delete", "--archive", "--recursive",
               "--dry-run", "--update", "--links", "--times", "--perms", "--owner", "--group", "--progress", "--stats",
               "--human-readable", "--quiet", "--force", "--inplace", "--append", "--relative", "--prune-empty-dirs"}
_RSYNC_SHORT_VALUED = "efTBM@"


def _rsync_split(args):
    """-> (flags, operands): rsync options may follow the operands, and some take a value (`--exclude foo`, `-e ssh`,
    `-ae ssh`): the value is not an operand. A long option that is a prefix of one that takes a value counts as taking it
    (rsync accepts unambiguous abbreviations); the booleans that are also such a prefix are listed."""
    flags, ops, writes, runners, remote, i, after = [], [], [], [], [], 0, False
    while i < len(args):
        a = args[i]
        i += 1
        if after or a == "-" or not a.startswith("-"):
            ops.append(a)
        elif a == "--":
            after = True
        elif a.startswith("--"):
            key = a.partition("=")[0]
            flags.append(a)
            takes = key in _RSYNC_VALUED or (key not in _RSYNC_BOOL and len(key) >= 4 and
                                             any(v.startswith(key) for v in _RSYNC_VALUED))
            if len(key) >= 4 and ("--rsh".startswith(key) or "--rsync-path".startswith(key)):
                # round 5: the remote shell, and the program it starts, are commands rsync runs; an abbreviation counts
                if "=" in a:
                    runners.append(a.partition("=")[2])
                elif i < len(args):
                    runners.append(args[i])
            if sl._rsync_write_opt(a):                       # R-558 r4 (N2): its value is a file or folder rsync writes
                rel = next((d for d in _RSYNC_DEST_DIRS if d.startswith(key)), None)  # r6 (H4): read from the destination
                if "=" in a:
                    writes.append((a.partition("=")[2], rel))
                elif i < len(args):
                    writes.append((args[i], rel))
            if len(key) >= 4 and "--remote-option".startswith(key):  # r6 (H4): an option handed to the other rsync
                if "=" in a:
                    remote.append(a.partition("=")[2])
                elif i < len(args):
                    remote.append(args[i])
            if takes and "=" not in a and i < len(args):
                i += 1
        else:
            cut = len(a)
            for j in range(1, len(a)):
                if a[j] in _RSYNC_SHORT_VALUED:
                    cut = j + 1
                    if a[j] == "e":                          # -e is --rsh
                        if j + 1 < len(a):
                            runners.append(a[j + 1:])
                        elif i < len(args):
                            runners.append(args[i])
                    if a[j] in "TM":                         # -T is --temp-dir; -M is --remote-option (r6)
                        got = a[j + 1:] if j + 1 < len(a) else (args[i] if i < len(args) else None)
                        if got is not None:
                            (writes.append((got, "--temp-dir")) if a[j] == "T" else remote.append(got))
                    if j + 1 >= len(a) and i < len(args):
                        i += 1
                    break
            flags.append(a[:cut])
    return flags, ops, writes, runners, remote


_RSYNC_DEST_DIRS = ("--backup-dir", "--partial-dir", "--temp-dir")


def _rsync_remote_path(op):
    """`host:path` -> `path` (`host:` -> `.`); None for a local word, a daemon `host::module` or an rsync:// URL. A colon
    before any slash makes a word remote, as rsync reads it."""
    if ":" not in op or op.startswith("/") or "/" in op.partition(":")[0] or op.startswith("rsync://"):
        return None
    rest = op.partition(":")[2]
    if rest.startswith(":"):
        return None
    return rest or "."


def _holds_protected(folder, ctx):
    """R-558 r6: the folder is the root, a folder above it, or a folder inside the project that protected data sits under."""
    f = sl._fold(os.path.realpath(folder)).rstrip(os.sep) + os.sep
    root = sl._fold(ctx.root).rstrip(os.sep) + os.sep
    if root.startswith(f):
        return True
    for pat in ctx.patterns:
        pre = _static_prefix(str(pat)).strip("/")
        if pre and sl._fold(os.path.join(ctx.root, pre)).startswith(f):
            return True
    return False


def _rsync_dest_dirs(writes, dest, ctx):
    """R-558 r6 (H4): rsync reads a relative --backup-dir from the destination folder, and a relative --partial-dir or
    -T/--temp-dir from each file's own folder in it (any folder of the tree), not from the current folder."""
    for w, rel in writes:
        if not rel or not w:
            continue
        word, w = w, val(w, ctx)
        if w is None:
            ctx.doubt("an rsync folder option the guard cannot read (%s)" % word)   # R-558 r7: `$X`, a backtick, `~NAME`
            continue
        if w.startswith("/"):
            continue
        if _GLOB & set(w):
            ctx.doubt("an rsync folder option with a glob (%s)" % w)
            continue
        if dest is None:
            ctx.doubt("an rsync folder option read from a destination the guard cannot read (%s)" % w)
            continue
        if rel == "--backup-dir":                       # the destination folder (or, for a file, the folder holding it)
            for d in dict.fromkeys((dest, os.path.dirname(dest.rstrip(os.sep)) or os.sep)):
                target(os.path.join(d, w), ctx, destructive=True, what="rsync --backup-dir")
            continue
        if ".." in w.replace("\\", "/").split("/"):
            ctx.doubt("an rsync folder option that climbs out of the destination (%s)" % w)
            continue
        target(os.path.join(dest, w), ctx, destructive=True, what="an rsync folder option")
        if _holds_protected(dest, ctx):
            ctx.doubt("an rsync folder option inside a destination that holds protected data (%s)" % w)


def rsync(args, ctx):
    flags, ops, writes, runners, remote = _rsync_split(args)
    if any(f.startswith("--") and len(f.partition("=")[0]) >= 4 and "--daemon".startswith(f.partition("=")[0]) for f in flags):
        ctx.doubt("rsync --daemon serves folders its configuration names")          # r6 (L2)
    rsh = bool(runners) or ctx.rsync_rsh
    if remote:                                               # r6 (H4): -M / --remote-option values are rsync options too
        _f, _o, more, _r, _m = _rsync_split(remote)          # r7: joined in order (`-M--backup-dir -M../data/real`)
        writes += more
    for code in runners:                                     # round 5: -e / --rsh runs its value like `sh -c`
        sub = Ctx(ctx.project, ctx.patterns, ctx.base, ctx.session, ctx.trusted)
        sub.here, sub.depth, sub.lost, sub.rsync_rsh = ctx.here, ctx.depth, ctx.lost, ctx.rsync_rsh
        run(code, sub)
        _absorb(ctx, sub)
        if sub.keys:
            ctx.hard = True                                  # the older scan reads the value as one word
    for w, _rel in writes:                                   # N2: --log-file, --write-batch, --backup-dir, -T ... write there,
        target(w, ctx, destructive=True, what="an rsync option that writes a file")   # a dry run (-n) included
    if rsh:
        # r6 (H3): with a remote shell set (-e, --rsh, RSYNC_RSH) the "remote" rsync may run here (`-e env`): host:path is
        # also the local path `path`. r7: and `host::module` / `rsync://` then read a module of an rsyncd.conf in the
        # current folder, whose path the guard cannot know: that asks
        for o in ops:
            if o.startswith("rsync://") or "::" in o.split("/")[0]:
                ctx.doubt("rsync operand %s may be a module of a daemon run by the remote shell" % o, hard=True)
        ops = [_rsync_remote_path(o) or o for o in ops]
    local = [o for o in ops if _rsync_remote_path(o) is None]
    dest_dir = None
    if local and len(ops) > 1 and local[-1] == ops[-1]:
        dv = val(ops[-1], ctx)
        dest_dir = None if dv is None else sl._norm_candidate(dv, ctx.here)
    if len(ops) > 1 and (local and local[-1] == ops[-1] or rsh):
        _rsync_dest_dirs(writes, dest_dir, ctx)
    if any(f in ("-n", "--dry-run") or _long(f, "--dry-run", 5) or (f.startswith("-") and not f.startswith("--") and "n" in f[1:])
           for f in flags):
        return
    ops = [o for o in ops if not re.match(r"^[\w.-]+@?[\w.-]*:[^/]", o) or o.startswith("/")]
    if not ops:
        return
    dest, srcs = ops[-1], ops[:-1]
    if any(f.startswith("--remove-source") for f in flags):
        for s_ in srcs:
            target(s_, ctx, destructive=True, what="rsync --remove-source-files")
    if ":" in dest and not dest.startswith("/"):
        return
    target(dest, ctx, destructive=True, what="rsync")
    v = val(dest, ctx)
    if v is None:
        # a target the guard cannot read whose own name is the root's, or a folder above it: the same as a known one
        for f in _unknown_folders(dest, ctx):
            if _above_root(f, ctx):
                since = len(ctx.doubts)
                ctx.doubt("rsync into %s, which may be a folder that holds the project" % dest, hard=True)
                ctx.mark_above(f, since)
        return
    # R-558 r4 (N1): rsync is not a trusted verb. Into a folder above the project it ALWAYS asks: its -R / --relative and
    # --files-from rebuild source paths under the target, --delete removes what the source lacks (the project among it),
    # -L -k -K follow links, and every option shape added a way to land on the project
    for d in _above_candidates(v, ctx):
        since = len(ctx.doubts)
        ctx.doubt("rsync into a folder that holds the project", hard=True)
        if any(f.startswith("--del") for f in flags):
            ctx.write(sl._norm_candidate(d, ctx.here))
        ctx.mark_above(d, since)


_R6_RSYNC_DOUBT = re.compile(r"rsync folder option|rsync --daemon|module of a daemon")


def rsync_strict(args, project, patterns, here, lost, rsh):
    """R-558 r6, for the base guard's strict scan: True when rsync writes resolved protected data (an option value, a host:path
    run by a local remote shell, a destination-relative folder option) or raises a round-6 doubt (a folder option that climbs
    out of the destination or sits in one that holds protected data, rsync --daemon)."""
    ctx = Ctx(project, patterns, here)
    ctx.here, ctx.lost, ctx.rsync_rsh = here, lost, ctx.rsync_rsh or rsh
    try:
        rsync(list(args), ctx)
    except Exception:
        return True
    return bool(ctx.keys) or any(_R6_RSYNC_DOUBT.search(d) for d in ctx.doubts)


def archive(head, toks, ctx):
    """tar / unzip / 7z ...: extracting writes into a folder; creating writes the archive file."""
    if head in sl._EXTRACTORS and sl._extracting(head, toks):
        if any(sl._unresolved(val(t, ctx) or t) for t in toks[1:] if not t.startswith("-")) and \
                any(val(t, ctx) is None for t in toks[1:]):
            ctx.doubt("an archive extracted to or from a name the guard cannot read")
            return
        if head in ("tar", "bsdtar", "gtar") and sl._tar_rewrites_names(toks):
            ctx.doubt("an archive extracted with absolute or rewritten member names")
            return
        for d in sl._extract_folders(head, [val(t, ctx) or t for t in toks], [ctx.here]):
            if level(d, ctx):
                ctx.write(d)
            elif _above_root(d, ctx) and not (head == "unzip" and any(re.fullmatch(r"-[a-zA-Z]*[ltpvZ][a-zA-Z]*", t)
                                                                      for t in toks[1:])):         # unzip -l lists only
                # M2 / R-562: the archive may hold a folder named like the project, which then lands on its real data
                since = len(ctx.doubts)
                ctx.doubt("an archive extracted into a folder that holds the project", hard=True)
                ctx.mark_above(d, since)
        return
    if head in ("tar", "bsdtar", "gtar"):
        out = None
        for k, t in enumerate(toks[1:], 1):
            if t in ("-f", "--file") and k + 1 < len(toks):
                out = toks[k + 1]
            elif t.startswith("--file="):
                out = t.partition("=")[2]
            elif t.startswith("-") and not t.startswith("--") and "f" in t and k + 1 < len(toks):
                out = toks[k + 1]
            elif k == 1 and not t.startswith("-") and "f" in t and k + 1 < len(toks):
                out = toks[k + 1]
        if out and out != "-":
            target(out, ctx, what="an archive")
        return
    if head in ("zip", "7z", "7za", "7zr"):
        ops = [t for t in toks[1:] if not t.startswith("-")]
        ops = ops[1:] if head != "zip" and ops and ops[0] in ("a", "u") else ops
        if ops:
            target(ops[0], ctx, what="an archive")


_FIND_EXECS = ("-exec", "-execdir", "-ok", "-okdir")
_FIND_FILE_PRIMARIES = ("-fprint", "-fprint0", "-fprintf", "-fls")


def _find_execs(args):
    """R-558 r3 (H3): the command words each -exec / -execdir / -ok / -okdir runs, up to its `+` (a `;` already ended the
    segment), with `{}` replaced by an unreadable name."""
    out = []
    for i, t in enumerate(args):
        if t in _FIND_EXECS:
            cmd = []
            for w in args[i + 1:]:
                if w == "+":
                    break
                cmd.append(w.replace("{}", _FOUND))
            out.append(cmd)
    return out


def find(args, ctx):
    ops = sl._find_operands(["find"] + list(args))
    writers = [t for t in args if t in sl._FIND_WRITERS]
    if not writers:
        return
    for i, t in enumerate(args):
        if t in _FIND_FILE_PRIMARIES and i + 1 < len(args):
            target(args[i + 1], ctx, destructive=True, what="find " + t)
    execs = _find_execs(args)
    for cmd in execs:                                                      # H3: the command it runs is judged like any other
        words = unwrap(cmd)
        if words:
            before = len(ctx.keys)
            command(words, {}, ctx)
            if len(ctx.keys) > before:
                ctx.hard = True                                            # the older scan never reads what find runs
    names = [os.path.basename(c[0]) for c in execs if c]
    pure_read = writers and all(t in _FIND_EXECS for t in writers) and names and \
        all(e in _READERS or e in ("true", "echo", "printf") for e in names)
    if pure_read:
        return
    for o in ops:
        v = val(o, ctx)
        if v and not v.startswith("-") and sl._fold(ctx.root).startswith(sl._fold(sl._norm_candidate(v, ctx.here)).rstrip(os.sep) + os.sep):
            ctx.write(sl._norm_candidate(v, ctx.here))                     # a delete from a folder that holds the project
            continue
        target(o, ctx, destructive=True, what="find with a delete or exec")


def sed(args, ctx):
    flags, ops = _flags_ops(args, valued={"-e", "-f", "--expression", "--file"})
    inplace = any(f == "--in-place" or f.startswith("--in-place=") or (f.startswith("-") and not f.startswith("--")
                                                                         and "i" in f[1:]) for f in flags)
    script_given = any(f in ("-e", "-f", "--expression", "--file") for f in flags)
    files = ops if script_given else ops[1:]
    script = " ".join(ops[:1]) if not script_given else " ".join(f for f in flags if not f.startswith("-"))
    if inplace:
        for f in files:
            target(f, ctx, what="sed -i")
        return
    if re.search(r"(?:^|[;{}\s/])[wW]\s*\S+", script) and not re.fullmatch(r"[^wW]*", script):
        ctx.doubt("a sed script that writes to a file")


_AWK_WRITES = re.compile(r"\b(?:print|printf)\b[^;}\n]*>{1,2}\s*\S|\bsystem\s*\(|\|\s*\"|\bgetline\b[^;}\n]*\|\s*\"|inplace")


def awk(args, ctx):
    prog = " ".join(a for a in args if not a.startswith("-") and (" " in a or "{" in a or "$" in a or "(" in a))
    if _AWK_WRITES.search(prog) or any(a in ("-i", "-f") for a in args) and "inplace" in " ".join(args):
        lits = re.findall(r">{1,2}\s*\"([^\"]+)\"", prog)
        if not lits or "system(" in prog or re.search(r"\|\s*\"", prog):
            ctx.doubt("an awk program that writes to a file or runs a command")
        else:
            for l in lits:
                target(l, ctx, what="awk")


# ------------------------------------------------------------------------------------------------------ interpreters

def _code_check(code, ctx, what="code"):
    if not sl._CODE_WRITES.search(code):
        return
    for lit in re.findall(r"""['"]([^'"\s]{1,200})['"]""", code):          # a protected file named in the code
        v = val(lit, ctx)
        if v and not v.startswith("-") and level(v, ctx) == "exact":
            ctx.write(sl._norm_candidate(v, ctx.here) if not (set(v) & _GLOB) else os.path.join(ctx.here, v))
            return
    if ctx.trusted:
        return
    if sl._writes_only_literals(code, ctx.project, ctx.patterns, [ctx.base, ctx.here]):
        return
    ctx.doubt("%s that writes or deletes files; where it writes cannot be read from the code" % what)


def _exact_args(args, ctx):
    hit = False
    for a in args:
        for cand in (a, a.partition("=")[2]) if "=" in a and not a.startswith("=") else (a,):
            v = val(cand, ctx)
            if v and not v.startswith("-") and level(v, ctx) == "exact":
                ctx.write(sl._norm_candidate(v, ctx.here) if not (set(v) & _GLOB) else os.path.join(ctx.here, v))
                hit = True
    return hit


def _absorb(ctx, sub):
    ctx.keys += sub.keys
    ctx.doubts += sub.doubts
    ctx.dclass += sub.dclass
    ctx.dverb += sub.dverb
    ctx.unsure = ctx.unsure or sub.unsure
    ctx.above_targets += sub.above_targets
    ctx.above = ctx.above or sub.above
    ctx.hard = ctx.hard or sub.hard


def shell(head, args, ctx):
    code_at = next((i for i, a in enumerate(args) if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a)), None)
    if code_at is not None and code_at + 1 < len(args):
        code = val(args[code_at + 1], ctx) or args[code_at + 1]
        sub = Ctx(ctx.project, ctx.patterns, ctx.base, ctx.session, ctx.trusted)
        sub.here, sub.depth, sub.lost, sub.rsync_rsh = ctx.here, ctx.depth, ctx.lost, ctx.rsync_rsh
        run(code, sub)
        _absorb(ctx, sub)
        return
    ops = [a for a in args if not a.startswith("-")]
    if ops:
        return script(ops[0], ops[1:], ctx)
    if {"--version", "--help", "-h", "-v"} & set(args):
        return
    if ctx.bodies:
        body = ctx.bodies.pop(0)
        sub = Ctx(ctx.project, ctx.patterns, ctx.base, ctx.session, ctx.trusted)
        sub.here, sub.depth, sub.lost, sub.rsync_rsh = ctx.here, ctx.depth, ctx.lost, ctx.rsync_rsh
        run(body, sub)
        _absorb(ctx, sub)
        return
    ctx.doubt("a shell that runs a program read from its input")


_OPT_WITH_VALUE = {"-W", "-X", "-r", "-I"}


def script(path, rest, ctx):
    """An interpreter run on a file: read the file when it is the session's own or recent (the older rule), and judge what
    its code writes. The user's older, committed code is not read: it is not asked about, unless it is handed the real data."""
    if ctx.here and level(ctx.here, ctx) == "exact":
        ctx.doubt("a program run inside protected data")
        return
    if os.path.basename(path) in ("budgit", "studio"):
        ctx.doubt("a budgit program run through an interpreter")
        return
    v = val(path, ctx)
    text = sl._script_text(v, ctx.here, ctx.session) if v else None
    if text is not None:
        if sl._CODE_WRITES.search(text) and not ctx.trusted and \
                not sl._writes_only_literals(text, ctx.project, ctx.patterns, [ctx.base, ctx.here]):
            ctx.doubt("a script written in this session that writes or deletes files; where it writes cannot be read from the code")
        return
    ctx.doubt("a program %s whose code cannot be read" % os.path.basename(path))
    _exact_args(rest, ctx)


def interpreter(head, args, env, ctx):
    if head in ("perl", "ruby") and any(re.match(r"^-[A-Za-z0-9]*i", a) for a in args if a.startswith("-") and not a.startswith("--")):
        skip = False
        for a in args:                                    # -i edits the named files in place
            if skip:
                skip = False
            elif a in ("-e", "-E"):
                skip = True
            elif not a.startswith("-"):
                target(a, ctx, destructive=True, what="an in-place edit")
    if "-m" in args[:3] and args.index("-m") + 1 < len(args):
        mod = args[args.index("-m") + 1]
        if mod.split(".")[0] in _NOPARSE_RUNNERS or mod in _NOPARSE_RUNNERS:
            _exact_args(args, ctx)
            return
        return script(mod.replace(".", "/") + ".py", args[args.index("-m") + 2:], ctx)
    if any(a in sl._INLINE_FLAGS and a != "-" for a in args) or "-" in args or (not args and ctx.bodies):
        k = next((i for i, a in enumerate(args) if a in sl._INLINE_FLAGS and a != "-"), None)
        if k is not None and k + 1 < len(args) and args[k] not in ("-s", "-x"):
            code = args[k + 1]
            _code_check(code, ctx, "code")
            return
        if ctx.bodies:
            body = ctx.bodies.pop(0)
            _code_check(body, ctx, "code")
            return
        if "-" in args or not args:
            ctx.doubt("a program read from the input")
            return
    ops = [a for a in args if not a.startswith("-")]
    # skip the value of options that take one (python -W ignore x.py, node -r mod x.js)
    i, flag_vals = 0, 0
    while i < len(args):
        if args[i] in _OPT_WITH_VALUE and i + 1 < len(args) and not args[i + 1].startswith("-"):
            flag_vals += 1
            ops = [a for a in ops if a != args[i + 1]] if ops and args[i + 1] in ops[:1] else ops
        i += 1
    if head in ("node", "nodejs", "deno", "bun") and any(a in ("--test", "test") for a in args):
        _exact_args(args, ctx)
        return
    if ops:
        return script(ops[0], ops[1:], ctx)
    if {"--version", "--help", "-V", "-h", "-v", "--help"} & set(args) or not args:
        return
    return


# --------------------------------------------------------------------------------------------------------------- git

def _toplevel(workdir):
    d = os.path.realpath(workdir)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def git(words, env, ctx):
    toks = list(words)
    toks[0] = "git"
    at, locs, cvals = sl._git_global_scan(toks)
    if at is None:
        return
    verb, args = toks[at], toks[at + 1:]
    workdir, sure = ctx.here, not ctx.lost
    for _s, _e, value, kind in locs:
        v = val(value, ctx)
        if v is None or kind in ("gitdir", "wt"):
            sure = False
            continue
        workdir = sl._norm_candidate(v, workdir)
    if any(n in env for n in ("GIT_WORK_TREE", "GIT_DIR", "GIT_INDEX_FILE", "GIT_COMMON_DIR")) or \
            any(cv.split("=", 1)[0].lower() == "core.worktree" for cv in cvals):
        sure = False
    if any(re.match(r"^(?:--paginate|-p$|-P$|--exec-path|--config-env|--html-path|--namespace|--super-prefix)", g)
           for g in toks[1:at]):
        ctx.doubt("a git global option that runs a program")
        return
    if verb == "describe" and any(a.startswith(("--dirty", "--broken")) for a in args):
        ctx.doubt("git describe --dirty reads the working tree through a program")
        return
    if verb == "worktree" and args[:1] != ["list"]:
        ctx.doubt("git worktree changes folders")
        return
    if (verb in sl._RO_SPECS or verb in _GIT_READ_EXTRA) and any(
            (set(a) & _GLOB and level(a, ctx, workdir) == "exact") or a.startswith("~") or "$" in a or "{" in a
            for a in args if not a.startswith("-")):
        ctx.doubt("a git read over a pattern that matches protected data")
        return
    if verb in ("branch", "tag", "remote", "config") and _tree(verb, args):
        ctx.doubt("git %s changes refs or configuration" % verb)
        return
    if verb in sl._RO_SPECS or verb in _GIT_READ_EXTRA or (verb == "stash" and args[:1] in (["list"], ["show"])) or \
            (verb in ("branch", "tag", "remote", "config") and not _tree(verb, args)):
        bad = [a for a in args if _GIT_BAD_READ_OPT.match(a)]
        if bad:
            for a in bad:
                if a.startswith(("--out", "-O")) and "=" in a:
                    out_file = a.partition("=")[2]
                    target(out_file if os.path.isabs(out_file) or "$" in out_file else os.path.join(workdir, out_file),
                           ctx, destructive=True, what="git --output")
                else:
                    ctx.doubt("a git option that writes a file or runs a program (%s)" % a)
            return
        if not sl._git_env_clean() or cvals or bool(env) or not sl._git_config_clean(workdir) or any("core." in c.lower() and "pager" in c.lower() or "fsmonitor" in c.lower() for c in cvals):
            ctx.doubt("git with a program in its environment or configuration")
        return
    if verb in ("add", "commit"):
        if not sure:
            ctx.doubt("git %s in a repository location the guard cannot verify" % verb)
            return
        resolved = [val(t, ctx) if "$" in t else t for t in toks]
        if any(r is None for r in resolved):
            ctx.doubt("git %s with a name the guard cannot read" % verb)
            return
        if not sl._git_exception(resolved, " ".join(resolved), ctx.project, ctx.patterns, ctx.here):
            ctx.doubt("git %s that names protected data" % verb)
            ctx.keys.append(ctx.root)
        return
    if verb not in _GIT_TREE_VERBS:
        ctx.doubt("git %s: a verb the guard does not class" % verb)
        return
    if not sure:
        ctx.doubt("git in a repository location the guard cannot verify")
        return
    top = _toplevel(workdir)
    if top is None:
        ctx.doubt("git outside a repository the guard can find")
        return
    whole = verb in ("checkout", "switch", "merge", "rebase", "pull", "clean", "revert", "cherry-pick", "am", "apply",
                     "read-tree", "checkout-index", "sparse-checkout", "submodule", "bisect", "filter-branch", "subtree") or \
        (verb == "stash" and args[:1] not in (["list"], ["show"], ["drop"], ["clear"])) or \
        (verb == "reset" and any(a.startswith(("--hard", "--merge", "--keep")) or a in ("--hard", "--merge", "--keep") for a in args))
    froot = sl._fold(ctx.root)
    ftop = sl._fold(top)
    if whole and (ftop == froot or froot.startswith(ftop + os.sep) or level(top, ctx)):
        ctx.write(ctx.root)
    pathy = verb in ("restore", "rm", "mv", "checkout", "worktree", "clean", "reset", "stash")
    if pathy:
        after = False
        for a in args:
            if a == "--":
                after = True
                continue
            if a.startswith("-") and not after:
                continue
            if verb == "stash" and not after and a in ("push", "save", "pop", "apply", "drop", "branch", "list", "show", "-u"):
                continue
            v = val(a, ctx)
            if v is None:
                if verb in ("restore", "rm", "mv", "worktree") and not _tail_excludes(a, ctx):
                    ctx.doubt("git %s with a name the guard cannot read" % verb)
                continue
            lv = level(v, ctx, workdir)
            if lv == "exact" or (lv in ("ancestor", "root") and verb in ("restore", "rm", "mv", "worktree", "checkout", "clean")):
                ctx.write(sl._norm_candidate(v, workdir))


def _tree(verb, args):
    """True when a branch / tag / remote / config call is not a plain read. `git config` writes unless it only reads a value
    or lists; branch, tag and remote change refs and .git, never the working files."""
    if verb == "config":
        return not any(a in ("--get", "--get-all", "--get-regexp", "-l", "--list", "--show-origin", "--show-scope") for a in args)
    if verb in ("branch", "tag"):
        listing = any(a in ("--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
                            "--show-current", "-n") or a.startswith(("--list", "--sort", "--format")) for a in args)
        if not listing and any(not a.startswith("-") for a in args):
            return True                                   # a name given: it makes a branch or a tag
        return any(re.match(r"^-[A-Za-z]*[dDmMcCfu]", a) or a in ("--delete", "--move", "--copy", "--force", "--edit-description",
                                                                  "--set-upstream-to", "--unset-upstream") for a in args
                   if not a.startswith("--contains"))
    if verb == "remote":
        return bool(args) and args[0] in ("add", "remove", "rm", "rename", "set-url", "prune", "update", "set-head", "set-branches")
    return False


# --------------------------------------------------------------------------------------------------------------- API

def judge(command, project, patterns, base, session=None, trusted=False):
    """Read a Bash command and return its Ctx: `.harmless`, `.keys` (resolved protected paths a write reaches), `.keyed`."""
    ctx = Ctx(project, patterns, base, session, trusted)
    ctx.raw = command
    try:
        run(command, ctx)
    except Exception as exc:                                   # a parser surprise is a doubt, never a pass
        ctx.doubt("the command could not be read (%s)" % type(exc).__name__)
    return ctx


_SCRIPTISH = re.compile(r"program|script|code|here-document|shell that runs|runs a command line", re.I)


def memory_keys(ctx):
    """What the user's yes to this ask would cover (see studiolib.real_data_yes_covers): `write:<path>` for each protected
    path a write reaches, `script:<root>` for a program whose writes cannot be read, `ambiguous:<root>` for a write whose
    target cannot be read, `lost:<root>` when a cd left the folder lost, `contents:<verb>:<root>` and `above:<verb>:<folder>` for the hard asks. A path or class the user did not answer
    for is never covered by another."""
    keys = ["write:" + sl._fold(p) for p in dict.fromkeys(ctx.keys)]
    classes = set()
    for why, klass, verb in zip(ctx.doubts, ctx.dclass, ctx.dverb):
        if klass == "above":
            continue                                       # remembered as `above:<verb>:<folder>` below
        if klass == "contents":
            classes.add("contents:" + sl._fold(verb))      # R-558 r6 (M1): a yes to one verb never covers another
        else:
            classes.add("script" if _SCRIPTISH.search(why) else "ambiguous")
    if ctx.unsure:
        classes.add("lost")                                # R-558 r7 (M3): the folder was lost: a key of its own, never `ambiguous`
    # R-558 r4 (N4): a hard ask has a memory class of its own, so a yes to an unrelated `ambiguous` or `script` ask never
    # passes it: `contents:<root>` for a copy whose listing cannot be trusted, `above:<folder>` for one into a folder that
    # holds the project (one yes per folder). R-558 r6 (M1): both carry the verb (`contents:cp:<root>`, `above:rsync:<folder>`)
    return keys + ["%s:%s" % (c, sl._fold(ctx.root)) for c in sorted(classes)] + \
        ["above:" + t for t in dict.fromkeys(ctx.above_targets)]


def has_write_signal(command):
    """Cheap text test for the parse-failure fallback: does the raw text look like it could write at all?"""
    if re.search(r"(?<![0-9&])>(?!&)|>>|>\||&>", re.sub(r"[0-9]?>\s*/dev/(?:null|stderr|stdout)|[0-9]?>&[0-9]", " ", command)):
        return True
    return bool(_WRITE_WORDS.search(command))
