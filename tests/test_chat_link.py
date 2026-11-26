"""R-239: `budgit chat-link <prefix>` turns the 12-character session prefix the Brote channel carries into the link of that chat in the
desktop app. Read-only: it reads the app's own session records fresh on every call, prints one link or nothing, opens and writes nothing."""
import json
import os
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
CLI_ID = "0123456789ab-cdef-0123-4567-89abcdef0123"
LOCAL = "local_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class ChatLinkTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.sessions = os.path.join(self.tmp, "Library", "Application Support", "Claude", "claude-code-sessions", "org", "acct")
        os.makedirs(self.sessions)

    def record(self, name, **body):
        path = os.path.join(self.sessions, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f)
        return path

    def link(self, prefix, lang="en"):
        env = dict(os.environ, HOME=self.tmp, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, "chat-link", prefix], cwd=self.tmp, env=env, capture_output=True, text=True)

    def test_one_match_prints_the_epitaxy_link_and_nothing_else(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID, title="x")
        r = self.link("0123456789ab")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "claude://claude.ai/epitaxy/%s\n" % LOCAL, ""))

    def test_no_match_prints_nothing_and_exits_one(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID)
        r = self.link("ffffffffffff")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertNotIn("Traceback", r.stderr)

    def test_two_matches_are_not_guessed(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID)
        other = "local_11111111-2222-3333-4444-555555555555"
        self.record(other, sessionId=other, cliSessionId=CLI_ID.replace("cdef", "beef"))
        r = self.link("0123456789ab")
        self.assertEqual((r.returncode, r.stdout), (1, ""))

    def test_an_archived_chat_does_not_count(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID, isArchived=True)
        self.assertEqual(self.link("0123456789ab").returncode, 1)

    def test_an_archived_twin_does_not_make_the_live_one_ambiguous(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID)
        other = "local_11111111-2222-3333-4444-555555555555"
        self.record(other, sessionId=other, cliSessionId=CLI_ID, isArchived=True)
        self.assertEqual(self.link("0123456789ab").stdout, "claude://claude.ai/epitaxy/%s\n" % LOCAL)

    def test_a_session_id_of_the_wrong_shape_never_becomes_a_link(self):
        for bad in ("local_../../x", "local_AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", "x" + LOCAL, LOCAL + "/..", "local_aaaa"):
            self.record(LOCAL, sessionId=bad, cliSessionId=CLI_ID)
            r = self.link("0123456789ab")
            self.assertEqual((r.returncode, r.stdout), (1, ""), bad)

    def test_a_prefix_that_is_too_short_or_odd_is_refused_without_reading_anything(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID)
        for bad in ("0123456", "", "0123456789ab; touch x", "../..", "0123456789ab\x1b[31m", "a" * 37):
            r = self.link(bad)
            self.assertNotEqual(r.returncode, 0, repr(bad))
            self.assertEqual(r.stdout, "")
            self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x")))

    def test_junk_records_are_skipped(self):
        with open(os.path.join(self.sessions, "local_junk.json"), "w") as f:
            f.write("{broken")
        with open(os.path.join(self.sessions, "local_big.json"), "w") as f:
            f.write(json.dumps({"sessionId": LOCAL, "cliSessionId": CLI_ID, "pad": "x" * 70000}))
        with open(os.path.join(self.sessions, "local_list.json"), "w") as f:
            json.dump(["not", "a", "dict"], f)
        self.record("notlocal", sessionId=LOCAL, cliSessionId=CLI_ID)
        r = self.link("0123456789ab")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertNotIn("Traceback", r.stderr)

    def test_the_record_is_read_fresh_on_every_call(self):
        path = self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID)
        self.assertEqual(self.link("0123456789ab").returncode, 0)
        with open(path, "w") as f:
            json.dump({"sessionId": LOCAL, "cliSessionId": "ffffffffffff-cdef-0123-4567-89abcdef0123"}, f)
        self.assertEqual(self.link("0123456789ab").returncode, 1)
        self.assertEqual(self.link("ffffffffffff").returncode, 0)

    def test_it_writes_nothing(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID)
        before = sorted(os.listdir(self.sessions))
        self.link("0123456789ab")
        self.assertEqual(sorted(os.listdir(self.sessions)), before)

    def test_without_the_app_folder_it_just_finds_nothing(self):
        import shutil
        shutil.rmtree(os.path.join(self.tmp, "Library"))
        r = self.link("0123456789ab")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertNotIn("Traceback", r.stderr)

    def test_a_missing_or_unreadable_layout_fails_closed_with_one_line_and_no_paths(self):
        import shutil
        shutil.rmtree(os.path.join(self.tmp, "Library"))
        r = self.link("0123456789ab")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertNotIn("Library", r.stderr)
        self.assertNotIn(self.tmp, r.stderr)

    def test_records_without_the_keys_we_read_say_so(self):
        self.record(LOCAL, other="x")                       # the app renamed its fields
        r = self.link("0123456789ab")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)

    def test_a_healthy_layout_with_no_match_stays_silent(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID)
        r = self.link("ffffffffffff")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "", ""))

    def test_nothing_but_the_link_ever_reaches_the_output(self):
        self.record(LOCAL, sessionId=LOCAL, cliSessionId=CLI_ID, title="SECRET TITLE", transcript="SECRET TEXT")
        r = self.link("0123456789ab")
        self.assertNotIn("SECRET", r.stdout + r.stderr)
        self.assertNotIn(self.sessions, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
