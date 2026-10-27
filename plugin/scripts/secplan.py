"""budgit security plan: which security reviewers a trigger calls for, from the registry's table. A printed plan only.

Nothing here runs a reviewer, sends a diff, or edits a file except one line appended to the project's selection log.
The table (`security_policy`) and the path classes (`path_risk`) are data in the skills registry; this module reads them,
decides whether the touched paths are security-relevant, and removes the reviewers the situation forbids:

- critical budget: no Claude agent now (R2 and R3 are queued; a blocking release may take them anyway);
- a protected path, or the Codex lane off: no Codex (R1);
- nothing in the plan is run by this command; each reviewer is named with the line that runs it.
"""
import json
import time

import i18n
import skills as sk
import studiolib as sl

CLAUDE_AGENTS = ("R2", "R3")
RUN_HINT = {
    "R0": "budgit secrets --staged; budgit audit-mcp; budgit audit",
    "R1": "budgit codex review",
    "R2": "Agent studio:security-reviewer, model=sonnet",
    "R3": "Agent studio:security-reviewer, model=opus",
}


def classify(registry, paths, diff_text=""):
    """-> {class: [matching path or '(diff)']} for the registry's `path_risk` patterns, case-insensitive substrings."""
    classes = {}
    diff = (diff_text or "").lower()
    for name, patterns in (registry.get("path_risk") or {}).items():
        hits = []
        for pat in patterns:
            pat = pat.lower()
            hits += [p for p in paths if pat in p.lower() and p not in hits]
            if diff and pat in diff and "(diff)" not in hits:
                hits.append("(diff)")
        if hits:
            classes[name] = hits
    return classes


def protected_hits(project, paths):
    patterns = (sl.load_project_config(project).get("protected_paths") if project else None) or []
    if not patterns:
        return []
    return [p for p in paths if sl._protected_match(p, project, patterns)]


def codex_lane_on():
    """The Codex lane is opt-in and needs its program; both must be true."""
    try:
        import codexlane
        return sl.load_config().get("codex") == "on" and bool(codexlane._codex_bin())
    except Exception:
        return False


def plan(registry, trigger, paths, level, protected=(), codex_on=False, small=False, blocking=False, diff_text=""):
    """-> dict: trigger, level, relevant, classes, protected, reviewers [{id, name, run}], queued [...], dropped [{reviewer, reason}]."""
    policy = registry.get("security_policy") or {}
    table = (policy.get("table") or {}).get(trigger)
    if table is None:
        raise sl.StudioError(i18n.t("security.bad_trigger", trigger=trigger, known=", ".join(sorted(policy.get("triggers") or {}))))
    classes = classify(registry, list(paths), diff_text)
    hits = list(protected)
    relevant = bool(classes or hits)
    by_level = table.get(level) or table.get("*") or {}
    kind = "relevant" if relevant else "routine"
    if small and not relevant and "small" in by_level:
        kind = "small"
    chosen = list(by_level.get(kind, by_level.get("routine", [])))
    queued = list(by_level.get("queued", []))
    dropped = []
    if level == "critical":
        for r in [x for x in chosen if x in CLAUDE_AGENTS]:
            chosen.remove(r)
            queued.append(r)
            dropped.append({"reviewer": r, "reason": "critical"})
        if blocking and trigger == "T4":
            chosen += [r for r in queued if r not in chosen]
            queued = []
    if "R1" in chosen and (hits or not codex_on):
        chosen.remove("R1")
        dropped.append({"reviewer": "R1", "reason": "protected_path" if hits else "codex_off"})
    names = policy.get("reviewers") or {}
    return {"trigger": trigger, "level": level, "relevant": relevant, "classes": classes, "protected": hits,
            "reviewers": [{"id": r, "name": names.get(r, r), "run": RUN_HINT.get(r, "")} for r in chosen],
            "queued": sorted(set(queued)), "dropped": dropped, "kind": kind}


def log_record(result, ts):
    """The line for the selection log: every plan names its reviewers and its trigger."""
    return {"ts": ts, "job": "security/plan", "role": "security", "trigger": result["trigger"], "budget": result["level"],
            "reviewer": [r["id"] for r in result["reviewers"]], "queued": result["queued"], "dropped": result["dropped"],
            "relevant": result["relevant"], "classes": sorted(result["classes"]), "protected": bool(result["protected"]),
            "via": "plan", "findings": None}


def format_plan(result, lang=None):
    t = lambda k, **kw: i18n.t(k, lang, **kw)
    lines = [t("security.head", trigger=result["trigger"], level=result["level"],
               kind=t("security.kind_relevant" if result["relevant"] else "security.kind_routine"))]
    if result["classes"]:
        lines.append(t("security.classes", classes=", ".join("%s (%s)" % (c, ", ".join(p[:3])) for c, p in sorted(result["classes"].items()))))
    if result["protected"]:
        lines.append(t("security.protected", paths=", ".join(result["protected"][:5])))
    if not result["reviewers"]:
        lines.append(t("security.none"))
    for r in result["reviewers"]:
        lines.append(t("security.reviewer", id=r["id"], name=r["name"], run=r["run"]))
    for d in result["dropped"]:
        lines.append(t("security.dropped", id=d["reviewer"], reason=t("security.reason." + d["reason"])))
    if result["queued"]:
        lines.append(t("security.queued", ids=", ".join(result["queued"])))
    lines.append(t("security.foot"))
    if result.get("logged"):
        lines.append(t("security.logged", path=result["logged"]))
    return "\n".join(lines)


def add_arguments(p):
    p.add_argument("action", nargs="?", default="plan", help="plan")
    p.add_argument("--trigger", required=True, help="T1 each edit, T2 each turn end, T3 each commit, T4 before a push or release")
    p.add_argument("--paths", nargs="*", default=[], help="the files the change touches (relative to the project)")
    p.add_argument("--diff", help="a file holding the diff text, for content patterns such as eval(")
    p.add_argument("--level", help="budget level (default: the current one)")
    p.add_argument("--small", action="store_true", help="the diff is small (T4 at the normal level takes the smaller reviewer)")
    p.add_argument("--blocking", action="store_true", help="the release is blocking: at critical, queued reviewers run now")
    p.add_argument("--registry")
    p.add_argument("--no-log", action="store_true")
    p.add_argument("--json", action="store_true")


def run(a, project=None, lang=None):
    if a.action != "plan":
        raise sl.StudioError(i18n.t("security.unknown_action", lang, action=a.action))
    registry = sk.load_registry(a.registry)
    level = sk.current_level(a.level)
    diff_text = ""
    if a.diff:
        with open(a.diff, encoding="utf-8", errors="ignore") as f:
            diff_text = f.read()
    paths = [p.strip() for p in a.paths if p.strip()]
    result = plan(registry, a.trigger.upper(), paths, level, protected_hits(project, paths), codex_lane_on(),
                  small=a.small, blocking=a.blocking, diff_text=diff_text)
    record = log_record(result, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    if project and not a.no_log:
        result["logged"] = sk.append_log(project, record)
    print(json.dumps(record, indent=2) if a.json else format_plan(result, lang))
    return 0
