"""R-1288: her lane cap is recorded once and every project obeys it until the weekly reset.
Throwaway state only: everything lives under a temp folder made by the shared test case."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import capacity
import studiolib as sl
from test_capacity import GateCase, CLI

INPUTS = {"level": "critical", "pace": None, "projected": None, "h5": None, "nodata": False}


def row(slug, private=False):
    return {"slug": slug, "path": "/nowhere/" + slug, "priority": "normal", "paused": False, "committed": 3, "ready": 3,
            "in_flight": 0, "stuck": 0, "waited_s": 0, "private": private, "ready_items": []}


class CapCase(GateCase):
    def setUp(self):
        super().setUp()
        real = os.path.realpath
        self.assertTrue(real(self.home).startswith(real(tempfile.gettempdir())), "state must live in the temp dir")
        self.budget.set_override("critical")
        data = sl.load_ledger(self.project)
        self.more = [sl.add_item(data, "x%d" % n, ["legacy"])["id"] for n in range(6)]
        sl.save_ledger(self.project, data)
        self.sessions = ["s-flow%04d" % n for n in range(1, 7)]
        for s in self.sessions[2:]:
            sl.activate(self.project, s)

    def typed(self):
        return sl.append_inbox(self.project, "hasta 4 en paralelo", "s-flow0001", "prompt")

    def cap_cli(self, *args, lang="es"):
        env = dict(os.environ, STUDIO_HOME=self.home, HOME=self.home, CLAUDE_CONFIG_DIR=self.home, BUDGIT_HOME=self.home,
                   BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, "capacity", *args], cwd=self.project, env=env,
                              capture_output=True, text=True)


class CapTest(CapCase):
    def test_critical_with_cap_4_allows_four_starts_and_refuses_the_fifth(self):
        capacity.set_cap(4, self.typed())
        items = [self.a, self.b] + self.more
        for n in range(4):
            self.start_in(items[n], self.sessions[n])                      # no override-by anywhere
        with self.assertRaises(sl.StudioError):
            self.start_in(items[4], self.sessions[4])

    def test_expired_cap_reverts_to_the_level_ceiling(self):
        capacity.set_cap(4, self.typed(), now=time.time() - 8 * 86400)     # its week ended a day ago
        pic = capacity.compute(inputs=INPUTS, write=False)
        self.assertEqual(pic["T"], 1)
        self.assertNotEqual(pic["reason"], "user_cap")
        self.assertEqual(pic["cap_expired"]["n"], 4)

    def test_by_with_an_unknown_input_is_refused(self):
        r = self.cap_cli("set", "4", "--by", "I-999")
        self.assertNotEqual(r.returncode, 0)
        self.assertIsNone(capacity.load_state().get("user_cap"))

    def test_clear_removes_the_cap(self):
        by = self.typed()
        r = self.cap_cli("set", "4", "--by", by)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(capacity.load_state()["user_cap"]["n"], 4)
        self.assertEqual(self.cap_cli("set", "--clear").returncode, 0)
        self.assertIsNone(capacity.load_state().get("user_cap"))

    def test_report_line_names_the_input_and_the_date_in_both_languages(self):
        by = self.typed()
        self.assertEqual(self.cap_cli("set", "4", "--by", by).returncode, 0)
        date = time.strftime("%Y-%m-%d", time.localtime(capacity.load_state()["user_cap"]["until"]))
        es = self.cap_cli().stdout
        en = self.cap_cli(lang="en").stdout
        self.assertIn("Límite 4 por proyecto por decisión suya (%s) hasta %s" % (by, date), es)
        self.assertIn("Limit 4 per project by your decision (%s) until %s" % (by, date), en)

    def test_expiry_line_prints_once(self):
        capacity.set_cap(4, self.typed(), now=time.time() - 8 * 86400)
        first = self.cap_cli(lang="en").stdout
        second = self.cap_cli(lang="en").stdout
        self.assertIn("expired on", first)
        self.assertNotIn("expired on", second)
        self.assertIsNone(capacity.load_state().get("user_cap_expired"))

    def test_set_and_clear_are_audited_with_the_input(self):
        by = self.typed()
        self.assertEqual(self.cap_cli("set", "4", "--by", by).returncode, 0)
        self.assertIn("lane cap set to 4 (asked in %s)" % by, self.audit_text())
        self.assertEqual(self.cap_cli("set", "--clear").returncode, 0)
        self.assertIn("lane cap of 4 cleared (set in %s)" % by, self.audit_text())

    def test_cap_applies_per_project_with_six_live_projects(self):
        capacity.set_cap(4, self.typed())
        real = capacity.portfolio_snapshot

        def six(now, current):
            rows, skipped = real(now, current)
            return rows + [row("p%d" % n) for n in range(5)], skipped
        items = [self.a, self.b] + self.more
        with mock.patch.object(capacity, "portfolio_snapshot", six):
            pic = capacity.compute(inputs=INPUTS, write=False)
            mine = next(v for v in pic["projects"].values() if os.path.realpath(v["path"]) == os.path.realpath(self.project))
            self.assertEqual(mine["slots"], 4)
            self.assertTrue(all(v["slots"] == 4 for v in pic["projects"].values()))
            for n in range(4):
                self.start_in(items[n], self.sessions[n])                  # no override-by
            with self.assertRaises(sl.StudioError):
                self.start_in(items[4], self.sessions[4])

    def test_protected_project_keeps_its_seat_under_her_cap(self):
        capacity.set_cap(2, self.typed())
        rows = [row("p1"), row("p2"), row("p3", private=True)]
        with mock.patch.object(capacity, "portfolio_snapshot", lambda now, current: (rows, [])):
            pic = capacity.compute(inputs=INPUTS, write=False)
        self.assertEqual(pic["reason"], "user_cap")
        self.assertGreaterEqual(pic["projects"]["p3"]["slots"], 1)


if __name__ == "__main__":
    unittest.main()
