"""Score the shape of a message to the user (R-1112 phase 0).

Stdlib only, Python 3.9-safe, language-neutral (no word lists).
The thresholds below mirror CLAUDE.md "Message shape": about 6 short lines,
no tables, lists of 5 or fewer, no file/row/code names, one step for the user,
no headings.
"""

import re

# Thresholds: see CLAUDE.md "Message shape" (13 Nov 2026, I-1649, I-1654, R-1112).
MAX_LINES = 8          # "about 6 short lines", with a little slack
LONG_LINE = 120        # characters; a line over this is long
MAX_LIST = 5           # "Lists of 5 or fewer"
MAX_CODE_NAMES = 0     # "No file, row or code names"
MAX_STEPS = 1          # "One step per message"
MAX_HEADINGS = 0       # no headings in a short message

_FENCE = re.compile(r"^\s*(```|~~~)\s*([A-Za-z0-9_+-]*)")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+\S")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_SHELL_LANGS = ("bash", "sh", "zsh", "shell", "console")

_EXT = (r"py|md|json|jsonl|sh|txt|yml|yaml|toml|js|ts|tsx|html|css|csv|sql|"
        r"lock|log|plist|xlsx|pdf|sha256")
# Order matters: matched spans are masked so nothing counts twice.
_NAME_PATTERNS = [
    re.compile(r"`[^`\n]+`"),
    re.compile(r"(?<![\w])(?:~|\.{1,2})?/[\w.\-@]+(?:/[\w.\-@]+)+"),
    re.compile(r"\b[\w\-]+(?:/[\w.\-]+)+\.(?:%s)\b" % _EXT),
    re.compile(r"\b[RI]-\d+\b"),
    re.compile(r"\b[\w\-]+\.(?:%s)\b" % _EXT),
]


def _split(text):
    """Return (prose lines outside fences, number of shell fences, all non-empty lines)."""
    prose = []
    shell_blocks = 0
    nonempty = []
    in_fence = False
    for raw in text.splitlines():
        m = _FENCE.match(raw)
        if m:
            if not in_fence:
                in_fence = True
                if m.group(2).lower() in _SHELL_LANGS:
                    shell_blocks += 1
            else:
                in_fence = False
            continue
        if raw.strip():
            nonempty.append(raw)
            if not in_fence:
                prose.append(raw)
    return prose, shell_blocks, nonempty


def _count_names(text):
    count = 0
    masked = text
    for pat in _NAME_PATTERNS:
        found = pat.findall(masked)
        count += len(found)
        masked = pat.sub(" ", masked)
    return count


def score(text):
    text = text or ""
    prose, shell_blocks, nonempty = _split(text)
    lines = len(nonempty)
    long_lines = sum(1 for ln in nonempty if len(ln.strip()) > LONG_LINE)
    table = any(_TABLE.match(ln) for ln in prose)
    max_list = run = 0
    for ln in prose:
        if _LIST.match(ln):
            run += 1
            max_list = max(max_list, run)
        else:
            run = 0
    code_names = _count_names(text)
    steps = shell_blocks + sum(1 for ln in prose if _NUMBERED.match(ln))
    headings = sum(1 for ln in prose if _HEADING.match(ln))
    ok = (lines <= MAX_LINES and not table and max_list <= MAX_LIST
          and code_names <= MAX_CODE_NAMES and steps <= MAX_STEPS
          and headings <= MAX_HEADINGS)
    return {
        "lines": lines,
        "long_lines": long_lines,
        "table": table,
        "max_list": max_list,
        "code_names": code_names,
        "steps": steps,
        "headings": headings,
        "ok": ok,
    }
