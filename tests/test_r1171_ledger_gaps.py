"""R-1171 (tracked on R-1173): `ledger due --clear` and `ledger evidence --decision / --rule`. Real CLI and real hook as subprocesses."""
import os

import studiolib as sl
import test_r1173a_ledger_no_yes as base


class DueClearTest(base.Base):
    def test_due_clear_drops_the_date_with_one_audit_line(self):
        self.assertEqual(self.cli("ledger", "due", "R-1", "2026-12-24").returncode, 0)
        r = self.cli("ledger", "due", "R-1", "--clear")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("due", self.item("R-1"))
        self.assertEqual(len(self.audit_lines("R-1 due 2026-12-24 -> cleared")), 1)

    def test_due_clear_with_a_date_or_with_nothing_is_refused(self):
        self.cli("ledger", "due", "R-1", "2026-12-24")
        for args in (("R-1", "2026-12-25", "--clear"), ("R-1",)):
            r = self.cli("ledger", "due", *args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("either a date", r.stdout + r.stderr)
        self.assertEqual(self.item("R-1")["due"], "2026-12-24")


class EvidenceNoShaTest(base.Base):
    def test_evidence_with_a_decision_adds_proof_and_one_audit_line(self):
        sl.append_inbox(self.project, "we decided the thing", "s1", "prompt")
        r = self.cli("ledger", "evidence", "R-1", "--decision", "I-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("decision I-1", self.item("R-1")["evidence"])
        self.assertEqual(self.item("R-1")["status"], "doing")
        self.assertEqual(len(self.audit_lines("R-1 evidence added")), 1)

    def test_evidence_with_a_rule_adds_proof(self):
        with open(os.path.join(self.project, "rules.md"), "w") as f:
            f.write("one\ntwo\n")
        r = self.cli("ledger", "evidence", "R-1", "--rule", "rules.md:2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rules.md:2", self.item("R-1")["evidence"])

    def test_evidence_refuses_a_missing_decision_a_bad_rule_and_the_wrong_count_of_proofs(self):
        with open(os.path.join(self.project, "rules.md"), "w") as f:
            f.write("one\n")
        for args, text in ((("--decision", "I-77"), "not an input in the inbox"),
                           (("--rule", "rules.md:99"), "--rule needs file:line"),
                           (("--rule", "../outside.md:1"), "--rule needs file:line"),
                           (("--decision", "I-1", "--rule", "rules.md:1"), "exactly one of"),
                           (("--sha", self.sha, "--rule", "rules.md:1"), "exactly one of"),
                           ((), "exactly one of")):
            r = self.cli("ledger", "evidence", "R-1", *args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn(text, r.stdout + r.stderr, args)
        self.assertEqual(self.item("R-1")["evidence"], "")

    def test_evidence_with_a_sha_still_works(self):
        self.assertEqual(self.cli("ledger", "evidence", "R-1", "--sha", self.sha).returncode, 0)


class HookTest(base.Base):
    def test_the_new_forms_pass_the_own_project_task_call_exception(self):
        def check(lane, protected):
            for words in (("ledger", "due", "R-1", "--clear"), ("ledger", "evidence", "R-1", "--decision", "I-1"),
                          ("ledger", "evidence", "R-1", "--rule", "rules.md:1")):
                got = self.bash(self.own(*words), lane)
                self.assertEqual(got[0], "allow", (words, lane, protected, got))
        self.each(check)

    def test_the_odd_flag_check_does_not_trip_on_the_new_flags(self):
        for words in (("ledger", "due", "R-1", "--clear"), ("ledger", "evidence", "R-1", "--decision", "I-1"),
                      ("ledger", "evidence", "R-1", "--rule", "rules.md:1")):
            self.assertFalse(sl._odd_flag(self.own(*words).split()))
