"""R-1109 c: the researcher template tags each source verbatim or via summary."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ResearcherSourceTags(unittest.TestCase):
    def setUp(self):
        path = os.path.join(ROOT, "plugin", "agents", "researcher.md")
        with open(path, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_both_tags_present(self):
        self.assertIn("`verbatim`", self.text)
        self.assertIn("`via summary`", self.text)

    def test_webfetch_counts_as_summariser(self):
        self.assertIn("WebFetch counts as a summariser", self.text)

    def test_key_fact_and_amount_rules(self):
        self.assertIn("says so in the summary line", self.text)
        self.assertIn("carries its source tag next to it", self.text)

    def test_relay_keeps_the_source_tag(self):
        path = os.path.join(ROOT, "plugin", "scripts", "reference", "team-rules.md")
        with open(path, encoding="utf-8") as fh:
            rules = fh.read()
        self.assertIn(
            "When you pass a research finding on, keep each source's `verbatim` / `via summary` tag; "
            "a figure or legal/tax rule resting only on `via summary` is marked unconfirmed.",
            rules,
        )


if __name__ == "__main__":
    unittest.main()
