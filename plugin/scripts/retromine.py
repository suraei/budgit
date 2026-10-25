"""budgit retro-mine — the user's corrections, mined from Claude Code transcripts. Offline, zero model tokens.

Reads ~/.claude/projects/<dir>/<session>.jsonl (the same discovery as `comply`), finds the user's prompts, and writes a
PRIVATE report (.budgit/private/, git-ignored, never exported). The report proposes; the user approves; only then does
anything become a lesson.
"""
import calendar
import math
import json
import os
import re
import time
import unicodedata
from collections import Counter, OrderedDict

import comply
import studiolib as sl

# harness wrappers that arrive as user records but are not the user's words
_WRAPPER = re.compile(r"^\s*<(command-|bash-|local-command|task-notification|side-sessions-event|system-reminder)", re.I)


def _records(path):
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def _text_of(rec):
    """The user's text: a string, or the `text` blocks of list content (screenshots come as text + image)."""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def is_user_prompt(rec):
    if rec.get("type") != "user" or rec.get("isSidechain") or "toolUseResult" in rec:
        return False
    # programmatic runs (SDK scripts, automated reviews) are not the user's typing, whatever their `origin` says
    if str(rec.get("entrypoint", "")).startswith("sdk") or rec.get("turnOrigin") == "sdk":
        return False
    origin = rec.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        if origin["kind"] != "human":
            return False
    elif rec.get("isMeta") or rec.get("isCompactSummary"):
        return False
    if rec.get("isCompactSummary"):
        return False
    text = _text_of(rec).strip()
    return bool(text) and not _WRAPPER.match(text)


def _epoch(ts):
    try:
        return float(calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0.0


_COMMAND = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
# skills that put design judgement in front of the work: none of them before a design miss = a gap
DESIGN_SKILLS = frozenset(("impeccable", "frontend-design", "design-taste-frontend", "high-end-visual-design",
                           "web-design-guidelines", "emil-design-eng", "design-critique", "design-system",
                           "accessibility-review", "ux-copy"))


def _skill_name(name):
    return str(name).strip().lstrip("/").rsplit(":", 1)[-1]


def _blank_window():
    return {"skills": [], "agents": [], "models": Counter(), "tools": 0}


def _before(win):
    model = win["models"].most_common(1)[0][0] if win["models"] else ""
    return {"skills": win["skills"], "agents": win["agents"], "model": model, "tools": win["tools"]}


def _absorb(win, rec):
    """Fold one non-prompt record into the window of work since the user's previous prompt."""
    if rec.get("type") == "user":
        text = _text_of(rec)
        m = _COMMAND.search(text) if text.lstrip().startswith("<command-") else None
        if m and not rec.get("isSidechain"):
            win["skills"].append(_skill_name(m.group(1)))
        return
    if rec.get("type") != "assistant" or rec.get("isSidechain"):
        return
    msg = rec.get("message") or {}
    content = msg.get("content")
    if not isinstance(content, list):
        return
    if msg.get("model"):
        win["models"][msg["model"]] += 1
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_use"):
            continue
        win["tools"] += 1
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        if block.get("name") == "Skill" and inp.get("skill"):
            win["skills"].append(_skill_name(inp["skill"]))
        elif block.get("name") in ("Agent", "Task"):
            win["agents"].append((inp.get("subagent_type") or "general-purpose", inp.get("model") or ""))


def prompts(path):
    """The user's prompts in a session, in order: dicts with session, ts, epoch, text and `before` — what the team had
    done since the user's previous prompt (skills, agents with model, main model, tool-call count)."""
    sid = os.path.basename(path)[:-6]
    win = _blank_window()
    for rec in _records(path):
        if is_user_prompt(rec):
            yield {"session": sid, "ts": rec.get("timestamp", ""), "epoch": _epoch(rec.get("timestamp")),
                   "text": _text_of(rec).strip(), "before": _before(win)}
            win = _blank_window()
        else:
            _absorb(win, rec)


# Classification table: category -> regexes over lowercased, accent-stripped text (so write them without accents).
# Generic phrases only (it ships in the plugin export): never quote a real sentence from the user here.
# An entry is a regex, or a tuple of regexes that must ALL match (a visual word AND a complaint word).
_COMPLAINT = r"\b(mal|raro|rara|feo|fea|horrible|roto|rota|wrong|bad|ugly|broken|cutre|generic[oa]?)\b"
CATEGORIES = OrderedDict([
    ("design", [
        r"no me gusta", r"\bfe[oa]s?\b", r"\bugly\b", r"se ve(n)? (mal|raro|rara|horrible|feo|fea|generic)",
        r"looks? (bad|off|ugly|wrong|generic|cheap|weird)", r"no (se )?convence",
        r"no (queda|quedan|se ve|se ven|encaja|encajan)( bien)?\b",
        (r"\b(colou?r(es)?|tipograf\w*|fuentes?|font|spacing|espaciados?|alinea\w*|alignment|contraste|contrast|"
         r"layout|diseno|design)\b", _COMPLAINT)]),
    ("redo", [
        r"\b(hazlo|hazla|haz esto|hacerlo|hacerla) (de nuevo|otra vez)", r"vuelve a (hacer|empezar|intentar)",
        r"\brehaz\b", r"\brehace\b", r"\bredo\b", r"(do|try) (it|this|that) again", r"start over",
        r"empieza de cero", r"desde cero", r"descarta (eso|todo|esto)",
        r"no es (lo que|esto lo que) (pedi|queria)", r"that'?s not what i (asked|wanted)"]),
    ("bug", [
        r"(?<!que )(?<!si )(?<!saber )no funciona", r"no sirve", r"no carga", r"no abre", r"se rompe", r"se ha roto", r"\bcrash\w*",
        r"\bbroken\b", r"(does ?n'?t|not|stopped) work(ing)?", r"\bfalla\b",
        r"(da|dan|sale|salen|tira|me sale|hay|gives|throws|shows) (un |el |este |an? )?error",
        r"sigue (fallando|igual)"]),
    ("clarity", [
        r"no (lo )?entiendo", r"no me queda claro", r"no se entiende", r"\bconfus(o|a|os|as|ing|ed)\b",
        r"explicalo mas simple", r"en cristiano", r"\bunclear\b", r"i don'?t understand", r"what does .* mean",
        r"too (complex|complicated|technical)", r"muy tecnic\w*"]),
    ("should-have", [
        r"ya te (lo )?(dije|habia dicho|explique|comente|conte|pedi)", r"te lo (dije|habia dicho|explique)",
        r"como (te )?(dije|comente|explique)", r"deberias haber", r"tenias que", r"se te (olvido|paso)",
        r"olvidaste", r"i (already )?(told|said|explained)", r"you should have", r"you forgot", r"as i said",
        r"otra vez te (lo )?tengo que"]),
])
CLASSIFY_CHARS = 300   # a correction states itself up front; what follows a long paste is pasted material
_COMPILED = OrderedDict(
    (cat, [tuple(re.compile(p) for p in ((e,) if isinstance(e, str) else e)) for e in entries])
    for cat, entries in CATEGORIES.items())


def _norm(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def classify(text):
    """Categories a prompt falls in (a prompt can be in several), in table order."""
    t = _norm(text[:CLASSIFY_CHARS])
    return [cat for cat, entries in _COMPILED.items() if any(all(r.search(t) for r in e) for e in entries)]


def snippet(text, width=160):
    """Redact the WHOLE prompt first, then cut: a credential straddling the cut can never leak a fragment."""
    one = " ".join(sl.redact(text).split())
    return one if len(one) <= width else one[:width].rstrip() + "\u2026"


def mine(root, days=7, project=None):
    """Per project dir: its sessions and the user's prompts."""
    out = OrderedDict()
    for path, d in comply.sessions(root, days, project):
        row = out.setdefault(d, {"sessions": 0, "prompts": [], "seen": set()})
        row["sessions"] += 1
        for p in prompts(path):
            key = (p["ts"], p["text"])   # a resumed session copies the earlier history: count each prompt once
            if key not in row["seen"]:
                row["seen"].add(key)
                row["prompts"].append(dict(p, dir=d))
    return out


REPEAT_JACCARD = 0.6
REPEAT_MIN_TOKENS = 6
REPEAT_GAP_S = 600
_STOP = frozenset("""
a al algo ante aqui as asi aun cada como con cual cuando de del desde donde el ella ellos en entre era es esa ese eso
esta estaba estan este esto fue ha han hay la las le les lo los me mi mis mucho muy ni no nos nuestro o os otra otro para
pero por porque que quien se ser si sin sobre su sus te tiene tu tus un una uno unos y ya yo
the and for are but not you all any can had was one our out has his how its may new now old see two who did get let
too use that this with have from they will your what when make like then them than into just also some been were
""".split()) | {"her", "she"}  # voice-ok: English stopwords
_WORD = re.compile(r"[a-z0-9_]{3,}")


def _tokens(text):
    return frozenset(w for w in _WORD.findall(_norm(text)) if w not in _STOP)


def _pairs(items):
    """Exact Jaccard >= REPEAT_JACCARD pairs over token sets, via prefix filtering (rare tokens first): only
    prompts that share a token inside their prefixes are ever compared. items: [(tokens, ref)]."""
    df = Counter(t for toks, _ in items for t in toks)
    order = [sorted(toks, key=lambda t: (df[t], t)) for toks, _ in items]
    index, out = {}, []
    for i, (toks, _) in enumerate(items):
        n = len(toks)
        prefix = order[i][:n - math.ceil(REPEAT_JACCARD * n) + 1]
        seen = set()
        for t in prefix:
            for j in index.get(t, ()):
                if j in seen:
                    continue
                seen.add(j)
                other = items[j][0]
                if min(n, len(other)) < REPEAT_JACCARD * max(n, len(other)):
                    continue
                inter = len(toks & other)
                if inter / (n + len(other) - inter) >= REPEAT_JACCARD:
                    out.append((j, i))
            index.setdefault(t, []).append(i)
    return out


def repeats(mined):
    """Asks the user made twice. Within a session: 10+ minutes apart. Across sessions of one project dir: listed apart.
    Only prompts with REPEAT_MIN_TOKENS meaningful tokens count (short ones like 'again' repeat by nature)."""
    same, cross = [], []
    for d, row in mined.items():
        items = [(toks, p) for p in row["prompts"] for toks in (_tokens(p["text"]),) if len(toks) >= REPEAT_MIN_TOKENS]
        for a, b in _pairs(items):
            pa, pb = items[a][1], items[b][1]
            if pa["ts"] == pb["ts"]:
                continue
            pair = {"dir": d, "first": pa, "second": pb}
            if pa["session"] == pb["session"]:
                if pa["epoch"] and pb["epoch"] and pb["epoch"] - pa["epoch"] > REPEAT_GAP_S:   # 0 = unreadable ts
                    same.append(pair)
            else:
                cross.append(pair)
    return {"same_session": same, "cross_session": cross}


def hits(mined):
    """The user's prompts that fall in at least one category, newest last: dicts with dir, session, ts, cats, text."""
    out = []
    for row in mined.values():
        for p in row["prompts"]:
            cats = classify(p["text"])
            if cats:
                out.append(dict(p, cats=cats))
    out.sort(key=lambda h: h["epoch"])
    return out


def design_gaps(mined):
    """Design corrections with no design skill (Skill tool or slash command) since the user's previous prompt."""
    return [h for h in hits(mined) if "design" in h["cats"] and not DESIGN_SKILLS & set(h["before"]["skills"])]


def render(mined, days, recent=5):
    lines = ["# retro-mine %s (last %d days)" % (time.strftime("%Y-%m-%d"), days), "",
             "PRIVATE: built from your transcripts. Git-ignored, never exported, never pasted into retro.md "
             "(counts only there). Snippets are redacted; this report proposes, you approve.", "",
             "## Prompts per project", "", "| project dir | sessions | your prompts |", "|---|---|---|"]
    total = Counter()
    for d, row in mined.items():
        lines.append("| %s | %d | %d |" % (d, row["sessions"], len(row["prompts"])))
        total["sessions"] += row["sessions"]
        total["prompts"] += len(row["prompts"])
    lines.append("| **total** | %d | %d |" % (total["sessions"], total["prompts"]))
    found = hits(mined)
    grid = {cat: Counter() for cat in CATEGORIES}
    for h in found:
        for cat in h["cats"]:
            grid[cat][h["dir"]] += 1
    dirs = list(mined)
    lines += ["", "## Corrections per category", "", "| category | total | " + " | ".join(dirs) + " |",
              "|---|---|" + "---|" * len(dirs)]
    for cat in CATEGORIES:
        lines.append("| %s | %d | %s |" % (cat, sum(grid[cat].values()), " | ".join(str(grid[cat][d]) for d in dirs)))
    lines += ["", "## Most recent per category (redacted, cut at 160 chars)"]
    for cat in CATEGORIES:
        mine_ = [h for h in found if cat in h["cats"]][-recent:][::-1]
        lines += ["", "### %s" % cat]
        lines += ["- %s `%s` %s" % (h["ts"][:10], h["session"][:8], snippet(h["text"])) for h in mine_] or ["- (none)"]
    design = [h for h in found if "design" in h["cats"]]
    gaps = design_gaps(mined)
    lines += ["", "## What came before a correction", "",
              "| category | corrections | with any skill before | with any agent before | no tools at all |",
              "|---|---|---|---|---|"]
    for cat in CATEGORIES:
        hs = [h for h in found if cat in h["cats"]]
        lines.append("| %s | %d | %d | %d | %d |" % (
            cat, len(hs), sum(1 for h in hs if h["before"]["skills"]), sum(1 for h in hs if h["before"]["agents"]),
            sum(1 for h in hs if not h["before"]["tools"])))
    models = Counter(h["before"]["model"] or "?" for h in found)
    lines += ["", "Main model before a correction: " + (", ".join("%s %d" % kv for kv in models.most_common()) or "-"),
              "", "## Design corrections with no design skill before", "",
              "%d of %d design corrections (design skills: %s)" % (len(gaps), len(design), ", ".join(sorted(DESIGN_SKILLS)))]
    lines += ["- %s `%s` %s" % (h["ts"][:10], h["session"][:8], snippet(h["text"])) for h in gaps[-recent:][::-1]]
    rep = repeats(mined)
    lines += ["", "## Said twice", "", "same session: %d \u00b7 across sessions of one project: %d"
              % (len(rep["same_session"]), len(rep["cross_session"]))]
    for title, key in (("Same session (10+ minutes apart)", "same_session"), ("Across sessions", "cross_session")):
        lines += ["", "### " + title]
        lines += ["- %s `%s` then %s `%s`: %s" % (r["first"]["ts"][:10], r["first"]["session"][:8],
                                                 r["second"]["ts"][:10], r["second"]["session"][:8],
                                                 snippet(r["second"]["text"])) for r in rep[key][-recent:][::-1]] or ["- (none)"]
    return "\n".join(lines) + "\n"


def ensure_ignored(project):
    """Add `<data dir>/private/` (.budgit, or .studio before migrating) to the project's .gitignore when missing."""
    path = os.path.join(project, ".gitignore")
    try:
        with open(path, encoding="utf-8") as f:
            cur = f.read()
    except OSError:
        cur = ""
    private_ignore = os.path.basename(sl.studio_dir(project)) + "/private/"
    if private_ignore in [ln.strip() for ln in cur.splitlines()]:
        return False
    sl.atomic_write(path, cur + ("" if not cur or cur.endswith("\n") else "\n") + private_ignore + "\n")
    return True


def default_out(project):
    return os.path.join(sl.studio_dir(project), "private", "retro-mine-%s.md" % time.strftime("%Y-%m-%d"))
