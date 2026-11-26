"""R-990: a bare or relative write target is judged against the shell's cwd; inside a verify folder it is refused."""
import os

import helpers  # noqa: F401
from helpers import StudioTestCase
import studiolib as sl

WRITERS = ("tee x", "echo hi | tee x", "cp /etc/hosts x", "mv y x", "echo hi > x", "echo hi >> x", "cat y > x", "sed -i s/a/b/ x")
READERS = ("cat x", "head -1 x", "ls", "ls -la", "grep foo x", "cat x | head -2")


class BareWritersTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.verify = os.path.join(self.project, ".budgit", "verify")
        self.sub = os.path.join(self.verify, "sub")
        os.makedirs(self.sub)
        self.outside = os.path.join(self.project, "docs")
        os.makedirs(self.outside)

    def d(self, command, cwd):
        return sl.verify_forgery_decision("Bash", {"command": command}, "general-purpose", cwd)

    def test_writers_refused_with_cwd_in_the_folder(self):
        for c in WRITERS:
            got = self.d(c, self.verify)
            self.assertIsNotNone(got, c)
            self.assertEqual(got[0], "deny", c)

    def test_writers_refused_with_cwd_in_a_subfolder(self):
        for c in WRITERS:
            got = self.d(c, self.sub)
            self.assertIsNotNone(got, c)
            self.assertEqual(got[0], "deny", c)

    def test_writers_refused_with_cwd_in_the_old_studio_folder(self):
        old = os.path.join(self.project, ".studio", "verify")
        os.makedirs(old)
        for c in WRITERS:
            self.assertIsNotNone(self.d(c, old), c)

    def test_writers_allowed_with_cwd_outside_the_folder(self):
        for c in WRITERS:
            self.assertIsNone(self.d(c, self.outside), c)

    def test_readers_allowed_inside_the_folder(self):
        for c in READERS:
            self.assertIsNone(self.d(c, self.verify), c)
            self.assertIsNone(self.d(c, self.sub), c)

    def test_verifier_still_writes(self):
        self.assertIsNone(sl.verify_forgery_decision("Bash", {"command": "tee x"}, "budgit:verifier", self.verify))
