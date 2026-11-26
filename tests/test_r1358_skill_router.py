"""R-1358 lane A: skills tree v2 (one data file), skills_warning asks only the `pick` names, a UserPromptSubmit router line
(advice only, once per type per session), a use log on PostToolUse Skill, an eval case file."""
import json
import os
import re
import unittest

from helpers import BudgitTestCase, ROOT
import agentcap
import hook
import leak
import skillrouter
import studiolib as sl
from test_leak import PRIVATE
from test_setup import HER

TREE = os.path.join(ROOT, "plugin", "scripts", "reference", "skills-tree.json")
HOOKS = os.path.join(ROOT, "plugin", "hooks", "hooks.json")
EVALS = os.path.join(ROOT, "plugin", "evals")
OLD_TEN = {"build-code", "fix-bug", "write-tests", "verify", "security-review", "research", "plan-review", "frontend-ui",
           "docs", "release"}
NEW_FOUR = {"finance-accounting", "market-data", "3d", "office-docs"}


def _tree():
    with open(TREE, encoding="utf-8") as f:
        return json.load(f)


class TreeV2Test(unittest.TestCase):
    def test_tree_v2_schema(self):
        t = _tree()
        self.assertEqual(t["version"], 2)
        self.assertEqual(set(t["types"]), OLD_TEN | NEW_FOUR)
        for name, spec in t["types"].items():
            self.assertIsInstance(spec, dict, name)
            for row in spec["also"]:
                self.assertTrue(row["skill"] and row["when"], (name, row))
            self.assertTrue(spec["signals"]["words"], name)
            self.assertIsInstance(spec["signals"]["files"], list, name)
            self.assertIsInstance(spec["outside"], list, name)

    def test_pick_has_one_to_three_names_for_every_type(self):
        for name, spec in _tree()["types"].items():
            self.assertTrue(1 <= len(spec["pick"]) <= 3, (name, spec["pick"]))
            for n in spec["pick"]:
                self.assertFalse(n.endswith("*"), (name, n))

    def test_defaults_and_unused_and_profile(self):
        t = _tree()
        self.assertEqual(t["types"]["frontend-ui"]["pick"], ["impeccable", "frontend-design:frontend-design"])
        for gone in ("finance:*", "bigdata:*", "daloopa:*", "fiscal-es-fr"):
            self.assertNotIn(gone, t["unused"])
        self.assertEqual(t["profiles"], {})

    def test_outside_section_keeps_protected_to_the_clean_copy_path(self):
        out = _tree()["outside"]
        self.assertEqual(out["protected"]["clean_copy"], "required")
        tools = {row["tool"] for spec in _tree()["types"].values() for row in spec["outside"]}
        self.assertTrue({"codex", "gemini"} <= tools, tools)
        self.assertIn("openrouter", out["tools"])

    def test_shipped_files_pass_the_leak_scanner_and_ship_no_profiles(self):
        self.assertEqual(_tree()["profiles"], {})
        cfg = dict(HER, private_terms=PRIVATE)
        found = leak.terms(cfg)
        for path in (TREE, os.path.join(ROOT, "plugin", "scripts", "skillrouter.py"), EVALS):
            hits = leak.scan(path, found)
            self.assertEqual(hits, [], leak.report(hits))


class OverlayProfileTest(BudgitTestCase):
    def write(self, text):
        with open(os.path.join(sl.home_state(), skillrouter.OVERLAY_NAME), "w", encoding="utf-8") as f:
            f.write(text)

    def test_overlay_profiles_are_merged_into_the_tree(self):
        os.makedirs(sl.home_state(), exist_ok=True)
        self.write(json.dumps({"profiles": {"Demo-Project": ["finance-accounting", "market-data"]}}))
        self.assertEqual(skillrouter.load_tree()["profiles"], {"demo-project": ["finance-accounting", "market-data"]})

    def test_a_profile_wins_a_tie_only_when_the_prompt_matched(self):
        os.makedirs(sl.home_state(), exist_ok=True)
        self.write(json.dumps({"profiles": {"demo-project": ["market-data"]}}))
        tree = skillrouter.load_tree()
        self.assertEqual(skillrouter.suggest_type("compara el earnings y el dashboard", tree, "/x/demo-project"), "market-data")
        self.assertIsNone(skillrouter.suggest_type("rename this variable", tree, "/x/demo-project"))

    def test_missing_or_invalid_overlay_means_no_profiles(self):
        os.makedirs(sl.home_state(), exist_ok=True)
        self.assertEqual(skillrouter.load_tree()["profiles"], {})
        for bad in ("{not json", "[]", '{"profiles": 3}', '{"profiles": {"a": "x"}}'):
            self.write(bad)
            self.assertEqual(skillrouter.load_tree()["profiles"], {}, bad)


class SkillsWarningPickTest(unittest.TestCase):
    def call(self, prompt):
        return agentcap.skills_warning({"subagent_type": "studio:builder", "prompt": prompt})

    def test_skills_warning_asks_only_the_pick_names(self):
        out = self.call("task: frontend-ui\n")
        self.assertIn("expected `skills: impeccable, frontend-design:frontend-design`", out)
        for extra in ("taste-v2", "gsap-core", "threejs", "design-taste-frontend"):
            self.assertNotIn(extra, out)

    def test_a_brief_with_the_pick_names_gets_nothing(self):
        self.assertIsNone(self.call("task: frontend-ui\nskills: impeccable, frontend-design:frontend-design\n"))

    def test_missing_task_suggests_a_type_from_the_brief_words(self):
        out = self.call("make the settings screen look better, the dashboard layout is ugly")
        self.assertIn("no `task:`", out)
        self.assertIn("task: frontend-ui", out)
        self.assertIn("impeccable", out)

    def test_missing_task_without_signal_has_no_suggestion(self):
        out = self.call("rename this variable")
        self.assertIn("no `task:`", out)
        self.assertNotIn("looks like", out)


class RouterTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        self.n = 0

    def line(self, prompt, session="s1", project=None):
        hook._OUT.clear()
        hook.skill_router_context({"session_id": session, "prompt": prompt}, project)
        return hook._OUT.get("hookSpecificOutput", {}).get("additionalContext")

    def test_router_line_for_a_spanish_ui_prompt(self):
        text = self.line("haz que la pantalla de ajustes se vea mejor")
        self.assertIn("frontend-ui", text)
        self.assertIn("impeccable", text)
        self.assertIn("frontend-design:frontend-design", text)
        self.assertIn("Skill tool", text)
        self.assertNotIn("taste-v2", text)

    def test_router_line_for_a_finance_prompt(self):
        text = self.line("calcula las plusvalías FIFO de este CSV")
        self.assertIn("finance-accounting", text)
        self.assertIn("fiscal-es-fr", text)

    def test_no_router_line_for_a_plain_code_prompt(self):
        self.assertIsNone(self.line("rename this variable"))
        self.assertIsNone(self.line(""))
        self.assertIsNone(self.line(None))

    def test_router_line_once_per_type_per_session(self):
        prompt = "haz que la pantalla de ajustes se vea mejor"
        self.assertIsNotNone(self.line(prompt, "sA"))
        self.assertIsNone(self.line(prompt, "sA"))
        self.assertIsNotNone(self.line(prompt, "sB"))
        self.assertIsNotNone(self.line("calcula las plusvalías FIFO de este CSV", "sA"))

    def test_a_file_name_in_the_prompt_is_a_signal(self):
        self.assertIn("frontend-ui", self.line("revisa settings.css y arregla el hueco", "sF"))


class UseLogTest(BudgitTestCase):
    def test_use_log_line_on_a_skill_post_tool_use(self):
        project = os.path.join(self.home, "proj-r1358")
        os.makedirs(project)
        sl.ensure_studio_dir(project)
        hook.record_skill_use({"session_id": "sU", "tool_name": "Skill",
                               "tool_input": {"skill": "impeccable", "args": "SECRET CONTENT"},
                               "tool_response": "BODY TEXT"}, project)
        path = os.path.join(sl.studio_dir(project), "skill-use.jsonl")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        rows = [json.loads(x) for x in raw.splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"ts", "session", "type", "skill"})
        self.assertEqual((rows[0]["session"], rows[0]["type"], rows[0]["skill"]), ("sU", "frontend-ui", "impeccable"))
        self.assertNotIn("SECRET", raw)
        self.assertNotIn("BODY", raw)

    def test_other_tools_write_no_use_log_line(self):
        project = os.path.join(self.home, "proj-r1358b")
        os.makedirs(project)
        sl.ensure_studio_dir(project)
        hook.record_skill_use({"session_id": "sU", "tool_name": "Bash", "tool_input": {"command": "ls"}}, project)
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(project), "skill-use.jsonl")))

    def test_hooks_json_matches_skill_on_post_tool_use(self):
        with open(HOOKS, encoding="utf-8") as f:
            matcher = json.load(f)["hooks"]["PostToolUse"][0]["matcher"]
        self.assertIn("Skill", matcher.split("|"))


class EvalFileTest(unittest.TestCase):
    def test_eval_cases_cover_the_four_prompts(self):
        """The official layout: evals/<case>/prompt.md (frontmatter + prompt body) and evals/<case>/graders/<name>.md."""
        def front(path):
            with open(path, encoding="utf-8") as f:
                m = re.match(r"---\n(.*?)\n---\n(.*)", f.read(), re.S)
            self.assertTrue(m, path)
            fields = {}
            for line in m.group(1).splitlines():
                k, _, v = line.partition(":")
                fields[k.strip()] = v.strip().strip("'")
            return fields, m.group(2).strip()

        def grader(case):
            folder = os.path.join(EVALS, case, "graders")
            names = os.listdir(folder)
            self.assertEqual(len(names), 1, case)
            fields, _ = front(os.path.join(folder, names[0]))
            self.assertEqual((fields["type"], fields["tool"]), ("tool_used", "Skill"))
            return fields

        self.assertEqual(sorted(os.listdir(EVALS)), ["market", "negative-rename", "plusvalias-fifo", "ui-settings"])
        for case in os.listdir(EVALS):
            fields, body = front(os.path.join(EVALS, case, "prompt.md"))
            self.assertTrue(body and fields["max_turns"] and "Skill" in fields["allowed_tools"], case)
        self.assertEqual(grader("ui-settings")["input_match"], "impeccable|frontend-design|taste-v2")
        self.assertEqual(grader("ui-settings")["min"], "1")
        self.assertIn("fiscal-es-fr", grader("plusvalias-fifo")["input_match"])
        self.assertIn("bigdata-com:", grader("market")["input_match"])
        neg = grader("negative-rename")
        self.assertEqual((neg["min"], neg["max"]), ("0", "0"))
        self.assertIn("impeccable", neg["input_match"])


if __name__ == "__main__":
    unittest.main()
