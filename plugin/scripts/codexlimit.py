"""R-853 (and R-881): remember when Codex says its usage limit is hit, so nothing asks for Codex until it returns.

One small record, `codex-limit.json`, in the Budgit state folder (studiolib.home_state). `until` is a Unix time. Dates come
from Python only (datetime, os.path), so it behaves the same on macOS, Linux and Windows. The clock is a parameter
(`now`), never an environment switch."""
import datetime
import json
import math
import os
import re
import time

import studiolib as sl

BACKOFF_S = 24 * 3600    # when Codex names no retry date: ask again after a day
MAX_AHEAD_S = 90 * 24 * 3600     # a recorded return date is never more than 90 days away
# Codex's own error line only: "You've hit your usage limit..." (straight or curly apostrophe), optionally after an "ERROR:"
# prefix, at the start of a line. A prompt or diff echoed back that merely mentions "usage limit" does not match.
LIMIT_RE = re.compile(r"^[ \t]*(?:error:[ \t]*)?you[’']ve hit your usage limit", re.I | re.M)
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_DATE_RE = re.compile(r"(?:try again|retry)[^\n]*?\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})"
                      r"(?:\s*(?:at\s*)?(\d{1,2}):(\d{2})\s*([AaPp][Mm])?)?", re.I)
FALLBACK_LINE = ("no external reviewer is available: use a fresh-context reviewer on another tier, given raw evidence only "
                 "and told to refute; its independence is weaker than Codex or Gemini")


def state_path():
    return os.path.join(sl.home_state(), "codex-limit.json")


def is_limit(text):
    return bool(text and LIMIT_RE.search(text))


def parse_retry(text):
    """-> Unix time of the retry date in `text` (local time), or None when there is none."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].lower())
    if not month:
        return None
    hour = minute = 0
    if m.group(4):
        hour, minute = int(m.group(4)), int(m.group(5))
        ampm = (m.group(6) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    try:
        return datetime.datetime(int(m.group(3)), month, int(m.group(2)), hour, minute).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def record(text, now=None):
    """Store the retry time found in `text` (a date in the past or none: now + 24 h). -> the stored Unix time."""
    now = time.time() if now is None else now
    until = parse_retry(text)
    if until is None or until <= now:
        until = now + BACKOFF_S
    until = min(until, now + MAX_AHEAD_S)
    try:
        os.makedirs(sl.home_state(), exist_ok=True)
        sl.atomic_write(state_path(), json.dumps({"until": until}) + "\n")
    except OSError:
        pass          # a read-only home: the limit is still reported to the caller, only not remembered
    return until


def blocked_until(now=None):
    """-> the Unix time Codex returns, or None when it is not known to be limited right now."""
    now = time.time() if now is None else now
    path = state_path()
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        until = float(json.loads(sl.read_text(path)).get("until"))
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return None
    if not math.isfinite(until):
        return None
    return min(until, now + MAX_AHEAD_S) if until > now else None


def protected(project):
    """True when `project` is a protected folder, or the protected list cannot be read (then nothing is known safe)."""
    return bool(project) and (bool(sl.protected_dir(project)) or not sl.protected_dirs_readable())


def when(until):
    """The retry time as a local date, for lines the user reads."""
    return datetime.datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
