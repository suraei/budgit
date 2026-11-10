"""R-849: an Agent spawn of an all-tools type starts at 65-79k fixed context (Budgit agents at 21-27k), so it is refused
unless the prompt carries a line `ALL-TOOLS: <reason>`. Pure function of the payload; the hook decides when it applies."""
import re

import studiolib as sl

INSTEAD = ("builder for code and fixes, verifier for verification, researcher for research, plan-critic for plans, "
           "security-reviewer for security, scout-reader for reading")
_LINE = re.compile(r"(?m)^\s*ALL-TOOLS:\s*\S")
_DASH = re.compile(r"^(?:budgit|studio)-(.+)$")


def is_budgit_agent(kind):
    """True only for a canonical Budgit agent: `budgit:X` or `studio:X` (X in AGENT_NAMES), or the cloud `budgit-X` /
    `studio-X` copies. Anything else (other plugins, odd suffixes, other case) is an all-tools type."""
    if not isinstance(kind, str):
        return False
    canon = sl.canon_agent(kind)
    if canon.startswith("budgit:") and canon[len("budgit:"):] in sl.AGENT_NAMES:
        return True
    m = _DASH.match(kind)
    return bool(m and m.group(1) in sl.AGENT_NAMES)


def has_reason(prompt):
    return bool(_LINE.search(str(prompt or "")))


def deny_reason(tool_input):
    """None when the spawn may go ahead, else the deny text. A payload that is not a dict is denied (never fail open)."""
    if not isinstance(tool_input, dict):
        kind = "unknown"
    else:
        kind = tool_input.get("subagent_type") or "general-purpose"   # no type means general-purpose
        if is_budgit_agent(kind) or has_reason(tool_input.get("prompt")):
            return None
    return ("budgit token guard: %s is an all-tools agent (65-79k tokens of fixed context per spawn). Use a Budgit agent "
            "instead (%s). If an all-tools type is really needed, add a line "
            "`ALL-TOOLS: <reason>` to the prompt." % (str(kind)[:60], INSTEAD))
