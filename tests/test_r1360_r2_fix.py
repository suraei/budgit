"""R-1360 security round 2: M-A (rg runs a program), M-B and L-1 (move door real path), L-2 (subagent identity)."""
import os
import tempfile
import unittest

import mergeguard
import test_r1360_read_verbs as rv


class RgRunsProgram(unittest.TestCase):
    def test_every_pre_spelling_is_refused(self):
        for cmd in ("rg --pre foo x push", "rg --pre=foo x", "rg --pre-glob '*.x' x", "rg --pre-glob=*.x x",
                    "rg --hostname-bin foo x", "rg --hostname-bin=foo x", "rg --pre git x", "rg --pre=git x"):
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(mergeguard._line_why(cmd, False, True, None))

    def test_unknown_long_option_gives_no_slot(self):
        self.assertEqual(mergeguard._grep_pattern_slots(["rg", "--bogus", "git", "x"], 0), set())
        self.assertEqual(mergeguard._grep_pattern_slots(["rg", "--pre", "foo", "git"], 0), set())

    def test_earlier_g2b_cases_still_give_slots(self):
        self.assertEqual(mergeguard._grep_pattern_slots(["rg", "-n", "git", "x"], 0), {2})
        self.assertEqual(mergeguard._grep_pattern_slots(["grep", "-rn", "git", "x"], 0), {2})
        self.assertEqual(mergeguard._grep_pattern_slots(["rg", "-e", "git", "x"], 0), {2})
        self.assertEqual(mergeguard._grep_pattern_slots(["rg", "-g", "*.py", "git", "x"], 0), {3})


class MoveDoorRealPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = os.path.realpath(self.tmp.name)
        self.live = os.path.join(base, "Live")
        os.makedirs(os.path.join(self.live, "wt"))
        os.makedirs(os.path.join(base, "out"))
        self.base = base

    def test_symlink_outside_to_inside_is_refused(self):
        link = os.path.join(self.base, "out", "link")
        os.symlink(os.path.join(self.live, "wt"), link)
        self.assertFalse(mergeguard._outside(link, self.live))

    def test_case_variant_dst_is_inside(self):
        variant = os.path.join(self.base, "LIVE")
        if not os.path.exists(variant):
            self.skipTest("case-sensitive disk")
        self.assertFalse(mergeguard._outside(os.path.join(variant, "x"), self.live))

    def test_plain_outside_path_still_passes(self):
        self.assertTrue(mergeguard._outside(os.path.join(self.base, "out", "new"), self.live))


class SubagentIdentity(unittest.TestCase):
    # hook.py passes `agent_id or agent_type` as the subagent flag; it assumes Claude Code sends one of them for a subagent call (the field
    # R-901 trusts). With only agent_type set, the move door stays closed.
    def test_move_door_closed_when_only_agent_type_is_set(self):
        self.assertIsNotNone(mergeguard._move_why(["/a/x", "/a/y"], "/live", "studio:builder"))

    def test_merge_tree_door_closed_when_only_agent_type_is_set(self):
        args = ["--write-tree", "a", "b"]
        self.assertIsNone(mergeguard._merge_tree_why(args, "", False))
        self.assertIsNotNone(mergeguard._merge_tree_why(args, "", "studio:builder"))


@unittest.skipUnless(rv.CAN_RUN, rv.SKIP)
class LinesLiteralName(rv.Base):
    # L-3: `lines` checks the tracked file by its literal name. A tracked file named with `*` must not make an untracked twin
    # that the glob would match readable. (The scratch env already sets GIT_LITERAL_PATHSPECS for this call; a `:(literal)` prefix
    # is then read as part of the name and breaks every lines read, so none is added.)
    def test_untracked_twin_of_a_tracked_glob_name_is_refused(self):
        d = self.repo({"f*.txt": "star\n"})
        rv.put(os.path.join(d, "fa.txt"), "twin\n")
        self.assertEqual(self.run_rr(d, ["lines", "f*.txt", "1", "5"]), (0, "star\n", ""))
        rc, out, err = self.run_rr(d, ["lines", "fa.txt", "1", "5"])
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("tracked", err)


if __name__ == "__main__":
    unittest.main()
