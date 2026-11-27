"""R-1125 #10 (her I-1670: the legal skills cannot go wrong and keep themselves up to date).
Rule: every safety promise the legal skill makes has a test that would fail if the promise broke. This file asserts, with a frozen
date, the promises of skills/legal-eu-es-fr (SKILL.md sections 0, 3 and 4): each person type, an expired row, the weekly-check
window, a page with a newer version noted (the pending-change marker) and the verdict of each worked case.
Synthetic rules, drafts and profiles only: no case text, no real profile, no ~/.studio, no ~/.budgit, no network."""
import datetime as dt
import json
import os
import tempfile
import unittest
import unittest.mock

from test_legalcheck import lc, codes, answer, FakeFetch, SKILL
from test_r1125_person_type import profile_text, table

GOLDEN_TODAY = dt.date(2026, 11, 14)          # frozen: no test here reads the clock
DAY = dt.timedelta(days=1)
EUR = "https://eur-lex.europa.eu/eli/reg/2016/679/oj"
BOFIP = "https://bofip.impots.gouv.fr/bofip/0000-PGP.html"          # synthetic page id on a real official host
SEVEN = ("employee", "micro", "bnc_reel", "autonomo_directa", "autonomo_objetiva", "company", "cross_border")


BOTH, MICRO_ONLY, REEL_ONLY = ("micro", "bnc_reel"), ("micro",), ("bnc_reel",)
# R-1125 #1: the French self-employed rows, as a golden table: id -> (applies_to, tag). A row added or dropped needs a line here.
FR_SE_ROWS = {
    "FR-BNC-COST-NECESSARY": (BOTH, "S"), "FR-BNC-COST-PAID": (BOTH, "S"), "FR-BNC-COST-SPLIT": (BOTH, "S"),
    "FR-BNC-PHONE-INTERNET-SPLIT": (BOTH, "S"), "FR-BNC-PREMISES-SURFACE": (BOTH, "S"), "FR-BNC-PREMISES-ENERGY": (BOTH, "S"),
    "FR-BNC-CAR-SCALE-OPTION": (BOTH, "S"), "FR-BNC-CAR-SCALE-COVERS": (BOTH, "S"), "FR-BNC-CAR-SCALE-VALUES": (BOTH, "U"),
    "FR-BNC-CAR-DEPR-CAP": (BOTH, "S"), "FR-BNC-EXPENSE-500": (REEL_ONLY, "S"),
    "FR-VAT-DED-COEFFICIENT": (REEL_ONLY, "S"), "FR-VAT-DED-MIXED": (REEL_ONLY, "S"), "FR-VAT-DED-CAR": (REEL_ONLY, "S"),
    "FR-VAT-DED-HOUSING": (REEL_ONLY, "S"), "FR-VAT-DED-FRANCHISE": (MICRO_ONLY, "U"),
    "FR-URSSAF-MICRO-RATE-BNC": (BOTH, "S"), "FR-URSSAF-MICRO-RATE-CIPAV": (BOTH, "S"), "FR-URSSAF-VL": (MICRO_ONLY, "S"),
    "FR-URSSAF-VL-DEADLINE": (MICRO_ONLY, "S"), "FR-URSSAF-ACRE-2026": (MICRO_ONLY, "S"), "FR-URSSAF-ACRE-BNC-RATES": (MICRO_ONLY, "U"),
    "FR-BNC-MICRO-ALLOWANCE": (BOTH, "S"), "FR-BNC-MICRO-ALLOWANCE-MIN": (BOTH, "S"),
    "FR-BNC-REEL-OPTION": (BOTH, "S"), "FR-BNC-REEL-OPTION-TERM": (BOTH, "S"),
    "FR-BNC-CGA-REDUCTION-GONE": (REEL_ONLY, "S"), "FR-BNC-CGA-FEES-COST": (REEL_ONLY, "S"),
}


_INC = ("employee", "autonomo_directa", "autonomo_objetiva", "company", "cross_border")
_GEN = ("employee", "autonomo_directa", "autonomo_objetiva", "cross_border")
_REG = ("employee", "autonomo_directa", "autonomo_objetiva")
_AUT, _D, _ALL = ("autonomo_directa", "autonomo_objetiva"), ("autonomo_directa",), ()
_M303, _DC = ("autonomo_directa", "autonomo_objetiva", "company"), ("autonomo_directa", "company")
_CR3, _M721 = ("employee", "autonomo_directa", "cross_border"), ("employee", "autonomo_directa", "company", "cross_border")
# R-1125 #8: the Spanish tax rows, as a golden table: id -> (applies_to, tag). () means every person type. A row added or dropped needs a line.
ES_TAX_ROWS = {
    "ES-IRPF-SAVINGS-STATE-2025": (_INC, "S"), "ES-IRPF-SAVINGS-REGIONAL-2025": (_INC, "S"), "ES-IRPF-SAVINGS-COMBINED-2025": (_INC, "S"),
    "ES-IRPF-SAVINGS-TOP-RATE-CHANGE": (_INC, "U"), "ES-IRPF-GENERAL-STATE-2025": (_GEN, "S"), "ES-IRPF-GENERAL-MINIMUM": (_GEN, "S"),
    "ES-IRPF-GENERAL-REGIONAL": (_REG, "U"), "ES-IRPF-HOME-SUPPLIES": (_D, "S"), "ES-IRPF-HOME-OWNERSHIP-SHARE": (_D, "U"),
    "ES-IRPF-MEALS": (_D, "S"), "ES-IRPF-HARD-TO-JUSTIFY": (_D, "S"), "ES-IRPF-M130-WHO": (_D, "S"), "ES-IRPF-M130-RATE": (_D, "S"),
    "ES-IRPF-M130-DEADLINES": (_D, "S"), "ES-IRPF-M130-EXEMPT-70": (_D, "S"), "ES-IRPF-CRYPTO-SAVINGS-BASE": (_CR3, "S"),
    "ES-IRPF-CRYPTO-FIFO": (_CR3, "S"), "ES-IVA-M303-DEADLINES": (_M303, "S"), "ES-IVA-GENERAL-RATE": (_ALL, "S"),
    "ES-IVA-REDUCED-RATES": (_ALL, "S"), "ES-IVA-MIXED-USE": (_DC, "S"), "ES-IVA-CAR-50": (_DC, "S"),
    "ES-RETA-REDUCED-TABLE-2026": (_AUT, "S"), "ES-RETA-GENERAL-TABLE-2026": (_AUT, "U"), "ES-RETA-RATE-2026": (_AUT, "S"),
    "ES-RETA-MAX-BASE-2026": (_AUT, "S"), "ES-RETA-TARIFA-PLANA": (_D, "U"), "ES-RETA-REGULARISATION": (_D, "U"),
    "ES-M721-THRESHOLD": (_M721, "U"), "ES-M721-DEADLINE": (_M721, "U"),
}


def row(rid, applies="", review_by="2027-04-24", checked="2026-11-01", tag="V", url=EUR):
    return ("| %s | Synthetic rule text. | Synthetic instrument | %s | in force | %s | %s | %s | %s |\n"
            % (rid, url, checked, review_by, tag, applies))


def slug(person_type):
    return "EU-SYN-" + person_type.upper().replace("_", "")


def cite(*ids):
    return ["- Synthetic point [ficha %s]" % i for i in ids]


def draft(verdict, *ids, extra=""):
    return answer(verdict, *cite(*ids), extra=extra)


def draft_money_safe(verdict, *ids):
    """A rule id such as ES-IRPF-... holds a tax word, so the draft counts as a money answer: give it its savings heading."""
    return draft(verdict, *ids, extra="Pase de ahorro: sin importes en este borrador sintético.")


def check(text, rs, person_types=None, snapshot=None, today=GOLDEN_TODAY):
    return lc.check_answer(text, rs, today, snapshot, person_types)


# ---------- the promises, as assertions (a mutation test below proves each one fails when the code breaks) ----------

def promise_person_type(tc, person_type):
    """Rows for the asker's type pass, rows for another type give row-not-applicable, an unknown type gives ask-person-type."""
    i = SEVEN.index(person_type)
    other = SEVEN[(i + 1) % len(SEVEN)]
    own_id, other_id, multi_id, open_id = slug(person_type), slug(other), "EU-SYN-MULTI", "EU-SYN-OPEN"
    rs = table(row(own_id, person_type), row(other_id, other), row(multi_id, "%s, %s" % (person_type, other)), row(open_id))
    parsed = lc.parse_profile(profile_text(person_type))["person_types"]
    tc.assertEqual(parsed, {person_type})
    use, ask, out = lc.rows_for(rs, parsed)
    tc.assertEqual([r.id for r in use], [own_id, multi_id, open_id])
    tc.assertEqual([r.id for r in out], [other_id])
    tc.assertEqual(ask, [])
    for ids in ((own_id,), (multi_id,), (open_id,), (own_id, multi_id, open_id)):
        tc.assertEqual(check(draft("VERDE", *ids), rs, parsed), [], ids)
    got = check(draft("VERDE", other_id), rs, parsed)
    tc.assertEqual([p.code for p in got], ["row-not-applicable", "green-unverified"])
    tc.assertIn(other_id, got[0].msg)
    tc.assertEqual(codes(check(draft("ÁMBAR", other_id), rs, parsed)), ["row-not-applicable"])
    # an unknown person type: every restricted row asks, none may support GREEN
    for text in (profile_text("(por confirmar)"), profile_text(""), profile_text("freelancer"), "# Legal profile\n"):
        tc.assertIsNone(lc.parse_profile(text)["person_types"], text)
    use, ask, out = lc.rows_for(rs, None)
    tc.assertEqual([r.id for r in use], [open_id])
    tc.assertEqual([r.id for r in ask], [own_id, other_id, multi_id])
    tc.assertEqual(out, [])
    amber = check(draft("ÁMBAR", own_id), rs, None)
    tc.assertEqual([p.code for p in amber], ["ask-person-type"])
    tc.assertEqual(lc.hard_problems(amber), [])
    green = check(draft("VERDE", own_id), rs, None)
    tc.assertEqual([p.code for p in green], ["ask-person-type", "green-unverified"])
    tc.assertEqual(codes(lc.hard_problems(green)), ["green-unverified"])
    # the shipped tables: what each type may use, as a golden table
    shipped = lc.load_rules()
    restricted = {r.id for r in shipped if r.applies_to}
    tc.assertEqual(restricted, {"FR-MICRO-THRESHOLDS", "FR-BNC-2035", "XB-SS-TWO-STATES"} | set(FR_SE_ROWS)
                   | {rid for rid, (types, _t) in ES_TAX_ROWS.items() if types})
    s_use, s_ask, s_out = lc.rows_for(shipped, parsed)
    tc.assertEqual({r.id for r in s_out}, SHIPPED_EXCLUDED[person_type])
    tc.assertEqual(s_ask, [])
    tc.assertEqual({r.id for r in lc.rows_for(shipped, None)[1]}, restricted)
    for rid in sorted(SHIPPED_EXCLUDED[person_type]):
        tc.assertEqual(codes(check(draft_money_safe("ÁMBAR", rid), shipped, parsed)), ["row-not-applicable"], rid)
    for rid in sorted(restricted - SHIPPED_EXCLUDED[person_type]):
        tc.assertNotIn("row-not-applicable", codes(check(draft_money_safe("ÁMBAR", rid), shipped, parsed)), rid)


def _excluded(base, person_type):
    """The base restricted rows plus every French self-employed row whose applies_to does not name the type."""
    return (set(base) | {rid for rid, (types, _tag) in FR_SE_ROWS.items() if person_type not in types}
            | {rid for rid, (types, _tag) in ES_TAX_ROWS.items() if types and person_type not in types})


SHIPPED_EXCLUDED = {
    "employee": _excluded({"FR-MICRO-THRESHOLDS", "FR-BNC-2035", "XB-SS-TWO-STATES"}, "employee"),
    "micro": _excluded(set(), "micro"),
    "bnc_reel": _excluded(set(), "bnc_reel"),
    "autonomo_directa": _excluded({"FR-MICRO-THRESHOLDS", "FR-BNC-2035"}, "autonomo_directa"),
    "autonomo_objetiva": _excluded({"FR-MICRO-THRESHOLDS", "FR-BNC-2035"}, "autonomo_objetiva"),
    "company": _excluded({"FR-MICRO-THRESHOLDS", "FR-BNC-2035", "XB-SS-TWO-STATES"}, "company"),
    "cross_border": _excluded({"FR-MICRO-THRESHOLDS", "FR-BNC-2035", "XB-SS-TWO-STATES"}, "cross_border"),
}


def promise_expired_row(tc):
    """A row past review_by cannot support GREEN; the day it is due it still can; AMBER may rest on it."""
    rs = table(row("EU-SYN-OLD", review_by=(GOLDEN_TODAY - DAY).isoformat()),
               row("EU-SYN-DUE", review_by=GOLDEN_TODAY.isoformat()),
               row("EU-SYN-NEW", review_by=(GOLDEN_TODAY + DAY).isoformat()),
               row("EU-SYN-SECOND", tag="S"))
    old, due, new, second = rs
    tc.assertFalse(lc.fresh(old, GOLDEN_TODAY))
    tc.assertTrue(lc.fresh(due, GOLDEN_TODAY))
    tc.assertTrue(lc.fresh(new, GOLDEN_TODAY))
    tc.assertFalse(lc.fresh(second, GOLDEN_TODAY))
    tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-OLD"), rs)], ["green-unverified"])
    tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-OLD", "EU-SYN-NEW"), rs)], ["green-unverified"])
    tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-SECOND"), rs)], ["green-unverified"])
    tc.assertEqual(check(draft("VERDE", "EU-SYN-DUE", "EU-SYN-NEW"), rs), [])
    tc.assertEqual(check(draft("ÁMBAR", "EU-SYN-OLD"), rs), [])
    # the same draft a day later: the row that was fine is now expired (the date, not the text, decides)
    tc.assertEqual(check(draft("VERDE", "EU-SYN-DUE"), rs, today=GOLDEN_TODAY), [])
    tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-DUE"), rs, today=GOLDEN_TODAY + DAY)], ["green-unverified"])
    listed = {r.id: why for r, why in lc.stale(rs, GOLDEN_TODAY)}
    tc.assertEqual(sorted(listed), ["EU-SYN-OLD", "EU-SYN-SECOND"])
    tc.assertIn("review_by", listed["EU-SYN-OLD"])


def snapshot_with_pending(rs):
    """A snapshot as the weekly diff leaves it: a baseline, then a changed page whose change nobody accepted."""
    snap = lc.empty_snapshot()
    pages = {BOFIP: "<p>Version 1 of the page</p>"}
    lc.diff(rs, [], snap, FakeFetch(pages), GOLDEN_TODAY - 7 * DAY)
    pages[BOFIP] = "<p>Version 2 of the page, with a newer version noted</p>"
    rep = lc.diff(rs, [], snap, FakeFetch(pages), GOLDEN_TODAY)
    return snap, rep


def promise_pending_page(tc):
    """A row whose official page has a newer version noted is not fresh: it cannot support GREEN until the change is accepted."""
    rs = table(row("EU-SYN-PAGE", url=BOFIP), row("EU-SYN-QUIET"))
    page = rs[0]
    tc.assertEqual(lc.lint_rules(rs), [])
    clean = lc.empty_snapshot()
    tc.assertTrue(lc.fresh(page, GOLDEN_TODAY, clean))
    snap, rep = snapshot_with_pending(rs)
    tc.assertEqual([(e["url"], e["status"]) for e in rep["sources"] if e["url"] == BOFIP], [(BOFIP, "changed")])
    tc.assertIn("pending", snap["sources"][BOFIP])
    tc.assertFalse(lc.fresh(page, GOLDEN_TODAY, snap))
    tc.assertTrue(lc.fresh(rs[1], GOLDEN_TODAY, snap), "a change on one page does not stale another row")
    tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-PAGE"), rs, snapshot=snap)], ["green-unverified"])
    tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-PAGE", "EU-SYN-QUIET"), rs, snapshot=snap)], ["green-unverified"])
    tc.assertEqual(check(draft("VERDE", "EU-SYN-QUIET"), rs, snapshot=snap), [])
    tc.assertEqual(check(draft("ÁMBAR", "EU-SYN-PAGE"), rs, snapshot=snap), [])
    # her yes (the legal checker's review) moves the baseline: only then is the row fresh again
    tc.assertEqual(lc.accept(snap, ["EU-SYN-PAGE"], rs), [BOFIP])
    tc.assertEqual(check(draft("VERDE", "EU-SYN-PAGE"), rs, snapshot=snap), [])


def promise_unreadable_snapshot(tc):
    """A snapshot that cannot be read is "pending unknown": nothing is fresh, never "nothing pending"."""
    rs = table(row("EU-SYN-QUIET"))
    home = tempfile.mkdtemp()
    tc.assertEqual(check(draft("VERDE", "EU-SYN-QUIET"), rs, snapshot=lc.snapshot_for_answer(home)), [])   # no file: no change seen
    for content in ("not json", "[]", json.dumps({"sources": "x"})):
        with open(os.path.join(home, "snapshot.json"), "w", encoding="utf-8") as f:
            f.write(content)
        snap = lc.snapshot_for_answer(home)
        tc.assertIs(snap, lc.UNKNOWN_SNAPSHOT, content)
        tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-QUIET"), rs, snapshot=snap)], ["green-unverified"], content)
    lc.save_snapshot(home, snapshot_with_pending(table(row("EU-SYN-PAGE", url=BOFIP)))[0])
    saved = lc.snapshot_for_answer(home)
    tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-PAGE"), table(row("EU-SYN-PAGE", url=BOFIP)), snapshot=saved)],
                   ["green-unverified"])


# One fixed sentence per trigger kind and language (SKILL.md section 2). They are written here, not read from the data file, so a
# phrase deleted from reference/settings.json makes a test fail instead of silently shrinking the check. No money words, no case text.
KIND_SAMPLES = {
    "contract": {"es": "Voy a firmar un contrato con un cliente.", "fr": "Je vais signer un contrat avec un client.",
                 "en": "I am about to sign a contract with a client."},
    "authority": {"es": "Me ha llegado un requerimiento de la Agencia Tributaria.", "fr": "J'ai reçu une mise en demeure de l'URSSAF.",
                  "en": "I received a letter from the tax office."},
    "filing": {"es": "Tengo que presentar la declaración de la renta.", "fr": "Je dois déposer la déclaration de revenus.",
               "en": "I need to file my tax return."},
    "criminal": {"es": "Podría haber responsabilidad penal.", "fr": "Il pourrait y avoir une responsabilité pénale.",
                 "en": "This could mean criminal exposure."},
    "personal_data": {"es": "Guardo datos personales de terceros.", "fr": "Je garde des données personnelles de tiers.",
                      "en": "I keep other people's personal data."},
    "breach": {"es": "Hubo una brecha de datos en el servicio.", "fr": "Il y a eu une fuite de données.",
               "en": "There was a data breach."},
    "publish": {"es": "Voy a publicarlo para desconocidos.", "fr": "Je vais le publier pour des inconnus.",
                "en": "I will publish it for strangers."},
}
LANGS = ("es", "fr", "en")
FULL_DISCLAIMERS = (
    "Orientación general, no asesoramiento jurídico ni fiscal. No soy abogada ni asesora colegiada: soy una herramienta. "
    "Cada cita lleva su fecha de comprobación.",
    "Information générale, pas un conseil juridique ni fiscal. Je ne suis pas avocate : je suis un outil. "
    "Chaque citation porte sa date de vérification.",
    "General guidance, not legal or tax advice. I am a tool, not a lawyer. Each citation carries the date it was checked.",
)


def trigger_draft(verdict, sentence, *ids):
    """A synthetic draft on a fresh row with one sentence added; it always carries a savings heading, so only the trigger decides."""
    return draft(verdict, *(ids or ("EU-SYN-OK",)), extra=sentence + "\nPase de ahorro: sin importes en este borrador sintético.")


def promise_escalation(tc):
    """A GREEN draft that hits an escalation trigger (SKILL.md section 2) is refused with escalation-trigger; AMBER and RED are not."""
    rs = table(row("EU-SYN-OK"), row("EU-SYN-ESC"))
    tc.assertEqual(check(trigger_draft("VERDE", "Sin novedad en este borrador sintético."), rs), [])
    with unittest.mock.patch.object(lc, "ESCALATION_ROWS", {"EU-SYN-ESC": "filing"}):
        tc.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-ESC"), rs)], ["escalation-trigger"])
        tc.assertEqual(check(draft("ÁMBAR", "EU-SYN-ESC"), rs), [])
        tc.assertEqual(check(draft("ROJO", "EU-SYN-ESC"), rs), [])
    for kind, by_lang in KIND_SAMPLES.items():
        for lang in LANGS:
            sentence = by_lang[lang]
            got = check(trigger_draft("VERDE", sentence), rs)
            tc.assertEqual([p.code for p in got], ["escalation-trigger"], (kind, lang))
            tc.assertIn(kind, got[0].msg)
            for amber_or_red in ("ÁMBAR", "ROJO"):
                tc.assertEqual(check(trigger_draft(amber_or_red, sentence), rs), [], (kind, lang, amber_or_red))


def promise_escalation_case(tc):
    """Case 5 (licences) is RED on six fresh rows. Rewritten as VERDE it must fail with escalation-trigger and nothing else.
    Read through the test only, as CaseGoldenTest does: its text is never copied here."""
    rules = lc.load_rules()
    head, sep, ans = case_answers()[LICENCES_CASE]
    tc.assertTrue(sep)
    stated = lc.VERDICT_RE.findall(ans)[0]
    as_green = ans.replace("Veredicto: %s" % stated, "Veredicto: VERDE", 1)
    tc.assertNotEqual(as_green, ans)
    tc.assertEqual(lc.hard_problems(check(ans, rules)), [])
    tc.assertEqual([p.code for p in lc.hard_problems(check(as_green, rules))], ["escalation-trigger"])


class PersonTypeGoldenTest(unittest.TestCase):
    """One asserted test per person type in reference/settings.json."""

    def test_the_settings_name_exactly_the_seven_types_so_a_new_type_needs_a_new_test(self):
        with open(os.path.join(SKILL, "reference", "settings.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(tuple(data["person_types"]), SEVEN)
        self.assertEqual(tuple(lc.PERSON_TYPES), SEVEN)
        self.assertEqual(set(SHIPPED_EXCLUDED), set(SEVEN))

    def test_employee(self):
        promise_person_type(self, "employee")

    def test_micro(self):
        promise_person_type(self, "micro")

    def test_bnc_reel(self):
        promise_person_type(self, "bnc_reel")

    def test_autonomo_directa(self):
        promise_person_type(self, "autonomo_directa")

    def test_autonomo_objetiva(self):
        promise_person_type(self, "autonomo_objetiva")

    def test_company(self):
        promise_person_type(self, "company")

    def test_cross_border(self):
        promise_person_type(self, "cross_border")

    def test_a_person_with_two_types_uses_the_rows_of_both(self):
        rs = table(row("EU-SYN-A", "micro"), row("EU-SYN-B", "cross_border"), row("EU-SYN-C", "company"))
        both = lc.parse_profile(profile_text("micro, cross_border"))["person_types"]
        self.assertEqual(both, {"micro", "cross_border"})
        self.assertEqual(check(draft("VERDE", "EU-SYN-A", "EU-SYN-B"), rs, both), [])
        self.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-C"), rs, both)], ["row-not-applicable", "green-unverified"])

    def test_an_unknown_type_in_a_row_is_a_lint_error_not_a_silent_exclusion(self):
        self.assertEqual(codes(lc.lint_rules(table(row("EU-SYN-A", "freelancer")))), ["bad-applies-to"])

    def test_the_shipped_rule_tables_lint_clean_at_the_frozen_date(self):
        self.assertEqual(lc.lint_rules(lc.load_rules()), [])


class ExpiredRowTest(unittest.TestCase):
    def test_a_row_past_review_by_blocks_a_green_draft_that_cites_it(self):
        promise_expired_row(self)

    def test_a_shipped_row_is_fresh_on_its_review_by_day_and_stale_the_day_after(self):
        shipped = [r for r in lc.load_rules() if r.tag == "V"]
        self.assertTrue(shipped)
        for r in shipped:
            rs = [r]
            if r.applies_to:
                types = {r.applies_to[0]}
            else:
                types = None
            trig = ["escalation-trigger"] if r.id in lc.ESCALATION_ROWS else []     # a row that is a trigger by nature (#11)
            self.assertEqual([p.code for p in check(draft_money_safe("VERDE", r.id), rs, types, today=r.review_by)], trig, r.id)
            self.assertEqual([p.code for p in check(draft_money_safe("VERDE", r.id), rs, types, today=r.review_by + DAY)],
                             ["green-unverified"] + trig, r.id)

    def test_the_shipped_s_and_u_rows_never_support_green(self):
        loose = [r for r in lc.load_rules() if r.tag != "V"]
        self.assertTrue(loose)
        for r in loose:
            types = {r.applies_to[0]} if r.applies_to else None
            trig = ["escalation-trigger"] if r.id in lc.ESCALATION_ROWS else []
            self.assertEqual([p.code for p in check(draft_money_safe("VERDE", r.id), [r], types)], ["green-unverified"] + trig, r.id)

    def test_the_weekly_check_window_comes_from_the_settings_data(self):
        with open(os.path.join(SKILL, "reference", "settings.json"), encoding="utf-8") as f:
            window = json.load(f)["stale_days"]
        self.assertEqual(window, lc.STALE_DAYS)
        last = lambda n: {"last_run": (GOLDEN_TODAY - n * DAY).isoformat()}   # noqa: E731
        self.assertFalse(lc.due(last(window - 1), GOLDEN_TODAY))
        self.assertTrue(lc.due(last(window), GOLDEN_TODAY))
        self.assertTrue(lc.due(last(window + 30), GOLDEN_TODAY))
        self.assertTrue(lc.due({"last_run": None}, GOLDEN_TODAY), "never run is due")
        self.assertTrue(lc.due(lc.empty_snapshot(), GOLDEN_TODAY))
        with unittest.mock.patch.object(lc, "STALE_DAYS", 3):
            self.assertTrue(lc.due(last(3), GOLDEN_TODAY))
            self.assertFalse(lc.due(last(2), GOLDEN_TODAY))


class StaleOfficialPageTest(unittest.TestCase):
    def test_a_page_with_a_newer_version_noted_blocks_green_until_accepted(self):
        promise_pending_page(self)

    def test_a_snapshot_that_cannot_be_read_blocks_green(self):
        promise_unreadable_snapshot(self)

    def test_the_pending_marker_written_by_hand_is_enough_and_a_markup_only_change_is_not(self):
        rs = table(row("EU-SYN-PAGE", url=BOFIP))
        by_hand = {"sources": {BOFIP: {"marker": "sha256:a", "pending": "sha256:b"}}}
        self.assertEqual([p.code for p in check(draft("VERDE", "EU-SYN-PAGE"), rs, snapshot=by_hand)], ["green-unverified"])
        snap = lc.empty_snapshot()
        lc.diff(rs, [], snap, FakeFetch({BOFIP: "<p>same text</p><script>a=1</script>"}), GOLDEN_TODAY - DAY)
        lc.diff(rs, [], snap, FakeFetch({BOFIP: "<div> same  text </div><script>a=2</script>"}), GOLDEN_TODAY)
        self.assertNotIn("pending", snap["sources"][BOFIP])
        self.assertEqual(check(draft("VERDE", "EU-SYN-PAGE"), rs, snapshot=snap), [])

    def test_a_page_that_goes_back_to_the_accepted_version_clears_the_pending_marker(self):
        rs = table(row("EU-SYN-PAGE", url=BOFIP))
        snap = lc.empty_snapshot()
        lc.diff(rs, [], snap, FakeFetch({BOFIP: "<p>one</p>"}), GOLDEN_TODAY - 2 * DAY)
        lc.diff(rs, [], snap, FakeFetch({BOFIP: "<p>two</p>"}), GOLDEN_TODAY - DAY)
        self.assertIn("pending", snap["sources"][BOFIP])
        lc.diff(rs, [], snap, FakeFetch({BOFIP: "<p>one</p>"}), GOLDEN_TODAY)
        self.assertEqual(check(draft("VERDE", "EU-SYN-PAGE"), rs, snapshot=snap), [])

    def test_the_skill_says_a_pending_row_is_not_fresh(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("**not fresh**", text)
        self.assertIn("cannot support a VERDE", text)


class PromiseMatrixTest(unittest.TestCase):
    """The rest of what SKILL.md section 4 promises, with the exact code each broken promise gives."""

    def setUp(self):
        self.rs = table(row("EU-SYN-OK"))

    def codes_of(self, text):
        return [p.code for p in check(text, self.rs)]

    def test_each_broken_promise_gives_its_own_code(self):
        good = draft("ÁMBAR", "EU-SYN-OK")
        self.assertEqual(self.codes_of(good), [])
        self.assertEqual(self.codes_of(good.replace("no asesoramiento jurídico ni fiscal", "x")), ["no-disclaimer"])
        self.assertEqual(self.codes_of(good.replace("Veredicto: ÁMBAR", "Resumen: ÁMBAR")), ["no-verdict"])
        self.assertEqual(self.codes_of(good + "- ver https://www.boe.es/x\n"), ["bare-url"])
        self.assertEqual(self.codes_of(draft("ÁMBAR", "EU-SYN-NOPE")), ["unknown-rule"])
        lexology = "- https://www.lexology.com/x [verificado hoy %s]" % GOLDEN_TODAY.isoformat()
        self.assertEqual(self.codes_of(answer("ÁMBAR", lexology)), ["non-official-url"])
        self.assertEqual(self.codes_of(answer("ÁMBAR", "- [verificado hoy %s]" % GOLDEN_TODAY.isoformat())), ["verified-without-url"])
        self.assertEqual(self.codes_of(answer("ÁMBAR")), ["no-citation"])
        self.assertEqual(self.codes_of(draft("ÁMBAR", "EU-SYN-OK", extra="Cuesta 120 € al año.")), ["no-savings-pass"])

    def test_green_needs_a_source_verified_on_the_frozen_day_and_nothing_split(self):
        url = "https://www.cnil.fr/fr/x"
        today = "- %s [verificado hoy %s]" % (url, GOLDEN_TODAY.isoformat())
        before = "- %s [verificado hoy %s]" % (url, (GOLDEN_TODAY - DAY).isoformat())
        self.assertEqual(self.codes_of(answer("VERDE", today)), [])
        self.assertEqual(self.codes_of(answer("VERDE", before)), ["green-unverified"])
        self.assertEqual(self.codes_of(answer("VERDE", today, "- Point [sin verificar]")), ["green-unverified"])
        self.assertEqual(self.codes_of(answer("VERDE", today, extra="Dividido: ES y FR leen distinto.")), ["green-split"])
        self.assertEqual(self.codes_of(answer("VERDE")), ["no-citation", "green-without-source"])

    def test_a_green_in_any_language_is_held_to_the_same_rule(self):
        fr = ("Information générale, pas un conseil juridique ni fiscal.\n\nVerdict : VERT\n\n- Point [non vérifié]\n")
        en = ("General guidance, not legal or tax advice.\n\nVerdict: GREEN\n\n- Point [unverified]\n")
        self.assertEqual(self.codes_of(fr), ["green-unverified"])
        self.assertEqual(self.codes_of(en), ["green-unverified"])


# ---------- the worked cases: the stated verdict of each, asserted ----------

CASE_VERDICT = {"1-residence-ei.md": "ámbar", "2-plan-finde.md": "ámbar", "3-own-accounts.md": "ámbar", "4-crypto.md": "ámbar",
                "5-budgit-licences.md": "rojo", "6-onphone-osint.md": "rojo", "7-work-timesheets.md": "ámbar"}


LICENCES_CASE = "5-budgit-licences.md"
U, E = "green-unverified", "escalation-trigger"
# The exact hard codes each worked case gets when its verdict is rewritten as VERDE (rows are fresh on the frozen day, so case 5
# fails on the trigger alone; the others also rest on an unverified row or source).
CASE_GREEN_CODES = {"1-residence-ei.md": [U], "2-plan-finde.md": [U, E], "3-own-accounts.md": [U], "4-crypto.md": [U, E],
                    "5-budgit-licences.md": [E], "6-onphone-osint.md": [U, E], "7-work-timesheets.md": [U, E]}


def case_answers():
    out = {}
    for name in sorted(os.listdir(os.path.join(SKILL, "cases"))):
        if name.endswith(".md"):
            with open(os.path.join(SKILL, "cases", name), encoding="utf-8") as f:
                head, sep, ans = f.read().partition("## Respuesta modelo")
            out[name] = (head, sep, ans)
    return out


class CaseGoldenTest(unittest.TestCase):
    """Read through the test only, as CasesTest does: the case text is never copied here."""

    def test_the_cases_folder_holds_exactly_the_cases_this_table_names(self):
        self.assertEqual(sorted(case_answers()), sorted(CASE_VERDICT))

    def test_each_case_states_the_verdict_its_golden_line_expects(self):
        for name, (head, sep, ans) in case_answers().items():
            self.assertTrue(sep, name)
            self.assertRegex(head, r"(?m)^Golden checks: \S", name)
            stated = [v.lower() for v in lc.VERDICT_RE.findall(ans)]
            self.assertEqual(stated, [CASE_VERDICT[name]], name)

    def test_the_checker_agrees_with_each_stated_verdict(self):
        """What the checker computes is whether GREEN is allowed. Each case states AMBER or RED, so rewriting it as GREEN must
        fail, and CASE_GREEN_CODES names the exact hard codes it fails with, case by case (a source problem, an escalation trigger
        by a row or a phrase, or both): a code that appears or vanishes for one case is a change someone has to look at."""
        rules = lc.load_rules()
        got = {}
        for name, (_h, _s, ans) in case_answers().items():
            self.assertEqual(lc.hard_problems(check(ans, rules)), [], name)
            stated = lc.VERDICT_RE.findall(ans)[0]
            as_green = ans.replace("Veredicto: %s" % stated, "Veredicto: VERDE", 1)
            self.assertNotEqual(as_green, ans, name)
            blocked = [p.code for p in lc.hard_problems(check(as_green, rules))]
            got[name] = blocked
        self.assertEqual(got, CASE_GREEN_CODES)

    def test_the_red_licences_case_rewritten_as_green_fails_with_escalation_trigger(self):
        """The gap #10 pinned is closed: the licences case is RED on fresh rows, so only the escalation check can refuse it."""
        promise_escalation_case(self)

    def test_a_case_that_cites_a_restricted_row_is_checked_for_its_person_type(self):
        rules = lc.load_rules()
        restricted = {r.id: set(r.applies_to) for r in rules if r.applies_to}
        seen = 0
        for name, (_h, _s, ans) in case_answers().items():
            used = [rid for rid in lc.RULE_TAG_RE.findall(ans) if rid in restricted]
            if not used:
                continue
            seen += 1
            for t in SEVEN:
                got = check(ans, rules, {t})
                wrong = {rid for rid in used if t not in restricted[rid]}
                self.assertEqual(sum(p.code == "row-not-applicable" for p in got), len([1 for rid in lc.RULE_TAG_RE.findall(ans)
                                                                                        if rid in wrong]), (name, t))
            self.assertEqual([p.code for p in check(ans, rules, None) if p.code != "ask-person-type"], [], name)
            self.assertIn("ask-person-type", [p.code for p in check(ans, rules, None)], name)
        self.assertGreaterEqual(seen, 1)


# ---------- escalation triggers (R-1125 #11): SKILL.md section 2 as data ----------

SETTINGS_PATH = os.path.join(SKILL, "reference", "settings.json")
# The rows that are a trigger by nature, as a golden table: a row added to or dropped from the data file needs a line here.
ROWS_GOLDEN = {
    "EU-GDPR-BREACH-72H": "breach",
    "EU-GDPR-LEGIT-INTEREST": "personal_data", "FR-CNIL-SCRAPING-ROBOTS": "personal_data",
    "FR-3916BIS-DUTY": "filing", "FR-3916BIS-FINE": "filing", "FR-3916-FINE-BANK": "filing", "FR-BNC-2035": "filing",
    "FR-VAT-CA3": "filing",
    "FR-CGI-CRYPTO-GAINS": "filing", "ES-IRPF-RESIDENCE": "filing", "ES-IRPF-PERMUTA": "filing", "ES-IRPF-FIFO": "filing",
    "ES-IRPF-TWO-MONTHS": "filing", "FR-CGI-RESIDENCE": "filing", "XB-ES-FR-TREATY": "filing",
    "FR-CGI-DAC8-PROVIDERS": "filing", "EU-DAC8-APPLIES": "filing",
    "EU-GDPR-CHILD-AGE": "personal_data", "FR-IL-MINOR-AGE15": "personal_data", "ES-LOPDGDD-AGE14": "personal_data",
    "ES-CP-403": "criminal", "FR-CP-433-17": "criminal", "FR-TITLE-AVOCAT": "criminal", "FR-CONSULT-ART54": "criminal",
    "ES-TITLE-ABOGADO": "criminal", "ES-EGAE-DENOMINATION": "criminal",
    "LIC-APACHE-S4": "publish", "LIC-AGPL-S13": "publish", "LIC-CC-NC": "publish", "LIC-CC-SA": "publish",
    **{rid: "filing" for rid in FR_SE_ROWS},          # R-1125 #1: every French self-employed tax row maps like its sibling FR-BNC-2035
    **{rid: "filing" for rid in ES_TAX_ROWS},         # R-1125 #8: every Spanish tax row maps like its sibling ES-IRPF-RESIDENCE
}


def settings_data():
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def section_2_items(skill_text):
    """The triggers SKILL.md section 2 names: the items of the middle-dot list, whitespace folded."""
    body = skill_text.split("## 2.", 1)[1].split("## 3.", 1)[0]
    listing = body.split("(section 7):", 1)[1].split(".\n", 1)[0]
    return [" ".join(item.split()).rstrip(".") for item in listing.split("·") if item.strip()]


def section_2_problems(skill_text, esc):
    """What is wrong between the section 2 list and the data: an item no entry names, an entry no item holds."""
    items = section_2_items(skill_text)
    words = [t["skill"] for t in esc["triggers"].values()] + list(esc["covered_elsewhere"])
    probs = ["no entry for item: %s" % i for i in items if sum(w in i for w in words) != 1]
    probs += ["no item for entry: %s" % w for w in words if sum(w in i for i in items) != 1]
    return probs


class EscalationTest(unittest.TestCase):
    def test_the_data_names_the_seven_kinds_each_with_phrases_in_three_languages(self):
        esc = settings_data()["escalation"]
        self.assertEqual(set(esc["triggers"]), set(KIND_SAMPLES))
        for kind, entry in esc["triggers"].items():
            for lang in LANGS:
                self.assertTrue(entry[lang] and all(isinstance(p, str) and p.strip() for p in entry[lang]), (kind, lang))
        self.assertEqual(set(lc.ESCALATION_PHRASES), set(KIND_SAMPLES))
        self.assertEqual(lc.ESCALATION_ROWS, esc["rows"])

    def test_the_rows_that_are_a_trigger_by_nature_are_this_golden_table(self):
        self.assertEqual(settings_data()["escalation"]["rows"], ROWS_GOLDEN)
        shipped = {r.id for r in lc.load_rules()}
        self.assertLessEqual(set(ROWS_GOLDEN), shipped)
        self.assertLessEqual(set(ROWS_GOLDEN.values()), set(KIND_SAMPLES))

    def test_each_shipped_trigger_row_refuses_a_green_draft_that_cites_it(self):
        shipped = lc.load_rules()
        for rid, kind in ROWS_GOLDEN.items():
            r = next(x for x in shipped if x.id == rid)
            types = {r.applies_to[0]} if r.applies_to else None
            if r.tag != "V":                      # an S row is also unverified: both codes, the trigger last
                want = ["green-unverified", "escalation-trigger"]
            else:
                want = ["escalation-trigger"]
            got = check(draft_money_safe("VERDE", rid), shipped, types, today=r.review_by)
            self.assertEqual([p.code for p in got], want, rid)
            self.assertIn(kind, got[-1].msg)
            self.assertEqual(check(draft_money_safe("ÁMBAR", rid), shipped, types, today=r.review_by), [], rid)
            self.assertEqual(check(draft_money_safe("ROJO", rid), shipped, types, today=r.review_by), [], rid)

    def test_every_phrase_in_the_data_refuses_a_green_draft_in_any_case_and_without_accents(self):
        rs = table(row("EU-SYN-OK"))
        for kind, entry in settings_data()["escalation"]["triggers"].items():
            for lang in LANGS:
                for stemmed in entry[lang]:
                    phrase = stemmed.replace("*", "o").lstrip("!")   # a stem word (firm*) is tried with one ending; ! keeps accents
                    for variant in (phrase, phrase.upper(), phrase.capitalize()):
                        got = [p.code for p in check(trigger_draft("VERDE", "Texto: %s." % variant), rs)]
                        self.assertEqual(got, ["escalation-trigger"], (kind, lang, variant))
                    self.assertEqual(check(trigger_draft("ÁMBAR", phrase), rs), [], (kind, lang, phrase))
        folded = [p.code for p in check(trigger_draft("VERDE", "Texto: responsabilite penale et declaracion de la renta."), rs)]
        self.assertEqual(folded, ["escalation-trigger"])

    def test_each_trigger_kind_in_each_language_is_refused_when_green(self):
        promise_escalation(self)

    def test_a_trigger_is_caught_in_common_word_forms(self):
        """Round 2 (F3): plurals and verb forms of a listed word hit, in all three languages."""
        rs = table(row("EU-SYN-OK"))
        for text in ("Hay delitos en juego.", "Several crimes were reported.", "Los contratos firmados ya están en casa.", "Firmé ayer.",
                     "Recibí una notification de l'URSSAF.", "Ils ont signé des contrats signés.", "She signed a contract.",
                     "Presenté la declaración ayer.", "I filed a return.", "Voy a publicarlo para desconocidos.",
                     "Il y a eu des violations de données.", "Se filtraron datos personales de terceros."):
            self.assertEqual([p.code for p in check(trigger_draft("VERDE", text), rs)], ["escalation-trigger"], text)
            self.assertEqual(check(trigger_draft("ÁMBAR", text), rs), [], text)

    def test_each_criminal_law_and_tax_fraud_phrase_refuses_green_in_each_language(self):
        """Round 3 (F5): penal / pénal / criminal law, tax fraud, tax evasion, laundering, delito fiscal."""
        rs = table(row("EU-SYN-OK"))
        sentences = {
            "es": ("Podría ser un fraude penal.", "Se trata de un delito fiscal.", "Hay fraude fiscal.", "Es evasión fiscal.",
                   "Es blanqueo de capitales.", "Es una cuestión de derecho penal.", "Es un asunto penal."),
            "fr": ("Il s'agit d'une fraude fiscale.", "C'est de l'évasion fiscale.", "C'est du blanchiment d'argent.",
                   "C'est une affaire pénale.", "Cela relève du droit pénal.", "Les poursuites pénales"),
            "en": ("This could be tax fraud.", "That is tax evasion.", "That is money laundering.", "That is criminal law."),
        }
        for lang, texts in sentences.items():
            for text in texts:
                self.assertEqual([p.code for p in check(trigger_draft("VERDE", text), rs)], ["escalation-trigger"], (lang, text))
                self.assertEqual(check(trigger_draft("ÁMBAR", text), rs), [], (lang, text))
        for lang in LANGS:
            data = settings_data()["escalation"]["triggers"]["criminal"][lang]
            for phrase in {"es": ("fraude fiscal", "delito fiscal", "blanqueo"), "fr": ("fraude fiscale", "évasion fiscale", "blanchiment"),
                           "en": ("tax fraud", "tax evasion", "money laundering")}[lang]:
                self.assertIn(phrase, data, (lang, phrase))

    def test_the_spanish_adjective_firme_is_not_a_trigger_but_firme_un_contrato_is(self):
        rs = table(row("EU-SYN-OK"))
        for text in ("Mantiene una postura firme.", "La sentencia es firme.", "Una firma digital basta."):
            self.assertEqual(check(trigger_draft("VERDE", text), rs), [], text)
        self.assertEqual([p.code for p in check(trigger_draft("VERDE", "Cuando firme un contrato nuevo."), rs)], ["escalation-trigger"])

    def test_a_settings_map_with_a_missing_row_stops_the_module_at_import(self):
        """The import-time check (validate_escalation_rows at load) is covered: a copy of the skill whose map names a row that no
        rules table holds cannot be loaded, and the untouched copy can."""
        import importlib.util
        import shutil
        work = tempfile.mkdtemp()
        copy = os.path.join(work, "skill")
        shutil.copytree(SKILL, copy, ignore=shutil.ignore_patterns("cases", "__pycache__"))

        def load(tag):
            spec = importlib.util.spec_from_file_location("legalcheck_copy_" + tag, os.path.join(copy, "scripts", "legalcheck.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        self.assertEqual(load("good").ESCALATION_ROWS, lc.ESCALATION_ROWS)
        path = os.path.join(copy, "reference", "settings.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["escalation"]["rows"]["EU-SYN-GONE"] = "filing"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaises(SystemExit) as caught:
            load("bad")
        self.assertIn("EU-SYN-GONE", str(caught.exception))

    def test_a_different_word_that_only_shares_a_prefix_does_not_hit(self):
        rs = table(row("EU-SYN-OK"))
        for text in ("Sobre Crimea no hay nada que decir.", "El firmware se actualiza solo.", "Los delicados detalles no cambian.",
                     "A signal was sent.", "Contratar a alguien más no aplica.",
                     "The jailbreak guide is not an issue."):
            self.assertEqual(check(trigger_draft("VERDE", text), rs), [], text)

    def test_every_es_irpf_row_and_every_minor_age_row_is_mapped_so_a_new_one_cannot_slip_by(self):
        shipped = [r.id for r in lc.load_rules()]
        families = ("ES-IRPF-", "RESIDENCE", "TREATY", "DAC8", "CRYPTO-GAINS")     # tax-position rows: a filing, whoever's the country
        for rid in shipped:
            if rid.startswith(families[0]) or any(f in rid for f in families[1:]):
                self.assertEqual(lc.ESCALATION_ROWS.get(rid), "filing", rid)
        for rid in shipped:                  # R-1125 #1: the French self-employed families are filing rows too
            if rid.startswith(("FR-BNC-", "FR-URSSAF-", "FR-VAT-DED-")):
                self.assertEqual(lc.ESCALATION_ROWS.get(rid), "filing", rid)
        for rid in shipped:                  # R-1125 #8: the Spanish tax families are filing rows too
            if rid.startswith(("ES-IVA-", "ES-RETA-", "ES-M721-")):
                self.assertEqual(lc.ESCALATION_ROWS.get(rid), "filing", rid)
        for rid in ("FR-CGI-RESIDENCE", "XB-ES-FR-TREATY", "FR-CGI-DAC8-PROVIDERS", "EU-DAC8-APPLIES"):
            self.assertIn(rid, shipped)
            self.assertEqual(lc.ESCALATION_ROWS.get(rid), "filing", rid)
        for rid in ("EU-GDPR-CHILD-AGE", "FR-IL-MINOR-AGE15", "ES-LOPDGDD-AGE14"):
            self.assertEqual(lc.ESCALATION_ROWS.get(rid), "personal_data", rid)
        for rid in ("FR-TITLE-AVOCAT", "FR-CONSULT-ART54", "ES-TITLE-ABOGADO"):
            self.assertEqual(lc.ESCALATION_ROWS.get(rid), "criminal", rid)

    def test_an_escalation_row_that_is_in_no_rules_table_fails_closed(self):
        rs = table(row("EU-SYN-OK"))
        lc.validate_escalation_rows(rs, {"EU-SYN-OK": "filing"})
        with self.assertRaises(SystemExit) as caught:
            lc.validate_escalation_rows(rs, {"EU-SYN-OK": "filing", "EU-SYN-GONE": "filing"})
        self.assertIn("EU-SYN-GONE", str(caught.exception))
        lc.validate_escalation_rows(lc.load_rules(), lc.ESCALATION_ROWS)        # the shipped map names only rows that exist

    def test_a_green_draft_without_a_trigger_still_passes_in_every_language(self):
        rs = table(row("EU-SYN-OK"))
        for text in (draft("VERDE", "EU-SYN-OK"), draft_money_safe("VERDE", "EU-SYN-OK"),
                     answer("VERDE", "- Point [verificado hoy %s] https://www.cnil.fr/fr/x" % GOLDEN_TODAY.isoformat())):
            self.assertEqual(check(text, rs), [], text)
        for disclaimer, verdict in zip(FULL_DISCLAIMERS, ("Veredicto: VERDE", "Verdict : VERT", "Verdict: GREEN")):
            text = "%s\n\n%s\n\n- Point [ficha EU-SYN-OK]\n" % (disclaimer, verdict)
            self.assertEqual(check(text, rs), [], verdict)
            amber = text.replace("VERDE", "ÁMBAR").replace("VERT", "ORANGE").replace("GREEN", "YELLOW")
            self.assertEqual(check(amber, rs), [], verdict)

    def test_the_words_of_a_phrase_must_stand_alone(self):
        rs = table(row("EU-SYN-OK"))
        self.assertEqual(check(trigger_draft("VERDE", "Sobre Crimea no hay nada que decir."), rs), [])
        self.assertEqual([p.code for p in check(trigger_draft("VERDE", "It is a crime."), rs)], ["escalation-trigger"])

    def test_amber_and_red_drafts_with_every_trigger_at_once_have_no_problem(self):
        rs = table(row("EU-SYN-OK"))
        sentence = " ".join(v["es"] for v in KIND_SAMPLES.values())
        for verdict in ("ÁMBAR", "ROJO"):
            self.assertEqual(check(trigger_draft(verdict, sentence), rs), [], verdict)
        self.assertEqual([p.code for p in check(trigger_draft("VERDE", sentence), rs)], ["escalation-trigger"])

    def test_the_trigger_is_a_hard_problem_in_every_verdict_language(self):
        rs = table(row("EU-SYN-OK"))
        for green in ("VERDE", "VERT", "GREEN"):
            text = "Not legal or tax advice.\n\nVerdict: %s\n\n- Point [ficha EU-SYN-OK]\nI will publish it for strangers.\n" % green
            if green == "VERDE":
                text = text.replace("Verdict:", "Veredicto:")
            self.assertEqual(codes(lc.hard_problems(check(text, rs))), ["escalation-trigger"], green)

    def test_the_question_line_and_the_body_are_both_read(self):
        rs = table(row("EU-SYN-OK"))
        question = "Pregunta: ¿puedo firmar un contrato firmado hoy?"
        lines = answer("VERDE", "- Point [ficha EU-SYN-OK]").splitlines()
        with_question = "\n".join([lines[0], question] + lines[1:]) + "\n"
        self.assertEqual([p.code for p in check(with_question, rs)], ["escalation-trigger"])
        self.assertEqual([p.code for p in check(trigger_draft("VERDE", "Se trata de un contrato firmado."), rs)], ["escalation-trigger"])

    def test_the_skill_section_2_and_the_data_name_the_same_triggers(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            skill = f.read()
        esc = settings_data()["escalation"]
        self.assertEqual(len(section_2_items(skill)), 9)
        self.assertEqual(section_2_problems(skill, esc), [])
        # the comparator bites: a trigger the data names and the skill does not, and the reverse
        more = json.loads(json.dumps(esc))
        more["triggers"]["new_kind"] = {"skill": "a brand new trigger"}
        self.assertEqual(section_2_problems(skill, more), ["no item for entry: a brand new trigger"])
        fewer = skill.replace("breach or leaked data", "an unrelated thing")
        self.assertTrue(section_2_problems(fewer, esc))
        self.assertEqual(esc["covered_elsewhere"], {"a split verdict": "green-split",
                                                    "a source that cannot be verified": "green-unverified"})

    def test_the_skill_says_the_checker_refuses_a_green_draft_that_hits_a_trigger(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            skill = f.read()
        self.assertIn("escalation-trigger", skill)
        self.assertIn("reference/settings.json", skill.split("## 2.", 1)[1].split("## 3.", 1)[0])

    def test_a_settings_file_without_a_usable_escalation_block_fails_closed(self):
        base = settings_data()
        d = tempfile.mkdtemp()
        broken = {}
        no_block = {k: v for k, v in base.items() if k != "escalation"}
        broken["noblock.json"] = no_block
        no_lang = json.loads(json.dumps(base))
        no_lang["escalation"]["triggers"]["breach"]["fr"] = []
        broken["nolang.json"] = no_lang
        bad_row = json.loads(json.dumps(base))
        bad_row["escalation"]["rows"]["EU-SYN-X"] = "not_a_kind"
        broken["badrow.json"] = bad_row
        no_phrases = json.loads(json.dumps(base))
        no_phrases["escalation"]["triggers"] = {}
        broken["notriggers.json"] = no_phrases
        for name, data in broken.items():
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            with self.assertRaises(SystemExit, msg=name):
                lc.load_settings(path)
        good = os.path.join(d, "good.json")
        with open(good, "w", encoding="utf-8") as f:
            json.dump(base, f)
        self.assertEqual(lc.load_settings(good)["escalation"]["rows"], base["escalation"]["rows"])


# ---------- the guards bite: break each promise and its test goes red ----------

class MutationTest(unittest.TestCase):
    def assertBites(self, promise, *args, **patches):
        with unittest.mock.patch.multiple(lc, **patches):
            with self.assertRaises(AssertionError):
                promise(self, *args)

    def test_an_expired_row_test_fails_if_fresh_ignores_the_date(self):
        self.assertBites(promise_expired_row, fresh=lambda rule, today, snapshot=None: rule.tag == "V")

    def test_the_person_type_test_fails_if_every_row_applies(self):
        self.assertBites(promise_person_type, "micro", row_status=lambda rule, person_types: "applies")

    def test_the_person_type_test_fails_if_an_unknown_type_is_treated_as_everyone(self):
        self.assertBites(promise_person_type, "company", rows_for=lambda rules, person_types: (list(rules), [], []))

    def test_the_pending_page_test_fails_if_pending_is_ignored(self):
        self.assertBites(promise_pending_page, pending=lambda rule, snapshot: False)

    def test_the_unreadable_snapshot_test_fails_if_unknown_means_nothing_pending(self):
        self.assertBites(promise_unreadable_snapshot, pending=lambda rule, snapshot: False)

    def test_the_escalation_test_fails_if_the_trigger_check_is_off(self):
        self.assertBites(promise_escalation, escalation_hits=lambda text, cited: [])
        self.assertBites(promise_escalation_case, escalation_hits=lambda text, cited: [])

    def test_the_escalation_test_fails_if_the_phrases_are_ignored(self):
        self.assertBites(promise_escalation, ESCALATION_PHRASES={})

    def test_the_escalation_test_fails_if_the_check_hits_every_verdict(self):
        """The other way round: a check that also refuses AMBER and RED drafts breaks the 'unaffected' half of the promise."""
        real = lc.check_answer

        def strict(text, rules, today, snapshot=None, person_types=None):
            probs = real(text, rules, today, snapshot, person_types)
            if lc.escalation_hits(text, []) and not any(p.code == "escalation-trigger" for p in probs):
                probs.append(lc.Problem("escalation-trigger", 0, "every verdict"))
            return probs
        with unittest.mock.patch.object(lc, "check_answer", strict):
            with self.assertRaises(AssertionError):
                promise_escalation(self)

    def test_the_promises_hold_unpatched(self):
        promise_escalation(self)
        promise_escalation_case(self)
        promise_expired_row(self)
        promise_pending_page(self)
        promise_unreadable_snapshot(self)
        promise_person_type(self, "micro")


if __name__ == "__main__":
    unittest.main()
