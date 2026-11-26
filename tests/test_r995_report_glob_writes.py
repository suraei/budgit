"""R-995: from a data folder or a project root, a write whose target only spells the verify folder through a glob, quotes, a
backslash, a symlink or a `cd` is refused. Readers, other data files and the verifier stay allowed."""
import os

import helpers  # noqa: F401
from helpers import StudioTestCase
import studiolib as sl


class ReportGlobWritesTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.project, ".budgit")
        os.makedirs(os.path.join(self.data, "verify"), exist_ok=True)
        os.makedirs(os.path.join(self.data, "other"), exist_ok=True)
        os.makedirs(os.path.join(self.project, "notes"), exist_ok=True)

    def d(self, command, cwd=None, agent="general-purpose"):
        return sl.verify_forgery_decision("Bash", {"command": command}, agent, cwd or self.data)

    def refused(self, command, cwd=None):
        got = self.d(command, cwd)
        self.assertIsNotNone(got, command)
        self.assertEqual(got[0], "deny", command)

    def test_glob_star_refused(self):
        self.refused("tee ver*/x")

    def test_glob_question_refused(self):
        self.refused("tee v?rify/x")

    def test_glob_bracket_refused(self):
        self.refused("tee [v]erify/x")

    def test_glob_from_project_root_refused(self):
        self.refused("tee .budgit/ver*/x", self.project)
        self.refused("tee .b*/verify/x", self.project)

    def test_empty_quotes_refused(self):
        self.refused("tee ver''ify/x")

    def test_double_quoted_part_refused(self):
        self.refused('tee "ver"ify/x')

    def test_backslash_refused(self):
        self.refused("cp a ./ver\\ify/x")

    def test_symlink_made_in_command_refused(self):
        self.refused("ln -s verify v; tee v/x")

    def test_symlink_on_disk_refused(self):
        os.symlink(os.path.join(self.data, "verify"), os.path.join(self.data, "v"))
        self.refused("tee v/x")
        self.refused("echo hi > v/x")

    def test_cd_into_verify_then_write_refused(self):
        self.refused("cd verify && tee x")

    def test_variable_form_still_refused(self):
        self.refused("d=verify; tee $d/x")

    def test_brace_form_still_refused(self):
        self.refused("tee {verify,q}/x")

    def test_readers_from_budgit_allowed(self):
        for c in ("cat ver*/x", "ls v?rify", "cat ver''ify/x", "head -1 [v]erify/x", "cd verify && cat x"):
            self.assertIsNone(self.d(c), c)

    def test_writes_outside_verify_allowed(self):
        for c in ("tee notes/x", "tee other/*.md", "tee other/x", "echo hi > other/x", "cp a other/y"):
            self.assertIsNone(self.d(c), c)
        self.assertIsNone(self.d("tee notes/x", self.project))

    def test_root_globs_that_cannot_reach_data_not_refused(self):
        for c in ("rm *.tmp", "rm x[z-a]", "tee notes/*.md"):
            self.assertFalse(sl._write_may_reach_verify(c, self.project), c)

    def test_root_dot_globs_refused(self):
        self.refused("rm -r .b*", self.project)
        self.refused("tee .b*/verify/x", self.project)

    def test_data_folder_prefix_glob_refused(self):
        self.refused("tee .budgit/v*/x", self.project)

    def test_verifier_still_writes(self):
        self.assertIsNone(self.d("tee ver*/x", agent="budgit:verifier"))
        self.assertIsNone(self.d("cd verify && tee x", agent="budgit:verifier"))
