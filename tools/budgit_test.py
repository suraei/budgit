#!/usr/bin/env python3
"""`budgit test --full` for this repo (R-814 part 1): the pool runner with no flags typed.

    python3 tools/budgit_test.py --full [runtests options]     the whole suite through the pool
    python3 tools/budgit_test.py [runtests options]            only what the change touches (runtests --changed)

Before running it counts the repo's files that iCloud has evicted (relocate.dataless_count). Above 0 it stops at once and
names the fix, `brctl download <path>`, so no hand step is left. Standard library only.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
EXIT_DATALESS = 4


def preflight(repo, count=None):
    """-> a message when evicted files stand in the way, else None. `count` is relocate.dataless_count, injectable."""
    if count is None:
        import relocate
        count = relocate.dataless_count
    return check(repo, count)[0]


def check(repo, count):
    """-> (stop message or None, warning or None). Evicted files stop the run; a walk that did not finish only warns."""
    found, complete = count(repo)
    if found > 0:
        return ("%d file(s) in %s are only in iCloud, not on this disk, so the suite would read empty files. "
                "Fix: brctl download %s   then run this again." % (found, repo, repo)), None
    if not complete:
        return None, ("the iCloud check could not look at every file in %s (it hit its cap or an unreadable folder), so "
                      "evicted files may be missed. If tests read empty files, run: brctl download %s" % (repo, repo))
    return None, None


WAIT_POLL = 30.0           # seconds between busy readings while waiting outside the locks
WAIT_LIMIT = 600.0         # after this long, run anyway with what the machine allows


def wait_calm(busy, sleep=time.sleep, clock=time.monotonic, poll=WAIT_POLL, limit=WAIT_LIMIT, say=print):
    """Wait, holding no lock, until `busy()` -> (bool, reason) says the machine is calm. After `limit` s it gives up
    waiting and says why. -> True when it ended calm, False when it ran out of patience. All timing is injectable."""
    start = clock()
    is_busy, why = busy()
    if not is_busy:
        return True
    say("budgit test: the machine is busy (%s); waiting outside the locks, looking again every %d s, for up to %d min"
        % (why, poll, limit // 60))
    while True:
        if clock() - start >= limit:
            say("budgit test: still busy after %d min; running anyway with the workers the machine allows (%s)"
                % (limit // 60, why))
            return False
        sleep(poll)
        is_busy, why = busy()
        if not is_busy:
            say("budgit test: the machine is calm now; starting")
            return True


LOCK_WAIT = 60             # seconds each lock may be waited for; then lockf gives up and the entry goes back to waiting
EX_TEMPFAIL = 75           # lockf's exit status when its -t timeout ran out


def locked(cmd, project, tmp=None, which=shutil.which, timeout=LOCK_WAIT):
    """The command under the PROJECT suite lock only, when lockf exists (the machine-wide full-suite lock is retired, §6). The
    lock file is the one `budgit suite --wait` takes: suitewait.lock_name in suitewait.lock_dir (per-user, private). On a
    timeout (`lockf -t`) lockf exits EX_TEMPFAIL with nothing held. Taken only here, after the outside-the-lock waiting."""
    if not which("lockf"):
        return cmd
    import suitewait
    tmp = tmp or suitewait.lock_dir()
    return ["lockf", "-k", "-t", str(int(timeout)), os.path.join(tmp, suitewait.lock_name(project))] + cmd


def main(argv, count=None, runner=None, repo=ROOT, busy=None, sleep=time.sleep, clock=time.monotonic,
         poll=WAIT_POLL, limit=WAIT_LIMIT, lock=True):
    args = list(argv)
    full = "--full" in args
    args = [a for a in args if a != "--full"]
    if count is None:
        import relocate
        count = relocate.dataless_count
    message, warning = check(repo, count)
    if message:
        print("budgit test: " + message)
        return EXIT_DATALESS
    if warning:
        print("budgit test: warning: " + warning)
    if not full and "--changed" not in args and "--files" not in args:
        args = ["--changed"] + args
    try:
        os.nice(10)                      # CPU-heavy work runs below the top project (children inherit it)
    except (AttributeError, OSError):
        pass
    cmd = [sys.executable, os.path.join(ROOT, "tools", "runtests.py")] + args
    run = runner or (lambda c: subprocess.call(c, cwd=ROOT))
    if full and lock:
        if busy is None:
            import runtests
            busy = runtests.busy_now
        project = os.path.basename(os.path.abspath(repo))
        while True:
            wait_calm(busy, sleep, clock, poll, limit)       # no lock is held while this waits
            wrapped = locked(cmd, project)
            rc = run(wrapped)
            if wrapped is cmd or rc != EX_TEMPFAIL:
                return rc
            print("budgit test: a suite lock is held by another run; released, waiting outside the locks again")
    return run(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
