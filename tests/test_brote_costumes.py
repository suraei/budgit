"""Brote's special-day looks (R-573): 19 Nov moustache and small hat in every pose and at the PC, 29 Mar party hat, 25 Nov flag and dark curly hair. The node tests drive the real sprites.js, personality.js and director.js."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOAT = ROOT / "tools/brote/float"
PLUGIN = ROOT / "plugin"


class CostumesNodeTest(unittest.TestCase):
    def test_node_suite(self):
        r = subprocess.run(["node", "--test", str(FLOAT / "costumes.test.mjs")], capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-1000:])
        self.assertIn("fail 0", r.stdout)


class CostumesShippedTest(unittest.TestCase):
    """The shipped copies carry the look too (the source text, no node needed)."""

    def test_plugin_sources_have_the_19_nov_look(self):
        for rel in ("hooks/brote-client.js", "float/src/sprites.js", "float/src/director.js", "float/src/personality.js"):
            t = (PLUGIN / rel).read_text()
            if rel.endswith("director.js"):
                self.assertIn("setLookDay", t, rel)
            elif rel.endswith("personality.js"):
                self.assertIn("mn_flex", t, rel)
            else:
                self.assertIn("costumeFor", t, rel)
                self.assertIn("'11-19'", t, rel)

    def test_bundle_resources_match_sources(self):
        for n in ("sprites.js", "director.js", "personality.js"):
            self.assertEqual((PLUGIN / "float/src" / n).read_bytes(), (PLUGIN / "float/Brote.app/Contents/Resources" / n).read_bytes(), n)


if __name__ == "__main__":
    unittest.main()
