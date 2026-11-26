"""R-997: a `read` value is unresolved input, so a write through it asks (not deny); a literal same-command assignment that
reaches verify still denies; the ask text goes through i18n in es and en, with no internal id and no English in es."""
import os
import re

import helpers  # noqa: F401
from helpers import StudioTestCase
import i18n
import studiolib as sl


class ReadAskI18nTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.data = os.path.join(self.project, ".budgit")
        os.makedirs(os.path.join(self.data, "verify"), exist_ok=True)

    def d(self, command, cwd):
        return sl.verify_forgery_decision("Bash", {"command": command}, "general-purpose", cwd)

    def test_while_read_loop_is_not_denied(self):
        got = self.d('while read f; do rm "$f"; done < list', self.project)
        self.assertTrue(got is None or got[0] != "deny", got)

    def test_read_target_asks(self):
        got = self.d("read X; tee $X/x", self.data)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "ask")

    def test_literal_not_verify_has_no_decision(self):
        self.assertIsNone(self.d("X=notes; tee $X/x", self.data))

    def test_literal_verify_denies(self):
        for c in ("X=verify; tee $X/x", "X=v; tee ${X}erify/x"):
            got = self.d(c, self.data)
            self.assertIsNotNone(got, c)
            self.assertEqual(got[0], "deny", c)

    def test_ask_text_es_and_en(self):
        for lang in ("es", "en"):
            text = i18n.t("guard.verify_unresolved_ask", lang=lang)
            self.assertNotIn("guard.verify_unresolved_ask", text)
            self.assertIsNone(re.search(r"\bR-\d", text), text)
        es = i18n.t("guard.verify_unresolved_ask", lang="es")
        self.assertNotEqual(es, i18n.t("guard.verify_unresolved_ask", lang="en"))
        for word in ("verify", "target", "write", "folder", "report"):
            self.assertIsNone(re.search(r"\b%s\b" % word, es, re.I), (word, es))
