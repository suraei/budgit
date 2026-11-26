"""R-1125 piece B (her I-1670: money answers hunt for savings; a rule that may have changed is never current).
#6 a rule whose source has a pending change is not fresh. #5 a money answer carries a «Pase de ahorro» block.
No network, no real snapshot: the fetcher and the snapshot are fakes."""
import os
import re
import subprocess
import sys
import tempfile
import json
import unittest

import test_legalcheck as base
from test_legalcheck import lc, rules, answer, codes, TODAY, ROW_OK, ROW_S, SKILL, SCRIPT

EUR_URL = "https://eur-lex.europa.eu/eli/reg/2016/679/oj"
BOE_URL = "https://www.boe.es/buscar/act.php?id=BOE-A-2025-24440"
PASE = "Pase de ahorro:\n- Deducción de gastos de oficina: hasta 300 € [ficha EU-GDPR-HOUSEHOLD]\n"
MONEY = "Pagarás 1.200 € de IVA este trimestre."


def snap(**pending):
    s = lc.empty_snapshot()
    for url in (EUR_URL, BOE_URL):
        s["sources"][url] = {"marker": "date:old", "seen": "2026-10-26"}
    for url, marker in pending.items():
        s["sources"][{"eur": EUR_URL, "boe": BOE_URL}[url]]["pending"] = marker
    return s


class PendingIsNotFreshTest(unittest.TestCase):
    def setUp(self):
        self.ok, self.sec = rules(ROW_OK, ROW_S)

    def test_a_rule_whose_source_is_pending_is_not_fresh(self):
        self.assertTrue(lc.fresh(self.ok, TODAY, snap()))
        self.assertFalse(lc.fresh(self.ok, TODAY, snap(eur="date:new")))

    def test_after_accept_the_rule_is_fresh_again(self):
        s = snap(eur="date:new")
        self.assertFalse(lc.fresh(self.ok, TODAY, s))
        self.assertEqual(lc.accept(s, [self.ok.id], [self.ok, self.sec]), [EUR_URL])
        self.assertTrue(lc.fresh(self.ok, TODAY, s))

    def test_a_rule_whose_source_has_no_pending_marker_is_unchanged(self):
        # another source pending, an unseen source, no snapshot at all: all as before
        self.assertTrue(lc.fresh(self.ok, TODAY, snap(boe="date:new")))
        self.assertTrue(lc.fresh(self.ok, TODAY, lc.empty_snapshot()))
        self.assertTrue(lc.fresh(self.ok, TODAY))
        self.assertTrue(lc.fresh(self.ok, TODAY, None))
        self.assertFalse(lc.fresh(self.sec, TODAY, snap()))   # tag S stays not fresh

    def test_a_pending_source_stops_a_green_answer(self):
        text = answer("VERDE", "- GDPR art. 2(2)(c) [ficha EU-GDPR-HOUSEHOLD]")
        self.assertEqual(codes(lc.check_answer(text, [self.ok], TODAY, snap())), [])
        self.assertEqual(codes(lc.check_answer(text, [self.ok], TODAY, snap(eur="date:new"))), ["green-unverified"])

    def test_the_answer_command_reads_the_snapshot(self):
        home = tempfile.mkdtemp()
        lc.save_snapshot(home, snap(eur="date:new"))
        d = tempfile.mkdtemp()
        path = os.path.join(d, "a.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(answer("VERDE", "- [ficha EU-GDPR-HOUSEHOLD]"))
        env = dict(os.environ, LEGAL_HOME=home)
        r = subprocess.run([sys.executable, "-I", SCRIPT, "answer", path, "--today", "2026-10-26"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("green-unverified", r.stdout)

    def test_the_baseline_comment_says_after_the_legal_checkers_review(self):
        with open(SCRIPT, encoding="utf-8") as f:
            src = f.read()
        line = [l for l in src.splitlines() if 'prev["pending"] = m' in l][0]
        self.assertIn("after the legal checker's review", line)
        self.assertNotIn("user's yes", line)


class IsMoneyTest(unittest.TestCase):
    """The notion of "money answer" and the heading finder (enforced by SavingsPassTest below)."""

    def test_the_english_disclaimer_alone_is_not_money(self):
        en = "General guidance, not legal or tax advice.\n\nVerdict: GREEN\n\n- [rule EU-GDPR-HOUSEHOLD]\n"
        self.assertFalse(lc.is_money(en))
        self.assertFalse(lc.is_money(answer("VERDE", "- GDPR art. 2(2)(c) [ficha EU-GDPR-HOUSEHOLD]")))

    def test_a_tax_word_or_an_amount_makes_it_money(self):
        for word in ("El IRPF sube.", "La TVA est due.", "VAT is due.", "Cuesta 40 EUR.", "Cuesta €40.", "Cuesta 40 euros.", MONEY):
            self.assertTrue(lc.is_money(answer("ÁMBAR", "- [ficha EU-GDPR-HOUSEHOLD]", extra=word)), word)

    def test_the_block_heading_is_found_in_three_languages(self):
        for heading in ("Passe d'économies :", "Savings pass:", "## Pase de ahorro", PASE):
            self.assertTrue(lc.SAVINGS_RE.search("x\n" + heading + "\n- y"), heading)
        self.assertFalse(lc.SAVINGS_RE.search("Qué haría: ahorro"))


class SavingsPassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.RS = rules(ROW_OK, ROW_S)

    def check(self, text):
        return codes(lc.check_answer(text, self.RS, TODAY))

    def test_a_money_draft_without_the_block_is_refused(self):
        got = self.check(answer("ÁMBAR", "- IVA [ficha EU-GDPR-HOUSEHOLD]", extra=MONEY))
        self.assertEqual(got, ["no-savings-pass"])

    def test_a_money_draft_with_the_block_passes(self):
        self.assertEqual(self.check(answer("ÁMBAR", "- IVA [ficha EU-GDPR-HOUSEHOLD]", extra=MONEY + "\n" + PASE)), [])

    def test_the_block_is_recognised_in_french_and_english(self):
        for heading in ("Passe d'économies :", "Savings pass:", "## Pase de ahorro"):
            text = answer("ÁMBAR", "- [ficha EU-GDPR-HOUSEHOLD]", extra=MONEY + "\n" + heading + "\n- x")
            self.assertEqual(self.check(text), [], heading)

    def test_a_non_money_draft_is_unaffected(self):
        text = answer("VERDE", "- GDPR art. 2(2)(c) [ficha EU-GDPR-HOUSEHOLD]")
        self.assertEqual(self.check(text), [])

    def test_the_english_disclaimer_alone_is_not_money(self):
        en = "General guidance, not legal or tax advice.\n\nVerdict: GREEN\n\n- [rule EU-GDPR-HOUSEHOLD]\n"
        self.assertEqual(self.check(en), [])

    def test_a_tax_word_or_an_amount_makes_it_money(self):
        for word in ("El IRPF sube.", "La TVA est due.", "VAT is due.", "Cuesta 40 EUR.", "Cuesta €40.", "Cuesta 40 euros."):
            got = self.check(answer("ÁMBAR", "- [ficha EU-GDPR-HOUSEHOLD]", extra=word))
            self.assertEqual(got, ["no-savings-pass"], word)

    def test_the_answer_command_refuses_a_money_draft_without_the_block(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "a.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(answer("ÁMBAR", "- [ficha EU-GDPR-HOUSEHOLD]", extra=MONEY))
        env = dict(os.environ, LEGAL_HOME=tempfile.mkdtemp())
        r = subprocess.run([sys.executable, "-I", SCRIPT, "answer", path, "--today", "2026-10-26"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("no-savings-pass", r.stdout)


class CorruptSnapshotTest(unittest.TestCase):
    def run_answer(self, snapshot_text):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, "snapshot.json"), "w", encoding="utf-8") as f:
            f.write(snapshot_text)
        path = os.path.join(tempfile.mkdtemp(), "a.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(answer("VERDE", "- [ficha EU-GDPR-HOUSEHOLD]"))
        env = dict(os.environ, LEGAL_HOME=home)
        return subprocess.run([sys.executable, "-I", SCRIPT, "answer", path, "--today", "2026-10-26"],
                              capture_output=True, text=True, env=env)

    def test_a_corrupt_snapshot_means_pending_unknown_so_not_fresh_and_no_crash(self):
        for bad in ("{not json", "", "[1, 2]", '{"sources": 3}'):
            r = self.run_answer(bad)
            self.assertNotIn("Traceback", r.stderr, bad)
            self.assertEqual(r.returncode, 1, bad + r.stdout + r.stderr)
            self.assertIn("green-unverified", r.stdout, bad)

    def test_a_good_snapshot_without_pending_still_passes(self):
        r = self.run_answer(json.dumps(snap()))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class WordingTest(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(SKILL, *parts), encoding="utf-8") as f:
            return f.read()

    def test_skill_template_has_the_savings_pass_in_three_languages(self):
        skill = self.read("SKILL.md")
        sec5 = skill.split("## 5. Answer template")[1].split("\n## 6.")[0]
        for name in ("Pase de ahorro", "Savings pass", "Passe d'économies"):
            self.assertIn(name, sec5)
        self.assertIn("accountant", sec5)

    def test_no_silent_default_rule_in_skill_and_checker(self):
        for text in (self.read("SKILL.md"), self.read("roles", "checker.md")):
            self.assertIn("never assumed as 0 %", text)
            self.assertIn("only if the user declines", text)

    def test_skill_no_longer_says_after_the_users_yes_for_accept(self):
        skill = self.read("SKILL.md")
        self.assertNotIn("changes only after the user's yes", skill)
        self.assertIn("legal checker's review", skill)

    def test_checker_role_refuses_a_money_answer_without_the_block(self):
        self.assertIn("Pase de ahorro", self.read("roles", "checker.md"))


if __name__ == "__main__":
    unittest.main()
