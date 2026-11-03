"""R-286 task 4: decisions come to the user only when one is needed - max 3 a day, urgent ones first, a 7-day safety net.

`world()` reads the portfolio into plain dicts; `pending()` turns it into cards; `select()` picks the ones to show today.
Both are pure. The CLI and the SessionStart hook do the I/O. Cap, backoff and what was shown live in the Budgit home
(`triggers.json`), because they are per portfolio, never in a project. Nothing here writes a ledger, and no card's default
drops, deletes or un-parks anything: an unanswered card changes nothing.

Privacy (plan fix C3): a card holds ids and counts only. A protected project is read through its card counts (no ledger
opened), shows under its portfolio id (`private~hash`), and a cross-project surface redacts its question text."""
import json
import os
import time
import unicodedata

import studiolib as sl

DAILY_CAP = 3
PARKED_PER_CARD = 5
PARKED_CARDS_PER_DAY = 1
PARKED_AGES = (30, 90)
SAFETY_NET_DAYS = 7
AGE_FACTOR = 1.5                    # same line as capacity.AGE_FACTOR
ABSTAIN_CONFIDENCE = 0.8            # a derived card at or above this whose default is a no-op is logged, never shown
DAY = 86400

DERIVED = ("age", "blocker", "next_empty", "release", "parked", "sweep")
EVENT = ("approval", "choice", "lane-stalled")
ORDER = {k: n for n, k in enumerate(EVENT + ("release", "age", "blocker", "next_empty", "sweep", "daily", "parked"))}
NOOP_DEFAULTS = ("keep_going", "keep_parked", "wait")


def _today(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _fold(text):
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def is_private_id(slug):
    return str(slug).startswith("private")


# ---------------------------------------------------------------- state (the Budgit home)
def state_path():
    return os.path.join(sl.home_state(), "triggers.json")


def load_state():
    try:
        with open(state_path(), encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    for name in ("keys", "parked_seen", "parked_hits", "last_release", "last_release_card"):
        if not isinstance(data.get(name), dict):
            data.pop(name, None)                  # a wrong-typed file reads as empty, never a traceback
    data.setdefault("keys", {})
    return data


def locked():
    return sl.home_lock("triggers")


def save_state(data):
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    sl.atomic_write(state_path(), json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def mark_shown(state, keys, now, cards=()):
    """Each key shown and still unanswered when it comes back counts as ignored: the next due then doubles. The items of
    a shown parked/release card are remembered, so the next card carries the next ones (the wave advances)."""
    for c in cards:
        if c.get("key") in keys and c.get("kind") in ("parked", "release"):
            seen = state.setdefault("parked_seen", {}).setdefault(c["project"], {})
            for item_id in c.get("items") or []:
                seen[item_id] = int(now)
    today = _today(now)
    if state.get("day") != today:
        state["day"], state["shown_today"], state["parked_today"] = today, 0, 0
    for key in keys:
        meta = state["keys"].setdefault(key, {})
        meta["ignored"] = meta.get("ignored", 0) + 1 if meta.get("shown_at") and not meta.get("answered_at") else 1
        meta["shown_at"] = int(now)
        meta.pop("answered_at", None)
        state["shown_today"] = state.get("shown_today", 0) + 1
        if key.startswith("release:"):                          # the release it announces is not announced twice
            slug, _, stamp = key[len("release:"):].rpartition(":")
            if stamp.isdigit():
                state.setdefault("last_release_card", {})[slug] = int(stamp)
        if key.startswith("parked:"):
            state["parked_today"] = state.get("parked_today", 0) + 1
    state["last_card_at"] = int(now)
    return state


def mark_answered(state, key, now, input_id=None):
    meta = state["keys"].setdefault(key, {})
    meta["answered_at"] = int(now)
    if input_id:
        meta["input"] = input_id
    return state


def next_due(meta):
    """Shown once: due again after a day; ignored twice: after two; then 4, 8 ... (a day times 2 to the ignored count less 1)."""
    if not meta or not meta.get("shown_at"):
        return 0
    return meta["shown_at"] + DAY * 2 ** (max(meta.get("ignored", 1), 1) - 1)


# ---------------------------------------------------------------- reading the portfolio
def _read_items(path, now):
    """The facts the cards need from one open ledger. Ids and numbers only."""
    data = sl.load_ledger(path)
    items = data["items"]
    live = [i for i in items if i["status"] in ("open", "doing")]
    parked = [i for i in live if i["status"] == "open" and i.get("phase") == "later"]
    committed = [i for i in live if i not in parked]
    doing = [i for i in committed if i["status"] == "doing"]
    ready = [i for i in committed if i["status"] == "open" and not i.get("blocked")]
    aged = []
    for i in doing:
        days = sl.appetite_days(i)
        since = sl._epoch(i.get("doing_since"))
        if days is not None and since is not None and now - since > AGE_FACTOR * days * DAY:
            aged.append(i["id"])
    blockers = [{"id": i["id"], "reason": _fold((i["blocked"] or {}).get("reason")), "mvp": i.get("phase") == "mvp",
                 "only_doing": i["status"] == "doing" and len(doing) == 1} for i in committed if i.get("blocked")]
    return {"ready": [i["id"] for i in ready], "doing": [i["id"] for i in doing], "aged": aged, "blockers": blockers,
            "parked": [{"id": i["id"], "at": sl._epoch(i.get("parked_at") or i.get("updated"))} for i in parked]}


def world(now=None):
    """-> {projects: [...], skipped: [...]}. Built from capacity.compute (slots, free, stuck) plus each open ledger. A
    protected project contributes counts only (its ledger stays closed)."""
    import capacity
    now = now or time.time()
    pic = capacity.compute(now, write=False)
    state = load_state()
    projects = []
    for slug, row in pic["projects"].items():
        entry = {"slug": slug, "path": row["path"], "private": row["private"], "paused": row["paused"], "free": row["free"],
                 "ready": [], "doing": [], "aged": [], "blockers": [], "parked": [], "ready_n": 0}
        if row["private"]:
            entry["ready_n"] = row["cap"]                       # a count is all a protected card gives
        else:
            try:
                entry.update(_read_items(row["path"], now))
                entry["ready_n"] = len(entry["ready"])
            except Exception:
                continue
        hits = (state.get("parked_hits") or {}).get(sl.path_key(row["path"])) or (state.get("parked_hits") or {}).get(os.path.realpath(row["path"]))
        entry["hits"] = {i: sl._epoch(t) or 0 for i, t in hits.items()} if isinstance(hits, dict) else {}
        entry["last_release"] = (state.get("last_release") or {}).get(slug)
        projects.append(entry)
    return {"projects": projects, "skipped": pic["skipped"]}


# ---------------------------------------------------------------- cards
def _card(kind, project, items, default, confidence, now, urgent=False, key=None, reversible=True, **extra):
    out = {"key": key or "%s:%s" % (kind, project), "kind": kind, "project": project, "items": list(items),
           "default": default, "if_no_answer": default, "confidence": confidence, "reversible": reversible,
           "urgent": urgent, "created": int(now), "expires_at": None, "lane": None, "question": None}
    out.update(extra)
    return out


def _parked_review(p, state, now):
    """The `later` items worth a look: past 30 or 90 days, matched by new input, or after a release. Oldest first."""
    seen = (state.get("parked_seen") or {}).get(p["slug"]) or {}
    released = p.get("last_release") if p.get("release_open") else None
    due = []
    for it in p["parked"]:
        crossed = [it["at"] + a * DAY for a in PARKED_AGES if it["at"] is not None and it["at"] + a * DAY <= now]
        hit, shown = p["hits"].get(it["id"]), seen.get(it["id"])
        if shown is None:
            ok = bool(crossed) or hit is not None or released is not None
        else:                                   # shown before: only something newer brings it back
            ok = any(c > shown for c in crossed) or (hit is not None and hit > shown) or (released is not None and released > shown)
        if ok:
            due.append(it)
    due.sort(key=lambda it: (it["at"] if it["at"] is not None else 0, it["id"]))
    return [it["id"] for it in due[:PARKED_PER_CARD]]


def pending(world_, state, now):
    cards = []
    sweep_ready = False
    for p in world_["projects"]:
        slug = p["slug"]
        if p["paused"]:
            continue
        if p["ready_n"] or p["ready"]:
            sweep_ready = True
        if p["aged"]:
            cards.append(_card("age", slug, p["aged"], "keep_going", 0.6, now))
        groups = {}
        for b in p["blockers"]:
            groups.setdefault(b["reason"], []).append(b)
        for group in groups.values():
            first = min(b["id"] for b in group)
            cards.append(_card("blocker", slug, sorted(b["id"] for b in group), "keep_going", 0.5, now,
                               urgent=any(b["mvp"] or b["only_doing"] for b in group), key="blocker:%s:%s" % (slug, first)))
        if p["free"] > 0 and not p["ready"] and not (p["private"] and p["ready_n"]):
            cards.append(_card("next_empty", slug, [], "wait", 0.6, now))
        released = p.get("last_release")
        seen = (state.get("last_release_card") or {}).get(slug)
        if released and (seen is None or released > seen):
            p = dict(p, release_open=True)
            cards.append(_card("release", slug, _parked_review(p, state, now), "keep_parked", 0.5, now,
                               key="release:%s:%d" % (slug, released)))
        review = _parked_review(p, state, now) if not p.get("release_open") else []
        if review:
            cards.append(_card("parked", slug, review, "keep_parked", 0.3, now, key="parked:%s:%s" % (slug, _today(now))))
    ref = state.get("last_card_at") or state.get("first_seen")
    if sweep_ready and ref and now - ref >= SAFETY_NET_DAYS * DAY:
        cards.append(_card("sweep", "*", [], "keep_going", 0.5, now, key="sweep"))
    cards.extend(event_cards(state, now))
    return cards


def event_cards(state, now):
    """Cards the autonomy lane appended to cards.jsonl (R-288). Open ones only; a bad line is skipped."""
    path = os.path.join(sl.home_state(), "cards.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            c = json.loads(line)
        except ValueError:
            continue
        if not isinstance(c, dict) or c.get("kind") not in EVENT or not isinstance(c.get("key"), str):
            continue
        num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        expires, conf = num(c.get("expires_at")), num(c.get("confidence"))
        if expires and expires < now:
            continue
        items = [x for x in c.get("items") or [] if isinstance(x, str)] if isinstance(c.get("items"), list) else []
        text = lambda v: v if isinstance(v, str) else None
        full = _card(c["kind"], text(c.get("project")) or "*", items, text(c.get("default")) or "wait",
                     conf if conf is not None and 0 <= conf <= 1 else None, num(c.get("created")) or now,
                     urgent=c.get("urgent") is True, key=c["key"], reversible=c.get("reversible") is not False)
        full.update(lane=text(c.get("lane")), question=text(c.get("question")), expires_at=expires,
                    if_no_answer=text(c.get("if_no_answer")) or full["default"])
        out.append(full)
    return out


def is_abstain(card):
    """High confidence, a derived card, and a default that changes nothing: logged, never shown, not counted."""
    return (card["kind"] in DERIVED and card.get("confidence") is not None and card["confidence"] >= ABSTAIN_CONFIDENCE
            and card["default"] in NOOP_DEFAULTS and not card["urgent"])


def select(cards, state, now):
    """-> (show, later). Urgent first; urgent cards count toward the cap but are never held back by it. A card answered,
    still waiting out its backoff, or abstained is neither shown nor held. At most one parked card a day."""
    today = _today(now)
    shown = state.get("shown_today", 0) if state.get("day") == today else 0
    parked = state.get("parked_today", 0) if state.get("day") == today else 0
    keys = state.get("keys") or {}
    show, later = [], []
    ranked = sorted(cards, key=lambda c: (not c["urgent"], bool((keys.get(c["key"]) or {}).get("shown_at")),
                                          ORDER.get(c["kind"], 99), c["key"]))     # new before re-surfaced, so a cap never starves them
    for c in ranked:
        meta = keys.get(c["key"]) or {}
        if meta.get("answered_at") or is_abstain(c) or (meta.get("shown_at") and now < next_due(meta)):
            continue
        if c["urgent"]:
            show.append(c)
            shown += 1
        elif shown >= DAILY_CAP or (c["kind"] == "parked" and parked >= PARKED_CARDS_PER_DAY):
            later.append(c)
        else:
            show.append(c)
            shown += 1
            parked += c["kind"] == "parked"
    return show, later


# ---------------------------------------------------------------- output
def redact(card, current_slug=None):
    """A protected project's card seen from another project: its question text and lane never leave (read-time rule)."""
    if is_private_id(card["project"]) and card["project"] != current_slug:
        return dict(card, question=None, lane=None)
    return card


def line(card, current_slug=None, lang=None):
    import i18n
    c = redact(card, current_slug)
    if is_private_id(c["project"]) and c["project"] != current_slug:
        return i18n.t("triggers.private", lang, project=c["project"])
    n = len(c["items"])
    ids = ", ".join(c["items"][:PARKED_PER_CARD]) + (" +%d" % (n - PARKED_PER_CARD) if n > PARKED_PER_CARD else "")
    key = "triggers.kind." + c["kind"].replace("-", "_")
    text = i18n.t(key, lang, project=c["project"], n=n, ids=ids)
    return ("[!] " if c["urgent"] else "") + text


def note_release(slug, now=None):
    """`release-check` passed for this project: the next triggers run carries the parked review (once)."""
    with locked():
        state = load_state()
        state.setdefault("last_release", {})[slug] = int(now or time.time())
        save_state(state)


def refresh(now=None, write=True):
    """Compute the cards and keep them (with the state) in the home cache the SessionStart line reads.
    -> (state, world, cards). The write merges into the file as it is now, under the lock."""
    now = now or time.time()
    state = load_state()
    w = world(now)
    cards = pending(w, {**state, "first_seen": state.get("first_seen") or int(now)}, now)
    cache = {"at": int(now), "cards": cards, "slugs": {sl.path_key(p["path"]): p["slug"] for p in w["projects"]}}
    abstained = {c["key"] for c in cards if is_abstain(c)}
    if write:
        with locked():
            state = load_state()
            state.setdefault("first_seen", int(now))
            state["abstained"] = sorted(set(state.get("abstained") or []) | abstained)[-50:]
            state["cache"] = cache
            save_state(state)
    else:
        state["cache"] = cache
    return state, w, cards


CACHE_FRESH_S = 3600


def daily_line(project, now, lang=None):
    """R-415: the day's one `budgit daily` line, on a startup session only. Integers, never a word of the ledger. The facts
    are worked out before the lock; inside it the card goes through select() (the daily cap and the backoff of every card)
    and is marked shown, so two sessions starting together show it once. A day with nothing to say spends no slot."""
    import daily
    import i18n
    facts = daily.session_facts(project, now)
    if not facts or not (facts["batches"] or facts["duplicates"] or facts["clean"]):
        return ""
    key = "daily:" + daily.repo_key(project)
    card = _card("daily", "*", [], "wait", 0.5, now, key=key)
    with locked():
        state = load_state()
        show, _later = select([card], state, now)
        if not show:
            return ""
        save_state(mark_shown(state, [key], now, [card]))
    if facts["clean"]:
        return i18n.t("daily.session_clean", lang, b=facts["batches"], d=facts["duplicates"], c=facts["clean"])
    return i18n.t("daily.session", lang, b=facts["batches"], d=facts["duplicates"])


def session_line(project, now=None, lang=None, source=None):
    """The SessionStart lines: how many decisions wait, and (startup sessions only) the daily line. Inside a project the
    decisions are that project's cards; in the hub (the Team Manager's folder) every project's. "" when nothing is due.
    Reads the cache; recomputes when it is an hour old."""
    now = now or time.time()
    decisions = _decisions_line(project, now, lang)
    daily_text = daily_line(project, now, lang) if source == "startup" else ""
    return "\n".join(x for x in (decisions, daily_text) if x)


def _decisions_line(project, now, lang):
    import i18n
    state = load_state()
    cache = state.get("cache")
    if not cache or now - cache.get("at", 0) > CACHE_FRESH_S:
        state, _w, _cards = refresh(now)
        cache = state["cache"]
    show, _later = select(cache["cards"], state, now)
    here = cache["slugs"].get(sl.path_key(project))
    try:
        in_hub = os.path.realpath(project) == sl.hub_path()
    except sl.StudioError:
        in_hub = False
    mine = [c for c in show if in_hub or c["project"] in (here, "*")]
    return i18n.t("triggers.session", lang, n=len(mine)) if mine else ""
