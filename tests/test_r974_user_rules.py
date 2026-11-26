from helpers import StudioTestCase
import agentcap


def call(prompt):
    return agentcap.single_source_warning({"prompt": prompt})


class UserRulesTest(StudioTestCase):
    def test_rules_from_the_user_no_warning(self):
        self.assertIsNone(call("Build X. Apply the rules from the user."))

    def test_own_words_no_warning(self):
        for p in ("Build X. Use the rules from your words.",
                  "Build X. Use the rules from their rules.",
                  "Build X. Use the rules from my words."):
            self.assertIsNone(call(p), p)

    def test_spanish_user_no_warning(self):
        self.assertIsNone(call("Haz X con las reglas del usuario."))

    def test_possessive_before_document_still_warns(self):
        for p in ("Build X. Use rules from her report.md as the whole task.",
                  "Build X. Use rules from their spec doc.",
                  "Build X. Use criteria from the user's report.md.",
                  "Haz X según su informe.",
                  "Haz X con reglas del usuario-informe.pdf."):
            self.assertIsNotNone(call(p), p)
            self.assertIn("R-929", call(p), p)

    def test_real_single_source_still_warns(self):
        self.assertIn("R-929", call("Build X. Use the rules from the new report."))
        self.assertIn("R-929", call("Use the rules from the user, and the rules from the new report."))
