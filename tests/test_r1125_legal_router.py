"""R-1125: an Agent or Task brief with a tax or legal word is denied unless it carries a route LINE: `LEGAL: legal-eu-es-fr panel
checker`, or `LEGAL: none - <reason>` (audited). Neutral names only."""
import os
import unittest
from unittest import mock

from helpers import BudgitTestCase, patch_env
import agentgate
import hook
import i18n
import studiolib as sl

ROUTE = "LEGAL: legal-eu-es-fr panel checker"
ZW, ZWJ, ZWNJ, WJ, BOM, SHY = (chr(c) for c in (0x200B, 0x200D, 0x200C, 0x2060, 0xFEFF, 0x00AD))   # all Unicode category Cf


def spawn(kind, prompt, tool="Agent"):
    return {"tool_name": tool, "session_id": "s1", "tool_input": {"model": "sonnet", "subagent_type": kind, "prompt": prompt}}


def denies(kind, prompt):
    return agentgate.deny_reason({"subagent_type": kind, "prompt": prompt})


class LegalRouterTest(BudgitTestCase):
    def run_hook(self, payload, project):
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use(payload, project)
        return hook._OUT

    def decision(self, out):
        return out.get("hookSpecificOutput", {}).get("permissionDecision")

    def audit_text(self):
        path = os.path.join(sl.studio_dir(self.project), "audit.log")
        return open(path, encoding="utf-8").read() if os.path.exists(path) else ""

    # --- the LEGAL line ------------------------------------------------------
    def test_a_builder_brief_with_tva_and_no_legal_line_is_denied_and_names_both_forms(self):
        out = self.run_hook(spawn("studio:builder", "Compute the quarterly TVA for the sample shop."), self.tmp)
        self.assertEqual(self.decision(out), "deny")
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        for part in ("TVA", "legal-eu-es-fr", "panel", "checker", "LEGAL: legal-eu-es-fr panel checker", "LEGAL: none - "):
            self.assertIn(part, reason)

    def test_each_legal_route_form_passes(self):
        for line in (ROUTE, "legal: Legal-EU-ES-FR PANEL CHECKER", "  LEGAL:   legal-eu-es-fr  panel   checker  ",
                     "LEGAL:legal-eu-es-fr panel checker", "LEGAL: legal-eu-es-fr panel checker.", "LEGAL: none - plain GDPR note",
                     "LEGAL: none – the brief only mentions VAT as a variable name", "LEGAL:none-a variable called tax"):
            out = self.run_hook(spawn("studio:builder", "Compute the quarterly TVA.\n" + line + "\nthen stop"), self.tmp)
            self.assertNotEqual(self.decision(out), "deny", line)
            self.assertIsNone(denies("studio:builder", "TVA.\n" + line), line)

    def test_a_route_line_missing_one_of_the_three_words_is_denied(self):
        for line in ("LEGAL: legal-eu-es-fr panel", "LEGAL: legal-eu-es-fr checker", "LEGAL: panel checker", "LEGAL: legal-eu-es-fr"):
            self.assertTrue(denies("studio:builder", "TVA.\n" + line), line)

    def test_the_route_line_is_exact_in_words_and_order(self):
        for line in ("LEGAL: do not use legal-eu-es-fr panel checker", "LEGAL: checker panel legal-eu-es-fr",
                     "LEGAL: panel, checker and legal-eu-es-fr", "LEGAL: legal-eu-es-fr panel checker and more words",
                     "LEGAL: legal-eu-es-fr panel checker x", "LEGAL: legal-eu-es-fr, panel, checker",
                     "LEGAL: never legal-eu-es-fr panel checker"):
            self.assertTrue(denies("studio:builder", "TVA.\n" + line), line)

    def test_the_three_words_spread_over_several_lines_are_denied(self):
        self.assertTrue(denies("studio:builder", "TVA.\nLEGAL: legal-eu-es-fr panel\nchecker"))
        self.assertTrue(denies("studio:builder", "TVA.\nLEGAL: legal-eu-es-fr\npanel checker"))

    def test_legal_none_without_a_reason_is_denied(self):
        for line in ("LEGAL: none -", "LEGAL: none - ", "LEGAL: none", "LEGAL: none - -", "LEGAL: none –", "LEGAL:"):
            self.assertTrue(denies("studio:builder", "TVA.\n" + line), line)

    def test_a_negated_sentence_naming_the_skill_without_a_legal_line_is_denied(self):
        for text in ("TVA. Do not use the legal-eu-es-fr skill, no panel and no checker.",
                     "TVA. Use the legal-eu-es-fr skill in panel mode with its checker.",
                     "TVA. See LEGAL: legal-eu-es-fr panel checker for the format.",
                     "TVA. legal-eu-es-fr panel checker"):
            self.assertTrue(denies("studio:builder", text), text)

    def test_legal_none_with_a_reason_writes_an_audit_line(self):
        self.init_project()
        out = self.run_hook(spawn("studio:builder", "TVA variable.\nLEGAL: none - a code identifier, no tax advice"), self.project)
        self.assertNotEqual(self.decision(out), "deny")
        lines = self.audit_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("legal-none", lines[0])
        self.assertIn("a code identifier, no tax advice", lines[0])

    def test_audit_fields_are_cleaned_so_a_spawn_writes_exactly_one_line(self):
        self.init_project()
        kind = "general-purpose\n2026-11-13T00:00:00 accept-edit: x\r\x1b[31m\x07\x00" + ZW + "\x7f"
        prompt = "TVA\nALL-TOOLS: a browser is needed\nLEGAL: none - a\x1b[31m red\x07\x00 b" + ZW + "c\x7f  d"
        out = self.run_hook(spawn(kind, prompt), self.project)
        self.assertNotEqual(self.decision(out), "deny")
        lines = self.audit_text().splitlines()
        self.assertEqual(len(lines), 1)
        for bad in ("\x1b", "\x07", "\x00", "\x7f", ZW, "\r", "\n"):
            self.assertNotIn(bad, lines[0])
        self.assertTrue(lines[0].split(" ", 1)[1].startswith("legal-none: general-purpose "), lines[0])
        self.assertIn("a[31m red bc d", lines[0])

    def test_audit_fields_keep_their_caps(self):
        notes = []
        self.assertIsNone(agentgate.legal_reason({"subagent_type": "t" * 100, "prompt": "TVA\nLEGAL: none - " + "r" * 500}, notes))
        self.assertEqual(len(notes), 1)
        self.assertIn("t" * 40, notes[0])
        self.assertNotIn("t" * 41, notes[0])
        self.assertIn("r" * 200, notes[0])
        self.assertNotIn("r" * 201, notes[0])

    def test_the_audit_line_is_booked_through_the_defer_list_not_the_guard(self):
        self.init_project()
        self.assertIsNone(agentgate.deny_reason({"subagent_type": "studio:builder", "prompt": "TVA\nLEGAL: none - a variable"}))
        self.assertEqual(self.audit_text(), "")                       # the guard alone writes nothing
        notes = []
        agentgate.deny_reason({"subagent_type": "studio:builder", "prompt": "TVA\nLEGAL: none - a variable"}, notes)
        self.assertEqual(self.audit_text(), "")
        self.assertEqual(len(notes), 1)
        todo = []
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use(spawn("studio:builder", "TVA\nLEGAL: none - a variable name"), self.project, todo)
        self.assertEqual(self.audit_text(), "")                       # decided, not yet booked
        self.assertEqual(len(todo), 2)                                # the audit booking, then the R-1213 run tracking
        for book in todo:
            book()
        self.assertEqual(len(self.audit_text().splitlines()), 1)

    def test_every_form_of_the_tax_model_names_triggers_and_a_year_does_not(self):
        for text in ("modelo 303", "modelo nº 130", "modelo 036", "Modelo n.º 100", "modelo no 037", "MODELO Nº  303",
                     "modelo n.o 036", "el modelo 100 del trimestre"):
            self.assertTrue(denies("studio:builder", text), text)
        for text in ("modelo 3030", "modelo no 3030", "modelo 12", "modelo 2035", "modelo nº 1300", "modelo n 303x"):
            self.assertIsNone(denies("studio:builder", text), text)
        self.assertIn("modelo 130", denies("studio:builder", "modelo nº 130"))

    def test_no_audit_line_for_a_route_line_a_denied_brief_or_a_brief_with_no_legal_word(self):
        self.init_project()
        self.run_hook(spawn("studio:builder", "TVA.\n" + ROUTE), self.project)
        self.run_hook(spawn("studio:builder", "TVA with no line"), self.project)
        self.run_hook(spawn("studio:builder", "refactor the parser\nLEGAL: none - nothing legal here"), self.project)
        self.assertNotIn("legal-none", self.audit_text())

    # --- every type ------------------------------------------------------------
    def test_a_researcher_brief_in_spanish_with_irpf_is_denied(self):
        out = self.run_hook(spawn("studio:researcher", "Resume como se calcula el IRPF de la tienda de ejemplo."), self.tmp)
        self.assertEqual(self.decision(out), "deny")
        self.assertIn("legal-eu-es-fr", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_researcher_brief_in_english_with_vat_is_denied(self):
        out = self.run_hook(spawn("budgit:researcher", "Summarise how VAT applies to the sample shop."), self.tmp)
        self.assertEqual(self.decision(out), "deny")

    def test_it_applies_to_every_subagent_type_but_a_fork(self):
        for kind in ("studio:builder", "budgit:researcher", "studio:verifier", "budgit:scout-reader"):
            self.assertIn("legal-eu-es-fr", denies(kind, "VAT question") or "", kind)
        gp = denies("general-purpose", "VAT question\nALL-TOOLS: needs the browser")
        self.assertIn("legal-eu-es-fr", gp or "")

    def test_the_task_tool_name_is_checked_like_agent(self):
        out = self.run_hook(spawn("studio:builder", "VAT question", tool="Task"), self.tmp)
        self.assertEqual(self.decision(out), "deny")

    # --- the word list ------------------------------------------------------
    def test_words_that_only_contain_a_legal_word_do_not_trigger(self):
        for text in ("fix the syntax error", "build the taxonomy view", "plan for the year 2035", "see line 2035 of the file",
                     "retry 2035 times", "the ivanov module", "modelo 3030", "ca30 and ca123", "the CFEx flag",
                     "numbers 303 130 100 and 2024 alone", "un agente autónomo", "agentes autónomos y autónoma",
                     "reta al otro equipo", "RETA", "el autonomo", "ivory and tvalue", "vatx", "modelo no 3030"):
            self.assertIsNone(denies("studio:builder", text), text)

    def test_the_new_spanish_and_french_phrases_trigger(self):
        for text in ("el trabajador autónomo", "TRABAJADOR AUTONOMO", "la cuota de autónomo", "la cuota de autónomos",
                     "el alta de autónomo", "un autoentrepreneur", "he is self employed", "modelo nº 303",
                     "modelo n.º 303", "modelo no 303", "Modelo Nº  303", "the GDPR", "el RGPD"):
            self.assertTrue(denies("studio:builder", text), text)

    def test_2035_triggers_only_in_its_form_names(self):
        for text in ("fill 2035-A", "fill 2035-b", "le formulaire 2035", "la déclaration 2035", "LA DECLARATION 2035"):
            self.assertTrue(denies("studio:builder", text), text)
        for text in ("2035", "in 2035", "line 2035", "formulaire 2036", "the 2035 roadmap"):
            self.assertIsNone(denies("studio:builder", text), text)

    def test_factura_and_filing_need_their_partner_word(self):
        self.assertIsNone(denies("studio:builder", "send the factura to the shop"))
        self.assertIsNone(denies("studio:builder", "the filing cabinet and the filing system"))
        self.assertTrue(denies("studio:builder", "la factura con IVA"))
        self.assertTrue(denies("studio:builder", "la factura y el impuesto"))
        self.assertTrue(denies("studio:builder", "filing the tax form"))
        self.assertTrue(denies("studio:builder", "filing the VAT return"))

    def test_accents_are_folded_and_words_match_whole_in_any_case(self):
        for text in ("Retención del 15", "la RETENCION", "gasto déductible", "gasto DEDUCTIBLE", "l'impôt", "l'IMPOT",
                     "residencia fiscal", "Residencia  Fiscal", "modelo 303", "Modelo   130", "micro-entrepreneur",
                     "Auto-Entrepreneur", "déclaration de revenus", "income tax", "self-employed", "the taxes",
                     "los impuestos", "las retenciones"):
            self.assertTrue(denies("studio:builder", text), text)

    def test_format_characters_and_soft_hyphens_do_not_hide_a_word(self):
        for text in ("T" + ZW + "VA", "TV" + SHY + "A", "T" + ZWJ + "V" + WJ + "A", BOM + "TVA", "I" + ZWNJ + "V" + ZW + "A",
                     "im" + SHY + "p" + ZW + "u" + ZWJ + "esto", "ta" + SHY + "x", "V" + ZW + "A" + SHY + "T"):
            self.assertTrue(denies("studio:builder", "look at " + text), ascii(text))
        self.assertTrue(denies("studio:builder", "TVA.\nLEGAL: legal-eu-es-fr panel" + ZW + " check" + SHY + "er" + "x"))   # route words are folded too, "checkerx" is not "checker"
        self.assertIsNone(denies("studio:builder", "TVA.\nLEG" + ZW + "AL: legal-eu-es-fr pa" + SHY + "nel check" + WJ + "er"))

    def test_iva_tva_and_vat_match_with_dots_or_hyphens_between_the_letters(self):
        for text in ("I.V.A.", "I.V.A", "T-V-A", "T.V.A.", "V.A.T.", "v-a-t", "i-v-a", "I.V-A", "el I.V.A. del mes"):
            self.assertTrue(denies("studio:builder", "look at " + text), text)
        for text in ("a.v.i", "i.v.", "t-v", "i.v.ax", "xi.v.a"):
            self.assertIsNone(denies("studio:builder", "look at " + text), text)

    def test_every_listed_word_triggers_and_is_named_in_the_deny_text(self):
        self.assertGreater(len(agentgate.LEGAL_WORDS), 35)
        for word in agentgate.LEGAL_WORDS:
            reason = denies("studio:builder", "please look at %s now" % word)
            self.assertTrue(reason, word)
            self.assertIn(word, reason)
        for gone in ("autónomo", "autónoma", "RETA"):
            self.assertNotIn(gone, agentgate.LEGAL_WORDS)

    def test_the_deny_text_comes_from_the_catalog_in_both_languages(self):
        for lang in ("en", "es"):
            patch_env(self, BUDGIT_LANG=lang)
            reason = denies("studio:builder", "VAT question")
            self.assertEqual(reason, i18n.t("agentgate.legal", lang, word="VAT"))
            for part in ("VAT", "legal-eu-es-fr", "panel", "checker", "LEGAL: legal-eu-es-fr panel checker", "LEGAL: none - "):
                self.assertIn(part, reason, (lang, part))
        self.assertNotEqual(i18n.t("agentgate.legal", "en", word="x"), i18n.t("agentgate.legal", "es", word="x"))

    # --- forks, projects, errors ---------------------------------------------
    def test_a_fork_is_not_checked(self):
        self.assertIsNone(agentgate.legal_reason({"subagent_type": "fork", "prompt": "Compute the TVA."}))
        self.assertIsNone(agentgate.legal_reason({"subagent_type": " Fork ", "prompt": "Compute the TVA."}))
        self.assertIsNone(denies("fork", "Compute the TVA.\nALL-TOOLS: a fork is the parent"))
        self.assertTrue(agentgate.legal_reason({"subagent_type": "studio:builder", "prompt": "Compute the TVA."}))

    def test_an_error_inside_the_check_is_logged_and_the_notice_names_the_legal_check(self):
        with mock.patch.object(agentgate, "legal_reason", side_effect=RuntimeError("boom")), \
                mock.patch.object(hook.sl, "log_error") as logged:
            out = self.run_hook(spawn("studio:builder", "VAT question"), self.tmp)
        logged.assert_any_call("legal gate")       # any_call: since 88a8d56a (R-1273) every launch also sweeps orphan runs and logs its own line when this unbound tmp has no ledger
        note = out.get("systemMessage", "")
        self.assertIn("legal", note)
        self.assertNotIn("all-tools", note)
        self.assertIn("enforcement did not run", note)
        self.assertNotEqual(self.decision(out), "deny")

    def test_an_audit_failure_in_legal_none_is_the_legal_check_failing(self):
        self.init_project()
        with mock.patch.object(sl, "audit", side_effect=OSError("disk")), mock.patch.object(hook.sl, "log_error") as logged:
            out = self.run_hook(spawn("studio:builder", "TVA\nLEGAL: none - a variable name"), self.project)
        logged.assert_called_with("legal audit")
        self.assertIn("legal", out.get("systemMessage", ""))
        self.assertNotIn("all-tools", out.get("systemMessage", ""))
        self.assertNotEqual(self.decision(out), "deny")

    def test_an_unbound_project_is_not_checked(self):
        out = self.run_hook(spawn("studio:builder", "VAT question"), None)
        self.assertNotEqual(self.decision(out), "deny")

    def test_a_brief_with_no_legal_word_is_untouched(self):
        self.assertIsNone(denies("studio:builder", "refactor the parser"))
        self.assertIsNone(denies("studio:builder", None))
        self.assertIsNone(denies("studio:builder", 7))


class ProbeShapesTest(BudgitTestCase):
    """The verifier's 24 probe shapes (probe_r1125.py), driven in process through hook.on_pre_tool_use with the test's own
    temporary home. Expected outcomes follow the design: the all-tools deny comes first; then the LEGAL: line rules."""
    B = "studio:builder"
    ROUTE = "LEGAL: legal-eu-es-fr panel checker"

    def probe(self, tool_input, tool="Agent", lang="en", bound=True):
        """-> (decision, reason, audit lines this call added)."""
        patch_env(self, BUDGIT_LANG=lang)
        self.init_project()
        path = os.path.join(sl.studio_dir(self.project), "audit.log")
        read = lambda: open(path, encoding="utf-8").read().splitlines() if os.path.exists(path) else []
        before = len(read())
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use({"tool_name": tool, "session_id": "s1", "tool_input": tool_input}, self.project if bound else None)
        h = hook._OUT.get("hookSpecificOutput", {})
        return h.get("permissionDecision"), h.get("permissionDecisionReason", ""), read()[before:]

    def legal(self, result, lang="en"):
        self.assertEqual(result[0], "deny", result)
        self.assertIn("legal guard" if lang == "en" else "guarda legal", result[1])
        self.assertEqual(result[2], [])

    def all_tools(self, result):
        self.assertEqual(result[0], "deny", result)
        self.assertIn("is an all-tools agent", result[1])
        self.assertNotIn("legal guard", result[1])
        self.assertEqual(result[2], [])

    def allowed(self, result, audits=0):
        self.assertNotEqual(result[0], "deny", result)
        self.assertEqual(len(result[2]), audits, result)

    def test_probe_01_general_purpose_without_all_tools_gets_the_all_tools_deny_first(self):
        self.all_tools(self.probe({"subagent_type": "general-purpose", "prompt": "compute TVA"}))

    def test_probe_02_no_type_and_a_tax_word_without_lines_gets_the_all_tools_deny(self):
        self.all_tools(self.probe({"prompt": "calcula el IRPF"}, tool="Task"))

    def test_probe_03_all_tools_line_and_tva_without_a_legal_line_gets_the_legal_deny(self):
        self.legal(self.probe({"subagent_type": "general-purpose", "prompt": "TVA\nALL-TOOLS: browser"}))

    def test_probe_04_all_tools_line_and_legal_none_passes_with_one_audit_line(self):
        r = self.probe({"subagent_type": "general-purpose", "prompt": "TVA\nALL-TOOLS: browser\nLEGAL: none - variable named tva"})
        self.allowed(r, audits=1)
        self.assertIn("legal-none: general-purpose", r[2][0])

    def test_probe_05_spanish_text_for_trabajador_autonomo(self):
        r = self.probe({"subagent_type": self.B, "prompt": "Explica el trabajador autónomo"}, lang="es")
        self.legal(r, "es")
        self.assertIn("trabajador autónomo", r[1])

    def test_probe_06_english_text_for_trabajador_autonomo(self):
        r = self.probe({"subagent_type": self.B, "prompt": "Explica el trabajador autónomo"}, lang="en")
        self.legal(r)
        self.assertIn("trabajador autónomo", r[1])

    def test_probe_07_fullwidth_tva_triggers(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": "look at ＴＶＡ now"}))

    def test_probe_08_a_crlf_route_line_passes(self):
        self.allowed(self.probe({"subagent_type": self.B, "prompt": "TVA\r\n" + self.ROUTE + "\r\nend"}))

    def test_probe_09_a_tab_separated_route_line_passes(self):
        self.allowed(self.probe({"subagent_type": self.B, "prompt": "TVA\nLEGAL:\tlegal-eu-es-fr\tpanel\tchecker"}))

    def test_probe_10_a_route_line_in_a_blockquote_does_not_count(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": "TVA\n> " + self.ROUTE}))

    def test_probe_11_a_route_line_inline_after_text_does_not_count(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": "TVA " + self.ROUTE}))

    def test_probe_12_two_none_lines_write_one_audit_line_with_the_first_reason(self):
        r = self.probe({"subagent_type": self.B, "prompt": "VAT\nLEGAL: none - first\nLEGAL: none - second"})
        self.allowed(r, audits=1)
        self.assertIn("first", r[2][0])
        self.assertNotIn("second", r[2][0])

    def test_probe_13_a_route_line_and_a_none_line_pass_with_no_audit_line(self):
        self.allowed(self.probe({"subagent_type": self.B, "prompt": "VAT\nLEGAL: none - first\n" + self.ROUTE}), audits=0)

    def test_probe_14_a_fork_gets_no_legal_deny(self):
        r = self.probe({"subagent_type": "fork", "prompt": "TVA"})
        self.assertNotIn("legal guard", r[1])             # a fork is never legal-checked (the all-tools gate is its own matter)
        self.assertEqual(r[2], [])
        self.allowed(self.probe({"subagent_type": "fork", "prompt": "TVA\nALL-TOOLS: a fork is the parent"}))

    def test_probe_15_an_unbound_session_is_not_gated(self):
        self.allowed(self.probe({"subagent_type": self.B, "prompt": "TVA"}, bound=False))

    def test_probe_16_no_trigger_words_pass(self):
        self.allowed(self.probe({"subagent_type": self.B, "prompt": "agente autónomo, reta, syntax, taxonomy, year 2035"}))

    def test_probe_17_zero_width_tva_triggers(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": "look T" + ZW + "VA"}))

    def test_probe_18_dotted_iva_and_modelo_130_trigger(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": "el I.V.A. y el modelo nº 130"}))
        self.legal(self.probe({"subagent_type": self.B, "prompt": "solo el modelo nº 130"}))

    def test_probe_19_a_none_reason_of_only_punctuation_is_denied(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": "TVA\nLEGAL: none - ..."}))

    def test_probe_20_a_none_line_with_an_em_dash_passes_with_one_audit_line(self):
        r = self.probe({"subagent_type": self.B, "prompt": "TVA\nLEGAL: none — variable"})
        self.allowed(r, audits=1)
        self.assertIn("variable", r[2][0])

    def test_probe_21_a_prompt_that_is_a_list_is_still_checked(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": ["TVA"]}))

    def test_probe_22_a_fork_with_case_and_spaces_is_not_legal_checked(self):
        r = self.probe({"subagent_type": " FORK ", "prompt": "TVA"})
        self.assertNotIn("legal guard", r[1])
        self.assertIsNone(agentgate.legal_reason({"subagent_type": " FORK ", "prompt": "TVA"}))

    def test_probe_23_the_year_2035_does_not_trigger(self):
        self.allowed(self.probe({"subagent_type": self.B, "prompt": "year 2035"}))

    def test_probe_24_the_2035_a_form_triggers(self):
        self.legal(self.probe({"subagent_type": self.B, "prompt": "fill 2035-A"}))

    def test_a_general_purpose_spawn_with_no_all_tools_line_and_a_tax_word_gets_the_all_tools_text(self):
        notes = []
        reason = agentgate.deny_reason({"subagent_type": "general-purpose", "prompt": "compute the VAT"}, notes)
        self.assertIn("is an all-tools agent", reason)
        self.assertIn("ALL-TOOLS: <reason>", reason)
        self.assertNotEqual(reason, i18n.t("agentgate.legal", "en", word="VAT"))
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
