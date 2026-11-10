"""R-889a: `budgit ext-review`, the one wrapper for every external-model call (codex, gemini).

Why: on 9 Nov 2026 three Gemini cross-reviews hung 1h45 to 2h13 on an overloaded machine and nothing closed them, because
nothing knew they were ours. Here every call has a deadline, its own process group, and an owner tag the R-847 sweep reads.

Contract: stdin = the diff; the reviewer's text goes to --out only when the tool finished with a verdict (never a partial
file); exit codes below are distinct; no child outlives the wrapper (group TERM, short grace, KILL; also on normal exit and
when the wrapper itself is terminated). What varies is read at run time: the CLIs on PATH, the OS (POSIX groups or the
Windows process-group flag), the tool's own quota state (from its output), the machine's speed (the user's --timeout)."""
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import procclose

DEFAULT_TIMEOUT_S = 600         # a review that has not answered in 10 minutes is not coming
HARD_CAP_S = 1800                # --timeout may lower the default, never raise it past this
KILL_GRACE_S = 3.0               # between TERM to the group and KILL
EXIT_OK, EXIT_USAGE, EXIT_NO_TOOL, EXIT_TIMEOUT, EXIT_UNAVAILABLE, EXIT_FAILED = 0, 2, 3, 4, 5, 6
TOOLS = ("codex", "gemini")      # the order `auto` tries
UNAVAILABLE_RE = re.compile(r"quota|\b429\b|\b50[0-4]\b|rate.?limit|resource.?exhausted|too many requests|overloaded", re.I)
WINDOWS = os.name == "nt"


def effective_timeout(requested):
    """The default, lowered by --timeout, never above the hard cap, never below 1 s."""
    if requested is None:
        return DEFAULT_TIMEOUT_S
    return max(1, min(float(requested), HARD_CAP_S))


def command(tool, prompt, out_tmp):
    """The read-only invocation. Codex also writes its final message to `out_tmp` (stdout carries progress noise)."""
    if tool == "codex":
        return ["codex", "exec", "-s", "read-only", "--skip-git-repo-check", "-o", out_tmp, "--", prompt]
    return ["gemini", "-p", prompt, "--approval-mode", "plan", "-o", "text"]


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


def run_tool(tool, path, diff_path, prompt, timeout_s, environ=None, grace=KILL_GRACE_S):
    """Run one tool. -> (status, text, detail) with status ok | timeout | unavailable | failed."""
    with tempfile.TemporaryDirectory(prefix="budgit-extreview-") as tmp:
        out_tmp = os.path.join(tmp, "answer.txt")
        err_path = os.path.join(tmp, "stderr.txt")
        raw_path = os.path.join(tmp, "stdout.txt")
        argv = command(tool, prompt, out_tmp)
        argv[0] = path
        env = dict(os.environ if environ is None else environ)
        env[procclose.OWNER_VAR] = owner_tag(env)
        kw = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if WINDOWS else {"start_new_session": True}
        with open(diff_path, "rb") as fin, open(raw_path, "wb") as fout, open(err_path, "wb") as ferr:
            proc = subprocess.Popen(argv, stdin=fin, stdout=fout, stderr=ferr, env=env, **kw)

            def on_term(signum, _frame):          # the wrapper itself is told to stop: take the tool down first
                stop_group(proc, grace)
                os._exit(128 + signum)
            old = {}
            if not WINDOWS:
                for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    try:
                        old[s] = signal.signal(s, on_term)
                    except ValueError:            # not the main thread (tests): no handler
                        pass
            try:
                try:
                    rc = proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    return "timeout", "", "timed out after %g s, no verdict" % timeout_s
            finally:
                stop_group(proc, grace)           # no child outlives the wrapper, finished or not
                for s, h in old.items():
                    signal.signal(s, h)
        text = ""
        if tool == "codex" and os.path.exists(out_tmp):
            text = _read(out_tmp)
        if not text.strip():
            text = _read(raw_path)
        err = _read(err_path)
        if UNAVAILABLE_RE.search(err + "\n" + text) and (rc != 0 or not text.strip()):
            return "unavailable", "", "no verdict: %s unavailable" % tool
        if rc != 0:
            return "failed", "", "no verdict: %s exited %d: %s" % (tool, rc, err.strip()[-300:])
        if not text.strip():
            return "failed", "", "no verdict: %s printed nothing" % tool
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


def review(tool, diff_path, prompt, out, timeout=None, which=shutil.which, environ=None, grace=KILL_GRACE_S, say=print):
    """-> exit code. Lines go through `say` (stdout); a stale --out is removed first so no old text passes as a verdict."""
    timeout_s = effective_timeout(timeout)
    try:
        if os.path.exists(out):
            os.unlink(out)
    except OSError:
        pass
    wanted = TOOLS if tool == "auto" else (tool,)
    found = [(t, which(t)) for t in wanted]
    found = [(t, p) for t, p in found if p]
    if not found:
        say("no external reviewer available (looked for: %s; install one or use a Claude reviewer)" % ", ".join(wanted))
        return EXIT_NO_TOOL
    last = EXIT_UNAVAILABLE
    for t, p in found:
        status, text, detail = run_tool(t, p, diff_path, prompt, timeout_s, environ=environ, grace=grace)
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
    return last


def main(argv=None):
    ap = argparse.ArgumentParser(prog="budgit ext-review", description="one timed, tagged wrapper for external-model reviews")
    ap.add_argument("--tool", choices=("auto",) + TOOLS, default="auto")
    ap.add_argument("--diff", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=None, help="seconds; default %d, hard cap %d" % (DEFAULT_TIMEOUT_S, HARD_CAP_S))
    a = ap.parse_args(argv)
    if not os.path.isfile(a.diff):
        print("diff file not found: %s" % a.diff)
        return EXIT_USAGE
    return review(a.tool, a.diff, a.prompt, a.out, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
