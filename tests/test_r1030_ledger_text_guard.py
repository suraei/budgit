"""R-1030: the verifier-only writer guard judges the quoted --text / --evidence / --title values of `budgit ledger ...` as text,
`ledger verify` included, while any real write into the verify folder still denies."""
import os
import unittest

from test_guard_trust import GuardCase, CLI
import studiolib as sl


class LedgerTextGuardTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.proj = os.path.join(self.tmp, "r1030proj")
        self.vdir = os.path.join(self.proj, ".budgit", "verify")
        os.makedirs(self.vdir)

    def verdict(self, cmd):
        got = sl.verify_forgery_decision("Bash", {"command": cmd}, None, self.proj)
        return got[0] if got else None

    def test_ledger_verify_with_a_report_path_inside_the_evidence_text_passes(self):
        cmd = '%s ledger verify R-1014 --project %s --evidence "0e04a7bd test_r1014_own_cli_rows (15 OK), %s/R-1014.md PASS"' % (CLI, self.proj, self.vdir)
        self.assertIsNone(self.verdict(cmd))
        cmd = '%s ledger verify R-1014 --project %s --evidence "verify/R-1014.md PASS"' % (CLI, self.proj)
        self.assertIsNone(self.verdict(cmd))

    def test_ledger_add_text_and_title_naming_the_folder_still_pass(self):
        cmd = '%s ledger add --title "read verify/R-1.md" --text "see %s/R-1.md" --appetite small --area main' % (CLI, self.vdir)
        self.assertIsNone(self.verdict(cmd))

    def test_a_verify_call_with_a_path_word_outside_the_text_flags_is_denied(self):
        for cmd in ('%s ledger verify R-1 --project %s' % (CLI, self.vdir),
                    '%s ledger verify R-1 --evidence "x" %s/R-1.md' % (CLI, self.vdir),
                    '%s ledger verify R-1 --evidence="x" --project=%s' % (CLI, self.vdir)):
            self.assertEqual(self.verdict(cmd), "deny", cmd)

    def test_a_substitution_in_the_evidence_is_denied(self):
        self.assertEqual(self.verdict('%s ledger verify R-1 --evidence "$(touch %s/R-1.md)"' % (CLI, self.vdir)), "deny")

    def test_real_writes_into_the_verify_folder_still_deny(self):
        target = os.path.join(self.vdir, "R-1.md")
        for cmd in ('echo PASS > %s' % target, 'echo PASS | tee %s' % target, 'cp /tmp/x %s' % target,
                    "python3 -c \"open('%s','w').write('PASS')\"" % target, "sed -i s/FAIL/PASS/ %s" % target,
                    '%s ledger verify R-1 --evidence "ok" > %s' % (CLI, target)):
            self.assertEqual(self.verdict(cmd), "deny", cmd)


if __name__ == "__main__":
    unittest.main()
