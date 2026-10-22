"""Guard denies in .budgit/audit.log + `budgit denies` (counts per guard, false-positive marks).
A deny line never holds the full command: sha256 of it + its first 60 chars with digits and long tokens masked."""
import datetime
import hashlib
import json
import os
import re

import studiolib as sl

DENY_TAG = "guard-deny"
FP_TAG = "guard-deny-fp"
_LINE = re.compile(r'^(\S+) %s (\S+) (\S+) (\S+) sha256=(\S+) cmd="(.*)" why="(.*)"$' % DENY_TAG)
_FP = re.compile(r"^(\S+) %s (\S+) (.*)$" % FP_TAG)


_VALUE = r"""(?:"[^"]*"|'[^']*'|\S+)"""
# NAME=value where the name looks like a secret (PASS, PWD, SECRET, TOKEN, KEY, AUTH, CRED... also inside DB_PASS), any length
_SECRET_ASSIGN = re.compile(r"(?i)([A-Za-z0-9_-]*(?:pass|pwd|secret|token|key|auth|cred)[A-Za-z0-9_-]*)=" + _VALUE)
# `sshpass -p value`, `mysql -pvalue` / `mysql -p value`
_PASS_FLAG = re.compile(r"(?i)\b((?:sshpass|mysql[\w-]*|mariadb[\w-]*)\b[^|;&]*?\s)-p\s*" + _VALUE)
# `redis-cli -a pw`; `--pass pw` / `--password pw` / `--passwd pw` (the `--password=pw` form is _SECRET_ASSIGN's)
_REDIS_A = re.compile(r"(?i)\b(redis-cli\b[^|;&]*?\s)-a\s+(?!-)" + _VALUE)
_LONG_PASS = re.compile(r"(?i)(?<![\w-])(--(?:pass|password|passwd))\s+(?!-)" + _VALUE)
# the password in `scheme://user:pass@host` or `user:pass@host`
_USERINFO = re.compile(r"\b([A-Za-z0-9._%+-]+):[^\s:@/'\"]+@")


def scrub(text, limit):
    """One line, known secret shapes masked (also short ones, P5 r9 D43), long tokens and every digit masked, cut."""
    text = " ".join(sl.redact(text or "").split())
    text = _SECRET_ASSIGN.sub(r"\1=[REDACTED]", text)
    text = _PASS_FLAG.sub(r"\1-p [REDACTED]", text)
    text = _REDIS_A.sub(r"\1-a [REDACTED]", text)
    text = _LONG_PASS.sub(r"\1 [REDACTED]", text)
    text = _USERINFO.sub(r"\1:[REDACTED]@", text)
    text = re.sub(r"[A-Za-z0-9_+/=.-]{24,}", "[..]", text)
    return re.sub(r"\d", "#", text).replace('"', "'")[:limit]


def _subject(tool, tool_input):
    if tool == "Bash":
        return tool_input.get("command") or ""
    return tool_input.get("file_path") or tool_input.get("path") or json.dumps(tool_input, sort_keys=True)


def log_denies(project, tool, tool_input, named):
    """named = [(guard, (\"deny\", reason)), ...]; one line per guard that denied."""
    text = _subject(tool, tool_input or {})
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    head = scrub(text, 60)
    for guard, decision in named:
        stamp = sl.now()
        deny_id = os.urandom(4).hex()          # two identical denies in one millisecond still get two ids
        sl.audit(project, '%s %s %s %s sha256=%s cmd="%s" why="%s"'
                 % (DENY_TAG, deny_id, tool, guard, digest, head, scrub(decision[1], 100)))


def _parse(project):
    try:
        lines = sl.read_text(os.path.join(sl.studio_dir(project), "audit.log")).splitlines()
    except OSError:
        lines = []
    denies, fps = [], {}
    for line in lines:
        m = _LINE.match(line)
        if m:
            denies.append(dict(zip(("ts", "id", "tool", "guard", "sha", "cmd", "why"), m.groups())))
            continue
        m = _FP.match(line)
        if m:
            fps[m.group(2)] = m.group(3)
    return denies, fps


def _cutoff(since):
    if since in (None, "", "all"):
        return None
    m = re.fullmatch(r"(\d+)([dh])", since)
    if not m:
        raise sl.StudioError("--since takes Nd, Nh or all (got %r)" % since)
    delta = datetime.timedelta(**{"days" if m.group(2) == "d" else "hours": int(m.group(1))})
    return (datetime.datetime.utcnow() - delta).strftime("%Y-%m-%dT%H:%M:%S")


def recent_count(project, since="7d"):
    """How many guard denies fall in the window (same reader and cutoff as `budgit denies`)."""
    cutoff = _cutoff(since)
    return sum(1 for d in _parse(project)[0] if cutoff is None or d["ts"] >= cutoff)


def summary(project, since="7d"):
    denies, fps = _parse(project)
    cutoff = _cutoff(since)
    denies = [d for d in denies if cutoff is None or d["ts"] >= cutoff]
    window = "all time" if cutoff is None else "the last " + since
    out = ["budgit denies: %d in %s (.budgit/audit.log)" % (len(denies), window)]
    counts = {}
    for d in denies:
        total, fp = counts.get(d["guard"], (0, 0))
        counts[d["guard"]] = (total + 1, fp + (d["id"] in fps))
    for guard, (total, fp) in sorted(counts.items(), key=lambda kv: (-kv[1][0], kv[0])):
        out.append("  %-16s %4d%s" % (guard, total, "   (%d false positive%s)" % (fp, "" if fp == 1 else "s")
                                       if fp else ""))
    if denies:
        out.append("latest:")
        out += ['  %s %s %-14s %s "%s"%s' % (d["id"], d["ts"][:16], d["guard"], d["tool"], d["cmd"],
                                             "  [FP]" if d["id"] in fps else "") for d in denies[-5:]]
        out.append('mark a false positive: budgit denies --fp <id> --note "why it was wrong"')
    return "\n".join(out)


def mark_fp(project, deny_id, note):
    denies, _ = _parse(project)
    if not any(d["id"] == deny_id for d in denies):
        raise sl.StudioError("no deny with id %s in .budgit/audit.log (see: budgit denies --since all)" % deny_id)
    note = " ".join(sl.redact(note or "").split())[:200]
    if not note:
        raise sl.StudioError("--note is required with --fp")
    sl.audit(project, "%s %s %s" % (FP_TAG, deny_id, note))
    return "budgit denies: %s marked as a false positive" % deny_id
