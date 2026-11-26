"""R-383: the model policy is enforced, not advised. An explicit model= dearer than the role's policy at the budget level is
lowered by the PreToolUse hook (one line says so) unless the prompt carries `ESCALATE:`; `budgit comply` counts the calls
that got through anyway as 'model above policy'."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import budget
import comply

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class TierTest(StudioTestCase):
    def test_families_are_ranked_and_unknown_names_have_no_rank(self):
        self.assertLess(budget.tier("haiku"), budget.tier("sonnet"))
        self.assertLess(budget.tier("sonnet"), budget.tier("opus"))
        self.assertEqual(budget.tier("claude-opus-5-5"), budget.tier("opus[1m]"))
        self.assertEqual(budget.tier("Claude-Sonnet-5-5"), budget.tier("sonnet"))
        for name in ("inherit", "", None, "gpt-5"):
            self.assertIsNone(budget.tier(name))

    def test_above_policy_names_the_role_model(self):
        self.assertEqual(budget.above_policy("general-purpose", "normal", "opus"), "sonnet")
        self.assertEqual(budget.above_policy("Explore", "normal", "sonnet"), "haiku")
        self.assertEqual(budget.above_policy("studio:verifier", "tight", "opus"), "sonnet")
        self.assertIsNone(budget.above_policy("studio:verifier", "normal", "opus"))        # judgement is opus at normal
        self.assertIsNone(budget.above_policy("general-purpose", "normal", "haiku"))       # cheaper is always fine
        self.assertIsNone(budget.above_policy("general-purpose", "normal", "inherit"))

    def test_a_role_with_no_model_at_the_level_is_not_lowered_to_the_mechanical_one(self):
        self.assertIsNone(budget.above_policy("general-purpose", "critical", "sonnet"))    # work is "none" at critical, not haiku
        self.assertEqual(budget.above_policy("studio:verifier", "critical", "opus"), "sonnet")
        self.assertEqual(budget.above_policy("Explore", "critical", "sonnet"), "haiku")

    def test_escalate_needs_the_marker_as_a_word(self):
        self.assertTrue(budget.escalated("do it\nESCALATE: two cheaper tries failed"))
        self.assertTrue(budget.escalated("ESCALATE: x"))
        self.assertFalse(budget.escalated("please escalate: later"))                       # case matters
        self.assertFalse(budget.escalated("NOESCALATE: x"))
        self.assertFalse(budget.escalated("DE-ESCALATE: the cheaper model is enough"))     # a hyphen is not a word start either
        self.assertFalse(budget.escalated("de-ESCALATE: x"))
        self.assertFalse(budget.escalated(None))


class HookTest(StudioTestCase):
    def run_hook(self, tool_input, tool="Agent", tid="toolu_a1", **env):
        payload = {"session_id": "s1", "tool_name": tool, "tool_use_id": tid, "tool_input": tool_input, "cwd": self.tmp}
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), capture_output=True, text=True,
                           env=dict(os.environ, STUDIO_HOME=self.home, BUDGIT_ADMIT_ADVICE="off", **env), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_a_dearer_model_is_lowered_keeping_every_other_field(self):
        budget.set_override("normal")
        inp = {"prompt": "p", "description": "d", "subagent_type": "general-purpose", "model": "opus", "run_in_background": True}
        out = self.run_hook(inp)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(out["updatedInput"], dict(inp, model="sonnet"))
        self.assertIn("model=opus lowered to sonnet", out["additionalContext"])
        self.assertIn("ESCALATE:", out["additionalContext"])
        self.assertEqual(budget.rewritten_ids("s1"), {"toolu_a1"})
        self.assertEqual(budget.filled_ids("s1"), set())

    def test_each_role_has_its_own_ceiling_and_a_full_model_id_counts(self):
        budget.set_override("tight")
        got = self.run_hook({"prompt": "p", "subagent_type": "studio:verifier", "model": "claude-opus-5-5"})
        self.assertEqual(got["hookSpecificOutput"]["updatedInput"]["model"], "sonnet")
        got = self.run_hook({"prompt": "p", "subagent_type": "Explore", "model": "sonnet"}, tid="toolu_b")
        self.assertEqual(got["hookSpecificOutput"]["updatedInput"]["model"], "haiku")

    def test_nothing_changes_at_or_below_the_policy_or_for_unknown_names(self):
        budget.set_override("normal")
        for tool_input in ({"prompt": "p", "subagent_type": "studio:verifier", "model": "opus"},
                           {"prompt": "p", "subagent_type": "general-purpose", "model": "sonnet"},
                           {"prompt": "p", "subagent_type": "general-purpose", "model": "haiku"},
                           {"prompt": "p", "model": "some-other-vendor-model"}):    # R-1018: inherit is filled, not left alone
            got = self.run_hook(tool_input)    # R-1211: a studio:* launch with no `task:` line may carry advice text; the model must stay untouched
            out = (got or {}).get("hookSpecificOutput") or {}
            self.assertNotIn("updatedInput", out, tool_input)
            self.assertNotIn("permissionDecision", out, tool_input)
            self.assertNotIn("model=", out.get("additionalContext", ""), tool_input)
        self.assertEqual(budget.rewritten_ids("s1"), set())

    def test_escalate_in_the_prompt_keeps_the_model(self):
        budget.set_override("normal")
        got = self.run_hook({"prompt": "hard bug\nESCALATE: sonnet failed twice", "subagent_type": "general-purpose", "model": "opus"})
        self.assertIsNone(got)
        self.assertEqual(budget.rewritten_ids("s1"), set())

    def test_the_task_alias_is_capped_too(self):
        budget.set_override("normal")
        got = self.run_hook({"prompt": "p", "model": "opus"}, tool="Task", tid="toolu_t")
        self.assertEqual(got["hookSpecificOutput"]["updatedInput"]["model"], "sonnet")

    def test_advise_says_it_and_off_is_silent_and_neither_rewrites(self):
        budget.set_override("normal")
        inp = {"prompt": "p", "model": "opus"}
        got = self.run_hook(inp, BUDGIT_MODEL_POLICY="advise")["hookSpecificOutput"]
        self.assertNotIn("updatedInput", got)
        self.assertIn("above the work policy", got["additionalContext"])
        self.assertIsNone(self.run_hook(inp, BUDGIT_MODEL_POLICY="off"))
        self.assertEqual(budget.rewritten_ids("s1"), set())

    def test_the_policy_text_the_brain_reads_names_the_marker(self):
        self.assertIn("ESCALATE:", budget.models_text("normal"))


def tool_use(mid, inp, tid):
    return {"type": "assistant", "timestamp": "2026-10-27T10:00:00Z",
            "message": {"id": mid, "model": "claude-opus-5-5",
                        "content": [{"type": "tool_use", "id": tid, "name": "Agent", "input": inp}]}}


class ComplyTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "projects")
        self.dir = os.path.join(self.root, "-Users-x-proj")
        os.makedirs(self.dir)

    def session(self, sid, recs):
        path = os.path.join(self.dir, sid + ".jsonl")
        with open(path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return path

    def test_a_dearer_model_that_got_through_is_a_violation_and_the_others_are_only_shown(self):
        path = self.session("s9", [
            tool_use("m1", {"prompt": "p", "subagent_type": "general-purpose", "model": "opus"}, "t_bad"),
            tool_use("m2", {"prompt": "p", "subagent_type": "general-purpose", "model": "opus"}, "t_lowered"),
            tool_use("m3", {"prompt": "x\nESCALATE: failed twice", "subagent_type": "general-purpose", "model": "opus"}, "t_esc"),
            tool_use("m4", {"prompt": "p", "subagent_type": "studio:verifier", "model": "opus"}, "t_ok"),
            tool_use("m5", {"prompt": "p", "subagent_type": "general-purpose", "model": "sonnet"}, "t_fine"),
        ])
        budget.record_rewrite("s9", "t_lowered", "opus", "sonnet")
        r = comply.audit(path, level="normal")
        self.assertEqual((r["models_above_policy"], r["models_lowered"], r["models_escalated"]), (1, 1, 1))
        self.assertEqual(comply.violations(r), 1)
        text = comply.table([dict(r, dir="d")])
        self.assertIn("model above policy", text)
        self.assertIn("lowered to the role policy by the hook", text)
        self.assertIn("kept with ESCALATE:", text)

    def test_the_same_calls_at_ample_are_judged_by_that_level(self):
        path = self.session("s10", [tool_use("m1", {"prompt": "p", "subagent_type": "studio:verifier", "model": "opus"}, "t1")])
        self.assertEqual(comply.audit(path, level="ample")["models_above_policy"], 0)
        self.assertEqual(comply.audit(path, level="tight")["models_above_policy"], 1)

    def test_the_command_prints_the_column(self):
        self.session("s11", [tool_use("m1", {"prompt": "p", "model": "opus"}, "t1")])
        r = subprocess.run([sys.executable, CLI, "comply", "--root", self.root, "--level", "normal"], capture_output=True, text=True,
                           env=dict(os.environ, STUDIO_HOME=self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("model above policy", r.stdout)
