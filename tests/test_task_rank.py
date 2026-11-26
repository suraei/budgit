"""Per-task priority: every open or doing item gets a score from what the user waits on, the bugs she sees, the items
it unblocks, its age and its cost, with every weight in one table (taskrank.WEIGHTS). The PM can pin an item to a
position with a stored reason (`ledger rank R-n <pos> --reason`). `budgit ledger top` and `budgit status` (also a bare
`budgit`) show the top 10. Same ledger, same day: same order."""
import calendar
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
import studiolib as sl
import taskrank

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
NOW = calendar.timegm((2026, 10, 27, 12, 0, 0))
WAIT = {"blocked": {"reason": "her pick", "since": "2026-10-27T10:00:00.000Z", "by": None, "on": "user",
                    "question": "her pick"}}


def stamp(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - days_ago * 86400))


class RankCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()

    def ledger(self, *rows):
        """rows: (text, created days ago, extra fields)"""
        data = sl.load_ledger(self.project)
        for text, age, extra in rows:
            it = sl.add_item(data, text, ["legacy"])
            it["created"] = stamp(age)
            it.update(extra)
        sl.save_ledger(self.project, data)
        return sl.load_ledger(self.project)

    def ids(self, data, n=10):
        return [r["id"] for r in taskrank.top(data, n=n, at=NOW)]

    def cli(self, *args, project=True):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args] + (["--project", self.project] if project else []),
                              env=env, capture_output=True, text=True, cwd=self.project)

    def ok(self, *args, **kw):
        r = self.cli(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class ScoreTest(RankCase):
    def test_each_input_moves_an_item_up_or_down(self):
        data = self.ledger(("plain", 0, {}),                                   # R-1
                           ("she waits on this", 0, WAIT),   # R-2
                           ("a bug she sees", 0, {"flags": ["bug"]}),          # R-3
                           ("old one", 70, {}),                                # R-4
                           ("costly", 0, {"appetite": "large"}),               # R-5
                           ("unblocked by the next", 0, {}),                   # R-6
                           ("needs R-6 first", 0, {}))                         # R-7
        order = self.ids(data)
        self.assertEqual(order[:2], ["R-2", "R-3"])
        self.assertLess(order.index("R-4"), order.index("R-1"))       # age counts
        self.assertLess(order.index("R-6"), order.index("R-1"))       # unblocking counts
        self.assertEqual(order[-1], "R-5")                            # cost counts against
        self.assertEqual(len(order), 7)

    def test_the_score_is_the_sum_of_its_parts_and_the_weights_live_in_one_table(self):
        data = self.ledger(("x", 21, dict(WAIT, flags=["bug"], appetite="medium")), ("after R-1", 0, {}))
        row = taskrank.top(data, at=NOW)[0]
        w = taskrank.WEIGHTS
        self.assertEqual(row["parts"], {"waiting": w["waiting"], "bug": w["bug"], "unblocks": w["unblocks"],
                                        "age": 3 * w["age"], "cost": 1 * w["cost"]})
        self.assertEqual(row["score"], sum(row["parts"].values()))
        old = dict(w)
        self.addCleanup(lambda: (w.clear(), w.update(old)))
        w["bug"] = 0
        w["waiting"] = 0
        w["unblocks"] = 0
        self.assertEqual(taskrank.top(data, at=NOW)[0]["parts"]["bug"], 0)

    def test_age_and_unblocking_are_capped(self):
        data = self.ledger(("ancient", 5000, {}), *(("cites R-1 (%d)" % n, 0, {}) for n in range(30)))
        parts = taskrank.top(data, at=NOW)[0]["parts"]
        self.assertEqual(parts["age"], taskrank.AGE_CAP_WEEKS * taskrank.WEIGHTS["age"])
        self.assertEqual(parts["unblocks"], taskrank.UNBLOCKS_CAP * taskrank.WEIGHTS["unblocks"])

    def test_only_open_and_doing_items_count_and_only_open_ones_unblock(self):
        data = self.ledger(("a", 0, {}), ("b", 0, {}), ("c R-1", 0, {}), ("d R-2", 0, {}))
        sl.set_status(self.project, data, "R-3", "done", evidence="x", outcome="You see it work")
        sl.find_item(data, "R-4")["status"] = "dropped"
        rows = taskrank.top(data, at=NOW)
        self.assertEqual(sorted(r["id"] for r in rows), ["R-1", "R-2"])
        self.assertTrue(all(r["parts"]["unblocks"] == 0 for r in rows))

    def test_same_ledger_same_order(self):
        data = self.ledger(*(("t%d" % n, n % 3, {}) for n in range(15)))
        first = taskrank.top(data, n=15, at=NOW)
        for _ in range(3):
            self.assertEqual(taskrank.top(json.loads(json.dumps(data)), n=15, at=NOW), first)
        self.assertEqual([r["id"] for r in first], ["R-%d" % n for n in range(1, 16)])   # a tie keeps ledger order

    def test_top_is_ten_by_default(self):
        data = self.ledger(*(("t%d" % n, 0, {}) for n in range(14)))
        self.assertEqual(len(taskrank.top(data, at=NOW)), 10)


class OverrideTest(RankCase):
    def test_a_pinned_item_takes_its_position_and_keeps_its_reason(self):
        data = self.ledger(("a", 0, WAIT), ("b", 0, {"flags": ["bug"]}), ("c", 0, {}), ("d", 0, {}))
        taskrank.pin(data, "R-4", 1, "she asked for it today", by="s-pm")
        self.assertEqual(self.ids(data), ["R-4", "R-1", "R-2", "R-3"])
        rank = sl.find_item(data, "R-4")["rank"]
        self.assertEqual((rank["pos"], rank["reason"], rank["by"]), (1, "she asked for it today", "s-pm"))
        self.assertTrue(rank["at"])
        taskrank.pin(data, "R-1", 99, "after everything")
        self.assertEqual(self.ids(data)[-1], "R-1")

    def test_a_pin_needs_a_reason_and_a_position_from_one(self):
        data = self.ledger(("a", 0, {}),)
        with self.assertRaises(sl.StudioError):
            taskrank.pin(data, "R-1", 1, "  ")
        with self.assertRaises(sl.StudioError):
            taskrank.pin(data, "R-1", 0, "why")

    def test_unpin_goes_back_to_the_score(self):
        data = self.ledger(("a", 0, WAIT), ("b", 0, {}))
        taskrank.pin(data, "R-2", 1, "x")
        taskrank.unpin(data, "R-2")
        self.assertNotIn("rank", sl.find_item(data, "R-2"))
        self.assertEqual(self.ids(data), ["R-1", "R-2"])

    def test_a_closed_item_cannot_be_pinned(self):
        data = self.ledger(("a", 0, {}),)
        sl.set_status(self.project, data, "R-1", "done", evidence="x", outcome="You see it work")
        with self.assertRaises(sl.StudioError):
            taskrank.pin(data, "R-1", 1, "why")


class CliTest(RankCase):
    def test_flag_rank_and_top_through_the_cli(self):
        self.ledger(("first thing", 0, {}), ("second thing", 0, {}), ("third thing", 0, {}))
        self.ok("ledger", "flag", "R-3", "bug")
        self.ok("ledger", "flag", "R-2", "bug")
        out = self.ok("ledger", "top")
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertTrue(lines[1].lstrip().startswith("1. R-2"), out)          # a tie keeps ledger order
        self.assertIn("porque:", out)                                          # R-1333: the line is the one order's «porque» line, not the score parts
        self.ok("ledger", "rank", "R-1", "1", "--reason", "blocks the release")
        top = json.loads(self.ok("ledger", "top", "--json"))
        self.assertEqual([r["id"] for r in top], ["R-1", "R-2", "R-3"])
        self.assertEqual(top[0]["rank"]["reason"], "blocks the release")
        self.assertIn("blocks the release", self.ok("ledger", "top"))
        self.assertIn("blocks the release", self.ok("ledger", "show", "R-1"))
        self.ok("ledger", "flag", "R-3", "bug", "--off")
        self.assertNotIn("flags", sl.find_item(sl.load_ledger(self.project), "R-3"))
        self.ok("ledger", "rank", "R-1", "--clear")
        self.assertNotIn("rank", sl.find_item(sl.load_ledger(self.project), "R-1"))

    def test_rank_without_a_reason_is_refused(self):
        self.ledger(("a", 0, {}),)
        r = self.cli("ledger", "rank", "R-1", "1")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("rank", sl.find_item(sl.load_ledger(self.project), "R-1"))

    def test_unknown_flag_is_refused(self):
        self.ledger(("a", 0, {}),)
        for name in ("urgent", "waiting"):                  # waiting on her is `ledger block --on user`, not a flag
            self.assertNotEqual(self.cli("ledger", "flag", "R-1", name).returncode, 0)

    def test_status_and_bare_budgit_show_the_top_ten(self):
        self.ledger(*(("task number %d" % n, 0, {}) for n in range(12)))
        for out in (self.ok("status"), self.ok(project=False)):
            self.assertIn("top 10", out)
            self.assertIn(" 10. R-10", out)
            self.assertNotIn("R-11 ", out.split("top 10", 1)[1].split("untracked", 1)[0])

    def test_a_bad_flags_or_rank_field_fails_validation(self):
        data = sl.load_ledger(self.project)
        sl.add_item(data, "a", ["legacy"])["flags"] = ["waiting"]
        with self.assertRaises(sl.StudioError):
            sl.save_ledger(self.project, data)
        data = sl.load_ledger(self.project)
        sl.add_item(data, "a", ["legacy"])["rank"] = {"pos": 0, "reason": "x"}
        with self.assertRaises(sl.StudioError):
            sl.save_ledger(self.project, data)
