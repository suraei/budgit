import time
import unittest

import agentcap

QUOTE = "Her words: «spring es un ejemplo pero va a pasar con muchas mas tecnologias».\nBuild the check.\n"


def call(prompt):
    return agentcap.example_only_warning({"prompt": prompt})


class ExampleOnlyTest(unittest.TestCase):
    def test_quoted_example_without_rule_warns(self):
        w = call(QUOTE)
        self.assertIsNotNone(w)
        self.assertTrue(w.startswith("R-929:"))
        self.assertNotIn("\n", w)

    def test_rule_line_passes(self):
        self.assertIsNone(call(QUOTE + "Rule: every technology she names is one case of the general ask\n"))
        self.assertIsNone(call(QUOTE + "Regla: cada tecnologia es un caso\n"))

    def test_quote_without_marker_passes(self):
        self.assertIsNone(call("Her words: «arregla el boton de guardar».\n"))

    def test_marker_outside_quotes_passes(self):
        self.assertIsNone(call("For example, build it. Her words: «arregla el boton».\n"))

    def test_non_dict_input_passes(self):
        self.assertIsNone(agentcap.example_only_warning(None))

    def test_200k_unclosed_guillemets_is_linear(self):
        start = time.process_time()                                      # CPU time, not wall (R-1282)
        w = call("«" * 200000)
        self.assertLess(time.process_time() - start, 1.0)
        self.assertIsNone(w)


if __name__ == "__main__":
    unittest.main()
