"""R-925: the size sets the due date (small today, medium +3 days, large needs a dated plan); late S/M items rank first."""
import datetime
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import i18n
import studiolib as sl
import taskrank

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def _day(delta=0):
    return (datetime.date.today() + datetime.timedelta(days=delta)).isoformat()


class SizeDueTest(StudioTestCase):
    def data(self, *sizes):
        data = {"items": [], "acks": {}}
        for n, size in enumerate(sizes, 1):
            sl.add_item(data, "thing %d" % n, ["legacy"], phase="mvp")
            if size:
                sl.set_appetite(data, "R-%d" % n, size)
        return data

    def test_small_is_due_today(self):
        self.assertEqual(self.data("small")["items"][0]["due"], _day(0))

    def test_medium_is_due_in_three_days(self):
        self.assertEqual(self.data("medium")["items"][0]["due"], _day(3))

    def test_large_has_no_due_and_is_flagged_as_needing_a_dated_plan(self):
        data = self.data("large")
        self.assertNotIn("due", data["items"][0])
        row = taskrank.top(data)[0]
        self.assertEqual(row["due_kind"], "plan")
        self.assertIn(i18n.t("rank.needs_plan"), taskrank.lines([row])[0])

    def test_an_explicit_due_is_kept_on_an_appetite_change(self):
        data = self.data("small")
        sl.set_appetite(data, "R-1", "small", due="2030-01-05")
        sl.set_appetite(data, "R-1", "medium")
        sl.set_appetite(data, "R-1", "large")
        self.assertEqual(data["items"][0]["due"], "2030-01-05")

    def test_a_computed_due_follows_the_size_when_it_changes(self):
        data = self.data("small")
        sl.set_appetite(data, "R-1", "medium")
        self.assertEqual(data["items"][0]["due"], _day(3))
        sl.set_appetite(data, "R-1", "large")
        self.assertNotIn("due", data["items"][0])

    def test_an_overdue_small_ranks_first_and_shows_days_late(self):
        data = self.data("medium", "small", "small")
        data["items"][1]["due"] = _day(-4)
        data["items"][0]["flags"] = ["bug"]            # a bug would win on score; late still goes first
        rows = taskrank.top(data)
        self.assertEqual(rows[0]["id"], "R-2")
        line = taskrank.lines(rows)[0]
        self.assertIn(i18n.t("rank.late", days=4), line)
        self.assertIn("4", line)
        self.assertNotEqual(i18n.t("rank.late", lang="en", days=4), i18n.t("rank.late", lang="es", days=4))

    def test_not_yet_due_does_not_jump_the_queue(self):
        data = self.data("medium", "small", "small")
        data["items"][0]["flags"] = ["bug"]            # best score; due in 3 days, not late
        data["items"][1]["due"] = _day(1)              # not yet due
        rows = taskrank.ranked(data)
        self.assertNotEqual(rows[0]["due_kind"], "late")
        self.assertTrue(all(r["due_kind"] != "late" for r in rows))
        self.assertEqual([r["id"] for r in rows],
                         [r["id"] for r in sorted(rows, key=lambda r: -r["score"])])   # sorted() is stable: ties keep ledger order
        self.assertEqual(rows[0]["id"], "R-1")

    def test_a_bad_due_string_is_ignored_not_fatal(self):
        for bad in ("2026-13-40", "soon", ""):
            data = self.data("small")
            data["items"][0]["due"] = bad
            sl.validate_ledger(data)
            self.assertEqual(sl.due_state(data["items"][0]), (None, 0))

    def test_a_non_string_due_is_ignored_not_fatal(self):
        for bad in (5, None, ["2026-01-01"], 1.5):
            data = self.data("small")
            data["items"][0]["due"] = bad
            sl.validate_ledger(data)
            self.assertEqual(sl.due_state(data["items"][0]), (None, 0))

    def test_a_bad_due_survives_a_save_and_load_round_trip(self):
        data = sl.empty_ledger()
        os.makedirs(sl.studio_dir(self.project), exist_ok=True)
        for n in (1, 2):
            sl.add_item(data, "thing %d" % n, ["legacy"], phase="mvp")
            sl.set_appetite(data, "R-%d" % n, "small")
        data["items"][0]["due"] = "2026-13-40"
        data["items"][1]["due"] = 5
        sl.save_ledger(self.project, data)
        back = sl.load_ledger(self.project)
        self.assertEqual(len(back["items"]), 2)
        self.assertEqual(back["items"][0]["due"], "2026-13-40")

    def test_ranking_works_with_a_bad_due_present(self):
        data = self.data("small", "small", "small")
        data["items"][0]["due"] = "2026-13-40"
        data["items"][1]["due"] = 5
        data["items"][2]["due"] = _day(-2)
        rows = taskrank.ranked(data)
        self.assertEqual(rows[0]["id"], "R-3")
        self.assertEqual(len(rows), 3)

    def test_an_invalid_due_on_appetite_still_raises_and_writes_nothing(self):
        data = self.data("small")
        before = dict(data["items"][0])
        with self.assertRaises(sl.StudioError):
            sl.set_appetite(data, "R-1", "medium", due="2026-13-40")
        self.assertEqual(data["items"][0], before)

    def test_the_guess(self):
        self.assertEqual(sl.guess_appetite("fix the typo in plugin/bin/budgit"), "medium")  # no extension: no file named
        self.assertEqual(sl.guess_appetite("fix the typo in README.md"), "small")
        self.assertEqual(sl.guess_appetite("change one line of the footer"), "small")
        self.assertEqual(sl.guess_appetite("cambia la línea 12"), "small")
        self.assertEqual(sl.guess_appetite("change a.py and b.py"), "medium")
        self.assertEqual(sl.guess_appetite("fix README.md " + "x" * 300), "medium")
        self.assertEqual(sl.guess_appetite("redesign the whole panel"), "medium")

    def test_a_legacy_add_prints_the_guess_and_the_show_has_the_due(self):
        self.init_project()
        run = lambda *a: subprocess.run([sys.executable, CLI] + list(a), cwd=self.project, capture_output=True, text=True)
        add = run("ledger", "add", "--src", "legacy", "--phase", "mvp", "--text", "fix the typo in README.md")
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertIn(i18n.t("ledger.guess", size="small"), add.stdout)
        show = run("ledger", "show", "R-1")
        self.assertIn("due: %s" % _day(0), show.stdout)
