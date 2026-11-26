"""R-1333 v1b: ONE order for the Budgit ledger (pure functions, no writes). No view calls it yet (the switch is a later step).

order(data, at, inbox) is the one outcome function; a view or the dry run only renders its dict.
  actionable   status open, not pinned, not waiting on the user, not blocked
  key          (tier, -urgent, -score, -late days, id): what the item is and how much it hurts the user, never time
  tier         0 = class A (a fix) or the user's urgency; 1 = unknown class; 2 = class B (new work)
  urgent       the user said so ABOUT THIS ITEM: an origin or directive word (i18n key ledger.plan_kw_urgent), short or about this
               item alone; or a hand due plus such a word naming a date. A link or a word in a long multi-item message is not enough
  score        type*10 + min(user words, 3)*3 + min(unblocks, 5)*2 + release*5
  type         3 = a fix for something the user sees (class A and the EXPLICIT flag "visible"; no word and no
               finding is read); 2 = an open correctness, security or privacy finding, or class A; 1 = performance; 0 = the rest. Only
               open findings count
  inherit      a blocker takes the best tier, urgent flag, type, score and late days of everything it blocks (fixed point, cycle safe)
  head of 10   key slots, one reserved slot for the newest class B ask of the user when the open class A count is not falling week
               over week, then rotation slots (oldest untouched first) so nothing is forgotten; age never enters the key, only the rotation
  sections     pinned > doing > waiting > blocked > head > parked > rest: every live item sits in exactly one
Reads only; the module never writes the ledger, the inbox or a status."""
import argparse
import datetime
import json
import math
import os
import re
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # python3 -I does not put the script folder on the path

import i18n
import studiolib as sl
from ledgergraph import ITEM_ID

WEIGHTS = {"type": 10, "user": 3, "unblocks": 2, "release": 5}   # late is a tie-break key, never added to the score
HER_CAP = 3
UNBLOCKS_CAP = 5
TOUCH_KINDS = ("launch", "handback", "merge", "verify", "done", "state")     # note, finding and retext never touch
LAUNCH_RUN_COUNTS = 2           # a trailing run of launch-only touches: the first two reset age, the rest do not
USER_WORD_EXCLUDED_HEADS = sl.USER_WORD_EXCLUDED_HEADS
USER_SOURCES = ("prompt", "answer")
AGE_CAP = 30
LATE_CAP_DAYS = 7
TREND_DAYS = 7
HEAD_SIZE = 10
ROTATION_MIN, ROTATION_MAX = 1, 4
ACTION_BUILD, ACTION_ASK, ACTION_FINISH = "build or fold", "ask the user", "finish, release or unblock"
OPEN = ("open", "doing")
INF = float("inf")
URGENT_KEY, DATE_KEY = "ledger.plan_kw_urgent", "ledger.plan_kw_date"
STOPS_KEY = "ledger.plan_kw_stops_work"             # the user's words that say a visible bug stops the work
REASON_KEYS = ("ledger.order_why_seen", "ledger.order_why_fix", "ledger.order_why_perf", "ledger.order_why_urgent",
               "ledger.order_why_asked", "ledger.order_why_unblocks", "ledger.order_why_release", "ledger.order_why_new",
               "ledger.order_why_unknown", "ledger.order_why_rotation", "ledger.order_why_rotation_unknown",
               "ledger.order_why_reserved", "ledger.order_why_late", "ledger.order_why_late_one",
               "ledger.order_why_rotation_one", "ledger.order_why_inherit", "ledger.order_why_stops", "ledger.order_why_inferred", "ledger.order_why_dated", "ledger.order_why_pinned")
FIX_FINDINGS = ("correctness", "security", "privacy")
_APPROVED = re.compile(r"\b(?:spec|mock|mockup|approved|aprobad[oa])\b", re.I)
_RX = {}


def _words(key):
    """The words of every language for `key`, from the i18n catalog (never written here), as one alternation."""
    return "|".join("(?:%s)" % i18n.CATALOG[lang][key] for lang in i18n.LANGS)


def _rx(key):
    if key not in _RX:
        _RX[key] = re.compile(r"(?<![\w'])(?:%s)(?![\w'])" % _words(key), re.I)
    return _RX[key]


def _ts(value):
    """Epoch seconds of a stamp; a malformed one (or none) is None, never a guess."""
    return sl._epoch(value)


def is_user_word(entry):
    """The user's own words: source prompt or answer, not a harness injection, not a shell or slash-command capture."""
    if not isinstance(entry, dict) or entry.get("source") not in USER_SOURCES:
        return False
    text = entry.get("text")
    if not isinstance(text, str) or sl.is_harness(text):
        return False
    return not sl._marker_head(text).casefold().startswith(tuple(h.casefold() for h in USER_WORD_EXCLUDED_HEADS))


def _names(text, item_id):
    return isinstance(text, str) and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(item_id), text) is not None


def _dicts(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def last_touch(item):
    """Epoch of the newest touch, or None. Touches: created_at/created, events of TOUCH_KINDS (a launch only when its text names
    this id, and in a trailing run of three or more launches only the first LAUNCH_RUN_COUNTS count), directives, changes,
    doing_since, released. Never updated, rank, notes, findings, retexts or triage edits."""
    stamps = [item.get("created_at"), item.get("created"), item.get("doing_since")]
    touches = []
    for e in _dicts(item.get("events")):
        kind = e.get("kind")
        if kind in TOUCH_KINDS and (kind != "launch" or _names(e.get("text"), item.get("id", ""))) and _ts(e.get("at")) is not None:
            touches.append((_ts(e.get("at")), kind))
    touches.sort()
    tail = 0
    for _, kind in reversed(touches):
        if kind != "launch":
            break
        tail += 1
    if tail > LAUNCH_RUN_COUNTS:
        touches = touches[:len(touches) - tail + LAUNCH_RUN_COUNTS]
    stamps += [d.get("at") for d in _dicts(item.get("directives"))]
    stamps += [c.get("at") for c in _dicts(item.get("changes"))]
    if isinstance(item.get("released"), dict):
        stamps.append(item["released"].get("at"))
    found = [t for t in map(_ts, stamps) if t is not None] + [t for t, _ in touches]
    return max(found) if found else None


# "question"="label" in the card's plain-text answer. A chain «"q" = "x" = "label"» is ONE pair whose value is the LAST quoted string, so
# a question that holds `x" = "urgent` cannot pass its own words off as the answer (security review L2).
_ANSWER_PAIR = re.compile(r'((?:"(?:[^"\\]|\\.)*"\s*=\s*)+)"((?:[^"\\]|\\.)*)"')
ANSWER_PREFIX = "AskUserQuestion answer:"
USER_TEXT_MAX = 20000                       # characters of one inbox entry read as the user's words, before any pattern runs (security review L1)


def user_words(entry):
    """THE one reader of the user's own words in an inbox entry (R-1333 round 7); every reader of inbox text as the user's words
    goes through it. A prompt: its text. A card answer: ONLY the values of the answers (the labels chosen and any typed
    «Other» text), never the question, the header or the option descriptions, which the assistant wrote. None when the entry is
    not the user's words or an answer holds no value."""
    if not is_user_word(entry):
        return None
    text = entry["text"][:USER_TEXT_MAX]
    if entry.get("source") != "answer":
        return text
    body = text[len(ANSWER_PREFIX):].strip() if text.startswith(ANSWER_PREFIX) else text.strip()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if isinstance(data, dict):                  # the JSON form is used alone when it is there: the plain-text pairs are never read then
        answers = data.get("answers")
        values = []
        for v in (answers.values() if isinstance(answers, dict) else ()):
            values += [x for x in v if isinstance(x, str)] if isinstance(v, list) else [v] if isinstance(v, str) else []
    elif sl.REJECTION_MARKER in body:           # a refused card: what the user typed after the marker
        values = [body[body.index(sl.REJECTION_MARKER) + len(sl.REJECTION_MARKER):]]
    else:
        values = [m.group(2) for m in _ANSWER_PAIR.finditer(body)]
    return " | ".join(v.strip() for v in values if v.strip()) or None


def user_index(inbox):
    """{I-n: epoch or None} for the user's words only."""
    return {e["id"]: _ts(e.get("ts")) for e in inbox or () if isinstance(e, dict) and isinstance(e.get("id"), str) and user_words(e) is not None}


def user_texts(inbox):
    """{I-n: text} for the user's words only (see user_words: an answer is only what was chosen or typed)."""
    out = {}
    for e in inbox or ():
        words = user_words(e) if isinstance(e, dict) and isinstance(e.get("id"), str) else None
        if words is not None:
            out[e["id"]] = words
    return out


_QUOTE_PAIRS = {"«": "»", '"': '"', "“": "”", "`": "`"}      # a quoted block is somebody else's text


def _strip_quoted(text):
    """Every quoted block replaced by a space, in ONE pass (security review L1: the old pattern grew with the square of the length
    on many unclosed marks). An opener with no closer left can never match later, so that kind is not searched again."""
    out, i, n, dead = [], 0, len(text), set()
    while i < n:
        ch = text[i]
        close = _QUOTE_PAIRS.get(ch)
        if close is not None and ch not in dead:
            j = text.find(close, i + 1)
            if j == -1:
                dead.add(ch)
            else:
                out.append(" ")
                i = j + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")
_PASTE = re.compile(r"->|→")


def user_statements(entry):
    """What the user STATES in an entry (R-1333 round 8): user_words minus what is not the user's own statement. Dropped: every
    sentence that is a question (it holds «¿» or ends with «?»), everything after «->» (text pasted as an example), quoted
    blocks, «>» lines and lines that repeat a Budgit message (they start with «budgit:»). Used for the stops-work check and
    the urgency check, so a phrase in a question or in a pasted message never counts. None when the entry is not the user's words."""
    text = user_words(entry)
    if text is None:
        return None
    text = _PASTE.split(text)[0]
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">") and not ln.lstrip().lower().startswith("budgit:")]
    text = _strip_quoted("\n".join(lines))
    kept = [s for s in (m.group().strip() for m in _SENTENCE.finditer(text)) if s and "¿" not in s and not s.endswith("?")]
    return " ".join(kept)


def user_statement_texts(inbox):
    """{I-n: what the user states} (see user_statements)."""
    out = {}
    for e in inbox or ():
        said = user_statements(e) if isinstance(e, dict) and isinstance(e.get("id"), str) else None
        if said is not None:
            out[e["id"]] = said
    return out


def _refs(item):
    """The I-n an item is LINKED to: its src list, the src of its directives and of its changes. An id quoted in the title, the text,
    a note or a change's words is never a link (R-758 took I-2109 from a technician note that quoted it)."""
    refs = set()
    for s in item.get("src") or ():
        if isinstance(s, str):
            refs.add(s)
    for d in _dicts(item.get("directives")):
        src = d.get("src")
        refs.update([src] if isinstance(src, str) else [s for s in src or () if isinstance(s, str)])
    for c in _dicts(item.get("changes")):
        if isinstance(c.get("src"), list):
            refs.update(s for s in c["src"] if isinstance(s, str))
    return refs


def her_word_ids(item, words, kids=()):
    """The DISTINCT ids of the user's words reached by the item and by every item folded into it (src, directive src, change src;
    never an id quoted in text or notes). A set of ids: an id reached twice (parent and fold) counts once."""
    return {ref for it in [item] + list(kids) for ref in _refs(it) if ref in words}


def user_times(item, words, kids=()):
    """-> (linked, [epoch]): the user's words reached by the item and its folds (one epoch per distinct id)."""
    ids = her_word_ids(item, words, kids)
    return bool(ids), [t for t in (words[i] for i in ids) if t is not None]


URGENT_MAX_CHARS = 400          # a longer message counts only when it is the origin of one item (it is about that item alone)


def origin_ids(item, words=None, directives=True):
    """The I-n that say what this item IS: its origin (first src) and, with `directives`, the src of a directive on it. Not a
    later link, a quote in the text, or a fold. With `words`, only the user's own words."""
    src = item.get("src")
    ids = {src[0]} if isinstance(src, list) and src and isinstance(src[0], str) else set()
    if directives:
        for d in _dicts(item.get("directives")):
            s = d.get("src")
            ids.update([s] if isinstance(s, str) else [x for x in s or () if isinstance(x, str)])
    return ids if words is None else {i for i in ids if i in words}


def origin_reach(items):
    """{I-n: how many items it is the origin of}: a message that starts many items is not about any one of them."""
    reach = {}
    for it in items:
        for i in origin_ids(it):
            reach[i] = reach.get(i, 0) + 1
    return reach


def _said(texts, reach, key, ids, stmts=None):
    """True when one of the user's words in `ids` (short, or the origin of this item alone) matches the i18n word list `key`.
    The length is the whole message; the match runs over `stmts` (what the user states, see user_statements) when given."""
    stated = texts if stmts is None else stmts
    return any(_rx(key).search(stated.get(i, "")) for i in ids if len(texts.get(i, "")) <= URGENT_MAX_CHARS or reach.get(i, 0) <= 1)


NEAR_DUE_DAYS = 2              # a hand due lifts to tier 0 only when it is at most this many days away (or already past)


def is_urgent(item, origin, texts, reach, today=None, stmts=None):
    """-> (phrase, dated, lift). `phrase` = the user's explicit urgency phrase; `dated` = a hand due plus a date word of the user; urgent = either. Phrase: the user said so ABOUT THIS ITEM: an origin or directive word (never a mere link) that matches
    ledger.plan_kw_urgent, and is short (<= URGENT_MAX_CHARS) or the origin of this item alone. An explicit phrase lifts the item
    to tier 0 whatever its class. A HAND due (ledger due stores no author, so auto-due never counts) counts only together with
    such a word that names a date: it is urgent first inside its own tier, and lifts to tier 0 only when the due date is at most
    NEAR_DUE_DAYS away or past."""
    about = [i for i in origin if len(texts.get(i, "")) <= URGENT_MAX_CHARS or reach.get(i, 0) <= 1]
    stated = texts if stmts is None else stmts      # the words match over what the user states (never a question or pasted text)
    rx = _rx(URGENT_KEY)
    if any(rx.search(stated.get(i, "")) for i in about):
        return True, False, True
    if item.get("due") and not item.get("due_auto") and any(_rx(DATE_KEY).search(stated.get(i, "")) for i in about):
        try:
            near = (datetime.date.fromisoformat(item["due"]) - (today or datetime.date.today())).days <= NEAR_DUE_DAYS
        except (TypeError, ValueError):
            near = False
        return False, True, near
    return False, False, False


def visible_source(item, classes=None):
    """Why an item counts as a fix for something the user sees (R-1333 round 11): the ONE explicit mark, never a guess from words or
    from a finding. "flag" = the item's flags hold "visible" (`budgit ledger flag R-n visible`); None otherwise. An open ux finding is
    not proof of something on the user's screen and gives no type 3. `classes` is unused, kept so callers need no change."""
    return "flag" if "visible" in (item.get("flags") or ()) else None


def item_type(item, cls):
    """3 = a fix for something the user sees: class A AND the explicit flag "visible"; no word, quote, statement, area or finding is read. 2 = a correctness, security or privacy fix (an open finding) or class A; 1 = performance; 0 = the rest."""
    import findings
    classes = {f.get("class") for f in findings.rows(item) if f.get("state") == "open"}
    if cls == "A" and visible_source(item, classes):
        return 3
    if cls == "A" or classes & set(FIX_FINDINGS):
        return 2
    return 1 if "performance" in classes else 0


def late_days(item, today, capped=True):
    """Days late, capped at LATE_CAP_DAYS for the key (`capped=False`: the true days, for the reason line). Small or medium: any
    due (auto or hand); large: only a hand due (R-925, I-1484). A tie-break only: it never makes an item urgent."""
    try:
        gap = (today - datetime.date.fromisoformat(item.get("due"))).days
    except (TypeError, ValueError):
        return 0
    if gap <= 0:
        return 0
    size = item.get("appetite")
    if size in sl.DUE_DAYS or (size == "large" and not item.get("due_auto")):
        return min(gap, LATE_CAP_DAYS) if capped else gap
    return 0


def rotation_slots(beyond):
    return min(max(math.ceil(beyond / AGE_CAP), ROTATION_MIN), ROTATION_MAX)


def _idkey(item_id):
    m = re.match(r"R-(\d+)$", str(item_id))
    return (int(m.group(1)) if m else 10 ** 12, str(item_id))


def _age_key(row):
    """Oldest first. An item with no valid stamp at all is the OLDEST (never the newest): nothing proves it was touched, so it
    rotates into the head like any old item and is never forgotten."""
    return (row["last_touch"] if row["last_touch"] is not None else -INF, _idkey(row["id"]))


def _key(row):
    """Inside a tier (the user's card I-2136, «según cada caso»): 0 a bug the user sees that stops the work, 1 the explicit urgency
    phrase in any class (urgent new work included), 2 a bug the user sees (type 3), 3 other class A fixes by type, 4 date urgency,
    5 the rest; then the score. Urgency never decays: nothing here reads an age."""
    if row["stops"]:
        group = 0
    elif row["phrase"]:
        group = 1
    elif row["type"] == 3:
        group = 2
    elif row["class"] == "A":
        group = 3
    else:
        group = 4 if row["dated"] else 5
    return (row["tier"], group, -row["type"], -row["score"], -row["late_days"], _idkey(row["id"]))


def _old(row):
    return row["untouched_days"] is None or row["untouched_days"] > AGE_CAP


def _closed_at(it):
    folded = it.get("folded") if isinstance(it.get("folded"), dict) else {}
    return _ts(it.get("done_at") or folded.get("at") or it.get("updated"))


def open_a_trend(items, at, lp):
    """-> (open class A now, open class A a week ago). Not falling = now >= before."""
    cut = at - TREND_DAYS * 86400
    now = before = 0
    for it in items:
        if it.get("status") in OPEN:
            if lp.item_class(it) != "A":
                continue
            now += 1
        else:
            closed = _closed_at(it)
            if closed is None or closed <= cut or lp.item_class(it) != "A":
                continue
        created = _ts(it.get("created_at") or it.get("created"))
        if created is None or created <= cut:
            before += 1
    return now, before


def _row(it, facts, kids, p50, cls):
    return dict(facts, id=it["id"], status=it["status"], title=it.get("title") or sl.oneline(it.get("text", ""), 60), p50=p50,
                score=0.0, n=0.0, late_days=0, cost=sl.appetite_days(it), folded=len(kids), area=it.get("area"),
                appetite=it.get("appetite"), phase=it.get("phase"), **{"class": cls})


def order(data, at=None, inbox=None, today=None):
    """The one outcome function. -> {pinned, head, doing, waiting, blocked, parked, rest, done_awaiting_verify, counts, basis,
    skipped, oldest_untouched_days, untouched_beyond_cap, rotation_slots, rotation_bound_days, rotation_alarm, unknown_in_head,
    urgent_outside_head, daily}; rows hold {id, status, title, class, tier, urgent, type, her_words, unblocks, release, score,
    p50 (shown, never keyed), untouched_days (None = unknown), user_days, late_days, cost, folded, ...}. Never changes the data."""
    import time
    import ledgerplan as lp
    at = time.time() if at is None else at
    today = today or datetime.date.fromtimestamp(at)
    items = [it for it in data["items"] if isinstance(it, dict) and isinstance(it.get("id"), str) and "status" in it]
    skipped = len(data["items"]) - len(items)       # not a dict, or no id or status: counted, never a KeyError
    clean = {"items": items}
    live = [it for it in items if it.get("status") in OPEN]
    by_id = {it["id"]: it for it in live}
    live_set = set(by_id)
    kids = {}
    for it in items:
        into = it["folded"].get("into") if isinstance(it.get("folded"), dict) else None
        if into:
            kids.setdefault(into, []).append(it)
    blocked_on = {}                  # unblocks = the live items BLOCKED on this one (blocked-by), never items that only mention its id
    for it in live:
        for b in lp.blockers(it, live_set):
            blocked_on[b] = blocked_on.get(b, 0) + 1
    words, texts = user_index(inbox), user_texts(inbox)
    stmts = user_statement_texts(inbox)
    reach = origin_reach(items)
    release = sl.current_version(clean)
    p50s = {}
    rows, where, edges, found_by = {}, {}, [], {}
    for it in live:
        iid = it["id"]
        mine = kids.get(iid, ())
        cls, source = lp.class_info(it)
        ids = her_word_ids(it, words, mine)
        times = [t for t in (words[i] for i in ids) if t is not None]
        touch = last_touch(it)
        phrase, dated, lift = is_urgent(it, origin_ids(it, words), texts, reach, today, stmts)
        urgent = phrase or dated
        says_stops = _said(texts, reach, STOPS_KEY, origin_ids(it, words), stmts)       # only what the user states: no question, no pasted text
        typ = item_type(it, cls)                             # type 3 only from the explicit mark: no word is read
        stops = typ == 3 and says_stops                      # a bug the user sees that stops the work
        unblocks = min(blocked_on.get(iid, 0), UNBLOCKS_CAP)
        rel = 1 if release is not None and it.get("phase") == release else 0
        facts = {"tier": 0 if (cls == "A" or lift) else 1 if cls == "unknown" else 2, "class_source": source, "urgent": urgent, "phrase": phrase, "dated": dated,
                 "stops": stops, "type": typ, "type_source": visible_source(it) if typ == 3 else None,
                 "her_words": len(ids), "unblocks": unblocks, "release": rel, "last_touch": touch,
                 "untouched_days": None if touch is None else max(0, int((at - touch) // 86400)),
                 "user_days": max(0.0, (at - max(times)) / 86400.0) if times else None, "her_last": max(times) if times else None,
                 "rank": it.get("rank"), "inherited": False}
        pkey = (it.get("area"), it.get("appetite"))
        if pkey not in p50s:
            p50s[pkey] = lp.p50(clean, pkey[0], pkey[1])
        row = _row(it, facts, mine, p50s[pkey], cls)
        row["late_days"] = late_days(it, today)
        row["late_real"] = late_days(it, today, capped=False)       # the reason line shows the true days, the key the capped ones
        row["score"] = float(typ * WEIGHTS["type"] + min(len(ids), HER_CAP) * WEIGHTS["user"] + unblocks * WEIGHTS["unblocks"]
                             + rel * WEIGHTS["release"])
        rows[iid] = row
        found = lp.blockers(it, live_set)
        text = "%s %s" % (it.get("title", ""), it.get("text", ""))
        waits = sl.waits_on_user(it) or (not found and not it.get("blocked") and lp.WAIT_WORDS.search(text) is not None)
        rank = it.get("rank")
        if isinstance(rank, dict) and type(rank.get("pos")) is int:
            where[iid] = "pinned"
        elif it["status"] == "doing":
            where[iid] = "doing"
        elif waits:
            where[iid] = "waiting"
        elif found or it.get("blocked"):
            where[iid] = "blocked"
        elif it.get("phase") == sl.LATER_PHASE and typ != 3:     # a bug the user sees enters whatever its phase
            where[iid] = "parked"
        else:
            where[iid] = "key"
        found_by[iid] = found
        if found and not waits:
            edges += [(iid, b) for b in found]
    for _ in range(len(edges) + 1):         # a blocker takes the best of every key factor of what it blocks, to a fixed point
        changed = False
        for blocked_id, blocker in edges:
            src, dst = rows[blocked_id], rows[blocker]
            for name, better in (("tier", lambda a, b: a < b), ("urgent", lambda a, b: a and not b), ("phrase", lambda a, b: a and not b), ("stops", lambda a, b: a and not b),
                                 ("dated", lambda a, b: a and not b), ("type", lambda a, b: a > b),
                                 ("score", lambda a, b: a > b), ("late_days", lambda a, b: a > b), ("late_real", lambda a, b: a > b)):
                if better(src[name], dst[name]):
                    dst[name] = src[name]
                    dst["inherited"] = changed = True
        if not changed:
            break
    for row in rows.values():
        row["n"] = row["score"]
    key_rows = [rows[i] for i in rows if where[i] == "key"]
    key_rows.sort(key=_key)
    parked = sorted((rows[i] for i in rows if where[i] == "parked"), key=_key)
    stale = []                                      # doing or blocked items that rotate (finish, release or unblock)
    for i in rows:
        row = rows[i]
        if where[i] not in ("doing", "blocked"):
            continue
        row["blocked_by"] = found_by[i]
        gone = False
        if where[i] == "blocked":
            block = by_id[i].get("blocked")
            ids = [("R-" + m.group(1)) for m in ITEM_ID.finditer(block.get("reason") or "")] if isinstance(block, dict) and block.get("on") != "user" else []
            gone = not found_by[i] and any(x != i for x in ids)     # it cites ids and none is open: nothing holds it
            row["blocker_gone"] = gone
        if _old(row) or gone:
            stale.append(row)
    beyond = sum(1 for r in key_rows + parked + stale if _old(r))
    slots = rotation_slots(beyond)
    bound = math.ceil(beyond / slots)
    trend = open_a_trend(items, at, lp)
    reserved = None
    if trend[0] > 0 and trend[0] >= trend[1]:       # open class A is not falling: the newest class B ask keeps one slot
        asks = [r for r in key_rows + parked if r["class"] == "B" and r["her_last"] is not None]
        def _born(r):                                # a tie on the same time: the item created first wins, then the lower id
            made = sl._epoch(by_id[r["id"]].get("created"))
            return float("inf") if made is None else made
        newest = min(asks, key=lambda r: (-r["her_last"], _born(r), _idkey(r["id"])), default=None)
        if newest is not None and newest not in key_rows[:max(HEAD_SIZE - slots, 0)]:
            reserved = newest
    keep = max(HEAD_SIZE - slots - (1 if reserved else 0), 0)
    head_key, spare = key_rows[:keep], [r for r in key_rows[keep:] if r is not reserved]
    pool = spare + [r for r in parked if r is not reserved]
    rotation = [min(pool, key=_age_key)] if pool else []          # slot 1: the oldest actionable item, always
    rotation += sorted([r for r in pool + stale if r not in rotation], key=_age_key)[:max(slots - len(rotation), 0)]
    rotation.sort(key=_age_key)
    for row in head_key:
        row["slot"] = "key"
    if reserved:
        reserved["slot"], reserved["from"], reserved["action"] = "reserved", where[reserved["id"]], ACTION_ASK
    for row in rotation:
        row["slot"] = "rotation"
        row["from"] = where[row["id"]]
        if row["from"] in ("doing", "blocked"):
            row["action"] = ACTION_FINISH
        elif row["from"] == "parked" or row["class"] != "A":
            row["action"] = ACTION_ASK
        else:
            row["action"] = ACTION_BUILD
        it = by_id[row["id"]]
        row["fold_allowed"] = _APPROVED.search("%s %s" % (it.get("title", ""), it.get("text", ""))) is None
    head = head_key + ([reserved] if reserved else []) + rotation
    in_head = {r["id"] for r in head}

    def since(row):
        block = by_id[row["id"]].get("blocked")
        return (_ts(block.get("since")) if isinstance(block, dict) else None) or 0

    waiting = sorted((rows[i] for i in rows if where[i] == "waiting"), key=lambda r: (since(r), _idkey(r["id"])))
    doing_all = [rows[i] for i in rows if where[i] == "doing"]
    out = {"pinned": sorted((rows[i] for i in rows if where[i] == "pinned"), key=lambda r: (by_id[r["id"]]["rank"]["pos"], _idkey(r["id"]))),
           "head": head,
           "doing": sorted((r for r in doing_all if r["id"] not in in_head), key=lambda r: (-(r["untouched_days"] or 0), _idkey(r["id"]))),
           "waiting": waiting,
           "blocked": sorted((rows[i] for i in rows if where[i] == "blocked" and i not in in_head), key=lambda r: (-(r["untouched_days"] or 0), _idkey(r["id"]))),
           "parked": [r for r in parked if r["id"] not in in_head],
           "rest": [r for r in spare if r["id"] not in in_head]}
    done = [it for it in items if it.get("status") == "done"]
    dated = [(t, it["id"]) for it in done for t in [_ts(it.get("done_at"))] if t is not None]
    oldest = min(dated, key=lambda p: (p[0], _idkey(p[1]))) if dated else None
    waited = [since(r) for r in waiting if since(r)]
    doing_since = [t for r in doing_all for t in [_ts(by_id[r["id"]].get("doing_since"))] if t is not None]
    unknown_touch = any(r["untouched_days"] is None for r in key_rows + parked)
    urgent_out = [r["id"] for r in sorted(key_rows + parked, key=_key) if r["phrase"] and r["id"] not in in_head]
    dated_out = [r["id"] for r in sorted(key_rows + parked, key=_key) if r["dated"] and not r["phrase"] and r["id"] not in in_head]
    everyone = key_rows + parked + [r for r in head if r not in key_rows + parked]

    def days(t):
        return max(0, int((at - t) // 86400))

    out.update({"done_awaiting_verify": {"n": len(done), "oldest": {"id": oldest[1], "days": days(oldest[0])} if oldest else None},
                "counts": {s: sum(1 for it in items if it.get("status") == s) for s in ("verified", "folded", "dropped")},
                "basis": "tier, urgent, score", "skipped": skipped,
                "oldest_untouched_days": None if unknown_touch else max((r["untouched_days"] for r in key_rows + parked), default=0),
                "untouched_beyond_cap": beyond, "rotation_slots": slots, "rotation_bound_days": bound, "rotation_alarm": bound > AGE_CAP,
                "unknown_in_head": sum(1 for r in head if r["class"] == "unknown"), "urgent_outside_head": urgent_out,
                "dated_outside_head": dated_out,
                "urgent_total": sum(1 for r in everyone if r["phrase"]),         # the explicit urgency phrase only
                "dated_total": sum(1 for r in everyone if r["dated"] and not r["phrase"]),     # a hand due and a date word only
                "key_slots": len(head_key), "key_rows_ahead": {r["id"]: i for i, r in enumerate(key_rows)},
                "open_class_a": {"now": trend[0], "week_ago": trend[1]}, "reserved_slot": bool(reserved),
                "doing_summary": {"n": len(doing_all), "oldest_days": days(min(doing_since)) if doing_since else None},
                "daily": {"oldest_waiting_days": days(min(waited)) if waited else None}})
    return out


# ---------------------------------------------------------------- the reason line and the daily line (i18n, every language)

def reason_line(row, lang=None):
    """One plain line for a head row, from i18n keys (no ids, no code names): why it is here."""
    def t(key, **kw):
        return i18n.t(key, lang=lang, **kw)
    parts = []
    if isinstance(row.get("rank"), dict) and row["rank"].get("reason"):      # a pin keeps its stored reason in every view
        parts.append(t("ledger.order_why_pinned", reason=row["rank"]["reason"]))
    if row.get("stops"):
        parts.append(t("ledger.order_why_stops"))
    if row.get("dated") and not row.get("phrase"):         # a hand due and a date word only: a dated item, never «urgent»
        parts.append(t("ledger.order_why_dated"))
    elif row.get("urgent"):
        parts.append(t("ledger.order_why_urgent"))
    if row["type"] == 3:
        parts.append(t("ledger.order_why_seen"))
    elif row["type"] == 2:
        parts.append(t("ledger.order_why_fix"))
    elif row["type"] == 1:
        parts.append(t("ledger.order_why_perf"))
    elif row["class"] == "B":
        parts.append(t("ledger.order_why_new"))
    else:
        parts.append(t("ledger.order_why_unknown"))
    if row.get("class_source") == "inferred" and row["class"] != "unknown":      # no flag and no Class mark: the words said so
        parts.append(t("ledger.order_why_inferred"))
    if row["her_words"]:
        parts.append(t("ledger.order_why_asked"))
    if row["unblocks"]:
        parts.append(t("ledger.order_why_unblocks", n=row["unblocks"]))
    if row.get("inherited"):
        parts.append(t("ledger.order_why_inherit"))
    if row["release"]:
        parts.append(t("ledger.order_why_release"))
    late = row.get("late_real", row["late_days"])         # the true days late, not the capped tie-break value
    if late:
        parts.append(t("ledger.order_why_late" + ("_one" if late == 1 else ""), n=late))     # plural form: 1 day
    if row.get("slot") == "reserved":
        parts.append(t("ledger.order_why_reserved"))
    if row.get("slot") == "rotation":
        days = row["untouched_days"]
        parts.append(t("ledger.order_why_rotation_unknown") if days is None else t("ledger.order_why_rotation" + ("_one" if days == 1 else ""), n=days))
    return " · ".join(parts)


def daily_line(res, lang=None):
    """The daily facts in one line: the oldest waiting ask, the done-awaiting-verify pile (count, oldest id and age), the
    rotation bound and its alarm, the unknown count in the head."""
    done = res["done_awaiting_verify"]
    old = done["oldest"]
    wait = res["daily"]["oldest_waiting_days"]
    line = i18n.t("ledger.order_daily", lang=lang, wait="-" if wait is None else wait, done=done["n"],
                  done_id=old["id"] if old else "-", done_days=old["days"] if old else "-", bound=res["rotation_bound_days"],
                  unknown=res["unknown_in_head"])
    if res["rotation_alarm"]:
        line += i18n.t("ledger.order_alarm", lang=lang, cap=AGE_CAP)
    return line


# ---------------------------------------------------------------- the dry run (read-only)

FACTOR_IDS = ("R-327", "R-521")


def _line(n, row):
    extra = "  [%s%s]" % (row["slot"], ": " + row["action"] if row.get("action") else "") if row.get("slot") else ""
    days = "unknown" if row["untouched_days"] is None else "%dd" % row["untouched_days"]
    return "%2d. %s  %s  tier %d  %s  type %d%s  score %g  late %dd  untouched %s%s  %s\n      porque: %s" % (
        n, row["id"], row["class"], row["tier"], "DATED" if row["dated"] and not row["phrase"] else "URGENT" if row["urgent"] else "-", row["type"],
        " (%s)" % row["type_source"] if row.get("type_source") else "", row["score"], row["late_days"], days,
        extra, sl.oneline(row["title"], 60), reason_line(row, "es"))


def _factors(new, item_id):
    for sec in ("pinned", "head", "doing", "waiting", "blocked", "parked", "rest"):
        for r in new[sec]:
            if r["id"] == item_id:
                why = ""
                if sec != "head" and item_id in new["key_rows_ahead"]:
                    why = " | not in the head: %d key rows rank ahead and the head has %d key slots" % (
                        new["key_rows_ahead"][item_id], new["key_slots"])
                return "%s: where %s | tier %d | urgent %s | type %d%s | user words %d | unblocks %d | release %d | score %g | late %dd | untouched %s | phase %s%s" % (
                    item_id, sec, r["tier"], r["urgent"], r["type"], " (%s)" % r["type_source"] if r.get("type_source") else "", r["her_words"], r["unblocks"], r["release"], r["score"],
                    r["late_days"], "unknown" if r["untouched_days"] is None else "%dd" % r["untouched_days"], r["phase"], why)
    return "%s: not a live item" % item_id


def compare(project, at=None, today=None):
    """-> {current: {top, plan}, new, entered, left, hand_due}. Reads the ledger and the inbox once; writes nothing."""
    import time
    import ledgerplan as lp
    import taskrank
    at = time.time() if at is None else at
    data = sl.load_ledger(project)
    inbox = sl.load_inbox(project)
    top = taskrank.top(data, taskrank.TOP_N, at)
    new = order(data, at=at, inbox=inbox, today=today)
    plan = lp.build(project, data, at=at, step0=False, order_res=new)["rows"]
    old_ids = [r["id"] for r in top]
    new_ids = [r["id"] for r in new["head"]]
    hand = [it["id"] for it in data["items"] if it.get("status") in OPEN and it.get("due") and not it.get("due_auto")]
    return {"current": {"top": [{"id": r["id"], "score": r["score"], "title": r["title"]} for r in top], "plan": [r["id"] for r in plan]},
            "new": new, "entered": [i for i in new_ids if i not in old_ids], "left": [i for i in old_ids if i not in new_ids],
            "hand_due": hand}


def dry_run(project, at=None, today=None, as_json=False):
    """The old order next to the new one, for the real ledger shape. Text, or the same dict as JSON."""
    import json
    got = compare(project, at, today)
    if as_json:
        return json.dumps(got, indent=1, sort_keys=True, default=str)
    new = got["new"]
    done = new["done_awaiting_verify"]
    head = new["head"]
    out = ["R-1333 v1b dry run (read-only, nothing is written): current order next to the new order",
           "key: (tier, urgent, score, late, id); tier 0 = class A or urgency, 1 = unknown, 2 = class B; score = type*10 + user words*3 + unblocks*2 + release*5",
           "basis: %s | oldest untouched actionable: %s | untouched beyond %dd: %d | rotation slots: %d" % (
               new["basis"], "unknown" if new["oldest_untouched_days"] is None else "%dd" % new["oldest_untouched_days"], AGE_CAP,
               new["untouched_beyond_cap"], new["rotation_slots"]),
           "rotation bound: %d days | alarm: %s (alarm when the bound is above %d)" % (
               new["rotation_bound_days"], "ON" if new["rotation_alarm"] else "off", AGE_CAP),
           "open class A: %d now, %d a week ago | reserved class B slot: %s" % (
               new["open_class_a"]["now"], new["open_class_a"]["week_ago"], "yes" if new["reserved_slot"] else "no"),
           "unknown class in the head: %d | skipped (no id or status): %d" % (new["unknown_in_head"], new["skipped"]),
"urgent items (an explicit phrase of the user): %d%s (the head holds %d) | dated items (a hand due and a date word, not urgent): %d" % (
               new["urgent_total"], " - TOO BROAD, above the head size" if new["urgent_total"] > HEAD_SIZE else "", len(head), new["dated_total"]),
           "urgent outside head (%d): %s" % (len(new["urgent_outside_head"]), ", ".join(new["urgent_outside_head"]) or "-"),
           "dated outside head (%d): %s" % (len(new["dated_outside_head"]), ", ".join(new["dated_outside_head"]) or "-"),
           "", "CURRENT order (budgit top, first %d):" % len(got["current"]["top"])]
    out += ["%2d. %s  score %g  %s" % (n, r["id"], r["score"], sl.oneline(r["title"], 60)) for n, r in enumerate(got["current"]["top"], 1)]
    out += ["CURRENT plan order (first 10): " + ", ".join(got["current"]["plan"][:10]), "",
            "NEW head (%d slots: key, reserved, rotation):" % len(head)]
    out += [_line(n, r) for n, r in enumerate(head, 1)]
    out += ["ENTERED head: " + (", ".join(got["entered"]) or "-"), "LEFT head: " + (", ".join(got["left"]) or "-"), "",
            "FACTORS (evidence for the bugs the user sees):"] + ["  " + _factors(new, i) for i in FACTOR_IDS] + [""]
    out += ["HAND-DUE live items (%d): %s" % (len(got["hand_due"]), ", ".join(got["hand_due"]) or "-"),
            "doing: %d (oldest %s) | sections: %s" % (
                new["doing_summary"]["n"], "-" if new["doing_summary"]["oldest_days"] is None else "%dd" % new["doing_summary"]["oldest_days"],
                ", ".join("%s %d" % (s, len(new[s])) for s in ("pinned", "doing", "waiting", "blocked", "head", "parked", "rest"))),
            "parked later: %d | done awaiting verify: %d%s | folded %d, dropped %d, verified %d" % (
                len(new["parked"]) + sum(1 for r in head if r.get("from") == "parked"), done["n"],
                " (oldest %s, %dd)" % (done["oldest"]["id"], done["oldest"]["days"]) if done["oldest"] else "",
                new["counts"]["folded"], new["counts"]["dropped"], new["counts"]["verified"]),
            "daily line (es): " + daily_line(new, "es")]
    return "\n".join(out)


# ---------------------------------------------------------------- the views (R-1333): `budgit ledger top`, the status top, the plan and the daily

def view(project, data=None, at=None, today=None):
    """THE call every view makes: the order of the project's ledger and inbox. Reads only. Same result as the dry run's `new`."""
    data = data if data is not None else sl.load_ledger(project)
    return order(data, at=at, inbox=sl.load_inbox(project), today=today)


def shown(res, n=HEAD_SIZE):
    """The rows a view shows: the PM's pins first (a block of their own, never counted in the head), then the first n head rows (the
    same rows, in the same order, as the dry run's NEW head)."""
    return list(res["pinned"]) + list(res["head"])[:n]


def sequence(res):
    """Every live item id in the order of the views: pinned, head, doing, parked, rest."""
    return [r["id"] for sec in ("pinned", "head", "doing", "parked", "rest") for r in res[sec]]


def head_lines(res, n=HEAD_SIZE, lang=None):
    """The text of the view: the pins as their own short block «fijadas (N)», each with its «porque» line, then the head rows (n of them,
    pins not counted), the id, the title and the «porque» line, as the dry run prints them."""
    pins = list(res["pinned"])
    out = [i18n.t("ledger.order_pins", lang=lang, n=len(pins))] if pins else []
    out += ["  %s  %s\n      porque: %s" % (r["id"], sl.oneline(r["title"], 60), reason_line(r, lang)) for r in pins]
    return out + ["%3d. %s  %s\n      porque: %s" % (k, r["id"], sl.oneline(r["title"], 60), reason_line(r, lang)) for k, r in enumerate(list(res["head"])[:n], 1)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="R-1333 dry run: the current order next to the new one (read-only)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    print(dry_run(os.path.abspath(args.project), as_json=args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
