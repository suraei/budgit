"""R-1360 task E: `task:` synonyms map to the canonical type before the type check (no warning), and the R-849 token guard keeps its
decision but names the studio agent for the brief's task type."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import agentcap  # noqa: E402
import agentgate  # noqa: E402
import skillrouter  # noqa: E402


def _types():
    return agentcap._skills_tree()["types"]


def _line(canon):
    return ", ".join(skillrouter.pick_of(_types()[canon]))


class SynonymTest(unittest.TestCase):
    def warn(self, task, canon):
        return agentcap.skills_warning({"subagent_type": "studio:builder",
                                        "prompt": "task: %s\nskills: %s\n" % (task, _line(canon))})

    def test_verification_is_verify(self):
        self.assertIsNone(self.warn("verification", "verify"))

    def test_tests_is_write_tests(self):
        self.assertIsNone(self.warn("tests", "write-tests"))

    def test_bugfix_is_fix_bug(self):
        self.assertIsNone(self.warn("bugfix", "fix-bug"))

    def test_review_is_plan_review_without_the_word_security(self):
        self.assertEqual(agentcap.task_synonym("review", "review"), "plan-review")
        self.assertIsNone(self.warn("review", "plan-review"))

    def test_review_is_security_review_with_the_word_security_in_the_line(self):
        self.assertEqual(agentcap.task_synonym("review", "review, security"), "security-review")
        self.assertIsNone(self.warn("review, security", "security-review"))

    def test_a_synonym_still_checks_the_skills_line(self):
        out = agentcap.skills_warning({"subagent_type": "studio:builder", "prompt": "task: verification\nskills: code-review\n"})
        self.assertIn("expected `skills: ", out)

    def test_an_unknown_word_still_gets_the_warning_with_the_type_list(self):
        out = agentcap.skills_warning({"subagent_type": "studio:builder", "prompt": "task: sparkles\n"})
        self.assertIn("names no known type", out)
        for t in _types():
            self.assertIn(t, out)
        self.assertEqual(agentcap.task_synonym("sparkles", "sparkles"), "sparkles")


class TokenGuardTest(unittest.TestCase):
    def reason(self, prompt, kind="general-purpose"):
        return agentgate.deny_reason({"subagent_type": kind, "prompt": prompt})

    def named(self, task, agent):
        out = self.reason("task: %s\n" % task)
        self.assertIsNotNone(out)
        self.assertIn(agent, out)
        self.assertIn("its reason must name the tool no Budgit agent has", out)
        return out

    def test_build_code_names_the_builder(self):
        self.named("build-code", "studio:builder")

    def test_fix_bug_names_the_builder(self):
        self.named("fix-bug", "studio:builder")

    def test_verify_names_the_verifier(self):
        self.named("verify", "studio:verifier")

    def test_research_names_the_researcher(self):
        self.named("research", "studio:researcher")

    def test_plan_review_names_the_plan_critic(self):
        self.named("plan-review", "studio:plan-critic")

    def test_security_review_names_the_security_reviewer(self):
        self.named("security-review", "studio:security-reviewer")

    def test_a_synonym_names_the_same_agent(self):
        self.named("verification", "studio:verifier")
        self.named("bugfix", "studio:builder")

    def test_one_type_does_not_name_the_other_agents(self):
        out = self.named("verify", "studio:verifier")
        self.assertNotIn("studio:builder", out)

    def test_no_task_line_lists_the_agents(self):
        out = self.reason("do it")
        for name in ("builder", "verifier", "researcher", "plan-critic", "security-reviewer"):
            self.assertIn(name, out)
        self.assertIn("its reason must name the tool no Budgit agent has", out)

    def test_the_decision_is_unchanged_general_purpose_is_denied_without_all_tools(self):
        self.assertIsNotNone(self.reason("task: build-code\n"))
        self.assertIsNotNone(self.reason("do it"))
        self.assertIsNotNone(self.reason("task: build-code\n", kind="Explore"))

    def test_the_decision_is_unchanged_general_purpose_passes_with_all_tools(self):
        self.assertIsNone(self.reason("task: build-code\nALL-TOOLS: needs the browser tool\n"))
        self.assertIsNone(self.reason("ALL-TOOLS: needs the browser tool\n"))

    def test_a_non_dict_payload_is_still_denied(self):
        self.assertIsNotNone(agentgate.deny_reason("nope"))


if __name__ == "__main__":
    unittest.main()
