import json, os, subprocess, sys, unittest

from helpers import StudioTestCase, ROOT

import secplan
import skills as sk

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
REAL = os.path.join(ROOT, "skills", "budgit-kickoff", "reference", "skills-registry.json")
RISKY = ["src/auth/login.py"]
PLAIN = ["docs/notes.md"]


def reg():
    return sk.load_registry(REAL)


def ids(result):
    return [r["id"] for r in result["reviewers"]]


class PlanTable(unittest.TestCase):
    def test_critical_budget_with_a_relevant_change_lists_no_claude_agent(self):
        for trigger in ("T1", "T2", "T3", "T4"):
            r = secplan.plan(reg(), trigger, RISKY, "critical", codex_on=True)
            self.assertTrue(r["relevant"])
            self.assertFalse(set(ids(r)) & {"R2", "R3"}, (trigger, ids(r)))

    def test_a_blocking_release_at_critical_takes_the_queued_agents(self):
        r = secplan.plan(reg(), "T4", RISKY, "critical", codex_on=True, blocking=True)
        self.assertIn("R3", ids(r))
        self.assertEqual(r["queued"], [])
        r = secplan.plan(reg(), "T4", RISKY, "critical", codex_on=True)
        self.assertEqual(r["queued"], ["R2", "R3"])

    def test_a_protected_path_drops_codex_and_says_why(self):
        r = secplan.plan(reg(), "T3", RISKY, "normal", protected=["secrets/keys.txt"], codex_on=True)
        self.assertNotIn("R1", ids(r))
        self.assertEqual(r["dropped"], [{"reviewer": "R1", "reason": "protected_path"}])
        self.assertTrue(r["relevant"])

    def test_codex_lane_off_drops_codex_even_without_a_protected_path(self):
        r = secplan.plan(reg(), "T4", PLAIN, "tight", codex_on=False)
        self.assertNotIn("R1", ids(r))
        self.assertEqual(r["dropped"], [{"reviewer": "R1", "reason": "codex_off"}])
        self.assertIn("R2", ids(r))

    def test_codex_stays_when_the_lane_is_on_and_nothing_is_protected(self):
        r = secplan.plan(reg(), "T3", RISKY, "normal", codex_on=True)
        self.assertEqual(ids(r), ["R1"])
        self.assertEqual(r["dropped"], [])

    def test_table_cells_per_level_for_a_commit_and_for_a_release(self):
        want = {("T3", "ample", True): ["R3", "R1"], ("T3", "normal", True): ["R1"], ("T3", "critical", True): ["R0"],
                ("T3", "normal", False): ["R0"], ("T4", "ample", False): ["R3", "R1", "R0"],
                ("T4", "normal", False): ["R3", "R0"], ("T4", "tight", False): ["R2", "R1", "R0"],
                ("T4", "critical", False): ["R1", "R0"]}
        for (trigger, level, risky), expected in want.items():
            r = secplan.plan(reg(), trigger, RISKY if risky else PLAIN, level, codex_on=True)
            self.assertEqual(ids(r), expected, (trigger, level, risky))

    def test_a_small_release_diff_at_normal_takes_the_smaller_reviewer(self):
        r = secplan.plan(reg(), "T4", PLAIN, "normal", codex_on=True, small=True)
        self.assertEqual(ids(r), ["R2", "R0"])
        r = secplan.plan(reg(), "T4", RISKY, "normal", codex_on=True, small=True)
        self.assertEqual(ids(r), ["R3", "R0"])     # a risky diff is never "small"

    def test_edits_get_only_the_local_scripts_and_turn_ends_get_nothing(self):
        self.assertEqual(ids(secplan.plan(reg(), "T1", RISKY, "ample", codex_on=True)), ["R0"])
        self.assertEqual(ids(secplan.plan(reg(), "T2", RISKY, "ample", codex_on=True)), [])

    def test_an_unknown_trigger_is_an_error(self):
        import studiolib as sl
        with self.assertRaises(sl.StudioError):
            secplan.plan(reg(), "T9", PLAIN, "normal")


class PathClasses(unittest.TestCase):
    def test_path_substrings_match_case_insensitively(self):
        c = secplan.classify(reg(), ["Lib/OAuth_Helper.py", "package.json", "README.md"])
        self.assertIn("auth", c)
        self.assertIn("dependencies", c)
        self.assertEqual(c["dependencies"], ["package.json"])
        self.assertNotIn("README.md", sum(c.values(), []))

    def test_diff_text_matches_content_patterns(self):
        c = secplan.classify(reg(), PLAIN, "+    result = pickle.loads(blob)\n")
        self.assertIn("eval", c)
        self.assertEqual(c["eval"], ["(diff)"])

    def test_a_plain_change_is_routine(self):
        r = secplan.plan(reg(), "T3", PLAIN, "normal", codex_on=True)
        self.assertFalse(r["relevant"])
        self.assertEqual(r["classes"], {})


class Cli(StudioTestCase):
    def run_cli(self, *args, lang="es"):
        env = dict(os.environ, BUDGIT_LANG=lang)
        env.pop("BUDGIT_BUDGET", None)
        r = subprocess.run([sys.executable, CLI, "--project", self.project, "security"] + list(args),
                           capture_output=True, text=True, env=env, cwd=self.tmp)
        return r.returncode, r.stdout, r.stderr

    def tree(self):
        out = {}
        for base, _, files in os.walk(self.project):
            for name in files:
                out[os.path.join(base, name)] = os.path.getsize(os.path.join(base, name))
        return out

    def test_the_plan_is_logged_with_its_reviewers_and_its_trigger(self):
        self.init_project()
        rc, out, err = self.run_cli("plan", "--trigger", "T4", "--paths", "src/auth/login.py", "--level", "normal", "--registry", REAL)
        self.assertEqual(rc, 0, err)
        log = os.path.join(self.project, self.DATA, sk.LOG_NAME)
        with open(log, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f.read().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trigger"], "T4")
        self.assertEqual(rows[0]["reviewer"], ["R3", "R0"])
        self.assertEqual(rows[0]["budget"], "normal")
        self.assertIn("auth", rows[0]["classes"])
        self.assertIn(log, out)

    def test_it_runs_nothing_and_writes_only_the_log(self):
        self.init_project()
        before = self.tree()
        rc, _, err = self.run_cli("plan", "--trigger", "T3", "--paths", "a/token.py", "--level", "ample", "--registry", REAL)
        self.assertEqual(rc, 0, err)
        after = self.tree()
        new = set(after) - set(before)
        self.assertEqual({os.path.basename(p) for p in new}, {sk.LOG_NAME})
        for p in before:
            self.assertEqual(before[p], after[p])

    def test_no_log_writes_nothing_and_a_project_without_a_data_folder_gets_no_log(self):
        rc, _, err = self.run_cli("plan", "--trigger", "T3", "--paths", "a/token.py", "--level", "ample", "--registry", REAL)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.tree(), {})
        self.init_project()
        before = self.tree()
        rc, _, _ = self.run_cli("plan", "--trigger", "T3", "--no-log", "--level", "ample", "--registry", REAL)
        self.assertEqual(rc, 0)
        self.assertEqual(self.tree(), before)

    def test_a_protected_path_in_the_project_config_drops_codex(self):
        self.init_project()
        with open(os.path.join(self.project, self.DATA, "project.json"), "w") as f:
            json.dump({"protected_paths": ["secrets/*"]}, f)
        rc, out, err = self.run_cli("plan", "--trigger", "T3", "--paths", "secrets/keys.txt", "--level", "normal", "--json", "--registry", REAL)
        self.assertEqual(rc, 0, err)
        rec = json.loads(out)
        self.assertNotIn("R1", rec["reviewer"])
        self.assertTrue(rec["protected"])
        self.assertEqual(rec["dropped"][0]["reviewer"], "R1")

    def test_spanish_and_english_output_and_an_unknown_trigger(self):
        rc, out, _ = self.run_cli("plan", "--trigger", "T4", "--paths", "x.md", "--level", "critical", "--no-log", "--registry", REAL)
        self.assertIn("Plan de seguridad para T4", out)
        self.assertIn("en cola para más tarde: R2, R3", out)
        rc, out, _ = self.run_cli("plan", "--trigger", "T4", "--paths", "x.md", "--level", "critical", "--no-log", "--registry", REAL, lang="en")
        self.assertIn("Security plan for T4", out)
        self.assertIn("queued for later: R2, R3", out)
        rc, _, err = self.run_cli("plan", "--trigger", "T9", "--no-log", "--registry", REAL)
        self.assertEqual(rc, 1)
        self.assertIn("T9", err)


if __name__ == "__main__":
    unittest.main()
