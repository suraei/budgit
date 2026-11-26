"""R-1284: the user's chat shows no English prose. Hook lines that ask the session for an answer ask in her language
(or the progress file), and a PreToolUse check stops a call that follows English prose in a Spanish chat."""
import json
import os
from unittest import mock

from helpers import StudioTestCase, patch_env
import general
import introspect
import spanishguard
import studiolib as sl

ENGLISH = ("I will now run the tests and then check that the output is what we expect before moving on to the "
           "next step of the plan.")
SPANISH = "Voy a ejecutar las pruebas y luego revisare que la salida sea la esperada antes de pasar al siguiente paso."
CMD = {"command": "python3 -m pytest tests/test_x.py"}


def _assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _tool_result():
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


class SpanishAskTest(StudioTestCase):
    def fail_three_times(self):
        note = None
        for _ in range(3):
            note = introspect.on_failure("s1", "Bash", CMD, "Exit code 1")
        return note

    def test_selfcheck_asks_in_spanish(self):
        note = self.fail_three_times()
        self.assertIn("budgit self-check:", note)
        self.assertIn("tercer fallo", note)
        self.assertIn("ineficiente o incorrecto", note)
        self.assertIn("Responde en español, o escribe notas en inglés solo en el archivo de progreso", note)
        self.assertNotIn("Before going on", note)
        self.assertEqual(note.count("archivo de progreso"), 1)
        patch_env(self, BUDGIT_LANG="en")
        introspect._save("s1", {})
        note = self.fail_three_times()
        self.assertIn("budgit self-check: 3rd failure", note)
        self.assertIn("inefficient or wrong", note)
        self.assertIn("Answer in English, or write English notes only to the progress file", note)

    def test_checkpoint_asks_in_spanish(self):
        with mock.patch.object(sl, "context_tokens", return_value=250000), \
                mock.patch.object(sl, "compact_window", return_value=300000):
            text = sl.checkpoint_due("s1", os.path.join(self.tmp, "t.jsonl"))
        self.assertIn("budgit checkpoint:", text)
        self.assertIn("250k", text)
        self.assertIn("300k", text)
        self.assertIn(".budgit/progress.md", text)
        self.assertIn("Dile al usuario en una línea", text)
        self.assertIn("Responde en español, o escribe notas en inglés solo en el archivo de progreso", text)
        self.assertNotIn("Tell the user", text)

    def test_triage_asks_in_spanish(self):
        text = general.stop_reason({}, "s1", ["I-5 untracked input"])
        self.assertIn("Clasifica cada entrada sin registrar en una línea", text)
        self.assertIn("budgit ledger link R-n --src I-n", text)
        self.assertIn("Responde en español, o escribe notas en inglés solo en el archivo de progreso", text)
        self.assertNotIn("Triage each", text)


class EnglishStopTest(StudioTestCase):
    def call(self, text=ENGLISH, tool="Bash", tool_input=None, records=None, path=None):
        path = path or os.path.join(self.tmp, "sess.jsonl")
        if records is None:
            records = [_user("hola"), _assistant(text)]
        if records:
            with open(path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")
        return spanishguard.english_stop({"tool_name": tool, "tool_input": tool_input or CMD, "transcript_path": path})

    def test_english_prose_before_tool_is_stopped(self):
        reason = self.call()
        self.assertIsNotNone(reason)
        self.assertIn("write it in Spanish or in progress.md", reason)
        self.assertIsNotNone(self.call(records=[_assistant(ENGLISH), _tool_result()]))   # a tool result is not a new prompt

    def test_spanish_text_passes(self):
        self.assertIsNone(self.call(SPANISH))
        mixed = "Voy a ejecutar the tests y luego check that the output is what we expect before the next step."
        self.assertIsNone(self.call(mixed))
        self.assertIsNone(self.call(ENGLISH.replace("run", "ejecutar el") + " ¿vale?"))

    def test_code_and_paths_pass(self):
        self.assertIsNone(self.call("```\n" + ENGLISH + "\n```"))
        self.assertIsNone(self.call("`" + ENGLISH + "` /srv/app/plugin/scripts/hook.py R-1284 I-2001 3efa1bd2"))
        self.assertIsNone(self.call('She wrote "%s" and «%s» in the file' % (ENGLISH, ENGLISH)))

    def test_short_text_passes(self):
        self.assertIsNone(self.call("Running the tests now to check that the output is fine."))
        self.assertIsNone(self.call(""))

    def test_non_spanish_project_passes(self):
        patch_env(self, BUDGIT_LANG="en")
        self.assertIsNone(self.call())

    def test_progress_write_passes(self):
        for name in (os.path.join(".budgit", "progress.md"), os.path.join(".studio", "progress-r1284.md")):
            path = os.path.join(self.project, name)
            for tool in ("Write", "Edit", "MultiEdit"):
                self.assertIsNone(self.call(tool=tool, tool_input={"file_path": path}), (tool, name))
        self.assertIsNotNone(self.call(tool="Write", tool_input={"file_path": os.path.join(self.project, "notes.md")}))

    def test_missing_transcript_fails_open(self):
        self.assertIsNone(self.call(records=[], path=os.path.join(self.tmp, "nope.jsonl")))
        self.assertIsNone(spanishguard.english_stop({"tool_name": "Bash"}))
        self.assertIsNone(spanishguard.english_stop({"tool_name": "Bash", "transcript_path": self.tmp}))
        garbage = os.path.join(self.tmp, "bad.jsonl")
        with open(garbage, "wb") as f:
            f.write(b"\xff\xfe not json\n{")
        self.assertIsNone(spanishguard.english_stop({"tool_name": "Bash", "transcript_path": garbage}))
        self.assertIsNone(spanishguard.english_stop(None))

    def test_text_before_a_new_user_message_passes(self):
        self.assertIsNone(self.call(records=[_assistant(ENGLISH), _user("sigue")]))

    def test_spanish_reply_quoting_english_commit_titles_passes(self):
        self.assertIsNone(self.call("Hecho. Commits: Fix the parser, Add tests for the hook, Remove the old guard, "
                                    "Update docs for this release."))

    def test_english_bullet_list_is_stopped(self):
        bullets = "- Run the tests before the release\n- Check that the output is fine\n* Add the missing test for the hook"
        self.assertIsNotNone(self.call(bullets))
        self.assertIsNotNone(self.call("1. " + ENGLISH))

    def test_spanish_reply_with_english_table_cells_passes(self):
        table = ("Resumen | prueba | resultado\n--- | --- | ---\nlogin is ok | passes with the new token\n"
                 "the build is ok | runs for all of the tests in this case\n| the cache is it | not that one |")
        self.assertIsNone(self.call(table))
        self.assertIsNotNone(self.call(ENGLISH + " " + ENGLISH.replace("run", "ran")))   # prose still stops

    def test_other_asks_in_spanish(self):
        import i18n
        note = "Responde en español, o escribe notas en inglés solo en el archivo de progreso."
        for key, fmt in (("replyfirst.warn", {"n": 5}), ("replyfirst.browser", {}), ("r1284.fix_check", {}),
                         ("r1284.push_ask", {"repo": "x"})):
            self.assertIn(note, i18n.ask(key, "es", **fmt), key)
            self.assertIn("Answer in English, or write English notes only to the progress file.",
                          i18n.ask(key, "en", **fmt), key)
        es = i18n.ask("r1284.fix_check", "es")      # no English tail after it, one answer-in clause, "push" kept for the guards tests
        self.assertNotIn("If you cannot get", es)
        self.assertIn("para y avísalo", es)
        self.assertEqual(es.count("archivo de progreso"), 1)
        self.assertIn("push", i18n.ask("r1284.push_ask", "es", repo="x"))
        scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "scripts")
        for fname, key in (("replyfirst.py", "replyfirst.warn"), ("replyfirst.py", "replyfirst.browser"),
                           ("studiolib.py", "r1284.fix_check"), ("studiolib.py", "r1284.push_ask")):
            with open(os.path.join(scripts, fname), encoding="utf-8") as f:
                self.assertIn('i18n.ask("%s"' % key, f.read(), key)
