"""R-983: `cat f | git commit -F -` and `git commit -F - < f` read the file that feeds stdin."""
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts plugin/scripts on sys.path before the plugin import)
import attribution

TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


class PipedFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        with open(os.path.join(self.home, "config.json"), "w") as fh:
            fh.write(json.dumps({"no_claude_attribution": True}))
        self.write("bad.txt", "fix thing\n\n%s\n" % TRAILER)
        self.write("good.txt", "fix thing\n\nMentions Claude in prose.\n")
        self.write("a.txt", "subject\n")

    def write(self, name, text):
        with open(os.path.join(self.home, name), "w") as fh:
            fh.write(text)

    def decide(self, command):
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": self.home}):
            return attribution.decision("Bash", {"command": command}, self.home, None)

    def test_cat_pipe_with_trailer_denied(self):
        self.assertEqual(self.decide("cat bad.txt | git commit -F -")[0], "deny")

    def test_redirect_with_trailer_denied(self):
        self.assertEqual(self.decide("git commit -F - < bad.txt")[0], "deny")

    def test_long_option_forms_denied(self):
        self.assertEqual(self.decide("cat bad.txt | git commit --file=-")[0], "deny")
        self.assertEqual(self.decide("git commit --file - < bad.txt")[0], "deny")

    def test_cat_pipe_clean_allowed(self):
        self.assertIsNone(self.decide("cat good.txt | git commit -F -"))

    def test_redirect_clean_allowed(self):
        self.assertIsNone(self.decide("git commit -F - < good.txt"))

    def test_cat_two_files_trailer_in_second_denied(self):
        self.assertEqual(self.decide("cat a.txt bad.txt | git commit -F -")[0], "deny")

    def test_missing_file_same_as_missing_dash_f(self):
        want = self.decide("git commit -F missing.txt")
        self.assertEqual(want[0], "ask")                    # R-1037 r4: missing now is unreadable now
        self.assertEqual(self.decide("cat missing.txt | git commit -F -"), want)
        self.assertEqual(self.decide("git commit -F - < missing.txt"), want)


class DevStdinTest(PipedFileTest):
    """R-998: -F /dev/stdin and -F /dev/fd/0 read the stdin file like -F -."""
    NAMES = ("/dev/stdin", "/dev/fd/0")

    def test_dev_stdin_with_trailer_denied(self):
        for n in self.NAMES:
            self.assertEqual(self.decide("git commit -F %s < bad.txt" % n)[0], "deny")
            self.assertEqual(self.decide("cat bad.txt | git commit -F %s" % n)[0], "deny")

    def test_dev_stdin_clean_allowed(self):
        for n in self.NAMES:
            self.assertIsNone(self.decide("git commit -F %s < good.txt" % n))
            self.assertIsNone(self.decide("cat good.txt | git commit -F %s" % n))


if __name__ == "__main__":
    unittest.main()
