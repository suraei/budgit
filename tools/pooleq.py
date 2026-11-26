#!/usr/bin/env python3
"""Equal-to-serial proof for the pool runner (R-618, I-834): run a test subset serially and pooled, compare the set of
(test id, outcome) pairs.

Usage: pooleq.py [--workers N] module [module ...]     e.g. pooleq.py test_runtests_workers test_runtests_flap
Exit 0 when both runs produced the same (id, outcome) set, 1 when they differ, 2 on a usage or run error.

Serial = one worker process running every module in turn (runtests.py --worker, the same unittest runner and the same
isolated HOME/TMPDIR as the pool's workers). Pooled = runtests.py --outcomes. Run it on a small subset; the whole tree is
the full suite and waits for the suite lock. Standard library only.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runtests as rt  # noqa: E402


def compare(serial, pooled):
    """-> messages (empty when equal). Both are lists of (id, outcome); a duplicated id counts as a difference."""
    out = []
    s, p = dict(serial), dict(pooled)
    if len(serial) != len(s) or len(pooled) != len(p):
        out.append("duplicate test id: serial %d entries for %d ids, pool %d entries for %d ids" % (len(serial), len(s), len(pooled), len(p)))
    for i in sorted(set(s) - set(p)):
        out.append("only in the serial run: %s (%s)" % (i, s[i]))
    for i in sorted(set(p) - set(s)):
        out.append("only in the pool run: %s (%s)" % (i, p[i]))
    for i in sorted(set(s) & set(p)):
        if s[i] != p[i]:
            out.append("outcome differs: %s serial=%s pool=%s" % (i, s[i], p[i]))
    return out


def serial_outcomes(mods, base):
    env, _ = rt.worker_env(base, 0)
    rec = os.path.join(base, "w0", "rec.json")
    r = subprocess.run([sys.executable, os.path.join(HERE, "runtests.py"), "--worker", rec] + mods, cwd=rt.ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(rec) as f:
        return [(x[0], x[1]) for x in json.load(f)["_ran"]], r.returncode


def pool_outcomes(mods, workers, base):
    out = os.path.join(base, "pool.json")
    r = subprocess.run([sys.executable, os.path.join(HERE, "runtests.py"), "--workers", str(workers), "--outcomes", out] + mods,
                       cwd=rt.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(out) as f:
        return [tuple(x) for x in json.load(f)], r.returncode


def main(argv):
    args, workers = list(argv), 3
    if args[:1] == ["--workers"]:
        workers, args = int(args[1]), args[2:]
    if not args:
        print(__doc__)
        return 2
    mods = rt.discover(args)
    with tempfile.TemporaryDirectory(prefix="pooleq-") as base:
        try:
            serial, src = serial_outcomes(mods, base)
            pooled, prc = pool_outcomes(mods, workers, base)
        except (OSError, ValueError, KeyError) as e:
            print("pooleq: a run did not produce its outcomes: %s" % e)
            return 2
    diff = compare(serial, pooled)
    for line in diff:
        print(line)
    print("serial: %d tests (exit %d); pool with %d workers: %d tests (exit %d)" % (len(serial), src, workers, len(pooled), prc))
    print("EQUAL" if not diff and src == prc else "DIFFERENT")
    return 0 if not diff and src == prc else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
