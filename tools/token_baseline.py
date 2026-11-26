#!/usr/bin/env python3
"""R-849: measure subagent token cost from real transcripts, zero model calls.

Reads only the usage fields and tool_use counts of agent-*.jsonl files (never
prints transcript text). The agent type comes from the sibling .meta.json.

    python3 tools/token_baseline.py <projects-dir-or-subagents-dir> [--since YYYY-MM-DD]

Per agent type and model it reports the sample count, the median fixed context of
the first assistant turn (input + cache_creation + cache_read tokens) and the
median total tokens per tool call (all turns, output included).
"""
import json
import os
import statistics
import sys
import time


def _usage_total(u, with_output):
    keys = ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
    if with_output:
        keys.append("output_tokens")
    return sum(int(u.get(k) or 0) for k in keys)


def read_transcript(path):
    """Return dict(first_context, total, calls, turns, model) or None."""
    turns = {}
    order = []
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message")
            if rec.get("type") != "assistant" or not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            key = msg.get("id") or rec.get("uuid") or str(len(order))
            if key not in turns:
                order.append(key)
                turns[key] = {"usage": usage, "tool_ids": set(), "model": msg.get("model")}
            else:
                turns[key]["usage"] = usage  # streaming repeats: the last one is final
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        turns[key]["tool_ids"].add(block.get("id") or id(block))
    if not order:
        return None
    first = turns[order[0]]
    calls = len(set().union(*[turns[k]["tool_ids"] for k in order]))
    return {
        "first_context": _usage_total(first["usage"], False),
        "total": sum(_usage_total(turns[k]["usage"], True) for k in order),
        "calls": calls,
        "turns": len(order),
        "model": first["model"] or "?",
    }


def agent_type(jsonl_path):
    meta = jsonl_path[: -len(".jsonl")] + ".meta.json"
    try:
        with open(meta, encoding="utf-8") as fh:
            return json.load(fh).get("agentType") or "?"
    except (OSError, ValueError):
        return "?"


def find_transcripts(root, since=None):
    out = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            if name.startswith("agent-") and name.endswith(".jsonl"):
                p = os.path.join(base, name)
                if since is not None and os.path.getmtime(p) < since:
                    continue
                out.append(p)
    return sorted(out)


def summarize(paths):
    groups = {}
    for p in paths:
        r = read_transcript(p)
        if r is None:
            continue
        groups.setdefault((agent_type(p), r["model"]), []).append(r)
    rows = []
    for (atype, model), rs in sorted(groups.items()):
        per_call = [r["total"] / r["calls"] for r in rs if r["calls"]]
        rows.append({
            "agent": atype,
            "model": model,
            "n": len(rs),
            "first_context": int(statistics.median(r["first_context"] for r in rs)),
            "per_call": int(statistics.median(per_call)) if per_call else 0,
            "median_calls": int(statistics.median(r["calls"] for r in rs)),
        })
    return rows


def render(rows):
    lines = [
        "| agent type | model | n | median first-turn context | median tokens per tool call | median calls |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append("| %s | %s | %d | %d | %d | %d |" % (
            r["agent"], r["model"], r["n"], r["first_context"], r["per_call"], r["median_calls"]))
    return "\n".join(lines)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    since = None
    if "--since" in argv:
        i = argv.index("--since")
        since = time.mktime(time.strptime(argv[i + 1], "%Y-%m-%d"))
        del argv[i:i + 2]
    print(render(summarize(find_transcripts(argv[0], since))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
