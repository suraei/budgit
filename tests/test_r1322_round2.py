"""R-1322 fix round 2 (security round 2: three lows). A fake table, fake ps/lsof and a fake kill: nothing here touches a real process."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from helpers import BudgitTestCase
import housekeeping
import leak
import procsweep
import studiolib as sl

H = 3600
LAUNCHER = "/opt/x/secretproj-launcher serve --demo --port 8123"


def old_row(pid, ppid, command, owner="sess1"):
    return {"pid": pid, "ppid": ppid, "start": time.time() - 10 * H, "tty": "??", "command": command, "owner": owner}


def probe_of(info):
    def probe(pids):
        return {p: {"cwd": info[p][0], "ports": info[p][1]} for p in pids if p in info}
    return probe


class L5JsonHidesNames(BudgitTestCase):
    def dump(self, names):
        out = io.StringIO()
        side = {"side_effect": ValueError("unreadable")} if names is None else {"return_value": names}
        with mock.patch.object(procsweep, "read_table", return_value=[old_row(100, 1, LAUNCHER)]), \
                mock.patch.object(procsweep, "probe", probe_of({100: ("/work/secretproj/app", [8123])})), \
                mock.patch.object(procsweep, "live_folders", return_value=[]), \
                mock.patch.object(housekeeping, "worktree_roots", return_value=[]), \
                mock.patch.object(leak, "protected_names", **side), contextlib.redirect_stdout(out):
            procsweep.main(as_json=True)
        return out.getvalue()

    def test_l5_the_json_goes_through_leak_hide_command_field_included(self):
        text = self.dump({"secretproj"})
        self.assertNotIn("secretproj", text.lower())
        self.assertEqual(json.loads(text)["stop"][0]["pid"], 100)

    def test_l5_unreadable_protected_names_drop_the_command_field_and_the_folder(self):
        text = self.dump(None)
        self.assertNotIn("secretproj", text.lower())
        rep = json.loads(text)
        for c in rep["stop"] + rep["kept"]:
            self.assertNotIn("command", c)
        self.assertIsNone(rep["stop"][0]["cwd"])


class L6ProtectedWorktrees(unittest.TestCase):
    def test_l6_a_linked_worktree_inside_a_protected_folder_is_not_a_root(self):
        wts = [{"path": "/proj/main"}, {"path": "/wt/one"}, {"path": "/prot/area/wt"}, {"path": "/wt/two"}]
        guard = lambda path, config=None: os.path.realpath(path).startswith("/prot")
        with mock.patch.object(housekeeping, "projects", return_value=(["/proj/main"], 0)), \
                mock.patch.object(housekeeping, "worktrees", return_value=wts), \
                mock.patch.object(sl, "protected_dir", side_effect=guard):
            self.assertEqual(housekeeping.worktree_roots({}), ["/wt/one", "/wt/two"])


class L7BlockedCount(unittest.TestCase):
    def test_l7_a_blocked_plan_counts_as_none_not_zero(self):
        with mock.patch.object(procsweep, "run", return_value={"stop": [], "kept": [], "blocked": "config unreadable"}):
            self.assertIsNone(procsweep.count())
        with mock.patch.object(procsweep, "run", return_value={"stop": [], "kept": [], "blocked": None}):
            self.assertEqual(procsweep.count(), 0)

    def test_l7_the_daily_shows_none_as_not_checked_never_as_zero(self):
        import daily
        out = {"projects": [{"id": "p", "protected": False, "counts": {"made": 1, "shut": 1, "pending": 0, "next": 0, "later": 0},
                             "rule": False, "batches": [], "duplicates": [], "order": [], "clean": {"remove": 2, "delete": 1}}],
               "no_project": False, "_names": frozenset(), "procs": None}
        text = daily.render(out, "en")
        self.assertIn("stale test servers: not checked", text)
        self.assertNotIn("stale test servers: 0", text)


class InsideRoots(BudgitTestCase):
    def test_a_sibling_with_the_same_prefix_is_not_inside_the_root(self):
        self.assertFalse(procsweep._inside("/x/wt-other", "/x/wt"))
        self.assertFalse(procsweep._inside("/x/wt-other/sub", "/x/wt"))
        self.assertTrue(procsweep._inside("/x/wt", "/x/wt"))
        self.assertTrue(procsweep._inside("/x/wt/sub", "/x/wt/"))

    def plan(self, cwd, roots):
        r = {"pid": 100, "ppid": 1, "start": 0.0, "tty": "??", "command": "python3 -m http.server 8000", "owner": None}
        return procsweep.plan([r], 10 * H, probe_of({100: (cwd, [8000])}), [], {}, holds=(), me=999999, roots=roots)

    def test_a_symlinked_cwd_resolves_to_its_real_path_before_the_root_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.realpath(tmp)
            root, other = os.path.join(real, "wt"), os.path.join(real, "wt-other")
            os.makedirs(os.path.join(root, "app"))
            os.makedirs(other)
            link_in = os.path.join(real, "link-in")
            link_out = os.path.join(real, "link-out")
            os.symlink(os.path.join(root, "app"), link_in)
            os.symlink(other, link_out)
            self.assertEqual([c["pid"] for c in self.plan(link_in, [root])["stop"]], [100])
            self.assertEqual(self.plan(link_out, [root])["stop"], [])
            self.assertEqual(self.plan(other, [root])["stop"], [])


if __name__ == "__main__":
    unittest.main()
