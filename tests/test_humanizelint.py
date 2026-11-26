"""R-267: the mechanical half of the humanize skill (layer 1 gate H4-H11, layer 2 word hygiene).
The Spanish lists keep only what a review against stop-slop confirmed, and the owner has not reviewed them; the tests pin behaviour, not Spanish quality."""
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT

sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import humanizelint as hl  # noqa: E402

SCRIPT = os.path.join(ROOT, "plugin", "scripts", "humanizelint.py")


def rules(text, **kw):
    return [f.rule for f in hl.scan(text, **kw)]


class GateTest(unittest.TestCase):
    def test_clean_text_has_no_findings(self):
        text = "Sync took 40 ms on a 10k-note vault, down from 120 ms in 0.12 (see `docs/bench.md`).\n"
        self.assertEqual(hl.scan(text), [])

    def test_h8_flags_guarantee_words_in_english(self):
        found = rules("It never fails and guarantees seamless sync.")
        self.assertEqual(found.count("H8"), 3)

    def test_h8_matches_whole_words_only(self):
        self.assertEqual(rules("The neverending story of alwaysfoo."), [])

    def test_h8_spanish_is_marked_unreviewed(self):
        findings = [f for f in hl.scan("Es una experiencia sin fricción para todos.", lang="es") if f.rule == "H8"]
        self.assertEqual(len(findings), 1)
        self.assertTrue(all("unreviewed" in f.message.lower() for f in findings))

    def test_h5_vague_method_names(self):
        self.assertIn("H5", rules("We apply robust strategies and best practices."))
        self.assertEqual(rules("Aplicamos mejores prácticas y estrategias integrales.", lang="es"), [])      # ordinary Spanish words: the review kept none

    def test_h6_empty_background(self):
        self.assertIn("H6", rules("Over the years the tool has evolved."))
        self.assertIn("H6", rules("En el mundo actual todo cambia.", lang="es"))

    def test_code_urls_and_inline_code_are_not_scanned(self):
        text = "Run `never_fail --always` now.\n\n```\nalways guarantees\n```\n\nSee https://example.com/always/best-practices.\n"
        self.assertEqual(hl.scan(text), [])

    def test_h4_percentage_without_comparison(self):
        self.assertIn("H4", rules("Sync is 40% faster."))

    def test_h4_percentage_with_comparison_passes(self):
        self.assertNotIn("H4", rules("Sync is 40% faster than v0.12."))
        self.assertNotIn("H4", rules("La sincronización va un 40 % más rápida frente a la 0.12.", lang="es"))

    def test_h9_long_doc_without_limits_section(self):
        body = "Some sentence about the tool and what it does for the reader today. " * 40
        self.assertIn("H9", rules("# Tool\n\n" + body, limits=True))

    def test_h9_limits_heading_satisfies_it(self):
        body = "Some sentence about the tool and what it does for the reader today. " * 40
        self.assertNotIn("H9", rules("# Tool\n\n" + body + "\n\n## Limits\n\nNo Windows.\n", limits=True))
        self.assertNotIn("H9", rules("# Tool\n\n" + body + "\n\n## Límites\n\nSin Windows.\n", lang="es", limits=True))

    def test_h9_short_doc_and_ui_strings_are_exempt(self):
        self.assertNotIn("H9", rules("# Tool\n\nShort page.\n", limits=True))
        body = "Some sentence about the tool and what it does for the reader today. " * 40
        self.assertNotIn("H9", rules(body, kind="ui", limits=True))

    def test_h9_is_off_unless_asked_for(self):
        body = "Some sentence about the tool and what it does for the reader today. " * 40
        self.assertNotIn("H9", rules("# FAQ\n\n" + body))

    def test_ordinary_spanish_words_are_not_flagged(self):
        for text in ("Siempre llego tarde y nunca me quejo.", "La ley garantiza el derecho y eliminar el archivo es posible.",
                     "Funciona perfectamente por completo.", "No solo compramos pan, sino también leche."):
            self.assertEqual(rules(text, lang="es"), [], text)

    def test_the_confirmed_spanish_crutches_are_flagged(self):
        for text in ("Cabe destacar que el plan es sólido y claro.", "Realmente creo que esto sirve para todos.",
                     "No es un problema de tiempo, es un problema de foco."):
            self.assertTrue(rules(text, lang="es"), text)

    def test_siempre_in_de_siempre_is_not_a_claim(self):
        self.assertEqual(rules("La línea de siempre vuelve pronto.", lang="es"), [])

    def test_allow_marker_on_the_same_line_silences_that_rule_there_only(self):
        text = "It never fails. <!-- humanizelint: ok H8 -->\nIt never rains.\n"
        self.assertEqual([(f.rule, f.line) for f in hl.scan(text)], [("H8", 2)])

    def test_allow_marker_does_not_silence_other_rules(self):
        text = "It never fails and is really fast. <!-- humanizelint: ok H8 -->\n"
        self.assertEqual(rules(text), ["W2"])

    def test_allow_argument_by_rule_and_text(self):
        self.assertEqual(rules("It never fails.", allow=("H8:never",)), [])
        self.assertEqual(rules("It never fails and guarantees it.", allow=("H8:never",)), ["H8"])
        self.assertEqual(rules("It never fails and guarantees it.", allow=("H8",)), [])

    def test_h11_number_without_source(self):
        self.assertIn("H11", rules("Users save 120 hours a year."))

    def test_h11_source_nearby_passes(self):
        self.assertNotIn("H11", rules("Users save 120 hours a year ([study](https://example.com/s))."))
        self.assertNotIn("H11", rules("Users save 120 hours a year, measured in `docs/bench.md`."))
        self.assertNotIn("H11", rules("Users save 120 hours a year (R-250)."))

    def test_h11_ignores_versions_dates_and_tables(self):
        self.assertNotIn("H11", rules("Released as 0.13.1 on 2026-10-25."))
        self.assertNotIn("H11", rules("| Where | Result |\n| --- | 120 hours |\n"))

    def test_ui_kind_skips_document_level_rules(self):
        found = rules("Sync is 40% faster. Users save 120 hours.", kind="ui")
        self.assertNotIn("H4", found)
        self.assertNotIn("H11", found)

    def test_finding_has_line_and_column(self):
        f = hl.scan("First line.\nSecond line.\nIt never fails.\n")[0]
        self.assertEqual((f.rule, f.line, f.col, f.match), ("H8", 3, 4, "never"))


class WordHygieneTest(unittest.TestCase):
    def test_throat_clearing(self):
        self.assertIn("W1", rules("Here's the thing: sync is slow."))

    def test_intensifiers_but_not_every_adverb(self):
        self.assertIn("W2", rules("This is really fast."))
        self.assertEqual(rules("It quickly starts and slowly stops."), [])

    def test_full_stop_as_punctuation_advice_is_not_a_crutch(self):
        self.assertEqual(rules("Use a comma or a full stop here."), [])

    def test_off_marker_exempts_a_region_and_keeps_line_numbers(self):
        text = "<!-- humanizelint: off -->\nIt never fails.\n<!-- humanizelint: on -->\nIt never rains.\n"
        found = hl.scan(text)
        self.assertEqual([(f.rule, f.line) for f in found], [("H8", 4)])

    def test_off_marker_may_carry_a_note(self):
        self.assertEqual(hl.scan("<!-- humanizelint: off (this page quotes the patterns) -->\nIt never fails.\n"), [])

    def test_off_marker_without_on_runs_to_the_end(self):
        self.assertEqual(hl.scan("# Rules\n<!-- humanizelint: off -->\nIt never fails.\n"), [])

    def test_jargon(self):
        self.assertIn("W3", rules("A real game-changer, so let's circle back."))

    def test_binary_contrast(self):
        self.assertIn("W4", rules("It's not a tool, it's a teammate."))
        self.assertIn("W4", rules("It isn't about speed. It's about trust."))

    def test_meta_commentary(self):
        self.assertIn("W5", rules("In this section, we'll look at sync."))

    def test_em_dash_is_style_in_english_only(self):
        self.assertIn("W6", rules("Sync is fast — very fast."))
        self.assertNotIn("W6", rules("Va rápido — muy rápido.", lang="es"))

    def test_passive_and_wh_openers_are_not_banned(self):
        # stop-slop bans both; they clash with ux-copy's own error pattern, so layer 2 drops them
        self.assertEqual(rules("Your card was declined by your bank. What happened: the bank said no."), [])

    def test_spanish_calques(self):
        self.assertIn("W1", rules("Lleva tu proyecto al siguiente nivel y desbloquea todo.", lang="es"))
        self.assertIn("W4", rules("No es una herramienta, es un compañero.", lang="es"))
        self.assertIn("W3", rules("Se ha procedido a la realización de la gestión.", lang="es"))

    def test_layers_are_labelled(self):
        gate = hl.scan("It never fails.")[0]
        style = hl.scan("This is really fast.")[0]
        self.assertEqual((gate.layer, style.layer), (1, 2))


class LangTest(unittest.TestCase):
    def test_detects_spanish_and_english(self):
        self.assertEqual(hl.detect_lang("La sincronización de las notas va para todos los usuarios con un plan."), "es")
        self.assertEqual(hl.detect_lang("The sync of the notes runs for all of the users with a plan."), "en")

    def test_auto_picks_the_spanish_list(self):
        found = rules("Esto funciona para los usuarios de la plataforma y es una experiencia sin fricción de verdad para todos.")
        self.assertIn("H8", found)


class CliTest(unittest.TestCase):
    def run_cli(self, text, *args):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return subprocess.run([sys.executable, "-I", SCRIPT, *args, path], capture_output=True, text=True)

    def test_gate_finding_exits_1_and_prints_location(self):
        r = self.run_cli("It never fails.\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn(":1:", r.stdout)
        self.assertIn("H8", r.stdout)

    def test_style_only_findings_exit_0(self):
        r = self.run_cli("This is really fast.\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("W2", r.stdout)

    def test_cli_require_limits_and_allow(self):
        body = "Some sentence about the tool and what it does for the reader today. " * 40
        self.assertNotIn("H9", self.run_cli(body).stdout)
        self.assertIn("H9", self.run_cli(body, "--require-limits").stdout)
        self.assertEqual(self.run_cli("It never fails.\n", "--allow", "H8:never").returncode, 0)

    def test_clean_file_says_so(self):
        r = self.run_cli("Sync is fast on small vaults.\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no findings", r.stdout)

    def test_kind_and_lang_flags(self):
        r = self.run_cli("Es un uso sin fricción.\n", "--lang", "es", "--kind", "ui")
        self.assertIn("H8", r.stdout)


class BudgitCommandTest(unittest.TestCase):
    """R-267 phase 3: `budgit humanize-lint` runs the same checks and the skill ships inside the kickoff skill."""
    BIN = os.path.join(ROOT, "plugin", "bin", "budgit")

    def run_budgit(self, text, *args):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return subprocess.run([sys.executable, self.BIN, "humanize-lint", *args, path], capture_output=True, text=True,
                                  env=dict(os.environ, BUDGIT_LANG="en"))

    def test_command_exits_1_on_a_gate_finding(self):
        r = self.run_budgit("It never fails.\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("H8", r.stdout)

    def test_command_passes_options_through(self):
        self.assertEqual(self.run_budgit("It never fails.\n", "--allow", "H8:never").returncode, 0)
        self.assertEqual(self.run_budgit("This is really fast.\n").returncode, 0)

    def test_skill_files_live_in_the_shipped_skill(self):
        base = os.path.join(ROOT, "skills", "budgit-kickoff", "humanize")
        for name in ("humanize.md", "registry-entry.json", os.path.join("reference", "layer2-en.md"), os.path.join("reference", "layer2-es.md")):
            self.assertTrue(os.path.isfile(os.path.join(base, name)), name)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "skills", "budgit-humanize")))


if __name__ == "__main__":
    unittest.main()
