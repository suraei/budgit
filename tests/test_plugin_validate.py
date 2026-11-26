"""R-53: `claude plugin validate --strict` is a release gate, so it runs with the suite (skipped without the CLI)."""
import os, shutil, subprocess, unittest
from helpers import ROOT


class PluginValidateTest(unittest.TestCase):
    def test_plugin_validates_strict(self):
        claude = shutil.which("claude")
        if not claude:
            self.skipTest("claude CLI not on PATH")
        r = subprocess.run([claude, "plugin", "validate", "--strict", os.path.join(ROOT, "plugin")],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn("Validation passed", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
