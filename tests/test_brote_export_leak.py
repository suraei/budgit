"""The Brote files that ship in the public export (personality.js, director.js) must read as product code:
no owner voice, no ledger numbers, no private paths or provenance, no Spanish notes. Same rules as the export's leak gate."""
import os, re, shutil, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin/scripts"))
import export, lint   # noqa: E402

FLOAT = ROOT / "tools/brote/float"
FLOAT_FILES = export.FLOAT_FILES
SHIPPED = FLOAT_FILES          # every file the export copies from tools/brote/float, in the folder itself
HOOK = ROOT / "plugin/hooks/brote-event.sh"
CODE_COMMENT = re.compile(r"(?://|^\s*#|/\*|^\s*\*).*\bR-\d+")   # ledger numbers in comments; item ids in test data (R-1) are fixtures
PATTERNS = {
    "ledger number": re.compile(r"\bR-\d+"),
    "private path": re.compile(r"\b(?:docs|research|\.studio|\.budgit)/"),
    "design provenance": re.compile(r"DESIGN REFERENCE|Not shipped|core-build|brote-personality|\b20\d\d-\d\d-\d\d\b"),
    "spanish": re.compile(r"[áéíóúñ¿¡]"),
}


def problems(directory):
    """What the export's rules would refuse in these files: [(file, line, rule, text)]."""
    paths = [os.path.join(directory, n) for n in SHIPPED if os.path.exists(os.path.join(directory, n))]
    if directory == str(FLOAT):
        paths.append(str(HOOK))
    out = [(os.path.basename(h.path), h.line, h.kind, h.term) for h in export.voice_hits(paths, directory)]
    for path in paths:
        for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            for rule, rx in PATTERNS.items():
                if path.endswith((".swift", ".sh", ".plist")) and rule in ("ledger number", "spanish"):
                    rx = CODE_COMMENT if rule == "ledger number" else None   # the app's own Spanish UI strings are product text
                if rx is None:
                    continue
                m = rx.search(line)
                if m:
                    out.append((os.path.basename(path), n, rule, m.group(0)))
    return out


class BroteExportLeakTest(unittest.TestCase):
    def test_shipped_files_are_clean(self):
        self.assertEqual(problems(str(FLOAT)), [])

    def test_the_rules_catch_a_leak(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("personality.js", "director.js"):
                shutil.copy(FLOAT / n, d)
            with open(os.path.join(d, "director.js"), "a") as f:
                f.write("// she came to Claude (R-231), see docs/research/x.md\n")
            rules = {p[2] for p in problems(d)}
            self.assertTrue({"owner voice", "ledger number", "private path"} <= rules, rules)

    def test_every_exported_float_file_and_the_hook_are_covered(self):
        self.assertTrue({"brote.swift", "build.sh", "personality.js", "director.js"} <= set(FLOAT_FILES))
        self.assertTrue(HOOK.exists())

    def test_header_is_one_neutral_line(self):
        first = (FLOAT / "personality.js").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, "/* Generated from Brote's personality data. Do not edit by hand. */")


if __name__ == "__main__":
    unittest.main()
