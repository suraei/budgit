"""R-1333 step 2 (her card I-2153): `budgit ledger top`, the status top, the plan and the daily all call ledgerorder.view, so they show
the same head with the same «porque» line as the dry run."""
import json
import unittest

import daily
import ledgerorder as lo
import ledgerplan
import studiolib as sl
from test_task_rank import RankCase


class Views(RankCase):
    def setUp(self):
        super().setUp()
        self.ledger(("plain one", 3, {}), ("a bug", 2, {"flags": ["bug"]}), ("a feature", 1, {"flags": ["feature"]}),
                    ("a seen bug", 0, {"flags": ["bug", "visible"]}), ("an old one", 40, {}))

    def head(self):
        res = lo.view(self.project)
        return res, [r["id"] for r in lo.shown(res)]

    def test_daily_shows_the_same_head_across_phases(self):
        self.ledger(*(("seen bug %d" % n, 0, {"flags": ["bug", "visible"], "phase": sl.LATER_PHASE}) for n in range(3)),
                    *(("more work %d" % n, n, {"phase": "mvp"}) for n in range(8)))
        res = lo.view(self.project)
        head = [r["id"] for r in res["head"]]
        later = [it["id"] for it in sl.load_ledger(self.project)["items"] if it.get("phase") == sl.LATER_PHASE]
        self.assertEqual(len(later), 3)
        self.assertTrue(set(later) <= set(head), (later, head))                 # three head items live in another phase than the next version
        (row,) = daily.collect(self.project, git=False)["projects"]
        self.assertEqual([o["id"] for o in row["order"]], [r["id"] for r in lo.shown(res)])
        self.assertEqual([o["why"] for o in row["order"]], [lo.reason_line(r) for r in lo.shown(res)])
        self.assertEqual(row["order_line"], lo.daily_line(res))

    def test_pins_do_not_take_head_slots(self):
        import taskrank
        self.ledger(*(("more work %d" % n, n, {}) for n in range(14)))
        data = sl.load_ledger(self.project)
        for n in range(1, 10):
            taskrank.pin(data, "R-%d" % n, n, "the user asked for it")
        sl.save_ledger(self.project, data)
        res = lo.view(self.project)
        self.assertEqual((len(res["pinned"]), len(res["head"])), (9, 10))
        self.assertEqual(len(lo.shown(res)), 19)                              # 9 pins and the 10 head rows
        lines = lo.head_lines(res)
        self.assertEqual(sum("porque:" in l for l in lines), 19)
        self.assertIn("fijadas (9)", lo.head_lines(res, lang="es")[0])
        text = self.ok("ledger", "top")
        self.assertEqual(text.count("porque:"), 19)
        self.assertIn("top 10 of", text)                                      # «top 10» counts the head rows only
        self.assertIn("top 10 (", self.ok("status"))
        self.assertEqual(len(json.loads(self.ok("ledger", "top", "--json"))), 19)
        plan = ledgerplan.build(self.project, sl.load_ledger(self.project), step0=False)
        self.assertIn("fijadas (9)", ledgerplan.render(plan, lang="es"))
        (row,) = daily.collect(self.project, git=False)["projects"]
        self.assertEqual(sum(1 for o in row["order"] if o.get("pinned")), 9)

    def test_the_three_views_show_the_same_head(self):
        res, ids = self.head()
        top = json.loads(self.ok("ledger", "top", "--json"))
        self.assertEqual([r["id"] for r in top], ids)
        self.assertEqual([r["why"] for r in top], [lo.reason_line(r) for r in lo.shown(res)])
        rows = ledgerplan.build(self.project, sl.load_ledger(self.project), step0=False)["rows"]
        self.assertEqual([r["id"] for r in rows if r["id"] in ids], ids)
        self.assertEqual([r["why"] for r in rows if r["id"] in ids], [lo.reason_line(r) for r in lo.shown(res)])
        (row,) = daily.collect(self.project, git=False)["projects"]
        self.assertEqual([o["id"] for o in row["order"]], ids)
        self.assertEqual([o["why"] for o in row["order"]], [lo.reason_line(r) for r in lo.shown(res)])
        self.assertEqual(row["order_line"], lo.daily_line(res))

    def test_top_and_status_print_the_porque_line_of_the_dry_run(self):
        res, ids = self.head()
        text = self.ok("ledger", "top")
        status = self.ok("status")
        for line in lo.head_lines(res):
            self.assertIn(line, text)
            self.assertIn(line, status)
        self.assertIn("porque:", text)
        self.assertEqual(ids[0], "R-4")                            # the marked fix first: type 3

    def test_the_plan_header_names_the_one_order_not_n_p50(self):
        import i18n
        plan = ledgerplan.build(self.project, sl.load_ledger(self.project), step0=False)
        for lang in i18n.LANGS:
            header = ledgerplan.render(plan, lang=lang).splitlines()[0]
            self.assertNotIn("N/p50", header, lang)
            self.assertNotIn("p50", header, lang)
            self.assertIn(str(plan["header"]["rows"]), header, lang)
        self.assertIn("lo que te frena", ledgerplan.render(plan, lang="es").splitlines()[0])
        self.assertIn("what stops you", ledgerplan.render(plan, lang="en").splitlines()[0])

    def test_the_dry_run_head_is_the_same_rows(self):
        res, ids = self.head()
        self.assertEqual([r["id"] for r in lo.compare(self.project)["new"]["head"]], [r["id"] for r in res["head"]])


if __name__ == "__main__":
    unittest.main()
