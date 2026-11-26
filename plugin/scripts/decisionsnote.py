"""One line for a PM session: the TM left new sections in its relay file.

Why: a cross-session send that the app's cap refuses is DROPPED, not queued, so the relay file is the only copy of what
the TM decided. Nothing read that file by itself; the user had to type a nudge. This line is the automatic reader.

Config: `relay_dir` in the home config.json (default None: no folder, no output, no file read). Inside it, `map.json`
maps a project path to the file name of that project's PM file, and `.read/<file>.marker` holds "<time> <offset>" only.
The hook opens only the file its own project maps to, reads at most its last 4 KB and only the section header lines,
and prints only a count, the captured time of the newest header and the folder path: never header text or a body.
Sections are ordered by byte offset; the time is a label and is never parsed. Any error skips silently.
A file now smaller than the marker's offset was truncated or rewritten: the offset counts as 0, so its sections show.

Who: only a session whose role is PM (the caller passes it; the config is read only then). A registered PM that reaches
the hook with no project takes its key from roles.detect, and only then is roles.detect called here. Subagent, lane and
TM sessions print nothing.

Limit: the marker moves when the line is shown, so after a compaction no ack remains. The keep-alive nudge is the
backstop for that case.
"""
import json
import os
import re

import studiolib as sl

TAIL = 4096
HEADER = re.compile(r"^## (\d\d? [A-Za-z]{3} \d\d:\d\d)", re.M)
MISSING_MARKER_SHOWS = 3


def _file_for(folder, project):
    """-> the absolute path of this project's mapped relay file, or None."""
    with open(os.path.join(folder, "map.json"), encoding="utf-8") as f:
        mapping = json.load(f)
    name = mapping.get(project)
    if name is None:
        real = os.path.realpath(project)
        name = next((v for k, v in mapping.items() if os.path.realpath(k) == real), None)
    if not isinstance(name, str) or not name or name != os.path.basename(name) or name.startswith("."):
        return None
    return os.path.join(folder, name)


def _headers(path):
    """-> (size, [(offset, time label)]) of the section headers in the last TAIL bytes."""
    size = os.path.getsize(path)
    start = max(0, size - TAIL)
    with open(path, "rb") as f:
        f.seek(start)
        raw = f.read(TAIL)
    if start:
        cut = raw.find(b"\n")
        if cut < 0:
            return size, []
        start += cut + 1
        raw = raw[cut + 1:]
    text = raw.decode("utf-8", "replace")
    out = []
    for m in HEADER.finditer(text):
        out.append((start + len(text[:m.start()].encode("utf-8")), m.group(1)))
    return size, out


def _read_marker(path):
    try:
        with open(path, encoding="utf-8") as f:
            word = f.read().split()
        return int(word[-1])
    except (OSError, ValueError, IndexError):
        return None


def line(inp, project, role):
    """-> the text to show this session, or None. Moves the marker when there is a section."""
    if role != "PM":
        return None
    try:
        folder = sl.load_config().get("relay_dir")
        if not isinstance(folder, str) or not folder:
            return None
        folder = os.path.expanduser(folder)
        key = project
        if not key:                       # the only roles.detect call here: a PM with no bound project
            import roles
            key = (roles.detect((inp or {}).get("session_id"), project) or {}).get("path")
        if not key:
            return None
        path = _file_for(folder, key)
        if not path or not os.path.isfile(path):
            return None
        size, heads = _headers(path)
        if not heads:
            return None
        marker = os.path.join(folder, ".read", os.path.basename(path) + ".marker")
        seen = _read_marker(marker)
        if seen is not None and seen > size:      # truncated or rewritten: read it from the start
            seen = 0
        new = heads[-MISSING_MARKER_SHOWS:] if seen is None else [h for h in heads if h[0] >= seen]
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        sl.atomic_write(marker, "%s %d\n" % (heads[-1][1], size), 0o600)
        if not new:
            return None
        return "budgit: new TM decisions (%d sections, newest %s) in your relay file, folder %s" % (len(new), new[-1][1], folder)
    except Exception:
        return None
