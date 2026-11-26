"""R-286 task 3: how many items may run at once, and who gets which slot.

Three layers, all plain arithmetic (zero model tokens):
  A. total()  - the portfolio's limit T from the budget level, kept between runs (drops at once, rises +1 a day).
  B. split()  - T shared between projects by priority weight, at least 1 each (even when that exceeds T, R-877), never above what is ready.
  C. free()   - what a project may still start now, less what is stuck.
`portfolio_snapshot` reads the project list; `check_start` is the gate `start_item` calls for a NEW start only.
A reason is a short code (see `capacity.r.*` in i18n); every user-facing sentence goes through i18n.

Privacy (plan fix C3): a protected project (livework.is_private) is read through its card counts only, and
shows under its portfolio id (`private~hash`); no title, reason text or folder name leaves this module for it."""
import json
import math
import os
import time

import studiolib as sl

LEVEL_LIMITS = {"ample": (1, 6), "normal": (1, 5), "tight": (1, 3), "critical": (1, 1)}     # (floor, ceiling); R-877: the floor is never 0
LEVELS = ("ample", "normal", "tight", "critical")
WEIGHTS = {"high": 3, "normal": 2, "low": 1}
PROJECT_CAP = 4
H5_GATE = 70
AGE_FACTOR = 1.5
PACE_RAISE_MAX = 5              # +1 only at pace <= this; above it the limit holds (a worse level already drops it)


def _today(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


# ---------------------------------------------------------------- Layer A
def total(prev, level, pace, in_use, projected, last_raise, today):
    """-> (T, reason). `prev` None: the ceiling. A lower ceiling applies at once; a higher one is reached +1 a day,
    only while at least half of the limit is in use and the pace is not above +5. A projection over 100% steps down."""
    eff, tail = level, ""
    if projected is not None and projected > 100 and LEVELS.index(level) < len(LEVELS) - 1:
        eff, tail = LEVELS[LEVELS.index(level) + 1], "+projected"
    floor, ceiling = LEVEL_LIMITS[eff]
    if prev is None:
        return ceiling, "start" + tail
    if prev > ceiling:
        return max(floor, ceiling), "drop" + tail
    if prev == ceiling:
        return max(floor, prev), "at_ceiling" + tail
    if pace is not None and pace > PACE_RAISE_MAX:
        return max(floor, prev), "hold_pace" + tail
    if in_use * 2 < prev:
        return max(floor, prev), "hold_use" + tail
    if last_raise == today:
        return max(floor, prev), "hold_day" + tail
    return max(floor, min(ceiling, prev + 1)), "raise" + tail


# ---------------------------------------------------------------- Layer B
def split(T, projects, per_project=None):
    """-> {slug: {slots, floor, cap, weight, reason}}. Floor 1 for a committed project that is not paused; water-filling
    by weight up to cap = min(ready, PROJECT_CAP); round down, leftovers by largest remainder (ties: higher weight).
    When the floors do not fit in T they go by weight, then by the longest wait."""
    out = {}
    elig = []
    for p in projects:
        weight = WEIGHTS.get(p.get("priority"), WEIGHTS["normal"])
        live = bool(p.get("committed")) and not p.get("paused")
        cap = max(1, min(int(p.get("ready") or 0), PROJECT_CAP)) if live else 0
        out[p["slug"]] = {"slots": 0, "floor": 1 if live else 0, "cap": cap, "weight": weight,
                          "reason": "paused" if p.get("paused") else "idle"}
        if live:
            elig.append(p)
    if not elig:
        return out
    if per_project:                       # your cap: every live, unpaused project gets N lanes, the portfolio total is not shared out.
        for p in elig:                    # Kept: paused and idle (nothing committed) stay at 0. Dropped: the ready-count cap, which
            out[p["slug"]].update(slots=per_project, cap=per_project, reason="user_cap")   # would shrink slots as items start.
        return out
    if len(elig) >= T:                   # R-877: the floors do not fit in T (or T is 0): every live project still keeps its one lane
        for p in elig:
            out[p["slug"]].update(slots=1, reason="floor")
        return out
    frozen, free_set, rest = {}, [p["slug"] for p in elig], float(T)
    while free_set:
        wsum = sum(out[s]["weight"] for s in free_set)
        share = {s: rest * out[s]["weight"] / wsum for s in free_set}
        over = [s for s in free_set if share[s] > out[s]["cap"] + 1e-9]
        under = [] if over else [s for s in free_set if share[s] < out[s]["floor"] - 1e-9]
        hit = over or under
        if not hit:
            break
        for s in hit:
            frozen[s] = out[s]["cap"] if over else out[s]["floor"]
            rest -= frozen[s]
            free_set.remove(s)
    real = {s: round(share[s], 9) for s in free_set} if free_set else {}
    whole = dict(frozen)
    for s, v in real.items():
        whole[s] = int(math.floor(v))
    spare = T - sum(whole.values()) if free_set else 0
    for s in sorted(real, key=lambda s: (-(real[s] - math.floor(real[s])), -out[s]["weight"])):
        if spare <= 0:
            break
        if whole[s] < out[s]["cap"]:
            whole[s] += 1
            spare -= 1
    for s, n in whole.items():
        why = "cap" if s in frozen and n == out[s]["cap"] and n > out[s]["floor"] else "floor" if n == out[s]["floor"] else "weights"
        out[s].update(slots=n, reason=why)
    return out


# ---------------------------------------------------------------- Layer C
def free(slots, in_flight, stuck):
    """-> (n, reason). Two stuck items stop new starts in the project; one stuck item costs a slot."""
    if stuck >= 2:
        return 0, "stuck"
    n = max(0, slots - stuck - in_flight)
    return n, ("ok" if n else "full")


def reason_text(reason, lang=None):
    """A reason code ("raise", "drop+projected", "full") as a sentence in the user's language."""
    import i18n
    head, _, tail = (reason or "").partition("+")
    text = i18n.t("capacity.r." + head, lang) if head else ""
    return text + (i18n.t("capacity.r." + tail, lang) if tail else "")


# ---------------------------------------------------------------- state
def _state_path():
    return os.path.join(sl.home_state(), "capacity.json")


def load_state():
    try:
        if os.path.islink(_state_path()):
            return {}
        with open(_state_path(), encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
    if not isinstance(data.get("T"), int) or isinstance(data.get("T"), bool):
        data.pop("T", None)                       # a wrong-typed file reads as empty, never a traceback
    for name in ("expedite", "projects", "user_cap", "user_cap_expired", "project_caps"):
        if not isinstance(data.get(name), dict):
            data.pop(name, None)
    return data


def save_state(data):
    try:
        os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
        sl.atomic_write(_state_path(), json.dumps(data, indent=1, sort_keys=True) + "\n")
    except (OSError, sl.StudioError):
        pass


# ---------------------------------------------------------------- reading the world
def budget_inputs(now=None):
    """The budget as the three numbers Layer A needs: {level, pace, projected, h5, nodata}. Nothing live reads as
    the normal level with `nodata` set (plan: never refuse every start for lack of data)."""
    import budget
    now = now or time.time()
    cur = budget.current(now)
    # R-1104: with `five_hour_window` off, plan_limits drops five_hour, so h5 is None and the start gate never refuses on it
    # (the host enforces any real limit anyway).
    limits = budget.plan_limits(budget.load().get("limits") or {})
    week = limits.get("seven_day")
    pace = None
    if isinstance(week, dict) and week.get("used") is not None and (week.get("resets_at") or 0) > now:
        pace = week["used"] - 100.0 * (1 - (week["resets_at"] - now) / budget.WEEK)
    outlook = budget.week_outlook(limits, now=now)
    five = limits.get("five_hour")
    h5 = five["used"] if isinstance(five, dict) and five.get("used") is not None and (five.get("resets_at") or 0) > now else None
    resets_at = week["resets_at"] if isinstance(week, dict) and (week.get("resets_at") or 0) > now else None
    return {"level": cur["level"], "pace": pace, "projected": outlook["projected"] if outlook else None, "h5": h5,
            "nodata": cur["reason"].startswith("no budget data"), "resets_at": resets_at}


# ---------------------------------------------------------------- your lane cap
CAP_MAX = 8


def set_cap(n, by, now=None, inputs=None, slug=None):
    """Record your lane cap once: {n, by, at, until, until_src}. It ends at the next weekly reset the budget knows
    (until_src "budget"), else now + 7 days ("7d"). `slug` sets that project alone (it wins over the all-projects cap);
    without it the cap applies to every project. Returns the record."""
    import budget
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= CAP_MAX:
        raise sl.StudioError("the lane cap is a whole number from 1 to %d" % CAP_MAX)
    now = now or time.time()
    try:
        resets = (inputs or budget_inputs(now)).get("resets_at")
    except Exception:
        resets = None
    live = resets is not None and resets > now
    rec = {"n": n, "by": by, "at": int(now), "until": int(resets if live else now + budget.WEEK),
           "until_src": "budget" if live else "7d"}
    with sl.home_lock("capacity"):
        state = load_state()
        if slug:
            state["project_caps"] = dict(state.get("project_caps") or {}, **{slug: rec})
        else:
            state["user_cap"] = rec
            state.pop("user_cap_expired", None)
        save_state(state)
    return rec


def clear_cap(slug=None):
    """Remove your cap: that project's own cap with `slug`, else the all-projects cap (and any unreported expiry)."""
    with sl.home_lock("capacity"):
        state = load_state()
        if slug:
            caps = dict(state.get("project_caps") or {})
            caps.pop(slug, None)
            state["project_caps"] = caps
        else:
            state.pop("user_cap", None)
            state.pop("user_cap_expired", None)
        save_state(state)


def slug_of(project):
    """The portfolio slug of a project folder, or None when it is not a managed project of the list."""
    import livework
    me = livework.canon(project)
    rows, _ = portfolio_snapshot()
    return next((r["slug"] for r in rows if livework.canon(r["path"]) == me), None)


def ack_expired():
    """The report has said once that your cap expired: forget it."""
    with sl.home_lock("capacity"):
        state = load_state()
        if state.pop("user_cap_expired", None) is not None:
            save_state(state)


def _aged(item, now):
    days = sl.appetite_days(item)
    since = sl._epoch(item.get("doing_since"))
    return days is not None and since is not None and now - since > AGE_FACTOR * days * 86400


def _live(item, now):
    """True when the item's claim is held by a session that is live now (a fresh claim record). Only that is work in flight."""
    return sl.claim_state(item, now) == "live"


def parallel_ready(projects):
    """{slug: [ready item ids]}: per project, ready items that fit the free slots and do not compete: no file in common
    with one already picked, and at most one that takes the suite lock. Paused and protected projects list nothing."""
    out = {}
    for slug, row in projects.items():
        if row.get("paused") or row.get("private"):
            continue
        taken, suite, picked = set(), False, []
        for item in row.get("ready_items") or ():
            if len(picked) >= (row.get("free") or 0):
                break
            if item.get("files") is None:                  # no declared files: overlap unknown, never listed as fitting
                continue
            files = set(item["files"])
            if files & taken or (item.get("suite") and suite):
                continue
            taken |= files
            suite = suite or bool(item.get("suite"))
            picked.append(item["id"])
        if picked:
            out[slug] = picked
    return out


def overlap_unknown(projects):
    """{slug: [ready item ids]} for ready items with no `files` field: whether they compete is unknown, so they are
    reported apart and never counted as fitting. Only projects with a free slot; paused and protected ones list nothing."""
    out = {}
    for slug, row in projects.items():
        if row.get("paused") or row.get("private") or not row.get("free"):
            continue
        ids = [i["id"] for i in row.get("ready_items") or () if i.get("files") is None]
        if ids:
            out[slug] = ids
    return out


def _project_row(slug, entry, items, now, private):
    live = [i for i in items if i["status"] in ("open", "doing")]
    parked = lambda i: i["status"] == "open" and i.get("phase") == "later"
    committed = [i for i in live if not parked(i)]
    ready = [i for i in committed if i["status"] == "open" and not i.get("blocked")]
    doing = [i for i in live if i["status"] == "doing" and _live(i, now)]       # a doing item nobody holds is not in flight
    stuck = sum(1 for i in doing if i.get("blocked") or _aged(i, now))          # stuck is a doing item only, never a blocked open one
    starts = [sl._epoch(i.get("started_at")) for i in items]
    starts = [s for s in starts if s is not None]
    ready_items = [{"id": i["id"], "files": [str(f) for f in i["files"]] if isinstance(i.get("files"), list) else None,
                    "suite": bool(i.get("suite"))} for i in ready]
    return {"slug": slug, "path": entry["path"], "priority": entry.get("priority"), "paused": entry.get("channel") == "off",
            "committed": len(committed), "ready": len(ready), "in_flight": len(doing), "stuck": stuck,
            "waited_s": (now - max(starts)) if starts else None, "private": private, "ready_items": ready_items}


def portfolio_snapshot(now=None, current=None):
    """-> (rows, skipped). One row per managed project of the portfolio list; `current` = (project, data) swaps in the
    caller's ledger (not yet saved). A bad path or a ledger that fails to load goes to `skipped` with a code."""
    import livework
    import portfolio
    now = now or time.time()
    reg = portfolio.load()
    if not reg or not reg["projects"]:
        return [], [("*", "no_list")]
    private = {e["path"] for e in reg["projects"] if livework.is_private(e["path"])}
    ids = portfolio._ids(reg["projects"], private)
    rows, skipped = [], []
    mine = livework.canon(current[0]) if current else None
    for e in reg["projects"]:
        path, slug = e["path"], ids[e["path"]]
        try:
            if not os.path.isdir(path) or not livework.has_ledger(path):
                skipped.append((slug, "unmanaged"))
                continue
            if path in private:                               # counts from the card, the ledger stays closed
                tasks, stale = portfolio._tasks(path, True, raw=True)
                tasks = tasks or {"doing": 0, "next": 0, "later": 0}
                rows.append({"slug": slug, "path": path, "priority": e.get("priority"), "paused": e.get("channel") == "off",
                             "committed": tasks["doing"] + tasks["next"], "ready": tasks["next"], "in_flight": tasks["doing"],
                             "stuck": stale or 0, "waited_s": None, "private": True})
                continue
            data = current[1] if mine and livework.canon(path) == mine else sl.load_ledger(path)
            rows.append(_project_row(slug, e, data["items"], now, False))
        except Exception:
            skipped.append((slug, "load_error"))
    return rows, skipped


def compute(now=None, current=None, inputs=None, write=True):
    """The whole picture: {T, reason, level, nodata, h5, projects: {slug: {...}}, skipped}. Writes the cache
    (`capacity.json`) that the status line and the live feed read, so they never scan every ledger."""
    now = now or time.time()
    inputs = inputs or budget_inputs(now)
    rows, skipped = portfolio_snapshot(now, current)
    state = load_state()
    in_use = sum(r["in_flight"] for r in rows if not r["paused"])         # a paused project holds no slot and counts for nothing
    cap = state.get("user_cap")
    cap_live = isinstance(cap, dict) and isinstance(cap.get("n"), int) and (cap.get("until") or 0) > now
    expired = state.get("user_cap_expired") or (cap if cap and not cap_live else None)
    if cap_live:                                   # your cap replaces the level ceiling; the split below still applies
        T, reason = cap["n"], "user_cap"
    else:
        T, reason = total(state.get("T"), inputs["level"], inputs.get("pace"), in_use, inputs.get("projected"),
                          state.get("last_raise"), _today(now))
    shares = split(T, rows, cap["n"] if cap_live else None)
    pcaps = {k: v for k, v in (state.get("project_caps") or {}).items()
             if isinstance(v, dict) and isinstance(v.get("n"), int) and (v.get("until") or 0) > now}
    for slug, rec in pcaps.items():                # a project's own cap wins; paused and idle projects stay at 0
        if slug in shares and shares[slug]["floor"] > 0:
            shares[slug].update(slots=rec["n"], cap=rec["n"], reason="project_cap")
    projects = {}
    for r in rows:
        s = shares[r["slug"]]
        n, why = free(s["slots"], r["in_flight"], r["stuck"])
        projects[r["slug"]] = {"ready_items": r.get("ready_items") or [],
                               "slots": s["slots"], "used": r["in_flight"], "free": n, "stuck": r["stuck"], "floor": s["floor"],
                               "cap": s["cap"], "weight": s["weight"], "layer": s["reason"], "free_reason": why,
                               "paused": r["paused"], "private": r["private"], "path": r["path"]}
    out = {"T": T, "reason": reason, "level": inputs["level"], "nodata": inputs["nodata"], "h5": inputs.get("h5"),
           "projects": projects, "skipped": skipped, "user_cap": cap if cap_live else None, "cap_expired": expired,
           "project_caps": pcaps,       # the real portfolio total: what runs now of what the slots allow, paused projects aside
           "portfolio": {"used": sum(v["used"] for v in projects.values() if not v["paused"]),
                         "slots": sum(v["slots"] for v in projects.values() if not v["paused"])}}
    if write and rows:
        new = {"T": T, "level": inputs["level"], "at": int(now), "expedite": state.get("expedite"),
               "user_cap": cap if cap_live else None, "user_cap_expired": expired, "project_caps": pcaps,
               "last_raise": _today(now) if state.get("T") is not None and T > state["T"] else state.get("last_raise"),
               "projects": {k: {"used": v["used"], "slots": v["slots"]} for k, v in projects.items()}}
        if new != {k: state.get(k) for k in new}:
            with sl.home_lock("capacity"):
                new["expedite"] = load_state().get("expedite")      # another start may have taken the urgent slot meanwhile
                save_state(new)
    return out


def cached():
    """The last computed {T, level, projects: {slug: {used, slots}}} or None. Never scans a ledger ([perf])."""
    st = load_state()
    if st.get("T") is None:
        return None
    return {"T": st["T"], "level": st.get("level"), "projects": st.get("projects") or {}}


# ---------------------------------------------------------------- the gate
def _expedite_taken(state, project_slug, item_id):
    """The single portfolio slot is taken by another item that is still `doing` (closing it frees the slot)."""
    held = state.get("expedite")
    if not held or (held.get("item"), held.get("project")) == (item_id, project_slug):
        return False
    try:
        import portfolio
        reg = portfolio.load() or {"projects": []}
        for e in reg["projects"]:
            if sl.path_key(e["path"]) == held.get("key") or portfolio.livework.canon(e["path"]) == held.get("path"):
                return sl.find_item(sl.load_ledger(e["path"]), held["item"])["status"] == "doing"
    except Exception:
        return False
    return False


def check_start(project, data, item, expedite=False, now=None, inputs=None):
    """None to allow the start, or a refusal {code, project, used, slots, reason} (the caller words it through i18n).
    Fails open: no portfolio list, a project outside it, or any error computing means the start goes ahead (plan C2)."""
    try:
        import livework
        now = now or time.time()
        pic = compute(now, current=(project, data), inputs=inputs)
        me = livework.canon(project)
        slug = next((k for k, v in pic["projects"].items() if livework.canon(v["path"]) == me), None)
        if slug is None or pic["projects"][slug]["paused"]:
            return None
        row = pic["projects"][slug]
        name = slug if row["private"] else os.path.basename(me.rstrip(os.sep))
        if pic["h5"] is not None and pic["h5"] >= H5_GATE:
            return {"code": "h5", "project": name, "used": row["used"], "slots": row["slots"], "reason": "h5", "h5": int(pic["h5"])}
        if row["free"] > 0:
            return None
        if expedite:
            with sl.home_lock("capacity"):
                state = load_state()
                if _expedite_taken(state, slug, item["id"]):
                    return {"code": "expedite_taken", "project": name, "used": row["used"], "slots": row["slots"], "reason": "expedite_taken"}
                state["expedite"] = {"item": item["id"], "project": slug, "key": sl.path_key(me), "since": int(now)}
                save_state(state)
            return None
        return {"code": "full", "project": name, "used": row["used"], "slots": row["slots"], "reason": row["free_reason"]}
    except Exception:
        return None
