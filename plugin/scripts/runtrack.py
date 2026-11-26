"""R-1213: live item state from agent launches and hand-backs, and the timeline of an item.

A launch (PreToolUse for Agent, booked under the project lock) reads the brief: the items it names in a `# R-n` heading or in
its first 5 lines are the items of the run. They get doing_by (agent type and short description), doing_since and one `launch`
event. A hand-back (SubagentStop) adds a `handback` event and lets go of doing_by when no other run holds the item. Neither
ever moves an item to verified or done: those stay with `ledger verify` and `ledger done`. Tracking never blocks a launch:
every failure is logged by the caller and the launch goes on. One write path: studiolib.save_ledger (which keeps the hash).
"""
import datetime
import os
import re

import studiolib as sl

BRIEF_CAP = 20000
FIRST_LINES = 5
MAX_IDS = 10
_ID = re.compile(r"R-\d+")
_HEADING = re.compile(r"^#+\s*R-\d+")
_ITEM_LINE = re.compile(r"^\s*(?:items?|task)\s*:?\s+R-\d+", re.I)      # R-1273: «Item R-n ...» / «task: R-n» count past line 5 too
_MD_PATH = re.compile(r"(?<![^\s\"'`()<>=:,])(/[^\s\"'`()<>=:,]+\.md)\b")     # starts only at a word edge: no quadratic rescans


def brief_ids(text):
    """The R-n a brief names where it counts: any `# R-n` heading line, and every id in its first 5 lines."""
    out = []
    for i, line in enumerate((text or "").splitlines()):
        if i < FIRST_LINES or _HEADING.match(line) or _ITEM_LINE.match(line):
            for found in _ID.findall(line):
                if found not in out:
                    out.append(found)
    return out


def _brief_file(project, prompt):
    """The one brief file the prompt names: an absolute .md path in a scratch folder or inside the project (never the
    project's own data folder), read to BRIEF_CAP bytes. -> its text, or ''."""
    for n, m in enumerate(_MD_PATH.finditer((prompt or "")[:BRIEF_CAP])):
        if n >= 3:
            break
        real = os.path.realpath(m.group(1))
        parts = real.split(os.sep)
        if any(p in sl.DATA_NAMES for p in parts) or not os.path.isfile(real):
            continue
        root = os.path.realpath(project)
        if "scratchpad" not in parts and real != root and not real.startswith(root + os.sep):
            continue
        try:
            with open(real, "rb") as f:
                return f.read(BRIEF_CAP).decode("utf-8", "replace")
        except OSError:
            continue
    return ""


def launch_ids(project, tool_input):
    ti = tool_input if isinstance(tool_input, dict) else {}
    prompt = ti.get("prompt") if isinstance(ti.get("prompt"), str) else ""
    ids = brief_ids(prompt)
    for found in brief_ids(_brief_file(project, prompt)):
        if found not in ids:
            ids.append(found)
    return ids[:MAX_IDS]


def _names(names):
    if names is not None:
        return names
    import livework
    return livework.private_names()


def _shielded(text, names, limit):
    import livework
    return livework.shield(sl.oneline(sl.redact(str(text or "")), limit), names)


def _item(data, item_id):
    return next((it for it in data["items"] if it["id"] == item_id), None)


def _same_type(a, b):
    try:
        return sl.canon_agent(a) == sl.canon_agent(b)
    except Exception:
        return a == b


def sweep_orphans(project, at=None):
    """-> the ids freed: studiolib.sweep_orphan_runs under the one save path (`at`: epoch seconds, None = now)."""
    if sl.ledger_tampered(project):
        return []
    data = sl.load_ledger(project)
    gone = sl.sweep_orphan_runs(project, data, at)
    if gone:
        sl.save_ledger(project, data)
    return gone


def on_launch(project, tool_input, at=None, names=None):
    """-> the ids it touched. Raises on a missing or broken ledger: the caller logs it and the launch goes on."""
    try:
        sweep_orphans(project, sl._epoch(at) if at else None)      # R-1273: every launch frees runs older than 12 h
    except Exception:
        sl.log_error("run tracking: orphan sweep failed")
    ids = launch_ids(project, tool_input)
    if not ids:
        return []
    if sl.ledger_tampered(project):
        sl.log_error("run tracking: ledger hash mismatch, launch not booked")
        return []
    ti = tool_input if isinstance(tool_input, dict) else {}
    names = _names(names)
    data = sl.load_ledger(project)
    at = at or sl.now()
    kind = sl.oneline(str(ti.get("subagent_type") or "agent"), 40)
    label = _shielded("%s (%s)" % (kind, ti.get("description") or ""), names, 80)
    touched = []
    for item_id in ids:
        item = _item(data, item_id)
        if item is None or item["status"] in ("dropped", "folded"):
            continue
        status = item["status"]
        if status in ("open", "doing"):         # a launch never reopens or advances a verified or done item
            if status == "open":
                item["status"] = "doing"
                sl.add_event(item, "state", label, "open -> doing", at=at)
                item.setdefault("started_at", at)
            owner = item.get("doing_by")
            if not (status == "doing" and owner and sl.valid_session(owner) and sl.read_pointer(owner) is not None):
                item["doing_by"] = label        # a live session's own claim is never taken by a launch
                item["doing_since"] = at
            runs = item.setdefault("runs", [])
            runs.append({"by": label, "type": kind, "at": at})
            item["updated"] = at
        sl.add_event(item, "launch", label, "launch (%s)" % _shielded(ti.get("description"), names, 100), at=at)
        touched.append(item_id)
    if touched:
        sl.save_ledger(project, data)
    return touched


def launch_prompt(transcript):
    """The first user message of the agent's own transcript (its launch prompt), size-capped; None when unreadable."""
    import json
    import agentcap
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(40), f):
                try:
                    text = agentcap._user_text(json.loads(line))
                except ValueError:
                    continue
                if text is not None:
                    return text[:BRIEF_CAP]
    except (OSError, TypeError):
        pass
    return None


def on_handback(project, agent_type, message, at=None, names=None, transcript=None):
    """-> the ids it touched. The items of the run are the ones its own launch prompt named (first user message of its
    transcript, read with the launch rule). No transcript or no prompt: nothing is cleared and the sweeps do it later;
    never a match by agent type alone."""
    first = next((ln.strip() for ln in (message or "").splitlines() if ln.strip()), "")
    if not first or not agent_type:
        return []
    prompt = launch_prompt(transcript) if transcript else None
    if prompt is None:
        # R-1273: no transcript: the ids the hand-back message itself names in its first lines (the launch rule), with the
        # agent type; no id named means nothing is released (never by type alone)
        own = brief_ids("\n".join((message or "").splitlines()[:FIRST_LINES]))
        if not own:
            sl.log_error("run tracking: no launch prompt for the hand-back, claims left to the sweeps")
            return []
    if sl.ledger_tampered(project):
        sl.log_error("run tracking: ledger hash mismatch, hand-back not booked")
        return []
    if prompt is not None:
        own = launch_ids(project, {"prompt": prompt})
    names = _names(names)
    data = sl.load_ledger(project)
    at = at or sl.now()
    holders = [it for it in data["items"] if it["id"] in own
               and any(_same_type(r.get("type"), agent_type) for r in it.get("runs") or ())]
    touched = []
    for item in holders:
        runs = item["runs"]
        for i, r in enumerate(runs):
            if _same_type(r.get("type"), agent_type):
                del runs[i]
                break
        if runs:
            item["doing_by"], item["doing_since"] = runs[-1]["by"], runs[-1]["at"]
        else:
            item.pop("runs", None)
            if not sl.valid_session(item.get("doing_by") or ""):
                item.pop("doing_by", None)
                item.pop("doing_since", None)
        item["updated"] = at
        import findings
        try:
            gap = findings.handback_gap(item, str(agent_type), message)   # judged before the run's own events move on
        except Exception as exc:                                          # a failed check never skips the booking
            gap = None
            sl.log_error("hand-back check failed: %s" % type(exc).__name__)
        sl.add_event(item, "handback", sl.oneline(str(agent_type), 40), _shielded(first, names, 150), at=at)
        if gap:
            sl.add_event(item, "note", sl.oneline(str(agent_type), 40), gap, at=at)
            sl.log_error("hand-back check: " + gap)
        touched.append(item["id"])
    if touched:
        sl.save_ledger(project, data)
    return touched


# ---------------------------------------------------------------- timeline

def _epoch(stamp):
    try:
        return datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def local_time(stamp):
    ts = _epoch(stamp)
    if ts is None:
        return sl.oneline(stamp, 40)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _span(seconds):
    s = max(0, int(seconds))
    if s >= 3600:
        return "%dh %02dm" % (s // 3600, s % 3600 // 60)
    return "%dm %02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


def ordered(item):
    return sorted(item.get("events") or [], key=lambda e: e.get("at") or "")      # stable: ties keep their written order


def elapsed_runs(events):
    """[(by, seconds or None)] per launch: its first later hand-back of the same agent type (None: still running)."""
    open_runs, out = [], []
    for e in events:
        if e.get("kind") == "launch":
            rec = [e.get("by") or "", _epoch(e.get("at")), None]
            open_runs.append(rec)
            out.append(rec)
        elif e.get("kind") == "handback":
            t = (e.get("by") or "").split(" (")[0]
            for rec in open_runs:
                if (rec[0] or "").split(" (")[0] == t:
                    start, end = rec[1], _epoch(e.get("at"))
                    rec[2] = end - start if start is not None and end is not None else None
                    open_runs.remove(rec)
                    break
    return [(by, secs) for by, _start, secs in out]


def timeline_lines(item, lang=None, names=()):
    import i18n
    events = ordered(item)
    if not events:
        return [i18n.t("ledger.timeline_none", lang, id=item["id"])]
    import livework
    lines = ["%s  %-8s %s  %s" % (local_time(e.get("at")), sl.oneline(e.get("kind") or "", 120), livework.shield(sl.oneline(e.get("by") or "", 120), names),
                                  livework.shield(sl.oneline(e.get("text") or "", 300), names)) for e in events]
    for by, secs in elapsed_runs(events):
        lines.append(i18n.t("ledger.timeline_elapsed" if secs is not None else "ledger.timeline_running", lang,
                            by=livework.shield(sl.oneline(by, 120), names), span=_span(secs) if secs is not None else ""))
    return lines
