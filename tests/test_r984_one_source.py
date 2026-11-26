import unittest

import helpers  # noqa: F401
import agentcap


def call(prompt):
    return agentcap.single_source_warning({"prompt": prompt})


class OneSourcePhrasingTest(unittest.TestCase):
    def test_en_phrasings_warn(self):
        for p in ("Build X as per your report.", "Build X with the format from their spec.",
                  "Build X per the report.", "Build X, from my doc."):
            self.assertIsNotNone(call(p), p)

    def test_es_phrasings_warn(self):
        for p in ("Haz X según lo que dice el informe.", "Haz X según su informe.",
                  "Haz X según el documento.", "Haz X from su spec."):
            self.assertIsNotNone(call(p), p)

    def test_rules_from_their_spec_still_warns(self):
        self.assertIsNotNone(call("Build X with rules from their spec."))

    def test_two_sources_en_no_warning(self):
        self.assertIsNone(call("Build X from our report and the spec."))

    def test_two_sources_es_no_warning(self):
        self.assertIsNone(call("Haz X según lo que dice el informe y la especificación."))

    def test_unrelated_no_warning(self):
        for p in ("Charge 5 per minute.", "Start from the start.", "Read from the beginning, per day."):
            self.assertIsNone(call(p), p)


if __name__ == "__main__":
    unittest.main()
