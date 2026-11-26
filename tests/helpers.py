import os, sys, tempfile, shutil, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))


def without_time_line(out):
    """R-1159: every prompt carries one local-time line first. A test about something else drops that line and compares the
    rest: -> the hook output without it, {} (or None for a None input) when nothing else was left."""
    import re
    if not isinstance(out, dict):
        return out
    out = dict(out)
    hso = dict(out.get("hookSpecificOutput") or {})
    text = re.sub(r"\Abudgit: (?:local time|hora local) \d\d:\d\d [^\n]*\n?", "", hso.get("additionalContext", ""))
    if text:
        hso["additionalContext"] = text
        out["hookSpecificOutput"] = hso
    else:
        out.pop("hookSpecificOutput", None)
    return out


def fastest(call, limit, runs=5):
    """CPU seconds of the quickest of up to `runs` calls, stopping at the first one under `limit`. Two things keep a
    bound on how fast something answers true under load: CPU time (process_time) does not grow while the Mac is busy
    with other work, as wall time does, and the quickest run is read, since stray work only ever adds time. A real
    blow-up (a quadratic or exponential shape) costs CPU in every run and still fails."""
    import time
    best = float("inf")
    for _ in range(runs):
        t = time.process_time()
        call()
        best = min(best, time.process_time() - t)
        if best < limit:
            break
    return best


def fastest_child(call, limit, runs=5):
    """Like `fastest`, for a call that runs a child process and waits for it: the CPU seconds the children used."""
    import resource
    best = float("inf")
    for _ in range(runs):
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        call()
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        best = min(best, (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime))
        if best < limit:
            break
    return best


RATIOS = []        # every same-run ratio measured in this process, so a measuring run can read them


def same_run_ratio(call, baseline, k, timer=fastest, floor=0.005):
    """R-873 (timing rule): CPU of `call` and of `baseline` (the same I/O with no work), both the best of 3 measured now,
    here, after a warm baseline pass, with limit 0 so nothing stops early. One isolated rerun is the second attempt, taken
    only when the first ratio is not under `k`. Returns (call_cpu, baseline_cpu) of the last attempt; the caller checks
    them against K and a catastrophe ceiling."""
    baseline()
    base = scan = 0.0
    for attempt in range(2):
        base = max(timer(baseline, 0, runs=3), floor)
        scan = timer(call, 0, runs=3)
        if scan < k * base:
            break
    RATIOS.append(scan / base)
    return scan, base


def assert_ratio(tc, call, baseline, k, ceiling, timer=fastest):
    """The shared assertion: call CPU under k x baseline CPU (same run), and under a catastrophe ceiling in seconds."""
    scan, base = same_run_ratio(call, baseline, k, timer)
    tc.assertLess(scan, k * base, "call %.3fs vs baseline %.4fs (ratio %.1f, K %s)" % (scan, base, scan / base, k))
    tc.assertLess(scan, ceiling)


def patch_env(tc, **values):
    """R-1050: the one way a test changes os.environ. The whole environment is put back by addCleanup (mock.patch.dict), so
    no hand-written restore can be forgotten; a value of None takes the variable out. Returns the patcher's os.environ."""
    from unittest import mock
    patcher = mock.patch.dict(os.environ)
    patcher.start()
    tc.addCleanup(patcher.stop)
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return os.environ


GUARDED_ENV = ("HOME", "BUDGIT_HOME", "STUDIO_HOME")     # R-1050: a class that leaves one of these changed fails


class StudioTestCase(unittest.TestCase):
    DATA = ".studio"        # the data folder this suite's fixtures use; BudgitTestCase runs the same tests on .budgit

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._env_at_class_start = {k: os.environ.get(k) for k in GUARDED_ENV}

    @classmethod
    def tearDownClass(cls):
        """R-1050: HOME, BUDGIT_HOME and STUDIO_HOME must be what they were when the class began. A leak is put back first, so
        it fails this one class and not every class that runs after it, then reported by name."""
        before = getattr(cls, "_env_at_class_start", None)
        try:
            if before is not None:
                after = {k: os.environ.get(k) for k in GUARDED_ENV}
                leaked = {k: (before[k], after[k]) for k in GUARDED_ENV if before[k] != after[k]}
                for k, (old, _) in leaked.items():
                    if old is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = old
                if leaked:
                    raise AssertionError("%s left the environment changed (was, now): %s" % (cls.__name__, leaked))
        finally:
            super().tearDownClass()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "proj")
        os.makedirs(self.project)
        # R-1050: the whole environment is put back by a cleanup registered first, so it runs last (after a subclass's own
        # patch_env cleanups and after tearDown); nothing below needs a hand-written restore.
        # BUDGIT_HOME wins over STUDIO_HOME (studiolib.env): one exported in the shell would send every fixture to the real
        # Budgit home. It is taken out for the test, so STUDIO_HOME alone names the home (R-428).
        # The fixtures assert the Spanish strings; tests/test_i18n.py covers detection and English. Claude Code's own
        # settings are pointed at an empty folder so this machine's language never leaks into a test.
        patch_env(self, STUDIO_HOME=self.home, BUDGIT_HOME=None, BUDGIT_LANG="es",
                  BUDGIT_ADMIT_ADVICE="off",        # the real machine's load must not add a line to a hook's output
                  CLAUDE_CONFIG_DIR=os.path.join(self.tmp, "claude-config"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def init_project(self):
        import studiolib as sl
        os.makedirs(os.path.join(self.project, self.DATA), exist_ok=True)
        sl.save_ledger(self.project, sl.empty_ledger())


class BudgitTestCase(StudioTestCase):
    """R-120: the same fixtures on the new data folder name (tests/test_both_names.py rewrites the suites onto it)."""
    DATA = ".budgit"


def codex_on(home):
    """R-52 T4: the Codex lane is opt-in; tests of the lane switch it on in their temp STUDIO_HOME."""
    import json
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "config.json"), "w") as f:
        json.dump({"codex": "on"}, f)


def protect(home, *dirs):
    """R-52: protected folders come from config (`protected_dirs`); merged into the temp STUDIO_HOME config."""
    import json
    os.makedirs(home, exist_ok=True)
    path = os.path.join(home, "config.json")
    cfg = json.load(open(path)) if os.path.exists(path) else {}
    cfg["protected_dirs"] = list(dirs)
    with open(path, "w") as f:
        json.dump(cfg, f)
