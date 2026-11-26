#!/usr/bin/env python3
"""R-1198: the ONE place that decides which test modules a CI shard runs. Standard library only, Python 3.8+.

Usage: ci_shard.py K/N      prints the modules of shard K of N, one per line (the workflow runs exactly these)
       ci_shard.py --plan N prints each shard's module count and recorded seconds (nothing runs)

Inputs:
  tests/.durations.json    module -> seconds (written by `runtests.py --record`); with none recorded every module
                           weighs 1 (a split by file count); a module missing from the file weighs the median;
  tests/ci_linux_skip.txt  one module per line, `module  # reason`; applied on Linux only, always printed, never silent.
Split: modules sorted longest first, each into the lightest shard (ties: the lower shard number); a rerun is identical.
"""
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
SKIP_FILE = os.path.join(TESTS, "ci_linux_skip.txt")
DURATIONS = os.path.join(TESTS, ".durations.json")


def discover(tests=TESTS):
    return sorted(f[:-3] for f in os.listdir(tests) if f.startswith("test_") and f.endswith(".py"))


def load_skip(path=SKIP_FILE):
    """-> {module: reason}. Blank lines and lines starting with # are comments. Every entry needs `# reason`;
    a line without one is an error, not a silent skip."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            mod, sep, reason = line.partition("#")
            mod, reason = mod.strip(), reason.strip()
            if mod.endswith(".py"):
                mod = mod[:-3]
            if not mod or " " in mod or not sep or not reason:
                raise ValueError("%s line %d: want `module  # reason`, got %r" % (os.path.basename(path), n, raw.rstrip()))
            if mod in out:
                raise ValueError("%s line %d: %s is listed twice" % (os.path.basename(path), n, mod))
            out[mod] = reason
    return out


def apply_skip(mods, skip, platform=sys.platform):
    """-> (kept, skipped). Skips only on Linux; elsewhere every module is kept and nothing is skipped."""
    if not platform.startswith("linux"):
        return list(mods), []
    return [m for m in mods if m not in skip], sorted(m for m in mods if m in skip)


def load_weights(mods, path=DURATIONS):
    """-> {module: weight}. Recorded seconds; the median for an unrecorded module; 1.0 each when nothing is recorded."""
    try:
        with open(path) as f:
            known = {k: float(v) for k, v in json.load(f).items()
                     if not k.startswith("_") and isinstance(v, (int, float))}
    except (OSError, ValueError):
        known = {}
    if not known:
        return {m: 1.0 for m in mods}
    med = statistics.median(known.values())
    return {m: known.get(m, med) for m in mods}


def parse_shard(text):
    """'K/N' -> (K, N) with 1 <= K <= N; ValueError otherwise."""
    try:
        k, n = (int(x) for x in str(text).split("/"))
    except ValueError:
        raise ValueError("shard wants K/N, like 3/8, got %r" % (text,))
    if not 1 <= k <= n:
        raise ValueError("shard K/N needs 1 <= K <= N, got %s" % text)
    return k, n


def split(mods, weights, n):
    """Exactly n lists (some may be empty), every module in exactly one, longest first into the lightest."""
    totals = [0.0] * n
    shards = [[] for _ in range(n)]
    for m in sorted(mods, key=lambda m: (-weights[m], m)):
        i = min(range(n), key=lambda i: (totals[i], i))
        totals[i] += weights[m]
        shards[i].append(m)
    return shards


def pick(mods, k, n, skip=None, platform=sys.platform, weights=None):
    """The one outcome function. -> (modules of shard k in run order, skipped modules, seconds of that shard)."""
    skip = load_skip() if skip is None else skip
    kept, skipped = apply_skip(mods, skip, platform)
    w = load_weights(kept) if weights is None else weights
    shards = split(kept, w, n)
    return shards[k - 1], skipped, sum(w[m] for m in shards[k - 1])


def main(argv):
    if len(argv) == 3 and argv[1] == "--plan":
        n = int(argv[2])
        kept, skipped = apply_skip(discover(), load_skip(), "linux")
        w = load_weights(kept)
        shards = split(kept, w, n)
        for i, s in enumerate(shards, 1):
            print("shard %d/%d: %3d modules  %7.1f s  heaviest %s" % (i, n, len(s), sum(w[m] for m in s), s[0] if s else "-"))
        tot = sum(w.values())
        print("modules %d, skipped %d, total %.1f s, even split %.1f s, heaviest shard %.1f s" % (
            len(kept), len(skipped), tot, tot / n, max(sum(w[m] for m in s) for s in shards)))
        return 0
    if len(argv) == 2:
        k, n = parse_shard(argv[1])
        mine, skipped, secs = pick(discover(), k, n, platform="linux")
        sys.stderr.write("shard %d/%d: %d modules, about %.0f s recorded; skipped on Linux (macOS-only, tests/ci_linux_skip.txt): %d %s\n"
                         % (k, n, len(mine), secs, len(skipped), " ".join(skipped)))
        print("\n".join(mine))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
