"""R-1322 follow-up: `budgit housekeeping procs --protect-port N / --unprotect-port N / --protect-listening` writes
housekeeping.proc_protect_ports of the config through the CLI's own writer (a tool write to the home folder is refused by the guard).
A scratch home everywhere; a fake lsof and a fake plan; os.kill is replaced and must never be called."""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import unittest
from unittest import mock

from helpers import BudgitTestCase, ROOT
import procsweep
import studiolib as sl

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")


class Ports(BudgitTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.home, "config.json")
        kill = mock.patch("os.kill")
        self.kill = kill.start()
        self.addCleanup(kill.stop)
        self.addCleanup(lambda: self.kill.assert_not_called())     # this feature never sends a signal

    def put(self, data, raw=False, mode=None):
        os.makedirs(self.home, exist_ok=True)
        with open(self.path, "w") as f:
            f.write(data if raw else json.dumps(data, indent=2) + "\n")
        if mode is not None:
            os.chmod(self.path, mode)

    def text(self):
        with open(self.path) as f:
            return f.read()

    def cfg(self):
        return json.loads(self.text())

    def call(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = procsweep.protect_main(**dict({"lang": "en"}, **kw))
        return rc, out.getvalue(), err.getvalue()

    def test_add_keeps_other_keys_and_their_order_and_prints_the_list(self):
        start = {"a": 1, "housekeeping": {"proc_min_age_h": 6, "proc_protect_ports": [4321]}, "z": {"k": [1, 2]}}
        self.put(start)
        rc, out, _ = self.call(add=["8123"])
        self.assertEqual(rc, 0)
        self.assertIn("4321, 8123", out)
        expected = {"a": 1, "housekeeping": {"proc_min_age_h": 6, "proc_protect_ports": [4321, 8123]}, "z": {"k": [1, 2]}}
        self.assertEqual(self.text(), json.dumps(expected, indent=2) + "\n")        # every other byte as it was, key order kept
        self.assertEqual(list(self.cfg()), ["a", "housekeeping", "z"])

    def test_dedup_and_sort(self):
        self.put({"housekeeping": {"proc_protect_ports": [4321, 80]}})
        rc, out, _ = self.call(add=["9000", "80", "9000", "0080"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.cfg()["housekeeping"]["proc_protect_ports"], [80, 4321, 9000])

    def test_remove_and_removing_an_absent_port_is_not_an_error(self):
        self.put({"housekeeping": {"proc_protect_ports": [80, 4321]}})
        self.assertEqual(self.call(remove=["4321", "9999"])[0], 0)
        self.assertEqual(self.cfg()["housekeeping"]["proc_protect_ports"], [80])
        rc, out, _ = self.call(remove=["80"])
        self.assertEqual(self.cfg()["housekeeping"]["proc_protect_ports"], [])
        self.assertEqual(rc, 0)

    def test_an_invalid_port_is_refused_and_nothing_is_written(self):
        self.put({"a": 1})
        before = self.text()
        for bad in ("0", "65536", "abc", "80/tcp", "-1", "8000-8100", "", "1.5", "²"):
            rc, _, err = self.call(add=["8123", bad])
            self.assertNotEqual(rc, 0, bad)
            self.assertEqual(self.text(), before, bad)
            rc, _, _ = self.call(remove=[bad])
            self.assertNotEqual(rc, 0, bad)
            self.assertEqual(self.text(), before, bad)

    def test_the_file_mode_is_kept_and_a_new_file_is_owner_only(self):
        self.put({"a": 1}, mode=0o640)
        self.call(add=["80"])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        os.remove(self.path)
        self.assertEqual(self.call(add=["80"])[0], 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(self.cfg(), {"housekeeping": {"proc_protect_ports": [80]}})

    def test_a_missing_housekeeping_section_is_created_after_the_other_keys(self):
        self.put({"hub": "x", "codex": "off"})
        self.assertEqual(self.call(add=["3000"])[0], 0)
        self.assertEqual(self.cfg(), {"hub": "x", "codex": "off", "housekeeping": {"proc_protect_ports": [3000]}})
        self.assertEqual(list(self.cfg()), ["hub", "codex", "housekeeping"])

    def test_an_unreadable_config_refuses_and_writes_nothing(self):
        for raw in ('{"a": 1,}', "[1, 2]", "", '{"housekeeping": 5}', '{"housekeeping": {"proc_protect_ports": ["8000/tcp"]}}',
                    '{"housekeeping": {"proc_protect_ports": 5}}'):
            self.put(raw, raw=True)
            rc, _, err = self.call(add=["80"])
            self.assertNotEqual(rc, 0, raw)
            self.assertEqual(self.text(), raw, raw)
            self.assertTrue(err.strip(), raw)

    def test_a_write_failure_says_why_and_exits_non_zero(self):
        self.put({"a": 1})
        before = self.text()
        with mock.patch.object(sl, "atomic_write", side_effect=OSError("disk full")):
            rc, out, err = self.call(add=["80"])
        self.assertNotEqual(rc, 0)
        self.assertIn("disk full", err)
        self.assertEqual(self.text(), before)

    def test_protect_listening_adds_the_ports_of_non_candidates_never_a_candidates(self):
        self.put({"a": 1})
        plan = {"stop": [{"pid": 100, "ports": [8123]}], "kept": [], "blocked": None, "stopped": [], "still_running": [], "applied": False}
        lsof = "p100\nn*:8123\np200\nn127.0.0.1:3000\np300\nn[::1]:5000\np300\nn*:5000\n"
        seen = []
        with mock.patch.object(procsweep, "run", return_value=plan), \
                mock.patch.object(procsweep, "_lsof", side_effect=lambda args: seen.append(args) or lsof):
            rc, out, _ = self.call(listening=True)
        self.assertEqual(rc, 1)                                                    # a candidate was skipped (round 2): non-zero
        self.assertEqual(self.cfg()["housekeeping"]["proc_protect_ports"], [3000, 5000])
        self.assertIn("3000, 5000", out)
        self.assertIn("-iTCP", seen[0])
        self.assertIn("-sTCP:LISTEN", seen[0])
        self.assertIn(str(os.getuid()), seen[0])                                    # this user's processes only

    def test_protect_listening_refuses_when_lsof_or_the_plan_cannot_be_trusted(self):
        self.put({"a": 1})
        before = self.text()
        plan = {"stop": [], "kept": [], "blocked": None, "stopped": [], "still_running": [], "applied": False}
        with mock.patch.object(procsweep, "run", return_value=plan), mock.patch.object(procsweep, "_lsof", return_value=None):
            self.assertNotEqual(self.call(listening=True)[0], 0)
        blocked = dict(plan, blocked="the live sessions cannot be read")
        with mock.patch.object(procsweep, "run", return_value=blocked), mock.patch.object(procsweep, "_lsof", return_value="p1\nn*:80\n"):
            self.assertNotEqual(self.call(listening=True)[0], 0)
        with mock.patch.object(procsweep, "run", return_value=dict(plan, error="holds file unreadable")), \
                mock.patch.object(procsweep, "_lsof", return_value="p1\nn*:80\n"):
            self.assertNotEqual(self.call(listening=True)[0], 0)
        self.assertEqual(self.text(), before)

    def test_combined_with_stop_or_apply_is_refused_and_nothing_runs(self):
        self.put({"a": 1})
        before = self.text()
        with mock.patch.object(procsweep, "run") as run:
            for extra in ({"stop": True}, {"apply": True}):
                rc, _, err = self.call(add=["80"], **extra)
                self.assertEqual(rc, 2)
                self.assertTrue(err.strip())
        run.assert_not_called()
        self.assertEqual(self.text(), before)

    def test_the_words_follow_the_language(self):
        self.put({"a": 1})
        self.assertIn("puertos", self.call(add=["80"], lang="es")[1])
        self.assertIn("ports", self.call(add=["81"], lang="en")[1])
        self.assertIn("80, 81", self.call(add=["81"], lang="en")[1])

    def test_the_cli_knows_the_three_flags(self):
        out = subprocess.run([sys.executable, BUDGIT, "housekeeping", "--help"], capture_output=True, text=True, timeout=120)
        for flag in ("--protect-port", "--unprotect-port", "--protect-listening"):
            self.assertIn(flag, out.stdout)


if __name__ == "__main__":
    unittest.main()
