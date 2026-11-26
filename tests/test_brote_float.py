"""R-151: the floating Brote (tools/brote/float). GUI-free gates only: nothing here opens a window.

Run from the repo root:  python3 -m unittest discover -s tests -p 'test_brote_float.py'
The Swift pieces are skipped when this is not macOS or there is no swiftc; the drift and hygiene gates always run.
"""
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brotecache

ROOT = Path(__file__).resolve().parents[1]
FLOAT = ROOT / "tools" / "brote" / "float"
SWIFTC = shutil.which("swiftc")
MAC = sys.platform == "darwin" and SWIFTC is not None
NODE = shutil.which("node")
ICON = "book-icon.json"
ICON_SRC = ROOT / "docs" / "design" / "2026-10-22-r153-book-icon.json"
UTC = {**os.environ, "TZ": "UTC"}

BANNED_SWIFT = [  # an API that asks the user for a permission, runs a shell, or reaches the network / other apps' content
    "AXIsProcessTrustedWithOptions", "kAXTrustedCheckOptionPrompt", "CGRequestScreenCaptureAccess", "CGRequestListenEventAccess",
    "/bin/sh", "/bin/zsh", "/bin/bash", "URLSession", "NSAppleScript", "osascript", "AEDeterminePermissionToAutomateTarget",
    "CGWindowListCreateImage", "SCShareableContent", "kCGWindowName", "kCGWindowOwnerName", "SMAppService", "addGlobalMonitorForEvents(matching: .keyDown",
    "keyDown", "keyUp", "flagsChanged", "CGEventTap", "launchApplication",
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=kw.pop("cwd", FLOAT), **kw)


@unittest.skipUnless(NODE, "node not found")
class Drift(unittest.TestCase):
    def test_exported_copies_are_current(self):
        r = run([NODE, "export.mjs", "--check"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_changed_plugin_section_is_caught(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "tools/brote/float").mkdir(parents=True)
            (tmp / "plugin/hooks").mkdir(parents=True)
            for n in ("export.mjs", "sprites.js", "say.js", "menu.json", ICON):
                shutil.copy(FLOAT / n, tmp / "tools/brote/float" / n)
            (tmp / "plugin/scripts").mkdir(parents=True)
            shutil.copy(ROOT / "plugin/scripts/i18n.py", tmp / "plugin/scripts/i18n.py")
            (tmp / "docs/design").mkdir(parents=True)
            shutil.copy(ICON_SRC, tmp / "docs/design" / ICON_SRC.name)
            for n in ("brote-client.js", "register.jsx"):
                shutil.copy(ROOT / "plugin/hooks" / n, tmp / "plugin/hooks" / n)
            ok = run([NODE, "export.mjs", "--check"], cwd=tmp / "tools/brote/float")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            for name, old, new in (("brote-client.js", "const W=34, H=28", "const W=35, H=28"), ("register.jsx", "t.days[1]", "t.days[2]")):
                p = tmp / "plugin/hooks" / name
                text = p.read_text()
                self.assertIn(old, text)
                p.write_text(text.replace(old, new, 1))
                bad = run([NODE, "export.mjs", "--check"], cwd=tmp / "tools/brote/float")
                self.assertEqual(bad.returncode, 1, name + " drift not caught")
                self.assertIn("DRIFT", bad.stderr)
                p.write_text(text)

    def test_golden_lines_are_current(self):
        before = (FLOAT / "tests/golden-say.json").read_text()
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "tools/brote/float/tests").mkdir(parents=True)
            (tmp / "plugin/hooks").mkdir(parents=True)
            (tmp / "plugin/scripts").mkdir(parents=True)
            shutil.copy(ROOT / "plugin/scripts/i18n.py", tmp / "plugin/scripts/i18n.py")   # the one catalog the goldens use
            shutil.copy(FLOAT / "export.mjs", tmp / "tools/brote/float/export.mjs")
            for n in ("brote-client.js", "register.jsx"):
                shutil.copy(ROOT / "plugin/hooks" / n, tmp / "plugin/hooks" / n)
            r = run([NODE, "export.mjs", "--golden"], cwd=tmp / "tools/brote/float", env=UTC)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((tmp / "tools/brote/float/tests/golden-say.json").read_text(), before)
        golden = json.loads(before)
        self.assertGreaterEqual(len(golden["cases"]), 60)
        self.assertEqual(sorted(golden["strings"]), ["en", "es"])
        self.assertEqual({c["lang"] for c in golden["cases"]}, {"en", "es"})


class Hygiene(unittest.TestCase):
    def test_a_changed_icon_source_or_copy_is_caught(self):
        # R-153 task 2: the book/panel art is data copied byte for byte; either side drifting fails --check
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "tools/brote/float").mkdir(parents=True)
            (tmp / "plugin/hooks").mkdir(parents=True)
            (tmp / "docs/design").mkdir(parents=True)
            for n in ("export.mjs", "sprites.js", "say.js", "menu.json", ICON):
                shutil.copy(FLOAT / n, tmp / "tools/brote/float" / n)
            (tmp / "plugin/scripts").mkdir(parents=True)
            shutil.copy(ROOT / "plugin/scripts/i18n.py", tmp / "plugin/scripts/i18n.py")
            for n in ("brote-client.js", "register.jsx"):
                shutil.copy(ROOT / "plugin/hooks" / n, tmp / "plugin/hooks" / n)
            shutil.copy(ICON_SRC, tmp / "docs/design" / ICON_SRC.name)
            self.assertEqual(run([NODE, "export.mjs", "--check"], cwd=tmp / "tools/brote/float").returncode, 0)
            for p in (tmp / "tools/brote/float" / ICON, tmp / "docs/design" / ICON_SRC.name):
                text = p.read_text()
                p.write_text(text.replace('"version": 1', '"version": 2', 1))
                bad = run([NODE, "export.mjs", "--check"], cwd=tmp / "tools/brote/float")
                self.assertEqual(bad.returncode, 1, str(p) + " drift not caught")
                self.assertIn("DRIFT", bad.stderr)
                p.write_text(text)

    def test_build_sh_is_small_and_clean(self):
        text = (FLOAT / "build.sh").read_text()
        self.assertLessEqual(len(text.splitlines()), 32)
        self.assertIn("set -euo pipefail", text)
        for bad in (r"\bcurl\b", r"\bwget\b", r"\bsudo\b", r"\beval\b", r"\bxattr\b", r"\bgit\b", r"\bnpm\b", r"\bpip3?\b", r"\bnode\b"):
            self.assertIsNone(re.search(bad, text), "build.sh must not use " + bad)
        for m in re.finditer(r"rm\s+-\S*r\S*\s+(?:--\s+)?(\S+)", text):
            self.assertEqual(m.group(1), '"$app"', "rm of anything but the bundle: " + m.group(0))
        self.assertIn("export PATH=/usr/bin:/bin", text)
        self.assertIn("must be an absolute path", text)
        self.assertIn("codesign --force --sign -", text)
        self.assertIn("cp sprites.js say.js personality.js director.js menu.json " + ICON, text)      # the art data travels in Resources
        self.assertTrue(os.access(FLOAT / "build.sh", os.X_OK))

    def test_build_sh_stops_with_one_line_off_macos(self):
        r = run(["./build.sh"], env={**os.environ, "BROTE_FORCE_OS": "Linux", "BROTE_OUT": tempfile.mkdtemp(prefix="brote-no-")})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip().splitlines(), ["Brote's floating window is macOS only."])
        self.assertEqual(r.stdout, "")

    def test_the_menu_words_come_from_the_catalog_not_from_swift(self):
        src = (FLOAT / "brote.swift").read_text()
        self.assertNotIn("Salir", src)
        table = json.loads((FLOAT / "menu.json").read_text())
        self.assertEqual(sorted(table), ["en", "es"])
        self.assertEqual(table["en"], "Quit Brote")
        self.assertLessEqual(len(table["en"]), len(table["es"]))         # the English title fits where the Spanish one did

    def test_swift_source_uses_no_prompting_or_shell_api(self):
        src = (FLOAT / "brote.swift").read_text()
        for bad in BANNED_SWIFT:
            self.assertNotIn(bad, src, "banned in brote.swift: " + bad)
        self.assertRegex(src, r"/usr/bin/python3")           # the one child, by absolute path
        self.assertNotRegex(src, r"(?<!\.claude-)plugin/(?!bin/budgit)[A-Za-z_/]*\.(py|json|md)")   # reads nothing else of the plugin

    def test_the_only_link_open_is_the_chat_link_and_the_only_command_run_follows_her_yes(self):
        # R-239 / R-241: one `NSWorkspace.shared.open(` and it sits behind the hand-test flag; `claude plugin ...` runs from runUpdate, which only askUpdate calls
        code = re.sub(r"//.*", "", (FLOAT / "brote.swift").read_text())
        self.assertEqual(code.count("NSWorkspace.shared.open("), 1)
        self.assertIn("if K.chatOpensByLink, let link = chatLink(prefix: sid", code)
        self.assertEqual(code.count("runUpdate("), 2)                       # its definition and the one call in askUpdate
        ask = code[code.index("func askUpdate()"):code.index("func runUpdate(")]
        self.assertLess(ask.index("a.runModal()"), ask.index("runUpdate(o)"))
        self.assertIn("if yesPicked { runUpdate(o) }", ask)
        self.assertNotIn("Process()", code.replace("let p = Process(), pipe = Pipe()", ""))      # runChild stays the one place a child starts

    def test_there_is_no_card_above_him(self):
        # R-166 / R-172: no text surface in the floating app; the data line only changes his body
        code = re.sub(r"//.*", "", (FLOAT / "brote.swift").read_text())
        for gone in ("CardView", "cardRect", "updateCard", "cardPanel", "cardMaxW", "cardGap", "cardLines", "headRow"):
            self.assertNotIn(gone, code, gone + " must be gone")

    def test_the_book_is_a_second_body_above_him_that_writes_two_numbers(self):
        # R-153 task 3: window order panel < him < book, one landing = two numbers, the selftest keeps a case per book function
        src = (FLOAT / "brote.swift").read_text()
        a, b = src.index("// MARK: - the self-test"), src.index("// MARK: - the AppKit shell")
        prod, selftest = re.sub(r"//.*", "", src[:a] + src[b:]), src[a:b]
        self.assertRegex(prod, r"bookPanel\.level = NSWindow\.Level\(rawValue: NSWindow\.Level\.floating\.rawValue \+ 1\)")
        self.assertEqual(re.findall(r'setNumber\("book[XY]"', prod), ['setNumber("bookX"', 'setNumber("bookY"'])   # only BookPlace.land writes them
        self.assertNotIn("bookX", re.sub(r"final class BookPlace.*?\n}\n", "", prod, flags=re.S).replace('store.number("bookX")', ""))
        for fn in ("bookSpawn", "bookBounds", "bookClamp", "bookRelease", "bookLook", "bookRibbon", "isBookPress", "pressTarget", "bookOnLeft",
                   "faceFlip", "spriteStale", "Dragger()", "BookPlace(", "BookIcon(", "frameInfo()", "palette("):
            self.assertIn(fn, selftest, fn + " lost its selftest case")
        self.assertNotIn("static let bookX", src)

    def test_the_libreta_panel_sits_below_both_bodies_and_follows_the_system(self):
        # R-153 task 4: panel level 2 < him (3) < book (4); never the key; semantic colours; the pure parts keep a selftest case
        src = (FLOAT / "brote.swift").read_text()
        a, b = src.index("// MARK: - the self-test"), src.index("// MARK: - the AppKit shell")
        prod, selftest = re.sub(r"//.*", "", src[:a] + src[b:]), src[a:b]
        self.assertRegex(prod, r"p\.level = NSWindow\.Level\(rawValue: 2\)")
        self.assertRegex(prod, r"override var canBecomeKey: Bool \{ false \}")
        shell = prod[prod.index("final class LibretaContent"):prod.index("func makeImage")]
        self.assertNotRegex(shell, r"NSColor\((?:red|white|calibrated|srgb|deviceRed)")                  # semantic colours only
        self.assertNotRegex(shell, r"NSColor\.(?:black|white)\b")
        for fn in ("libretaRect", "libretaOpaque", "libretaModel", "barFill", "barGrid", "capTitle", "autoTitle", "iconGrid"):
            self.assertIn(fn + "(", selftest, fn + " lost its selftest case")
        # review fixes: one 3 pt unit for the portrait and the icons; the open panel is not chased by the book; the slide is not cut by the poll
        self.assertRegex(prod, r"let pw = 34 \* px, ph = 28 \* px")
        self.assertIn("8 * px", prod)
        repo = prod[prod.index("func repositionLibreta"):prod.index("func libretaOverPointer")]
        self.assertIn("layout.claude == nil", repo)
        self.assertIn("libretaTarget", repo)
        self.assertIn('"P17"', prod)
        self.assertRegex(prod, r'"panel"')

    def test_swift_writes_only_its_own_places(self):
        src = (FLOAT / "brote.swift").read_text()
        a, b = src.index("// MARK: - the self-test"), src.index("// MARK: - entry")
        prod = src[:a] + src[b:]                      # the self-test writes only to its own temp folder
        code = re.sub(r"//.*", "", prod)
        writes = re.findall(r"(?:write\(to|createFile|removeItem|createDirectory|moveItem|copyItem|\.write\()", code)
        self.assertLessEqual(len(writes), 4, writes)  # the probe log: createDirectory, createFile, one append; nothing else
        # the only plugin paths named: the script it runs and the two folders hubTrusted checks (bin, scripts)
        named = code.replace(" plugin/bin/budgit", "")
        for ok in ('"plugin/bin/budgit"', '"plugin/bin"', '"plugin/scripts"', '"/.claude-plugin/marketplace.json"'):   # R-317: the one other file of the plugin's world it reads, to see which folder a directory marketplace means
            named = named.replace(ok, "")
        self.assertNotIn("plugin/", named)
        self.assertNotIn(".studio", code)
        self.assertNotIn("/.budgit", code)

    def test_info_plist(self):
        p = plistlib.loads((FLOAT / "Info.plist").read_bytes())
        self.assertEqual(p["CFBundleIdentifier"], "es.budgit.brote.float")
        self.assertIs(p["LSUIElement"], True)
        self.assertEqual(p["CFBundleExecutable"], "Brote")
        self.assertFalse([k for k in p if k.endswith("UsageDescription")], "no permission strings")


BROTE_INPUTS = [FLOAT / n for n in ("brote.swift", "Info.plist", "sprites.js", "say.js", "personality.js", "director.js", "menu.json",
                                    "book-icon.json", "build.sh")] + [Path(__file__).resolve()]


@unittest.skipUnless(MAC, "needs macOS + swiftc")
@brotecache.skip_unless_changed("test_brote_float.Core", BROTE_INPUTS)
class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="brote-core-")
        # R-870: compiled once per (source, swiftc version, flags) and shared through tests/brotecache.py
        cls.build = brotecache.cached_swiftc(["-D", "CORE_ONLY", "-O", "-swift-version", "5", "brote.swift", "-framework", "JavaScriptCore"],
                                             [FLOAT / "brote.swift"], FLOAT, swiftc=SWIFTC, out_name="core")
        cls.exe = (cls.build.path / "core") if cls.build.path else Path(cls.tmp) / "core"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_core_compiles(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr[-3000:])

    def test_core_links_no_gui_framework(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr[-3000:])
        libs = subprocess.run(["otool", "-L", str(self.exe)], capture_output=True, text=True).stdout
        for gui in ("AppKit", "QuartzCore", "SwiftUI", "CoreGraphics.framework/Versions/A/CoreGraphics"):
            if gui == "CoreGraphics.framework/Versions/A/CoreGraphics":
                continue     # Foundation pulls CoreGraphics in; only the window frameworks are the gate
            self.assertNotIn(gui, libs)
        self.assertIn("JavaScriptCore", libs)

    def test_selftest_passes(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr[-3000:])
        r = subprocess.run([str(self.exe), "--selftest", "--assets", str(FLOAT)], capture_output=True, text=True, env=UTC, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])
        self.assertRegex(r.stdout, r"SELFTEST OK \d+ checks")
        self.assertNotIn("FAIL", r.stdout)
        n = int(re.search(r"SELFTEST OK (\d+) checks", r.stdout).group(1))
        self.assertGreaterEqual(n, 120, "the selftest must keep its breadth")

    def test_selftest_reports_its_own_absence(self):
        """A selftest pointed at an empty assets folder must FAIL, not pass with nothing run."""
        self.assertEqual(self.build.returncode, 0, self.build.stderr[-3000:])
        with tempfile.TemporaryDirectory() as empty:
            r = subprocess.run([str(self.exe), "--selftest", "--assets", empty], capture_output=True, text=True, env=UTC, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FAIL", r.stdout)


@unittest.skipUnless(MAC, "needs macOS + swiftc")
@brotecache.skip_unless_changed("test_brote_float.Bundle", BROTE_INPUTS)
class Bundle(unittest.TestCase):
    def test_build_makes_a_signed_bundle(self):
        # R-870: build.sh runs once per key (all files it reads + swiftc version); the --hub line is checked by the exported-copy test
        r = brotecache.cached_script_build(FLOAT / "build.sh", [FLOAT / n for n in ("brote.swift", "Info.plist", "sprites.js", "say.js", "personality.js",
                                                                                   "director.js", "menu.json", "book-icon.json")], None, [], swiftc=SWIFTC)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-3000:])
        if not r.hit:
            self.assertIn("--hub", r.stdout)
        out = r.path / "out"
        app = Path(out) / "Brote.app"
        exe = app / "Contents/MacOS/Brote"
        self.assertTrue(exe.exists())
        for n in ("sprites.js", "say.js", "personality.js", "director.js", "menu.json"):
            self.assertEqual((app / "Contents/Resources" / n).read_bytes(), (FLOAT / n).read_bytes())
        import hashlib
        stamp = (app / "Contents/Resources/build.txt").read_text().strip()
        self.assertEqual(stamp, hashlib.sha256((FLOAT / "brote.swift").read_bytes()).hexdigest()[:12], "build.txt names the source that built the bundle")
        v = subprocess.run(["codesign", "--verify", "--strict", "--verbose=2", str(app)], capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stderr)
        st = subprocess.run([str(exe), "--selftest", "--assets", str(FLOAT)], capture_output=True, text=True, env=UTC, timeout=120)
        self.assertEqual(st.returncode, 0, "the full binary's own selftest (no window is made): " + st.stdout[-1500:])
        libs = subprocess.run(["otool", "-L", str(exe)], capture_output=True, text=True).stdout
        self.assertNotRegex(libs, r"@rpath|/usr/local|/opt/")
        self.assertIn("AppKit", libs)
        tracked = subprocess.run(["git", "status", "--porcelain", "tools/brote/float/build"], capture_output=True, text=True, cwd=ROOT).stdout
        self.assertEqual(tracked.strip(), "", "build/ (module cache) must be ignored")


class OneHomeSourceGuard(unittest.TestCase):
    """R-589 (spec: every way home SHALL use only effectiveHome). Source guard: explore, goHome, returnIfStray, guardZone and wanderLimits
    never use the saved home (`homeSpot()`) as a place to go; they route through homeTarget / effectiveHome (directly or via goHome / ZoneCtl.tick)."""
    SRC = Path(os.environ.get("BROTE_SWIFT_SRC", str(FLOAT / "brote.swift")))
    ROUTES = {  # function -> at least one of these must appear in its body
        "explore": ("homeTarget(",),
        "goHome": ("homeTarget(",),
        "returnIfStray": ("homeTarget(",),
        "guardZone": ("zc.tick(", "goHome(", "homeTarget("),
        "wanderLimits": ("effectiveHome(",),
    }
    # ALLOW LIST (round 8). Inside the five functions, the names `home` / `saved` and any direct homeSpot() / savedHomeTarget() call
    # may appear ONLY as an argument (at any depth) of one of these calls. Anything else is a violation, whatever it looks like.
    ALLOWED_CALLS = ("zc.zone", "zc.tick", "effectiveHome", "livesAt", "visible")      # visible only as visible(for: ...)
    # Three non-destination shapes, also allowed: the binding `let home|saved = layout.homeSpot()` (alone on its line),
    # the receiver `saved.flatMap {` whose closure calls effectiveHome, and a bare `!= nil` existence test.
    USE = re.compile(r"\b(?:home|saved)\b|\bhomeSpot\(\)|\bsavedHomeTarget\(\)")

    @staticmethod
    def body(text, name):
        m = re.search(r"\bfunc " + name + r"\(", text)
        assert m, "func %s not found in brote.swift" % name
        i = text.index("{", m.end())
        depth, j = 0, i
        while j < len(text):
            depth += (text[j] == "{") - (text[j] == "}")
            j += 1
            if depth == 0:
                break
        return m.start(), j

    @staticmethod
    def blank(text):
        """Comments and string literals become spaces, so offsets and line numbers stay the same."""
        text = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), text)
        return re.sub(r'"(?:[^"\\\n]|\\.)*"', lambda m: " " * len(m.group(0)), text)

    @classmethod
    def violations(cls, text):
        """List of (function, line number, line text) for every use of the saved home that is not on the allow list."""
        code = cls.blank(text)
        out = []
        for name in cls.ROUTES:
            a, b = cls.body(code, name)
            body = code[a:b]
            effective_home_local = bool(re.search(r"\blet home = saved\.flatMap \{[^\n]*\beffectiveHome\(", body))
            for m in cls.USE.finditer(body):
                p, q = m.start(), m.end()
                tok = m.group(0)
                before, after = body[:p], body[q:]
                line_start = before.rfind("\n") + 1
                line_end = body.find("\n", q)
                line = body[line_start:line_end if line_end >= 0 else len(body)]
                ok = False
                if tok in ("home", "saved") and re.match(r":(?!:)", after):
                    ok = True      # an argument label, not a value
                elif re.search(r"\blet $", before[line_start:]) and tok in ("home", "saved"):
                    ok = True      # the name being bound
                elif re.fullmatch(r"\s*let (?:home|saved) = layout\.homeSpot\(\)\s*", line):
                    ok = True      # the one binding
                elif re.match(r"\s*!=\s*nil\b", after):
                    ok = True      # only asks whether there is a saved home
                elif tok == "home" and effective_home_local:
                    ok = True      # this `home` is the effectiveHome result, not the saved spot
                elif tok == "saved" and re.match(r"\.flatMap \{[^\n]*\beffectiveHome\(", after):
                    ok = True      # saved.flatMap { effectiveHome(...) }
                elif tok == "saved" and re.search(r"\blet home = saved\.flatMap \{[^\n]*\beffectiveHome\([^\n]*\} \?\? $", before[line_start:]):
                    ok = True      # its fallback when effectiveHome has no free spot at all
                else:
                    stack = []
                    for i, ch in enumerate(body[:p]):
                        if ch in "([":
                            stack.append(i)
                        elif ch in ")]" and stack:
                            stack.pop()
                    if stack and body[stack[-1]] == "(":
                        callee = re.search(r"([\w.]+)$", body[:stack[-1]])
                        callee = callee.group(1) if callee else ""
                        last = callee.split(".")[-1]
                        if (callee in ("zc.zone", "zc.tick") or last in ("effectiveHome", "livesAt")
                                or (last == "visible" and body[stack[-1] + 1:].startswith("for:"))):
                            ok = True
                if not ok:
                    out.append((name, code.count("\n", 0, a + p) + 1, text.splitlines()[code.count("\n", 0, a + p)].strip()))
        return out

    def test_every_way_home_goes_through_the_one_home(self):
        text = self.SRC.read_text(encoding="utf-8")
        code = self.blank(text)
        for name, routes in self.ROUTES.items():
            a, b = self.body(code, name)
            self.assertTrue(any(r in code[a:b] for r in routes), "%s must route through %s" % (name, " / ".join(routes)))
        bad = self.violations(text)
        self.assertEqual(bad, [], "; ".join("%s line %d uses the saved home outside zc.zone / zc.tick / effectiveHome / livesAt / visible(for: : %s" % v for v in bad))

    def test_the_guard_rejects_each_way_home_mutant(self):
        """Replay (in memory) of the mutants that got past the deny-list guard: each must be refused, naming the right function."""
        text = self.SRC.read_text(encoding="utf-8")
        self.assertEqual(self.violations(text), [])
        mutants = {
            "m5 explore targets the saved home": ("let back = target?.pt ?? layout.pos", "let back = layout.homeSpot() ?? layout.pos", "explore"),
            "m6 guardZone flies into the saved home": ("startRoute([.fly(to: spot)]", "startRoute([.fly(to: home ?? spot)]", "guardZone"),
            "m7 goHome uses the saved target": ("guard let t = homeTarget() else { return false }", "guard let t = savedHomeTarget() else { return false }", "goHome"),
            "m8 wanderLimits wanders round the saved home": ("home: home, zone: zc.zone(noGo, home: saved))", "home: saved, zone: zc.zone(noGo, home: saved))", "wanderLimits"),
        }
        for label, (old, new, func) in mutants.items():
            self.assertEqual(text.count(old), 1, "%s: the mutation site changed, update the replay" % label)
            bad = self.violations(text.replace(old, new))
            self.assertIn(func, [v[0] for v in bad], "%s: the guard stayed green" % label)


@unittest.skipUnless(os.path.exists("/usr/bin/python3"), "no system python3")
class DataChild(unittest.TestCase):
    def test_the_real_argv_leaves_no_file_anywhere(self):
        """E8: the exact argv + env the app uses, against a copy of plugin/ and a copy of the real ledger, with HOME in the temp
        folder, adds no file at all (no __pycache__, nothing under ~/.studio) and answers with a status object."""
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            shutil.copytree(ROOT / "plugin", tmp / "plugin", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            hub = tmp / "hub"
            hub.mkdir()
            ledger = ROOT / ".studio"
            if (ledger / "ledger.json").exists():
                (hub / ".studio").mkdir()
                for n in ("ledger.json", "ledger.md", "ledger.sha256"):
                    if (ledger / n).exists():
                        shutil.copy(ledger / n, hub / ".studio" / n)
            before = sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*"))
            env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp), "LANG": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
            r = subprocess.run(["/usr/bin/python3", "-B", str(tmp / "plugin/bin/budgit"), "status", "--brote", "--project", str(hub)],
                               capture_output=True, text=True, env=env, timeout=30, cwd="/")
            after = sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*"))
            self.assertEqual([p for p in after if p not in before], [], "HOME was the temp folder: any write of the child would show here")
            if (ledger / "ledger.json").exists():
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("state", json.loads(r.stdout), "the happy path ran, not an early exit")


if __name__ == "__main__":
    unittest.main()
