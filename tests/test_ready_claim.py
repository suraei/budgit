"""The ready-claim Stop check: a reply that calls outward work ready, complete or correct needs an independent check from today.
Synthetic names only."""
import json
import os
import time
from unittest import mock

from helpers import StudioTestCase
import i18n
import outguard
import verifyfile

READY_ES = "Todo está listo, ya puedes presentar el modelo."
READY_EN = "The filing is ready to send."
READY_FR = "Tout est prêt, le dossier est vérifié."
PASS_ES = READY_ES + " Verificación independiente: 30 de 30."


class Base(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.n = 0
        os.makedirs(os.path.join(self.project, ".budgit", "verify"))
        os.makedirs(os.path.join(self.project, "data"))

    def mark(self, project=None):
        """The marker the real-data guard relies on: a non-empty protected_paths in the project config."""
        with open(os.path.join(project or self.project, ".budgit", "project.json"), "w", encoding="utf-8") as f:
            json.dump({"protected_paths": ["data/**"]}, f)

    def population(self, rows=30, name="ops.csv"):
        path = os.path.join(self.project, "data", name)
        with open(path, "w", encoding="utf-8") as f:
            if name.endswith(".csv"):
                f.write("id,amount\n")
                f.writelines("%d,%d\n" % (i, i * 3) for i in range(rows))
            else:
                f.writelines(json.dumps({"id": i}) + "\n" for i in range(rows))
        return "data/" + name

    def verify(self, name="check", first="PASS", n=30, m=30, reviewer="agent-7", rows=30, pop="data/ops.csv", age_days=0,
              line=None, population_line=True):
        if pop and not os.path.exists(os.path.join(self.project, pop)):
            self.population(rows, os.path.basename(pop))
        body = [first, "reviewer: %s" % reviewer, line or "%d of %d" % (n, m)]
        if population_line:
            body.append("population: %s rows=%d" % (pop, rows))
        path = os.path.join(self.project, ".budgit", "verify", name + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body) + "\n")
        if age_days:
            then = time.time() - age_days * 86400 - 3600
            os.utime(path, (then, then))
        return path

    def findings(self, rows, name="check"):
        path = os.path.join(self.project, ".budgit", "verify", name + ".findings.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def session(self):
        self.n += 1
        return "sess%d" % self.n

    def claim(self, text, lang="es", project=None, session=None, turn=None):
        return outguard.ready_claim(project or self.project, text, session or self.session(), lang=lang, turn=turn)[0]


class ReadyClaimTest(Base):
    def test_a_ready_word_in_a_marked_project_with_no_pass_file_is_blocked(self):
        self.mark()
        self.assertIsNotNone(self.claim(READY_ES))

    def test_a_pass_file_from_today_with_n_equal_m_and_another_reviewer_passes(self):
        self.mark()
        self.verify()
        self.assertIsNone(self.claim(PASS_ES))

    def test_the_reply_must_state_the_exact_n_and_m_even_when_they_are_equal(self):
        self.mark()
        self.verify()
        self.assertIsNotNone(self.claim(READY_ES))
        self.assertIsNotNone(self.claim(READY_ES + " 29 de 30."))
        self.assertIsNotNone(self.claim(READY_ES + " 30 de 300."))
        self.assertIsNone(self.claim(READY_EN + " Verified 30 of 30."))

    def test_zero_of_m_is_blocked_even_when_the_reply_says_it(self):
        self.mark()
        self.verify(n=0, m=5, rows=5, pop="data/five.csv")
        self.assertIsNotNone(self.claim("Está listo: 0 de 5.", session="zero1"))
        self.assertFalse(verifyfile.check(os.path.join(self.project, ".budgit", "verify", "check.md"), self.project, "me")[0])

    def test_a_pass_file_older_than_today_is_blocked(self):
        self.mark()
        self.verify(age_days=2)
        self.assertIsNotNone(self.claim(READY_ES))

    def test_n_below_m_needs_the_same_n_de_m_in_the_reply(self):
        self.mark()
        self.verify(n=12, m=30)
        self.assertIsNotNone(self.claim(READY_ES))
        self.assertIsNone(self.claim("Esta listo en parte: 12 de 30 comprobadas."))
        self.assertIsNotNone(self.claim("Esta listo en parte: 13 de 30 comprobadas."))

    def test_a_reviewer_equal_to_the_session_id_is_blocked(self):
        self.mark()
        self.verify(reviewer="same-chat")
        self.assertIsNotNone(self.claim(READY_ES, session="same-chat"))
        self.assertIsNone(self.claim(PASS_ES, session="other-chat"))

    def test_a_fail_first_line_is_blocked(self):
        self.mark()
        self.verify(first="FAIL")
        self.assertIsNotNone(self.claim(READY_ES))

    def test_an_unmarked_project_is_not_checked(self):
        self.assertIsNone(self.claim(READY_ES))
        with open(os.path.join(self.project, ".budgit", "project.json"), "w", encoding="utf-8") as f:
            json.dump({"protected_paths": []}, f)
        self.assertIsNone(self.claim(READY_ES))

    def test_a_negation_in_the_same_clause_does_not_trigger(self):
        self.mark()
        for text in ("Todavía no está listo.", "Aún no está completo, faltan dos filas.", "It is not ready to send yet.",
                     "The file isn't verified.", "Ce n'est pas encore prêt.", "Pas encore vérifié."):
            self.assertIsNone(self.claim(text), text)
        self.assertIsNotNone(self.claim("No hay dudas, está listo."))

    def test_spanish_english_and_french_words_each_trigger(self):
        self.mark()
        for text in (READY_ES, "Está todo correcto.", "Sin errores.", "Ya puedes firmar.", READY_EN, "All set.", "The report is verified.",
                     "Nothing missing.", READY_FR, "C'est complet."):
            self.assertIsNotNone(self.claim(text), text)

    def test_ordinary_text_and_code_do_not_trigger(self):
        self.mark()
        for text in ("Hecho. Siguiente: revisar la fila 3.", "Run `ready_to_send` first.", "The complete list is in the file."):
            self.assertIsNone(self.claim(text), text)

    def test_a_population_of_30_rows_with_17_of_17_is_blocked(self):
        self.mark()
        self.verify(n=17, m=17, rows=17)
        self.population(30)
        self.assertIsNotNone(self.claim(READY_ES))

    def test_an_open_finding_is_blocked(self):
        self.mark()
        self.verify()
        self.findings([{"id": "f1", "item": "row 4", "text": "amount differs", "status": "open", "evidence": ""}])
        self.assertIsNotNone(self.claim(READY_ES))

    def test_a_closed_finding_with_empty_evidence_is_blocked_and_with_evidence_passes(self):
        self.mark()
        self.verify()
        self.findings([{"id": "f1", "item": "row 4", "text": "x", "status": "closed", "evidence": "  "}])
        self.assertIsNotNone(self.claim(READY_ES))
        self.findings([{"id": "f1", "item": "row 4", "text": "x", "status": "closed", "evidence": "source A, amount fixed"}])
        self.assertIsNone(self.claim(PASS_ES))

    def test_a_missing_population_file_is_blocked(self):
        self.mark()
        path = self.verify()
        os.remove(os.path.join(self.project, "data", "ops.csv"))
        self.assertTrue(os.path.exists(path))
        self.assertIsNotNone(self.claim(READY_ES))

    def test_the_reason_is_in_the_users_language_and_blocks_once(self):
        self.mark()
        sess = self.session()
        reason, note = outguard.ready_claim(self.project, READY_ES, sess, lang="es")
        self.assertEqual(reason, i18n.t("ready.reason", "es"))
        self.assertIn("verificación independiente", reason)
        self.assertIsNone(note)
        reason, note = outguard.ready_claim(self.project, READY_ES, sess, lang="es")
        self.assertIsNone(reason)
        self.assertEqual(note, i18n.t("ready.note", "es"))
        en, _ = outguard.ready_claim(self.project, READY_EN, self.session(), lang="en")
        self.assertEqual(en, i18n.t("ready.reason", "en"))
        self.assertIn("independent verification", en)

    def test_a_stop_already_continued_lets_the_turn_end_with_a_note(self):
        self.mark()
        reason, note = outguard.ready_claim(self.project, READY_ES, self.session(), lang="es", active=True)
        self.assertIsNone(reason)
        self.assertEqual(note, i18n.t("ready.note", "es"))

    def test_the_guard_never_raises(self):
        self.assertEqual(outguard.ready_claim(None, None, None), (None, None))
        self.assertEqual(outguard.ready_claim("/no/such/folder", READY_ES, "s"), (None, None))

    def test_stop_checks_runs_it_and_a_fail_open_error_passes(self):
        import general
        self.mark()
        inp = {"session_id": "wired1", "cwd": self.project, "stop_hook_active": False, "last_assistant_message": READY_ES}
        reason, note = general.stop_checks(inp, self.project)
        self.assertIn(i18n.t("ready.reason", "es"), reason)
        inp = dict(inp, session_id="wired2", last_assistant_message=PASS_ES)
        self.verify()
        self.assertIsNone(general.stop_checks(inp, self.project)[0])
        inp = dict(inp, session_id="wired3", stop_hook_active=True)
        os.remove(os.path.join(self.project, ".budgit", "verify", "check.md"))
        reason, note = general.stop_checks(inp, self.project)
        self.assertIsNone(reason)
        self.assertEqual(note, i18n.t("ready.note", "es"))


class VerifyFileTest(Base):
    def test_the_parser_returns_ok_and_a_reason(self):
        path = self.verify()
        ok, reason = verifyfile.check(path, self.project, "me")
        self.assertTrue(ok, reason)
        self.assertEqual(verifyfile.check(path, self.project, "agent-7")[0], False)

    def test_n_de_m_and_csv_and_jsonl_counts(self):
        path = self.verify(line="30 de 30")
        self.assertTrue(verifyfile.check(path, self.project, "me")[0])
        os.remove(os.path.join(self.project, "data", "ops.csv"))
        path = self.verify(pop="data/ops.jsonl")
        self.assertTrue(verifyfile.check(path, self.project, "me")[0])

    def test_rows_that_disagree_with_the_recount_or_with_m_fail(self):
        path = self.verify(rows=29)
        self.assertFalse(verifyfile.check(path, self.project, "me")[0])
        path = self.verify(rows=30, n=29, m=29)
        self.assertFalse(verifyfile.check(path, self.project, "me")[0])

    def test_a_blank_or_non_integer_count_and_a_missing_line_fail(self):
        for bad in ("x of y", "17 of", "of 30"):
            self.assertFalse(verifyfile.check(self.verify(line=bad), self.project, "me")[0], bad)
        self.assertFalse(verifyfile.check(self.verify(population_line=False), self.project, "me")[0])

    def test_a_broken_findings_row_fails(self):
        path = self.verify()
        self.findings(["not json"])
        self.assertFalse(verifyfile.check(path, self.project, "me")[0])
        self.findings(['{"id": "1", "status": "maybe"}'])
        self.assertFalse(verifyfile.check(path, self.project, "me")[0])

    def test_an_absolute_population_path_works_and_a_missing_file_fails(self):
        self.population()
        path = self.verify(pop=os.path.join(self.project, "data", "ops.csv"))
        self.assertTrue(verifyfile.check(path, self.project, "me")[0])
        self.assertFalse(verifyfile.check(os.path.join(self.project, "gone.md"), self.project, "me")[0])


class PopulationSafetyTest(Base):
    def outside(self):
        path = os.path.join(self.tmp, "outside.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("id\n" + "".join("%d\n" % i for i in range(30)))
        return path

    def passes(self, path):
        return verifyfile.check(path, self.project, "me")[0]

    def test_a_population_path_with_dotdot_or_absolute_outside_the_project_fails(self):
        out = self.outside()
        self.assertFalse(self.passes(self.verify(pop="../outside.csv")))
        self.assertFalse(self.passes(self.verify(pop=out)))

    def test_a_symlink_that_leaves_the_project_fails_and_one_that_stays_passes(self):
        os.symlink(self.outside(), os.path.join(self.project, "data", "link.csv"))
        self.assertFalse(self.passes(self.verify(pop="data/link.csv")))
        self.population()
        os.symlink(os.path.join(self.project, "data", "ops.csv"), os.path.join(self.project, "data", "near.csv"))
        self.assertTrue(self.passes(self.verify(pop="data/near.csv")))

    def test_a_fifo_fails_without_hanging(self):
        os.mkfifo(os.path.join(self.project, "data", "pipe.csv"))
        self.assertFalse(self.passes(self.verify(pop="data/pipe.csv")))

    def test_a_population_over_the_size_cap_fails(self):
        self.population()
        path = self.verify()
        self.assertTrue(self.passes(path))
        with mock.patch.object(verifyfile, "MAX_POPULATION", 100):
            self.assertFalse(self.passes(path))

    def test_a_file_with_no_line_break_in_the_first_chunk_fails_and_chunked_counting_is_exact(self):
        with open(os.path.join(self.project, "data", "blob.csv"), "w", encoding="utf-8") as f:
            f.write("x" * 200)
        path = self.verify(pop="data/blob.csv", n=1, m=1, rows=1)
        with mock.patch.object(verifyfile, "CHUNK", 64):
            self.assertFalse(self.passes(path))
            self.population()
            self.assertTrue(self.passes(self.verify()))


class ReadyTurnTest(Base):
    def transcript(self, *lines):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for role, content in lines:
                f.write(json.dumps({"type": role, "message": {"role": role, "content": content}}) + "\n")
        return path

    def test_a_second_claim_in_a_new_turn_within_120_seconds_is_blocked(self):
        self.mark()
        sess = self.session()
        self.assertIsNotNone(self.claim(READY_ES, session=sess, turn="t1"))
        self.assertIsNone(self.claim(READY_ES, session=sess, turn="t1"))
        self.assertIsNotNone(self.claim(READY_ES, session=sess, turn="t2"))

    def test_the_turn_key_follows_the_last_real_user_prompt(self):
        base = [("user", "first"), ("assistant", [{"type": "text", "text": "ok"}])]
        key1 = outguard.turn_key({"transcript_path": self.transcript(*base)})
        results = base + [("user", [{"type": "tool_result", "content": "x"}])]
        self.assertEqual(outguard.turn_key({"transcript_path": self.transcript(*results)}), key1)
        key2 = outguard.turn_key({"transcript_path": self.transcript(*(results + [("user", "second")]))})
        self.assertNotEqual(key1, key2)
        self.assertIsNone(outguard.turn_key({"transcript_path": os.path.join(self.tmp, "none.jsonl")}))

    def test_stop_checks_blocks_once_per_turn_and_again_in_the_next_turn(self):
        import general
        self.mark()
        base = [("user", "first"), ("assistant", [{"type": "text", "text": "a"}])]
        inp = {"session_id": "turn1", "cwd": self.project, "last_assistant_message": READY_ES,
               "transcript_path": self.transcript(*base)}
        self.assertIn(i18n.t("ready.reason", "es"), general.stop_checks(inp, self.project)[0])
        self.assertIsNone(general.stop_checks(inp, self.project)[0])
        inp["transcript_path"] = self.transcript(*(base + [("user", "second")]))
        self.assertIn(i18n.t("ready.reason", "es"), general.stop_checks(inp, self.project)[0])


class TriggerWordsTest(Base):
    def test_narrowed_spanish_words_do_not_trigger_on_ordinary_use(self):
        self.mark()
        for text in ("Aquí tienes la lista de la compra.", "Escribe tu nombre completo.", "Hecho.", "The task is done.",
                     "Una lista para revisar mañana".replace("lista para", "lista, para")):
            self.assertIsNone(self.claim(text), text)

    def test_ready_phrases_trigger(self):
        self.mark()
        for text in ("Está listo para enviar.", "Los modelos están listos.", "El modelo está completo.", "Todo hecho.",
                     "Ya está hecho.", "All done.", "Tout est fait.", "Ya listo."):
            self.assertIsNotNone(self.claim(text), text)


class FailOpenAndNotesTest(Base):
    def test_an_unreadable_verify_folder_blocks_instead_of_raising(self):
        self.mark()
        self.verify()
        with mock.patch("os.listdir", side_effect=PermissionError("denied")):
            self.assertIsNotNone(self.claim(PASS_ES))

    def test_the_gate_note_and_the_ready_note_are_both_kept(self):
        import general
        self.mark()
        inp = {"session_id": "notes1", "cwd": self.project, "stop_hook_active": True, "last_assistant_message": READY_ES}
        with mock.patch.object(general, "gate_stop", return_value=(None, "gate-note")):
            reason, note = general.stop_checks(inp, self.project)
        self.assertIsNone(reason)
        self.assertIn("gate-note", note)
        self.assertIn(i18n.t("ready.note", "es"), note)


class RoundThreeTest(Base):
    def transcript(self, entries):
        path = os.path.join(self.tmp, "t3.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return path

    def user(self, text, **extra):
        return dict({"type": "user", "message": {"role": "user", "content": text}}, **extra)

    def test_the_same_prompt_in_two_turns_blocks_both_turns(self):
        import general
        self.mark()
        first = [self.user("sí", uuid="u1"), {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": "x"}}]
        inp = {"session_id": "rep1", "cwd": self.project, "last_assistant_message": READY_ES, "transcript_path": self.transcript(first)}
        self.assertIn(i18n.t("ready.reason", "es"), general.stop_checks(inp, self.project)[0])
        self.assertIsNone(general.stop_checks(inp, self.project)[0])
        inp["transcript_path"] = self.transcript(first + [self.user("sí", uuid="u2")])
        self.assertIn(i18n.t("ready.reason", "es"), general.stop_checks(inp, self.project)[0])

    def test_the_turn_key_uses_the_uuid_then_the_timestamp_plus_the_text(self):
        key = lambda *entries: outguard.turn_key({"transcript_path": self.transcript(entries)})
        self.assertNotEqual(key(self.user("sí", uuid="u1")), key(self.user("sí", uuid="u2")))
        self.assertEqual(key(self.user("sí", uuid="u1")), key(self.user("sí", uuid="u1")))
        self.assertNotEqual(key(self.user("sí", timestamp="t1")), key(self.user("sí", timestamp="t2")))
        self.assertNotEqual(key(self.user("sí", uuid="u1")), key(self.user("no", uuid="u1")))

    def test_a_bare_listo_or_listos_triggers_but_a_bare_lista_does_not(self):
        self.mark()
        for text in ("Listo.", "Listos.", "Hecho. Listo."):
            self.assertIsNotNone(self.claim(text), text)
        for text in ("Lista.", "Aquí está la lista en el archivo.", "Listas.", "Revisa la lista de la compra."):
            self.assertIsNone(self.claim(text), text)

    def test_terminado_and_completado_trigger_only_after_a_helper_word(self):
        self.mark()
        for text in ("Está terminado.", "Todo completado.", "Ya terminado.", "Están completados."):
            self.assertIsNotNone(self.claim(text), text)
        for text in ("Terminado.", "Completado.", "El campo completado se guarda."):
            self.assertIsNone(self.claim(text), text)

    def test_turn_key_is_computed_only_after_the_marker_a_word_hit_and_a_failed_check(self):
        import general
        inp = {"session_id": "lazy1", "cwd": self.project, "last_assistant_message": READY_ES}
        with mock.patch.object(outguard, "turn_key", return_value="k") as spy:
            general.stop_checks(inp, self.project)                                  # unmarked project
            self.mark()
            general.stop_checks(dict(inp, session_id="lazy2", last_assistant_message="Hecho."), self.project)    # no word
            self.verify()
            general.stop_checks(dict(inp, session_id="lazy3", last_assistant_message=PASS_ES), self.project)    # check passes
            spy.assert_not_called()
            general.stop_checks(dict(inp, session_id="lazy4"), self.project)        # marker, word, no pass
            spy.assert_called_once()
