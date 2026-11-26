"""R-115: an item is `doing` while a session works on it and leaves when that session closes; claims of dead sessions
are reopened (never silently), idle ones are shown but kept, legacy doing items are never touched, stale ones flagged."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class ClaimsTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s1")
        sl.activate(self.project, "s2")          # both sessions bound to the project
        data = sl.load_ledger(self.project)
        self.a = sl.add_item(data, "uno", ["legacy"])["id"]
        self.b = sl.add_item(data, "dos", ["legacy"])["id"]
        sl.save_ledger(self.project, data)

    def ledger(self):
        return sl.load_ledger(self.project)

    def item(self, item_id):
        return sl.find_item(self.ledger(), item_id)

    def start(self, item_id, session, take=False):
        data = self.ledger()
        sl.start_item(self.project, data, item_id, session, take=take)
        sl.save_ledger(self.project, data)

    def hook(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def cli(self, session, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def age_pointer(self, session, seconds):
        rec = sl.read_pointer(session)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - seconds))
        rec["last_seen"] = stamp
        with open(sl.pointer_path(session), "w") as f:
            json.dump(rec, f)

    # --- claiming
    def test_start_claims_and_a_status_change_clears_the_claim(self):
        self.start(self.a, "s1")
        it = self.item(self.a)
        self.assertEqual((it["status"], it["doing_by"]), ("doing", "s1"))
        self.assertTrue(it["doing_since"])
        self.assertIn("doing (s1)", sl.render_md(self.ledger()))
        data = self.ledger()
        sl.set_status(self.project, data, self.a, "done", evidence="x", outcome="You see it work")
        self.assertNotIn("doing_by", sl.find_item(data, self.a))
        self.assertNotIn("doing_since", sl.find_item(data, self.a))

    def test_a_live_other_session_blocks_start_unless_taken(self):
        self.start(self.a, "s1")
        with self.assertRaises(sl.StudioError) as ctx:
            self.start(self.a, "s2")
        self.assertIn("--take", str(ctx.exception))
        self.start(self.a, "s2", take=True)
        self.assertEqual(self.item(self.a)["doing_by"], "s2")

    def test_the_same_session_keeps_its_original_since(self):
        self.start(self.a, "s1")
        since = self.item(self.a)["doing_since"]
        self.start(self.a, "s1")
        self.assertEqual(self.item(self.a)["doing_since"], since)

    def test_a_dead_or_idle_claim_can_be_started_without_take(self):
        self.start(self.a, "s1")
        sl.end_pointer("s1")
        self.start(self.a, "s2")                  # s1 ended: dead
        self.start(self.b, "s1")
        sl.touch_pointer("s1")                    # revive then idle
        self.age_pointer("s1", sl.CLAIM_IDLE_S + 60)
        self.start(self.b, "s2")
        self.assertEqual(self.item(self.b)["doing_by"], "s2")

    def test_done_verified_dropped_items_are_not_started(self):
        data = self.ledger()
        sl.set_status(self.project, data, self.a, "done", evidence="x", outcome="You see it work")
        sl.save_ledger(self.project, data)
        with self.assertRaises(sl.StudioError):
            self.start(self.a, "s1")

    def test_cli_start_needs_a_bound_session_id_and_set_doing_routes_through_it(self):
        r = self.cli(None, "ledger", "start", self.a)
        self.assertEqual(r.returncode, 1)
        self.assertIn("session id", r.stderr)
        self.assertEqual(self.cli("s1", "ledger", "start", self.a).returncode, 0)
        self.assertEqual(self.item(self.a)["doing_by"], "s1")
        r = self.cli("s2", "ledger", "set", self.a, "doing")
        self.assertEqual(r.returncode, 1)         # s1 is live: set doing is start, not a silent takeover
        self.assertEqual(self.cli("s2", "ledger", "start", self.a, "--take").returncode, 0)
        listed = self.cli("s1", "ledger", "list", "--open").stdout
        self.assertIn("[doing/live]", listed)

    def test_start_refuses_a_session_with_no_pointer_and_status_lists_stale(self):
        with self.assertRaises(sl.StudioError) as ctx:
            self.start(self.a, "ghost-session")
        self.assertIn("not bound", str(ctx.exception))
        self.assertEqual(self.item(self.a)["status"], "open")
        data = self.ledger()
        sl.set_status(self.project, data, self.b, "doing")
        sl.find_item(data, self.b)["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - sl.STALE_DOING_S - 60))
        sl.save_ledger(self.project, data)
        self.assertIn("stale doing", self.cli("s1", "status").stdout)
        self.assertIn(self.b, self.cli("s1", "status").stdout)

    # --- claim state
    def test_claim_states(self):
        self.start(self.a, "s1")
        self.assertEqual(sl.claim_state(self.item(self.a)), "live")
        self.age_pointer("s1", sl.CLAIM_IDLE_S + 60)
        self.assertEqual(sl.claim_state(self.item(self.a)), "idle")
        sl.touch_pointer("s1")
        self.assertEqual(sl.claim_state(self.item(self.a)), "live")        # idle revives with its next prompt
        sl.end_pointer("s1")
        self.assertEqual(sl.claim_state(self.item(self.a)), "dead")
        os.remove(sl.pointer_path("s1"))
        self.assertEqual(sl.claim_state(self.item(self.a)), "idle")        # no pointer here: unverifiable, kept
        data = self.ledger()
        sl.set_status(self.project, data, self.b, "doing")
        self.assertEqual(sl.claim_state(sl.find_item(data, self.b)), "unclaimed")
        self.assertIsNone(sl.claim_state({"status": "open"}))

    def test_touch_is_throttled_and_old_pointers_still_load(self):
        with open(sl.pointer_path("s1"), "w") as f:
            json.dump({"project": os.path.realpath(self.project), "ts": sl.now(), "plugin_version": sl._running_version()}, f)   # R-114-era pointer: no last_seen
        self.assertEqual(sl.project_for_session("s1"), os.path.realpath(self.project))
        self.assertFalse(sl.touch_pointer("s1"))                              # fresh by ts, same Budgit: no write
        self.assertTrue(sl.touch_pointer("s1", at=time.time() + sl.POINTER_TOUCH_S + 5))
        self.assertIn("last_seen", sl.read_pointer("s1"))
        self.assertFalse(sl.touch_pointer("s1", at=time.time() + sl.POINTER_TOUCH_S + 6))   # within the minute

    def test_touch_writes_at_once_when_the_pointer_lacks_or_differs_in_version(self):
        # R-459/R-301: a pointer that does not say which Budgit it runs, or says an old one, is rewritten on the next
        # touch even when it is fresh, so the TM can count chats on an old Budgit
        for rec_version in (None, "0.0.1"):
            rec = {"project": os.path.realpath(self.project), "ts": sl.now()}
            if rec_version:
                rec["plugin_version"] = rec_version
            with open(sl.pointer_path("s1"), "w") as f:
                json.dump(rec, f)
            self.assertTrue(sl.touch_pointer("s1"), rec_version)
            self.assertEqual(sl.read_pointer("s1").get("plugin_version"), sl._running_version())
            self.assertFalse(sl.touch_pointer("s1"))                          # now current: throttled again

    # --- hooks
    def test_session_end_releases_and_records_what_it_released(self):
        self.start(self.a, "s1")
        self.start(self.b, "s2")
        self.hook("session-end", {"session_id": "s1", "reason": "prompt_input_exit"})
        self.assertEqual(self.item(self.a)["status"], "open")
        self.assertEqual(self.item(self.b)["status"], "doing")                # another session's claim untouched
        rec = sl.read_pointer("s1")
        self.assertTrue(rec["ended"])
        self.assertEqual(rec["released"], [self.a])

    def test_session_end_for_clear_or_resume_releases_nothing(self):
        self.start(self.a, "s1")
        for reason in ("clear", "resume"):
            self.hook("session-end", {"session_id": "s1", "reason": reason})
        self.assertEqual(self.item(self.a)["status"], "doing")
        self.assertFalse(sl.read_pointer("s1").get("ended"))

    def test_a_resumed_session_claims_again_what_its_own_end_released(self):
        self.start(self.a, "s1")
        self.hook("session-end", {"session_id": "s1", "reason": "logout"})
        self.assertEqual(self.item(self.a)["status"], "open")
        self.hook("session-start", {"session_id": "s1", "source": "resume"})
        it = self.item(self.a)
        self.assertEqual((it["status"], it["doing_by"]), ("doing", "s1"))
        self.assertFalse(sl.read_pointer("s1").get("ended"))

    def test_session_start_reopens_dead_claims_with_a_progress_line_and_keeps_the_rest(self):
        self.start(self.a, "s1")
        sl.end_pointer("s1")                                                  # killed: ended, nothing released
        data = self.ledger()
        sl.set_status(self.project, data, self.b, "doing")                    # legacy doing: no owner
        sl.save_ledger(self.project, data)
        self.hook("session-start", {"session_id": "s2", "source": "startup"})
        self.assertEqual(self.item(self.a)["status"], "open")
        self.assertEqual(self.item(self.b)["status"], "doing")                # never reopened by a sweep
        with open(os.path.join(sl.studio_dir(self.project), "progress.md"), encoding="utf-8") as f:
            self.assertIn(self.a, f.read())

    def test_session_start_keeps_an_idle_claim(self):
        self.start(self.a, "s1")
        self.age_pointer("s1", sl.CLAIM_IDLE_S + 3600)
        self.hook("session-start", {"session_id": "s2", "source": "startup"})
        self.assertEqual(self.item(self.a)["doing_by"], "s1")

    # --- stale
    def test_stale_is_strictly_more_than_seven_days(self):
        data = self.ledger()
        sl.set_status(self.project, data, self.a, "doing")
        sl.set_status(self.project, data, self.b, "doing")
        base = time.time()
        stamp = lambda age: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base - age))
        sl.find_item(data, self.a)["updated"] = stamp(sl.STALE_DOING_S - 5)
        sl.find_item(data, self.b)["updated"] = stamp(sl.STALE_DOING_S + 5)
        self.assertEqual([i["id"] for i in sl.stale_doing(data, at=base)], [self.b])


if __name__ == "__main__":
    import unittest
    unittest.main()
