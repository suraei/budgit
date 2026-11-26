"""R-1178: a coordinator (TM or PM main session) answers the user first and delegates the work.
Two checks, both hook-side and free of model tokens:
- a warning once per user prompt when the coordinator has made N tool calls (default 6, config key `reply_first_calls`)
  since the prompt (it never refuses);
- a refusal of browser clicks, typing and form filling from a coordinator main session: those go to a background agent.
Subagents (agent_id or agent_type present) and sessions with no coordinator role are never touched."""
import json
import os
import re

import studiolib as sl

DEFAULT_CALLS = 6
BROWSER_PREFIXES = ("mcp__Claude_Browser__", "mcp__claude-in-chrome__")
BROWSER_ACTIONS = ("left_click", "right_click", "double_click", "triple_click", "type", "key", "left_click_drag")
COORDINATORS = ("TM", "PM")


def limit():
    """The warning threshold: config.json `reply_first_calls` when it is a whole number from 1 up, else 6."""
    value = sl._home_config().get("reply_first_calls")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return DEFAULT_CALLS


def _path(session_id):
    return os.path.join(sl.home_state(), "reply-first", re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id))[:64])


def _read(session_id):
    try:
        with open(_path(session_id), encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("n") or 0), bool(data.get("warned"))
    except (OSError, ValueError, AttributeError, TypeError):
        return 0, False


def _write(session_id, n, warned):
    path = _path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sl.atomic_write(path, json.dumps({"n": n, "warned": warned}) + "\n")


def on_prompt(session_id):
    """A new user prompt: the count and the once-flag start again."""
    if not sl.valid_session(session_id):
        return
    try:
        os.remove(_path(session_id))
    except OSError:
        pass


def is_coordinator(inp, project):
    """True for a main session whose role is TM or PM AND that is registered as such (the portfolio names it as the TM or as a
    project's PM). roles.classify also calls an unregistered top-level session a PM; a lane opened by hand is not refused."""
    if not isinstance(inp, dict) or inp.get("agent_id") or inp.get("agent_type"):
        return False
    try:
        import roles
        sid = inp.get("session_id")
        tm, entries = roles._entries()
        if not sid or not (tm == sid or any(e.get("pm") == sid for e in entries)):
            return False
        info = roles.detect(sid, project)
    except Exception:
        return False
    return bool(info) and info.get("role") in COORDINATORS


def on_call(inp, project, lang=None):
    """PreToolUse: -> the warning text on the call that reaches the limit, once per prompt, else None. Fails open."""
    try:
        if not sl.valid_session(inp.get("session_id")) or not is_coordinator(inp, project):
            return None
        n, warned = _read(inp["session_id"])
        n += 1
        fire = n >= limit() and not warned
        _write(inp["session_id"], n, warned or fire)
        if not fire:
            return None
        import i18n
        return i18n.ask("replyfirst.warn", lang, n=n)
    except Exception:
        return None


def is_browser_action(inp):
    """True for a browser click, type, key, drag or form fill (reads and screenshots are not)."""
    tool = inp.get("tool_name")
    if not isinstance(tool, str) or not tool.startswith(BROWSER_PREFIXES):
        return False
    name = tool.split("__")[-1]
    if name in ("form_input", "javascript_tool"):      # page script can click and type too
        return True
    if name == "computer":
        action = (inp.get("tool_input") or {}).get("action")
        return action in BROWSER_ACTIONS
    return False


def browser_refusal(inp, project, lang=None):
    """PreToolUse: -> the refusal text for a coordinator's browser click, else None."""
    try:
        if not is_browser_action(inp) or not is_coordinator(inp, project):
            return None
        import i18n
        return i18n.ask("replyfirst.browser", lang)
    except Exception:
        return None
