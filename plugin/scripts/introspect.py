"""Continuous introspection, deterministic and 0 tokens until a trigger fires.

Triggers: the same tool failure for the 3rd time in a session (PostToolUseFailure), and team rules broken since
the previous prompt (live `comply` delta at UserPromptSubmit). Each fires once, then resets, so it never nags.
"""
import glob
import hashlib
import json
import os

import studiolib as sl

REPEAT = 3
ASK = ("Before going on, ask: is this inefficient or wrong? In one line: what you change now, or the ledger item "
       "you open (`budgit ledger add`); if nothing is wrong, say why and continue.")
LIVE = ("agents_without_model", "bursts_over_limit", "agents_over_call_budget", "compactions_without_checkpoint")
# not live: untracked inputs (own Stop guard) and research_unsaved (a running researcher has not saved yet → noise)


def _state_path(session_id):
    return os.path.join(sl.home_state(), "introspect", "%s.json" % session_id)


def _load(session_id):
    try:
        with open(_state_path(session_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(session_id, data):
    os.makedirs(os.path.dirname(_state_path(session_id)), mode=0o700, exist_ok=True)
    sl.atomic_write(_state_path(session_id), json.dumps(data))


def _key(tool, tool_input):
    """Same call = same tool AND same input (minus the free-text description). Bash: whitespace-normalised command."""
    tool_input = dict(tool_input or {})
    tool_input.pop("description", None)
    if tool == "Bash":   # hash the WHOLE command; the prefix is only for display (verifier R-50: 120-char collisions)
        cmd = " ".join(str(tool_input.get("command", "")).split())
        return "Bash:%s#%s" % (cmd[:60], hashlib.sha1(cmd.encode()).hexdigest()[:10])
    blob = json.dumps(tool_input, sort_keys=True, ensure_ascii=True, default=str)
    hint = tool_input.get("file_path") or tool_input.get("pattern") or tool_input.get("query") or tool_input.get("url") or ""
    return "%s:%s#%s" % (tool, str(hint)[:60], hashlib.sha1(blob.encode()).hexdigest()[:10])


def on_failure(session_id, tool, tool_input, error):
    if not sl.valid_session(session_id):
        return None
    data = _load(session_id)
    fails = data.setdefault("fails", {})
    key = _key(tool, tool_input)
    fails[key] = fails.get(key, 0) + 1
    note = None
    if fails[key] >= REPEAT:
        fails[key] = 0
        note = ("budgit self-check: 3rd failure of the same call (%s): %s. Retrying the same way is waste. "
                % (key[:80], (error or "").splitlines()[0][:120] if error else "error") + ASK)
    _save(session_id, data)
    return note


def _new_lines(path, offset):
    """Complete lines appended since `offset` -> (records, new_offset). Reads only the new bytes."""
    try:
        size = os.path.getsize(path)
        if size < offset:      # rewritten file: start over
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return [], offset
    end = chunk.rfind(b"\n") + 1
    recs = []
    for line in chunk[:end].splitlines():
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    return recs, offset + end


def _live_counts(transcript_path, level, inc):
    """comply's live counters, incrementally: state `inc` keeps offsets and running tallies (verifier R-50: 1.8 s
    when the whole 716 MB session was re-read on every prompt)."""
    import budget
    import comply
    pol = budget.POLICY[level]
    recs, inc["off"] = _new_lines(transcript_path, inc.get("off", 0))
    per_msg, nomodel = inc.setdefault("per_msg", {}), inc.setdefault("nomodel", [])
    for rec in recs:
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            if not inc.get("ckpt"):
                inc["cwc"] = inc.get("cwc", 0) + 1
            inc["ckpt"] = False
            continue
        for mid, block in comply._tool_uses(rec):
            if comply._is_progress_write(block):
                inc["ckpt"] = True
            if block.get("name") in ("Agent", "Task"):
                per_msg[mid or "-"] = per_msg.get(mid or "-", 0) + 1
                if not (block.get("input") or {}).get("model"):
                    nomodel.append(block.get("id"))
    subs = inc.setdefault("subs", {})
    sid = os.path.basename(transcript_path)[:-6]
    for ap in glob.glob(os.path.join(os.path.dirname(transcript_path), sid, "subagents", "agent-*.jsonl")):
        off, calls = subs.get(ap, (0, 0))
        new, off2 = _new_lines(ap, off)
        if off2 < off:
            calls = 0
        calls += sum(1 for rec in new for _ in comply._tool_uses(rec))
        subs[ap] = (off2, calls)
    filled = budget.filled_ids(sid)
    return {"agents_without_model": sum(1 for t in nomodel if t not in filled),
            "bursts_over_limit": sum(1 for n in per_msg.values() if n > max(pol["subagents"], 1)),
            "agents_over_call_budget": sum(1 for _, c in subs.values() if c > pol["agent_calls"]),
            "compactions_without_checkpoint": inc.get("cwc", 0)}


def live_check(session_id, transcript_path, level=None):
    if not sl.valid_session(session_id) or not transcript_path or not os.path.isfile(transcript_path):
        return None
    import budget
    import comply
    level = level or budget.current()["level"]
    data = _load(session_id)
    if data.get("path") != transcript_path:
        data["inc"], data["path"] = {}, transcript_path
    now = _live_counts(transcript_path, level, data.setdefault("inc", {}))
    before = data.get("live")
    if data.get("level") != level:   # caps changed: old work re-scored is not news (verifier R-50)
        before = None
    data["live"], data["level"] = now, level
    _save(session_id, data)
    if before is None:
        return None
    grew = ["+%d %s" % (now[k] - before.get(k, 0), comply.LABELS[k]) for k in LIVE if now[k] > before.get(k, 0)]
    if not grew:
        return None
    return "budgit self-check: since the last prompt: %s (rules: reference/budget.md). " % ", ".join(grew) + ASK
