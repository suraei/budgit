"""R-849: all-tools Agent types are denied in a bound project unless the prompt has an `ALL-TOOLS: <reason>` line."""
import unittest

from helpers import BudgitTestCase
import agentgate
import hook


def spawn(kind, prompt="do it"):
    return {"tool_name": "Agent", "session_id": "s1", "tool_input": {"model": "sonnet", "subagent_type": kind, "prompt": prompt}}


class AgentGateTest(BudgitTestCase):
    def run_hook(self, payload, project):
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use(payload, project)
        return hook._OUT.get("hookSpecificOutput", {})

    def test_a_studio_agent_is_allowed(self):
        out = self.run_hook(spawn("studio:builder"), self.tmp)
        self.assertNotEqual(out.get("permissionDecision"), "deny")

    def test_general_purpose_is_denied_and_names_the_studio_agents(self):
        out = self.run_hook(spawn("general-purpose"), self.tmp)
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("builder", out["permissionDecisionReason"])
        self.assertIn("scout-reader", out["permissionDecisionReason"])

    def test_every_all_tools_type_is_denied(self):
        for kind in ("Explore", "Plan", "claude-code-guide", "pr-review-toolkit:code-reviewer", "other-plugin:thing", None):
            self.assertTrue(agentgate.deny_reason({"subagent_type": kind, "prompt": "x"}), kind)

    def test_an_all_tools_line_with_a_reason_allows_it(self):
        out = self.run_hook(spawn("general-purpose", "task\nALL-TOOLS: needs the browser tools"), self.tmp)
        self.assertNotEqual(out.get("permissionDecision"), "deny")

    def test_an_empty_reason_does_not_allow_it(self):
        self.assertTrue(agentgate.deny_reason({"subagent_type": "Explore", "prompt": "ALL-TOOLS:   "}))

    def test_only_canonical_budgit_agents_pass(self):
        for kind in ("budgit:builder", "budgit-builder", "studio:builder", "budgit:scout-reader"):
            self.assertIsNone(agentgate.deny_reason({"subagent_type": kind, "prompt": "x"}), kind)
        for kind in ("studio:../x", "studio:anything", "Studio:builder", "budgit:", "budgit-evil", "x-budgit-builder", 5):
            self.assertTrue(agentgate.deny_reason({"subagent_type": kind, "prompt": "x"}), kind)

    def test_a_tool_input_that_is_not_a_dict_is_denied(self):
        for bad in (None, "x", ["a"], 7):
            self.assertTrue(agentgate.deny_reason(bad), repr(bad))

    def test_a_gate_that_crashes_says_enforcement_did_not_run(self):
        real = agentgate.deny_reason
        agentgate.deny_reason = lambda ti, project=None: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, agentgate, "deny_reason", real)
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use(spawn("general-purpose"), self.tmp)
        self.assertIn("enforcement did not run", hook._OUT.get("systemMessage", ""))

    def test_an_unbound_project_is_not_gated(self):
        out = self.run_hook(spawn("general-purpose"), None)
        self.assertNotEqual(out.get("permissionDecision"), "deny")


if __name__ == "__main__":
    unittest.main()
