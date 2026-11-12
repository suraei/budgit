"""R-891 slice 1 / R-910: `budgit asks`, the deterministic list of every ask the user made (read-only).

One row per ledger item whose `src` names an inbox entry of the user's real words (source prompt or answer, R-656), in every phase.
The state comes from the ledger, git and the verify reports only: lost, not started, in progress, built, verified, shipped.
Nothing is written. Git is read with `-c core.fsmonitor=false` and no hooks (studiolib.safe_git_argv).
"""
import datetime
import json
import os
import re
import subprocess

import studiolib as sl

STATES = ("lost", "overdue", "not started", "in progress", "built", "verified", "shipped", "dropped")
SHORT_WORDS = 3                                         # a prompt of this many words or fewer is a bare acknowledgement
_DOC_PATH = re.compile(r"^(\.studio/|\.budgit/|docs/)|\.(md|txt)$")
_SUBJECT = re.compile(r"^(R-\d+)(?![\d-])")
NO_LANE_HOURS = 24
HARNESS_TAGS = ("<bash-input>", "<bash-stdout>", "<command-name>", "<command-message>", "<local-command-stdout>",
                "<system-reminder>", "<task-notification>", "<scheduled-task")
HARNESS_OPENERS = ("Your questions have been answered", "The user answered", "Tool permission request",
                   "[Cross-session idle notice]", "Background (from the session", "The user doesn't want to proceed",
                   "[SYSTEM NOTIFICATION", "[Subagent hand-back]", "Another Claude session sent a message")
COMPACTION_OPENER = "This session is being continued from a previous conversation"
HER_SOURCES = ("prompt", "answer")                      # "manual" is an agent's note, never the user's words
GIT_TIMEOUT = 30
_TIME = re.compile(r"(?<![\d:.\-])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?Z?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ITEM = re.compile(r"(?<![\w-])R-(\d+)(?!\d)")


def _her_ids(project):
    return {e.get("id") for e in sl.load_inbox(project) if e.get("source") in HER_SOURCES and isinstance(e.get("id"), str)}


def _version(text):
    m = re.fullmatch(r"v?(\d+(?:\.\d+)+)", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _git(project, *args):
    try:
        r = subprocess.run(sl.safe_git_argv(project, *args), capture_output=True, text=True, env=sl.safe_git_env(),
                           timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _git_facts(project):
    """-> (set of item ids with a build commit, set of ids a branch or worktree names).
    A build commit has a subject that starts with the item id (`R-861:` or `R-861 `) and changes a file that is not a
    doc or a ledger note; a commit that only mentions the id ("moved to R-861") is not a build."""
    built, branches = set(), set()
    log = _git(project, "log", "--all", "--name-only", "--format=%x1e%s") or ""
    for rec in log.split("\x1e"):
        lines = [l for l in rec.splitlines() if l.strip()]
        m = _SUBJECT.match(lines[0]) if lines else None
        if m and any(not _DOC_PATH.search(f.strip()) for f in lines[1:]):
            built.add(m.group(1))
    names = (_git(project, "branch", "--all", "--format=%(refname:short)") or "").splitlines()
    names += [os.path.basename(l[len("worktree "):].strip()) for l in
              (_git(project, "worktree", "list", "--porcelain") or "").splitlines() if l.startswith("worktree ")]
    for ref in names:
        for m in re.finditer(r"(?<![a-z0-9])r-?(\d+)(?!\d)", ref.strip().lower()):
            branches.add("R-" + m.group(1))
    return built, branches


def cut_version(project):
    """The highest release already cut in this project: the version in a RELEASE-<v>.md or RC-<v>.md report under
    <main checkout>/.budgit/verify (what `budgit release-check` leaves). None when there is none."""
    best = None
    for name in sl.DATA_NAMES:
        folder = os.path.join(main_root(project), name, "verify")
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for n in names:
            m = re.fullmatch(r"(?:RELEASE|RC)-v?(\d+(?:\.\d+)+)\.md", n)
            v = _version(m.group(1)) if m else None
            if v and (best is None or v > best):
                best = v
    return best


def public_version(project):
    """The last release: the public version the daily update check cached from the public repo's plugin.json
    (updatelib, update-check.json in Budgit's home), read only; else the highest cut release report (cut_version).
    A tuple of ints, or None when neither is known."""
    try:
        import updatelib
        cached = _version(updatelib._read_cache()[1])
    except Exception:
        cached = None
    return cached or cut_version(project)


def main_root(project):
    """The main checkout of `project` (itself unless it is a linked worktree), by `git rev-parse --git-common-dir`."""
    out = (_git(project, "rev-parse", "--git-common-dir") or "").strip()
    if not out:
        return project
    common = os.path.normpath(os.path.join(project, out))
    return os.path.dirname(common) if os.path.basename(common) == ".git" else project


def _passes(project, item_id):
    """Line 1 of <main checkout>/.budgit/verify/R-n.md starts with PASS (a worktree reads the main checkout's reports)."""
    folders = [os.path.join(root, name) for root in dict.fromkeys((main_root(project), project)) for name in sl.DATA_NAMES]
    for folder in folders:                       # .budgit, or .studio of a project not migrated yet
        path = os.path.join(folder, "verify", item_id + ".md")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                if f.readline().strip().startswith("PASS"):
                    return True
        except OSError:
            continue
    return False


def _text_of(it):
    return "\n".join(str(it.get(k) or "") for k in ("title", "text", "evidence", "contract"))


def _carried(item, target, own_ids):
    """True when the target carries the folded item's words: one of its I-n ids is in the target's `src` (what
    `ledger link` writes) or in its text, or the target's text holds the item's title."""
    blob = _text_of(target).lower()
    ids = [s for s in item.get("src") or () if s in own_ids]
    if any(i in (target.get("src") or ()) for i in ids):
        return True
    if any(re.search(r"(?<![\w-])" + re.escape(i.lower()) + r"(?!\d)", blob) for i in ids):
        return True
    title = (item.get("title") or "").strip().lower()
    return bool(title) and title in blob


def compute(project, now=None, public="auto"):
    """-> {"rows": [...], "summary": {state: n}, "planned": [...]}. `now` is a local datetime (the clock, injectable);
    `public` is the last public release as a tuple (default: read from the update check cache; None = unknown)."""
    now = now or datetime.datetime.now()
    last = public_version(project) if public == "auto" else public
    data = sl.load_ledger(project)
    own_ids = _her_ids(project)
    by_id = {it["id"]: it for it in data["items"]}
    named, branches = _git_facts(project)

    def own_state(it):
        ph = _version(it.get("phase") or "")
        cut = bool(ph and last and ph <= last)             # the item's phase is already released
        passed = _passes(project, it["id"])
        if it["status"] in ("done", "verified"):
            if not ph or not last or cut:
                return "shipped"
            return "verified" if passed else "built"
        if cut and not passed:
            return "overdue"                               # open or doing in a phase already cut: the lost-ask case
        if passed:
            return "verified"
        if it["id"] in named:                              # a commit that starts with the id and changes real files
            return "built"
        if it["status"] == "doing" or it["id"] in branches:
            return "in progress"
        return "not started"

    memo = {}

    def state(it, seen=()):
        if it["id"] in memo:
            return memo[it["id"]]
        into = (it.get("folded") or it.get("parked") or {}).get("into") if it["status"] in ("folded", "parked") else None
        if it["status"] in ("folded", "parked") and into:
            tgt = by_id.get(into)
            if tgt is None or tgt["status"] == "dropped" or it["id"] in seen:
                out = "lost"
            else:
                out = state(tgt, seen + (it["id"],))
        else:
            out = own_state(it)
        memo[it["id"]] = out
        return out

    def row(iid, title, st, status, phase, words, into="", flag=False, reason="", no_lane=False):
        return {"id": iid, "title": title, "state": st, "status": status, "phase": phase, "words": words, "into": into,
                "words_not_carried": flag, "reason": reason, "no_lane_24h": no_lane}

    rows = []
    held = set()                                         # every I-n that some item (live, folded or dropped) holds
    for it in data["items"]:
        held.update(s for s in it.get("src") or () if isinstance(s, str))
        words = [s for s in it.get("src") or () if isinstance(s, str) and s in own_ids]
        if not words:
            continue
        title = sl.oneline(it.get("title") or it.get("text") or "", 70)
        if it["status"] == "dropped":                    # the user's words, dropped: listed with the reason, never lost silently
            why = ("asked in %s" % it["dropped_by"]) if it.get("dropped_by") else ""
            rows.append(row(it["id"], title, "dropped", "dropped", it.get("phase") or "", words,
                            reason=(why + " " + sl.oneline(it.get("evidence") or "", 60)).strip()))
            continue
        into = (it.get("folded") or it.get("parked") or {}).get("into") if it["status"] in ("folded", "parked") else None
        tgt = by_id.get(into) if into else None
        flag = bool(tgt and tgt["status"] in ("done", "verified") and not _carried(it, tgt, own_ids))
        rows.append(row(it["id"], title, state(it), it["status"], it.get("phase") or "", words, into or "", flag,
                        no_lane=_no_lane(it, branches, now)))
    short, noise, seen_text = [], [], set()
    for e in sl.load_inbox(project):                     # the inbox is the source of truth for the user's words
        if e.get("source") not in HER_SOURCES or not isinstance(e.get("id"), str):
            continue
        text = str(e.get("text") or "")
        repeat = text in seen_text                       # an exact copy of an older entry: only the oldest stays
        seen_text.add(text)
        if e["id"] in held:
            continue
        if repeat or is_harness_text(text):
            noise.append(e["id"])
        elif _bare(e, text):
            short.append({"id": e["id"], "text": sl.oneline(text, 60)})
        else:
            rows.append(row(e["id"], sl.oneline(text, 70), "lost", "inbox", "", [e["id"]], reason="in no item"))
    summary = {s: 0 for s in STATES}
    for r in rows:
        summary[r["state"]] += 1
    return {"rows": rows, "summary": summary, "short_replies": short, "harness_noise": noise,
            "planned": planned(project, data, named, now)}


def is_harness_text(text):
    """Harness text, not the user's words (R-656/R-657): it starts with a harness tag or the compaction-summary opener."""
    body = (text or "").lstrip()
    return body.startswith(HARNESS_TAGS) or body.startswith(HARNESS_OPENERS) or body.startswith(COMPACTION_OPENER)


def _bare(entry, text):
    """A bare acknowledgement: a card answer that only picks an option, or a prompt of SHORT_WORDS words or fewer."""
    if entry.get("source") == "answer" and text.lstrip().startswith("AskUserQuestion answer"):
        return True
    return len(text.split()) <= SHORT_WORDS


def _no_lane(it, branches, now):
    """R-919, Class A gate: an open bug item with the user's words, older than NO_LANE_HOURS, that no branch or worktree names."""
    if it["status"] != "open" or "bug" not in (it.get("flags") or ()) or it["id"] in branches:
        return False
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            made = datetime.datetime.strptime(it.get("created") or "", fmt).replace(tzinfo=datetime.timezone.utc)
            break
        except ValueError:
            made = None
    if made is None:
        return False
    return now.astimezone(datetime.timezone.utc) - made > datetime.timedelta(hours=NO_LANE_HOURS)


def planned(project, data, named, now):
    """R-910: a progress line naming R-n with a clock time that has passed today, while the item has no commit, is not doing
    and has no verify report. -> [{"id", "time", "title", "file"}]"""
    by_id = {it["id"]: it for it in data["items"]}
    sdir = sl.studio_dir(project)
    today = now.date().isoformat()
    out, seen = [], set()
    try:
        names = sorted(n for n in os.listdir(sdir) if n.startswith("progress") and n.endswith(".md"))
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(sdir, name), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        heading = None                                   # the date of the heading the line sits under
        for line in lines:
            if line.lstrip().startswith("#"):
                dates = _DATE.findall(line)
                heading = dates[0] if dates else None
                continue
            dates = _DATE.findall(line)
            if today not in dates and not (not dates and heading == today):
                continue                                 # only a line dated today, or under a heading dated today, is today's plan
            if any(d != today for d in dates):
                continue
            bare = _STAMP.sub(" ", line)               # a log timestamp is not a planned time
            tm = _TIME.search(bare)
            if not tm:
                continue
            at = now.replace(hour=int(tm.group(1)), minute=int(tm.group(2)), second=0, microsecond=0)
            if at > now:
                continue
            for m in _ITEM.finditer(bare):
                iid = "R-" + m.group(1)
                it = by_id.get(iid)
                if it is None or iid in seen:
                    continue
                if iid in named or it["status"] in ("doing", "done", "verified", "dropped", "folded") or _passes(project, iid):
                    continue
                seen.add(iid)
                out.append({"id": iid, "time": "%02d:%02d" % (at.hour, at.minute),
                            "title": sl.oneline(it.get("title") or it.get("text") or "", 70), "file": name})
    return out


def render(result, t):
    """Text lines; `t` is the i18n function."""
    lines = []
    for r in result["rows"]:
        into = (" -> " + r["into"]) if r["into"] else ""
        flag = ("  [%s]" % t("asks.words_not_carried")) if r["words_not_carried"] else ""
        flag += ("  [%s]" % t("asks.no_lane")) if r["no_lane_24h"] else ""
        flag += ("  {%s}" % r["reason"]) if r["reason"] else ""
        lines.append("%s  %s  %s  %s%s  (%s)%s" % (r["id"], t("asks.state." + r["state"].replace(" ", "_")), r["phase"] or "-",
                                                  r["title"], into, ", ".join(r["words"]), flag))
    if not result["rows"]:
        lines.append(t("asks.none"))
    counts = ", ".join("%s %d" % (t("asks.state." + s.replace(" ", "_")), result["summary"][s]) for s in STATES)
    lines.append(t("asks.summary", n=len(result["rows"]), counts=counts))
    lines.append(t("asks.noise", n=len(result["harness_noise"])))
    lines.append(t("asks.short_replies", n=len(result["short_replies"]),
                   ids=", ".join(r["id"] for r in result["short_replies"][:40])))
    if result["planned"]:
        lines.append(t("asks.planned_head"))
        for p in result["planned"]:
            lines.append("  %s  %s  %s  (%s)" % (p["id"], p["time"], p["title"], p["file"]))
    return lines


def run(project, state=None, as_json=False, now=None, t=None, gate=False):
    """-> (text, exit code). With `gate`, the exit code is 1 when an open Class A item with the user's words has had no lane for
    over NO_LANE_HOURS (for the stand-up); otherwise 0."""
    result = compute(project, now)
    hits = [r["id"] for r in result["rows"] if r["no_lane_24h"]]
    if state:
        wanted = state.strip().lower().replace("_", " ")
        result["rows"] = [r for r in result["rows"] if r["state"] == wanted]
    code = 1 if gate and hits else 0
    if as_json:
        return json.dumps(result, ensure_ascii=False, indent=2), code
    return "\n".join(render(result, t)), code
