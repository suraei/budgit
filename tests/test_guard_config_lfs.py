"""R-335 class 7 (usability): the standard git-lfs filter section in a git config does not make every plain git read ask;
any other filter, or an lfs section that runs something else, still does."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import GuardCase, sl  # noqa: E402

LFS = '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n\tsmudge = git-lfs smudge -- %f\n\tprocess = git-lfs filter-process\n\trequired = true\n'


class LfsConfigTest(GuardCase):
    def put(self, where, text):
        path = os.path.join(self.project, ".git", "config") if where == "repo" else os.path.join(self.tmp, "fake-user-home", ".gitconfig")
        with open(path, "a") as fh:
            fh.write(text)

    def status(self):
        return self.d("git -C %s status --ignore-submodules=all" % self.root)    # R-1294: a flagless status asks for the submodule rule

    def test_the_standard_lfs_section_in_either_config_is_not_a_reason_to_ask(self):
        self.assertIsNone(self.status())
        self.put("repo", LFS)
        self.assertIsNone(self.status())
        self.put("global", LFS)
        self.assertIsNone(self.status())

    def test_an_lfs_section_that_runs_something_else_asks(self):
        self.put("repo", '[filter "lfs"]\n\tclean = sh evil.sh\n\tsmudge = git-lfs smudge -- %f\n')
        self.assertEqual(self.status()[0], "ask")

    def test_another_filter_next_to_the_lfs_one_asks(self):
        self.put("repo", LFS + '[filter "x"]\n\tclean = sh x\n')
        self.assertEqual(self.status()[0], "ask")

    def test_an_lfs_value_with_a_chained_command_asks(self):
        for bad in ("git-lfs clean -- %f; sh evil.sh", "git-lfs clean -- %f && id", "git-lfs clean `id`", "git-lfs clean $(id)"):
            self.put("repo", '[filter "lfs"]\n\tclean = %s\n' % bad)
            self.assertEqual(self.status()[0], "ask", bad)
            open(os.path.join(self.project, ".git", "config"), "w").write("[core]\n\tbare = false\n")


if __name__ == "__main__":
    unittest.main()
