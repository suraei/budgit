"""Which task comes next: a score per open or doing item, and the PM's pins on top of it.

Every weight is in WEIGHTS. An item's score is the sum of its parts:
  waiting   it waits on the user (`blocked` with on: user, the one source of that state)
  bug       a bug the user sees (flag `bug`)
  unblocks  per other open item that names it in its words (at most UNBLOCKS_CAP)
  age       per whole week since it was created (at most AGE_CAP_WEEKS)
  cost      per day of its appetite (no appetite: no cost)
Ties keep ledger order. A pinned item (`rank` {pos, reason, at, by}) takes its position whatever its score."""
import time

import studiolib as sl
from ledgergraph import _cited

WEIGHTS = {"waiting": 100, "bug": 60, "unblocks": 20, "age": 2, "cost": -5}
UNBLOCKS_CAP = 5
AGE_CAP_WEEKS = 26
TOP_N = 10
OPEN = ("open", "doing")


def _weeks(item, at):
    created = sl._epoch(item.get("created"))
    if created is None:
        return 0
    return min(max(0, int((at - created) // 86400)) // 7, AGE_CAP_WEEKS)


def waits_on_user(item):
    return sl.waits_on_user(item)


def _parts(item, cited_by, at):
    flags = item.get("flags") or ()
    days = sl.appetite_days(item) or 0
    return {"waiting": WEIGHTS["waiting"] if waits_on_user(item) else 0,
            "bug": WEIGHTS["bug"] if "bug" in flags else 0,
            "unblocks": min(cited_by.get(item["id"], 0), UNBLOCKS_CAP) * WEIGHTS["unblocks"],
            "age": _weeks(item, at) * WEIGHTS["age"],
            "cost": days * WEIGHTS["cost"]}


def ranked(data, at=None):
    """The live items (statuses in OPEN), best first, as rows {id, status, title, score, parts, rank}."""
    at = at if at is not None else time.time()
    items = [it for it in data["items"] if it.get("status") in OPEN]
    cited_by = {}
    for it in items:
        for ref in _cited(it):
            cited_by[ref] = cited_by.get(ref, 0) + 1
    rows = []
    for n, it in enumerate(items):
        parts = _parts(it, cited_by, at)
        rows.append({"id": it["id"], "status": it["status"], "title": it.get("title") or sl.oneline(it["text"], 60),
                     "score": sum(parts.values()), "parts": parts, "rank": it.get("rank"), "_n": n})
    out = sorted((r for r in rows if not r["rank"]), key=lambda r: (-r["score"], r["_n"]))
    for r in sorted((r for r in rows if r["rank"]), key=lambda r: (r["rank"]["pos"], r["_n"])):
        out.insert(min(r["rank"]["pos"] - 1, len(out)), r)
    for r in out:
        del r["_n"]
    return out


def top(data, n=TOP_N, at=None):
    return ranked(data, at)[:n]


def _open_item(data, item_id):
    item = sl.find_item(data, item_id)
    if item["status"] not in OPEN:
        raise sl.StudioError("%s is %s; only an open or doing item is ranked" % (item_id, item["status"]))
    return item


def pin(data, item_id, pos, reason, by=None):
    """`ledger rank R-n <pos> --reason`: the PM's override, kept with its reason."""
    item = _open_item(data, item_id)
    reason = (reason or "").strip()
    if not reason:
        raise sl.StudioError("rank needs --reason: why this item goes to that position")
    if not isinstance(pos, int) or isinstance(pos, bool) or pos < 1:
        raise sl.StudioError("the position starts at 1")
    item["rank"] = {"pos": pos, "reason": reason, "at": sl.now(), "by": by}
    item["updated"] = sl.now()
    return item


def unpin(data, item_id):
    item = sl.find_item(data, item_id)
    if item.pop("rank", None) is not None:
        item["updated"] = sl.now()
    return item


def flag(data, item_id, name, on=True):
    if name not in sl.FLAGS:
        raise sl.StudioError("unknown flag %r (use %s)" % (name, ", ".join(sl.FLAGS)))
    item = sl.find_item(data, item_id)
    flags = [f for f in item.get("flags") or () if f != name] + ([name] if on else [])
    if flags:
        item["flags"] = [f for f in sl.FLAGS if f in flags]
    else:
        item.pop("flags", None)
    item["updated"] = sl.now()
    return item


def _why(row):
    p = row["parts"]
    words = [k for k in ("waiting", "bug") if p[k]]
    if p["unblocks"]:
        words.append("unblocks %d" % (p["unblocks"] // WEIGHTS["unblocks"] if WEIGHTS["unblocks"] else 0))
    if p["age"]:
        words.append("%d weeks old" % (p["age"] // WEIGHTS["age"] if WEIGHTS["age"] else 0))
    if p["cost"]:
        words.append("cost %g days" % (p["cost"] / WEIGHTS["cost"] if WEIGHTS["cost"] else 0))
    return ", ".join(words)


def lines(rows):
    out = []
    for n, r in enumerate(rows, 1):
        why = ("pinned: %s" % r["rank"]["reason"]) if r["rank"] else _why(r)
        out.append("%3d. %s [%s] %g  %s%s" % (n, r["id"], r["status"], r["score"], r["title"],
                                              " — " + why if why else ""))
    return out
