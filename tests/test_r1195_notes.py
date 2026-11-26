"""R-1195 A (I-1796): a note about a task is a directive on it, a property; it never changes the task count."""
import json
import os
import subprocess
import sys
import time

from test_flow_data import FlowCase, CLI
from test_inbox_capture import run_hook
import studiolib as sl

SESSION = "s-flow0001"


def in_order(text, *needles):
    pos = [text.index(n) for n in needles]
    return pos == sorted(pos)


class NotesTest(FlowCase):
    def inbox(self, text, source="prompt"):
        return sl.append_inbox(self.project, text, SESSION, source)

    def set_cap(self, cap):
        path = os.path.join(self.project, self.DATA, "project.json")
        cfg = {}
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            pass
        cfg["open_cap"] = cap
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def counts(self):
        return sl.ledger_counts(sl.load_ledger(self.project))

    def test_a_directive_leaves_the_open_count_unchanged_and_show_lists_it(self):
        before = self.counts()
        n_items = len(sl.load_ledger(self.project)["items"])
        i = self.inbox("la libreta debe ser verde")
        self.ok("ledger", "link", self.a, "--src", i, "--directive", "la libreta debe ser verde")
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(sl.load_ledger(self.project)["items"]), n_items)
        self.assertIn("la libreta debe ser verde", self.ok("ledger", "show", self.a))
        self.assertEqual(self.item(self.a)["directives"][0]["src"], i)
        entry = [e for e in sl.load_inbox(self.project) if e["id"] == i][0]
        self.assertEqual(entry["kind"], "directive")
        self.assertEqual(sl.untracked(self.project, sl.load_ledger(self.project)), [])

    def test_the_notice_names_match_then_link_directive_then_ack_then_add(self):
        text = sl.untracked_notice([{"id": "I-9", "text": "algo"}])
        self.assertTrue(in_order(text, "ledger match", "link R-n --src I-n --directive", "ledger ack", "ledger add"))
        self.assertIn("only for new actionable work", text)

    def test_the_card_answer_note_has_the_same_order(self):
        out = run_hook(self.home, "post-tool-use", {"session_id": SESSION, "cwd": self.project, "tool_name": "AskUserQuestion",
                                                    "tool_use_id": "t1", "tool_input": {"questions": [{"question": "Which colour?"}]},
                                                    "tool_response": "Verde"})
        self.assertTrue(in_order(out, "ledger match", "link R-n --src", "--directive", "ledger ack", "ledger add"))
        self.assertIn("only for new actionable work", out)

    def test_a_card_answer_naming_an_open_item_becomes_a_directive_and_adds_nothing(self):
        n_items = len(sl.load_ledger(self.project)["items"])
        out = run_hook(self.home, "post-tool-use", {"session_id": SESSION, "cwd": self.project, "tool_name": "AskUserQuestion",
                                                    "tool_use_id": "t2",
                                                    "tool_input": {"questions": [{"question": "About %s: which colour?" % self.a}]},
                                                    "tool_response": "Verde"})
        self.assertIn("if the answer asks for new work, triage it with `ledger add --src", out)    # a real new task is never lost
        data = sl.load_ledger(self.project)
        self.assertEqual(len(data["items"]), n_items)
        self.assertIn("Verde", sl.find_item(data, self.a)["directives"][0]["text"])
        self.assertEqual(sl.load_inbox(self.project)[-1]["kind"], "directive")

    def card(self, tid, questions):
        return run_hook(self.home, "post-tool-use", {"session_id": SESSION, "cwd": self.project, "tool_name": "AskUserQuestion",
                                                     "tool_use_id": tid, "tool_input": {"questions": questions},
                                                     "tool_response": "Verde"})

    def test_a_card_naming_two_items_leaves_the_answer_untracked_with_the_normal_triage_note(self):
        out = self.card("t3", [{"question": "About %s and %s: which colour?" % (self.a, self.b)}])
        self.assertIn("ledger match", out)
        self.assertNotIn("directive on", out)
        self.assertNotIn("directives", sl.find_item(sl.load_ledger(self.project), self.a))
        self.assertNotIn("kind", sl.load_inbox(self.project)[-1])

    def test_a_card_with_two_questions_leaves_the_answer_untracked_with_the_normal_triage_note(self):
        out = self.card("t4", [{"question": "About %s: colour?" % self.a}, {"question": "Size?"}])
        self.assertIn("ledger match", out)
        self.assertNotIn("directive on", out)
        self.assertNotIn("directives", sl.find_item(sl.load_ledger(self.project), self.a))

    def test_a_single_item_card_attaches_and_its_note_carries_the_new_work_line(self):
        out = self.card("t5", [{"question": "Colour?", "options": [{"label": "Verde for %s" % self.a}]}])
        self.assertIn("directive on %s" % self.a, out)
        self.assertIn("if the answer asks for new work, triage it with `ledger add --src", out)

    def test_an_add_that_fails_on_a_bad_due_leaves_the_inbox_entry_without_kind_task(self):
        i = self.inbox("haz algo nuevo con fecha mala")
        r = self.cli("ledger", "add", "--src", i, "--area", "triage", "--phase", "later", "--appetite", "small", "--due", "not-a-date")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotEqual([e for e in sl.load_inbox(self.project) if e["id"] == i][0].get("kind"), "task")

    def test_an_unrelated_old_prompt_given_to_over_cap_is_recorded_not_valid(self):
        self.set_cap(1)
        old = self.inbox("prompt viejo")
        new = self.inbox("prompt nuevo")
        for src, over, valid in ((new, old, False), (new, new, True), (None, new, False)):
            args = ["ledger", "add", "--text", "cosa %s %s" % (over, valid), "--area", "triage", "--phase", "later", "--appetite", "small",
                    "--over-cap", over]
            if src:
                args += ["--src", src]
            self.ok_s(*args)
            self.assertEqual(sl.load_ledger(self.project)["items"][-1]["over_cap"], {"src": over, "valid": valid})
        r = self.cli("ledger", "add", "--src", new, "--area", "triage", "--phase", "later", "--appetite", "small", "--over-cap", new)   # no session
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sl.load_ledger(self.project)["items"][-1]["over_cap"]["valid"], False)

    def ok_s(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_CODE_SESSION_ID=SESSION)
        r = subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_add_at_the_cap_still_adds_with_one_warning_and_no_cap_no_warning(self):
        out = self.ok("ledger", "add", "--text", "nueva cosa uno", "--area", "triage", "--phase", "later", "--appetite", "small")
        self.assertNotIn("open items, at or above the cap", out)
        self.set_cap(2)
        n = len(sl.load_ledger(self.project)["items"])
        out = self.ok("ledger", "add", "--text", "nueva cosa dos", "--area", "triage", "--phase", "later", "--appetite", "small")
        self.assertEqual(len(sl.load_ledger(self.project)["items"]), n + 1)
        warn = [l for l in out.splitlines() if "at or above the cap" in l]
        self.assertEqual(len(warn), 1)
        self.assertTrue(in_order(warn[0], "ledger match", "--directive", "ledger fold"))

    def test_over_cap_is_valid_only_for_a_prompt_entry(self):
        self.set_cap(1)
        manual = self.inbox("x manual", source="manual")
        note = self.inbox("x note", source="note")
        prompt = self.inbox("x prompt", source="prompt")
        for i, valid in ((manual, False), (note, False), (prompt, True)):
            self.ok_s("ledger", "add", "--src", i, "--text", "cosa %s" % i, "--area", "triage", "--phase", "later", "--appetite", "small", "--over-cap", i)
            last = sl.load_ledger(self.project)["items"][-1]
            self.assertEqual(last["over_cap"], {"src": i, "valid": valid})

    def test_add_sets_kind_task_on_its_inbox_entry(self):
        i = self.inbox("haz algo nuevo")
        self.ok("ledger", "add", "--src", i, "--area", "triage", "--phase", "later", "--appetite", "small")
        self.assertEqual([e for e in sl.load_inbox(self.project) if e["id"] == i][0]["kind"], "task")

    def test_the_status_line_shows_created_and_closed_counts(self):
        data = sl.load_ledger(self.project)
        old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 10 * 86400))
        for it in data["items"]:
            it["created"] = old
        sl.save_ledger(self.project, data)
        self.assertIn("created vs closed, last 3 days: 0 created, 0 closed", self.ok("status"))
        self.ok("ledger", "add", "--text", "reciente", "--area", "triage", "--phase", "later", "--appetite", "small")
        self.assertIn("1 created, 0 closed", self.ok("status"))
        self.ok("ledger", "fold", self.a, "--into", self.b, "--reason", "same task")       # a fold is a close: the number moves
        self.assertIn("1 closed", self.ok("status"))
        self.ok("ledger", "add", "--text", "una cosa que sobra", "--area", "triage", "--phase", "later", "--appetite", "small")
        extra = sl.load_ledger(self.project)["items"][-1]["id"]
        self.ok("ledger", "drop", extra, "--not-needed", "--evidence", "no longer needed")
        self.assertIn("2 closed", self.ok("status"))
