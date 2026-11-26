import json, os, shutil
from helpers import StudioTestCase, ROOT
import studiolib as sl

FIX = os.path.join(ROOT, "tests", "fixtures", "transcript_basic.jsonl")


class InboxTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        shutil.copy(FIX, self.transcript)

    def texts(self):
        return [e["text"] for e in sl.load_inbox(self.project)]

    def test_harness_prompts_are_ignored(self):
        for text in ("<task-notification>x", "<agent-message from='a'>", "[Subagent hand-back] r",
                     "<cross-session-message from='b'>", "  <system-reminder>y",
                     "Another Claude session sent a message:\n<agent-message from='c'>z"):
            self.assertIsNone(sl.append_inbox(self.project, text, "s1", "prompt"))
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_prompt_text_kept_verbatim(self):
        long_text = "Celíaca, vegana — " + "x" * 5000
        self.assertEqual(sl.append_inbox(self.project, long_text, "s1", "prompt"), "I-1")
        self.assertEqual(self.texts(), [long_text])

    def test_reconcile_captures_human_queued_answers_and_rejections(self):
        new = sl.reconcile_transcript(self.project, self.transcript, "s1")
        self.assertEqual(self.texts(), [
            "Quiero una app de recetas con foto de la nevera",
            'Your questions have been answered: "¿Para quién?"="Producto público, sin IA". You can now continue.',
            "y también código de barras",
            "the user said:\nno toques la base de datos",
            "continua", "continua", "[image]"])
        self.assertEqual(new, ["I-1", "I-2", "I-3", "I-4", "I-5", "I-6", "I-7"])
        sources = [e["source"] for e in sl.load_inbox(self.project)]
        self.assertEqual(sources[1], "answer")

    def test_repeated_reconcile_is_idempotent(self):
        sl.reconcile_transcript(self.project, self.transcript, "s1")
        state = sl.load_state(self.project)
        state["offsets"] = {}
        sl.save_state(self.project, state)
        self.assertEqual(sl.reconcile_transcript(self.project, self.transcript, "s1"), [])

    def test_prompt_hook_entry_is_matched_not_duplicated(self):
        self.assertEqual(sl.append_inbox(self.project, "Quiero una app de recetas con foto de la nevera",
                                         "s1", "prompt"), "I-1")
        new = sl.reconcile_transcript(self.project, self.transcript, "s1")
        self.assertNotIn("I-1", new)
        self.assertEqual(self.texts().count("Quiero una app de recetas con foto de la nevera"), 1)
        self.assertEqual(sl.load_inbox(self.project)[0]["ref"], "u1")

    def test_answer_captured_by_tool_hook_is_not_duplicated(self):
        sl.append_inbox(self.project, "answered via hook", "s1", "answer", ref="toolu_ask1")
        sl.reconcile_transcript(self.project, self.transcript, "s1")
        self.assertEqual(sum(1 for e in sl.load_inbox(self.project) if e.get("ref") == "toolu_ask1"), 1)

    def test_partial_last_line_is_read_later(self):
        sl.reconcile_transcript(self.project, self.transcript, "s1")
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write("}}\n")
        new = sl.reconcile_transcript(self.project, self.transcript, "s1")
        self.assertEqual(new, ["I-8"])
        self.assertEqual(self.texts()[-1], "mensaje a medio escribir")

    def test_since_skips_older_messages(self):
        state = sl.load_state(self.project)
        state["since"]["s1"] = "2026-10-11T08:03:45.000Z"
        sl.save_state(self.project, state)
        sl.reconcile_transcript(self.project, self.transcript, "s1")
        self.assertEqual(self.texts(), ["continua", "continua", "[image]"])

    def test_untracked_is_per_session_and_excludes_linked_and_acked(self):
        sl.reconcile_transcript(self.project, self.transcript, "s1")
        sl.append_inbox(self.project, "otra sesión", "s2", "prompt")
        data = sl.load_ledger(self.project)
        self.assertEqual(len(sl.untracked(self.project, data, session="s1")), 7)
        self.assertEqual([e["id"] for e in sl.untracked(self.project, data, session="s2")], ["I-8"])
        sl.add_item(data, "app recetas", ["I-1", "I-2", "I-3"])
        sl.ack(data, ["I-4", "I-5", "I-6", "I-7"], "instructions folded into R-1")
        self.assertEqual(sl.untracked(self.project, data, session="s1"), [])
        self.assertEqual(len(sl.untracked(self.project, data)), 1)

    def test_ids_are_max_plus_one_even_with_corrupt_lines(self):
        sl.append_inbox(self.project, "uno", "s1", "prompt")
        with open(os.path.join(sl.studio_dir(self.project), "inbox.jsonl"), "a") as f:
            f.write("{corrupt\n")
        sl.append_inbox(self.project, "dos", "s1", "prompt")
        self.assertEqual([e["id"] for e in sl.load_inbox(self.project)], ["I-1", "I-2"])

    def test_missing_transcript_is_noop(self):
        self.assertEqual(sl.reconcile_transcript(self.project, "/nope.jsonl", "s1"), [])
        self.assertEqual(sl.reconcile_transcript(self.project, None, "s1"), [])
