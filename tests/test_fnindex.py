"""R-274: tools/index/fnindex.py prints a function outline of a big source file from a grep-style pass, so an agent reads the outline and
then only the lines it needs. Generated on demand, never stored, so it cannot go stale. Read-only; no parser, no graph, no dependencies."""
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

from helpers import ROOT

TOOL = os.path.join(ROOT, "tools", "index", "fnindex.py")
sys.path.insert(0, os.path.dirname(TOOL))
import fnindex


class OutlineTest(unittest.TestCase):
    def test_python_defs_and_classes_with_line_numbers_and_nesting(self):
        src = "import os\n\nclass Box:\n    def open(self, x):\n        return x\n\nasync def fetch(url):\n    pass\n\ndef _helper(): pass\n"
        self.assertEqual(fnindex.outline(src, ".py"), [
            (3, 0, "class Box:"), (4, 1, "def open(self, x):"), (7, 0, "async def fetch(url):"), (10, 0, "def _helper(): pass")])

    def test_python_ignores_defs_inside_strings_and_comments(self):
        src = '# def nope():\nx = "def also_nope():"\ndef real():\n    pass\n'
        self.assertEqual([n for _, _, n in fnindex.outline(src, ".py")], ["def real():"])

    def test_swift_declarations_marks_and_modifiers(self):
        src = ("import AppKit\n// MARK: - Physics\nfinal class Brote: NSObject {\n    private static func tick(_ dt: Double) -> Int {\n        return 1\n    }\n"
               "    @objc func pop() {}\n}\nextension Brote: Equatable {}\nenum Mood { case calm }\nstruct P { var x = 1 }\nprotocol Speaks {}\n    let f = { func inner() {} }\n")
        got = fnindex.outline(src, ".swift")
        names = [n for _, _, n in got]
        self.assertIn("// MARK: - Physics", names)
        self.assertIn("final class Brote: NSObject {", [n for n in names])
        self.assertIn("private static func tick(_ dt: Double) -> Int {", names)
        self.assertIn("@objc func pop() {}", names)
        for kind in ("extension Brote", "enum Mood", "struct P", "protocol Speaks"):
            self.assertTrue(any(n.startswith(kind) for n in names), kind)
        self.assertEqual(got[0][0], 2)

    def test_javascript_functions_and_arrow_consts(self):
        src = "const a = 1;\nfunction go(x) {\n}\nexport async function load() {}\nconst step = (dt) => {\n};\nclass Dir {\n  tick() {}\n}\n"
        names = [n for _, _, n in fnindex.outline(src, ".js")]
        self.assertEqual(names, ["function go(x) {", "export async function load() {}", "const step = (dt) => {", "class Dir {"])

    def test_long_signatures_are_cut(self):
        src = "def f(" + ", ".join("argument_%d" % i for i in range(40)) + "):\n    pass\n"
        (_, _, text), = fnindex.outline(src, ".py")
        self.assertLessEqual(len(text), 100)
        self.assertTrue(text.endswith("…"))

    def test_the_outline_is_much_smaller_than_the_file(self):
        src = "".join("def f%d(a):\n" % i + "".join("    step_%d = a + %d\n" % (j, j) for j in range(10)) + "    return a\n\n" for i in range(200))
        self.assertLess(len(fnindex.render("big.py", src)), len(src) // 3)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_tool(self, *args):
        return subprocess.run([sys.executable, TOOL] + list(args), capture_output=True, text=True)

    def test_prints_path_header_then_line_and_text(self):
        p = os.path.join(self.tmp, "a.py")
        open(p, "w").write("def one():\n    pass\n")
        r = self.run_tool(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a.py (2 lines)", r.stdout)
        self.assertIn("    1  def one():", r.stdout)

    def test_an_unknown_extension_or_missing_file_is_a_clear_error(self):
        p = os.path.join(self.tmp, "a.txt")
        open(p, "w").write("x")
        r = self.run_tool(p)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unsupported", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.run_tool(os.path.join(self.tmp, "nope.py")).returncode, 2)

    def test_it_writes_nothing(self):
        p = os.path.join(self.tmp, "a.py")
        open(p, "w").write("def one(): pass\n")
        before = sorted(os.listdir(self.tmp))
        self.run_tool(p)
        self.assertEqual(sorted(os.listdir(self.tmp)), before)

    def test_it_handles_the_real_big_files_quickly_and_small(self):
        for rel in ("tools/brote/float/brote.swift", "plugin/scripts/studiolib.py", "plugin/scripts/rawguard.py"):
            path = os.path.join(ROOT, rel)
            r = self.run_tool(path)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(len(r.stdout), os.path.getsize(path) // 5, rel)
            self.assertGreater(r.stdout.count("\n"), 50, rel)


if __name__ == "__main__":
    unittest.main()
