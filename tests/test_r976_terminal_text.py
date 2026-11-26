"""R-976: the terminal band shows Brote's one-line text form while BIG_PET_IN_TERMINAL is false (until the compact family ships)."""
import os
import re
import unittest

from helpers import ROOT

REG = os.path.join(ROOT, "plugin", "hooks", "register.jsx")


class TerminalText(unittest.TestCase):
    def setUp(self):
        with open(REG, encoding="utf-8") as f:
            self.src = f.read()

    def test_switch_is_false(self):
        self.assertRegex(self.src, r"const BIG_PET_IN_TERMINAL = false\b")

    def test_terminal_branch_returns_the_text_form_while_the_switch_is_false(self):
        m = re.search(r"if \(\(e\.surface === 'terminal' && !BIG_PET_IN_TERMINAL\) \|\| [^\n]*\) \{\n(.*?)\n  \}\n  const still", self.src, re.S)
        self.assertIsNotNone(m, "the terminal text branch must be gated first by the switch, on the terminal surface only")
        body = m.group(1)
        self.assertIn("return (", body)
        self.assertIn("{line}", body)
        self.assertNotIn("<Client", body)
        # the Client height-15 branch only follows that return, so it is unreachable while the switch is false
        self.assertLess(self.src.index("if ((e.surface === 'terminal' && !BIG_PET_IN_TERMINAL)"), self.src.index("height={15}"))


if __name__ == "__main__":
    unittest.main()
