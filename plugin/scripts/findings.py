"""R-1213 part 2: structured findings on ledger items.

A finding is {id: F<n> per item, sev, class, text, state, at, by}; state is open, fixed (with a sha) or deferred (to an item).
Every change appends an item event of kind `finding`. One write path: the caller saves with studiolib.save_ledger (which keeps
the integrity hash). `open_high` is THE count `ledger done` reads; `handback_gap` is THE check a review hand-back runs.
"""
import re

import studiolib as sl

SEVS = ("HIGH", "MED", "LOW")
CLASSES = ("correctness", "security", "privacy", "performance", "test-gap", "docs", "ux", "process")
STATES = ("open", "fixed", "deferred")
MAX_FINDINGS = 200
_SHA = re.compile(r"[0-9a-f]{7,40}")
_ITEM = re.compile(r"R-\d+")
_VERDICT_BAD = ("HOLD", "FAIL")


def _shield(text, names, limit):
    import runtrack
    return runtrack._shielded(text, names, limit)


def rows(item):
    return [f for f in item.get("findings") or () if isinstance(f, dict)]


def add(data, item_id, sev, cls, text, at=None, by=None, names=None):
    import i18n
    item = sl.find_item(data, item_id)
    if sev not in SEVS:
        raise sl.StudioError(i18n.t("finding.bad_sev", sevs=", ".join(SEVS)))
    if cls not in CLASSES:
        raise sl.StudioError(i18n.t("finding.bad_class", classes=", ".join(CLASSES)))
    import runtrack
    names = runtrack._names(names)
    line = _shield(text, names, 200)
    if not line:
        raise sl.StudioError(i18n.t("finding.no_text"))
    items = item.setdefault("findings", [])
    if len(items) >= MAX_FINDINGS:
        raise sl.StudioError(i18n.t("finding.too_many", n=MAX_FINDINGS))
    nums = [int(f["id"][1:]) for f in rows(item) if re.fullmatch(r"F\d+", str(f.get("id")))]
    fid = "F%d" % (max(nums, default=0) + 1)
    at = at or sl.now()
    who = sl.oneline(str(by or "budgit"), 60)
    items.append({"id": fid, "sev": sev, "class": cls, "text": line, "state": "open", "at": at, "by": who})
    item["updated"] = at
    sl.add_event(item, "finding", who, "%s %s %s: %s" % (fid, sev, cls, line), at=at)
    return fid


def set_state(data, item_id, fid, state, arg=None, at=None, by=None, project=None):
    """`project` (the CLI always passes it) makes `fixed` need a sha that is a commit in the project repo (the proof check of
    `ledger evidence`): a made-up sha never closes a finding."""
    import i18n
    item = sl.find_item(data, item_id)
    row = next((f for f in rows(item) if f.get("id") == fid), None)
    if row is None:
        raise sl.StudioError(i18n.t("finding.missing", id=item_id, fid=sl.oneline(str(fid), 20)))
    if state not in STATES:
        raise sl.StudioError(i18n.t("finding.bad_state", states=", ".join(STATES)))
    row.pop("fixed_by", None)
    row.pop("deferred_to", None)
    if state == "fixed":
        sha = (arg or "").strip().lower()
        if not _SHA.fullmatch(sha):
            raise sl.StudioError(i18n.t("finding.bad_sha"))
        if project is not None and not sl._sha_in_repo(project, sha):
            raise sl.StudioError(i18n.t("finding.unreachable_sha", sha=sha))
        row["fixed_by"] = sha
        note = "fixed " + sha
    elif state == "deferred":
        target = (arg or "").strip()
        if not _ITEM.fullmatch(target) or not any(i["id"] == target for i in data["items"]):
            raise sl.StudioError(i18n.t("finding.bad_target", target=sl.oneline(target, 20)))
        row["deferred_to"] = target
        note = "deferred to " + target
    else:
        note = "open"
    row["state"] = state
    at = at or sl.now()
    item["updated"] = at
    sl.add_event(item, "finding", sl.oneline(str(by or "budgit"), 60), "%s -> %s" % (fid, note), at=at)
    return row


def open_counts(item):
    out = {s: 0 for s in SEVS}
    for f in rows(item):
        if f.get("state") == "open" and f.get("sev") in out:
            out[f["sev"]] += 1
    return out


def open_high(item):
    return open_counts(item)["HIGH"]


def done_gate(item, lang=None):
    """Called by `ledger done` for one item. Raises when an open HIGH finding remains; -> a warning line (or None) for open MED/LOW."""
    import i18n
    c = open_counts(item)
    if c["HIGH"]:
        raise sl.StudioError(i18n.t("finding.done_refused", lang, id=item["id"], n=c["HIGH"]))
    if c["MED"] or c["LOW"]:
        return i18n.t("finding.done_warn", lang, id=item["id"], med=c["MED"], low=c["LOW"])
    return None


def listing(data, item_id=None, names=()):
    """-> rows (open first, then by id), each {item, id, sev, class, text, state, at, by}. No id: the current phase's items
    (the release in play; with none, every item)."""
    if item_id:
        items = [sl.find_item(data, item_id)]
    else:
        phase = sl.current_version(data)
        items = [i for i in data["items"] if (not phase or i.get("phase") == phase)]
    out = []
    for it in items:
        for f in rows(it):
            out.append({"item": it["id"], "id": f.get("id"), "sev": f.get("sev"), "class": f.get("class"),
                        "text": _shield(f.get("text"), names, 200), "state": f.get("state"), "at": f.get("at"), "by": _shield(f.get("by"), names, 60)})
    out.sort(key=lambda r: (r["state"] != "open", SEVS.index(r["sev"]) if r["sev"] in SEVS else 9, r["item"], r["id"] or ""))
    return out


def counts(found):
    out = {"severity": {}, "class": {}, "state": {}}
    for r in found:
        for key, val in (("severity", r["sev"]), ("class", r["class"]), ("state", r["state"])):
            out[key][val] = out[key].get(val, 0) + 1
    return out


def lines(found):
    return ["%s %s [%s] %s %s: %s" % (r["item"], r["id"], r["state"], r["sev"], r["class"], r["text"]) for r in found]


def verdict_of(message):
    first = next((ln.strip() for ln in (message or "").splitlines() if ln.strip()), "")
    word = re.sub(r"^[\s>*_#`-]+", "", first).split(" ")[0].strip(":*_`.,;").upper() if first else ""
    return word


def run_start(item, agent_type):
    """When the newest launch of this agent type was booked (None when there is none)."""
    start = None
    for e in item.get("events") or ():
        if e.get("kind") == "launch" and (e.get("by") or "").split(" (")[0] == agent_type.split(" (")[0]:
            if start is None or (e.get("at") or "") > start:
                start = e.get("at")
    return start


def handback_gap(item, agent_type, message, lang=None):
    """The hand-back check: a HOLD or FAIL report needs finding rows written since this run began (earlier rows never count).
    -> a message telling the reviewer to add them, or None. PASS never needs rows. Without a known launch nothing is judged."""
    if verdict_of(message) not in _VERDICT_BAD:
        return None
    start = run_start(item, agent_type or "")
    if start is None:
        sl.log_error("hand-back check skipped: no launch event of %s on %s" % (agent_type, item.get("id")))
        return None
    if any((f.get("at") or "") >= start for f in rows(item)):
        return None
    import i18n
    return i18n.t("finding.handback_gap", lang, id=item["id"], verdict=verdict_of(message))
