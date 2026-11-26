"""R-1360 G2b: a plain `grep` or `rg` pattern argument (every -e / --regexp value, else the first non-option word) is data for the
program-word check. Every other check still sees the whole line. The program word is spelled g+it here so the file never holds it."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import mergeguard  # noqa: E402

G = "g" + "it"


class PatternIsData(unittest.TestCase):
    def setUp(self):
        self.main = os.path.realpath(tempfile.mkdtemp(prefix="r1360g2b-"))
        subprocess.run((G, "init", "-q"), cwd=self.main, check=True, capture_output=True)
        self.file = os.path.join(self.main, "f.py")
        patch = mock.patch.object(mergeguard, "checkout_root", return_value=self.main)
        patch.start()
        self.addCleanup(patch.stop)

    def judge(self, command):
        return mergeguard.decision("Bash", {"command": command}, self.main)

    def allowed(self, command):
        self.assertIsNone(self.judge(command), command)

    def refused(self, command):
        d = self.judge(command)
        self.assertEqual(d[0] if d else None, "deny", (command, d))

    def test_allow_a_double_quoted_pattern_holding_the_word(self):
        self.allowed('grep -n "def %s" %s' % (G, self.file))

    def test_allow_a_single_quoted_pattern_holding_the_word(self):
        self.allowed("rg '%s status' %s" % (G, self.file))

    def test_allow_the_word_given_with_e_and_regexp(self):
        self.allowed("grep -e %s -e other %s" % (G, self.file))
        self.allowed("rg --regexp %s %s" % (G, self.file))

    def test_allow_the_word_with_extended_flag(self):
        self.allowed('grep -E "%s|hg" %s' % (G, self.file))

    def test_refuse_the_word_as_a_second_command_after_a_pipe(self):
        self.refused("grep x %s | %s push origin" % (self.file, G))

    def test_refuse_a_substitution_inside_the_pattern(self):
        self.refused('grep "$(%s status)" %s' % (G, self.file))

    def test_refuse_a_pattern_file_source(self):
        self.refused("grep -f patterns.txt %s %s" % (G, self.file))

    def test_refuse_the_word_as_a_later_command_in_a_chain(self):
        self.refused("grep x %s && %s push origin" % (self.file, G))

    def test_refuse_the_word_as_a_path_argument(self):
        self.refused("grep x %s" % G)

    def test_refuse_a_wrapper_before_grep(self):
        self.refused('env grep "%s" %s' % (G, self.file))


if __name__ == "__main__":
    unittest.main()
