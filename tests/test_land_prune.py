"""R-1313: a lane's worktree goes when its branch has landed on main and the tree is clean. Temp repos built with real git worktree
commands; the argv of every git call is recorded to prove no force flag is ever used. Nothing waits on a clock."""
import errno
import os
import stat
from unittest import mock

import housekeeping
import landprune
import studiolib as sl
import test_housekeeping as th


class LandPruneCase(th.HkCase):
    def setUp(self):
        super().setUp()
        self.argv = []
        real = housekeeping.git

        def recording(path, *args):
            self.argv.append(list(args))
            return real(path, *args)
        patcher = mock.patch.object(housekeeping, "git", recording)
        patcher.start()
        self.addCleanup(patcher.stop)

    def prune(self, apply=True, cwd=None):
        return landprune.run(self.project, apply=apply, cwd=cwd or self.tmp, config=sl.load_config())

    def reasons(self, result):
        return {os.path.realpath(k["path"]): k["reason"] for k in result["kept"]}

    def assertNoForce(self):
        flat = [a for argv in self.argv for a in argv]
        for bad in ("--force", "-f", "-D", "--force-delete", "-ff"):
            self.assertNotIn(bad, flat)

    def test_landed_clean_worktree_is_removed_with_no_force_flag(self):
        path = self.lane("claude/item-1")
        result = self.prune()
        self.assertFalse(os.path.exists(path))
        self.assertNotIn("claude/item-1", self.branches())
        self.assertEqual([os.path.realpath(path)], [os.path.realpath(p) for p in result["removed"]])
        self.assertIn(["worktree", "remove", os.path.realpath(path)], self.argv)
        self.assertIn(["branch", "-d", "claude/item-1"], self.argv)
        self.assertIn(["worktree", "prune"], self.argv)
        self.assertNoForce()

    def test_dirty_worktree_is_kept(self):
        path = self.lane("claude/item-2")
        self.write_file("claude_item-2.txt", "changed\n", root=path)          # a modified tracked file
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("dirty", self.reasons(result)[os.path.realpath(path)])
        self.assertIn("claude/item-2", self.branches())

    def test_unmerged_worktree_is_kept(self):
        path = self.lane("claude/item-3", merged=False)
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("unmerged", self.reasons(result)[os.path.realpath(path)])

    def test_locked_worktree_is_kept(self):
        path = self.lane("claude/item-4")
        self.git("worktree", "lock", path)
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("locked", self.reasons(result)[os.path.realpath(path)])

    def test_worktree_the_process_runs_in_is_kept(self):
        path = self.lane("claude/item-5")
        result = self.prune(cwd=os.path.join(path, "sub"))
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("current", self.reasons(result)[os.path.realpath(path)])

    def test_branch_with_no_commit_of_its_own_is_kept(self):
        path = self.lane("claude/item-6", commit=False)                       # just started: equal to main, not landed
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("notstarted", self.reasons(result)[os.path.realpath(path)])

    def test_main_checkout_is_never_listed(self):
        result = self.prune()
        self.assertTrue(os.path.isdir(self.project))
        self.assertEqual([], result["removed"])
        self.assertEqual([], result["kept"])

    def test_untracked_verify_report_is_copied_to_main_first(self):
        path = self.lane("claude/item-7")
        self.write_file(".budgit/verify/R-7.md", "PASS 3 of 3\n", root=path)  # ignored by the repo, still evidence
        self.write_file(".studio/reports/r7.md", "report\n", root=path)
        self.write_file("NOTES.md", "root note\n", root=path)
        result = self.prune()
        self.assertFalse(os.path.exists(path))
        for rel, text in ((".budgit/verify/R-7.md", "PASS 3 of 3\n"), (".studio/reports/r7.md", "report\n"), ("NOTES.md", "root note\n")):
            with open(os.path.join(self.project, rel)) as f:
                self.assertEqual(text, f.read())
        self.assertEqual(3, len(result["copied"]))
        self.assertNoForce()

    def test_untracked_symlink_with_an_evidence_name_keeps_the_worktree_and_copies_nothing(self):
        path = self.lane("claude/item-14")
        outside = os.path.join(self.tmp, "secret.txt")
        self.write_file("secret.txt", "private\n", root=self.tmp)
        os.makedirs(os.path.join(path, ".studio", "reports"))
        os.symlink(outside, os.path.join(path, ".studio", "reports", "x.txt"))
        os.symlink(outside, os.path.join(path, "notes.md"))
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("untracked", self.reasons(result)[os.path.realpath(path)])
        self.assertFalse(os.path.lexists(os.path.join(self.project, "notes.md")))
        self.assertFalse(os.path.lexists(os.path.join(self.project, ".studio", "reports", "x.txt")))

    def test_items_select_only_lanes_whose_every_r_token_is_closed(self):
        own = self.lane("claude/r5-own")
        mixed = self.lane("claude/r5-r1313-batch")
        longer = self.lane("claude/r50-other")
        result = landprune.run(self.project, apply=True, cwd=self.tmp, config=sl.load_config(), items=["5"])
        self.assertFalse(os.path.exists(own))
        self.assertTrue(os.path.isdir(mixed))
        self.assertTrue(os.path.isdir(longer))
        self.assertEqual([], result["kept"])                    # the others are not even listed

    def kept_unreadable(self, path):
        result = landprune.run(self.project, apply=True, cwd=self.tmp)
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("config unreadable", self.reasons(result)[os.path.realpath(path)])

    def test_malformed_config_file_keeps_the_lane(self):
        path = self.lane("claude/item-15")
        with open(os.path.join(self.home, "config.json"), "w") as f:
            f.write('{"protected_dirs": [')                           # load_config swallows this and reads the defaults
        self.kept_unreadable(path)

    def test_protected_dirs_given_as_text_keeps_the_lane(self):
        path = self.lane("claude/item-16")
        self.config(protected_dirs=self.project)
        self.kept_unreadable(path)

    def test_protected_dirs_with_an_unresolvable_entry_keeps_the_lane(self):
        path = self.lane("claude/item-17")
        self.config(protected_dirs=[self.project, 5])
        self.kept_unreadable(path)

    def test_a_protected_owning_checkout_keeps_its_other_lanes_when_run_from_a_lane(self):
        started_in = self.lane("claude/item-18")
        other = self.lane("claude/item-19")
        self.config(protected_dirs=[self.project])                     # only the main checkout is listed, not the lanes
        result = landprune.run(started_in, apply=True, cwd=started_in)
        self.assertEqual("protected", result["skipped"])
        self.assertTrue(os.path.isdir(other))
        self.assertTrue(os.path.isdir(started_in))

    def test_private_project_prunes_nothing(self):
        path = self.lane("claude/item-20")
        with open(os.path.join(sl.studio_dir(self.project), "project.json"), "w") as f:
            f.write('{"private": true}')
        result = self.prune()
        self.assertEqual("private", result["skipped"])
        self.assertTrue(os.path.isdir(path))

    def test_untracked_symlinked_directory_keeps_the_worktree(self):
        path = self.lane("claude/item-21")
        os.makedirs(os.path.join(path, ".studio", "reports"))
        os.symlink(self.tmp, os.path.join(path, ".studio", "reports", "dir"))     # a link to a folder: never followed, never evidence
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("untracked", self.reasons(result)[os.path.realpath(path)])

    def test_a_link_swapped_in_after_judging_is_not_followed_by_the_copy(self):
        path = self.lane("claude/item-22")
        outside = os.path.join(self.tmp, "outside.txt")
        self.write_file("outside.txt", "private\n", root=self.tmp)
        os.symlink(outside, os.path.join(path, "swapped.md"))
        dest = os.path.join(self.project, "swapped.md")
        with self.assertRaises(OSError):
            landprune._copy_regular(os.path.join(path, "swapped.md"), dest)
        self.assertFalse(os.path.exists(dest))

    def test_lane_holding_only_a_progress_file_is_removed_and_the_file_is_archived(self):
        path = self.lane("claude/item-12")
        self.write_file(".progress-x.md", "notes\n", root=path)
        result = self.prune()
        self.assertFalse(os.path.exists(path))
        archived = os.path.join(self.project, ".studio", "archive", "lane-progress", os.path.basename(path), ".progress-x.md")
        with open(archived) as f:
            self.assertEqual("notes\n", f.read())
        self.assertEqual([], result["kept"])

    def test_progress_file_that_differs_in_the_archive_keeps_the_worktree(self):
        path = self.lane("claude/item-13")
        self.write_file(".progress-y.md", "mine\n", root=path)
        self.write_file(".studio/archive/lane-progress/%s/.progress-y.md" % os.path.basename(path), "other\n")
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("evidence-conflict", self.reasons(result)[os.path.realpath(path)])

    def test_evidence_that_differs_in_main_is_not_overwritten_and_blocks(self):
        path = self.lane("claude/item-8")
        self.write_file(".budgit/verify/R-8.md", "from the lane\n", root=path)
        self.write_file(".budgit/verify/R-8.md", "already in main\n")
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        with open(os.path.join(self.project, ".budgit/verify/R-8.md")) as f:
            self.assertEqual("already in main\n", f.read())
        self.assertEqual("evidence-conflict", self.reasons(result)[os.path.realpath(path)])

    def test_other_untracked_file_keeps_the_worktree_and_is_never_deleted(self):
        path = self.lane("claude/item-9")
        self.write_file("scratch.txt", "mine\n", root=path)
        self.write_file(".budgit/verify/R-9.md", "PASS\n", root=path)
        result = self.prune()
        self.assertEqual("untracked", self.reasons(result)[os.path.realpath(path)])
        self.assertTrue(os.path.exists(os.path.join(path, "scratch.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.project, ".budgit/verify/R-9.md")))   # nothing copied when it is skipped

    def test_protected_project_is_left_alone(self):
        path = self.lane("claude/item-10")
        self.config(protected_dirs=[self.project])
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("protected", result["skipped"])

    def test_dry_run_changes_nothing_and_says_what_would_go(self):
        path = self.lane("claude/item-11")
        self.write_file(".budgit/verify/R-11.md", "PASS\n", root=path)
        before = self.git("worktree", "list", "--porcelain").stdout
        result = self.prune(apply=False)
        self.assertEqual(before, self.git("worktree", "list", "--porcelain").stdout)
        self.assertTrue(os.path.exists(os.path.join(path, ".budgit/verify/R-11.md")))
        self.assertFalse(os.path.exists(os.path.join(self.project, ".budgit/verify/R-11.md")))
        self.assertIn("claude/item-11", self.branches())
        self.assertEqual([os.path.realpath(path)], [os.path.realpath(p) for p in result["would_remove"]])
        self.assertEqual([], result["removed"])
        for argv in self.argv:
            self.assertNotIn(argv[:2], (["worktree", "remove"], ["worktree", "prune"]))
            self.assertNotEqual("-d", argv[1] if len(argv) > 1 else None)

    def test_detached_worktree_is_kept(self):
        path = self.lane("claude/item-23")
        self.git("checkout", "-q", "--detach", cwd=path)
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("detached", self.reasons(result)[os.path.realpath(path)])
        self.assertEqual([], result["removed"])

    def test_a_fifo_in_the_lane_keeps_it_even_with_an_evidence_name(self):
        path = self.lane("claude/item-24")
        fifo = os.path.join(path, "pipe.md")
        os.mkfifo(fifo)
        result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(stat.S_ISFIFO(os.lstat(fifo).st_mode))
        self.assertEqual("untracked", self.reasons(result)[os.path.realpath(path)])
        self.assertFalse(os.path.lexists(os.path.join(self.project, "pipe.md")))

    def test_a_device_in_the_lane_keeps_it_even_with_an_evidence_name(self):
        path = self.lane("claude/item-25")
        self.write_file("dev.md", "looks like a note\n", root=path)           # a real file, reported as a character device below
        real = os.lstat
        fake = os.stat_result((stat.S_IFCHR | 0o600,) + (0,) * 9)

        def lstat(p, *a, **k):
            return fake if str(p).endswith("dev.md") else real(p, *a, **k)
        with mock.patch.object(os, "lstat", lstat):
            result = self.prune()
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("untracked", self.reasons(result)[os.path.realpath(path)])
        self.assertFalse(os.path.lexists(os.path.join(self.project, "dev.md")))

    def test_a_lane_inside_a_protected_folder_is_kept_as_protected(self):
        guarded = os.path.join(self.tmp, "guarded")
        os.makedirs(guarded)
        path = os.path.join(guarded, "wt-item-26")
        self.git("worktree", "add", "-q", "-b", "claude/item-26", path, "main")
        self.write_file("item-26.txt", "x\n", root=path)
        self.commit("work", cwd=path)
        self.git("merge", "-q", "--ff-only", "claude/item-26")
        self.config(protected_dirs=[os.path.realpath(guarded)])             # the main checkout is not inside it, only this lane
        result = self.prune()
        self.assertIsNone(result["skipped"])
        self.assertTrue(os.path.isdir(path))
        self.assertEqual("protected", self.reasons(result)[os.path.realpath(path)])

    def evidence_lane(self, name):
        path = self.lane(name)
        self.write_file(".budgit/verify/R-31.md", "PASS 1 of 1\n", root=path)
        return path, os.path.join(path, ".budgit", "verify", "R-31.md")

    def assertLaneSurvives(self, name, path, lane_file, result, why):
        self.assertTrue(os.path.isdir(path))
        with open(lane_file) as f:
            self.assertEqual("PASS 1 of 1\n", f.read())                       # the lane's own copy is untouched
        self.assertIn(name, self.branches())
        self.assertEqual([], result["removed"])
        self.assertEqual([], result["would_remove"])
        self.assertEqual(1, len(result["failed"]))
        self.assertIn(why, result["failed"][0]["why"])

    def test_a_copy_that_fails_keeps_the_lane_and_its_own_copy(self):
        path, lane_file = self.evidence_lane("claude/item-31")
        with mock.patch.object(landprune, "_copy_regular", side_effect=OSError(errno.EIO, "disk gone")):
            result = self.prune()
        self.assertLaneSurvives("claude/item-31", path, lane_file, result, "evidence not copied")
        self.assertFalse(os.path.exists(os.path.join(self.project, ".budgit", "verify", "R-31.md")))

    def test_any_error_in_the_copy_keeps_the_lane(self):
        path, lane_file = self.evidence_lane("claude/item-32")
        with mock.patch.object(landprune, "_copy_regular", side_effect=RuntimeError("boom")):
            result = self.prune()
        self.assertLaneSurvives("claude/item-32", path, lane_file, result, "evidence not copied")

    def test_a_copy_whose_bytes_differ_keeps_the_lane_and_its_own_copy(self):
        path, lane_file = self.evidence_lane("claude/item-33")

        def differing(src, dest):
            with open(dest, "wb") as f:
                f.write(b"other bytes\n")
        with mock.patch.object(landprune, "_copy_regular", differing):
            result = self.prune()
        self.assertLaneSurvives("claude/item-33", path, lane_file, result, "the copy differs")

    def test_a_missing_o_nofollow_still_copies_the_evidence(self):
        path, lane_file = self.evidence_lane("claude/item-34")
        class WithoutNoFollow:                                                # landprune sees an os module like Windows has
            def __getattr__(self, name):
                if name == "O_NOFOLLOW":
                    raise AttributeError(name)
                return getattr(os, name)
        with mock.patch.object(landprune, "os", WithoutNoFollow()):
            result = self.prune()
        self.assertFalse(os.path.exists(path))
        self.assertEqual([], result["failed"])
        with open(os.path.join(self.project, ".budgit", "verify", "R-31.md")) as f:
            self.assertEqual("PASS 1 of 1\n", f.read())
