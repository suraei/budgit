"""Stop a tool call that follows English prose in a Spanish chat (PreToolUse, main session).
Reads only the tail of the transcript, reuses the humanize detector's masks and word lists, and fails open."""
import json
import os
import re

import humanizelint as hl
import i18n

TAIL = 131072
MIN_WORDS = 12
REASON = "budgit: write it in Spanish or in progress.md"
_PROGRESS = re.compile(r"^progress[^/\\]*\.md$", re.I)
_SKIP = re.compile(r"\"[^\"\n]*\"|«[^»\n]*»|(?:~|\.{1,2})?/[\w.~/-]+|\b[RI]-\d+\b|\b[0-9a-f]{7,40}\b")
_MARKS = re.compile(r"[áéíóúñüÁÉÍÓÚÑÜ¿¡]")
_WORD = re.compile(r"[A-Za-z']+")
_ROWS = re.compile(r"^[^\n]*\|[^\n]*$", re.M)      # table rows quote cells; list items are NOT masked (English bullets stop)
_ES = re.compile(hl.ES_WORDS.pattern + r"|\bhecho\b", re.I)


def _last_text(path):
    """The last assistant text block in the tail of the transcript; None when a new user prompt came after it."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - TAIL))
        lines = f.read().decode("utf-8", "replace").splitlines()
    if size > TAIL:
        lines = lines[1:]          # the first line is cut in half
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            msg = entry["message"]
            role, content = msg.get("role") or entry.get("type"), msg.get("content")
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if role == "user" and text.strip():
            return None
        if role == "assistant" and text.strip():
            return text
    return None


def _english_prose(text):
    plain = _SKIP.sub(" ", _ROWS.sub(" ", hl.mask(text)))     # cells and list items quote titles and labels
    if len(_WORD.findall(plain)) < MIN_WORDS or _MARKS.search(plain) or _ES.search(plain):
        return False
    return bool(hl.EN_WORDS.search(plain))


def english_stop(inp):
    """-> the reason to deny the call, or None (every doubt and every error: None)."""
    try:
        tool_input = inp.get("tool_input") or {}
        if inp.get("tool_name") in ("Write", "Edit", "MultiEdit") and _PROGRESS.match(
                os.path.basename(str(tool_input.get("file_path", "")))):
            return None
        if i18n.detect() != "es" or not isinstance(inp.get("transcript_path"), str):
            return None
        text = _last_text(inp["transcript_path"])
        return REASON if text and _english_prose(text) else None
    except Exception:
        return None
