"""R-1125 #1: French self-employed tax rows and the French tax role. Synthetic drafts and profiles only, a frozen date, no case text."""
import os
import re
import unittest

from test_legalcheck import lc, codes, SKILL
from test_r1125_golden import (FR_SE_ROWS, GOLDEN_TODAY, check, draft, draft_money_safe, REEL_ONLY, MICRO_ONLY, BOTH,
                               ROWS_GOLDEN)

CHECKED = "2026-11-14"
LAST_REVIEW = "2026-12-31"
# Figures the brief forbids the rows to state (unsettled, of unknown year, or not quoted).
NEVER_STATE = ("29 579", "29579", "29,579", "155 g", "156 g", "155g", "156g", "0,636", "0.636", "0,357", "0.357", "1 395", "0,427", "0.427",
               "12.8 %", "19.2 %", "13.4 %", "17.4 %", "12,8", "19,2", "13,4", "17,4", "g CO2", "g/km")


def shipped_se():
    return {r.id: r for r in lc.load_rules() if r.id in FR_SE_ROWS}


def read(*parts):
    with open(os.path.join(SKILL, *parts), encoding="utf-8") as f:
        return f.read()


class FrenchSelfEmployedRowsTest(unittest.TestCase):
    def test_the_shipped_rows_are_exactly_the_golden_table(self):
        self.assertEqual(set(shipped_se()), set(FR_SE_ROWS))
        families = [r.id for r in lc.load_rules() if r.id.startswith(("FR-BNC-", "FR-URSSAF-", "FR-VAT-DED-"))]
        self.assertEqual(set(families) - set(FR_SE_ROWS), {"FR-BNC-2035"})

    def test_each_new_row_passes_lint_and_applies_to_has_only_known_values(self):
        rows = shipped_se()
        self.assertEqual(lc.lint_rules(list(rows.values())), [])
        for rid, r in rows.items():
            self.assertTrue(r.applies_to, rid)
            self.assertLessEqual(set(r.applies_to), set(lc.PERSON_TYPES), rid)
            self.assertEqual(r.applies_to, FR_SE_ROWS[rid][0], rid)
        self.assertEqual(lc.lint_rules(lc.load_rules()), [])

    def test_each_new_row_is_tag_s_or_u_checked_today_and_reviewed_by_the_end_of_the_year(self):
        for rid, r in shipped_se().items():
            self.assertEqual(r.tag, FR_SE_ROWS[rid][1], rid)
            self.assertIn(r.tag, ("S", "U"), rid)              # the research was summarised: never V
            self.assertEqual(r.checked.isoformat(), CHECKED, rid)
            self.assertLessEqual(r.review_by.isoformat(), LAST_REVIEW, rid)
            self.assertTrue(r.url.startswith("https://"), rid)

    def test_a_u_row_states_no_figure(self):
        for rid, r in shipped_se().items():
            if r.tag == "U":
                self.assertFalse(re.search(r"\d", r.rule), (rid, r.rule))

    def test_no_row_states_a_figure_the_brief_forbids(self):
        for rid, r in shipped_se().items():
            for bad in NEVER_STATE:
                self.assertNotIn(bad, r.rule + " " + r.instrument, (rid, bad))

    def test_the_rows_hold_the_quoted_figures(self):
        rows = shipped_se()
        want = {"FR-URSSAF-MICRO-RATE-BNC": "25.6 %", "FR-URSSAF-MICRO-RATE-CIPAV": "23.2 %", "FR-BNC-MICRO-ALLOWANCE": "34 %",
                "FR-BNC-MICRO-ALLOWANCE-MIN": "305 EUR", "FR-BNC-EXPENSE-500": "500 EUR", "FR-URSSAF-VL-DEADLINE": "30 September",
                "FR-URSSAF-ACRE-2026": "25 %", "FR-BNC-CGA-REDUCTION-GONE": "2025"}
        for rid, text in want.items():
            self.assertIn(text, rows[rid].rule, rid)
        for cap in ("30 000", "20 300", "18 300", "9 900"):
            self.assertIn(cap, rows["FR-BNC-CAR-DEPR-CAP"].rule)

    def test_rows_keep_the_conditions_of_their_source(self):
        rows = shipped_se()
        e500 = rows["FR-BNC-EXPENSE-500"].rule
        for needle in ("tolerance", "renewal of current items", "does not cover the initial fit-out"):
            self.assertIn(needle, e500)
        cap = rows["FR-BNC-CAR-DEPR-CAP"].rule
        for needle in ("first registered before the WLTP regime", "bands for WLTP-era cars differ", "must be opened"):
            self.assertIn(needle, cap)
        energy = rows["FR-BNC-PREMISES-ENERGY"].rule
        self.assertNotIn("exists", energy)
        self.assertIn("No official percentage was found", energy)
        self.assertIn("assumption, not an official figure", energy)
        self.assertNotIn("charges no VAT", rows["FR-VAT-DED-FRANCHISE"].rule)
        self.assertIn("no VAT deduction", rows["FR-VAT-DED-FRANCHISE"].rule)
        self.assertEqual(len(rows), 28)

    def test_the_role_says_comparison_rows_reach_a_micro_only_for_the_comparison(self):
        self.assertIn("only inside the micro-vs-réel comparison", read("roles", "tax-fr.md"))

    def test_the_rows_are_one_line_and_not_empty(self):
        for rid, r in shipped_se().items():
            self.assertTrue(r.rule.strip(), rid)      # protected names are policed by test_protected_names
            self.assertNotIn("\n", r.rule)


class FrenchSelfEmployedDraftsTest(unittest.TestCase):
    def test_a_green_draft_that_cites_a_new_s_row_is_refused(self):
        rows = list(shipped_se().values())
        self.assertTrue(rows)
        for r in rows:
            types = {r.applies_to[0]}
            got = check(draft_money_safe("VERDE", r.id), rows, types, today=r.review_by)
            self.assertIn("green-unverified", codes(got), r.id)
            self.assertEqual(codes(lc.hard_problems(got)), ["escalation-trigger", "green-unverified"], r.id)   # a filing row, and not fresh
            self.assertEqual(check(draft_money_safe("ÁMBAR", r.id), rows, types, today=r.review_by), [], r.id)

    def test_a_micro_profile_citing_a_bnc_reel_only_row_gets_row_not_applicable(self):
        rows = shipped_se()
        reel_only = [rid for rid, (types, _t) in FR_SE_ROWS.items() if types == REEL_ONLY]
        self.assertIn("FR-BNC-CGA-REDUCTION-GONE", reel_only)
        for rid in reel_only:
            got = check(draft_money_safe("ÁMBAR", rid), list(rows.values()), {"micro"}, today=GOLDEN_TODAY)
            self.assertEqual(codes(got), ["row-not-applicable"], rid)
            self.assertEqual(check(draft_money_safe("ÁMBAR", rid), list(rows.values()), {"bnc_reel"}, today=GOLDEN_TODAY), [], rid)

    def test_a_bnc_reel_profile_citing_a_micro_only_row_gets_row_not_applicable_and_a_comparison_row_reaches_both(self):
        rows = list(shipped_se().values())
        for rid in [i for i, (t, _x) in FR_SE_ROWS.items() if t == MICRO_ONLY]:
            self.assertEqual(codes(check(draft_money_safe("ÁMBAR", rid), rows, {"bnc_reel"})), ["row-not-applicable"], rid)
        for rid in ("FR-BNC-MICRO-ALLOWANCE", "FR-BNC-MICRO-ALLOWANCE-MIN", "FR-BNC-COST-NECESSARY", "FR-BNC-CAR-SCALE-OPTION"):
            self.assertEqual(FR_SE_ROWS[rid][0], BOTH)
            for who in ({"micro"}, {"bnc_reel"}):
                self.assertEqual(check(draft_money_safe("ÁMBAR", rid), rows, who), [], (rid, who))

    def test_the_savings_pass_lint_still_refuses_a_money_draft_without_its_block(self):
        rows = list(shipped_se().values())
        bare = check(draft("ÁMBAR", "FR-BNC-MICRO-ALLOWANCE", extra="Ahorro estimado: 500 EUR de impuestos."), rows, {"micro"})
        self.assertEqual(codes(bare), ["no-savings-pass"])
        self.assertEqual(check(draft_money_safe("ÁMBAR", "FR-BNC-MICRO-ALLOWANCE"), rows, {"micro"}), [])

    def test_every_new_row_is_mapped_to_the_filing_kind(self):
        for rid in FR_SE_ROWS:
            self.assertEqual(lc.ESCALATION_ROWS.get(rid), "filing", rid)
            self.assertEqual(ROWS_GOLDEN.get(rid), "filing", rid)


class FrenchTaxRoleTest(unittest.TestCase):
    def test_the_role_file_exists_and_skill_and_the_france_role_route_to_it(self):
        self.assertTrue(os.path.isfile(os.path.join(SKILL, "roles", "tax-fr.md")))
        self.assertIn("roles/tax-fr.md", read("SKILL.md"))
        self.assertIn("roles/tax-fr.md", read("roles", "fr.md"))
        routing = [x for x in read("SKILL.md").splitlines() if "roles/tax-fr.md" in x or "also loads" in x]
        self.assertTrue(any("`micro` or `bnc_reel`" in x for x in read("SKILL.md").splitlines()))
        self.assertTrue(routing)

    def test_the_role_runs_the_five_savings_lines_and_asks_for_the_share(self):
        role = read("roles", "tax-fr.md")
        for needle in ("max(34 % x turnover, 305 EUR)", "Car, scale vs real, each year", "Premises surface split",
                       "abolished CGA reduction", "Franchise VAT means no recovery", "0 % only if", "ask the share"):
            self.assertIn(needle, role)
        self.assertIn("answers only from the rows", role)
        ids = set(re.findall(r"`((?:FR-BNC|FR-URSSAF|FR-VAT-DED)-[A-Z0-9-]+)`", role))
        known = {r.id for r in lc.load_rules()}
        self.assertTrue(ids)
        self.assertLessEqual(ids, known)

    def test_the_role_never_states_a_forbidden_figure(self):
        role = read("roles", "tax-fr.md")
        for bad in ("29 579", "155 g", "156 g", "0,636"):
            self.assertNotIn(bad, role)

    def test_light_mode_stays_small(self):
        self.assertLessEqual(len(read("SKILL.md").splitlines()), 143)


if __name__ == "__main__":
    unittest.main()
