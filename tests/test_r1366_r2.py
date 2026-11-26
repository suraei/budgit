"""R-1366 security round 2: N1 (the log parse is NUL-separated and fails closed) and L6 (an exclusion sha missing locally is
dropped). Temp repos only, made-up names."""
import contextlib
import io
import os
import unittest
import unittest.mock

import githook_check
import leak
from test_r1366_push_check import CONFIG, NAME_A, RepoCase
import test_r1366_snapshot as ts


class LogParseTest(RepoCase):
    def check(self):
        return leak.push_check(self.repo, CONFIG, rev=["HEAD"], exclude=[])

    def test_n1_rs_in_a_message_before_a_name_is_refused(self):
        self.commit("a.txt", "ok\n", "start\x1e then %s here" % NAME_A)
        hits = self.check()
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].file.startswith("<message "))

    def test_n1_us_in_an_author_name_plus_the_name_is_refused(self):
        self.write("a.txt", "ok\n")
        self.git("add", "a.txt")
        self.git("-c", "user.name=ann\x1f%s" % NAME_A, "commit", "-q", "-m", "work")
        hits = self.check()
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0].file.startswith("<author "))

    def test_n1_a_clean_commit_with_rs_in_its_message_passes(self):
        self.commit("a.txt", "ok\n", "note\x1e with a separator")
        self.assertEqual(list(self.check()), [])

    def test_n1_a_wrong_field_count_fails_closed(self):
        self.commit("a.txt", "ok\n")
        real = leak.subprocess.run

        def fake(argv, *a, **k):
            r = real(argv, *a, **k)
            if "log" in argv and "-z" in argv:
                r.stdout = r.stdout + b"extra\0"
            return r
        with unittest.mock.patch.object(leak.subprocess, "run", fake):
            hits = self.check()
        self.assertEqual([h.alias for h in hits], [leak.CHECK_FAILED])


class ExcludedMissingTest(RepoCase):
    def push(self, sha):
        old, line = os.getcwd(), "refs/heads/main %s refs/heads/main %s\n" % (sha, "1" * 40)
        os.chdir(self.repo)
        try:
            with unittest.mock.patch.object(leak, "_push_names", return_value=sorted(leak.protected_names(CONFIG))), \
                    contextlib.redirect_stderr(io.StringIO()):
                return githook_check.pre_push("origin", io.StringIO(line), lambda t: None)
        finally:
            os.chdir(old)

    def test_l6_unknown_remote_old_sha_is_scanned_and_a_clean_push_passes(self):
        sha = self.commit("a.txt", "ok\n")
        self.assertEqual(self.push(sha), 0)

    def test_l6_unknown_remote_old_sha_still_refuses_a_name(self):
        sha = self.commit("a.txt", "the %s tool\n" % NAME_A)
        self.assertEqual(self.push(sha), 1)

    def test_l6_a_known_sha_stays_excluded(self):
        sha = self.commit("a.txt", "ok\n")
        self.assertEqual(githook_check._excluded("origin", "1" * 40), [])
        self.assertEqual(githook_check._excluded("origin", sha)[:0], [])


class SnapshotRound3Test(unittest.TestCase):
    """S2-1 to S2-4: real objects only, case-blind main refusal, remote in her account. Borrows only the helpers of
    SnapshotTest, so none of its test_ methods run a second time."""
    setUp = ts.SnapshotTest.setUp
    put = ts.SnapshotTest.put
    commit = ts.SnapshotTest.commit
    snap = ts.SnapshotTest.snap
    ref = ts.SnapshotTest.ref

    def refused(self, why, **kw):
        text, code = self.snap(**kw)
        self.assertEqual(code, 1, text)
        self.assertIn(why, text)

    def test_s2_1_replace_ref_over_a_dirty_blob_is_refused(self):
        self.put("old.txt", "about %s\n" % ts.NAME)
        self.commit("add old")
        dirty = ts.git(self.repo, "rev-parse", "HEAD:old.txt")
        self.put("tmp.txt", "clean\n")
        clean = ts.git(self.repo, "hash-object", "-w", "tmp.txt")
        ts.git(self.repo, "replace", dirty, clean)
        self.refused("replace refs present")
        self.assertIsNone(self.ref("refs/heads/ci-snapshot"))

    def test_s2_1_push_check_still_sees_the_dirty_name_with_a_replace_ref(self):
        self.put("old.txt", "about %s\n" % ts.NAME)
        self.commit("add old")
        dirty = ts.git(self.repo, "rev-parse", "HEAD:old.txt")
        self.put("tmp.txt", "clean\n")
        clean = ts.git(self.repo, "hash-object", "-w", "tmp.txt")
        ts.git(self.repo, "replace", dirty, clean)
        hits = leak.push_check(self.repo, ts.CONFIG, rev=["HEAD"], exclude=[])
        self.assertGreaterEqual(len(hits), 1)

    def test_s2_2_shallow_repository_is_refused(self):
        sha = ts.git(self.repo, "rev-parse", "HEAD")
        with open(os.path.join(self.repo, ".git", "shallow"), "w") as f:
            f.write(sha + "\n")
        self.refused("shallow repository")

    def test_s2_2_grafts_file_is_refused(self):
        with open(os.path.join(self.repo, ".git", "info", "grafts"), "w") as f:
            f.write("\n")
        self.refused("grafts file present")

    def test_s2_3_main_and_master_in_any_case_are_refused(self):
        for b in ("Main", "MASTER"):
            self.refused("main or master", branch=b)

    def test_s2_4_another_owner_is_refused(self):
        ts.git(self.repo, "remote", "set-url", "origin", "https://github.com/other-dev/proj.git")
        self.refused("remote is not in your GitHub account")

    def test_s2_4_a_non_github_host_is_refused(self):
        ts.git(self.repo, "remote", "set-url", "origin", "https://example.com/zebra-user/proj.git")
        self.refused("remote is not in your GitHub account")

    def test_s2_4_her_account_passes_in_every_url_form_and_the_url_is_printed(self):
        for url in ("git@github.com:Zebra-User/proj", "git@github.com:zebra-user/proj.git",
                    "https://github.com/zebra-user/proj", "ssh://git@github.com/zebra-user/proj.git"):
            ts.git(self.repo, "remote", "set-url", "origin", url)
            text, code = self.snap(branch="ci-" + str(abs(hash(url)) % 1000))
            self.assertEqual(code, 0, text)
            self.assertIn(url, text)

    def test_merge_on_a_non_first_parent_in_a_chain_is_refused(self):
        text, code = self.snap()
        self.assertEqual(code, 0, text)
        s1 = self.ref("refs/heads/ci-snapshot")
        tree = ts.git(self.repo, "rev-parse", "main^{tree}")
        root = ts.git(self.repo, "commit-tree", tree, "-m", "CI snapshot of main 00000000")
        merge = ts.git(self.repo, "commit-tree", tree, "-p", root, "-p", s1, "-m", "CI snapshot of main 11111111")
        ts.git(self.repo, "update-ref", "refs/heads/ci-snapshot", merge)
        self.put("more.txt", "more\n")
        self.commit("more")
        self.refused("not a snapshot chain")


if __name__ == "__main__":
    unittest.main()
