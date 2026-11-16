"""R-1128 hotfix 1 (I-1687, I-1689): an agent may not search the whole disk, the home folder, or any folder above the project.

decision(tool, tool_input, project, cwd) -> ("deny", text) or None. It judges Bash (find, grep -r, rg, fd, ag, ack, ugrep,
locate, mdfind, ls -R, du, tree, git grep --no-index), the Grep tool and the Glob tool. The search root is resolved with
realpath, so a symlink inside the project that points above it is judged by where it lands. A root is refused when it is `/`,
the home folder, a folder above the home folder, or a folder above the project (or above the working folder; a worktree
passes because the working folder is the worktree, and `worktrees=` may add more folders to protect). A search inside the
project, a worktree of it, the scratchpad, or a path in the allowed list (project.json `search_roots_allowed`) passes. The
allowed list never widens to `/`, the home folder or a folder above it (safe_allowed), and an edit of it asks (studiolib).
Anything it cannot read, in a command that searches, is refused (fails closed); a command that does not search is never
touched.

What it reads (round 2): the command is split by its own quote-aware reader (quotes, backslashes, heredocs, `$(...)` and
backticks, which it reads as commands of their own); `cd`, `pushd` and `env -C` add the new folder to the folders a later
search may run in (every search is judged in every folder the command may have reached); a wrapper (timeout, nice, env,
nohup, stdbuf, command, sudo, xargs ...) is skipped up to the next command word, and `sh|bash|zsh -c` strings are read as
commands; each searcher has its own flag table, and a flag the table does not know makes every operand a possible root.

Round 3 (fail closed): every command is tokenized; a command with a searcher word (after quotes and backslashes are removed) is
refused when its command name is hidden (`$x`, `${IFS}`, `$(..)`, backticks, a glob such as `fin?`), when a shell reads its
commands from stdin or `-s`, or when a wrapper (env -S, busybox, toybox) hides the command. Residual, accepted: a command name
built from pieces with no searcher word anywhere in the text (`a=fi; b=nd; $a$b /`, or an encoded name) is not seen.

Out of scope on purpose: MCP search tools (a server decides what it reads; `budgit audit-mcp` reviews its config), and the
inside of a symlink loop followed with `find -L` or `rg --follow` (the root is judged, not what the links reach)."""
import fnmatch
import os
import re

# Release switch (R-1128 hotfix 1): the guard ships OFF (security review r3 H-A: unknown command names still pass; its fixes go to
# hotfix 2). A user's own `search_guard` key ("on" or "off") in the home config.json wins over it. It is never read from
# project.json, which an agent can edit.
SEARCH_GUARD_DEFAULT = False

TEXTS = {
    "en": {
        "deny_root": "budgit guard (search root): {tool} was refused because the search root `{root}` is {why}. Rule: an agent "
                     "does not search the whole disk, the home folder or a folder above the project. Way out: search inside the "
                     "project folder (for example `find . -name ...`, or Grep and Glob with a path inside the project); "
                     "if you really need another folder, report it in your hand-back so the user can allow it in "
                     "`search_roots_allowed` of project.json.",
        "deny_unreadable": "budgit guard (search root): {tool} was refused because its search root `{root}` cannot be read "
                           "safely (a variable, a substitution, a folder change it cannot follow, a wrapper such as xargs or "
                           "an unclosed quote, a heredoc, `$(...)`, a backtick or a comment). Rule: a command that mentions a search and "
                           "cannot be read safely is refused. Way out: write the root as a plain path inside the project folder, "
                           "split the command in plain steps, and for a commit use a plain quoted message: `git commit -m \"...\"`, "
                           "without a heredoc or `$(...)`.",
        "why_disk": "the whole disk",
        "why_home": "the home folder",
        "why_above": "a folder above the project or the home folder",
    },
    "es": {
        "deny_root": "guarda de budgit (raiz de busqueda): se rechazo {tool} porque la raiz de busqueda `{root}` es {why}. Regla: un "
                     "agente no busca en todo el disco, en la carpeta personal ni en una carpeta por encima del proyecto. "
                     "Salida: busca dentro de la carpeta del proyecto (por ejemplo `find . -name ...`, o Grep y Glob con una "
                     "ruta dentro del proyecto); si de verdad hace falta otra carpeta, cuentalo en tu informe final para que "
                     "el usuario la permita en `search_roots_allowed` de project.json.",
        "deny_unreadable": "guarda de budgit (raiz de busqueda): se rechazo {tool} porque su raiz de busqueda `{root}` no se "
                           "puede leer con seguridad (una variable, una sustitucion, un cambio de carpeta que no puede "
                           "seguir, un envoltorio como xargs, una comilla sin cerrar, un heredoc, `$(...)`, una comilla invertida o un "
                           "comentario). Regla: un comando que menciona una busqueda y no se puede leer con seguridad se "
                           "rechaza. Salida: escribe la raiz como una ruta normal dentro de la carpeta del proyecto, divide "
                           "el comando en pasos simples y, para un commit, usa un mensaje simple entre comillas: "
                           "`git commit -m \"...\"`, sin heredoc ni `$(...)`.",
        "why_disk": "todo el disco",
        "why_home": "la carpeta personal",
        "why_above": "una carpeta por encima del proyecto o de la carpeta personal",
    },
}

_GREPS = {"grep", "egrep", "fgrep", "rgrep", "zgrep", "zegrep", "zfgrep"}
_SEARCHERS = {"find", "rg", "fd", "fdfind", "ag", "ack", "locate", "mdfind", "ugrep", "ug", "ls", "du", "tree"} | _GREPS
_WRAPPERS = {"sudo", "doas", "env", "command", "time", "nice", "ionice", "nohup", "exec", "builtin", "xargs", "timeout",
             "stdbuf", "setsid", "caffeinate", "busybox", "toybox"}      # busybox and toybox run an applet: find, grep, sh ...
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "mksh"}
_SOURCE = {"source", "."}
_NAMES = sorted(_SEARCHERS | _SHELLS | _WRAPPERS | {"eval"})
_DYN = re.compile(r"[$`]")                                                # a variable, `$(` or a backtick
_HOMEPATH = re.compile(r"^\$(?:HOME|PWD|\{HOME\}|\{PWD\})/[^$`]*$")       # `$HOME/bin/x` resolves, it is a path
_GLOBCH = re.compile(r"[*?\[{]")
_ESC = re.compile(r"\\[xuU0-7]")                                           # a hex, octal or unicode escape can spell a command
_CD = {"cd", "pushd", "popd"}
_NEUTRAL = {"{", "}", "!", "if", "then", "else", "elif", "do", "while", "until", "coproc"}   # words that come before a command
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
_WORD = re.compile(r"(?:^|[^\w.-])(?:" + "|".join(sorted(_SEARCHERS, key=len, reverse=True)) + r")(?![\w-])")
_VAR = re.compile(r"\$(\w+|\{\w+\})")


def _spec(sv="", sb="", lv="", lb=""):
    return {"sv": set(sv), "sb": set(sb), "lv": set(lv.split()), "lb": set(lb.split())}


# One flag table per tool. A flag that is not in its table is "unsure": every operand then counts as a possible root.
_GREP = _spec("efABCmdD", "EFGPiyvwxcLloqsbHhnTZaIrRUuVz0123456789",
              "regexp file after-context before-context context max-count directories devices include exclude exclude-from "
              "exclude-dir label binary-files group-separator",
              "recursive dereference-recursive extended-regexp fixed-strings basic-regexp perl-regexp ignore-case "
              "no-ignore-case invert-match word-regexp line-regexp count files-without-match files-with-matches "
              "only-matching quiet silent no-messages byte-offset with-filename no-filename line-number initial-tab null "
              "text null-data line-buffered no-group-separator version help unix-byte-offsets color colour")
_RG = _spec("ABCefgjmMErtTd", "SxsiwvnNlcoquULHIzFPpabhV0.",
            "after-context before-context context regexp file glob iglob type type-not type-add type-clear max-depth maxdepth "
            "max-count max-filesize replace threads encoding color colors sort sortr ignore-file pre pre-glob path-separator "
            "context-separator field-context-separator field-match-separator engine dfa-size-limit regex-size-limit "
            "max-columns hostname-bin hyperlink-format",
            "files type-list hidden no-ignore no-ignore-vcs no-ignore-parent no-ignore-dot no-ignore-global no-ignore-exclude "
            "no-ignore-files no-ignore-messages unrestricted follow fixed-strings ignore-case case-sensitive smart-case "
            "word-regexp line-regexp invert-match line-number no-line-number only-matching quiet json heading no-heading "
            "with-filename no-filename column vimgrep null null-data text binary multiline multiline-dotall pcre2 no-pcre2 "
            "no-messages no-config stats trim version help pretty passthru files-with-matches files-without-matches count "
            "count-matches max-columns-preview debug trace sort-files one-file-system crlf mmap no-mmap block-buffered "
            "line-buffered include-zero search-zip auto-hybrid-regex byte-offset stop-on-nonmatch")
_AG = _spec("gGmpW", "acfFilLnNoQrsStuUvwz0HhD",
            "depth ignore ignore-dir path-to-ignore max-count workers pager width file-search-regex",
            "all-types case-sensitive count files-with-matches files-without-matches filename follow fixed-strings hidden "
            "ignore-case invert-match line-numbers literal list-file-types no-filename no-numbers nocolor nogroup numbers "
            "one-device only-matching parallel passthru print-long-lines search-binary search-files skip-vcs-ignores "
            "smart-case stats unrestricted vimgrep word-regexp null nobreak noheading column color group heading")
_FD = _spec("dEetScj", "HIisgFaLp0uqhVl1",
            "max-depth min-depth exact-depth exclude extension type size color threads search-path base-directory "
            "changed-within changed-before newer older owner max-results ignore-file format and",
            "hidden no-ignore no-ignore-vcs no-global-ignore-file no-ignore-parent glob regex fixed-strings ignore-case "
            "case-sensitive absolute-path follow full-path print0 unrestricted list-details quiet prune help version "
            "one-file-system no-require-git relative-path strip-cwd-prefix")
_UNKNOWN = _spec()


def lang_now():
    try:
        import i18n
        lang = i18n.detect()
        return lang if lang in TEXTS else "en"
    except Exception:
        return "en"


def text(name, lang=None, **fmt):
    lang = lang if lang in TEXTS else lang_now()
    out = TEXTS[lang].get(name) or TEXTS["en"][name]
    fmt.setdefault("tool", "this call")
    fmt.setdefault("root", "?")
    fmt.setdefault("why", TEXTS[lang]["why_above"])
    return out.format(**fmt)


class _Unreadable(Exception):
    pass


def safe_allowed(entries, home, project=None, worktrees=None):
    """The allowed-roots list as realpaths, minus anything that would defeat the guard: `/`, the home folder or a folder above
    it, and a folder above the project or above a worktree (their siblings would open up) are ignored; an entry equal to the
    project or a worktree, or inside one, stays; `~` is expanded; a relative entry is read against the project; a non-string
    is dropped."""
    out = []
    guarded = [os.path.realpath(g) for g in ([project] if project else []) + list(worktrees or []) if g]
    for p in entries or []:
        if not isinstance(p, str) or not p.strip():
            continue
        q = p.strip()
        if q == "~" or q.startswith("~/"):
            q = home + q[1:]
        elif q.startswith("~"):
            continue
        if not os.path.isabs(q):
            if not project:
                continue
            q = os.path.join(project, q)
        q = os.path.realpath(q)
        if q == "/" or _inside(home, q):                 # `/`, home itself, or any folder above home
            continue
        if any(g != q and _inside(g, q) for g in guarded):   # a proper ancestor of the project or of a worktree
            continue
        out.append(q)
    return out


def _braces(word, budget=None):
    """Brace forms `{/,.}` as the shell expands them; a number or letter range is unreadable; too many forms is unreadable."""
    budget = [64] if budget is None else budget
    a = word.find("{")
    if a < 0:
        return [word]
    depth, b, commas = 0, -1, []
    for k in range(a, len(word)):
        ch = word[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                b = k
                break
        elif ch == "," and depth == 1:
            commas.append(k)
    if b < 0:
        return [word]
    pre, post, inner = word[:a], word[b + 1:], word[a + 1:b]
    if not commas:
        if ".." in inner:
            raise _Unreadable(word)
        return [pre + "{" + inner + "}" + rest for rest in _braces(post, budget)]
    cuts = [a] + commas + [b]
    out = []
    for x, y in zip(cuts, cuts[1:]):
        budget[0] -= 1
        if budget[0] < 0:
            raise _Unreadable(word)
        out.extend(_braces(pre + word[x + 1:y] + post, budget))
    return out


def _expand(word, home, cwd):
    """The folder a root word names, as a realpath. A variable other than HOME, a substitution or an unknown ~user cannot be
    read: _Unreadable."""
    if "`" in word or "$(" in word:
        raise _Unreadable(word)
    if word == "~" or word.startswith("~/"):
        word = home + word[1:]
    elif word.startswith("~"):
        raise _Unreadable(word)

    def sub(m):
        name = m.group(1).strip("{}")
        if name == "HOME":
            return home
        if name == "PWD":
            return cwd
        raise _Unreadable(word)
    word = _VAR.sub(sub, word)
    m = re.search(r"[*?\[{]", word)                     # a glob root is judged by its fixed folder prefix
    if m:
        word = word[:m.start()]
        word = word[:word.rfind("/") + 1] if "/" in word else ""
    if not word:
        word = "."
    if not os.path.isabs(word):
        word = os.path.join(cwd, word)
    return os.path.realpath(word)


def _expand_all(word, home, cwd):
    return [_expand(w, home, cwd) for w in _braces(word)]


def _inside(path, folder):
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _why(root, home, above):
    """None when the root is fine, else the key of the reason."""
    if root == "/":
        return "why_disk"
    if root == home:
        return "why_home"
    if _inside(home, root) or any(a != root and _inside(a, root) for a in above):   # a proper ancestor of home or of the project/worktree
        return "why_above"
    return None


# ---------------------------------------------------------------- reading a command

_DOLLAR = re.compile(r"\$(?:[A-Za-z_]\w*|\{[A-Za-z_]\w*\})")


def _target_ok(s, i):
    """A redirection target must be a plain word: not a variable, a substitution, a second redirection or a separator."""
    n = len(s)
    while i < n and s[i] in " \t":
        i += 1
    return i < n and s[i] not in "$`()<>&|;\n\r"


def _readable(s):
    """ALLOW LIST (round 3b). A command with a searcher word is read only when it is made of: plain words (a backslash may
    escape one character that is not a newline, `$NAME` and `${NAME}` may stand in a word); '...' not right after a `$`;
    "..." holding no `$`, backtick or backslash; the separators `;` `&&` `||` `|` and a newline; and the plain redirections
    `>` `>>` `N>&M` `&>` `<`. Everything else (`$'..'`, `$".."`, `$(..)`, a backtick, `(` `)`, `<<` of any kind, `<(..)`, `>(..)`,
    a backslash-newline, a comment, `;;`, a lone `&`) is unreadable, so a quoting trick the reader does not know can only
    be refused, never slip through."""
    n, i = len(s), 0
    while i < n:
        c = s[i]
        if c == "'":
            j = s.find("'", i + 1)
            if j < 0 or s[i - 1:i] == "$":
                return False
            i = j + 1
        elif c == '"':
            j = i + 1
            if s[i - 1:i] == "$":
                return False
            while j < n and s[j] != '"':
                if s[j] in "$`\\":
                    return False
                j += 1
            if j >= n:
                return False
            i = j + 1
        elif c == "\\":
            if i + 1 >= n or s[i + 1] == "\n":
                return False
            i += 2
        elif c == "$":
            m = _DOLLAR.match(s, i)
            if not m:
                return False
            i = m.end()
        elif c == ";":
            if s.startswith(";;", i):
                return False
            i += 1
        elif c == "&":
            if s.startswith("&&", i):
                i += 2
            elif s.startswith("&>", i):
                i += 3 if s.startswith("&>>", i) else 2
                if not _target_ok(s, i):
                    return False
            else:
                return False
        elif c == "|":
            if s.startswith("|&", i):
                return False
            i += 2 if s.startswith("||", i) else 1
        elif c == ">":
            if s.startswith((">(", ">|"), i):
                return False
            i += 2 if s.startswith(">>", i) else 1
            m = re.match(r"&(?:\d+|-)(?![\w$])", s[i:])  # 2>&1
            if m:
                i += m.end()
            elif not _target_ok(s, i):
                return False
        elif c == "<":
            if s.startswith(("<<", "<(", "<&", "<>"), i):
                return False
            i += 1
            if not _target_ok(s, i):
                return False
        elif c in "`()":
            return False
        elif c == "#":
            if i == 0 or s[i - 1] in " \t\r\n;&|<>":
                return False                             # a comment
            i += 1
        else:
            i += 1
    return True


def _tokenize(s):
    """The command as a flat list of segments (each a list of words), in order. Quotes and backslashes are honoured (a quoted
    `(` is a word, not an operator), heredoc bodies are skipped (an unquoted one is read for substitutions), and `$(...)` and
    backticks are read as commands of their own; the word that held one keeps a `$(` or a backtick as its mark."""
    segs, n = [], len(s)

    def cmd(i, close, depth):
        if depth > 12:
            raise _Unreadable(s[:60])
        words, word, have, paren, pending = [], [], False, 0, []

        def endword():
            nonlocal word, have
            if have:
                words.append("".join(word))
            word, have = [], False

        def endseg():
            nonlocal words
            endword()
            if words:
                segs.append(words)
            words = []

        while i < n:
            c = s[i]
            if close == "`" and c == "`":
                endseg()
                return i + 1
            if close == ")" and c == ")" and paren == 0:
                endseg()
                return i + 1
            if c == "\\":
                if i + 1 < n:
                    if s[i + 1] != "\n":
                        word.append(s[i + 1])
                        have = True
                    i += 2
                else:
                    i += 1
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise _Unreadable(s[i:i + 60])
                word.append(s[i + 1:j])
                have = True
                i = j + 1
                continue
            if c == '"':
                i += 1
                have = True
                while True:
                    if i >= n:
                        raise _Unreadable(s[-60:])
                    d = s[i]
                    if d == '"':
                        i += 1
                        break
                    if d == "\\" and i + 1 < n:
                        e = s[i + 1]
                        word.append(e if e in '"\\$`' else d + e)
                        i += 2
                    elif d == "$" and s.startswith("$(", i):
                        word.append("$(")
                        i = cmd(i + 2, ")", depth + 1)
                    elif d == "`":
                        word.append("`")
                        i = cmd(i + 1, "`", depth + 1)
                    else:
                        word.append(d)
                        i += 1
                continue
            if c == "$" and s.startswith("$(", i):
                word.append("$(")
                have = True
                i = cmd(i + 2, ")", depth + 1)
                continue
            if c == "`":
                word.append("`")
                have = True
                i = cmd(i + 1, "`", depth + 1)
                continue
            if c == "#" and not have:
                j = s.find("\n", i)
                i = n if j < 0 else j
                continue
            if c in " \t\r":
                endword()
                i += 1
                continue
            if c == "\n":
                endseg()
                i += 1
                while pending:
                    delim, quoted, dash = pending.pop(0)
                    body = []
                    while i < n:
                        j = s.find("\n", i)
                        line = s[i:] if j < 0 else s[i:j]
                        i = n if j < 0 else j + 1
                        if (line.lstrip("\t") if dash else line) == delim:
                            break
                        body.append(line)
                    if body and not quoted:
                        segs.extend(_tokenize('"' + "\n".join(body).replace('"', '\\"') + '"'))
                continue
            if c in ";&|":
                endseg()
                i += 1
                continue
            if c == "(":
                endseg()
                paren += 1
                i += 1
                continue
            if c == ")":
                endseg()
                paren = max(0, paren - 1)
                i += 1
                continue
            if c == "<" and s.startswith("<<", i) and not s.startswith("<<<", i):
                k = i + 2
                dash = k < n and s[k] == "-"
                k += 1 if dash else 0
                while k < n and s[k] in " \t":
                    k += 1
                d, quoted = [], False
                while k < n and s[k] not in " \t\r\n;&|()<>":
                    if s[k] in "'\"\\":
                        quoted = True
                    else:
                        d.append(s[k])
                    k += 1
                pending.append(("".join(d), quoted, dash))
                endword()
                words.append("<<")                       # a mark: this command reads its input from a here-document
                i = k
                continue
            word.append(c)
            have = True
            i += 1
        if close:
            raise _Unreadable(s[-60:])
        endseg()
        return i

    cmd(0, None, 0)
    return segs


def _strip_redirs(words):
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
            continue
        if re.match(r"^\d*(>>?|<)$", w):
            skip = True
        elif re.match(r"^\d*(>>?|<)", w):
            continue
        else:
            out.append(w)
    return out


def _scan(args, spec):
    """Operands and flags of a command line by its flag table -> (operands, flag names, {flag: [values]}, unsure)."""
    ops, flags, vals, unsure, i = [], set(), {}, False, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            ops.extend(args[i:])
            break
        if a.startswith("--"):
            name, eq, val = a[2:].partition("=")
            if name in spec["lv"]:
                if not eq:
                    if i >= len(args):
                        unsure = True
                        break
                    val = args[i]
                    i += 1
                vals.setdefault(name, []).append(val)
            elif name in spec["lb"]:
                flags.add(name)
            else:
                unsure = True
            continue
        if a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                c = a[j]
                if c in spec["sv"]:
                    val = a[j + 1:]
                    if not val:
                        if i >= len(args):
                            unsure = True
                            break
                        val = args[i]
                        i += 1
                    vals.setdefault(c, []).append(val)
                    break
                if c in spec["sb"]:
                    flags.add(c)
                    j += 1
                else:
                    unsure = True
                    break
            continue
        ops.append(a)
    return ops, flags, vals, unsure


def _grep_roots(args, always_recursive=False):
    """-> (roots, recursive). An empty roots list means: the working folder."""
    ops, flags, vals, unsure = _scan(args, _GREP)
    rec = always_recursive or unsure or bool(flags & {"r", "R", "recursive", "dereference-recursive"}) or \
        any(v == "recurse" for k in ("d", "directories") for v in vals.get(k, []))
    given = any(k in vals for k in ("e", "f", "regexp", "file"))
    return (ops if (unsure or given) else ops[1:]), rec


def _find_roots(args):
    out, i, n = [], 0, len(args)
    while i < n:
        a = args[i]
        if a in ("-H", "-L", "-P", "-E", "-X", "-x", "-s", "-d", "-D") or re.fullmatch(r"-O\d?", a):
            i += 1                                       # -D's value, if any, is read as one more (harmless) root
        elif a == "-f":
            if i + 1 < n:
                out.append(args[i + 1])
            i += 2
        elif a in ("(", ")", "!", "--"):
            i += 1                                       # an operand after these still counts as a root
        elif a.startswith("-") and a != "-":
            break
        else:
            out.append(a)
            i += 1
    return out


def _roots_of(head, args):
    """The search roots a segment names (an empty list means: the working folder); None when the segment does not search."""
    if head == "find":
        return _find_roots(args)
    if head in _GREPS:
        roots, rec = _grep_roots(args, head == "rgrep")
        return roots if rec else None
    if head == "locate":
        return ["/"]
    if head == "mdfind":
        for i, a in enumerate(args):
            if a == "-onlyin" and i + 1 < len(args):
                return [args[i + 1]]
        return ["/"]
    if head == "rg":
        ops, flags, vals, unsure = _scan(args, _RG)
        files = bool(flags & {"files", "type-list"})
        given = any(k in vals for k in ("e", "f", "regexp", "file"))
        return ops if (unsure or files or given) else ops[1:]
    if head == "ag":
        ops, flags, vals, unsure = _scan(args, _AG)
        return ops if (unsure or "g" in vals) else ops[1:]
    if head in ("fd", "fdfind"):
        ops, flags, vals, unsure = _scan(args, _FD)
        return (ops if unsure else ops[1:]) + vals.get("search-path", []) + vals.get("base-directory", [])
    if head == "ls":
        rec = any(a == "--recursive" or (a.startswith("-") and not a.startswith("--") and "R" in a) for a in args
                  if a != "--")
        return _scan(args, _UNKNOWN)[0] if rec else None
    return _scan(args, _UNKNOWN)[0]                      # ack, ugrep, ug, du, tree: every operand is a possible root


def _is_command(word):
    b = os.path.basename(word)
    return b in _SEARCHERS or b in _SHELLS or b in _WRAPPERS or b in _CD or b in ("eval", "git")


def _cd_to(target, st, cwds):
    out = []
    for c in cwds:
        try:
            for real in _expand_all(target, st["home"], c):
                if real not in out:
                    out.append(real)
        except _Unreadable:
            st["bad"] = target
    return out


def _do_cd(head, args, st):
    if head == "popd":
        return                                           # every folder it can return to is already in the list
    ops, dd = [], False
    for a in args:
        if not dd and a == "--":
            dd = True
        elif not dd and re.fullmatch(r"-[LPe@n]+", a):
            continue
        elif not dd and (a == "-" or a.startswith("+") or a.startswith("-")):
            st["bad"] = a
            return
        else:
            ops.append(a)
    for real in _cd_to(ops[0] if ops else "~", st, st["cwds"]):
        if real not in st["cwds"]:
            st["cwds"].append(real)


def _xargs_safe(inner, args):
    """xargs may feed only a plain (non-recursive) grep: the files come from the pipe and a grep without -r reads files only."""
    return inner in _GREPS and inner != "rgrep" and not _grep_roots(args)[1]


def enabled():
    """Whether the search guard runs: the user's `search_guard` ("on" or "off") in Budgit's home config.json, else
    SEARCH_GUARD_DEFAULT. Never project.json."""
    try:
        import studiolib as sl
        value = sl._home_config().get("search_guard")
    except Exception:
        value = None
    if value in ("off", False):
        return False
    if value in ("on", True):
        return True
    return bool(SEARCH_GUARD_DEFAULT)


def _gate(command):
    """True when the command has a searcher word once its quotes and backslashes are removed (`fi""nd`, `f\\ind`, `'fi'nd`), or
    a hex, unicode or octal escape that decodes to one (`$'\\x66ind'`, `printf '\\146ind'`). Only such a command is refused for
    what it hides; the others are never touched."""
    decoded = re.sub(r"\\x([0-9a-fA-F]{1,2})|\\u([0-9a-fA-F]{4})|\\([0-7]{1,3})",
                     lambda m: chr(int(m.group(1) or m.group(2) or m.group(3), 16 if (m.group(1) or m.group(2)) else 8) % 0x110000),
                     command)
    return bool(_WORD.search(re.sub(r"[\\'\"]", "", decoded)))


def _glob_hits(word):
    """True when a command word with glob or brace characters (`fin?`, `[f]ind`, `{f,}ind`) could be a searcher, a shell or a
    wrapper by name."""
    base = os.path.basename(word)
    if word == "*" or not _GLOBCH.search(base):          # a bare `*` is a case-statement default arm, never a command
        return False
    try:
        forms = _braces(base)
    except _Unreadable:
        return True
    return any(fnmatch.fnmatchcase(n, f) for f in forms for n in _NAMES)


def _candidate(rest):
    """The word a wrapper is most likely to run: the first one that is not an option, an option's value, an assignment or a
    duration."""
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--" or _ASSIGN.match(a) or re.fullmatch(r"\d+(\.\d+)?[smhd]?", a):
            i += 1
        elif a.startswith("-"):
            i += 2 if re.fullmatch(r"-[ugnckCDUiotepshr]", a) else 1
        else:
            return a
    return None


def _walk(words, st, found, depth):
    cwds = st["cwds"]
    gate = st["gate"]
    redir_in = any(re.match(r"^\d*<", w) for w in words)          # `< file`, `<<EOF`, `<<<` or `<(...)` feeds this command
    words = _strip_redirs(words)
    while words:
        w = words[0]
        head = os.path.basename(w)
        if _ASSIGN.match(w) or head in _NEUTRAL:
            words = words[1:]
            continue
        if _glob_hits(w):
            raise _Unreadable(w)                         # fin?, [f]ind: a name that may be a searcher, whatever else is said
        if gate and _DYN.search(w) and not _HOMEPATH.match(w) and not re.search(r"\s", w):
            raise _Unreadable(w)                         # $x, ${IFS}, $'..', $(..), `..` where the command goes
        if head in _WRAPPERS:
            rest = words[1:]
            for k, a in enumerate(rest):                 # env -C DIR runs the command in DIR
                if head == "env" and a in ("-C", "--chdir") and k + 1 < len(rest):
                    cwds = _cd_to(rest[k + 1], st, cwds)
                elif head == "env" and a.startswith("--chdir="):
                    cwds = _cd_to(a.split("=", 1)[1], st, cwds)
            j = next((k for k, a in enumerate(rest) if _is_command(a)), None)
            if gate and head == "env" and any(re.match(r"^-[A-Za-z]*S|^--split-string", a)
                                              for a in (rest if j is None else rest[:j])):
                raise _Unreadable("env -S")              # a string that is split into a command
            if j is None:
                cand = _candidate(rest)
                if cand is not None and (_glob_hits(cand) or (gate and _DYN.search(cand) and not _HOMEPATH.match(cand))):
                    raise _Unreadable(cand[:60])         # a wrapper whose command is unknown and cannot be read
                return
            if head == "xargs":
                inner = os.path.basename(rest[j])
                if gate and (inner in _SEARCHERS and not _xargs_safe(inner, rest[j + 1:]) or inner in _SHELLS
                             or inner in _WRAPPERS or inner == "eval"):
                    raise _Unreadable("xargs " + inner)
            words = rest[j:]
            continue
        args = words[1:]
        if head in _CD:
            _do_cd(head, args, st)
            return
        if head in _SHELLS or head == "eval":
            if head == "eval":
                inner = " ".join(args)
            else:
                k = next((k for k, a in enumerate(args) if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a)), None)
                inner = args[k + 1] if k is not None and k + 1 < len(args) else None
            if inner is not None:
                _run(inner, st, found, depth + 1)
            elif gate and head != "eval" and (redir_in or not [a for a in args if not a.startswith("-")]
                                              or any(re.fullmatch(r"-[A-Za-z]*s[A-Za-z]*", a) for a in args)):
                raise _Unreadable(head + " reads its commands from stdin")   # echo 'find /' | sh, sh <<< "..", sh -s
            return
        if head in _SOURCE and gate and (redir_in or any(a.startswith(("/dev/", "/proc/")) for a in args)):
            raise _Unreadable(head + " reads commands from a stream")        # source <(..), . /dev/stdin
        if head == "git":
            k, local = 0, cwds
            while k < len(args) and args[k].startswith("-"):
                if args[k] == "-C" and k + 1 < len(args):
                    local = _cd_to(args[k + 1], st, local)
                    k += 2
                elif args[k] in ("-c", "--git-dir", "--work-tree", "--namespace") and k + 1 < len(args):
                    k += 2
                else:
                    k += 1
            if k < len(args) and args[k] == "grep" and "--no-index" in args[k + 1:]:
                roots, cwds = _scan(args[k + 1:], _UNKNOWN)[0], local
                head = "git grep"
            else:
                return
        elif head not in _SEARCHERS:
            return
        else:
            roots = _roots_of(head, args)
            if roots is None:
                return
        if st["bad"]:
            raise _Unreadable("cd " + st["bad"])
        for c in cwds:
            found.extend((head, r, c) for r in (roots or ["."]))
        return


def _run(command, st, found, depth):
    if depth > 4:
        if st["gate"]:
            raise _Unreadable(command[:60])
        return
    if st["gate"] and not _readable(command):
        raise _Unreadable(command[:60])
    try:
        segs = _tokenize(command)
    except _Unreadable:
        if st["gate"]:
            raise
        return
    for seg in segs:
        _walk(seg, st, found, depth)


def _bash_roots(command, home, cwd, gate=None):
    """-> list of (head, root word, folder it is read against) for every searching segment; raises _Unreadable."""
    found = []
    gate = _gate(command) if gate is None else gate
    _run(command, {"home": home, "cwds": [cwd], "bad": None, "gate": gate}, found, 0)
    return found


def decision(tool, tool_input, project, cwd, allowed=None, env=None, worktrees=None):
    """-> ("deny", text) or None."""
    tool_input = tool_input or {}
    env = os.environ if env is None else env
    try:
        home = os.path.realpath(env.get("HOME") or os.path.expanduser("~"))
    except Exception:
        home = "/"
    if not enabled():
        return None
    cwd = os.path.realpath(cwd or project or os.getcwd())
    proj = os.path.realpath(project) if project else None
    above = ([proj] if proj else []) + ([] if proj and _inside(cwd, proj) else [cwd]) + \
        [os.path.realpath(p) for p in (worktrees or [])]    # the working folder only when it is not inside the project (`..` from src/ is the project)
    ok = safe_allowed(allowed, home, proj, above)
    if tool == "Bash":
        command = tool_input.get("command") or ""
        gate = _gate(command)
        try:
            roots = [(h, real, r) for h, r, c in _bash_roots(command, home, cwd, gate) for real in _expand_all(r, home, c)]
        except _Unreadable as e:
            return ("deny", text("deny_unreadable", tool="Bash", root=str(e)[:80]))
        except Exception:
            if not gate:
                return None
            return ("deny", text("deny_unreadable", tool="Bash", root=command[:60]))
        label = "Bash"
    elif tool in ("Grep", "Glob"):
        try:
            word = tool_input.get("path") or ""
            pat = tool_input.get("pattern") or "" if tool == "Glob" else ""
            if tool == "Glob" and (os.path.isabs(pat) or pat.startswith("~") or pat.startswith("..") or "/../" in pat):
                word = os.path.join(word, pat) if word and not os.path.isabs(pat) and not pat.startswith("~") else pat
            roots = [(tool, real, word or ".") for real in _expand_all(word or ".", home, cwd)]
        except Exception:
            return ("deny", text("deny_unreadable", tool=tool, root=str(tool_input.get("path") or tool_input.get("pattern"))[:80]))
        label = tool
    else:
        return None
    for _, real, shown in roots:
        if any(_inside(real, a) for a in ok):
            continue
        why = _why(real, home, above)
        if why:
            return ("deny", text("deny_root", tool=label, root=real if real != shown else shown,
                                 why=text(why)))
    return None
