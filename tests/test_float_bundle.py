"""R-292 plan B: the prebuilt bundle that ships in plugin/float/ is the real thing and agrees with the sources.

These do not rebuild (tests/test_float_build.py does that, and proves a rebuild is byte-identical); they look at the committed bundle.
They fail when the sources under tools/brote/float changed and nobody rebuilt plugin/float, or when anyone edited a file of the bundle.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "scripts"))
import floatmanifest as fm  # noqa: E402
import lint  # noqa: E402

FLOAT = ROOT / "plugin" / "float"
SRC = ROOT / "tools" / "brote" / "float"
EXE = FLOAT / "Brote.app" / "Contents" / "MacOS" / "Brote"
MAC = sys.platform == "darwin" and shutil.which("codesign") is not None


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})


class Bundle(unittest.TestCase):
    def test_the_bundle_is_in_the_plugin(self):
        self.assertTrue(EXE.is_file(), "plugin/float/Brote.app is not built: BROTE_OUT=plugin/float tools/brote/float/build-universal.sh")

    def test_it_is_exactly_its_manifest_and_built_from_the_current_sources(self):
        self.assertEqual(fm.check(str(FLOAT), str(SRC)), [])

    def test_every_file_is_a_plain_file_and_only_the_app_binary_runs(self):
        for d, dirs, files in os.walk(FLOAT):
            for n in dirs + files:
                p = Path(d) / n
                self.assertFalse(p.is_symlink(), str(p))
            for n in files:
                p = Path(d) / n
                run = bool(p.stat().st_mode & stat.S_IXUSR)
                self.assertEqual(run, p == EXE, f"{p.relative_to(FLOAT)}: only the app binary is executable")

    def test_the_manifest_is_the_one_the_plugin_ships_a_copy_of_the_sources_for(self):
        for n in fm.SRC_FILES:
            self.assertEqual((FLOAT / "src" / n).read_bytes(), (SRC / n).read_bytes(), n)

    @unittest.skipUnless(MAC, "needs macOS tools")
    def test_both_architectures_signed_ad_hoc_with_no_team_and_no_entitlements(self):
        self.assertEqual(sorted(sh("lipo", "-archs", str(EXE)).stdout.split()), ["arm64", "x86_64"])
        # a copy without extended attributes: a synced folder (iCloud Documents) adds Finder attributes to the bundle folder, which codesign refuses
        tmp = Path(tempfile.mkdtemp(prefix="float-sig-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        app = tmp / "Brote.app"
        self.assertEqual(sh("ditto", "--noextattr", "--noqtn", "--norsrc", str(FLOAT / "Brote.app"), str(app)).returncode, 0)
        self.assertEqual(sh("codesign", "--verify", "--strict", "--deep", str(app)).returncode, 0)
        info = sh("codesign", "-dvvv", str(app)).stderr
        self.assertIn("Signature=adhoc", info)
        self.assertIn("TeamIdentifier=not set", info)
        self.assertEqual(sh("codesign", "-d", "--entitlements", "-", str(app)).stdout.strip(), "")

    @unittest.skipUnless(MAC, "needs macOS tools")
    def test_only_system_libraries_and_no_personal_path_in_the_binary(self):
        libs = [l.split()[0] for l in sh("otool", "-L", str(EXE)).stdout.splitlines() if l.startswith(("\t", " ")) and l.strip()]   # a universal file prints one list per slice
        self.assertTrue(libs and all(l.startswith(("/System/", "/usr/lib/")) for l in libs), libs)
        strings = sh("strings", "-a", str(EXE)).stdout
        self.assertEqual([m.group(0) for m in lint.PERSONAL.finditer(strings) if m.group(1).lower() not in lint.PLACEHOLDERS], [])


if __name__ == "__main__":
    unittest.main()
