"""R-1322 protect-ports round 2 (security HOLD 0/1/3). A scratch home, a fake lsof and a fake plan; os.kill must never be called."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from helpers import StudioTestCase
import procsweep
import studiolib as sl

NEW_PLAN = {"stop": [{"pid": 100, "ports": [8000], "command": "python3 -m http.server"}], "kept": [], "blocked": None,
            "stopped": [], "still_running": [], "applied": False}


class Round2(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.home, "config.json")
        kill = mock.patch("os.kill")
        self.kill = kill.start()
        self.addCleanup(kill.stop)
        self.addCleanup(lambda: self.kill.assert_not_called())

    def put(self, data):
        os.makedirs(self.home, exist_ok=True)
        with open(self.path, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")

    def cfg(self):
        with open(self.path) as f:
            return json.load(f)

    def call(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = procsweep.protect_main(**dict({"lang": "en"}, **kw))
        return rc, out.getvalue(), err.getvalue()

    # 1. MED: a skipped candidate is named, with the line that protects it
    def test_protect_listening_names_each_skipped_candidate_and_exits_non_zero(self):
        self.put({"a": 1})
        lsof = "p100\nn*:8000\np200\nn127.0.0.1:3000\n"
        with mock.patch.object(procsweep, "run", return_value=NEW_PLAN), mock.patch.object(procsweep, "_lsof", return_value=lsof):
            rc, out, err = self.call(listening=True)
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.cfg()["housekeeping"]["proc_protect_ports"], [3000])        # the other ports are still saved
        text = out + err
        self.assertIn("100", text)
        self.assertIn("python3", text)
        self.assertIn("--protect-port 8000", text)

    def test_protect_listening_with_no_skipped_candidate_exits_zero(self):
        self.put({"a": 1})
        plan = dict(NEW_PLAN, stop=[])
        with mock.patch.object(procsweep, "run", return_value=plan), mock.patch.object(procsweep, "_lsof", return_value="p200\nn*:3000\n"):
            self.assertEqual(self.call(listening=True)[0], 0)

    # 2. LOW: ASCII digits only in the lsof output
    def test_a_unicode_digit_in_the_lsof_output_is_skipped_without_a_traceback(self):
        self.put({"a": 1})
        plan = dict(NEW_PLAN, stop=[])
        with mock.patch.object(procsweep, "run", return_value=plan), \
                mock.patch.object(procsweep, "_lsof", return_value="p200\nn*:²\np201\nn*:3000\n"):
            rc, out, err = self.call(listening=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.cfg()["housekeeping"]["proc_protect_ports"], [3000])
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.call(add=["²"])[0], 2)

    # 3. LOW: a saved port written as text is read like the sweep reads it
    def test_the_setter_normalizes_a_saved_text_port_to_an_int_and_still_refuses_bad_ones(self):
        self.put({"housekeeping": {"proc_protect_ports": ["8080", 4321]}})
        self.assertEqual(sl.set_proc_protect_ports([80], []), [80, 4321, 8080])
        self.assertEqual(self.cfg()["housekeeping"]["proc_protect_ports"], [80, 4321, 8080])
        for bad in (True, 70000, "0", "80/tcp", 0):
            self.put({"housekeeping": {"proc_protect_ports": [bad]}})
            before = open(self.path).read()
            with self.assertRaises(sl.StudioError):
                sl.set_proc_protect_ports([80], [])
            self.assertEqual(open(self.path).read(), before)

    # 4. LOW: a symlinked config.json is refused and nothing is written
    def test_a_symlinked_config_is_refused_and_nothing_is_written(self):
        os.makedirs(self.home, exist_ok=True)
        real = os.path.join(self.tmp, "elsewhere.json")
        with open(real, "w") as f:
            f.write('{"a": 1}\n')
        os.symlink(real, self.path)
        rc, _, err = self.call(add=["80"])
        self.assertNotEqual(rc, 0)
        self.assertTrue(err.strip())
        self.assertEqual(open(real).read(), '{"a": 1}\n')
        self.assertTrue(os.path.islink(self.path))

    # 5. LOW: setup() and the setter take the same config lock and do not lose each other's keys
    def spy_lock(self):
        real, taken = sl.home_lock, []

        def spy(name, strict=False):
            taken.append((name, strict))
            return real(name, strict=strict)
        return spy, taken

    def setup_args(self):
        hub = os.path.join(self.tmp, "hub")
        return dict(accounts={"octocat": "12345+octocat@users.noreply.github.com"}, default_account="octocat", hub=hub,
                    projects_root=self.tmp, codex="off")

    def test_setup_reads_and_writes_the_config_under_the_strict_config_lock(self):
        spy, taken = self.spy_lock()
        with mock.patch.object(sl, "home_lock", spy):
            sl.setup(**self.setup_args())
            self.assertIn(("config", True), taken)
            del taken[:]
            sl.set_proc_protect_ports([80], [])
            self.assertIn(("config", True), taken)

    def test_the_setter_and_setup_do_not_lose_each_others_keys(self):
        sl.setup(**self.setup_args())
        sl.set_proc_protect_ports([8123], [])
        sl.setup(force=True, **dict(self.setup_args(), codex="on"))
        cfg = self.cfg()
        self.assertEqual(cfg["housekeeping"]["proc_protect_ports"], [8123])
        self.assertEqual(cfg["codex"], "on")
        sl.set_proc_protect_ports([9000], [])
        cfg = self.cfg()
        self.assertEqual(cfg["housekeeping"]["proc_protect_ports"], [8123, 9000])
        self.assertEqual((cfg["codex"], cfg["default_github_account"]), ("on", "octocat"))


if __name__ == "__main__":
    unittest.main()
