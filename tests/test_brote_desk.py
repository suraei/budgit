"""R-150: the Brote desktop dev mod (tools/brote/desk) under `claude plugin test`, plus its node unit tests (skipped without the tools)."""
import os, shutil, subprocess, tempfile, unittest
from helpers import ROOT

DESK = os.path.join(ROOT, "tools", "brote", "desk")


class BroteDeskTest(unittest.TestCase):
    def test_generated_module_is_current(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "plugin")
            shutil.copytree(os.path.join(ROOT, "plugin"), os.path.join(tmp, "plugin"), ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(DESK, os.path.join(tmp, "tools", "brote", "desk"))
            r = subprocess.run([node, os.path.join(tmp, "tools", "brote", "desk", "build.mjs")], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(tmp, "tools", "brote", "desk", "hooks", "register.jsx")) as built, \
                 open(os.path.join(DESK, "hooks", "register.jsx")) as shipped:
                self.assertEqual(built.read(), shipped.read(), "hooks/register.jsx is stale: run node tools/brote/desk/build.mjs")

    def test_node_unit_tests(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH")
        r = subprocess.run([node, "--test", os.path.join(DESK, "src")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])

    def test_mod_passes_the_kit(self):
        claude = shutil.which("claude")
        if not claude:
            self.skipTest("claude CLI not on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "brote-desk")
            shutil.copytree(DESK, dest, ignore=shutil.ignore_patterns("src", "build.mjs", "*.md"))
            shutil.copy(os.path.join(ROOT, "tests", "kit", "brote-desk.test.ts"), os.path.join(dest, "hooks", "desk.test.ts"))
            r = subprocess.run([claude, "plugin", "test", dest], capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
            v = subprocess.run([claude, "plugin", "validate", DESK], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
            self.assertEqual(v.returncode, 0, v.stdout[-2000:] + v.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
