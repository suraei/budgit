"""R-301: a chat on an old Budgit hands its work to a fresh chat (which loads the installed version) and is archived only
after the fresh chat echoed every fact of the handoff back.

One record per old chat at <budgit home>/relay/<old session>.json:
  {old, app_id, title, running, installed, state, handoff, facts, new, reason, at}
State: handoff -> echoed -> done, or failed (the old chat stays as it is). The archive guard in hook.py reads it:
the old chat is never archived before `echoed`. Budgit only records and checks; the model starts and archives chats
with the app's own tools, and only the chat the user just typed to ever starts a relay."""
import json
import os
import re

import studiolib as sl
import updatelib

STATES = ("handoff", "echoed", "done", "failed")
_SID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_APP_ID = re.compile(r"^local_[A-Za-z0-9_-]{1,80}$")
_MAX_BYTES = 65536
_FACTS = (re.compile(r"\b[RI]-\d+\b"),                                         # ledger and inbox ids
          re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"),  # commits: hex with a digit and a letter
          re.compile(r"\bclaude/[A-Za-z0-9._/-]+"),                             # branches
          re.compile(r"(?<![\w/:.~-])/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)+"))  # absolute paths


def _dir():
    return os.path.join(sl.home_state(), "relay")


def _path(old):
    if not isinstance(old, str) or not _SID.match(old):
        raise sl.StudioError("not a session id: %r" % (old,))
    return os.path.join(_dir(), old + ".json")


def load(old):
    try:
        with open(_path(old), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError, sl.StudioError):
        return None
    return rec if isinstance(rec, dict) and rec.get("state") in STATES else None


def _save(rec):
    os.makedirs(_dir(), exist_ok=True)
    rec["at"] = sl.now()
    sl.atomic_write(_path(rec["old"]), json.dumps(rec, ensure_ascii=False, indent=1) + "\n")


def facts(text):
    """What the successor must restate: every R-n/I-n id, commit, claude/ branch and absolute path, in text order."""
    found = []
    for pattern in _FACTS:
        for m in pattern.finditer(text):
            fact = m.group(0).rstrip(".,;:)/")
            if fact and fact not in found:
                found.append(fact)
    return found


def said(fact, text):
    """`fact` stands in `text` as a whole token: R-1 is not said by R-12, nor claude/foo by claude/foo-bar, nor /tmp/a
    by C:/tmp/a (a path also needs no drive colon before it)."""
    before = r"[\w/.~:-]" if fact.startswith("/") else r"[\w/.~-]"
    return re.search(r"(?<!%s)%s(?![\w/~-]|\.\w)" % (before, re.escape(fact)), text) is not None


def _read(path):
    with open(path, encoding="utf-8") as f:
        text = f.read(_MAX_BYTES + 1)
    if len(text) > _MAX_BYTES:
        raise sl.StudioError("%s is longer than %d bytes" % (path, _MAX_BYTES))
    if not text.strip():
        raise sl.StudioError("%s is empty" % path)
    return text


def _session(session):
    if not session or not _SID.match(session):
        raise sl.StudioError("no session id (CLAUDE_CODE_SESSION_ID): run this inside the chat")
    return session


def handoff(session, path, title=None, app_id=None):
    """The old chat records what it was doing. Refused when this chat already runs the installed version."""
    old = _session(session)
    if not isinstance(app_id, str) or not _APP_ID.match(app_id):
        raise sl.StudioError("--app-id local_… is required: the app archives this chat by that id, and the archive "
                             "guard can only hold it back when it knows the id")
    running, installed = updatelib.running_version(), updatelib.installed_version()
    if not updatelib.older(running, installed):
        raise sl.StudioError("this chat runs %s, the installed Budgit is %s: nothing to renew" % (running, installed))
    prev = load(old)
    if prev and prev["state"] in ("echoed", "done"):
        raise sl.StudioError("%s was already handed to %s (%s)" % (old, prev.get("new"), prev["state"]))
    text = _read(path)
    rec = {"old": old, "app_id": app_id, "title": title or None, "running": running,
           "installed": installed, "state": "handoff", "handoff": text, "facts": facts(text), "new": None,
           "reason": None}
    _save(rec)
    return rec


def echo(session, old, path):
    """The successor restates the handoff. Refused unless it runs the installed version and names every fact."""
    new = _session(session)
    rec = load(old)
    if not rec:
        raise sl.StudioError("no handoff for %s" % old)
    if new == old:
        raise sl.StudioError("the echo must come from the new chat, not from %s" % old)
    if rec["state"] != "handoff":
        raise sl.StudioError("%s is %s, not waiting for an echo" % (old, rec["state"]))
    running, installed = updatelib.running_version(), updatelib.installed_version()
    if not running or running != installed:
        raise sl.StudioError("this chat runs %s, not the installed %s: open the successor as a new chat" %
                             (running, installed))
    text = _read(path)
    missing = [f for f in rec["facts"] if not said(f, text)]
    if missing:
        raise sl.StudioError("the echo misses: %s" % ", ".join(missing))
    rec.update(state="echoed", new=new)
    _save(rec)
    return rec


def done(session, old):
    """The successor archived the old chat."""
    rec = load(old)
    if not rec or rec["state"] != "echoed":
        raise sl.StudioError("%s is %s: done only after the echo" % (old, rec["state"] if rec else "unknown"))
    if session and session != rec["new"]:
        raise sl.StudioError("only %s, which echoed the handoff, marks it done" % rec["new"])
    rec["state"] = "done"
    _save(rec)
    return rec


def fail(old, reason):
    """Any step failed: the old chat stays as it is and is never archived for this relay."""
    rec = load(old)
    if not rec:
        raise sl.StudioError("no handoff for %s" % old)
    if rec["state"] == "done":
        raise sl.StudioError("%s is already done" % old)
    if not (reason or "").strip():
        raise sl.StudioError("--reason is required")
    rec.update(state="failed", reason=" ".join(reason.split()))
    _save(rec)
    return rec


def _names():
    try:
        return sorted(n[:-5] for n in os.listdir(_dir()) if n.endswith(".json"))
    except OSError:
        return []


def records():
    return [r for r in (load(n) for n in _names()) if r]


def broken():
    """Old-chat ids whose record exists but cannot be read."""
    return [n for n in _names() if not load(n)]


def reset(old):
    """Remove an unreadable record so archiving works again. A readable one is never cleared here: it holds its chat."""
    path = _path(old)
    if not os.path.exists(path):
        raise sl.StudioError("no relay record for %s" % old)
    if load(old):
        raise sl.StudioError("the record for %s is readable (%s); only an unreadable one is reset" %
                             (old, load(old)["state"]))
    os.remove(path)


def archive_block(caller, target):
    """The archive guard: a reason to deny archiving `target`, or None. `target` is an app id (local_…) or "self";
    a chat with no relay record gets no opinion."""
    olds = [caller] if target == "self" else _names()
    broken = [n for n in olds if n and os.path.exists(os.path.join(_dir(), n + ".json")) and not load(n)]
    if broken:                          # fail closed: an unreadable record may be the one that holds this chat back
        return ("budgit: the renewal record %s is unreadable, so no chat is archived. Clear it with "
                "`budgit relay reset %s`." % (os.path.join(_dir(), broken[0] + ".json"), broken[0]))
    if target == "self":
        rec = load(caller) if caller else None
    else:
        rec = next((r for r in records() if target in (r["old"], r.get("app_id"))), None)
    if not rec or rec["state"] in ("echoed", "done"):
        return None
    if rec["state"] == "failed":
        return "budgit: this chat's renewal failed (%s); it stays as it is and is not archived." % rec.get("reason")
    return ("budgit: this chat is archived only after the new chat echoed its handoff "
            "(`budgit relay echo %s --file F` from the new chat)." % rec["old"])


def shown(session, version=None):
    """True when the renewal block was already given to `session` for this installed version; marks it otherwise."""
    if not session or not _SID.match(session):
        return True
    version = version or updatelib.installed_version()
    path = os.path.join(_dir(), session + ".shown")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read().strip() == version:
                return True
    except OSError:
        pass
    os.makedirs(_dir(), exist_ok=True)
    sl.atomic_write(path, "%s\n" % version)
    return False
