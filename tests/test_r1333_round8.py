"""R-1333 round 8: a stops-work or urgency phrase counts only when the user states it (never in a question, never in pasted text);
a stops word is not on-screen evidence for type 3; a hand-due-only item is dated, not urgent; unblocks counts blocked items only.
The entry shapes copy the real ones: I-1438 (a question) and I-1738 (a Budgit message pasted after «ejemplo ->»)."""
import unittest

import i18n
from test_r1333_one_order import Base, her, due, lo, stamp

I_1438 = "hay algo en la forma  de medir que nos este ralentizando?"                       # shape fragment: a question, a double space, a «?» ending
I_1738 = "tienes muchos bloqueos ejemplo -> Un control le impidió leer la lista,"      # shape fragment: the marker «ejemplo ->» and pasted text after it
UX = [{"id": "F1", "sev": "MED", "text": "x", "state": "open", "at": "2026-11-20T00:00:00.000Z", "by": None, "class": "ux"}]


class R8(Base):
    def row(self, res, item_id):
        return self.rows(res)[item_id]


class Statements(R8):
    def test_a_question_is_not_a_stops_statement(self):
        self.add(1, flags=["bug"], src=["I-1"])
        self.add(2, flags=["bug"], src=["I-2"])
        self.add(3, flags=["feature"], src=["I-3"])
        self.inbox = [her(1, text=I_1438), her(2, text="¿esto me frena todo. y que más?"), her(3, text="¿será urgente?")]
        res = self.order()
        self.assertEqual(lo.user_statements(self.inbox[0]), "")
        for iid in ("R-1", "R-2"):
            self.assertFalse(self.row(res, iid)["stops"], iid)
            self.assertEqual(self.row(res, iid)["type"], 2, iid)                # no on-screen evidence either
            self.assertNotIn("te impide trabajar", lo.reason_line(self.row(res, iid), "es"), iid)
        self.assertFalse(self.row(res, "R-3")["urgent"])                          # a question is no urgency phrase
        self.assertIn("frena", lo.user_statements(her(4, text="esto me frena todo")))   # the same words stated count

    def test_pasted_budgit_text_is_not_her_statement(self):
        self.add(1, flags=["bug", "visible"], src=["I-1"], findings=UX)
        self.add(2, flags=["bug", "visible"], src=["I-2"], findings=UX)
        self.inbox = [her(1, text=I_1738), her(2, text='me pasa esto:\n> Budgit: me impide trabajar\nbudgit: me bloquea\ndice "me frena" y «no puedo seguir»')]
        res = self.order()
        said = lo.user_statements(self.inbox[0])
        self.assertIn("bloqueos", said)
        self.assertNotIn("impidi", said)
        for iid in ("R-1", "R-2"):
            self.assertFalse(self.row(res, iid)["stops"], iid)
            self.assertEqual(self.row(res, iid)["type"], 3, iid)                # still a bug the user sees: the finding says so
            self.assertNotIn("te impide trabajar", lo.reason_line(self.row(res, iid), "es"), iid)

    def test_stops_word_alone_is_not_type3(self):
        self.add(1, flags=["bug"], src=["I-1"])                                   # no finding, no area, no visible word
        self.add(2, flags=["bug", "visible"], src=["I-2"], findings=UX)
        self.inbox = [her(1, text="esto me frena todo"), her(2, text="esto me frena todo")]
        res = self.order()
        self.assertEqual((self.row(res, "R-1")["type"], self.row(res, "R-1")["stops"]), (2, False))
        self.assertEqual((self.row(res, "R-2")["type"], self.row(res, "R-2")["stops"]), (3, True))


class Wording(R8):
    def test_hand_due_only_is_dated_not_urgent(self):
        self.add(1, due=due(5), appetite="large", src=["I-1"])
        self.add(2, flags=["feature"], src=["I-2"])
        self.inbox = [her(1, text="lo quiero para el 2026-11-20"), her(2, text="hazlo asap")]
        res = self.order()
        dated, urgent = self.row(res, "R-1"), self.row(res, "R-2")
        self.assertTrue(dated["dated"] and not dated["phrase"])
        line = lo.reason_line(dated, "es")
        self.assertIn(i18n.t("ledger.order_why_dated", lang="es"), line)
        self.assertNotIn(i18n.t("ledger.order_why_urgent", lang="es"), line)
        self.assertIn(i18n.t("ledger.order_why_urgent", lang="es"), lo.reason_line(urgent, "es"))
        self.assertEqual((res["urgent_total"], res["dated_total"]), (1, 1))
        self.assertNotIn("R-1", res["urgent_outside_head"])


class Unblocks(R8):
    def test_unblocks_counts_blocked_items_only(self):
        self.add(1)
        self.add(2, text="the context is in R-1 (R-1 option 3)")                # a mention
        self.add(3, blocked={"reason": "needs R-1", "since": stamp(1)})          # R-3 is blocked on R-1
        self.add(4, text="see R-1")
        res = self.order()
        self.assertEqual(self.row(res, "R-1")["unblocks"], 1)
        self.assertEqual(self.row(res, "R-2")["unblocks"], 0)


if __name__ == "__main__":
    unittest.main()
