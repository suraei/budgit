"""R-1366 slice 2: leak.push_check, the strict names check a push must pass. Temp repos only, made-up names, config passed in
or patched: the real home and a real repo are never read."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from helpers import ROOT

import githook_check
import leak
import studiolib as sl

NAME_A, NAME_B = "zebracorp", "Mökki-Tool"
CONFIG = {"protected_dirs": ["/nonexistent-r1366/" + NAME_A, "/nonexistent-r1366/" + NAME_B]}


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.env = dict(os.environ, HOME=self.tmp.name, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        r = subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                            "-c", "commit.gpgsign=false"] + list(args), capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as f:
            f.write(text)

    def commit(self, rel, text, message="work"):
        self.write(rel, text)
        self.git("add", rel)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


class PushCheckTest(RepoCase):
    def test_a_clean_repo_passes(self):
        self.commit("a.txt", "nothing here\n")
        hits = leak.push_check(self.repo, CONFIG)
        self.assertEqual(list(hits), [])
        self.assertEqual(hits.files, 1)

    def test_a_name_in_a_tracked_file_is_refused_and_only_the_alias_is_shown(self):
        self.commit("a.txt", "ok\nthe %s tool\n" % NAME_A)
        hits = leak.push_check(self.repo, CONFIG)
        self.assertEqual([(h.file, h.line, h.alias) for h in hits], [("a.txt", 2, "protected-2")])
        text, code = leak.names_check_cli(self.repo, config=CONFIG)
        self.assertEqual(code, 1)
        self.assertIn("names-check: REFUSED: 1 hits", text)
        self.assertIn("a.txt:2 protected-2", text)
        self.assertNotIn(NAME_A, text.lower())

    def test_a_name_in_a_tracked_file_name_is_refused(self):
        self.commit("%s-notes.txt" % NAME_A, "ok\n")
        hits = leak.push_check(self.repo, CONFIG)
        self.assertEqual(len(hits), 1)
        self.assertNotIn(NAME_A, repr(list(hits)).lower())

    def test_a_name_in_an_untracked_file_is_ignored(self):
        self.commit("a.txt", "ok\n")
        self.write("loose.txt", NAME_B)
        self.assertEqual(list(leak.push_check(self.repo, CONFIG)), [])

    def test_a_substring_is_refused_because_this_check_is_strict(self):
        self.commit("a.txt", "see %sx here\n" % NAME_A)
        self.assertEqual(len(leak.push_check(self.repo, CONFIG)), 1)

    def test_a_name_in_a_message_after_base_is_refused(self):
        base = self.commit("a.txt", "ok\n")
        self.commit("b.txt", "ok\n", "fix for %s" % NAME_A)
        hits = leak.push_check(self.repo, CONFIG, base=base)
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0].line, hits[0].alias), (0, "protected-2"))
        self.assertTrue(hits[0].file.startswith("<message "))
        text, code = leak.names_check_cli(self.repo, base=base, config=CONFIG)
        self.assertEqual(code, 1)
        self.assertNotIn(NAME_A, text.lower())

    def test_a_name_only_in_history_before_base_passes(self):
        self.commit("a.txt", "ok\n", "old note about %s" % NAME_A)
        base = self.commit("b.txt", "ok\n", "clean")
        self.commit("c.txt", "ok\n", "also clean")
        self.assertEqual(list(leak.push_check(self.repo, CONFIG, base=base)), [])

    def test_an_unreadable_base_is_refused(self):
        self.commit("a.txt", "ok\n")
        self.assertEqual(len(leak.push_check(self.repo, CONFIG, base="no-such-ref")), 1)


class FailClosedTest(RepoCase):
    def refused(self, hits):
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].alias, "check-failed")

    def test_a_protected_dirs_that_is_not_a_list_is_refused(self):
        self.commit("a.txt", "ok\n")
        self.refused(leak.push_check(self.repo, {"protected_dirs": "oops"}))

    def test_a_protected_list_that_gives_no_name_is_refused(self):
        self.commit("a.txt", "ok\n")
        self.refused(leak.push_check(self.repo, {"protected_dirs": ["/nonexistent-r1366/ab"]}))

    def test_a_config_file_that_cannot_be_read_is_refused(self):
        self.commit("a.txt", "ok\n")
        with unittest.mock.patch.object(sl, "config_readable", return_value=False):
            self.refused(leak.push_check(self.repo))

    def test_a_config_load_that_raises_is_refused(self):
        self.commit("a.txt", "ok\n")
        with unittest.mock.patch.object(sl, "load_config", side_effect=RuntimeError(NAME_A)):
            hits = leak.push_check(self.repo)
        self.refused(hits)
        self.assertNotIn(NAME_A, repr(list(hits)).lower())

    def test_no_protected_dirs_at_all_is_clean(self):
        self.commit("a.txt", "ok\n")
        self.assertEqual(list(leak.push_check(self.repo, {"protected_dirs": []})), [])

    def test_a_folder_that_is_not_a_repo_is_refused(self):
        self.refused(leak.push_check(self.tmp.name, CONFIG))


class CliTest(RepoCase):
    def test_exit_codes_and_the_clean_line(self):
        self.commit("a.txt", "ok\n")
        text, code = leak.names_check_cli(self.repo, config=CONFIG)
        self.assertEqual((text, code), ("names-check: clean (1 files)", 0))
        self.commit("b.txt", NAME_B + "\n")
        text, code = leak.names_check_cli(self.repo, config=CONFIG)
        self.assertEqual(code, 1)
        self.assertIn("b.txt:1 protected-1", text)
        self.assertNotIn("kki", text.lower())

    def test_a_failed_check_exits_1_without_a_name(self):
        text, code = leak.names_check_cli(self.repo, config={"protected_dirs": "oops"})
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", text)


class PrePushTest(RepoCase):
    """The git pre-push hook (githook_check.pre_push) is the push path Budgit installs: a hit stops the push."""

    def run_hook(self, sha, config):
        stdin = io.StringIO("refs/heads/main %s refs/heads/main %s\n" % (sha, "0" * 40))
        err = io.StringIO()
        old = os.getcwd()
        os.chdir(self.repo)
        try:
            with unittest.mock.patch.object(sl, "load_config", return_value=config), \
                    unittest.mock.patch.object(sys, "stderr", err):
                code = githook_check.pre_push("origin", stdin, lambda text: None)
        finally:
            os.chdir(old)
        return code, err.getvalue()

    def test_a_push_carrying_a_name_in_a_file_is_refused(self):
        sha = self.commit("a.txt", "%s\n" % NAME_A)
        code, err = self.run_hook(sha, CONFIG)
        self.assertEqual(code, 1)
        self.assertIn("names-check", err)
        self.assertNotIn(NAME_A, err.lower())

    def test_a_push_carrying_a_name_in_a_message_is_refused(self):
        sha = self.commit("a.txt", "ok\n", "about %s" % NAME_B)
        code, err = self.run_hook(sha, CONFIG)
        self.assertEqual(code, 1)
        self.assertNotIn("kki", err.lower())

    def test_a_clean_push_passes(self):
        sha = self.commit("a.txt", "ok\n")
        self.assertEqual(self.run_hook(sha, CONFIG)[0], 0)

    def test_an_unreadable_config_refuses_the_push(self):
        sha = self.commit("a.txt", "ok\n")
        code, err = self.run_hook(sha, {"protected_dirs": "oops"})
        self.assertEqual(code, 1)
        self.assertIn("names-check", err)


class SplitNameTest(RepoCase):
    """M2: a name split across a line break matches the whole text but no single line; it is still a hit."""

    def test_a_name_split_by_a_newline_is_refused(self):
        self.commit("a.txt", "zebra\ncorp\n")
        hits = leak.push_check(self.repo, CONFIG)
        self.assertEqual([(h.file, h.line, h.alias) for h in hits], [("a.txt", 1, "protected-2")])

    def test_a_name_split_by_a_form_feed_is_refused(self):
        self.commit("a.txt", "Mökki\x0cTool\n")
        hits = leak.push_check(self.repo, CONFIG)
        self.assertEqual([(h.file, h.line, h.alias) for h in hits], [("a.txt", 1, "protected-1")])


class RangeScanTest(RepoCase):
    """M1: every object in the pushed range is read, not only the tree at the tip."""

    def history(self):
        self.base = self.commit("a.txt", "ok\n")
        self.old = self.commit("b.txt", "the %s tool\n" % NAME_A, "add b")
        self.tip = self.commit("b.txt", "gone\n", "remove b text")

    def test_an_old_blob_removed_at_the_tip_is_refused_for_a_full_history(self):
        self.history()
        hits = leak.push_check(self.repo, CONFIG, rev=self.tip, exclude=[])
        self.assertEqual([(h.file, h.alias) for h in hits], [("b.txt", "protected-2")])

    def test_an_old_blob_is_refused_for_base_below_that_commit(self):
        self.history()
        text, code = leak.names_check_cli(self.repo, base=self.base, config=CONFIG)
        self.assertEqual(code, 1)
        self.assertIn("protected-2", text)
        self.assertNotIn(NAME_A, text.lower())

    def test_the_same_name_already_on_the_excluded_ref_passes(self):
        self.history()
        self.assertEqual(list(leak.push_check(self.repo, CONFIG, rev=self.tip, exclude=[self.old])), [])
        self.assertEqual(leak.names_check_cli(self.repo, base=self.old, config=CONFIG)[1], 0)

    def test_a_name_in_an_author_field_is_refused(self):
        self.commit("a.txt", "ok\n")
        self.write("b.txt", "ok\n")
        self.git("add", "b.txt")
        self.git("commit", "-q", "--author=%s <x@example.invalid>" % NAME_A, "-m", "clean")
        hits = leak.push_check(self.repo, CONFIG, exclude=[])
        self.assertEqual([h.alias for h in hits], ["protected-2"])
        self.assertNotIn(NAME_A, repr(list(hits)).lower())

    def test_a_clean_range_passes(self):
        self.commit("a.txt", "ok\n")
        self.commit("b.txt", "fine\n")
        hits = leak.push_check(self.repo, CONFIG, exclude=[])
        self.assertEqual(list(hits), [])

    def test_a_range_error_is_refused(self):
        self.commit("a.txt", "ok\n")
        hits = leak.push_check(self.repo, CONFIG, exclude=["0" * 40])
        self.assertEqual([h.alias for h in hits], ["check-failed"])


class HookRangeTest(RepoCase):
    def push(self, lsha, rsha="0" * 40, lref="refs/heads/main", rref="refs/heads/main"):
        stdin = io.StringIO("%s %s %s %s\n" % (lref, lsha, rref, rsha))
        err = io.StringIO()
        old = os.getcwd()
        os.chdir(self.repo)
        try:
            with unittest.mock.patch.object(sl, "load_config", return_value=CONFIG), \
                    unittest.mock.patch.object(sys, "stderr", err):
                code = githook_check.pre_push("origin", stdin, lambda text: None)
        finally:
            os.chdir(old)
        return code, err.getvalue()

    def test_a_new_branch_carrying_an_old_blob_is_refused(self):
        self.commit("b.txt", "the %s tool\n" % NAME_A)
        tip = self.commit("b.txt", "gone\n")
        code, err = self.push(tip)
        self.assertEqual(code, 1)
        self.assertIn("protected-2", err)
        self.assertNotIn(NAME_A, err.lower())

    def test_a_new_branch_excludes_what_the_remote_already_has(self):
        old = self.commit("b.txt", "the %s tool\n" % NAME_A)
        tip = self.commit("b.txt", "gone\n")
        self.git("update-ref", "refs/remotes/origin/main", old)
        self.assertEqual(self.push(tip)[0], 0)

    def test_an_update_excludes_the_remote_sha(self):
        old = self.commit("b.txt", "the %s tool\n" % NAME_A)
        tip = self.commit("b.txt", "gone\n")
        self.assertEqual(self.push(tip, rsha=old)[0], 0)

    def test_a_branch_named_after_a_protected_name_is_refused(self):
        tip = self.commit("a.txt", "ok\n")
        code, err = self.push(tip, lref="refs/heads/%s-fix" % NAME_A, rref="refs/heads/%s-fix" % NAME_A)
        self.assertEqual(code, 1)
        self.assertIn("protected-2", err)
        self.assertNotIn(NAME_A, err.lower())

    def test_a_push_holding_only_the_delete_of_a_protected_branch_name_is_refused(self):
        self.commit("a.txt", "ok\n")
        code, err = self.push("0" * 40, rsha="1" * 40, lref="(delete)", rref="refs/heads/%s-old" % NAME_A)
        self.assertEqual(code, 1)
        self.assertIn("protected-2", err)
        self.assertNotIn(NAME_A, err.lower())


if __name__ == "__main__":
    unittest.main()
