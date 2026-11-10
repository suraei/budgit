"""R-847 b: a test server or fixture that dies with its parent.

`python3 parentwatch.py [--ttl SECONDS] -- <command> [args]` runs the command as a child in its own process group, and
exits (closing the child's group) when its parent is gone (parent pid changed, typically to 1) or after the TTL.
In-process use: `start(ttl_s)` starts a daemon thread that calls `os._exit` on the same two conditions.
Standing instruction: processes nobody uses are always closed. POSIX only; on Windows only the TTL applies.
"""
import os
import signal
import subprocess
import sys
import threading
import time

DEFAULT_TTL_S = 2 * 3600
POLL_S = 1.0


def should_exit(parent, started, ttl_s, now=None, getppid=os.getppid):
    """'parent' when the parent died, 'ttl' when too old, else None."""
    if getppid() != parent:
        return "parent"
    if ttl_s is not None and (time.monotonic() if now is None else now) - started >= ttl_s:
        return "ttl"
    return None


def start(ttl_s=DEFAULT_TTL_S, poll_s=POLL_S, on_exit=None):
    parent, began = os.getppid(), time.monotonic()
    on_exit = on_exit or (lambda why: os._exit(0))

    def loop():
        while True:
            why = should_exit(parent, began, ttl_s)
            if why:
                on_exit(why)
                return
            time.sleep(poll_s)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def run(argv, ttl_s=DEFAULT_TTL_S, poll_s=POLL_S):
    parent, began = os.getppid(), time.monotonic()
    child = subprocess.Popen(argv, start_new_session=(os.name == "posix"))
    while child.poll() is None:
        if should_exit(parent, began, ttl_s):
            try:
                os.killpg(child.pid, signal.SIGTERM) if os.name == "posix" else child.terminate()
                child.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                child.kill()
            return 0
        time.sleep(poll_s)
    return child.returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ttl = DEFAULT_TTL_S
    if argv[:1] == ["--ttl"]:
        ttl, argv = float(argv[1]), argv[2:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    return run(argv, ttl) if argv else 2


if __name__ == "__main__":
    sys.exit(main())
