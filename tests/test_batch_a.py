"""Batch A (0.13.3): R-477a a claim nobody touched for a day goes back to open, even when its chat is alive;
R-297 a chat that waited over 4 h on the user can be handed over and archived by the keep-alive (the data it needs:
`status --waiting --over`, the app's id of the chat, `handoff write --session`). Fake names only."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT, protect
from test_waiting_on_user import WaitCase
import chatlink
import livework
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
CLI_ID = "01234567-89ab-cdef-0123-456789abcdef"
LOCAL = "local_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def stamp(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - seconds_ago))


class ClaimExpiryTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-live")
        data = sl.load_ledger(self.project)
        self.a = sl.add_item(data, "uno", ["legacy"])["id"]
        self.b = sl.add_item(data, "dos", ["legacy"])["id"]
        sl.save_ledger(self.project, data)

    def claim(self, item_id, touched_ago):
        data = sl.load_ledger(self.project)
        sl.start_item(self.project, data, item_id, "s-live")
        item = sl.find_item(data, item_id)
        item["updated"] = item["doing_since"] = stamp(touched_ago)
        sl.save_ledger(self.project, data)

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def sweep(self):
        data = sl.load_ledger(self.project)
        gone = sl.sweep_dead_claims(self.project, data)
        sl.save_ledger(self.project, data)
        return gone

    def test_a_claim_untouched_for_over_a_day_goes_back_to_open_with_a_note_even_if_its_chat_is_live(self):
        self.claim(self.a, sl.CLAIM_TOUCH_S + 60)
        self.assertEqual(sl.claim_state(self.item(self.a)), "live")
        self.assertEqual(self.sweep(), [self.a])
        item = self.item(self.a)
        self.assertEqual(item["status"], "open")
        self.assertEqual(item["released"]["by"], "s-live")
        self.assertIn("not touched for 24 hours", item["released"]["why"])

    def test_a_claim_touched_within_the_day_stays(self):
        self.claim(self.a, sl.CLAIM_TOUCH_S - 600)
        self.assertEqual(self.sweep(), [])
        self.assertEqual(self.item(self.a)["status"], "doing")

    def test_starting_the_item_again_renews_the_claim(self):
        self.claim(self.a, sl.CLAIM_TOUCH_S + 60)
        data = sl.load_ledger(self.project)
        sl.start_item(self.project, data, self.a, "s-live")
        sl.save_ledger(self.project, data)
        self.assertEqual(self.sweep(), [])
        self.assertEqual(self.item(self.a)["doing_by"], "s-live")

    def test_an_old_item_with_no_owner_is_left_alone(self):
        data = sl.load_ledger(self.project)
        sl.set_status(self.project, data, self.b, "doing")
        sl.find_item(data, self.b).pop("doing_by", None)
        sl.find_item(data, self.b)["updated"] = stamp(3 * sl.CLAIM_TOUCH_S)
        sl.save_ledger(self.project, data)
        self.assertEqual(self.sweep(), [])
        self.assertEqual(self.item(self.b)["status"], "doing")

    def prompt_out(self, session):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, "user-prompt-submit"], env=env, capture_output=True, text=True,
                           input=json.dumps({"session_id": session, "cwd": self.project, "prompt": "sigue"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_owner_is_told_when_its_own_claim_is_released_and_nobody_else_is(self):
        self.claim(self.a, sl.CLAIM_TOUCH_S + 60)
        sl.activate(self.project, "s-other")
        self.assertNotIn("went back to open", self.prompt_out("s-other"))      # not its claim: no word to it
        self.claim(self.b, sl.CLAIM_TOUCH_S + 60)
        out = self.prompt_out("s-live")
        self.assertIn(self.b, out)
        self.assertIn("ledger start", out)

    def test_the_prompt_hook_expires_it_too_because_a_long_chat_never_starts_again(self):
        self.claim(self.a, sl.CLAIM_TOUCH_S + 60)
        self.claim(self.b, 60)
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, "user-prompt-submit"], env=env, capture_output=True, text=True,
                           input=json.dumps({"session_id": "s-live", "cwd": self.project, "prompt": "sigue con lo tuyo"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.item(self.a)["status"], self.item(self.b)["status"]), ("open", "doing"))
        with open(os.path.join(sl.studio_dir(self.project), "progress.md"), encoding="utf-8") as f:
            self.assertIn(self.a, f.read())


class WaitingHandoverTest(WaitCase):
    def setUp(self):
        super().setUp()
        self.apps = os.path.join(self.tmp, "Library", "Application Support", "Claude", "claude-code-sessions", "org", "acct")
        os.makedirs(self.apps)

    def app_record(self, local, cli):
        with open(os.path.join(self.apps, local + ".json"), "w", encoding="utf-8") as f:
            json.dump({"sessionId": local, "cliSessionId": cli}, f)

    def run_cli(self, *args, project=None):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_CONFIG_DIR=self.cfg, HOME=self.tmp)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", project or self.project], env=env,
                              capture_output=True, text=True)

    def test_local_id_names_the_one_live_chat_for_a_cli_prefix(self):
        self.app_record(LOCAL, CLI_ID)
        self.assertEqual(chatlink.local_id("01234567-89", home=self.tmp), LOCAL)
        self.assertIsNone(chatlink.local_id("ffffffffffff", home=self.tmp))
        self.assertIsNone(chatlink.local_id("../etc", home=self.tmp))

    def test_a_waiting_chat_carries_its_ids_and_a_protected_one_carries_none(self):
        hidden = os.path.join(self.tmp, "cofre")
        os.makedirs(hidden)
        protect(self.home, hidden)
        self.app_record(LOCAL, CLI_ID)
        self.chat(CLI_ID, "Lane A", "waiting", 3600)
        self.chat("s-lane-b", "Secret lane", "waiting", 3600, cwd=hidden)
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        try:
            chats = {c["name"]: c for c in livework.waiting_chats(home=self.tmp)}
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR") if old is None else os.environ.__setitem__("CLAUDE_CONFIG_DIR", old)
        lane = chats["Lane A"]
        self.assertEqual((lane["session"], lane["local_id"], lane["cwd"]), (CLI_ID, LOCAL, self.project))
        secret = chats[None]
        self.assertEqual((secret["session"], secret["local_id"], secret["cwd"], secret["protected"]), (None, None, None, True))

    def test_over_keeps_only_what_has_waited_that_long(self):
        self.ok("ledger", "block", "R-1", "--on", "user", "--question", "vieja")
        self.ok("ledger", "block", "R-2", "--on", "user", "--question", "reciente")
        self.age_block("R-1", 5 * 3600)
        self.age_block("R-2", 600)
        self.chat("s-lane-a", "Old lane", "waiting", 5 * 3600)
        self.chat("s-lane-b", "Fresh lane", "waiting", 1200)
        out = json.loads(self.run_cli("status", "--waiting", "--json", "--over", "4").stdout)
        self.assertEqual([i["id"] for i in out["items"]], ["R-1"])
        self.assertEqual([c["name"] for c in out["chats"]], ["Old lane"])
        every = json.loads(self.run_cli("status", "--waiting", "--json").stdout)
        self.assertEqual(len(every["chats"]), 2)

    def test_over_refuses_a_negative_or_non_finite_number(self):
        for bad in ("-1", "nan", "inf", "abc"):
            r = self.run_cli("status", "--waiting", "--json", "--over", bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.run_cli("status", "--waiting", "--json", "--over", "0").returncode, 0)

    def age_block(self, item_id, seconds):
        data = sl.load_ledger(self.project)
        sl.find_item(data, item_id)["blocked"]["since"] = stamp(seconds)
        sl.save_ledger(self.project, data)

    def test_handoff_write_for_another_chat_lists_the_tasks_that_chat_holds(self):
        sl.activate(self.project, "s-lane-a")
        data = sl.load_ledger(self.project)
        sl.start_item(self.project, data, "R-2", "s-lane-a")
        sl.save_ledger(self.project, data)
        r = self.run_cli("handoff", "write", "--session", "s-lane-a", "--lane", "lane-a")
        self.assertEqual(r.returncode, 0, r.stderr)
        path = os.path.join(sl.studio_dir(self.project), "private", "handoff", "lane-a.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("R-2", text)
        self.assertNotIn("R-3", text)
        with open(os.path.join(os.path.dirname(path), "lane-a.facts.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["session"], "s-lane-a")
