"""R-1076: every agent template tells the agent to stop and report on a refusal."""
import glob
import os
import unittest

AGENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "agents")
PHRASE = "On a refusal by a guard"


class AgentRefusalRule(unittest.TestCase):
    def test_every_agent_template_has_the_refusal_rule(self):
        files = sorted(glob.glob(os.path.join(AGENTS, "*.md")))
        self.assertGreaterEqual(len(files), 7)
        for path in files:
            with open(path, encoding="utf-8") as fh:
                self.assertIn(PHRASE, fh.read(), os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
