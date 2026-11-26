"""R-1322 fix round 3 (verifier FAIL 12 of 13): an unreadable lsof blocks the plan with an empty protect list; worktree roots fail
closed on an unresolvable protected entry and skip a private project's folder. A fake table, fake lsof, fake kill."""
import os
import unittest
from unittest import mock

import housekeeping
import procsweep
import studiolib as sl

NOW = 1_800_000_000.0
H = 3600
DEMO = "python3 /plugin/bin/budgit serve --demo --port 8123"


def marked_orphan():
    return {"pid": 500, "ppid": 1, "start": NOW - 10 * H, "tty": "??", "command": DEMO, "owner": "sess1"}


class F1LsofUnreadableBlocks(unittest.TestCase):
    def planned(self, cwd_answer, listen_answer):
        answers = iter([cwd_answer, listen_answer])
        with mock.patch.object(procsweep, "_lsof", side_effect=lambda args: next(answers)):
            probe = procsweep.probe
            return procsweep.plan([marked_orphan()], NOW, probe, [], {}, holds=(), me=999999, roots=[])

    def test_f1_both_lsof_lookups_unreadable_block_the_plan_with_an_empty_protect_list(self):
        r = self.planned(None, None)
        self.assertEqual(r["stop"], [])
        self.assertIn("lsof could not be read", r["blocked"])
        self.assertIn("lsof could not be read", r["kept"][0]["reason"])

    def test_f1_either_lookup_alone_blocks_too(self):
        for cwd, listen in ((None, ""), ("p500\nn/work\n", None)):
            r = self.planned(cwd, listen)
            self.assertEqual(r["stop"], [], (cwd, listen))
            self.assertIn("lsof could not be read", r["blocked"])

    def test_f1_both_lookups_readable_still_stop_the_orphan(self):
        r = self.planned("p500\nn/work/demo\n", "")
        self.assertEqual([c["pid"] for c in r["stop"]], [500])
        self.assertIsNone(r["blocked"])

    def test_f1_run_signals_nothing_and_count_is_none(self):
        answers = iter([None, None] * 4)
        kill = mock.Mock()
        with mock.patch.object(procsweep, "_lsof", side_effect=lambda args: next(answers)):
            rep = procsweep.run(True, reader=lambda: [marked_orphan()], live=[], config={}, now=NOW, kill=kill, sleep=lambda s: None,
                                holds=[], me=999999, roots=[])
        kill.assert_not_called()
        self.assertEqual(rep["stopped"], [])
        self.assertIn("lsof could not be read", procsweep.render(rep, "en"))
        with mock.patch.object(procsweep, "run", return_value=rep):
            self.assertIsNone(procsweep.count())


class F2F3WorktreeRoots(unittest.TestCase):
    WTS = [{"path": "/proj/main"}, {"path": "/wt/one"}, {"path": "/priv/proj/wt"}, {"path": "/wt/two"}]

    def roots(self, readable=True, private=("/priv/proj",)):
        def projects(config=None, private_out=None):
            if private_out is not None:
                private_out.extend(private)
            return (["/proj/main"], 0)
        with mock.patch.object(housekeeping, "projects", side_effect=projects), \
                mock.patch.object(housekeeping, "worktrees", return_value=self.WTS), \
                mock.patch.object(sl, "protected_dirs_readable", return_value=readable):
            return housekeeping.worktree_roots({})

    def test_f2_an_unresolvable_protected_entry_means_no_worktree_roots(self):
        self.assertIsNone(self.roots(readable=False))

    def test_f3_a_worktree_inside_a_private_project_folder_is_not_a_root(self):
        self.assertEqual(self.roots(), ["/wt/one", "/wt/two"])
        self.assertEqual(self.roots(private=()), ["/wt/one", "/priv/proj/wt", "/wt/two"])
        self.assertEqual(self.roots(private=("/priv/pro",)), ["/wt/one", "/priv/proj/wt", "/wt/two"])      # a prefix is not inside


if __name__ == "__main__":
    unittest.main()
