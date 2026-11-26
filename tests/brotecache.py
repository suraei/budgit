"""R-870: a shared build cache for the tests that really compile Swift (Brote).

The full suite used to compile brote.swift several times per run (one `swiftc -O` is ~800 MB). Now a build is keyed by
sha256(input file bytes + `swiftc --version` + flags) and kept OUTSIDE the repo and every Budgit home:
  macOS   ~/Library/Caches/budgit-test/brote/<key>
  Linux   $XDG_CACHE_HOME or ~/.cache  /budgit-test/brote/<key>
  Windows %LOCALAPPDATA%\\budgit-test\\brote\\<key>
Override with BUDGIT_TEST_CACHE (tests use it). A cache dir that cannot be written falls back to a per-run temp dir (said once on
stderr). A build is made in a temp name and moved with os.replace, under one lock file per key, so pool workers never see a
half-built folder and only one of them compiles; the others wait on the lock and then hit.
Only a successful build is cached. No swiftc on the machine: callers skip with a reason (see swiftc_or_none).
"""
import atexit
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    import fcntl
except ImportError:      # Windows: a lock file made with O_EXCL instead
    fcntl = None

_fallback = []


def cleanup_fallback():
    """Remove the per-run fallback folder (runs at exit; tests may call it)."""
    while _fallback:
        shutil.rmtree(_fallback.pop(), ignore_errors=True)


class Built:
    """What a caller needs from a build: returncode and stderr like a CompletedProcess, the folder, and whether the cache answered."""
    def __init__(self, returncode, stdout, stderr, path, hit, key=None):
        self.returncode, self.stdout, self.stderr, self.path, self.hit, self.key = returncode, stdout, stderr, path, hit, key


def swiftc_or_none():
    return shutil.which("swiftc")


def swiftc_version(swiftc):
    r = subprocess.run([swiftc, "--version"], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def default_base():
    """The persistent cache folder of this machine (no disk access). The test runner gives every worker a scratch HOME and
    passes the real one as BUDGIT_TEST_REAL_HOME, so the folder is the same for every run."""
    home = Path(os.environ["BUDGIT_TEST_REAL_HOME"]) if os.environ.get("BUDGIT_TEST_REAL_HOME") else Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "budgit-test" / "brote"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "budgit-test" / "brote"
    return Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "budgit-test" / "brote"


def cache_root():
    env = os.environ.get("BUDGIT_TEST_CACHE")
    if env:
        base = Path(env)
    else:
        base = default_base()
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = tempfile.NamedTemporaryFile(dir=str(base), prefix=".w", delete=True)
        probe.close()
        return base
    except OSError:
        if not _fallback:
            _fallback.append(Path(tempfile.mkdtemp(prefix="budgit-test-brote-")))
            atexit.register(cleanup_fallback)
            sys.stderr.write("brotecache: %s is not writable; using the per-run folder %s (no sharing between runs)\n" % (base, _fallback[0]))
        return _fallback[0]


# Eviction rule, applied after a successful store: the KEEP_MIN newest entries always stay (one full run asks for 5 distinct keys,
# so 8 leaves room for a changed source); older ones stay while they are at most KEEP_DAYS old and the running total of the
# entries newer than them is within KEEP_MAX_BYTES. An entry in use (leased) is never removed.
KEEP_MIN = 8
KEEP_DAYS = 7
KEEP_MAX_BYTES = 3 * 1024 ** 3
KEY_ENV = ("SDKROOT", "MACOSX_DEPLOYMENT_TARGET", "DEVELOPER_DIR", "TOOLCHAINS", "SOURCE_DATE_EPOCH", "BROTE_FORCE_OS")   # read by swiftc or the build scripts


def _tool(cmd):
    """Output of a toolchain query, or the fixed value "none" when the tool is missing or fails (Linux, Windows, no Xcode)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).strip()
        return out if r.returncode == 0 and out else "none"
    except (OSError, subprocess.SubprocessError):
        return "none"


def toolchain_identity():
    """What `swiftc --version` does not show: SDK path, SDK version, linker identity, and the env vars the build reads."""
    parts = ["sdk-path=" + _tool(["xcrun", "--show-sdk-path"]),
             "sdk-version=" + _tool(["xcrun", "--show-sdk-version"]),
             "ld=" + _tool(["xcrun", "ld", "-v"])]
    parts += ["%s=%s" % (k, os.environ.get(k, "")) for k in KEY_ENV]
    return "\n".join(parts)


def make_key(files, extra, swiftc):
    h = hashlib.sha256()
    h.update(b"T" + toolchain_identity().encode() + b"\0")
    for f in sorted(str(x) for x in files):
        h.update(b"F" + os.path.basename(f).encode() + b"\0")
        h.update(Path(f).read_bytes())
        h.update(b"\0")
    h.update(b"V" + swiftc_version(swiftc).encode() + b"\0")
    for e in extra:
        h.update(b"A" + str(e).encode() + b"\0")
    return h.hexdigest()


class _Lock:
    def __init__(self, path):
        self.path, self.fd = str(path), None

    def __enter__(self):
        if fcntl:
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        else:
            end = time.time() + 1800
            while True:
                try:
                    self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    break
                except FileExistsError:
                    if time.time() > end:
                        raise
                    time.sleep(0.2)
        return self

    def __exit__(self, *a):
        if fcntl:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
        else:
            os.close(self.fd)
            os.unlink(self.path)


_leases = []      # open lease fds, held until this process ends: the entries this process uses cannot be evicted


def lease(root, key):
    """Mark entry `key` as in use by this process (a shared flock on <key>.use, held to exit) and refresh its mtime.
    Without fcntl (Windows) only the mtime is refreshed."""
    try:
        os.utime(str(Path(root) / key))
    except OSError:
        pass
    if fcntl:
        try:
            fd = os.open(str(Path(root) / (key + ".use")), os.O_CREAT | os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_SH)
            _leases.append(fd)
        except OSError:
            pass


def release_leases():
    """Drop this process's leases (they end at exit anyway; tests call it to play 'the user process is gone')."""
    while _leases:
        try:
            os.close(_leases.pop())
        except OSError:
            pass


def _in_use(root, name):
    """True when another process (or this one) holds a lease on entry `name`."""
    if not fcntl:
        return False
    try:
        fd = os.open(str(Path(root) / (name + ".use")), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def _tree_bytes(path):
    total = 0
    for d, _dirs, files in os.walk(str(path)):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total


def evict(root, keep=KEEP_MIN, max_age=KEEP_DAYS * 86400, max_bytes=KEEP_MAX_BYTES, now=None):
    """Under the caller's lock: keep the newest `keep` key folders (by mtime) and any older one that is at most `max_age` seconds
    old while the running size stays within `max_bytes`; remove the rest, never one that is in use (leased).
    A hit refreshes the mtime, so a used entry is also young; the lease covers one used for a long time."""
    now = time.time() if now is None else now
    entries = [p for p in Path(root).iterdir() if p.is_dir() and ".tmp-" not in p.name]
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    doomed = []
    for i, p in enumerate(entries):
        total += _tree_bytes(p)
        if i >= keep and (now - p.stat().st_mtime > max_age or total > max_bytes):
            doomed.append(p)
    for old in doomed:
        if _in_use(root, old.name):
            continue
        shutil.rmtree(old, ignore_errors=True)
        try:
            (Path(root) / (old.name + ".use")).unlink()
        except OSError:
            pass


def cached_dir(key, build):
    """Return Built(path = the cached folder for key). build(dest) fills the empty folder dest and returns a CompletedProcess-like."""
    root = cache_root()
    final = root / key
    with _Lock(root / (key + ".lock")):
        if final.is_dir():
            lease(root, key)
            return Built(0, "", "", final, True, key)
        tmp = Path(tempfile.mkdtemp(prefix=key[:12] + ".tmp-", dir=str(root)))
        try:
            r = build(tmp)
            if r.returncode != 0:
                return Built(r.returncode, r.stdout, r.stderr, None, False, key)
            os.replace(str(tmp), str(final))
            tmp = None
            lease(root, key)
            evict(root)
            return Built(0, r.stdout, r.stderr, final, False, key)
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)


def cached_swiftc(args, inputs, cwd, swiftc=None, out_name="out"):
    """swiftc <args> -o <dir>/<out_name>, once per key. args must not hold temp paths (they are part of the key).
    Returns Built; .path / out_name is the binary."""
    swiftc = swiftc or swiftc_or_none()
    key = make_key(inputs, ["swiftc"] + list(args), swiftc)

    def build(dest):
        mc = dest / "mc"
        mc.mkdir()
        r = subprocess.run([swiftc] + list(args) + ["-module-cache-path", str(mc), "-o", str(dest / out_name)],
                           capture_output=True, text=True, cwd=str(cwd))
        shutil.rmtree(mc, ignore_errors=True)
        return r
    return cached_dir(key, build)


def cached_script_build(script, inputs, _unused, extra, env=None, timeout=900, swiftc=None):
    """Run a build script that writes a bundle folder under $BROTE_OUT, once per key; the result folder is Built.path (the BROTE_OUT contents)."""
    # the script sets PATH=/usr/bin:/bin, so the compiler it runs is /usr/bin/swiftc, not the caller's PATH one (the key must version that one)
    swiftc = swiftc or (SCRIPT_SWIFTC if os.path.exists(SCRIPT_SWIFTC) else swiftc_or_none())
    key = make_key(list(inputs) + [script], ["script"] + list(extra), swiftc)

    def build(dest):
        return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=timeout,
                              env={**(env or os.environ), "BROTE_OUT": str(dest / "out")})
    return cached_dir(key, build)


SCRIPT_SWIFTC = "/usr/bin/swiftc"
REPRO_MARKER = "repro-ok"
FORCE_REPRO_ENV = "BUDGIT_TEST_FORCE_REPRO"


def mark_repro(built):
    """After the reproducibility build passed for this cache entry, record it in the entry."""
    if built.path:
        try:
            (Path(built.path) / REPRO_MARKER).write_text("ok\n")
        except OSError:
            pass


RC_ENV = "BUDGIT_TEST_RC"      # set by the release-candidate suite: the compile tests run whatever changed
BASE_REF = "main"
_ROOT = Path(__file__).resolve().parents[1]


def changed_inputs(paths, root=None, run=subprocess.run):
    """The `paths` that differ from BASE_REF in the working tree (tracked changes and untracked files). When git cannot answer
    (no repo, no main) every path counts as changed, so a doubt runs the test."""
    root = str(root or _ROOT)
    paths = [str(p) for p in paths]
    try:
        a = run(["git", "-C", root, "diff", "--name-only", BASE_REF, "--"] + paths, capture_output=True, text=True, timeout=60)
        b = run(["git", "-C", root, "ls-files", "--others", "--exclude-standard", "--"] + paths, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return paths
    if a.returncode != 0 or b.returncode != 0:
        return paths
    return sorted(set((a.stdout + b.stdout).split()))


def unchanged_skip_reason(name, inputs, root=None, env=None, run=subprocess.run):
    """None when the Swift-compiling test `name` must run: BUDGIT_TEST_RC or BUDGIT_TEST_FORCE_REPRO is set, or one of its inputs
    (plus this cache module) changed against main. Otherwise the skip reason; it says the test did NOT run."""
    env = os.environ if env is None else env
    if env.get(RC_ENV) or env.get(FORCE_REPRO_ENV):
        return None
    if changed_inputs(list(inputs) + [Path(__file__).resolve()], root, run):
        return None
    return "NOT RUN (skipped, not passed): %s compiles Swift and its inputs are unchanged against %s (set %s=1 or %s=1 to run it)" % (
        name, BASE_REF, RC_ENV, FORCE_REPRO_ENV)


def skip_unless_changed(name, inputs):
    """Class or test decorator: skip with unchanged_skip_reason (computed once, at import). Put it BELOW the platform skip so
    that reason wins on a machine without swiftc."""
    why = unchanged_skip_reason(name, inputs)
    return unittest.skipIf(why is not None, why or "")


def repro_skip_reason(built):
    """None when the reproducibility build must run; else the skip reason (names the key). Runs when the entry was just
    built (miss), has no marker, or BUDGIT_TEST_FORCE_REPRO is set (nightly, RC)."""
    if os.environ.get(FORCE_REPRO_ENV) or not built.hit or not built.path or not (Path(built.path) / REPRO_MARKER).exists():
        return None
    return "reproducibility already proven for cache key %s (set %s=1 to force it)" % (built.key, FORCE_REPRO_ENV)
