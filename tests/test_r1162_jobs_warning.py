"""R-1162: the blocked-folder warning (jobs.blocked_folder_warning) is all Spanish with es; en text is unchanged."""
import os
import re
import unittest

from helpers import StudioTestCase
import jobs

EN_WORDS = re.compile(r"\b(the|sits|may|not|run|folder|or|and|last|option|risky)\b")


class JobsWarning(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.user = os.path.realpath(os.path.join(self.tmp, "u"))
        self.proj = os.path.join(self.user, "Documents", "p")
        os.makedirs(self.proj)
        self.plugin = os.path.join(self.user, "x", "plugin", "bin", "budgit")

    def warn(self, lang):
        return jobs.blocked_folder_warning("cleanup", self.plugin, cwd=self.proj, home=self.user, lang=lang)

    def test_es_all_spanish(self):
        text = self.warn("es")
        self.assertIn("el proyecto (", text)
        self.assertNotIn("the project", text)
        stripped = re.sub(r"`[^`]*`", "", text)
        self.assertEqual(EN_WORDS.findall(stripped), [])

    def test_en_unchanged(self):
        text = self.warn("en")
        self.assertTrue(text.startswith("Warning: the project (%s) sits in a folder that macOS may block" % self.proj))
        self.assertIn("(3) last option, and risky:", text)

    def test_job_messages_es(self):
        self.assertIn("tarea desconocida", __import__("i18n").t("jh.unknown_job", "es", name="x"))
        self.assertIn("unknown job x", __import__("i18n").t("jh.unknown_job", "en", name="x"))


if __name__ == "__main__":
    unittest.main()
