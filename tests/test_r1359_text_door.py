"""R-1359: `--text-file PATH` on the verbs that take free text (ledger add, ledger finding add, ledger link --directive,
tm log, progress add, friction add), so words a shell guard refuses (the version-control program's name) and backticks reach
the stored text unchanged. Temp project only."""
import os
import re
import subprocess
import sys

from unittest import mock

from helpers import BudgitTestCase, ROOT, patch_env
import studiolib as sl
import textdoor

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
REFUSED = 64
GUARDED ="run git status then `echo hi` and $(date) ok"       # words and backticks a shell guard or zsh would act on


class TextDoorTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()

    def cli(self, *args, expect=0):
        r = subprocess.run([sys.executable, BUDGIT, *args, "--project", self.project], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120,
                           env=dict(os.environ, STUDIO_HOME=self.home, BUDGIT_LANG="en"))
        if expect == REFUSED:        # a refusal: the ledger verbs exit 1, tm/progress/friction exit 64; both are non-zero
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("text-file", r.stdout + r.stderr)
        else:
            self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r

    def file(self, text, name="note.txt"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def add_item(self):
        self.cli("ledger", "add", "--text", "base item", "--phase", "mvp", "--appetite", "small", "--area", "main")

    def last_line(self, name):
        with open(os.path.join(sl.studio_dir(self.project), name), encoding="utf-8") as f:
            return f.read().splitlines()[-1]

    # --- each verb stores the exact text ---
    def test_ledger_add_text_file_stores_the_exact_text(self):
        self.cli("ledger", "add", "--text-file", self.file(GUARDED + "\n"), "--phase", "mvp", "--appetite", "small", "--area", "main")
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["text"], GUARDED)

    def test_finding_add_text_file_stores_the_exact_text(self):
        self.add_item()
        text = "uses `git log` here"
        self.cli("ledger", "finding", "add", "R-1", "--sev", "LOW", "--class", "docs", "--text-file", self.file(text))
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["findings"][0]["text"], text)

    def test_link_directive_text_file_stores_the_exact_text(self):
        self.add_item()
        text = "keep `git` words out"
        self.cli("ledger", "link", "R-1", "--src", "I-1", "--text-file", self.file(text))
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["directives"][0]["text"], text)

    def test_tm_log_text_file_stores_the_exact_text(self):
        self.cli("tm", "log", "--text-file", self.file(GUARDED + "\n"))
        self.assertTrue(self.last_line(os.path.join("private", "tm", "status.md")).endswith(" " + GUARDED))

    def test_progress_add_text_file_appends_one_dated_line(self):
        self.cli("progress", "add", "--text-file", self.file(GUARDED + "\n"))
        self.assertRegex(self.last_line("progress.md"), r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2} " + re.escape(GUARDED) + "$")

    def test_friction_add_text_file_appends_one_dated_line(self):
        self.cli("friction", "add", "--text-file", self.file(GUARDED))
        self.cli("friction", "add", "--text-file", self.file("second `x`"))
        self.assertTrue(self.last_line("friction.md").endswith("second `x`"))
        with open(os.path.join(sl.studio_dir(self.project), "friction.md"), encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 2)

    # --- both flags together ---
    def test_both_flags_together_are_refused_and_write_nothing(self):
        p = self.file("x")
        self.cli("ledger", "add", "--text", "a", "--text-file", p, "--phase", "mvp", "--appetite", "small", "--area", "main", expect=64)
        self.assertEqual(sl.load_ledger(self.project)["items"], [])
        self.cli("progress", "add", "--text", "a", "--text-file", p, expect=64)
        self.cli("friction", "add", "--text", "a", "--text-file", p, expect=64)
        self.cli("tm", "log", "a", "--text-file", p, expect=64)
        self.add_item()
        self.cli("ledger", "link", "R-1", "--src", "I-1", "--directive", "a", "--text-file", p, expect=64)
        self.cli("ledger", "finding", "add", "R-1", "--sev", "LOW", "--class", "docs", "--text", "a", "--text-file", p, expect=64)

    # --- empty, missing, directory, symlink ---
    def test_an_empty_or_missing_file_or_a_directory_is_refused(self):
        for bad in (self.file("", "empty.txt"), self.file("\n\n", "blank.txt"), os.path.join(self.tmp, "nope.txt"), self.tmp):
            r = self.cli("progress", "add", "--text-file", bad, expect=64)
            self.assertIn("text-file", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(self.project), "progress.md")))

    def test_a_symlink_out_of_the_scratch_folder_and_project_is_refused(self):
        link = os.path.join(self.tmp, "link.txt")
        os.symlink("/etc/hosts", link)
        self.cli("progress", "add", "--text-file", link, expect=64)

    def test_a_symlink_inside_the_scratch_folder_is_accepted(self):
        link = os.path.join(self.tmp, "link.txt")
        os.symlink(self.file("through a link"), link)
        self.cli("progress", "add", "--text-file", link)
        self.assertTrue(self.last_line("progress.md").endswith("through a link"))

    # --- the cap ---
    def test_the_cap_refuses_one_character_over_and_takes_the_limit(self):
        self.cli("progress", "add", "--text-file", self.file("a" * textdoor.CAP))
        r = self.cli("progress", "add", "--text-file", self.file("a" * (textdoor.CAP + 1)), expect=64)
        self.assertIn(str(textdoor.CAP), r.stdout + r.stderr)

    # --- the roots rule: the whole real path must sit inside the project, its data folder or the scratch folder ---
    def test_a_plain_file_outside_the_roots_is_refused(self):
        outside = os.path.join(self.tmp, "sibling")
        os.makedirs(outside)
        path = os.path.join(outside, "secret.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("private")
        with self.assertRaises(sl.StudioError) as ctx:
            textdoor.read(path, self.project, roots=[self.project])
        self.assertIn("text-file", str(ctx.exception))
        self.assertEqual(textdoor.read(path, self.project, roots=[outside]), "private")

    def test_a_symlinked_parent_folder_outside_the_roots_is_refused(self):
        outside = os.path.join(self.tmp, "sibling")
        os.makedirs(outside)
        with open(os.path.join(outside, "secret.txt"), "w", encoding="utf-8") as f:
            f.write("private")
        os.symlink(outside, os.path.join(self.project, "linkdir"))
        with self.assertRaises(sl.StudioError) as ctx:
            textdoor.read(os.path.join(self.project, "linkdir", "secret.txt"), self.project, roots=[self.project])
        self.assertIn("text-file", str(ctx.exception))
        os.symlink("/etc", os.path.join(self.tmp, "etclink"))                 # and with the real default roots
        with self.assertRaises(sl.StudioError):
            textdoor.read(os.path.join(self.tmp, "etclink", "hosts"), self.project)

    def test_a_file_holding_only_a_dash_logs_the_dash_and_does_not_read_stdin(self):
        self.cli("tm", "log", "--text-file", self.file("-\n"))
        self.assertTrue(self.last_line(os.path.join("private", "tm", "status.md")).endswith(" -"))

    # --- review round: broad env roots, hidden folders, a FIFO, a file of another user ---
    def test_an_env_root_that_is_home_or_above_it_or_slash_is_dropped(self):
        home = os.path.join(self.tmp, "fakehome")
        os.makedirs(home)
        for broad in (home, self.tmp, "/"):
            patch_env(self, HOME=home, TMPDIR=broad, CLAUDE_CODE_TMPDIR=None)
            self.assertNotIn(os.path.realpath(broad), [os.path.realpath(r) for r in textdoor._roots(self.project)], broad)
        sub = os.path.join(self.tmp, "scratch")
        os.makedirs(sub)
        patch_env(self, HOME=home, TMPDIR=sub)                    # a folder that is not above the home stays a root
        self.assertIn(os.path.realpath(sub), [os.path.realpath(r) for r in textdoor._roots(self.project)])

    def test_a_file_under_a_dot_folder_or_private_inside_the_project_is_refused(self):
        for folder in (".git", ".ssh", "private", os.path.join(".budgit", "private")):
            os.makedirs(os.path.join(self.project, folder), exist_ok=True)
            path = os.path.join(self.project, folder, "x.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("secret")
            with self.assertRaises(sl.StudioError, msg=folder):
                textdoor.read(path, self.project)
        own = os.path.join(self.project, ".budgit", "note.txt")      # the project's own data folder stays readable
        with open(own, "w", encoding="utf-8") as f:
            f.write("ok")
        self.assertEqual(textdoor.read(own, self.project), "ok")

    def test_a_fifo_is_refused_and_does_not_hang(self):
        fifo = os.path.join(self.tmp, "pipe")
        os.mkfifo(fifo)
        self.cli("progress", "add", "--text-file", fifo, expect=REFUSED)

    def test_a_file_owned_by_another_user_is_refused(self):
        path = self.file("mine")
        self.assertEqual(textdoor.read(path, self.project), "mine")
        with mock.patch.object(textdoor.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(sl.StudioError) as ctx:
                textdoor.read(path, self.project)
        self.assertIn("text-file", str(ctx.exception))

    def test_a_file_that_is_not_utf8_is_refused(self):
        p = os.path.join(self.tmp, "bin.txt")
        with open(p, "wb") as f:
            f.write(b"\xff\xfe\x00bad")
        self.cli("progress", "add", "--text-file", p, expect=64)


if __name__ == "__main__":
    import unittest
    unittest.main()
