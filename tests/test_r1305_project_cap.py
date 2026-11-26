"""R-1305: a lane cap can be set for one project; the report shows the real portfolio total.
Throwaway state only (the shared test case keeps everything under a temp folder); fake rows stand in for other projects."""
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

import capacity
from test_capacity import CLI
from test_r1288_capacity_cap import CapCase, INPUTS, row


class ProjectCapCase(CapCase):
    def run_cap(self, *args, lang="es", project=None):
        env = dict(os.environ, STUDIO_HOME=self.home, HOME=self.home, CLAUDE_CONFIG_DIR=self.home, BUDGIT_HOME=self.home,
                   BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        argv = [sys.executable, CLI, "capacity", *args] + (["--project", project] if project else [])
        return subprocess.run(argv, cwd=self.project, env=env, capture_output=True, text=True)

    def real_slug(self):
        return capacity.portfolio_snapshot()[0][0]["slug"]

    def pic(self):
        real = capacity.portfolio_snapshot

        def more(now, current):
            rows, skipped = real(now, current)
            return rows + [row("p0"), row("p1")], skipped
        with mock.patch.object(capacity, "portfolio_snapshot", more):
            return capacity.compute(inputs=INPUTS, write=False)


class ProjectCapTest(ProjectCapCase):
    def test_two_projects_have_different_caps(self):
        by, mine = self.typed(), self.real_slug()
        capacity.set_cap(2, by, slug=mine)
        capacity.set_cap(3, by, slug="p0")
        slots = {k: v["slots"] for k, v in self.pic()["projects"].items()}
        self.assertEqual((slots[mine], slots["p0"]), (2, 3))
        self.assertEqual(slots["p1"], 1)                                   # no cap of its own: the level decides

    def test_all_projects_cap_with_a_project_override(self):
        by, mine = self.typed(), self.real_slug()
        capacity.set_cap(4, by)
        capacity.set_cap(2, by, slug="p0")
        slots = {k: v["slots"] for k, v in self.pic()["projects"].items()}
        self.assertEqual((slots[mine], slots["p0"], slots["p1"]), (4, 2, 4))     # the project's own cap wins

    def test_clear_for_one_project_leaves_the_others(self):
        by, mine = self.typed(), self.real_slug()
        capacity.set_cap(4, by)
        capacity.set_cap(3, by, slug="p0")
        r = self.run_cap("set", "2", "--by", by, project=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(capacity.load_state()["project_caps"][mine]["n"], 2)
        self.assertIn("project %s" % mine, self.audit_text())               # the audit line names the project
        r = self.run_cap("set", "--clear", project=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        state = capacity.load_state()
        self.assertEqual(sorted(state["project_caps"]), ["p0"])
        self.assertEqual(state["user_cap"]["n"], 4)

    def test_report_total_line_in_both_languages(self):
        by = self.typed()
        self.assertEqual(self.run_cap("set", "4", "--by", by).returncode, 0)
        date = time.strftime("%Y-%m-%d", time.localtime(capacity.load_state()["user_cap"]["until"]))
        es = self.run_cap().stdout
        en = self.run_cap(lang="en").stdout
        self.assertIn("Límite 4 por proyecto por decisión suya (%s) hasta %s · total ahora 0 de 4 posibles" % (by, date), es)
        self.assertIn("Limit 4 per project by your decision (%s) until %s · total now 0 of 4 possible" % (by, date), en)

    def test_a_live_cap_does_not_change_the_busy_decision(self):
        import admit
        calm = {"pressure": 1, "free_true_mb": 200, "compressor_mb": 500, "total_mb": 16000}
        full = dict(calm, compressor_mb=12000, free_true_mb=6000)

        def decide(mem):
            return admit.decide({"cpu_load": 1.0, "cores": 8, "free_mb": mem["free_true_mb"], "busy": 0, "mem": mem}, "lane", {})
        before = (decide(calm)["verdict"], decide(full)["verdict"])
        capacity.set_cap(8, self.typed())
        capacity.set_cap(8, self.typed(), slug=self.real_slug())
        self.assertEqual((decide(calm)["verdict"], decide(full)["verdict"]), before)
        self.assertEqual(before, ("go", "refuse"))


if __name__ == "__main__":
    unittest.main()
