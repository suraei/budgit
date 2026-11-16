"""Replay the message-shape score over transcripts (R-1112 phase 0).

Run as `budgit msgshape replay [--last N]`; it finds the transcripts itself and takes no path.

Picks the final assistant text of each turn that began with a real user
prompt (not a tool result, peer or subagent message), newest first, takes the
first N and prints ONLY numbers. It never prints or saves message text.
"""
import hashlib
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgshape  # noqa: E402

_SKIP_PREFIX = ("<", "[Request interrupted", "Caveat:",
                "This session is being continued")


def _blocks(msg):
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def _is_real_prompt(obj):
    if obj.get("isMeta") or obj.get("toolUseResult") is not None:
        return False
    blocks = _blocks(obj.get("message"))
    if any(b.get("type") == "tool_result" for b in blocks):
        return False
    texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    text = "\n".join(texts).strip()
    return bool(text) and not text.startswith(_SKIP_PREFIX)


def turn_finals(path):
    """Yield (timestamp, final assistant text) for each turn in one transcript."""
    in_turn = False
    last = None  # (ts, text or None) of the last assistant entry in the turn
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("isSidechain"):
                continue
            kind = obj.get("type")
            if kind == "user":
                if not _is_real_prompt(obj):
                    continue
                if in_turn and last and last[1]:
                    yield last
                in_turn, last = True, None
            elif kind == "assistant" and in_turn:
                texts = [b.get("text", "") for b in _blocks(obj.get("message"))
                         if b.get("type") == "text" and b.get("text", "").strip()]
                last = (obj.get("timestamp") or "", texts[-1] if texts else None)
    if in_turn and last and last[1]:
        yield last


def check_out(project, out):
    """--out must resolve to a file inside <project data folder>/reports; anything else is refused."""
    import studiolib as sl
    reports = os.path.realpath(os.path.join(sl.studio_dir(project), "reports"))
    target = os.path.realpath(os.path.expanduser(out))
    if os.path.commonpath([reports, target]) != reports or target == reports:
        raise sl.StudioError("--out must be a file inside %s" % reports)
    return target


def replay_lines(project, last=30):
    """The numbers-only report lines for the last `last` coordinator messages of this project's transcripts."""
    import glob
    import studiolib as sl
    rows = []
    for path in sorted(glob.glob(os.path.join(sl.transcripts_dir(project), "*.jsonl"))):
        tag = hashlib.sha1(os.path.basename(path).encode()).hexdigest()[:8]
        try:
            for ts, text in turn_finals(path):
                rows.append((ts, tag, msgshape.score(text)))
        except OSError:
            pass
    rows.sort(key=lambda r: r[0], reverse=True)
    rows = rows[:last]
    lines = ["session ok lines table max_list code_names steps"]
    for _, tag, s in rows:
        lines.append("%s %d %d %d %s %s %s" % (tag, int(s["ok"]), s["lines"], int(s["table"]),
                                              s["max_list"], s["code_names"], s["steps"]))
    oks = sum(1 for _, _, s in rows if s["ok"])
    med = statistics.median([s["lines"] for _, _, s in rows]) if rows else 0
    lines.append("TOTAL ok %d of %d, median lines %s" % (oks, len(rows), med))
    return lines
