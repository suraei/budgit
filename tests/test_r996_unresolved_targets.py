"""R-996: from a data folder or a project root, a writer whose target is built by command substitution or backticks, or is a
variable the same command assigns (VAR=, export, read, for VAR in <list reaching verify>), is refused. Variables from the
fixed environment, single-quoted `$`, readers and cwds outside the project stay allowed."""
import os
import tempfile

import helpers  # noqa: F401
from helpers import StudioTestCase
import studiolib as sl

REFUSED = ["tee $(echo verify)/x", "tee `echo v`/x", "X=verify; tee $X/x", "export X=verify; tee ${X}/x",
           "env X=verify tee $X/x", "cp a $(echo verify)/x", "echo hi > $(echo verify)/x",
           'for f in .budgit/verify/*; do rm "$f"; done', 'for f in .b*/v*/*; do rm "$f"; done']


class UnresolvedTargetsTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.project, ".budgit")
        os.makedirs(os.path.join(self.data, "verify"), exist_ok=True)
        self.studio = os.path.join(self.project, ".studio")
        os.makedirs(self.studio, exist_ok=True)

    def d(self, command, cwd):
        return sl.verify_forgery_decision("Bash", {"command": command}, "general-purpose", cwd)

    def test_substituted_and_assigned_refused(self):
        for cwd in (self.data, self.studio, self.project):
            for c in REFUSED[2:5]:
                got = self.d(c, cwd)
                self.assertIsNotNone(got, (c, cwd))
                self.assertEqual(got[0], "deny", (c, cwd))
            for c in REFUSED[:2] + REFUSED[5:7]:
                got = self.d(c, cwd)
                self.assertIsNotNone(got, (c, cwd))
                self.assertEqual(got[0], "ask", (c, cwd))

    def test_for_loop_refused_only_when_list_reaches_verify(self):
        for c in REFUSED[7:]:
            got = self.d(c, self.project)
            self.assertIsNotNone(got, c)
            self.assertEqual(got[0], "deny", c)
        self.assertIsNone(self.d('for f in build/*; do rm "$f"; done', self.project))

    def test_environment_variables_and_quoted_dollar_not_refused(self):
        for cwd in (self.data, self.studio, self.project):
            for c in ("echo {} > $X/trust.json", "tee '$x'", "rm $HOME/.cache/x", "tee $V/x", "tee ${V}/x"):
                self.assertIsNone(self.d(c, cwd), (c, cwd))

    def test_readers_allowed(self):
        for cwd in (self.data, self.studio, self.project):
            for c in ("cat $(echo verify)/x", "cat `echo v`/x", "cat $V/x", "echo $HOME"):
                self.assertIsNone(self.d(c, cwd), (c, cwd))

    def test_outside_project_unchanged(self):
        with tempfile.TemporaryDirectory() as out:
            out = os.path.realpath(out)
            for c in ("tee $(echo v)/x", "tee `echo v`/x", "X=v; tee $X/x"):
                self.assertIsNone(self.d(c, out), c)


if __name__ == "__main__":
    import unittest
    unittest.main()
