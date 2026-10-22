"""R-52 leak gate: nothing that identifies the owner leaves in a shared copy.

The words to look for are never in this file (it ships too): they come from the owner's config.json (accounts,
noreply emails and their numeric id, hub, projects_root, `private_terms`), the folder names under projects_root, the
hub's projects/ideas/lessons-inbox names and each project's GitHub repo name. Fixed patterns: home paths with a real
user name (lint.PERSONAL) and, in Markdown, «quoted words» of 3+ words (the user's quotes). Whole words, case-insensitive, ≥4 chars.
Deterministic, read-only, stdlib only.
"""
import os
import re
import subprocess
from collections import namedtuple

import lint
import studiolib as sl

MIN_LEN = 4
GENERIC = {"index", "readme", "changelog", "license", "template", "inbox"}   # file names every hub has
QUOTE = re.compile(r"«(?:[^»\s]+[ \t]+){2,}[^»\s]+»")      # the user's quotes: 3+ words («MVP n/m» is a label)
Hit = namedtuple("Hit", "path line kind term")


def _stem(name):
    return os.path.splitext(name)[0]


def _origin_repo(path):
    try:
        r = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True,
                           timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]+[^/]+/([^/\s]+?)(?:\.git)?/?$", r.stdout.strip()) if r.returncode == 0 else None
    return m.group(1) if m else None


def terms(config=None):
    """{lowercase term: kind} built from the owner's config and folders."""
    config = config if config is not None else sl.load_config()
    out = {}

    def add(kind, term):
        term = (term or "").strip()
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
    hub = config.get("hub")
    for key in ("hub", "projects_root"):
        if config.get(key):
            add(key, config[key])
            add(key, os.path.expanduser(config[key]))
    hub_real = os.path.realpath(os.path.expanduser(hub)) if hub else None
    root = config.get("projects_root")
    if root and os.path.isdir(os.path.expanduser(root)):
        base = os.path.expanduser(root)
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if os.path.isdir(path) and os.path.realpath(path) != hub_real and not name.startswith("."):
                add("project folder", name)
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
        for n, line in enumerate(text.splitlines(), 1):
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


def report(hits):
    if not hits:
        return "leak check: clean"
    lines = ["%s:%d: %s %r" % h for h in hits[:200]]
    if len(hits) > 200:
        lines.append("… %d more" % (len(hits) - 200))
    return "leak check: %d hit(s) — nothing private may leave\n%s" % (len(hits), "\n".join(lines))
