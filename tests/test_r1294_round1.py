"""R-1294 security fix round 1: a mixed line keeps the needs-ignore ask, a quoted program word is read by its lexed form, a config
path that is a FIFO or a link declines without blocking, and a fault in the reader asks for a work-tree status or diff.

The program's name is built from chr() pieces; command lines write it literally only where the guard must see it."""
import os
import threading
import unittest
from unittest import mock

import i18n
import studiolib as sl
import test_r1294_read_exception as base

NEEDS = base.NEEDS
GENERIC = "budgit guard: this command could not be read safely, and the project has protected real data."


def prompt(case, call, seconds=10):
    """Run `call` in a daemon thread; a call that blocks fails the test after `seconds` instead of hanging the run."""
    box = []
    worker = threading.Thread(target=lambda: box.append(call()), daemon=True)
    worker.start()
    worker.join(seconds)
    case.assertFalse(worker.is_alive(), "the call blocked for %s s" % seconds)
    return box[0]


class Round1(base.Case):
    def line(self, text):
        return sl.real_data_check("Bash", {"command": text}, self.project, cwd=self.project)[0]

    def test_r1_a_pathspec_read_then_a_flagless_status_asks_with_the_needs_ignore_text(self):
        for text in ("git log -- data/real ; git status", "git log -- data/real && git diff", "git log -- data.json ; git status",
                     "git -C %s log -- data/real ; git status" % self.root,
                     "git -C %s log -- data/real ; git -C %s status" % (self.root, self.root),
                     "git -C %s log -- data/real && git -C %s status" % (self.root, self.root),
                     "git -C %s log -- data/real ; git -C %s diff" % (self.root, self.root)):
            got = self.line(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got[0], "ask", text)
            self.assertTrue(got[1].startswith(i18n.t(NEEDS)), "%s -> %r" % (text, got[1]))

    def test_r1_the_same_mixed_line_with_the_flag_keeps_passing(self):
        text = "git -C %s log -- data/real ; git -C %s status --ignore-submodules=all" % (self.root, self.root)
        self.assertIsNone(self.line(text))

    def test_r2_a_quoted_program_word_before_status_asks(self):
        for text in ("g''it -C %s status" % self.root, "g\"\"it -C %s diff" % self.root,
                     "g''it -C %s sta''tus" % self.root):
            got = self.line(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got[0], "ask", text)

    def test_r2_the_helper_sees_a_quoted_program_word(self):
        self.assertIsNotNone(sl._flagless_read_ask("g''it -C %s status" % self.root))
        self.assertIsNone(sl._flagless_read_ask("echo status"))

    def test_r3_a_fifo_at_a_config_path_declines_promptly(self):
        fifo = os.path.join(self.fake_home, ".gitconfig")
        os.mkfifo(fifo)
        got, keys = prompt(self, lambda: self.check("log"))
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "ask")
        self.assertTrue(got[1].startswith(i18n.t("guard.r1294_config_unreadable")), got[1])

    def test_r3_a_link_at_a_config_path_declines(self):
        # round 2: only the repo's own metadata files decline on a link; user and system links are followed once
        target = os.path.join(self.tmp, "real-config")
        open(target, "w").write("[core]\n\tabbrev = 7\n")
        os.symlink(target, os.path.join(self.root, ".git", "config.worktree"))
        self.declined("log", key="guard.r1294_config_unreadable")

    def test_r3_a_plain_config_file_still_reads(self):
        open(os.path.join(self.fake_home, ".gitconfig"), "w").write("[core]\n\tabbrev = 7\n")
        self.allowed("log")

    def test_r4_a_fault_in_the_core_asks_for_a_status_or_a_diff(self):
        for text in ("git -C %s status --ignore-submodules=all" % self.root, "git -C %s status" % self.root,
                     "git -C %s diff" % self.root, "g''it -C %s status" % self.root):
            with mock.patch.object(sl, "_real_data_core", side_effect=ValueError("injected")):
                got = self.line(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got[0], "ask", text)
            self.assertEqual(got[1], GENERIC, text)

    def test_r4_a_fault_in_the_core_still_lets_a_command_with_no_write_and_no_status_pass(self):
        with mock.patch.object(sl, "_real_data_core", side_effect=ValueError("injected")):
            self.assertIsNone(self.line("ls %s" % self.root))


if __name__ == "__main__":
    unittest.main()
