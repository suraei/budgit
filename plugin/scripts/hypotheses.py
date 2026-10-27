"""budgit hypotheses: the claims a simulated user made, each one testable, scored later against real data.

A persona run is a hypothesis, never a finding. Every claim goes in here with what real data would settle it, stays `open`
until someone scores it, and is never edited into a result. One small JSON file per project (`hypotheses.json` in the data
folder); the only thing this command writes.
"""
import json
import os
import re

import i18n
import studiolib as sl

NAME = "hypotheses.json"
STATUSES = ("open", "confirmed", "refuted", "unclear")
LINE = re.compile(r"^\s*(?:[-*]\s*)?Hypothesis:\s*(?P<claim>.+?)\s*\|\s*confirm with:\s*(?P<confirm>.+?)\s*$", re.I)


def path(project):
    return os.path.join(sl.studio_dir(project), NAME)


def load(project):
    try:
        with open(path(project), encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("id")] if isinstance(rows, list) else []


def _save(project, rows):
    sl.ensure_studio_dir(project)
    sl.atomic_write(path(project), json.dumps(rows, indent=1, ensure_ascii=False) + "\n")


def parse_report(text):
    """-> [(claim, confirm_with)] from `Hypothesis: <claim> | confirm with: <data>` lines. Lines without both halves are skipped."""
    return [(m.group("claim"), m.group("confirm")) for m in (LINE.match(l) for l in text.splitlines()) if m]


def add(project, claim, confirm_with, persona="", run="", source="", today=None):
    rows = load(project)
    number = 1 + max([int(r["id"][2:]) for r in rows if re.fullmatch(r"H-\d+", r["id"])] or [0])
    row = {"id": "H-%d" % number, "date": today or sl.now()[:10], "claim": claim.strip(), "confirm_with": confirm_with.strip(),
           "persona": persona, "run": run, "source": source, "status": "open", "scored": None, "evidence": None}
    rows.append(row)
    _save(project, rows)
    return row


def score(project, hid, status, evidence="", today=None):
    if status not in STATUSES[1:]:
        raise sl.StudioError(i18n.t("hyp.bad_status", status=status, known=", ".join(STATUSES[1:])))
    rows = load(project)
    for row in rows:
        if row["id"] == hid.upper():
            if status != "unclear" and not evidence.strip():
                raise sl.StudioError(i18n.t("hyp.need_evidence", id=row["id"]))
            row.update(status=status, scored=today or sl.now()[:10], evidence=evidence.strip() or None)
            _save(project, rows)
            return row
    raise sl.StudioError(i18n.t("hyp.missing", id=hid))


def import_report(project, report_path, persona="", run=""):
    try:
        with open(report_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise sl.StudioError(i18n.t("hyp.unreadable", path=report_path, why=exc.__class__.__name__))
    if not re.match(r"HYPOTHESIS\s+[\u2014-]", text.lstrip()):
        raise sl.StudioError(i18n.t("hyp.not_a_report", path=report_path))
    known = {(r["claim"].lower(), r.get("source")) for r in load(project)}
    added = []
    for claim, confirm in parse_report(text):
        if (claim.lower(), os.path.basename(report_path)) in known:
            continue
        added.append(add(project, claim, confirm, persona=persona, run=run, source=os.path.basename(report_path)))
    return added


def format_list(rows, status=None, lang=None):
    t = lambda k, **kw: i18n.t(k, lang, **kw)
    rows = [r for r in rows if not status or r["status"] == status]
    if not rows:
        return t("hyp.none")
    tally = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    lines = [t("hyp.head", **{s: tally[s] for s in STATUSES})]
    for r in rows:
        lines.append("  %s [%s] %s -> %s" % (r["id"], r["status"], r["claim"], r["confirm_with"]))
        if r.get("evidence"):
            lines.append("      %s" % t("hyp.evidence", text=r["evidence"], date=r["scored"]))
    return "\n".join(lines)


def add_arguments(p):
    p.add_argument("action", nargs="?", default="list", help="list, add, score, import")
    p.add_argument("rest", nargs="*", help="score: H-n confirmed|refuted|unclear; import: the persona report file")
    p.add_argument("--claim", help="add: the claim a real user could confirm or refute")
    p.add_argument("--confirm-with", dest="confirm_with", help="add: the real data that would settle it")
    p.add_argument("--persona", default="")
    p.add_argument("--run", default="", help="which run of N produced it, e.g. 2/3")
    p.add_argument("--evidence", default="", help="score: the real signal (a conversation, a sign-up count, a forum post) with its date")
    p.add_argument("--status", help="list: only this status")
    p.add_argument("--json", action="store_true")


def run(a, project, lang=None):
    if not project:
        raise sl.StudioError(i18n.t("hyp.no_project", lang))
    if a.action == "list":
        rows = load(project)
        print(json.dumps(rows, indent=2, ensure_ascii=False) if a.json else format_list(rows, a.status, lang))
    elif a.action == "add":
        if not a.claim or not a.confirm_with:
            raise sl.StudioError(i18n.t("hyp.need_both", lang))
        row = add(project, a.claim, a.confirm_with, a.persona, a.run)
        print(i18n.t("hyp.added", lang, id=row["id"]))
    elif a.action == "score":
        if len(a.rest) < 2:
            raise sl.StudioError(i18n.t("hyp.need_score_args", lang))
        row = score(project, a.rest[0], a.rest[1], a.evidence)
        print(i18n.t("hyp.scored", lang, id=row["id"], status=row["status"]))
    elif a.action == "import":
        if not a.rest:
            raise sl.StudioError(i18n.t("hyp.need_report", lang))
        added = import_report(project, a.rest[0], a.persona, a.run)
        print(i18n.t("hyp.imported", lang, n=len(added), ids=", ".join(r["id"] for r in added) or "-"))
    else:
        raise sl.StudioError(i18n.t("hyp.unknown_action", lang, action=a.action))
    return 0
