"""budgit design-check — deterministic design gates (P2). Stdlib only, 0 model tokens.

diff: compare screenshot PNGs against the user's approved baselines; a changed, resized or missing screen is drift
(a real project's lesson: features silently restyled screens nobody was looking at).
"""
import json
import os
import struct
import zlib

import studiolib as sl


class DesignCheckError(Exception):
    pass


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    """-> (width, height, [ (r, g, b), ... ] row-major). 8-bit RGB/RGBA, non-interlaced (what Chrome writes)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DesignCheckError("%s: not a PNG" % path)
    pos, idat, head = 8, [], None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if not head:
        raise DesignCheckError("%s: no IHDR" % path)
    w, h, depth, ctype, _, _, interlace = head
    if depth != 8 or ctype not in (2, 6) or interlace:
        raise DesignCheckError("%s: only 8-bit RGB/RGBA non-interlaced PNGs are supported" % path)
    ch = 3 if ctype == 2 else 4
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise DesignCheckError("%s: corrupt image data (%s)" % (path, e))
    stride = w * ch
    if len(raw) < h * (stride + 1):
        raise DesignCheckError("%s: truncated image data" % path)
    pixels, prev = [], bytearray(stride)
    for y in range(h):
        filt = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = row[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if filt == 1:
                row[i] = (row[i] + a) & 0xFF
            elif filt == 2:
                row[i] = (row[i] + b) & 0xFF
            elif filt == 3:
                row[i] = (row[i] + (a + b) // 2) & 0xFF
            elif filt == 4:
                row[i] = (row[i] + _paeth(a, b, c)) & 0xFF
            elif filt != 0:
                raise DesignCheckError("%s: bad filter %d" % (path, filt))
        pixels.extend(tuple(row[i:i + 3]) for i in range(0, stride, ch))
        prev = row
    return w, h, pixels


def diff_png(base, cur):
    """-> (pct_changed, why) ; why = '' | 'size'."""
    bw, bh, bp = read_png(base)
    cw, ch, cp = read_png(cur)
    if (bw, bh) != (cw, ch):
        return 100.0, "size %dx%d -> %dx%d" % (bw, bh, cw, ch)
    changed = sum(1 for p, q in zip(bp, cp) if p != q)
    return 100.0 * changed / max(len(bp), 1), ""


def diff_dirs(baseline, current, max_pct=0.5):
    rows = []
    for name in sorted(n for n in os.listdir(baseline) if n.lower().endswith(".png")):
        cur = os.path.join(current, name)
        if not os.path.isfile(cur):
            rows.append(dict(name=name, pct=100.0, drift=True, why="missing"))
            continue
        pct, why = diff_png(os.path.join(baseline, name), cur)
        rows.append(dict(name=name, pct=pct, drift=bool(why) or pct > max_pct, why=why))
    return rows


def diff_report(rows, max_pct):
    out = []
    for r in rows:
        mark = "DRIFT" if r["drift"] else "ok   "
        out.append("%s %-40s %6.2f%% %s" % (mark, r["name"], r["pct"], r["why"]))
    n = sum(r["drift"] for r in rows)
    out.append("%d of %d screen(s) drifted (threshold %.2f%% changed pixels)" % (n, len(rows), max_pct))
    return "\n".join(out), n


# ---- tokens: literal design values outside the token files (P2, plan task 4a) ----
import re  # noqa: E402
import subprocess  # noqa: E402

UI_EXT = {".css", ".scss", ".sass", ".less", ".html", ".htm", ".jsx", ".tsx", ".js", ".ts", ".vue", ".svelte",
          ".astro"}
SKIP_DIRS = {"node_modules", "vendor", "out", "coverage", "fixtures", "__pycache__", "venv", "scratchpad", "spikes",
             "docs"}   # + hidden dirs and dist*/build* (spike 2026-10-19: worktrees, drafts, mocks, builds = noise)


def _skip_dir(name):
    return name in SKIP_DIRS or name.startswith(".") or name.startswith("dist") or name.startswith("build")
TOKEN_FILE = re.compile(r"^_?(?:tokens|theme|design-tokens)\b|\.tokens\.|^DESIGN\.md$|^tailwind\.config\.", re.I)
CSS_EXT = {".css", ".scss", ".sass", ".less"}

PRAGMA = re.compile(r"design-ok:\s*\w")
COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->|^\s*//.*$")
CUSTOM_PROP = re.compile(r"--[\w-]+\s*:[^;}]*")
URL = re.compile(r"url\([^)]*\)", re.I)
HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
COLOR_FN = re.compile(r"(?<![\w-])(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\(\s*(?!var\()", re.I)
SIZE_PROPS = (r"font|font-size|border(?:-(?:top|bottom))?(?:-(?:left|right))?-radius|box-shadow|gap|row-gap|column-gap|"
              r"margin(?:-[a-z]+)?|padding(?:-[a-z]+)?|line-height|letter-spacing")
CSS_DECL = re.compile(r"(?<![\w-])(?:%s)\s*:\s*([^;{}]*)" % SIZE_PROPS, re.I)
JSX_DECL = re.compile(r"(?<![\w-])(fontSize|borderRadius|boxShadow|gap|rowGap|columnGap|margin\w*|padding\w*|"
                      r"lineHeight|letterSpacing)\s*:\s*(\"[^\"]*\"|'[^']*'|[\d.]+(?=\s*[,}]))")
PX = re.compile(r"(?<![\w.-])(\d*\.?\d+)px\b")
TW_ARB = re.compile(r"(?<![\w-])[a-z][\w:-]*-\[([^\]\s]+)\]")
DECL = re.compile(r"(?<![\w$.-])([A-Za-z][\w-]*)\s*:\s*([^;{}]*)")
COLOUR_PROP = re.compile(r"colou?r|background|^bg|fill|stroke|border|outline|shadow|accent|caret|tint", re.I)
QUOTED_HEX = re.compile(r"([\"'`])(#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4}))\1")
ANCHOR_CTX = re.compile(r"(?:\b(?:href|to|id|for|target|selector|hash|anchor)\s*[:=]\s*\{?|"
                        r"(?:querySelector(?:All)?|getElementById|closest|matches|scrollTo|\$|jQuery)\s*\(\s*)$", re.I)
WORD = re.compile(r"(?<![\w.$#-])([A-Za-z]+)(?![\w.(-])")
NAMED_COLOURS = set((
    "aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood "
    "cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod "
    "darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon "
    "darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray "
    "dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green "
    "greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon "
    "lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon "
    "lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta "
    "maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen "
    "mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive "
    "olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru "
    "pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen "
    "seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato "
    "turquoise violet wheat white whitesmoke yellow yellowgreen").split())
TRANSITION_ALL = re.compile(r"transition\s*:\s*[\"']?all\b|(?<![\w-])transition-all(?![\w-])", re.I)


def is_ui_file(path):
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if any(_skip_dir(p) for p in parts[:-1]) or ".min." in name or TOKEN_FILE.search(name):
        return False
    return os.path.splitext(name)[1].lower() in UI_EXT


NUDGE_PX = 2   # 0-2px = hairlines and optical nudges, not design values (spike: 66 of 187 hits in a real app were 2px)


def _px_over_hairline(value):
    return any(float(n) > NUDGE_PX for n in PX.findall(value))


DIGIT = re.compile(r"\d")
COLOR_FN_HINTS = ("rgb", "hsl", "lab(", "lch(", "hwb(")   # oklab/oklch end in lab(/lch(
MAYBE = re.compile(r"[#(:\d]|-\[|transition", re.I)   # nothing below can match a text without one of these


def _scan_line(text, css=True):
    # each regex runs only when its trigger substring is there (verifier R-47-P2 round 3: ~15 µs per line made
    # 2,000 files of one-line rules take 2.3 s); a gate may let a text through, never hold a match back
    if not MAYBE.search(text) or ("design-ok" in text and PRAGMA.search(text)):
        return []
    low = text.lower()
    if not css and ("/*" in text or "<!--" in text or "//" in text):   # CSS bodies come with comments blanked
        text = COMMENT.sub(lambda m: " " * len(m.group(0)), text)        # (a `^//` here would eat a flattened body)
    if "url(" in low:
        text = URL.sub(lambda m: " " * len(m.group(0)), text)
    if "--" in text:
        text = CUSTOM_PROP.sub(lambda m: " " * len(m.group(0)), text)   # token definitions are the allowed place
    has_hex, has_digit = "#" in text, bool(DIGIT.search(text))
    hits = []
    for m in (TW_ARB.finditer(text) if "-[" in text else ()):
        v = m.group(1)
        if HEX.fullmatch(v) or COLOR_FN.match(v):
            hits.append((m.start(), m.end(), "colour"))
        elif PX.fullmatch(v) and _px_over_hairline(v):
            hits.append((m.start(), m.end(), "size"))
    # colours only in a value context (verifier R-47-P2: selectors, href="#x", querySelector('#x'), "Order: #12"
    # were flagged; `color: red` and `font:` were missed)
    for m in (DECL.finditer(text) if ":" in text and (has_hex or "bg" in low or COLOUR_PROP.search(text)) else ()):
        prop, val, vstart = m.group(1), m.group(2), m.start(2)
        rest = text[m.end():].lstrip()
        if rest.startswith("{") or val.rstrip().endswith(","):   # a selector such as li:hover #fade {
            continue
        colourish = bool(COLOUR_PROP.search(prop))
        if css or colourish:
            for h in HEX.finditer(val):
                hits.append((vstart + h.start(), vstart + h.end(), "colour"))
        if colourish:
            for w in WORD.finditer(val):
                if w.group(1).lower() in NAMED_COLOURS:
                    hits.append((vstart + w.start(), vstart + w.end(), "colour"))
    for m in (QUOTED_HEX.finditer(text) if has_hex else ()):
        if not ANCHOR_CTX.search(text[:m.start()]):
            hits.append((m.start(2), m.end(2), "colour"))
    for m in (COLOR_FN.finditer(text) if any(f in low for f in COLOR_FN_HINTS) else ()):
        hits.append((m.start(), m.end(), "colour"))
    for m in (CSS_DECL.finditer(text) if "px" in text else ()):   # one hit per declaration, placed on its first offending value
        px = next((x for x in PX.finditer(m.group(1)) if float(x.group(1)) > NUDGE_PX), None)
        if px:
            hits.append((m.start(1) + px.start(), m.start(1) + px.end(), "size"))
    for m in (JSX_DECL.finditer(text) if has_digit else ()):
        prop, v = m.group(1), m.group(2)
        if v[0] in "\"'":
            bad = _px_over_hairline(v)
        else:
            bad = prop != "lineHeight" and float(v.rstrip(".") or 0) > NUDGE_PX
        if bad:
            hits.append((m.start(), m.end(), "size"))
    for m in (TRANSITION_ALL.finditer(text) if "transition" in low else ()):
        hits.append((m.start(), m.end(), "transition-all"))
    hits.sort()
    kept = []
    for h in hits:   # one finding per span and kind (a Tailwind [#hex] is not also a bare #hex)
        if any(k[2] == h[2] and h[0] < k[1] and k[0] < h[1] for k in kept):
            continue
        kept.append(h)
    return kept


STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
HTML_COMMENT_MARK = re.compile(r"<!--|-->")   # inside <style> these only hide live CSS from ancient browsers
LINE_COMMENT = re.compile(r"^[ \t]*//[^\n]*", re.M)
# SCSS/Less/<style lang=…> `//` anywhere on a line, never url(//…) or http:// (verifier R-47-P2 round 4: `.card { // shell`
# hid the whole block once bodies are flattened); plain .css has no `//` comments, so it keeps the line-start rule
LINE_COMMENT_ANY = re.compile(r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|url\([^)\n]*\))|(?<![:\w/])//[^\n]*""", re.I)
_blank_line_comment = lambda m: m.group(0) if m.group(1) else _blank(m)   # strings and url(…) are skipped whole
# (verifier R-47-P2 round 5: `content: " // "; color: #999` hid the colour)
_blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))   # keep offsets and line breaks


def _css_regions(path, text):
    if os.path.splitext(path)[1].lower() in CSS_EXT:
        return [(0, len(text))]
    return [(m.start(2), m.end(2)) for m in STYLE_BLOCK.finditer(text)]


def scan_text(path, text, first_line=1, keep=None):
    """Whole-file scan. CSS (files and <style> blocks) is read per DECLARATION, so a value Prettier put on the next
    line is still seen (verifier R-47-P2 round 2); other code per line. `keep`: only report these line numbers."""
    import bisect
    lines = text.split("\n")
    starts = [0]
    for ln in lines[:-1]:
        starts.append(starts[-1] + len(ln) + 1)
    lineno = lambda off: first_line + bisect.bisect_right(starts, off) - 1
    raw = []
    regions = _css_regions(path, text)
    for a, b in regions:
        css = BLOCK_COMMENT.sub(_blank, HTML_COMMENT_MARK.sub(_blank, text[a:b]))
        if path.lower().endswith(".css"):
            css = LINE_COMMENT.sub(_blank, css)
        else:
            css = LINE_COMMENT_ANY.sub(_blank_line_comment, css)
        for seg in re.finditer(r"[^{}]+", css):   # one scan per block body (verifier R-47-P2 round 3: per-declaration
            body = seg.group(0)                    # scanning cost 209k calls on 2,000 files)
            if css[seg.end():seg.end() + 1] == "{":   # ends in a selector (`#fade,\n.x {`, SCSS `&:hover {`)
                body = body[:body.rfind(";") + 1]      # keep only the declarations before it
            if not body.strip():
                continue
            for start, _, kind in _scan_line(body.replace("\n", " "), True):
                raw.append((lineno(a + seg.start() + start), kind))
    if regions != [(0, len(text))]:   # the non-CSS part of html/jsx/vue…, line by line
        rest = text
        for a, b in regions:
            rest = rest[:a] + re.sub(r"[^\n]", " ", rest[a:b]) + rest[b:]
        for i, ln in enumerate(rest.split("\n")):
            if ln.strip():
                for _, _, kind in _scan_line(ln, False):
                    raw.append((first_line + i, kind))
    out = []
    for n, kind in sorted(raw):
        src = lines[n - first_line]
        if (keep is not None and n not in keep) or PRAGMA.search(src):
            continue
        out.append(dict(path=path, line=n, kind=kind, snippet=src.strip()[:100]))
    return out


def scan_lines(path, numbered):
    """Consecutive (lineno, text) pairs → findings (a whole file, or a contiguous excerpt)."""
    numbered = list(numbered)
    if not numbered:
        return []
    return scan_text(path, "\n".join(t for _, t in numbered), numbered[0][0])


def _skipped(path, skip):
    import fnmatch
    return any(fnmatch.fnmatch(path, g) for g in skip)


def token_globs(repo):
    """Token files the project declares in .budgit/project.json → design.tokens (globs, repo-relative)."""
    return _project_design(repo, "tokens")


def scan_tree(root, skip=()):
    skip = tuple(skip) + token_globs(root)
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not _skip_dir(d)]
        for name in files:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if not is_ui_file(rel) or _skipped(rel, skip):
                continue
            try:
                with open(os.path.join(root, rel), encoding="utf-8", errors="ignore") as f:
                    out.extend(scan_lines(rel, enumerate(f.read().splitlines(), 1)))
            except OSError:
                continue
    return out


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(diff_text):
    """Unified diff -> {path: [(new_lineno, text)]} for added lines only."""
    out, path, n = {}, None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].split("\t")[0]
            path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
            continue
        if line.startswith("--- ") or line.startswith("diff --git"):
            continue
        m = _HUNK.match(line)
        if m:
            n = int(m.group(1))
            continue
        if path is None:
            continue
        if line.startswith("+"):
            out.setdefault(path, []).append((n, line[1:]))
            n += 1
        elif line.startswith(" "):
            n += 1
    return out


def scan_changes(repo, base="HEAD", skip=()):
    def git(*args):
        r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
        if r.returncode:
            raise DesignCheckError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
        return r.stdout
    skip = tuple(skip) + token_globs(repo)
    out = []
    for path, lines in added_lines(git("diff", "--unified=0", "--no-color", "--no-ext-diff", base, "--")).items():
        if is_ui_file(path) and not _skipped(path, skip):
            try:   # whole file for context (multi-line declarations), report only the added lines
                with open(os.path.join(repo, path), encoding="utf-8", errors="ignore") as f:
                    out.extend(scan_text(path, f.read(), keep={n for n, _ in lines}))
            except OSError:
                out.extend(f for n, t in lines for f in scan_lines(path, [(n, t)]))
    for path in git("ls-files", "--others", "--exclude-standard").splitlines():
        if is_ui_file(path) and not _skipped(path, skip):
            try:
                with open(os.path.join(repo, path), encoding="utf-8", errors="ignore") as f:
                    out.extend(scan_lines(path, enumerate(f.read().splitlines(), 1)))
            except OSError:
                continue
    return out


def tokens_report(found):
    lines = ["%s:%d  %-14s %s" % (f["path"], f["line"], f["kind"], f["snippet"]) for f in found]
    by_kind = {}
    for f in found:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    lines.append("%d literal value(s) outside the tokens%s. Use a token (var(--…)/theme), or add "
                 "`design-ok: <reason>` on the line." % (len(found), (" — " + ", ".join(
                     "%s %d" % kv for kv in sorted(by_kind.items()))) if found else ""))
    return "\n".join(lines)


# ---- docs: the project's design decisions are written down (any file, any format) ----
# impeccable and Google DESIGN.md use ## Colors / ## Typography / ## Layout; accept plain synonyms and YAML keys too
DOC_SECTIONS = (("colour", r"colou?rs?|palette"), ("type", r"typography|type|fonts?"),
                ("spacing", r"layout|spacing|space|grid"))
DOC_STATES = re.compile(r"\bempty\b", re.I), re.compile(r"\bloading\b", re.I), re.compile(r"\berror\b", re.I)


def _project_design(repo, key):
    try:
        with open(os.path.join(sl.studio_dir(repo), "project.json"), encoding="utf-8") as f:
            val = (json.load(f).get("design") or {}).get(key) or []
    except (OSError, ValueError, AttributeError):
        return ()
    return tuple(v for v in ([val] if isinstance(val, str) else val) if isinstance(v, str))


def design_docs(repo):
    """-> (paths found, declared entries that match nothing). Declared in .budgit/project.json → design.docs (paths
    or globs, repo-relative); undeclared → every .md under docs/design/."""
    import glob
    declared = _project_design(repo, "docs")
    found, missing = [], []
    for entry in declared or ("docs/design/**/*.md",):
        hits = sorted(p for p in glob.glob(os.path.join(repo, entry), recursive=True) if os.path.isfile(p))
        if not hits and declared:
            missing.append(entry)
        found += [h for h in hits if h not in found]
    return found, missing


def check_docs(repo):
    """-> list of problems (empty = ok): across the project's design docs, colour, type and spacing have a heading
    or key, and empty/loading/error states are named. Never rejects impeccable's own DESIGN.md."""
    found, missing = design_docs(repo)
    problems = ["declared design doc not found: %s" % m for m in missing]
    if not found:
        return problems or ["no design doc found: write the design decisions anywhere, in any format, and list the "
                            "files in .budgit/project.json → design.docs (default: docs/design/*.md)"]
    text = ""
    for path in found:
        with open(path, encoding="utf-8", errors="replace") as f:
            text += f.read() + "\n"
    heads = re.findall(r"^#{1,4}\s+(.+)$|^([A-Za-z][\w-]*)\s*:", text, re.M)
    names = " | ".join((a or b).strip().lower() for a, b in heads)
    lacking = [label for label, pat in DOC_SECTIONS if not re.search(r"\b(?:%s)\b" % pat, names)]
    if not all(rx.search(text) for rx in DOC_STATES):
        lacking.append("states (empty/loading/error)")
    if lacking:
        problems.append("design docs (%s) do not cover: %s" % (", ".join(os.path.relpath(p, repo) for p in found),
                                                               ", ".join(lacking)))
    return problems


# ---- shots: headless Chrome screenshots, animations off, the zoom viewport included ----
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# name -> (CSS width, CSS height, device scale). zoom: a 1280×720 page at 80 % browser zoom on a retina screen;
# a project overrides it in .budgit/project.json → design.zoom = [width, height, scale] (use_project_zoom)
VIEWPORTS = {"375x812": (375, 812, 1), "768x1024": (768, 1024, 1), "1440x900": (1440, 900, 1),
             "zoom": (1600, 900, 1.6)}
VIEWPORT_ALIASES = {"her-zoom": "zoom"}   # voice-ok: the old name, still accepted as input so existing baselines keep working


def canon_viewport(name):
    return VIEWPORT_ALIASES.get(name, name)


def use_project_zoom(repo):
    """Apply .budgit/project.json → design.zoom ([width, height, scale]) to the `zoom` viewport. A bad value is ignored."""
    try:
        with open(os.path.join(sl.studio_dir(repo), "project.json"), encoding="utf-8") as f:
            val = (json.load(f).get("design") or {}).get("zoom")
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(val, list) and len(val) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in val):
        VIEWPORTS["zoom"] = (int(val[0]), int(val[1]), float(val[2]))


def find_chrome(chrome=None):
    import shutil
    for cand in (chrome, sl.env("CHROME"), CHROME_MAC, shutil.which("google-chrome"),
                 shutil.which("chromium")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
        if chrome:   # an explicit path that is not there is an error, not a fallback
            break
    raise DesignCheckError("Chrome not found%s; install Google Chrome or set BUDGIT_CHROME=/path/to/chrome"
                           % (" at %s" % chrome if chrome else ""))


class _Chrome:
    """Headless Chrome driven over --remote-debugging-pipe (fd 3 in, fd 4 out; NUL-terminated JSON), the way
    Puppeteer/Playwright do. Viewports are set with Emulation.setDeviceMetricsOverride because --window-size lies in
    --headless=new (Chrome 155, 2026-10-19: 1600x900 → page saw 1600x813; 375 wide → 500)."""

    def __init__(self, chrome, profile):
        import fcntl
        import subprocess
        c_in, self._w = os.pipe()     # chrome reads fd 3
        self._r, c_out = os.pipe()    # chrome writes fd 4

        def fds():                    # high copies first, so dup2 onto 3/4 cannot clobber the other end
            a, b = fcntl.fcntl(c_in, fcntl.F_DUPFD, 10), fcntl.fcntl(c_out, fcntl.F_DUPFD, 10)
            os.dup2(a, 3)
            os.dup2(b, 4)
        argv = [chrome, "--headless=new", "--remote-debugging-pipe", "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--mute-audio",
                "--user-data-dir=" + profile, "about:blank"]
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, preexec_fn=fds, pass_fds=(3, 4))
        finally:
            os.close(c_in)
            os.close(c_out)
        self._buf, self._id, self.events = b"", 0, []

    def _read_msg(self, deadline):
        import select
        import time
        while b"\0" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise DesignCheckError("Chrome did not answer in time")
            ready, _, _ = select.select([self._r], [], [], min(left, 1.0))
            if ready:
                data = os.read(self._r, 1 << 20)
                if not data:
                    raise DesignCheckError("Chrome exited (code %s)" % self.proc.poll())
                self._buf += data
            elif self.proc.poll() is not None:
                raise DesignCheckError("Chrome exited (code %s)" % self.proc.returncode)
        raw, self._buf = self._buf.split(b"\0", 1)
        return json.loads(raw)

    def call(self, method, params=None, session=None, timeout=30):
        import time
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        try:
            os.write(self._w, json.dumps(msg).encode() + b"\0")
        except OSError:
            raise DesignCheckError("Chrome exited (code %s)" % self.proc.poll())
        deadline = time.monotonic() + timeout
        while True:
            rec = self._read_msg(deadline)
            if rec.get("id") == self._id:
                if "error" in rec:
                    raise DesignCheckError("%s: %s" % (method, rec["error"].get("message")))
                return rec.get("result", {})
            if "method" in rec:
                self.events.append(rec["method"])

    def wait_event(self, name, timeout=30):
        import time
        deadline = time.monotonic() + timeout
        while name not in self.events:
            rec = self._read_msg(deadline)
            if "method" in rec:
                self.events.append(rec["method"])

    def page(self, viewport, motion="reduce"):
        w, h, scale = VIEWPORTS[canon_viewport(viewport)]
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.call("Page.enable", session=sid)
        self.call("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": scale,
                                                         "mobile": w < 768}, session=sid)
        self.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": motion}]},
                  session=sid)
        return sid

    def goto(self, sid, url, settle=True):
        self.events = []
        nav = self.call("Page.navigate", {"url": url}, session=sid)
        if nav.get("errorText"):   # verifier R-47-P2: a dead server gave Chrome's error page with exit 0
            raise DesignCheckError("page did not load: %s" % nav["errorText"])
        self.wait_event("Page.loadEventFired")
        status = self.call("Runtime.evaluate", {"returnByValue": True, "expression":
                           "(performance.getEntriesByType('navigation')[0] || {}).responseStatus || 0"},
                           session=sid)["result"].get("value") or 0
        if isinstance(status, int) and status >= 400:
            raise DesignCheckError("HTTP %d" % status)
        # animations off for the frame (Playwright's animations:"disabled"), web fonts loaded, two frames settled
        if settle:
                self.call("Runtime.evaluate", {"awaitPromise": True, "expression": SETTLE_JS}, session=sid)

    def close(self):
        try:
            self.call("Browser.close", timeout=5)
        except DesignCheckError:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        os.close(self._w)
        os.close(self._r)


SETTLE_JS = ("(() => { const s = document.createElement('style'); s.textContent = '*,*::before,*::after{"
             "animation-duration:0s!important;animation-delay:0s!important;transition:none!important;"
             "caret-color:transparent!important}'; (document.head || document.documentElement).appendChild(s);"
             " return (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => new Promise(r =>"
             " requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 150))))).then(() => true); })()")


def _shot_name(spec):
    if "=" in spec.split("://", 1)[0]:
        name, url = spec.split("=", 1)
        return name, url
    rest = spec.split("://", 1)[-1]
    rest = rest.split("/", 1)[1] if "/" in rest else ""
    return (re.sub(r"[^A-Za-z0-9]+", "-", rest).strip("-").lower() or "page"), spec


def _with_chrome(chrome, fn):
    import tempfile
    chrome = find_chrome(chrome)
    with tempfile.TemporaryDirectory(prefix="budgit-chrome-") as profile:
        browser = _Chrome(chrome, profile)
        try:
            return fn(browser)
        finally:
            browser.close()


def shots(specs, out, chrome=None, viewports=None):
    """specs: 'name=URL' or 'URL'. -> list of PNG paths, one per spec × viewport. Throwaway profile, one Chrome."""
    import base64
    viewports = [canon_viewport(v) for v in viewports or list(VIEWPORTS)]
    for v in viewports:
        if v not in VIEWPORTS:
            raise DesignCheckError("unknown viewport %s (known: %s)" % (v, ", ".join(VIEWPORTS)))
    os.makedirs(out, exist_ok=True)

    def run(browser):
        made = []
        for spec in specs:
            name, url = _shot_name(spec)
            for v in viewports:
                png = os.path.join(out, "%s@%s.png" % (name, v))
                try:
                    sid = browser.page(v)
                    browser.goto(sid, url)
                    data = browser.call("Page.captureScreenshot", {"format": "png"}, session=sid)["data"]
                except DesignCheckError as exc:
                    raise DesignCheckError("no screenshot for %s@%s (%s): %s" % (name, v, url, exc))
                with open(png, "wb") as f:
                    f.write(base64.b64decode(data))
                made.append(png)
        return made
    return _with_chrome(chrome, run)


def probe_viewport(viewport, chrome=None):
    """What a page really sees in that viewport: 'WxH@dpr' from innerWidth/innerHeight/devicePixelRatio."""
    def run(browser):
        sid = browser.page(viewport)
        # a real app declares width=device-width; without it a mobile viewport lays out at 980 px (true phone behaviour)
        browser.goto(sid, "data:text/html,<meta name='viewport' content='width=device-width,initial-scale=1'>")
        val = browser.call("Runtime.evaluate", {"expression": "[innerWidth, innerHeight, devicePixelRatio]",
                                                "returnByValue": True}, session=sid)["result"]["value"]
        if isinstance(val, str):   # the fake Chrome in tests answers a string
            return val
        return "%dx%d@%s" % (val[0], val[1], ("%.2f" % val[2]).rstrip("0").rstrip("."))
    return _with_chrome(chrome, run)


# ---- motion: static lint of what animates ----
# Two passes (prefers-reduced-motion no-preference / reduce). Running animations come from document.getAnimations();
# transitions and @keyframes from the CSSOM rules whose @media matches. Hover/focus transitions are checked as rules
# only (nothing is hovered); cross-origin sheets cannot be read and are counted, never passed silently.
MOTION_JS = r"""(() => new Promise(done => setTimeout(() => {
  const meta = new Set(['offset', 'easing', 'composite', 'computedOffset']);
  const running = document.getAnimations().map(a => {
    const e = a.effect, t = e && e.getComputedTiming ? e.getComputedTiming() : {};
    const kf = e && e.getKeyframes ? e.getKeyframes() : [];
    return {name: a.animationName || a.transitionProperty || a.id ||
                  (e && e.target ? e.target.tagName.toLowerCase() : 'animation'),
            kind: a.constructor.name, ms: typeof t.duration === 'number' ? t.duration : 0,
            infinite: t.iterations === Infinity,
            props: [...new Set(kf.flatMap(k => Object.keys(k).filter(p => !meta.has(p))))]};
  });
  const ms = v => Math.max(0, ...v.split(',').map(x => x.trim())
                  .map(x => x.endsWith('ms') ? parseFloat(x) : parseFloat(x) * 1000).filter(n => !isNaN(n)));
  const rules = []; let unreadable = 0;
  const walk = list => { for (const r of list) {
    if (r.type === CSSRule.KEYFRAMES_RULE) {
      const p = new Set(); for (const k of r.cssRules) for (let i = 0; i < k.style.length; i++) p.add(k.style[i]);
      rules.push({sel: '@keyframes ' + r.name, kind: 'keyframes', props: [...p], ms: 0}); continue;
    }
    if (r.media && r.cssRules) { if (matchMedia(r.media.mediaText).matches) walk(r.cssRules); continue; }
    if (r.styleSheet) { try { walk(r.styleSheet.cssRules); } catch (e) { unreadable++; } continue; }
    if (r.style && r.style.transitionProperty) {
      const d = ms(r.style.transitionDuration || '0s');
      if (d > 0) rules.push({sel: r.selectorText, kind: 'transition', ms: d,
                             props: r.style.transitionProperty.split(',').map(x => x.trim())});
    }
    if (r.cssRules) walk(r.cssRules);
  } };
  for (const s of document.styleSheets) { try { walk(s.cssRules); } catch (e) { unreadable++; } }
  done(JSON.stringify({running, rules, unreadable}));
}, 400)))()"""
MOTION_OK = {"transform", "opacity", "translate", "rotate", "scale", "visibility"}
MOTION_STILL = {"opacity", "visibility"}            # what may still run under reduced motion
MOTION_LAYOUT = re.compile(r"^(?:width|height|min-|max-|top|left|right|bottom|inset|margin|padding|border(?:-\w+)?-width"
                           r"|font-size|line-height|letter-spacing|word-spacing|gap|row-gap|column-gap|flex|grid|display"
                           r"|position)")
UI_MS = 300


def _kebab(prop):
    return prop if prop.startswith("--") else re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), prop)


def _prop_findings(where, props, ms, infinite=False):
    out = []
    props = [_kebab(p) for p in props]
    if "all" in props:
        out.append(("FAIL", where, "transition: all (name the properties: transform, opacity)"))
    layout = [p for p in props if MOTION_LAYOUT.match(p)]
    paint = [p for p in props if p not in MOTION_OK and p != "all" and p not in layout]
    if layout:
        out.append(("FAIL", where, "animates layout (%s): use transform/opacity" % ", ".join(layout)))
    if paint:
        out.append(("WARN", where, "animates %s: transform/opacity are the cheap ones" % ", ".join(paint)))
    if ms > UI_MS and not infinite:
        out.append(("WARN", where, "%d ms: UI motion over %d ms needs a written reason" % (ms, UI_MS)))
    return out


def motion_findings(passes):
    """passes: {"no-preference": {"running": [...], "rules": [...]}, "reduce": {...}} -> [(level, where, msg)]."""
    found = []
    free = passes.get("no-preference") or {}
    for a in free.get("running", ()):
        found += _prop_findings(a["name"], a["props"], a.get("ms", 0), a.get("infinite"))
    for r in free.get("rules", ()):
        found += _prop_findings(r["sel"], r["props"], r.get("ms", 0))
    for a in (passes.get("reduce") or {}).get("running", ()):
        moving = sorted({_kebab(p) for p in a["props"]} - MOTION_STILL)
        if moving:
            found.append(("FAIL", a["name"], "still moves (%s) under reduced motion: swap it for opacity/instant"
                          % ", ".join(moving)))
    return list(dict.fromkeys(found))


def motion(urls, chrome=None, viewport="1440x900"):
    """-> [(url, findings, notes)] for each URL, one Chrome, both reduced-motion passes."""
    viewport = canon_viewport(viewport)
    if viewport not in VIEWPORTS:
        raise DesignCheckError("unknown viewport %s (known: %s)" % (viewport, ", ".join(VIEWPORTS)))

    def run(browser):
        results = []
        for url in urls:
            passes, notes = {}, []
            for pref in ("no-preference", "reduce"):
                sid = browser.page(viewport, motion=pref)
                browser.goto(sid, url, settle=False)
                raw = browser.call("Runtime.evaluate", {"expression": MOTION_JS, "awaitPromise": True,
                                                        "returnByValue": True}, session=sid)["result"].get("value")
                try:
                    passes[pref] = json.loads(raw)
                except (TypeError, ValueError):
                    raise DesignCheckError("motion probe gave no data for %s" % url)
            unread = passes["no-preference"].get("unreadable", 0)
            if unread:
                notes.append("%d stylesheet(s) not readable (cross-origin): their motion was NOT checked" % unread)
            notes.append("hover/focus transitions checked as CSS rules only; device feel stays a manual check")
            results.append((url, motion_findings(passes), notes))
        return results
    return _with_chrome(chrome, run)


def motion_report(results):
    """-> (text, rc): rc 1 when any FAIL."""
    lines, failed = [], False
    for url, found, notes in results:
        lines.append("== %s" % (url if len(url) < 100 else url[:97] + "..."))
        for lvl, where, msg in found:
            failed = failed or lvl == "FAIL"
            lines.append("%s %s: %s" % (lvl, where, msg))
        if not found:
            lines.append("motion: clean (transform/opacity only, nothing moves under reduced motion)")
        lines += ["note: " + n for n in notes]
    return "\n".join(lines), 1 if failed else 0


# ---- approve: current shots become the baseline, only on the user's word ----
def approve(current, baseline, user_word):
    import datetime
    import shutil
    if not (user_word or "").strip():
        raise DesignCheckError("approve needs the user's word: pass --user-word \"<what the user said>\". Baselines are "
                               "what the user approved by eye; never approve them yourself.")
    names = sorted(n for n in os.listdir(current) if n.lower().endswith(".png"))
    if not names:
        raise DesignCheckError("no PNG screenshots in %s" % current)
    os.makedirs(baseline, exist_ok=True)
    for n in names:
        shutil.copyfile(os.path.join(current, n), os.path.join(baseline, n))
    with open(os.path.join(baseline, "APPROVED.md"), "a", encoding="utf-8") as f:
        f.write("## %s\nUser's word: \"%s\"\nScreens: %s\n\n" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), user_word.strip(), ", ".join(names)))
    return names


# ---- detect: impeccable's detector when installed; otherwise UNVERIFIED, never a silent pass ----
IMPECCABLE = os.path.expanduser("~/.claude/skills/impeccable/scripts/impeccable")


def detect(targets):
    """-> (rc, text). rc 0 clean, 1 findings, 2 UNVERIFIED (not installed or could not scan)."""
    import subprocess
    exe = sl.env("IMPECCABLE") or IMPECCABLE
    if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        return 2, "UNVERIFIED: impeccable detector not installed (%s); design anti-patterns were not checked. Use lenses/design.md and the rubric (reference/design-rubric.md) instead." % exe
    try:
        r = subprocess.run([exe, "detect"] + list(targets), capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 2, "UNVERIFIED: impeccable detect timed out"
    out = (r.stderr + r.stdout).strip()
    if r.returncode == 0:
        return 0, "impeccable detect: clean\n" + out
    if r.returncode == 2:
        return 1, "impeccable detect: findings\n" + out
    return 2, "UNVERIFIED: impeccable detect could not scan (exit %d)\n%s" % (r.returncode, out)
