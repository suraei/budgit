"""R-283 S3: the project card (`<data folder>/private/card.json`), rewritten by the ledger writer. Counts, ids, ages and versions
only; a protected project is read from its card and its ledger is not opened. Fake names only."""
import json
import os
import subprocess
import time

from test_portfolio import PortfolioCase, SECRET
import card
import portfolio
import studiolib as sl


def card_file(project):
    return os.path.join(project, ".studio", "private", "card.json")


class CardWriteTest(PortfolioCase):
    def setUp(self):
        super().setUp()
        self.root, self.hub = self.setup_three()
        self.cofre = self.mk(os.path.join(self.root, "cofre"), [
            ("R-1", "doing", {"title": SECRET, "text": SECRET}), ("R-2", "open"), ("R-3", "open", {"phase": "later"}),
            ("R-4", "done"), ("R-5", "verified"), ("R-6", "dropped")])

    def test_the_ledger_writer_leaves_a_card_under_private_with_counts_and_ids_only(self):
        data = json.load(open(card_file(self.cofre)))
        self.assertEqual(data["counts"], {"open": 2, "doing": 1, "done": 1, "verified": 1, "dropped": 1, "folded": 0, "later": 1})
        self.assertEqual([d["id"] for d in data["doing"]], ["R-1"])
        self.assertIsInstance(data["doing"][0]["age_days"], int)
        self.assertEqual(data["version"], 1)
        self.assertIsNone(data["budgit_version"])
        self.assertNotIn(SECRET, open(card_file(self.cofre)).read())
        self.assertNotIn("cofre", open(card_file(self.cofre)).read().lower())

    def test_every_ledger_change_rewrites_it(self):
        ledger = sl.load_ledger(self.cofre)
        ledger["items"][1]["status"] = "doing"
        sl.save_ledger(self.cofre, ledger)
        self.assertEqual(json.load(open(card_file(self.cofre)))["counts"]["doing"], 2)

    def test_the_versions_stamped_by_the_upgrade_survive_a_rewrite(self):
        self.assertTrue(card.stamp(self.cofre, budgit_version="0.13.3", rules_version="7"))
        sl.save_ledger(self.cofre, sl.load_ledger(self.cofre))
        data = json.load(open(card_file(self.cofre)))
        self.assertEqual((data["budgit_version"], data["rules_version"]), ("0.13.3", "7"))

    def test_a_card_that_cannot_be_written_never_costs_the_ledger_write(self):
        os.remove(card_file(self.cofre))
        os.rmdir(os.path.dirname(card_file(self.cofre)))
        with open(os.path.dirname(card_file(self.cofre)), "w") as f:          # `private` is a file: the card cannot be made
            f.write("x")
        ledger = sl.load_ledger(self.cofre)
        ledger["items"][1]["status"] = "done"
        sl.save_ledger(self.cofre, ledger)
        self.assertEqual(sl.load_ledger(self.cofre)["items"][1]["status"], "done")

    def test_state_reports_missing_ok_and_old(self):
        self.assertEqual(card.state(self.cofre)[0], "ok")
        os.utime(sl._ledger_path(self.cofre), (time.time() + 60, time.time() + 60))     # the ledger moved on after the card
        self.assertEqual(card.state(self.cofre)[0], "old")
        os.remove(card_file(self.cofre))
        self.assertEqual(card.state(self.cofre), ("missing", None))
        with open(card_file(self.cofre), "w") as f:
            f.write("{not json")
        self.assertEqual(card.state(self.cofre), ("missing", None))


class ProtectedFromCardTest(PortfolioCase):
    def setUp(self):
        super().setUp()
        self.root, self.hub = self.setup_three()
        self.cofre = self.mk(os.path.join(self.root, "cofre"), [("R-1", "doing", {"title": SECRET}), ("R-2", "open"),
                                                                ("R-3", "open", {"phase": "later"}), ("R-4", "done")])
        self.ok("add", self.cofre)
        self.ok("protect", "cofre")

    def row(self):
        return self.protected_rows()[0]

    def test_a_protected_row_takes_its_numbers_from_the_card(self):
        self.assertEqual(self.row()["tasks"], {"doing": 1, "next": 1, "later": 1, "done": 1})
        self.assertEqual(self.row()["card"]["state"], "ok")

    def test_the_ledger_of_a_protected_project_is_not_opened(self):
        with open(sl._ledger_path(self.cofre), "w") as f:
            f.write("{broken")                                  # an unreadable ledger changes nothing in the row
        os.utime(sl._ledger_path(self.cofre), (time.time() - 3600, time.time() - 3600))
        self.assertEqual(self.row()["tasks"]["doing"], 1)
        calls = []
        real = sl.load_ledger
        sl.load_ledger = lambda p: calls.append(p) or real(p)
        try:
            portfolio._tasks(self.cofre, True)
        finally:
            sl.load_ledger = real
        self.assertEqual(calls, [])

    def test_a_missing_card_says_not_upgraded_and_shows_no_counts_from_the_ledger(self):
        os.remove(card_file(self.cofre))
        row = self.row()
        self.assertEqual(row["card"], {"state": "missing", "age_days": None})
        self.assertEqual(row["tasks"], {"doing": 0, "next": 0, "later": 0, "done": 0})
        text = self.ok()
        self.assertIn("sin actualizar", text)
        self.assertNotIn(SECRET, text)

    def test_an_old_card_shows_its_age(self):
        os.utime(sl._ledger_path(self.cofre), (time.time() + 60, time.time() + 60))
        self.assertEqual(self.row()["card"]["state"], "old")
        self.assertIn("ficha de hace 0d", self.ok())

    def test_an_unprotected_project_still_reads_its_ledger(self):
        rows = self.rows()
        self.assertEqual(rows["hubby"]["tasks"], {"doing": 1, "next": 1, "later": 1, "done": 1})
        self.assertEqual(rows["hubby"]["card"]["state"], "ok")

class CardEdgeTest(PortfolioCase):
    def test_a_data_folder_or_private_folder_that_is_a_link_is_never_written_through(self):
        import shutil
        for link_data in (True, False):
            proj = self.mk(os.path.join(self.proj("Dev"), "lnk%s" % link_data), [("R-1", "doing")])
            outside = os.path.join(self.tmp, "outside-%s" % link_data)
            os.makedirs(outside)
            data_dir = sl.studio_dir(proj)
            if link_data:
                shutil.move(data_dir, data_dir + ".real")
                os.symlink(outside, data_dir)
            else:
                shutil.rmtree(os.path.join(data_dir, "private"), ignore_errors=True)
                os.symlink(outside, os.path.join(data_dir, "private"))
            self.assertFalse(card.write(proj, sl.empty_ledger()))
            self.assertFalse(card.stamp(proj, budgit_version="0.1.0"))
            self.assertEqual(os.listdir(outside), [], "link_data=%s" % link_data)

    def test_the_card_never_shows_up_in_git_status(self):
        for track in (False, True):
            proj = os.path.join(self.proj("Dev"), "git%s" % track)
            os.makedirs(proj)
            subprocess.run(["git", "init", "-q", proj], check=True)
            if track:
                os.makedirs(os.path.join(proj, ".studio"))
                with open(os.path.join(proj, ".studio", "project.json"), "w") as f:
                    json.dump({"tracks_ledger": True}, f)
            self.mk(proj, [("R-1", "doing")])
            self.assertTrue(os.path.exists(card_file(proj)))
            subprocess.run(["git", "-C", proj, "add", "-A"], check=True)
            staged = subprocess.run(["git", "-C", proj, "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout
            self.assertNotIn("card.json", staged, "track=%s: %r" % (track, staged))

    def test_the_stuck_count_is_the_ledgers_own_for_a_protected_and_an_open_project(self):
        old = {"updated": "2026-01-01T00:00:00.000Z", "created": "2026-01-01T00:00:00.000Z"}
        root, hub = self.setup_three()
        cofre = self.mk(os.path.join(root, "cofre"), [("R-1", "doing", old), ("R-2", "doing")])
        self.ok("add", cofre)
        plain = portfolio._tasks(cofre, False)[1]
        self.assertEqual(portfolio._tasks(cofre, True)[1], plain)

    def test_a_hand_edited_item_id_never_reaches_the_card(self):
        proj = self.mk(os.path.join(self.proj("Dev"), "x"), [("R-1", "doing")])
        data = sl.load_ledger(proj)
        data["items"][0]["id"] = "R-1 /home/me/secret-name"
        card.write(proj, data)
        self.assertEqual(json.load(open(card_file(proj)))["doing"], [])

    def test_an_id_with_a_trailing_newline_never_reaches_the_card(self):
        proj = self.mk(os.path.join(self.proj("Dev"), "nl"), [("R-1", "doing")])
        data = sl.load_ledger(proj)
        data["items"][0]["id"] = "R-12\n"
        card.write(proj, data)
        self.assertEqual(json.load(open(card_file(proj)))["doing"], [])

    def test_the_stuck_total_counts_every_doing_item_even_with_odd_ids(self):
        old = {"updated": "2026-01-01T00:00:00.000Z", "created": "2026-01-01T00:00:00.000Z"}
        proj = self.mk(os.path.join(self.proj("Dev"), "odd"), [("R-1", "doing", old), ("R-2", "doing", old)])
        data = sl.load_ledger(proj)
        data["items"][1]["id"] = "weird id"
        card.write(proj, data)
        self.assertEqual(json.load(open(card_file(proj)))["stuck"], 2)
        self.assertEqual(portfolio._tasks(proj, True)[1], 2)

    def test_a_stamp_before_any_ledger_write_still_keeps_the_card_out_of_git(self):
        proj = os.path.join(self.proj("Dev"), "stampgit")
        os.makedirs(proj)
        subprocess.run(["git", "init", "-q", proj], check=True)
        self.mk(proj, [("R-1", "doing")])
        gi = os.path.join(proj, ".gitignore")
        os.remove(gi)                                           # as if no ledger write had ever ensured it
        self.assertTrue(card.stamp(proj, budgit_version="1"))
        subprocess.run(["git", "-C", proj, "add", "-A"], check=True)
        staged = subprocess.run(["git", "-C", proj, "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout
        self.assertNotIn("card.json", staged)

