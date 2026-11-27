"""R-1378 label gap (owner's trust issue): the plain weekly line says where its figure comes from. With a stored account reading the
line carries the account-reading label; with none it says it is an estimate. The old-read and mismatch notes are unchanged.
Fixtures come from the account-floor module; temp HOME only (StudioTestCase)."""
import unittest
from helpers import StudioTestCase
from test_r1378_account_floor import AccountHelpers, HOUR


class WeeklyLabelTest(AccountHelpers, StudioTestCase):
    def weekly_bit(self, lang, at):
        reason = self.b.current(now=at, lang=lang)["reason"]
        return [b for b in reason.split("; ") if b.startswith("weekly ")][0]

    def test_account_reading_label_in_en_and_es(self):
        self.acct(31)
        at = self.t + HOUR
        self.assertTrue(self.weekly_bit("en", at).endswith("(app reading)"), self.weekly_bit("en", at))
        self.assertTrue(self.weekly_bit("es", at).endswith("(lectura de la app)"), self.weekly_bit("es", at))

    def test_no_account_reading_says_estimate_in_en_and_es(self):
        self.host(6, self.t)
        at = self.t + HOUR
        self.assertTrue(self.weekly_bit("en", at).endswith("(estimate, no app reading)"), self.weekly_bit("en", at))
        self.assertTrue(self.weekly_bit("es", at).endswith("(estimación, sin lectura de la app)"), self.weekly_bit("es", at))

    def test_old_read_and_mismatch_notes_unchanged(self):
        self.acct(31)
        self.assertEqual(self.b.account_notes("es", now=self.t + 30 * HOUR), ["app leída hace 30 h"])
        self.assertEqual(self.b.account_notes("en", now=self.t + 30 * HOUR), ["app read 30 h ago"])


if __name__ == "__main__":
    unittest.main()
