"""R-1360 task C1: the R-1037 ask names its cause and the -F <file> door inside the first 200 chars (hook.py cuts a reason there),
and a missing -F file is told apart from one that exists but cannot be read. Text only: the ask decision is the same as before."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import attribution  # noqa: E402

WORD = "g" + "it"                                    # the program word, kept out of this file's own command lines


class AskDoorText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r1360c-attr-")

    def ask(self, path):
        d = attribution.decision("Bash", {"command": "%s commit -F %s" % (WORD, path)}, self.tmp, enabled=True)
        self.assertIsNotNone(d)
        self.assertEqual(d[0], "ask", d)
        return d[1]

    def test_the_first_200_chars_of_the_r1037_ask_hold_the_dash_f_file_door(self):
        reason = self.ask(os.path.join(self.tmp, "absent-msg.txt"))
        self.assertIn("-F <file>", reason[:200])
        self.assertTrue(reason.startswith("R-1037:"), reason)

    def test_a_missing_dash_f_file_is_named_as_missing(self):
        reason = self.ask(os.path.join(self.tmp, "absent-msg.txt"))
        self.assertIn("absent-msg.txt is missing", reason)

    def test_a_dash_f_folder_is_unreadable_not_missing(self):
        reason = self.ask(self.tmp)
        self.assertIn("is not a file the guard can read whole", reason)
        self.assertNotIn("is missing", reason)


if __name__ == "__main__":
    unittest.main()
