"""An item closes itself at merge: tasks close on their own, with the evidence written down.

ONE function decides what "merged" means: `merges_after` lists the merge commits on the default branch (first-parent) that came
after the last commit this step checked, and `item_ids` reads the item ids a merge subject names. `reconcile` closes an open, doing or
verified item named by such a merge, through the existing done path (studiolib.done_item), when a verifier report for it starts
with the word PASS, is sealed by budgit:verifier, and names the merged branch tip (the merge's second parent) or an ancestor of it
with no later code change. Without that report nothing closes: the item is kept in `pending` and `budgit status` lists it. Any
error is logged and closes nothing. The last-checked sha and the pending list live in the project state (state.json), key
`merge_reconcile`; every sha read back from it is checked as 40 hex characters before it reaches git."""
import os
import re
import subprocess

import studiolib as sl

ID = re.compile(r"\bR-(\d+)\b")
OPEN = ("open", "doing", "verified")
BATCH = 50                       # merges handled per run; the rest wait for the next trigger, never skipped
READ_CAP = 256 * 1024            # a report is read up to this many bytes
PENDING_CAP = 200
GIT_TIMEOUT_S = 20
NOT_CODE_PREFIX = (".studio/", ".budgit/", "docs/")
SHA40 = re.compile(r"[0-9a-f]{40}")
PASS_WORD = re.compile(r"PASS(?:$|[ :\-])")
HEADER_MAX = 12
SCOPE_LINE =re.compile(r"\s*scope\s*:\s*(\S.*)$", re.I)
SCOPE = re.compile(r"\b(?:part|phase)\s+\w+", re.I)    # a report that names a part or phase proves only that part
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean(text):
    """No control character (below 0x20 or 0x7f) reaches the ledger."""
    return CONTROL.sub("", text or "")


def _note(text):
    """One plain line in the hooks log (the same file log_error writes)."""
    try:
        os.makedirs(sl.home_state(), exist_ok=True)
        sl.append_text(os.path.join(sl.home_state(), "hooks.log"), "%s merge reconcile: %s\n" % (sl.now(), text))
    except Exception:
        pass


def item_ids(subject):
    """The item ids named in a commit subject, in order, once each."""
    return list(dict.fromkeys("R-" + n for n in ID.findall(subject or "")))


def _git(top, *args):
    r = subprocess.run(["git", "--no-optional-locks", "-C", top, *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    return r.returncode, r.stdout.strip()


def _top_and_branch(project):
    """(top, branch) of the project's own repository, or None when it has none of its own (nothing to reconcile)."""
    rc, top = _git(project, "rev-parse", "--show-toplevel")
    if rc or not top or not os.path.samefile(top, project):
        return None
    import maincheck
    return top, maincheck.main_branch(project, top)


def merges_after(top, branch, last=None):
    """-> (rows, new_last, left_out). rows = [{sha, tip, subject}] oldest first, at most BATCH. With a valid `last` the BATCH
    OLDEST merges after it are taken and new_last is the newest of them (or None when none remain: the caller uses the branch
    tip), so the next run continues and no merge is skipped. With no usable `last` the newest BATCH are taken and `left_out`
    counts the older ones."""
    spec = "refs/heads/" + branch
    if last and SHA40.fullmatch(last) and _git(top, "merge-base", "--is-ancestor", last, spec)[0] == 0:
        spec, since = "%s..%s" % (last, spec), True
    else:
        since = False
    rc, out = _git(top, "log", "--first-parent", "--merges", "--format=%H%x1f%P%x1f%s%x1e", spec, "--")
    if rc:
        raise sl.StudioError("git log failed in %s" % top)
    rows = []
    for rec in out.split("\x1e"):
        parts = rec.strip().split("\x1f")
        if len(parts) == 3 and parts[0]:
            parents = parts[1].split()
            if len(parents) > 1:
                rows.append({"sha": parts[0], "tip": parents[1], "subject": clean(parts[2])})
    rows.reverse()                                  # oldest first
    if since:
        taken = rows[:BATCH]
        return taken, (taken[-1]["sha"] if len(rows) > BATCH else None), 0
    left = max(0, len(rows) - BATCH)
    return rows[left:], None, left


def _read(path):
    """The bytes of a report (at most READ_CAP), or None when it is larger: that counts as no PASS."""
    with open(path, "rb") as f:
        data = f.read(READ_CAP + 1)
    if len(data) > READ_CAP:
        _note("a report over %d bytes counts as no PASS: %s" % (READ_CAP, os.path.basename(path)))
        return None
    return data


def _not_code(name):
    return name.startswith(NOT_CODE_PREFIX) or ("/" not in name and name.endswith(".md"))


def _verdict(top, project, item_id, tip):
    """(path, data, reason). path is the report that proves the merged tip: the word PASS first, sealed, naming the tip or an
    ancestor with no code change after it. An unreadable report raises: the caller closes nothing."""
    found = sl.verify_reports(project, item_id)
    if not found:
        return None, None, "no report"
    path = found[0]
    data = _read(path)
    if data is None:
        return None, None, "the report is too large"
    lines = data.decode("utf-8", "replace").splitlines()
    if not PASS_WORD.match((lines[0] if lines else "").strip()):
        return None, None, "the report is not PASS"
    if not sl.report_sealed(path):
        return None, None, "the report is not sealed by the verifier"
    header = []                                  # the header block: every line up to the first blank one, at most 12
    for l in lines[:HEADER_MAX]:
        if not l.strip():
            break
        header.append(l)
    declared = [m.group(1).strip() for m in (SCOPE_LINE.match(l) for l in header) if m]
    if declared:                                 # a structured `scope: ...` line decides: only "whole" is the whole item
        if declared[0].lower() != "whole":
            return None, None, "partial:" + clean(declared[0])[:40]
    else:
        scope = SCOPE.search(" ".join(header))
        if scope:
            return None, None, "partial:" + clean(scope.group(0))[:40]
    sha = lines[2].strip().lower() if len(lines) > 2 else ""
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return None, None, "the report names no sha"
    rc, full = _git(top, "rev-parse", "--verify", "--quiet", sha + "^{commit}")
    if rc or not SHA40.fullmatch(full or ""):
        return None, None, "the report sha is unknown here"
    if full != tip:
        if _git(top, "merge-base", "--is-ancestor", full, tip)[0] != 0:
            return None, None, "the report is for another sha"
        rc, names = _git(top, "diff", "--name-only", full, tip, "--")
        if rc or any(n and not _not_code(n) for n in names.splitlines()):
            return None, None, "code changed after the report's sha"
    return path, data, ""


def _test_names(top, merge, data):
    names = list(dict.fromkeys(sl._PROOF_TEST.findall(data.decode("utf-8", "replace"))))
    if not names:
        rc, out = _git(top, "diff", "--name-only", merge["sha"] + "^1", merge["sha"], "--", "tests")
        names = [os.path.basename(n) for n in out.splitlines() if n.endswith(".py")]
    return names[:8]


def _evidence(project, top, branch, merge, path, data):
    names = _test_names(top, merge, data)
    rel = os.path.relpath(path, sl.main_project_root(project))
    return clean("merged to %s in %s: %s; tests: %s; verify file %s" % (branch, merge["sha"][:12], merge["subject"][:80],
                                                                         ", ".join(names) or "none", rel))


def _state(project):
    """(state, merge_reconcile). Every sha read back is a full 40 hex characters; an entry that fails is dropped with one log line."""
    st = sl.load_state(project)
    mr = st.get("merge_reconcile")
    if not isinstance(mr, dict):
        mr = {}
    if mr.get("last") is not None and not (isinstance(mr["last"], str) and SHA40.fullmatch(mr["last"])):
        _note("dropped a malformed last-checked sha from the state")
        mr["last"] = None
    pending, raw = {}, mr.get("pending")
    for i, p in (raw.items() if isinstance(raw, dict) else ()):
        ok = (isinstance(i, str) and re.fullmatch(r"R-\d+", i) and isinstance(p, dict)
              and all(isinstance(p.get(k), str) and SHA40.fullmatch(p[k]) for k in ("merge", "tip")))
        if ok:
            pending[i] = {"merge": p["merge"], "tip": p["tip"], "subject": clean(str(p.get("subject") or ""))[:200]}
            if p.get("old") is True:
                pending[i]["old"] = True
            if isinstance(p.get("partial"), str) and p["partial"]:
                pending[i]["partial"] = clean(p["partial"])[:40]
        else:
            _note("dropped a malformed pending entry from the state")
    mr["pending"] = pending
    return st, mr


def reconcile(project, force=False):
    """The one reconcile step. -> {"closed": [ids], "waiting": [(id, sha8)], "error": bool}. Never raises."""
    out = {"closed": [], "waiting": [], "error": False}
    try:
        _reconcile(project, out, force)
    except Exception:
        sl.log_error("merge reconcile")
        out.update(closed=[], error=True)
    return out


def _fast_head(project):
    """The default branch tip read from the repository files with no git process (None when it cannot be told)."""
    gitdir = os.path.join(project, ".git")
    if not os.path.isdir(gitdir):
        return None
    names = [n for n in (sl.load_project_config(project).get("main_branch"), "main", "master") if n]
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", str(name)) or ".." in str(name):
            continue
        try:
            with open(os.path.join(gitdir, "refs", "heads", name), encoding="utf-8") as f:
                sha = f.read().strip()
            if SHA40.fullmatch(sha):
                return sha
        except OSError:
            pass
        try:
            with open(os.path.join(gitdir, "packed-refs"), encoding="utf-8") as f:
                for line in f:
                    sha, _, ref = line.strip().partition(" ")
                    if ref == "refs/heads/" + name and SHA40.fullmatch(sha):
                        return sha
        except OSError:
            pass
    return None


def head_moved(project):
    """Cheap, no git process in the usual case: True when the default branch tip is not the last-checked sha (or cannot be told)."""
    try:
        head = _fast_head(project)
        if head is None:
            return True
        return head != _state(project)[1].get("last")
    except Exception:
        return True


def _reconcile(project, out, force):
    where = _top_and_branch(project)
    if not where:
        return
    top, branch = where
    rc, head = _git(top, "rev-parse", "--verify", "refs/heads/" + branch)
    if rc or not SHA40.fullmatch(head or ""):
        raise sl.StudioError("no tip for %s" % branch)
    state, mr = _state(project)
    if mr.get("last") == head and not mr["pending"] and not force:
        return
    data = sl.load_ledger(project)
    items = {i["id"]: i for i in data["items"]}
    first = mr.get("last") is None                # the first run only lists: older merges point at reports nobody scope-checked
    merges = {i: {"sha": p["merge"], "tip": p["tip"], "subject": p["subject"], "old": p.get("old")} for i, p in mr["pending"].items()}
    rows, new_last, left = merges_after(top, branch, mr.get("last"))
    if left:
        _note("first run: %d older merges were left out (only the newest %d are checked)" % (left, BATCH))
    for m in rows:
        for i in item_ids(m["subject"]):
            merges.pop(i, None)                  # the newest merge naming an item wins, and it goes last
            merges[i] = m
    plan, pending = [], {}
    for i, m in merges.items():                   # decide everything first: an error here closes nothing
        item = items.get(i)
        if item is None or item["status"] not in OPEN:
            continue
        entry = {"merge": m["sha"], "tip": m["tip"], "subject": m["subject"][:200]}
        if first or m.get("old"):                 # listed, never closed: nobody checked what an older report covers
            pending[i] = dict(entry, old=True)
            continue
        path, body, why = _verdict(top, project, i, m["tip"])
        if path:
            plan.append((i, m, _evidence(project, top, branch, m, path, body)))
        else:
            pending[i] = dict(entry, partial=why[8:]) if why.startswith("partial:") else entry
    for i, m, evidence in sorted(plan, key=lambda r: int(r[0][2:])):
        try:
            sl.done_item(project, data, i, evidence, derived_outcome=clean("closed by merge %s: %s" % (m["sha"][:12], m["subject"][:120])))
            sl.audit(project, "merge-reconcile %s: closed by %s" % (i, m["sha"][:12]))
            out["closed"].append(i)
        except sl.StudioError:                    # e.g. no test names: the item waits, nothing else is stopped
            pending[i] = {"merge": m["sha"], "tip": m["tip"], "subject": m["subject"][:200]}
    if out["closed"]:
        sl.save_ledger(project, data)
    if len(pending) > PENDING_CAP:
        _note("pending list over %d: the oldest %d dropped" % (PENDING_CAP, len(pending) - PENDING_CAP))
        pending = dict(list(pending.items())[-PENDING_CAP:])
    mr.update(last=new_last or head, pending=pending)
    state["merge_reconcile"] = mr
    sl.save_state(project, state)
    out["waiting"] = [(i, p["merge"][:8]) for i, p in sorted(pending.items(), key=lambda kv: int(kv[0][2:]))]


def waiting_lines(project, data):
    """The `budgit status` lines for merged items that still wait for a verifier PASS."""
    try:
        by_id = {i["id"]: i for i in data["items"]}
        pending = _state(project)[1]["pending"]
        return [("merged, partial PASS (%s), waiting for the whole item: %s (%s)" % (p["partial"], i, p["merge"][:8])) if p.get("partial")
                else "merged, waiting for a verifier PASS: %s (%s)" % (i, p["merge"][:8])
                for i, p in sorted(pending.items(), key=lambda kv: int(kv[0][2:])) if by_id.get(i, {}).get("status") in OPEN]
    except Exception:
        sl.log_error("merge reconcile: waiting lines")
        return []


def is_merge_command(command):
    """True for a Bash command that ran git merge, pull or commit (the only ones that can move the default branch)."""
    return bool(re.search(r"\bgit\b[^;&|]*?\b(merge|pull|commit)\b", command or ""))
