"""R-665: one JSON line per agent hand-back in <project>/.budgit/timelines.jsonl, written by the SubagentStop hook.

Zero model tokens. Fields: ts, project, agent, task (the brief's `task:` line, else its first line), wall_s, calls,
waits (wait-only calls), budget (the brief's `Budget: N`, 0 if none), result (first line of the hand-back), refused,
red, released (ids whose ledger claim this hand-back let go). The write is an O_APPEND under flock; any failure is
swallowed and noted in hooks.log (never raises into the hook). No bound project: nothing is written.

Waste (for the stand-up), minutes from the fields of the lines of the last 24 h. Each hand-back's waste goes to ONE
cause, by this fixed priority, so the minutes never add up to more than its wall time:
  1. refusals:          the whole wall_s, when the agent's transcript holds a denied tool_result (is_error true and a
                        DENY_BUDGIT / DENY_HOST marker), or, with no transcript, a structured `Refused:` line naming
                        something other than none/nothing/no. Free prose is never read.
  2. red gates:         the whole wall_s, when a `FAILED (` line has no later `OK` line, or the hand-back says the gate is red
  3. over-budget calls: wall_s * (calls - budget) / calls, when budget > 0 and calls > budget
  4. idle waits:        wall_s * waits / calls
The keep-alive flags an active project (channel not off, an open or doing item) with no line in 24 h.
"""
import datetime
import fcntl
import json
import os
import re
import stat as stat_mod

import studiolib as sl

NAME = "timelines.jsonl"
DAY_S = 24 * 3600
CAUSES = ("over-budget calls", "refusals", "red gates", "idle waits")
_REFUSED_LINE = re.compile(r"(?im)^\W*(?:anything\s+)?refused\s*:\s*(.*)$")
_EMPTY_VALUE = re.compile(r"(?i)\W*(?:(?:none|nothing|no|nil|n/a)\b.*)?$")
_GATE_RED = re.compile(r"(?i)\b(?:gate (?:is |was |stays |came back )?red|red gate)\b")

# A refusal is a tool_result with is_error true whose text BEGINS with a denial (a failed test or grep that merely quotes one
# mid-text is not a refusal). Budgit's own deny (hook.py:368 PreToolUse deny output with permissionDecisionReason, hook.py:1278
# the fallback deny «budgit guard: …»): the host shows it as «PreToolUse:<Tool> hook error: budgit …», so the text STARTS
# with DENY_START and its FIRST line holds DENY_FIRST_LINE.
DENY_START, DENY_FIRST_LINE = "PreToolUse:", "hook error: budgit"
# host wording, not sampled in this repo; unverified. The text starts with the first words and its first line holds the rest:
DENY_HOST = (("Permission to use", "has been denied"), ("The user doesn't want to proceed",))


def is_denial(text):
    """True when the text of an is_error tool_result starts like a Budgit or host denial (never a mid-text quote)."""
    text = str(text or "").lstrip()
    first = text.splitlines()[0] if text else ""
    if text.startswith(DENY_START) and DENY_FIRST_LINE in first:
        return True
    return any(text.startswith(words[0]) and all(w in first for w in words) for words in DENY_HOST)


def is_refused(message):
    """Fallback for a missing transcript: True only for a structured `Refused:` / `anything refused:` line that names
    something other than none, nothing, no or empty. Free prose is ignored on purpose (it misread both ways)."""
    return any(not _EMPTY_VALUE.match(m.group(1)) for m in _REFUSED_LINE.finditer(str(message or "")))


def is_red(message):
    """True when a `FAILED (` line has no later `OK` line (a later green run), or the hand-back says the gate is red."""
    lines = [ln.strip() for ln in str(message or "").splitlines()]
    failed = [i for i, ln in enumerate(lines) if "FAILED (" in ln]
    if failed and not any(ln == "OK" or ln.startswith("OK ") for ln in lines[failed[-1] + 1:]):
        return True
    return any(_GATE_RED.search(ln) and "first" not in ln.lower() for ln in lines)


_TASK = re.compile(r"(?im)^\W*task\s*:\s*(.+)$")


def path(project):
    return os.path.join(sl.studio_dir(project), NAME)


def _stamp(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _first(text, n):
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    return sl.oneline(sl.redact(line), n)


def _scan(transcript):
    """-> (launch prompt, tool calls, wait-only calls, wall seconds, denied tool_results) from the agent's own transcript;
    zeros when unreadable."""
    import agentcap
    import runtrack
    calls = waits = denied = 0
    first = last = None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                at = _epoch(rec.get("timestamp"))
                if at is not None:
                    first = at if first is None else first
                    last = at
                msg = rec.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                for b in content if isinstance(content, list) else ():
                    if not isinstance(b, dict):
                        continue
                    if rec.get("type") == "assistant" and b.get("type") == "tool_use":
                        calls += 1
                        waits += bool(agentcap.is_wait(b.get("name"), b.get("input")))
                    elif rec.get("type") == "user" and b.get("type") == "tool_result" and b.get("is_error") is True:
                        body = b.get("content")
                        if isinstance(body, list):
                            body = " ".join(str(x.get("text", "")) for x in body if isinstance(x, dict))
                        denied += is_denial(body)
    except (OSError, TypeError):
        return None, 0, 0, 0, None                 # denied None: no usable transcript
    return runtrack.launch_prompt(transcript), calls, waits, int(last - first) if first is not None else 0, denied


def _append(file, line):
    fd = os.open(file, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)                               # closing releases the flock


def _dropped(project, touched):
    """Of the ids a hand-back touched, the ones whose claim (doing_by) is really gone now; a claim another run still holds stays."""
    if not touched:
        return []
    try:
        held = {it["id"] for it in sl.load_ledger(project)["items"] if it.get("doing_by")}
    except Exception:
        return []
    return [i for i in touched if i not in held]


def record(project, inp, released=(), at=None):
    """The hand-back line of one agent. -> the line, or None (no project, or a failure that was logged and swallowed)."""
    if not project:
        return None
    try:
        import agentcap
        transcript = agentcap._transcript(inp)
        prompt, calls, waits, wall, denied = _scan(transcript) if transcript else (None, 0, 0, 0, None)
        m = _TASK.search(prompt or "")
        message = inp.get("last_assistant_message") or ""
        b = agentcap._BRIEF_BUDGET.search(prompt or "")
        line = {"ts": _stamp(at if at is not None else __import__("time").time()),
                "project": os.path.basename(os.path.realpath(project)), "agent": sl.oneline(str(inp.get("agent_type") or ""), 40),
                "task": sl.oneline(sl.redact(m.group(1) if m else _first(prompt, 150)), 150), "wall_s": wall, "calls": calls,
                "waits": waits, "budget": int(b.group(1)) if b else 0, "result": _first(message, 200),
                "refused": is_refused(message) if denied is None else denied > 0, "red": is_red(message), "released": _dropped(project, list(released or ()))}
        sl.ensure_studio_dir(project)
        _append(path(project), line)
        return line
    except Exception:
        sl.log_error("timelines: the hand-back line was not written")
        return None


SF_DATALESS = 0x40000000       # macOS: the file's content is evicted to iCloud; opening it makes the system download it (R-1312)
LIMIT_S = 20                   # default time limit of `budgit timelines silent`
EVICTED, TIME_LIMIT = "evicted", "time limit"


def is_evicted(file, stat=os.stat):
    """True when the file is dataless (SF_DATALESS). A stat failure is False: the normal read path handles a missing file."""
    try:
        return bool(getattr(stat(file), "st_flags", 0) & SF_DATALESS)
    except OSError:
        return False


PROJECT_JSON_MAX = 64 * 1024
TIMELINE_MAX = 32 * 1024 * 1024    # a timeline file is read up to this many bytes


def _read_regular(file, limit, tail=False):
    """The first limit+1 bytes of a REGULAR file, or with tail=True its LAST `limit` bytes without the first, partial line (an
    append-only file keeps its newest lines). Opened with O_NOFOLLOW (a symlink fails) and O_NONBLOCK (a FIFO cannot block
    the open), and fstat must say regular file, so a device, a FIFO or a symlink to one raises OSError instead of hanging."""
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        size = os.fstat(fd).st_size
        if not stat_mod.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file: " + os.path.basename(file))
        with os.fdopen(fd, "rb") as f:
            fd = None
            if tail and size > limit:
                f.seek(size - limit)
                data = f.read(limit)
                return data.partition(b"\n")[2]
            return f.read(limit + 1)
    finally:
        if fd is not None:
            os.close(fd)


def _marked_private(project, stat, clock, deadline):
    """True when the project's own project.json says "private": true, or when it cannot be read safely: evicted, past the
    deadline, over 64 KB, or any read failure (all count as private). A missing project.json is not a mark."""
    if deadline is not None and clock() >= deadline:
        return True                                       # past the limit nothing more is looked at
    f = os.path.join(sl.studio_dir(project), "project.json")
    try:
        st = stat(f)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if getattr(st, "st_flags", 0) & SF_DATALESS or st.st_size > PROJECT_JSON_MAX:
        return True
    try:
        data = _read_regular(f, PROJECT_JSON_MAX)
        return len(data) > PROJECT_JSON_MAX or json.loads(data.decode("utf-8")).get("private") is True
    except Exception:
        return True


def private_checker(stat=os.stat, clock=None, deadline=None):
    """-> f(project path) -> True when the project must not be named. First the protected folders (`protected_dirs`, the list
    portfolio and the guard keep, livework's rule), read ONCE here from the config files. Then, for a project outside them,
    its own project.json mark, read only if not evicted, before the deadline and under 64 KB (see _marked_private).
    When the folder list cannot be read, every project counts as private (names hidden)."""
    try:
        import livework
        strings, bad = livework._config_dir_scan(livework._home())
        if bad:
            return lambda p: True
        dirs = [sl.protected_entry(s) for s in strings]
        if not all(dirs):                                 # an entry that cannot be resolved: fail closed, hide every name
            return lambda p: True
        return lambda p: (any(livework._under(sl.fold_path(os.path.realpath(p)), d) for d in dirs)
                          or _marked_private(p, stat, clock, deadline))
    except Exception:
        return lambda p: True


def display_name(project, is_private):
    """The basename of a project, or 'private' for a protected one (or when privacy cannot be told)."""
    try:
        return "private" if is_private(project) else os.path.basename(os.path.realpath(project))
    except Exception:
        return "private"


def skipped_line(skipped, is_private=None):
    """'timelines: skipped N projects (evicted: a, b; time limit: c)' from [(project path, reason)]; '' when none. Basenames only;
    a protected project is counted as 'private', never named. No project file is opened."""
    if not skipped:
        return ""
    is_private = is_private or private_checker()
    groups = {}
    for p, reason in skipped:
        groups.setdefault(reason, []).append(display_name(p, is_private))
    return "timelines: skipped %d projects (%s)" % (
        len(skipped), "; ".join("%s: %s" % (r, ", ".join(groups[r])) for r in (EVICTED, TIME_LIMIT) if r in groups))


def read_lines(project, since=None, stat=os.stat):
    out = []
    if is_evicted(path(project), stat):
        return out
    try:
        text = _read_regular(path(project), TIMELINE_MAX, tail=True).decode("utf-8", errors="replace")
        for raw in text.splitlines():
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            at = _epoch(rec.get("ts")) if isinstance(rec, dict) else None
            if at is not None and (since is None or at >= since):
                out.append(rec)
    except OSError:
        pass
    return out


def waste_minutes(rec):
    """{cause: minutes} of one line, by the rules in the module docstring."""
    wall, calls = rec.get("wall_s") or 0, rec.get("calls") or 0
    budget, waits = rec.get("budget") or 0, rec.get("waits") or 0
    out = dict.fromkeys(CAUSES, 0.0)
    if rec.get("refused"):                                   # one cause only, by the priority in the module docstring
        out["refusals"] = wall / 60
    elif rec.get("red"):
        out["red gates"] = wall / 60
    elif budget > 0 and calls > budget:
        out["over-budget calls"] = wall * (calls - budget) / calls / 60
    elif calls > 0:
        out["idle waits"] = wall * min(waits, calls) / calls / 60
    return out


def top_waste(projects, now=None, limit=3):
    """-> [(cause, minutes)], biggest first, zero causes left out, over the last 24 h of the given project folders."""
    import time
    since = (now if now is not None else time.time()) - DAY_S
    total = dict.fromkeys(CAUSES, 0.0)
    for p in projects:
        for rec in read_lines(p, since):
            for cause, mins in waste_minutes(rec).items():
                total[cause] += mins
    return sorted(((c, round(m, 1)) for c, m in total.items() if m > 0), key=lambda cm: -cm[1])[:limit]


def active_projects(skipped=None, stat=os.stat, clock=None, deadline=None):
    """Registered projects whose channel is not off and that hold an open or doing item. A project whose ledger file is evicted
    is not opened, and past the deadline (clock() >= deadline) the rest are not reached; both go to `skipped` as (path, reason)."""
    import portfolio
    skipped = [] if skipped is None else skipped
    out = []
    for e in (portfolio.load() or {}).get("projects", []):
        try:
            if not isinstance(e, dict) or not isinstance(e.get("path"), str) or e.get("channel") == "off":
                continue
            if deadline is not None and clock() >= deadline:
                skipped.append((e["path"], TIME_LIMIT))
            elif is_evicted(sl._ledger_path(e["path"]), stat):
                skipped.append((e["path"], EVICTED))
            elif any(it.get("status") in ("open", "doing") for it in sl.load_ledger(e["path"])["items"]):
                out.append(e["path"])
        except Exception:
            continue
    return out


def silent_lines(projects, now=None, skipped=None, stat=os.stat, clock=None, deadline=None, is_private=None):
    """One line per active project with no timeline line in the last 24 h (for the keep-alive output). An evicted timeline file is
    not opened and past the deadline the rest are not read; both go to `skipped` as (path, reason)."""
    import time
    skipped = [] if skipped is None else skipped
    since = (now if now is not None else time.time()) - DAY_S
    is_private = is_private or private_checker()          # a protected project is named "private", never by its folder
    out = []
    for p in projects:
        if deadline is not None and clock() >= deadline:
            skipped.append((p, TIME_LIMIT))
        elif is_evicted(path(p), stat):
            skipped.append((p, EVICTED))
        elif not read_lines(p, since, stat):
            out.append("timelines: %s has no timeline line in the last 24 h" % display_name(p, is_private))
    return out


def standup_block(projects=None, now=None):
    """The stand-up step's text: the top 3 waste causes by minutes. Deterministic, no model."""
    projects = active_projects() if projects is None else projects
    top = top_waste(projects, now)
    if not top:
        text = "Waste, last 24 h: none recorded."
    else:
        text = "Waste, last 24 h (top %d by minutes): " % len(top) + "; ".join("%s %.1f min" % cm for cm in top)
    import extuse                      # R-1358: one more line, only when an outside model ran in the last 7 days
    use = extuse.week_line(projects, now)
    return text + "\n" + use if use else text


def writer_installed(config_dir=None):
    """True unless Claude Code's registry lists this plugin and no registered installPath holds scripts/timelines.py. The hooks run
    the installed copy, so without the file no hand-back writes a line. No registry, no entry or any error: True (unknown)."""
    try:
        import updatelib
        name = updatelib._manifest(updatelib._plugin_root())[0]
        data = updatelib._read_json(os.path.join(updatelib._config_dir(config_dir), "plugins", "installed_plugins.json"))
        plugins = data.get("plugins") if isinstance(data, dict) else None
        if not name or not isinstance(plugins, dict):
            return True
        found = False
        for key, entries in plugins.items():
            plugin, _at, market = str(key).partition("@")
            if plugin != name or not updatelib._NAME.match(market):
                continue
            for e in entries if isinstance(entries, list) else ():
                found = True
                root = e.get("installPath") if isinstance(e, dict) else None
                if isinstance(root, str) and os.path.isfile(os.path.join(root, "scripts", "timelines.py")):
                    return True
        return not found
    except Exception:
        return True


def cli_text(op, limit_s=LIMIT_S, stat=os.stat, clock=None):
    """The text of `budgit timelines standup|silent` (the scheduled stand-up and keep-alive run these). `silent` stops after
    limit_s seconds and ends with one line naming the projects it skipped (evicted files, time limit)."""
    if op == "standup":
        return standup_block()
    if not writer_installed():
        return "timelines: writer not in the installed plugin"
    import time
    clock = clock or time.monotonic
    deadline, skipped = clock() + limit_s, []
    is_private = private_checker(stat, clock, deadline)    # the folder list is read once, here; project.json only when safe
    lines = silent_lines(active_projects(skipped, stat, clock, deadline), skipped=skipped, stat=stat, clock=clock, deadline=deadline,
                         is_private=is_private)
    if not lines and not skipped:
        lines = ["timelines: no silent active project."]
    return "\n".join(lines + ([skipped_line(skipped, is_private)] if skipped else []))
