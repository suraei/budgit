"""tokens — where does her weekly limit go? Zero-token meter over Claude Code transcripts.

Reads ~/.claude/projects/<dir>/<session>.jsonl (+ <session>/subagents/agent-*.jsonl with .meta.json),
dedupes assistant records by message.id (one API call is logged once per content block) and sums usage.

"weighted" = input-price-equivalent units *within one model* (API price ratios, a proxy for the
subscription limit, which Anthropic does not publish): input 1 · cache write 5m 1.25 / 1h 2 ·
cache read 0.1 · output 5.  https://docs.anthropic.com/en/docs/about-claude/pricing

Usage: python3 tokens.py [--days N] [--top N] [projects_root]
"""
import argparse, glob, json, os, sys, time
from collections import defaultdict

W = {"input": 1.0, "cw5m": 1.25, "cw1h": 2.0, "cache_read": 0.1, "output": 5.0}
FIELDS = ("calls", "input", "cw5m", "cw1h", "cache_read", "output", "weighted")


def _folder_prefix():
    """The transcript-folder prefix of the home's Documents folder, as Claude Code encodes it ("/" becomes "-"),
    built at run time so no account name is written in this file."""
    return os.path.expanduser("~").replace(os.sep, "-") + "-Documents-"


def records(path, seen):
    """Yield (timestamp, model, usage-dict) once per API call; `seen` is shared across files because a
    forked session's transcript repeats its parent's records, which were billed only once."""
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return
    with fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            u, mid = msg.get("usage"), msg.get("id")
            if rec.get("type") != "assistant" or not u or mid in seen:
                continue
            seen.add(mid)
            yield rec.get("timestamp") or "", msg.get("model") or "?", u


def usage_of(path, seen):
    for _, model, u in records(path, seen):
        yield model, u


def add(acc, u):
    cc = u.get("cache_creation") or {}
    cw = u.get("cache_creation_input_tokens") or 0
    cw1h = cc.get("ephemeral_1h_input_tokens") or 0
    cw5m = cc.get("ephemeral_5m_input_tokens", cw - cw1h) or 0
    row = {"input": u.get("input_tokens") or 0, "cw5m": cw5m, "cw1h": cw1h,
           "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0}
    acc["calls"] += 1
    for k, v in row.items():
        acc[k] += v
        acc["weighted"] += v * W[k]


def title_of(path):
    title, first = None, None
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if '"custom-title"' in line:
                try:
                    title = json.loads(line).get("customTitle") or title
                except ValueError:
                    pass
            elif first is None and '"type":"user"' in line.replace(" ", ""):
                try:
                    c = json.loads(line)["message"]["content"]
                    first = c if isinstance(c, str) else next(
                        (b.get("text") for b in c if isinstance(b, dict) and b.get("type") == "text"), None)
                except (ValueError, KeyError, TypeError):
                    pass
    return " ".join((title or first or "?").split())[:60]


def scan(root, days):
    cutoff = time.time() - days * 86400
    sessions, seen = [], set()
    by_agent = defaultdict(lambda: dict.fromkeys(FIELDS, 0))
    for main in sorted(glob.glob(os.path.join(root, "*", "*.jsonl")), key=os.path.getmtime):
        if os.path.getmtime(main) < cutoff:
            continue
        sid = os.path.basename(main)[:-6]
        models = defaultdict(lambda: dict.fromkeys(FIELDS, 0))
        for model, u in usage_of(main, seen):
            add(models[model], u)
        sub = dict.fromkeys(FIELDS, 0)
        for ap in glob.glob(os.path.join(os.path.dirname(main), sid, "subagents", "agent-*.jsonl")):
            try:
                kind = json.load(open(ap[:-6] + ".meta.json")).get("agentType", "?")
            except (OSError, ValueError):
                kind = "?"
            for model, u in usage_of(ap, seen):
                add(by_agent["%s (%s)" % (kind, model.replace("claude-", ""))], u)
                add(sub, u)
        if not models and not sub["calls"]:
            continue
        sessions.append({"project": os.path.basename(os.path.dirname(main)).replace(_folder_prefix(), ""),
                         "title": title_of(main), "models": dict(models), "subagents": sub,
                         "weighted": sum(m["weighted"] for m in models.values()) + sub["weighted"]})
    return sessions, dict(by_agent)


def by_day(root, days, who=None):
    """Spend per UTC day from record timestamps (a session spans days). `who`, if given, is filled with
    {day: {session title: weighted}} so the biggest spenders of each day can be named."""
    cutoff = time.time() - days * 86400
    seen, out = set(), defaultdict(lambda: dict.fromkeys(FIELDS, 0))
    files = glob.glob(os.path.join(root, "*", "*.jsonl")) + glob.glob(os.path.join(root, "*", "*", "subagents", "agent-*.jsonl"))
    for path in sorted(files, key=os.path.getmtime):
        if os.path.getmtime(path) < cutoff:
            continue
        main = path if "/subagents/" not in path else os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(path))), os.path.basename(os.path.dirname(os.path.dirname(path))) + ".jsonl")
        label = None
        for ts, model, u in records(path, seen):
            day = ts[:10] or "?"
            before = out[day]["weighted"]
            add(out[day], u)
            if who is not None:
                label = label or title_of(main) if os.path.isfile(main) else (label or "?")
                who.setdefault(day, defaultdict(float))[label] += out[day]["weighted"] - before
    return dict(out)


def top_agents(root, days, limit=15):
    """Individual subagents ranked by spend: one runaway agent (hundreds of calls at a growing
    context) costs more than many small ones."""
    cutoff, seen, rows, titles = time.time() - days * 86400, set(), [], {}
    for path in glob.glob(os.path.join(root, "*", "*", "subagents", "agent-*.jsonl")):
        if os.path.getmtime(path) < cutoff:
            continue
        sess = os.path.dirname(os.path.dirname(path))
        main = sess + ".jsonl"
        acc = dict.fromkeys(FIELDS, 0)
        for _, _, u in records(path, seen):
            add(acc, u)
        if not acc["calls"]:
            continue
        try:
            meta = json.load(open(path[:-6] + ".meta.json"))
        except (OSError, ValueError):
            meta = {}
        if main not in titles:
            titles[main] = title_of(main) if os.path.isfile(main) else "?"
        rows.append({"kind": meta.get("agentType", "?"), "what": (meta.get("description") or "")[:60],
                     "session": titles[main], "calls": acc["calls"], "weighted": acc["weighted"],
                     "avg_ctx": (acc["cache_read"] + acc["cw1h"] + acc["cw5m"] + acc["input"]) / acc["calls"],
                     "output": acc["output"]})
    return sorted(rows, key=lambda r: -r["weighted"])[:limit]


def fmt(n):
    return "%.1fM" % (n / 1e6) if n >= 1e6 else "%.0fk" % (n / 1e3) if n >= 1e3 else "%d" % n


def report(sessions, by_agent, top):
    out = []
    total = sum(s["weighted"] for s in sessions) or 1
    out.append("| session | project | weighted | share | main calls | avg context/call | subagents | output |")
    out.append("|---|---|---|---|---|---|---|---|")
    for s in sorted(sessions, key=lambda s: -s["weighted"])[:top]:
        calls = sum(m["calls"] for m in s["models"].values())
        ctx = sum(m["cache_read"] + m["cw5m"] + m["cw1h"] + m["input"] for m in s["models"].values())
        outp = sum(m["output"] for m in s["models"].values())
        out.append("| %s | %s | %s | %d%% | %d | %s | %s | %s |" % (
            s["title"].replace("|", "/"), s["project"], fmt(s["weighted"]), 100 * s["weighted"] / total,
            calls, fmt(ctx / max(calls, 1)), fmt(s["subagents"]["weighted"]), fmt(outp)))
    out.append("\n| subagent type (model) | calls | weighted | output |")
    out.append("|---|---|---|---|")
    for k, a in sorted(by_agent.items(), key=lambda x: -x[1]["weighted"])[:top]:
        out.append("| %s | %d | %s | %s |" % (k, a["calls"], fmt(a["weighted"]), fmt(a["output"])))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--agents", action="store_true", help="most expensive individual subagents")
    ap.add_argument("--by-day", action="store_true", help="spend per day (record timestamps) + top sessions")
    a = ap.parse_args(argv)
    if a.agents:
        for r in top_agents(a.root, a.days, a.top):
            print("%7s  %4d calls  avg ctx %5s  out %5s  %s | %s | %s" % (
                fmt(r["weighted"]), r["calls"], fmt(r["avg_ctx"]), fmt(r["output"]), r["kind"], r["what"], r["session"][:28]))
        return 0
    if a.by_day:
        who = {}
        days = by_day(a.root, a.days, who)
        for day in sorted(days):
            d = days[day]
            top = sorted(who.get(day, {}).items(), key=lambda x: -x[1])[:3]
            print("%s  weighted %7s  calls %6d  output %6s  | %s" % (
                day, fmt(d["weighted"]), d["calls"], fmt(d["output"]),
                "; ".join("%s %s" % (t[:34], fmt(w)) for t, w in top)))
        return 0
    sessions, by_agent = scan(a.root, a.days)
    print(report(sessions, by_agent, a.top))


if __name__ == "__main__":
    sys.exit(main())
