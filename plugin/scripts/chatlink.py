"""R-239: from the 12-character session prefix the Brote channel carries to the link of that chat in the desktop app.

Read-only and local: it reads the app's own session records fresh on every call (a resumed chat can change its CLI id), takes
only three keys from each, and builds the link from a record id that matches a strict shape, never from file text."""
import glob
import json
import os
import re

_PREFIX = re.compile(r"^[A-Za-z0-9-]{8,36}$")
_LOCAL = re.compile(r"^local_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MAX_BYTES = 65536
LINK = "claude://claude.ai/epitaxy/"


def valid_prefix(prefix):
    return isinstance(prefix, str) and bool(_PREFIX.match(prefix))


def sessions_dir(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Library", "Application Support", "Claude", "claude-code-sessions")


def _records(home):
    for path in sorted(glob.glob(os.path.join(sessions_dir(home), "*", "*", "local_*.json"))):
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                continue
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            yield data


def layout_problem(home=None):
    """None when the app's session records look as expected, else a short reason. The folder layout belongs to the desktop app and may
    change, so a missing or unreadable folder, or records without the keys we read, are said out loud instead of looking like 'no chat'."""
    folder = sessions_dir(home)
    if not os.path.isdir(folder) or not os.access(folder, os.R_OK):
        return "folder"
    seen = 0
    for rec in _records(home):
        seen += 1
        if isinstance(rec.get("cliSessionId"), str) and isinstance(rec.get("sessionId"), str):
            return None
    return "records" if seen else None


def chat_link(prefix, home=None):
    """The link of the one live chat whose CLI session id starts with `prefix`; None for no match, several, or a bad prefix."""
    if not valid_prefix(prefix):
        return None
    ids = {rec["sessionId"] for rec in _records(home)
           if isinstance(rec.get("cliSessionId"), str) and rec["cliSessionId"].startswith(prefix)
           and not rec.get("isArchived") and isinstance(rec.get("sessionId"), str) and _LOCAL.match(rec["sessionId"])}
    return LINK + ids.pop() if len(ids) == 1 else None
