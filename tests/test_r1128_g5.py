"""R-1128 hotfix: the done gate accepts any runner's test id and a rule-only proof; a verify FAIL older than the fix is superseded."""
import os
import subprocess
import unittest

from helpers import StudioTestCase, ROOT
import studiolib as sl


class DoneGateAnyRunner(unittest.TestCase):
    def ok(self, ev):
        sl.check_done_proof(ev)

    def test_js_test_id_is_accepted(self):
        self.ok("abcdef1 dashboard.spec.js > renders")
        self.ok("abcdef1 web/app.test.ts renders the list")

    def test_pytest_and_unittest_ids_are_accepted(self):
        self.ok("abcdef1 tests/test_x.py::test_it")
        self.ok("abcdef1 test_mod.Case.test_it")

    def test_rule_only_item_closes_with_rule_line_and_check(self):
        self.ok("rule: plugin/rules/team.md:42 check: the guard test refuses the old wording")

    def test_rule_without_check_or_line_is_refused(self):
        with self.assertRaises(sl.StudioError):
            sl.check_done_proof("rule: plugin/rules/team.md:42")
        with self.assertRaises(sl.StudioError):
            sl.check_done_proof("rule: team.md check: looked fine")

    def test_sha_without_any_test_is_still_refused(self):
        with self.assertRaises(sl.StudioError):
            sl.check_done_proof("abcdef1 looked fine")


class SupersededVerify(StudioTestCase):
    def git(self, *args):
        r = subprocess.run(["git", "-C", self.project, "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def setUp(self):
        super().setUp()
        self.init_project()
        self.git("init", "-q")
        with open(os.path.join(self.project, "a.txt"), "w") as f:
            f.write("one")
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "one")
        self.old = self.git("rev-parse", "HEAD")
        with open(os.path.join(self.project, "a.txt"), "w") as f:
            f.write("two")
        self.git("commit", "-q", "-a", "-m", "fix")
        self.fix = self.git("rev-parse", "HEAD")

    def report(self, first, sha):
        d = sl.verify_dirs(self.project)[0]
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "R-1.md")
        with open(path, "w") as f:
            f.write("%s\nreviewer: v1\n%s\nround: 1\n" % (first, sha))
        return path

    def test_fail_older_than_fix_commit_is_superseded(self):
        self.report("FAIL", self.old)
        item = {"id": "R-1", "evidence": "%s test_x" % self.fix}
        self.assertEqual(sl.verify_state(self.project, item), "superseded")

    def test_fail_for_the_fix_commit_stays_fail(self):
        self.report("FAIL", self.fix)
        item = {"id": "R-1", "evidence": "%s test_x" % self.fix}
        self.assertEqual(sl.verify_state(self.project, item), "FAIL")

    def test_pass_and_missing_states(self):
        item = {"id": "R-1", "evidence": ""}
        self.assertEqual(sl.verify_state(self.project, item), "missing")
        self.report("PASS", self.fix)
        self.assertEqual(sl.verify_state(self.project, item), "PASS")

    def test_verifier_instructions_stamp_sha_and_round(self):
        with open(os.path.join(ROOT, "plugin", "agents", "verifier.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("line 3 is the commit sha", text)
        self.assertIn("round: <n>", text)
        self.assertIn("OVERWRITES", text)


class RefusalNamesTheMatch(unittest.TestCase):
    def test_refusal_names_the_path_and_the_rule(self):
        class Into:
            oor_keys = [("oor-read", "/scratch/elsewhere/data.csv")]
            doubts = []
        text = sl._oor_detail(Into())
        self.assertIn("/scratch/elsewhere/data.csv", text)
        self.assertIn("real data outside the project folder", text)

    def test_refusal_names_the_unplaceable_word(self):
        class Into:
            oor_keys = []
            doubts = ["a word that could not be read (foo)"]
        self.assertIn("(foo)", sl._oor_detail(Into()))


if __name__ == "__main__":
    unittest.main()
