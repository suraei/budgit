"""R-889a: `budgit ext-review`, the one wrapper for every external-model call (codex, gemini).

Why: on 9 Nov 2026 three Gemini cross-reviews hung 1h45 to 2h13 on an overloaded machine and nothing closed them, because
nothing knew they were ours. Here every call has a deadline, its own process group, and an owner tag the R-847 sweep reads.

Contract: stdin = the diff; the reviewer's text goes to --out only when the tool finished with a verdict (never a partial
file); exit codes below are distinct; no child outlives the wrapper (group TERM, short grace, KILL; also on normal exit and
when the wrapper itself is terminated). What varies is read at run time: the CLIs on PATH, the OS (POSIX groups or the
Windows process-group flag), the tool's own quota state (from its output), the machine's speed (the user's --timeout)."""
import argparse
import errno
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import codexlimit
import procclose

DEFAULT_TIMEOUT_S = 600         # a review that has not answered in 10 minutes is not coming
HARD_CAP_S = 1800                # --timeout may lower the default, never raise it past this
KILL_GRACE_S = 3.0               # between TERM to the group and KILL
EXIT_OK, EXIT_USAGE, EXIT_NO_TOOL, EXIT_TIMEOUT, EXIT_UNAVAILABLE, EXIT_FAILED = 0, 2, 3, 4, 5, 6
EXIT_PROTECTED = 7
TOOLS = ("codex", "gemini")     # the order `auto` tries
UNAVAILABLE_RE = re.compile(r"quota|\b429\b|\b50[0-4]\b|rate.?limit|resource.?exhausted|too many requests|overloaded", re.I)
WINDOWS = os.name == "nt"
POLL_S = 0.2                     # how often a running tool's stderr and stdout are read (R-1051)
TAIL_LINES, TAIL_CHARS = 5, 700  # what a no-verdict line carries of the tool's stderr
# R-1051: STDERR output that means the tool cannot answer, whatever the clock says. (reason, pattern). Only stderr is ever
# scanned: stdout is the answer being written, and a verdict may quote "login required" or "(y/n)" or "quota". A line that only
# talks about a retry is transient (the tool backs off and tries again); a retry line that carries a rate-limit answer is counted.
RETRY_RE = re.compile(r"retry|retrying|attempt \d|backoff", re.I)
NON_ANSWERS = (
    ("needs sign-in (an auth or consent prompt is waiting for input)",
     re.compile(r"waiting for (?:user )?auth|opening authentication|please visit the following url|enter the authorization code"
                r"|please set an auth method|login required|not logged in|do you want to continue|\(y/n\)|\[y/n\]", re.I)),
    ("model not available",
     re.compile(r"model.{0,60}(?:not found|not supported|does not exist|is not a valid)|unknown model|invalid model"
                r"|models/\S+ is not found", re.I)),
    ("quota exhausted",
     re.compile(r"quota exceeded|exceeded your (?:current )?quota|exhausted your (?:daily )?(?:quota|capacity)|resource_exhausted"
                r"|usage limit|daily limit", re.I)),
)
FALLBACK_RE = re.compile(r"Model fallback activated: switching from (\S+) to (\S+)")
FALLBACK_LIMIT = 3
ATTEMPT_503_RE = re.compile(r"Attempt (\d+) failed with status 503")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
RETRY_LIMIT = 3                 # retry lines after a 429 / quota answer before the tool is called unavailable
STDOUT_PROMPT_BYTES = 4000      # a prompt waiting for input is short; a long stdout is an answer being written
RETRY_429_RE = re.compile(r"\b429\b|rate.?limit|resource.?exhausted|too many requests|quota", re.I)   # a rate-limit answer (not "500 ms")
_B = r"(?<![A-Za-z0-9])"       # not `\b`: `_` is a word character, so `\b` would miss access_token= and GEMINI_API_KEY=
_SECRET_RES = (
    # name, optional closing quote (JSON), `=` or `:`, optional opening quote, optional scheme, then the value
    re.compile(_B + r"((?i:(?:api[_-]?key|token|secret|password|passwd|authorization|credential)s?))(\"?\s*[=:]\s*\"?)(?:(?i:bearer|basic|token|digest|negotiate)\s+)?[^\s\"',;]+"),
    re.compile(r"(?i)([?&](?:key|token|access_token|refresh_token|id_token|client_secret|api_key|code|sig|signature)=)[^&\s\"']+"),
    re.compile(_B + r"(?:AIza[0-9A-Za-z_-]{20,}|ya29\.[0-9A-Za-z_-]{20,}|GOCSPX-[0-9A-Za-z_-]{10,}|sk-[0-9A-Za-z_-]{16,}"
               r"|gh[pousr]_[0-9A-Za-z]{20,}|xox[abprs]-[0-9A-Za-z-]{10,}"
               r"|eyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{5,})"),
)
# The general rule (R-1051 r3): ANY run of OPAQUE_MIN or more characters (no space or quote/bracket/`?&=` delimiter) that mixes at
# least two of upper, lower and digit is a secret unless it looks like a path or a hyphenated name. Symbols `/ + = _ - .` may be
# inside the run, so a base64 secret with `/` or `+` cannot split into short pieces and escape.
OPAQUE_MIN = 24
_RUN_RE = re.compile(r"[^\s\"',;()<>\[\]{}?&=]{%d,}={0,2}" % OPAQUE_MIN)
_SEP_RE = re.compile(r"[/\-_.:+]")


def _wordlike(piece):
    """A short piece of a path or name: not a long mixed-case or letter-digit jumble."""
    if len(piece) > 20:
        return False
    upper = sum(c.isupper() for c in piece)
    lower = sum(c.islower() for c in piece)
    flips = sum(1 for a, b in zip(piece, piece[1:]) if a.isalpha() != b.isalpha() and (a.isdigit() or b.isdigit()))
    return not (upper >= 4 and lower >= 1) and flips <= 2


def _looks_like_path_or_name(run):
    """Path exemption: a run that starts with `/`, `~/`, `./` or `../`, or whose pieces (split on / - _ . : +) are all short
    words (a model name, a dotted module, a relative path, a URL with a plain path). A random secret has long or mixed pieces."""
    if run.startswith(("/", "~/", "./", "../")):
        return True
    rest = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/]*", "", run)          # a URL: judge what follows the host
    pieces = [p for p in _SEP_RE.split(rest) if p]
    return len(pieces) >= 2 and all(_wordlike(p) for p in pieces)


def _opaque(m):
    run = m.group(0)
    classes = bool(re.search(r"[A-Z]", run)) + bool(re.search(r"[a-z]", run)) + bool(re.search(r"\d", run))
    if classes < 2 or _looks_like_path_or_name(run):
        return run
    return "[redacted]"


_API_KEY_SPACE_RE = re.compile(r"(?i)\bapi[_ -]?key\s+(?!(?:not|is|was|has|required|invalid|missing|expired|rejected|revoked|provided|valid)\b)"
                               r"[A-Za-z0-9_.+/-]{8,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s\"']+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_PROJECT_RE = re.compile(r"(?i)\b(projects/)[^/\s\"',;]+")


def redact(text):
    """Secrets out of a line of tool output before it is shown: key=value and JSON pairs (also access_token, client_secret,
    GEMINI_API_KEY), Basic and Bearer credentials, `API key <value>`, URL query secrets, known token shapes, long opaque runs,
    emails and cloud project ids."""
    text = _SECRET_RES[0].sub(lambda m: "%s%s[redacted]" % (m.group(1), m.group(2)), text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _API_KEY_SPACE_RE.sub("API key [redacted]", text)
    text = _SECRET_RES[1].sub(lambda m: m.group(1) + "[redacted]", text)
    for rx in _SECRET_RES[2:]:
        text = rx.sub("[redacted]", text)
    text = _EMAIL_RE.sub("[email]", text)
    text = _PROJECT_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    return _RUN_RE.sub(_opaque, text)


_FRAME_RE = re.compile(r"^(at\s|\(?(file://)?\S+:\d+:\d+\)?$)")


def tail_of(text, lines=TAIL_LINES, chars=TAIL_CHARS):
    """The last `lines` non-empty lines of `text`, redacted, at most `chars` characters, joined with ' | '."""
    raw = [l.strip() for l in text.splitlines() if l.strip()]
    kept = [l for l in raw if not _FRAME_RE.match(l)]      # R-1053: the error line, not the stack under it
    rows = [redact(l) for l in (kept or raw)][-lines:]
    out = " | ".join(rows)
    return out[-chars:]


class Watch:
    """Reads a tool's two output files while it runs (they only grow). `scan()` returns (status, reason, line) for the first
    known non-answer seen so far, else None. Offsets are kept, so a flood of stderr is read once and never blocks the tool
    (it writes to a file, not a pipe)."""

    def __init__(self, raw_path, err_path):
        self.raw_path, self.err_path = raw_path, err_path
        self.off = {"err": 0, "out": 0}
        self.carry = {"err": "", "out": ""}
        self.out_bytes = 0
        self.retries = 0
        self.fallbacks = 0
        self.overloads = set()

    def _new(self, key, path):
        try:
            with open(path, "rb") as f:
                f.seek(self.off[key])
                data = f.read()
        except OSError:
            return []
        self.off[key] += len(data)
        if key == "out":
            self.out_bytes = self.off[key]
        text = self.carry[key] + data.decode("utf-8", "replace")
        parts = text.split("\n")
        self.carry[key] = parts.pop()
        # a prompt often has no newline: the unfinished line is looked at too, as a partial (never counted twice)
        return [(p, True) for p in parts] + ([(self.carry[key], False)] if self.carry[key] else [])

    def scan(self):
        self._new("out", self.raw_path)          # only to keep the stdout size; never scanned
        for line, complete in self._new("err", self.err_path):
            for reason, rx in NON_ANSWERS:       # a quota line that also says "retry" is still a quota stop
                if rx.search(line):
                    return reason, redact(line.strip())[:300]
            # a tool that backs off once is fine; one that keeps retrying after rate-limit answers is not coming back
            m = FALLBACK_RE.search(line)      # R-1053: the default model keeps failing and the CLI loops over fallbacks
            if complete and m:
                self.fallbacks += 1
                if self.fallbacks >= FALLBACK_LIMIT:
                    return ("gemini keeps switching models (%s -> %s): the key has no quota or capacity for its default model; "
                            "try --model" % (redact(m.group(1)), redact(m.group(2)))), redact(line.strip())[:300]
            a = ATTEMPT_503_RE.search(line)
            if complete and a:     # one counting rule: distinct "Attempt k failed" numbers; an echoed line is not a new try
                self.overloads.add(a.group(1))
                if len(self.overloads) >= RETRY_LIMIT:
                    return ("gemini: the model is overloaded (503 UNAVAILABLE) after %d tries; try later or --model" % len(self.overloads),
                            redact(line.strip())[:300])
            if complete and RETRY_RE.search(line) and RETRY_429_RE.search(line):
                self.retries += 1
                if self.retries >= RETRY_LIMIT:
                    return "quota exhausted (the tool retried %d times after rate-limit answers)" % self.retries, redact(line.strip())[:300]
        return None

    def stdout_size(self):
        try:
            return os.path.getsize(self.raw_path)
        except OSError:
            return self.out_bytes


def effective_timeout(requested):
    """The default, lowered by --timeout, never above the hard cap, never below 1 s."""
    if requested is None:
        return DEFAULT_TIMEOUT_S
    return max(1, min(float(requested), HARD_CAP_S))


DEBUG_TAIL_LINES, DEBUG_TAIL_CHARS = 15, 2000


def command(tool, prompt, out_tmp, debug=False, model=None):
    """The read-only invocation. Codex also writes its final message to `out_tmp` (stdout carries progress noise)."""
    pick = ["-m", model] if model else []
    if tool == "codex":
        return ["codex", "exec", "-s", "read-only", "--skip-git-repo-check"] + pick + ["-o", out_tmp, "--", prompt]
    argv = ["gemini", "-p", prompt, "--approval-mode", "plan", "--skip-trust", "-o", "text"] + pick
    return argv + ["--debug"] if debug else argv  # R-1053: trust only the empty work folder, this run


# R-1054: the outside tool gets only these variables (and LC_*); path-bearing ones (PWD, OLDPWD, GIT_DIR, ...) leak the repo.
CHILD_ENV_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "TMPDIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS", "OPENAI_API_KEY", "CODEX_HOME",
    "SYSTEMROOT", "USERPROFILE", "APPDATA", "LOCALAPPDATA",       # Windows needs these to start a process at all
)


def child_env(source, work):
    """The allowlisted copy of `source` for the tool, with PWD at its empty folder. No OLDPWD."""
    keep = {k.upper() for k in CHILD_ENV_ALLOW}
    env = {k: v for k, v in source.items() if k.upper() in keep or k.startswith("LC_")}
    env["PWD"] = work
    return env


def owner_tag(environ=None):
    """The value the sweep reads: `extreview:<session id or this pid>`. Plain characters only (procclose.owner_from_env)."""
    env = os.environ if environ is None else environ
    base = re.sub(r"[^A-Za-z0-9_.-]", "", env.get(procclose.OWNER_VAR, "") or "") or str(os.getpid())
    return procclose.EXT_OWNER_PREFIX + base


def _signal_group(proc, sig):
    try:
        if WINDOWS:
            if sig == signal.SIGTERM:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, sig)          # start_new_session: the group id is the child's pid        # start_new_session: the group id is the child's pid
    except (OSError, ValueError):
        pass


def _group_alive(proc):
    if WINDOWS:
        return proc.poll() is None
    try:
        os.killpg(proc.pid, 0)
        return True
    except OSError:
        return False


def stop_group(proc, grace=KILL_GRACE_S):
    """TERM to the whole group, wait `grace`, KILL what is left. Used on timeout, on normal exit (stray grandchildren) and
    when the wrapper is terminated."""
    if not _group_alive(proc):
        return
    _signal_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and _group_alive(proc):
        try:
            proc.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
    if _group_alive(proc):
        _signal_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def _poll_exit(proc, wait_s):
    """True when the leader has exited, False after waiting up to `wait_s`. Where the OS allows (waitid with WNOWAIT) the exit is
    seen WITHOUT reaping, so the caller can close the group while the leader's pid is still ours (a reaped pid may be reused,
    and a group signal would then hit a stranger: R-1051 S3)."""
    if not WINDOWS and hasattr(os, "waitid"):
        end = time.monotonic() + wait_s
        try:
            while True:
                if os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                    return True
                if time.monotonic() >= end:
                    return False
                time.sleep(0.02)
        except OSError:
            pass                                  # not supported here: fall back to the reaping wait
    elif not WINDOWS and hasattr(select, "kqueue"):       # macOS: no os.waitid in Python; kqueue NOTE_EXIT also fires before the reap
        try:
            kq = select.kqueue()
            try:
                watch = select.kevent(proc.pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT, select.KQ_NOTE_EXIT)
                return bool(kq.control([watch], 1, wait_s))
            finally:
                kq.close()
        except OSError as e:
            if e.errno == errno.ESRCH and proc.returncode is None:      # already exited, and nobody has reaped it but us
                return True
    try:
        proc.wait(timeout=wait_s)
        return True
    except subprocess.TimeoutExpired:
        return False


def run_tool(tool, path, diff_path, prompt, timeout_s, environ=None, grace=KILL_GRACE_S, now=None, debug=False, model=None):
    """Run one tool. -> (status, text, detail) with status ok | timeout | unavailable | failed.
    R-1053: `debug` asks gemini for its own debug lines and shows more of them (15 lines, 2000 chars), still redacted."""
    tail = (lambda t: tail_of(t, DEBUG_TAIL_LINES, DEBUG_TAIL_CHARS)) if debug else tail_of
    with tempfile.TemporaryDirectory(prefix="budgit-extreview-") as tmp:
        out_tmp = os.path.join(tmp, "answer.txt")
        err_path = os.path.join(tmp, "stderr.txt")
        raw_path = os.path.join(tmp, "stdout.txt")
        argv = command(tool, prompt, out_tmp, debug, model)
        argv[0] = path
        full = dict(os.environ if environ is None else environ)
        owner = owner_tag(full)
        kw = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if WINDOWS else {"start_new_session": True}
        work = os.path.join(tmp, "work")          # R-1054: the tool starts here, an empty 0700 folder; never the caller's folder or the repo
        env = child_env(full, work)
        env[procclose.OWNER_VAR] = owner         # the one variable the wrapper adds
        os.mkdir(work, 0o700)                     # the raw stdout/stderr and the answer file live in `tmp`, outside this folder
        with open(diff_path, "rb") as fin, open(raw_path, "wb") as fout, open(err_path, "wb") as ferr:
            proc = subprocess.Popen(argv, stdin=fin, stdout=fout, stderr=ferr, env=env, cwd=work, **kw)

            def on_term(signum, _frame):          # the wrapper itself is told to stop: take the tool down first
                stop_group(proc, grace)
                shutil.rmtree(tmp, ignore_errors=True)       # os._exit skips the `with`: no raw stderr/stdout stays in /tmp (S1)
                os._exit(128 + signum)
            old = {}
            if not WINDOWS:
                for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    try:
                        old[s] = signal.signal(s, on_term)
                    except ValueError:            # not the main thread (tests): no handler
                        pass
            watch = Watch(raw_path, err_path)
            stopped = None                        # None = the tool finished by itself; else ("timeout",) or ("nonanswer", reason, line)
            rc = None
            closed = False
            try:
                deadline = time.monotonic() + timeout_s
                while True:                       # R-1051: not a blind wait; the tool's own output is read while it runs
                    left = deadline - time.monotonic()
                    if _poll_exit(proc, max(0.0, min(POLL_S, left))):
                        stop_group(proc, grace)   # leader exited, not yet reaped: close strays now, then reap (S3)
                        closed = True
                        rc = proc.wait()
                        break
                    hit = watch.scan() if tool == "gemini" else None      # codex echoes the diff and has its own limit record (R-853)
                    if hit:
                        stopped = ("nonanswer",) + hit
                        break
                    if time.monotonic() >= deadline:
                        stopped = ("timeout",)
                        break
            finally:
                if not closed:                    # no child outlives the wrapper, finished or not (also on an exception)
                    stop_group(proc, grace)
                for s, h in old.items():
                    signal.signal(s, h)
        if stopped and stopped[0] == "timeout":
            err = _read(err_path)
            out_n = watch.stdout_size()
            out_part = "stdout: %d bytes" % out_n if out_n else "stdout: none"
            if out_n and not err.strip() and out_n <= STDOUT_PROMPT_BYTES:      # silent stderr and a short stdout: maybe a prompt
                out_part += " (%s)" % tail_of(_read(raw_path), lines=3, chars=300)
            return "timeout", "", "timed out after %g s, no verdict; %s; last stderr: %s" % (
                timeout_s, out_part, tail(err) or "(none)")
        if stopped:
            reason, line = stopped[1], stopped[2]
            status = "unavailable" if reason.startswith(("quota", "gemini keeps", "gemini: the model is overloaded")) else "failed"
            return status, "", "no verdict: %s %s (%s), stopped at once instead of waiting %g s: %s" % (
                tool, "unavailable" if status == "unavailable" else "cannot answer", reason, timeout_s, line)
        text = ""
        if tool == "codex" and os.path.exists(out_tmp):
            text = _read(out_tmp)
        if not text.strip():
            text = _read(raw_path)
        err = _read(err_path)
        if tool == "codex" and codexlimit.is_limit(err + "\n" + text) and (rc != 0 or not text.strip()):
            until = codexlimit.record(err + "\n" + text, now)       # R-853: remember it; nothing asks for Codex before then
            return "unavailable", "", "no verdict: codex usage limit reached, back %s" % codexlimit.when(until)
        if UNAVAILABLE_RE.search(err + "\n" + text) and (rc != 0 or not text.strip()):
            return "unavailable", "", "no verdict: %s unavailable: %s" % (tool, tail(err + "\n" + text) or "(no output)")
        if rc != 0:
            return "failed", "", "no verdict: %s exited %d: %s" % (tool, rc, tail(err) or "(no stderr)")
        if not text.strip():
            return "failed", "", "no verdict: %s printed nothing; last stderr: %s" % (tool, tail(err) or "(none)")
        return "ok", text, ""


def _read(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def write_out(out, text):
    """Atomic: the file appears complete or not at all."""
    d = os.path.dirname(os.path.abspath(out))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".extreview-", dir=d)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, out)


def review(tool, diff_path, prompt, out, timeout=None, which=shutil.which, environ=None, grace=KILL_GRACE_S, say=print, now=None, cwd=None, debug=False, model=None):
    """-> exit code. Lines go through `say` (stdout); a stale --out is removed first so no old text passes as a verdict.
    R-853: `auto` skips Codex until the date it reported its usage limit will end (`now` is the clock, a parameter).
    The protected-project refusal is the first thing here, so every caller (CLI, main, any other) passes through it."""
    line = refuse_protected(diff_path, cwd or os.getcwd())
    if line:
        say(line)
        return EXIT_PROTECTED
    timeout_s = effective_timeout(timeout)
    try:
        if os.path.exists(out):
            os.unlink(out)
    except OSError:
        pass
    wanted = TOOLS if tool == "auto" else (tool,)
    if tool == "auto":
        until = codexlimit.blocked_until(now)
        if until:
            say("codex skipped: its usage limit ends %s" % codexlimit.when(until))
            wanted = tuple(t for t in wanted if t != "codex")
    found = [(t, which(t)) for t in wanted]
    found = [(t, p) for t, p in found if p]
    if not found:
        say("no external reviewer available (looked for: %s; install one or use a Claude reviewer)" % ", ".join(wanted))
        if tool == "auto":
            say(codexlimit.FALLBACK_LINE)
        return EXIT_NO_TOOL
    last = EXIT_UNAVAILABLE
    for t, p in found:
        status, text, detail = run_tool(t, p, diff_path, prompt, timeout_s, environ=environ, grace=grace, now=now, debug=debug, model=model)
        if status == "ok":
            write_out(out, text)
            say("%s verdict saved: %s" % (t, out))
            return EXIT_OK
        say(detail if status != "unavailable" else "%s (%s)" % (detail, "trying the next tool" if (t, p) != found[-1] and tool == "auto" else "stopping"))
        if status == "timeout":
            return EXIT_TIMEOUT
        if status == "failed":
            return EXIT_FAILED
        last = EXIT_UNAVAILABLE
    if tool == "auto":
        say(codexlimit.FALLBACK_LINE)
    return last


def refuse_protected(diff, cwd):
    """-> a refusal line when the working folder or the diff's folder is protected, else None."""
    for where in (cwd, os.path.dirname(os.path.abspath(diff))):
        if codexlimit.protected(where):
            return ("ext-review refuses inside a protected project (%s): use `budgit codex review --clean-copy` or a "
                    "fresh-context reviewer" % where)
    return None


def main(argv=None, cwd=None):
    ap = argparse.ArgumentParser(prog="budgit ext-review", description="one timed, tagged wrapper for external-model reviews")
    ap.add_argument("--tool", choices=("auto",) + TOOLS, default="auto")
    ap.add_argument("--diff", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=None, help="seconds; default %d, hard cap %d" % (DEFAULT_TIMEOUT_S, HARD_CAP_S))
    ap.add_argument("--debug", action="store_true", help="gemini only: pass --debug and show the last 15 redacted stderr lines "
                    "in a no-verdict message; codex gets nothing extra")
    ap.add_argument("--model", default=None, help="model name for gemini and codex (-m NAME); default: the tool's own")
    a = ap.parse_args(argv)
    if a.model is not None and not MODEL_RE.match(a.model):
        print("bad --model name (letters, digits, . _ - only, 1 to 64 characters)")
        return EXIT_USAGE
    if not os.path.isfile(a.diff):
        print("diff file not found: %s" % a.diff)
        return EXIT_USAGE
    return review(a.tool, a.diff, a.prompt, a.out, a.timeout, cwd=cwd, debug=a.debug, model=a.model)


if __name__ == "__main__":
    sys.exit(main())
