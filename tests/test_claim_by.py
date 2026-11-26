"""doing_by names the lane that builds the item: `ledger start R-n --by <session name or id>` claims it for that
session (the PM can hand it to another with --take). When that session ends, or is not seen for longer than a stale
doing item (STALE_DOING_S), its doing items go back to open with a note saying why."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class ClaimByCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        for sid in ("s-pm", "s-lane1", "s-lane2"):
            sl.activate(self.project, sid)
        data = sl.load_ledger(self.project)
        self.a = sl.add_item(data, "uno", ["legacy"])["id"]
        self.b = sl.add_item(data, "dos", ["legacy"])["id"]
        sl.save_ledger(self.project, data)
        self.cfg = os.path.join(self.tmp, "claude-config")
        os.makedirs(os.path.join(self.cfg, "sessions"))
        self.n = 0

    def live(self, sid, name):
        """A session file the way Claude Code writes it, for a pid that is alive (this test)."""
        self.n += 1
        with open(os.path.join(self.cfg, "sessions", "%d-%d.json" % (os.getpid(), self.n)), "w") as f:
            json.dump({"pid": os.getpid(), "sessionId": sid, "cwd": self.project, "name": name, "status": "busy",
                       "kind": "interactive"}, f)

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def env(self, session):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_CONFIG_DIR=self.cfg)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return env

    def cli(self, session, *args):
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=self.env(session),
                              capture_output=True, text=True)

    def ok(self, session, *args):
        r = self.cli(session, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def hook(self, event, payload):
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=self.env(None),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def age_pointer(self, session, seconds):
        rec = sl.read_pointer(session)
        rec["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - seconds))
        with open(sl.pointer_path(session), "w") as f:
            json.dump(rec, f)


class StartByTest(ClaimByCase):
    def test_the_pm_claims_an_item_for_a_lane_by_id(self):
        self.ok("s-pm", "ledger", "start", self.a, "--by", "s-lane1")
        it = self.item(self.a)
        self.assertEqual((it["status"], it["doing_by"], it["assigned_by"]), ("doing", "s-lane1", "s-pm"))
        self.assertIn("s-lane1", self.ok("s-pm", "ledger", "show", self.a))

    def test_a_lane_is_found_by_its_name(self):
        self.live("s-lane2", "Lane · task ranking")
        self.ok("s-pm", "ledger", "start", self.a, "--by", "lane · TASK ranking")
        self.assertEqual(self.item(self.a)["doing_by"], "s-lane2")

    def test_an_unknown_or_ambiguous_name_claims_nothing(self):
        self.live("s-lane1", "Lane")
        self.live("s-lane2", "Lane")
        for who in ("nobody here", "Lane"):
            r = self.cli("s-pm", "ledger", "start", self.a, "--by", who)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.item(self.a)["status"], "open")

    def test_the_pm_reassigns_with_take(self):
        self.ok("s-pm", "ledger", "start", self.a, "--by", "s-lane1")
        r = self.cli("s-pm", "ledger", "start", self.a, "--by", "s-lane2")
        self.assertNotEqual(r.returncode, 0)                          # lane1 is live: no silent steal
        self.assertIn("--take", r.stderr)
        self.ok("s-pm", "ledger", "start", self.a, "--by", "s-lane2", "--take")
        self.assertEqual(self.item(self.a)["doing_by"], "s-lane2")
        with open(os.path.join(sl.studio_dir(self.project), "audit.log"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"%s .*s-lane1.*s-lane2" % self.a)

    def test_without_by_the_caller_claims_it_as_before(self):
        self.ok("s-lane1", "ledger", "start", self.a)
        it = self.item(self.a)
        self.assertEqual(it["doing_by"], "s-lane1")
        self.assertNotIn("assigned_by", it)


class ReleaseTest(ClaimByCase):
    def test_the_lane_ending_reopens_its_items_with_a_note(self):
        self.ok("s-pm", "ledger", "start", self.a, "--by", "s-lane1")
        self.ok("s-pm", "ledger", "start", self.b, "--by", "s-lane2")
        self.hook("session-end", {"session_id": "s-lane1", "reason": "other", "cwd": self.project})
        it = self.item(self.a)
        self.assertEqual(it["status"], "open")
        self.assertEqual((it["released"]["by"], it["released"]["why"]), ("s-lane1", "session ended"))
        self.assertEqual(self.item(self.b)["status"], "doing")
        self.assertIn("session ended", self.ok("s-pm", "ledger", "show", self.a))

    def test_a_lane_not_seen_past_the_stale_threshold_is_released_at_the_next_session_start(self):
        self.ok("s-pm", "ledger", "start", self.a, "--by", "s-lane1")
        self.ok("s-pm", "ledger", "start", self.b, "--by", "s-lane2")
        self.age_pointer("s-lane1", sl.STALE_DOING_S + 60)
        self.age_pointer("s-lane2", sl.CLAIM_IDLE_S + 60)            # idle only: kept
        self.hook("session-start", {"session_id": "s-pm", "source": "startup", "cwd": self.project})
        it = self.item(self.a)
        self.assertEqual(it["status"], "open")
        self.assertEqual(it["released"]["by"], "s-lane1")
        self.assertIn("not seen", it["released"]["why"])
        self.assertEqual(self.item(self.b)["doing_by"], "s-lane2")
        with open(os.path.join(sl.studio_dir(self.project), "progress.md"), encoding="utf-8") as f:
            self.assertIn(self.a, f.read())

    def test_starting_again_clears_the_note(self):
        self.ok("s-pm", "ledger", "start", self.a, "--by", "s-lane1")
        self.hook("session-end", {"session_id": "s-lane1", "reason": "other", "cwd": self.project})
        self.ok("s-lane2", "ledger", "start", self.a)
        self.assertNotIn("released", self.item(self.a))
