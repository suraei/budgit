"""Extended brain: typed [[links]] between markdown notes (P3 thin slice).

Read-only: lint + small graph queries. Never writes a note; the only file it writes is the
gitignored query log `.budgit/brain-queries.jsonl`. Syntax: skills/budgit-kickoff/reference/brain.md.
"""
import json
import os
import re
import time
import unicodedata
from collections import namedtuple

import studiolib as sl

VOCAB = ("supersedes", "depends_on", "derived_from", "implements", "supports", "contradicts")
IMPACT_TYPES = ("depends_on", "derived_from", "implements")
DEFAULT_ROOTS = ("research", "lessons", "docs", "radar")
EXEMPT = {"index", "readme"}
CAP = 1500

Edge = namedtuple("Edge", "type target line")
Link = namedtuple("Link", "target line")
Issue = namedtuple("Issue", "level rel line cls msg")

WIKI = re.compile(r"(!?)\[\[([^\[\]\n]*)\]\]")
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
BULLET = re.compile(r"^\s*[-*+]\s+(\S+)\s+\[\[")
DATAVIEW = re.compile(r"^\s*[\w-]+::\s")
FM_KEY = re.compile(r"^([\w-]+):\s*(.*)$")
FM_ITEM = re.compile(r"^\s+-\s+(.*)$")
# any indent (fences live inside list items too); a backtick info string may not hold backticks (CommonMark)
FENCE = re.compile(r"^\s*(?:(`{3,})[^`]*$|(~{3,}))")
QUOTE = re.compile(r"^(?:\s*>)+")   # fences inside blockquotes / Obsidian callouts
HRULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


class BrainError(Exception):
    pass


def norm(s):
    return unicodedata.normalize("NFC", s).strip().lower()


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _frontmatter(lines):
    """-> (dict, body_start, unclosed). Values are str or list of str."""
    if not lines or lines[0].strip() != "---":
        return None, 0, False
    for j in range(1, len(lines)):
        if lines[j].strip() in ("---", "..."):
            break
    else:
        return None, 1, True
    fm, key = {}, None
    for raw in lines[1:j]:
        m = FM_KEY.match(raw)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                fm[key] = [_unquote(x) for x in val[1:-1].split(",") if x.strip()]
            elif val:
                fm[key] = _unquote(val)
            else:
                fm[key] = []
            continue
        m = FM_ITEM.match(raw)
        if m and key and isinstance(fm.get(key), list):
            fm[key].append(_unquote(m.group(1)))
    return fm, j + 1, False


class Note:
    def __init__(self, repo, path):
        self.path = path
        self.rel = os.path.relpath(path, repo).replace(os.sep, "/")
        self.stem = os.path.splitext(os.path.basename(path))[0]
        self.key = norm(self.stem)
        self.edges, self.links, self.issues = [], [], []
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if text.startswith("\ufeff"):
            text = text[1:]
        text = HTML_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)   # keep line numbers
        lines = text.replace("\r\n", "\n").split("\n")
        fm, start, unclosed = _frontmatter(lines)
        self.fm = fm or {}
        if unclosed:
            self._issue("ERROR", 1, "unclosed-frontmatter", "frontmatter opened with --- but never closed")
        elif fm is None:
            self._issue("WARN", 1, "no-frontmatter", "no frontmatter")
        if not all(unicodedata.is_normalized("NFC", part) for part in self.rel.split("/")):
            self._issue("ERROR", 1, "non-nfc-name", "filename is not NFC (rename it; links break on other systems)")
        al = self.fm.get("aliases", [])
        al = [al] if isinstance(al, str) else list(al)
        if isinstance(self.fm.get("id"), str):
            al.append(self.fm["id"])
        self.aliases = sorted({norm(a) for a in al if a.strip()} - {self.key})
        st = self.fm.get("status")
        self.status = norm(st) if isinstance(st, str) else ""
        self.title = self.stem
        self._body(lines, start)

    def _issue(self, level, line, cls, msg):
        self.issues.append(Issue(level, self.rel, line, cls, msg))

    def _body(self, lines, start):
        fence, in_rel, seen_rel, titled = None, False, False, False
        blank, in_list, indented = True, False, False
        for i in range(start, len(lines)):
            line, n = lines[i], i + 1
            q = QUOTE.match(line)
            depth, bare = (q.group(0).count(">"), line[q.end():]) if q else (0, line)
            lead = len(bare.expandtabs(4)) - len(bare.expandtabs(4).lstrip(" "))
            if fence and depth < fence[1]:
                fence, blank = None, True   # the blockquote ended, and its fence with it (CommonMark)
            if fence:   # closes only with the same char, at least as long, at the same quote depth
                mark, room = fence[0], fence[2] + (4 if depth else 3)   # closer: ≤3 spaces past its container
                if depth == fence[1] and lead <= room and \
                        re.match(r"^\s*%s{%d,}\s*$" % (re.escape(mark[0]), len(mark)), bare):
                    fence, blank = None, True   # a new block starts: indented code may follow
                continue
            if not line.strip():
                blank = True
                continue
            deep = line.startswith("    ") or line.startswith("\t")
            if indented and deep:
                continue
            indented = deep and blank and not in_list      # indented code block (not a list continuation)
            blank = False
            if indented:                                   # a ``` here is code, not a fence
                continue
            if not (in_list and line[:1] in " \t"):       # indented lines continue the list item
                in_list = bool(LIST_ITEM.match(line))      # a quote or a fence line ends the list
            f = FENCE.match(bare)
            if f and (in_list or lead <= (4 if depth else 3)):   # 4+ spaces outside a list: not a fence
                fence = (f.group(1) or f.group(2), depth, lead if in_list else 0)
                continue
            if HRULE.match(bare):
                blank = True                       # a thematic break ends the block: indented code may follow
                continue
            line = INLINE_CODE.sub("", line)
            h = HEADING.match(line)
            if h:
                level = len(h.group(1))
                if level == 1 and not titled:
                    self.title, titled = h.group(2), True
                if level <= 2:
                    is_rel = level == 2 and h.group(2).strip().lower() == "relations"
                    if is_rel and seen_rel:
                        self._issue("ERROR", n, "duplicate-relations", "a second ## Relations heading (use one)")
                    seen_rel = seen_rel or is_rel
                    in_rel = is_rel
                blank, in_list = True, False   # a heading ends the block: indented code may follow
                continue
            if DATAVIEW.match(line):
                self._issue("WARN", n, "dataview-field", "Dataview `key::` field (not read by budgit brain)")
            links = list(WIKI.finditer(line))
            if not links:
                continue
            b = BULLET.match(line)
            typed = None
            if b:
                word = b.group(1)
                if word in VOCAB:
                    if in_rel:
                        typed = word
                    else:
                        self._issue("ERROR", n, "outside-relations",
                                    "`- %s [[...]]` outside ## Relations (move it there)" % word)
                elif in_rel:
                    self._issue("ERROR", n, "unknown-type",
                                "unknown relation `%s` (use: %s)" % (word, ", ".join(VOCAB)))
            for k, m in enumerate(links):
                raw = m.group(2)
                if m.group(1):
                    self._issue("ERROR", n, "embed", "embed ![[%s]] (use a plain [[link]])" % raw)
                if "#^" in raw:
                    self._issue("ERROR", n, "block-ref", "block reference [[%s]] (link the note)" % raw)
                target = raw.split("|")[0].split("#")[0].strip()
                if not target:
                    continue
                if typed and k == 0:
                    self.edges.append(Edge(typed, target, n))
                else:
                    self.links.append(Link(target, n))


def declared_roots(repo):
    try:
        with open(os.path.join(sl.studio_dir(repo), "project.json"), encoding="utf-8") as f:
            roots = (json.load(f).get("brain") or {}).get("roots")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(roots, str):
        roots = [roots]
    return [r for r in roots if isinstance(r, str)] if isinstance(roots, list) and roots else None


class Brain:
    def __init__(self, repo, roots):
        self.repo, self.roots = repo, roots
        self.notes, self.names, self.paths = {}, {}, {}
        found = []
        for root in roots:
            top = os.path.join(repo, root)
            for dirpath, dirs, files in os.walk(top):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if f.endswith(".md") and not f.startswith("."):
                        found.append(Note(repo, os.path.join(dirpath, f)))
        stems = {}
        for nt in found:
            stems.setdefault(nt.key, []).append(nt)
        for key, group in stems.items():
            for nt in group:
                if len(group) > 1:
                    nt.key = norm(os.path.splitext(nt.rel)[0])
                self.notes[nt.key] = nt
                self.paths[norm(os.path.splitext(nt.rel)[0])] = nt.key
        for nt in self.notes.values():
            for name in [norm(nt.stem)] + nt.aliases:
                self.names.setdefault(name, set()).add(nt.key)

    def resolve(self, target):
        """-> (key, None) | (None, 'unresolved' | 'ambiguous')."""
        t = norm(target)
        if t.endswith(".md"):
            t = t[:-3]
        keys = self.names.get(t)
        if not keys and "/" in t:
            keys = {k for p, k in self.paths.items() if p == t or p.endswith("/" + t)}
        if not keys:
            return None, "unresolved"
        if len(keys) > 1:
            return None, "ambiguous"
        return next(iter(keys)), None

    def find(self, name):
        key, err = self.resolve(name)
        if err == "ambiguous":
            raise BrainError("[[%s]] matches several notes; use [[folder/%s]]" % (name, name))
        if not key:
            raise BrainError("no note [[%s]] in %s" % (name, ", ".join(self.roots)))
        return key


def load(repo, roots=None):
    repo = os.path.realpath(repo)
    return Brain(repo, roots or declared_roots(repo) or list(DEFAULT_ROOTS))


def _supersedes(b):
    out = {}
    for nt in b.notes.values():
        for e in nt.edges:
            if e.type == "supersedes":
                key, _ = b.resolve(e.target)
                if key and key != nt.key:
                    out.setdefault(nt.key, []).append((key, e.line))
    return out


def _cycles(b, graph=None):
    """-> {key: line} for notes on a supersedes cycle (iterative DFS, never recursion-deep)."""
    graph = _supersedes(b) if graph is None else graph
    colour, on = {}, {}
    for start in graph:
        if colour.get(start):
            continue
        stack = [(start, iter(graph.get(start, ())))]
        path = [start]
        colour[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = 2
                stack.pop(); path.pop()
                continue
            tgt, line = nxt
            if colour.get(tgt) == 1:
                for k in path[path.index(tgt):]:
                    on.setdefault(k, graph[k][0][1])
            elif not colour.get(tgt):
                colour[tgt] = 1
                path.append(tgt)
                stack.append((tgt, iter(graph.get(tgt, ()))))
    return on


def lint(b, only=None):
    issues = []
    inbound = set()
    for nt in b.notes.values():
        issues.extend(nt.issues)
        for e in nt.edges:
            key, err = b.resolve(e.target)
            if err:
                issues.append(Issue("ERROR", nt.rel, e.line, err, "%s [[%s]]" % (e.type, e.target)))
                continue
            if key == nt.key:
                issues.append(Issue("ERROR", nt.rel, e.line, "self-edge", "%s points at itself" % e.type))
                continue
            inbound.add(key)
            if e.type == "supersedes" and b.notes[key].status != "superseded":
                issues.append(Issue("ERROR", nt.rel, e.line, "supersedes-status",
                                    "[[%s]] is superseded: set its `status: superseded`" % e.target))
        for l in nt.links:
            key, err = b.resolve(l.target)
            if err:
                issues.append(Issue("ERROR", nt.rel, l.line, err, "[[%s]]" % l.target))
            elif key != nt.key:
                inbound.add(key)
    for name, keys in b.names.items():
        if len(keys) > 1 and name not in EXEMPT:
            rels = sorted(b.notes[k].rel for k in keys)
            for k in keys:
                issues.append(Issue("ERROR", b.notes[k].rel, 1, "collision",
                                    "name `%s` is used by %s" % (name, ", ".join(rels))))
    for key, line in _cycles(b).items():
        issues.append(Issue("ERROR", b.notes[key].rel, line, "supersedes-cycle", "supersedes chain loops back"))
    for nt in b.notes.values():
        if norm(nt.stem) in EXEMPT or nt.edges or nt.links or nt.key in inbound:
            continue
        issues.append(Issue("WARN", nt.rel, 1, "orphan", "no links in or out"))
    if only:
        rel = os.path.relpath(os.path.realpath(only), b.repo).replace(os.sep, "/")
        issues = [i for i in issues if i.rel == rel]
    return sorted(set(issues), key=lambda i: (i.rel, i.line, i.cls))


def current(b, name):
    key = b.find(name)
    graph = _supersedes(b)
    by = {}
    for src, tgts in graph.items():
        for tgt, _ in tgts:
            by.setdefault(tgt, []).append(src)
    cyc = _cycles(b, graph)
    heads, seen, todo = set(), set(), [key]
    while todo:
        n = todo.pop()
        if n in cyc:
            raise BrainError("supersedes cycle through %s; run `budgit brain lint`" % b.notes[n].rel)
        if n in seen:
            continue
        seen.add(n)
        if by.get(n):
            todo.extend(by[n])
        else:
            heads.add(n)
    return sorted(heads)


def impact(b, name, hops=2):
    key = b.find(name)
    rev = {}
    for nt in b.notes.values():
        for e in nt.edges:
            if e.type in IMPACT_TYPES:
                tgt, _ = b.resolve(e.target)
                if tgt:
                    rev.setdefault(tgt, set()).add(nt.key)
    out, seen, level = [], {key}, [key]
    for _ in range(hops):
        nxt = sorted({s for n in level for s in rev.get(n, ())} - seen)
        out.extend(nxt); seen.update(nxt); level = nxt
    return out


def backlinks(b, name):
    key = b.find(name)
    out = set()
    for nt in b.notes.values():
        if nt.key == key:
            continue
        for e in nt.edges:
            if b.resolve(e.target)[0] == key:
                out.add((nt.key, e.type))
        for l in nt.links:
            if b.resolve(l.target)[0] == key:
                out.add((nt.key, None))
    return sorted(out, key=lambda x: (x[0], x[1] or ""))


def index(b, folder=None):
    lines, last = [], None
    pre = (folder.strip("/") + "/") if folder else ""
    for nt in sorted(b.notes.values(), key=lambda n: n.rel):
        if not nt.rel.startswith(pre):
            continue
        d = os.path.dirname(nt.rel)
        if d != last:
            lines.append("## %s" % d); last = d
        link = nt.stem if nt.key == norm(nt.stem) else os.path.splitext(nt.rel)[0]
        lines.append("- [[%s]] — %s" % (link, nt.title))
    return lines


def cap(lines, limit=CAP):
    out, size = [], 0
    for i, line in enumerate(lines):
        size += len(line.encode()) + 1
        if size > limit - 40:
            out.append("… %d more (narrow the query)" % (len(lines) - i))
            break
        out.append(line)
    return "\n".join(out)


def describe(b, key, extra=""):
    nt = b.notes[key]
    return "%s%s — %s" % (nt.title, extra, nt.rel)


def log_query(repo, sub, args, n, text, redirected=False):
    d = sl.studio_dir(repo)
    if not os.path.isdir(d):
        return
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
           "sub": sub, "args": list(args), "n": n, "bytes": len(text.encode()), "redirected": bool(redirected)}
    try:
        with open(os.path.join(d, "brain-queries.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def lint_report(issues, n_notes, verbose=False):
    errs = [i for i in issues if i.level == "ERROR"]
    warns = [i for i in issues if i.level == "WARN"]
    shown = errs + (warns if verbose else [])
    lines = ["%s:%d: %s %s — %s" % (i.rel, i.line, i.level, i.cls, i.msg) for i in shown]
    tail = "" if verbose or not warns else " (--verbose lists them)"
    lines.append("brain lint: %d notes, %d errors, %d warnings%s" % (n_notes, len(errs), len(warns), tail))
    return "\n".join(lines)


def _in_roots(repo, path, roots):
    rel = os.path.relpath(os.path.realpath(path), repo).replace(os.sep, "/")
    return not rel.startswith("../") and any(rel.startswith(r.strip("/") + "/") for r in roots)


def hook_errors(repo, path):
    """PostToolUse: errors of the one edited note, '' when silent (not a note, no brain.roots, clean)."""
    if not path or not path.endswith(".md"):
        return ""
    repo = os.path.realpath(repo)
    roots = declared_roots(repo)
    if not roots or not _in_roots(repo, path, roots) or not os.path.isfile(path):
        return ""
    errs = [i for i in lint(load(repo, roots), only=path) if i.level == "ERROR"]
    if not errs:
        return ""
    return "budgit brain: %s has %d link error(s) — fix before moving on:\n%s" % (
        errs[0].rel, len(errs), cap(["%s:%d: %s — %s" % (i.rel, i.line, i.cls, i.msg) for i in errs], 800))


def session_pointer(repo):
    roots = declared_roots(os.path.realpath(repo))
    if not roots:
        return ""
    return ("brain (%s): before trusting an old note run `budgit brain current <note>`; "
            "`impact`/`backlinks` for what depends on it (reference/brain.md)" % " ".join(roots))
