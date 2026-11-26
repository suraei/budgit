"""R-292 plan B, step 1: the prebuilt floating Brote bundle (tools/brote/float/build-universal.sh + plugin/scripts/floatmanifest.py).

The bundle is built once for the whole module (about 70 s: both slices build in parallel), then checked: both architectures, ad-hoc
signature without a team or entitlements, only system libraries, no home path in the binary, a manifest that lists exactly the files
in the bundle, a check that refuses tampering / stale sources / mixed files, a build that repeats byte for byte from another folder and
another $HOME, and refusals that write nothing.
Skipped when this is not macOS with the command line tools; the manifest unit tests always run.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOAT = ROOT / "tools" / "brote" / "float"
BUILD = FLOAT / "build-universal.sh"
MANIFEST = ROOT / "plugin" / "scripts" / "floatmanifest.py"
MAC = sys.platform == "darwin" and shutil.which("swiftc") is not None
sys.path.insert(0, str(ROOT / "plugin" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import brotecache


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(out, home):
    return sh(["bash", str(BUILD)], env={**os.environ, "BROTE_OUT": str(out), "HOME": str(home)}, timeout=900)


def thin(exe, arch, dest):
    r = sh(["lipo", str(exe), "-thin", arch, "-output", str(dest)])
    assert r.returncode == 0, r.stderr
    return dest


class ManifestUnit(unittest.TestCase):
    """floatmanifest.py on a made-up bundle: no compiler involved."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="float-man-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.src = self.d / "refsrc"           # the development sources the bundle's src/ was copied from
        self.src.mkdir()
        import floatmanifest as manifest
        self.m = manifest
        self.fd = self.d / "float"
        (self.fd / "src").mkdir(parents=True)
        for n in manifest.SRC_FILES:
            (self.src / n).write_text("source " + n)
            (self.fd / "src" / n).write_text("source " + n)
        (self.fd / "Brote.app/Contents/MacOS").mkdir(parents=True)
        (self.fd / "Brote.app/Contents/MacOS/Brote").write_bytes(b"\xcf\xfa binary")
        (self.fd / "Brote.app/Contents/Info.plist").write_text("plist")
        (self.fd / "BUILD-INFO.txt").write_text("swiftc test")
        manifest.write(str(self.fd))

    def test_clean_bundle_passes_with_and_without_sources(self):
        self.assertEqual(self.m.check(str(self.fd), str(self.src)), [])
        self.assertEqual(self.m.check(str(self.fd)), [])

    def test_manifest_is_sorted_two_space_format_and_lists_sources(self):
        lines = (self.fd / "MANIFEST.sha256").read_text().splitlines()
        self.assertEqual([l.split("  ", 1)[1] for l in lines], sorted(l.split("  ", 1)[1] for l in lines))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}  \S+", l) for l in lines))
        self.assertIn(hashlib.sha256(b"source brote.swift").hexdigest() + "  src/brote.swift", lines)

    def test_flipped_byte_in_the_binary_is_refused_by_name(self):
        p = self.fd / "Brote.app/Contents/MacOS/Brote"
        p.write_bytes(b"\xcf\xfb binary")
        self.assertEqual(self.m.check(str(self.fd)), ["MISMATCH Brote.app/Contents/MacOS/Brote"])

    def test_a_changed_script_resource_is_refused_too(self):
        (self.fd / "Brote.app/Contents/Info.plist").write_text("plist2")
        self.assertIn("MISMATCH Brote.app/Contents/Info.plist", self.m.check(str(self.fd)))

    def test_an_extra_file_in_the_bundle_is_a_mixed_bundle(self):
        (self.fd / "Brote.app/Contents/extra.js").write_text("x")
        self.assertEqual(self.m.check(str(self.fd)), ["EXTRA Brote.app/Contents/extra.js"])

    def test_a_missing_file_is_refused(self):
        (self.fd / "Brote.app/Contents/Info.plist").unlink()
        self.assertEqual(self.m.check(str(self.fd)), ["MISSING Brote.app/Contents/Info.plist"])

    def test_a_symlink_in_the_bundle_is_refused(self):
        os.symlink("/etc/hosts", self.fd / "Brote.app/Contents/link")
        self.assertEqual(self.m.check(str(self.fd)), ["SYMLINK Brote.app/Contents/link"])

    def test_a_symlinked_bundle_is_refused(self):
        real = self.d / "elsewhere"
        shutil.move(str(self.fd / "Brote.app"), str(real))
        os.symlink(real, self.fd / "Brote.app")
        self.assertTrue(self.m.check(str(self.fd)))

    def test_source_changed_after_the_build_is_stale(self):
        (self.src / "brote.swift").write_text("new source")
        self.assertEqual(self.m.check(str(self.fd), str(self.src)), ["STALE src/brote.swift (the source changed after the build)"])
        self.assertEqual(self.m.check(str(self.fd)), [], "without a reference folder the bundle alone is what is checked")

    def test_a_changed_copy_of_the_sources_inside_the_bundle_is_refused(self):
        (self.fd / "src" / "brote.swift").write_text("edited in place")
        self.assertEqual(self.m.check(str(self.fd)), ["MISMATCH src/brote.swift"])

    def test_an_unlisted_source_file_is_refused(self):
        (self.fd / "src" / "extra.swift").write_text("x")
        self.assertEqual(self.m.check(str(self.fd)), ["EXTRA src/extra.swift"])

    def test_missing_manifest_and_garbled_manifest_are_refused(self):
        (self.fd / "MANIFEST.sha256").write_text("not a manifest\n")
        self.assertIn("BAD MANIFEST.sha256 line 1", self.m.check(str(self.fd)))
        (self.fd / "MANIFEST.sha256").unlink()
        self.assertEqual(self.m.check(str(self.fd)), ["MISSING MANIFEST.sha256"])

    def test_path_tricks_in_the_manifest_are_refused(self):
        digest = "0" * 64
        (self.fd / "MANIFEST.sha256").write_text(f"{digest}  ../../etc/passwd\n{digest}  /abs/path\n")
        problems = self.m.check(str(self.fd))
        self.assertIn("BAD MANIFEST.sha256 line 1", problems)
        self.assertIn("BAD MANIFEST.sha256 line 2", problems)

    def test_a_duplicate_line_is_refused(self):
        lines = (self.fd / "MANIFEST.sha256").read_text().splitlines()
        (self.fd / "MANIFEST.sha256").write_text("\n".join(lines + [lines[0]]) + "\n")
        self.assertTrue(any(p.startswith("BAD MANIFEST.sha256 line") for p in self.m.check(str(self.fd))))

    def test_command_line_exit_codes(self):
        ok = sh([sys.executable, str(MANIFEST), "check", str(self.fd), str(self.src)])
        self.assertEqual((ok.returncode, ok.stdout), (0, ""))
        (self.fd / "Brote.app/Contents/Info.plist").write_text("tampered")
        bad = sh([sys.executable, str(MANIFEST), "check", str(self.fd)])
        self.assertEqual(bad.returncode, 1)
        self.assertIn("MISMATCH Brote.app/Contents/Info.plist", bad.stdout)
        self.assertEqual(sh([sys.executable, str(MANIFEST)]).returncode, 2)


@unittest.skipUnless(MAC, "needs macOS + swiftc")
@brotecache.skip_unless_changed("test_float_build.Universal", [FLOAT / n for n in (
    "brote.swift", "Info.plist", "sprites.js", "say.js", "personality.js", "director.js", "menu.json", "book-icon.json", "build-universal.sh")]
                                + [MANIFEST, Path(__file__).resolve()])
class Universal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="float-uni-"))
        cls.out1, cls.out2 = cls.tmp / "one" / "out", cls.tmp / "two" / "deeper" / "out"
        cls.home1, cls.home2 = cls.tmp / "home1", cls.tmp / "home2"
        for h in (cls.home1, cls.home2):
            h.mkdir()
        # R-870: the first build is shared through tests/brotecache.py (once per key); the reproducibility test below still builds a second one for real
        names = ("brote.swift", "Info.plist", "sprites.js", "say.js", "personality.js", "director.js", "menu.json", "book-icon.json", "build-universal.sh")
        cls.r1 = brotecache.cached_script_build(BUILD, [FLOAT / n for n in names] + [MANIFEST], None, [],
                                                env={**os.environ, "HOME": str(cls.home1)})
        if cls.r1.path:
            cls.out1 = cls.r1.path / "out"
        cls.app = cls.out1 / "Brote.app"
        cls.exe = cls.app / "Contents/MacOS/Brote"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_the_build_succeeds(self):
        self.assertEqual(self.r1.returncode, 0, self.r1.stdout[-1500:] + self.r1.stderr[-3000:])
        self.assertTrue(self.exe.exists())

    def test_both_architectures_are_in_the_binary(self):
        self.assertEqual(set(sh(["lipo", "-archs", str(self.exe)]).stdout.split()), {"arm64", "x86_64"})

    def test_each_slice_is_adhoc_signed_with_no_team_and_no_entitlements(self):
        v = sh(["codesign", "--verify", "--strict", "--deep", str(self.app)])
        self.assertEqual(v.returncode, 0, v.stderr)
        for arch in ("arm64", "x86_64"):
            d = sh(["codesign", "-dvvv", "--arch", arch, str(self.app)])
            self.assertIn("Signature=adhoc", d.stderr, arch)
            self.assertIn("TeamIdentifier=not set", d.stderr, arch)
            e = sh(["codesign", "-d", "--entitlements", "-", "--arch", arch, str(self.app)])
            self.assertNotIn("<key>", e.stdout + e.stderr, f"{arch} must carry no entitlements")

    def test_only_system_libraries_and_the_system_swift_runtime_path(self):
        for arch in ("arm64", "x86_64"):
            s = thin(self.exe, arch, self.tmp / f"t-{arch}")
            libs = sh(["otool", "-L", str(s)]).stdout.splitlines()[1:]
            for line in libs:
                self.assertRegex(line.strip(), r"^/(System/Library|usr/lib)/", f"{arch}: {line.strip()}")
            rpaths = re.findall(r"LC_RPATH\s+cmdsize \d+\s+path (\S+)", sh(["otool", "-l", str(s)]).stdout)
            self.assertEqual(rpaths, ["/usr/lib/swift"], arch)

    def test_no_home_or_build_path_inside_either_slice(self):
        for arch in ("arm64", "x86_64"):
            s = thin(self.exe, arch, self.tmp / f"s-{arch}")
            text = sh(["strings", "-a", str(s)]).stdout
            import lint
            self.assertEqual([m.group(0) for m in lint.PERSONAL.finditer(text) if m.group(1).lower() not in lint.PLACEHOLDERS], [], arch)
            for needle in (str(self.tmp), str(Path.home()), "brote-build."):
                self.assertNotIn(needle, text, arch)

    def test_the_manifest_lists_every_file_and_checks_clean(self):
        import floatmanifest as manifest
        self.assertEqual(manifest.check(str(self.out1), str(FLOAT)), [])
        text = (self.out1 / "MANIFEST.sha256").read_text()
        for must in ("Brote.app/Contents/MacOS/Brote", "Brote.app/Contents/Resources/director.js", "Brote.app/Contents/Resources/sprites.js",
                     "Brote.app/Contents/_CodeSignature/CodeResources", "BUILD-INFO.txt", "src/brote.swift", "src/build-universal.sh"):
            self.assertIn("  " + must + "\n", text)

    def test_build_info_records_the_toolchain(self):
        info = (self.out1 / "BUILD-INFO.txt").read_text()
        self.assertIn("source brote.swift sha256 " + hashlib.sha256((FLOAT / "brote.swift").read_bytes()).hexdigest(), info)
        self.assertRegex(info, r"swiftc .*Swift version")
        self.assertRegex(info, r"sdk \d+(\.\d+)* build \w+")
        self.assertRegex(info, r"\nld .*ld")

    def test_build_txt_names_the_source(self):
        stamp = (self.app / "Contents/Resources/build.txt").read_text().strip()
        self.assertEqual(stamp, hashlib.sha256((FLOAT / "brote.swift").read_bytes()).hexdigest()[:12])

    def test_tampering_a_copy_is_refused_and_the_original_is_untouched(self):
        copy = self.tmp / "copy"
        shutil.copytree(self.out1, copy, symlinks=True)
        exe = copy / "Brote.app/Contents/MacOS/Brote"
        data = bytearray(exe.read_bytes())
        data[100] ^= 1
        exe.write_bytes(bytes(data))
        r = sh([sys.executable, str(MANIFEST), "check", str(copy)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("MISMATCH Brote.app/Contents/MacOS/Brote", r.stdout)
        (copy / "Brote.app/Contents/Resources/director.js").write_text("// changed script")
        self.assertIn("MISMATCH Brote.app/Contents/Resources/director.js", sh([sys.executable, str(MANIFEST), "check", str(copy)]).stdout)
        self.assertEqual(sh([sys.executable, str(MANIFEST), "check", str(self.out1), str(FLOAT)]).returncode, 0)

    def test_the_binary_runs_its_selftest_for_this_machine(self):
        r = sh([str(self.exe), "--selftest", "--assets", str(FLOAT)], env={**os.environ, "TZ": "UTC"}, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:])
        self.assertIn("SELFTEST OK", r.stdout)

    def test_a_second_build_from_another_folder_and_home_is_byte_identical(self):
        """One real build, run once per cache key (R-870): it checks byte identity AND that a build replaces only its own four
        paths (precious.txt stays, Brote.app/stale goes). After it passes, a `repro-ok` marker is written into the cache entry;
        on a later hit with the marker this test skips naming the key. BUDGIT_TEST_FORCE_REPRO=1 forces the rerun (nightly, RC)."""
        why = brotecache.repro_skip_reason(self.r1)
        if why:
            self.skipTest(why)
        self.out2.mkdir(parents=True)
        (self.out2 / "precious.txt").write_text("mine")
        (self.out2 / "Brote.app").mkdir()
        (self.out2 / "Brote.app" / "stale").write_text("old")
        r2 = build(self.out2, self.home2)
        self.assertEqual(r2.returncode, 0, r2.stdout[-1500:] + r2.stderr[-3000:])
        self.assertEqual((self.out2 / "precious.txt").read_text(), "mine")
        self.assertFalse((self.out2 / "Brote.app" / "stale").exists())
        for arch in ("arm64", "x86_64"):
            a = thin(self.out1 / "Brote.app/Contents/MacOS/Brote", arch, self.tmp / f"a-{arch}").read_bytes()
            b = thin(self.out2 / "Brote.app/Contents/MacOS/Brote", arch, self.tmp / f"b-{arch}").read_bytes()
            self.assertEqual(hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest(), f"{arch} slice differs between builds")
        self.assertEqual((self.out1 / "MANIFEST.sha256").read_text(), (self.out2 / "MANIFEST.sha256").read_text(),
                         "the whole bundle, signature included, repeats")
        brotecache.mark_repro(self.r1)


class Refusals(unittest.TestCase):
    """The script refuses before it writes anything (no compiler needed)."""

    def run_build(self, env_extra, home=None):
        home = home or tempfile.mkdtemp(prefix="float-ref-")
        self.addCleanup(shutil.rmtree, home, True)
        return sh(["bash", str(BUILD)], env={**os.environ, "HOME": home, **env_extra}, timeout=60)

    def test_not_macos(self):
        out = tempfile.mkdtemp(prefix="float-out-")
        self.addCleanup(shutil.rmtree, out, True)
        r = self.run_build({"BROTE_FORCE_OS": "Linux", "BROTE_OUT": out})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("macOS only", r.stderr)
        self.assertEqual(os.listdir(out), [])

    @unittest.skipUnless(sys.platform == "darwin", "the xcode-select check runs first on a Mac")
    def test_missing_or_relative_or_dotted_out_folder(self):
        for bad in ("", "relative/path", "/tmp/../tmp/x"):
            r = self.run_build({"BROTE_OUT": bad})
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("BROTE_OUT", r.stderr, bad)

    @unittest.skipUnless(sys.platform == "darwin", "the xcode-select check runs first on a Mac")
    def test_never_builds_over_the_installed_copy(self):
        home = tempfile.mkdtemp(prefix="float-home-")
        self.addCleanup(shutil.rmtree, home, True)
        installed = Path(home) / "Library/Application Support/BroteWindow"
        installed.mkdir(parents=True)
        (installed / "Brote.app").mkdir()
        (installed / "Brote.app" / "keep").write_text("installed")
        r = self.run_build({"BROTE_OUT": str(installed)}, home=home)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("installed", r.stderr)
        self.assertEqual((installed / "Brote.app" / "keep").read_text(), "installed")


if __name__ == "__main__":
    unittest.main()
