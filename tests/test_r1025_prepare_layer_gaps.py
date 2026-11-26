"""R-1025 / R-1027: gaps in the R-1017 allowlist layer in front of the word guard.
 - the commit shape takes -a, -am, -qam, -aqm (the R-1014 chain: five test runs joined by &&, then `commit -am <message>`);
   -c, -F, --template, --amend and aliases still go to the word guard;
 - a reader path is read against the folder the command runs in (the hook's cwd argument), and any line with cd, pushd
   or popd goes to the word guard.
The refused and passing phrases live here only."""
import os
import unittest

from helpers import StudioTestCase
import studiolib as sl

FILE = "approved" + "_prepare"
APPROVE = "budgit prepare --approve"
RUN = "env PYTHONPATH=/wt/tests:/wt/plugin/scripts lockf /tmp/l python3 -W ignore -m unittest discover -s /wt/tests -p 'test_%s*'"


def budgit_home():
    """R-1050: read at call time, as the guard does (it reads HOME when it runs), never fixed at import."""
    return os.path.join(os.path.expanduser("~"), ".budgit")


class Base(StudioTestCase):
    def asked(self, command, cwd=None):
        got = sl.approval_decision(command, self.project, cwd)
        if got is not None:
            self.assertEqual(got[0], "ask", command)
        return got is not None


class CommitShapeTakesAllTest(Base):
    def test_the_r1014_chain_of_five_runs_then_commit_am_passes(self):
        chain = " && ".join(RUN % n for n in ("r1014", "hook", "guard_home", "guards2", "r901"))
        for flags in ("-am", "-qam", "-aqm", "-a -m", "-q -a -m", "-m"):
            cmd = "%s && git -C /wt commit %s 'R-1014: let budgit prepare --approve text pass'" % (chain, flags)
            self.assertFalse(self.asked(cmd), flags)

    def test_commit_with_other_options_still_goes_to_the_word_guard(self):
        for opts in ("-c x -am", "-F x -m", "--template x -m", "--amend -m", "-am --amend -m", "-aem", "-ac"):
            self.assertTrue(self.asked("git commit %s '%s'" % (opts, APPROVE)), opts)
        self.assertTrue(self.asked("git -c alias.ci='!%s' ci" % APPROVE))
        self.assertTrue(self.asked("git -c user.name=x commit -am '%s'" % APPROVE))

    def test_commit_am_with_a_message_naming_the_file_still_asks(self):
        self.assertTrue(self.asked("git commit -am " + FILE))


class ReaderPathReadAgainstCwdTest(Base):
    def test_a_relative_reader_path_with_cwd_inside_the_budgit_folder_asks(self):
        self.assertTrue(self.asked("grep -r %s ." % FILE, cwd=budgit_home()))
        self.assertTrue(self.asked("grep -r %s sub/dir" % FILE, cwd=budgit_home()))
        self.assertTrue(self.asked("grep -r %s . | head" % FILE, cwd=budgit_home()))

    def test_the_same_reader_from_a_project_folder_passes(self):
        self.assertFalse(self.asked("grep -r %s ." % FILE, cwd=self.project))

    def test_cd_pushd_popd_send_the_line_to_the_word_guard(self):
        for hop in ("cd " + budgit_home(), "cd ~/.budgit", "pushd " + budgit_home(), "popd", "true && cd /tmp"):
            self.assertTrue(self.asked("%s; grep -r %s ." % (hop, FILE)), hop)
        self.assertTrue(self.asked("cd ~/.budgit && grep -r %s ." % FILE, cwd=self.project))
        self.assertTrue(self.asked("cd ~/.budgit; git commit -am '%s'" % APPROVE, cwd=self.project))


if __name__ == "__main__":
    unittest.main()
