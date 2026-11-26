"""R-1333 v1b: the one order ranks what an item is and how much it hurts her (fix before new, her urgency, type, her words,
unblocks, release). Time enters only through the rotation slots. Fixtures built through sl.add_item; no view changes."""
import re
import unittest

import i18n
import studiolib as sl
from test_r1333_one_order import Base, her, stamp, due, AT, TODAY, lo

URGENT_ES = "esto es urgente, no puedo trabajar"
FINDING = {"id": "F1", "sev": "MED", "text": "x", "state": "open", "at": "2026-11-20T00:00:00.000Z", "by": None}


def finding(cls, state="open"):
    return [dict(FINDING, **{"class": cls, "state": state})]


class V1b(Base):
    def row(self, res, item_id):
        return self.rows(res)[item_id]


class Tier(V1b):
    def test_fix_before_new_in_key_slots(self):
        self.add(1, flags=["feature"], src=["I-1", "I-2", "I-3"])
        self.add(2, flags=["bug"])
        self.add(3, src=["legacy"], blocked=None)
        self.inbox = [her(1), her(2), her(3)]
        self.add(4, flags=["feature"], age=20)
        self.add(5, flags=["feature"], age=20)
        self.add(6, flags=["feature"], age=20)
        self.add(7, flags=["feature"], age=20, text="blocked by R-1")
        res = self.order()
        ids = self.head_ids(res)
        self.assertEqual(ids[0], "R-2")                       # the fix first, whatever the new work scores
        self.assertLess(ids.index("R-2"), ids.index("R-1"))
        self.assertGreater(self.row(res, "R-1")["score"], 0)

    def test_unknown_between_A_and_B(self):
        self.add(1, flags=["feature"], src=["I-1", "I-2", "I-3"])
        self.add(2)
        self.add(3, flags=["bug"])
        self.inbox = [her(1), her(2), her(3)]
        res = self.order()
        self.assertEqual(self.head_ids(res), ["R-3", "R-2", "R-1"])
        self.assertEqual([self.row(res, i)["tier"] for i in ("R-3", "R-2", "R-1")], [0, 1, 2])
        self.assertEqual(res["unknown_in_head"], 1)


class Urgency(V1b):
    def test_her_urgency_lifts_to_tier0(self):
        self.add(1, flags=["bug"], age=20)
        self.add(2, src=["I-5"])                                  # unknown class
        self.add(3, flags=["feature"], src=["I-6"])               # class B, English word
        self.inbox = [her(5, text=URGENT_ES), her(6, text="do it asap")]
        res = self.order()
        for iid in ("R-2", "R-3"):
            self.assertEqual(self.row(res, iid)["tier"], 0, iid)
            self.assertTrue(self.row(res, iid)["urgent"], iid)
        self.assertFalse(self.row(res, "R-1")["urgent"])
        self.assertEqual(self.head_ids(res)[-1], "R-1")           # urgency is the first key inside tier 0

    def test_urgency_word_needs_her_word_source(self):
        self.add(1, title="urgente: asap, no puedo trabajar", text="urgent right now")
        self.add(2, src=["I-5"])
        self.add(3, src=["I-6"])
        self.inbox = [her(5, source="hook", text=URGENT_ES), her(6, text="<bash-input>urgente")]
        res = self.order()
        for iid in ("R-1", "R-2", "R-3"):
            self.assertFalse(self.row(res, iid)["urgent"], iid)
            self.assertEqual(self.row(res, iid)["tier"], 1, iid)

    def test_ya_no_se_ve_is_not_urgent(self):
        self.add(1, src=["I-1"])
        self.inbox = [her(1, text="ya no se ve el menu lateral")]
        self.assertFalse(self.row(self.order(), "R-1")["urgent"])

    def test_hand_due_without_her_word_is_tiebreak_only(self):
        self.add(1, due=due(5), appetite="large")
        self.add(2, due=due(5), appetite="large", src=["I-2"])                   # her word names no date
        self.add(3, due=due(5), appetite="large", src=["I-3"])                   # her word names a date
        self.add(4, due=due(5), due_auto=True, appetite="medium", src=["I-3"])   # auto due never counts
        self.inbox = [her(2, text="mira esto"), her(3, text="lo quiero para el 2026-11-20")]
        res = self.order()
        self.assertFalse(self.row(res, "R-1")["urgent"])
        self.assertFalse(self.row(res, "R-2")["urgent"])
        self.assertTrue(self.row(res, "R-3")["urgent"])
        self.assertFalse(self.row(res, "R-4")["urgent"])
        self.assertEqual(self.row(res, "R-1")["late_days"], 5)                   # the tie-break still reads it


class Type(V1b):
    def test_bug_she_sees_enters_from_parked_phase(self):
        self.add(1, flags=["bug", "visible"], phase=sl.LATER_PHASE, src=["I-1"], age=20)       # the explicit mark makes it type 3
        self.add(2, flags=["bug"], phase=sl.LATER_PHASE, age=20)
        self.inbox = [her(1, text="no se ve el menu lateral")]       # the same words without the mark change nothing
        res = self.order()
        seen, control = self.row(res, "R-1"), self.row(res, "R-2")
        self.assertEqual(seen["type"], 3)
        self.assertEqual((seen["where"], seen.get("slot")), ("head", "key"))
        self.assertNotEqual(control.get("slot"), "key")

    def test_type_order_ux_over_correctness_over_perf_over_process(self):
        self.add(1, flags=["bug", "visible"], findings=finding("ux"))
        self.add(2, flags=["bug"], findings=finding("correctness"))
        self.add(3, findings=finding("performance"))
        self.add(4, findings=finding("test-gap"))
        self.add(5, flags=["bug", "visible"])                     # the explicit mark
        self.add(6, findings=finding("privacy"))
        res = self.order()
        types = {i: self.row(res, "R-%d" % i)["type"] for i in range(1, 7)}
        self.assertEqual(types, {1: 3, 2: 2, 3: 1, 4: 0, 5: 3, 6: 2})
        scores = [self.row(res, "R-%d" % i)["score"] for i in (1, 2, 3, 4)]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertLess(self.head_ids(res).index("R-1"), self.head_ids(res).index("R-2"))

    def test_only_open_findings_count(self):
        self.add(1, flags=["bug"], findings=finding("ux", "fixed"))
        self.add(2, flags=["bug"], findings=finding("ux", "deferred"))
        self.add(3, findings=finding("security", "fixed"))
        self.add(4, flags=["bug"], findings=finding("ux"))
        res = self.order()
        self.assertEqual([self.row(res, "R-%d" % i)["type"] for i in (1, 2, 3, 4)], [2, 2, 0, 2])       # a ux finding is no mark: class A is type 2

    def test_an_area_is_not_a_mark(self):
        self.add(1, flags=["bug"], area="brote")                  # a visible-surface area alone no longer makes type 3
        self.assertEqual(self.row(self.order(), "R-1")["type"], 2)


class HerWordIds(V1b):
    def test_her_word_ids_are_distinct_across_folds(self):
        parent = self.add(1, src=["I-1"], text="see I-1 and I-2")
        kid = self.add(2, status="folded", src=["I-2", "I-3"])
        kid["folded"] = {"into": "R-1", "reason": "dup", "at": stamp(1)}
        words = lo.user_index([her(1), her(2), her(3), her(9, source="hook")])
        self.assertEqual(lo.her_word_ids(parent, words, [kid]), {"I-1", "I-2", "I-3"})
        self.inbox = [her(1), her(2), her(3)]
        self.assertEqual(self.row(self.order(), "R-1")["her_words"], 3)


class Inherit(V1b):
    def test_blocker_inherits_urgency(self):
        self.add(1)
        self.add(2, src=["I-5"], blocked={"reason": "needs R-1", "since": stamp(1), "on": "R-1"})
        self.add(3, flags=["bug"], age=20)
        self.inbox = [her(5, text=URGENT_ES)]
        res = self.order()
        self.assertTrue(self.row(res, "R-1")["urgent"])
        self.assertEqual(self.row(res, "R-1")["tier"], 0)
        self.assertEqual(self.head_ids(res)[0], "R-1")

    def test_blocker_inherits_type(self):
        self.add(1)
        self.add(2, flags=["bug", "visible"], findings=finding("ux"), blocked={"reason": "needs R-1", "since": stamp(1), "on": "R-1"})
        self.add(3, flags=["bug"], age=20)
        res = self.order()
        self.assertEqual(self.row(res, "R-1")["type"], 3)
        self.assertGreaterEqual(self.row(res, "R-1")["score"], 30)
        self.assertLess(self.head_ids(res).index("R-1"), self.head_ids(res).index("R-3"))


class NothingForgotten(V1b):
    def test_rotation_bound_alarm_above_age_cap(self):
        for i in range(1, 126):
            self.add(i, age=40)
        res = self.order()
        self.assertEqual(res["rotation_slots"], lo.ROTATION_MAX)
        self.assertEqual(res["rotation_bound_days"], 32)
        self.assertTrue(res["rotation_alarm"])
        self.assertIn("32", lo.daily_line(res, "en"))

    def test_rotation_no_alarm_with_a_small_backlog(self):
        for i in range(1, 6):
            self.add(i, age=40)
        res = self.order()
        self.assertFalse(res["rotation_alarm"])
        self.assertLessEqual(res["rotation_bound_days"], lo.AGE_CAP)

    def test_launch_only_touches_do_not_reset_age_after_three(self):
        it = self.add(1, age=60)
        for days in (10, 9):
            it["events"].append({"at": stamp(days), "kind": "launch", "text": "launch R-1"})
        self.assertEqual(self.row(self.order(), "R-1")["untouched_days"], 9)
        it["events"].append({"at": stamp(8), "kind": "launch", "text": "launch R-1"})
        self.assertEqual(self.row(self.order(), "R-1")["untouched_days"], 9)       # the third does not reset
        it["events"].append({"at": stamp(1), "kind": "handback", "text": "R-1 back"})
        self.assertEqual(self.row(self.order(), "R-1")["untouched_days"], 1)       # a real touch does

    def test_reserved_B_slot_when_A_not_falling(self):
        for i in range(1, 13):
            self.add(i, flags=["bug"], age=20)
        self.add(50, flags=["feature"], age=3, src=["I-7"])
        self.inbox = [her(7, days_ago=1)]
        res = self.order()
        self.assertEqual(self.row(res, "R-50").get("slot"), "reserved")
        self.assertEqual(len(res["head"]), 10)
        for i in range(60, 65):                                   # class A closed this week: A is falling
            self.add(i, flags=["bug"], age=30, status="done", done_at=stamp(2))
        res = self.order()
        self.assertNotEqual(self.row(res, "R-50").get("slot"), "reserved")
        self.assertNotIn("R-50", self.head_ids(res))

    def test_unstamped_item_reports_untouched_unknown(self):
        it = self.add(1)
        for key in ("created", "created_at"):
            it.pop(key, None)
        it["events"] = []
        res = self.order()
        row = self.row(res, "R-1")
        self.assertIsNone(row["untouched_days"])
        self.assertEqual(res["untouched_beyond_cap"], 1)
        self.assertIsNone(res["oldest_untouched_days"])


class Reason(V1b):
    ENGLISH = re.compile(r"\b(?:fix|bug|unblocks?|you|asked|new|release|untouched|test|urgent|late)\b", re.I)
    ID = re.compile(r"\b[RI]-\d+")

    def test_reason_line_has_no_ids_or_english_in_es(self):
        self.add(1, flags=["bug", "visible"], findings=finding("ux"), src=["I-1"], phase=None)
        self.add(2, age=40)
        self.add(3, flags=["feature"], age=40)
        self.add(4, text="blocked by R-1", flags=["bug"])
        self.inbox = [her(1, text=URGENT_ES)]
        res = self.order()
        self.assertTrue(res["head"])
        for r in res["head"]:
            line = lo.reason_line(r, "es")
            self.assertTrue(line, r["id"])
            self.assertIsNone(self.ID.search(line), line)
            self.assertIsNone(self.ENGLISH.search(line), line)
            self.assertTrue(lo.reason_line(r, "en"))
        first = lo.reason_line(self.row(res, "R-1"), "es")
        self.assertIn("arreglo de algo que ves", first)
        self.assertIn("lo pediste tú", first)

    def test_every_language_has_the_keys(self):
        for lang in i18n.LANGS:
            for key in ("ledger.plan_kw_urgent", "ledger.plan_kw_date", "ledger.order_daily"):
                self.assertTrue(i18n.CATALOG[lang][key], (lang, key))
            for key in lo.REASON_KEYS:
                self.assertTrue(i18n.CATALOG[lang][key], (lang, key))

    def test_reason_line_plural_one_day(self):
        self.add(1, appetite="small", due=due(1), age=1)
        res = self.order()
        row = self.row(res, "R-1")
        self.assertEqual(row["late_days"], 1)
        es, en = lo.reason_line(row, "es"), lo.reason_line(row, "en")
        self.assertIn("hace 1 día", es)
        self.assertNotIn("1 días", es)
        self.assertIn("overdue 1 day", en)
        self.assertNotIn("1 days", en)
        row = dict(row, late_days=0, slot="rotation", untouched_days=1)
        self.assertIn("1 día sin tocar", lo.reason_line(row, "es"))
        self.assertNotIn("1 días", lo.reason_line(row, "es"))


class UrgencyAboutThisItem(V1b):
    def test_long_multi_item_message_does_not_make_urgent(self):
        long_text = "urgente " + "y mas cosas de otros temas " * 30                  # over 400 characters
        self.add(1, src=["I-5"])
        self.add(2, src=["I-5"])
        self.add(3, src=["I-6"])                                                   # same long message, but about this item alone
        self.add(4, src=["I-7"])                                                   # short message, two items
        self.add(5, src=["I-7"])
        self.inbox = [her(5, text=long_text), her(6, text=long_text), her(7, text="urgente, no puedo trabajar")]
        res = self.order()
        self.assertGreater(len(long_text), 400)
        self.assertEqual([self.row(res, "R-%d" % i)["urgent"] for i in (1, 2, 3, 4, 5)], [False, False, True, True, True])

    def test_linked_but_not_origin_is_not_urgent(self):
        self.add(1, flags=["bug"], src=["legacy", "I-1"])
        self.add(2, flags=["bug"], src=["I-1"])
        self.add(3, src=["legacy", "I-1"])
        self.inbox = [her(1, text=URGENT_ES + ", no se ve el menu lateral")]      # visible words, but R-1 only links to them
        res = self.order()
        self.assertEqual(self.row(res, "R-1")["type"], 2)
        self.assertEqual(self.row(res, "R-2")["type"], 2)                         # the words never make type 3 (only the mark does)
        self.assertEqual(self.row(res, "R-1")["her_words"], 1)                    # still her word, only not urgency
        self.assertFalse(self.row(res, "R-1")["urgent"])
        self.assertFalse(self.row(res, "R-3")["urgent"])
        self.assertTrue(self.row(res, "R-2")["urgent"])

    def test_prioridad_alone_is_not_urgent(self):
        self.add(1, src=["I-1"])
        self.add(2, src=["I-2"])
        self.inbox = [her(1, text="esto tiene prioridad, el urge de siempre"), her(2, text="me bloquea, hoy mismo")]
        res = self.order()
        self.assertFalse(self.row(res, "R-1")["urgent"])
        self.assertTrue(self.row(res, "R-2")["urgent"])

    def test_far_hand_due_class_b_stays_tier2(self):
        date_word = "lo quiero para el 2026-12-05"
        self.add(1, flags=["feature"], src=["I-1"], due=due(-10), appetite="large")      # due in 10 days
        self.add(2, flags=["feature"], src=["I-2"], due=due(-1), appetite="large")       # due tomorrow
        self.add(3, flags=["feature"], src=["I-3"], due=due(-2), appetite="large")       # due in 2 days
        self.add(4, flags=["feature"], src=["I-4"], due=due(3), appetite="large")        # already late
        self.add(5, flags=["feature"], src=["I-5"], appetite="large")                    # her phrase, no due: tier 0 whatever the class
        self.add(6, flags=["feature"], src=["I-6"], due=due(-3), appetite="large")       # due in 3 days: not yet
        self.add(7, flags=["bug"], src=["legacy"])
        self.inbox = [her(1, text=date_word), her(2, text=date_word), her(3, text=date_word), her(4, text=date_word),
                      her(5, text="no puedo trabajar"), her(6, text=date_word)]
        res = self.order()
        for iid, tier in (("R-1", 2), ("R-2", 0), ("R-3", 0), ("R-4", 0), ("R-5", 0), ("R-6", 2), ("R-7", 0)):
            self.assertEqual(self.row(res, iid)["tier"], tier, iid)
        for iid in ("R-1", "R-6"):
            self.assertTrue(self.row(res, iid)["urgent"], iid)                            # urgent first inside its own tier
        ids = self.head_ids(res)
        self.assertLess(ids.index("R-7"), ids.index("R-1"))                               # the far due never passes a fix

    def test_bug_she_sees_above_past_hand_due_class_b(self):
        self.add(1, flags=["feature"], src=["I-1"], due=due(5), appetite="large")        # class B, past hand due + date word
        self.add(2, flags=["bug", "visible"], src=["I-2"])                               # a bug she sees (marked), no urgency
        self.add(3, flags=["feature"], src=["I-3"])                                      # class B with her explicit phrase
        self.add(4, flags=["bug"], src=["legacy"])                                       # a fix she does not see
        self.inbox = [her(1, text="lo quiero para el 2026-11-20"), her(2, text="la barra no sale"), her(3, text="urgente")]
        res = self.order()
        self.assertEqual([self.row(res, i)["tier"] for i in ("R-1", "R-2", "R-3", "R-4")], [0, 0, 0, 0])
        self.assertEqual(self.head_ids(res)[:4], ["R-3", "R-2", "R-4", "R-1"])           # her phrase (any class), a bug she sees, other fixes, then the date: class B with a past due last

    def test_visible_bug_that_stops_work_above_urgent_new_work(self):
        self.add(1, flags=["feature"], src=["I-1"])                                      # class B with her explicit phrase
        self.add(2, flags=["bug", "visible"], src=["I-2"])                               # class A type 3 (marked) that stops her work
        self.add(3, flags=["bug", "visible"], src=["I-3"])                               # class A type 3 (marked) that does not
        self.inbox = [her(1, text="urgente"), her(2, text="la barra no se ve y no puedo trabajar"), her(3, text="la barra no sale")]
        res = self.order()
        self.assertEqual([self.row(res, i)["stops"] for i in ("R-1", "R-2", "R-3")], [False, True, False])
        self.assertEqual(self.head_ids(res)[:3], ["R-2", "R-1", "R-3"])
        self.assertIn("te impide trabajar", lo.reason_line(self.row(res, "R-2"), "es"))

    def test_urgent_new_work_above_visible_bug_that_does_not_stop_work(self):
        self.add(1, flags=["bug"], src=["legacy"])                                       # class A type 2
        self.add(2, flags=["feature"], src=["I-2"])                                      # class B, urgent
        self.add(3, flags=["feature"], src=["I-3"])                                      # class B, not urgent
        self.add(4, flags=["bug", "visible"], src=["I-4"])                               # class A type 3 (marked), does not stop her work
        self.inbox = [her(2, text="urgente"), her(3, text="una idea"), her(4, text="la barra no sale")]
        res = self.order()
        self.assertEqual(self.head_ids(res)[:4], ["R-2", "R-4", "R-1", "R-3"])

    def test_urgency_never_decays(self):
        self.add(1, flags=["feature"], src=["I-1"], age=300)
        self.inbox = [her(1, days_ago=200, text="urgente")]
        row = self.row(self.order(), "R-1")
        self.assertTrue(row["urgent"])
        self.assertEqual(row["tier"], 0)

    def test_class_b_never_becomes_type3_even_with_the_mark(self):
        self.add(1, flags=["feature", "visible"], title="una barra nueva")
        self.assertEqual(self.row(self.order(), "R-1")["type"], 0)


if __name__ == "__main__":
    unittest.main()
