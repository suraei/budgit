"""R-1137 item 1: the SessionStart budget, in ONE place. Every source of a session's start text (the role card, the rules
digest, the budget text, the project summary, the extras) is handed to fit() as a section; fit() returns the one
additionalContext that fits the role's lines AND bytes. Claude Code saves a SessionStart context over 10 KB to a file
(persisted-output); no role's budget is near that.

Priority, highest first (a section is only ever replaced by ITS pointer line, never dropped without one):
  1. the role line and the hard-safety lines, in full (tag "role", "fence");
  2. the pointer lines (TRAILER, always shown; a section's own pointer takes the place of its text);
  3. the project summary heads (project, MVP, the open-items count);
  4. everything else, as pointers: the higher a section's rank, the earlier it becomes a pointer.
"""

# role -> (lines, bytes). A session with no known role takes the lane row.
TABLE = {"subagent": (15, 4000), "lane": (25, 4000), "PM": (40, 4000), "TM": (60, 4000)}
DEFAULT_ROLE = "lane"

TRAILER = (("rules", "budgit rules show <topic>"), ("project state", "budgit status"),
           ("open items", "budgit ledger list --open"))

# tag -> (rank, pointer or None). rank None: the section never degrades. A higher rank degrades first. A pointer of None
# means the TRAILER already covers it.
TAGS = {
    "role": (None, None),                 # role line, hard safety, the rules-file path, a protected-folder warning
    "fence": (None, None),                # BEGIN/END UNTRUSTED PROJECT DATA
    "summary.head": (None, None),         # project, MVP, open-items count
    "tmnotes": (None, None),              # R-1343 F-2: unread TM notes in their own frame, at most 5 lines + 1; never degraded
    "summary.action": (28, ("a round cap or a decision waits", "budgit status")),
    "summary.untracked": (26, ("input of this session not yet in the ledger", "budgit status")),
    "action": (27, ("a checkpoint, resume or decision waits", "budgit status")),
    "role.rules": (35, ("more role rules", "budgit rules show <topic>")),         # the role's own lines
    "role.common": (38, ("common team rules", "budgit rules show <topic>")),     # the lines every role shares
    "decisions": (30, ("TM decisions wait in your relay file", "budgit status")),   # degrades after notice (40) and before summary.action (28)
    "notice": (40, ("notices and setup questions", "budgit status")),
    "summary.itemlist": (50, ("open items", "budgit ledger list --open")),
    "summary.progress": (52, ("last checkpoint", "budgit status")),
    "summary.reports": (53, ("saved agent reports", "budgit status")),
    "summary.extra": (33, ("more project notes", "budgit status")),   # a data-folder conflict, the brain pointer, the once-a-day stand-up: few lines, shown once, so kept long
    "budget": (58, ("model budget", "budgit budget")),
    "rules": (60, ("team rules digest", "budgit rules show <topic>")),
    "summary.cli": (62, None),
    "other": (45, ("more context", "budgit status")),
}
CLAMPS = (240, 200, 160, 120)       # last resort for bytes: a line longer than this is cut and the cut is marked with an ellipsis


def section(tag, text, short=None):
    return {"tag": tag if tag in TAGS else "other", "text": text, "short": short}


def _pointer_line(p):
    return "%s: %s" % p


def _levels(s):
    rank, pointer = TAGS[s["tag"]]
    levels = [s["text"].split("\n")]
    if rank is None:
        return levels
    if s["short"]:
        levels.append(s["short"].split("\n"))
    levels.append([_pointer_line(pointer)] if pointer else [])
    return levels


def _render(secs, trailer=True):
    out, shown = [], set()
    for s in secs:
        chunk = s["levels"][s["level"]]
        if s["level"] and s["level"] == len(s["levels"]) - 1 and s["rank"] is not None:
            chunk = [l for l in chunk if l not in shown]        # the same pointer twice is shown once
            shown.update(chunk)
        out.extend(chunk)
    if trailer:
        out.extend(l for l in (_pointer_line(p) for p in TRAILER) if l not in shown and l not in out)
    return out


def _size(lines):
    return len("\n".join(lines).encode("utf-8"))


def fit(role, sections):
    """sections: [section(tag, text, short)] in emit order. -> (text, report). The text has at most the role's lines and
    bytes. report: {"role", "lines", "bytes", "pointers": [tags turned into a pointer], "short": [tags shortened]}."""
    max_lines, max_bytes = TABLE.get(role) or TABLE[DEFAULT_ROLE]
    secs = []
    for s in sections:
        if not s.get("text"):
            continue
        rank = TAGS[s["tag"]][0]
        secs.append({"tag": s["tag"], "rank": rank, "levels": _levels(s), "level": 0})

    def over(lines):
        return len(lines) > max_lines or _size(lines) > max_bytes
    lines = _render(secs)
    while over(lines):
        cand = [i for i, s in enumerate(secs) if s["rank"] is not None and s["level"] < len(s["levels"]) - 1]
        if not cand:
            break
        i = max(cand, key=lambda k: (secs[k]["rank"], k))
        secs[i]["level"] += 1
        lines = _render(secs)
    if over(lines):                                    # last resort: every pointer in ONE line
        cmds = []
        for s in secs:
            if s["rank"] is not None and s["level"] == len(s["levels"]) - 1 and s["levels"][s["level"]]:
                cmds.append(s["levels"][s["level"]][0].split(": ", 1)[-1])
        cmds += [p[1] for p in TRAILER]
        uniq = list(dict.fromkeys(cmds))
        keep = []
        for s in secs:
            if s["rank"] is None or s["level"] < len(s["levels"]) - 1:
                keep.extend(s["levels"][s["level"]])
        lines = keep + ["more: " + " | ".join(uniq)]
    for width in CLAMPS:
        if not over(lines):
            break
        lines = [l if len(l) <= width else l[:width - 1].rstrip() + "…" for l in lines]
    report = {"role": role if role in TABLE else DEFAULT_ROLE, "lines": len(lines), "bytes": _size(lines),
              "pointers": [s["tag"] for s in secs if s["rank"] is not None and s["level"] == len(s["levels"]) - 1],
              "short": [s["tag"] for s in secs if 0 < s["level"] < len(s["levels"]) - 1]}
    return "\n".join(lines), report
