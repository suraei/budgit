"""The verify-file check behind a "ready" claim: one pure function, `check(path, project, session)` -> (ok, reason).

The file format (shared with the legal skill's filing check, so both read it the same way):
  .budgit/verify/<name>.md
    line 1            PASS or FAIL
    reviewer: <id>    must differ from the current session id
    N of M            (or N de M) integers; M counts the real population
    population: <path> rows=<n>     path relative to the project root, or absolute; a .csv (header excluded) or a .jsonl
  .budgit/verify/<name>.findings.jsonl      one JSON object per line: {"id","item","text","status":"open"|"closed","evidence"}
Rules: the first line is PASS; the file was modified today (local time); N == M (or, when the caller passes the reply, the reply
states the same "N de M"); M equals `rows=` and equals the recount of the population file; the reviewer is not this session; no
findings row is open, a closed row has evidence, a row that does not parse fails; a missing findings file means zero findings.
Nothing here raises: any failure is (False, reason). The reasons are for the log and the tests, not for the user."""
import datetime
import json
import os
import re

MAX_BYTES = 1 << 20            # a verify file is a few lines; a bigger one is not read
MAX_POPULATION = 50 << 20      # a population file over this fails the check; it is read in chunks
CHUNK = 1 << 20                # a stretch this long with no line break fails the check
_COUNT = re.compile(r"^[\s>*_#`-]*(\d+)\s+(?:of|de)\s+(\d+)[\s*_`.]*$", re.I)
_REVIEWER = re.compile(r"^[\s>*_#`-]*reviewer\s*:\s*(\S.*?)[\s*_`]*$", re.I)
_POPULATION = re.compile(r"^[\s>*_#`-]*population\s*:\s*(\S.*?)\s+rows\s*=\s*(\d+)[\s*_`]*$", re.I)
FINDINGS_SUFFIX = ".findings.jsonl"


def _lines(path, limit=MAX_BYTES):
    if os.path.getsize(path) > limit:
        raise ValueError("file too large")
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def parse(path):
    """-> dict {first, reviewer, n, m, pop_path, pop_rows} (missing parts are None). Raises on an unreadable file."""
    lines = _lines(path)
    got = {"first": lines[0].strip() if lines else "", "reviewer": None, "n": None, "m": None, "pop_path": None, "pop_rows": None}
    for line in lines[1:]:
        if got["reviewer"] is None:
            r = _REVIEWER.match(line)
            if r:
                got["reviewer"] = r.group(1).strip()
                continue
        if got["n"] is None:
            c = _COUNT.match(line)
            if c:
                got["n"], got["m"] = int(c.group(1)), int(c.group(2))
                continue
        if got["pop_path"] is None:
            p = _POPULATION.match(line)
            if p:
                got["pop_path"], got["pop_rows"] = p.group(1).strip().strip("`"), int(p.group(2))
    return got


def recount(path):
    """Rows in a population file: a .csv without its header line, a .jsonl as it is; blank lines never count. Raises on any other kind."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".csv", ".jsonl"):
        raise ValueError("population must be .csv or .jsonl")
    if not os.path.isfile(path):                              # a FIFO, a device or a folder is never opened
        raise OSError("population file missing")
    if os.path.getsize(path) > MAX_POPULATION:
        raise ValueError("population file too large")
    rows, total, carry = 0, 0, b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_POPULATION:
                raise ValueError("population file too large")
            parts = (carry + chunk).split(b"\n")
            carry = parts.pop()
            if len(carry) >= CHUNK:
                raise ValueError("no line break within %d bytes" % CHUNK)
            rows += sum(1 for p in parts if p.strip())
    if carry.strip():
        rows += 1
    return max(rows - 1, 0) if ext == ".csv" else rows


def inside(root, path):
    """True when the real path of `path` lies under the real path of `root` (symlinks and `..` are followed first)."""
    try:
        root, path = os.path.realpath(root), os.path.realpath(path)
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def findings_ok(path):
    """-> (ok, reason) for the findings file next to a verify file; a missing file means zero findings."""
    if not os.path.exists(path):
        return True, "no findings file"
    try:
        lines = _lines(path)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return False, "findings file unreadable (%s)" % exc.__class__.__name__
    for no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return False, "findings row %d does not parse" % no
        if not isinstance(row, dict) or row.get("status") not in ("open", "closed"):
            return False, "findings row %d has no valid status" % no
        if row["status"] == "open":
            return False, "findings row %d is open" % no
        evidence = row.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            return False, "findings row %d is closed without evidence" % no
    return True, "findings closed"


def _today(path, today):
    when = datetime.date.fromtimestamp(os.stat(path).st_mtime)
    return when == (today or datetime.date.today())


def check(path, project, session, reply=None, today=None):
    """-> (ok, reason). `reply` (the text going to the user) lets N < M pass when it states the same "N de M"; without it N must equal M.
    `today` is for tests. Never raises."""
    try:
        if not isinstance(path, str) or not os.path.isfile(path):
            return False, "verify file missing"
        got = parse(path)
        if got["first"] != "PASS":
            return False, "first line is not PASS"
        if not _today(path, today):
            return False, "verify file is not from today"
        if not got["reviewer"]:
            return False, "no reviewer line"
        if isinstance(session, str) and got["reviewer"] == session:
            return False, "reviewer is the current session"
        if got["n"] is None or got["m"] is None:
            return False, "no N of M line"
        n, m = got["n"], got["m"]
        if n < 1 or m < 1 or n > m:
            return False, "N of M is not a valid count"
        if got["pop_path"] is None:
            return False, "no population line"
        pop = got["pop_path"]
        if not os.path.isabs(pop):
            pop = os.path.join(project, pop)
        if not inside(project, pop):
            return False, "population path is outside the project"
        try:
            counted = recount(pop)
        except (OSError, ValueError):
            return False, "population file missing or unreadable"
        if m != got["pop_rows"] or m != counted:
            return False, "M %d does not match population rows=%d and recount %d" % (m, got["pop_rows"], counted)
        if reply is not None:                                # a reply must state this file's exact N and M
            said = isinstance(reply, str) and re.search(r"(?<!\d)%d\s+(?:de|of)\s+%d(?!\d)" % (n, m), reply, re.I)
            if not said:
                return False, "the reply does not state %d of %d" % (n, m)
        elif n < m:
            return False, "N %d is below M %d" % (n, m)
        ok, why = findings_ok(os.path.splitext(path)[0] + FINDINGS_SUFFIX)
        if not ok:
            return False, why
        return True, "pass"
    except Exception as exc:
        return False, "verify file unreadable (%s)" % exc.__class__.__name__
