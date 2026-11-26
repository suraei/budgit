"""One meaning per count: the bar, `budgit status`, the Brote panel's feed and the session summary read the open / doing /
next-version numbers from one function (`sl.ledger_counts`) over one ledger. `budgit status` speaks of the current version
(the lowest release with unfinished work), not of an MVP, once a release is in play. The panel's watch contract (the ledger
is a watched file; a look every 2 s, a run at most 3 s apart) keeps it within 10 s of a ledger write."""
import json
import os
import re
import subprocess
import sys

from helpers import ROOT
from test_livework import LiveCase
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
SWIFT = os.path.join(ROOT, "plugin", "float", "src", "brote.swift")

ITEMS = [("R-1", "open", {"phase": "0.13.2"}), ("R-2", "open", {"phase": "0.13.2"}), ("R-3", "doing", {"phase": "0.13.2"}),
         ("R-4", "done", {"phase": "0.13.2"}), ("R-5", "open", {"phase": "0.13.3"}), ("R-6", "open", {"phase": "later"}),
         ("R-7", "open", {"phase": "later"}), ("R-8", "open", {"phase": "mvp"}), ("R-9", "done", {"phase": "mvp"}),
         ("R-10", "dropped", {"phase": "0.13.2"})]


class OneCountTest(LiveCase):
    def setUp(self):
        super().setUp()
        self.p = self.mk(self.proj("book"), ITEMS)
        self.config(hub=self.p)

    def status(self, *args, lang=None):
        env = dict(os.environ)
        if lang:
            env["BUDGIT_LANG"] = lang      # R-1160: the status labels follow the machine's language; a test about the numbers pins it
        r = subprocess.run([sys.executable, CLI, "status", *args, "--project", self.p], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_function_counts_one_ledger(self):
        c = sl.ledger_counts(sl.load_ledger(self.p))
        self.assertEqual((c["doing"], c["next"], c["later"], c["done"]), (1, 4, 2, 2))
        self.assertEqual((c["version"], c["version_left"], c["version_total"]), ("0.13.2", 3, 4))

    def test_the_current_version_is_the_lowest_release_with_unfinished_work(self):
        data = sl.load_ledger(self.p)
        for it in data["items"]:
            if it["phase"] == "0.13.2":
                it["status"] = "done"
        self.assertEqual(sl.ledger_counts(data)["version"], "0.13.3")
        for it in data["items"]:
            if it["phase"] == "0.13.3":
                it["status"] = "done"
        c = sl.ledger_counts(data)
        self.assertIsNone(c["version"])
        self.assertEqual((c["version_left"], c["version_total"]), (0, 0))

    def test_brote_line_and_feed_show_the_same_numbers(self):
        full = sl.ledger_counts(sl.load_ledger(self.p))
        want = sl.feed_counts(sl.load_ledger(self.p))
        self.assertEqual(want, {k: full[k] for k in want})
        self.assertEqual(json.loads(self.status("--brote"))["counts"], want)
        self.assertEqual(self.feed(self.p)["projects"][0]["counts"], want)
        line = self.status("--line")
        self.assertRegex(line, r"1 en curso")
        self.assertRegex(line, r"4 por hacer")
        self.assertRegex(line, r"2 ideas")

    def test_status_speaks_of_the_current_version_not_of_the_mvp(self):
        out = self.status(lang="en")
        self.assertIn("0.13.2: 3 left of 4", out)
        self.assertNotIn("MVP", out)
        self.assertIn("items: 10 (7 open/doing)", out)
        summary = sl.session_summary(self.p, "s1")
        self.assertIn("0.13.2: 3 left of 4", summary)
        self.assertNotIn("MVP", summary)

    def test_without_a_release_the_mvp_text_stays(self):
        p = self.mk(self.proj("plain"), [("R-1", "open", {"phase": "mvp"}), ("R-2", "open", {"phase": "later"})])
        out = subprocess.run([sys.executable, CLI, "status", "--project", p], env=dict(os.environ),
                             capture_output=True, text=True).stdout
        self.assertIn("MVP 0/1 done", out)
        self.assertIsNone(sl.ledger_counts(sl.load_ledger(p))["version"])

    def test_the_ledger_is_a_watched_file_and_the_watch_pace_is_inside_ten_seconds(self):
        feed = self.feed(self.p)
        self.assertIn(os.path.realpath(os.path.join(sl.studio_dir(self.p), "ledger.json")), feed["watch"]["files"])
        with open(SWIFT, encoding="utf-8") as f:
            m = re.search(r"static let every = ([\d.]+), settle = ([\d.]+), gap = ([\d.]+)", f.read())
        every, settle, gap = (float(x) for x in m.groups())
        self.assertLessEqual(every + max(settle, gap), 10)         # a look, then the run (3 s apart at most) with the new ledger
        self.assertEqual(feed["watch"]["check_s"], 2)
