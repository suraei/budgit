"""`budgit ledger plan` (R-1266): the order of the open items from taskrank, class A, measured p50 and area batches.

ONE outcome function, build(): the text and the --json output both render its dict, nothing is computed twice.
Read-only: it never writes the ledger or a status, and it never re-dates (`ledger due` stays the only door).
  score   N / p50 minutes; N = taskrank parts waiting + bug + unblocks + age (cost and late days are shown, not added)
  p50     median wall minutes doing_since (or started_at) to done_at, same area and appetite, n >= MIN_N; a thinner
          group reads the median of all done items, marked «global estimate»; no history at all: «no estimate»
  order   ONE key, order_key(): pins first, class A before the rest, score, ledger order. A key without the score is refused."""
import re
import statistics
import subprocess
import time

import i18n
import studiolib as sl
import taskrank
from ledgergraph import ITEM_ID, _cited

MIN_N = 5                      # samples per area and appetite before a group has its own p50
MAX_MIN = 24 * 60              # a sample longer than a day is not work time
CLASS_RANK = {"A": 0, "B": 1, "unknown": 1}      # B and unknown never rank above A


def _words(key):
    """The keywords of every language for `key`, read from the i18n catalog (never written here), as one alternation."""
    return "|".join("(?:%s)" % i18n.CATALOG[lang][key] for lang in i18n.LANGS)


class _Lazy:
    """A compiled pattern built from the catalog on first use."""
    def __init__(self, key, shape):
        self.key, self.shape, self.rx = key, shape, None

    def _rx(self):
        if self.rx is None:
            self.rx = re.compile(self.shape % _words(self.key), re.I)
        return self.rx

    def search(self, text):
        return self._rx().search(text)

    def finditer(self, text):
        return self._rx().finditer(text)


BUG_WORDS = _Lazy("ledger.plan_kw_bug", r"\b(?:%s)\b")
FEATURE_WORDS = _Lazy("ledger.plan_kw_feature", r"\b(?:%s)\b")
WAIT_WORDS = _Lazy("ledger.plan_kw_wait", r"\b(?:%s)")
BLOCKER = _Lazy("ledger.plan_kw_block", r"\b(?:%s)\s*:?\s*(R-\d+)")
CLASS_MARK = re.compile(r"\bclass\s*([ab])\b", re.I)         # an item that says «Class A» or «Class B» itself


def _t(lang, key, **kw):
    return i18n.t(key, lang=lang, **kw)


def _minutes(it):
    start = sl._epoch(it.get("doing_since") or it.get("started_at"))
    end = sl._epoch(it.get("done_at"))
    if start is None or end is None:
        return None
    m = (end - start) / 60.0
    return m if 0 <= m <= MAX_MIN else None


def p50(data, area, appetite):
    """-> {minutes, n, mark}: mark "own" (n >= MIN_N), "global" (the median of all done items; n is the own count) or None."""
    done = [it for it in data["items"] if it.get("status") == "done"]
    if area is not None and appetite is not None:
        own = [m for m in (_minutes(it) for it in done if it.get("area") == area and it.get("appetite") == appetite)
               if m is not None]
        if len(own) >= MIN_N:
            return {"minutes": statistics.median(own), "n": len(own), "mark": "own"}
    else:
        own = []
    every = [m for m in (_minutes(it) for it in done) if m is not None]
    if not every:
        return {"minutes": None, "n": len(own), "mark": None} if own else {"minutes": None, "n": 0, "mark": None}
    return {"minutes": statistics.median(every), "n": len(own), "mark": "global"}


def class_info(it):
    """(class, source): THE one class function (R-1333); daily, the plan, the order and taskrank all read it.
    A = a bug or a slow or failing function; B = new function, expansion, idea, docs (waits for the user's yes, never
    above A); unknown = the words give no signal. source is "flagged" when the item says so itself (the flag set where
    it is born, or a `Class A` / `Class B` mark in its title or text, the first mention wins) and "inferred" when only
    its words (title, text, area) say so. Notes never count: they quote other items."""
    flags = it.get("flags") or ()
    if "bug" in flags:                     # the flag is set where the item is born: it wins over the words
        return "A", "flagged"
    if "feature" in flags:
        return "B", "flagged"
    mark = CLASS_MARK.search("%s %s" % (it.get("title") or "", it.get("text") or ""))
    if mark:
        return mark.group(1).upper(), "flagged"
    text = "%s %s %s" % (it.get("title", ""), it.get("text", ""), it.get("area") or "")
    if BUG_WORDS.search(text):
        return "A", "inferred"
    return ("B" if FEATURE_WORDS.search(text) else "unknown"), "inferred"


def item_class(it):
    """The class of an item: A, B or unknown (see class_info for the source)."""
    return class_info(it)[0]


def blockers(it, open_ids):
    """Open items this one waits for: a reference after a blocking word in its words, or any reference in the reason of
    its own (non-user) `blocked` field. A bare citation is an unblocks weight in taskrank, not a blocker."""
    found = []
    block = it.get("blocked")
    if isinstance(block, dict) and block.get("on") != "user":
        found += ["R-" + m.group(1) for m in ITEM_ID.finditer(block.get("reason") or "")]
    cited = _cited(it)
    found += [m.group(1) for m in BLOCKER.finditer("%s %s" % (it.get("title", ""), it.get("text", ""))) if m.group(1) in cited]
    out = []
    for i in found:
        if i in open_ids and i != it["id"] and i not in out:
            out.append(i)
    return out


def order_key(row):
    """The one sort key. It must carry the score: an order by size or id alone is refused."""
    if row.get("score") is None or "idx" not in row:
        raise sl.StudioError(i18n.t("ledger.plan_refuse_key"))
    pos = (row.get("rank") or {}).get("pos")
    if pos:
        return (0, pos, 0, 0.0, row["idx"])
    return (1, 0, CLASS_RANK[row["class"]], -row["score"], row["idx"])


def build(project, data, at=None, lanes=2, probe=None, step0=True, budget_s=30, clock=None, order_res=None):
    at = at if at is not None else time.time()
    items = {it["id"]: it for it in data["items"] if it.get("status") in taskrank.OPEN}
    ranked = taskrank.ranked(data, at)
    nmap = {r["id"]: sum(v for k, v in r["parts"].items() if k != "cost") for r in ranked}
    stay, class_b, blocked_by = [], [], {}
    for r in ranked:
        it = items[r["id"]]
        found = blockers(it, set(items))
        if sl.waits_on_user(it) or (not found and not it.get("blocked") and WAIT_WORDS.search("%s %s" % (it.get("title", ""), it.get("text", "")))):
            class_b.append({"id": it["id"], "title": r["title"], "reason": "waits"})
        elif found:
            blocked_by[it["id"]] = found
        elif it.get("blocked"):
            blocked_by[it["id"]] = []
        else:
            stay.append(r)
    for waiting, found in blocked_by.items():
        for b in found:
            nmap[b] = max(nmap[b], nmap[waiting])
    rows = []
    for idx, r in enumerate(stay):
        it = items[r["id"]]
        cls, source = class_info(it)
        rows.append({"id": it["id"], "status": it["status"], "title": r["title"], "class": cls, "class_source": source, "n": nmap[it["id"]],
                     "parts": r["parts"], "p50": p50(data, it.get("area"), it.get("appetite")), "area": it.get("area"),
                     "appetite": it.get("appetite"), "late": r["due_days"] if r["due_kind"] == "late" else 0,
                     "rank": r["rank"], "idx": idx})
    by_n = all(row["p50"]["minutes"] is not None for row in rows) and bool(rows)
    for row in rows:
        row["score"] = row["n"] / max(row["p50"]["minutes"], 1.0) if by_n else float(row["n"])
    rows.sort(key=order_key)
    import ledgerorder                              # R-1333: the plan shows the one order (head first) and its «porque» line
    res = order_res if order_res is not None else ledgerorder.view(project, data, at=at)      # the dry run passes its own result: the inbox is read once
    pos = {iid: n for n, iid in enumerate(ledgerorder.sequence(res))}
    why = {r["id"]: ledgerorder.reason_line(r) for sec in ("pinned", "head") for r in res[sec]}
    rows.sort(key=lambda row: pos.get(row["id"], len(pos)))             # stable: an id the order does not list keeps its old place
    for row in rows:
        if row["id"] in why:
            row["why"] = why[row["id"]]
    rows, verify, stopped = _step0(project, items, rows, probe or default_probe, step0, budget_s, clock or time.monotonic)
    not_scheduled = [{"id": i, "reason": ("blocked by %s" % ", ".join(f)) if f else "blocked: %s" % sl.oneline(items[i]["blocked"].get("reason", ""), 60),
                      "blocked_by": f, "blocked_reason": sl.oneline((items[i].get("blocked") or {}).get("reason", ""), 60)}
                     for i, f in blocked_by.items()]
    lane_list, overlap = batches(rows, lanes)
    return {"header": {"order_basis": "N/p50" if by_n else "N", "rows": len(rows), "step0_stopped": stopped, "lane_note": None},
            "fixed": [row["id"] for row in rows if row["rank"]], "rows": rows, "lanes": lane_list, "verify_list": verify, "overlap": overlap,
            "class_b": class_b, "not_scheduled": not_scheduled, "step0": bool(step0)}


BATCH_MAX = 6
LANES_DEFAULT = 2


def lanes_from_admit(answer, flag):
    """-> (lanes, refusal reason). `answer` is admit.check(kind="lane", ...), asked once by the CLI layer. A number in
    lane_cap caps the lanes; None means no admit cap, so --lanes, else 2; a verdict other than go plans 0 new lanes."""
    want = flag if flag else LANES_DEFAULT
    if not answer:
        return want, None
    if answer.get("verdict") != "go":
        return 0, answer.get("reason") or answer.get("verdict")
    cap = answer.get("lane_cap")
    return (want if cap is None else min(want, cap)), None


def batches(rows, lanes):
    """Batches by area, 6 rows at most, in order of their best row; a row with no area is a batch of its own (overlap
    unknown: the PM checks before dispatch). Batches of one area share a lane, one after the other. -> (lanes, overlap)."""
    groups, order = {}, []
    for row in rows:
        key = row["area"] if row["area"] is not None else ("?", row["id"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    made = []
    for key in order:
        for i in range(0, len(groups[key]), BATCH_MAX):
            made.append({"area": key if isinstance(key, str) else None, "rows": [r["id"] for r in groups[key][i:i + BATCH_MAX]]})
    for n, b in enumerate(made, 1):
        b["id"] = "B%d" % n
    overlap = [{"batch": b["id"], "area": b["area"], "unknown": b["area"] is None,
                "must_not_run_with": [o["id"] for o in made if o is not b and b["area"] is not None and o["area"] == b["area"]]}
               for b in made]
    out = [{"lane": n, "batches": []} for n in range(1, lanes + 1)]
    for b in made:
        if not out:
            break
        home = next((lane for lane in out if b["area"] is not None and any(x["area"] == b["area"] for x in lane["batches"])), None)
        (home or min(out, key=lambda lane: len(lane["batches"])))["batches"].append(b)
    return out, overlap


TEST_RE = re.compile(r"\btest_[A-Za-z0-9_]+")
PY_RE = re.compile(r"[A-Za-z0-9_./-]+\.py\b")
MAX_TOKEN = 80
MAX_TOKENS = 20


def _good(tok):
    return bool(tok) and not tok.isspace() and not tok.startswith("-") and len(tok) <= MAX_TOKEN and "\n" not in tok and "\0" not in tok


def step0_tokens(item):
    """(tests, symbols) named in the item's words. Dropped: a leading `-`, empty, over 80 characters, a newline or NUL."""
    text = "%s %s" % (item.get("title", ""), item.get("text", ""))
    tests = [t for t in TEST_RE.findall(text) if _good(t)]
    syms = []
    for raw in re.findall(r"`([^`]*)`", text) + PY_RE.findall(text):
        tok = raw.strip()
        tok = tok[:-2] if tok.endswith("()") else tok
        if _good(tok) and not tok.startswith("test_") and not re.match(r"^R-\d+$", tok) and tok not in syms:
            syms.append(tok)
    tests = list(dict.fromkeys(tests))
    return tests[:MAX_TOKENS], syms[:max(0, MAX_TOKENS - len(tests))]


def default_probe(project, item, timeout_s):
    """Cheap check on main: one `git grep` per row, argv only. -> {verdict, missing}; verdict built | partial | none |
    unknown (no tokens, no main ref, git error) | not_run (the budget ran out). `built` needs every named test and a symbol."""
    tests, syms = step0_tokens(item)
    if not tests and not syms:
        return {"verdict": "unknown", "missing": ""}
    argv = ["git", "-C", project, "grep", "-n", "-F", "-I", "-o"]
    for tok in tests + syms:
        argv += ["-e", tok]
    argv += ["main", "--", "plugin/", "tests/"]
    try:
        got = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=max(timeout_s, 0.05))
    except subprocess.TimeoutExpired:
        return {"verdict": "not_run", "missing": ""}
    except OSError:
        return {"verdict": "unknown", "missing": ""}
    if got.returncode not in (0, 1):
        return {"verdict": "unknown", "missing": ""}
    seen = set(line.split(":", 3)[3] for line in got.stdout.splitlines() if line.count(":") >= 3)
    found = lambda tok: any(tok in m for m in seen)         # one -e per token; a match maps back to every token it holds
    tests_ok = [t for t in tests if found(t)]
    syms_ok = [s for s in syms if found(s)]
    if tests and len(tests_ok) == len(tests) and syms_ok:
        return {"verdict": "built", "missing": ""}
    if tests_ok or syms_ok:
        return {"verdict": "partial", "missing": "test" if (not tests or len(tests_ok) < len(tests)) else "symbol"}
    return {"verdict": "none", "missing": ""}


def _step0(project, items, rows, probe, step0, budget_s, clock):
    """Rows that look built go to the verify list (for a verifier; no status changes); the rest keep their lane place,
    marked. R-1273: studiolib.built_unmerged(project, items) is read once when it exists (a set of ids)."""
    fn = getattr(sl, "built_unmerged", None)
    unmerged = set()
    if fn:
        try:
            unmerged = set(fn(project, list(items.values())))
        except Exception:
            unmerged = set()
    verify = [{"id": r["id"], "title": r["title"], "reason": "built_unmerged"} for r in rows if r["id"] in unmerged]
    cand = [r for r in rows if r["id"] not in unmerged]
    if not step0:
        return cand, verify, None
    kept, stopped = [], None
    deadline = clock() + budget_s
    for k, row in enumerate(cand, 1):
        left = deadline - clock()
        got = {"verdict": "not_run", "missing": ""} if left <= 0 else probe(project, items[row["id"]], left)
        if got["verdict"] == "not_run" and stopped is None:
            stopped = {"row": k, "of": len(cand)}
        if got["verdict"] == "built":
            verify.append({"id": row["id"], "title": row["title"], "reason": "built_test"})
        else:
            row["step0"] = got
            kept.append(row)
    return kept, verify, stopped


def _p50_text(p, lang):
    if p["minutes"] is None:
        return _t(lang, "ledger.plan_no_estimate")
    text = "%g min" % round(p["minutes"], 1)
    return text + (" (%s)" % _t(lang, "ledger.plan_global", n=p["n"]) if p["mark"] == "global" else "")


def _mark(row, lang):
    got = row.get("step0") or {}
    verdict = got.get("verdict")
    if verdict == "partial":
        return "  [%s]" % _t(lang, "ledger.plan_mark_partial", what=_t(lang, "ledger.plan_missing_" + got["missing"]))
    if verdict in ("not_run", "unknown"):
        return "  [%s]" % _t(lang, "ledger.plan_mark_not_run")
    return ""


def _row_line(row, lang):
    late = "  late %dd" % row["late"] if row["late"] else ""
    yes = "  [%s]" % _t(lang, "ledger.plan_needs_yes") if row["class"] == "B" else ""
    line = "  %s [%s] %.3f  N %d  p50 %s%s%s%s  %s" % (row["id"], row["class"], row["score"], row["n"], _p50_text(row["p50"], lang), late,
                                                      yes, _mark(row, lang), sl.oneline(row["title"], 60))
    return line + "\n      porque: " + row["why"] if row.get("why") else line          # R-1333: the same line as `ledger top`


def _verify_lines(plan, lang):
    return [_t(lang, "ledger.plan_sec_verify")] + ["  %s  %s  %s" % (v["id"], _t(lang, "ledger.plan_" + v["reason"]), sl.oneline(v["title"], 60))
                                                  for v in plan["verify_list"]]


def _not_why(row, lang):
    if row["blocked_by"]:
        return _t(lang, "ledger.plan_reason_blocked", ids=", ".join(row["blocked_by"]))
    return _t(lang, "ledger.plan_reason_blocked_other", reason=row["blocked_reason"])


def render_verify(plan, lang=None):
    """`--step0-only`: only the list for a verifier."""
    return "\n".join(_verify_lines(plan, lang) if plan["verify_list"] else [_t(lang, "ledger.plan_none_verify")])


def render(plan, lang=None):
    """The text of the plan, from the same dict --json prints. The last line is always the critic line."""
    h = plan["header"]
    out = [_t(lang, "ledger.plan_header", rows=h["rows"], basis=h["order_basis"]), _t(lang, "ledger.plan_built_note")]
    if h.get("lane_note"):
        out.append(_t(lang, "ledger.plan_no_lane", reason=h["lane_note"]))
    if h["step0_stopped"]:
        out.append(_t(lang, "ledger.plan_step0_stopped", k=h["step0_stopped"]["row"], n=h["step0_stopped"]["of"]))
    if not plan["rows"] and not plan["class_b"] and not plan["not_scheduled"] and not plan["verify_list"]:
        out.append(_t(lang, "ledger.plan_empty"))
    if plan["verify_list"]:
        out += _verify_lines(plan, lang)
    pins = [r for r in plan["rows"] if r.get("rank")]
    if plan["fixed"]:
        out += [_t(lang, "ledger.plan_sec_fixed") + ": " + ", ".join(plan["fixed"])]
    if pins:                                         # R-1333: the pins are a short block of their own, each with its «porque» line
        out += [i18n.t("ledger.order_pins", lang=lang, n=len(pins))] + [_row_line(r, lang) for r in pins]
    if plan["rows"]:
        out += [_t(lang, "ledger.plan_sec_order")] + [_row_line(r, lang) for r in plan["rows"] if not r.get("rank")]
    if plan["class_b"]:
        out += [_t(lang, "ledger.plan_sec_classb")] + ["  %s  %s" % (r["id"], _t(lang, "ledger.plan_reason_waits")) for r in plan["class_b"]]
    if plan["lanes"]:
        out.append(_t(lang, "ledger.plan_sec_lanes"))
        for lane in plan["lanes"]:
            out += ["  %s" % _t(lang, "ledger.plan_lane", n=lane["lane"])] + [
                "    %s (%s): %s" % (b["id"], b["area"] or _t(lang, "ledger.plan_no_area"), ", ".join(b["rows"])) for b in lane["batches"]]
    if plan["overlap"]:
        out.append(_t(lang, "ledger.plan_sec_overlap"))
        for o in plan["overlap"]:
            note = _t(lang, "ledger.plan_overlap_check") if o["unknown"] else (
                _t(lang, "ledger.plan_overlap_not", ids=", ".join(o["must_not_run_with"])) if o["must_not_run_with"] else "-")
            out.append("  %s %s: %s" % (o["batch"], o["area"] or "?", note))
    if plan["not_scheduled"]:
        out += [_t(lang, "ledger.plan_sec_notsched")] + ["  %s  %s" % (r["id"], _not_why(r, lang)) for r in plan["not_scheduled"]]
    out.append(_t(lang, "ledger.plan_critic_last"))
    return "\n".join(out)
