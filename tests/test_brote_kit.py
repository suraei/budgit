"""R-114: the shipped Brote modules under `claude plugin test` (skipped without the CLI)."""
import json, os, shutil, subprocess, tempfile, unittest
from helpers import ROOT
import i18n


class BroteKitTest(unittest.TestCase):
    def test_modules_pass_the_kit(self):
        claude = shutil.which("claude")
        if not claude:
            self.skipTest("claude CLI not on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "plugin")
            shutil.copytree(os.path.join(ROOT, "plugin"), dest, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(os.path.join(ROOT, "tests", "kit", "brote.test.ts"), os.path.join(dest, "hooks", "brote.test.ts"))
            strings = {lang: i18n.brote_strings(lang) for lang in i18n.LANGS}   # scripts/i18n.py is the one catalog
            with open(os.path.join(dest, "hooks", "i18n-fixture.ts"), "w", encoding="utf-8") as f:
                f.write("export const STRINGS: any = %s\n" % json.dumps(strings, ensure_ascii=False))
            r = subprocess.run([claude, "plugin", "test", dest], capture_output=True, text=True, timeout=180,
                               stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, "\n".join(l for l in r.stdout.splitlines() if "(pass)" not in l)[-3000:] + r.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
