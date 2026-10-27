"""budgit lint — hidden-character and personal-path scan (P6, from ECC's CI validators).

Finds what a reviewer cannot see or should not ship: bidi/zero-width/tag/variation-selector characters, exotic spaces
and every other format or control character (Trojan Source, hidden prompt text, invisible marks), plus home-directory
paths with a real user name. Deterministic, stdlib only. Read-only by default; `fix_text` is the narrow fixer behind
`budgit lint --fix` (R-287): it removes what scan_text reports, keeps emoji sequences and the joiners and marks of
Indic, Persian and Arabic text, and never normalises (no NFKC, no NFC rewrite: accents and the n-tilde are not touched).
"""
import difflib
import os
import struct
import zlib
import re
import shutil
import stat
import subprocess
import unicodedata
from collections import Counter, namedtuple

DEFAULT_ROOTS = ("skills", "plugin", "lessons")
CHUNK = 1 << 20                # a file is read in pieces of about this size, each cut after a newline (R-302: no size limit)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}   # what an export leaves out of a copy
NOISE_DIRS = {".git", "__pycache__"}                    # all a scan leaves out: version-control internals and compiled output
BAD_BYTE = re.compile(r"[\udc80-\udcff]")      # what surrogateescape leaves for an undecodable byte
PERSONAL = re.compile(r"(?:/Users/|/home/|\b[A-Za-z]:\\Users\\)([A-Za-z0-9][A-Za-z0-9._-]*)[/\\]")
PLACEHOLDERS = {"x", "you", "me", "user", "username", "name", "example", "runner", "shared", "your-name",
                "yourname", "someone", "foo", "jane", "john", "<name>", "$user", "public", "default", "guest"}


# ---- hidden characters (R-287) ----
FILLERS = {0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0}    # invisible letters/fillers outside Cf/Cc
HANGUL = ((0x1100, 0x11FF), (0xA960, 0xA97F), (0xD7B0, 0xD7FF))   # a filler between archaic jamo is legitimate
SKIN_TONES = range(0x1F3FB, 0x1F400)
TAG_BODY = {"gbeng", "gbsct", "gbwls"}                # the only subdivision flags in emoji sets (England, Scotland, Wales)
# format marks newer than the Unicode tables of an old Python (3.9 ships 13): hidden on every interpreter, not only the new ones
FORMAT_MARKS = {0x0890, 0x0891, 0x08E2, 0x180F, 0x110BD, 0x110CD, 0x1BCA0, 0x1BCA1, 0x1BCA2, 0x1BCA3} | set(range(0x13430, 0x13441))
BIDI_MARKS = {0x200E, 0x200F, 0x061C}                 # LRM, RLM, ALM: legitimate beside right-to-left letters only
ARABIC = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
INDIC = ((0x0900, 0x0DFF),)                           # Devanagari .. Sinhala
CJK = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x3FFFF))
EMOJI = ((0x00A9, 0x00AA), (0x00AE, 0x00AF), (0x203C, 0x203D), (0x2049, 0x204A), (0x2122, 0x2123), (0x2139, 0x213A),
         (0x2194, 0x21AA), (0x231A, 0x23FF), (0x24C2, 0x24C3), (0x25AA, 0x25FE), (0x2600, 0x27BF), (0x2934, 0x2935),
         (0x2B05, 0x2B55), (0x3030, 0x3031), (0x303D, 0x303E), (0x3297, 0x3299), (0x1F000, 0x1FAFF))
Hidden = namedtuple("Hidden", "index line col char")
HIDDEN_CATS = frozenset(("Cc", "Cf", "Co", "Cs", "Zl", "Zp", "Cn"))   # R-302: unknown (Cn) is hidden too, by default
EMOJI_BLOCKS = (0x1F000, 0x1FAFF)       # new emoji land unassigned in an old Python's tables; they draw as a glyph, not as nothing


def _build_candidates():
    """A regex of exactly the characters _is_hidden accepts, built from the Unicode tables of the Python that runs it, so a scan of
    a clean file is one C-speed pass and a code point added after those tables were made is a candidate, not a gap."""
    cat = unicodedata.category
    hit = [cp for cp in range(0x110000) if cat(chr(cp)) in HIDDEN_CATS]
    hit += [cp for cp in range(0x110000) if cat(chr(cp)) == "Zs" and cp != 0x20]
    hit += list(FILLERS) + list(FORMAT_MARKS) + list(range(0xFE00, 0xFE10)) + list(range(0xE0100, 0xE01F0)) + list(range(0x180B, 0x180E))
    drop = {0x09, 0x0A, 0x0D} | set(range(0xDC80, 0xDD00)) | {cp for cp in range(EMOJI_BLOCKS[0], EMOJI_BLOCKS[1] + 1) if cat(chr(cp)) == "Cn"}
    cps = sorted(set(hit) - drop)
    ranges, start, prev = [], None, None
    for cp in cps:
        if start is None:
            start = prev = cp
        elif cp == prev + 1:
            prev = cp
        else:
            ranges.append((start, prev))
            start = prev = cp
    if start is not None:
        ranges.append((start, prev))
    esc = lambda c: "\\u%04x" % c if c < 0x10000 else "\\U%08x" % c
    cls = lambda rs: "[" + "".join(esc(a) if a == b else esc(a) + "-" + esc(b) for a, b in rs) + "]"
    bmp = [(a, min(b, 0xFFFF)) for a, b in ranges if a < 0x10000]
    astral = [(max(a, 0x10000), b) for a, b in ranges if b >= 0x10000]
    # two classes: the regex engine turns a class of 16-bit characters into a bitmap, but walks a class that holds astral
    # characters range by range; the lookahead keeps that walk to the rare astral character
    return re.compile(cls(bmp) + "|(?=[\U00010000-\U0010ffff])" + cls(astral))


class _Lazy:
    """CANDIDATES: built on first use (about a third of a second), never at import."""
    _re = None

    def _get(self):
        if _Lazy._re is None:
            _Lazy._re = _build_candidates()
        return _Lazy._re

    def finditer(self, text):
        return self._get().finditer(text)

    def match(self, text):
        return self._get().match(text)

    def search(self, text):
        return self._get().search(text)


CANDIDATES = _Lazy()
ASCII_CANDIDATES = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")      # the whole hidden set below U+0080: pure-ASCII text (most files) skips the wide net


def _in(o, ranges):
    return any(a <= o <= b for a, b in ranges)


def _is_hidden(ch):
    if not ch:
        return False
    o = ord(ch)
    if ch in "\n\t\r" or 0xDC80 <= o <= 0xDCFF:            # DC80-DCFF: what surrogateescape leaves for a bad byte (reported apart)
        return False
    cat = unicodedata.category(ch)
    if cat == "Cn":
        return not EMOJI_BLOCKS[0] <= o <= EMOJI_BLOCKS[1]
    return (cat in HIDDEN_CATS or (cat == "Zs" and ch != " ") or o in FILLERS or o in FORMAT_MARKS
            or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or 0x180B <= o <= 0x180D)


def _base(text, i):
    """The character before index i, looking past at most one variation selector and one skin tone (the two things that
    sit between an emoji and a joiner)."""
    i -= 1
    for skippable in (lambda o: 0xFE00 <= o <= 0xFE0F, lambda o: o in SKIN_TONES):
        if i >= 0 and skippable(ord(text[i])):
            i -= 1
    return text[i] if i >= 0 else ""


def _letter_in(ch, ranges):
    """ch is a visible letter or mark of a script in `ranges`: a hidden character never counts as script text."""
    return bool(ch) and _in(ord(ch), ranges) and not _is_hidden(ch) and unicodedata.category(ch)[0] in "LM"


def _emoji(ch):
    return bool(ch) and (_in(ord(ch), EMOJI) or 0x1F1E6 <= ord(ch) <= 0x1F1FF)


def _strong(c):
    """'R' for a right-to-left letter, 'L' for any other strong letter or direction mark, '' for the rest (neutral)."""
    cls = unicodedata.bidirectional(c)
    if cls in ("R", "AL"):
        return "R" if ord(c) not in BIDI_MARKS else ""
    return "L" if cls == "L" else ""


def _beside_rtl(text, i, reach=48):
    """A direction mark exists to steer the neutral characters (spaces, punctuation, digits) between two letters: it is
    legitimate when the nearest strong letter on either side is right-to-left. Looks at most `reach` characters out."""
    for step in (-1, 1):
        j, n = i + step, 0
        while 0 <= j < len(text) and n < reach and text[j] != "\n":
            if ord(text[j]) in BIDI_MARKS:
                break
            s = _strong(text[j])
            if s:
                if s == "R":
                    return True
                break
            j, n = j + step, n + 1
    return False


def _allowed(text, i):
    """True when the hidden character at text[i] is part of something legitimate: an emoji sequence, a joiner in Indic,
    Persian or Arabic text, a direction mark beside a right-to-left letter, a variation selector on
    the character it modifies. One variation selector per base: a second one is a channel, not a presentation."""
    o = ord(text[i])
    prev, nxt = text[i - 1] if i else "", text[i + 1] if i + 1 < len(text) else ""
    if o == 0xFE0F or o == 0xFE0E:
        if _emoji(prev):
            return True
        return prev != "" and prev in "#*0123456789" and nxt == "\u20e3"
    if 0xFE00 <= o <= 0xFE0D or 0xE0100 <= o <= 0xE01EF:
        return bool(prev) and _in(ord(prev), CJK)
    if 0x180B <= o <= 0x180D or o == 0x180F:
        return bool(prev) and 0x1800 <= ord(prev) <= 0x18AF and not _is_hidden(prev)
    if o == 0x200D:                                            # ZWJ
        if _emoji(_base(text, i)) and _emoji(nxt):
            return True
        return _letter_in(prev, INDIC + ARABIC) and _letter_in(nxt, INDIC + ARABIC)
    if o == 0x200C:                                            # ZWNJ
        return _letter_in(prev, INDIC + ARABIC) and _letter_in(nxt, INDIC + ARABIC)
    if o in BIDI_MARKS:                                        # one mark after text, never a run of them
        return not _is_hidden(prev) and _beside_rtl(text, i)
    if o in (0x115F, 0x1160):                                  # Hangul fillers: archaic syllables put them between jamo
        return any(c and _in(ord(c), HANGUL) and ord(c) not in (0x115F, 0x1160) for c in (prev, nxt))
    return False


def _tag_run(text, i):
    """(end, allowed) of the run of tag characters that starts at i: allowed only for the cancel-terminated body of
    England, Scotland or Wales after a black flag. `end` includes a closing cancel tag."""
    k = i
    while k < len(text) and 0xE0000 < ord(text[k]) < 0xE007F:
        k += 1
    cancel = k < len(text) and ord(text[k]) == 0xE007F
    body = "".join(chr(ord(c) - 0xE0000) for c in text[i:k])
    ok = bool(i) and text[i - 1] == "\U0001F3F4" and cancel and body in TAG_BODY
    return k + (1 if cancel else 0), ok


def hidden_in(text, first=True):
    """Every hidden character in text that is not part of something legitimate -> [Hidden(index, line, col, char)].
    A BOM at the very start of a file is an encoding mark, not hidden text: pass first=False for a later piece of one, and
    for a name, a link target or a commit message, which have no encoding mark. Linear in the text: a tag run is judged once
    per run, a direction mark looks a bounded distance out."""
    out = []
    seen, line_no, line_start = 0, 1, 0
    skip_until = 0
    for m in (ASCII_CANDIDATES if text.isascii() else CANDIDATES).finditer(text):
        i, ch = m.start(), m.group()
        if i < skip_until or not _is_hidden(ch) or (i == 0 and first and ch == "\ufeff"):
            continue
        n = text.count("\n", seen, i)
        seen = i
        if n:
            line_no += n
            line_start = text.rfind("\n", 0, i) + 1
        if 0xE0000 < ord(ch) < 0xE007F:
            end, ok = _tag_run(text, i)
            skip_until = end
            if not ok:
                out.extend(Hidden(j, line_no, j - line_start + 1, text[j]) for j in range(i, end))
            continue
        if not _allowed(text, i):
            out.append(Hidden(i, line_no, i - line_start + 1, ch))
    return out


def codepoint(ch):
    """'U+200B ZERO WIDTH SPACE' (controls with no name: 'U+001B <control>')."""
    return "U+%04X %s" % (ord(ch), unicodedata.name(ch, "<control>"))


def fix_text(text):
    """text without the hidden characters scan reports: exotic spaces become a space, line/paragraph separators a
    newline, the rest is deleted. Everything else (accents, emoji sequences, joiners in context, CRLF, BOM) is untouched.
    Removing a character can change what its neighbours are, so it repeats until nothing is left to report."""
    while True:
        bad = {h.index: h.char for h in hidden_in(text)}
        if not bad:
            return text
        out = []
        for i, ch in enumerate(text):
            if i not in bad:
                out.append(ch)
            elif unicodedata.category(ch) == "Zs":
                out.append(" ")
            elif unicodedata.category(ch) in ("Zl", "Zp"):
                out.append("\n")
        text = "".join(out)


def show(text, keep="\n\t\r"):
    """text with every hidden character written as <U+XXXX>, for a diff a person can read. A path passes keep="":
    CR, LF and TAB in a file name must not shape the report either."""
    return "".join("<U+%04X>" % ord(c) if (_is_hidden(c) and c not in keep) or (c in "\n\t\r" and c not in keep) else c
                   for c in text)


Finding = namedtuple("Finding", "path line col kind detail")


BINARY_MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                b"\xca\xfe\xba\xbe", b"\x89PNG", b"GIF8", b"%PDF", b"PK\x03\x04", b"\x1f\x8b", b"SQLite format 3\x00",
                b"\xff\xd8\xff", b"RIFF", b"OggS", b"ID3", b"wOFF", b"wOF2", b"\x00\x01\x00\x00", b"OTTO", b"\x00asm")
TEXT_EXTS = {".md", ".py", ".json", ".sh", ".toml", ".yaml", ".yml", ".txt", ".js", ".ts", ".mjs", ".cfg", ".ini", ".csv",
             ".xml", ".html", ".css", ".jsx", ".tsx", ".swift", ".svg"}
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _text(path):
    """(text, None) to scan, (None, finding detail) for a text file that is really binary, (None, None) to skip."""
    try:
        if not _regular(path):                    # a pipe or device would block the read forever
            return None, None
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None, None
    if raw[:2] in UTF16_BOMS:             # UTF-16 text is full of NULs: decode it instead of skipping it
        return raw.decode("utf-16", errors="replace"), None               # the decoder consumed the mark: scan_text(first=False)
    if b"\x00" in raw[:8192]:
        if os.path.splitext(path)[1].lower() in TEXT_EXTS:
            return None, "NUL bytes in a file named like text (hidden content cannot be scanned)"
        return None, None                 # a real binary (png, db, ...)
    return raw.decode("utf-8", errors="surrogateescape"), None   # a stray bad byte must not hide the rest of the file


def scan_text(path, text, first=True):
    out = []
    for h in hidden_in(text, first):
        out.append(Finding(path, h.line, h.col, "invisible", codepoint(h.char)))
    bad = None
    for n, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        m = BAD_BYTE.search(line)
        if m and bad is None:
            bad = Finding(path, n, m.start() + 1, "invalid-utf8",
                          "byte 0x%02X (file not valid UTF-8)" % (ord(m.group()) - 0xDC00))
        for m in PERSONAL.finditer(line):
            if m.group(1).lower() not in PLACEHOLDERS:
                out.append(Finding(path, n, m.start() + 1, "personal-path", "home path with a user name"))
    if bad:
        out.append(bad)
    out.sort(key=lambda f: (f.line, f.col))
    return out


def _regular(path):
    """A regular file (or a link to one). A pipe, socket or device would block the read forever."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


WHOLE_FILE_LIMIT = 64 * 1024 * 1024      # a small image may open with a text chunk (XMP): judge the whole file, not its first 8 KB
CONTROL_DETAIL = re.compile(r"U\+(?:00[01][0-9A-F]|007F)\b")


ODD = re.compile("[\udc80-\udcff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text_dominant(data):
    """True when a file that claims to be binary reads as text: at most one character in four is a control or a byte that is
    not valid UTF-8 (a real program or image is mostly both)."""
    chars = data.decode("utf-8", errors="surrogateescape")
    return len(ODD.findall(chars)) * 4 <= len(chars)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _valid_png(data):
    """True for a whole PNG file: the signature, then chunks whose lengths and CRC-32 check out, IHDR first, IEND last at the very
    end. A text file cannot pass this by accident, and a real image may carry text chunks (XMP) that are not hidden text."""
    if not data.startswith(PNG_SIGNATURE):
        return False
    pos, first, n = len(PNG_SIGNATURE), True, len(data)
    while pos + 12 <= n:
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 8 + size + 4
        if end > n or (first and kind != b"IHDR"):
            return False
        if zlib.crc32(data[pos + 4:pos + 8 + size]) & 0xFFFFFFFF != struct.unpack(">I", data[end - 4:end])[0]:
            return False
        first = False
        pos = end
        if kind == b"IEND":
            return pos == n
    return False


def scan_file(path, skipped=None):
    """scan_text for a file of any size, read in pieces of CHUNK bytes cut after a newline, so no line, character or emoji
    sequence is ever split and the findings (line numbers too) are those of the whole text. A file taken for a real binary is
    not scanned: its path is appended to `skipped` when a list is given. -> [Finding]"""
    try:
        f = open(path, "rb")
    except OSError:
        return []
    claimed_binary = False
    with f:
        head = f.read(8192)
        if head[:2] in UTF16_BOMS:                      # UTF-16 text is full of NULs: decode it instead of skipping it
            return scan_text(path, (head + f.read()).decode("utf-16", errors="replace"), first=False)   # the decoder ate the mark: a further one is hidden
        if b"\x00" in head:
            ext = os.path.splitext(path)[1].lower()
            if ext in TEXT_EXTS:
                return [Finding(path, 1, 1, "binary-text-file", "NUL bytes in a file named like text (hidden content cannot be scanned)")]
            if ext in BINARY_EXTS or head.startswith(BINARY_MAGIC):
                whole = head + f.read() if os.fstat(f.fileno()).st_size <= WHOLE_FILE_LIMIT else head
                if not _text_dominant(head) or not _text_dominant(whole) or _valid_png(whole):
                    if skipped is not None:
                        skipped.append(path)
                    return []                           # a real binary (png, db, a compiled program ...)
                claimed_binary = True                   # a name or a magic number does not make text safe: scan what reads as text
        f.seek(0)                                       # a file that is neither: scanned as text, its NUL flagged
        out, buf, line0, first, bad_seen = [], b"", 1, True, False
        while True:
            block = f.read(CHUNK)
            if block:
                buf += block
                cut = buf.rfind(b"\n", len(buf) - len(block)) + 1
                if cut == 0:                            # no newline yet: a line longer than a piece keeps growing
                    continue
                piece, buf = buf[:cut], buf[cut:]
            else:
                piece, buf = buf, b""
            if piece:
                for fnd in scan_text(path, piece.decode("utf-8", errors="surrogateescape"), first):
                    if fnd.kind == "invalid-utf8":
                        if bad_seen:
                            continue
                        bad_seen = True
                    out.append(fnd._replace(line=fnd.line + line0 - 1))
                line0 += piece.count(b"\n")
                first = False
            if not block:
                break
    if claimed_binary:                                  # control bytes and bad bytes are what a real binary is made of
        out = [x for x in out if x.kind == "invisible" and not CONTROL_DETAIL.match(x.detail)]
    return out


def entry_findings(path, skipped=None):
    """Everything to say about one entry: its name, then by kind: a link (its target string, never its text), a pipe, socket or
    device (named, never opened), or a regular file (scanned in full)."""
    out = name_findings(path)
    if os.path.islink(path):
        try:
            target = os.readlink(path)
        except OSError:
            return out
        return out + [Finding(path, 0, 0, "hidden-target", d) for d in name_codepoints(target)]
    if not _regular(path):
        if os.path.lexists(path):
            out.append(Finding(path, 0, 0, "non-regular-file", "pipe, socket or device (never opened)"))
        return out
    return out + scan_file(path, skipped)


def _files(paths):
    """Every entry under paths that has a name worth checking: files of any kind, and links (also links to folders, which
    are never followed from inside the tree: their target is scanned where it really is)."""
    for p in paths:
        if os.path.lexists(p) and not os.path.isdir(p):               # a file, a link, a pipe or a device named on its own
            yield p
            continue
        for d, dirs, files in os.walk(p):
            dirs[:] = sorted(x for x in dirs if x not in NOISE_DIRS)
            for x in dirs:
                if os.path.islink(os.path.join(d, x)):
                    yield os.path.join(d, x)
            for f in sorted(files):
                yield os.path.join(d, f)


def name_codepoints(path):
    """The hidden characters of a file's own path, CR, LF and TAB included (they shape a report line): 'U+XXXX NAME' each."""
    return [codepoint(h.char) for h in hidden_in(path, False)] + [codepoint(c) for c in path if c in "\n\t\r"]


def name_findings(path):
    """Hidden characters in a file's own path: a name can carry them just as well as the text can."""
    return [Finding(path, 0, 0, "hidden-name", d) for d in name_codepoints(path)]


def scan(paths, repo, skipped=None):
    """Findings for the given paths, or for DEFAULT_ROOTS under repo when paths is None."""
    if not paths:
        paths = [os.path.join(repo, r) for r in DEFAULT_ROOTS if os.path.isdir(os.path.join(repo, r))]
    out = [Finding(p, 0, 0, "missing-path", "path does not exist (nothing was scanned there)")
           for p in paths if not os.path.lexists(p)]
    for path in _files(paths):
        out.extend(entry_findings(path, skipped))
    return out


BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".icns", ".pdf", ".zip", ".gz", ".tgz", ".woff", ".woff2",
               ".ttf", ".otf", ".mp3", ".mp4", ".mov", ".wav", ".db", ".sqlite", ".pyc", ".ds_store", ".bin", ".so", ".dylib", ".o",
               ".a", ".class", ".jar", ".exe", ".dll", ".wasm", ".car", ".nib"}
LOG_RANGE = re.compile(r"^(?:--all|[A-Za-z0-9_./^~@{}:+-]*[A-Za-z0-9_./^~@{}:+]|[A-Za-z0-9_./^~@{}:+]+\.\.\.?[A-Za-z0-9_./^~@{}:+]*)$")


def _git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    return r.stdout if r.returncode == 0 else None


def tracked_paths(repo, links=False):
    """Every file git tracks under repo (regular files; binary extensions left out), or None when repo is not a git work tree.
    links=True also lists the tracked symbolic links, for their names only."""
    raw = _git(repo, "ls-files", "-z")
    if raw is None:
        return None
    paths = (os.path.join(repo, name) for name in raw.decode("utf-8", "surrogateescape").split("\0") if name)
    return [p for p in paths if (os.path.islink(p) if links else False) or (os.path.isfile(p) and not os.path.islink(p))
            and os.path.splitext(p)[1].lower() not in BINARY_EXTS]   # a symlink's text is its target's business, its name is its own


def scan_tracked(repo):
    paths = tracked_paths(repo, links=True)
    if paths is None:
        return [Finding(repo, 0, 0, "not-a-git-repo", "not a git repository (nothing was scanned)")]
    out = []
    for path in paths:
        out.extend(entry_findings(path))
    return out


def scan_log(repo, rng="HEAD"):
    """Hidden characters in commit messages (report-only: a history rewrite is a separate, deliberate step) ->
    [Finding("commit <8 hex>", line, col, "invisible", detail)]."""
    rng = "--all" if rng == "all" else rng             # `--log all`: argparse would take a bare --all for an option
    if not LOG_RANGE.match(rng) or (rng.startswith("-") and rng != "--all"):
        return [Finding(repo, 0, 0, "bad-range", "not a revision range: %s" % show(rng, ""))]
    raw = _git(repo, "log", "--format=%h%x1f%B%x1e", "--abbrev=8", rng)
    if raw is None:
        return [Finding(repo, 0, 0, "not-a-git-repo", "not a git repository or unknown range (nothing was scanned)")]
    out = []
    for entry in raw.decode("utf-8", "surrogateescape").split("\x1e"):
        if "\x1f" not in entry:
            continue
        sha, message = entry.lstrip("\n").split("\x1f", 1)
        for h in hidden_in(message, False):
            out.append(Finding("commit " + sha, h.line, h.col, "invisible", codepoint(h.char)))
    return out


def fix_files(paths, write=True):
    """The narrow fixer (R-287): rewrite files so scan_text's hidden characters are gone.
    -> (changes [(path, old, new)], skipped [(path, why)]). With write=False nothing is written (preview)."""
    changes, skipped = [], []
    for path in _files(paths):
        if os.path.islink(path):                      # never read through a link: its text is its target's, fixed where it lives
            continue
        text, binary = _text(path)
        if binary or text is None or not hidden_in(text):
            continue
        try:
            with open(path, "rb") as f:
                raw = f.read()
            old = raw.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            skipped.append((path, "not valid UTF-8 text"))
            continue
        if raw[:2] in UTF16_BOMS:
            skipped.append((path, "UTF-16 text"))
            continue
        new = fix_text(old)
        if write:
            tmp = path + ".lint-tmp"
            with open(tmp, "wb") as f:
                f.write(new.encode("utf-8"))
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
        changes.append((path, old, new))
    return changes, skipped


def diff(path, old, new, repo):
    rel = os.path.relpath(path, repo) if path.startswith(repo + os.sep) else path
    return "".join(difflib.unified_diff(show(old).splitlines(True), show(new).splitlines(True),
                                        "a/" + show(rel, ""), "b/" + show(rel, ""), n=0))


def skipped_lines(skipped, repo, limit=10):
    """One line per file taken for a real binary and so not read, the way the export gate names it."""
    lines = ["%s: binary file, not read (it reads as a program or an image, not as text)" %
             show(os.path.relpath(p, repo) if p.startswith(repo + os.sep) else p, "") for p in skipped[:limit]]
    if len(skipped) > limit:
        lines.append("... %d more binary files not read" % (len(skipped) - limit))
    return lines


def report(findings, repo):
    lines = []
    for f in findings:
        rel = os.path.relpath(f.path, repo) if f.path.startswith(repo + os.sep) else f.path
        lines.append("%s:%d:%d: %s %s" % (show(rel, ""), f.line, f.col, f.kind, f.detail))
    counts = Counter(f.detail for f in findings if f.kind == "invisible")
    if counts:
        lines.append("by codepoint:")
        lines.extend("  %s x%d" % (d, n) for d, n in sorted(counts.items()))
    lines.append("budgit lint: %d findings" % len(findings))
    return "\n".join(lines)
