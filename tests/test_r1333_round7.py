"""R-1333 round 7: the user's own words only (an answer is what she chose or typed, never the assistant's question or option text),
type 3 needs visible evidence, the stops-work list, and the true days late in the reason line."""
import json
import unittest

import i18n
from test_r1333_one_order import Base, her, due, lo


def answer(n, question, options, picked, source="answer", days_ago=0):
    """An inbox entry shaped like the real AskUserQuestion answer: the card (question, header, option descriptions) next to her answers."""
    card = {"questions": [{"question": question, "header": "Header with urgente",
                           "options": [{"label": label, "description": desc} for label, desc in options], "multiSelect": False}],
            "answers": {question: picked}}
    return her(n, days_ago=days_ago, source=source, text="AskUserQuestion answer: " + json.dumps(card, ensure_ascii=False))


OPTIONS = [("Solo lo cambiado (Recomendado)", "Como hoy con la versión urgente, hoy mismo, asap"), ("Todo", "Todo el conjunto")]


class R7(Base):
    def row(self, res, item_id):
        return self.rows(res)[item_id]


class HerWordsOnly(R7):
    def test_answer_question_text_is_never_her_words(self):
        self.add(1, flags=["feature"], src=["I-1"])                 # R-790's shape: «versión urgente» only in an option description
        entry = answer(1, "¿Cómo lo hacemos? es urgente", OPTIONS, "Solo lo cambiado (Recomendado)")
        self.inbox = [entry]
        self.assertEqual(lo.user_words(entry), "Solo lo cambiado (Recomendado)")
        res = self.order()
        row = self.row(res, "R-1")
        self.assertFalse(row["urgent"])
        self.assertEqual(row["tier"], 2)
        plain = her(2, source="answer", text='User has answered your questions: "¿Algo urgente, hoy mismo?"="Sí". You can now continue.')
        self.assertEqual(lo.user_words(plain), "Sí")                # the plain-text form of the same answer
        self.assertNotIn("I-1", lo.user_texts([her(1, source="answer", text="AskUserQuestion answer: " + json.dumps({"questions": [{"question": "urgente"}]}))]))

    def test_answer_label_is_her_words(self):
        self.add(1, flags=["feature"], src=["I-1"])
        self.add(2, flags=["feature"], src=["I-2"])
        self.inbox = [answer(1, "¿Cuándo?", OPTIONS, "Es urgente, hoy mismo"), answer(2, "¿Cuándo?", OPTIONS, ["Todo", "asap"])]
        self.assertEqual(lo.user_words(self.inbox[0]), "Es urgente, hoy mismo")
        self.assertEqual(lo.user_words(self.inbox[1]), "Todo | asap")      # a multi-select: every label she ticked
        res = self.order()
        for iid in ("R-1", "R-2"):
            self.assertTrue(self.row(res, iid)["urgent"], iid)
            self.assertEqual(self.row(res, iid)["tier"], 0, iid)
        self.assertEqual(self.row(res, "R-1")["her_words"], 1)

    def test_hand_due_date_words_are_her_words_only(self):
        self.add(1, due=due(5), appetite="large", src=["I-1"])      # R-731's shape: the date is in the assistant's question
        self.add(2, due=due(5), appetite="large", src=["I-2"])
        self.inbox = [answer(1, "¿Desde el 2026-11-13 lo cerramos?", OPTIONS, "Mastodon"),
                      answer(2, "¿Para cuándo?", OPTIONS, "Para el 2026-11-20")]
        res = self.order()
        self.assertFalse(self.row(res, "R-1")["urgent"])
        self.assertTrue(self.row(res, "R-2")["urgent"])

    def test_an_answer_with_no_value_is_not_her_words(self):
        entry = her(1, source="answer", text="AskUserQuestion answer: " + json.dumps({"questions": [{"question": "q"}], "answers": {}}))
        self.assertIsNone(lo.user_words(entry))
        self.assertEqual(lo.user_index([entry]), {})


class Type3Evidence(R7):
    def test_type3_needs_visible_evidence(self):
        self.add(1, flags=["bug"], src=["I-1"], area="release")     # R-1255's shape: her word is the origin, nothing is visible
        self.add(2, flags=["bug"], src=["I-2"], area="load")
        self.add(3, flags=["bug", "visible"], src=["I-3"])           # only the explicit mark makes type 3
        self.add(4, flags=["bug"], area="brote")                    # a visible area is not a mark
        self.inbox = [her(1, text="brote hoy debe quedar correcto y al dia"), her(2, text="hoy la pantalla tarda demasiado en abrir"),
                      her(3, text="hoy no se ve el boton de arriba")]
        res = self.order()
        self.assertEqual([self.row(res, "R-%d" % i)["type"] for i in (1, 2, 3, 4)], [2, 2, 3, 2])
        self.assertNotIn(i18n.t("ledger.order_why_seen", lang="es"), lo.reason_line(self.row(res, "R-1"), "es"))     # no «arreglo de algo que ves»


class StopsWords(R7):
    PHRASES_ES = ("este blocker y otros blockers estan ralentizando varias tareas del dia",
                  "me frena todo", "no puedo seguir", "esto ralentiza", "me impidió trabajar", "impide el arranque")
    PHRASES_EN = ("it slows me down", "this blocks me", "it stopped me", "I cannot continue", "a blocker")

    def test_stops_words_cover_the_real_words_of_r1135(self):
        rx = lo._rx(lo.STOPS_KEY)
        for phrase in self.PHRASES_ES + self.PHRASES_EN:
            self.assertIsNotNone(rx.search(phrase), phrase)
        for phrase in ("el bloque de código", "un bloqueo cualquiera", "ya está"):         # explicit phrases only, never a bare «bloque»
            self.assertIsNone(rx.search(phrase), phrase)
        ux = [{"id": "F1", "sev": "MED", "text": "x", "state": "open", "at": "2026-11-20T00:00:00.000Z", "by": None, "class": "ux"}]
        self.add(1, flags=["bug", "visible"], src=["I-1"], findings=ux)           # R-1135's shape: an open ux finding and her typed answer (I-1710)
        self.inbox = [answer(1, "¿Qué pasa?", OPTIONS, self.PHRASES_ES[0])]
        row = self.row(self.order(), "R-1")
        self.assertTrue(row["stops"])
        self.assertEqual(row["type"], 3)
        self.assertIn("te impide trabajar", lo.reason_line(row, "es"))


class LateDays(R7):
    def test_reason_line_shows_the_true_days_late_not_the_cap(self):
        self.add(1, flags=["bug"], due=due(11), appetite="small")   # R-977's shape: a real 11 days showed 7
        res = self.order()
        row = self.row(res, "R-1")
        self.assertEqual((row["late_days"], row["late_real"]), (lo.LATE_CAP_DAYS, 11))     # the key keeps the cap, the line shows the truth
        self.assertIn("hace 11 días", lo.reason_line(row, "es"))
        self.assertNotIn("hace 7 días", lo.reason_line(row, "es"))


if __name__ == "__main__":
    unittest.main()
