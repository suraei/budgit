"""A hook or tool refusal text is never recorded as her input; only her typed prompts and her card answers are."""
import json
import os

from helpers import StudioTestCase
import studiolib as sl

CARD_LINT = ("PreToolUse:AskUserQuestion hook error: budgit: El aviso cita carpetas y columnas; "
             "redactalo con frases cortas.")
POST_NOTE = "PostToolUse:Bash hook error: budgit: nota sobre la herramienta."


class RefusalNotInputTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(os.path.realpath(self.home).startswith(os.path.realpath(self.tmp)))
        self.assertTrue(os.path.realpath(os.environ["STUDIO_HOME"]).startswith(os.path.realpath(self.tmp)))
        self.init_project()

    def texts(self):
        return [e["text"] for e in sl.load_inbox(self.project)]

    def transcript(self, records):
        tp = os.path.join(self.tmp, "t.jsonl")
        with open(tp, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return tp

    def test_the_card_lint_refusal_shape_is_not_recorded(self):
        self.assertIsNone(sl.append_inbox(self.project, CARD_LINT, "s1", "prompt"))
        self.assertIsNone(sl.append_inbox(self.project, CARD_LINT, "s1", "answer"))
        tp = self.transcript([
            {"type": "assistant", "uuid": "a1", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "AskUserQuestion", "input": {}}]}},
            {"type": "user", "uuid": "u1", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": CARD_LINT}]}},
        ])
        sl.reconcile_transcript(self.project, tp, "s1")
        self.assertEqual(self.texts(), [])

    def test_a_post_tool_hook_note_is_not_recorded(self):
        self.assertIsNone(sl.append_inbox(self.project, POST_NOTE, "s1", "prompt"))
        self.assertIsNone(sl.append_inbox(self.project, "UserPromptSubmit hook error: budgit: x", "s1", "prompt"))
        self.assertEqual(self.texts(), [])

    def test_her_prompt_quoting_a_refusal_line_in_a_longer_sentence_is_recorded(self):
        text = "me salio esto: " + CARD_LINT + " y quiero que lo arregles"
        self.assertEqual(sl.append_inbox(self.project, text, "s1", "prompt"), "I-1")
        self.assertEqual(self.texts(), [text])

    def test_a_refusal_line_followed_by_her_own_sentence_is_recorded_in_full(self):
        text = CARD_LINT + "\nesto me salio al pedir la tarjeta, mira por que"
        self.assertEqual(sl.append_inbox(self.project, text, "s1", "prompt"), "I-1")
        self.assertEqual(self.texts(), [text])

    def test_a_lowercase_stop_hook_error_prompt_is_recorded(self):
        for text in ("stop hook error: por que sale esto", "Stop hook error: por que sale esto",
                     "pretooluse:bash hook error: lo escribi yo"):
            self.assertIsNotNone(sl.append_inbox(self.project, text, "s1", "prompt"), text)
        self.assertEqual(len(self.texts()), 3)

    def test_the_real_rejection_with_the_user_said_is_recorded_as_her_answer(self):
        rej = "The user doesn't want to proceed with this tool use. the user said: mejor la opcion dos"
        tp = self.transcript([
            {"type": "assistant", "uuid": "a1", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "AskUserQuestion", "input": {}}]}},
            {"type": "user", "uuid": "u1", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": rej}]}},
            {"type": "assistant", "uuid": "a2", "message": {"content": [
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}]}},
            {"type": "user", "uuid": "u2", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": rej}]}},
        ])
        sl.reconcile_transcript(self.project, tp, "s1")
        self.assertEqual(self.texts(), [rej, "the user said: mejor la opcion dos"])
        self.assertEqual([e["source"] for e in sl.load_inbox(self.project)], ["answer", "answer"])

    def test_a_plain_spanish_prompt_and_a_real_card_answer_are_still_recorded(self):
        self.assertEqual(sl.append_inbox(self.project, "haz la libreta, por favor", "s1", "prompt"), "I-1")
        tp = self.transcript([
            {"type": "assistant", "uuid": "a1", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "AskUserQuestion", "input": {}}]}},
            {"type": "user", "uuid": "u1", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "User has answered: si, adelante"}]}},
        ])
        sl.reconcile_transcript(self.project, tp, "s1")
        self.assertEqual(self.texts(), ["haz la libreta, por favor", "User has answered: si, adelante"])
