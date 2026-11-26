"""R-1333 round 11 (her card I-2145, fixed mark, no guessing): type 3 (a fix for something the user sees) comes ONLY from an explicit
mark: the item's flags hold "visible". No word in the title, text, a quote, the user's statements, the area or a finding is read. `budgit ledger flag R-n visible` sets the mark and `--off` removes it; `ledger add --flag visible` sets it at birth."""
import unittest

import i18n
import studiolib as sl
from helpers import StudioTestCase
from test_r1333_one_order import Base, her, lo
from test_task_rank import RankCase

UX = [{"id": "F1", "sev": "MED", "text": "x", "state": "open", "at": "2026-11-20T00:00:00.000Z", "by": None, "class": "ux"}]
SEEN = i18n.t("ledger.order_why_seen", lang="es")


class Marked(Base):
    def row(self, res, item_id):
        return self.rows(res)[item_id]

    def test_visible_needs_the_mark(self):
        self.add(1, flags=["bug"], src=["I-1"], title="Botón de la barra", text="el botón no se ve en la ventana", area="brote")
        self.add(2, flags=["bug"], title="«ya no aparece el botón»", text='dijo "no se ve la barra" y mando captura')
        self.inbox = [her(1, text="ya no se ve el botón de la barra, no aparece")]
        res = self.order()
        for iid in ("R-1", "R-2"):
            self.assertEqual(self.row(res, iid)["type"], 2, iid)
            self.assertIsNone(self.row(res, iid)["type_source"], iid)
            self.assertNotIn(SEEN, lo.reason_line(self.row(res, iid), "es"), iid)
        self.assertFalse(hasattr(lo, "VISIBLE_WORDS_KEY"))
        for lang in i18n.LANGS:
            self.assertNotIn("ledger.plan_kw_visible", i18n.CATALOG[lang])

    def test_the_flag_makes_type3(self):
        self.add(1, flags=["bug", "visible"], title="Plain title")
        self.add(2, flags=["feature", "visible"], title="New thing")          # class B never becomes type 3
        res = self.order()
        row = self.row(res, "R-1")
        self.assertEqual((row["type"], row["type_source"]), (3, "flag"))
        self.assertIn(SEEN, lo.reason_line(row, "es"))
        self.assertIn("arreglo de algo que ves", lo.reason_line(row, "es"))
        self.assertEqual(self.row(res, "R-2")["type"], 0)

    def test_ux_finding_makes_type3(self):
        """Name kept from the brief; since the last change an open ux finding alone is type 2 (it is not proof of something on the
        user's screen); only the flag gives type 3."""
        self.add(1, flags=["bug"], findings=UX)
        self.add(2, flags=["bug"], findings=[dict(UX[0], state="fixed")])
        self.add(3, flags=["bug", "visible"], findings=UX)                      # the flag still wins with a finding present
        res = self.order()
        for iid in ("R-1", "R-2"):
            row = self.row(res, iid)
            self.assertEqual((row["type"], row["type_source"]), (2, None), iid)
            self.assertNotIn(SEEN, lo.reason_line(row, "es"), iid)
        self.assertEqual((self.row(res, "R-3")["type"], self.row(res, "R-3")["type_source"]), (3, "flag"))

    def test_stops_needs_her_statement_and_the_mark(self):
        self.add(1, flags=["bug"], src=["I-1"])
        self.add(2, flags=["bug", "visible"], src=["I-2"])
        self.add(3, flags=["bug", "visible"], src=["I-3"])
        self.inbox = [her(1, text="no puedo trabajar"), her(2, text="no puedo trabajar"), her(3, text="me gusta")]
        res = self.order()
        self.assertEqual([self.row(res, "R-%d" % i)["stops"] for i in (1, 2, 3)], [False, True, False])


class NewestAsk(Base):
    def row(self, res, item_id):
        return self.rows(res)[item_id]

    def bugs(self):
        for i in range(1, 13):                                   # open class A is not falling, so one slot is reserved
            self.add(i, flags=["bug"], age=20)

    def test_her_last_ignores_ids_quoted_in_notes(self):
        self.bugs()
        self.add(758, flags=["feature"], age=3, src=["I-3"], text="technician note: see I-9 for the same idea")      # R-758 shape: quotes I-9 only in text
        self.add(403, flags=["feature"], age=9, src=["I-9"])                                                      # R-403 shape: I-9 is its own src
        self.inbox = [her(3, days_ago=5), her(9, days_ago=1)]
        res = self.order()
        self.assertEqual(lo.her_word_ids(self.data["items"][12], {"I-3": 1, "I-9": 2}), {"I-3"})
        self.assertEqual(self.row(res, "R-758")["her_words"], 1)
        self.assertEqual(self.row(res, "R-403")["her_words"], 1)
        self.assertGreater(self.row(res, "R-403")["her_last"], self.row(res, "R-758")["her_last"])
        self.assertEqual(self.row(res, "R-403").get("slot"), "reserved")
        self.assertNotEqual(self.row(res, "R-758").get("slot"), "reserved")

    def test_a_tie_on_the_same_time_goes_to_the_item_created_first(self):
        self.bugs()
        self.add(69, flags=["feature"], age=10, src=["I-9"])                                   # created first, lower id
        self.add(70, flags=["feature"], age=3, src=["I-9"])                                    # created later, higher id
        self.inbox = [her(9, days_ago=1)]
        res = self.order()
        self.assertEqual(self.row(res, "R-69").get("slot"), "reserved")
        self.assertNotEqual(self.row(res, "R-70").get("slot"), "reserved")


class FlagCli(RankCase):
    def test_ledger_flag_cli_sets_and_removes(self):
        self.ledger(("a fix", 0, {}),)
        self.ok("ledger", "flag", "R-1", "bug")
        self.ok("ledger", "flag", "R-1", "visible")
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), "R-1")["flags"], ["bug", "visible"])
        self.ok("ledger", "flag", "R-1", "visible", "--off")
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), "R-1")["flags"], ["bug"])
        self.ok("ledger", "flag", "R-1", "bug", "--off")
        self.assertNotIn("flags", sl.find_item(sl.load_ledger(self.project), "R-1"))

    def test_ledger_add_flag_visible_sets_the_mark(self):
        self.ok("ledger", "add", "--src", "legacy", "--text", "a fix you see", "--phase", "mvp", "--class", "bug", "--flag", "visible")
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), "R-1")["flags"], ["bug", "visible"])
        self.assertNotEqual(self.cli("ledger", "add", "--src", "legacy", "--text", "x", "--phase", "mvp", "--flag", "urgent").returncode, 0)

    def test_the_visible_flag_is_logged_with_its_source(self):          # security review L3
        self.ledger(("a fix", 0, {}),)
        self.ok("ledger", "flag", "R-1", "visible", "--src", "I-5")
        change = sl.find_item(sl.load_ledger(self.project), "R-1")["changes"][-1]
        self.assertEqual((change["what"], change["src"]), ("flag visible on", ["I-5"]))
        self.assertIn("by", change)
        self.ok("ledger", "flag", "R-1", "visible", "--off")
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), "R-1")["changes"][-1]["what"], "flag visible off")
        self.assertNotEqual(self.cli("ledger", "flag", "R-1", "visible", "--src", "not an id").returncode, 0)
        self.ok("ledger", "flag", "R-1", "bug")                                   # another flag leaves no trace
        self.assertEqual(len(sl.find_item(sl.load_ledger(self.project), "R-1")["changes"]), 2)
        self.ok("ledger", "add", "--src", "legacy", "--text", "born marked", "--phase", "mvp", "--flag", "visible")
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), "R-2")["changes"][-1]["what"], "flag visible on")


class InboxTextLimits(unittest.TestCase):                                   # security review L1 and L2
    def test_a_long_unclosed_quote_is_read_in_linear_time_and_capped(self):
        import time
        entry = her(1, text="«" * 200000 + " no puedo trabajar")
        t0 = time.monotonic()
        said = lo.user_statements(entry)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(len(lo.user_words(entry)), lo.USER_TEXT_MAX)
        self.assertEqual(lo._strip_quoted("a «b» c «d"), "a   c «d")             # the same result as the old pattern, one pass
        self.assertIsInstance(said, str)

    def test_the_json_answer_is_used_alone_and_a_question_cannot_fake_the_value(self):
        import json
        question = 'x" = "urgent'
        entry = her(1, source="answer", text="AskUserQuestion answer: " + json.dumps({"questions": [{"question": question}], "answers": {question: "Plan"}}))
        self.assertEqual(lo.user_words(entry), "Plan")
        plain = her(2, source="answer", text='AskUserQuestion answer: "Which x" = "urgent one?"="Plan", "Other"="Mastodon"')
        self.assertEqual(lo.user_words(plain), "Plan | Mastodon")


if __name__ == "__main__":
    unittest.main()
