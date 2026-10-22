"""Per-subagent tool-call ceiling (R-63). Briefs said "~60 calls" and agents ran 186-220; the hook
now counts every tool call a subagent makes (hook input carries agent_id only inside subagents). Zero model tokens.
cap-5 -> a note to wrap up; past cap+20 -> every tool but Write is denied so the agent saves its report and ends."""
import os
import re

import studiolib as sl

SOFT_MARGIN, HARD_MARGIN = 5, 20


def _path(agent_id):
    return os.path.join(sl.home_state(), "agent-calls", re.sub(r"[^A-Za-z0-9_-]", "_", str(agent_id))[:64])


def _bump(agent_id):
    path = _path(agent_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            n = int(f.read().strip() or 0)
    except (OSError, ValueError):
        n = 0
    n += 1
    with open(path, "w") as f:
        f.write(str(n))
    return n


def reset(agent_id):
    try:
        os.remove(_path(agent_id))
    except OSError:
        pass


def check(inp, cap):
    """None, ("note", text) or ("deny", reason) for this tool call."""
    agent_id = inp.get("agent_id")
    if not agent_id:
        return None
    n = _bump(agent_id)
    soft, hard = cap - SOFT_MARGIN, cap + HARD_MARGIN
    if n > hard and inp.get("tool_name") != "Write":
        return ("deny", "budgit: tool-call ceiling reached (%d calls, budget ~%d): stop now — write your final "
                        "report (done, evidence, what is left) as your last message; Write is still allowed to "
                        "save a report file." % (n - 1, cap))
    if n == soft or (n > soft and (n - soft) % 10 == 0 and n <= hard):
        return ("note", "budgit: %d tool calls (budget ~%d, hard stop at %d): commit/save what you have now and "
                        "report what is left instead of starting new work." % (n, cap, hard))
    return None
