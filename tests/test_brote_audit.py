"""R-114 (L-015 rule 4): the Brote sprite audit measures the art rules, so it must pass the approved mock and FAIL on
mock copies that break each rule (a floating pet, a hole in the body, a keyless colour, a grey outline, a non-integer
scale). Skipped where node is missing."""
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import ROOT

AUDIT = os.path.join(ROOT, "tools", "brote", "audit-sprites.mjs")
MOCK = os.path.join(ROOT, "docs", "design", "2026-10-21-r114-brote-v4.html")
MODULE = os.path.join(ROOT, "plugin", "hooks", "brote-client.js")


@unittest.skipUnless(shutil.which("node") and os.path.isfile(MOCK), "node or the mock is missing")
class BroteAuditTest(unittest.TestCase):
    def run_audit(self, html):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "mock.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return subprocess.run(["node", AUDIT, path], capture_output=True, text=True, timeout=60, cwd=ROOT)

    def mock(self):
        with open(MOCK, encoding="utf-8") as f:
            return f.read()

    def mutate(self, old, new):
        html = self.mock()
        self.assertEqual(html.count(old), 1, old)
        return html.replace(old, new)

    def test_the_approved_mock_passes(self):
        r = self.run_audit(self.mock())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS: 0 deviations", r.stdout)
        self.assertIn("grid 34x28", r.stdout)

    def test_a_floating_pet_fails_the_support_rule(self):
        r = self.run_audit(self.mutate("gy=OY+16", "gy=OY+18"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("DEVIATION support", r.stdout)

    def test_a_hole_in_the_body_fails_the_silhouette_rule(self):
        r = self.run_audit(self.mutate('"obbbbbbbbbbbbbbbBo",\n  "obbbbbbbbbbbbbbbBo",',
                                       '"ob.bbbbbbbbbbbbbBo",\n  "obbbbbbbbbbbbbbbBo",'))
        self.assertEqual(r.returncode, 1)
        self.assertIn("DEVIATION silhouette", r.stdout)

    def test_a_colour_key_without_a_colour_fails_the_palette_rule(self):
        r = self.run_audit(self.mutate("smile:[[10,7,'m']", "smile:[[10,7,'Q']"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("DEVIATION palette", r.stdout)

    def test_a_grey_outline_fails_the_palette_rule(self):
        r = self.run_audit(self.mutate("agotado: {o:'#a89cbb'", "agotado: {o:'#a0a0a0'"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("grey", r.stdout)

    def test_a_non_integer_scale_fails_the_unit_rule(self):
        r = self.run_audit(self.mutate(".card canvas{width:136px;height:112px}", ".card canvas{width:140px;height:112px}"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("DEVIATION unit", r.stdout)

    def test_a_sprite_pushed_off_the_grid_fails_the_unit_rule(self):
        r = self.run_audit(self.mutate("const W=34, H=28, OX=8, OY=10;", "const W=34, H=28, OX=8, OY=20;"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("DEVIATION unit", r.stdout)

    # --- the shipped surface module (what users actually get)
    def run_module(self, js):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "brote-client.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(js)
        return subprocess.run(["node", AUDIT, path], capture_output=True, text=True, timeout=60, cwd=ROOT)

    def module(self):
        with open(MODULE, encoding="utf-8") as f:
            return f.read()

    def test_the_shipped_module_passes_including_the_pointing_gesture(self):
        r = self.run_module(self.module())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS: 0 deviations", r.stdout)

    def test_a_shipped_module_with_a_floating_pet_fails(self):
        js = self.module()
        self.assertEqual(js.count("gy=OY+16"), 1)
        r = self.run_module(js.replace("gy=OY+16", "gy=OY+18"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("DEVIATION support", r.stdout)

    def test_the_shipped_module_draws_the_same_pixel_output_as_the_approved_mock(self):
        cmp = os.path.join(ROOT, "tools", "brote", "compare-frames.mjs")
        r = subprocess.run(["node", cmp], capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("identical to the approved mock", r.stdout)
        js = self.module()
        self.assertEqual(js.count("e:'#4b3a57'"), 1)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "brote-client.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(js.replace("e:'#4b3a57'", "e:'#4b3a58'"))
        r = subprocess.run(["node", cmp, MOCK, path], capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stdout)

    def test_one_frame_takes_under_8_ms(self):
        r = subprocess.run(["node", os.path.join(ROOT, "tools", "brote", "perf-frame.mjs")], capture_output=True, text=True,
                           timeout=60, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(subprocess.run(["node", os.path.join(ROOT, "tools", "brote", "perf-frame.mjs"), "--max-ms", "0.0001"],
                                        capture_output=True, text=True, timeout=60, cwd=ROOT).returncode, 1)

    def test_behaviour_checks_pass_and_fail_on_their_mutations(self):
        chk = os.path.join(ROOT, "tools", "brote", "behaviour-check.mjs")
        r = subprocess.run(["node", chk], capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        js = self.module()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for old, new, word in (("Math.round((this.k+0.1)*10)/10", "this.k+0.08", "exactly 10"),
                               ("if (stillNow) return", "if (props.still) return", "freeze")):
            self.assertEqual(js.count(old), 1, old)
            path = os.path.join(d, "m.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(js.replace(old, new))
            r = subprocess.run(["node", chk, path], capture_output=True, text=True, timeout=60, cwd=ROOT)
            self.assertEqual(r.returncode, 1, old)
            self.assertIn(word, r.stdout)


if __name__ == "__main__":
    unittest.main()


BOOK = os.path.join(ROOT, "tools", "brote", "float", "book-icon.json")


@unittest.skipUnless(shutil.which("node") and os.path.isfile(BOOK), "node or the book icon data is missing")
class BookAuditTest(unittest.TestCase):
    """R-153 B6 / L15: the book and the panel art are measured the same way as Brote (box 20 x 28, base row 25, shadow row 26,
    outline saturation, 8 x 8 particle-class icons, the 62 x 5 bar), and the audit FAILS on copies that break each rule."""

    def audit(self, data=None):
        import json
        path = BOOK
        if data is not None:
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            path = os.path.join(d, "book-icon.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        return subprocess.run(["node", AUDIT, "--book", path], capture_output=True, text=True, timeout=60, cwd=ROOT)

    def data(self):
        import json
        with open(BOOK, encoding="utf-8") as f:
            return json.load(f)

    def test_the_shipped_icon_data_passes(self):
        r = self.audit()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("book audit: 0 deviations", r.stdout)

    def test_a_wrong_box_height_fails(self):
        d = self.data(); d["box"]["h"] = 27
        self.assertEqual(self.audit(d).returncode, 1)

    def test_a_floating_book_fails(self):
        d = self.data(); d["parts"]["closed"]["y"] -= 1
        self.assertEqual(self.audit(d).returncode, 1)

    def test_a_panel_icon_that_is_not_8_by_8_fails(self):
        d = self.data(); d["panel_icons"]["done"] = d["panel_icons"]["done"][:-1]
        r = self.audit(d)
        self.assertEqual(r.returncode, 1)
        self.assertIn("panel", r.stdout)

    def test_a_panel_icon_with_an_outline_fails(self):
        d = self.data(); d["panel_icons"]["next"][1] = "...oo..."
        self.assertEqual(self.audit(d).returncode, 1)

    def test_a_key_with_no_colour_fails(self):
        d = self.data(); d["panel_icons"]["doing"][0] = "..Q..l.."
        self.assertEqual(self.audit(d).returncode, 1)

    def test_a_progress_bar_of_the_wrong_size_fails(self):
        d = self.data(); d["progress_bar"]["w"] = 61
        self.assertEqual(self.audit(d).returncode, 1)
