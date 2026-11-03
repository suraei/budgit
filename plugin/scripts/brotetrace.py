"""R-353: a tiny always-on trace of what the bar decided, so a real desktop chat can show whether the line is above the prompt or in the
footer, never both. One JSON line per change in <BUDGIT_HOME>/brote-trace.jsonl, the last 200 kept. A line holds only: the time, a hash of
the session id, the surfaces the host listed, whether the band drew and the footer was set, the footer's length (never its text) and the
plugin version. No project name, path or line text ever reaches it; every field is checked here whatever the caller sent."""
import hashlib
import json
import os
import re
import time

CAP = 200
_SURFACE = re.compile(r"[a-z][a-z0-9-]{0,19}", re.ASCII)
_VERSION = re.compile(r"\d{1,5}(\.\d{1,5}){0,3}", re.ASCII)


def trace_path(home):
    return os.path.join(home, "brote-trace.jsonl")


def record(sid, surfaces, band, drawn, footer_len, version, footer_at_draw=None, tail_len=0):
    """The line to write, or None when the input is not what the mod sends."""
    try:
        listed = None if surfaces in ("", "-", None) else [s for s in str(surfaces).split(",")[:8]]
        if listed is not None and not all(_SURFACE.fullmatch(s) for s in listed):
            return None
        flags = {k: v for k, v in (("band", band), ("drawn", drawn), ("footer_at_draw", footer_at_draw))}
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()), "session": hashlib.sha256(str(sid).encode("utf-8")).hexdigest()[:12],
               "surfaces": listed}
        for key, value in flags.items():
            if value is None and key == "footer_at_draw":
                continue
            if value not in ("yes", "no"):
                return None
            out[key] = value == "yes"
        if not re.fullmatch(r"[0-9]{1,6}", str(footer_len)):
            return None
        length = int(footer_len)
        if not 0 <= length <= 100000:
            return None
        out["footer_set"] = length > 0
        out["footer_len"] = length
        if not re.fullmatch(r"[0-9]{1,6}", str(tail_len)):
            return None
        out["tail_len"] = int(tail_len)           # the line at the end of the hint line under the prompt (terminal): its length, never its text
        out["version"] = version if isinstance(version, str) and _VERSION.fullmatch(version) else ""
        return out
    except (TypeError, ValueError):
        return None


def append(home, line, cap=CAP):
    """Add `line` to the trace and keep the last `cap`. Never raises: a trace must not disturb a session."""
    try:
        os.makedirs(home, mode=0o700, exist_ok=True)
        path = trace_path(home)
        if os.path.islink(path):
            return False
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > cap:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines[-cap:])
            os.replace(tmp, path)
        return True
    except OSError:
        return False
