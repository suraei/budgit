"""R-283 S3: the project card, `<data folder>/private/card.json`: what the portfolio may know about a project without
reading its private text. The ledger writer (`studiolib.save_ledger`) rewrites it on every ledger change.

It holds counts, ids, ages, versions and times, and nothing else: no item text, no file name, no branch name. It lives
under `private/` because some projects track their ledger in git (`tracks_ledger`), and the card must not travel with it.
The versions are stamped by the upgrade (u4), never guessed here; a rewrite keeps whatever the last stamp said.
The R-286 fields (`priority`, stuck count, free slots) and the pending picks have no source yet: the card does not
carry them until the code that computes them exists."""
import json
import os
import re
import time

import studiolib as sl

VERSION = 1
ID = re.compile(r"^R-\d{1,6}$")
KEEP = ("budgit_version", "rules_version")             # stamped by the upgrade, kept across a rewrite
STALE_S = 1                                            # a card written before the ledger by more than this is old


def path(project):
    return os.path.join(sl.studio_dir(project), "private", "card.json")


def _age_days(stamp, at):
    epoch = sl._epoch(stamp)
    return None if epoch is None else max(0, int((at - epoch) // 86400))


def build(project, data, previous=None, at=None):
    """The card as a dict for a loaded ledger `data`. Ids, ages and counts only."""
    at = at if at is not None else time.time()
    previous = previous or {}
    counts = {s: 0 for s in sl.STATUSES}
    doing = []
    for it in data.get("items", []):
        status = it.get("status")
        if status in counts:
            counts[status] += 1
        if status == "doing" and ID.fullmatch(str(it.get("id"))):
            doing.append({"id": it["id"],
                          "age_days": _age_days(it.get("updated") or it.get("created"), at)})
    counts["later"] = sum(1 for it in data.get("items", []) if it.get("status") == "open" and it.get("phase") == "later")
    card = {"version": VERSION, "written_at": sl.now(), "counts": counts, "doing": doing,
            "stuck": len(sl.stale_doing(data, at))}      # one total, the ledger's own rule: both views show the same number
    for key in KEEP:
        card[key] = previous.get(key)
    return card


def read(project):
    """The card as written, or None when it is missing or unreadable."""
    try:
        card = json.loads(sl.read_text(path(project)))
    except (OSError, ValueError):
        return None
    return card if isinstance(card, dict) and card.get("version") == VERSION else None


def ignore_private(project):
    """The card is machine-local: keep `<data folder>/private/` out of git for a project that is in a git tree."""
    if not sl.in_git_tree(project):
        return
    line = os.path.basename(sl.studio_dir(project)) + "/private/"
    gi = os.path.join(project, ".gitignore")
    existing = sl.read_lenient(gi) if os.path.exists(gi) else ""
    if line not in existing.splitlines():
        sl.append_text(gi, ("\n" if existing and not existing.endswith("\n") else "") + line + "\n")


def write(project, data):
    """Rewrite the card for a ledger that was just saved. Best effort: a card that cannot be written never costs the ledger
    write. Returns True when written."""
    try:
        card = build(project, data, read(project))
        target = path(project)
        sl.ensure_subdir(project, "private")
        ignore_private(project)
        sl.atomic_write(target, json.dumps(card, ensure_ascii=False, indent=2) + "\n")
        return True
    except Exception:
        sl.log_error("project card")
        return False


def stamp(project, **versions):
    """u4: record `budgit_version` / `rules_version` in the card (the rest is rebuilt from the ledger)."""
    card = None
    try:
        card = read(project)
        data = sl.load_ledger(project)
        card = build(project, data, card)
        for key in KEEP:
            if key in versions:
                card[key] = versions[key]
        target = path(project)
        sl.ensure_subdir(project, "private")
        ignore_private(project)
        sl.atomic_write(target, json.dumps(card, ensure_ascii=False, indent=2) + "\n")
        return True
    except Exception:
        sl.log_error("project card stamp")
        return False


def state(project, at=None):
    """('ok', age_days) | ('old', age_days) | ('missing', None): `old` when the ledger changed after the card was written."""
    at = at if at is not None else time.time()
    card = read(project)
    if card is None:
        return "missing", None
    written = sl._epoch(card.get("written_at"))
    if written is None:
        return "missing", None
    age = max(0, int((at - written) // 86400))
    try:
        ledger_mtime = os.path.getmtime(sl._ledger_path(project))
    except OSError:
        return "ok", age
    return ("old" if ledger_mtime - written > STALE_S else "ok"), age


def counts_view(project):
    """What a protected row may show: the card's counts as the portfolio's task numbers, or None."""
    card = read(project)
    if card is None:
        return None
    c = card.get("counts") or {}
    return {"doing": c.get("doing", 0), "next": c.get("open", 0) - c.get("later", 0), "later": c.get("later", 0),
            "done": c.get("done", 0) + c.get("verified", 0)}
