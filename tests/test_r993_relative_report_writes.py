"""R-993: a relative write target is resolved against the payload cwd, whatever the cwd is; a report in the verify folder
is refused from the data folder, the project root and a sibling folder. Readers and other data-folder files stay allowed."""
import os

import helpers  # noqa: F401
from helpers import StudioTestCase
import studiolib as sl

WRITE_VERBS = ("tee %s", "echo hi | tee %s", "cp /etc/hosts %s", "mv y %s", "echo hi > %s", "echo hi >> %s",
               "cat y > %s", "sed -i s/a/b/ %s")
READ_VERBS = ("cat %s", "head -1 %s", "ls %s", "grep foo %s", "cat %s | head -2")


class RelativeReportWritesTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.project, ".budgit")
        os.makedirs(os.path.join(self.data, "verify"), exist_ok=True)
        os.makedirs(os.path.join(self.data, "other"), exist_ok=True)
        os.makedirs(os.path.join(self.project, ".studio", "verify"), exist_ok=True)
        self.sibling = os.path.join(os.path.dirname(self.project), "sib-" + os.path.basename(self.project))
        os.makedirs(self.sibling, exist_ok=True)

    def d(self, command, cwd):
        return sl.verify_forgery_decision("Bash", {"command": command}, "general-purpose", cwd)

    def refused(self, target, cwd):
        for verb in WRITE_VERBS:
            c = verb % target
            got = self.d(c, cwd)
            self.assertIsNotNone(got, (c, cwd))
            self.assertEqual(got[0], "deny", (c, cwd))

    def allowed(self, verbs, target, cwd):
        for verb in verbs:
            c = verb % target
            self.assertIsNone(self.d(c, cwd), (c, cwd))

    def test_cwd_in_data_folder_relative_verify_refused(self):
        self.refused("verify/x", self.data)

    def test_cwd_in_data_folder_dot_slash_verify_refused(self):
        self.refused("./verify/x", self.data)

    def test_cwd_at_project_root_budgit_verify_refused(self):
        self.refused(".budgit/verify/x", self.project)

    def test_cwd_at_project_root_studio_verify_refused(self):
        self.refused(".studio/verify/x", self.project)

    def test_cwd_in_old_studio_folder_relative_verify_refused(self):
        self.refused("verify/x", os.path.join(self.project, ".studio"))

    def test_sibling_folder_relative_path_refused(self):
        self.refused("../%s/.budgit/verify/x" % os.path.basename(self.project), self.sibling)

    def test_verifier_still_writes(self):
        got = sl.verify_forgery_decision("Bash", {"command": "tee verify/x"}, "budgit:verifier", self.data)
        self.assertIsNone(got)

    def test_readers_stay_allowed_from_these_cwds(self):
        self.allowed(READ_VERBS, "verify/x", self.data)
        self.allowed(READ_VERBS, "./verify/x", self.data)
        self.allowed(READ_VERBS, ".budgit/verify/x", self.project)
        self.allowed(READ_VERBS, "../%s/.budgit/verify/x" % os.path.basename(self.project), self.sibling)

    def test_writes_to_other_data_files_stay_allowed(self):
        for verb in ("echo hi > %s", "echo hi >> %s", "cp /etc/hosts %s"):
            self.assertIsNone(self.d(verb % ".budgit/other/x", self.project), verb)
        self.assertIsNone(self.d("echo hi > other/x", self.data))

    def test_plain_writes_elsewhere_stay_allowed(self):
        self.assertIsNone(self.d("tee notes.txt", self.project))
        self.assertIsNone(self.d("echo hi > notes.txt", self.sibling))
