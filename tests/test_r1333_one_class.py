"""R-1333: ledgerplan.class_info is the only class function. daily, the plan, the order and taskrank all read it."""
import glob
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "plugin", "scripts")
sys.path.insert(0, SCRIPTS)

import daily
import i18n
import ledgerorder
import ledgerplan
import taskrank


def item(n, **kw):
    return dict({"id": "R-%d" % n, "status": "open", "title": "t%d" % n, "text": "plain work", "created": "2026-11-01"}, **kw)


CASES = [
    item(1, flags=["bug"]),                                        # flagged by the flag
    item(2, text="Class A: tidy the docs"),                        # flagged by the mark
    item(3, text="Class B (process): the bug list is long"),       # the mark wins over the words
    item(4, text="a bug in the bar"),                              # inferred A
    item(5, text="a new feature for the board"),                   # inferred B
    item(6, text="plain work"),                                    # no signal
    item(7, flags=["feature"]),
]


class OneClassFunction(unittest.TestCase):
    def test_one_class_function_for_daily_plan_and_taskrank(self):
        rows = {r["id"]: r for r in taskrank.ranked({"items": CASES}, 1795000000.0)}
        for it in CASES:
            cls, source = ledgerplan.class_info(it)
            self.assertEqual(ledgerplan.item_class(it), cls, it["id"])
            self.assertEqual((rows[it["id"]]["class"], rows[it["id"]]["class_source"]), (cls, source), it["id"])   # taskrank
            want = ("A" if source == "flagged" else "A?") if cls == "A" else "B"
            self.assertEqual(daily.class_mark(it), want, it["id"])                                                 # daily
            self.assertIn(source, ("flagged", "inferred"))
        self.assertEqual([ledgerplan.class_info(i) for i in CASES[:5]],
                         [("A", "flagged"), ("A", "flagged"), ("B", "flagged"), ("A", "inferred"), ("B", "inferred")])

    def test_no_second_classifier_remains(self):
        defs = []
        for path in sorted(glob.glob(os.path.join(SCRIPTS, "*.py"))):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            if "def item_class(" in src or "def class_info(" in src:
                defs.append(os.path.basename(path))
        self.assertEqual(defs, ["ledgerplan.py"])
        for name in ("daily.py", "taskrank.py", "ledgerorder.py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
                src = f.read()
            for needle in ("def classify(", "CLASS_MARK", "CLASS_A_WORDS", "BUG_WORDS.search"):
                self.assertNotIn(needle, src, "%s still holds its own class reader: %s" % (name, needle))

    def test_reason_line_says_inferred_only_when_the_class_is_inferred(self):
        base = {"type": 2, "class": "A", "her_words": 0, "unblocks": 0, "release": 0, "late_days": 0}
        for lang, word in (("es", "inferido"), ("en", "inferred")):
            self.assertIn(word, ledgerorder.reason_line(dict(base, class_source="inferred"), lang))
            self.assertNotIn(word, ledgerorder.reason_line(dict(base, class_source="flagged"), lang))
        self.assertIn("ledger.order_why_inferred", ledgerorder.REASON_KEYS)
        for lang in i18n.LANGS:
            self.assertIn("ledger.order_why_inferred", i18n.CATALOG[lang])


if __name__ == "__main__":
    unittest.main()
