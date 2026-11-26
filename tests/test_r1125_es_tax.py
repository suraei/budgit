"""R-1125 #8: Spanish tax rows, the Spanish tax role and the figure-consistency lint. Synthetic drafts and profiles only, a frozen date, no case text."""
import os
import re
import unittest

from test_legalcheck import lc, codes, SKILL
from test_r1125_golden import (ES_TAX_ROWS, GOLDEN_TODAY, check, draft_money_safe, ROWS_GOLDEN)

CHECKED = "2026-11-14"
LAST_REVIEW = "2026-12-31"
# Figures and facts the brief forbids the Spanish rows and role to state (unsettled, of unknown year, or not quoted).
NEVER_STATE = ("80 EUR", "80 euros", "22 March", "Ley 2/2026", "86 %", "93 %", "4 909", "Cuenca", "Soria", "Teruel", "31.5")


def shipped_es():
    return {r.id: r for r in lc.load_rules() if r.id in ES_TAX_ROWS}


def read(*parts):
    with open(os.path.join(SKILL, *parts), encoding="utf-8") as f:
        return f.read()


def rule(rid, text, topic="", file="syn.md", line=1):
    raw = {"topic": topic}
    return lc.Rule(rid, text, "Synthetic", "https://example.org", "in force", None, None, "S", raw, file, line, ())


class SpanishTaxRowsTest(unittest.TestCase):
    def test_the_shipped_rows_are_exactly_the_golden_table(self):
        self.assertEqual(set(shipped_es()), set(ES_TAX_ROWS))
        self.assertEqual(len(shipped_es()), 30)
        families = [r.id for r in lc.load_rules() if r.id.startswith(("ES-IVA-", "ES-RETA-", "ES-M721-"))]
        self.assertEqual(set(families) - set(ES_TAX_ROWS), set())
        old = {"ES-IRPF-RESIDENCE", "ES-IRPF-PERMUTA", "ES-IRPF-FIFO", "ES-IRPF-TWO-MONTHS"}
        self.assertEqual({r.id for r in lc.load_rules() if r.id.startswith("ES-IRPF-")} - set(ES_TAX_ROWS), old)   # old ids kept, none duplicated

    def test_each_new_row_passes_lint_and_applies_to_has_only_known_values(self):
        rows = shipped_es()
        self.assertEqual(lc.lint_rules(list(rows.values())), [])
        for rid, r in rows.items():
            self.assertLessEqual(set(r.applies_to), set(lc.PERSON_TYPES), rid)
            self.assertEqual(r.applies_to, ES_TAX_ROWS[rid][0], rid)
            if "employee" in r.applies_to:
                self.assertNotIn("micro", r.applies_to)
        self.assertEqual(lc.lint_rules(lc.load_rules()), [])

    def test_each_new_row_is_tag_s_or_u_checked_today_and_reviewed_by_the_end_of_the_year(self):
        for rid, r in shipped_es().items():
            self.assertEqual(r.tag, ES_TAX_ROWS[rid][1], rid)
            self.assertIn(r.tag, ("S", "U"), rid)              # the research was summarised: never V
            self.assertEqual(r.checked.isoformat(), CHECKED, rid)
            self.assertLessEqual(r.review_by.isoformat(), LAST_REVIEW, rid)
            self.assertTrue(r.url.startswith("https://"), rid)
            self.assertNotIn("\n", r.rule)

    def test_a_u_row_states_no_figure(self):
        for rid, r in shipped_es().items():
            if r.tag == "U":
                text = re.sub(r"\b(?:19|20)\d\d\b|\b\d+/\d{4}\b|\b[Mm]odelo \d+|\b[1-9]\s?(?:January)\b", " ", lc.CITED_ID_RE.sub(" ", r.rule))
                self.assertFalse(re.search(r"\d", text), (rid, r.rule))

    def test_no_never_state_figure_appears_anywhere(self):
        blobs = {rid: r.rule + " " + r.instrument for rid, r in shipped_es().items()}
        blobs["tax-es.md"] = read("roles", "tax-es.md")
        blobs["roles/es.md"] = read("roles", "es.md")
        blobs["SKILL.md"] = read("SKILL.md")
        for name, text in blobs.items():
            for bad in NEVER_STATE:
                self.assertNotIn(bad, text, (name, bad))
        self.assertIn("2023, 2024 and 2025", shipped_es()["ES-RETA-TARIFA-PLANA"].rule)    # the years of the only official amount
        self.assertIn("states none", shipped_es()["ES-RETA-TARIFA-PLANA"].rule)

    def test_the_rows_hold_the_quoted_figures(self):
        rows = shipped_es()
        want = {"ES-IRPF-HOME-SUPPLIES": ("30 %", "600 EUR"), "ES-IRPF-HARD-TO-JUSTIFY": ("5 %", "2 000 EUR"),
                "ES-IRPF-M130-RATE": ("20 %",), "ES-IRPF-M130-EXEMPT-70": ("70 %",), "ES-IVA-GENERAL-RATE": ("21 %",),
                "ES-IVA-REDUCED-RATES": ("10 %", "4 %"), "ES-RETA-RATE-2026": ("28.30 %",), "ES-RETA-MAX-BASE-2026": ("5 101.20",),
                "ES-IRPF-MEALS": ("53.34", "26.67", "91.35", "48.08"), "ES-IVA-CAR-50": ("50 %",),
                "ES-IRPF-SAVINGS-COMBINED-2025": ("19 %", "21 %", "23 %", "27 %", "30 %"),
                "ES-IRPF-GENERAL-STATE-2025": ("9.5 %", "12 %", "15 %", "18.5 %", "22.5 %", "24.5 %")}
        for rid, texts in want.items():
            for text in texts:
                self.assertIn(text, rows[rid].rule, rid)
        self.assertIn("15 %", rows["ES-IRPF-SAVINGS-STATE-2025"].rule)
        self.assertNotIn("28 %", rows["ES-IRPF-SAVINGS-COMBINED-2025"].rule)    # no 28 % band exists for 2025
        self.assertNotRegex(rows["ES-IRPF-SAVINGS-TOP-RATE-CHANGE"].rule, r"\d\d %")  # the 2024 side is not quoted: U, no figure

    def test_rows_keep_the_conditions_of_their_source(self):
        rows = shipped_es()
        need = {"ES-IRPF-HOME-SUPPLIES": ("Main home only", "if the taxpayer proves it"),
                "ES-IRPF-HARD-TO-JUSTIFY": ("simplificada rule only", "art. 32.2", "capped at 2 000 EUR a year"),
                "ES-IRPF-M130-WHO": ("normal or simplificada", "does not cover objective estimation"),
                "ES-IRPF-M130-RATE": ("non-agricultural", "8 % in Ceuta and Melilla"),
                "ES-IRPF-M130-EXEMPT-70": ("previous year", "new activity"),
                "ES-IRPF-M130-DEADLINES": ("nil return", "Direct-debit dates are earlier"),
                "ES-IVA-M303-DEADLINES": ("direct debit",),
                "ES-IVA-MIXED-USE": ("2021 edition", "recheck"), "ES-IVA-CAR-50": ("2021", "sales agents", "once use in the activity"),
                "ES-IRPF-SAVINGS-STATE-2025": ("tax year 2025", "state half"),
                "ES-IRPF-GENERAL-STATE-2025": ("state part only",), "ES-IRPF-GENERAL-REGIONAL": ("never a total general rate without the region",),
                "ES-RETA-RATE-2026": ("charged on top",), "ES-IRPF-CRYPTO-SAVINGS-BASE": ("outside an economic activity",),
                "ES-IRPF-MEALS": ("restaurant or hospitality", "electronic", "excess is not deductible")}
        for rid, needles in need.items():
            for needle in needles:
                self.assertIn(needle, rows[rid].rule, (rid, needle))


class SpanishTaxDraftsTest(unittest.TestCase):
    def test_a_green_draft_that_cites_a_new_s_row_is_refused(self):
        rows = list(shipped_es().values())
        for r in [x for x in rows if x.tag == "S"]:
            types = {(r.applies_to or ("employee",))[0]}
            got = check(draft_money_safe("VERDE", r.id), rows, types, today=r.review_by)
            self.assertIn("green-unverified", codes(got), r.id)
            self.assertEqual(codes(lc.hard_problems(got)), ["escalation-trigger", "green-unverified"], r.id)
            self.assertEqual(check(draft_money_safe("ÁMBAR", r.id), rows, types, today=r.review_by), [], r.id)

    def test_an_employee_citing_an_autonomo_only_row_gets_row_not_applicable(self):
        rows = list(shipped_es().values())
        only = [rid for rid, (types, _t) in ES_TAX_ROWS.items() if types and "employee" not in types]
        self.assertIn("ES-IRPF-M130-RATE", only)
        self.assertIn("ES-RETA-RATE-2026", only)
        for rid in only:
            self.assertEqual(codes(check(draft_money_safe("ÁMBAR", rid), rows, {"employee"}, today=GOLDEN_TODAY)), ["row-not-applicable"], rid)
        for rid in ("ES-IRPF-SAVINGS-COMBINED-2025", "ES-IRPF-GENERAL-STATE-2025", "ES-IVA-GENERAL-RATE"):
            self.assertEqual(check(draft_money_safe("ÁMBAR", rid), rows, {"employee"}, today=GOLDEN_TODAY), [], rid)
        self.assertEqual(check(draft_money_safe("ÁMBAR", "ES-IRPF-M130-RATE"), rows, {"autonomo_directa"}, today=GOLDEN_TODAY), [])
        self.assertEqual(codes(check(draft_money_safe("ÁMBAR", "ES-IRPF-M130-RATE"), rows, {"autonomo_objetiva"}, today=GOLDEN_TODAY)),
                         ["row-not-applicable"])

    def test_every_new_row_is_mapped_to_the_filing_kind(self):
        for rid in ES_TAX_ROWS:
            self.assertEqual(lc.ESCALATION_ROWS.get(rid), "filing", rid)
            self.assertEqual(ROWS_GOLDEN.get(rid), "filing", rid)


class SpanishTaxRoleTest(unittest.TestCase):
    def test_the_role_file_exists_and_skill_and_the_spain_role_route_to_it(self):
        self.assertTrue(os.path.isfile(os.path.join(SKILL, "roles", "tax-es.md")))
        self.assertIn("roles/tax-es.md", read("SKILL.md"))
        self.assertIn("roles/tax-es.md", read("roles", "es.md"))
        line = [x for x in read("SKILL.md").splitlines() if "roles/tax-es.md" in x]
        self.assertEqual(len(line), 1)
        for t in ("autonomo_directa", "autonomo_objetiva", "employee"):
            self.assertIn(t, line[0])
            self.assertIn(t, read("roles", "tax-es.md").splitlines()[2] + read("roles", "tax-es.md").splitlines()[3])
        self.assertLessEqual(len(read("SKILL.md").splitlines()), 143)

    def test_the_role_runs_the_savings_pass_and_asks_for_the_share(self):
        role = read("roles", "tax-es.md")
        for needle in ("Simplificada vs normal, in euros", "min(5 % x positive difference, 2 000 EUR)", "Home supplies", "Dietas",
                       "Modelo 130 check", "RETA base vs real income, with the regularisation", "use 0 % only if", "ask the share",
                       "answers only from the rows", "Our rows win over any other skill's figures"):
            self.assertIn(needle, " ".join(role.split()), needle)
        known = {r.id for r in lc.load_rules()}
        ids = set(re.findall(r"`(ES-(?:IRPF|IVA|RETA|M721)-[A-Z0-9-]+)`", role))
        self.assertTrue(ids)
        self.assertLessEqual(ids - {"ES-IRPF-*", "ES-IVA-*", "ES-RETA-*", "ES-M721-*"}, known)

    def test_the_role_never_assumes_zero_or_states_a_forbidden_figure(self):
        role = read("roles", "tax-es.md")
        self.assertNotRegex(role, r"assum\w+ (?:as )?0 %(?! only)")
        for bad in NEVER_STATE:
            self.assertNotIn(bad, role)


class FigureLintTest(unittest.TestCase):
    def test_a_role_line_that_cites_a_row_next_to_its_own_figure_passes(self):
        rows = [rule("ES-X-ONE", "The rate is 5 % up to 2 000 EUR a year, and 2 000.50 EUR above.")]
        text = "- Flat amount: 5 % and 2000 EUR (`ES-X-ONE`), also 2 000,50 EUR.\n"
        self.assertEqual(lc.lint_figures(rows, {"role.md": text}), [])

    def test_a_role_line_with_another_figure_fails(self):
        rows = [rule("ES-X-ONE", "The rate is 5 % up to 2 000 EUR a year.")]
        got = lc.lint_figures(rows, {"role.md": "- Flat amount: 3 % (`ES-X-ONE`).\n"})
        self.assertEqual(codes(got), ["figure-mismatch"])
        got = lc.lint_figures(rows, {"role.md": "1. First line with the id `ES-X-ONE`\n   wraps and says 2 500 EUR.\n"})
        self.assertEqual(codes(got), ["figure-mismatch"])      # a wrapped line belongs to its item
        self.assertEqual(lc.lint_figures(rows, {"role.md": "- A figure of 9 % with no id.\n- Unknown row ES-Z-NONE says 9 %.\n"}), [])

    def test_two_rows_with_the_same_id_or_topic_and_different_figures_fail(self):
        same = [rule("ES-X-ONE", "Rate 5 %.", line=3), rule("ES-X-ONE", "Rate 6 %.", line=9)]
        self.assertEqual(codes(lc.lint_figures(same, {})), ["figure-mismatch"])
        topic = [rule("ES-X-ONE", "Rate 5 %.", topic="rate"), rule("ES-X-TWO", "Rate 6 %.", topic="rate")]
        self.assertEqual(codes(lc.lint_figures(topic, {})), ["figure-mismatch"])
        agree = [rule("ES-X-ONE", "Rate 5 %.", topic="rate"), rule("ES-X-TWO", "A rate of 5.0 %, in other words.", topic="rate")]
        self.assertEqual(lc.lint_figures(agree, {}), [])
        other = [rule("ES-X-ONE", "Rate 5 %.", topic="a"), rule("ES-X-TWO", "Rate 6 %.", topic="b")]
        self.assertEqual(lc.lint_figures(other, {}), [])

    def test_the_lint_reads_a_topic_column_from_a_table_and_sees_the_roles_of_a_tree(self):
        table = ("| id | rule | instrument | url | status | checked | review_by | tag | topic |\n|---|---|---|---|---|---|---|---|---|\n"
                 "| ES-X-ONE | Rate 5 %. | i | https://e.org | ok | 2026-11-14 | 2026-12-31 | S | rate |\n"
                 "| ES-X-TWO | Rate 7 %. | i | https://e.org | ok | 2026-11-14 | 2026-12-31 | S | rate |\n")
        self.assertEqual(codes(lc.lint_figures(lc.parse_rules(table, "syn.md"), {})), ["figure-mismatch"])
        texts = lc.load_texts()
        self.assertIn("SKILL.md", texts)
        self.assertIn(os.path.join("roles", "tax-es.md"), texts)
        self.assertIn(os.path.join("roles", "tax-fr.md"), texts)

    def test_every_spanish_way_of_writing_a_figure_is_read_and_a_mismatch_is_caught(self):
        rows = [rule("ES-X-ONE", "The cap is 5 101.20 EUR a month and the rate is 21 %.")]
        for same in ("5.101,20 euros", "5 101,20 euros", "5.101,20 €", "5101,20 euros", "21 por ciento", "21 por 100", "21 €"[:2] + " %"):
            self.assertEqual(lc.lint_figures(rows, {"r.md": "- Cap `ES-X-ONE` is %s.\n" % same}), [], same)
        for wrong in ("5.102,20 euros", "5 102,20 euros", "5.102,20 €", "5.102 euros", "22 por ciento", "22 por 100", "22 euros"):
            got = lc.lint_figures(rows, {"r.md": "- Cap `ES-X-ONE` is %s.\n" % wrong})
            self.assertEqual(codes(got), ["figure-mismatch"], wrong)
        also = [rule("ES-X-ONE", "Cap 5.101,20 euros.", topic="t"), rule("ES-X-TWO", "Cap 5 101.20 EUR.", topic="t")]
        self.assertEqual(lc.lint_figures(also, {}), [])      # one figure written two ways is one figure

    def test_a_home_ownership_row_is_u_keeps_its_conditions_and_states_no_figure(self):
        row = shipped_es()["ES-IRPF-HOME-OWNERSHIP-SHARE"]
        self.assertEqual(row.tag, "U")
        for needle in ("owner's own percentage", "before 2017", "current rule must be opened first"):
            self.assertIn(needle, row.rule)
        self.assertNotRegex(re.sub(r"\b20\d\d\b", " ", lc.CITED_ID_RE.sub(" ", row.rule)), r"\d")

    def test_the_combined_savings_row_cites_the_resident_page_of_the_state_row(self):
        rows = shipped_es()
        self.assertEqual(rows["ES-IRPF-SAVINGS-COMBINED-2025"].url, rows["ES-IRPF-SAVINGS-STATE-2025"].url)
        self.assertNotIn("residentes-extranjero", rows["ES-IRPF-SAVINGS-COMBINED-2025"].url)

    def test_the_721_deadline_is_u_with_no_date_and_the_303_row_keeps_the_objetiva_note(self):
        r721 = shipped_es()["ES-M721-DEADLINE"]
        self.assertEqual(r721.tag, "U")
        self.assertNotIn("March", r721.rule.replace("draft order of March 2026", ""))
        self.assertIn("must be opened", r721.rule)
        self.assertIn("objective estimation", shipped_es()["ES-IVA-M303-DEADLINES"].rule)
        self.assertIn("direct debit", shipped_es()["ES-IVA-M303-DEADLINES"].rule)

    def test_the_real_tree_has_no_figure_mismatch(self):
        self.assertEqual(lc.lint_figures(lc.load_rules(), lc.load_texts()), [])

    def test_a_wrong_figure_planted_in_the_real_role_is_caught(self):
        texts = lc.load_texts()
        role = os.path.join("roles", "tax-es.md")
        texts[role] = texts[role].replace("base x 28.30 %", "base x 29.30 %")
        got = lc.lint_figures(lc.load_rules(), texts)
        self.assertEqual(codes(got), ["figure-mismatch"])
        self.assertIn("ES-RETA-RATE-2026", got[0].msg)


if __name__ == "__main__":
    unittest.main()
