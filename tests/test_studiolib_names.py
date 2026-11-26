"""A module-level name defined twice in studiolib silently replaces the first definition for every caller (a second
`_RUNNERS` once broke the hook-bypass guard). Each top-level name is defined once, except the ones listed here."""
import ast
import collections
import os
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts", "studiolib.py")
KNOWN = {"_GLOB_CHARS"}            # defined twice before this test existed, with the same meaning


class OneDefinitionPerNameTest(unittest.TestCase):
    def test_no_top_level_name_is_defined_twice(self):
        tree = ast.parse(open(SRC).read())
        seen = collections.Counter()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                seen[node.name] += 1
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        seen[t.id] += 1
        twice = sorted(n for n, c in seen.items() if c > 1 and n not in KNOWN)
        self.assertEqual(twice, [])


if __name__ == "__main__":
    unittest.main()
