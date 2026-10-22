"""Budget levels: how Budgit paces its work against the user's plan limits. Zero model tokens.

The level changes HOW the team works (models, agent count, research depth, when to checkpoint) and
never WHAT it does: the invariants below hold at every level.

Signals, most trustworthy first: the user's explicit override (`budgit budget set`), the plan's own 5-hour
and weekly percentages (status line `rate_limits`, or `budgit budget record` after the model reads
get_usage), else "normal". The level follows pace (used % minus elapsed % of the weekly window), with
absolute ceilings and a 5-point hysteresis so it does not flap.
"""
import json
import os
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


ROLES = ("judgement = verifier, plan-critic, design/security review; work = researcher, implementers, "
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
            "one line." % (brain, m["judgement"], m["work"], m["mechanical"], ROLES))


def policy_text(level):
    p = POLICY[level]
    return (models_text(level) + "\n" + "budgit budget: %s. Subagents: at most %d in parallel, ~%d tool calls each; research: %s; "
            "heavy work: %s; other LLM: %s; checkpoint at %d%% of the window.\n%s"
            % (level, p["subagents"], p["agent_calls"], p["research"], p["defer"],
               p["other_llm"], round(p["checkpoint_at"] * 100), INVARIANTS))


def short_line(level, reason):
    p = POLICY[level]
    m = p["models"]
    return ("budgit budget: %s — %s. Brain %s (build sessions: sonnet); agents (pass model= on every call): judgement %s, work %s, mechanical %s. "
            "At most %d parallel subagents, ~%d tool calls each, checkpoint at %d%% of the window; "
            "ledger, phase gates, verification, progress.md and the user's scope are unchanged. Details: `budgit budget`."
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


def record(seven_day=None, five_hour=None, extra=None, now=None):
    """seven_day / five_hour: (used_percent, resets_at_epoch); extra: (spent_usd, monthly_limit_usd)."""
    data = load()
    limits = data.setdefault("limits", {})
    for name, value in (("seven_day", seven_day), ("five_hour", five_hour)):
        if value is not None:
            limits[name] = {"used": float(value[0]), "resets_at": float(value[1])}
    if extra is not None:
        data["extra"] = {"spent": float(extra[0]), "limit": float(extra[1])}
    data["seen_at"] = now or time.time()
    save(data)


def set_override(level):
    chosen = level_name(level)
    if chosen is None:
        raise sl.StudioError("unknown budget level %r (use one of: %s; or %s)"
                             % (level, ", ".join(LEVELS), ", ".join(sorted(LEVEL_ALIASES))))
    data = load()
    data["override"] = chosen
    save(data)


def clear_override():
    data = load()
    data.pop("override", None)
    save(data)


# --- classification ------------------------------------------------------------------------------
def _level_for(u7, pace, u5, m):
    if u7 >= 90 - m or u5 >= 90 - m or pace >= 25 - m:
        return "critical"
    if pace >= 10 - m or u5 >= 70 - m or u7 >= 80 - m:
        return "tight"
    if pace <= -15 and u5 < 40 and u7 < 60:
        return "ample"
    return "normal"


def classify(limits, prev=None, now=None):
    """-> (level, reason). A window whose reset time has passed is treated as unused (stale snapshot)."""
    now = now or time.time()
    live = {k: v for k, v in (limits or {}).items()
            if isinstance(v, dict) and (v.get("resets_at") or 0) > now and v.get("used") is not None}
    if not live:
        return "normal", "no budget data (no live 5-hour/weekly snapshot)"
    u7 = live["seven_day"]["used"] if "seven_day" in live else 0.0
    u5 = live["five_hour"]["used"] if "five_hour" in live else 0.0
    pace, bits = 0.0, []
    if "seven_day" in live:
        elapsed = 100.0 * (1 - (live["seven_day"]["resets_at"] - now) / WEEK)
        pace = u7 - elapsed
        bits.append("weekly %d%% used with %d%% of the week elapsed (pace %+d)" % (u7, max(elapsed, 0), pace))
    if "five_hour" in live:
        bits.append("5-hour window %d%% used" % u5)
    raw = _level_for(u7, pace, u5, 0)
    strict = _level_for(u7, pace, u5, HYSTERESIS)
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
    if used >= 100 or h5 >= 100:
        state = "dormido"
    elif used >= 90 or (proj is not None and proj > 140):
        state = "enfermo"
    elif proj is None:
        state = "contento"
    elif proj > 120:
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
    level, reason = classify(data.get("limits"), prev=data.get("level"), now=now)
    if data.get("level") != level:
        data["level"] = level
        save(data)
    extra = data.get("extra")
    if extra and level in ("tight", "critical"):
        reason += "; extra usage $%.2f of $%.0f this month (real money once the plan limit is hit)" % (
            extra["spent"], extra["limit"])
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
