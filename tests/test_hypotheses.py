import json, os, subprocess, sys, unittest

from helpers import StudioTestCase, ROOT

import hypotheses as hyp
import skills as sk
import skillselect as sel

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
REAL = os.path.join(ROOT, "skills", "budgit-kickoff", "reference", "skills-registry.json")
AGENT = os.path.join(ROOT, "plugin", "agents", "persona-sim.md")

REPORT = """HYPOTHESIS — simulated user
run 2 of 3 · persona from one model: all personas share its blind spots

I gave up at the second form because the evidence says the persona reads on a phone on the bus.

Hypothesis: People on a phone abandon at the sign-up form | confirm with: the sign-up funnel drop-off by device
- Hypothesis: A free tier decides whether they come back | confirm with: five interviews asking what they would pay for
Hypothesis: a claim with no way to test it
Friction: the brief was long
"""


class Fixture(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.report = os.path.join(self.tmp, "persona-run2.md")
        with open(self.report, "w", encoding="utf-8") as f:
            f.write(REPORT)

    def cli(self, *args, lang="es"):
        env = dict(os.environ, BUDGIT_LANG=lang)
        env.pop("BUDGIT_BUDGET", None)
        r = subprocess.run([sys.executable, CLI, "--project", self.project, "hypotheses"] + list(args),
                           capture_output=True, text=True, env=env, cwd=self.tmp)
        return r.returncode, r.stdout, r.stderr


class ParseAndLog(Fixture):
    def test_only_lines_with_a_claim_and_a_way_to_confirm_it_are_hypotheses(self):
        self.assertEqual(hyp.parse_report(REPORT), [
            ("People on a phone abandon at the sign-up form", "the sign-up funnel drop-off by device"),
            ("A free tier decides whether they come back", "five interviews asking what they would pay for")])

    def test_import_files_each_claim_open_with_its_source_and_a_second_import_adds_nothing(self):
        added = hyp.import_report(self.project, self.report, persona="commuter", run="2/3")
        self.assertEqual([r["id"] for r in added], ["H-1", "H-2"])
        self.assertTrue(all(r["status"] == "open" and r["scored"] is None for r in added))
        self.assertEqual(added[0]["source"], "persona-run2.md")
        self.assertEqual(added[0]["run"], "2/3")
        self.assertEqual(hyp.import_report(self.project, self.report), [])
        self.assertEqual(len(hyp.load(self.project)), 2)

    def test_a_file_that_is_not_a_persona_report_is_refused(self):
        other = os.path.join(self.tmp, "notes.md")
        with open(other, "w") as f:
            f.write("Hypothesis: x | confirm with: y\n")
        import studiolib as sl
        with self.assertRaises(sl.StudioError):
            hyp.import_report(self.project, other)
        self.assertEqual(hyp.load(self.project), [])

    def test_scoring_needs_real_evidence_except_for_unclear(self):
        import studiolib as sl
        row = hyp.add(self.project, "claim", "data")
        with self.assertRaises(sl.StudioError):
            hyp.score(self.project, row["id"], "confirmed")
        with self.assertRaises(sl.StudioError):
            hyp.score(self.project, row["id"], "maybe", "x")
        with self.assertRaises(sl.StudioError):
            hyp.score(self.project, "H-99", "unclear")
        done = hyp.score(self.project, "h-1", "refuted", "3 of 5 interviews said the opposite, 2026-10-26", today="2026-10-27")
        self.assertEqual((done["status"], done["scored"]), ("refuted", "2026-10-27"))
        self.assertEqual(hyp.score(self.project, "H-1", "unclear")["evidence"], None)

    def test_ids_never_repeat(self):
        hyp.add(self.project, "a", "b")
        hyp.add(self.project, "c", "d")
        self.assertEqual([r["id"] for r in hyp.load(self.project)], ["H-1", "H-2"])


class Cli(Fixture):
    def test_import_list_and_score_through_the_command_in_spanish_and_english(self):
        rc, out, err = self.cli("import", self.report, "--persona", "commuter")
        self.assertEqual(rc, 0, err)
        self.assertIn("2 hipótesis archivadas", out)
        rc, out, _ = self.cli("list")
        self.assertIn("2 abiertas", out)
        self.assertIn("H-1 [open] People on a phone abandon at the sign-up form -> the sign-up funnel drop-off by device", out)
        rc, out, err = self.cli("score", "H-1", "confirmed", "--evidence", "funnel data, 2026-10-30")
        self.assertEqual(rc, 0, err)
        rc, out, _ = self.cli("list", "--status", "confirmed", lang="en")
        self.assertIn("scored 2026-", out)
        self.assertNotIn("H-2", out)
        rc, out, err = self.cli("score", "H-2", "confirmed")
        self.assertEqual(rc, 1)
        self.assertIn("--evidence", out + err)

    def test_the_only_file_written_is_the_hypotheses_file(self):
        before = set(os.listdir(os.path.join(self.project, self.DATA)))
        self.cli("import", self.report)
        after = set(os.listdir(os.path.join(self.project, self.DATA)))
        self.assertEqual(after - before, {hyp.NAME})


class RunsByBudget(unittest.TestCase):
    def test_the_validate_row_runs_five_three_one_and_none_by_level_and_the_agent_says_so(self):
        reg = sk.load_registry(REAL)
        for level, n in (("ample", 5), ("normal", 3), ("tight", 1), ("critical", 0)):
            plan = sel.plan(reg, "validate/reaction", {"budget": level})
            self.assertEqual(plan["persona_runs"], n, level)
        self.assertNotIn("persona_runs", sel.plan(reg, "design/build", {"budget": "normal", "surface": "web"}))

    def test_the_why_output_always_tells_the_caller_to_report_the_spread(self):
        reg = sk.load_registry(REAL)
        result, record = sk.why(reg, "validate/reaction", {}, None, level="normal", log=False, env={"author": None})
        self.assertEqual(record["runs"], 3)
        text = sk.format_why(result, "en")
        self.assertIn("Run the simulated user 3 time(s)", text)
        self.assertIn("spread and the disagreements", text)
        none = sk.format_why(sk.why(reg, "validate/reaction", {}, None, level="critical", log=False, env={"author": None})[0], "en")
        self.assertIn("evidence was not simulated", none)


class AgentText(unittest.TestCase):
    def test_the_agent_forbids_invented_quotes_asks_for_run_labels_and_testable_claims(self):
        with open(AGENT, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Never invent a quote", text)
        self.assertIn("run k of N", text)
        self.assertIn("persona from one model", text)
        self.assertIn("Hypothesis: <claim", text)
        self.assertIn("budgit hypotheses import", text)
        self.assertIn("the spread", text)


if __name__ == "__main__":
    unittest.main()
