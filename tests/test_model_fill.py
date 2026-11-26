"""Her I-19: "lo más eficiente... ajustando según consumo". An Agent call without model= gets the model of its role
at the current budget level, filled by the PreToolUse hook (no prompt, no retry). An explicit model is never touched."""
import json, os, subprocess, sys

from helpers import StudioTestCase, ROOT
import budget

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


class ModelFillTest(StudioTestCase):
    def run_hook(self, tool_input, tool="Agent", tid="toolu_a1"):
        payload = {"session_id": "s1", "tool_name": tool, "tool_use_id": tid, "tool_input": tool_input,
                   "cwd": self.tmp}
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), capture_output=True,
                           text=True, env=dict(os.environ, STUDIO_HOME=self.home), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_roles(self):
        self.assertEqual(budget.role_for("budgit:verifier"), "judgement")
        self.assertEqual(budget.role_for("budgit:plan-critic"), "judgement")
        self.assertEqual(budget.role_for("pr-review-toolkit:code-reviewer"), "judgement")
        self.assertEqual(budget.role_for("Plan"), "judgement")
        self.assertEqual(budget.role_for("Explore"), "mechanical")
        self.assertEqual(budget.role_for("claude-code-guide"), "mechanical")
        self.assertEqual(budget.role_for("budgit:researcher"), "work")
        self.assertEqual(budget.role_for(None), "work")   # no type = general-purpose

    def test_model_follows_level(self):
        self.assertEqual(budget.model_for("budgit:verifier", "normal"), "opus")
        self.assertEqual(budget.model_for("budgit:verifier", "tight"), "sonnet")
        self.assertEqual(budget.model_for("general-purpose", "normal"), "sonnet")
        self.assertEqual(budget.model_for("general-purpose", "critical"), "haiku")  # no work model at critical
        self.assertEqual(budget.model_for("Explore", "ample"), "haiku")

    def test_hook_fills_missing_model_keeping_every_field(self):
        budget.set_override("normal")
        inp = {"prompt": "p", "description": "d", "subagent_type": "studio:verifier", "run_in_background": True}
        out = self.run_hook(inp)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(out["updatedInput"], dict(inp, model="opus"))
        self.assertIn("model=opus",out["additionalContext"])
        self.assertEqual(budget.filled_ids("s1"), {"toolu_a1"})

    def test_explicit_model_and_other_tools_untouched(self):
        got = self.run_hook({"prompt": "p", "model": "haiku", "subagent_type": "studio:verifier"})
        out = (got or {}).get("hookSpecificOutput") or {}    # R-1211: advice about a missing `task:` line is allowed; no model change is not
        self.assertNotIn("updatedInput", out)
        self.assertNotIn("permissionDecision", out)
        self.assertNotIn("model=", out.get("additionalContext", ""))
        self.assertIsNone(self.run_hook({"command": "ls"}, tool="Bash"))
        self.assertEqual(budget.filled_ids("s1"), set())

    def test_task_alias_is_filled_too(self):
        out = self.run_hook({"prompt": "p"}, tool="Task", tid="toolu_t")
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["model"], budget.model_for(None, budget.current()["level"]))
