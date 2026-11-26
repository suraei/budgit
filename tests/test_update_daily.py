"""R-555: a daily check of the public plugin.json, so a user whose marketplace clone is never refreshed still hears of a
new release. The cache is read in the hook's path; a stale one is refreshed by a detached child. No test touches the network."""
import json
import os
import time
import unittest
from unittest import mock

from helpers import StudioTestCase, ROOT
import updatelib

URL = "https://raw.githubusercontent.com/someone/budgit/main/plugin/.claude-plugin/plugin.json"


class DailyCheckTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.home, exist_ok=True)
        self.root = os.path.join(self.tmp, "plugin")
        os.makedirs(os.path.join(self.root, ".claude-plugin"))
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "budgit", "version": "1.0.0", "homepage": "https://github.com/someone/budgit"}, f)
        self.cache = os.path.join(self.home, "update-check.json")
        os.environ.pop("BUDGIT_NO_UPDATE_CHECK", None)

    def tearDown(self):
        os.environ.pop("BUDGIT_NO_UPDATE_CHECK", None)
        super().tearDown()

    def write_cache(self, version, age):
        with open(self.cache, "w") as f:
            json.dump({"checked_at": time.time() - age, "version": version}, f)

    def read_cache(self):
        with open(self.cache) as f:
            return json.load(f)

    def state(self):
        with mock.patch.object(updatelib, "_spawn_refresh") as spawn:
            got = updatelib.update_state(loaded="1.0.0", root=self.root)
        return got, spawn

    def test_a_stale_cache_starts_one_refresh(self):
        self.write_cache("1.0.0", 25 * 3600)
        _got, spawn = self.state()
        self.assertEqual(spawn.call_count, 1)

    def test_no_cache_starts_one_refresh(self):
        _got, spawn = self.state()
        self.assertEqual(spawn.call_count, 1)

    def test_a_fresh_cache_does_not_refresh(self):
        self.write_cache("1.0.0", 3600)
        _got, spawn = self.state()
        self.assertEqual(spawn.call_count, 0)

    def test_a_newer_public_version_shows_the_notice(self):
        self.write_cache("1.2.0", 3600)
        got, _spawn = self.state()
        self.assertEqual(got, {"kind": "available", "running": "1.0.0", "available": "1.2.0"})

    def test_an_equal_or_older_public_version_shows_nothing(self):
        for v in ("1.0.0", "0.9.0"):
            self.write_cache(v, 3600)
            self.assertIsNone(self.state()[0])

    def test_an_invalid_cached_version_is_ignored(self):
        self.write_cache("99; rm -rf", 3600)
        self.assertIsNone(self.state()[0])

    def test_a_garbled_cache_file_is_ignored(self):
        with open(self.cache, "w") as f:
            f.write("{nope")
        got, _spawn = self.state()
        self.assertIsNone(got)

    def test_the_refresh_claims_the_interval_before_the_child_runs(self):
        with mock.patch.object(updatelib.subprocess, "Popen") as popen:
            updatelib._spawn_refresh(self.root)
        self.assertEqual(popen.call_count, 1)
        self.assertLess(time.time() - self.read_cache()["checked_at"], 5)

    def test_refresh_stores_the_fetched_version(self):
        with mock.patch.object(updatelib, "fetch_public_version", return_value="1.3.0") as fetch:
            updatelib.refresh_cache(self.root)
        fetch.assert_called_once_with(URL)
        data = self.read_cache()
        self.assertEqual(data["version"], "1.3.0")
        self.assertLess(time.time() - data["checked_at"], 5)

    def test_a_network_error_is_silent_keeps_the_old_version_and_writes_checked_at(self):
        self.write_cache("1.1.0", 25 * 3600)
        with mock.patch.object(updatelib, "fetch_public_version", side_effect=updatelib.UpdateError("URLError")):
            updatelib.refresh_cache(self.root)
        data = self.read_cache()
        self.assertEqual(data["version"], "1.1.0")
        self.assertLess(time.time() - data["checked_at"], 5)

    def test_an_unexpected_error_never_raises(self):
        with mock.patch.object(updatelib, "fetch_public_version", side_effect=RuntimeError("boom")):
            updatelib.refresh_cache(self.root)
        self.assertLess(time.time() - self.read_cache()["checked_at"], 5)

    def test_the_env_opt_out_stops_the_refresh_and_the_notice(self):
        self.write_cache("1.2.0", 25 * 3600)
        os.environ["BUDGIT_NO_UPDATE_CHECK"] = "1"
        got, spawn = self.state()
        self.assertEqual(spawn.call_count, 0)
        self.assertIsNone(got)
        with mock.patch.object(updatelib, "fetch_public_version") as fetch:
            updatelib.refresh_cache(self.root)
        self.assertEqual(fetch.call_count, 0)

    def test_the_config_opt_out_stops_the_refresh(self):
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"update_check": "off"}, f)
        _got, spawn = self.state()
        self.assertEqual(spawn.call_count, 0)

    def test_a_checkout_that_was_never_exported_never_asks(self):
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "budgit", "version": "1.0.0"}, f)
        _got, spawn = self.state()
        self.assertEqual(spawn.call_count, 0)

    def test_the_request_is_a_plain_get_to_the_fixed_url(self):
        seen = {}

        def fake(url, timeout=None):
            seen["url"], seen["timeout"] = url, timeout
            raise OSError("no network in tests")
        with mock.patch.object(updatelib.urllib.request, "urlopen", fake):
            with self.assertRaises(updatelib.UpdateError):
                updatelib.fetch_public_version(URL)
        self.assertEqual(seen["url"], URL)
        self.assertNotIn("?", seen["url"])

    def test_the_version_pattern_is_strict(self):
        for bad in ("1.2.3\n", "١.٢", "1.2.3.4.5", "123456", "1..2", "", "1.2.3 "):
            self.assertFalse(updatelib.valid_version(bad), repr(bad))
        for good in ("1", "0.13.3", "12345.1.2.3"):
            self.assertTrue(updatelib.valid_version(good), good)

    def test_a_bad_checked_at_counts_as_stale(self):
        now = time.time()
        for raw in ("NaN", "Infinity", "-Infinity", '"yesterday"', "null", "true", repr(now + 25 * 3600), repr(now + 10 ** 9)):
            with open(self.cache, "w") as f:
                f.write('{"checked_at": %s, "version": "1.0.0"}' % raw)
            _got, spawn = self.state()
            self.assertEqual(spawn.call_count, 1, raw)

    def test_a_checked_at_a_little_ahead_is_still_fresh(self):
        self.write_cache("1.0.0", -3600)
        self.assertEqual(self.state()[1].call_count, 0)

    def test_a_busy_lock_means_no_spawn(self):
        import fcntl
        fd = os.open(os.path.join(self.home, "update-check.lock"), os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with mock.patch.object(updatelib.subprocess, "Popen") as popen:
                updatelib._spawn_refresh(self.root)
            self.assertEqual(popen.call_count, 0)
            self.assertFalse(os.path.exists(self.cache))
        finally:
            os.close(fd)

    def test_a_cache_that_turned_fresh_inside_the_lock_means_no_spawn(self):
        # The caller saw a stale cache; by the time the lock is held another process has refreshed it.
        self.write_cache("1.0.0", 3600)
        with mock.patch.object(updatelib.subprocess, "Popen") as popen:
            updatelib._spawn_refresh(self.root)
        self.assertEqual(popen.call_count, 0)

    def test_the_lock_is_released_before_the_child_starts(self):
        import fcntl
        seen = {}

        def fake_popen(*a, **k):
            fd = os.open(os.path.join(self.home, "update-check.lock"), os.O_WRONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen["free"] = True
            except OSError:
                seen["free"] = False
            finally:
                os.close(fd)
        with mock.patch.object(updatelib.subprocess, "Popen", fake_popen):
            updatelib._spawn_refresh(self.root)
        self.assertTrue(seen["free"])

    def test_a_fifo_in_place_of_the_cache_never_blocks(self):
        import signal
        os.mkfifo(self.cache)
        signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(AssertionError("blocked on the FIFO")))
        signal.alarm(5)
        try:
            got, spawn = self.state()
        finally:
            signal.alarm(0)
        self.assertIsNone(got)
        self.assertEqual(spawn.call_count, 1)

    def test_a_fifo_in_place_of_the_lock_never_blocks(self):
        import signal
        os.mkfifo(os.path.join(self.home, "update-check.lock"))
        signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(AssertionError("blocked on the FIFO")))
        signal.alarm(5)
        try:
            with mock.patch.object(updatelib.subprocess, "Popen") as popen:
                try:
                    updatelib._spawn_refresh(self.root)
                except OSError:
                    pass                                    # the caller swallows it
        finally:
            signal.alarm(0)
        self.assertEqual(popen.call_count, 0)

    def test_the_public_text_names_the_daily_check(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        public_text = {}
        for rel in ("share/README.md", "docs/wiki/Privacy-and-security.md"):
            with open(os.path.join(base, rel), encoding="utf-8") as f:
                text = f.read()
            public_text[rel] = text
            for needle in ("raw.githubusercontent.com", '"update_check": "off"', "once a day"):
                self.assertIn(needle, text, "%s lacks %s" % (rel, needle))
        with open(os.path.join(base, "docs", "wiki", "Customise.md"), encoding="utf-8") as f:
            public_text["docs/wiki/Customise.md"] = f.read()
        self.assertTrue(any("BUDGIT_NO_UPDATE_CHECK" in text for text in public_text.values()),
                        "public docs lack BUDGIT_NO_UPDATE_CHECK")
        with open(os.path.join(base, "share", "README.md"), encoding="utf-8") as f:
            self.assertNotIn("only when you run `budgit scout`", f.read())
        with open(os.path.join(base, "share", "CHANGELOG.md"), encoding="utf-8") as f:
            self.assertIn("BUDGIT_NO_UPDATE_CHECK", f.read())


if __name__ == "__main__":
    unittest.main()
