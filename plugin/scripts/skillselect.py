"""The skill selector (D28, D35-D38): which installed skill does a job get, and why. Pure functions, zero model tokens.

`select(registry, job, traits, env)` reads only its arguments: the registry (skills-registry.json, loaded by skills.py),
the job ("design/critique"), the traits ("stakes=routine"), and the environment (what is installed and allowed here).
It never reads a file, a clock or the network, so every case is a table-driven test.

Procedure (the plan's 4.1 with the amendments of 15.4): hard filters, each loser listed with a reason code; the author
continues when the project names one; score = 3*fit + S*(2*check + receipt) - B*cost - risk; a tie band of one point
(continuity, then lower risk, then lower cost); a fallback chain that ends in project text; `gap` is a valid answer.
Reason codes are stable; skills.py turns them into words in the user's language.
"""
import re

LEVELS = ("ample", "normal", "tight", "critical")
SELECTABLE = ("installed", "active")
RISK_BASE = {"PASS": 0, "PASS-WITH-CONDITIONS": 1}
SURFACE_ALIASES = {
    "pixel-sprite": "pixel", "pixel-prop": "pixel", "pixel-icon": "pixel", "pixel-scene": "pixel", "sprite": "pixel",
    "terminal-text": "terminal", "status-line": "terminal", "statusline": "terminal",
    "macos-native": "native-macos", "native": "native-macos", "native-panel": "native-macos", "native-macos-panel": "native-macos",
    "app": "app-ui", "dashboard": "app-ui", "landing-page": "landing", "marketing": "landing",
    "readme": "docs", "docs-readme": "docs",
}
TRAIT_KEYS_NORMALISED = ("surface", "platform")
UNREGISTERED_LISTED = 10


def normalise(traits):
    out = {}
    for key, value in (traits or {}).items():
        key, value = str(key).strip().lower(), str(value).strip().lower().replace(" ", "-")
        if key in TRAIT_KEYS_NORMALISED:
            value = SURFACE_ALIASES.get(value, value)
        out[key] = value
    return out


def parse_traits(text):
    """'stakes=routine,budget=tight' -> {'stakes': 'routine', 'budget': 'tight'}."""
    out = {}
    for part in re.split(r"[,\s]+", text or ""):
        if "=" in part:
            key, _, value = part.partition("=")
            if key.strip():
                out[key.strip()] = value.strip()
    return out


def level_of(traits, env):
    for candidate in ((traits or {}).get("budget"), (env or {}).get("budget")):
        if candidate in LEVELS:
            return candidate
    return "normal"


# --- reading the registry ------------------------------------------------------------------------------
def entries_by_id(registry):
    return {e["id"]: e for e in registry.get("entries", [])}


def area_of(job):
    return job.split("/", 1)[0]


def fit_candidates(job, traits):
    """[(registry key, surface_specific)] a job can be filed under, most specific first: area/surface/rest, job-language, job."""
    area, _, rest = job.partition("/")
    keys = []
    surface = traits.get("surface")
    if surface and rest:
        keys.append(("%s/%s/%s" % (area, surface, rest), True))
    if traits.get("language"):
        keys.append(("%s-%s" % (job, traits["language"]), False))
    keys.append((job, False))
    return keys


def fit_row(registry, entry, job, traits):
    """-> (key, row) of the entry's evidence for this job, or (None, None). Generic rows apply to web or unspecified
    surfaces only: a pixel sprite never inherits what a skill knows about web pages."""
    fits = entry.get("fits") or {}
    surface = traits.get("surface")
    generic_ok = (not surface) or (registry.get("surfaces", {}).get(surface, {}).get("generic_ok", False))
    for key, specific in fit_candidates(job, traits):
        if key in fits and (specific or generic_ok):
            return key, fits[key]
    return None, None


def risk_of(entry):
    sec = entry.get("security") or {}
    if "risk" in sec:
        return int(sec["risk"])
    verdict = str(sec.get("verdict") or "")
    risk = 1
    for word, base in RISK_BASE.items():
        if verdict == word or verdict.startswith(word + " "):
            risk = base
    flags = entry.get("flags") or {}
    if flags.get("binary_unaudited") or flags.get("data_leaves_machine"):
        risk = 2
    return risk


def agents_of(entry, row):
    if row and "agents" in row:
        return int(row["agents"])
    return int((entry.get("cost") or {}).get("agents", 0))


def cost_of(entry, row):
    if row and "cost" in row:
        return int(row["cost"])
    return int((entry.get("cost") or {}).get("class", 0))


def match_tokens(traits):
    tokens = set()
    for key, value in traits.items():
        tokens.add(value)
        tokens.add("%s:%s" % (key, value))
    return tokens


# --- hard filters --------------------------------------------------------------------------------------
def hard_filter(entry, traits, env, cap, exclude, agents):
    """-> reason code of the first filter the entry fails, else None."""
    if entry.get("status") not in SELECTABLE:
        return "status:%s" % entry.get("status")
    if entry.get("enabled") is not True:
        return "disabled"
    if str((entry.get("security") or {}).get("verdict") or "").upper().startswith("HOLD"):
        return "hold"
    if entry["id"] in exclude:
        return "same_skill"
    state = (env.get("state") or {}).get(entry["id"])
    if state in ("drifted", "missing"):
        return state
    upstream = entry.get("upstream") or {}
    if upstream.get("state") == "update-pending" and upstream.get("open_condition"):
        return "update_pending"
    need = entry.get("requires") or {}
    if need.get("node") and not env.get("node"):
        return "requires:node"
    if need.get("chrome") and not env.get("chrome"):
        return "requires:chrome"
    if need.get("binary") and env.get("binary_allowed") is False:
        return "requires:binary"
    if need.get("network") and env.get("network") is False:
        return "requires:network"
    if need.get("browser_tool") and not env.get("browser_tool"):
        return "requires:browser_tool"
    if need.get("codex") and not env.get("codex"):
        return "requires:codex"
    if need.get("pr") and traits.get("where") != "pr":
        return "requires:pr"
    for key in need.get("keys") or []:
        if key not in (env.get("keys") or []):
            return "requires:key"
    tokens = match_tokens(traits)
    for token in entry.get("not_for") or []:
        if str(token).lower() in tokens:
            return "not_for:%s" % token
    for rule in entry.get("conflicts_rules") or []:
        if rule in (env.get("project_rules") or []):
            return "project_rule:%s" % rule
    if agents > cap:
        return "agent_cap"
    return None


def _order(c):
    """Sort key: continuity hand, then lower risk, then lower cost, then the higher score, then the name."""
    return (0 if c["continuity"] else 1, c["risk"], c["cost"], -c["score"], c["id"])


def _why(code, c, top=None):
    return {"continuity": "the project's author skill (%s) continues" % c["id"],
            "default": "provisional default for a tie on evidence (%s)" % c["id"],
            "score": "highest score (%d)" % c["score"],
            "band": "within one point of %s: lower risk or cost wins" % top}[code]


# --- the selector --------------------------------------------------------------------------------------
def select(registry, job, traits=None, env=None, exclude=(), want_roles=None):
    """-> dict: primary (id or None), role, scores, rejected [{skill, reason}], beat [{skill, reason}], fallback, gap,
    provisional, bakeoff, load, agents, why. `exclude` skills are refused as `same_skill` (a checker is never its author)."""
    traits = normalise(traits)
    env = env or {}
    level = level_of(traits, env)
    policy = (registry.get("budget_policy") or {}).get("levels", {}).get(level, {})
    B, cap = int(policy.get("B", 1)), int(policy.get("agent_cap", 3))
    S = 2 if traits.get("stakes") == "pillar" else 1
    job_info = (registry.get("jobs") or {}).get(job, {})
    role = want_roles or job_info.get("role")
    area = area_of(job)
    entries = registry.get("entries", [])
    rejected, survivors = [], []
    for e in entries:
        key, row = fit_row(registry, e, job, traits)
        relevant = any(k.startswith(area + "/") for k in (e.get("fits") or {}))
        fit = int(row.get("fit", 0)) if row else 0
        agents = agents_of(e, row)
        reason = hard_filter(e, traits, env, cap, set(exclude), agents) if (relevant or fit) else None
        if reason is not None:
            token_hit = reason.startswith("not_for:")
            if fit >= 2 or (token_hit and relevant):
                rejected.append({"skill": e["id"], "reason": reason})
            continue
        if fit < 2 or not row:
            continue
        if role and role not in _roles(e):
            continue
        if e.get("read") in ("description", False):
            rejected.append({"skill": e["id"], "reason": "read_description"})
            continue
        check, receipt, cost, risk = int(row.get("check", 0)), int(row.get("receipt", 0)), cost_of(e, row), risk_of(e)
        score = 3 * fit + S * (2 * check + receipt) - B * cost - risk
        survivors.append({"id": e["id"], "entry": e, "fit": fit, "check": check, "receipt": receipt, "cost": cost, "risk": risk,
                          "score": score, "agents": agents, "key": key,
                          "continuity": traits.get("author") == e["id"] and bool(job_info.get("continuity"))})
    unregistered = sorted(env.get("unregistered") or [])
    rejected += [{"skill": sid, "reason": "unregistered"} for sid in unregistered[:UNREGISTERED_LISTED]]
    result = {"job": job, "traits": traits, "level": level, "S": S, "B": B, "agent_cap": cap, "role": role,
              "primary": None, "scores": {c["id"]: c["score"] for c in survivors}, "companions": [], "rejected": rejected,
              "beat": [], "fallback": [], "fallback_used": None, "gap": None, "provisional": False, "bakeoff": None,
              "load": None, "agents": 0, "via": None, "why": "", "registry": registry.get("version"),
              "unregistered_total": len(unregistered), "why_code": None, "why_score": None, "why_top": None}
    if not survivors:
        result["gap"] = gap_for(registry, job, traits, level)
        result["fallback"] = list(result["gap"]["fallback"])
        result["fallback_used"] = result["fallback"][-1] if result["fallback"] else None
        result["why"] = "no skill has fit 2 or more for this job: gap, project rules apply"
        result["why_code"] = "gap"
        return result
    survivors.sort(key=lambda c: (-c["score"], c["id"]))
    best = survivors[0]
    band = sorted((c for c in survivors if best["score"] - c["score"] <= 1), key=_order)
    pick = next((c for c in survivors if c["continuity"]), None)      # continuity binds the author role only
    code = "continuity" if pick else "score"
    if pick is None:
        pick = band[0]
        if pick["id"] != best["id"]:
            code = "band"
        tied = _tied_on_evidence(survivors) if job_info.get("bakeoff") else []
        if tied:
            result["bakeoff"] = "needed"
            default = _default_for(registry, job, traits, level)
            chosen = next((c for c in tied if c["id"] == default), None)
            if chosen:
                pick, code = chosen, "default"
                result["provisional"] = True
    entry = pick["entry"]
    result.update(primary=pick["id"], agents=pick["agents"], load=entry.get("load", "full"),
                  fallback=list(entry.get("fallback") or []), why=_why(code, pick, best["id"]),
                  why_code=code, why_score=pick["score"], why_top=best["id"])
    for c in survivors:
        if c["id"] != pick["id"]:
            result["beat"].append({"skill": c["id"], "reason": "score %d vs %d" % (c["score"], pick["score"]),
                                   "score": c["score"], "pick_score": pick["score"]})
    result["score"] = pick["score"]
    result["evidence"] = (pick["entry"].get("fits") or {}).get(pick["key"], {}).get("ev")
    return result


def _roles(entry):
    role = entry.get("role") or []
    return [role] if isinstance(role, str) else list(role)


def _tied_on_evidence(survivors):
    """The survivors that share the best (fit, check, receipt); empty unless two or more do."""
    top = max((c["fit"], c["check"], c["receipt"]) for c in survivors)
    same = [c for c in survivors if (c["fit"], c["check"], c["receipt"]) == top]
    return same if len(same) > 1 else []


def _default_for(registry, job, traits, level):
    """The provisional default for a tie on evidence: keyed by area/surface, job or area, then by level or stakes."""
    table = ((registry.get("budget_policy") or {}).get("provisional_defaults") or {})
    area = area_of(job)
    entry = (table.get("%s/%s" % (area, traits.get("surface"))) or table.get(job) or table.get(area) or {})
    return entry.get(level) or entry.get(traits.get("stakes", "routine")) or entry.get("routine")


def gap_for(registry, job, traits, level):
    """What a job gets when no skill has fit 2: the project's own rules, named, plus the watch row to open."""
    table = registry.get("gaps") or {}
    chain = []
    for key in ("surface:%s" % traits.get("surface"), "job:%s" % job, "area:%s" % area_of(job)):
        row = table.get(key)
        if row:
            chain += [x for x in row.get("rules", []) if x not in chain]
            if row.get("lens") and row["lens"] not in chain:
                chain.append(row["lens"])
    if not chain:
        chain = ["lens:%s.md" % area_of(job)]
    where = traits.get("surface") or "any"
    return {"job": job, "surface": traits.get("surface"), "fallback": chain, "watch_row": "gap/%s/%s" % (job, where)}


# --- plan: author, checkers, debate, agent cap (15.4) ----------------------------------------------------
def plan(registry, job, traits=None, env=None):
    """The author for a job plus the checkers the budget level allows. -> select()'s dict with checkers [{...}], debate,
    agent_runs, recommendations. A checker equal to the author is rejected as `same_skill`; the plan's agents, sub-agents
    included, never exceed the level's cap."""
    traits = normalise(traits)
    env = env or {}
    base = select(registry, job, traits, env)
    level = base["level"]
    policy = registry["budget_policy"]["levels"][level]
    info = (registry.get("jobs") or {}).get(job, {})
    base.update(checkers=[], debate=False, agent_runs=base["agents"], recommendations=[], needs_d39=[])
    by_level = info.get("runs_by_level")
    if by_level:     # D33: a job answered by simulated users runs N times by budget level; the spread is always reported
        base["persona_runs"] = int(by_level.get(level, 1))
    author = base["primary"]
    if info.get("role") != "author" or not info.get("checks"):
        return base
    room = int(policy["agent_cap"]) - base["agent_runs"]
    paid = int(policy.get("checkers", 0))
    used_skills = {author} if author else set()
    for check_job in info["checks"]:
        sub = select(registry, check_job, dict(traits, author=author or ""), env, exclude=used_skills)
        for rej in sub["rejected"]:
            if rej["reason"] == "same_skill" and rej["skill"] not in {r["skill"] for r in base["rejected"]}:
                base["rejected"].append(dict(rej, job=check_job))
        pick = sub["primary"]
        if not pick:
            continue
        entry = entries_by_id(registry)[pick]
        is_instrument = "instrument" in _roles(entry) and "checker" not in _roles(entry)
        if not is_instrument and paid <= 0:
            continue
        if sub["agents"] > room:
            base["rejected"].append({"skill": pick, "reason": "agent_cap", "job": check_job})
            continue
        if not is_instrument:
            paid -= 1
        room -= sub["agents"]
        base["agent_runs"] += sub["agents"]
        used_skills.add(pick)
        base["checkers"].append({"job": check_job, "skill": pick, "agents": sub["agents"], "load": sub["load"], "why": sub["why"]})
    if policy.get("debate") and traits.get("stakes") == "pillar" and author and base["checkers"]:
        base["debate"] = True
        base["needs_d39"].append("debate")
    kind = info.get("policy_kind")
    if isinstance(kind, dict):
        kind = kind.get(traits.get("surface")) or kind.get("*")
    row = policy_row(registry, kind, level)
    if row:
        base["recommendations"].append(row)
        if row.get("needs_d39"):
            base["needs_d39"].append("policy")
    return base


def policy_row(registry, kind, level):
    for row in (registry.get("budget_policy") or {}).get("matrix", []):
        if row.get("kind") == kind:
            cell = row.get("cells", {}).get(level)
            if cell:
                return {"kind": kind, "level": level, "text": cell.get("text"), "text_es": cell.get("text_es"), "needs_d39": bool(cell.get("needs_d39"))}
    return None


# --- the log line (D29, D38) -----------------------------------------------------------------------------
def log_line(result, ts, nodes=None):
    """One JSON-able record for .budgit/skills-log.jsonl."""
    checkers = result.get("checkers") or []
    return {
        "ts": ts, "job": result["job"], "traits": result["traits"], "picked": result["primary"],
        "role": result.get("role"), "why": result["why"],
        "beat": result["beat"], "rejected": result["rejected"],
        "companions": [c["skill"] for c in checkers],
        "fallback_used": result.get("fallback_used"), "gap": result["gap"]["watch_row"] if result.get("gap") else None,
        "registry": result["registry"], "budget": result["level"], "via": result.get("via"),
        "debate": bool(result.get("debate")), "winner": None,
        "provisional": bool(result.get("provisional")), "bakeoff": result.get("bakeoff"),
        "agent_runs": result.get("agent_runs", result.get("agents", 0)), "runs": result.get("persona_runs"),
        "nodes": nodes if nodes is not None else [{"id": result["primary"] or "gap", "calls": 0, "tokens": None}],
    }
