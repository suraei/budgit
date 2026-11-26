"""R-849 item C: the call budget comes from the brief's `Call budget: N` line.
At N a note says report now; past N + 25 % every tool but Write and the final message is refused."""
import json
import os
from helpers import StudioTestCase
import agentcap


class BriefBudgetTest(StudioTestCase):
    def transcript(self, agent, brief):
        sess = os.path.join(self.home, "proj", "sess1")
        os.makedirs(os.path.join(sess, "subagents"))
        main = sess + ".jsonl"
        with open(os.path.join(sess, "subagents", "agent-%s.jsonl" % agent), "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": brief}}) + "\n")
        return main

    def run_calls(self, n, main, agent, tool="Bash"):
        out = None
        for _ in range(n):
            out = agentcap.check({"agent_id": agent, "tool_name": tool, "transcript_path": main}, 60)
        return out

    def test_brief_budget_is_read_from_the_first_user_message(self):
        main = self.transcript("b1", "Do the thing.\nCall budget: 48 tool calls. Report before it.")
        self.assertEqual(agentcap.brief_budget({"agent_id": "b1", "transcript_path": main}), 48)

    def test_note_at_n_and_refusal_past_n_plus_25_percent(self):
        main = self.transcript("b2", "Call budget: 40")
        self.assertIsNone(self.run_calls(39, main, "b2"))
        kind, text = self.run_calls(1, main, "b2")
        self.assertEqual(kind, "note")
        self.assertIn("report now", text)
        self.assertIsNone(self.run_calls(4, main, "b2"))     # calls 41..44 are allowed and quiet
        kind, _ = self.run_calls(6, main, "b2")              # call 50 = 40 + 25 %: allowed, with a note
        self.assertEqual(kind, "note")
        kind, text = self.run_calls(1, main, "b2")
        self.assertEqual(kind, "deny")
        self.assertIn("final message", text)
        wt = os.path.realpath(os.path.join(self.home, "lwt"))     # a linked worktree on a feature branch
        os.makedirs(os.path.join(wt, "gd"))
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % os.path.join(wt, "gd"))
        with open(os.path.join(wt, "gd", "HEAD"), "w") as f:
            f.write("ref: refs/heads/claude/x\n")
        ok ={"agent_id": "b2", "tool_name": "Write", "transcript_path": main, "cwd": wt,
              "tool_input": {"file_path": wt + "/progress.md"}}
        self.assertIsNone(agentcap.check(ok, 60))                # R-868: only the own progress file
        self.assertIsNone(self.run_calls(1, main, "b2", tool="SubagentHandback"))

    def test_a_stated_budget_is_capped_at_500_on_first_read_and_from_the_cache(self):
        main = self.transcript("b4", "Call budget: 9999")
        arg = {"agent_id": "b4", "transcript_path": main}
        self.assertEqual(agentcap.brief_budget(arg), 500)
        self.assertEqual(agentcap.brief_budget(arg), 500)        # second read comes from the cache file
        main = self.transcript2("b5", "Call budget: 500")
        self.assertEqual(agentcap.brief_budget({"agent_id": "b5", "transcript_path": main}), 500)

    def transcript2(self, agent, brief):
        sess = os.path.join(self.home, "proj", "sess2")
        os.makedirs(os.path.join(sess, "subagents"), exist_ok=True)
        with open(os.path.join(sess, "subagents", "agent-%s.jsonl" % agent), "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": brief}}) + "\n")
        return sess + ".jsonl"

    def test_no_budget_line_falls_back_to_the_level_cap(self):
        main = self.transcript("b3", "No number here.")
        self.assertIsNone(agentcap.brief_budget({"agent_id": "b3", "transcript_path": main}))
        self.assertIsNone(self.run_calls(54, main, "b3"))
        kind, _ = self.run_calls(1, main, "b3")
        self.assertEqual(kind, "note")                        # the policy cap 60 - 5


if __name__ == "__main__":
    import unittest
    unittest.main()
