"""R-1211: the skills decision tree (data file), the coverage read, the brief advisory, the agent files, the guard row."""
import json
import os
import tempfile
import unittest

from test_guard_trust import GuardCase, CLI
import agentcap
import skillrouter
import skills
import studiolib as sl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREE = os.path.join(ROOT, "plugin", "scripts", "reference", "skills-tree.json")
AGENTS = os.path.join(ROOT, "plugin", "agents")


def _tree():
    with open(TREE, encoding="utf-8") as f:
        return json.load(f)


class TreeDataTest(unittest.TestCase):
    def test_the_tree_json_loads_and_has_the_ten_types(self):
        t = _tree()
        self.assertTrue({"build-code", "fix-bug", "write-tests", "verify", "security-review", "research",
                         "plan-review", "frontend-ui", "docs", "release"} <= set(t["types"]))   # R-1358: version 2 adds four types

    def test_every_type_has_at_least_one_skill(self):
        for name, spec in _tree()["types"].items():
            self.assertTrue(skillrouter.type_names(spec), name)

    def test_no_name_is_both_mapped_and_unused(self):
        t = _tree()
        mapped = {n for v in t["types"].values() for n in skillrouter.type_names(v)}
        self.assertFalse(mapped & set(t["unused"]), mapped & set(t["unused"]))

    def test_every_unused_entry_has_a_reason(self):
        for k, v in _tree()["unused"].items():
            self.assertTrue(str(v).strip(), k)


class CoverageTest(unittest.TestCase):
    def test_coverage_drops_mapped_prefix_and_unused_and_returns_unknown(self):
        tree = {"types": {"build-code": ["fakemapped", "fakegsap-*"]}, "unused": {"fakeunused": "no use"}}
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            for n in ("fakemapped", "fakegsap-x", "fakeunused", "fakeunknown", "fakenomd"):
                os.makedirs(os.path.join(d, n))
                if n != "fakenomd":
                    with open(os.path.join(d, n, "SKILL.md"), "w") as f:
                        f.write("x")
            self.assertEqual(skills.skills_coverage([d], tree), ["fakeunknown"])

    def test_coverage_ignores_a_missing_dir(self):
        self.assertEqual(skills.skills_coverage(["/tmp/r1211-no-such-dir"], {"types": {}, "unused": {}}), [])


class OwnFolderTest(unittest.TestCase):
    def test_coverage_of_the_repos_own_plugin_skills_folder_is_empty(self):
        self.assertEqual(skills.skills_coverage([os.path.join(ROOT, "plugin", "skills")], _tree()), [])

    def test_no_type_holds_a_wildcard_name_and_the_expected_line_holds_none(self):
        t = _tree()
        for name, spec in t["types"].items():
            for n in skillrouter.pick_of(spec):          # R-1358: only `pick` is asked for; `also` may hold a prefix
                self.assertFalse(n.endswith("*"), (name, n))
        for name in t["types"]:
            out = agentcap.skills_warning({"subagent_type": "studio:builder", "prompt": "task: %s\n" % name})
            self.assertIn("expected `skills: ", out)
            for n in out.split("expected `skills: ")[1].rstrip(".`").split(", "):
                self.assertFalse(n.endswith("*"), (name, n))

    def test_plan_critic_cannot_write(self):
        with open(os.path.join(AGENTS, "plan-critic.md"), encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.startswith("disallowedTools:")]
        self.assertTrue(lines)
        names = [x.strip() for x in lines[0].split(":", 1)[1].split(",")]
        self.assertIn("Write", names)
        self.assertIn("Edit", names)


class WarningTest(unittest.TestCase):
    def call(self, prompt, sub="studio:builder"):
        return agentcap.skills_warning({"subagent_type": sub, "prompt": prompt})

    def test_no_advisory_for_a_non_studio_subagent_type(self):
        self.assertIsNone(self.call("do it", "general-purpose"))
        self.assertIsNone(self.call("do it", "Explore"))

    def test_missing_task_line_names_the_types(self):
        out = self.call("build a thing")
        self.assertIn("no `task:`", out)
        for t in _tree()["types"]:
            self.assertIn(t, out)

    def test_short_skills_line_gets_the_expected_line(self):
        out = self.call("task: verify\nskills: code-review\n")
        self.assertIn("expected `skills: ", out)
        self.assertIn("superpowers:verification-before-completion", out)

    def test_task_without_skills_line_gets_the_expected_line(self):
        self.assertIn("expected `skills: ", self.call("task: fix-bug"))

    def test_complete_skills_line_gives_nothing(self):
        line = ", ".join(skillrouter.pick_of(_tree()["types"]["verify"]))
        self.assertIsNone(self.call("task: verify\nskills: %s\n" % line))

    def test_reads_task_from_an_absolute_md_brief_under_tmp_and_never_echoes_other_text(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            p = os.path.join(d, "brief.md")
            with open(p, "w") as f:
                f.write("# secret heading\nSECRET-LINE-XYZ do not echo\ntask: fix-bug\nskills: engineering:debug\n")
            out = self.call("Read your brief and follow it: " + p)
        self.assertIn("superpowers:systematic-debugging", out)
        self.assertNotIn("SECRET-LINE-XYZ", out)
        self.assertNotIn("secret heading", out)


class AgentFilesTest(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(AGENTS, name + ".md"), encoding="utf-8") as f:
            return f.read()

    def test_five_agents_list_skill_and_have_the_block(self):
        for n in ("builder", "verifier", "security-reviewer", "researcher", "plan-critic"):
            text = self.read(n)
            tools = [l for l in text.splitlines() if l.startswith("tools:")][0]
            self.assertIn("Skill", [x.strip() for x in tools[6:].split(",")], n)
            self.assertIn("\nSkills:", text, n)

    def test_persona_sim_and_scout_reader_do_not(self):
        for n in ("persona-sim", "scout-reader"):
            text = self.read(n)
            tools = [l for l in text.splitlines() if l.startswith("tools:")]
            for l in tools:
                self.assertNotIn("Skill", [x.strip() for x in l[6:].split(",")], n)
            self.assertNotIn("\nSkills:", text, n)


class GuardRowTest(GuardCase):
    def verdict(self, cmd, role="subagent"):
        agent = sl.VERIFIER if role == "verifier" else None
        got = sl.home_write_check("Bash", {"command": cmd}, self.root, agent, subagent=role != "pm")[0]
        return got[0] if got else None

    def test_skills_coverage_and_its_help_pass_for_subagent_and_verifier(self):
        for cmd in ("skills coverage", "skills coverage --help"):
            for role in ("subagent", "verifier"):
                self.assertIsNone(self.verdict("%s %s" % (CLI, cmd), role), (cmd, role))

    def test_a_writing_skills_action_stays_refused(self):
        for cmd in ("skills build", "skills load-plan"):
            self.assertEqual(self.verdict("%s %s" % (CLI, cmd)), "deny", cmd)


if __name__ == "__main__":
    unittest.main()
