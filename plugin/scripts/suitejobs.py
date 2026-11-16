"""R-1101: a suite a subagent starts with `budgit suite --background` and follows with `budgit wait <job>`.

A foreground Bash call ends at 600 s and the R-847 stop rule blocks a turn that ends with a marked process alive, so a long
suite cannot be waited on in one call and cannot be left running. The job is the middle path: `start` runs the suite under a
small runner, detached but marked (BUDGIT_OWNER), and records {job id, pid, pid start time, owner session, nonce, exit path, log
path, started} in a job file under the Budgit home (0600, atomic). `wait` polls for at most 540 s per call and prints running /
passed / failed with the log tail; it is called again until the job ends. The stop hook still blocks while the job lives
(live_jobs) and names `budgit wait <job>` as the way out.

Files, all in <home>/suite-jobs/ (a real folder of the user, mode 0700, never a link): <id>.json (the record), <id>.log (the
suite's output), <id>.exit (written by the runner when the suite ends: {"code": N, "nonce": ...}). The runner is
`python3 suitejobs.py run <id>.json -- <argv>`: it takes the nonce from its environment (and removes it from the suite's), reads
the exit path from the record, and needs only the standard library. `wait` trusts an .exit only when its nonce matches the record
AND the job's pid has ended.

The owner is the session marker read the way procclose reads it: from the process table (the environment a process was started
with), never from this process's own BUDGIT_OWNER, so `export BUDGIT_OWNER=x` cannot move a job out of the stop hook's sight.
"""
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

MAX_WAIT_S = 540                    # one `budgit wait` call stays under the 600 s cap of a foreground Bash call
TAIL_LINES = 40
TAIL_BYTES = 64 * 1024
POLL_S = 1.0
START_TOLERANCE_S = 2.0             # ps start times have 1 s resolution
OWNER_VAR = "BUDGIT_OWNER"
NONCE_VAR = "BUDGIT_JOB_NONCE"
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
EXIT_PASSED, EXIT_FAILED, EXIT_STILL_RUNNING, EXIT_REFUSED, EXIT_USAGE = 0, 1, 3, 2, 64


class JobError(Exception):
    pass


def jobs_dir():
    import studiolib as sl
    return os.path.join(sl.home_state(), "suite-jobs")


def ensure_dir():
    """The jobs folder, made if missing. -> its path; JobError for a link, a folder of another user or one others can open."""
    d = jobs_dir()
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        st = os.lstat(d)
    except OSError as exc:
        raise JobError("the jobs folder %s cannot be used: %s" % (d, exc))
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise JobError("the jobs folder %s is not a plain folder; refused" % d)
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise JobError("the jobs folder %s must belong to the user and be mode 0700; refused" % d)
    return d


def session_marker():
    """The session's BUDGIT_OWNER as the process table shows it for this process or its nearest marked ancestor, else None.
    The table holds the environment each process STARTED with, so an `export` in the current shell does not change it."""
    import procclose
    try:
        table = procclose.read_table()
    except Exception:
        return None
    by = {r["pid"]: r for r in table}
    cur, seen = os.getpid(), set()
    while cur in by and cur not in seen:
        seen.add(cur)
        if by[cur].get("owner"):
            return by[cur]["owner"]
        cur = by[cur]["ppid"]
    return None


def owner():
    value = session_marker()
    if not value:
        raise JobError("this session carries no BUDGIT_OWNER marker, so a job could not be followed or closed by the stop hook; refused")
    return value


def _paths(job_id):
    base = os.path.join(jobs_dir(), job_id)
    return base + ".json", base + ".log", base + ".exit"


def _atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def pid_start(pid):
    """The start time (epoch seconds) of a live pid from plain `ps`, else None. Compared with the recorded one: pid reuse."""
    import procclose
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=10,
                             env=dict(os.environ, LC_ALL="C"), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return procclose.parse_lstart(out.stdout.split()) if out.returncode == 0 and out.stdout.strip() else None


def _kill(child):
    for fn in (lambda: os.killpg(child.pid, signal.SIGKILL), child.kill):
        try:
            fn()
        except (OSError, ValueError):
            pass
    try:
        child.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def start(argv, project):
    """Run `argv` (the suite command line) detached under a runner, marked with the session's owner. -> the job id.
    Never leaves a runner the stop hook cannot see: when the start time or the record cannot be had, the child is killed."""
    me = owner()
    ensure_dir()
    job_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    rec_path, log_path, exit_path = _paths(job_id)
    nonce = secrets.token_hex(16)
    logfd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    env = dict(os.environ)
    env[OWNER_VAR], env[NONCE_VAR] = me, nonce
    try:
        child = subprocess.Popen([sys.executable, "-I", "-S", os.path.abspath(__file__), "run", rec_path, "--"] + list(argv), stdin=subprocess.DEVNULL,
                                 stdout=logfd, stderr=subprocess.STDOUT, env=env, cwd=project or None, start_new_session=True)
    finally:
        os.close(logfd)
    try:
        began = pid_start(child.pid)
        if began is None:
            raise JobError("the start time of the job process could not be read; the job was stopped")
        _atomic(rec_path, json.dumps({"job": job_id, "pid": child.pid, "pid_start": began, "owner": me, "nonce": nonce, "exit": exit_path,
                                      "log": log_path, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "project": project}, indent=1) + "\n")
    except BaseException as exc:
        _kill(child)
        if isinstance(exc, JobError):
            raise
        raise JobError("the job record could not be written (%s); the job was stopped" % exc)
    return job_id


def read_job(job_id):
    """-> the validated record; JobError when the id is malformed, the file is missing or damaged."""
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise JobError("not a job id: %r" % (job_id,))
    rec_path = _paths(job_id)[0]
    try:
        with open(rec_path, encoding="utf-8") as fh:
            rec = json.load(fh)
    except FileNotFoundError:
        raise JobError("no job %s" % job_id)
    except (OSError, ValueError, RecursionError, MemoryError, UnicodeError):
        raise JobError("job %s: the record is damaged" % job_id)
    if not isinstance(rec, dict) or rec.get("job") != job_id or not isinstance(rec.get("pid"), int) or isinstance(rec.get("pid"), bool) \
            or not isinstance(rec.get("nonce"), str) or not rec["nonce"]:
        raise JobError("job %s: the record is damaged" % job_id)
    return rec


def read_exit(job_id, nonce):
    """-> the runner's result {"code": N} when the suite ended and the file carries this job's nonce, else None."""
    try:
        with open(_paths(job_id)[2], encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError, RecursionError, MemoryError, UnicodeError):
        return None
    ok = isinstance(got, dict) and isinstance(got.get("code"), int) and not isinstance(got.get("code"), bool) \
        and isinstance(nonce, str) and nonce and got.get("nonce") == nonce
    return got if ok else None


def tail(job_id, lines=TAIL_LINES):
    """The last `lines` of the log read from its end (at most 64 KB), every control character shown as escape text."""
    import studiolib as sl
    try:
        with open(_paths(job_id)[1], "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - TAIL_BYTES))
            data = fh.read(TAIL_BYTES)
    except OSError:
        return ""
    return "\n".join(sl._show_safe(line) for line in data.decode("utf-8", "replace").splitlines()[-lines:])


def check(job_id, me=None):
    """-> (state, code): state is running / passed / failed. JobError (to refuse) for a job of another session, or whose pid
    is now another process with no result. A result counts only with this job's nonce AND once the pid has ended; a dead runner
    with no valid result is failed with code None (it was killed)."""
    rec = read_job(job_id)
    if rec.get("owner") != (me or owner()):
        raise JobError("job %s belongs to another session; refused" % job_id)
    began = pid_start(rec["pid"])
    recorded = rec.get("pid_start")
    alive = began is not None and (not isinstance(recorded, (int, float)) or abs(began - recorded) <= START_TOLERANCE_S)
    got = read_exit(job_id, rec["nonce"])
    if alive:
        return "running", None
    if got is not None:
        return ("passed" if got["code"] == 0 else "failed"), got["code"]
    if began is None:
        return "failed", None
    raise JobError("job %s: pid %d is now another process (pid reuse); refused" % (job_id, rec["pid"]))


def wait(job_id, max_s=MAX_WAIT_S, me=None, poll=POLL_S, clock=time.monotonic, sleep=time.sleep):
    """Poll until the job ends or `max_s` seconds pass (checked at least once). -> (state, code, tail text)."""
    if max_s > MAX_WAIT_S or max_s < 0:
        raise JobError("--max must be between 0 and %d seconds (a foreground call ends at 600 s)" % MAX_WAIT_S)
    ensure_dir()
    deadline = clock() + max_s
    while True:
        state, code = check(job_id, me)
        if state != "running" or clock() >= deadline:
            return state, code, tail(job_id)
        sleep(min(poll, max(0.0, deadline - clock())))


RUNNER_SHAPE = re.compile(r"(?i)^\S*python[\d.]*(?:\s+-[A-Za-z]+)*\s+\S*suitejobs\.py run \S+ -- ")    # R-1121: the runner starts as `python -I -S suitejobs.py run ...`


def runner_rows(table, sid, since, exclude=()):
    """Rows of this session's runner processes (marker + the argv shape of `suitejobs.py run <record> -- ...`), started since `since`.
    For the stop hook when the job records cannot be read: it closes what it can recognise without them."""
    return [r for r in table if r.get("owner") == sid and r["pid"] not in exclude and RUNNER_SHAPE.match(" ".join(str(r.get("command") or "").split()))
            and (since is None or r["start"] + START_TOLERANCE_S >= since)]


def report(job_id, max_s=MAX_WAIT_S):
    """The text and exit code of `budgit wait`: 0 passed, 1 failed, 3 still running, 2 refused, 64 bad --max."""
    try:
        state, code, text = wait(job_id, max_s)
    except JobError as exc:
        return "budgit wait: %s" % exc, (EXIT_USAGE if "--max" in str(exc) else EXIT_REFUSED)
    out = "%s\nexit code: %s\n" % (state, "-" if code is None and state == "running" else ("killed" if code is None else code))
    out += "last %d log lines of job %s:\n%s" % (TAIL_LINES, job_id, text)
    if state == "running":
        out += "\n(still running: call `budgit wait %s` again)" % job_id
    return out, {"passed": EXIT_PASSED, "failed": EXIT_FAILED, "running": EXIT_STILL_RUNNING}[state]


def live_jobs(table, sid, since, exclude=()):
    """The stop hook's view: rows of the process table that are a still-running job of session `sid`, started since `since`
    (the subagent's own start; None = no limit). A job with a valid result, or whose pid is gone or reused, is not live; a job
    record with no start time counts as live. RAISES (JobError or OSError) when the jobs cannot be read: the caller fails closed."""
    out = []
    try:
        ensure_dir()
        names = os.listdir(jobs_dir())
    except FileNotFoundError:
        return out
    by = {r["pid"]: r for r in table}
    for name in names:
        if not name.endswith(".json") or not JOB_ID.fullmatch(name[:-5]):
            continue
        rec = read_job(name[:-5])
        row = by.get(rec["pid"])
        if not row or row["pid"] in exclude or rec.get("owner") != sid or row.get("owner") != sid or read_exit(rec["job"], rec["nonce"]) is not None:
            continue
        recorded = rec.get("pid_start")
        if isinstance(recorded, (int, float)) and abs(row["start"] - recorded) > START_TOLERANCE_S:
            continue
        if since is not None and row["start"] + START_TOLERANCE_S < since:
            continue
        out.append(dict(row, job=rec["job"]))
    return out


def _run(rec_path, argv):
    """The runner: run the suite (without the nonce in its environment), hand it TERM when asked, and write the result file
    last, at the path the record names, with the nonce."""
    nonce = os.environ.pop(NONCE_VAR, "")
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL)

    def forward(signum, _frame):
        try:
            child.send_signal(signal.SIGTERM)
        except OSError:
            pass
    signal.signal(signal.SIGTERM, forward)
    code = child.wait()
    rec, deadline = None, time.time() + 15
    while rec is None and time.time() < deadline:               # the starter writes the record right after the fork
        try:
            with open(rec_path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            time.sleep(0.2)
    if not isinstance(rec, dict) or not isinstance(rec.get("exit"), str) or os.path.dirname(rec["exit"]) != os.path.dirname(rec_path) or not nonce:
        return 1
    _atomic(rec["exit"], json.dumps({"code": code if code >= 0 else 128 - code, "nonce": nonce, "ended": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 4 and sys.argv[1] == "run" and sys.argv[3] == "--":
        sys.exit(_run(sys.argv[2], sys.argv[4:]))
    sys.stderr.write("suitejobs: internal runner, use `budgit suite --background`\n")
    sys.exit(EXIT_USAGE)
