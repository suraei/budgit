import unittest

import helpers  # noqa: F401
import agentcap


def warns(text):
    return agentcap._ONE_SOURCE.search(text) is not None


class OneSourceConnectors(unittest.TestCase):
    def test_comma_second_source_en(self):
        self.assertFalse(warns("take it from your report, the spec"))
        self.assertFalse(warns("take it from your report, and the spec"))

    def test_plus_ampersand_en(self):
        self.assertFalse(warns("per my report plus the logs"))
        self.assertFalse(warns("per our report & the logs"))

    def test_second_source_es(self):
        self.assertFalse(warns("según el informe y el spec"))
        self.assertFalse(warns("según el informe, el spec"))
        self.assertFalse(warns("según el informe e la guía"))

    def test_report_from_x_and_y_is_two_sources(self):
        self.assertFalse(warns("per the report from QA and the logs"))
        self.assertFalse(warns("según el informe de QA y los logs"))

    def test_docs_folder_and_report_page_do_not_warn(self):
        self.assertFalse(warns("from the docs folder"))
        self.assertFalse(warns("charge per report page"))
        self.assertFalse(warns("según el informe página 3"))

    def test_rules_alternative_connectors(self):
        self.assertFalse(warns("rules from their spec and the I-9 words"))
        self.assertFalse(warns("reglas del spec, y el diseño"))

    def test_r984_cases_still_warn(self):
        for t in ("per your report", "from their spec", "según lo que dice el informe",
                  "rules from their spec", "per the report", "según el documento",
                  "per the report from QA", "from the docs"):
            self.assertTrue(warns(t), t)


class OneSourceNounFamilies(unittest.TestCase):
    def test_round1_regressions_warn(self):
        for t in ("per the specification", "from the specification", "from the specifications",
                  "per the documentation", "from their documentation", "según la documentación",
                  "per the documents", "from its documents", "from the document",
                  "según los nuevos informes"):
            self.assertTrue(warns(t), t)

    def test_gendered_possessive_is_not_a_source_phrase(self):
        self.assertFalse(warns("per her report"))
        self.assertFalse(warns("from his documents"))

    def test_excluded_noise_words_do_not_warn(self):
        for t in ("from the reporter", "según el reportero", "from the docstring", "per the docu",
                  "from the reporting tool", "per the reportage", "from the docs folder",
                  "charge per report page"):
            self.assertFalse(warns(t), t)


if __name__ == "__main__":
    unittest.main()
