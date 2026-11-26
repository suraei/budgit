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
import threading
import time

import codexlimit
import extuse
import i18n
import procclose

DEFAULT_TIMEOUT_S = 600         # a review that has not answered in 10 minutes is not coming
HARD_CAP_S = 1800                # --timeout may lower the default, never raise it past this
KILL_GRACE_S = 3.0               # between TERM to the group and KILL
EXIT_OK, EXIT_USAGE, EXIT_NO_TOOL, EXIT_TIMEOUT, EXIT_UNAVAILABLE, EXIT_FAILED = 0, 2, 3, 4, 5, 6
EXIT_PROTECTED = 7
TOOLS = ("gemini", "codex")     # the command-line reviewers, in the order `auto` tries them
AUTO_ORDER = TOOLS + ("openrouter",)     # R-1254: the full order; a reviewer not set up, out of quota or locked is skipped
HTTP_DEADLINE_S = 120           # R-1254: a product deadline for an HTTP review (listing and every try share it); --timeout may change it
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


def review(tool, diff_path, prompt, out, timeout=None, which=shutil.which, environ=None, grace=KILL_GRACE_S, say=print, now=None, cwd=None, debug=False, model=None, purpose="review"):
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
    if tool in PROVIDERS:
        return _http_logged(tool, purpose, diff_path, prompt, out, http_deadline(timeout), environ, say, model, cwd, which, now, None)
    auto = tool == "auto"
    wanted = TOOLS if auto else (tool,)
    skipped = []                      # R-1254: (reviewer, why) for every auto reviewer that did not give the verdict
    if auto:
        until = codexlimit.blocked_until(now)
        if until:
            say("codex skipped: its usage limit ends %s" % codexlimit.when(until))
            wanted = tuple(t for t in wanted if t != "codex")
            skipped.append(("codex", "usage limit"))
    found = [(t, which(t)) for t in wanted]
    if auto:
        skipped += [(t, "not set up") for t, p in found if not p]
    found = [(t, p) for t, p in found if p]
    if not found and not auto:
        say("no external reviewer available (looked for: %s; install one or use a Claude reviewer)" % ", ".join(wanted))
        return EXIT_NO_TOOL
    last = EXIT_UNAVAILABLE
    for t, p in found:
        began = time.monotonic()
        status, text, detail = run_tool(t, p, diff_path, prompt, timeout_s, environ=environ, grace=grace, now=now, debug=debug, model=model)
        extuse.log(cwd or os.getcwd(), t, purpose, extuse.failure_class(status, detail), time.monotonic() - began)   # R-1358: every attempt
        if status == "ok":
            write_out(out, text)
            say("%s verdict saved: %s" % (t, out))
            if auto:
                say(ran_line(t, skipped))
            return EXIT_OK
        say(detail if status != "unavailable" else "%s (%s)" % (detail, "trying the next tool" if auto else "stopping"))
        if status == "failed":
            return EXIT_FAILED
        if status == "timeout" and not auto:
            return EXIT_TIMEOUT
        last = EXIT_TIMEOUT if status == "timeout" else EXIT_UNAVAILABLE      # in auto a timeout also goes on to the next reviewer
        skipped.append((t, "timed out" if status == "timeout" else "out of quota or unavailable"))
    if not auto:
        return last
    skip = []
    rc = _http_logged("openrouter", purpose, diff_path, prompt, out, http_deadline(timeout), environ, say, model, cwd, which, now, skip)
    if rc == EXIT_OK:
        say(ran_line("openrouter", skipped))
        return EXIT_OK
    if skip:                          # not set up or locked: skipped, the earlier result stands
        if not found:
            say("no external reviewer available (looked for: %s; install one or use a Claude reviewer)" % ", ".join(AUTO_ORDER))
            say("openrouter skipped: %s" % skip[0])
            say(codexlimit.FALLBACK_LINE)
            return EXIT_NO_TOOL
        say("openrouter skipped: %s" % skip[0])
        say(codexlimit.FALLBACK_LINE)
        return last
    if rc in (EXIT_TIMEOUT, EXIT_UNAVAILABLE):
        say(codexlimit.FALLBACK_LINE)
    return rc


def ran_line(ran, skipped):
    """The one line that says which auto reviewer gave the verdict and which were passed over, and why."""
    return "auto: ran: %s%s" % (ran, " (skipped: %s)" % ", ".join("%s %s" % kv for kv in skipped) if skipped else "")


# R-1254: OpenAI-compatible HTTP reviewers. A later provider is one more row here (base URL, path, key source, models).
# Only models ending in `:free` are ever sent; the Anthropic/Claude family is never used as an outside reviewer.
PROVIDERS = {
    "openrouter": {
        "base": "https://openrouter.ai/api/v1", "path": "/chat/completions", "base_env": "OPENROUTER_BASE_URL",
        "keychain": "budgit-openrouter", "models_path": "/models", "cache": "openrouter-free-models.json",
        "max_diff_bytes": 200_000,
    },
}                                  # no model id here: the free ids are read from the provider's own listing at run time
FREE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~/-]{0,100}:free\Z")
BLOCKED_FAMILY_RE = re.compile(r"anthropic|claude", re.I)
# The key is read from the macOS Keychain only (never an env variable, a file or an argument) and is never printed or written.
NO_KEY_LINE = "La clave de OpenRouter aún no está en el llavero («%s»); no se ha enviado nada."
SECURITY_TIMEOUT_S = 15
SECURITY_NOT_FOUND = 44            # `security find-generic-password`: the item is not in the keychain
ERR_BODY_CHARS = 300
MAX_MODEL_TRIES = 3                # free models tried in the listing's order before the review is called unavailable


def http_deadline(requested):
    """The wall-clock deadline of one HTTP review: the product default, or --timeout (never above the hard cap)."""
    return HTTP_DEADLINE_S if requested is None else effective_timeout(requested)


def with_deadline(fn, seconds):
    """-> (True, result) when `fn()` finished within `seconds` of wall-clock time, else (False, None). The call runs in a daemon
    thread, so a server that drips bytes slowly cannot hold the review past the deadline; an exception in `fn` is re-raised."""
    box = []

    def run():
        try:
            box.append(fn())
        except Exception as e:           # handed back to the caller's thread
            box.append(e)
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(max(0.0, seconds))
    if th.is_alive() or not box:
        return False, None
    if isinstance(box[0], Exception):
        raise box[0]
    return True, box[0]


def check_free_model(model):
    """-> a one-line refusal, or None when `model` is a free id outside the Anthropic/Claude family."""
    if BLOCKED_FAMILY_RE.search(model):
        return "model refused: %s is an Anthropic/Claude model; outside reviewers must be another family" % redact(model)[:80]
    if not FREE_MODEL_RE.match(model):
        return "model refused: only free models are allowed (the id must end in :free)"
    return None


def get_key(spec, which=shutil.which):
    """-> (key, state), state ok | missing | locked. The key comes from the macOS Keychain only, through the `security` found by
    `which`; no env variable, file or argument is ever a key source. Exit 44 is "not there"; any other failure or a hang is "locked"."""
    sec = which("security")
    if not sec:
        return None, "missing"
    try:
        r = subprocess.run([sec, "find-generic-password", "-s", spec["keychain"], "-w"],
                           capture_output=True, text=True, timeout=SECURITY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, "locked"
    except (OSError, subprocess.SubprocessError):
        return None, "missing"
    if r.returncode == 0:
        key = r.stdout.strip()
        return (key, "ok") if key else (None, "missing")
    return None, "missing" if r.returncode == SECURITY_NOT_FOUND else "locked"


def base_url(spec, environ=None):
    """The spec's base URL. The env override is honoured only for a loopback http (a test server): a key and a diff are never
    sent to another address an environment variable names."""
    env = os.environ if environ is None else environ
    url = (env.get(spec["base_env"]) or "").strip().rstrip("/")
    if re.match(r"http://(127\.0\.0\.1|localhost)(:\d+)?(/|\Z)", url):
        return url
    return spec["base"]


def _opener(base):
    import urllib.request
    loopback = bool(re.match(r"http://(127\.0\.0\.1|localhost)[:/]", base))
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) if loopback else urllib.request.build_opener()


def free_ids(ids):
    """The ids that may be used: a string ending in :free, outside the Anthropic/Claude family, in the listing's order, no repeats."""
    seen, out = set(), []
    for i in ids:
        if isinstance(i, str) and i not in seen and FREE_MODEL_RE.match(i) and not BLOCKED_FAMILY_RE.search(i):
            seen.add(i)
            out.append(i)
    return out


def fetch_free_models(spec, base, timeout_s):
    """The provider's own listing, read now (a public GET, no key). -> the allowed free ids, or None when it cannot be read."""
    import json
    try:
        with _opener(base).open(base + spec["models_path"], timeout=max(0.5, timeout_s)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return free_ids(item.get("id") for item in data["data"])
    except Exception:                  # any failure to read the listing is the same answer: unavailable
        return None


def free_models(spec, base, project, now, deadline_at):
    """The free ids for today: the cache in `<project>/.budgit/` when it is from today, else the live listing (then cached).
    -> (ids or None, status), status ok | timeout | unavailable."""
    import json
    day = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    path = os.path.join(project, ".budgit", spec["cache"])
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("day") == day and free_ids(cached.get("models") or []):
            return free_ids(cached["models"]), "ok"
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    left = deadline_at - time.monotonic()
    done, ids = with_deadline(lambda: fetch_free_models(spec, base, left), left)
    if not done:
        return None, "timeout"
    if not ids:
        return None, "unavailable"
    try:
        write_out(path, json.dumps({"day": day, "models": ids}) + "\n")
    except OSError:
        pass                           # a read-only project: the list still serves this run
    return ids, "ok"


def http_call(spec, base, key, model, content, timeout_s):
    """One chat-completions call with urllib. -> (status, text, detail) like run_tool. The key is scrubbed from everything returned."""
    import json
    import socket
    import urllib.error
    import urllib.request
    scrub = lambda s: redact(s.replace(key, "[redacted]"))
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}]}).encode("utf-8")
    req = urllib.request.Request(base + spec["path"], data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with _opener(base).open(req, timeout=max(0.5, timeout_s)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        try:
            tail = tail_of(scrub(e.read(2000).decode("utf-8", "replace")), 2, ERR_BODY_CHARS)
        except Exception:
            tail = ""
        if e.code == 429 or e.code >= 500:
            return "unavailable", "", "no verdict: openrouter unavailable (HTTP %d): %s" % (e.code, tail or "(no body)")
        return "failed", "", "no verdict: openrouter answered HTTP %d (%s)" % (
            e.code, "the key was rejected" if e.code in (401, 403) else tail or "(no body)")
    except (socket.timeout, TimeoutError):
        return "timeout", "", "timed out after %g s, no verdict (openrouter)" % timeout_s
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            return "timeout", "", "timed out after %g s, no verdict (openrouter)" % timeout_s
        return "failed", "", "no verdict: openrouter could not be reached: %s" % scrub(str(e.reason))[:200]
    except (ValueError, KeyError, IndexError, TypeError, OSError):
        return "failed", "", "no verdict: openrouter sent an answer without a review text"
    if not isinstance(text, str) or not text.strip():
        return "failed", "", "no verdict: openrouter printed nothing"
    return "ok", text.replace(key, "[redacted]"), ""


def review_http(tool, diff_path, prompt, out, timeout_s, environ, say, model, cwd=None, which=shutil.which, now=None, skip=None):
    """The HTTP reviewers' path of `review`: protected refusal, model check, size and personal-data refusals, key from the Keychain,
    the live free-model list, then the calls under ONE wall-clock deadline `timeout_s`, and the same write and exit codes.
    `skip` (a list) is given by `auto`: a reviewer not set up or locked adds its reason there instead of saying a line."""
    project = cwd or os.getcwd()
    line = refuse_protected(diff_path, project)      # first of all, before a key, a listing or a request exists
    if line:
        say(line)
        return EXIT_PROTECTED
    spec = PROVIDERS[tool]
    if model:
        line = check_free_model(model)
        if line:
            say(line)
            return EXIT_USAGE
    if os.path.getsize(diff_path) > spec["max_diff_bytes"]:
        say("diff too big for %s: %d bytes, the limit is %d" % (tool, os.path.getsize(diff_path), spec["max_diff_bytes"]))
        return EXIT_USAGE
    diff_text = _read(diff_path)
    import codexclean
    hits = codexclean.personal_hits(diff_text)
    if hits:
        say("%s refused: the diff looks like it has personal data (%s); nothing was sent" % (
            tool, ", ".join("%s x%d" % kv for kv in sorted(hits.items()))))
        return EXIT_PROTECTED
    key, state = get_key(spec, which)
    if not key:
        if skip is not None:
            skip.append("not set up (no key in the Keychain)" if state == "missing" else i18n.t("extreview.locked_skip"))
        else:
            say(NO_KEY_LINE % spec["keychain"] if state == "missing" else i18n.t("extreview.locked_line", keychain=spec["keychain"]))
        return EXIT_NO_TOOL
    base = base_url(spec, environ)
    deadline_at = time.monotonic() + timeout_s
    code = {"timeout": EXIT_TIMEOUT, "unavailable": EXIT_UNAVAILABLE}
    timed_out = "timed out after %g s, no verdict (%s)" % (timeout_s, tool)
    if model:
        models = (model,)
    else:
        models, st = free_models(spec, base, project, now, deadline_at)
        if not models:
            say(timed_out if st == "timeout" else "no verdict: %s unavailable: its free model list could not be read or has no usable free model" % tool)
            return code[st]
        models = models[:MAX_MODEL_TRIES]
    content = prompt + "\n\n" + diff_text
    status, text, detail = "failed", "", ""
    for i, m in enumerate(models):
        left = deadline_at - time.monotonic()
        done, res = with_deadline(lambda: http_call(spec, base, key, m, content, left), left) if left > 0 else (False, None)
        status, text, detail = res if done else ("timeout", "", timed_out)
        if status == "timeout":
            detail = timed_out                      # the whole call's deadline, not the share left for this try
        if status == "ok":
            write_out(out, text)
            say("%s verdict saved: %s (model %s)" % (tool, out, m))
            return EXIT_OK
        if status != "unavailable" or i == len(models) - 1:
            break
        say("%s (model %s), trying the next free model" % (detail, m))
    say(detail)
    return code.get(status, EXIT_FAILED)


def _http_logged(tool, purpose, diff_path, prompt, out, timeout_s, environ, say, model, cwd, which, now, skip):
    """review_http plus the use log (R-1358): a run that reached the provider is logged; a refusal or "not set up" is not a run."""
    began = time.monotonic()
    rc = review_http(tool, diff_path, prompt, out, timeout_s, environ, say, model, cwd=cwd, which=which, now=now, skip=skip)
    word = {EXIT_OK: "ok", EXIT_TIMEOUT: "timeout", EXIT_UNAVAILABLE: "quota", EXIT_FAILED: "failed"}.get(rc)
    if word:
        extuse.log(cwd or os.getcwd(), tool, purpose, word, time.monotonic() - began)
    return rc


CHECK_TIMEOUT_S = 8
WAITING = "a prompt is waiting / hung"
# The probes are the cheapest non-interactive calls: codex has a status command; gemini has none, so one tiny prompt is sent.
PROBES = {"codex": ["login", "status"], "gemini": ["-p", "ping", "--skip-trust", "-o", "text"]}


def _probe(path, args, environ, timeout_s, grace=1.0):
    """Run one probe with stdin closed, its own group and a deadline. -> (rc or None when it hung, its text). The text is for
    classification only and is never printed."""
    with tempfile.TemporaryDirectory(prefix="budgit-extcheck-") as tmp:
        work = os.path.join(tmp, "work")
        os.mkdir(work, 0o700)
        raw = os.path.join(tmp, "out.txt")
        env = child_env(dict(os.environ if environ is None else environ), work)
        env["NO_BROWSER"] = "1"         # a probe never opens a login page
        env["BROWSER"] = "true"
        kw = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if WINDOWS else {"start_new_session": True}
        with open(raw, "wb") as fo:
            proc = subprocess.Popen([path] + args, stdin=subprocess.DEVNULL, stdout=fo, stderr=subprocess.STDOUT, env=env, cwd=work, **kw)
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                rc = None
            finally:
                stop_group(proc, grace)
        return rc, _read(raw)


def _classify(rc, text):
    """-> (login, quota) words from a finished probe's text."""
    if codexlimit.is_limit(text) or NON_ANSWERS[2][1].search(text) or UNAVAILABLE_RE.search(text):
        return "unknown", "quota reached"
    if NON_ANSWERS[0][1].search(text):
        return "not logged in", "quota unknown"
    if rc == 0:
        return "logged in", "quota ok"
    return "unknown (exit %s)" % rc, "quota unknown"


def check(which=shutil.which, environ=None, timeout=CHECK_TIMEOUT_S, now=None):
    """R-1358: one line per outside reviewer: installed or not, logged in or not, quota reached or not, or that a prompt is
    waiting. Only non-interactive probes (stdin closed, a short deadline); nothing starts a login or accepts terms; the lines are
    fixed words, so no key, token or tool text can reach them."""
    lines = []
    for tool in TOOLS:
        path = which(tool)
        if not path:
            lines.append("%s: not installed" % tool)
            continue
        rc, text = _probe(path, PROBES[tool], environ, timeout)
        if rc is None:
            lines.append("%s: installed, %s (the probe timed out after %g s; open it in a terminal and answer it there)" % (tool, WAITING, timeout))
            continue
        login, quota = _classify(rc, text)
        if tool == "codex" and codexlimit.blocked_until(now):
            quota = "quota reached (back %s)" % codexlimit.when(codexlimit.blocked_until(now))
        lines.append("%s: installed, %s, %s" % (tool, login, quota))
    key, state = get_key(PROVIDERS["openrouter"], which)
    key = None                          # only the state is used; the key is dropped at once
    lines.append("openrouter: HTTP, no CLI; " + {
        "ok": "logged in (key in the Keychain), quota not checked",
        "missing": "not logged in (no key in the Keychain)",
        "locked": "installed, %s (the Keychain is locked or asking)" % WAITING}.get(state, "not logged in"))
    return lines


def print_check(which=shutil.which, environ=None, timeout=CHECK_TIMEOUT_S, say=print, now=None):
    for l in check(which, environ, timeout, now):
        say(l)
    return EXIT_OK


def refuse_protected(diff, cwd):
    """-> a refusal line when the working folder or the diff's folder is protected, else None."""
    for where in (cwd, os.path.dirname(os.path.abspath(diff))):
        if codexlimit.protected(where):
            return ("ext-review refuses inside a protected project (%s): use `budgit codex review --clean-copy` or a "
                    "fresh-context reviewer" % where)
    return None


def main(argv=None, cwd=None):
    ap = argparse.ArgumentParser(prog="budgit ext-review", description="one timed, tagged wrapper for external-model reviews")
    ap.add_argument("--tool", choices=("auto",) + TOOLS + tuple(PROVIDERS), default="auto")
    ap.add_argument("--diff")
    ap.add_argument("--prompt")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true", help="report each reviewer: installed, logged in, quota, or a prompt waiting (the gemini probe spends one tiny request)")
    ap.add_argument("--purpose", default="review", help="a short word for the use log (letters, digits, - _)")
    ap.add_argument("--timeout", type=float, default=None, help="seconds; default %d, hard cap %d" % (DEFAULT_TIMEOUT_S, HARD_CAP_S))
    ap.add_argument("--debug", action="store_true", help="gemini only: pass --debug and show the last 15 redacted stderr lines "
                    "in a no-verdict message; codex gets nothing extra")
    ap.add_argument("--model", default=None, help="model name for gemini and codex (-m NAME); default: the tool's own")
    a = ap.parse_args(argv)
    if a.check:
        return print_check()
    if not (a.diff and a.prompt and a.out):
        ap.error("--diff, --prompt and --out are required (unless --check)")
    if a.model is not None and a.tool not in PROVIDERS and not MODEL_RE.match(a.model):
        print("bad --model name (letters, digits, . _ - only, 1 to 64 characters)")
        return EXIT_USAGE
    if not os.path.isfile(a.diff):
        print("diff file not found: %s" % a.diff)
        return EXIT_USAGE
    return review(a.tool, a.diff, a.prompt, a.out, a.timeout, cwd=cwd, debug=a.debug, model=a.model, purpose=a.purpose)


if __name__ == "__main__":
    sys.exit(main())
