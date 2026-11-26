"""R-1152: a question card that asks her to judge a file-like thing must carry a path or link. Real hook, temp home."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "scripts", "hook.py")


def card(question, desc="", label="Si"):
    return {"questions": [{"question": question, "header": "Q", "multiSelect": False,
                           "options": [{"label": label, "description": desc}, {"label": "No", "description": "Cancel"}]}]}


class CardRefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_card(self, lang, tool_input):
        env = dict(os.environ, STUDIO_HOME=os.path.join(self.tmp.name, "home"), BUDGIT_LANG=lang, CLAUDE_CODE_SESSION_ID="s1")
        for k in ("BUDGIT_HOME", "BUDGIT_JOB"):
            env.pop(k, None)
        payload = {"session_id": "s1", "cwd": self.tmp.name, "tool_name": "AskUserQuestion", "tool_input": tool_input}
        out = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), env=env,
                             capture_output=True, text=True, stdin=None)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.last = "code=%s stdout=%r stderr=%r" % (out.returncode, out.stdout, out.stderr)
        return out.stdout

    def refused(self, lang, tool_input, word):
        out = self.run_card(lang, tool_input)
        self.assertIn('"deny"', out, self.last)
        out = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(word, out, self.last)
        self.assertIn("ruta" if lang == "es" else "path", out, self.last)

    def passes(self, lang, tool_input):
        out = self.run_card(lang, tool_input)
        self.assertNotIn("deny", out, self.last)

    def test_refuse_mocks_without_path(self):
        self.refused("es", card("¿Te gustan las maquetas?"), "maquetas")
        self.refused("en", card("Do you like the mocks?"), "mocks")

    def test_refuse_option_drawn_without_link(self):
        self.refused("es", card("¿Te gusta la opción B dibujada?"), "opción B dibujada")
        self.refused("en", card("Do you like option B drawn?"), "option B drawn")

    def test_refuse_screenshot_whose_path_is_outside_the_card(self):
        self.refused("es", card("¿Vale la captura que te dejé?"), "captura")
        self.refused("en", card("Is the screenshot fine?", desc="Yes"), "screenshot")

    def test_pass_path_in_description(self):
        self.passes("es", card("¿Te gustan las maquetas?", desc="Están en /Users/x/mocks/a.html"))
        self.passes("en", card("Do you like the mocks?", desc="See ~/mocks/a.html"))

    def test_pass_link_in_question(self):
        self.passes("es", card("¿Te gusta la opción B dibujada? https://claude.ai/public/artifacts/abc"))
        self.passes("en", card("Is the page ok? file:///Users/x/p.html"))

    def test_pass_card_with_no_file_like_thing(self):
        self.passes("es", card("¿Publico ahora?"))
        self.passes("en", card("Publish now?"))

    def test_pass_design_decision_is_not_a_file(self):
        self.passes("en", card("The design decision is yours; which way?"))
        self.passes("es", card("¿Aprobamos la decisión de diseño?"))

    def test_setup_card_four_text_from_the_file_passes(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "budgit-kickoff", "reference", "setup.md")
        lines = [l for l in open(path, encoding="utf-8").read().splitlines() if l.startswith("4. Codex/ChatGPT lane")]
        self.assertEqual(len(lines), 1)
        self.passes("en", card(lines[0], desc=lines[0]))

    def test_table_of_cards_through_the_real_hook(self):
        table = [
            ("en", "Codex lane on or off (second opinion and images on the user's ChatGPT login)", True),
            ("es", "¿Te gustan las maquetas?", False),
            ("es", "¿Te gusta esta página?", False),
            ("es", "¿Te gusta esta página? https://example.com/p", True),
            ("es", "elige entre las opciones A, B, C", True),
            ("es", "revisa el archivo", False),
            ("es", "revisa ~/Projects/hub/docs/design/", True),
            ("es", "¿Aprobamos la decisión de diseño?", True),
            ("en", "screenshot at C:\\Users\\x\\shot.png", True),
            ("en", "the file format should be JSON", True),
            ("es", "mira la captura", False),
            ("es", "¿Qué te parece la referencia al archivo?", False),
            ("en", "Which design do you want?", False),
            ("en", "Which design do you want? See ~/Documents/designs/", True),
            ("es", "elige una página", False),
            ("es", "la página web de la tienda https://example.com", True),
            ("es", "el archivo Cargo.toml", False),
        ]
        for lang, text, ok in table:
            out = self.run_card(lang, card(text))
            self.assertEqual("deny" not in out, ok, "%s -> %s" % (text, self.last))

    def test_refusal_text_follows_language(self):
        self.assertIn("pero no da su ruta o enlace", self.run_card("es", card("¿Te gusta la imagen?")))
        self.assertIn("gives no path or link", self.run_card("en", card("Do you like the image?")))


if __name__ == "__main__":
    unittest.main()
