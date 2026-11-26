"""A closed item without done_at (closed before the field existed) is never given a made-up time: `ledger show` says
"date unknown", and nothing that reads the ledger crashes on the missing value."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class DoneAtUnknownTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        data = sl.load_ledger(self.project)
        for n in range(3):
            sl.add_item(data, "item %d" % n, ["legacy"], phase="mvp")
        sl.save_ledger(self.project, data)
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        raw["items"][0].update(status="done", evidence="old")                # closed long ago: no done_at
        raw["items"][1].update(status="verified", evidence="old", done_at_unproven=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        sl.accept_edit(self.project)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def test_show_says_date_unknown(self):
        for item_id in ("R-1", "R-2"):
            r = self.cli("ledger", "show", item_id)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("done_at: date unknown", r.stdout)
        self.assertNotIn("done_at", self.cli("ledger", "show", "R-3").stdout)    # an open item has no close date
        self.assertNotIn("done_at", sl.find_item(sl.load_ledger(self.project), "R-1"))   # nothing was written

    def test_no_reader_crashes(self):
        for args in (("status",), ("ledger", "list"), ("ledger", "list", "--json"), ("ledger", "top"),
                     ("ledger", "show", "R-1", "--json"), ("release-check",), ("release-check", "--items", "R-1"),
                     ("ledger", "repair-done-at"), ("status", "--brote")):
            r = self.cli(*args)
            self.assertNotIn("Traceback", r.stderr + r.stdout, args)
