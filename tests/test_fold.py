"""Fold: `ledger fold R-n --into R-m --reason …` marks R-n "folded into R-m". A folded item no longer counts as open
anywhere (lists, the MVP counter, the top 10, the live feed's counts and the portfolio) and shows under R-m.
`ledger unfold R-n` puts it back to open."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl
import taskrank

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class FoldCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-fold0001")
        data = sl.load_ledger(self.project)
        for n in range(1, 6):
            sl.add_item(data, "item %d" % n, ["legacy"], phase="mvp" if n <= 3 else "")
        sl.save_ledger(self.project, data)

    def data(self):
        return sl.load_ledger(self.project)

    def item(self, item_id):
        return sl.find_item(self.data(), item_id)

    def cli(self, *args, session=None, lang=None):
        env = dict(os.environ, STUDIO_HOME=self.home)
        if lang:
            env["BUDGIT_LANG"] = lang      # R-1160: the status labels follow the machine's language; a test about the numbers pins it
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def ok(self, *args, **kw):
        r = self.cli(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class FoldTest(FoldCase):
    def test_fold_writes_folded_into_and_keeps_the_reason(self):
        out = self.ok("ledger", "fold", "R-2", "--into", "R-1", "--reason", "same login bug")
        self.assertIn("R-2 -> folded into R-1", out)
        it = self.item("R-2")
        self.assertEqual(it["status"], "folded")
        self.assertEqual((it["folded"]["into"], it["folded"]["reason"]), ("R-1", "same login bug"))
        self.assertTrue(it["folded"]["at"])
        with open(os.path.join(sl.studio_dir(self.project), "audit.log"), encoding="utf-8") as f:
            self.assertIn("R-2 folded into R-1", f.read())

    def test_it_shows_under_its_target(self):
        self.ok("ledger", "fold", "R-4", "--into", "R-1", "--reason", "a part of it")
        self.assertRegex(self.ok("ledger", "show", "R-1"), r"folded here: R-4 \(a part of it\)")
        self.assertIn("folded into R-1", self.ok("ledger", "show", "R-4"))
        with open(os.path.join(sl.studio_dir(self.project), "ledger.md"), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("folded into R-1", md)
        self.assertIn("folds R-4", md)
        listed = self.ok("ledger", "list")
        self.assertIn("R-4 [folded into R-1]", listed)

    def test_folded_items_stop_counting_as_open_everywhere(self):
        self.ok("ledger", "start", "R-3", session="s-fold0001")
        self.ok("ledger", "fold", "R-3", "--into", "R-1", "--reason", "dup")
        self.ok("ledger", "fold", "R-5", "--into", "R-1", "--reason", "dup")
        data = self.data()
        self.assertNotIn("doing_by", sl.find_item(data, "R-3"))                     # the claim goes with it
        listed = [l.split(" ", 1)[0] for l in self.ok("ledger", "list", "--open").splitlines()]
        self.assertEqual(listed, ["R-1", "R-2", "R-4"])
        mvp, left, _ = sl._mvp_state(data)
        self.assertEqual([i["id"] for i in mvp], ["R-1", "R-2"])                    # MVP counter: 0/2, not 0/3
        self.assertNotIn("R-3", left)
        self.assertNotIn("R-3", [r["id"] for r in taskrank.top(data)])
        self.assertIn("items: 5 (3 open/doing)", self.ok("status", lang="en"))
        scope = sl.release_scope(data, ["R-3", "R-5"])                             # neither built nor blocking
        self.assertEqual(sum(len(v) for v in scope.values()), 0)

    def test_unfold_puts_it_back_to_open(self):
        self.ok("ledger", "fold", "R-2", "--into", "R-1", "--reason", "dup")
        self.ok("ledger", "unfold", "R-2")
        it = self.item("R-2")
        self.assertEqual(it["status"], "open")
        self.assertNotIn("folded", it)
        self.assertIn("R-2", self.ok("ledger", "list", "--open"))
        self.assertNotIn("folded here", self.ok("ledger", "show", "R-1"))


class RefuseTest(FoldCase):
    def refused(self, *args):
        before = json.dumps(self.data())
        r = self.cli(*args)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.dumps(self.data()), before)
        return r.stderr

    def test_bad_folds_change_nothing(self):
        self.refused("ledger", "fold", "R-2", "--into", "R-2", "--reason", "x")             # itself
        self.refused("ledger", "fold", "R-2", "--into", "R-99", "--reason", "x")            # unknown
        self.refused("ledger", "fold", "R-2", "--into", "R-1", "--reason", "  ")            # no reason
        self.ok("ledger", "fold", "R-3", "--into", "R-1", "--reason", "dup")
        self.assertIn("R-1", self.refused("ledger", "fold", "R-2", "--into", "R-3", "--reason", "x"))  # into a folded one
        self.refused("ledger", "fold", "R-3", "--into", "R-2", "--reason", "x")             # already folded
        self.refused("ledger", "unfold", "R-2")                                             # not folded

    def test_a_closed_item_is_not_folded_and_the_status_is_not_set_by_hand(self):
        self.ok("ledger", "outcome", "R-2", "You see it closed")
        self.ok("ledger", "done", "R-2", "--evidence", "abcdef1 test_fold")
        self.refused("ledger", "fold", "R-2", "--into", "R-1", "--reason", "x")
        self.refused("ledger", "set", "R-4", "folded")

    def test_the_ledger_rejects_a_fold_without_its_target(self):
        data = self.data()
        sl.find_item(data, "R-2")["status"] = "folded"
        with self.assertRaises(sl.StudioError):
            sl.save_ledger(self.project, data)
        data = self.data()
        sl.find_item(data, "R-2")["folded"] = {"into": "R-1", "reason": "x", "at": sl.now()}
        with self.assertRaises(sl.StudioError):                                    # folded field on an open item
            sl.save_ledger(self.project, data)
