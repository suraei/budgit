"""R-283 S10: the Team Manager's inbox. The TM chat's prompts are kept as T-n in the Budgit home (never in a project's
inbox), `budgit portfolio route T-n <project> [--item R-n]` copies one into that project's inbox as an input (I-n),
and the portfolio view counts the ones not routed yet. Fake names and paths only."""
import json
import os
import subprocess
import sys

from test_portfolio import PortfolioCase
import portfolio
import studiolib as sl

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "scripts", "hook.py")
TM = "s-tm000001"


class TmCase(PortfolioCase):
    def setUp(self):
        super().setUp()
        self.root, self.hub = self.setup_three()
        self.huerto = os.path.join(self.root, "huerto")
        self.view()                                  # seeds the list
        self.ok("tm", TM)

    def hook(self, event, session, **payload):
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(dict(session_id=session, **payload)),
                           env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def say(self, text, session=TM):
        return self.hook("user-prompt-submit", session, prompt=text)

    def tm_inbox(self):
        return portfolio.load_tm()

    def project_inbox(self, path):
        return sl.load_inbox(path)


class CaptureTest(TmCase):
    def test_the_tm_session_is_recorded_and_shown(self):
        self.assertEqual(portfolio.tm_session(), TM)
        self.assertIn(TM[:8], self.ok("tm"))
        self.assertNotEqual(self.run_cli("tm", "no es una sesion").returncode, 0)

    def test_a_tm_prompt_becomes_t1_and_stays_out_of_every_project_inbox(self):
        self.pointer(TM, self.hub)                   # even when the TM chat sits in the hub folder
        before = len(self.project_inbox(self.hub))
        out = self.say("Prioriza huerto esta semana")
        recs = self.tm_inbox()
        self.assertEqual([(r["id"], r["text"], r["routed"]) for r in recs], [("T-1", "Prioriza huerto esta semana", None)])
        self.assertEqual(len(self.project_inbox(self.hub)), before)
        self.assertIn("T-1", out)
        self.hook("stop", TM, transcript_path="")
        self.assertEqual(len(self.project_inbox(self.hub)), before)

    def test_other_chats_still_write_their_project_inbox(self):
        self.pointer("s-pmhb0001", self.hub)
        self.say("Arregla el login", session="s-pmhb0001")
        self.assertEqual(self.tm_inbox(), [])
        self.assertEqual(self.project_inbox(self.hub)[-1]["text"], "Arregla el login")

    def test_harness_text_and_empty_prompts_are_skipped(self):
        self.say("   ")
        self.say("<task-notification>x</task-notification>")
        self.assertEqual(self.tm_inbox(), [])

    def test_numbers_keep_counting(self):
        self.say("uno")
        self.say("dos")
        self.assertEqual([r["id"] for r in self.tm_inbox()], ["T-1", "T-2"])


class RouteTest(TmCase):
    def test_route_copies_the_words_into_the_project_inbox_once(self):
        self.say("En huerto, el riego primero")
        self.ok("route", "T-1", "huerto")
        entry = self.project_inbox(self.huerto)[-1]
        self.assertEqual((entry["text"], entry["source"], entry["ref"]), ("En huerto, el riego primero", "tm", "T-1"))
        routed = self.tm_inbox()[0]["routed"]
        self.assertEqual((routed["project"], routed["input"], routed["item"]), ("huerto", entry["id"], None))
        r = self.run_cli("route", "T-1", "hubby")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("huerto", r.stderr)

    def test_route_with_item_links_the_input(self):
        self.say("R-1 de huerto: también el abono")
        self.ok("route", "T-1", "huerto", "--item", "R-1")
        iid = self.tm_inbox()[0]["routed"]["input"]
        self.assertIn(iid, sl.find_item(sl.load_ledger(self.huerto), "R-1")["src"])

    def test_unknown_ids_fail_and_change_nothing(self):
        self.say("algo")
        self.assertNotEqual(self.run_cli("route", "T-9", "huerto").returncode, 0)
        self.assertNotEqual(self.run_cli("route", "T-1", "huerto", "--item", "R-99").returncode, 0)
        self.assertIsNone(self.tm_inbox()[0]["routed"])
        self.assertNotEqual(self.run_cli("route", "T-1", "taller").returncode, 0)   # no ledger yet: adopt it first

    def test_a_protected_project_is_named_by_its_private_id(self):
        cofre = self.mk(os.path.join(self.root, "cofre"), [("R-1", "open")])
        self.ok("add", cofre)
        self.ok("protect", "cofre")
        self.say("Para el cofre: revisa permisos")
        self.ok("route", "T-1", portfolio.project_id(cofre))
        self.assertTrue(self.tm_inbox()[0]["routed"]["project"].startswith("private~"))
        self.assertNotIn("cofre", self.ok("inbox", "--all").lower().replace("para el cofre", ""))


class ListTest(TmCase):
    def test_inbox_lists_the_unrouted_and_all_with_their_project(self):
        self.say("primero")
        self.say("segundo")
        self.ok("route", "T-1", "huerto")
        out = self.ok("inbox")
        self.assertIn("T-2", out)
        self.assertNotIn("T-1", out)
        everything = self.ok("inbox", "--all")
        self.assertIn("T-1", everything)
        self.assertIn("huerto", everything)
        rows = json.loads(self.ok("inbox", "--json"))
        self.assertEqual([r["id"] for r in rows], ["T-2"])

    def test_the_view_counts_what_is_not_routed(self):
        self.say("primero")
        self.say("segundo")
        self.ok("route", "T-1", "huerto")
        self.assertEqual(self.view()["footer"]["tm_unrouted"], 1)
        self.assertIn("1 mensaje del TM sin repartir", self.ok())
