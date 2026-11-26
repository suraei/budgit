"""R-1183: an agent finds a rule or a ledger item in at most 2 CLI calls. Runs the real bin/budgit on a scratch project."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class FindabilityTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "the search guard refuses a read of the ledger", ["legacy"], title="Search guard asks too much")
        sl.add_item(data, "the daily stand-up digest is missing a line", ["legacy"], title="Stand-up digest")
        sl.add_item(data, "make the suite faster", ["legacy"], title="Speed of the suite", phase="v2")
        done = sl.add_item(data, "an old guard fix", ["legacy"], title="Guard fix")
        done["status"] = "done"
        sl.save_ledger(self.project, data)
        self.calls = 0

    def cli(self, *args, project=True):
        self.calls += 1
        env = dict(os.environ, STUDIO_HOME=self.home, BUDGIT_HOME=self.home, HOME=self.tmp)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        extra = ["--project", self.project] if project else []
        r = subprocess.run([sys.executable, CLI, *args, *extra], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_find_the_rule_on_pushing_in_one_call(self):
        out = self.cli("rules", "show", "--grep", "push", project=False)
        self.assertIn("Never push, publish or post without the user's yes", out)
        self.assertIn("permissions:", out)
        self.assertNotIn("## ", out)
        self.assertLessEqual(self.calls, 2)

    def test_grep_in_one_topic_prints_bodies_only(self):
        out = self.cli("rules", "show", "permissions", "--grep", "PUSH", project=False)
        self.assertTrue(out.startswith("- Never push"))
        self.assertNotIn("permissions:", out)

    def test_find_open_items_about_the_search_guard(self):
        out = self.cli("ledger", "list", "--open", "--text", "guard")
        self.assertIn("search guard", out)
        self.assertNotIn("old guard fix", out)
        self.assertLessEqual(self.calls, 2)

    def test_find_the_item_for_the_stand_up(self):
        rows = json.loads(self.cli("ledger", "list", "--text", "stand-up", "--json"))["items"]
        self.assertEqual([r["title"] for r in rows], ["Stand-up digest"])
        self.assertLessEqual(self.calls, 2)

    def test_no_match_exits_zero_with_one_line(self):
        self.assertEqual(len(self.cli("rules", "show", "--grep", "zzqqxx", project=False).strip().splitlines()), 1)
        self.assertEqual(len(self.cli("ledger", "list", "--text", "zzqqxx").strip().splitlines()), 1)

    def test_by_area_counts_equal_ledger_counts_totals(self):
        out = json.loads(self.cli("ledger", "list", "--by-area", "--json"))
        total = sl.ledger_counts(sl.load_ledger(self.project))
        for key in ("doing", "next", "later", "done"):
            self.assertEqual(sum(c[key] for c in out["by_area"].values()), total[key], key)
            self.assertEqual(out["totals"][key], total[key], key)
        self.assertIn("security", out["by_area"])

    def test_area_and_phase_filters_combine(self):
        self.assertIn("faster", self.cli("ledger", "list", "--area", "performance", "--phase", "v2"))
        self.assertNotIn("faster", self.cli("ledger", "list", "--area", "security"))

    def test_json_keeps_its_old_shape_for_old_callers(self):
        rows = json.loads(self.cli("ledger", "list", "--json", "--open"))
        self.assertIsInstance(rows, list)
        self.assertEqual({"id", "status", "phase", "title", "doing_by", "doing_since", "blocked", "started_at", "appetite"},
                         set(rows[0]))

    def test_hook_pointer_is_present_and_short(self):
        import hook
        line = hook.FIND_POINTER
        self.assertIn("budgit rules show --grep", line)
        self.assertIn("budgit ledger list --open --text", line)
        self.assertLessEqual(len(line.split()), 30)
        with open(os.path.join(ROOT, "plugin", "scripts", "hook.py"), encoding="utf-8") as f:
            self.assertIn('_context("SessionStart", FIND_POINTER)', f.read())


if __name__ == "__main__":
    import unittest
    unittest.main()
