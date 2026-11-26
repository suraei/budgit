"""Blocked on the user is a state: `ledger block R-n --on user --question "..."` (the item keeps its status and its place
in the score), `ledger unblock R-n --answer "..."`, `budgit status --waiting` lists what waits on her, oldest first: the
items and the live chats waiting on one of her prompts (a protected project's chat is counted, never named). The status
line says "waiting on you: N" in her language. A default answer applies after its timeout only when she allowed it in
an input of her own (`--allowed-by I-n`), through `ledger apply-defaults`."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT, protect
import studiolib as sl
import taskrank

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def stamp(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - seconds_ago))


class WaitCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        data = sl.load_ledger(self.project)
        for n in range(1, 5):
            sl.add_item(data, "item %d" % n, ["legacy"], phase="0.13.2")
        sl.save_ledger(self.project, data)
        self.her = sl.append_inbox(self.project, "si no contesto en 4 h, sigue con la opcion A", "s-her", "prompt")
        self.typed = sl.append_inbox(self.project, "added by hand", "s-pm", "manual")
        self.cfg = os.path.join(self.tmp, "claude-config")
        os.makedirs(os.path.join(self.cfg, "sessions"))
        self.n = 0

    def chat(self, sid, name, status, ago, cwd=None):
        self.n += 1
        with open(os.path.join(self.cfg, "sessions", "%d-%d.json" % (os.getpid(), self.n)), "w") as f:
            json.dump({"pid": os.getpid(), "sessionId": sid, "cwd": cwd or self.project, "name": name,
                       "status": status, "statusUpdatedAt": int((time.time() - ago) * 1000),
                       "waitingFor": "permission" if status == "waiting" else None, "kind": "interactive"}, f)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_CONFIG_DIR=self.cfg)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def ok(self, *args):
        r = self.cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def age_block(self, item_id, seconds):
        data = sl.load_ledger(self.project)
        sl.find_item(data, item_id)["blocked"]["since"] = stamp(seconds)
        sl.save_ledger(self.project, data)


class BlockTest(WaitCase):
    def test_block_on_user_keeps_the_status_and_feeds_the_score(self):
        self.ok("ledger", "block", "R-2", "--on", "user", "--question", "¿A o B?")
        it = self.item("R-2")
        self.assertEqual(it["status"], "open")
        self.assertEqual((it["blocked"]["on"], it["blocked"]["question"]), ("user", "¿A o B?"))
        top = taskrank.top(sl.load_ledger(self.project))
        self.assertEqual(top[0]["id"], "R-2")
        self.assertEqual(top[0]["parts"]["waiting"], taskrank.WEIGHTS["waiting"])
        self.assertIn("¿A o B?", self.ok("ledger", "show", "R-2"))

    def test_a_question_is_required_and_on_takes_user_only(self):
        for args in (("--on", "user"), ("--on", "user", "--question", "  "), ("--on", "pm", "--question", "x")):
            self.assertNotEqual(self.cli("ledger", "block", "R-1", *args).returncode, 0, args)
        self.assertNotIn("blocked", self.item("R-1"))
        self.ok("ledger", "block", "R-1", "--reason", "waits on the API key")          # the old block still works
        self.assertNotIn("on", self.item("R-1")["blocked"])

    def test_a_default_needs_her_own_permission_and_a_timeout(self):
        base = ("ledger", "block", "R-1", "--on", "user", "--question", "¿A o B?", "--default", "A")
        for extra in ((), ("--timeout", "4h"), ("--allowed-by", self.her), ("--timeout", "4h", "--allowed-by", self.typed),
                      ("--timeout", "4h", "--allowed-by", "I-999"), ("--timeout", "soon", "--allowed-by", self.her)):
            r = self.cli(*base, *extra)
            self.assertNotEqual(r.returncode, 0, extra)
        self.assertNotIn("blocked", self.item("R-1"))
        self.ok(*base, "--timeout", "4h", "--allowed-by", self.her)
        d = self.item("R-1")["blocked"]["default"]
        self.assertEqual((d["text"], d["after_s"], d["allowed_by"]), ("A", 4 * 3600, self.her))

    def test_unblock_keeps_her_answer(self):
        self.ok("ledger", "block", "R-1", "--on", "user", "--question", "¿A o B?")
        self.ok("ledger", "unblock", "R-1", "--answer", "B")
        it = self.item("R-1")
        self.assertNotIn("blocked", it)
        self.assertEqual((it["answered"]["question"], it["answered"]["answer"], it["answered"]["by"]),
                         ("¿A o B?", "B", "user"))


class DefaultTest(WaitCase):
    def test_a_default_applies_only_after_its_timeout(self):
        self.ok("ledger", "block", "R-1", "--on", "user", "--question", "¿A o B?", "--default", "A",
                "--timeout", "4h", "--allowed-by", self.her)
        self.ok("ledger", "block", "R-2", "--on", "user", "--question", "¿sin defecto?")
        self.age_block("R-2", 30 * 86400)
        self.assertIn("0 ", self.ok("ledger", "apply-defaults"))
        self.assertIn("blocked", self.item("R-1"))
        self.age_block("R-1", 4 * 3600 + 60)
        self.ok("ledger", "apply-defaults")
        it = self.item("R-1")
        self.assertNotIn("blocked", it)
        self.assertEqual((it["answered"]["answer"], it["answered"]["by"]), ("A", "default"))
        self.assertIn("blocked", self.item("R-2"))                     # no default: it waits as long as it takes
        with open(os.path.join(sl.studio_dir(self.project), "audit.log"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"R-1 .*default.*%s" % self.her)


class WaitingListTest(WaitCase):
    def test_status_waiting_lists_items_and_chats_oldest_first(self):
        self.ok("ledger", "block", "R-3", "--on", "user", "--question", "segunda pregunta")
        self.ok("ledger", "block", "R-1", "--on", "user", "--question", "primera pregunta")
        self.ok("ledger", "block", "R-4", "--reason", "waits on the API key")          # not on her
        self.age_block("R-1", 7200)
        self.age_block("R-3", 600)
        hidden = os.path.join(self.tmp, "secret-project")
        os.makedirs(hidden)
        protect(self.home, hidden)
        self.chat("s-lane-a", "Lane A", "waiting", 3600)
        self.chat("s-lane-b", "Secret lane", "waiting", 21 * 3600, cwd=hidden)
        self.chat("s-lane-c", "Busy lane", "busy", 10)
        out = self.ok("status", "--waiting")
        self.assertLess(out.index("primera pregunta"), out.index("segunda pregunta"))
        self.assertNotIn("API key", out)
        self.assertNotIn("Busy lane", out)
        self.assertNotIn("Secret lane", out)
        self.assertNotIn(hidden, out)
        data = json.loads(self.ok("status", "--waiting", "--json"))
        self.assertEqual([i["id"] for i in data["items"]], ["R-1", "R-3"])
        self.assertEqual([c["protected"] for c in data["chats"]], [True, False])       # 21 h first
        self.assertIsNone(data["chats"][0]["name"])
        self.assertEqual(data["chats"][1]["name"], "Lane A")
        self.assertGreater(data["chats"][0]["waiting_s"], 20 * 3600)

    def test_nothing_waiting(self):
        self.assertIn("nothing", self.ok("status", "--waiting").lower())
        self.assertEqual(json.loads(self.ok("status", "--waiting", "--json")),
                         {"items": [], "chats": [], "all_blocked": False})

    def test_the_keep_alive_skips_a_project_whose_open_items_are_all_blocked(self):
        flag = lambda: json.loads(self.ok("status", "--waiting", "--json"))["all_blocked"]
        self.assertFalse(flag())
        for n in (1, 2, 3):
            self.ok("ledger", "block", "R-%d" % n, "--reason", "needs a decision")
        self.assertFalse(flag())                                       # R-4 is still free to build
        self.ok("ledger", "block", "R-4", "--on", "user", "--question", "¿A o B?")
        self.assertTrue(flag())
        self.ok("ledger", "unblock", "R-2")
        self.assertFalse(flag())
        self.ok("ledger", "drop", "R-2", "--by-user", "I-1")           # a dropped item is not work
        self.assertTrue(flag())


class StatusLineTest(WaitCase):
    def sit(self, waiting):
        return {"name": "demo", "doing": 1, "next": 2, "later": 0, "week": 40, "forecast": "ok", "out_day": "",
                "out_hour": 0, "stale": 0, "h5": 0, "waiting": waiting}

    def test_the_line_says_who_waits_in_her_language(self):
        self.assertIn("te esperan: 2", sl.status_line(self.sit(2), "es"))
        self.assertIn("waiting on you: 2", sl.status_line(self.sit(2), "en"))
        for lang in ("es", "en"):
            self.assertLessEqual(len(sl.status_line(self.sit(12), lang)), sl.LINE_MAX)
            self.assertNotRegex(sl.status_line(self.sit(0), lang), "esperan|waiting on you")

    def test_the_cli_line_counts_items_and_chats_of_this_project(self):
        self.ok("ledger", "block", "R-1", "--on", "user", "--question", "¿A o B?")
        self.chat("s-lane-a", "Lane A", "waiting", 60, cwd=os.path.join(self.project, ".claude", "worktrees", "x"))
        self.chat("s-other", "Other", "waiting", 60, cwd=self.tmp)
        line = self.ok("status", "--line")
        self.assertRegex(line, r"(te esperan|waiting on you): 2")
