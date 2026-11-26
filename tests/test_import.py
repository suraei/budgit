import os, shutil
from helpers import StudioTestCase, ROOT
import studiolib as sl

FIX = os.path.join(ROOT, "tests", "fixtures", "ledger_v0.md")


class ImportTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, bind=False)
        self.src = os.path.join(sl.studio_dir(self.project), "ledger-v0.md")
        shutil.copy(FIX, self.src)

    def test_imports_r_rows_from_any_table_keeping_numbers(self):
        n = sl.import_v0_markdown(self.project, self.src)
        self.assertEqual(n, 5)
        data = sl.load_ledger(self.project)
        self.assertEqual([i["id"] for i in data["items"]], ["R-0", "R-1", "R-2", "R-9", "R-10"])
        self.assertEqual([i["status"] for i in data["items"]], ["doing", "open", "dropped", "doing", "open"])
        self.assertEqual(data["items"][0]["text"], '"quiero una app | de recetas"')
        self.assertEqual(sl.add_item(data, "new", ["I-1"])["id"], "R-11")

    def test_keeps_decisions_corrections_and_hypotheses(self):
        sl.import_v0_markdown(self.project, self.src)
        data = sl.load_ledger(self.project)
        self.assertEqual([r["id"] for r in data["v0_rows"]], ["D1", "H1", "C1"])
        md = open(os.path.join(sl.studio_dir(self.project), "ledger.md"), encoding="utf-8").read()
        self.assertIn("Producto público", md)
        self.assertIn("ledger-v0.md", md)

    def test_refuses_when_ledger_has_items(self):
        data = sl.load_ledger(self.project)
        sl.add_item(data, "x", ["legacy"])
        sl.save_ledger(self.project, data)
        with self.assertRaises(sl.StudioError):
            sl.import_v0_markdown(self.project, self.src)

    def test_import_from_outside_copies_archive(self):
        os.remove(self.src)
        sl.import_v0_markdown(self.project, FIX)
        self.assertTrue(os.path.isfile(self.src))


HUB = """# Studio ledger

| ID | Input (resumen fiel) | Tipo | Estado |
|----|----------------------|------|--------|
| R1 | Equipo de roles | Requisito | Pendiente (pilar Equipo) |
| R4 | Todo localizado | Requisito | Decidido: repo GitHub privado |
| R8 | Investigar skills | Tarea | Investigado ✔ (informe A) |
| R30 | continua | Decisión | Hecho: kickoff v0.5 |
| R33 | animaciones | Requisito | En curso: research A/B |
| R40 | algo | Idea | Descartado por ella |
| R41 | como empiezo | Pregunta | Respondida con prompt |
"""


class ImportHubFormatTest(StudioTestCase):
    """Hand ledgers put the status in a column named status/estado, not always the 3rd."""
    def setUp(self):
        super().setUp()
        sl.activate(self.project, bind=False)
        self.src = os.path.join(sl.studio_dir(self.project), "ledger-v0.md")
        open(self.src, "w", encoding="utf-8").write(HUB)

    def test_status_column_found_by_header_and_spanish_words_mapped(self):
        sl.import_v0_markdown(self.project, self.src)
        items = {i["id"]: i for i in sl.load_ledger(self.project)["items"]}
        self.assertEqual({k: v["status"] for k, v in items.items()},
                         {"R-1": "open", "R-4": "done", "R-8": "done", "R-30": "done",
                          "R-33": "doing", "R-40": "dropped", "R-41": "done"})
        self.assertEqual(items["R-30"]["evidence"], "Hecho: kickoff v0.5")
        self.assertEqual(items["R-1"]["text"], "Equipo de roles")
