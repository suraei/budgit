"""R-1060: no test file holds a literal secret-shaped string; fixtures are joined at run time."""
import glob, os, re, unittest

from helpers import ROOT

# the patterns are themselves built from parts, so this file passes its own scan
SHAPES = {
    "jwt": re.compile("ey" + r"J[A-Za-z0-9_-]{8,}\.ey" + r"J[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "google-api-key": re.compile("AI" + r"za[0-9A-Za-z_-]{35}"),
    "google-oauth-secret": re.compile("GOC" + r"SPX-[0-9A-Za-z_-]{28}"),
    "github-pat": re.compile("gh" + r"p_[0-9A-Za-z]{36}"),
    "stripe": re.compile(r"[sr]k_(?:live|test)_[0-9A-Za-z]{16,}"),
}


class FakeTokens(unittest.TestCase):
    def test_no_test_file_holds_a_literal_token_shape(self):
        hits = []
        for path in sorted(glob.glob(os.path.join(ROOT, "tests", "*.py"))):
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    for rule, rx in SHAPES.items():
                        if rx.search(line):
                            hits.append("%s:%d %s" % (os.path.basename(path), n, rule))
        self.assertEqual(hits, [], "literal token shapes in tests (build them at run time from parts)")

    def test_guard_patterns_do_detect_a_shape(self):
        sample = "gh" + "p_" + "a" * 36
        self.assertTrue(SHAPES["github-pat"].search(sample))
        self.assertTrue(SHAPES["jwt"].search(".".join(["ey" + "Jabcdefgh", "ey" + "Jabcdefgh", "abcdefgh"])))


if __name__ == "__main__":
    unittest.main()
