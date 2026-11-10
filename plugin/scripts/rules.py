"""R-788 step 1-4: team rules per role. Shipped text in plugin/rules/*.md, a digest per role, full topic text on demand.

State (setup-answers.json) lives in postupdate.py. A replacement topic is read ONLY from <Budgit home>/rules.d, never
from a project folder. Digest budgets (characters / 4): plain chat 150, worker 350, lead 600, coordinator 800."""
import json
import os
import re

import studiolib as sl

DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules"))
BUDGETS = {"plain": 150, "worker": 350, "lead": 600, "coordinator": 800}
BEGIN = "<!-- budgit-rules:begin -->"
END = "<!-- budgit-rules:end -->"
_HEAD = {"plain": "Budgit rules (core)", "worker": "Budgit rules (worker)", "lead": "Budgit rules (project lead)",
         "coordinator": "Budgit rules (coordinator)"}
_ROLE_TAGS = {"plain": ("core",), "worker": ("core", "worker"), "lead": ("core", "lead"),
              "coordinator": ("core", "coordinator")}


def tokens(text):
    return (len(text) + 3) // 4


def topics():
    with open(os.path.join(DIR, "index.json"), encoding="utf-8") as f:
        return json.load(f)["topics"]


def topic_ids():
    return [t["id"] for t in topics()]


def _find(topic_id):
    for t in topics():
        if t["id"] == topic_id:
            return t
    raise sl.StudioError("no rules topic %r (budgit rules list)" % topic_id)


def rules_d():
    return os.path.join(sl.home_state(), "rules.d")        # the Budgit home only, never a project folder


def _replacement(topic_id):
    path = os.path.join(rules_d(), topic_id + ".md")
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def shipped_text(topic_id):
    t = _find(topic_id)
    with open(os.path.join(DIR, t["file"]), encoding="utf-8") as f:
        lines = f.read().split("\n")
    out, on = [], False
    for line in lines:
        if line.startswith("## "):
            on = line.startswith("## %d. " % t["n"])
        if on:
            out.append(line)
    return "\n".join(out).strip() + "\n"


def text(topic_id, shipped_only=False):
    rep = None if shipped_only else _replacement(topic_id)
    return rep if rep is not None else shipped_text(topic_id)


def _digest_of(t, shipped_only):
    rep = None if shipped_only else _replacement(t["id"])
    if rep is None:
        return t["digest"]
    bullets = " ".join(x.lstrip("- ").strip() for x in rep.splitlines() if x.startswith("- "))
    return (bullets or "your own text") [:100] +(" (your edit)" if bullets else "")


DIGEST_CAP = 80


def digest(role, off=(), shipped_only=False):
    """The digest for plain|worker|lead|coordinator. `off` topics are omitted; edits replace the digest line."""
    tags = _ROLE_TAGS[role]
    lines = [_HEAD[role] + "; full text: budgit rules show <topic>"]
    for t in topics():
        if t["id"] in off or not any(r in tags for r in t["roles"]):
            continue
        d = _digest_of(t, shipped_only)
        lines.append("- %s: %s" % (t["id"], d if len(d) <= DIGEST_CAP else d[:DIGEST_CAP - 1].rstrip() + "…"))   # R-849: `budgit rules show` has the rest
    return "\n".join(lines)


def bake(agent_text, block):
    """The worker digest between markers, replacing any block already there: baking twice is byte-identical."""
    clean = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", agent_text, flags=re.S).rstrip("\n") + "\n"
    return clean + "\n" + BEGIN + "\n" + block.strip("\n") + "\n" + END + "\n"


def worker_block():
    return digest("worker", shipped_only=True)


def edit(topic_id):
    """Copy the shipped topic to rules.d so the user can change it; an existing replacement is kept. Returns the path."""
    _find(topic_id)
    os.makedirs(rules_d(), mode=0o700, exist_ok=True)
    path = os.path.join(rules_d(), topic_id + ".md")
    if os.path.islink(path):
        raise sl.StudioError("%s is a link; not writing" % path)
    if not os.path.exists(path):
        sl.atomic_write(path, shipped_text(topic_id))
    return path
