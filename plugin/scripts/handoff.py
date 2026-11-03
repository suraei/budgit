"""R-283 S5: renewal of a long chat by handoff and echo (plan 5.2). The CLI does the mechanical half.

`handoff write` collects what can be READ and writes it to `<data folder>/private/handoff/<lane>.md` (gitignored), with
marked blanks for the judgement only the model has (next steps, traps). It also stores the facts in a sidecar
(`<lane>.facts.json`) so editing the text cannot weaken the check. `handoff check <lane> --echo <file>` compares the
successor's echo with the facts: the tasks the old chat holds, the user's decisions linked to them, the commits since the chat
began, the branch and the uncommitted files. It exits non-zero and names what the echo leaves out. Only an echo that
says all of them lets the PM archive the old chat.

Not the same thing as `relay` (R-301), which renews a chat running an OLD BUDGIT VERSION: that record lives in the Budgit
home and gates the archive guard. This one is for a chat whose context grew, in any project, and is advisory: the PM
reads the exit code before archiving.
"""
import json
import os
import re
import subprocess

import card
import relay
import studiolib as sl

LANE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
BLANK = "<!-- FILL"
MAX_ECHO_BYTES = 65536
MAX_COMMITS = 40
NEXT = "## Next steps"
TIMEOUT_S = 6
GROUPS = ("tasks", "decisions", "commits", "branch", "uncommitted")


def lane_name(lane, session):
    name = lane or (session or "")[:8]
    if not LANE.match(name or ""):
        raise sl.StudioError("lane name must be 1-40 letters, digits, - or _ (got %r)" % (name,))
    return name


def _dir(project):
    return os.path.join(sl.studio_dir(project), "private", "handoff")


def _git(project, *args):
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-C", project, *args], capture_output=True, text=True,
                             timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _changed(project, prefix):
    """Uncommitted files under the project folder, relative to it, real names (no git quoting), data folders left out."""
    out = _git(project, "-c", "core.quotepath=false", "status", "--porcelain", "-z", "-uall", "--", ".")
    if not out:
        return []
    skip = (sl.DATA_NAME + "/", sl.OLD_DATA_NAME + "/")
    files, entries, i = [], out.split("\0"), 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":                                    # a rename or copy: the next entry is where it came from
            i += 1
        path = entry[3:]
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        if path and not path.startswith(skip):
            files.append(path)
    return files


def collect(project, session):
    """The facts a successor must restate, read from the ledger, the inbox and git. Each group is a list of strings."""
    data = sl.load_ledger(project)
    mine = [it for it in data["items"] if it.get("status") == "doing" and it.get("doing_by") == session]
    inbox = {e.get("id"): e.get("text") for e in sl.load_inbox(project)}
    decisions = []
    for it in mine:
        for src in it.get("src") or []:
            if src in inbox and src not in [d[0] for d in decisions]:
                decisions.append((src, inbox[src]))
    pointer = sl.read_pointer(session) or {}
    since = pointer.get("ts")
    since = since if isinstance(since, str) and sl._epoch(since) is not None else None
    log = _git(project, "log", "-%d" % (MAX_COMMITS + 1), "--format=%h %s", "--since=" + since) if since else None
    commits = [line for line in (log or "").splitlines() if line.strip()]
    more = len(commits) > MAX_COMMITS
    commits = commits[:MAX_COMMITS]
    branch = (_git(project, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    top = (_git(project, "rev-parse", "--show-toplevel") or "").strip()
    prefix = (_git(project, "rev-parse", "--show-prefix") or "").strip()
    files = _changed(project, prefix)
    return {"items": mine, "decisions": decisions, "commits": commits, "more": more, "branch": branch, "worktree": top,
            "files": files, "since": since}


def _checks(project):
    try:
        cfg = json.loads(sl.read_lenient(os.path.join(sl.studio_dir(project), "project.json")))
        verify = cfg.get("verify") or {}
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for key in ("prepare", "checks"):
        for cmd in verify.get(key) or []:
            if isinstance(cmd, str) and cmd.strip():
                out.append(cmd.strip())
    return out


def _facts(found):
    return {"tasks": [it["id"] for it in found["items"]], "decisions": [d[0] for d in found["decisions"]],
            "commits": [c.split(" ", 1)[0] for c in found["commits"]], "branch": [found["branch"]] if found["branch"] else [],
            "uncommitted": list(found["files"])}


def render(lane, session, found, checks):
    lines = ["# Handoff: %s" % lane, "", "Written %s by session %s." % (sl.now(), session), "",
             "Branch: %s" % (found["branch"] or "-"), "Worktree: %s" % (found["worktree"] or "-"), "",
             "## Tasks this chat holds", ""]
    lines += ["- %s %s [%s]" % (it["id"], it.get("title") or "", it.get("status")) for it in found["items"]] or ["- (none)"]
    lines += ["", "## The user's decisions linked to those tasks (verbatim)", ""]
    lines += ["- %s: %s" % (i, t) for i, t in found["decisions"]] or ["- (none)"]
    lines += ["", "## Commits since this chat began", ""]
    lines += ["- %s" % c for c in found["commits"]] or ["- (none)" if found["since"] else "- (the start of this chat is unknown)"]
    if found["more"]:
        lines += ["- (and older ones: see `git log`)"]
    lines += ["", "## Uncommitted files", ""]
    lines += ["- %s" % f for f in found["files"]] or ["- (none)"]
    lines += ["", "## Test commands", ""]
    lines += ["- %s" % c for c in checks] or ["- (none in project.json)"]
    lines += ["", NEXT, "", BLANK + ": what the next chat does first, in order -->", "",
              "## Traps", "", BLANK + ": what looks fine and is not, what was tried and failed -->", ""]
    return "\n".join(lines)


def write(project, session, lane=None):
    """Write the handoff and its facts. Returns (path, facts). Needs a session that is bound to the project."""
    if not sl.valid_session(session):
        raise sl.StudioError("handoff write needs a session id (CLAUDE_CODE_SESSION_ID); run it inside the chat")
    name = lane_name(lane, session)
    found = collect(project, session)
    folder = sl.ensure_subdir(project, "private", "handoff")
    card.ignore_private(project)
    facts = _facts(found)
    path = os.path.join(folder, name + ".md")
    text = render(name, session, found, _checks(project))
    try:
        old = sl.read_text(path)
    except (OSError, ValueError):
        old = ""
    if NEXT in old and NEXT in text:                           # a second write refreshes the facts, never the text already filled in
        text = text[:text.index(NEXT)] + old[old.index(NEXT):]
    sl.atomic_write(path, text)
    sl.atomic_write(os.path.join(folder, name + ".facts.json"),
                    json.dumps({"session": session, "written_at": sl.now(), "facts": facts}, indent=1) + "\n")
    return path, facts


def check(project, lane, echo_path):
    """Compare the successor's echo with the stored facts. Returns {'missing': {group: [...]}, 'blank': bool}. Raises
    when there is no handoff for the lane or the echo cannot be read."""
    name = lane_name(lane, None)
    folder = _dir(project)
    try:
        facts = json.loads(sl.read_text(os.path.join(folder, name + ".facts.json")))["facts"]
    except (OSError, ValueError, KeyError, TypeError):
        raise sl.StudioError("no handoff for lane %r: run `budgit handoff write --lane %s` in the old chat first" % (name, name))
    if not isinstance(facts, dict) or not all(isinstance(facts.get(g, []), list) and all(isinstance(f, str) for f in facts.get(g, []))
                                              for g in GROUPS):
        raise sl.StudioError("the facts of lane %r are not readable: write the handoff again" % name)
    try:
        with open(echo_path, encoding="utf-8") as f:
            text = f.read(MAX_ECHO_BYTES + 1)
    except OSError as exc:
        raise sl.StudioError("cannot read the echo %s (%s)" % (echo_path, exc.strerror))
    except UnicodeDecodeError:
        raise sl.StudioError("the echo %s is not UTF-8 text" % echo_path)
    if len(text) > MAX_ECHO_BYTES:
        raise sl.StudioError("the echo is longer than %d bytes" % MAX_ECHO_BYTES)
    if not text.strip():
        raise sl.StudioError("the echo is empty")
    missing = {}
    for group in GROUPS:
        gone = [f for f in facts.get(group) or [] if not relay.said(f, text)]
        if gone:
            missing[group] = gone
    try:
        blank = BLANK in sl.read_text(os.path.join(folder, name + ".md"))
    except OSError:
        blank = False
    return {"missing": missing, "blank": blank}
