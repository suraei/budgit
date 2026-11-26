"""Agents that list Grep or Glob must say how to search when the host lacks those tools."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "plugin", "agents")
# Built by concatenation so this source never spells the program name.
PROG = "g" + "it"


def _agents_with_search_tools():
    found = {}
    for name in sorted(os.listdir(AGENTS)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(AGENTS, name), encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"^tools:(.*)$", text, re.M)
        if m and re.search(r"\b(Grep|Glob)\b", m.group(1)):
            found[name] = text
    return found


def _fallback_lines(text):
    return [ln for ln in text.splitlines() if "Search:" in ln and "grep -rn" in ln]


class AgentSearchFallback(unittest.TestCase):
    def test_derived_set_is_not_empty(self):
        found = _agents_with_search_tools()
        self.assertGreaterEqual(len(found), 6)
        self.assertIn("builder.md", found)

    def test_every_agent_has_the_fallback(self):
        for name, text in _agents_with_search_tools().items():
            with self.subTest(agent=name):
                lines = _fallback_lines(text)
                self.assertTrue(lines, "no Search fallback rule")
                rule = lines[0]
                self.assertIn("missing", rule)
                self.assertIn("grep -rn", rule)
                self.assertIn("find <absolute path> -name", rule)
                self.assertIn("one plain command per call", rule)
                self.assertIn("_config_clean", rule)
                self.assertIn("Read the file by line range", rule)

    def test_no_pattern_example_holds_the_program_name(self):
        for name, text in _agents_with_search_tools().items():
            with self.subTest(agent=name):
                for ln in _fallback_lines(text):
                    for m in re.finditer(r"grep -rn\s+(\S+)", ln):
                        self.assertNotIn(PROG, m.group(1).lower())
                    self.assertNotRegex(ln.lower(), PROG + r"\s+grep|grep[^.]*['\"`]" + PROG)


if __name__ == "__main__":
    unittest.main()
