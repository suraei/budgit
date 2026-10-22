"""budgit comply — does the team follow its own rules? Offline, zero model tokens, over Claude Code transcripts.

Checks per main session (~/.claude/projects/<dir>/<session>.jsonl + <session>/subagents/agent-*.jsonl):
- agents_without_model: Agent calls with no `model=` (the budget rule: pass it on EVERY call)
- bursts_over_limit: one assistant message launching more agents than the level's parallel cap
- agents_over_call_budget: subagents whose tool calls exceed the level's per-agent budget
- compactions_without_checkpoint: compactions with no progress.md write since the previous one
- untracked_inputs / research_unsaved: only for sessions bound to a Budgit project (ledger, .budgit/reports)
"""
import glob
import json
import os
import re
import time

import budget
import studiolib as sl

COUNTERS = ("agents_without_model", "bursts_over_limit", "agents_over_call_budget",
            "compactions_without_checkpoint", "untracked_inputs", "research_unsaved")
LABELS = {"agents_without_model": "agents without model=", "bursts_over_limit": "bursts over parallel cap",
          "agents_over_call_budget": "agents over call budget",
          "compactions_without_checkpoint": "compactions w/o checkpoint",
          "untracked_inputs": "untracked inputs", "research_unsaved": "research not saved"}


def _records(path):
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _tool_uses(rec):
    msg = rec.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    if rec.get("type") != "assistant" or not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield msg.get("id"), block


def _is_progress_write(block):
    if block.get("name") not in ("Write", "Edit", "MultiEdit"):
        return False
    return str((block.get("input") or {}).get("file_path", "")).endswith(tuple(os.path.join(n, "progress.md") for n in sl.DATA_NAMES))


def _subagents(main_path):
    sid = os.path.basename(main_path)[:-6]
    for ap in sorted(glob.glob(os.path.join(os.path.dirname(main_path), sid, "subagents", "agent-*.jsonl"))):
        try:
            with open(ap[:-6] + ".meta.json") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        aid = os.path.basename(ap)[len("agent-"):-6]
        calls, wrote_research = 0, False
        for rec in _records(ap):
            for _, block in _tool_uses(rec):
                calls += 1
                if block.get("name") == "Write" and "/research/" in str((block.get("input") or {}).get("file_path", "")):
                    wrote_research = True
        yield aid, meta.get("agentType", "?"), calls, wrote_research


def audit(main_path, level=None, project=None):
    level = level or budget.current()["level"]
    pol = budget.POLICY[level]
    sid = os.path.basename(main_path)[:-6]
    r = dict.fromkeys(COUNTERS, 0)
    r.update(session=sid, level=level, agents=0, compactions=0, models_filled=0)
    per_message, checkpointed = {}, False
    filled = budget.filled_ids(sid)   # the PreToolUse hook filled these (model fill); shown, not violations
    for rec in _records(main_path):
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            r["compactions"] += 1
            if not checkpointed:
                r["compactions_without_checkpoint"] += 1
            checkpointed = False
            continue
        for mid, block in _tool_uses(rec):
            if _is_progress_write(block):
                checkpointed = True
            if block.get("name") in ("Agent", "Task"):
                r["agents"] += 1
                per_message[mid] = per_message.get(mid, 0) + 1
                if not (block.get("input") or {}).get("model"):
                    if block.get("id") in filled:
                        r["models_filled"] += 1
                    else:
                        r["agents_without_model"] += 1
    r["bursts_over_limit"] = sum(1 for n in per_message.values() if n > max(pol["subagents"], 1))
    subs = list(_subagents(main_path))
    r["agents_over_call_budget"] = sum(1 for _, _, calls, _ in subs if calls > pol["agent_calls"])
    project = project or sl.project_for_session(sid)
    if project:
        r["project"] = project
        r["untracked_inputs"] = len(sl.untracked(project, sl.load_ledger(project), session=sid))
        reports = " ".join(sl.recent_reports(project, n=10000))
        # saved = a report under .budgit/reports (SubagentStop hook) OR the agent wrote research/*.md itself
        r["research_unsaved"] = sum(1 for aid, kind, _, wrote in subs
                                    if kind.endswith("researcher") and not wrote and aid[:8] not in reports)
    return r


def violations(r):
    return sum(r[k] for k in COUNTERS)


# the user's standing rule: CTF/pentest material stays out of corpora and listings
EXCLUDE_DIRS = re.compile(r"ctf|pentest", re.I)


def claude_dir_name(path):
    """Claude Code's transcript dir for a project: the absolute path with every non-alphanumeric char as '-'."""
    return re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(path))


def sessions(root, days=7, project=None):
    """Main-session transcripts modified in the last `days`, oldest first, as (path, claude dir name).
    All projects, or only `project`: its own transcript dir plus sessions bound to it from elsewhere.
    Keeps the CTF/pentest exclusion; shared by `comply` and `retro-mine`."""
    cutoff = time.time() - days * 86400
    want = os.path.realpath(project) if project else None
    out = []
    for main in sorted(glob.glob(os.path.join(root, "*", "*.jsonl")), key=os.path.getmtime):
        d = os.path.basename(os.path.dirname(main))
        if EXCLUDE_DIRS.search(d) or os.path.getmtime(main) < cutoff:
            continue
        if want:
            bound = sl.project_for_session(os.path.basename(main)[:-6])
            if d != claude_dir_name(want) and not (bound and os.path.realpath(bound) == want):
                continue
        out.append((main, d))
    return out


def scan(root, days=7, level=None, project=None):
    rows = []
    for main, d in sessions(root, days, project):
        row = audit(main, level=level)
        row["dir"] = d
        rows.append(row)
    return rows


def table(rows):
    quiet = [r for r in rows if not r["agents"] and not violations(r)]
    rows = [r for r in rows if r not in quiet]
    if not rows:
        return "no sessions with agents or violations in range (%d quiet)" % len(quiet)
    out = ["session   dir                              agents  " + "  ".join(LABELS[k] for k in COUNTERS)]
    totals = dict.fromkeys(COUNTERS, 0)
    for r in rows:
        out.append("%-8s  %-32s %6d  %s" % (r["session"][:8], r["dir"][-32:], r["agents"],
                                            "  ".join(str(r[k]).rjust(len(LABELS[k])) for k in COUNTERS)))
        for k in COUNTERS:
            totals[k] += r[k]
    out.append("TOTAL" + " " * 37 + "%6d  %s" % (sum(r["agents"] for r in rows),
                                                  "  ".join(str(totals[k]).rjust(len(LABELS[k])) for k in COUNTERS)))
    n_filled = sum(r.get("models_filled", 0) for r in rows)
    if n_filled:
        out.append("(%d agent model%s filled by hook: not violations, but the brain forgot model=)"
                   % (n_filled, "" if n_filled == 1 else "s"))
    if quiet:
        out.append("(%d quiet session%s hidden: no agents, no violations)" % (len(quiet), "" if len(quiet) == 1 else "s"))
    return "\n".join(out)
