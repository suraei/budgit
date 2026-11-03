"""R-415: `budgit daily [target] [--all] [--json]`, the morning read of a ledger: the flow count, batches of items that belong
together, probable duplicates, the order to take the next version in, and a preview of what housekeeping would clean.

It decides nothing and changes no ledger: it lists. The only file it writes is its own cache in the Budgit home
(`daily.json`: the day, and per repo a hash with two integers), read back by the one SessionStart line.

Privacy (the gate of R-415): a protected project is read through its card counts only (its ledger is never opened) and shows
under its `private~hash` id with the words "flow, batches and duplicates not computed". Every free-text fragment of a plain
project (label, item id, area, file name) goes through leak.hide() with the names of all protected projects, and the rendered
text / JSON is scrubbed once more. An error prints a code, never a message that could carry a ledger's text.
"""
import difflib
import hashlib
import json
import os
import re
import time

import card
import housekeeping
import leak
import livework
import portfolio
import studiolib as sl

DUP_RATIO = 0.8                 # titles this alike (after folding case and accents) are a probable duplicate
BATCH_MIN_WORDS = 2             # meaningful words two items must share to belong together (a shared file name is enough alone)
BATCH_MAX = 8                   # a batch is a sitting's work: a larger cluster is split, strongest links first
GIT_BUDGET_S = 6                # one deadline for every git call of the cleaning preview
SESSION_BUDGET_MS = 100         # the SessionStart line gives up (and stays silent) when the facts take longer
DUP_PAIR_CAP = 4000             # candidate pairs judged at most; the rest of a huge ledger is left for the next run
SESSION_DUP_CAP = 800           # the SessionStart line judges fewer pairs so a large ledger still fits its 100 ms
SHOW_ORDER = 8                  # lines of the order the text report prints (the JSON has all of them)
CACHE = "daily.json"

CLASS_MARK = re.compile(r"\bclass\s*([ab])\b", re.I)
CLASS_A_WORDS = re.compile(r"\b(?:bugs?|broken|crash\w*|slow|performance|roto|lento|no funciona)\b", re.I)
FILE = re.compile(r"[\w./-]{0,200}[\w-]\.(?:py|js|ts|tsx|json|md|html|css|sh|txt|yml|yaml|toml|cfg|ini)\b", re.I)
EXPLICIT = re.compile(r"\b(?:duplicate of|same as|covered by|superseded by)\s+(R-\d+)\b", re.I)
CLASS_RANK = {"A": 0, "A?": 1, "B": 2}


def _num(item_id):
    m = re.search(r"\d+", str(item_id))
    return (int(m.group()) if m else 0, str(item_id))


def _text(item):
    return " ".join(str(item.get(k) or "") for k in ("title", "text"))


# ---------------------------------------------------------------- the pure parts
def classify(item):
    """"A" or "B" when the item says `Class A` / `Class B` itself (the first mention wins), "A?" when its title or text
    looks like a bug, a crash or a slow thing, else "B". Notes never count: they quote other items."""
    text = _text(item)
    mark = CLASS_MARK.search(text)
    if mark:
        return mark.group(1).upper()
    return "A?" if CLASS_A_WORDS.search(text) else "B"


FILE_SCAN = 20000                  # characters of an item looked at for file names


def _files(item):
    return {os.path.basename(m.group()).lower() for m in FILE.finditer(_text(item)[:FILE_SCAN])}


def _cutoff(n):
    """A word in more items than this names no group: it is the project's own vocabulary."""
    return max(6, min(n // 5, 25))


def _pairs_by_key(keyed, limit):
    """{(i, j): set of shared keys} over keys held by 2..limit items. keyed: [set of keys] per item index."""
    posting = {}
    for n, keys in enumerate(keyed):
        for key in keys:
            posting.setdefault(key, []).append(n)
    out = {}
    for key, rows in posting.items():
        if 2 <= len(rows) <= limit:
            for a in range(len(rows)):
                for b in range(a + 1, len(rows)):
                    out.setdefault((rows[a], rows[b]), set()).add(key)
    return out


# words of a title that say nothing about the area (en, es): never offered as a word of a group
AREA_FILLER = frozenset("""item items both rule rules auto thing things task tasks each same next need needs make makes
elemento elementos tarea tareas regla reglas ambos ambas cosa cosas""".split())


def _stem_words(name):
    return set(re.findall(r"[a-z0-9]+", os.path.splitext(name)[0].casefold()))


def batches(items, avoid=()):
    """Groups of 2 to BATCH_MAX items that share a file name (basename only) or BATCH_MIN_WORDS meaningful words.
    -> [{"ids": [...], "words": [...], "files": [...]}], the group with the lowest id first. The strongest links join
    first and a group stops growing at BATCH_MAX, so a vocabulary shared along a chain never makes one huge batch.
    `avoid`: words that are part of a protected name are never offered as a word of a group, and a file name that holds one
    (`qzvk.py` for a protected `qzvk-portal`) is not offered either."""
    items = list(items)
    avoid = set(avoid)
    limit = _cutoff(len(items))
    words = [{w for w in sl._tokens(_text(i)) if w not in avoid and w not in AREA_FILLER and not w.isdigit()} for i in items]
    files = [{f for f in _files(i) if not _stem_words(f) & avoid} for i in items]
    by_word = _pairs_by_key(words, limit)
    by_file = _pairs_by_key(files, limit)
    links = [(len(by_file.get(pair, ())) * 100 + len(by_word.get(pair, ())), pair) for pair in set(by_word) | set(by_file)
             if pair in by_file or len(by_word[pair]) >= BATCH_MIN_WORDS]
    links.sort(key=lambda x: (-x[0], x[1]))
    parent, size = list(range(len(items))), [1] * len(items)

    def root(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for _strength, (a, b) in links:
        ra, rb = root(a), root(b)
        if ra != rb and size[ra] + size[rb] <= BATCH_MAX:
            parent[rb] = ra
            size[ra] += size[rb]
    groups = {}
    for n in range(len(items)):
        groups.setdefault(root(n), []).append(n)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        count = {}
        for key_sets in (words, files):
            for n in members:
                for key in key_sets[n]:
                    count[key] = count.get(key, 0) + 1
        usable = lambda key: count[key] >= 2
        df = {}
        for n in range(len(items)):
            for w in words[n]:
                df[w] = df.get(w, 0) + 1
        shared_words = sorted((w for n in members for w in words[n] if usable(w) and df[w] <= limit), key=lambda w: (-count[w], w))
        shared_files = sorted({f for n in members for f in files[n] if usable(f)})
        out.append({"ids": sorted((items[n]["id"] for n in members), key=_num),
                    "words": list(dict.fromkeys(shared_words))[:4], "files": shared_files[:3]})
    out.sort(key=lambda b: _num(b["ids"][0]))
    return out


def _folded_title(item):
    import triggers
    return triggers._fold(sl.item_title(item))


def duplicates(open_items, all_items=None, deadline=None, cap=None):
    """Probable duplicate pairs among the open items: titles at least DUP_RATIO alike once case and accents are folded, or an
    item that says `duplicate of / same as / covered by / superseded by R-n` for an R-n that exists and is open (status open
    or doing; a parked `later` item is open, so it can be the target).
    -> [(id, id)], lowest first. Nothing is dropped or merged here."""
    open_items = list(open_items)
    everything = list(all_items) if all_items is not None else open_items
    live = {i["id"] for i in everything if i.get("status") in ("open", "doing")}
    found = set()
    for it in open_items:
        for m in EXPLICIT.finditer(_text(it)):
            target = m.group(1).upper()
            if target in live and target != it["id"]:
                found.add(tuple(sorted((it["id"], target), key=_num)))
    titles = [_folded_title(i) for i in open_items]
    keyed = [sl._tokens(t) for t in titles]
    cand = sorted(_pairs_by_key(keyed, _cutoff(len(open_items))))
    for done, (a, b) in enumerate(cand[:DUP_PAIR_CAP if cap is None else cap]):
        if deadline is not None and done % 64 == 0 and time.monotonic() > deadline:
            break
        m = difflib.SequenceMatcher(None, titles[a], titles[b])
        if m.real_quick_ratio() >= DUP_RATIO and m.quick_ratio() >= DUP_RATIO and m.ratio() >= DUP_RATIO:
            found.add(tuple(sorted((open_items[a]["id"], open_items[b]["id"]), key=_num)))
    return sorted(found, key=lambda p: (_num(p[0]), _num(p[1])))


def _scope(items):
    """-> (pending, committed, next version or None, the items of the next version, or every committed item without one)."""
    pending = [i for i in items if i.get("status") in ("open", "doing")]
    committed = [i for i in pending if not (i.get("status") == "open" and i.get("phase") == "later")]
    versions = sorted({str(i.get("phase")) for i in pending if housekeeping.VERSION.fullmatch(str(i.get("phase")))},
                      key=lambda v: [int(x) for x in v.lstrip("v").split(".")])
    nxt = versions[0] if versions else None
    scope = [i for i in pending if str(i.get("phase")) == nxt] if nxt else committed
    return pending, committed, nxt, scope


# ---------------------------------------------------------------- the repo key and the cache
def repo_key(project):
    """A 16-hex hash of the repo's common git dir (the same for every worktree of it), read from the `.git` files with no
    subprocess; a folder with no git is keyed by its own path. Only the hash is ever stored."""
    path = os.path.realpath(project)
    probe, git_dir = path, None
    while True:
        dot = os.path.join(probe, ".git")
        if os.path.exists(dot):
            git_dir = dot
            break
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    base = path
    if git_dir:
        base = git_dir
        try:
            if os.path.isfile(git_dir):
                with open(git_dir, encoding="utf-8", errors="replace") as f:
                    line = f.read().strip()
                if line.startswith("gitdir:"):
                    base = os.path.join(os.path.dirname(git_dir), line[len("gitdir:"):].strip())
                    try:
                        with open(os.path.join(base, "commondir"), encoding="utf-8") as f:
                            base = os.path.join(base, f.read().strip())
                    except OSError:
                        pass
        except OSError:
            pass
        base = os.path.realpath(base)
    return hashlib.sha256(base.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def cache_path():
    return os.path.join(sl.home_state(), CACHE)


def _read_cache():
    try:
        with open(cache_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cached_clean(project, now):
    """The to-clean count a `budgit daily` run left today for this repo, or None (another day, never run, odd file)."""
    data = _read_cache()
    row = (data.get("clean") or {}).get(repo_key(project)) if isinstance(data.get("clean"), dict) else None
    if data.get("day") != time.strftime("%Y-%m-%d", time.localtime(now)) or not isinstance(row, dict):
        return None
    ok = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return row["remove"] + row["delete"] if ok(row.get("remove")) and ok(row.get("delete")) else None


def _write_cache(day, found):
    """found: {repo key: {"remove": n, "delete": m}}. Merged into today's file under the lock; another day's file starts over."""
    if not found:
        return
    try:
        with sl.home_lock("daily"):
            data = _read_cache()
            clean = data.get("clean") if data.get("day") == day and isinstance(data.get("clean"), dict) else {}
            clean.update(found)
            os.makedirs(os.path.dirname(cache_path()), exist_ok=True)
            sl.atomic_write(cache_path(), json.dumps({"day": day, "clean": clean}, indent=1, sort_keys=True) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- the cleaning preview
class _Clean:
    """What housekeeping would remove or delete (the same plan() and the same live-session rule as `budgit housekeeping`),
    counted behind one deadline. Every doubt is "not checked", never 0."""

    def __init__(self, enabled, load, now):
        self.enabled, self.now = enabled, now
        self.load = housekeeping.load_now() if load is None else load
        self.deadline = None
        self.live = False
        self.before = None
        self.config = None

    def __enter__(self):
        if self.enabled:
            self.deadline = time.monotonic() + GIT_BUDGET_S
            self.before, housekeeping._deadline = housekeeping._deadline, self.deadline
        return self

    def __exit__(self, *exc):
        if self.enabled:
            housekeeping._deadline = self.before

    def count(self, path):
        if not self.enabled or (self.load is not None and self.load > housekeeping._load_limit()):
            return None
        try:
            if not os.path.exists(os.path.join(path, ".git")) or housekeeping.top_level(path) != os.path.realpath(path):
                return None
            if self.live is False:
                self.live = housekeeping.live_cwds()
                self.config = sl.load_config()
            if self.live is None:
                return None                                       # the live sessions could not be read: unknown, not "nothing to tidy"
            plan = housekeeping.plan(path, self.now, live=self.live, config=self.config)
        except Exception:
            return None
        if plan.get("skip") or time.monotonic() > self.deadline:
            return None
        return {"remove": len(plan["remove"]), "delete": len(plan["delete"])}


# ---------------------------------------------------------------- the report
NO_COUNTS = ("made", "shut", "pending", "next", "later")


def _empty_row(label, protected):
    """A row with nothing known yet: every count is None (unknown), never 0."""
    return {"id": label, "protected": protected, "counts": {k: None for k in NO_COUNTS},
            "rule": False, "batches": [], "duplicates": [], "order": [], "clean": "not checked"}


def _row(path, label, is_priv, names, avoid, now, clean, prefixed):
    """One project's row. A protected one is counts from its card; the ledger is not opened. A plain one is hidden()
    in every free-text field."""
    row = _empty_row(label, is_priv)
    if is_priv:
        if not livework.has_ledger(path):
            row["error"] = "unreadable"
            return row
        counts = (card.read(path) or {}).get("counts")          # the card's own counts; a missing one is "no data", never 0
        if isinstance(counts, dict) and all(isinstance(counts.get(k), int) and not isinstance(counts.get(k), bool)
                                            for k in ("doing", "open", "later")):
            nxt = counts["open"] - counts["later"]
            row["counts"] = {"made": None, "shut": None, "pending": counts["doing"] + nxt + counts["later"],
                             "next": nxt, "later": counts["later"]}
        return row
    try:
        items = sl.load_ledger(path)["items"]
        f = housekeeping.flow(path, now)
    except Exception:
        row["error"] = "unreadable"
        return row
    pending, _committed, nxt, scope = _scope(items)
    later = sum(1 for i in pending if i.get("status") == "open" and i.get("phase") == "later")
    row["counts"] = {"made": f["made"], "shut": f["shut"], "pending": f["pending"], "next": len(scope), "later": later}
    row["rule"] = f["made"] > f["shut"]
    row["_next"] = nxt
    prefix = label + "/" if prefixed else ""
    # words and file names come from the text with the protected names already hidden, so a piece of a name is never an area
    shown_scope = [dict(i, title=leak.hide(i.get("title"), names) if i.get("title") else None, text=leak.hide(i.get("text"), names))
                   for i in scope]
    rename = lambda i: leak.hide(prefix + i, names)
    row["batches"] = [{"ids": [rename(i) for i in b["ids"]],
                       "area": None if prefixed else leak.hide(" ".join(b["files"] or b["words"][:3]), names) or None}
                      for b in batches(shown_scope, avoid | {"hidden"})]
    committed_open = [i for i in pending if not (i.get("status") == "open" and i.get("phase") == "later")]
    row["duplicates"] = [[rename(a), rename(b)] for a, b in duplicates(committed_open, all_items=items)]
    ranked = sorted(scope, key=lambda i: CLASS_RANK[classify(i)])           # stable: ledger order inside a class
    row["order"] = [{"id": rename(i["id"]), "class": classify(i)} for i in ranked]
    found = clean.count(path)
    if found is not None:
        row["clean"] = found
        row["_key"] = repo_key(path)
    return row


def _config_ok():
    """False when a config file is there but cannot be read, or `protected_dirs` holds an entry that cannot be resolved: the
    protected folders are then unknown, so no ledger is opened (as in general.py)."""
    return bool(sl.config_readable() and sl.protected_dirs_readable())


def _avoid(names):
    """The words that are parts of protected names: an area never offers one of them as a word of its own."""
    parts = set()
    for name in names:
        parts |= sl._tokens(name)
        parts |= {w for w in re.findall(r"[a-z0-9]+", leak._fold_text(name)[0]) if len(w) >= 2}
    return parts


def collect(project=None, all_projects=False, target=None, now=None, git=True, load=None):
    """The daily as data. `project`: the current folder; `target`: a project of the portfolio list by name; `all_projects`:
    every project of the list. A protected project is counts only; a failing one carries an `error` code."""
    now = now if now is not None else time.time()
    if not _config_ok():                               # `protected_dirs` cannot be read: no folder can be called plain
        return {"projects": [], "no_project": False, "error": "config", "_names": frozenset()}
    names = leak.private_names()
    avoid = _avoid(names)
    data = portfolio.load() or portfolio.seed(portfolio._found(livework.live_sessions()["sessions"]))
    entries = list(data["projects"])
    private = {e["path"] for e in entries if leak.is_protected(e["path"])}
    ids = portfolio._ids(entries, private)
    if all_projects:
        chosen = entries
    else:
        if target:
            try:
                entry = portfolio.find(data, target)
            except sl.StudioError:
                entry = None
            real = entry["path"] if entry else None
        else:
            real = livework.canon(project) if project else None
        chosen = [e for e in entries if e["path"] == real] or ([{"path": real}] if real else [])
        if chosen and chosen[0]["path"] not in ids:
            if leak.is_protected(real):
                private.add(real)
                ids[real] = portfolio.PRIVATE + "~" + livework._short_id(real)
            else:
                ids[real] = os.path.basename(real.rstrip(os.sep))
    rows, found = [], {}
    with _Clean(git, load, now) as clean:
        for e in chosen:
            path = e["path"]
            is_priv = path in private
            label = ids.get(path) or portfolio.PRIVATE
            if not is_priv:
                label = leak.hide(label, names)
            try:
                if not os.path.isdir(path):
                    row = _empty_row(label, is_priv)
                    row["error"] = "missing"
                else:
                    row = _row(path, label, is_priv, names, avoid, now, clean, all_projects) if not (is_priv and not livework.has_ledger(path)) \
                        else dict(_empty_row(label, True), error="unreadable")
            except Exception:
                row = _empty_row(label, is_priv)
                row["error"] = "unreadable"
            if row.get("_key"):
                found[row["_key"]] = row["clean"]
            rows.append(row)
    _write_cache(time.strftime("%Y-%m-%d", time.localtime(now)), found)
    return {"projects": rows, "no_project": not rows, "_names": frozenset(names)}      # a frozenset: json.dumps(out) fails, never leaks


# ---------------------------------------------------------------- output
def _scrub(obj, names):
    if isinstance(obj, str):
        return leak.hide(obj, names)
    if isinstance(obj, list):
        return [_scrub(x, names) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v, names) for k, v in obj.items()}
    return obj


def to_json(out):
    """The documented shape: `{"projects": [...]}`; keys that start with `_` are internal and never leave. Every string is
    scrubbed of protected names once more."""
    names = out.get("_names") or leak.private_names()
    rows = [{k: v for k, v in row.items() if not k.startswith("_")} for row in out["projects"]]
    for row in rows:
        row.setdefault("clean", "not checked")
    doc = {"projects": rows}
    if out.get("error"):
        doc["error"] = out["error"]
    return _scrub(doc, names)


def render(out, lang=None):
    import i18n
    t = lambda key, **kw: i18n.t(key, lang, **kw)
    names = out.get("_names") or leak.private_names()
    lines = [t("daily.title")]
    if out.get("error"):
        lines.append(t("daily.error", project="-", code=out["error"]))
        return "\n".join(lines) + "\n"
    if out.get("no_project"):
        lines.append(t("daily.no_project"))
    for row in out["projects"]:
        label = row["id"]
        if row.get("error"):
            lines.append(t("daily.error", project=label, code=row["error"]))
            continue
        c = row["counts"]
        if row["protected"]:
            lines.append(t("daily.protected_nodata", project=label) if c["next"] is None
                         else t("daily.protected", project=label, next=c["next"], later=c["later"]))
            continue
        lines.append(t("hk.flow", project=label, made=c["made"], shut=c["shut"], pending=c["pending"],
                       next=row.get("_next") or "-", count=c["next"]))
        if row["rule"]:
            lines.append(t("hk.flow_rule", project=label))
        if not row["batches"] and not row["duplicates"]:
            lines.append(t("daily.nothing"))
        for b in row["batches"]:
            lines.append(t("daily.batch", n=len(b["ids"]), ids=", ".join(b["ids"])) + (t("daily.area", area=b["area"]) if b["area"] else ""))
        for a, b in row["duplicates"]:
            lines.append(t("daily.dup", a=a, b=b))
        if row["order"]:
            lines.append(t("daily.order", n=len(row["order"])))
            for o in row["order"][:SHOW_ORDER]:
                lines.append("    " + t("daily.class." + o["class"].replace("?", "q"), id=o["id"]))
            if len(row["order"]) > SHOW_ORDER:
                lines.append("    " + t("daily.more", n=len(row["order"]) - SHOW_ORDER))
        clean = row.get("clean")
        lines.append(t("daily.clean", remove=clean["remove"], delete=clean["delete"]) if isinstance(clean, dict) else t("daily.clean_unchecked"))
    return leak.hide("\n".join(lines) + "\n", names)


# ---------------------------------------------------------------- the SessionStart facts
def session_facts(project, now=None):
    """-> {"batches": n, "duplicates": n, "clean": n or None}, integers only, for the one SessionStart line; None for a protected
    project (its ledger is not opened), a project that cannot be read, or when the work took more than SESSION_BUDGET_MS.
    `clean` is read from today's cache and never computed here."""
    now = now if now is not None else time.time()
    start = time.monotonic()
    budget = SESSION_BUDGET_MS / 1000.0
    try:
        project = livework.canon(project)
        if not _config_ok() or leak.is_protected(project) or not livework.has_ledger(project):
            return None
        items = sl.load_ledger(project)["items"]
        pending, committed, _nxt, scope = _scope(items)
        found = {"batches": len(batches(scope)), "duplicates": len(duplicates(committed, all_items=items, deadline=start + budget, cap=SESSION_DUP_CAP))}
        if time.monotonic() - start >= budget:
            return None
        found["clean"] = cached_clean(project, now)
    except Exception:
        return None
    return found
