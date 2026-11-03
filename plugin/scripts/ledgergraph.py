"""R-273: the ledger read as a graph (items, the user's inputs, commits, verify receipts), for `budgit release-check`.

Read-only and derived: nothing here writes a file or changes the ledger schema. The edges come from what is already stored: item
text that names another `R-n`, the `.studio/verify/R-n.md` receipt, the claiming session's pointer, and commit messages that name an
item. Text-derived edges are a floor (an item can depend on another without saying so), so every finding is advice, never a block.
"""
import os
import re
import subprocess
import time

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


DRIFT_GRACE_S = 24 * 3600          # an item may stay open this long after the commit that says it is fixed, built or merged
DRIFT_WINDOW_DAYS = 30             # how far back on main the check looks
_SAYS_DONE = re.compile(r"\b(?:(?:hot|bug)?fix(?:es|ed|ing)?|(?:re)?build(?:s|ing)?|rebuilt|built|merg(?:e|es|ed|ing))\b", re.I)


def _same_folder(a, b):
    """The same directory on disk (APFS and NTFS fold case, so comparing the path strings would miss `/PRIVATE/tmp/x` for `/private/tmp/x`)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _main_branch(project):
    for name in ("main", "master"):
        r = subprocess.run(["git", "--no-optional-locks", "-C", project, "rev-parse", "--verify", "--quiet", "refs/heads/" + name],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        if r.returncode == 0:
            return name
    return None


def drift(project, scope, now=None, window_days=DRIFT_WINDOW_DAYS):
    """R-362: the ledger against `git log main`, which is the only place the item<->commit links are worked out (the commit-msg hook
    never writes the ledger). -> {"drift": [(item_id, sha8, subject)], "branch": name|None, "git": None | "not-a-repo" | "git-failed" | "timed-out"}.
    A timeout is its own answer, and with items left to check ("candidates") it is an incomplete check, never a pass.
    An item drifts when it is still open or in progress, a commit on main older than a day names it in a subject that says
    fix/build/merge, and it is not parked (`later`) with a reason. Only the items of `scope` (release_scope) are looked at. The
    newest such commit is the one reported."""
    out = {"drift": [], "branch": None, "git": None, "shallow": False, "candidates": 0}
    candidates = {it["id"]: it for it in scope["blocking"] + scope["deferred"] if not sl.later_reason(it)}
    now = time.time() if now is None else now
    git = ["git", "--no-optional-locks", "-C", project]
    try:
        top = subprocess.run(git + ["rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        # a project inside someone else's repository has no main of its own: that log names other projects' items
        if top.returncode != 0 or not _same_folder(top.stdout.strip(), project):
            out["git"] = "not-a-repo"
            return out
        branch = out["branch"] = _main_branch(project)
        if not branch:
            out["git"] = "git-failed"
            return out
        shallow = subprocess.run(git + ["rev-parse", "--is-shallow-repository"], capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        out["shallow"] = shallow.stdout.strip() == "true"
        # no --since and no -n: either stops the walk at an old commit that sits above newer ones; the window is applied below
        r = subprocess.run(git + ["log", "refs/heads/" + branch, "--format=%H%x1f%ct%x1f%s%x00", "--"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        out["git"] = "timed-out"
        out["candidates"] = len(candidates)
        return out
    except (OSError, subprocess.SubprocessError):
        out["git"] = "git-failed"
        return out
    if r.returncode != 0:
        out["git"] = "git-failed"
        return out
    newest = {}
    for rec in r.stdout.split("\x00"):
        sha, _, rest = rec.strip().partition("\x1f")
        stamp, _, subject = rest.partition("\x1f")
        try:
            age = now - int(stamp)
        except ValueError:
            continue
        if not sha or age <= DRIFT_GRACE_S or age > window_days * 86400 or not _SAYS_DONE.search(subject):
            continue
        for n in dict.fromkeys(ITEM_ID.findall(subject)):
            i = "R-" + n
            if i in candidates and (i not in newest or age < newest[i][0]):
                newest[i] = (age, sha[:8], subject)
    out["drift"] = [(i, v[1], v[2]) for i, v in sorted(newest.items(), key=lambda kv: int(kv[0][2:]))]
    return out
