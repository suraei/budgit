"""Budget levels: how Budgit paces its work against the user's plan limits. Zero model tokens.

The level changes HOW the team works (models, agent count, research depth, when to checkpoint) and
never WHAT it does: the invariants below hold at every level.

Signals, most trustworthy first: the user's explicit override (`budgit budget set`), the plan's own 5-hour
and weekly percentages (status line `rate_limits`, or `budgit budget record` after the model reads
get_usage), else "normal". The level follows pace (used % minus elapsed % of the weekly window), with
absolute ceilings and a 5-point hysteresis so it does not flap.
"""
import contextlib
import fcntl
import json
import math
import os
import re
import time

import studiolib as sl

LEVELS = ("ample", "normal", "tight", "critical")
# `budgit budget set` takes the English names and the Spanish ones the first versions used.
LEVEL_ALIASES = {"holgado": "ample", "ajustado": "tight", "critico": "critical", "crítico": "critical"}


def level_name(word):
    """'tight', 'TIGHT', 'ajustado' -> 'tight'; anything else -> None."""
    word = str(word).strip().lower()
    word = LEVEL_ALIASES.get(word, word)
    return word if word in LEVELS else None


def label(level, lang=None):
    """The level as the user reads it: AMPLE or HOLGADO, by the user's language."""
    import i18n
    return i18n.t("budget.level." + level, lang)
WEEK = 7 * 86400
HYSTERESIS = 5

POLICY = {
    "ample": dict(brain="opus", models=dict(judgement="opus", work="sonnet", mechanical="haiku"), subagents=4, agent_calls=80, checkpoint_at=0.75,
                  research="new angles allowed (>=2 agreeing sources on decision-critical facts)",
                  defer="nothing", other_llm="images; second opinions welcome"),
    "normal": dict(brain="opus", models=dict(judgement="opus", work="sonnet", mechanical="haiku"), subagents=3, agent_calls=60, checkpoint_at=0.65,
                   research="reuse saved research first, one agent per new angle",
                   defer="announce heavy runs before starting", other_llm="images; bulk drafts when it saves quota"),
    "tight": dict(brain="opus", models=dict(judgement="sonnet", work="sonnet", mechanical="haiku"), subagents=2, agent_calls=40, checkpoint_at=0.55,
                  research="reuse saved research; at most one new angle plus one confirmation",
                  defer="ask the user before any heavy run (batches, broad research); queue it for after the reset",
                  other_llm="push images and bulk drafts/reviews to the ChatGPT lane"),
    "critical": dict(brain="sonnet", models=dict(judgement="sonnet", work="none", mechanical="haiku"), subagents=0, agent_calls=25, checkpoint_at=0.45,
                     research="none new: saved research only",
                     defer="no new agents except the verifier of a gate the user is waiting on; offer to resume after the reset",
                     other_llm="only the ChatGPT lane for anything heavy"),
}

INVARIANTS = ("Unchanged at every level: input ledger capture, phase gates and their artefacts, "
              "verification before 'done', progress.md and the checkpoint before compaction, agent reports saved "
              "to files, and the user's scope (if the budget cannot cover it, offer to defer or let the user choose "
              "what goes in; never shrink it silently).")


def policy(level):
    return POLICY[level]


_JUDGEMENT = ("verifier", "critic", "review", "security", "architect", "auditor")
_MECHANICAL = ("explore", "claude-code-guide", "statusline")


def role_for(subagent_type):
    """Role of an agent type (ROLES): judgement reviews/verifies, mechanical looks things up, work does the rest."""
    t = (subagent_type or "general-purpose").lower()
    if t == "plan" or any(k in t for k in _JUDGEMENT):
        return "judgement"
    if any(k in t for k in _MECHANICAL):
        return "mechanical"
    return "work"


def model_for(subagent_type, level):
    m = POLICY[level]["models"]
    model = m[role_for(subagent_type)]
    return m["mechanical"] if model == "none" else model


TIERS = ("haiku", "sonnet", "opus", "fable")          # cheapest to dearest; the index is the rank
ESCALATE = re.compile(r"(?<![A-Za-z-])ESCALATE:")          # "DE-ESCALATE:" and "NOESCALATE:" are not the marker


def tier(model):
    """Rank of a model name or id ("opus", "claude-sonnet-5-5", "opus[1m]"), or None when it names no known family
    (inherit, a full id of something else): such a value is never rewritten."""
    text = str(model or "").lower()
    for rank in range(len(TIERS) - 1, -1, -1):
        if TIERS[rank] in text:
            return rank
    return None


def escalated(prompt):
    """R-383: a prompt that carries `ESCALATE: <why>` keeps the model it asked for."""
    return bool(ESCALATE.search(str(prompt or "")))


def above_policy(subagent_type, level, given):
    """-> the role's model when `given` is dearer than it, else None. A role the level gives no model ("none": work at critical,
    where the policy is to start no new agents) has no ceiling to lower to: model_for would answer haiku, and a caller that
    chose to run one anyway is not pushed down to it."""
    if POLICY[level]["models"][role_for(subagent_type)] == "none":
        return None
    allowed = model_for(subagent_type, level)
    rank, cap = tier(given), tier(allowed)
    return allowed if rank is not None and cap is not None and rank > cap else None


def policy_mode():
    """BUDGIT_MODEL_POLICY: enforce (default), advise (say it, rewrite nothing) or off."""
    mode = (sl.env("MODEL_POLICY") or "enforce").lower()
    return mode if mode in ("enforce", "advise", "off") else "enforce"


def _rewrites_path():
    return os.path.join(sl.home_state(), "model-rewrites.log")


def record_rewrite(session_id, tool_use_id, given, model):
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    sl.append_text(_rewrites_path(), "%s %s %s %s\n" % (session_id or "-", tool_use_id or "-", given, model))


def rewritten_ids(session_id):
    """tool_use ids whose explicit model the hook lowered to the role's policy in that session."""
    try:
        with open(_rewrites_path(), encoding="utf-8") as f:
            return {p[1] for p in (l.split() for l in f) if len(p) == 4 and p[0] == session_id}
    except OSError:
        return set()


def _fills_path():
    return os.path.join(sl.home_state(), "model-fills.log")


def record_fill(session_id, tool_use_id, model):
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    sl.append_text(_fills_path(), "%s %s %s\n" % (session_id or "-", tool_use_id or "-", model))


def filled_ids(session_id):
    """tool_use ids whose model the hook filled in that session (comply does not count them as violations)."""
    try:
        with open(_fills_path(), encoding="utf-8") as f:
            return {p[1] for p in (l.split() for l in f) if len(p) == 3 and p[0] == session_id}
    except OSError:
        return set()


ROLES = ("judgement = verifier, plan-critic, design/security review; work = builder, researcher, implementers, "
         "persona-sim; mechanical = lookups, log reading, formatting, summaries")


BUILD_NOTE = (" for plan/design/QA judgement; build sessions: sonnet (executing a reviewed plan, tests, commits, "
              "releases — switch the session model: desktop set_session_model, terminal /model sonnet; back to opus "
              "for a gate decision)")


def models_text(level):
    p = POLICY[level]
    m = p["models"]
    brain = "main model: %s%s" % (p["brain"], BUILD_NOTE)
    return ("%s. On EVERY Agent call pass model= : judgement=%s, work=%s, mechanical=%s (%s). Escalate one step "
            "(work->opus, mechanical->sonnet) only after the cheaper model failed twice or when the user asks, and say so in "
            "one line: put `ESCALATE: <why>` in that call's prompt, otherwise the hook lowers a dearer model= to the role's." % (brain, m["judgement"], m["work"], m["mechanical"], ROLES))


def policy_text(level):
    p = POLICY[level]
    return (models_text(level) + "\n" + "budgit budget: %s. Subagents: at most %d in parallel, ~%d tool calls each; research: %s; "
            "heavy work: %s; other LLM: %s; checkpoint at %d%% of the window.\n%s"
            % (level, p["subagents"], p["agent_calls"], p["research"], p["defer"],
               p["other_llm"], round(p["checkpoint_at"] * 100), INVARIANTS))


def short_line(level, reason):
    p = POLICY[level]
    m = p["models"]
    return ("budgit budget: %s — %s. Brain %s (build sessions: sonnet); pass model= on every agent call: judgement %s, work %s, mechanical %s. "
            "Max %d parallel subagents, ~%d calls each, checkpoint at %d%%. Details: `budgit budget`."
            % (level, reason, p["brain"], m["judgement"], m["work"], m["mechanical"], p["subagents"],
               p["agent_calls"], round(p["checkpoint_at"] * 100)))


def checkpoint_at(level):
    return POLICY[level]["checkpoint_at"]


# --- state ---------------------------------------------------------------------------------------
def _path():
    return os.path.join(sl.home_state(), "budget.json")


def load():
    path = _path()
    try:
        if os.path.islink(path):
            return {}
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data):
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    sl.atomic_write(_path(), json.dumps(data, indent=1))


WINDOW_JITTER = 600     # two reset times this close are the same window (sessions report them to the second, or nearly)


@contextlib.contextmanager
def _locked():
    """One writer at a time: every session of every project reads, merges and rewrites the same file."""
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    try:
        fd = os.open(os.path.join(sl.home_state(), "budget.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


STALE_AFTER = 1800      # a stored value nobody has refreshed for this long is not defended against a fresh reading
MAX_AHEAD = {"seven_day": WEEK + 6 * 3600, "five_hour": 6 * 3600}   # no window ends further ahead than its length (+ slack)


def _plausible(name, value, now):
    used, resets = value
    if resets is None:                      # R-522: the host may send a window with no reset time; the figure still counts
        return 0.0 <= used <= 100.0
    return 0.0 <= used <= 100.0 and math.isfinite(resets) and 0 < resets <= now + MAX_AHEAD[name]


def _merge_window(old, new, authoritative, now, seen_at):
    """R-212 #4: sessions share one snapshot file and lag each other (weekly 45, 34, 45, 38 within five minutes), so a
    snapshot never lowers what is stored for the same window while the stored value is fresh. A later window replaces
    it, an earlier one is ignored. What the user types (`budgit budget record`) is authoritative.
    -> (value to store, True when the reading was taken: only a taken reading keeps the stored value fresh)."""
    if new.get("resets_at") is None:       # R-522: a figure with no reset time is the host's current word (the line used to show an older stored one)
        keep = old.get("resets_at") if isinstance(old, dict) and (old.get("resets_at") or 0) > now else None
        return {"used": new["used"], "resets_at": keep}, True
    if authoritative or not isinstance(old, dict) or old.get("used") is None or old.get("resets_at") is None:
        return new, True
    if now - (seen_at or 0) > STALE_AFTER:
        return new, True
    if old["resets_at"] <= now - WINDOW_JITTER:      # the stored window is over
        return new, True
    if new["resets_at"] > old["resets_at"] + WINDOW_JITTER:
        return new, True
    if new["resets_at"] < old["resets_at"] - WINDOW_JITTER:
        return old, False
    return {"used": max(old["used"], new["used"]), "resets_at": max(old["resets_at"], new["resets_at"])}, \
        new["used"] >= old["used"]


def record(seven_day=None, five_hour=None, extra=None, now=None, authoritative=False, extra_enabled=None):
    """seven_day / five_hour: (used_percent, resets_at_epoch); extra: (spent_usd, monthly_limit_usd); extra_enabled: True or
    False when the account says whether extra usage is switched on, None when it does not say. A reading outside
    what a window can be (used not in 0..100, an end further away than the window is long) is dropped."""
    now = now or time.time()
    with _locked():
        data = load()
        limits = data.setdefault("limits", {})
        seen = data.get("seen") if isinstance(data.get("seen"), dict) else {}
        for name, value in (("seven_day", seven_day), ("five_hour", five_hour)):
            if value is None:
                continue
            pair = (float(value[0]), None if value[1] is None else float(value[1]))
            if _plausible(name, pair, now):
                new = {"used": pair[0], "resets_at": pair[1]}
                limits[name], taken = _merge_window(limits.get(name), new, authoritative, now, seen.get(name))
                if taken:
                    seen[name] = max(now, seen.get(name) or 0)
        if not limits:
            data.pop("limits")
        data["seen"] = seen
        if extra is None and extra_enabled is False:      # R-862: "disabled" with no figures still replaces an older enabled reading
            extra = (0.0, (data.get("extra") or {}).get("limit") or 0.0)
        if extra is not None and all(math.isfinite(float(x)) and float(x) >= 0 for x in extra):
            was = data.get("extra") if isinstance(data.get("extra"), dict) else {}
            flag = extra_enabled if isinstance(extra_enabled, bool) else was.get("enabled")      # a reading that does not say keeps what we knew
            data["extra"] = {"spent": float(extra[0]), "limit": float(extra[1])}
            if isinstance(flag, bool):
                data["extra"]["enabled"] = flag
        data["seen_at"] = max(now, data.get("seen_at") or 0)
        save(data)


# R-522: a weekly sub-limit the host may send under a kind we do not know. It is never the all-models weekly, never named after
# a model, and only ever read from the host's own data: with no host data there is nothing to show.
KNOWN_KINDS = ("five_hour", "seven_day", "spend_limit")


def _kinds_path():
    return os.path.join(sl.home_state(), "limit-kinds.json")


def note_kinds(names):
    """Counts of the unknown limit kinds the host sent (names only: no figures, no text), in the machine state folder."""
    clean = [re.sub(r"[^a-z0-9_.-]", "", str(n).lower())[:40] for n in names]
    clean = [n for n in clean if n and n not in KNOWN_KINDS]
    if not clean:
        return
    with _locked():
        try:
            with open(_kinds_path(), encoding="utf-8") as f:
                counts = json.load(f)
        except (OSError, ValueError):
            counts = {}
        if not isinstance(counts, dict):
            counts = {}
        for n in clean:
            if n in counts or len(counts) < 30:
                counts[n] = int(counts.get(n) or 0) + 1
        os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
        sl.atomic_write(_kinds_path(), json.dumps(counts, indent=1, sort_keys=True))


def record_subcap(used, resets=None, label=None, now=None):
    """The tightest unknown-kind window of the last reading; used None clears it."""
    now = now or time.time()
    with _locked():
        data = load()
        if used is None or not 0.0 <= float(used) <= 100.0:
            if "subcap" not in data:
                return
            data.pop("subcap")
        else:
            clean = re.sub(r"[^A-Za-z0-9 ._-]", "", str(label or "")).strip()[:24] or None
            data["subcap"] = {"used": float(used), "resets_at": resets, "label": clean, "at": now}
        save(data)


def subcap_for_line(seven_used, now=None):
    """-> {used, label} when the host's sub-limit is fresh and tighter than the all-models weekly, else None."""
    now = now or time.time()
    sub = load().get("subcap")
    if seven_used is None or not isinstance(sub, dict) or sub.get("used") is None:
        return None
    if now - (sub.get("at") or 0) > STALE_AFTER or (sub.get("resets_at") is not None and sub["resets_at"] <= now):
        return None
    return {"used": float(sub["used"]), "label": sub.get("label")} if float(sub["used"]) > float(seven_used) else None


def set_override(level):
    chosen = level_name(level)
    if chosen is None:
        raise sl.StudioError("unknown budget level %r (use one of: %s; or %s)"
                             % (level, ", ".join(LEVELS), ", ".join(sorted(LEVEL_ALIASES))))
    with _locked():
        data = load()
        data["override"] = chosen
        save(data)


def clear_override():
    with _locked():
        data = load()
        data.pop("override", None)
        save(data)


# --- classification ------------------------------------------------------------------------------
TIGHT_RATIO = 1.5        # R-865: burning faster than this many times the sustainable pace is tight
CRITICAL_RATIO = 2.5      # ... and this many times is critical
MIN_WEEK_DAYS = 0.25       # a weekly reading in the first 6 h is too small a sample for a rate
MIN_FIVE_SHARE = 0.10      # likewise the first 10 % of the 5-hour window
FIVE_HOUR = 5 * 3600


def _ratio(used, elapsed_share, floor):
    """Used % over the share of its window already gone (1.0 = exactly the pace that lasts the window)."""
    return used / max(elapsed_share, floor) / 100.0 if used > 0 else 0.0


def _level_for(u7, pace, u5, m, r7=0.0, r5=0.0):
    k = 1 - m / 25.0       # hysteresis also relaxes the ratio thresholds (m is 0 or HYSTERESIS)
    if u7 >= 90 - m or u5 >= 90 - m or pace >= 25 - m or max(r7, r5) > CRITICAL_RATIO * k:
        return "critical"
    if pace >= 10 - m or u5 >= 70 - m or u7 >= 80 - m or max(r7, r5) > TIGHT_RATIO * k:
        return "tight"
    if pace <= -15 and u5 < 40 and u7 < 60:
        return "ample"
    return "normal"


def classify(limits, prev=None, now=None):
    """-> (level, reason). A window whose reset time has passed is treated as unused (stale snapshot)."""
    now = now or time.time()
    live = {k: v for k, v in (limits or {}).items()
            if isinstance(v, dict) and (v.get("resets_at") or 0) > now and v.get("used") is not None}
    loose = (limits or {}).get("seven_day")      # R-522: a weekly figure with no reset time still counts (no pace; the ceilings apply)
    if "seven_day" not in live and isinstance(loose, dict) and loose.get("resets_at") is None and loose.get("used") is not None:
        live["seven_day"] = loose
    loose5 = (limits or {}).get("five_hour")     # R-525: likewise a 5-hour figure with no reset time: no pace, the % ceilings apply
    if "five_hour" not in live and isinstance(loose5, dict) and loose5.get("resets_at") is None and loose5.get("used") is not None:
        live["five_hour"] = loose5
    if not live:
        return "normal", "no budget data (no live 5-hour/weekly snapshot)"
    u7 = live["seven_day"]["used"] if "seven_day" in live else 0.0
    u5 = live["five_hour"]["used"] if "five_hour" in live else 0.0
    pace, bits, r7, r5 = 0.0, [], 0.0, 0.0
    if "seven_day" in live and live["seven_day"].get("resets_at") is None:
        bits.append("weekly %d%% used (no reset time given)" % u7)
    elif "seven_day" in live:
        elapsed = 100.0 * (1 - (live["seven_day"]["resets_at"] - now) / WEEK)
        pace = u7 - elapsed
        bits.append("weekly %d%% used with %d%% of the week elapsed (pace %+d)" % (u7, max(elapsed, 0), pace))
        days = max(WEEK * max(elapsed, 0) / 100.0 / 86400, MIN_WEEK_DAYS)
        r7 = _ratio(u7, max(elapsed, 0) / 100.0, MIN_WEEK_DAYS * 86400 / WEEK)       # R-865: x the sustainable pace (100/7 % a day)
        bits.append("%d%% a day so far against %d%% a day that keeps you inside the week (%.1fx)" % (u7 / days, 100 / 7.0, r7))
        if "five_hour" not in live:       # R-522: no 5-hour window (Ultra): a fast weekly burn has nothing else to stop it
            bits.append("no 5-hour window, so the daily pace off the weekly is the guard")
    if "five_hour" in live:
        five_at = live["five_hour"].get("resets_at")
        if five_at is None:               # R-525: no reset time, so no pace: the % alone decides
            bits.append("5-hour window %d%% used (no reset time given)" % u5)
        else:
            share = min(max(1 - (five_at - now) / FIVE_HOUR, 0.0), 1.0)
            r5 = _ratio(u5, share, MIN_FIVE_SHARE)
            bits.append("5-hour window %d%% used with %d%% of it elapsed (%.1fx the pace that lasts it)" % (u5, share * 100, r5))
    raw = _level_for(u7, pace, u5, 0, r7, r5)
    strict = _level_for(u7, pace, u5, HYSTERESIS, r7, r5)
    rank = LEVELS.index
    level = raw
    if prev in LEVELS:
        level = max(raw, min(prev, strict, key=rank), key=rank)
    return level, "; ".join(bits)


def week_outlook(limits, now=None):
    """Where the weekly window lands at the average rate since its start. None without a live weekly snapshot;
    projected/runs_out None in the first 10 % of the week (too little history to extrapolate).
    -> {used, resets_at, projected (% at reset), runs_out (epoch when 100 % is hit before the reset, else None)}"""
    now = now or time.time()
    w = (limits or {}).get("seven_day")
    if isinstance(w, dict) and w.get("used") is not None and w.get("resets_at") is None:    # R-522: the figure with no reset time
        return {"used": float(w["used"]), "resets_at": None, "projected": None, "runs_out": None}
    if not (isinstance(w, dict) and w.get("used") is not None and (w.get("resets_at") or 0) > now):
        return None
    used, resets = float(w["used"]), float(w["resets_at"])
    out = {"used": used, "resets_at": resets, "projected": None, "runs_out": None}
    elapsed = now - (resets - WEEK)
    if elapsed < 0.1 * WEEK:
        return out
    rate = used / elapsed                        # % per second so far
    out["projected"] = rate * WEEK
    if out["projected"] > 100 and rate > 0:
        out["runs_out"] = max(now, resets - WEEK + 100 / rate)
    return out


STRONG_FROM = 0.35      # R-330: before this share of the week has passed, the projection alone never reaches agotado or enfermo (it tops out at cansado)


def brote_state(limits, now=None):
    """R-114: Brote's health from the weekly projection (the approved mock's `outlook()`), plus the 5 h overlay.
    -> {state, fresh, used, projection, runs_out, resets_at, h5}. state "unknown" (fresh False) without a live weekly
    snapshot; the first 10 % of the week has no projection and reads as contento."""
    now = now or time.time()
    five = (limits or {}).get("five_hour")
    h5 = int(five["used"]) if isinstance(five, dict) and five.get("used") is not None \
        and (five.get("resets_at") or 0) > now else 0
    o = week_outlook(limits, now=now)
    if o is None:
        return {"state": "dormido" if h5 >= 100 else "unknown", "fresh": False, "used": None, "projection": None,
                "runs_out": None, "resets_at": None, "h5": h5}
    used, proj = o["used"], o["projected"]
    early = o["resets_at"] is None or (1 - (o["resets_at"] - now) / WEEK) < STRONG_FROM     # R-330: too soon for a straight line to be believed
    if used >= 100 or h5 >= 100:
        state = "dormido"
    elif used >= 90 or (proj is not None and proj > 140 and not early):
        state = "enfermo"
    elif proj is None:
        state = "contento"
    elif proj > 120 and not early:
        state = "agotado"
    elif proj > 100:
        state = "cansado"
    elif proj > 90:
        state = "justo"
    elif proj > 70:
        state = "contento"
    else:
        state = "radiante"
    return {"state": state, "fresh": True, "used": used, "projection": proj, "runs_out": o["runs_out"],
            "resets_at": o["resets_at"], "h5": h5}


def current(now=None):
    """-> {level, reason, source}. Persists the computed level (the hysteresis needs the previous one)."""
    data = load()
    if data.get("override") in LEVELS:
        return {"level": data["override"], "reason": "set by you (budgit budget auto to release)",
                "source": "override"}
    limits = data.get("limits")
    if isinstance(limits, dict) and (now or time.time()) - (data.get("seen_at") or 0) > STALE_AFTER:
        limits = {k: v for k, v in limits.items() if not (isinstance(v, dict) and v.get("resets_at") is None)}   # R-522: a figure with no reset time only counts while fresh
    level, reason = classify(limits, prev=data.get("level"), now=now)
    if data.get("level") != level:
        with _locked():                       # re-read under the lock so a merge that just happened is not overwritten
            fresh = load()
            fresh["level"] = level
            save(fresh)
    extra = data.get("extra")
    # R-862: the limit is a ceiling, never spend. Nothing is claimed for a switched-off account, and with the flag unknown only
    # an amount actually spent (> 0) against a real limit is worth a line.
    if extra and level in ("tight", "critical") and extra.get("enabled") is not False \
            and (extra.get("enabled") is True or (extra["spent"] > 0 and extra["limit"] > 0)):
        # real money only when the account is known to have extra usage on; without the flag we do not claim it
        tail = "real money once the plan limit is hit" if extra.get("enabled") is True \
            else "billed as real money only if extra usage is enabled on your account"
        reason += "; extra usage $%.2f of $%.0f this month (%s)" % (extra["spent"], extra["limit"], tail)
    return {"level": level, "reason": reason, "source": "limits" if data.get("limits") else "none"}


# --- announcements ---------------------------------------------------------------------------------
def notice(session_id, now=None):
    """Once per level change per session -> (message for the user in their language, context for the model) or None."""
    if not sl.valid_session(session_id):
        return None
    cur = current(now=now)
    flag = os.path.join(sl.home_state(), "budget-seen", session_id)
    try:
        with open(flag, encoding="utf-8") as f:
            last = f.read().strip()
    except OSError:
        last = "normal"          # a fresh session at normal pace has nothing to announce
    if cur["level"] == last:
        return None
    os.makedirs(os.path.dirname(flag), mode=0o700, exist_ok=True)
    sl.atomic_write(flag, cur["level"])
    import i18n
    lang = i18n.detect()
    text = i18n.t("budget.notice", lang, level=label(cur["level"], lang), reason=cur["reason"])
    return "budgit: " + text, policy_text(cur["level"]) + "\nWhy: " + cur["reason"]
