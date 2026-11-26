"""R-1137 item 4: the advisory admit note is said once per session (a changed text counts as new); a guard decision is never deduped;
any state error falls back to emitting. Runs hook.py pre-tool-use as a subprocess with Agent payloads."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
MARK = "budgit admit"


class NotesOnceTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.config(max_busy=0)

    def config(self, **admit):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"admit": admit}, f)

    def run_hook(self, payload):
        env = dict(os.environ, STUDIO_HOME=self.home, BUDGIT_HOME=self.home, BUDGIT_ADMIT_ADVICE="on", BUDGIT_SUITE_LOAD_LIMIT="0")
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), capture_output=True,
                           text=True, env=env, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def agent(self, session="s1", tid="toolu_1"):
        return self.run_hook({"session_id": session, "tool_name": "Agent", "tool_use_id": tid, "cwd": self.tmp,
                              "tool_input": {"subagent_type": "general-purpose", "model": "haiku", "description": "x",
                                             "prompt": "do a small thing"}})

    def test_second_call_in_a_session_has_no_note(self):
        first, second = self.agent(tid="t1"), self.agent(tid="t2")
        self.assertIn(MARK, first, "scene must produce the admit note")
        self.assertNotIn(MARK, second)
        print("R-1137 bytes per Agent call: first %d, repeat %d, saved %d" % (len(first), len(second), len(first) - len(second)))

    def test_new_session_hears_it_again(self):
        self.agent("s1")
        self.assertIn(MARK, self.agent("s2"))

    def test_changed_verdict_text_is_new(self):
        import hook
        os.makedirs(self.home, exist_ok=True)
        refuse = "budgit admit: refuse: falta memoria: compressor %s GB of 16 GB RAM (limit 30 %%). aviso"
        self.assertTrue(hook._note_unseen("s9", refuse % "5.2"))
        self.assertFalse(hook._note_unseen("s9", refuse % "5.8"))     # drift under 1 GB: same note
        self.assertFalse(hook._note_unseen("s9", refuse % "4.5"))
        self.assertTrue(hook._note_unseen("s9", refuse % "7.9"))      # more than 1 GB from the one said: new
        self.assertTrue(hook._note_unseen("s9", "budgit admit: wait: la CPU al 80 % supera el limite"))   # verdict changed
        self.assertFalse(hook._note_unseen("s9", "budgit admit: wait: la CPU al 70 % supera el limite"))

    def test_unwritable_or_linked_state_still_emits(self):
        folder = os.path.join(self.home, "notes-seen")
        os.makedirs(self.home, exist_ok=True)
        target = os.path.join(self.tmp, "elsewhere")
        os.makedirs(target)
        os.symlink(target, folder)                      # the state folder is a link: dedup skipped
        self.assertIn(MARK, self.agent(tid="t1"))
        self.assertIn(MARK, self.agent(tid="t2"))
        self.assertEqual(os.listdir(target), [])

    def test_linked_state_file_still_emits(self):
        folder = os.path.join(self.home, "notes-seen")
        os.makedirs(folder)
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as f:
            f.write("keep")
        os.symlink(victim, os.path.join(folder, "s1"))
        self.assertIn(MARK, self.agent(tid="t1"))
        self.assertIn(MARK, self.agent(tid="t2"))
        with open(victim) as f:
            self.assertEqual(f.read(), "keep")

    def test_deny_on_a_second_call_is_still_a_deny(self):
        self.assertIn(MARK, self.agent(tid="t1"))
        out = self.run_hook({"session_id": "s1", "tool_name": "Bash", "tool_use_id": "t2", "cwd": self.tmp,
                             "tool_input": {"command": "git reset --hard HEAD~1"}})
        self.assertRegex(out, r'"permissionDecision": "(deny|ask)"')
