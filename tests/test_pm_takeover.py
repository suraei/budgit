"""R-1343 (reopened): a PM chat that was renewed, cleared or resumed gets a new session id while the portfolio still names the
old one, so the new chat read as a lane and never saw the TM notes. A new session takes the PM role only from an ended PM,
only when it is interactive, top level and in the project's own folder; the first one under the lock wins. Fake names only."""
import json
import os
import threading

import test_tm_notes as tn
import livework
import portfolio
import roles
import studiolib as sl

OLD = "s-pmold001"
NEW = "s-pmnew001"
OTHER = "s-pmnew002"


class TakeoverCase(tn.NoteCase):
    def setUp(self):
        super().setUp()
        self.ok("pm", "huerto", OLD)                 # the recorded PM is a session that is no longer running
        for sid in (NEW, OTHER):
            self.pointer(sid, self.huerto)
            self.session(sid, self.huerto)

    def taken(self):                                 # the line in the language the hook detects from the same environment
        import i18n
        return i18n.t("roles.pm_takeover", old="s-pmold0")

    def row(self):
        return portfolio.find(portfolio.load(), "huerto")

    def start(self, sid=NEW, **payload):
        payload.setdefault("cwd", self.huerto)
        return self.hook("session-start", sid, source=payload.pop("source", "compact"), **payload)

    def prompt_from(self, sid=NEW, **payload):
        payload.setdefault("cwd", self.huerto)
        return self.contexts(self.hook("user-prompt-submit", sid, prompt="hola", **payload))


class TakeoverTest(TakeoverCase):
    def test_new_session_takes_pm_from_ended_pm_and_sees_notes(self):
        self.ok("note", "huerto", "--item", "R-1", "al arrancar")
        out = self.start(source="compact")
        self.assertIn("TM notes (not the user's words): N-1", out)                    # at SessionStart
        self.assertIn(self.taken(), out)
        self.assertIn("budgit team role: PM", out)
        row = self.row()
        self.assertEqual((row["pm"], row["previous_pm"]), (NEW, OLD))
        self.assertTrue(row["previous_pm_at"])
        self.assertTrue(portfolio.load_notes(self.huerto)[0]["read"])
        self.assertEqual([e["text"] for e in sl.load_inbox(self.huerto) if "al arrancar" in e["text"]], [])   # never an I-n
        self.ok("pm", "huerto", OLD)                                                    # a second takeover, this time on a prompt
        self.ok("note", "huerto", "al preguntar")
        text = self.prompt_from(OTHER)
        self.assertIn("TM notes (not the user's words): N-2", text)                      # at the first UserPromptSubmit
        self.assertIn(self.taken(), text)
        self.assertEqual(self.row()["pm"], OTHER)

    def test_the_takeover_line_is_in_spanish_when_the_language_is(self):
        import i18n
        self.assertIn("(ended)", i18n.t("roles.pm_takeover", "en", old="abcd1234"))
        self.assertIn("tomado de abcd1234", i18n.t("roles.pm_takeover", "es", old="abcd1234"))

    def test_no_takeover_from_live_pm(self):
        self.session(OLD, self.huerto)                                                   # the recorded PM is running
        self.ok("note", "huerto", "para el PM vivo")
        self.assertNotIn("TM notes", self.start())
        self.assertNotIn("N-1", self.prompt_from())
        self.assertEqual(self.row()["pm"], OLD)
        self.assertNotIn("previous_pm", self.row())
        self.assertFalse(portfolio.load_notes(self.huerto)[0]["read"])

    def test_no_takeover_from_worktree_or_subagent(self):
        self.ok("note", "huerto", "no para ti")
        tree = os.path.join(self.huerto, ".claude", "worktrees", "rama")
        sub = os.path.join(self.huerto, "docs")
        os.makedirs(tree)
        os.makedirs(sub)
        scratch = os.path.join(self.uhome, "scratch")
        os.makedirs(scratch)
        for cwd in (tree, sub, scratch):
            self.assertNotIn("N-1", self.start(cwd=cwd) + self.prompt_from(cwd=cwd))
        self.assertNotIn("N-1", self.prompt_from(agent_id="a1", agent_type="studio:x"))   # a subagent
        self.assertNotIn("TM notes", self.start(agent_id="a1", agent_type="studio:x"))
        with open(os.path.join(self.bhome, "sessions", NEW + ".json"), "w") as f:         # an explicit parent pointer: a lane
            json.dump({"project": self.huerto, "ts": sl.now(), "last_seen": sl.now(), "parent": OTHER}, f)
        self.assertNotIn("N-1", self.start() + self.prompt_from())
        self.assertEqual(self.row()["pm"], OLD)
        self.assertNotIn("previous_pm", self.row())
        self.assertFalse(portfolio.load_notes(self.huerto)[0]["read"])

    def test_takeover_race_first_wins(self):
        barrier = threading.Barrier(2)
        results = {}

        def go(sid):
            barrier.wait()
            results[sid] = roles.assign(sid, self.huerto, self.huerto)

        threads = [threading.Thread(target=go, args=(sid,)) for sid in (NEW, OTHER)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [sid for sid, r in results.items() if "pm" in r]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(self.row()["pm"], winners[0])
        self.assertEqual(self.row()["previous_pm"], OLD)                                 # the loser did not overwrite the record
        self.assertEqual(roles.assign(OTHER if winners[0] == NEW else NEW, self.huerto, self.huerto), [])
        self.assertEqual(self.row()["pm"], winners[0])

    def test_notes_print_at_session_start_for_every_source(self):
        for n, source in enumerate(("startup", "resume", "compact", "clear"), 1):
            self.ok("pm", "huerto", OLD)                                                 # the recorded PM is gone again
            self.ok("note", "huerto", "fuente %s" % source)
            out = self.start(source=source)
            self.assertIn("TM notes (not the user's words): N-%d" % n, out, source)
            self.assertIn("fuente %s" % source, out)
            self.assertIn(self.taken(), out, source)
            self.assertEqual(self.row()["pm"], NEW, source)
            self.assertTrue(portfolio.load_notes(self.huerto)[-1]["read"], source)       # marked read only because it was printed

    def test_no_takeover_when_liveness_is_unknown(self):
        from unittest import mock
        with mock.patch.object(roles.sys, "platform", "linux"):
            self.assertFalse(roles._liveness_known())
        with mock.patch.object(roles.sys, "platform", "darwin"):
            self.assertTrue(roles._liveness_known())
        self.ok("note", "huerto", "sin saber")
        with mock.patch.object(roles, "_liveness_known", return_value=False):
            done = roles.assign(NEW, self.huerto, self.huerto)
            self.assertNotIn("pm", done)
            self.assertEqual(self.row()["pm"], OLD)                                      # the recorded PM keeps the role
            self.assertNotIn("previous_pm", self.row())
            self.assertIsNone(roles.take_over_now({"session_id": NEW, "cwd": self.huerto}, self.huerto))
            self.assertEqual(self.row()["pm"], OLD)
            _role, parts = roles.session_start_parts({"session_id": NEW, "cwd": self.huerto}, self.huerto)
        texts = [t for _tag, t in parts]
        self.assertTrue([t for t in texts if "budgit portfolio pm <" in t], texts)        # tells how to set the PM by hand
        self.assertEqual(self.row()["pm"], OLD)
        self.assertFalse(portfolio.load_notes(self.huerto)[0]["read"])                   # a lane, not the PM: nothing consumed
        import i18n
        for lang in ("en", "es"):
            self.assertIn("budgit portfolio pm <", i18n.t("roles.pm_manual", lang, old="s-pmold0"))

    def link_worktree(self, folder, name):
        """A folder whose `.git` file names a worktree of the huerto repo, with its own ledger: livework.canon() maps it to huerto."""
        wtdir = os.path.join(self.huerto, ".git", "worktrees", name)
        os.makedirs(wtdir)
        self.mk(folder, [("R-1", "open")])
        with open(os.path.join(folder, ".git"), "w") as f:
            f.write("gitdir: %s\n" % wtdir)
        self.assertEqual(livework.canon(folder), os.path.realpath(self.huerto))    # the mapping the fix must not trust
        return folder

    def assert_kept_out(self, folder):
        self.pointer(NEW, folder)                                                         # the chat is bound to the folder itself
        self.ok("note", "huerto", "no para esta carpeta")
        done = roles.assign(NEW, folder, folder)
        self.assertNotIn("pm", done)
        out = self.start(cwd=folder) + self.prompt_from(cwd=folder)
        self.assertNotIn("N-1", out)
        self.assertNotIn(self.taken(), out)
        self.assertEqual(self.row()["pm"], OLD)
        self.assertNotIn("previous_pm", self.row())
        self.assertFalse(portfolio.load_notes(self.huerto)[0]["read"])

    def test_no_takeover_from_a_real_worktree_layout(self):
        self.assert_kept_out(self.link_worktree(os.path.join(self.uhome, "studio-wt", "huerto-rama"), "rama"))

    def test_no_takeover_from_a_crafted_pointer_folder(self):
        self.assert_kept_out(self.link_worktree(os.path.join(self.uhome, "scratch-crafted"), "cualquiera"))

    def test_unverified_live_record_blocks_takeover(self):
        rec = self.session(OLD, self.huerto, pidDomain=None)                              # alive pid, but the file cannot be verified
        self.assertNotIn("pidDomain", rec)
        listed = [s for s in livework.live_sessions()["sessions"] if s["session_id"] == OLD]
        self.assertEqual([s["verified"] for s in listed], [False])
        self.ok("note", "huerto", "el PM sigue vivo")
        self.assertNotIn("N-1", self.start() + self.prompt_from())
        self.assertEqual(self.row()["pm"], OLD)
        self.assertNotIn("previous_pm", self.row())
        self.assertFalse(portfolio.load_notes(self.huerto)[0]["read"])
