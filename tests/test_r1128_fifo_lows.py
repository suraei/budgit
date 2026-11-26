"""R-1128 LOW-1/2: a Budgit file a hook opens is opened only when it is a regular file; a FIFO planted there never blocks."""
import os
import tempfile
import threading
import time
import unittest
import unittest.mock

import hook
import hookfallback


def _within(fn, seconds=2.0):
    """Run fn in a daemon thread; -> True when it returned within `seconds`."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(seconds)
    return not t.is_alive()


class FifoLows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BUDGIT_HOME")
        os.environ["BUDGIT_HOME"] = self.tmp.name
        self.addCleanup(self._restore)
        self.assertEqual(hookfallback.home(), self.tmp.name)

    def _restore(self):
        if self.old is None:
            os.environ.pop("BUDGIT_HOME", None)
        else:
            os.environ["BUDGIT_HOME"] = self.old

    def _hold_reader(self, path):
        """Keep a reader open on the FIFO so a blocking write open would otherwise succeed or hang; closed by cleanup."""
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, fd)
        return fd

    def test_hooks_log_fifo_returns_and_writes_nothing(self):
        path = os.path.join(self.tmp.name, "hooks.log")
        os.mkfifo(path)
        rfd = self._hold_reader(path)
        self.assertTrue(_within(lambda: hookfallback.log_library_failure("session-start")))
        try:
            data = os.read(rfd, 100)             # nothing written: EOF (no writer) or would-block
        except BlockingIOError:
            data = b""
        self.assertEqual(data, b"")

    def test_hooks_log_fifo_no_reader_returns(self):
        os.mkfifo(os.path.join(self.tmp.name, "hooks.log"))
        self.assertTrue(_within(lambda: hookfallback.log_library_failure("session-start")))

    def test_hooks_log_regular_file_still_gets_the_line(self):
        hookfallback.log_library_failure("session-start")
        with open(os.path.join(self.tmp.name, "hooks.log"), encoding="utf-8") as fh:
            self.assertIn("session-start", fh.read())

    def test_count_fifo_returns_and_writes_nothing(self):
        path = os.path.join(self.tmp.name, "hook-fallback.count")
        os.mkfifo(path)
        rfd = self._hold_reader(path)
        self.assertTrue(_within(hookfallback.count))
        try:
            data = os.read(rfd, 100)             # nothing written: EOF (no writer) or would-block
        except BlockingIOError:
            data = b""
        self.assertEqual(data, b"")

    def test_count_regular_file_still_counts(self):
        hookfallback.count()
        hookfallback.count()
        with open(os.path.join(self.tmp.name, "hook-fallback.count"), encoding="ascii") as fh:
            self.assertEqual(fh.read(), "2")

    def test_stamp_fifo_read_returns_none(self):
        path = os.path.join(self.tmp.name, "stamp")
        os.mkfifo(path)
        out = []
        self.assertTrue(_within(lambda: out.append(hook.read_regular(path))))
        self.assertEqual(out, [None])

    def test_stamp_regular_file_is_read(self):
        path = os.path.join(self.tmp.name, "stamp")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("2026-11-16\n")
        self.assertEqual(hook.read_regular(path).strip(), "2026-11-16")

    def test_stamp_symlink_and_missing_return_none(self):
        real = os.path.join(self.tmp.name, "real")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write("x")
        link = os.path.join(self.tmp.name, "link")
        os.symlink(real, link)
        self.assertIsNone(hook.read_regular(link))
        self.assertIsNone(hook.read_regular(os.path.join(self.tmp.name, "nope")))


class FifoRound2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _home(self):
        old = os.environ.get("BUDGIT_HOME")
        os.environ["BUDGIT_HOME"] = self.tmp.name
        self.addCleanup(lambda: os.environ.pop("BUDGIT_HOME", None) if old is None else os.environ.__setitem__("BUDGIT_HOME", old))

    def test_config_fifo_returns_unreadable(self):
        self._home()
        os.mkfifo(os.path.join(self.tmp.name, "config.json"))
        out = []
        self.assertTrue(_within(lambda: out.append(hookfallback.protected_state(self.tmp.name))))
        self.assertFalse(out[0][2])

    def test_config_regular_file_still_read(self):
        self._home()
        with open(os.path.join(self.tmp.name, "config.json"), "w", encoding="utf-8") as fh:
            fh.write('{"protected_dirs": ["/tmp/zz-protected"]}')
        folders, _, readable = hookfallback.protected_state(self.tmp.name)
        self.assertTrue(readable)
        self.assertEqual(len(folders), 1)

    def test_project_json_fifo_returns_unreadable(self):
        self._home()
        os.mkdir(os.path.join(self.tmp.name, ".budgit"))
        os.mkfifo(os.path.join(self.tmp.name, ".budgit", "project.json"))
        out = []
        self.assertTrue(_within(lambda: out.append(hookfallback.protected_state(self.tmp.name))))
        self.assertFalse(out[0][2])

    def test_project_json_regular_file_still_read(self):
        self._home()
        os.mkdir(os.path.join(self.tmp.name, ".budgit"))
        with open(os.path.join(self.tmp.name, ".budgit", "project.json"), "w", encoding="utf-8") as fh:
            fh.write('{"protected_paths": ["secret"]}')
        _, patterns, readable = hookfallback.protected_state(self.tmp.name)
        self.assertTrue(readable)
        self.assertEqual(len(patterns), 1)

    def test_on_tm_portfolio_fifo_returns_false(self):
        os.mkfifo(os.path.join(self.tmp.name, "portfolio.json"))
        out = []
        with unittest.mock.patch.object(hook.sl, "home_state", return_value=self.tmp.name):
            self.assertTrue(_within(lambda: out.append(hook.on_tm("user-prompt-submit", {"session_id": "s1"}))))
        self.assertEqual(out, [False])

    def test_on_tm_regular_file_still_read(self):
        with open(os.path.join(self.tmp.name, "portfolio.json"), "w", encoding="utf-8") as fh:
            fh.write('{"tm": "other"}')
        with unittest.mock.patch.object(hook.sl, "home_state", return_value=self.tmp.name):
            self.assertFalse(hook.on_tm("stop", {"session_id": "s1"}))


if __name__ == "__main__":
    unittest.main()
