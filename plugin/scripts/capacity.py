"""R-286 task 3: how many items may run at once, and who gets which slot.

Three layers, all plain arithmetic (zero model tokens):
  A. total()  - the portfolio's limit T from the budget level, kept between runs (drops at once, rises +1 a day).
  B. split()  - T shared between projects by priority weight, at least 1 each, never above what is ready.
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

LEVEL_LIMITS = {"ample": (1, 6), "normal": (1, 5), "tight": (1, 3), "critical": (0, 1)}     # (floor, ceiling)
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
def split(T, projects):
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
    if not elig or T <= 0:
        return out
    if len(elig) >= T:
        order = sorted(elig, key=lambda p: (-out[p["slug"]]["weight"], -(p.get("waited_s") if p.get("waited_s") is not None else math.inf)))
        for p in order[:T]:
            out[p["slug"]].update(slots=1, reason="floor")
        for p in order[T:]:
            out[p["slug"]]["reason"] = "no_room"
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
    for name in ("expedite", "projects"):
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
    limits = budget.load().get("limits") or {}
    week = limits.get("seven_day")
    pace = None
    if isinstance(week, dict) and week.get("used") is not None and (week.get("resets_at") or 0) > now:
        pace = week["used"] - 100.0 * (1 - (week["resets_at"] - now) / budget.WEEK)
    outlook = budget.week_outlook(limits, now=now)
    five = limits.get("five_hour")
    h5 = five["used"] if isinstance(five, dict) and five.get("used") is not None and (five.get("resets_at") or 0) > now else None
    return {"level": cur["level"], "pace": pace, "projected": outlook["projected"] if outlook else None, "h5": h5,
            "nodata": cur["reason"].startswith("no budget data")}


def _aged(item, now):
    days = sl.appetite_days(item)
    since = sl._epoch(item.get("doing_since"))
    return days is not None and since is not None and now - since > AGE_FACTOR * days * 86400


def _project_row(slug, entry, items, now, private):
    live = [i for i in items if i["status"] in ("open", "doing")]
    parked = lambda i: i["status"] == "open" and i.get("phase") == "later"
    committed = [i for i in live if not parked(i)]
    ready = [i for i in committed if i["status"] == "open" and not i.get("blocked")]
    doing = [i for i in live if i["status"] == "doing"]
    stuck = sum(1 for i in live if i.get("blocked")) + sum(1 for i in doing if not i.get("blocked") and _aged(i, now))
    starts = [sl._epoch(i.get("started_at")) for i in items]
    starts = [s for s in starts if s is not None]
    return {"slug": slug, "path": entry["path"], "priority": entry.get("priority"), "paused": entry.get("channel") == "off",
            "committed": len(committed), "ready": len(ready), "in_flight": len(doing), "stuck": stuck,
            "waited_s": (now - max(starts)) if starts else None, "private": private}


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
                tasks, stale = portfolio._tasks(path, True)
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
    in_use = sum(r["in_flight"] for r in rows)
    T, reason = total(state.get("T"), inputs["level"], inputs.get("pace"), in_use, inputs.get("projected"),
                      state.get("last_raise"), _today(now))
    shares = split(T, rows)
    projects = {}
    for r in rows:
        s = shares[r["slug"]]
        n, why = free(s["slots"], r["in_flight"], r["stuck"])
        projects[r["slug"]] = {"slots": s["slots"], "used": r["in_flight"], "free": n, "stuck": r["stuck"], "floor": s["floor"],
                               "cap": s["cap"], "weight": s["weight"], "layer": s["reason"], "free_reason": why,
                               "paused": r["paused"], "private": r["private"], "path": r["path"]}
    out = {"T": T, "reason": reason, "level": inputs["level"], "nodata": inputs["nodata"], "h5": inputs.get("h5"),
           "projects": projects, "skipped": skipped}
    if write and rows:
        new = {"T": T, "level": inputs["level"], "at": int(now), "expedite": state.get("expedite"),
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
