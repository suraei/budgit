"""R-1322: `budgit housekeeping procs` -- the local test servers that agents start and never stop.

Verifiers, builders and browser checks start demo web servers (`serve --demo`, `python -m http.server`) and leave them running when
the agent ends. This sweep finds them by what they are, never by a project name: a process is a candidate only when ALL hold:

  1. its command matches a test-server pattern: the built-in BUILTIN_PATTERNS plus `housekeeping.proc_patterns` of the user's config;
  2. it is older than `housekeeping.proc_min_age_h` hours (default 4, never under MIN_AGE_FLOOR_S);
  3. it carries the Budgit session marker in its ENVIRONMENT (procclose BUDGIT_OWNER, never read from argv), or its folder is inside
     a linked worktree of a plain project (housekeeping.worktree_roots()); ppid 1 alone never qualifies;
  4. it is orphaned (parent is launchd/missing: the agent shell that owned it is gone), or its folder is not a live session's folder
     (live_folders(); when the sessions folder is absent or cannot be read: nothing is stopped).

Nothing is stopped either when the config file is unreadable or a `proc_protect_ports` entry is invalid (the protect list cannot be
trusted); the output says why. The command line is never shown: only the program name and the matched pattern.

And it is kept, whatever the rules above say, when it: matches no pattern; listens on (or names in its command) a port of
`housekeeping.proc_protect_ports` (unknown ports are kept too while that list is not empty); has an interactive terminal shell as an
ancestor (a shell with a tty, one the user typed in); is claude or a Claude app helper; is a shell or a reader (grep, tail...) that only
quotes the command; is this process or an ancestor of it; has a pid of 1 or less; or is held by the user (procclose holds, R-1095).

ONE outcome function, `plan()`, is what the dry run, `--stop`, the housekeeping job and the daily stand-up count all call.
`run(stop=True)` re-reads the table, plans again, and sends SIGTERM only to a pid that is still a candidate with the same start
time; it never sends SIGKILL, it reports what is still running. Nothing in this file names a project or a port."""
import math
import os
import re
import signal
import subprocess
import sys
import time

import studiolib as sl

BUILTIN_PATTERNS = ("serve --demo", "-m http.server")
DEFAULT_MIN_AGE_H = 4
MIN_AGE_FLOOR_S = 600                  # the same floor as procclose: nothing younger than ten minutes is ever a candidate
START_TOLERANCE_S = 2.0                # ps start times have 1 s resolution
WAIT_S = 1.0
SHELLS = ("sh", "bash", "zsh", "dash", "fish", "ksh", "tcsh", "csh")
READERS = ("grep", "egrep", "rg", "ugrep", "ps", "pgrep", "tail", "less", "cat", "sed", "awk", "vi", "vim", "nano", "git", "echo")
NO_TTY = ("??", "?", "-", "")
PS = "/bin/ps"
LSOF = "/usr/sbin/lsof"
_PORT_ARG = re.compile(r"(?:^|\s)(?:--port(?:=|\s+)|-p\s+)(\d{1,5})(?=\s|$)")
_HTTP_PORT = re.compile(r"http\.server\s+(\d{1,5})(?=\s|$)")
_LIVE_AUTO = object()


# ---------------------------------------------------------------- settings
def settings(config=None):
    """-> {patterns, protect_ports, invalid_ports, min_age_s} from the `housekeeping` section of the config; a wrong age or pattern
    list falls back to the default, a wrong protect-port entry is listed in invalid_ports (the plan then stops nothing)."""
    config = config if config is not None else sl.load_config()
    hk = config.get("housekeeping") if isinstance(config.get("housekeeping"), dict) else {}
    extra = hk.get("proc_patterns")
    patterns = BUILTIN_PATTERNS + tuple(p.strip() for p in extra if isinstance(p, str) and p.strip()) if isinstance(extra, list) else BUILTIN_PATTERNS
    ports, invalid = set(), []
    given = hk.get("proc_protect_ports")
    if given is not None and not isinstance(given, list):
        invalid.append(given)
    for p in given if isinstance(given, list) else ():
        raw = p
        if isinstance(p, bool):
            invalid.append(raw)
            continue
        if isinstance(p, str) and p.strip().isdigit():
            p = int(p.strip())
        if isinstance(p, int) and 1 <= p <= 65535:
            ports.add(p)
        else:
            invalid.append(raw)
    hours = hk.get("proc_min_age_h")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
        hours = DEFAULT_MIN_AGE_H
    return {"patterns": patterns, "protect_ports": ports, "invalid_ports": invalid, "min_age_s": max(MIN_AGE_FLOOR_S, hours * 3600)}


def hours_to_s(hours):
    """--proc-hours -> seconds; ValueError (with a message to show) unless finite and at least the ten-minute floor (procclose.check_min_age)."""
    import procclose
    try:
        return procclose.check_min_age(float(hours) * 3600)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("--proc-hours must be a finite number of hours, at least %.2f (ten minutes), got %s" % (MIN_AGE_FLOOR_S / 3600, procclose.esc(hours)))


# ---------------------------------------------------------------- the process table
def _ps(argv):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10, env=dict(os.environ, LC_ALL="C"), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def read_env(pid):
    """The ENVIRONMENT of an own-user process as NUL-separated text (never argv); "" when unreadable."""
    import procclose
    if sys.platform == "darwin":
        return procclose.procargs_env(pid)
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/%d/environ" % pid, "rb") as f:
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""
    return ""


def _owner_of(env_text):
    import procclose
    return procclose.owner_from_env(env_text or "")


def parse_table(text):
    """`ps -A -ww -o uid=,pid=,ppid=,lstart=,tty=,command=` -> [{uid, pid, ppid, start, tty, command}]; a pid listed twice is dropped."""
    import procclose
    rows, seen, dup = [], set(), set()
    for line in text.splitlines():
        parts = line.split(None, 9)
        if len(parts) < 9 or not all(p.isdigit() for p in parts[:3]):
            continue
        start = procclose.parse_lstart(parts[3:8])
        if start is None:
            continue
        pid = int(parts[1])
        if pid in seen:
            dup.add(pid)
        seen.add(pid)
        rows.append({"uid": int(parts[0]), "pid": pid, "ppid": int(parts[2]), "start": start, "tty": parts[8],
                     "command": parts[9] if len(parts) > 9 else ""})
    return [r for r in rows if r["pid"] not in dup]


def read_table(run=None, environ=None):
    """The table; each own-user row also carries `owner`, the session marker read from its environment (None when there is none)."""
    rows = parse_table((run or _ps)([PS, "-A", "-ww", "-o", "uid=,pid=,ppid=,lstart=,tty=,command="]))
    uid, environ = os.getuid(), environ or read_env
    for r in rows:
        if r["uid"] == uid:
            r["owner"] = _owner_of(environ(r["pid"]))
    return rows


def live_folders():
    """The folders live Claude sessions work in; None when the sessions folder is absent or cannot be read (then nothing is stopped)."""
    try:
        import livework
        found = livework.live_sessions()
        if found.get("status") != "ok":
            return None
        return [os.path.realpath(s["cwd"]) for s in found["sessions"] if s.get("cwd")]
    except Exception:
        return None


def _lsof(args):
    """lsof's stdout. "" (nothing matched) only for exit 0 with no rows, or exit 1 with empty stdout and empty stderr; None for
    anything else (a warning, a partial failure, a timeout), which the callers keep as unknown."""
    try:
        out = subprocess.run([LSOF, "-nP", "-F", "pn"] + args, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if out.returncode == 0:
        return out.stdout
    if out.returncode == 1 and not out.stdout.strip() and not out.stderr.strip():
        return ""
    return None


def _fields(text):
    """lsof -F output -> [(pid, name)]."""
    pid, found = None, []
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            found.append((pid, line[1:]))
    return found


def probe(pids):
    """-> {pid: {"cwd": path or None, "ports": [int] or None}} for the pids; None = lsof could not say (unknown, never "none")."""
    pids = sorted(set(pids))
    out = {p: {"cwd": None, "ports": None} for p in pids}
    if not pids:
        return out
    joined = ",".join(str(p) for p in pids)
    cwd = _lsof(["-a", "-d", "cwd", "-p", joined])
    if cwd is None:                                              # a lookup that failed: plan() stops nothing (never "no folder")
        for p in pids:
            out[p]["failed"] = True
    for pid, name in _fields(cwd or ""):
        if pid in out:
            out[pid]["cwd"] = name
    listen = _lsof(["-a", "-iTCP", "-sTCP:LISTEN", "-p", joined])
    if listen is None:
        for p in pids:
            out[p]["failed"] = True
    else:
        for p in pids:
            out[p]["ports"] = []
        for pid, name in _fields(listen):
            port = name.rsplit(":", 1)[-1]
            if port.isdigit() and pid in out:
                out[pid]["ports"].append(int(port))
    return out


# ---------------------------------------------------------------- the one outcome function
def _tokens(command):
    return command.split()


def _program(command):
    toks = _tokens(command)
    return os.path.basename(toks[0]).lstrip("-") if toks else ""


def is_claude(command):
    """Claude Code, the Claude app and its helpers, also when run by node (cli.js under a claude package folder). The arguments of
    any other process never make it one, but a claude process whose arguments quote a demo command is still claude."""
    low = command.lower()
    if "claude.app/" in low or "claude helper" in low:
        return True
    toks = _tokens(command)
    if any(os.path.basename(t) in ("claude", "claude-code") for t in toks[:2]):
        return True
    return any(os.path.basename(t) == "cli.js" and "claude" in os.path.dirname(t).lower() for t in toks[:3])


def _quoted_only(command):
    """A shell or a reader that only carries the command as text (an agent's `zsh -c "... serve --demo"`, `grep serve --demo`)."""
    prog = _program(command)
    return prog in SHELLS or prog in READERS


def _command_ports(command):
    return {int(p) for p in _PORT_ARG.findall(command) + _HTTP_PORT.findall(command)}


def _is_terminal_shell(r):
    return r.get("tty") not in NO_TTY and _program(r.get("command") or "") in SHELLS


def _related(a, b):
    sep = os.sep
    return a == b or a.startswith(b.rstrip(sep) + sep) or b.startswith(a.rstrip(sep) + sep)


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def redact(command, patterns):
    """The program name and the matched pattern: the rest of a command line (tokens, paths, names) is never shown."""
    hit = next((p for p in patterns if p in command), None)
    return " ".join(x for x in (_program(command), hit) if x)


def plan(table, now, probe_fn, live, config, holds=(), me=None, min_age_s=None, roots=None, blocked=None):
    """What the sweep would stop: {stop: [{pid, ppid, age_s, ports, cwd, why, command, start}], kept: [{pid, command, reason}], blocked}.
    `live` is the list of live session folders, or None when they could not be read; `roots` the linked worktree folders (a list, a
    function returning one, or None = unknown); `blocked` a reason that keeps every candidate (an unreadable config). The command
    is the redacted one. Only processes that match a test-server pattern are ever listed; nothing else is looked at. No signal is sent."""
    import procclose
    cfg = settings(config)
    if blocked is None and cfg["invalid_ports"]:
        blocked = "proc_protect_ports has an invalid entry (%s); nothing stopped" % procclose.esc(cfg["invalid_ports"])
    if blocked is None and live is None:
        blocked = "the live sessions cannot be read (folder absent or unreadable); nothing stopped"
    min_age = cfg["min_age_s"] if min_age_s is None else max(MIN_AGE_FLOOR_S, min_age_s)
    uid = os.getuid()
    by = {r["pid"]: r for r in table}                         # parents are looked up in the whole table, whatever their uid
    mine = [r for r in table if r.get("uid", uid) == uid]
    safe = procclose.ancestors(table, os.getpid() if me is None else me)
    held = procclose.held_map(table, list(holds))
    out = {"stop": [], "kept": [], "blocked": blocked}
    pending = []
    for r in mine:
        cmd = r.get("command") or ""
        if not any(p in cmd for p in cfg["patterns"]):
            continue
        shown = redact(cmd, cfg["patterns"])
        reason = None
        if r["pid"] <= 1:
            reason = "system pid"
        elif is_claude(cmd):
            reason = "claude"
        elif _quoted_only(cmd):
            reason = "shell or reader quoting the command"
        elif r["pid"] in safe:
            reason = "this process or an ancestor"
        elif now - r["start"] < min_age:
            reason = "young"
        elif procclose.hold_of(table, r, held) is not None:
            reason = "held by the user"
        else:
            seen, cur = set(), r["ppid"]
            while cur in by and cur not in seen:                 # an ancestor the user typed in: a shell with a terminal
                seen.add(cur)
                if _is_terminal_shell(by[cur]):
                    reason = "terminal shell ancestor"
                    break
                cur = by[cur]["ppid"]
        if reason:
            out["kept"].append({"pid": r["pid"], "command": shown, "reason": reason})
        elif blocked:
            out["kept"].append({"pid": r["pid"], "command": shown, "reason": "nothing stopped: " + blocked})
        else:
            pending.append(r)
    facts = probe_fn([r["pid"] for r in pending]) if pending else {}
    if any((facts.get(r["pid"]) or {}).get("failed") for r in pending):      # lsof could not be read: with any list, nothing is stopped
        blocked = out["blocked"] = "lsof could not be read; nothing stopped"
        out["kept"] += [{"pid": r["pid"], "command": redact(r["command"], cfg["patterns"]), "reason": blocked} for r in pending]
        return out
    protect = cfg["protect_ports"]
    worktrees = None
    for r in pending:
        cmd, shown = r["command"], redact(r["command"], cfg["patterns"])
        info = facts.get(r["pid"]) or {}
        cwd, ports = info.get("cwd"), info.get("ports")
        if not r.get("owner"):                                   # no session marker in its environment: only a worktree folder qualifies
            if worktrees is None:
                found = roots() if callable(roots) else roots
                worktrees = [os.path.realpath(x) for x in found] if found is not None else []
            if not (cwd and any(_inside(os.path.realpath(cwd), w) for w in worktrees)):
                out["kept"].append({"pid": r["pid"], "command": shown, "reason": "no Budgit session marker and not in a worktree folder"})
                continue
        named = _command_ports(cmd)
        if protect and (named & protect or set(ports or ()) & protect or (ports is None and not named)):
            out["kept"].append({"pid": r["pid"], "command": shown, "reason": "protected or unknown port"})
            continue
        why = []
        if r["ppid"] in (0, 1) or r["ppid"] not in by:
            why.append("orphan")
        elif cwd:
            real = os.path.realpath(cwd)
            if not any(_related(real, c) for c in live):
                why.append("idle cwd")
        if not why:
            out["kept"].append({"pid": r["pid"], "command": shown, "reason": "owned by a live parent in a live folder"})
            continue
        out["stop"].append({"pid": r["pid"], "ppid": r["ppid"], "age_s": int(now - r["start"]), "ports": sorted(set(ports or ()) | named),
                            "cwd": cwd, "why": why, "command": shown, "start": r["start"]})
    return out


def run(stop=False, reader=None, probe=None, live=_LIVE_AUTO, config=None, now=None, kill=os.kill, sleep=time.sleep, holds=None, me=None,
        min_age_s=None, roots=_LIVE_AUTO):
    """The sweep -> {stop, kept, stopped, still_running, applied[, error, blocked]}. A dry run (the default) signals nothing. With `stop`,
    each candidate is planned again from a fresh table and gets SIGTERM only if it is still a candidate with the same start time;
    the ones still alive after a short wait are reported, never killed."""
    reader = reader or read_table
    probe_fn = probe or globals()["probe"]
    now = time.time() if now is None else now
    blocked = None
    if config is None:
        if sl.unreadable_config_paths():
            blocked = "the config file is unreadable (the protected ports are unknown); nothing stopped"
        config = sl.load_config()
    if live is _LIVE_AUTO:
        live = live_folders()
    if roots is _LIVE_AUTO:
        import housekeeping
        roots = housekeeping.worktree_roots
    rep = {"stop": [], "kept": [], "stopped": [], "still_running": [], "applied": bool(stop)}
    if holds is None:
        import procclose
        try:
            holds = procclose.active_holds()[0]
        except OSError as exc:                                   # an untrusted holds file: nothing is listed, nothing is stopped
            rep["error"] = "holds file unreadable; nothing stopped (%s)" % procclose.esc(exc)
            return rep
    found = plan(reader(), now, probe_fn, live, config, holds=holds, me=me, min_age_s=min_age_s, roots=roots, blocked=blocked)
    rep["stop"], rep["kept"], rep["blocked"] = found["stop"], found["kept"], found.get("blocked")
    if not stop or not found["stop"]:
        return rep
    again = {c["pid"]: c for c in plan(reader(), now, probe_fn, live, config, holds=holds, me=me, min_age_s=min_age_s, roots=roots,
                                       blocked=blocked)["stop"]}
    for c in found["stop"]:
        if not isinstance(c["pid"], int) or c["pid"] <= 1:       # never pid 0 (the process group), 1 (launchd) or a negative (a group)
            continue
        fresh = again.get(c["pid"])
        if not fresh or abs(fresh["start"] - c["start"]) > START_TOLERANCE_S:
            continue
        try:
            kill(c["pid"], signal.SIGTERM)
            rep["stopped"].append(c["pid"])
        except OSError:
            pass
    if rep["stopped"]:
        sleep(WAIT_S)
        alive = {r["pid"]: r for r in reader()}
        starts = {c["pid"]: c["start"] for c in found["stop"]}
        rep["still_running"] = [p for p in rep["stopped"] if p in alive and abs(alive[p]["start"] - starts[p]) <= START_TOLERANCE_S]
    return rep


def count():
    """The number the daily stand-up shows: how many a dry run lists, or None when the sweep could not be read."""
    try:
        rep = run(False)
    except Exception:
        return None
    return None if rep.get("error") or rep.get("blocked") else len(rep["stop"])     # blocked is not "none": the daily shows it as not checked


def procs_selected(only):
    """The procs task is part of a housekeeping run by default and with `--only procs`; any other `--only` leaves it out (no signal)."""
    return not only or "procs" in only


# ---------------------------------------------------------------- the words
def _age(seconds):
    return "%dh" % (seconds // 3600) if seconds < 48 * 3600 else "%dd" % (seconds // 86400)


def _names():
    """The protected project names to hide, or None when they cannot be read (then no folder is shown at all)."""
    try:
        import leak
        return leak.protected_names()
    except Exception:
        return None


def _shown_cwd(cwd, names):
    if not cwd or names is None:
        return None
    import leak
    return leak.hide(cwd, names)


def safe_report(rep):
    """The report for --json: folders pass through leak.hide (or are dropped when the names are unreadable); commands are redacted already."""
    names = _names()
    out = dict(rep)
    out["stop"] = [dict(c, cwd=_shown_cwd(c.get("cwd"), names)) for c in rep.get("stop", [])]
    if names is None:                                            # the names cannot be read: no command field at all
        out["stop"] = [{k: v for k, v in c.items() if k != "command"} for c in out["stop"]]
        out["kept"] = [{k: v for k, v in c.items() if k != "command"} for c in rep.get("kept", [])]
    return out


def safe_json(rep):
    """The --json text: the whole dump goes through leak.hide when the protected names are readable."""
    import json
    text = json.dumps(safe_report(rep), indent=2)
    names = _names()
    if names:
        import leak
        text = leak.hide(text, names)
    return text


def render(rep, lang=None):
    import i18n
    t = lambda key, **kw: i18n.t(key, lang, **kw)
    if rep.get("error"):
        return t("hk.proc_error", why=rep["error"])
    names = _names()
    lines = []
    if rep.get("blocked"):
        lines.append(t("hk.proc_error", why=rep["blocked"]))
    for c in rep["stop"]:
        why = ", ".join(t("hk.proc_r_orphan") if w == "orphan" else t("hk.proc_r_idle") for w in c["why"])
        lines.append(t("hk.proc_line", pid=c["pid"], ppid=c["ppid"], age=_age(c["age_s"]), port=",".join(str(p) for p in c["ports"]) or "-",
                       cwd=_shown_cwd(c.get("cwd"), names) or "-", why=why))
    for pid in rep["stopped"]:
        lines.append(t("hk.proc_stopped", pid=pid))
    for pid in rep["still_running"]:
        lines.append(t("hk.proc_still", pid=pid))
    if not rep["stop"]:
        lines.append(t("hk.proc_none"))
    elif not rep["applied"]:
        lines.append(t("hk.proc_dry"))
    text = "\n".join(lines)
    if names:
        import leak
        text = leak.hide(text, names)
    return text


def housekeeping_text(apply_changes, lang=None, min_age_s=None, only=None):
    """The text the housekeeping job prints (the job passes True: `--apply` stops them), or "" when `--only` leaves the procs task out."""
    if not procs_selected(only):
        return ""
    return render(run(bool(apply_changes), min_age_s=min_age_s), lang)


def main(stop=False, hours=None, as_json=False, apply=False):
    """`budgit housekeeping procs [--stop|--apply] [--proc-hours H]` -> exit code. A dry run unless --stop or --apply."""
    import json
    try:
        min_age = None if hours is None else hours_to_s(hours)
    except ValueError as exc:
        print("budgit housekeeping: %s" % exc, file=sys.stderr)
        return 2
    rep = run(bool(stop or apply), min_age_s=min_age)
    print(safe_json(rep) if as_json else render(rep))
    return 1 if rep.get("error") else 0


# ---------------------------------------------------------------- the protected ports (a setter: it never signals)
def parse_port(value):
    """A port number 1..65535 as written (digits only); ValueError (with a message to show) for anything else."""
    import procclose
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,5}", text) or not 1 <= int(text) <= 65535:
        raise ValueError("not a port number (1 to 65535): %s" % procclose.esc(value))
    return int(text)


def listening_ports(exclude_pids):
    """-> ({port} this user's processes listen on now, leaving out the given pids and every port they listen on (the candidates),
    {pid: {port}} of the given pids); None when lsof cannot be read (the same /usr/sbin/lsof call as the sweep). ASCII digits only."""
    found = _lsof(["-a", "-iTCP", "-sTCP:LISTEN", "-u", str(os.getuid())])
    if found is None:
        return None
    mine, theirs = set(), {}
    for pid, name in _fields(found):
        port = name.rsplit(":", 1)[-1]
        if re.fullmatch(r"[0-9]{1,5}", port) and 1 <= int(port) <= 65535:
            if pid in exclude_pids:
                theirs.setdefault(pid, set()).add(int(port))
            else:
                mine.add(int(port))
    return mine - set().union(*theirs.values()), theirs


def protect_main(add=None, remove=None, listening=False, stop=False, apply=False, lang=None):
    """`budgit housekeeping procs --protect-port N / --unprotect-port N / --protect-listening` -> exit code. Writes
    housekeeping.proc_protect_ports of the config (studiolib.set_proc_protect_ports) and prints the resulting list. It never sends a
    signal and is refused together with --stop or --apply. --protect-listening adds the ports of the processes that the sweep would NOT
    stop; it adds nothing when the plan or lsof cannot be read."""
    import i18n
    t = lambda key, **kw: i18n.t(key, lang, **kw)

    def refuse(why, code=1):
        print(t("hk.proc_ports_err", why=why), file=sys.stderr)
        return code
    if stop or apply:
        return refuse(t("hk.proc_ports_combo"), 2)
    try:
        adds, removes = [parse_port(p) for p in add or ()], [parse_port(p) for p in remove or ()]
    except ValueError as exc:
        return refuse(str(exc), 2)
    skipped = []
    if listening:
        rep = run(False)
        if rep.get("error") or rep.get("blocked"):
            return refuse(rep.get("error") or rep["blocked"])
        candidates = {c["pid"] for c in rep["stop"]}
        found = listening_ports(candidates)
        if found is None:
            return refuse(t("hk.proc_ports_lsof"))
        ports, held = found
        adds += sorted(ports - {p for c in rep["stop"] for p in c.get("ports") or ()})
        skipped = [(c, sorted(set(c.get("ports") or ()) | held.get(c["pid"], set()))) for c in rep["stop"]]
    try:
        if adds or removes:
            saved = sl.set_proc_protect_ports(adds, removes)
        else:
            saved = sorted(settings(sl.load_config())["protect_ports"])
    except (sl.StudioError, OSError) as exc:
        return refuse(str(exc))
    print(t("hk.proc_ports_now", ports=", ".join(str(p) for p in saved)) if saved else t("hk.proc_ports_none"))
    for c, ports in skipped:                         # a candidate is a server the sweep would stop: named, with the line that protects it
        names = _names()
        line = t("hk.proc_ports_skipped", pid=c["pid"], command=(c.get("command") or "-") if names is not None else "-",
                 ports=", ".join(str(p) for p in ports) or "-",
                 flags=" ".join("--protect-port %d" % p for p in ports) or "--protect-port N")
        if names:
            import leak
            line = leak.hide(line, names)
        print(line, file=sys.stderr)
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
