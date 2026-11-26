"""The user's prompts and card answers reach the inbox: a prompt that arrives behind harness reminder blocks keeps its own
words, a chat opened inside a project binds on its own (and one opened in a lane worktree or a protected folder does
not), and a backfill imports what was missed from the session transcripts, once."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
REM = "<system-reminder>\nAttribution for commits: none.\n</system-reminder>\n"


def run_hook(home, event, payload):
    env = dict(os.environ, STUDIO_HOME=home)
    r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


class ReminderPrefixTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()

    def texts(self):
        return [e["text"] for e in sl.load_inbox(self.project)]

    def test_leading_reminder_blocks_are_dropped_and_the_user_words_kept(self):
        self.assertEqual(sl.append_inbox(self.project, REM + REM + "haz la libreta, por favor", "s1", "prompt"), "I-1")
        self.assertEqual(self.texts(), ["haz la libreta, por favor"])

    def test_a_reminder_alone_is_harness(self):
        self.assertIsNone(sl.append_inbox(self.project, REM + "\n  ", "s1", "prompt"))
        self.assertIsNone(sl.append_inbox(self.project, REM + "<task-notification>x", "s1", "prompt"))
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_real_shapes_reminder_plus_her_words_is_kept_and_every_peer_shape_is_not(self):
        sl.append_inbox(self.project, REM + REM + "continua con R-394", "s1", "prompt")
        for tail in ("<cross-session-message from=\"local_x\" name=\"PM\">\nhola\n</cross-session-message>",
                     "<agent-message from=\"a\">x</agent-message>", "<side-sessions-event>x</side-sessions-event>",
                     "<task-notification>x</task-notification>", "[SYSTEM NOTIFICATION] algo",
                     "Another Claude session sent a message while you were working:\n<cross-session-message>",
                     "<command-name>/model</command-name>", "<local-command-stdout>x</local-command-stdout>"):
            self.assertIsNone(sl.append_inbox(self.project, REM + tail, "s1", "prompt"), tail)
            self.assertIsNone(sl.append_inbox(self.project, tail, "s1", "prompt"), tail)
        self.assertIsNone(sl.append_inbox(self.project, REM + REM, "s1", "prompt"))
        self.assertEqual(self.texts(), ["continua con R-394"])

    def test_a_marker_behind_invisible_characters_or_in_capitals_is_still_harness(self):
        for text in ("\ufeff<task-notification>x", "\u200b<cross-session-message from=\"a\">x", "<TASK-NOTIFICATION>x",
                     REM + "\ufeff<Side-Sessions-Event>x", "\xa0\xa0[SYSTEM NOTIFICATION - NOT USER INPUT] x"):
            self.assertIsNone(sl.append_inbox(self.project, text, "s1", "prompt"), repr(text))
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_the_stop_reconcile_leaves_out_subagent_turns(self):
        tp = os.path.join(self.tmp, "t.jsonl")
        with open(tp, "w") as f:
            f.write(json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-10-27T12:00:00.000Z", "isSidechain": True,
                                "origin": {"kind": "human"}, "message": {"content": "de un subagente"}}) + "\n")
            f.write(json.dumps({"type": "user", "uuid": "u2", "timestamp": "2026-10-27T12:01:00.000Z",
                                "origin": {"kind": "human"}, "message": {"content": "de ella"}}) + "\n")
        sl.reconcile_transcript(self.project, tp, "s1")
        self.assertEqual(self.texts(), ["de ella"])

    def test_a_reminder_inside_the_prompt_stays_and_an_unclosed_one_is_harness(self):
        text = "mira esto <system-reminder>cita</system-reminder> y sigue"
        sl.append_inbox(self.project, text, "s1", "prompt")
        self.assertEqual(self.texts(), [text])
        self.assertIsNone(sl.append_inbox(self.project, "<system-reminder>sin cerrar", "s1", "prompt"))

    def test_the_prompt_hook_and_the_busy_spool_both_strip(self):
        sl.activate(self.project, "s1")
        out = run_hook(self.home, "user-prompt-submit", {"session_id": "s1", "cwd": self.project,
                                                         "prompt": REM + "primera frase"})
        self.assertEqual(self.texts(), ["primera frase"], out)
        sl.spool_inbox(self.project, REM + "segunda frase", "s1", "prompt")
        sl.drain_spool(self.project)
        self.assertEqual(self.texts(), ["primera frase", "segunda frase"])

    def test_the_transcript_copy_of_that_prompt_is_the_same_input(self):
        sl.append_inbox(self.project, REM + "una frase", "s1", "prompt")
        tp = os.path.join(self.tmp, "t.jsonl")
        with open(tp, "w") as f:
            f.write(json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-10-27T12:00:00.000Z",
                                "origin": {"kind": "human"},
                                "message": {"content": [{"type": "text", "text": REM}, {"type": "text", "text": "una frase"}]}}) + "\n")
        sl.reconcile_transcript(self.project, tp, "s1")
        self.assertEqual(self.texts(), ["una frase"])
        self.assertEqual(sl.load_inbox(self.project)[0]["ref"], "u1")


class HarnessMarkerTest(StudioTestCase):
    """The marker of a harness message cannot hide, and a user's own words are never taken for one."""

    def test_a_marker_hidden_behind_anything_that_shows_nothing_is_still_harness(self):
        tag = "<task-notification>x"
        lead = ("\u2003", "\u3000", "\u0085", "\u2000", "\u202f", "\x1c", "\u1680", "\u205f", "\u202a", "\u202e", "\u2066",
                "\u2067", "\u061c", "\u00ad", "\ufeff", "\u200b", " \u200b ", "\n\ufeff\n", "\u200f\u2060")
        for ch in lead:
            self.assertTrue(sl.is_harness(ch + tag), repr(ch))
        for text in ("\uff1ctask-notification>x", "\uff1c\uff54\uff41\uff53\uff4b\uff0d\uff4e\uff4f\uff54\uff49\uff46\uff49"
                     "\uff43\uff41\uff54\uff49\uff4f\uff4e\uff1ex", "<task\ufeff-notification>x", "<task-notif\u200bication>x",
                     "<TaSk-NoTiFiCaTiOn>x", "[SYSTEM NOTIFICATION - NOT USER INPUT]", "\u200b[Subagent hand-back] x",
                     "\ufeffThis session is being continued from a previous conversation x",
                     "Another\u00a0Claude session sent a message:"):
            self.assertTrue(sl.is_harness(text), repr(text))

    def test_a_marker_behind_a_very_long_run_of_nothing_or_marks_that_show_nothing_is_harness(self):
        tag = "<task-notification>x"
        for lead in ("\n" * 5000, " \u200b" * 3000, "\ufe0f", "\u034f", "\U000e0002", "\U000e0001\U000e0020", "\u0301", "\u3164"):
            self.assertTrue(sl.is_harness(lead + tag), repr(lead[:6]))
        self.assertTrue(sl.is_harness("<task\ufe0f-not\u034fification>x"))
        self.assertFalse(sl.is_harness("<task notification>x"))
        self.assertFalse(sl.is_harness("\n" * 5000 + "haz la libreta"))

    def test_the_users_own_words_are_never_harness(self):
        for text in ("this session is being continued from a previous conversation, ok?", "Another claude session sent a message? mine",
                     "[system notification] como lo de antes", "haz la libreta <task-notification> no", "\u200bhaz la libreta",
                     "\ufeff[system] fix", "<tarea>x", "line\n<task-notification>x", "\u200f\u05e9\u05dc\u05d5\u05dd"):
            self.assertFalse(sl.is_harness(text), repr(text))

    def test_a_forged_marker_never_lands_in_the_inbox_but_plain_words_behind_it_do(self):
        self.init_project()
        for text in ("\u2003<task-notification>x", "\uff1ctask-notification>x", "\u202e<agent-message>x"):
            self.assertIsNone(sl.append_inbox(self.project, text, "s1", "prompt"), repr(text))
        self.assertEqual(sl.append_inbox(self.project, "\u2003haz la libreta", "s1", "prompt"), "I-1")


class UndoIdTest(StudioTestCase):
    def test_undo_without_numbered_backfill_entries_and_a_bad_floor_do_not_crash_or_reuse(self):
        self.init_project()
        sl.append_inbox(self.project, "uno", "s1", "prompt")
        entries = sl.load_inbox(self.project)
        entries.append({"id": "weird", "backfill": True, "text": "x", "session": "s", "source": "prompt"})
        entries.append({"id": None, "backfill": True, "text": "y", "session": "s", "source": "prompt"})
        entries.append({"id": 7, "backfill": True, "text": "z", "session": "s", "source": "prompt"})
        sl._rewrite_inbox(self.project, entries)
        self.assertEqual(sl.append_inbox(self.project, "tres", "s1", "prompt"), "I-2")
        self.assertEqual(sl.undo_backfill(self.project), 3)
        self.assertEqual(sl.append_inbox(self.project, "dos", "s1", "prompt"), "I-3")
        for bad in (True, "7", 7.0, None, -5, [3]):
            state = sl.load_state(self.project)
            state["inbox_floor"] = bad
            sl.save_state(self.project, state)
            got = sl.append_inbox(self.project, "otra %r" % (bad,), "s1", "prompt")
            self.assertRegex(got, r"^I-\d+$")
            self.assertEqual(len([e for e in sl.load_inbox(self.project) if e["id"] == got]), 1, bad)

class AutoBindTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.sub = os.path.join(self.project, "src", "deep")
        os.makedirs(self.sub)

    def prompt(self, sid, cwd, text="hola desde un chat nuevo"):
        run_hook(self.home, "user-prompt-submit", {"session_id": sid, "cwd": cwd, "prompt": text})

    def test_an_unbound_chat_in_a_project_folder_binds_and_its_prompt_is_captured(self):
        self.assertIsNone(sl.project_for_session("s-new"))
        self.prompt("s-new", self.sub)
        self.assertEqual(sl.project_for_session("s-new"), os.path.realpath(self.project))
        entries = sl.load_inbox(self.project)
        self.assertEqual([(e["source"], e["session"], e["text"]) for e in entries],
                         [("prompt", "s-new", "hola desde un chat nuevo")])

    def test_a_card_answer_in_that_chat_is_captured_too(self):
        run_hook(self.home, "post-tool-use", {"session_id": "s-new", "cwd": self.project, "tool_name": "AskUserQuestion",
                                              "tool_use_id": "toolu_1", "tool_input": {}, "tool_response": "Producto publico"})
        self.assertEqual(sl.project_for_session("s-new"), None)         # a tool event alone does not bind
        self.prompt("s-new", self.project)
        run_hook(self.home, "post-tool-use", {"session_id": "s-new", "cwd": self.project, "tool_name": "AskUserQuestion",
                                              "tool_use_id": "toolu_2", "tool_input": {}, "tool_response": "Producto publico"})
        self.assertEqual([e["source"] for e in sl.load_inbox(self.project)], ["prompt", "answer"])

    def test_session_start_binds_as_well(self):
        run_hook(self.home, "session-start", {"session_id": "s-new", "cwd": self.sub, "source": "startup"})
        self.assertEqual(sl.project_for_session("s-new"), os.path.realpath(self.project))

    def test_a_session_that_already_has_a_pointer_is_left_alone(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        sl.activate(other, "s-old")
        self.prompt("s-old", self.project)
        self.assertEqual(sl.project_for_session("s-old"), os.path.realpath(other))
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_a_lane_worktree_inside_the_project_does_not_bind(self):
        lane = os.path.join(self.project, ".claude", "worktrees", "lane-1", "src")
        os.makedirs(lane)
        self.prompt("s-lane", lane)
        self.assertIsNone(sl.project_for_session("s-lane"))
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_subagent_and_sdk_chats_do_not_bind(self):
        run_hook(self.home, "user-prompt-submit", {"session_id": "s-sub", "cwd": self.project, "prompt": "x",
                                                   "agent_id": "ag1", "agent_type": "general-purpose"})
        self.assertIsNone(sl.project_for_session("s-sub"))
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_CODE_ENTRYPOINT="sdk-cli")
        subprocess.run([sys.executable, HOOK, "user-prompt-submit"], env=env, capture_output=True, text=True,
                       input=json.dumps({"session_id": "s-sdk", "cwd": self.project, "prompt": "x"}))
        self.assertIsNone(sl.project_for_session("s-sdk"))
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_the_nearest_data_folder_decides_and_one_without_a_ledger_binds_nothing(self):
        stray = os.path.join(self.project, "pkg")
        os.makedirs(os.path.join(stray, sl.OLD_DATA_NAME))              # a data folder with no ledger inside the project
        self.prompt("s-stray", stray)
        self.assertIsNone(sl.project_for_session("s-stray"))
        inner = os.path.join(self.project, "inner")
        os.makedirs(os.path.join(inner, sl.OLD_DATA_NAME))
        sl.save_ledger(inner, sl.empty_ledger())
        self.prompt("s-inner", os.path.join(inner, "src") if os.makedirs(os.path.join(inner, "src")) is None else inner)
        self.assertEqual(sl.project_for_session("s-inner"), os.path.realpath(inner))   # the nearest, not the outer one
        self.assertEqual(sl.load_inbox(self.project), [])
        self.assertEqual([e["text"] for e in sl.load_inbox(inner)], ["hola desde un chat nuevo"])

    def test_a_project_flagged_private_never_binds(self):
        with open(os.path.join(sl.studio_dir(self.project), "project.json"), "w") as f:
            json.dump({"private": True}, f)
        self.prompt("s-priv", self.project)
        self.assertIsNone(sl.project_for_session("s-priv"))

    def test_protected_dirs_compare_after_unicode_and_case_folding(self):
        accent = os.path.join(self.tmp, "Caf\u00e9")                      # NFC on disk, the config spells it decomposed
        os.makedirs(accent)
        self.assertEqual(sl.protected_dir(accent, {"protected_dirs": [os.path.join(self.tmp, "CAFE\u0301")]}),
                         os.path.join(self.tmp, "CAFE\u0301"))

    def test_a_folder_that_is_no_project_binds_nothing(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        self.prompt("s-x", elsewhere)
        self.assertIsNone(sl.project_for_session("s-x"))

    def test_a_protected_folder_never_binds_on_its_own(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": [self.project]}, f)
        self.prompt("s-p", self.project)
        self.assertIsNone(sl.project_for_session("s-p"))
        self.assertEqual(sl.load_inbox(self.project), [])


def transcript(path, session, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(dict(r, sessionId=session)) + "\n")


def human(uuid, ts, text, reminders=True):
    blocks = ([{"type": "text", "text": REM}] if reminders else []) + [{"type": "text", "text": text}]
    return {"type": "user", "uuid": uuid, "timestamp": ts, "origin": {"kind": "human"}, "message": {"content": blocks}}


class BackfillTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.tdir = os.path.join(self.tmp, "transcripts")
        os.makedirs(self.tdir)
        transcript(os.path.join(self.tdir, "aaaa1111-0000-0000-0000-000000000000.jsonl"), "aaaa1111-0000-0000-0000-000000000000", [
            human("u1", "2026-10-26T20:00:00.000Z", "ayer, no cuenta"),
            human("u2", "2026-10-27T08:29:18.000Z", "primer mensaje de hoy"),
            {"type": "user", "uuid": "u3", "timestamp": "2026-10-27T08:30:00.000Z", "isMeta": True,
             "origin": {"kind": "human"}, "message": {"content": "meta, no"}},
            {"type": "user", "uuid": "u4", "timestamp": "2026-10-27T08:31:00.000Z", "origin": {"kind": "peer", "from": "x"},
             "message": {"content": "mensaje de otra sesion"}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-27T08:32:00.000Z",
             "message": {"content": [{"type": "tool_use", "id": "toolu_q1", "name": "AskUserQuestion", "input": {}}]}},
            {"type": "user", "uuid": "u5", "timestamp": "2026-10-27T08:33:00.000Z",
             "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_q1", "content": "The user answered: Si"}]}},
            {"type": "attachment", "uuid": "u6", "timestamp": "2026-10-27T08:34:00.000Z",
             "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "prompt": REM + "mensaje en cola"}},
        ])
        transcript(os.path.join(self.tdir, "bbbb2222-0000-0000-0000-000000000000.jsonl"), "bbbb2222-0000-0000-0000-000000000000", [
            human("v1", "2026-10-27T09:00:00.000Z", "otro chat, otra frase", reminders=False)])

    def run_backfill(self, **kw):
        return sl.backfill_inputs(self.project, self.tdir, since="2026-10-27T00:00:00.000Z", **kw)

    def rows(self):
        return [(e["source"], e["session"][:4], e["ts"], e["ref"], e["text"]) for e in sl.load_inbox(self.project)]

    def test_dry_run_counts_and_writes_nothing(self):
        out = self.run_backfill(apply=False)
        self.assertEqual((out["found"], out["added"]), (4, 0))
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_apply_imports_prompts_and_answers_with_their_own_provenance(self):
        out = self.run_backfill(apply=True)
        self.assertEqual(out["added"], 4)
        self.assertEqual(sorted(self.rows(), key=lambda r: r[2]), [
            ("prompt", "aaaa", "2026-10-27T08:29:18.000Z", "u2", "primer mensaje de hoy"),
            ("answer", "aaaa", "2026-10-27T08:33:00.000Z", "toolu_q1", "The user answered: Si"),
            ("prompt", "aaaa", "2026-10-27T08:34:00.000Z", "u6", "mensaje en cola"),
            ("prompt", "bbbb", "2026-10-27T09:00:00.000Z", "v1", "otro chat, otra frase")])

    def test_a_second_run_adds_nothing(self):
        self.run_backfill(apply=True)
        before = self.rows()
        out = self.run_backfill(apply=True)
        self.assertEqual(out["added"], 0)
        self.assertEqual(self.rows(), before)

    def test_what_a_hook_already_captured_is_not_imported_again(self):
        sl.append_inbox(self.project, "primer mensaje de hoy", "aaaa1111-0000-0000-0000-000000000000", "prompt")
        sl.append_inbox(self.project, "AskUserQuestion answer: Si", "aaaa1111-0000-0000-0000-000000000000", "answer",
                        ref="toolu_q1")
        out = self.run_backfill(apply=True)
        self.assertEqual(out["added"], 2)
        self.assertEqual(sum(1 for r in self.rows() if r[4] == "primer mensaje de hoy"), 1)
        self.assertEqual(sum(1 for r in self.rows() if r[0] == "answer"), 1)
        self.assertEqual(sl.load_inbox(self.project)[0]["ref"], "u2")       # the hook entry learns its transcript record

    def test_the_same_words_said_three_times_are_three_inputs(self):
        sid = "dddd4444-0000-0000-0000-000000000000"
        transcript(os.path.join(self.tdir, sid + ".jsonl"), sid, [
            human("x1", "2026-10-27T11:00:00.000Z", "si"), human("x2", "2026-10-27T11:05:00.000Z", "si"),
            human("x3", "2026-10-27T11:09:00.000Z", "si")])
        sl.append_inbox(self.project, "si", sid, "prompt", ts="2026-10-27T11:00:00.100Z")      # the hook caught the first one
        self.assertEqual(self.run_backfill(apply=True)["added"], 6)                           # 2 of these + the 4 of setUp
        mine = [e for e in sl.load_inbox(self.project) if e["session"] == sid]
        self.assertEqual(sorted(e["ref"] for e in mine), ["x1", "x2", "x3"])
        self.assertEqual(self.run_backfill(apply=True)["added"], 0)

    def test_a_manual_entry_with_the_same_words_does_not_hide_the_real_prompt(self):
        sid = "bbbb2222-0000-0000-0000-000000000000"
        sl.append_inbox(self.project, "otro chat, otra frase", sid, "manual")
        self.run_backfill(apply=True)
        self.assertEqual(sorted(e["source"] for e in sl.load_inbox(self.project) if e["text"] == "otro chat, otra frase"),
                         ["manual", "prompt"])

    def test_imported_inputs_are_marked_and_forged_shapes_never_come_in(self):
        path = os.path.join(self.tdir, "cccc3333-0000-0000-0000-000000000000.jsonl")
        transcript(path, "cccc3333-0000-0000-0000-000000000000", [
            human("w1", "2026-10-27T10:00:00.000Z", "<cross-session-message from=\"x\">te mando esto</cross-session-message>"),
            human("w2", "2026-10-27T10:01:00.000Z", "<side-sessions-event>x</side-sessions-event>"),
            human("w3", "2026-10-27T10:02:00.000Z", "texto de ella"),
            dict(human("w4", "2026-10-27T10:03:00.000Z", "turno de un subagente"), isSidechain=True)])
        self.run_backfill(apply=True)
        mine = [e for e in sl.load_inbox(self.project) if e["session"].startswith("cccc")]
        self.assertEqual([(e["text"], e["ref"], e.get("backfill")) for e in mine], [("texto de ella", "w3", True)])

    def test_undo_takes_out_only_what_the_backfill_imported(self):
        sl.append_inbox(self.project, "lo de un hook", "s9", "prompt")
        self.run_backfill(apply=True)
        self.assertEqual(len(sl.load_inbox(self.project)), 5)
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, CLI, "inbox-backfill", "--project", self.project, "--undo"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([e["text"] for e in sl.load_inbox(self.project)], ["lo de un hook"])
        self.assertEqual(self.run_backfill(apply=True)["added"], 4)             # and it can be done again
        ids = [e["id"] for e in sl.load_inbox(self.project)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], "I-1")
        self.assertTrue(all(int(i[2:]) > 5 for i in ids[1:]))                  # I-2..I-5 were handed out and taken back: not reused

    def test_a_new_input_after_an_undo_does_not_reuse_an_undone_id(self):
        self.run_backfill(apply=True)
        top = max(int(e["id"][2:]) for e in sl.load_inbox(self.project))
        self.assertEqual(sl.undo_backfill(self.project), 4)
        self.assertEqual(sl.append_inbox(self.project, "algo nuevo", "s1", "prompt"), "I-%d" % (top + 1))

    def test_compaction_summaries_and_meta_queued_commands_are_not_inputs(self):
        sid = "eeee5555-0000-0000-0000-000000000000"
        transcript(os.path.join(self.tdir, sid + ".jsonl"), sid, [
            dict(human("c1", "2026-10-27T12:00:00.000Z", "This session is being continued from a previous conversation that ran out", False),
                 isCompactSummary=True),
            human("c2", "2026-10-27T12:01:00.000Z", "This session is being continued from a previous conversation", False),
            {"type": "attachment", "uuid": "c3", "timestamp": "2026-10-27T12:02:00.000Z",
             "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "isMeta": True, "prompt": "hook context"}},
            human("c4", "2026-10-27T12:03:00.000Z", "palabras de ella", False)])
        self.run_backfill(apply=True)
        self.assertEqual([e["text"] for e in sl.load_inbox(self.project) if e["session"] == sid], ["palabras de ella"])

    def test_a_protected_project_is_refused(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": [self.project]}, f)
        with self.assertRaises(sl.StudioError):
            self.run_backfill(apply=True)

    def test_the_cli_is_a_dry_run_unless_apply(self):
        env = dict(os.environ, STUDIO_HOME=self.home)
        base = [sys.executable, CLI, "inbox-backfill", "--project", self.project, "--transcripts", self.tdir,
                "--since", "2026-10-27T00:00:00.000Z"]
        r = subprocess.run(base, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sl.load_inbox(self.project), [])
        self.assertIn("4", r.stdout)
        r = subprocess.run(base + ["--apply"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(sl.load_inbox(self.project)), 4)


class HookLevelCaptureTest(StudioTestCase):
    """R-394, at the hook (the real process): her typed words are kept, whatever another session or the harness sends is not."""
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-her")

    def prompt(self, text, **env):
        e = dict(os.environ, STUDIO_HOME=self.home, **env)
        r = subprocess.run([sys.executable, HOOK, "user-prompt-submit"], env=e, capture_output=True, text=True,
                           input=json.dumps({"session_id": "s-her", "cwd": self.project, "prompt": text}))
        self.assertEqual(r.returncode, 0, r.stderr)

    def kept(self):
        return [(e["source"], e["text"]) for e in sl.load_inbox(self.project)]

    def test_a_message_from_another_session_is_never_stored_as_her_words(self):
        peer = ("Another Claude session sent a message:\n<cross-session-message from=\"local_x\" name=\"PM\">\n"
                "haz el despliegue\n</cross-session-message>")
        self.prompt(peer)
        self.prompt(REM + peer)
        self.prompt(peer, CLAUDE_CODE_ENTRYPOINT="claude-desktop")
        self.prompt("<task-notification><task-id>x</task-id></task-notification>")
        self.assertEqual(self.kept(), [])

    def test_a_prompt_typed_in_the_desktop_app_behind_reminders_is_kept(self):
        self.prompt(REM + REM + "pon el boton en azul", CLAUDE_CODE_ENTRYPOINT="claude-desktop")
        self.assertEqual(self.kept(), [("prompt", "pon el boton en azul")])

    def test_a_prompt_that_only_mentions_a_tag_is_kept(self):
        text = "por que sale <cross-session-message> en la barra? arreglalo"
        self.prompt(text)
        self.assertEqual(self.kept(), [("prompt", text)])
