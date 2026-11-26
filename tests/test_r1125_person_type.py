"""R-1125 #9 (her I-1670, I-1671: the legal skill adapts to who is asking).
Each rule row says which person types it applies to; an answer uses only the rows that apply and asks when the person type
is unknown; the settings that drift (hosts, feeds, the freshness window, the panel cap) live in one data file.
Synthetic profiles only: no real profile, no ~/.studio, no ~/.budgit."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

import test_legalcheck as base
from test_legalcheck import lc, codes, answer, TODAY, SKILL, SCRIPT, HEADER, ROW_OK

SETTINGS = os.path.join(SKILL, "reference", "settings.json")
HEADER_AT = ("| id | rule | instrument | url | status | checked | review_by | tag | applies_to |\n"
             "|---|---|---|---|---|---|---|---|---|\n")
MICRO = ("| FR-MICRO-THRESHOLDS | The micro regime applies within the turnover thresholds. | Official sheet "
         "| https://entreprendre.service-public.gouv.fr/vosdroits/F32353 | applies 2026 | 2026-10-26 | 2027-04-24 | V | micro |\n")
BNC = ("| FR-BNC-2035 | A BNC business under the real regime files form 2035-SD. | Form 2035-SD "
       "| https://www.impots.gouv.fr/sites/default/files/formulaires/2035-sd/2026/2035-sd_5382.pdf | in force | 2026-10-26 | 2027-04-24 | V | bnc_reel |\n")
OPEN = ("| EU-GDPR-HOUSEHOLD | Purely personal or household activity is outside the GDPR. | GDPR art. 2(2)(c) "
        "| https://eur-lex.europa.eu/eli/reg/2016/679/oj | in force | 2026-10-26 | 2027-04-24 | V |  |\n")


def table(*rows):
    return lc.parse_rules(HEADER_AT + "".join(rows))


def draft(verdict, *cites):
    return answer(verdict, *cites)


def profile_text(person_type, regime="micro-BNC"):
    return "# Legal profile\n\n- Residence and nationality: synthetic\n- person_type: %s\n- regime: %s\n- Last confirmed: 2026-11-14\n" % (
        person_type, regime)


class AppliesToRowsTest(unittest.TestCase):
    def test_a_row_lists_its_person_types_and_empty_means_all(self):
        micro, bnc, open_ = table(MICRO, BNC, OPEN)
        self.assertEqual(micro.applies_to, ("micro",))
        self.assertEqual(open_.applies_to, ())
        multi = table(MICRO.replace("| micro |", "| micro, bnc_reel ; Company |"))[0]
        self.assertEqual(multi.applies_to, ("micro", "bnc_reel", "company"))
        no_column = base.rules(ROW_OK)[0]                 # a table with no applies_to column at all
        self.assertEqual(no_column.applies_to, ())

    def test_the_lint_accepts_known_person_types_and_rejects_unknown_ones(self):
        self.assertEqual(lc.lint_rules(table(MICRO, BNC, OPEN)), [])
        bad = table(MICRO.replace("| micro |", "| micro, freelancer |"))
        self.assertEqual(codes(lc.lint_rules(bad)), ["bad-applies-to"])
        self.assertIn("freelancer", lc.lint_rules(bad)[0].msg)

    def test_the_seven_person_types_are_the_ones_the_brief_names(self):
        self.assertEqual(set(lc.PERSON_TYPES), {"employee", "micro", "bnc_reel", "autonomo_directa", "autonomo_objetiva",
                                                 "company", "cross_border"})

    def test_the_shipped_tables_lint_clean_and_tag_only_the_plain_rows(self):
        rs = lc.load_rules()
        self.assertEqual(lc.lint_rules(rs), [])
        tagged = {r.id: r.applies_to for r in rs if r.applies_to}
        self.assertEqual(tagged["FR-MICRO-THRESHOLDS"], ("micro", "bnc_reel"))
        self.assertEqual(tagged["FR-BNC-2035"], ("micro", "bnc_reel"))
        self.assertLessEqual({"FR-MICRO-THRESHOLDS", "FR-BNC-2035", "XB-SS-TWO-STATES"}, set(tagged))
        # the French self-employed rows (R-1125 #1) are pinned in test_r1125_golden.FR_SE_ROWS
        extra = set(tagged) - {"FR-MICRO-THRESHOLDS", "FR-BNC-2035", "XB-SS-TWO-STATES"}
        self.assertTrue(all(x.startswith(("FR-BNC-", "FR-URSSAF-", "FR-VAT-DED-", "ES-IRPF-", "ES-IVA-", "ES-RETA-", "ES-M721-"))
                            for x in extra), extra)       # the Spanish ones (R-1125 #8) are pinned in test_r1125_golden.ES_TAX_ROWS
        for t in ("employee", "company", "cross_border"):
            self.assertNotIn(t, tagged["XB-SS-TWO-STATES"])

    def test_a_micro_comparing_both_bnc_regimes_gets_no_row_not_applicable(self):
        rs = lc.load_rules()
        text = draft("ÁMBAR", "- [ficha FR-MICRO-THRESHOLDS]", "- [ficha FR-BNC-2035]")
        self.assertNotIn("row-not-applicable", codes(lc.check_answer(text, rs, TODAY, None, {"micro"})))
        self.assertNotIn("row-not-applicable", codes(lc.check_answer(text, rs, TODAY, None, {"bnc_reel"})))

    def test_a_cross_border_only_employee_gets_row_not_applicable_on_the_self_employed_row(self):
        rs = lc.load_rules()
        text = draft("ÁMBAR", "- [ficha XB-SS-TWO-STATES]")
        self.assertIn("row-not-applicable", codes(lc.check_answer(text, rs, TODAY, None, {"cross_border"})))
        self.assertNotIn("row-not-applicable", codes(lc.check_answer(text, rs, TODAY, None, {"micro", "cross_border"})))

    def test_the_rows_command_lists_what_applies_ask_and_excluded(self):
        rules_dir, home = tempfile.mkdtemp(), tempfile.mkdtemp()
        with open(os.path.join(rules_dir, "t.md"), "w", encoding="utf-8") as f:
            f.write(HEADER_AT + "".join((MICRO, BNC, OPEN)))
        env = dict(os.environ, LEGAL_HOME=home)

        def run():
            return subprocess.run([sys.executable, "-I", SCRIPT, "--rules", rules_dir, "rows"],
                                  capture_output=True, text=True, env=env)
        r = run()                                                        # no profile: unknown
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ask the person type", r.stdout)
        self.assertIn("ask FR-MICRO-THRESHOLDS", r.stdout)
        self.assertIn("1 rule(s) apply, 2 to ask about, 0 excluded", r.stdout)
        with open(os.path.join(home, "legal-profile.md"), "w", encoding="utf-8") as f:
            f.write(profile_text("micro"))
        r = run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("person type: micro", r.stdout)
        self.assertIn("excluded FR-BNC-2035 (applies to bnc_reel)", r.stdout)
        self.assertIn("2 rule(s) apply, 0 to ask about, 1 excluded", r.stdout)

    def test_the_script_stops_when_settings_json_is_missing(self):
        import shutil
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "scripts"))
        os.makedirs(os.path.join(d, "rules"))
        shutil.copy(SCRIPT, os.path.join(d, "scripts", "legalcheck.py"))             # a skill copy with no reference/settings.json
        r = subprocess.run([sys.executable, "-I", os.path.join(d, "scripts", "legalcheck.py"), "home"],
                           capture_output=True, text=True, env=dict(os.environ, LEGAL_HOME=tempfile.mkdtemp()))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("settings file", r.stderr)
        self.assertIn("missing or malformed", r.stderr)


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.micro, self.bnc, self.open = table(MICRO, BNC, OPEN)

    def test_row_status_by_person_type(self):
        self.assertEqual(lc.row_status(self.micro, {"micro"}), "applies")
        self.assertEqual(lc.row_status(self.micro, {"employee"}), "excluded")
        self.assertEqual(lc.row_status(self.micro, {"employee", "micro"}), "applies")
        self.assertEqual(lc.row_status(self.micro, None), "ask")
        self.assertEqual(lc.row_status(self.open, {"employee"}), "applies")
        self.assertEqual(lc.row_status(self.open, None), "applies")

    def test_rows_for_keeps_only_the_rows_that_apply_and_names_the_ones_to_ask_about(self):
        use, ask, out = lc.rows_for([self.micro, self.bnc, self.open], {"micro"})
        self.assertEqual([r.id for r in use], ["FR-MICRO-THRESHOLDS", "EU-GDPR-HOUSEHOLD"])
        self.assertEqual([r.id for r in out], ["FR-BNC-2035"])
        self.assertEqual(ask, [])
        use, ask, out = lc.rows_for([self.micro, self.bnc, self.open], None)
        self.assertEqual([r.id for r in use], ["EU-GDPR-HOUSEHOLD"])
        self.assertEqual([r.id for r in ask], ["FR-MICRO-THRESHOLDS", "FR-BNC-2035"])
        self.assertEqual(out, [])


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self.rs = table(MICRO, BNC, OPEN)

    def check(self, verdict, cite, person_types):
        return lc.check_answer(draft(verdict, cite), self.rs, TODAY, None, person_types)

    def test_a_row_for_the_askers_type_is_used_as_before(self):
        self.assertEqual(self.check("VERDE", "- [ficha FR-MICRO-THRESHOLDS]", {"micro"}), [])

    def test_a_row_for_another_type_is_refused_and_never_supports_green(self):
        got = self.check("VERDE", "- [ficha FR-MICRO-THRESHOLDS]", {"employee"})
        self.assertEqual(codes(got), ["green-unverified", "row-not-applicable"])
        amber = self.check("ÁMBAR", "- [ficha FR-MICRO-THRESHOLDS]", {"employee"})
        self.assertEqual(codes(amber), ["row-not-applicable"])

    def test_unknown_person_type_on_a_restricted_row_gets_an_amber_note_to_ask(self):
        got = self.check("ÁMBAR", "- [ficha FR-BNC-2035]", None)
        self.assertEqual(codes(got), ["ask-person-type"])
        self.assertIn("AMBER", got[0].msg)
        self.assertIn("ask the person type", got[0].msg)
        self.assertEqual(lc.hard_problems(got), [], "the note does not fail an answer that is already AMBER")

    def test_unknown_person_type_never_lets_a_restricted_row_support_green(self):
        got = self.check("VERDE", "- [ficha FR-BNC-2035]", None)
        # FR-BNC-2035 is a filing row, so it is also an escalation trigger by nature (R-1125 #11)
        self.assertEqual(codes(got), ["ask-person-type", "escalation-trigger", "green-unverified"])
        self.assertEqual(codes(lc.hard_problems(got)), ["escalation-trigger", "green-unverified"])

    def test_an_unrestricted_row_needs_no_person_type(self):
        self.assertEqual(self.check("VERDE", "- [ficha EU-GDPR-HOUSEHOLD]", None), [])
        self.assertEqual(self.check("VERDE", "- [ficha EU-GDPR-HOUSEHOLD]", {"employee"}), [])

    def test_the_answer_command_reads_the_profile_person_type(self):
        rules_dir, home, out = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
        with open(os.path.join(rules_dir, "t.md"), "w", encoding="utf-8") as f:
            f.write(HEADER_AT + "".join((MICRO, OPEN)))
        path = os.path.join(out, "a.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(draft("ÁMBAR", "- [ficha FR-MICRO-THRESHOLDS]"))
        env = dict(os.environ, LEGAL_HOME=home)

        def run(*extra):
            return subprocess.run([sys.executable, "-I", SCRIPT, "--rules", rules_dir, "answer", path, "--today", "2026-10-26"],
                                  capture_output=True, text=True, env=env)
        r = run()                                                        # no profile at all: unknown
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ask-person-type", r.stdout)
        with open(os.path.join(home, "legal-profile.md"), "w", encoding="utf-8") as f:
            f.write(profile_text("employee"))
        r = run()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("row-not-applicable", r.stdout)
        with open(os.path.join(home, "legal-profile.md"), "w", encoding="utf-8") as f:
            f.write(profile_text("micro"))
        r = run()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("person", r.stdout)


class ProfileTest(unittest.TestCase):
    def test_person_type_and_free_text_regime_are_read_from_the_profile(self):
        p = lc.parse_profile(profile_text("bnc_reel", "BNC réel, déclaration contrôlée (my accountant's words)"))
        self.assertEqual(p["person_types"], {"bnc_reel"})
        self.assertEqual(p["regime"], "BNC réel, déclaration contrôlée (my accountant's words)")

    def test_a_cross_border_micro_may_hold_two_types(self):
        self.assertEqual(lc.parse_profile(profile_text("micro, cross_border"))["person_types"], {"micro", "cross_border"})

    def test_unconfirmed_empty_missing_or_unknown_values_mean_unknown(self):
        for text in (profile_text("(por confirmar)"), profile_text(""), profile_text("freelancer"),
                     profile_text("micro, freelancer"), "# Legal profile\n- Residence: synthetic\n", ""):
            self.assertIsNone(lc.parse_profile(text)["person_types"], text)

    def test_read_profile_from_the_legal_home_and_never_crashes(self):
        home = tempfile.mkdtemp()
        self.assertIsNone(lc.read_profile(home)["person_types"])                # no file
        with open(os.path.join(home, "legal-profile.md"), "w", encoding="utf-8") as f:
            f.write(profile_text("company"))
        self.assertEqual(lc.read_profile(home)["person_types"], {"company"})
        with open(os.path.join(home, "legal-profile.md"), "wb") as f:
            f.write(b"\xff\xfe\x00 not utf-8")
        self.assertIsNone(lc.read_profile(home)["person_types"])                # unreadable: unknown, not an error

    def test_the_template_and_the_skill_ask_for_both_fields(self):
        with open(os.path.join(SKILL, "reference", "profile-template.md"), encoding="utf-8") as f:
            tpl = f.read()
        self.assertEqual(lc.parse_profile(tpl)["person_types"], None)         # the blank template is unknown
        for key in ("person_type:", "regime:"):
            self.assertIn(key, tpl)
        for t in lc.PERSON_TYPES:
            self.assertIn(t, tpl)
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            skill = f.read()
        for needle in ("applies_to", "person_type", "ask the person type"):
            self.assertIn(needle, skill)


class SettingsAreDataTest(unittest.TestCase):
    def data(self):
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)

    def test_the_hosts_feeds_window_and_cap_come_from_the_data_file(self):
        d = self.data()
        self.assertEqual(tuple(d["official_hosts"]), lc.OFFICIAL_HOSTS)
        self.assertEqual(d["manual_hosts"], lc.MANUAL_HOSTS)
        self.assertEqual(d["stale_days"], lc.STALE_DAYS)
        self.assertEqual(d["panel_cap"], lc.PANEL_CAP)
        self.assertEqual(d["person_types"], list(lc.PERSON_TYPES))
        self.assertIn("legifrance.gouv.fr", d["manual_hosts"])
        self.assertTrue(lc.official("https://www.boe.es/x"))

    def test_the_script_holds_no_host_list_or_window_literal_any_more(self):
        with open(SCRIPT, encoding="utf-8") as f:
            src = f.read()
        for needle in ('"curia.europa.eu"', '"cnil.fr"', '"gnu.org"', "STALE_DAYS = 7", "MANUAL_HOSTS = {"):
            self.assertNotIn(needle, src)

    def test_the_registry_network_list_and_the_data_file_agree(self):
        with open(os.path.join(SKILL, "registry-entry.json"), encoding="utf-8") as f:
            reg = json.load(f)
        self.assertEqual(sorted(reg["requires"]["network"]), sorted(self.data()["official_hosts"]))
        self.assertNotIn("OFFICIAL_HOSTS in scripts", json.dumps(reg["security"]))
        self.assertEqual(reg["cost"]["agents_panel"], self.data()["panel_cap"])

    def test_the_skill_text_states_the_panel_cap_of_the_data_file(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            skill = f.read()
        self.assertIn("(at most %d)" % self.data()["panel_cap"], skill)

    def test_a_changed_window_and_host_list_change_the_behaviour_without_a_code_edit(self):
        with unittest.mock.patch.object(lc, "STALE_DAYS", 3):
            self.assertTrue(lc.due({"last_run": "2026-10-23"}, TODAY))
        self.assertFalse(lc.due({"last_run": "2026-10-23"}, TODAY))             # the shipped 7 days
        with unittest.mock.patch.object(lc, "OFFICIAL_HOSTS", ("example.org",)):
            self.assertTrue(lc.official("https://example.org/a"))
            self.assertFalse(lc.official("https://www.boe.es/x"))

    def test_load_settings_reads_a_given_file_and_fails_closed_on_a_bad_one(self):
        d = tempfile.mkdtemp()
        good = os.path.join(d, "s.json")
        with open(good, "w", encoding="utf-8") as f:
            json.dump({"official_hosts": ["a.example"], "manual_hosts": {}, "stale_days": 2, "panel_cap": 1,
                       "person_types": ["micro"], "regimes": {"fr": ["x"]}, "escalation": self.data()["escalation"]}, f)
        s = lc.load_settings(good)
        self.assertEqual((s["official_hosts"], s["stale_days"], s["panel_cap"]), (("a.example",), 2, 1))
        for name, content in (("missing.json", None), ("empty.json", "{}"), ("junk.json", "not json"),
                              ("nohosts.json", json.dumps({"official_hosts": [], "manual_hosts": {}, "stale_days": 7,
                                                           "panel_cap": 3, "person_types": ["micro"]})),
                              ("badwindow.json", json.dumps({"official_hosts": ["a.example"], "manual_hosts": {},
                                                             "stale_days": 0, "panel_cap": 3, "person_types": ["micro"]}))):
            path = os.path.join(d, name)
            if content is not None:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            with self.assertRaises(SystemExit, msg=name):
                lc.load_settings(path)

    def test_regime_suggestions_exist_per_country_and_the_template_lists_them(self):
        regimes = self.data()["regimes"]
        self.assertEqual(sorted(regimes), ["es", "fr"])
        with open(os.path.join(SKILL, "reference", "profile-template.md"), encoding="utf-8") as f:
            tpl = f.read()
        for country in regimes.values():
            self.assertTrue(country)
            for name in country:
                self.assertIn(name, tpl)


if __name__ == "__main__":
    unittest.main()
