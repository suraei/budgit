"""R-1108 A: verifier and persona-sim read web pages as text first, with capped scaled screenshots."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "plugin", "agents")


def _read(name):
    with open(os.path.join(AGENTS, name), encoding="utf-8") as fh:
        return fh.read()


class TextFirstBrowser(unittest.TestCase):
    def test_both_templates_carry_the_rule(self):
        for name in ("verifier.md", "persona-sim.md"):
            text = _read(name)
            with self.subTest(agent=name):
                self.assertIn("text first, with `read_page` or `get_page_text`", text)
                self.assertIn("scale about 0.5", text)
                self.assertIn("at most 6 screenshots per run", text)
                self.assertIn("(`read_page` or `find`), not", text)
                self.assertIn("Screenshots taken: N of 6", text)

    def test_verifier_limits_screenshots_to_design_or_unprovable(self):
        text = _read("verifier.md")
        self.assertIn("only for a `[design]` check, or when text cannot prove the state", text)
        self.assertIn(
            "Shots that `budgit design-check shots` saves to disk do not count toward the 6; viewing one of them does.",
            text,
        )


if __name__ == "__main__":
    unittest.main()
