"""R-52 leak gate: nothing that identifies the owner leaves in a shared copy.

The words to look for are never in this file (it ships too): they come from the owner's config.json (accounts,
noreply emails and their numeric id, hub, projects_root, `private_terms`), the folder names under projects_root, the
hub's projects/ideas/lessons-inbox names, each project's GitHub repo name (a folder that shares the hub's git storage is
the hub, not another project), and the folder names of every `protected_dirs` entry and every project marked `"private": true`,
wherever they live. Fixed patterns: home paths with a real
user name (lint.PERSONAL) and, in Markdown, «quoted words» of 3+ words (the user's quotes). Whole words, case-insensitive, ≥4 chars.
Deterministic, read-only, stdlib only.
"""
import os
import unicodedata
import re
import subprocess
from collections import namedtuple

import lint
import studiolib as sl

MIN_LEN = 4
GENERIC = {"index", "readme", "changelog", "license", "template", "inbox", "processed", "archive", "done",   # names every hub has
           "general"}      # R-578: a common word in code; a project folder of that name says nothing private
QUOTE = re.compile(r"«(?:[^»\s]+[ \t]+){2,}[^»\s]+»")      # the user's quotes: 3+ words («MVP n/m» is a label)
Hit = namedtuple("Hit", "path line kind term")
PROTECTED = "protected project"      # a `protected_dirs` folder name: found in text files only, never printed (R-428)


def _stem(name):
    return os.path.splitext(name)[0]


def _origin_repo(path):
    try:
        r = subprocess.run(sl.safe_git_argv(path, "remote", "get-url", "origin"), capture_output=True, text=True,
                           timeout=5, env=sl.safe_git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]+[^/]+/([^/\s]+?)(?:\.git)?/?$", r.stdout.strip()) if r.returncode == 0 else None
    return m.group(1) if m else None


def _common_dir(path):
    """The repo's shared git dir (one for a repo and all its worktrees), or None."""
    try:
        r = subprocess.run(sl.safe_git_argv(path, "rev-parse", "--path-format=absolute", "--git-common-dir"),
                           capture_output=True, text=True, timeout=5, env=sl.safe_git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _private_projects(skip):
    """Projects Budgit has worked in (session pointers) whose project.json says private, outside `skip` folders."""
    import livework
    seen = set()
    for path in livework._pointer_projects():
        real = os.path.realpath(path)
        if real in seen or any(livework._under(real.casefold(), d) for d in skip):
            continue
        seen.add(real)
        try:
            if livework.has_ledger(real) and sl.load_project_config(real).get("private") is True:
                yield real
        except Exception:
            continue


def _generic_dirs():
    """Folder names too common to say "this text is about a protected project" (`~/Dev` as a protected_dirs entry)."""
    import livework
    return livework.GENERIC_DIRS


def protected_names(config=None):
    """Lowercase folder names of the owner's `protected_dirs`: the project names that must not appear in code or skills.
    Every entry counts, whatever other kind its name has in terms(); generic folder names (`~/Dev`) and names under
    MIN_LEN are left out. Raises ValueError when `protected_dirs` is not a list of readable entries: the callers (sweep,
    sweep_messages, export) must stop, never read an unreadable list as "no protected project"."""
    config = config if config is not None else sl.load_config()
    if not sl.protected_dirs_readable(config):
        raise ValueError("protected_dirs is not a readable list")
    generic = _generic_dirs()
    out = set()
    for raw in config.get("protected_dirs") or ():
        if isinstance(raw, str) and raw.strip():
            name = os.path.basename(os.path.expanduser(raw).rstrip(os.sep)).strip().lower()
            if len(name) >= MIN_LEN and name not in GENERIC and name not in generic:
                out.add(name)
    return out


def terms(config=None):
    """{lowercase term: kind} built from the owner's config and folders."""
    config = config if config is not None else sl.load_config()
    out = {}

    def add(kind, term):
        term = unicodedata.normalize("NFC", (term or "").strip())
        if len(term) >= MIN_LEN and term.lower() not in GENERIC:
            out.setdefault(term.lower(), kind)

    for account, email in (config.get("github_noreply") or {}).items():
        add("account", account)
        add("email", email)
        m = re.match(r"(\d+)\+", email or "")
        if m:
            add("github id", m.group(1))
    for term in config.get("private_terms") or ():
        add("private term", term)
    for name in sorted(protected_names(config)):      # a name already listed as a private term keeps that kind
        add(PROTECTED, name)
    hub = config.get("hub")
    for key in ("hub", "projects_root"):
        if config.get(key):
            add(key, config[key])
            add(key, os.path.expanduser(config[key]))
    hub_real = os.path.realpath(os.path.expanduser(hub)) if hub else None
    hub_git = _common_dir(hub_real) if hub_real and os.path.isdir(hub_real) else None
    root = config.get("projects_root")
    if root and os.path.isdir(os.path.expanduser(root)):
        base = os.path.expanduser(root)
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if not os.path.isdir(path) or os.path.realpath(path) == hub_real or name.startswith("."):
                continue
            if hub_git and _common_dir(path) == hub_git:      # shares the hub's git storage: it is the hub
                continue
            add("project folder", name)
            add("repo name", _origin_repo(path))
    import livework
    protected = []
    for d in config.get("protected_dirs") or ():
        written = os.path.expanduser(d).rstrip(os.sep)
        real = os.path.realpath(written)
        protected.append(real.casefold())
        for name in (os.path.basename(written), os.path.basename(real)):     # the name in config and, if a link, its target
            if name.casefold() not in livework.GENERIC_DIRS:
                add("private folder", name)
    for path in _private_projects(protected):
        add("private project", os.path.basename(path))
        add("repo name", _origin_repo(path))
    if hub_real:
        for sub in ("projects", "ideas", os.path.join("lessons", "inbox")):
            d = os.path.join(hub_real, sub)
            for name in (sorted(os.listdir(d)) if os.path.isdir(d) else ()):
                stem = _stem(name)
                add("hub " + sub.split(os.sep)[0], re.sub(r"^(?:\d{4}-\d{2}-\d{2}-|IDEA-\d+-)", "", stem))
    return out


def _pattern(found):
    words = sorted(found, key=len, reverse=True)
    if not words:
        return None
    return re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % "|".join(re.escape(w) for w in words), re.I)


def scan(root, found=None, allow=()):
    """Every hit under root (file or folder) -> [Hit]. allow: exact 'relpath:term' entries the user approved."""
    found = terms() if found is None else found
    pat = _pattern(found)
    allowed = {a.lower() for a in allow}
    hits = []
    files = [root] if os.path.isfile(root) else [
        os.path.join(d, f) for d, dirs, names in os.walk(root)
        for f in names if not dirs.__setitem__(slice(None), sorted(x for x in dirs if x not in lint.SKIP_DIRS))]
    base = root if os.path.isdir(root) else os.path.dirname(root)
    for path in sorted(files):
        text, _ = lint._text(path)
        if text is None:
            continue
        rel = os.path.relpath(path, base)
        md = path.endswith(".md")
        for n, line in enumerate(unicodedata.normalize("NFC", text).splitlines(), 1):    # a name typed with a combining accent is the same name
            got = []
            if pat:
                got += [(found[m.group(0).lower()], m.group(0)) for m in pat.finditer(line)]
            got += [("home path", m.group(0)) for m in lint.PERSONAL.finditer(line)
                    if m.group(1).lower() not in lint.PLACEHOLDERS]
            if md:
                got += [("quoted words", m.group(0)) for m in QUOTE.finditer(line)]
            for kind, term in got:
                if ("%s:%s" % (rel, term)).lower() not in allowed:
                    hits.append(Hit(rel, n, kind, term))
    return hits


_PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")
MAX_BINARY = 64 * 1024 * 1024


def scan_binary_strings(root, found=None, allow=()):
    """The text scanners skip any file with a NUL byte, so a compiled binary is read here as `strings -a` would: every run of 4+
    printable bytes is checked for the owner's words and home paths. Hit.line is the byte offset of the run. [Hit]"""
    found = terms() if found is None else found
    found = {k: v for k, v in found.items() if v != PROTECTED}      # a compiled symbol can hold a folder name by chance
    pat = _pattern(found)
    allowed = {a.lower() for a in allow}
    hits = []
    for d, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(d, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, root)
            if os.path.getsize(path) > MAX_BINARY:
                hits.append(Hit(rel, 0, "too large to read", str(os.path.getsize(path))))
                continue
            with open(path, "rb") as f:
                data = f.read()
            for m in _PRINTABLE.finditer(data):
                text = m.group(0).decode("ascii")
                got = [(found[w.group(0).lower()], w.group(0)) for w in pat.finditer(text)] if pat else []
                got += [("home path", w.group(0)) for w in lint.PERSONAL.finditer(text) if w.group(1).lower() not in lint.PLACEHOLDERS]
                for kind, term in got:
                    if ("%s:%s" % (rel, term)).lower() not in allowed:
                        hits.append(Hit(rel, m.start(), kind, term))
    return hits


SWEEP_DIRS = ("skills", "plugin", "tests", "tools")     # R-428: the gate covers code and shipped skills; notes and history are out


def _name_regex(names):
    """Whole word, any case; a name with a separator inside (`a-b`) also matches `ab`, `a_b`, `a.b` and `a b`."""
    spell = lambda n: r"[\W_]*".join(re.escape(x) for x in re.split(r"[^A-Za-z0-9]+", n) if x)
    return re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(spell(n) for n in sorted(names, key=len, reverse=True)), re.I)


MESSAGE_FALLBACK = 200                                   # commits checked when there is no release commit or tag to start from
_RELEASE_SUBJECT = re.compile(r"Release ([0-9]+\.[0-9]+\.[0-9]+)")        # no \d: git -E does not know it


def _version_tuple(text):
    m = re.fullmatch(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)", (text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _current_version(root):
    """The version of the plugin in `root` (plugin/.claude-plugin/plugin.json or .claude-plugin/plugin.json), or None."""
    import json
    for rel in ("plugin/.claude-plugin/plugin.json", ".claude-plugin/plugin.json"):
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                v = _version_tuple(json.load(f).get("version"))
        except (OSError, ValueError, AttributeError):
            continue
        if v:
            return v
    return None


def sweep_messages(root, config=None, fallback=MESSAGE_FALLBACK):
    """R-428: the commit messages (subject and body) since the last release of the git work tree `root` that carry a protected
    project's name, same names and same matching as sweep(). The last release is the nearer of the last `Release x.y.z` commit
    and the last tag; when `root` has a plugin version, only releases BELOW it count, because the release commit of the version
    being cut sits at the end of its own release. With no release, the last `fallback` commits are checked and `since` is None.
    -> {"hits": [short hash], "scanned": n, "since": "release" | "tag" | None}. A name is never in the result.
    Nothing is scanned when the config lists no protected folder, `root` is not a work tree or has no commit; git missing or
    hung raises, as in sweep(), so the caller never reads it as clean."""
    out = {"hits": [], "scanned": 0, "since": None}
    names = protected_names(config)
    if not names:
        return out
    import subprocess

    def git(*args):
        r = subprocess.run(sl.safe_git_argv(root, *args), capture_output=True, timeout=30, env=sl.safe_git_env())
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None

    if git("rev-parse", "--verify", "-q", "HEAD") is None:
        return out
    bases = {}
    current = _current_version(root)
    top = None
    for line in (git("log", "--format=%H%x1f%s", "-E", "--grep", _RELEASE_SUBJECT.pattern) or "").splitlines():
        sha, _, subject = line.partition("\x1f")
        m = _RELEASE_SUBJECT.match(subject)
        v = _version_tuple(m.group(1)) if m else None
        if v is None or (current and v >= current):
            continue
        if top is None or v > top:                       # the highest version below the current one; its newest commit
            top, bases["release"] = v, sha
        if not current:
            break                                        # no version to compare with: the nearest release commit
    if current:
        tags = [(v, t) for t in (git("tag", "--merged", "HEAD") or "").split() for v in [_version_tuple(t)] if v and v < current]
        tag = max(tags)[1] if tags else ""
    else:
        tag = (git("describe", "--tags", "--abbrev=0", "HEAD") or "").strip()
    if tag:
        sha = (git("rev-parse", tag + "^{commit}") or "").strip()
        if sha:
            bases["tag"] = sha
    best = None
    for kind, sha in bases.items():
        count = (git("rev-list", "--count", sha + "..HEAD") or "").strip()
        if count.isdigit() and (best is None or int(count) < best[0]):
            best = (int(count), kind, sha)
    log = ["log", "--format=%h%x1f%B%x1e", "--abbrev=8"] + ([best[2] + "..HEAD"] if best else ["-n", str(fallback), "HEAD"])
    raw = git(*log) or ""
    out["since"] = best[1] if best else None
    pat = _name_regex(names)
    for entry in raw.split("\x1e"):
        if "\x1f" not in entry:
            continue
        sha, msg = entry.lstrip("\n").split("\x1f", 1)
        out["scanned"] += 1
        if pat.search(unicodedata.normalize("NFC", msg)):
            out["hits"].append(sha)
    return out


def sweep(root, config=None):
    """R-428: tracked TEXT files under SWEEP_DIRS of the git work tree `root`, and the names of every tracked path there, that
    carry a protected project's name (whole word, any case; `-`, `_` and `.` split words, so `c-name` and `name_two` count) -> [Hit]. The name is never in a Hit: path and term say <hidden>.
    [] when the config lists no protected folder or `root` is not a git work tree."""
    names = protected_names(config)
    if not names:
        return []
    import subprocess
    alnum = _name_regex(names)
    try:
        raw = subprocess.run(sl.safe_git_argv(root, "ls-files", "-z"), capture_output=True, timeout=30,
                             env=sl.safe_git_env()).stdout
    except (OSError, subprocess.SubprocessError):
        raise                                             # git missing or hung: the caller must not read that as "clean"
    hits = []
    for rel in raw.decode("utf-8", "surrogateescape").split("\0"):
        if not rel or rel.split("/", 1)[0] not in SWEEP_DIRS:
            continue
        shown = alnum.sub("<hidden>", rel)
        if shown != rel:                                  # a file name: `7-name.md` counts
            hits.append(Hit(shown, 0, PROTECTED, "<hidden>"))
        path = os.path.join(root, rel)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = data.decode("utf-16", "replace")           # a text file saved as UTF-16 is text
        elif b"\0" in data:
            continue                                      # a compiled binary is out of scope (a symbol can hold the letters by chance)
        else:
            text = data.decode("utf-8", "replace")
        for n, line in enumerate(text.splitlines(), 1):
            for _m in alnum.finditer(line):
                hits.append(Hit(shown, n, PROTECTED, "<hidden>"))
    return hits


# ---------------------------------------------------------------- R-415: hiding a protected name in a free-text fragment
HIDDEN = "<hidden>"
_hide_cache = {}


# blank-looking letters that are not format characters (Hangul fillers, braille blank): ignored like a zero-width space
_BLANKS = frozenset("\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0")


def _ignorable(c):
    """A character that shows nothing of its own: a combining mark, a variation selector, a format character, a blank filler."""
    return unicodedata.combining(c) or unicodedata.category(c) in ("Mn", "Me", "Cf") or c in _BLANKS


def _fold_text(text):
    """-> (folded, where). NFKD, case-folded, accents and invisible characters (zero-width, soft hyphen, variation selectors,
    fillers) dropped, so `Caf\u00e9`, `cafe\u0301` and `CAFE` read alike; where[i] is the index in `text` of the character that made
    folded[i]."""
    out, where = [], []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKD", ch).casefold():
            if _ignorable(c):
                continue
            out.append(c)
            where.append(i)
    return "".join(out), where


HIDE_SUBSTRING_MIN = 4             # a name of this many letters and digits or more is hidden inside longer words too


def _hide_pattern(names):
    key = frozenset(n for n in names if isinstance(n, str))
    if key not in _hide_cache:
        patterns = []                                  # one pattern per name: names that overlap are merged by hide(), not cut apart
        for name in sorted(key, key=len, reverse=True):
            folded, _ = _fold_text(unicodedata.normalize("NFC", name))
            parts = [re.escape(p) for p in re.split(r"[\W_]+", folded) if p]
            if not parts:                              # a name with no letter or digit (an emoji) cannot be told from other text
                continue
            chars = "".join(re.split(r"[\W_]+", folded))
            if len(chars) >= HIDE_SUBSTRING_MIN:       # `QzVkClient.py` and `myqzvk.py` hide a name of 4 or more characters;
                patterns.append(re.compile(r"[\W_]*".join(re.escape(c) for c in chars)))      # `Qzvkportal` is `Qzvk_Portal` too
            else:                                      # a shorter name is a hit only as a whole word, or `ab` would hide `table`
                patterns.append(re.compile(r"(?<![^\W_])(?:%s)(?![^\W_])" % r"[\W_]*".join(parts)))
        _hide_cache[key] = tuple(patterns)
    return _hide_cache[key]


def hide(text, names, mask=HIDDEN):
    """R-415: `text` with every protected name in `names` replaced by `mask`. Any case, NFC or NFD, any separator (`-`, `_`,
    `.`, space or none between the parts of a name), invisible characters ignored. A name of 4 or more letters and digits
    is hidden inside longer words as well, and with a separator between any two of its characters; a shorter one only as a whole
    word.
    A name with no letter or digit (an emoji) and look-alike letters from another script are not caught.
    Runs on every free-text fragment of a plain project before it is printed or cached."""
    text = "" if text is None else str(text)
    patterns = _hide_pattern(names) if text else ()
    if not patterns:
        return text
    folded, where = _fold_text(text)
    found = []
    for pattern in patterns:
        for m in pattern.finditer(folded):
            if m.start() == m.end():
                continue
            start, end = where[m.start()], where[m.end() - 1] + 1
            while end < len(text) and _ignorable(text[end]):
                end += 1
            found.append((start, end))
    spans = []
    for start, end in sorted(found):                   # overlapping or touching hits of any names become one
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    out, at = [], 0
    for start, end in spans:
        out.append(text[at:start])
        out.append(mask)
        at = end
    out.append(text[at:])
    return "".join(out)


def is_protected(path):
    """livework.is_private(), and a project.json that is there but cannot be read counts as protected too: it cannot say
    `"private": false`, so the ledger beside it stays closed."""
    import livework
    if livework.is_private(path):
        return True
    try:
        pj = os.path.join(sl.studio_dir(path), "project.json")
        if not os.path.lexists(pj):
            return False
        import json
        return not isinstance(json.loads(sl.read_text(pj).lstrip("\ufeff")), dict)
    except (OSError, ValueError, RecursionError):
        return True


def private_names(config=None):
    """R-415: the folder name and the project.json name of every protected project, as written (hide() folds them): each
    `protected_dirs` entry, and every project of the portfolio list, the session pointers and the children of projects_root
    that livework.is_private() says is protected - `"private": true` counts without a `protected_dirs` entry. No minimum
    length and no git; a generic folder (`~/Dev`) given as a protected_dirs entry is left out, as in protected_names()."""
    import livework
    config = config if config is not None else sl.load_config()
    names = set()

    def add(name):
        if isinstance(name, str) and name.strip():
            names.add(name.strip())

    def add_ledger_name(path):                         # the project.json name of a project that has a ledger
        try:
            if livework.has_ledger(path):
                add(sl.load_project_config(path).get("name"))
        except Exception:
            pass

    raws = [r for r in config.get("protected_dirs") or () if isinstance(r, str) and r.strip()]
    try:
        raws += [r for r in livework._config_dir_strings(livework._home()) if r not in raws]      # the old home-folder configs too
    except Exception:
        pass
    for raw in raws:
        written = os.path.expanduser(raw).rstrip(os.sep)
        generic = True
        for name in (os.path.basename(written), os.path.basename(os.path.realpath(written))):
            if name and name.casefold() not in livework.GENERIC_DIRS:
                add(name)
                generic = False
        add_ledger_name(written)
        if not generic and os.path.isdir(written) and not livework.has_ledger(written):
            try:                                       # a container of protected projects: each project folder in it is one
                for child in os.listdir(written):
                    where = os.path.join(written, child)
                    if not child.startswith(".") and child.casefold() not in livework.GENERIC_DIRS and os.path.isdir(where) \
                            and (livework.has_ledger(where) or os.path.exists(os.path.join(where, ".git"))):
                        add(child)
                        add_ledger_name(where)
            except OSError:
                pass
    paths = set()
    aliases = {}
    try:
        import portfolio
        for e in (portfolio.load() or {}).get("projects", []):
            paths.add(e["path"])
            aliases[e["path"]] = e.get("name")
    except Exception:
        pass
    try:
        paths |= set(livework._pointer_projects())
    except Exception:
        pass
    root = config.get("projects_root")
    root = os.path.expanduser(root) if root else None
    try:
        paths |= {os.path.join(root, n) for n in os.listdir(root) if not n.startswith(".")} if root and os.path.isdir(root) else set()
    except OSError:
        pass
    for path in paths:
        try:
            if os.path.isdir(path) and is_protected(path):
                add(os.path.basename(path.rstrip(os.sep)))
                add(aliases.get(path))                 # the name the owner gave it in the list is what the feed shows
                if livework.has_ledger(path):
                    add(sl.load_project_config(path).get("name"))
        except Exception:
            continue
    return names


def report(hits):
    if not hits:
        return "leak check: clean"
    lines = ["%s:%d: %s %r" % (lint.show(h.path, ""), h.line, h.kind, "<hidden>" if h.kind == PROTECTED else h.term)
             for h in hits[:200]]                             # a path never shapes the report; a protected name is never printed
    if len(hits) > 200:
        lines.append("… %d more" % (len(hits) - 200))
    return "leak check: %d hit(s) — nothing private may leave\n%s" % (len(hits), "\n".join(lines))
