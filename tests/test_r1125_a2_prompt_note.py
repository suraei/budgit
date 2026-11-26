"""R-1125 A2: a prompt with a tax or legal word gets a note naming the legal-eu-es-fr skill (panel and checker); the skill
descriptions carry the trigger words; the registry has the four tax and filing jobs. Advice only: the prompt is never blocked."""
import json
import os
import re
import subprocess
import sys
from unittest import mock

from helpers import BudgitTestCase, ROOT
import agentgate
import hook
import i18n

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
SKILL = os.path.join(ROOT, "skills", "legal-eu-es-fr")
PROMPTS = {
    "es": "¿Qué gastos deducibles puedo meter en el modelo 303 como autónomo?",
    "fr": "Quels frais professionnels puis-je déduire de ma TVA en micro-entrepreneur ?",
    "en": "How much income tax do I owe as a self-employed freelancer in Spain?",
}
TRIGGERS = ("impuestos", "autónomo", "IVA", "TVA", "gastos deducibles", "frais professionnels", "2035", "URSSAF",
            "modelo 303", "130", "100", "déclaration")
PEOPLE = ("employee", "micro-entrepreneur", "BNC réel", "autónomo", "company", "cross-border")


def frontmatter_description(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    line = [ln for ln in m.group(1).splitlines() if ln.startswith("description:")][0]
    return line[len("description:"):].strip()


class PromptNoteTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)

    def note(self, prompt):
        hook._OUT.clear()
        hook.legal_prompt_context({"session_id": "s1", "prompt": prompt})
        return hook._OUT.get("hookSpecificOutput", {}).get("additionalContext")

    def test_a_spanish_tax_prompt_gets_the_note(self):
        text = self.note(PROMPTS["es"])
        self.assertIn("legal-eu-es-fr", text)
        self.assertIn("panel", text)
        self.assertIn("checker", text)
        self.assertIn("fiscal-es-fr", text)

    def test_a_french_tax_prompt_gets_the_note(self):
        self.assertIn("legal-eu-es-fr", self.note(PROMPTS["fr"]))

    def test_an_english_tax_prompt_gets_the_note(self):
        self.assertIn("legal-eu-es-fr", self.note(PROMPTS["en"]))

    def test_no_note_on_a_non_tax_prompt(self):
        self.assertIsNone(self.note("add a login page and fix the button colour"))
        self.assertIsNone(self.note(""))
        self.assertIsNone(self.note(None))

    def test_a_huge_prompt_is_read_only_at_its_start_and_fast(self):
        import time
        late = "x" * 1100000 + " impuestos"
        start = time.process_time()                                      # CPU time, not wall (R-1282)
        self.assertIsNone(self.note(late))
        self.assertLess(time.process_time() - start, 1.0)
        self.assertIn("legal-eu-es-fr", self.note("impuestos " + "x" * 1100000))

    def test_the_note_never_holds_the_prompt_text_and_never_blocks(self):
        text = self.note(PROMPTS["es"])
        self.assertNotIn("gastos", text)
        self.assertNotIn("decision", json.dumps(hook._OUT))
        self.assertNotIn("permissionDecision", json.dumps(hook._OUT))

    def test_detection_raising_gives_no_block_and_no_note_and_is_logged(self):
        with mock.patch.object(agentgate, "legal_word", side_effect=RuntimeError("boom")), \
                mock.patch.object(hook.sl, "log_error") as log:
            self.assertIsNone(self.note(PROMPTS["es"]))
        self.assertEqual(hook._OUT, {})
        log.assert_called_once()

    def test_the_note_text_exists_in_english_and_spanish(self):
        for lang in ("en", "es"):
            self.assertIn("legal.prompt_note", i18n.CATALOG[lang])
            self.assertIn("legal-eu-es-fr", i18n.t("legal.prompt_note", lang))

    def test_the_real_hook_adds_the_note_for_the_three_languages_and_not_otherwise(self):
        env = dict(os.environ, STUDIO_HOME=self.home)
        for lang, prompt in list(PROMPTS.items()) + [("none", "rename the helper function")]:
            r = subprocess.run([sys.executable, HOOK, "user-prompt-submit"], input=json.dumps({"session_id": "zzz", "prompt": prompt}),
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual("legal-eu-es-fr" in r.stdout, lang != "none", (lang, r.stdout))
            self.assertNotIn('"permissionDecision"', r.stdout)
            self.assertNotIn('"decision"', r.stdout)


class SkillTextTest(BudgitTestCase):
    def test_the_description_has_the_trigger_words_and_person_types(self):
        desc = frontmatter_description(os.path.join(SKILL, "SKILL.md"))
        low = desc.casefold()
        for word in TRIGGERS + PEOPLE:
            self.assertIn(word.casefold(), low, word)
        self.assertIn("separate tax skill", desc)
        self.assertLessEqual(len(desc), 1024)

    def test_the_registry_json_is_valid_and_has_the_four_jobs(self):
        with open(os.path.join(SKILL, "registry-entry.json"), encoding="utf-8") as f:
            entry = json.load(f)
        for job in ("question/tax-fr", "question/tax-es", "filing/prepare", "filing/review"):
            self.assertIn(job, entry["fits"], job)
            self.assertIsInstance(entry["fits"][job]["fit"], int)
            self.assertTrue(entry["fits"][job].get("ev"), job)
        self.assertIn("question/legal-eu-es-fr", entry["fits"])
