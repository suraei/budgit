"""R-273: the ledger read as a graph (items, the user's inputs, commits, verify receipts), for `budgit release-check`.

Read-only and derived: nothing here writes a file or changes the ledger schema. The edges come from what is already stored: item
text that names another `R-n`, the `.studio/verify/R-n.md` receipt, the claiming session's pointer, and commit messages that name an
item. Text-derived edges are a floor (an item can depend on another without saying so), so every finding is advice, never a block.
"""
import os
import re
import subprocess

import studiolib as sl

ITEM_ID = re.compile(r"\bR-(\d+)\b")
SAFE_REF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/~^@{}-]*$")
GIT_TIMEOUT_S = 20


def _cited(item):
    """Ids named in an item's own words, without itself and in first-seen order."""
    seen = []
    for m in ITEM_ID.finditer("%s %s" % (item.get("title", ""), item.get("text", ""))):
        i = "R-" + m.group(1)
        if i != item["id"] and i not in seen:
            seen.append(i)
    return seen


def _has_pass_receipt(project, item_id):
    try:
        with open(os.path.join(sl.studio_dir(project), "verify", item_id + ".md"), encoding="utf-8", errors="replace") as f:
            return f.readline().strip().startswith("PASS")
    except OSError:
        return False


def _commits(project, since):
    """-> (commits [(sha8, message)], problem|None). `since` must look like a ref, never an option."""
    if not SAFE_REF.match(since) or ".." in since:
        return [], "bad-ref"
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", project, "log", "--no-merges", "--format=%H%x1f%B%x1e", since + "..HEAD", "--"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return [], "git-failed"
    if r.returncode != 0:
        return [], "git-failed"
    out = []
    for rec in r.stdout.split("\x1e"):
        sha, _, msg = rec.strip().partition("\x1f")
        if sha:
            out.append((sha[:8], msg.strip()))
    return out, None


def findings(project, data, scope, since=None):
    """scope is `sl.release_scope(...)`. -> {unverified: [R-n], orphans: [(R-n, state)], cites_unbuilt: [(R-n, [R-m])],
    unlinked_commits: [(sha, subject)], commit_open: [(sha, R-n) a commit naming an item still marked open, not in progress], git: None | "bad-ref" | "git-failed"}."""
    by_id = {it["id"]: it for it in data["items"]}
    unbuilt = {it["id"] for it in scope["blocking"]}
    out = {"unverified": [], "orphans": [], "cites_unbuilt": [], "unlinked_commits": [], "commit_open": [], "git": None}
    for it in scope["built"]:
        if not _has_pass_receipt(project, it["id"]):
            out["unverified"].append(it["id"])
        waiting = [i for i in _cited(it) if i in unbuilt]
        if waiting:
            out["cites_unbuilt"].append((it["id"], waiting))
    for it in scope["blocking"] + scope["deferred"]:
        state = sl.claim_state(it)
        if state in ("dead", "unclaimed"):
            out["orphans"].append((it["id"], state))
    if since:
        commits, problem = _commits(project, since)
        out["git"] = problem
        for sha, msg in commits:
            named = [m for m in dict.fromkeys("R-" + n for n in ITEM_ID.findall(msg))]
            if not named:
                out["unlinked_commits"].append((sha, msg.splitlines()[0] if msg else ""))
            for i in named:
                if by_id.get(i, {}).get("status") == "open":      # doing is normal while work lands in pieces
                    out["commit_open"].append((sha, i))
    return out


def any_findings(f):
    return any(f[k] for k in ("unverified", "orphans", "cites_unbuilt", "unlinked_commits", "commit_open", "git"))
