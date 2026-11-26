"""R-868: the call budget counts each round (the brief, then every SendMessage resume), not the whole session.
Past the limit `git -C <worktree> add|commit` still works so green work can be saved."""
import json
import os
from helpers import StudioTestCase
import agentcap


class RoundBudgetTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.sess = os.path.join(self.home, "proj", "sessr")
        os.makedirs(os.path.join(self.sess, "subagents"), exist_ok=True)
        self.main = self.sess + ".jsonl"
        self.wt = self.linked("wt", "claude/work")

    def linked(self, name, branch):
        """A linked worktree: `.git` is a file pointing at a gitdir whose HEAD names the branch."""
        wt = os.path.realpath(os.path.join(self.home, name))
        os.makedirs(os.path.join(wt, "gd"))
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % os.path.join(wt, "gd"))
        with open(os.path.join(wt, "gd", "HEAD"), "w") as f:
            f.write("ref: refs/heads/%s\n" % branch)
        return wt

    def say(self, agent, text):
        p = os.path.join(self.sess, "subagents", "agent-%s.jsonl" % agent)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")

    def tool_result(self, agent):
        p = os.path.join(self.sess, "subagents", "agent-%s.jsonl" % agent)
        rec = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}
        with open(p, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def calls(self, n, agent, tool="Bash", command=None, path=None, cwd="default"):
        out = None
        for _ in range(n):
            inp = {"agent_id": agent, "tool_name": tool, "transcript_path": self.main}
            if cwd == "default":
                inp["cwd"] = self.wt
            elif cwd:
                inp["cwd"] = cwd
            if command:
                inp["tool_input"] = {"command": command}
            if path:
                inp["tool_input"] = {"file_path": path}
            self.tool_result(agent)                      # tool results are not rounds
            out = agentcap.check(inp, 60)
        return out

    def test_a_resumed_agent_gets_a_fresh_round_count(self):
        self.say("r1", "Call budget: 20")
        self.assertIsNone(self.calls(19, "r1"))
        self.assertEqual(self.calls(1, "r1")[0], "note")           # call 20 of round 1
        self.say("r1", "Continue: commit the work.")                # SendMessage resume, no new line
        self.assertIsNone(self.calls(19, "r1"))                     # round 2 starts at 0 with the original 20
        self.assertEqual(self.calls(1, "r1")[0], "note")

    def test_a_new_budget_line_on_resume_applies(self):
        self.say("r2", "Call budget: 10")
        self.calls(10, "r2")
        self.say("r2", "Next round.\nCall budget: 30")
        self.assertIsNone(self.calls(29, "r2"))
        kind, text = self.calls(1, "r2")
        self.assertEqual(kind, "note")
        self.assertIn("budget 30", text)

    def test_the_warn_text_names_the_checkpoint_commit(self):
        self.say("r3", "Call budget: 5")
        kind, text = self.calls(5, "r3")
        self.assertEqual(kind, "note")
        self.assertIn("Commit green work now as a checkpoint", text)
        self.assertIn("hand back", text)

    def test_past_the_limit_git_add_and_git_commit_are_refused(self):
        self.say("r4", "Call budget: 8")
        self.calls(11, "r4")                                        # hard = 10: call 11 is refused
        for cmd in ("git -C %s commit -m x", "git -C %s add -u", "git -C %s add a.py", "git -C %s push"):
            kind, text = self.calls(1, "r4", command=cmd % self.wt)
            self.assertEqual(kind, "deny", cmd)
            self.assertIn("your caller commits", text)

    def over(self, agent):
        self.say(agent, "Call budget: 4")
        self.calls(8, agent)                                        # hard = 5: well over

    def test_file_tools_never_touch_the_transcript_or_counter_state(self):
        self.over("f1")
        sub = os.path.join(self.sess, "subagents", "agent-f1.jsonl")
        state = os.path.join(agentcap.sl.home_state(), "agent-calls", "f1.round")
        for tool in ("Write", "Edit"):
            for p in (sub, state, os.path.join(self.sess, "subagents", "agent-other.jsonl")):
                self.assertEqual(self.calls(1, "f1", tool=tool, path=p)[0], "deny")
        self.say("f2", "Call budget: 50")                           # not past any limit: still refused
        self.assertEqual(self.calls(1, "f2", tool="Write", path=state)[0], "deny")

    def test_a_forged_transcript_line_or_overwritten_round_state_does_not_reset(self):
        self.over("g1")
        write = os.path.join(self.sess, "subagents", "agent-g1.jsonl")
        self.assertEqual(self.calls(1, "g1", tool="Write", path=write)[0], "deny")    # the forge itself is refused
        state = os.path.join(agentcap.sl.home_state(), "agent-calls", "g1.round")
        self.assertEqual(self.calls(1, "g1", tool="Edit", path=state)[0], "deny")     # so is the state overwrite
        self.assertEqual(self.calls(1, "g1")[0], "deny")                              # and the count stood

    def test_past_the_limit_write_is_only_the_own_progress_file(self):
        self.over("w1")
        self.assertIsNone(self.calls(1, "w1", tool="Write", path=os.path.join(self.wt, "progress.md")))
        self.assertEqual(self.calls(1, "w1", tool="Edit", path=os.path.join(self.wt, "notes", "progress-x.md"))[0], "deny")
        self.assertIsNone(self.calls(1, "w1", tool="Edit", path=os.path.join(self.wt, "progress-x.md")))
        self.assertEqual(self.calls(1, "w1", tool="Write", path=os.path.join(self.wt, "app.py"))[0], "deny")
        self.assertEqual(self.calls(1, "w1", tool="Write", path="/tmp/progress.md")[0], "deny")

    def test_progress_write_needs_a_linked_worktree_off_main_with_a_cwd(self):
        self.over("o1")
        onmain = self.linked("onmain", "main")
        self.assertEqual(self.calls(1, "o1", tool="Write", path=onmain + "/progress.md", cwd=onmain)[0], "deny")
        main_tree = os.path.realpath(os.path.join(self.home, "maintree"))
        os.makedirs(os.path.join(main_tree, ".git"))                # a primary checkout: .git is a directory
        with open(os.path.join(main_tree, ".git", "HEAD"), "w") as f:
            f.write("ref: refs/heads/feature\n")
        self.assertEqual(self.calls(1, "o1", tool="Write", path=main_tree + "/progress.md", cwd=main_tree)[0], "deny")
        self.assertEqual(self.calls(1, "o1", tool="Write", path=self.wt + "/progress.md", cwd=None)[0], "deny")
        self.assertIsNone(self.calls(1, "o1", tool="Write", path=self.wt + "/progress.md"))

    def test_corrupt_round_state_or_counter_does_not_reset_to_zero(self):
        self.over("c1")
        base = os.path.join(agentcap.sl.home_state(), "agent-calls", "c1")
        with open(base + ".round", "w") as f:
            f.write("{not json")
        self.say("c1", "resume")
        self.assertEqual(self.calls(1, "c1")[0], "deny")            # still over: no reset
        with open(base, "w") as f:
            f.write("garbage")
        self.assertEqual(self.calls(1, "c1")[0], "deny")            # unreadable counter fails closed

    def test_an_unreadable_transcript_does_not_reset_the_count(self):
        self.over("u1")
        os.remove(os.path.join(self.sess, "subagents", "agent-u1.jsonl"))
        self.assertEqual(self.calls(1, "u1")[0], "deny")
        with open(os.path.join(self.sess, "subagents", "agent-u1.jsonl"), "w") as f:
            f.write("not json at all\n{broken\n")
        self.assertEqual(self.calls(1, "u1")[0], "deny")

    def test_a_round_budget_is_capped_at_500(self):
        self.say("x1", "Call budget: 9999")
        self.assertIsNone(self.calls(499, "x1"))
        self.assertEqual(self.calls(1, "x1")[0], "note")            # call 500 = the cap, not 9999
        self.say("x1", "Call budget: 8000")
        self.assertEqual(agentcap._read_round({"agent_id": "x1", "transcript_path": self.main}), 500)

    def test_add_everything_is_refused_past_the_limit(self):
        self.over("a1")
        for arg in ("-A", ".", "--all", ".git/config", ".env"):
            self.assertEqual(self.calls(1, "a1", command="git -C %s add %s" % (self.wt, arg))[0], "deny", arg)

    def test_at_cap_plus_21_only_the_progress_file_passes_and_any_bash_is_denied(self):
        self.say("k1", "Call budget: 40")
        self.calls(51, "k1")                                        # hard = 50
        self.assertEqual(self.calls(1, "k1", command="ls")[0], "deny")
        self.assertEqual(self.calls(1, "k1", command="git -C %s commit -m green" % self.wt)[0], "deny")
        self.assertIsNone(self.calls(1, "k1", tool="Write", path=self.wt + "/progress-k1.md"))

    def test_each_non_round_record_kind_is_skipped(self):
        for i, rec in enumerate(({"isCompactSummary": True}, {"isVisibleInTranscriptOnly": True}, {"isMeta": True},
                                 {"interrupt": True})):
            agent = "s%d" % i
            self.say(agent, "Call budget: 4")
            self.calls(8, agent)
            p = os.path.join(self.sess, "subagents", "agent-%s.jsonl" % agent)
            text = "[Request interrupted by user]" if "interrupt" in rec else "summary"
            with open(p, "a") as f:
                f.write(json.dumps(dict(type="user", message={"role": "user", "content": text},
                                       **({} if "interrupt" in rec else rec))) + "\n")
            self.assertEqual(self.calls(1, agent)[0], "deny", rec)     # no new round: still refused

    def test_a_real_recorded_coordinator_resume_starts_a_round(self):
        fx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "r868_resume_record.json")
        with open(fx) as f:
            line = f.read().strip()
        rec = json.loads(line)
        self.assertTrue(rec["isMeta"])                               # as recorded: isMeta with origin coordinator
        self.say("q1", "Call budget: 10")
        self.calls(10, "q1")
        with open(os.path.join(self.sess, "subagents", "agent-q1.jsonl"), "a") as f:
            f.write(line + "\n")
        self.assertIsNone(self.calls(5, "q1"))                       # round 2: count restarted
        for kind in ("task-notification", "system-reminder"):        # recorded isMeta too, but not rounds
            with open(os.path.join(self.sess, "subagents", "agent-q1.jsonl"), "a") as f:
                f.write(json.dumps(dict(rec, origin={"kind": kind})) + "\n")
        self.assertIsNone(self.calls(4, "q1"))                       # 9 calls: no reset, still under 10
        self.assertEqual(self.calls(1, "q1")[0], "note")             # the 10th of round 2 is the budget note

    def test_a_task_notification_meta_record_does_not_start_a_round(self):
        rec = {"type": "user", "isMeta": True, "origin": {"kind": "task-notification", "producer": "session-task"},
               "message": {"role": "user", "content": "<task-notification>done</task-notification>"}}
        self.assertIsNone(agentcap._user_text(rec))
        self.assertIsNone(agentcap._user_text(dict(rec, origin={"kind": "system-reminder"})))
        self.assertIsNone(agentcap._user_text(dict(rec, origin=None)))
        self.assertIsNotNone(agentcap._user_text(dict(rec, origin={"kind": "coordinator"})))

    def test_a_non_progress_file_in_the_cwd_is_refused_past_the_limit(self):
        self.over("n2")
        self.assertEqual(self.calls(1, "n2", tool="Write", path=self.wt + "/notes.md")[0], "deny")
        self.assertIsNone(self.calls(1, "n2", tool="Write", path=self.wt + "/progress.md"))

    def test_an_empty_counter_is_corrupt_and_fails_closed(self):
        self.over("e1")
        with open(os.path.join(agentcap.sl.home_state(), "agent-calls", "e1"), "w") as f:
            f.write("")
        self.assertEqual(self.calls(1, "e1")[0], "deny")

    def test_a_non_object_transcript_line_changes_nothing_and_an_error_fails_closed_past_the_limit(self):
        self.over("j1")
        p = os.path.join(self.sess, "subagents", "agent-j1.jsonl")
        with open(p, "a") as f:
            f.write("[1, 2]\n3\nnull\n")
        self.assertEqual(self.calls(1, "j1")[0], "deny")            # skipped, no reset, no crash
        real = agentcap._read_round
        agentcap._read_round = lambda inp: 1 / 0                    # an error inside the check
        try:
            self.assertEqual(self.calls(1, "j1")[0], "deny")        # past the limit: refused
            self.say("j2", "Call budget: 50")
            self.assertIsNone(self.calls(1, "j2"))                  # under the limit: may pass
        finally:
            agentcap._read_round = real

    def test_state_file_is_written_atomically(self):
        self.say("t1", "Call budget: 9")
        self.calls(1, "t1")
        d = os.path.join(agentcap.sl.home_state(), "agent-calls")
        self.assertEqual([n for n in os.listdir(d) if ".tmp" in n], [])

    def test_protected_x_replay_60_calls_then_resume_then_10_calls_not_refused(self):
        self.say("v1", "Call budget: 50")
        self.calls(60, "v1")                                        # 50 + 25 % = 62: round 1 ends near the wall
        self.say("v1", "Commit your green code and hand back.")
        self.assertIn("budget read: 50", self.calls(1, "v1")[1])    # the round's first call prints the budget read, never a refusal
        for _ in range(9):
            self.assertIsNone(self.calls(1, "v1"))

    def test_no_budget_line_stays_unbound_and_parallel_agents_count_apart(self):
        self.say("p1", "No number.")
        self.say("p2", "Call budget: 6")
        self.assertIsNone(self.calls(40, "p1"))                     # level cap 60: quiet at 40
        self.calls(8, "p2")
        self.assertEqual(self.calls(1, "p2")[0], "deny")            # p2 is over, p1 is not
        self.assertIsNone(self.calls(1, "p1"))


if __name__ == "__main__":
    import unittest
    unittest.main()
