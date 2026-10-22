"""budgit lint — hidden-character and personal-path scan (P6, from ECC's CI validators).

Finds what a reviewer cannot see or should not ship: bidi/zero-width/tag characters (Trojan Source, hidden prompt
text) and home-directory paths with a real user name. Deterministic, read-only, stdlib only.
"""
import os
import re
from collections import namedtuple

DEFAULT_ROOTS = ("skills", "plugin", "lessons")
MAX_BYTES = 2 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
INVISIBLE = re.compile(r"[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]")
BAD_BYTE = re.compile(r"[\udc80-\udcff]")      # what surrogateescape leaves for an undecodable byte
PERSONAL = re.compile(r"(?:/Users/|/home/|\b[A-Za-z]:\\Users\\)([A-Za-z0-9][A-Za-z0-9._-]*)[/\\]")
PLACEHOLDERS = {"x", "you", "me", "user", "username", "name", "example", "runner", "shared", "your-name",
                "yourname", "someone", "foo", "jane", "john", "<name>", "$user", "public", "default", "guest"}

Finding = namedtuple("Finding", "path line col kind detail")


TEXT_EXTS = {".md", ".py", ".json", ".sh", ".toml", ".yaml", ".yml", ".txt", ".js", ".ts", ".mjs"}
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _text(path):
    """(text, None) to scan, (None, finding detail) for a text file that is really binary, (None, None) to skip."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None, None
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, None
    if raw[:2] in UTF16_BOMS:             # UTF-16 text is full of NULs: decode it instead of skipping it
        return raw.decode("utf-16", errors="replace"), None
    if b"\x00" in raw[:8192]:
        if os.path.splitext(path)[1].lower() in TEXT_EXTS:
            return None, "NUL bytes in a file named like text (hidden content cannot be scanned)"
        return None, None                 # a real binary (png, db, ...)
    return raw.decode("utf-8", errors="surrogateescape"), None   # a stray bad byte must not hide the rest of the file


def scan_text(path, text):
    out = []
    bad = None
    for n, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        m = BAD_BYTE.search(line)
        if m and bad is None:
            bad = Finding(path, n, m.start() + 1, "invalid-utf8",
                          "byte 0x%02X (file not valid UTF-8)" % (ord(m.group()) - 0xDC00))
        for m in INVISIBLE.finditer(line):
            if n == 1 and m.start() == 0 and m.group() == "\ufeff":
                continue                  # a BOM at the very start is an encoding mark, not hidden text
            out.append(Finding(path, n, m.start() + 1, "invisible", "U+%04X" % ord(m.group())))
        for m in PERSONAL.finditer(line):
            if m.group(1).lower() not in PLACEHOLDERS:
                out.append(Finding(path, n, m.start() + 1, "personal-path", "home path with a user name"))
    if bad:
        out.append(bad)
    return out


def _files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for d, dirs, files in os.walk(p):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for f in sorted(files):
                yield os.path.join(d, f)


def scan(paths, repo):
    """Findings for the given paths, or for DEFAULT_ROOTS under repo when paths is None."""
    if not paths:
        paths = [os.path.join(repo, r) for r in DEFAULT_ROOTS if os.path.isdir(os.path.join(repo, r))]
    out = [Finding(p, 0, 0, "missing-path", "path does not exist (nothing was scanned there)")
           for p in paths if not os.path.exists(p)]
    for path in _files(paths):
        text, binary = _text(path)
        if binary:
            out.append(Finding(path, 1, 1, "binary-text-file", binary))
        elif text is not None:
            out.extend(scan_text(path, text))
    return out


def report(findings, repo):
    lines = []
    for f in findings:
        rel = os.path.relpath(f.path, repo) if f.path.startswith(repo + os.sep) else f.path
        lines.append("%s:%d:%d: %s %s" % (rel, f.line, f.col, f.kind, f.detail))
    lines.append("budgit lint: %d findings" % len(findings))
    return "\n".join(lines)
