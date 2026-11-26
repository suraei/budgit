"""R-238: the bar under the prompt appears in every folder (walk-up to the project, an account-only line without one) and
`status --probe` tells what a surface and a folder resolve to."""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class FindProjectTest(StudioTestCase):
    def deep(self, *parts):
        path = os.path.join(self.project, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def test_a_subfolder_finds_the_project_above_it(self):
        self.init_project()
        self.assertEqual(sl.find_project(self.deep("a", "b", "c")), os.path.realpath(self.project))
        self.assertEqual(sl.find_project(self.project), os.path.realpath(self.project))

    def test_no_ledger_anywhere_is_none(self):
        self.assertIsNone(sl.find_project(self.deep("a", "b")))
        self.assertIsNone(sl.find_project(os.path.join(self.tmp, "does", "not", "exist")))

    def test_it_looks_at_most_eight_folders_up(self):
        self.init_project()
        eight = self.deep(*"abcdefgh")
        nine = self.deep(*"abcdefghi")
        self.assertEqual(sl.find_project(eight), os.path.realpath(self.project))
        self.assertIsNone(sl.find_project(nine))

    def test_it_never_returns_the_home_folder_or_anything_above_it(self):
        home = os.path.join(self.tmp, "fakehome")
        work = os.path.join(home, "dev", "app")
        os.makedirs(work)
        for here in (home, os.path.join(self.tmp)):
            os.makedirs(os.path.join(here, self.DATA), exist_ok=True)
            sl.save_ledger(here, sl.empty_ledger())
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertIsNone(sl.find_project(work))          # the ledgers at HOME and above are not this folder's project
            self.assertIsNone(sl.find_project(home))

    def test_a_project_inside_home_is_found(self):
        home = os.path.join(self.tmp, "fakehome")
        proj = os.path.join(home, "dev", "app")
        os.makedirs(os.path.join(proj, "src"))
        os.makedirs(os.path.join(proj, self.DATA))
        sl.save_ledger(proj, sl.empty_ledger())
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(sl.find_project(os.path.join(proj, "src")), os.path.realpath(proj))

    def test_the_walk_up_is_fast_on_a_twelve_level_path(self):       # [perf] under 5 ms
        import time
        deep = self.deep(*"abcdefghijkl")
        start = time.process_time()                                    # CPU time, not wall: a loaded machine cannot fail it (R-1282)
        for _ in range(20):
            sl.find_project(deep)
        self.assertLess((time.process_time() - start) / 20, 0.005)

    def test_resolve_project_with_an_explicit_folder_still_needs_the_ledger_there(self):
        self.init_project()
        with self.assertRaises(sl.StudioError):
            sl.resolve_project(self.deep("a"))                 # writes stay exact: only the status lines walk up


class BarCliTest(StudioTestCase):
    def cli(self, *args, lang="es"):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.tmp, env=env, capture_output=True, text=True)

    def record_week(self, used):
        import time
        self.cli("budget", "record", "--seven-day", str(used), "--seven-day-resets", str(time.time() + 3 * 86400))

    def test_a_subfolder_gets_the_projects_line(self):
        self.init_project()
        sub = os.path.join(self.project, "tests")
        os.makedirs(sub)
        r = self.cli("status", "--line", "--project", sub)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("proj \u00b7 sin tareas en Budgit"), r.stdout)

    def test_a_folder_without_a_ledger_gets_the_account_only_line(self):
        r = self.cli("status", "--line", "--project", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(r.stdout.rstrip("\n"), "budgit  ◌ sin libreta aquí")

    def test_the_account_part_keeps_the_plan_limit_when_it_is_known(self):
        self.record_week(40)
        line = self.cli("status", "--line", "--project", self.project).stdout.rstrip("\n")
        self.assertTrue(line.startswith("budgit  ◌ sin libreta aquí ┊ "), line)
        self.assertIn("%", line)
        self.assertLessEqual(len(line), 80)

    def test_english(self):
        r = self.cli("status", "--line", "--project", self.project, lang="en")
        self.assertEqual(r.stdout.rstrip("\n"), "budgit  ◌ no ledger here")

    def test_a_missing_folder_is_not_an_error(self):
        r = self.cli("status", "--line", "--project", os.path.join(self.tmp, "gone"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.rstrip("\n"), "budgit  ◌ sin libreta aquí")

    def test_the_account_line_carries_nothing_of_any_project(self):
        self.init_project()
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        line = self.cli("status", "--line", "--project", other).stdout
        self.assertNotIn("en curso", line)
        self.assertNotIn(os.path.basename(self.project), line)

    def test_the_full_status_and_writes_still_need_the_exact_folder(self):
        self.init_project()
        sub = os.path.join(self.project, "tests")
        os.makedirs(sub)
        self.assertNotEqual(self.cli("status", "--project", sub).returncode, 0)
        self.assertNotEqual(self.cli("ledger", "list", "--project", sub).returncode, 0)

    def test_brote_json_walks_up_too_and_is_empty_without_a_project(self):
        self.init_project()
        sub = os.path.join(self.project, "tests")
        os.makedirs(sub)
        self.assertEqual(json.loads(self.cli("status", "--brote", "--project", sub).stdout)["state"] in ("unknown", "radiante", "contento", "justo", "cansado", "agotado", "enfermo", "dormido"), True)
        self.assertEqual(self.cli("status", "--brote", "--project", self.tmp).stdout, "")

    def test_the_probe_names_surface_engine_plugin_folder_project_and_line(self):
        self.init_project()
        sub = os.path.join(self.project, "tests")
        os.makedirs(sub)
        r = self.cli("status", "--probe", "--project", sub, "--surface", "desktop", "--engine", "2.1.308", "--shown", "budgit  una linea")
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in ("desktop", "2.1.308", "budgit  una linea", os.path.realpath(self.project), os.path.realpath(sub)):
            self.assertIn(word, r.stdout)
        with open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            self.assertIn(json.load(f)["version"], r.stdout)

    def test_the_probe_in_a_folder_without_a_project_says_so_in_both_languages(self):
        es = self.cli("status", "--probe", "--project", self.project, "--surface", "terminal", "--engine", "2.1.308", "--shown", "x")
        en = self.cli("status", "--probe", "--project", self.project, "--surface", "terminal", "--engine", "2.1.308", "--shown", "x", lang="en")
        self.assertIn("ninguno", es.stdout)
        self.assertIn("none", en.stdout)

    def test_the_probe_text_is_one_bounded_block_without_control_characters(self):
        r = self.cli("status", "--probe", "--project", self.project, "--surface", "x\x1b[31m" + "s" * 500, "--engine", "e\n" * 50, "--shown", "l" * 900)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("\x1b", r.stdout)
        self.assertLessEqual(len(r.stdout), 1500)


if __name__ == "__main__":
    unittest.main()
