"""R-870: the shared Brote build cache (tests/brotecache.py). A fake swiftc on disk counts its calls; nothing here needs a real compiler.

SHALL lines (each is one test below):
  - a second compile with the same key does not run swiftc          -> test_second_compile_with_same_key_runs_no_swiftc
  - a changed source recompiles                                    -> test_a_source_change_recompiles
  - a changed swiftc version or flag recompiles (part of the key)  -> test_version_and_flags_are_part_of_the_key
  - two concurrent callers produce exactly one compile             -> test_two_concurrent_callers_compile_once
  - a failed compile is not cached                                 -> test_a_failed_compile_is_not_cached
  - an unwritable cache dir falls back to a per-run folder         -> test_unwritable_cache_falls_back
  - with no swiftc the real-compile tests skip with a reason       -> test_no_swiftc_skips_with_a_reason
  - N keys of one run all hit on the second run                    -> test_a_second_run_hits_every_key_one_run_needed
  - a stale key past the bound is evicted                          -> test_a_stale_key_past_the_bound_is_evicted
  - unchanged inputs skip with a reason; a changed one runs; the RC or force env runs
                                  -> test_unchanged_inputs_skip_with_a_reason_that_names_the_test, test_a_changed_input_runs,
                                     test_the_rc_or_force_env_runs_unchanged_inputs
  - a skipped compile test is reported skipped by name, never passed; a changed brote.swift is never skipped
                                  -> test_a_skipped_compile_test_is_reported_skipped_with_its_name_not_passed,
                                     test_a_changed_brote_swift_is_never_skipped
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brotecache as bc

FAKE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "fake swiftc version $FAKE_VERSION"; exit 0; fi
echo x >> "$FAKE_COUNT"
sleep 0.3
[ -n "$FAKE_FAIL" ] && { echo boom >&2; exit 1; }
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { echo built > "$2"; }; shift; done
exit 0
"""


@unittest.skipIf(os.name == "nt", "the fake compiler is a shell script")
class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brotecache-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fake = self.tmp / "swiftc"
        self.fake.write_text(FAKE)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.count = self.tmp / "count"
        self.src = self.tmp / "brote.swift"
        self.src.write_text("v1")
        env = {"BUDGIT_TEST_CACHE": str(self.tmp / "cache"), "FAKE_COUNT": str(self.count), "FAKE_VERSION": "1"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("FAKE_FAIL", None)

    def calls(self):
        return len(self.count.read_text().split()) if self.count.exists() else 0

    def compile(self, args=("-O",)):
        return bc.cached_swiftc(list(args), [self.src], self.tmp, swiftc=str(self.fake))

    def test_second_compile_with_same_key_runs_no_swiftc(self):
        a = self.compile()
        b = self.compile()
        self.assertEqual((a.returncode, a.hit, b.returncode, b.hit), (0, False, 0, True))
        self.assertEqual(self.calls(), 1)
        self.assertTrue((b.path / "out").is_file())

    def test_a_source_change_recompiles(self):
        self.compile()
        self.src.write_text("v2")
        self.compile()
        self.assertEqual(self.calls(), 2)

    def test_version_and_flags_are_part_of_the_key(self):
        self.compile()
        self.compile(("-O", "-D", "CORE_ONLY"))
        self.assertEqual(self.calls(), 2)
        os.environ["FAKE_VERSION"] = "2"
        self.compile()
        self.assertEqual(self.calls(), 3)

    def test_two_concurrent_callers_compile_once(self):
        # two real processes (as pool workers are), not threads, so the file lock is what is under test
        code = ("import sys; sys.path.insert(0, %r); import brotecache as bc; from pathlib import Path\n"
                "r = bc.cached_swiftc(['-O'], [Path(%r)], %r, swiftc=%r); sys.exit(r.returncode)") % (
                    str(Path(__file__).resolve().parent), str(self.src), str(self.tmp), str(self.fake))
        ps = [subprocess.Popen([sys.executable, "-c", code], env=os.environ.copy()) for _ in range(2)]
        self.assertEqual([p.wait(timeout=60) for p in ps], [0, 0])
        self.assertEqual(self.calls(), 1)
        self.assertEqual([n for n in os.listdir(self.tmp / "cache") if ".tmp-" in n], [], "no half-built folder is left")

    def test_a_failed_compile_is_not_cached(self):
        os.environ["FAKE_FAIL"] = "1"
        a = self.compile()
        self.assertNotEqual(a.returncode, 0)
        self.assertIn("boom", a.stderr)
        os.environ.pop("FAKE_FAIL")
        b = self.compile()
        self.assertEqual((b.returncode, b.hit), (0, False))
        self.assertEqual(self.calls(), 2)

    def test_unwritable_cache_falls_back(self):
        bad = self.tmp / "ro"
        bad.write_text("a file, so no folder can be made under it")
        os.environ["BUDGIT_TEST_CACHE"] = str(bad / "sub")
        bc._fallback.clear()
        root = bc.cache_root()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertNotEqual(root, bad / "sub")
        self.assertTrue(os.access(root, os.W_OK))
        self.assertEqual(self.compile().returncode, 0)

    def test_a_changed_sdk_version_gives_a_new_key(self):
        real = bc._tool

        def with_sdk(v):
            return lambda cmd: ("sdk " + v) if "--show-sdk-version" in cmd else real(cmd)
        with mock.patch.object(bc, "_tool", with_sdk("1")):
            k1 = bc.make_key([self.src], ["-O"], str(self.fake))
            self.assertEqual(k1, bc.make_key([self.src], ["-O"], str(self.fake)))
        with mock.patch.object(bc, "_tool", with_sdk("2")):
            self.assertNotEqual(k1, bc.make_key([self.src], ["-O"], str(self.fake)))

    def test_env_var_changes_the_key(self):
        a = bc.make_key([self.src], ["-O"], str(self.fake))
        with mock.patch.dict(os.environ, {"SDKROOT": "/elsewhere"}):
            self.assertNotEqual(a, bc.make_key([self.src], ["-O"], str(self.fake)))

    def test_no_xcrun_uses_none_and_does_not_crash(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "nothing-here")}):
            self.assertEqual(bc._tool(["xcrun", "--show-sdk-version"]), "none")
            self.assertIn("sdk-version=none", bc.toolchain_identity())

    def _store(self, key, age_days=0.0):
        import time
        r = bc.cached_dir(key, lambda d: subprocess.CompletedProcess([], 0, "", ""))
        bc.release_leases()          # the user of this entry is gone
        os.utime(str(self.tmp / "cache" / key), (time.time() - age_days * 86400,) * 2)
        return r

    def _left(self):
        return sorted(n for n in os.listdir(self.tmp / "cache") if not n.endswith((".lock", ".use")))

    def test_a_second_run_hits_every_key_one_run_needed(self):
        n = bc.KEEP_MIN          # as many distinct keys as the bound promises to keep
        keys = ["k%02d" % i for i in range(n)]
        for k in keys:
            self.assertFalse(bc.cached_dir(k, lambda d: subprocess.CompletedProcess([], 0, "", "")).hit)
            bc.release_leases()
        for k in keys:           # the second run: no fake build may run
            def boom(d):
                raise AssertionError("a build ran for a key the first run stored")
            self.assertTrue(bc.cached_dir(k, boom).hit, k)
        self.assertGreaterEqual(bc.KEEP_MIN, 5, "one full run asks for 5 distinct keys")

    def test_a_stale_key_past_the_bound_is_evicted(self):
        for i in range(bc.KEEP_MIN):
            self._store("young%d" % i, age_days=0.1 * i)
        self._store("stale", age_days=bc.KEEP_DAYS + 3)      # beyond the newest KEEP_MIN and older than KEEP_DAYS
        self._store("trigger")                               # a store runs eviction
        left = self._left()
        self.assertNotIn("stale", left)
        self.assertIn("trigger", left)
        self.assertEqual(len([n for n in left if n.startswith("young")]), bc.KEEP_MIN, "recent entries are not touched")

    def test_a_recent_key_beyond_the_minimum_stays_until_the_size_cap(self):
        for i in range(bc.KEEP_MIN + 2):
            self._store("r%02d" % i, age_days=0.01 * (bc.KEEP_MIN + 2 - i))
        self.assertEqual(len(self._left()), bc.KEEP_MIN + 2, "young entries are kept past the minimum")
        bc.evict(self.tmp / "cache", max_bytes=-1)           # a cap nothing fits in: only the minimum stays
        self.assertEqual(len(self._left()), bc.KEEP_MIN)

    def test_the_newest_minimum_stays_even_when_old(self):
        for i in range(bc.KEEP_MIN):
            self._store("o%d" % i, age_days=bc.KEEP_DAYS + 5 + i)
        self._store("trigger")
        self.assertEqual(len([n for n in self._left() if n.startswith("o")]), bc.KEEP_MIN - 1)

    def test_the_default_folder_follows_the_real_home_the_runner_passes(self):
        with mock.patch.dict(os.environ, {"BUDGIT_TEST_REAL_HOME": str(self.tmp / "real"), "HOME": str(self.tmp / "scratch")}):
            os.environ.pop("BUDGIT_TEST_CACHE", None)
            self.assertTrue(str(bc.default_base()).startswith(str(self.tmp / "real")))

    # ---- skip unless changed (R-1196) ----
    def _git(self, changed, rc=0):
        def run(cmd, **kw):
            out = "\n".join(changed) if "diff" in cmd else ""
            return subprocess.CompletedProcess(cmd, rc, out, "")
        return run

    def test_unchanged_inputs_skip_with_a_reason_that_names_the_test(self):
        why = bc.unchanged_skip_reason("test_x.Compile", [self.src], env={}, run=self._git([]))
        self.assertIn("test_x.Compile", why)
        self.assertIn("NOT RUN", why)
        self.assertIn("not passed", why)

    def test_a_changed_input_runs(self):
        self.assertIsNone(bc.unchanged_skip_reason("t", [self.src], env={}, run=self._git(["tools/brote/float/brote.swift"])))

    def test_a_changed_brote_swift_is_never_skipped(self):
        swift = self.tmp / "brote.swift"
        for other in ([], ["README.md"]):                    # nothing else changed, or only an unrelated file
            run = self._git(["brote.swift"] + other)
            self.assertIsNone(bc.unchanged_skip_reason("test_brote_float.Core", [swift], env={}, run=run))

    def test_the_rc_or_force_env_runs_unchanged_inputs(self):
        for var in (bc.RC_ENV, bc.FORCE_REPRO_ENV):
            self.assertIsNone(bc.unchanged_skip_reason("t", [self.src], env={var: "1"}, run=self._git([])), var)

    def test_when_git_cannot_answer_the_test_runs(self):
        self.assertIsNone(bc.unchanged_skip_reason("t", [self.src], env={}, run=self._git([], rc=128)))
        def gone(cmd, **kw):
            raise OSError("no git")
        self.assertIsNone(bc.unchanged_skip_reason("t", [self.src], env={}, run=gone))

    def test_a_skipped_compile_test_is_reported_skipped_with_its_name_not_passed(self):
        why = bc.unchanged_skip_reason("test_x.Compile", [self.src], env={}, run=self._git([]))

        @unittest.skipIf(why is not None, why)
        class Compiles(unittest.TestCase):
            def test_it(self):
                raise AssertionError("must not run")
        res = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(Compiles).run(res)
        self.assertEqual((len(res.failures), len(res.errors)), (0, 0))
        self.assertEqual(len(res.skipped), 1)
        self.assertIn("test_x.Compile", res.skipped[0][1])

    def test_the_compile_modules_use_the_skip_rule_and_no_test_reads_the_live_cache(self):
        here = Path(__file__).resolve().parent
        for mod in ("test_brote_float", "test_brote_scenario", "test_float_build", "test_export_public"):
            text = (here / (mod + ".py")).read_text()
            self.assertTrue("skip_unless_changed" in text or "unchanged_skip_reason" in text, mod)
        self.assertTrue(os.environ["BUDGIT_TEST_CACHE"].startswith(str(self.tmp)), "tests here use a temp cache")

    @unittest.skipIf(bc.fcntl is None, "the lease is a flock; Windows only refreshes the mtime")
    def test_eviction_never_removes_an_entry_another_worker_is_using(self):
        import time
        cache = self.tmp / "cache"
        bc.cached_dir("k0", lambda d: subprocess.CompletedProcess([], 0, "", ""))
        bc.release_leases()
        stale = (time.time() - (bc.KEEP_DAYS + 1) * 86400,) * 2
        os.utime(str(cache / "k0"), stale)       # k0 is the oldest and past the age bound, as eviction sees it
        # another worker process hits k0 and keeps using it (its lease lasts while the process lives)
        code = ("import sys, time; sys.path.insert(0, %r); import brotecache as bc, subprocess\n"
                "r = bc.cached_dir('k0', lambda d: subprocess.CompletedProcess([], 0, '', ''))\n"
                "print('ready', r.hit, flush=True); sys.stdin.readline()") % str(Path(__file__).resolve().parent)
        user = subprocess.Popen([sys.executable, "-c", code], env=os.environ.copy(), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(user.stdout.readline().split(), ["ready", "True"])
            os.utime(str(cache / "k0"), stale)   # age it again after the hit refreshed it
            for i in range(1, bc.KEEP_MIN + 2):
                bc.cached_dir("k%d" % i, lambda d: subprocess.CompletedProcess([], 0, "", ""))
                bc.release_leases()
                os.utime(str(cache / ("k%d" % i)), (time.time() - (100 - i),) * 2)
            self.assertTrue((cache / "k0").is_dir(), "an entry in use by another worker was evicted")
        finally:
            user.stdin.write("\n")
            user.stdin.flush()
            user.wait(timeout=30)
            user.stdin.close()
            user.stdout.close()
        # once its user is gone it is evictable again
        bc.cached_dir("k99", lambda d: subprocess.CompletedProcess([], 0, "", ""))
        self.assertFalse((cache / "k0").exists())

    def test_a_hit_refreshes_the_entry_mtime(self):
        import time
        bc.cached_dir("k0", lambda d: subprocess.CompletedProcess([], 0, "", ""))
        os.utime(str(self.tmp / "cache" / "k0"), (time.time() - 5000,) * 2)
        bc.cached_dir("k0", lambda d: subprocess.CompletedProcess([], 0, "", ""))
        self.assertGreater((self.tmp / "cache" / "k0").stat().st_mtime, time.time() - 100)

    def test_the_fallback_folder_is_removed(self):
        bad = self.tmp / "ro"
        bad.write_text("a file")
        os.environ["BUDGIT_TEST_CACHE"] = str(bad / "sub")
        bc._fallback.clear()
        root = bc.cache_root()
        self.assertTrue(root.is_dir())
        bc.cleanup_fallback()
        self.assertFalse(root.exists())

    def test_repro_marker_skips_once_proven_and_names_the_key(self):
        a = self.compile()
        self.assertIsNone(bc.repro_skip_reason(a), "a fresh build has no marker: the repro build runs")
        bc.mark_repro(a)
        b = self.compile()
        self.assertTrue(b.hit)
        why = bc.repro_skip_reason(b)
        self.assertIn(b.key, why)
        self.assertEqual(self.calls(), 1)
        with mock.patch.dict(os.environ, {bc.FORCE_REPRO_ENV: "1"}):
            self.assertIsNone(bc.repro_skip_reason(b), "the test-only env var forces the rerun")
        self.src.write_text("v2")
        c = self.compile()
        self.assertIsNone(bc.repro_skip_reason(c), "a new key has no marker")

    def test_a_script_build_keys_on_usr_bin_swiftc_not_the_callers_path(self):
        script = self.tmp / "build.sh"
        script.write_text("#!/bin/sh\nmkdir -p \"$BROTE_OUT\"\necho x >> \"$FAKE_COUNT\"\n")
        seen = []
        real = bc.make_key

        def spy(files, extra, swiftc):
            seen.append(swiftc)
            return real(files, extra, swiftc)
        with mock.patch.object(bc, "make_key", spy), mock.patch.object(bc, "swiftc_version", lambda s: "v"), \
                mock.patch.dict(os.environ, {"PATH": str(self.tmp) + os.pathsep + os.environ.get("PATH", "")}):
            bc.cached_script_build(script, [self.src], None, [])
        if os.path.exists(bc.SCRIPT_SWIFTC):
            self.assertEqual(seen, ["/usr/bin/swiftc"])
        self.assertNotEqual(seen[0], str(self.fake))

    def test_no_swiftc_skips_with_a_reason(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "nothing-here")}):
            self.assertIsNone(bc.swiftc_or_none())
        # the real-compile tests gate on exactly this value
        import test_brote_float
        with mock.patch.object(test_brote_float, "SWIFTC", None):
            self.assertFalse(test_brote_float.SWIFTC and sys.platform == "darwin")
        skip = getattr(test_brote_float.Core, "__unittest_skip__", None)
        if bc.swiftc_or_none() is None or sys.platform != "darwin":
            self.assertTrue(skip)
            self.assertIn("swiftc", test_brote_float.Core.__unittest_skip_why__)


if __name__ == "__main__":
    unittest.main()
