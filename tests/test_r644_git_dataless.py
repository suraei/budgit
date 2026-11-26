"""R-644: a check stops early when iCloud has evicted files under a repo's git dir, and a read-only listing names the repos.
The eviction flag is injected: relocate.SF_DATALESS is pointed at a user-settable stat flag (UF_HIDDEN) that the test sets
on a fake pack file with os.chflags, so no test depends on real iCloud eviction and no content is ever read."""
import builtins
import io
import os
import shlex
import stat
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

import test_relocate as tr
import relocate

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_test  # noqa: E402

FLAG = getattr(stat, "UF_HIDDEN", 0)
needs_flags = unittest.skipUnless(hasattr(os, "chflags") and FLAG, "needs os.chflags")


def put(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class Fake(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(self.cleanup)
        patcher = mock.patch.object(relocate, "SF_DATALESS", FLAG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = {"protected_dirs": [], "projects_root": self.tmp}      # never the real home config
        cfg = mock.patch.object(relocate.sl, "load_config", side_effect=lambda: dict(self.config))
        cfg.start()
        self.addCleanup(cfg.stop)

    def cleanup(self):
        import shutil
        for base, _dirs, files in os.walk(self.tmp):
            for f in files:
                try:
                    os.chflags(os.path.join(base, f), 0, follow_symlinks=False)
                except (OSError, AttributeError, NotImplementedError):
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def repo(self, name, packs=3):
        path = os.path.join(self.tmp, name)
        for i in range(packs):
            put(os.path.join(path, ".git", "objects", "pack", "p%d.pack" % i))
        put(os.path.join(path, "README.md"))
        return path

    def evict(self, path):
        os.chflags(path, FLAG)

    def worktree(self, main_repo, name):
        wt = os.path.join(self.tmp, name)
        gitdir = os.path.join(main_repo, ".git", "worktrees", name)
        os.makedirs(gitdir)
        put(os.path.join(wt, ".git"), "gitdir: %s\n" % gitdir)
        return wt


@needs_flags
class GitDatalessTest(Fake):
    def test_a_worktrees_git_file_leads_to_the_common_dir(self):
        main = self.repo("main")
        wt = self.worktree(main, "wt1")
        self.assertEqual(relocate.common_git_dir(wt), os.path.join(main, ".git"))
        self.evict(os.path.join(main, ".git", "objects", "pack", "p1.pack"))
        self.assertEqual(relocate.git_dataless(wt), (1, True))

    def test_one_evicted_pack_gives_count_one_and_zero_gives_zero(self):
        main = self.repo("main")
        self.assertEqual(relocate.git_dataless(main), (0, True))
        self.evict(os.path.join(main, ".git", "objects", "pack", "p0.pack"))
        self.assertEqual(relocate.git_dataless(main), (1, True))

    def test_an_unreadable_git_file_is_unknown_not_a_crash(self):
        wt = os.path.join(self.tmp, "broken")
        put(os.path.join(wt, ".git"), "\xff not a gitdir line")
        self.assertIsNone(relocate.common_git_dir(wt))
        self.assertEqual(relocate.git_dataless(wt), (0, False))
        self.assertEqual(relocate.git_dataless(os.path.join(self.tmp, "missing")), (0, False))
        with mock.patch.object(builtins, "open", side_effect=OSError("denied")):
            self.assertEqual(relocate.git_dataless(wt), (0, False))


class HostilePointerTest(Fake):
    """Security round 1: a pointer file or a symlink must not lead the walk into a protected or private folder."""

    def vault(self):
        vault = self.repo("vault")
        self.config["protected_dirs"] = [vault]
        return vault

    def pointer(self, name, target):
        wt = os.path.join(self.tmp, name)
        put(os.path.join(wt, ".git"), "gitdir: %s\n" % target)
        return wt

    def test_a_worktree_pointer_into_a_protected_metadata_folder_is_not_checked_and_never_printed(self):
        vault = self.vault()
        wt = self.pointer("wt", os.path.join(vault, ".git", "worktrees", "x"))
        self.assertIsNone(relocate.common_git_dir(wt))
        self.assertEqual(relocate.git_dataless(wt), (0, False))
        if FLAG and hasattr(os, "chflags"):
            os.chflags(os.path.join(vault, ".git", "objects", "pack", "p1.pack"), FLAG)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = gate_test.check_dataless(wt)
        self.assertIsNone(rc)
        self.assertNotIn("vault", out.getvalue())
        self.assertNotIn("evicted 1", out.getvalue())

    def test_a_plain_pointer_to_a_protected_metadata_folder_or_project_root_is_not_checked(self):
        vault = self.vault()
        self.assertIsNone(relocate.common_git_dir(self.pointer("wt1", os.path.join(vault, ".git"))))
        self.assertIsNone(relocate.common_git_dir(self.pointer("wt2", vault)))
        self.assertIsNone(relocate.common_git_dir(self.pointer("wt3", os.path.join(vault, ".git", "objects"))))

    def test_a_pointer_to_the_root_or_to_a_folder_that_is_not_metadata_is_not_checked(self):
        self.assertIsNone(relocate.common_git_dir(self.pointer("wt1", os.sep)))
        self.assertIsNone(relocate.common_git_dir(self.pointer("wt2", os.path.join(self.tmp, "wt2"))))
        with mock.patch.object(relocate, "dataless_count", side_effect=AssertionError("walked")):
            self.assertEqual(relocate.git_dataless(self.pointer("wt3", os.sep)), (0, False))

    def test_a_metadata_folder_with_a_head_file_under_another_name_is_still_checked(self):
        bare = os.path.join(self.tmp, "store.git")
        put(os.path.join(bare, "HEAD"), "ref: refs/heads/main\n")
        self.assertEqual(relocate.common_git_dir(self.pointer("wt", bare)), bare)

    def test_a_pointer_into_a_private_folder_is_not_checked(self):
        diary = self.repo("diary")
        wt = self.pointer("wt", os.path.join(diary, ".git"))
        self.assertIsNone(relocate.common_git_dir(wt, private=("diary",), base=self.tmp))
        self.assertEqual(relocate.git_dataless(wt, private=("diary",), base=self.tmp), (0, False))

    def test_a_symlinked_metadata_folder_pointing_into_a_protected_project_is_not_checked(self):
        vault = self.vault()
        link = os.path.join(self.tmp, "linked")
        os.makedirs(link)
        os.symlink(os.path.join(vault, ".git"), os.path.join(link, ".git"))
        self.assertIsNone(relocate.common_git_dir(link))
        self.assertEqual(relocate.git_dataless(link), (0, False))


@needs_flags
class WalkLimitTest(Fake):
    def test_the_walk_stops_at_the_time_limit_and_says_incomplete(self):
        main = self.repo("main")
        self.evict(os.path.join(main, ".git", "objects", "pack", "p0.pack"))
        ticks = iter(range(0, 10 ** 6, int(relocate.WALK_SECONDS) + 1))
        with mock.patch.object(relocate, "_clock", lambda: next(ticks)):
            self.assertEqual(relocate.dataless_count(os.path.join(main, ".git")), (0, False))
        self.assertEqual(relocate.dataless_count(os.path.join(main, ".git")), (1, True))        # the real clock

    def test_an_evicted_starting_folder_counts_without_a_walk(self):
        main = self.repo("main")
        gitdir = os.path.join(main, ".git")
        self.evict(gitdir)
        self.addCleanup(os.chflags, gitdir, 0)
        with mock.patch.object(relocate.os, "scandir", side_effect=AssertionError("walked")):
            self.assertEqual(relocate.dataless_count(gitdir), (1, True))
            self.assertEqual(relocate.git_dataless(main), (1, True))


@needs_flags
class GateTest(Fake):
    def run_main(self, root, modules):
        out = io.StringIO()
        with mock.patch.object(gate_test, "prepare", return_value=list(sys.path)), \
                mock.patch.object(gate_test, "ROOT", root), \
                mock.patch.object(gate_test, "branch_files", return_value=[]), \
                mock.patch.object(gate_test.unittest, "main") as ran, redirect_stdout(out):
            ran.return_value.result.wasSuccessful.return_value = True
            rc = gate_test.main(modules)
        return rc, out.getvalue(), ran

    def test_one_evicted_pack_exits_4_with_one_line_and_no_test_run(self):
        main = self.repo("main")
        self.evict(os.path.join(main, ".git", "objects", "pack", "p2.pack"))
        rc, out, ran = self.run_main(main, ["test_x"])
        git = os.path.join(main, ".git")
        self.assertEqual(rc, 4)
        ran.assert_not_called()
        self.assertEqual(out.strip().splitlines(), [
            "gate: iCloud has evicted 1 files under %s; tests would hang. Free disk space, then run: brctl download %s" % (git, git)])

    def test_the_suggested_download_command_quotes_a_path_with_a_space_and_a_quote(self):
        main = self.repo("my repo's; touch x")
        git = os.path.join(main, ".git")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = gate_test.check_dataless(main, counter=lambda _r: (2, True))
        self.assertEqual(rc, 4)
        command = out.getvalue().strip().split("then run: ", 1)[1]
        self.assertEqual(command, "brctl download " + shlex.quote(git))
        self.assertEqual(shlex.split(command), ["brctl", "download", git])

    def test_zero_evicted_runs_normally(self):
        main = self.repo("main")
        rc, out, ran = self.run_main(main, ["test_x"])
        self.assertEqual(rc, 0)
        ran.assert_called_once()
        self.assertNotIn("evicted", out)

    def test_an_incomplete_walk_prints_a_note_and_goes_on(self):
        main = self.repo("main")
        with mock.patch.object(gate_test.relocate, "git_dataless", return_value=(0, False)):
            rc, out, ran = self.run_main(main, ["test_x"])
        self.assertEqual(rc, 0)
        ran.assert_called_once()
        self.assertIn("gate: note", out)


@needs_flags
class ListingTest(tr.World):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(relocate, "SF_DATALESS", FLAG)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pack(self, repo):
        return os.path.join(repo, ".git", "objects", "pack")

    def evict_in(self, repo):
        p = put(os.path.join(self.pack(repo), "p1.pack"))
        os.chflags(p, FLAG)
        self.addCleanup(lambda: os.path.exists(p) and os.chflags(p, 0))

    def test_the_listing_counts_per_repo_and_skips_a_private_folder(self):
        self.evict_in(self.alpha)
        self.evict_in(self.hub)
        private = os.path.join(self.old_root, "diary")
        os.makedirs(os.path.join(private, ".git"))
        self.evict_in(private)
        rows = relocate.evicted_listing(self.new_root, private=["diary"])
        text = "\n".join(relocate.render_evicted(rows))
        self.assertNotIn("diary", text)
        self.assertNotIn(private, text)
        by = {os.path.basename(p): (n, c) for p, n, c in rows if p}
        self.assertEqual(by["alpha"], (1, True))
        self.assertEqual(by["studio"], (1, True))
        self.assertNotIn(self.vault, text)                       # a protected project shows only its hash label

    def test_a_pointer_from_an_unprotected_folder_into_a_protected_project_is_not_checked_in_the_listing(self):
        self.evict_in(self.vault)
        wt = os.path.join(self.old_root, "wt")
        put(os.path.join(wt, ".git"), "gitdir: %s\n" % os.path.join(self.vault, ".git", "worktrees", "x"))
        rows = relocate.evicted_listing(self.new_root)
        text = "\n".join(relocate.render_evicted(rows))
        by = {p: (n, c) for p, n, c in rows}
        self.assertEqual(by[wt], (0, False))
        self.assertNotIn(self.vault, text)
        self.assertNotIn("evicted 1", text)

    def test_the_listing_never_reads_content(self):
        self.evict_in(self.alpha)
        real = builtins.open

        def guarded(path, *a, **k):
            p = str(path)
            if p.endswith(".pack") or os.sep + "objects" + os.sep in p or p.endswith("secret.txt"):
                raise AssertionError("read content of " + p)
            return real(path, *a, **k)
        with mock.patch.object(builtins, "open", guarded):
            rows = relocate.evicted_listing(self.new_root)
        self.assertTrue(any(n == 1 for _p, n, _c in rows))


if __name__ == "__main__":
    unittest.main()
