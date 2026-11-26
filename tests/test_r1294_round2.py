"""R-1294 security fix round 2: a program word built by a variable or a glob asks when the line holds status or diff, a link at a
user or system config path is followed once to its real file (a link inside the repo metadata still declines), and the config
scan is cached by path, size and mtime.

The program's name is written literally in command lines only where the guard must see it."""
import os
import unittest
from unittest import mock

import i18n
import studiolib as sl
import test_r1294_read_exception as base

UNREADABLE = "guard.r1294_config_unreadable"


class Round2(base.Case):
    def line(self, text):
        return sl.real_data_check("Bash", {"command": text}, self.project, cwd=self.project)[0]

    def asks(self, text):
        got = self.line(text)
        self.assertIsNotNone(got, text)
        self.assertEqual(got[0], "ask", text)

    def test_p1_a_program_word_built_by_a_variable_asks_before_status_or_diff(self):
        for text in ("a=gi; b=t; $a$b -C %s status" % self.root, "a=gi; b=t; ${a}${b} -C %s diff" % self.root,
                     "p=/usr/bin/gi; ${p}t -C %s status --ignore-submodules=all" % self.root,
                     "a=gi; b=t; $a$b status"):
            self.asks(text)

    def test_p1_a_glob_path_to_the_program_asks_before_status_or_diff(self):
        for text in ("/usr/bin/gi? -C %s status" % self.root, "/usr/bin/g*t -C %s diff" % self.root,
                     "/usr/bin/[g]it -C %s status --ignore-submodules=all" % self.root):
            self.asks(text)

    def test_p1_the_helper_names_an_unplaced_program_word(self):
        self.assertTrue(sl._holds_worktree_read("a=gi; $a''t status"))
        self.assertFalse(sl._holds_worktree_read("ls *.py"))
        self.assertFalse(sl._holds_worktree_read("echo $HOME"))
        self.assertFalse(sl._holds_worktree_read("ls docs"))

    def test_p1_a_line_with_no_status_or_diff_keeps_its_verdict(self):
        self.assertIsNone(self.line("ls %s" % os.path.join(self.root, "docs")))
        self.assertIsNone(self.line("echo $HOME"))

    def test_p2_a_link_at_the_user_config_follows_to_a_clean_file(self):
        target = os.path.join(self.tmp, "dotfiles", "gitconfig")
        os.makedirs(os.path.dirname(target))
        open(target, "w").write("[core]\n\tabbrev = 7\n")
        os.symlink(target, os.path.join(self.fake_home, ".gitconfig"))
        self.allowed("log")

    def test_p2_a_link_at_the_user_config_whose_target_is_unsafe_declines(self):
        target = os.path.join(self.tmp, "dotfiles", "gitconfig")
        os.makedirs(os.path.dirname(target))
        open(target, "w").write("[core]\n\tfsmonitor = /bin/true\n")
        os.symlink(target, os.path.join(self.fake_home, ".gitconfig"))
        self.declined("log")

    def test_p2_a_link_at_the_user_config_to_a_fifo_declines_without_blocking(self):
        target = os.path.join(self.tmp, "fifo-target")
        os.mkfifo(target)
        os.symlink(target, os.path.join(self.fake_home, ".gitconfig"))
        got, keys = base_prompt(self, lambda: self.check("log"))
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "ask")

    def test_p2_a_link_at_a_system_config_follows_to_a_clean_file(self):
        target = os.path.join(self.tmp, "sys", "real-gitconfig")
        open(target, "w").write("[core]\n\tabbrev = 7\n")
        os.symlink(target, self.sys_cfg)
        self.allowed("log")

    def test_p2_a_link_inside_the_repo_metadata_still_declines(self):
        target = os.path.join(self.tmp, "elsewhere-config")
        open(target, "w").write("[core]\n\tabbrev = 7\n")
        os.symlink(target, os.path.join(self.root, ".git", "config.worktree"))
        self.declined("log", key=UNREADABLE)

    def test_p3_the_scan_is_cached_by_path_size_and_mtime(self):
        sl._CONFIG_SCAN_CACHE.clear()
        real = sl._read_config_file
        with mock.patch.object(sl, "_read_config_file", side_effect=real) as spy:
            self.check("log")
            first = spy.call_count
            self.check("log")
            self.assertEqual(spy.call_count, first, "the second judge re-read the files")
            self.assertGreater(first, 0)
            with open(os.path.join(self.fake_home, ".gitconfig"), "w") as fh:     # a changed file is read again
                fh.write("[core]\n\tfsmonitor = /bin/true\n")
            got, keys = self.check("log")
            self.assertGreater(spy.call_count, first)
        self.assertEqual(got[0], "ask")

    def test_p3_a_cached_decline_keeps_its_named_reason(self):
        sl._CONFIG_SCAN_CACHE.clear()
        pad = "# " + "x" * 98 + "\n"
        with open(os.path.join(self.root, ".git", "config"), "a") as fh:
            fh.write(pad * (262144 // len(pad) + 5))
        self.declined("log", key="guard.r1294_config_big")
        self.declined("log", key="guard.r1294_config_big")


def base_prompt(case, call):
    import test_r1294_round1 as r1
    return r1.prompt(case, call)


if __name__ == "__main__":
    unittest.main()
