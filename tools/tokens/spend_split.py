"""spend_split — where does the spend go, in the four ways R-274 asked about. Zero-token meter over Claude Code transcripts.

  components   input / cache write / cache read / output, in tokens.py's input-price-equivalent units
  context      share of spend by context size per call (the cost of a long chat that re-reads everything)
  answers      text-only answers vs tool calls, and how many text answers are long (output costs 5x input)
  pdf          sessions that read a PDF and the share of spend in them (an upper bound: not every call carries the pages)

Same weights and the same dedupe-by-message-id as tokens.py; one API call is logged once per content block, so the output count
takes the largest value seen for the id. Read-only.

Usage: python3 spend_split.py [--days N] [--project SUBSTRING] [projects_root]
"""
import argparse
import glob
import json
import os
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokens import W  # noqa: E402

BUCKETS = (("<=100k", 100_000), ("100-200k", 200_000), ("200-400k", 400_000), (">400k", float("inf")))
LONG_ANSWER = 1000
CAP_TOKENS = 200_000      # what-if: compaction keeps every call under this context


def _usage_row(u):
    cc = u.get("cache_creation") or {}
    cw = u.get("cache_creation_input_tokens") or 0
    cw1h = cc.get("ephemeral_1h_input_tokens") or 0
    cw5m = cc.get("ephemeral_5m_input_tokens", cw - cw1h) or 0
    return {"input": u.get("input_tokens") or 0, "cw5m": cw5m, "cw1h": cw1h,
            "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0}


def _is_pdf_path(p):
    return isinstance(p, str) and p.lower().endswith(".pdf")


def _read_file(path, seen):
    """-> (calls [{row, tools}], pdf_reads). A call already in `seen` (a fork repeats its parent's records) is skipped."""
    calls, pdf_ids, pdf_docs = {}, set(), 0
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return [], 0
    with fh:
        for line in fh:
            low = line.lower()
            if '"usage"' not in line and '.pdf' not in low and 'application/pdf' not in low and '"document"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            blocks = content if isinstance(content, list) else []
            if rec.get("type") == "user":
                pdf_docs += sum(1 for b in blocks if isinstance(b, dict) and (b.get("type") == "document" or (b.get("source") or {}).get("media_type") == "application/pdf"))
                continue
            if rec.get("type") != "assistant":
                continue
            has_tool = False
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    has_tool = True
                    if _is_pdf_path((b.get("input") or {}).get("file_path")):
                        pdf_ids.add(b.get("id") or id(b))
            u, mid = msg.get("usage"), msg.get("id")
            if not u or mid is None or mid in seen:
                continue
            c = calls.setdefault(mid, {"row": _usage_row(u), "tools": False})
            c["tools"] = c["tools"] or has_tool
            c["row"]["output"] = max(c["row"]["output"], _usage_row(u)["output"])
    seen.update(calls)
    return list(calls.values()), len(pdf_ids) + pdf_docs


def _weighted(row):
    return sum(row[k] * W[k] for k in W)


def split(root, days=7, project=None):
    cutoff = time.time() - days * 86400
    seen = set()
    comps = defaultdict(float)
    ctx = {label: {"calls": 0, "weighted": 0.0} for label, _ in BUCKETS}
    text_out, tool_calls, total, sub_total, calls_n = [], 0, 0.0, 0.0, 0
    long_text_w, pdf_sessions, pdf_reads, pdf_w, cap_saved = 0, 0, 0, 0.0, 0.0
    for main in sorted(glob.glob(os.path.join(root, "*", "*.jsonl")), key=os.path.getmtime):
        if os.path.getmtime(main) < cutoff or (project and project not in os.path.basename(os.path.dirname(main))):
            continue
        sid = main[:-6]
        files = [(main, False)] + [(p, True) for p in sorted(glob.glob(os.path.join(sid, "subagents", "agent-*.jsonl")))]
        session_w, session_pdf = 0.0, 0
        for path, is_sub in files:
            calls, pdfs = _read_file(path, seen)
            session_pdf += pdfs
            for c in calls:
                row, w = c["row"], _weighted(c["row"])
                calls_n += 1
                total += w
                session_w += w
                sub_total += w if is_sub else 0
                for k in W:
                    comps[{"cw5m": "cache_write", "cw1h": "cache_write"}.get(k, k)] += row[k] * W[k]
                size = row["input"] + row["cw5m"] + row["cw1h"] + row["cache_read"]
                label = next(l for l, top in BUCKETS if size <= top)
                cap_saved += max(0, size - CAP_TOKENS) * W["cache_read"]        # the excess is re-read context
                ctx[label]["calls"] += 1
                ctx[label]["weighted"] += w
                if c["tools"]:
                    tool_calls += 1
                else:
                    text_out.append(row["output"])
                    if row["output"] >= LONG_ANSWER:
                        long_text_w += row["output"] * W["output"]
        if session_pdf:
            pdf_sessions += 1
            pdf_reads += session_pdf
            pdf_w += session_w
    share = lambda x: (x / total) if total else 0.0
    return {
        "calls": calls_n, "weighted": total, "components": dict(comps),
        "subagent_share": share(sub_total), "cap_saving": share(cap_saved),
        "context": [{"label": l, "calls": ctx[l]["calls"], "share": share(ctx[l]["weighted"])} for l, _ in BUCKETS],
        "answers": {"text_calls": len(text_out), "tool_calls": tool_calls,
                    "text_median": statistics.median(text_out) if text_out else 0,
                    "text_p90": sorted(text_out)[int(0.9 * (len(text_out) - 1))] if text_out else 0,
                    "long_text_calls": sum(1 for o in text_out if o >= LONG_ANSWER), "long_text_share": share(long_text_w)},
        "pdf": {"sessions": pdf_sessions, "reads": pdf_reads, "share": share(pdf_w)},
    }


def _m(n):
    return "%.1fM" % (n / 1e6) if n >= 1e6 else "%.0fk" % (n / 1e3)


def render(r):
    out = ["%d calls, %s weighted units (subagents %.0f%%)" % (r["calls"], _m(r["weighted"]), 100 * r["subagent_share"])]
    if not r["calls"]:
        return "\n".join(out)
    out += ["", "| component | weighted | share |", "|---|---|---|"]
    for k in ("input", "cache_write", "cache_read", "output"):
        out.append("| %s | %s | %.1f%% |" % (k, _m(r["components"].get(k, 0)), 100 * r["components"].get(k, 0) / r["weighted"]))
    out += ["", "| context per call | calls | share of spend |", "|---|---|---|"]
    out += ["| %s | %d | %.1f%% |" % (b["label"], b["calls"], 100 * b["share"]) for b in r["context"]]
    out.append("what-if every call stayed under %dk of context (compaction at the gates): up to %.1f%% less spend" % (CAP_TOKENS // 1000, 100 * r["cap_saving"]))
    a = r["answers"]
    out += ["", "answers: %d text-only calls (median %d, p90 %d output tokens), %d tool calls; %d text answers >= %d tokens = %.1f%% of spend"
            % (a["text_calls"], a["text_median"], a["text_p90"], a["tool_calls"], a["long_text_calls"], LONG_ANSWER, 100 * a["long_text_share"])]
    p = r["pdf"]
    out.append("pdf: %d sessions read or attached a PDF (%d references); they hold %.1f%% of spend, the whole session, not the PDFs' share" % (p["sessions"], p["reads"], 100 * p["share"]))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--project", help="only project folders whose name contains this text")
    args = ap.parse_args(argv)
    print(render(split(args.root, args.days, args.project)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
