"""R-847: processes nobody uses are always closed, by the hooks and the daily run, never by a model call.

Standing instruction: unused processes are always closed, and a leak of them is escalated as a serious incident.

The mechanism is a marker, not a guess. SessionStart writes `BUDGIT_OWNER=<session id>` into CLAUDE_ENV_FILE; Claude Code
puts it in the environment of every Bash command, so every process a session or its subagents start carries it. A closer
only ever signals a process that (a) is the current user's, (b) carries a marker, (c) is not this hook, nor an ancestor of
it, and (d) still has the same pid AND start time as when it was read (pid reuse). Hooks are not classified by auto mode,
so no `kill` allow rule is needed. Without CLAUDE_ENV_FILE support nothing carries a marker and nothing is closed
(the fall-back is said in the log, never a guess). Windows cannot read another process's environment: it reads no table.
"""
import calendar
import datetime
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

import studiolib as sl

OWNER_VAR = "BUDGIT_OWNER"
HER_WORDS = "standing instruction since 9 Nov 2026: unused processes are always closed"
DEFAULT_MIN_AGE_S = 3 * 3600          # the daily backstop; config key "orphan_min_age_s" overrides
EXT_OWNER_PREFIX = "extreview:"       # R-889a: `budgit ext-review` tags its tool child BUDGIT_OWNER=extreview:<session or pid>
EXT_ORPHAN_MIN_AGE_S = 60             # an orphaned tagged child is closed after this (the wrapper is gone, nothing waits on it)
START_TOLERANCE_S = 2.0              # ps start times have 1 s resolution
WAIT_S = 0.8                          # TERM wait; SessionEnd's budget is ~1.5 s in all, so KILL follows at once
_MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


# ---------------------------------------------------------------- the marker
def marker_line(session_id):
    """The CLAUDE_ENV_FILE line. Only a plain character set survives, so nothing quotes, expands or escapes."""
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "", str(session_id or ""))
    return 'export %s="%s"\n' % (OWNER_VAR, safe) if safe else ""


def write_marker(envfile, session_id):
    """Append the marker once per session. False when there is no file to write, no id, or the write failed."""
    line = marker_line(session_id)
    if not envfile or not line:
        return False
    try:
        with open(envfile, "a+", encoding="utf-8") as f:
            f.seek(0)
            if line not in f.read():
                f.write(line)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- the process table readers (one per platform)
def parse_lstart(tokens):
    """['Mon','Nov','9','10:05:00','2026'] -> epoch seconds (local time), or None."""
    try:
        _dow, mon, day, hms, year = tokens
        h, mi, s = (int(x) for x in hms.split(":"))
        return time.mktime((int(year), _MONTHS[mon], int(day), h, mi, s, 0, 0, -1))
    except (ValueError, KeyError):
        return None


def owner_from_env(env):
    """The marker value from an environment given as NUL-separated entries (never from argv text), or None."""
    for entry in env.split("\0"):
        if entry.startswith(OWNER_VAR + "="):
            value = entry[len(OWNER_VAR) + 1:]
            return value if re.fullmatch(r"[A-Za-z0-9_.:-]+", value) else None
    return None


def parse_procargs2(buf):
    """macOS KERN_PROCARGS2 buffer -> the environment as NUL-separated text. Layout: int argc, the exec path, NUL padding,
    argc argument strings, then the environment strings. The arguments are skipped, so a wrapper whose command text mentions
    the variable is never mistaken for a carrier."""
    if len(buf) < 4:
        return ""
    argc = int.from_bytes(buf[:4], sys.byteorder)
    rest = buf[4:]
    end = rest.find(b"\0")
    if end < 0:
        return ""
    rest = rest[end:].lstrip(b"\0")
    parts = rest.split(b"\0")
    return "\0".join(p.decode("utf-8", "replace") for p in parts[argc:] if p)


def procargs_env(pid):
    """The environment of one own-user process through sysctl (CTL_KERN 1, KERN_PROCARGS2 49); "" when unreadable (Apple
    binaries and other users' processes refuse)."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value == 0:
            return ""
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return ""
        return parse_procargs2(buf.raw[:size.value])
    except Exception:
        return ""


def parse_ps(text, env_in_command=False):
    """ps -o pid=,ppid=,pgid=,lstart= lines (no command, so no argument can forge a row) -> [{pid, ppid, pgid, start,
    command, owner, env_seen}]. A pid listed twice is dropped. The owner is never read from text (argv can quote anything);
    the platform reader fills owner and env_seen from the environment, and the command from `attach_commands`."""
    rows, seen, dup = [], set(), set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 8 or not all(p.isdigit() for p in parts[:3]):
            continue
        start = parse_lstart(parts[3:8])
        if start is None:
            continue
        pid = int(parts[0])
        if pid in seen:
            dup.add(pid)
        seen.add(pid)
        rows.append({"pid": pid, "ppid": int(parts[1]), "pgid": int(parts[2]), "start": start,
                     "command": "", "owner": None, "env_seen": False})
    return [r for r in rows if r["pid"] not in dup]


def attach_commands(rows, text):
    """`ps -o pid=,command=` text -> each row's command. Only pids already in the table count, and a pid seen on two lines
    (an argument with a newline can forge one) gets no command at all, so it can never match anything."""
    by = {r["pid"]: r for r in rows}
    got, dup = {}, set()
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if parts and parts[0].isdigit() and int(parts[0]) in by:
            pid = int(parts[0])
            if pid in got:
                dup.add(pid)
            got[pid] = parts[1] if len(parts) > 1 else ""
    for pid, cmd in got.items():
        by[pid]["command"] = "" if pid in dup else cmd
    return rows


BLIND_NOTE = "procclose: env unreadable, closers inert"


def check_blind(table):
    """One line to procclose.log and stderr when the reader returned processes but none showed an environment (so no marker
    can ever be seen): never silent. Rows without an `env_seen` flag (a fake reader) are never called blind."""
    if table and all(r.get("env_seen") is False for r in table):
        log(BLIND_NOTE)
        sys.stderr.write("budgit: " + BLIND_NOTE + "\n")
        return True
    return False


def _ps(argv):
    env = dict(os.environ, LC_ALL="C")
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def read_darwin(run=_ps, environ=procargs_env):
    """The table from plain `ps` (argv only); the environment of each process from sysctl KERN_PROCARGS2, which keeps argv and
    environment apart. Apple binaries and other users' processes show none (owner None): their closing rides on the marked
    ancestor's subtree (named residual in docs/spec.md when that ancestor is already dead)."""
    rows = parse_ps(run(["ps", "-A", "-o", "pid=,ppid=,pgid=,lstart="]))
    attach_commands(rows, run(["ps", "-A", "-ww", "-o", "pid=,command="]))
    for r in rows:
        text = environ(r["pid"])
        r["owner"] = owner_from_env(text)
        r["env_seen"] = bool(text)
    return rows


def read_linux(run=_ps, environ=None, uid=None):
    uid = os.getuid() if uid is None else uid
    rows = parse_ps(run(["ps", "-U", str(uid), "-o", "pid=,ppid=,pgid=,lstart="]))
    attach_commands(rows, run(["ps", "-U", str(uid), "-ww", "-o", "pid=,args="]))

    def proc_environ(pid):
        try:
            with open("/proc/%d/environ" % pid, "rb") as f:
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""
    environ = environ or proc_environ
    for r in rows:
        text = environ(r["pid"])
        r["owner"] = owner_from_env(text)
        r["env_seen"] = bool(text.strip("\0 "))
    return rows


def read_table(platform=None):
    platform = platform or sys.platform
    if platform == "darwin":
        return read_darwin()
    if platform.startswith("linux"):
        return read_linux()
    log("procclose: platform %s has no environment reader, closers inert" % platform)
    return []          # Windows: no environment read, so no marker, so nothing is ever closed (UNTESTED there)


# ---------------------------------------------------------------- selection
def ancestors(table, pid):
    """pid and every ancestor of it in the table (the claude process, its shells, this hook): never signalled."""
    by = {r["pid"]: r for r in table}
    out, cur = set(), pid
    while cur in by and cur not in out:
        out.add(cur)
        cur = by[cur]["ppid"]
    out.add(pid)
    return out


def same(a, b):
    return a and b and a["pid"] == b["pid"] and abs(a["start"] - b["start"]) <= START_TOLERANCE_S


def owned_by(table, session_id, exclude):
    return [r for r in table if r["owner"] == session_id and r["pid"] not in exclude]


def descendants(table, pid):
    kids = {}
    for r in table:
        kids.setdefault(r["ppid"], []).append(r)
    out, stack, seen = [], [pid], {pid}
    while stack:
        for c in kids.get(stack.pop(), []):
            if c["pid"] not in seen:
                seen.add(c["pid"])
                out.append(c)
                stack.append(c["pid"])
    return out


def orphans(table, now, min_age_s, exclude):
    """Marker-carrying processes of any session whose parent is gone (ppid 1) and that are old enough. An external-review
    child (R-889a: owner tagged `extreview:`) has a wrapper that always waits for it, so ppid 1 means the wrapper died:
    it keeps its own EXT_ORPHAN_MIN_AGE_S (60 s) threshold, not the daily one. The MIN_AGE_FLOOR_S (600 s) floor applies
    only to the sweep's --min-age-s and the config value, never to this extreview threshold."""
    return [r for r in table if r["owner"] and r["ppid"] == 1 and r["pid"] not in exclude
            and now - r["start"] >= (EXT_ORPHAN_MIN_AGE_S if r["owner"].startswith(EXT_OWNER_PREFIX) else min_age_s)]


# ---------------------------------------------------------------- the hold list (R-1095)
# The user's later, specific answer overrides the general standing yes: a process the user kept on purpose is never closed until the
# hold expires. An entry needs the user's quoted words, the note it came from and an expiry at most MAX_HOLD_DAYS away.
MAX_HOLD_DAYS = 14
_PORT_ARG = re.compile(r"(?:^|\s)(?:--port(?:=|\s+)|-p\s+)(\d+)(?=\s|$)")


def holds_path():
    return os.path.join(sl.home_state(), "procs-hold.json")


MIN_AGE_FLOOR_S = 600                 # an orphan younger than this is never closed, whatever is asked or configured


class HoldsError(OSError):
    """The holds file exists but cannot be trusted. Never read as "no holds": the callers close nothing."""


def esc(text):
    """Control characters (newline, escape, NUL) shown as escapes, so words can never forge a log or screen line."""
    return repr(str(text))[1:-1]


def check_min_age(value):
    """-> the age in seconds as a float; ValueError (with a message to show) unless it is finite and at least MIN_AGE_FLOOR_S."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError("--min-age-s must be a number of seconds, got %s" % esc(value))
    if not math.isfinite(v) or v < MIN_AGE_FLOOR_S:
        raise ValueError("--min-age-s must be a finite number of at least %d seconds, got %s" % (MIN_AGE_FLOOR_S, esc(value)))
    return v


SRC_RE = re.compile(r"I-[0-9]+")
ID_RE = re.compile(r"[0-9a-f]{32}")
MAX_PORT_SPAN = 100


def _date(text):
    """A full ISO date YYYY-MM-DD, nothing before or after it (no slicing)."""
    if not isinstance(text, str) or len(text) != 10:
        raise ValueError("not a full ISO date")
    return datetime.date.fromisoformat(text)


def _validate_entry(e):
    """Raise ValueError unless the entry has a 32-hex id, the user's words, an I-n, a kind with a usable value, an `added` date not
    after today and an expiry within MAX_HOLD_DAYS of it (both full ISO dates)."""
    if not isinstance(e, dict):
        raise ValueError("an entry is not an object")
    if not isinstance(e.get("id"), str) or not ID_RE.fullmatch(e["id"]):
        raise ValueError("an entry has no valid id")
    if e.get("kind") not in ("pid", "port"):
        raise ValueError("an entry has no valid kind")
    if not isinstance(e.get("words"), str) or not e["words"].strip():
        raise ValueError("an entry has no words")
    if not isinstance(e.get("src"), str) or not SRC_RE.fullmatch(e["src"]):
        raise ValueError("an entry has no I-n src")
    try:
        added, until = _date(e.get("added")), _date(e.get("expires"))
    except ValueError:
        raise ValueError("an entry has no valid added or expires date")
    if added > _today():
        raise ValueError("an entry was added in the future")
    if (until - added).days > MAX_HOLD_DAYS:
        raise ValueError("an entry lasts more than %d days" % MAX_HOLD_DAYS)
    if e["kind"] == "port":
        if not _parse_port_range(e.get("value")):
            raise ValueError("an entry has a bad port")
    else:
        try:
            int(e["value"])
            start = e["start"]
            if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
                raise ValueError("start is not a finite number")
        except (KeyError, TypeError, ValueError):
            raise ValueError("an entry has a bad pid or start time")


def _read_holds():
    """The entries. [] only when the file does not exist; anything else wrong with it raises HoldsError."""
    path = holds_path()
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise HoldsError("cannot stat %s: %s" % (path, e))
    if stat.S_ISLNK(st.st_mode):
        raise HoldsError("%s is a symlink" % path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise HoldsError("cannot read %s: %s" % (path, e))
    if not isinstance(data, list):
        raise HoldsError("%s is not a list" % path)
    for e in data:
        try:
            _validate_entry(e)
        except ValueError as err:
            raise HoldsError("%s: %s" % (path, err))
    return data


def _parse_port_range(value):
    m = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", str(value).strip())
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2) or lo)
    return (lo, hi) if 1 <= lo <= hi <= 65535 and hi - lo + 1 <= MAX_PORT_SPAN else None


def _today(today=None):
    import datetime
    return today or datetime.date.today()


def _split(entries, today):
    live, gone = [], []
    for e in entries:
        (live if _date(e["expires"]) >= today else gone).append(e)
    return live, gone


def active_holds(today=None):
    """-> (the entries still in force, the expired ones). Raises HoldsError when the file cannot be trusted."""
    return _split(_read_holds(), _today(today))


def session_id():
    for var in (OWNER_VAR, "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        v = re.sub(r"[^A-Za-z0-9_.:-]", "", os.environ.get(var) or "")
        if v:
            return v
    return "unknown"


def _locked_update(change):
    """Read, `change(entries) -> (new entries or None, result)` and replace, all inside one flock. The lock file is opened
    without following a symlink, the new file is a mkstemp in the same folder (mode 0600) moved over the old one."""
    import fcntl
    import tempfile
    path = holds_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    lk = os.open(path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lk, fcntl.LOCK_EX)
        new, result = change(_read_holds())
        if new is not None:
            fd, tmp = tempfile.mkstemp(dir=folder, prefix="procs-hold.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(new, f, indent=2)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        return result
    finally:
        os.close(lk)


def report_expired(today=None):
    """Say each expired hold once (marks it `reported` under the lock, logs it) -> the lines. [] when there is no file."""
    try:
        os.lstat(holds_path())
    except FileNotFoundError:
        return []
    today, lines = _today(today), []

    def change(entries):
        gone = _split(entries, today)[1]
        changed = False
        for e in entries:
            if e in gone and not e.get("reported"):
                lines.append("hold expired and no longer protects: %s %s (%s) until %s «%s»" % (
                    esc(e["kind"]), esc(e["value"]), esc(e["src"]), esc(e["expires"]), esc(e["words"])))
                e["reported"], changed = True, True
        return (entries if changed else None), None
    _locked_update(change)
    for line in lines:
        log(line)
    return lines


def _unreadable(err):
    """Said loudly, in the log and on stderr; the caller then signals nothing."""
    msg = "holds file unreadable; nothing closed (%s)" % esc(err)
    log("procclose: " + msg)
    sys.stderr.write("budgit: " + msg + "\n")


def add_hold(kind, value, words, src, until, today=None, reader=None, me=None):
    """Validate and store one hold -> (True, entry) or (False, why)."""
    today = _today(today)
    if kind not in ("pid", "port"):
        return False, "kind must be pid or port"
    if not str(words or "").strip():
        return False, "a hold needs the user's quoted words (--words)"
    if not SRC_RE.fullmatch(str(src or "").strip()):
        return False, "a hold needs the note it came from (--src I-n)"
    try:
        exp = datetime.date.fromisoformat(str(until))
    except ValueError:
        return False, "--until must be YYYY-MM-DD"
    if exp < today:
        return False, "--until is already past"
    if (exp - today).days > MAX_HOLD_DAYS:
        return False, "a hold lasts at most %d days" % MAX_HOLD_DAYS
    entry = {"kind": kind, "words": str(words).strip(), "src": str(src).strip(), "expires": exp.isoformat(),
             "added": today.isoformat(), "id": uuid.uuid4().hex}
    table = (reader or read_table)()
    if kind == "port":
        rng = _parse_port_range(value)
        if not rng:
            return False, "port must be N or LO-HI, within 1..65535 and at most %d ports wide" % MAX_PORT_SPAN
        if not any(rng[0] <= int(n) <= rng[1] for r in table for n in _PORT_ARG.findall(r.get("command") or "")):
            return False, ("no running process names this port in its command line; a port set by env or config cannot be held, "
                           "hold the pid")
        entry["value"] = "%d-%d" % rng if rng[0] != rng[1] else rng[0]
    else:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            return False, "pid must be a number"
        if pid <= 1:
            return False, "pid %d cannot be held: it would cover every orphan" % pid
        if pid in ancestors(table, os.getpid() if me is None else me):
            return False, "pid %d is the caller or one of its ancestors" % pid
        row = next((r for r in table if r["pid"] == pid), None)
        if not row:
            return False, "pid %d is not running, so its start time cannot be recorded" % pid
        entry["value"], entry["start"] = pid, row["start"]
    _locked_update(lambda entries: (entries + [entry], None))
    log("hold added id=%s session=%s %s %s until %s (%s) words=«%s»" % (
        esc(entry["id"]), esc(session_id()), esc(kind), esc(entry["value"]), esc(entry["expires"]), esc(entry["src"]),
        esc(entry["words"])))
    return True, entry


def remove_hold(hold_id, words, src):
    """Remove the hold with this stable id (never a list position) -> True, or False when no entry has it. Logged."""
    if not str(words or "").strip():
        raise ValueError("removing a hold needs --words")
    if not SRC_RE.fullmatch(str(src or "").strip()):
        raise ValueError("removing a hold needs --src I-n")
    removed = []

    def change(entries):
        keep = [e for e in entries if e.get("id") != hold_id]
        removed.extend(e for e in entries if e.get("id") == hold_id)
        return (keep if removed else None), None
    _locked_update(change)
    for e in removed:
        log("hold removed id=%s session=%s %s %s (%s) removed with src=%s words=«%s»" % (
            esc(hold_id), esc(session_id()), esc(e["kind"]), esc(e["value"]), esc(e["src"]), esc(src), esc(words)))
    return bool(removed)


def hold_matches(entry, row):
    if entry.get("kind") == "pid":
        try:
            return row["pid"] == int(entry["value"]) and abs(row["start"] - float(entry["start"])) <= START_TOLERANCE_S
        except (KeyError, TypeError, ValueError):
            return False
    rng = _parse_port_range(entry.get("value"))
    return bool(rng) and any(rng[0] <= int(n) <= rng[1] for n in _PORT_ARG.findall(row.get("command") or ""))


def held_map(table, holds):
    """{pid: hold} for every process a hold matches and every descendant of one."""
    out = {}
    for r in table:
        for h in holds:
            if hold_matches(h, r):
                out[r["pid"]] = h
                for d in descendants(table, r["pid"]):
                    out.setdefault(d["pid"], h)
                break
    return out


def hold_of(table, row, held):
    """The hold covering `row` or any of its descendants, else None."""
    if row["pid"] in held:
        return held[row["pid"]]
    return next((held[d["pid"]] for d in descendants(table, row["pid"]) if d["pid"] in held), None)


# ---------------------------------------------------------------- the closer
def close(targets, reader=read_table, kill=os.kill, killpg=os.killpg, sleep=time.sleep, wait_s=WAIT_S, me=None, only_marked=False, holds=None):
    """Terminate `targets` (rows read earlier): process groups first, a bounded wait, then SIGKILL. Each pid is re-read
    first and skipped when its start time changed (pid reuse) or its marker is gone. A group is signalled as a group only when
    every member of it in the fresh table is a target, so an unmarked neighbour never dies with it. -> the rows closed."""
    me = os.getpid() if me is None else me
    fresh = reader()
    safe = ancestors(fresh, me)
    get_holds = (lambda: holds) if holds is not None else (lambda: active_holds()[0])
    try:
        held = held_map(fresh, get_holds())        # R-1095: the user's hold wins; re-read right before each signal phase
    except HoldsError as e:                        # an untrusted holds file: signal nothing
        _unreadable(e)
        return []
    live = [t for t in targets if any(same(t, f) and f["owner"] and f["pid"] not in safe for f in fresh)
            and hold_of(fresh, t, held) is None]
    kids = {}
    for f in fresh:
        kids.setdefault(f["ppid"], []).append(f)
    depth, tree = {}, {}
    stack = [(t["pid"], 0) for t in live]
    while stack:                                   # the whole subtree of a marked process, from this one snapshot
        pid, d = stack.pop()
        if pid in safe or (pid in depth and depth[pid] >= d):
            continue
        depth[pid] = d
        for c in kids.get(pid, []):
            if only_marked and not c["owner"]:      # the sweep: a child that is not marked in THIS read is never touched
                continue
            stack.append((c["pid"], d + 1))
    byid = {f["pid"]: f for f in fresh}
    tree = [byid[p] for p in sorted(depth, key=lambda p: -depth[p]) if p in byid]     # deepest first
    def send(sig, victims, table):
        """Signal `victims` (rows of `table`, deepest first); a group goes as a group only when every member of it in `table`
        is a victim."""
        ids = {v["pid"] for v in victims}
        groups = {}
        for f in table:
            groups.setdefault(f["pgid"], []).append(f["pid"])
        own_pgid = next((f["pgid"] for f in table if f["pid"] == me), None)
        done = set()
        for t in victims:
            g = t["pgid"]
            if g not in done and g != own_pgid and g not in (0, 1) and all(p in ids for p in groups.get(g, [])):
                done.add(g)
                try:
                    killpg(g, sig)
                except OSError:
                    pass
        for t in victims:
            if t["pgid"] not in done:
                try:
                    kill(t["pid"], sig)
                except OSError:
                    pass

    send(signal.SIGTERM, tree, fresh)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        now_rows = reader()
        if not any(same(t, f) for t in tree for f in now_rows):
            return live
        sleep(0.05)
    again = reader()                  # pids and group ids can be reused during the wait: KILL only what still matches
    by = {f["pid"]: f for f in again}
    try:
        held = held_map(again, get_holds())
    except HoldsError as e:                        # the file went bad since TERM: no SIGKILL
        _unreadable(e)
        return live
    survivors = [by[t["pid"]] for t in tree if t["pid"] in by and same(t, by[t["pid"]]) and t["pid"] not in safe
                 and t["pid"] not in held and (not only_marked or by[t["pid"]]["owner"])]
    send(signal.SIGKILL, survivors, again)
    return live


def log(line):
    try:
        os.makedirs(sl.home_state(), exist_ok=True)
        sl.append_text(os.path.join(sl.home_state(), "procclose.log"), "%s %s\n" % (sl.now(), line))
    except Exception:
        pass


def _fmt(r, now):
    shown = r["command"][:160].encode("unicode_escape").decode("ascii")        # no raw newline can forge a log line
    return "pid=%d age=%ds owner=%s command=%s" % (r["pid"], now - r["start"], r["owner"], shown)


# ---------------------------------------------------------------- the hooks
def on_session_start(inp):
    """False when CLAUDE_ENV_FILE is missing or unwritable: nothing carries the marker, and the closers do nothing."""
    ok = write_marker(os.environ.get("CLAUDE_ENV_FILE"), inp.get("session_id"))
    if not ok:
        log("session-start: no CLAUDE_ENV_FILE support; processes of session %s are not marked, so none is closed" % inp.get("session_id"))
    return ok


def on_session_end(inp, reader=read_table, **kw):
    sid = re.sub(r"[^A-Za-z0-9_.:-]", "", str(inp.get("session_id") or ""))
    if not sid or inp.get("reason") in ("clear", "resume"):
        return []
    table = reader()
    check_blind(table)
    now = time.time()
    closed = close(owned_by(table, sid, ancestors(table, kw.get("me") or os.getpid())), reader=reader, **kw)
    for r in closed:
        log("session-end closed " + _fmt(r, now))
    return closed


def transcript_facts(path):
    """-> (start epoch or None, [commands the agent ran in the background]) from a subagent transcript (JSONL)."""
    start, cmds = None, []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if start is None and isinstance(row.get("timestamp"), str):
                    try:
                        start = calendar.timegm(time.strptime(row["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
                    except ValueError:
                        pass
                content = (row.get("message") or {}).get("content") if isinstance(row.get("message"), dict) else None
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                        inp = block.get("input") or {}
                        text = str(inp.get("command") or "")
                        if inp.get("run_in_background") or re.search(r"&\s*$|\bnohup\b", text):
                            cmds.append(text)
    except OSError:
        pass
    return start, cmds


def _norm(text):
    return re.sub(r"\s+", " ", text).strip()


def agent_leftovers(table, sid, start, cmds, exclude):
    """Live processes of this session, started since the agent began, whose command line is one the agent ran in the
    background (or sits inside one of them): what it left running. Without a start or a command nothing is claimed."""
    if start is None or not cmds:
        return []
    wanted = set()
    for c in cmds:
        shape = claim_shape(c)
        if shape:
            wanted.add(shape)
        else:                                      # named, never claimed: a pipe, a chain or a bare program is too broad to match
            log("subagent gate: backgrounded command not claimable (%s)" % c[:120].encode("unicode_escape").decode("ascii"))
    out = []
    for r in owned_by(table, sid, exclude):
        if r["start"] + START_TOLERANCE_S < start:
            continue
        cmd = _norm(r["command"])
        if cmd and cmd in wanted:                 # exact, normalized: a sibling "python3" or "sleep 600" never matches
            out.append(r)
    return out


def claim_shape(command):
    """The ONE accepted shape (allow list). A backgrounded Bash line is claimable only as `[cd <path> &&] [nohup|exec] <program>
    <args...> [redirections] [&]`: exactly those four wrappers are stripped, whitespace normalized. What remains must hold no
    `| ; && || & < > backtick $( newline` and at least two arguments after the program. Returns that string, else None
    (pipes, chains, a bare `python3` or `sleep 600` are never claimed)."""
    s = command.strip()
    s = re.sub(r"^cd\s+[^\s;&|<>$`]+\s*&&\s*", "", s, count=1)
    s = re.sub(r"\s*&$", "", s, count=1) if not s.endswith("&&") else s
    while True:
        t = re.sub(r"\s+\d*>&\d+$|\s+\d*>>?\s*[^\s;&|<>$`]+$", "", s)
        if t == s:
            break
        s = t
    s = re.sub(r"^(?:nohup|exec)\s+", "", s, count=1)
    if re.search(r"[|;&<>`\n]|\$\(", s):
        return None
    s = _norm(s)
    return s if len(s.split()) >= 3 else None      # the program and at least two arguments: `sleep 600` is too common to claim


def _state_path(agent_id):
    return os.path.join(sl.home_state(), "procclose-blocked", re.sub(r"[^A-Za-z0-9_-]", "_", str(agent_id))[:64])


def on_subagent_stop(inp, reader=read_table, **kw):
    """Once: block the stop and name each process. The second stop of the same agent closes them itself and logs a FAIL.
    -> a dict for the hook output, or None when nothing is left."""
    sid = re.sub(r"[^A-Za-z0-9_.:-]", "", str(inp.get("session_id") or ""))
    path = inp.get("agent_transcript_path")      # the agent's own transcript only: the session's would widen the match
    if not sid or not path:
        return None
    start, cmds = transcript_facts(path)
    table = reader()
    check_blind(table)
    safe = ancestors(table, kw.get("me") or os.getpid())
    left = agent_leftovers(table, sid, start, cmds, safe)
    import suitejobs                                  # R-1101: a suite job the agent started is its child too, even though it was not backgrounded in Bash
    try:
        jobs = suitejobs.live_jobs(table, sid, start, safe)
    except Exception as exc:                          # fail CLOSED: jobs that cannot be read still block the stop, with the remedy
        log("subagent gate: suite jobs unreadable (%s)" % esc(str(exc)[:120]))
        jobs = [{"pid": -1, "job": "<job id>", "command": "", "start": 0, "owner": sid}]
    left += [r for r in jobs if r["pid"] not in {x["pid"] for x in left}]
    state = _state_path(inp.get("agent_id") or path)
    if not left:
        try:
            os.remove(state)
        except OSError:
            pass
        return None
    now = time.time()
    if not inp.get("stop_hook_active") and not os.path.exists(state):
        try:
            os.makedirs(os.path.dirname(state), exist_ok=True)
            sl.append_text(state, "blocked %s\n" % sl.now())
        except OSError:
            pass
        what = "; ".join("stop %d %s" % (r["pid"], r["command"].split(" %s=" % OWNER_VAR)[0][:80]) for r in left if "job" not in r)
        waits = ["run `budgit wait %s` until it ends" % r["job"] for r in jobs]
        if waits and not what:
            return {"decision": "block", "reason": "budgit R-847: the suite job you started is still running (%s), then finish. "
                                                   "Ending the turn now would close it." % "; ".join(waits)}
        return {"decision": "block", "reason": "budgit R-847: you left a process running (%s) before handing back. Stop it, then finish. "
                                               "A server left running is a FAIL.%s" % (what, (" And " + "; ".join(waits) + ".") if waits else "")}
    if kw.get("holds") is None:
        try:
            _read_holds()
        except OSError as e:                          # never a silent pass: the gate says it could not close
            _unreadable(e)
            try:
                os.remove(state)
            except OSError:
                pass
            log("FAIL agent %s handed back with %d live process(es); holds file unreadable, nothing closed" % (
                esc(inp.get("agent_id")), len(left)))
            return {"systemMessage": "budgit R-847 FAIL: %d process(es) left running by this agent were NOT closed: holds file "
                                     "unreadable; nothing closed" % len(left)}
    unread = [r for r in left if r["pid"] == -1]      # L-Q4: the jobs could not be read; close this session's runners by marker and argv shape
    if unread:
        left = [r for r in left if r["pid"] != -1] + [r for r in suitejobs.runner_rows(table, sid, start, safe) if r["pid"] not in {x["pid"] for x in left}]
    closed = close(left, reader=reader, **kw) if left else []
    for r in closed:
        log("FAIL agent %s handed back with a live process; closed it: %s" % (inp.get("agent_id"), _fmt(r, now)))
    try:
        os.remove(state)
    except OSError:
        pass
    if unread:
        log("FAIL agent %s handed back while its suite jobs could not be read; closed %d runner(s)" % (esc(inp.get("agent_id")), len(closed)))
        return {"systemMessage": "budgit R-847 FAIL: this agent's suite jobs could not be read; %d runner process(es) of this session were closed "
                                 "by the hook (a job that survived is closed by the 3 h sweep)" % len(closed)}
    return {"systemMessage": "budgit R-847 FAIL: %d process(es) left running by this agent were closed by the hook" % len(closed)} if closed else None


# ---------------------------------------------------------------- the daily backstop
def sweep_orphans(apply_changes, reader=read_table, min_age_s=None, now=None, held_out=None, expired_out=None, **kw):
    """Marker-carrying processes with ppid 1 older than the threshold (config `orphan_min_age_s`, default 3 h, never under
    MIN_AGE_FLOOR_S). Dry run unless `apply_changes`; each one is logged with its pid, age, command and owner under the user's
    standing yes. An untrusted holds file closes nothing (dry run or not). -> [rows]"""
    if min_age_s is None:
        try:
            min_age_s = check_min_age(sl.load_config().get("orphan_min_age_s") or DEFAULT_MIN_AGE_S)
        except ValueError as e:
            log("procclose: config orphan_min_age_s ignored, default used (%s)" % e)
            min_age_s = DEFAULT_MIN_AGE_S
    now = time.time() if now is None else now
    table = reader()
    check_blind(table)
    try:
        holds = kw["holds"] if kw.get("holds") is not None else active_holds()[0]
        if kw.get("holds") is None:
            lines = report_expired()
            if expired_out is not None:
                expired_out.extend(lines)
    except OSError as e:                          # HoldsError, or a lock that cannot be taken: nothing is signalled
        _unreadable(e)
        return []
    candidates = orphans(table, now, min_age_s, ancestors(table, kw.get("me") or os.getpid()))
    found = []
    held = held_map(table, holds)
    kept = []
    for r in candidates:
        hold = hold_of(table, r, held)
        if hold:                                  # R-1095: the user kept it on purpose; never listed as closable
            kept.append((r, hold))
            log("daily sweep HELD by the user (%s, %s) until %s: %s" % (esc(hold.get("src")), esc(hold.get("words")), esc(hold.get("expires")), _fmt(r, now)))
    candidates = [r for r in candidates if hold_of(table, r, held) is None]
    if held_out is not None:
        held_out.extend(kept)
    for r in candidates:                          # until the user decides on a keep list (tmux, gpg-agent, ssh mux): only a process
        if all(d["owner"] for d in descendants(table, r["pid"])):     # whose whole subtree is marked is closed
            found.append(r)
        else:
            log("daily sweep SKIPPED (an unmarked descendant, maybe a daemon with the user's shells) " + _fmt(r, now))
    if not apply_changes:
        for r in found:
            log("dry-run would close " + _fmt(r, now))
        return found
    closed = close(found, reader=reader, only_marked=True, **kw)
    for r in closed:
        log("daily sweep closed %s; reason: %s" % (_fmt(r, now), HER_WORDS))
    return closed


def main(argv=None):
    """`budgit procs [--apply] [--min-age-s N]`: the dry run lists, --apply closes (the user's standing yes)."""
    import argparse
    p = argparse.ArgumentParser(prog="budgit procs")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--min-age-s", default=None)
    p.add_argument("--hold", nargs=2, metavar=("KIND", "VALUE"))
    p.add_argument("--words")
    p.add_argument("--src")
    p.add_argument("--until")
    p.add_argument("--holds", action="store_true")
    p.add_argument("--unhold", metavar="ID")
    a = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    if a.min_age_s is not None:
        try:
            a.min_age_s = check_min_age(a.min_age_s)
        except ValueError as e:
            print("budgit procs: %s" % e, file=sys.stderr)
            return 2
    if a.hold:
        try:
            ok, res = add_hold(a.hold[0], a.hold[1], a.words, a.src, a.until)
        except OSError as e:
            print("not held: %s" % e)
            return 1
        print("held: %s" % json.dumps(res) if ok else "not held: %s" % res)
        return 0 if ok else 1
    if a.holds or a.unhold is not None:
        if a.unhold is not None:
            try:
                ok = remove_hold(a.unhold, a.words, a.src)
            except ValueError as e:
                print("not removed: %s" % e)
                return 1
            except OSError as e:
                print("not removed: %s" % esc(e))
                return 1
            print("removed hold %s" % esc(a.unhold) if ok else "no hold with id %s" % esc(a.unhold))
            return 0 if ok else 1
        try:
            entries = _read_holds()
        except OSError as e:
            print("holds file unreadable: %s" % esc(e))
            return 1
        gone = _split(entries, _today())[1]
        for e in entries:
            print("%s  %s %s until %s (%s) «%s»%s" % (esc(e.get("id", "-")), esc(e["kind"]), esc(e["value"]), esc(e["expires"]),
                                                      esc(e["src"]), esc(e["words"]),
                                                      "  [expired]" if e in gone else ""))
        if not entries:
            print("no holds")
        return 0
    held_out, expired = [], []
    rows = sweep_orphans(a.apply, min_age_s=a.min_age_s, held_out=held_out, expired_out=expired)
    now = time.time()
    for r in rows:
        print(("closed " if a.apply else "would close ") + _fmt(r, now))
    for r, h in held_out:
        print("held by the user (%s) until %s: %s" % (esc(h.get("src")), esc(h.get("expires")), _fmt(r, now)))
    for line in expired:                             # an expired hold is said once, in a dry run, --apply and housekeeping
        print(line)
    try:
        _read_holds()
    except OSError:                                  # sweep_orphans already said it on stderr and in the log
        print("holds file unreadable; nothing closed")
        return 1
    if not rows and not held_out:
        print("no orphaned marked processes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
