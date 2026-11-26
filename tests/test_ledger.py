import json, os
from helpers import StudioTestCase
import studiolib as sl


class LedgerTest(StudioTestCase):
    def test_add_and_render(self):
        data = sl.empty_ledger()
        it = sl.add_item(data, "Foto de la nevera | varias", ["I-1"], phase="validate")
        self.assertEqual(it["id"], "R-1")
        self.assertEqual(it["status"], "open")
        self.assertIn("Foto de la nevera \\| varias", sl.render_md(data))

    def test_new_ids_are_max_plus_one_after_legacy_numbers(self):
        data = sl.empty_ledger()
        sl.add_item(data, "legacy zero", ["legacy"], item_id="R-0")
        sl.add_item(data, "legacy nine", ["legacy"], item_id="R-9")
        self.assertEqual(sl.add_item(data, "new", ["I-1"])["id"], "R-10")
        with self.assertRaises(sl.StudioError):
            sl.add_item(data, "dup", ["I-2"], item_id="R-9")

    def test_done_requires_evidence(self):
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "x", ["I-1"], outcome="You see it closed")
        with self.assertRaises(sl.StudioError):
            sl.set_status(self.project, data, "R-1", "done")
        sl.set_status(self.project, data, "R-1", "done", evidence="tests pass: 12 ok")
        self.assertEqual(data["items"][0]["evidence"], "tests pass: 12 ok")

    def test_verified_requires_pass_report(self):
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "x", ["I-1"])
        with self.assertRaises(sl.StudioError):
            sl.set_status(self.project, data, "R-1", "verified")
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        os.makedirs(vdir)
        with open(os.path.join(vdir, "R-1.md"), "w") as f:
            f.write("FAIL\nbroken\n")
        with self.assertRaises(sl.StudioError):
            sl.set_status(self.project, data, "R-1", "verified")
        with open(os.path.join(vdir, "R-1.md"), "w") as f:
            f.write("PASS\nall EARS checks green\n")
        with self.assertRaises(sl.StudioError):               # PASS but not written by studio:verifier
            sl.set_status(self.project, data, "R-1", "verified")
        sl.write_seal(os.path.join(vdir, "R-1.md"))
        sl.set_status(self.project, data, "R-1", "verified")
        self.assertEqual(data["items"][0]["status"], "verified")

    def test_drop_requires_user_input_reference(self):
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "x", ["I-1"])
        with self.assertRaises(sl.StudioError):
            sl.set_status(self.project, data, "R-1", "dropped")
        with self.assertRaises(sl.StudioError):  # I-9 does not exist in inbox
            sl.set_status(self.project, data, "R-1", "dropped", by_user="I-9")

    def test_save_load_roundtrip_and_tamper(self):
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "ñandú con acentos", ["I-1"])
        sl.save_ledger(self.project, data)
        self.assertFalse(sl.ledger_tampered(self.project))
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["text"], "ñandú con acentos")
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        raw = json.load(open(path, encoding="utf-8"))
        raw["items"][0]["text"] = "rewritten by hand"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        self.assertTrue(sl.ledger_tampered(self.project))

    def test_accept_edit_keeps_previous_and_rehashes(self):
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "original", ["I-1"])
        sl.save_ledger(self.project, data)
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        raw = json.load(open(path, encoding="utf-8"))
        raw["items"][0]["text"] = "edited by hand"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        diff = sl.accept_edit(self.project)
        self.assertIn("edited by hand", diff)
        self.assertFalse(sl.ledger_tampered(self.project))
        prev = open(path + ".prev", encoding="utf-8").read()
        self.assertIn("original", prev)

    def test_corrupt_ledger_raises_studio_error(self):
        self.init_project()
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "w") as f:
            f.write("{not json")
        with self.assertRaises(sl.StudioError):
            sl.load_ledger(self.project)
