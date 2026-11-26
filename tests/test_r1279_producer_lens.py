"""Producer-field lens: reviewers ask who writes each field a test reads; fixtures come from the real writer."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "plugin", "agents")


def _read(name):
    with open(os.path.join(AGENTS, name), encoding="utf-8") as fh:
        return fh.read()


class ProducerLens(unittest.TestCase):
    def test_both_reviewers_carry_the_real_producer_lens(self):
        for name in ("plan-critic.md", "verifier.md"):
            text = _read(name)
            with self.subTest(agent=name):
                self.assertIn("real producer", text)
                self.assertIn("never typed by hand", text)
                self.assertIn("no producer", text)

    def test_verifier_has_resize_and_javascript_tools(self):
        tools = [l for l in _read("verifier.md").splitlines() if l.startswith("tools:")][0]
        self.assertIn("mcp__Claude_Browser__resize_window", tools)
        self.assertIn("mcp__Claude_Browser__javascript_tool", tools)


if __name__ == "__main__":
    unittest.main()
