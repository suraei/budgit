"""R-153 task 1: `status --brote` feed v2 (queue_recent, done_recent, titles, total), `short_title`, `ledger add --title`."""
import json
import os
import time
import unittest

from helpers import StudioTestCase, fastest_child, assert_ratio
import studiolib as sl
import test_brote_status as tbs


class ShortTitleTest(StudioTestCase):
    def test_cuts_at_the_first_separator(self):
        self.assertEqual(sl.short_title("Hacer la libreta bonita (con icono)"), "Hacer la libreta bonita")
        self.assertEqual(sl.short_title("Mover la tarjeta: ahora sí"), "Mover la tarjeta")
        self.assertEqual(sl.short_title("Quitar la banda — por decisión suya"), "Quitar la banda")
        self.assertEqual(sl.short_title("Probar el panel. Luego el libro"), "Probar el panel")
        self.assertEqual(sl.short_title("Mover la libreta"), "Mover la libreta")

    def test_boilerplate_or_tiny_head_takes_the_text_after_the_colon(self):
        self.assertEqual(sl.short_title("Her request: dame un libro pixel"), "Dame un libro pixel")
        self.assertEqual(sl.short_title("R-12: arreglar el guard (urgente)"), "Arreglar el guard")
        self.assertEqual(sl.short_title("Fix: hacer algo bien"), "Hacer algo bien")

    def test_strips_quotes_stray_closers_and_the_final_stop(self):
        self.assertEqual(sl.short_title('"Quita la tarjeta" ya'), "Quita la tarjeta ya")
        self.assertEqual(sl.short_title("Cosa nueva)"), "Cosa nueva")
        self.assertEqual(sl.short_title("Algo hecho."), "Algo hecho")
        self.assertEqual(sl.short_title("Con (par) cerrado y uno suelto]"), "Con")  # first ' (' cuts, so the stray closer is gone too

    def test_caps_at_46_at_a_word_boundary_with_an_ellipsis(self):
        t = sl.short_title("una frase muy larga que no tiene ningún separador y sigue y sigue sin parar nunca")
        self.assertLessEqual(len(t), sl.TITLE_MAX)
        self.assertTrue(t.endswith("…"))
        self.assertNotIn("  ", t)
        self.assertEqual(t[:-1], t[:-1].rstrip())
        self.assertTrue(t.startswith("Una frase muy larga"))
        # never cut inside a word: the text before the ellipsis is a prefix made of whole words
        src = "una frase muy larga que no tiene ningún separador y sigue y sigue sin parar nunca"
        self.assertIn(t[1:-1].lower().rstrip(), src)
        self.assertTrue(src.lower().startswith(t[:-1].lower().rstrip()))
        self.assertEqual(src[len(t[:-1].rstrip())], " ")

    def test_exactly_46_is_left_alone(self):
        s = "x" * 46
        self.assertEqual(sl.short_title(s), "X" + "x" * 45)


class FeedV2Test(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s1")

    cli = tbs.BroteCliTest.cli
    record = tbs.BroteCliTest.record

    def put(self, specs):
        """specs: (id, status, phase, extra dict)"""
        data = sl.load_ledger(self.project)
        for id_, st, phase, extra in specs:
            it = {"id": id_, "src": [], "text": "tarea larga de " + id_ + ": detalle", "status": st,
                  "evidence": "e" if st in ("done", "verified") else "", "phase": phase,
                  "created": "2026-10-01T00:00:00.000Z", "updated": "2026-10-01T00:00:00.000Z"}
            it.update(extra)
            data["items"].append(it)
        with sl.locked(self.project):
            sl.save_ledger(self.project, data)

    def feed(self):
        r = self.cli("status", "--brote")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_v2_adds_keys_and_keeps_every_v1_key(self):
        specs = [("R-%d" % n, "open", "build", {}) for n in range(1, 13)]
        specs += [("R-13", "open", "later", {}), ("R-14", "doing", "build", {})]
        specs += [("R-%d" % n, st, "build", {}) for n, st in ((15, "done"), (16, "verified"), (17, "done"), (18, "verified"), (19, "dropped"))]
        self.put(specs)
        out = self.feed()
        self.assertEqual(out["version"], 2)
        for k in ("state", "counts", "stale", "idle", "doing", "queue", "later", "h5", "fresh"):
            self.assertIn(k, out)
        self.assertEqual(out["counts"], {"doing": 1, "next": 12, "later": 1, "done": 4})
        self.assertEqual([i["id"] for i in out["queue"]], ["R-%d" % n for n in range(1, 13)])   # v1 order untouched (oldest first)
        ids = [i["id"] for i in out["queue_recent"]]
        self.assertEqual(ids, ["R-12", "R-11", "R-10", "R-9", "R-8", "R-7", "R-6", "R-5"])        # 8 newest, strictly descending, parked excluded
        self.assertEqual([i["id"] for i in out["done_recent"]], ["R-18", "R-17", "R-16"])
        self.assertEqual((out["total"], out["done_total"]), (12 + 1 + 1 + 4, 4))                  # dropped out, parked in

    def test_legacy_ids_without_a_number_sort_last(self):
        self.put([("R-2", "open", "build", {}), ("legacy-x", "open", "build", {}), ("R-9", "open", "build", {})])
        self.assertEqual([i["id"] for i in self.feed()["queue_recent"]], ["R-9", "R-2", "legacy-x"])

    def test_title_is_used_when_present_else_the_short_title_of_the_full_text(self):
        self.put([("R-1", "open", "build", {"title": "Título propio"}), ("R-2", "open", "build", {}),
                  ("R-3", "doing", "build", {"title": "En marcha"}), ("R-4", "done", "build", {})])
        out = self.feed()
        by = {i["id"]: i["title"] for i in out["queue_recent"] + out["doing"] + out["done_recent"]}
        self.assertEqual(by["R-1"], "Título propio")
        self.assertEqual(by["R-2"], "Tarea larga de R-2")
        self.assertEqual(by["R-3"], "En marcha")
        self.assertEqual(by["R-4"], "Tarea larga de R-4")
        self.assertEqual([i["title"] for i in out["queue"]], ["Título propio", "Tarea larga de R-2"])   # existing v1 lists carry it too

    def test_empty_ledger_still_answers(self):
        out = self.feed()
        self.assertEqual((out["total"], out["done_total"], out["queue_recent"], out["done_recent"]), (0, 0, [], []))

    def test_answers_fast_on_a_big_ledger(self):
        self.put([("R-%d" % n, "open" if n % 3 else "done", "build", {}) for n in range(1, 400)])
        self.feed()
        # R-873 follow-up: same-run ratio of child CPU. Baseline = the same CLI doing nothing (`--version`), best of 3, now.
        # Measured ratios on 5 runs: 1.2, 1.2, 1.2, 1.2, 1.2 (worst 1.2), so K = 3.6 (3x worst). CEILING only catches a catastrophe.
        assert_ratio(self, self.feed, lambda: self.cli("--version"), k=3.6, ceiling=30.0, timer=fastest_child)


class LedgerTitleTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s1")

    cli = tbs.BroteCliTest.cli

    def add(self, *extra, text="una tarea nueva"):
        return self.cli("ledger", "add", "--src", "legacy", "--phase", "later", "--text", text, *extra)

    def test_a_title_is_stored_and_survives_the_other_writers_and_the_render(self):
        r = self.add("--title", "Tarea nueva corta")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = sl.load_ledger(self.project)
        self.assertEqual(data["items"][0]["title"], "Tarea nueva corta")
        self.assertEqual(data["items"][0]["text"], "una tarea nueva")        # her words stay the text
        self.assertEqual(self.cli("ledger", "set", "R-1", "doing").returncode, 0)
        self.assertEqual(self.cli("ledger", "done", "R-1", "--outcome", "La tarea nueva queda hecha", "--evidence", "abcdef1 test_hecho").returncode, 0)
        item = sl.load_ledger(self.project)["items"][0]
        self.assertEqual((item["title"], item["status"]), ("Tarea nueva corta", "done"))
        self.assertTrue(os.path.exists(os.path.join(sl.studio_dir(self.project), "ledger.md")))

    def test_46_characters_is_accepted(self):
        self.assertEqual(self.add("--title", "y" * 46).returncode, 0)

    def test_over_46_or_blank_is_rejected_and_adds_nothing(self):
        for bad in ("y" * 47, "   ", ""):
            r = self.add("--title", bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertTrue(r.stderr.strip(), "a message is expected")
        self.assertEqual(sl.load_ledger(self.project)["items"], [])

    def test_a_bad_title_with_no_src_leaves_the_inbox_untouched(self):
        before = sl.load_inbox(self.project)
        for bad in ("y" * 47, "   "):
            r = self.cli("ledger", "add", "--text", "una tarea nueva", "--phase", "later", "--appetite", "small", "--area", "main", "--title", bad)
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertEqual(sl.load_inbox(self.project), before)
        self.assertEqual(sl.load_ledger(self.project)["items"], [])

    def test_without_a_title_it_still_works_and_validates(self):
        self.assertEqual(self.add().returncode, 0)
        item = sl.load_ledger(self.project)["items"][0]
        self.assertNotIn("title", item)


class LibretaCopyTest(unittest.TestCase):
    """R-153 task 4: the Libreta panel's words live in the plugin catalog and reach the app through the feed's `t`."""
    KEYS = ("book.head_empty", "book.head_one", "book.head_many", "book.head_stale_one", "book.head_stale_many", "book.old",
            "book.progress", "book.progress_none", "book.h_doing", "book.h_next", "book.h_done", "book.empty_doing", "book.empty_next",
            "book.empty_done", "book.stale_cap", "book.more_newest", "book.parked_one", "book.parked_many")

    def test_every_key_is_in_both_languages_with_the_same_placeholders(self):
        import re
        import i18n
        en, es = i18n.brote_strings("en"), i18n.brote_strings("es")
        for k in self.KEYS:
            self.assertIn(k, en, k)
            self.assertIn(k, es, k)
            self.assertNotEqual(en[k], es[k], k)
            self.assertEqual(sorted(re.findall(r"\{\w+\}", en[k])), sorted(re.findall(r"\{\w+\}", es[k])), k)

    def test_the_spanish_voice_of_the_header(self):
        import i18n
        es = i18n.brote_strings("es")
        self.assertEqual(es["book.head_one"], "Hay una tarea en marcha.")
        self.assertEqual(es["book.head_stale_many"], "Llevan más de 7 días sin moverse. ¿Las miramos?")
        self.assertEqual(es["book.old"], "Datos de hace un rato")
        self.assertEqual(es["book.progress"].format(done=3, total=10, pct=30), "3 de 10 hechas · 30 %")
