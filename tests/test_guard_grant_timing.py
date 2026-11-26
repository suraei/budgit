"""R-335 class 3: the transcript line of `/budgit-trust work` may land after the UserPromptSubmit hook ran. The grant is
then finished by the next PreToolUse of the same chat, which reads the transcript again; it never depends on timing."""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import HookCase2, sl  # noqa: E402

TYPED = "/budgit-trust work"


class GrantTimingTest(HookCase2):
    SID = "timing-chat"

    def setUp(self):
        super().setUp()
        sl.activate(self.project, session_id=self.SID)
        self.path = self.transcript("hello")                     # the typed line is NOT in the transcript yet

    def type_line(self, text=TYPED):
        with open(self.path, "a") as fh:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")

    def prompt_now(self):
        return self.fire("user-prompt-submit", dict(hook_event_name="UserPromptSubmit", prompt=TYPED,
                                                    transcript_path=self.path), session_id=self.SID)

    def tool_now(self, cmd="git -C %s status" % "x", sid=None):
        return self.fire("pre-tool-use", dict(hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": cmd},
                                              transcript_path=self.path), session_id=sid or self.SID)

    def test_a_line_that_lands_after_the_prompt_hook_is_proven_at_the_next_tool_call(self):
        out = self.prompt_now()
        self.assertIn("NO se ha", out.get("systemMessage", ""))                       # told: not turned on yet
        self.assertFalse(sl.trust_work_on(self.project))
        self.type_line()
        self.tool_now()
        self.assertTrue(sl.trust_work_on(self.project))

    def test_a_pending_grant_is_finished_once_and_then_gone(self):
        self.prompt_now()
        self.type_line()
        self.tool_now()
        sl.revoke_trust(self.project)
        self.tool_now()
        self.assertFalse(sl.trust_work_on(self.project), "a revoked grant never comes back from the pending note")

    def test_another_message_typed_after_it_voids_the_pending_grant(self):
        self.prompt_now()
        self.type_line()
        self.type_line("something else I typed")
        self.tool_now()
        self.assertFalse(sl.trust_work_on(self.project))

    def test_a_pending_grant_is_for_its_own_chat_only(self):
        self.prompt_now()
        self.type_line()
        self.tool_now(sid="another-chat")
        self.assertFalse(sl.trust_work_on(self.project))

    def test_a_pending_grant_expires(self):
        self.prompt_now()
        pend = os.path.join(sl.home_state(), "trust-pending.json")
        data = json.load(open(pend))
        for entry in data.values():
            entry["ts"] = time.time() - 3600
        json.dump(data, open(pend, "w"))
        self.type_line()
        self.tool_now()
        self.assertFalse(sl.trust_work_on(self.project))

    def test_a_malformed_pending_note_grants_nothing_and_does_not_break_the_prompt_hook(self):
        pend = os.path.join(sl.home_state(), "trust-pending.json")
        for bad in ("soon", None, [1], {"x": 1}, 1e999, time.time() + 3600):
            json.dump({self.SID: {"project": self.project, "ts": bad}}, open(pend, "w"))
            self.type_line()
            self.tool_now()                                          # the bad note for this chat is expired, not a grant
            self.assertFalse(sl.trust_work_on(self.project), repr(bad))
            self.path = self.transcript("hello")                     # the typed line is not there yet: a note is written
            json.dump({"older-chat": {"project": self.project, "ts": bad}}, open(pend, "w"))
            self.prompt_now()                                        # must not crash on somebody else's bad note
            self.assertIn(self.SID, json.load(open(pend)), repr(bad))
            self.assertNotIn("older-chat", json.load(open(pend)), repr(bad))

    def test_without_a_pending_note_a_typed_line_alone_grants_nothing(self):
        self.type_line()
        self.tool_now()
        self.assertFalse(sl.trust_work_on(self.project))

    def test_a_subagents_tool_call_never_finishes_the_grant(self):
        self.prompt_now()
        self.type_line()
        self.fire("pre-tool-use", dict(hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": "ls"},
                                       transcript_path=self.path, agent_id="a1", agent_type="general-purpose"),
                  session_id=self.SID)
        self.assertFalse(sl.trust_work_on(self.project))


if __name__ == "__main__":
    unittest.main()
