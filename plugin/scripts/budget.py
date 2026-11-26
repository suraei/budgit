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
import hashlib
import json
import math
import os
import re
import stat
import time
import unicodedata

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


TIERS = ("haiku", "sonnet", "opus", "fable")          # cheapest to dearest; the index is the rank

# R-915 (2): ONE table, role -> (default model, ceiling). Every Agent launch is rewritten to min(asked, ceiling); a launch
# with no model= gets the default; `ESCALATE: <why>` lifts the ceiling one step (logged). Unknown types are "work" (sonnet).
# The budget level can only lower these further (POLICY), never raise them.
ROLE_TABLE = {"judgement": ("opus", "opus"), "work": ("sonnet", "sonnet"), "mechanical": ("haiku", "haiku")}
FORK = "fork"           # a fork keeps the parent's model (the tool ignores model= there): never touched


def is_fork(subagent_type):
    return str(subagent_type or "").strip().lower() == FORK


def _cheaper(a, b):
    return a if TIERS.index(a) <= TIERS.index(b) else b


def model_for(subagent_type, level):
    m = POLICY[level]["models"]
    role = role_for(subagent_type)
    model = m[role]
    model = m["mechanical"] if model == "none" else model
    return _cheaper(model, ROLE_TABLE[role][0])


def ceiling_for(subagent_type, level, escalate=False):
    """Dearest model the role may run: the table's ceiling (one step up with ESCALATE), never above the level's model."""
    role = role_for(subagent_type)
    cap = ROLE_TABLE[role][1]
    if POLICY[level]["models"][role] != "none":      # R-1018: "none" (work at critical) keeps the table ceiling, never above normal's
        cap = _cheaper(cap, model_for(subagent_type, level))
    if escalate == MARK_FABLE:                        # a written reason lifts any role to the top model
        cap = TIERS[-1]
    elif escalate:
        cap = TIERS[min(TIERS.index(cap) + 1, len(TIERS) - 1)]
    return cap
ESCALATE = re.compile(r"(?<![A-Za-z-])ESCALATE:")          # "DE-ESCALATE:" and "NOESCALATE:" are not the marker
ESCALATE_FABLE = re.compile(r"(?<![A-Za-z-])ESCALATE-FABLE:[ \t]*(\S.{7,})")      # the reason (8+ characters) is on the same line
_REASON = re.compile(r"(?<![A-Za-z-])ESCALATE(?:-FABLE)?:[ \t]*(.*)")
MARK_ONE, MARK_FABLE = "ESC", "ESC-FABLE"


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


def marker(prompt):
    """The escalation marker a prompt carries: MARK_FABLE (`ESCALATE-FABLE: <why>`), MARK_ONE (`ESCALATE: <why>`) or ""."""
    text = str(prompt or "")
    if _fable_reason(text):
        return MARK_FABLE
    return MARK_ONE if ESCALATE.search(text) else ""


def _fable_reason(text):
    """The reason of the first `ESCALATE-FABLE:` that starts a line (after spaces; never inside a ``` fence, so never quoted, in
    backticks or mid-sentence) and holds 8 characters once format characters (category Cf, zero-width ones included) and the
    spaces around it are stripped. A reason that starts with `<` is a placeholder, not a reason. Else ""."""
    fence = None                                     # (character, length) of the open fence
    for line in text.split("\n"):                    # only a newline ends a line: other separators do not start one
        body = line.lstrip(" ")
        if len(line) - len(body) >= 4 or body.startswith("\t"):
            continue                                 # indented code: neither a fence nor a marker
        opener = _FENCE.match(body)
        if opener:
            char, size = opener.group(1)[0], len(opener.group(1))
            if fence is None:
                fence = (char, size)
            elif char == fence[0] and size >= fence[1] and not body[size:].strip():      # a closer holds only whitespace after the run
                fence = None
        elif fence is None:
            m = ESCALATE_FABLE.match(body)
            reason = "".join(c for c in m.group(1) if unicodedata.category(c) not in ("Cf", "Mn") and c not in _FILLERS).strip() if m else ""
            if len(reason) >= 8 and sum(c.isalnum() for c in reason) >= 3 and not reason.startswith("<"):
                return reason
    return ""


_FENCE = re.compile(r"(`{3,}|~{3,})")
# Hangul fillers (U+3164, U+115F, U+1160, U+FFA0): letters by category, blank on screen
_FILLERS = "".join(map(chr, (0x3164, 0x115F, 0x1160, 0xFFA0)))


def reason_length(prompt):
    """Characters of the written reason after the marker that marker() matched (never the text itself)."""
    text = str(prompt or "")
    if _fable_reason(text):
        return len(_fable_reason(text))
    m = _REASON.search(text)
    return len(m.group(1).strip()) if m else 0


def above_policy(subagent_type, level, given, escalate=False):
    """-> the role's ceiling when `given` is dearer than it, else None (a fork is never touched). A role the level gives no model
    ("none": work at critical, where the policy is to start no new agents) has no ceiling to lower to: a caller that
    chose to run one anyway is not pushed down to it. `escalate` lifts the ceiling one step."""
    if is_fork(subagent_type):
        return None
    allowed = ceiling_for(subagent_type, level, escalate)
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


def record_escalation(session_id, tool_use_id, given, kept):
    """R-915: every ESCALATE that lifted a ceiling is logged (session, tool_use, asked, model it ran on)."""
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    sl.append_text(os.path.join(sl.home_state(), "model-escalations.log"),
                   "%s %s %s %s\n" % (session_id or "-", tool_use_id or "-", given, kept))


def _runs_path():
    return os.path.join(sl.home_state(), "model-runs.log")


def _prompt_key(text):
    return hashlib.sha1(str(text or "").strip().encode("utf-8", "ignore")).hexdigest()[:16]


RUNS_MAX_BYTES = 1024 * 1024          # past this the log moves to model-runs.log.1 (one older file is kept)


READ_MAX_BYTES = 8 * 1024 * 1024      # most of a transcript or log this module reads
MODEL_MAX_CHARS = 64


class NotRegular(OSError):
    """The path is a pipe, a device or a socket: opening it for reading could wait forever."""


def _open_regular(path, flags, mode=0o600):
    """Open without blocking and require a regular file (fstat on the open descriptor, so nothing can swap it in between)."""
    fd = os.open(path, flags | os.O_NONBLOCK, mode)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise NotRegular("%s is not a regular file" % path)
    return fd


def _read_text(path):
    with os.fdopen(_open_regular(path, os.O_RDONLY), "rb") as f:
        return f.read(READ_MAX_BYTES).decode("utf-8", "ignore")


def _append_run(row):
    os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
    path = _runs_path()
    _rotate_if_big(path)
    fd = _open_regular(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _rotate_if_big(path):
    """Move a log over RUNS_MAX_BYTES to path.1. One writer rotates: it holds an exclusive lock on path.lock and looks at the size
    again under it, so a second writer never replaces the archive the first just made. A lock that cannot be taken, or any
    other failure, skips the rotation: the append still happens."""
    try:
        if os.path.getsize(path) <= RUNS_MAX_BYTES:
            return
        fd = _open_regular(path + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.path.getsize(path) > RUNS_MAX_BYTES:
                os.replace(path, path + ".1")
        finally:
            os.close(fd)
    except OSError:
        pass


def record_run(session_id, tool_use_id, subagent_type, level, requested, passed, prompt):
    """One line per Agent call the cap handles: what was asked, what went through, the marker and the length of its reason.
    The prompt itself is never written, only a short key so the served model can be matched to the call later."""
    _append_run({"session": session_id or "-", "tool_use_id": tool_use_id or "-", "role": role_for(subagent_type), "level": level,
                 "requested": requested, "passed": passed, "marker": marker(prompt) or "none",
                 "reason_len": reason_length(prompt), "key": _prompt_key(prompt)})


def _run_rows():
    try:
        return [r for r in (_loads(l) for l in _read_text(_runs_path()).splitlines()) if r]
    except OSError:
        return []


def _loads(line):
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def record_served(session_id, agent_id, transcript):
    """SubagentStop: the models the agent's own transcript says answered (assistant message.model), matched to its run line by
    the prompt key. No readable transcript or no model in it is `unknown` with the reason; mismatch=1 when a served model is
    not the requested family."""
    import retromine
    models, key, why = [], None, "no-transcript"
    try:
        text = _read_text(transcript) if isinstance(transcript, str) else ""
    except NotRegular:
        text, why = "", "not-regular-file"
    except OSError:
        text = ""
    for rec in (r for r in (_loads(l) for l in text.splitlines()) if r):
        if key is None and rec.get("type") == "user":
            key = _prompt_key(retromine._text_of(rec))
        msg = rec.get("message") if rec.get("type") == "assistant" else None
        if isinstance(msg, dict) and msg.get("model"):
            models.append(str(msg["model"])[:MODEL_MAX_CHARS])
    rows = _run_rows()
    done = {r.get("tool_use_id") for r in rows if r.get("served")}
    run = next((r for r in reversed(rows) if not r.get("served") and r.get("session") == (session_id or "-")
                and r.get("key") == key and r.get("tool_use_id") not in done), None) if key else None
    if not models:
        served, reason = "unknown", ("no-assistant-model" if key else why)
    else:
        served, reason = ",".join(sorted(set(models))), ""
    want, passed = (tier(run["requested"]), tier(run["passed"])) if run else (None, None)
    mismatch = int(want is not None and any(tier(m) != want for m in models))
    ignored = int(passed is not None and any(tier(m) != passed for m in models))      # served is not the family the cap passed
    _append_run({"session": session_id or "-", "agent_id": agent_id or "-", "tool_use_id": run["tool_use_id"] if run else "-",
                 "served": served, "mismatch": mismatch, "cap_ignored": ignored, "reason": reason})


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


def _policy_text_in(level, lang):
    """R-1160: the `budgit budget` text from the catalog (the same words as policy_text, in the user's language)."""
    import i18n
    p = POLICY[level]
    m = p["models"]
    main = i18n.t("r1160.budget_main", lang, brain=p["brain"], note=i18n.t("r1160.budget_note", lang))
    models = i18n.t("r1160.budget_models", lang, main=main, j=m["judgement"], w=m["work"], m=m["mechanical"],
                    roles=i18n.t("r1160.budget_roles", lang))
    policy = i18n.t("r1160.budget_policy", lang, level=level, subagents=p["subagents"], calls=p["agent_calls"],
                    research=i18n.t("r1160.research." + level, lang), defer=i18n.t("r1160.defer." + level, lang),
                    other=i18n.t("r1160.other." + level, lang), pct=round(p["checkpoint_at"] * 100))
    return models + "\n" + policy + "\n" + i18n.t("r1160.budget_invariants", lang)


def policy_text(level, lang=None):
    if lang == "es":
        return _policy_text_in(level, lang)
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
# R-1309: lag between sessions is a few points (45, 34, 38). A same-window drop of RESET_DROP points or more is taken from any source, so a
# correct lower reading always replaces a wrong higher one (only a typed figure is held against a higher non-typed one). After such a drop,
# for STALE_AFTER, a same-window reading at or above (pre-drop figure - RESET_DROP) is a session still on the old figure and is ignored.
def _num(v):
    """A finite int or float (never a bool), as a float; anything else None. The file may have been edited by hand."""
    try:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
    except (OverflowError, ValueError):      # a huge integer cannot become a float
        return None


RESET_DROP = 20.0
MAX_AHEAD = {"seven_day": WEEK + 6 * 3600, "five_hour": 6 * 3600}   # no window ends further ahead than its length (+ slack)


def _plausible(name, value, now):
    used, resets = value
    if resets is None:                      # R-522: the host may send a window with no reset time; the figure still counts
        return 0.0 <= used <= 100.0
    return 0.0 <= used <= 100.0 and math.isfinite(resets) and 0 < resets <= now + MAX_AHEAD[name]


_INVISIBLE = re.compile("[" + "".join(chr(c) for c in (*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x206A), 0xFEFF, 0x061C)) + "]+")      # zero-width and bidi controls: they can reorder or hide a name
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def _clean_name(text, limit=40):
    """A source name for one line of the line text: no control characters, no bidi or zero-width characters, at most `limit` long."""
    return _CONTROL.sub(" ", _INVISIBLE.sub("", str(text))).strip()[:limit]


AUTH_HOLD_MAX = 6 * 3600      # R-1309 follow-up: a typed reading holds off a higher non-typed figure for this long at most
RELEASE_OLD = "the typed reading is older than 6 h"
RELEASE_AGREE = "two sources agree on a higher figure"
RELEASE_NOTE = 3600           # how long the line keeps saying why a held figure was taken
HOST_SRC = "host limits"      # the source name of the host's own feed; its newest figure is kept as a reference for the other sources
# R-1309 rounds 4 to 6: inside one window usage only rises; a rise within (step + rate per hour since the typed reading) points of the typed figure
# is growth, not a stale writer. The numbers are about three times the real growth of each window (the measured weekly figure grew about 1.5
# points an hour; a session window can run from 0 to 100 in its 5 h), never one number for both: window -> (RISE_STEP points, RISE_RATE points per hour).
RISE_BOUND = {"seven_day": (15.0, 5.0), "five_hour": (30.0, 30.0)}
RISE = "rise"                 # returned in place of True/text when a plausible rise was taken past the typed protection


def _merge_window(old, new, authoritative, now, seen_at, last=None, pending=None, src=None, host=None, window="seven_day"):
    """R-212 #4: sessions share one snapshot file and lag each other (weekly 45, 34, 45, 38 within five minutes), so a
    small drop never lowers what is stored for the same window while the stored value is fresh. A later window replaces
    it, an earlier one is ignored. What the user types (`budgit budget record`) is authoritative and wins in its window.
    R-1309 case 2: a figure with no reset time is judged like any other, in the stored reset time's window. The app showed
    17 % while a stale writer kept sending 62 and 83, so:
      - a drop of RESET_DROP points or more from a source that is not authoritative is taken (the higher figure was wrong, or
        a reset happened) and marked, so the stale writer coming back within STALE_AFTER is ignored and flagged;
      - a figure higher than the last authoritative one, same window, from a source that is not authoritative is held as stale
        until a newer authoritative reading or a new reset time (`last` carries auth_used and auth_resets).
    -> (value to store, True when the reading was taken: only a taken reading keeps the stored value fresh,
        True when a higher figure was held: the line then says the reading may be stale; a RELEASE_* text instead when a
        higher figure held after a typed reading was taken: the line then says why). `pending` is the held mark of the window
        and `src` the name of the source of this reading. R-1309 round 4: usage only rises inside a window, so a figure at or above
        the kept one and at most RISE_STEP points above it (or within a point of `host`, the newest figure the host itself sent,
        for a source other than the host) is taken at once and returns RISE; only a bigger jump meets the hold above."""
    last = last if isinstance(last, dict) else {}
    live_old = isinstance(old, dict) and old.get("used") is not None and (old.get("resets_at") or 0) > now
    if new.get("resets_at") is None:       # R-522: no reset time: the stored window's end is kept while that window is live
        keep = old.get("resets_at") if live_old else None
        if keep is None or authoritative:
            return {"used": new["used"], "resets_at": keep}, True, False
        new = {"used": new["used"], "resets_at": keep}
    if authoritative or not isinstance(old, dict) or old.get("used") is None or old.get("resets_at") is None:
        return new, True, False
    if old["resets_at"] <= now - WINDOW_JITTER:      # the stored window is over
        return new, True, False
    if new["resets_at"] > old["resets_at"] + WINDOW_JITTER:      # a new reset time: a new window
        return new, True, False
    stale = now - (seen_at or 0) > STALE_AFTER
    if new["resets_at"] < old["resets_at"] - WINDOW_JITTER:
        return (new, True, False) if stale else (old, False, False)
    auth_used, auth_resets = _num(last.get("auth_used")), _num(last.get("auth_resets"))
    if auth_used is not None and auth_resets is not None and abs(old["resets_at"] - auth_resets) <= WINDOW_JITTER \
            and new["used"] > auth_used:
        if new["used"] < old["used"]:      # R-1309 round 7: a lower figure in the same window never replaces the kept one and is never a reset
            return old, False, False       # (only a new reset time is a reset; a correct figure at the kept level is taken afterwards)
        # R-1309: higher than what the user typed, same window: a stale writer, held. The hold ends AUTH_HOLD_MAX after the typed
        # reading (a typed reading with no stored time counts as old), or when a second, different source also reports a figure
        # above the kept one within STALE_AFTER of the held one. The release is returned as the reason text.
        host_used, host_at, host_resets = (_num(host.get("used")), _num(host.get("at")), _num(host.get("resets_at"))) \
            if isinstance(host, dict) else (None, None, None)
        near_host = src != HOST_SRC and host_used is not None and host_at is not None and host_resets is not None \
            and 0 <= now - host_at <= STALE_AFTER and abs(new["used"] - host_used) <= 1.0 \
            and abs(host_resets - old["resets_at"]) <= WINDOW_JITTER      # the host sent this figure itself, just now, for this window
        auth_at = _num(last.get("auth_at"))      # `record` stamps a row from an older build once, so None is a hand-edited row
        age = None if auth_at is None else now - auth_at
        step, rate = RISE_BOUND.get(window, RISE_BOUND["seven_day"])
        bound = auth_used + step + rate * max(0.0, age or 0.0) / 3600.0      # what real use could have grown to since the typing
        if new["used"] <= max(bound, old["used"]) or near_host:
            # R-1309 rounds 4 and 5: usage only rises inside a window; a figure real use can reach is growth, not a stale writer. The
            # stored reset time stays, so several rises cannot walk the window away from the typed one.
            return dict(new, resets_at=old["resets_at"]), True, RISE
        if age is None or age < 0:      # an unknown or future time holds like a fresh typed reading (`record` restamps it to now)
            return old, False, True
        if age > AUTH_HOLD_MAX:
            return new, True, RELEASE_OLD
        held_used, held_at = (_num(pending.get("used")), _num(pending.get("at"))) if isinstance(pending, dict) else (None, None)
        held_src = pending.get("src") if isinstance(pending, dict) else None
        if held_used is not None and held_at is not None and held_used > old["used"] and 0 <= now - held_at <= STALE_AFTER \
                and src and held_src and src != held_src:
            return new, True, RELEASE_AGREE
        return old, False, True
    if stale:
        return new, True, False
    if old["used"] - new["used"] >= RESET_DROP:      # R-1309: far below the stored figure: the stored one was wrong, or a reset
        return dict(new, reset_from=old["used"], reset_at=now), True, False
    marker = {k: _num(old.get(k)) for k in ("reset_from", "reset_at")}
    marker = marker if None not in marker.values() else {}      # a half or hand-edited marker counts as absent
    if marker and now - marker["reset_at"] <= STALE_AFTER \
            and new["used"] >= marker.get("reset_from") - RESET_DROP and new["used"] > old["used"]:
        return old, False, True          # R-1309: a session still on the pre-drop figure, not growth: the drop stays, the line says why
    merged = {"used": max(old["used"], new["used"]), "resets_at": max(old["resets_at"], new["resets_at"])}
    merged.update(marker)
    return merged, new["used"] >= old["used"], False


def record(seven_day=None, five_hour=None, extra=None, now=None, authoritative=False, extra_enabled=None, source=None, mine=False):
    """seven_day / five_hour: (used_percent, resets_at_epoch); extra: (spent_usd, monthly_limit_usd); extra_enabled: True or
    False when the account says whether extra usage is switched on, None when it does not say. A reading outside
    what a window can be (used not in 0..100, an end further away than the window is long) is dropped."""
    caller_now = now
    with _locked():
        now = caller_now or time.time()      # R-1309: the clock is read under the lock, so a writer that waited is not dated before the one it waited for
        data = load()
        limits = data.setdefault("limits", {})
        seen = data.get("seen") if isinstance(data.get("seen"), dict) else {}
        readings = data.get("readings") if isinstance(data.get("readings"), dict) else {}      # R-1309: who sent each stored figure, and when
        held_marks = data.get("held") if isinstance(data.get("held"), dict) else {}
        host_marks = data.get("host") if isinstance(data.get("host"), dict) else {}      # the newest figure the host feed itself sent, per window
        src = _clean_name(source) or None if source else None
        for name, value in (("seven_day", seven_day), ("five_hour", five_hour)):
            if value is None:
                continue
            kept = limits.get(name)
            if isinstance(kept, dict) and (_num(kept.get("used")) is None
                                           or (kept.get("resets_at") is not None and _num(kept.get("resets_at")) is None)):
                limits.pop(name)      # a hand-edited figure that is not a number (a huge integer too) is no figure
            stored_row = readings.get(name)
            if isinstance(stored_row, dict) and _num(stored_row.get("auth_used")) is not None:
                typed_at = _num(stored_row.get("auth_at"))
                if typed_at is None or typed_at > now:      # an older build's row, or a time ahead of this clock: the 6 h start here
                    stored_row["auth_at"] = now
            try:
                pair = (float(value[0]), None if value[1] is None else float(value[1]))
            except (OverflowError, ValueError, TypeError):      # a reading that is not a number is no reading
                continue
            if _plausible(name, pair, now):
                new = {"used": pair[0], "resets_at": pair[1]}
                seen_before = _num(seen.get(name))      # a hand-edited time that is not a number is no time
                floor = _floor(data, now) if name == "seven_day" else None      # R-1378: the app's account figure referees the weekly
                lowers = bool(authoritative and mine)      # only the user's `--mine` may go below the app's figure
                if floor is not None and not lowers and pair[1] is not None and pair[1] > now \
                        and abs(pair[1] - floor[1]) > WINDOW_JITTER:
                    data["account"]["seven_day"]["off_at"] = now      # the reset time moved, later or earlier: a new window, the floor is released
                    limits[name], taken, held = new, True, False
                elif floor is not None and not lowers:
                    limits[name], taken, held = _floor_merge(limits.get(name), new, authoritative, floor)
                else:
                    if floor is not None:
                        data["account"]["seven_day"]["off_at"] = now
                    limits[name], taken, held = _merge_window(limits.get(name), new, authoritative, now, seen_before,
                                                              last=readings.get(name), pending=held_marks.get(name), src=src,
                                                              host=host_marks.get(name), window=name)
                if src == HOST_SRC:      # the host's own newest figure, taken or held: the other sources are compared with it
                    host_marks[name] = {"used": pair[0], "at": now,
                                        "resets_at": pair[1] if pair[1] is not None else (limits.get(name) or {}).get("resets_at")}
                if taken:
                    seen[name] = max(now, seen_before or 0)
                    row = {"src": src, "at": now, "auth": bool(authoritative)}
                    before = readings.get(name) if isinstance(readings.get(name), dict) else {}
                    released = isinstance(held, str) and held != RISE       # a release ends the typed protection of the window
                    if authoritative:                      # what the user typed, for the window it names
                        row.update(auth_used=pair[0], auth_resets=limits[name]["resets_at"], auth_at=now)
                    elif not released and _num(before.get("auth_resets")) is not None and limits[name].get("resets_at") is not None \
                            and abs(limits[name]["resets_at"] - before["auth_resets"]) <= WINDOW_JITTER:
                        row.update(auth_used=before.get("auth_used"), auth_resets=before["auth_resets"], auth_at=before.get("auth_at"))
                        if held == RISE:      # the typed figure stays the baseline; the last taken figure is kept apart
                            row["auth_last"] = limits[name]["used"]
                    readings[name] = row
                    held_marks.pop(name, None)
                    why = data.get("released") if isinstance(data.get("released"), dict) else {}
                    if released:
                        why[name] = {"why": held, "at": now, "src": src}
                    elif authoritative or held == RISE:      # a typed reading or a plain rise ends an old release note
                        why.pop(name, None)
                    data["released"] = why
                    if src:
                        data["source"] = src      # what `current` quotes when the figures are old
                elif held:
                    held_marks[name] = {"used": pair[0], "at": now, "src": src}
        if not limits:
            data.pop("limits")
        data["seen"] = seen
        data["readings"] = readings
        data["held"] = held_marks
        data["host"] = host_marks
        if extra is None and extra_enabled is False:      # R-862: "disabled" with no figures still replaces an older enabled reading
            prior = data.get("extra") if isinstance(data.get("extra"), dict) else {}
            extra = (0.0, _num(prior.get("limit")) or 0.0)
        try:      # a figure that is not a number (a string, a huge integer, None) makes the whole extra reading no reading
            extra = None if extra is None else tuple(_num(x) for x in extra)
        except TypeError:
            extra = None
        if extra is not None and None not in extra and all(x >= 0 for x in extra):
            was = data.get("extra") if isinstance(data.get("extra"), dict) else {}
            flag = extra_enabled if isinstance(extra_enabled, bool) else was.get("enabled")      # a reading that does not say keeps what we knew
            data["extra"] = {"spent": float(extra[0]), "limit": float(extra[1])}
            if isinstance(flag, bool):
                data["extra"]["enabled"] = flag
        data["seen_at"] = max(now, _num(data.get("seen_at")) or 0)
        save(data)


# --- R-1378: the app's account figure referees the weekly --------------------------------------------------------------------
# `budget account` takes only the raw get_usage payload (never a typed number) and keeps its all-models weekly as a floor: while that
# window is live the level is never below it, a same-window drop under it is refused, and a non-typed rise bigger than RISE_STEP is held
# (no time-grown ceiling, so a stale 83 never throttles however sparse the sweeps are). A typed figure is stored as the user's own but cannot
# hide the app's figure; only `record --mine` goes below it, and only until the next account read.
ACCOUNT_SRC = "account"
ACCOUNT_LABELS = (("weekly", "all models"),)      # (weekly marker, all-models marker) per locale; add a locale when a real payload shows one
ACCOUNT_MAX_AGE = 2 * 3600      # a payload whose derived read time (resetsAt - resetsIn) is older than this is refused (resetsIn has hour steps)
ACCOUNT_OLD = 12 * 3600         # an account read older than this is named on line 1; the floor still holds (usage only rises inside a window)
RISE_STEP = RISE_BOUND["seven_day"][0]
_NOT_WEEKLY = re.compile(r"5[- ]?h|five|session", re.I)


def _span_seconds(text):
    m = re.fullmatch(r"\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*", str(text or ""))
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60)


def _iso_epoch(text):
    import datetime
    try:
        stamp = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.timestamp() if stamp.tzinfo else None


def parse_account(payload, now):
    """The one all-models weekly row of a raw get_usage payload -> (used, resets_at epoch). Refuses (StudioError) on a plan that is not
    ok, zero or several matching rows (the message lists the labels it saw), a missing figure, resetsAt or resetsIn, a payload whose
    derived read time is more than ACCOUNT_MAX_AGE old, and a window that is not live."""
    plan = payload.get("plan") if isinstance(payload, dict) else None
    if not isinstance(plan, dict) or plan.get("status") != "ok":
        raise sl.StudioError("account: refused, plan.status is not ok")
    rows = [w for w in plan.get("windows") if isinstance(w, dict)] if isinstance(plan.get("windows"), list) else []
    seen = ", ".join(_clean_name(w.get("label"), 40) or "?" for w in rows) or "none"
    hits = [w for w in rows if not _NOT_WEEKLY.search(str(w.get("label") or ""))
            and any(a in str(w.get("label") or "").lower() and b in str(w.get("label") or "").lower() for a, b in ACCOUNT_LABELS)]
    if len(hits) != 1:
        raise sl.StudioError("account: refused, %d rows are the weekly all-models row (labels seen: %s)" % (len(hits), seen))
    used, resets, span = _num(hits[0].get("percentUsed")), _iso_epoch(hits[0].get("resetsAt")), _span_seconds(hits[0].get("resetsIn"))
    if used is None or not 0.0 <= used <= 100.0:
        raise sl.StudioError("account: refused, the row has no usable percentUsed")
    if resets is None:
        raise sl.StudioError("account: refused, the row has no resetsAt")
    if span is None:
        raise sl.StudioError("account: refused, the row has no resetsIn, so its age is unknown")
    if now - (resets - span) > ACCOUNT_MAX_AGE:
        raise sl.StudioError("account: refused, the payload is about %d h old" % ((now - (resets - span)) // 3600))
    if not now < resets <= now + MAX_AHEAD["seven_day"]:
        raise sl.StudioError("account: refused, resetsAt is not a live weekly window")
    return used, resets


def account(payload, now=None):
    """Store the account figure of a get_usage payload and lift the kept weekly to it. -> the stored {used, resets_at, at}."""
    caller_now = now
    with _locked():
        now = caller_now or time.time()
        used, resets = parse_account(payload, now)
        data = load()
        acct = data.get("account") if isinstance(data.get("account"), dict) else {}
        old = acct.get("seven_day") if isinstance(acct.get("seven_day"), dict) else {}
        o_used, o_resets = _num(old.get("used")), _num(old.get("resets_at"))
        new = {"used": used, "resets_at": resets, "at": now}
        if o_used is not None and o_resets is not None and o_resets > now and abs(o_resets - resets) <= WINDOW_JITTER and used < o_used:
            new["used"] = o_used      # a newer, lower read in the same week (two logins?): the higher one is kept, and line 1 says so
            new["mismatch"] = {"lower": used, "at": now}
        acct["seven_day"] = new
        data["account"] = acct
        limits = data.setdefault("limits", {})
        kept = limits.get("seven_day")
        k_used, k_resets = (_num(kept.get("used")), _num(kept.get("resets_at"))) if isinstance(kept, dict) else (None, None)
        if not (k_used is not None and k_resets is not None and k_resets > now and abs(k_resets - resets) <= WINDOW_JITTER
                and k_used >= new["used"]):
            limits["seven_day"] = {"used": new["used"], "resets_at": resets}
            readings = data.get("readings") if isinstance(data.get("readings"), dict) else {}
            readings["seven_day"] = {"src": ACCOUNT_SRC, "at": now, "auth": False}
            data["readings"] = readings
        row = (data.get("readings") or {}).get("seven_day") if isinstance(data.get("readings"), dict) else None
        if isinstance(row, dict) and _num(row.get("auth_used")) is not None and _num(row.get("auth_used")) < new["used"]:
            for key in ("auth_used", "auth_resets", "auth_at"):      # a typed figure under a newer app read is superseded by it
                row.pop(key, None)
        held = data.get("held") if isinstance(data.get("held"), dict) else {}
        mark = held.get("seven_day")
        if isinstance(mark, dict) and (_num(mark.get("used")) or 0.0) <= new["used"]:
            held.pop("seven_day")      # the app's figure reached the held one
        data["held"] = held
        seen = data.get("seen") if isinstance(data.get("seen"), dict) else {}
        seen["seven_day"] = max(now, _num(seen.get("seven_day")) or 0)
        data["seen"] = seen
        data["seen_at"] = max(now, _num(data.get("seen_at")) or 0)
        data["source"] = ACCOUNT_SRC
        save(data)
    return new


def _floor(data, now):
    """(used, resets_at) of the account figure while its window is live and neither `--mine` nor a moved reset time switched it off
    after it was read; else None."""
    acct = data.get("account")
    sd = acct.get("seven_day") if isinstance(acct, dict) else None
    if not isinstance(sd, dict):
        return None
    used, resets, at = _num(sd.get("used")), _num(sd.get("resets_at")), _num(sd.get("at"))
    if used is None or resets is None or at is None or resets <= now:
        return None
    off = _num(sd.get("off_at"))
    return None if off is not None and off >= at else (used, resets)


def _floor_merge(old, new, authoritative, floor):
    """A weekly reading while the account floor is live -> (value, taken, held), the triple _merge_window returns. A typed figure at or
    above the floor rules, below it is stored as the user's own and the level stays at the floor; a non-typed one below the kept level is refused
    (never a reset), one at most RISE_STEP above it is taken, a bigger one is held."""
    f_used, f_resets = floor
    k_used = f_used
    if isinstance(old, dict) and _num(old.get("used")) is not None and _num(old.get("resets_at")) is not None \
            and abs(_num(old["resets_at"]) - f_resets) <= WINDOW_JITTER:
        k_used = max(f_used, _num(old["used"]))
    kept = {"used": k_used, "resets_at": f_resets}
    u = new["used"]
    if authoritative:
        return ({"used": u, "resets_at": f_resets} if u >= f_used else kept), True, False
    if u < k_used:
        return kept, False, False
    if u - k_used <= RISE_STEP:
        return {"used": u, "resets_at": f_resets}, True, False
    return kept, False, True


def floored(limits, data, now):
    """The limits with the weekly raised to the live account floor (a missing or lower weekly is replaced)."""
    floor = _floor(data, now)
    if floor is None:
        return limits
    out = dict(limits) if isinstance(limits, dict) else {}
    week = out.get("seven_day")
    used, resets = (_num(week.get("used")), _num(week.get("resets_at"))) if isinstance(week, dict) else (None, None)
    same = used is not None and resets is not None and abs(resets - floor[1]) <= WINDOW_JITTER
    if not (same and used >= floor[0]):
        out["seven_day"] = {"used": max(floor[0], used if same else 0.0), "resets_at": floor[1]}
    return out


def account_notes(lang=None, now=None):
    """The lines that come before the budget line (R-1378): «el sistema marca N %, sin confirmar» for a higher figure held and not yet
    reached by the app, «escrito 19 · app 31 (HH:MM)» when the typed figure sits under the app's, «app leída hace N h» for an old
    account read, and the mismatch note. Display only: the level never reads them."""
    import i18n
    now = now or time.time()
    data = load()
    lines = []
    week = (data.get("limits") or {}).get("seven_day") if isinstance(data.get("limits"), dict) else None
    kept = _num(week.get("used")) if isinstance(week, dict) else None
    floor = _floor(data, now)
    sd = (data.get("account") or {}).get("seven_day") if isinstance(data.get("account"), dict) else None
    held = (data.get("held") or {}).get("seven_day") if isinstance(data.get("held"), dict) else None
    h_used, h_at = (_num(held.get("used")), _num(held.get("at"))) if isinstance(held, dict) else (None, None)
    if h_used is not None and h_at is not None and kept is not None and h_used > kept \
            and (floor is not None or 0 <= now - h_at <= STALE_AFTER):
        lines.append(i18n.t("r1378.unconfirmed", lang, host=int(h_used)))
    if floor is not None and isinstance(sd, dict):
        row = (data.get("readings") or {}).get("seven_day") if isinstance(data.get("readings"), dict) else None
        typed = _num(row.get("auth_used")) if isinstance(row, dict) else None
        if typed is not None and typed < floor[0]:
            lines.append(i18n.t("r1378.typed_below", lang, typed=int(typed), app=int(floor[0]),
                                at=time.strftime("%H:%M", time.localtime(_num(sd.get("at")) or now))))
        if now - (_num(sd.get("at")) or now) > ACCOUNT_OLD:
            lines.append(i18n.t("r1378.app_old", lang, hours=int((now - sd["at"]) // 3600)))
        mism = sd.get("mismatch")
        if isinstance(mism, dict) and _num(mism.get("lower")) is not None:
            lines.append(i18n.t("r1378.mismatch", lang, low=int(mism["lower"]), high=int(floor[0])))
    return lines


# H1 evidence (ends once it is answered): which kinds each session reports, one row when a session's (kind, percentUsed, resetsAt)
# changes. Exactly 7 keys, the session as a salted local hash, no project path, no prompt text; 0600; rotates at FEED_MAX.
FEED_LOG = "session-usage.jsonl"
FEED_MAX = 256 * 1024
FEED_SESSIONS = 200


def _feed_salt():
    path = os.path.join(sl.home_state(), "session-usage.salt")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text
    except OSError:
        pass
    text = os.urandom(16).hex()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def note_session_feed(session_id, model, rows, source, now=None):
    """rows: [(kind, percentUsed, resetsAt)] as one session sent them. Appends a row for each (kind, percentUsed, resetsAt) that differs
    from that session's last one. -> the number of rows written. No session id, no log."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    now = now or time.time()
    wrote = 0
    prim = lambda v: v if v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)) else _clean_name(v, 64)
    with _locked():
        os.makedirs(sl.home_state(), mode=0o700, exist_ok=True)
        digest = hashlib.sha256((_feed_salt() + sid).encode("utf-8")).hexdigest()[:16]
        last_path = os.path.join(sl.home_state(), "session-usage-last.json")
        try:
            with open(last_path, encoding="utf-8") as f:
                last = json.load(f)
        except (OSError, ValueError):
            last = {}
        last = last if isinstance(last, dict) else {}
        mine_ = last.pop(digest, None)
        mine_ = mine_ if isinstance(mine_, dict) else {}
        log = os.path.join(sl.home_state(), FEED_LOG)
        for kind, pct, resets in rows:
            kind = clean_kind(kind, 64)
            sig = [prim(pct), prim(resets)]
            if not kind or mine_.get(kind) == sig:
                continue
            mine_[kind] = sig
            line = json.dumps({"at": now, "source": _clean_name(source, 24) or "-", "session": digest, "model": _clean_name(model, 64) or "-",
                               "kind": kind, "percentUsed": sig[0], "resetsAt": sig[1]}, sort_keys=True) + "\n"
            try:
                if os.path.getsize(log) >= FEED_MAX:
                    os.replace(log, log + ".1")      # one old file is kept
            except OSError:
                pass
            fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            wrote += 1
        last[digest] = mine_
        while len(last) > FEED_SESSIONS:
            last.pop(next(iter(last)))
        sl.atomic_write(last_path, json.dumps(last))
    return wrote


# R-522: a weekly sub-limit the host may send under a kind we do not know. It is never the all-models weekly, never named after
# a model, and only ever read from the host's own data: with no host data there is nothing to show.
KNOWN_KINDS = ("five_hour", "seven_day", "spend_limit")


def _kinds_path():
    return os.path.join(sl.home_state(), "limit-kinds.json")


def clean_kind(name, limit=40):
    """A limit kind as the host named it, reduced to lower-case letters, digits and _ . - (no control, bidi or other characters),
    at most `limit` long. The limit-kinds file and the raw feed copy both use it."""
    return re.sub(r"[^a-z0-9_.-]", "", str(name).lower())[:limit]


def note_kinds(names):
    """Counts of the unknown limit kinds the host sent (names only: no figures, no text), in the machine state folder."""
    clean = [clean_kind(n) for n in names]
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
        used = _num(used)      # not a number (a string, a huge integer, NaN) is no sub-limit
        if used is None or not 0.0 <= used <= 100.0:
            if "subcap" not in data:
                return
            data.pop("subcap")
        else:
            clean = re.sub(r"[^A-Za-z0-9 ._-]", "", str(label or "")).strip()[:24] or None
            data["subcap"] = {"used": float(used), "resets_at": _num(resets), "label": clean, "at": now}      # a stamp that is not a number is dropped
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


def plan_limits(limits):
    """R-1104: the one place the 5-hour window is dropped for a plan that has none (config `five_hour_window` "off").
    Every view and gate reads limits through here, so the status line, Brote and the start gate cannot disagree."""
    if isinstance(limits, dict) and "five_hour" in limits and sl.five_hour_window() == "off":
        return {k: v for k, v in limits.items() if k != "five_hour"}
    return limits


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
    if isinstance(limits, dict) and (now or time.time()) - (_num(data.get("seen_at")) or 0) > STALE_AFTER:
        limits = {k: v for k, v in limits.items() if not (isinstance(v, dict) and v.get("resets_at") is None)}   # R-522: a figure with no reset time only counts while fresh
    limits = floored(limits, data, now or time.time())      # R-1378: never below the app's account figure while its window is live
    level, reason = classify(plan_limits(limits), prev=data.get("level"), now=now)
    if data.get("level") != level:
        with _locked():                       # re-read under the lock so a merge that just happened is not overwritten
            fresh = load()
            fresh["level"] = level
            save(fresh)
    seen_num = _num(data.get("seen_at"))
    age = None if seen_num is None else (now or time.time()) - seen_num
    if data.get("limits") and age is not None and age > STALE_AFTER:       # R-1309: say so when the figures are not live
        src = data.get("source")
        src = _clean_name(src) if isinstance(src, str) else ""
        reason += "; stale: last reading %d min old%s" % (age // 60, " (from %s)" % src if src else "")
    held = data.get("held") if isinstance(data.get("held"), dict) else {}
    mark = held.get("seven_day") if isinstance(held.get("seven_day"), dict) else {}
    seen_week = _num(mark.get("at")) if mark else None
    mark_used = _num(mark.get("used")) if mark else None
    week = limits.get("seven_day") if isinstance(limits, dict) else None
    if seen_week is not None and mark_used is not None and 0 <= (now or time.time()) - seen_week <= STALE_AFTER \
            and isinstance(week, dict) and week.get("used") is not None:
        who = _clean_name(mark.get("src") or "")      # R-1309 case 2
        reason += "; weekly reading may be stale: %s reported %d%% against the %d%% kept%s" % (
            "another source" if not who else who, mark_used, week["used"], "")
    rel = data.get("released") if isinstance(data.get("released"), dict) else {}
    rel = rel.get("seven_day") if isinstance(rel.get("seven_day"), dict) else {}
    rel_at = _num(rel.get("at"))
    if rel_at is not None and rel.get("why") in (RELEASE_OLD, RELEASE_AGREE) and 0 <= (now or time.time()) - rel_at <= RELEASE_NOTE \
            and isinstance(week, dict) and week.get("used") is not None:      # R-1309 follow-up: a held higher figure was taken, and why
        who = _clean_name(rel.get("src") or "")
        reason += "; weekly figure taken after a typed reading: %s%s" % (rel["why"], " (from %s)" % who if who else "")
    extra = data.get("extra")
    # R-862: the limit is a ceiling, never spend. Nothing is claimed for a switched-off account, and with the flag unknown only
    # an amount actually spent (> 0) against a real limit is worth a line.
    if extra and level in ("tight", "critical") and extra.get("enabled") is not False \
            and (extra.get("enabled") is True or (extra["spent"] > 0 and extra["limit"] > 0)):
        # real money only when the account is known to have extra usage on; without the flag we do not claim it
        tail = "real money once the plan limit is hit" if extra.get("enabled") is True \
            else "billed as real money only if extra usage is enabled on your account"
        reason += "; extra usage $%.2f of $%.0f this month (%s)" % (extra["spent"], extra["limit"], tail)
    return {"level": level, "reason": reason, "source": "limits" if data.get("limits") or _floor(data, now or time.time()) else "none"}


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
