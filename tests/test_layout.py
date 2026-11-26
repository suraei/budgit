"""R-306: plans, research, reports and handovers live in the data folder; `budgit layout` lists, moves and undoes."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import layout
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class LayoutTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.config = {}

    def put(self, rel, text="x"):
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def cli(self, *args, config=None):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump(config or {}, f)
        return subprocess.run([sys.executable, CLI, "layout", "--project", self.project] + list(args), capture_output=True, text=True,
                              env=dict(os.environ, STUDIO_HOME=self.home))

    def test_only_documents_in_the_well_known_wrong_folders_are_found(self):
        self.put("docs/plans/a.md")
        self.put("research/b.md")
        self.put("docs/reports/c.html")
        self.put("docs/handoffs/d.txt")
        self.put("docs/manual.md")                                    # the product's own docs are not touched
        self.put("src/plans/e.md")                                    # not a well-known place
        self.put("docs/plans/code.py")                                # not a document
        self.put(".studio/plans/ok.md")                               # already in the data folder
        got = {(i["from"], i["to"]) for i in layout.scan(self.project, self.config)["items"]}
        self.assertEqual(got, {("docs/plans/a.md", ".studio/plans/a.md"), ("research/b.md", ".studio/research/b.md"),
                               ("docs/reports/c.html", ".studio/reports/c.html"), ("docs/handoffs/d.txt", ".studio/handover/d.txt")})

    def test_a_protected_project_is_counted_and_never_named_or_moved(self):
        self.put("docs/plans/secret-name.md")
        cfg = {"protected_dirs": [self.project]}
        got = layout.scan(self.project, cfg)
        self.assertEqual((got["protected"], got["count"], got["items"]), (True, 1, []))
        r = self.cli("--move", "--yes", config=cfg)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("secret-name", r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.project, "docs", "plans", "secret-name.md")))
        self.assertNotIn("secret-name", self.cli(config=cfg).stdout)

    def test_a_dry_run_changes_nothing_and_move_yes_moves_then_undo_puts_back(self):
        self.put("docs/plans/a.md", "A")
        self.put("docs/plans/sub/b.md", "B")
        r = self.cli("--move")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.project, "docs", "plans", "a.md")))
        r = self.cli("--move", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.project, ".studio", "plans", "sub", "b.md")) as f:
            self.assertEqual(f.read(), "B")
        self.assertFalse(os.path.exists(os.path.join(self.project, "docs", "plans")))        # emptied folders go
        r = self.cli("--undo")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.project, "docs", "plans", "a.md")) as f:
            self.assertEqual(f.read(), "A")
        self.assertFalse(os.path.exists(os.path.join(self.project, ".studio", "plans", "a.md")))
        self.assertEqual(self.cli("--undo").returncode, 1)                                    # nothing recorded any more

    def test_a_document_already_at_the_target_is_never_overwritten(self):
        self.put("research/a.md", "old")
        self.put(".studio/research/a.md", "new")
        r = layout.move(self.project, apply=True, config=self.config)
        self.assertEqual((r["moved"], r["kept"]), (0, 1))
        with open(os.path.join(self.project, ".studio", "research", "a.md")) as f:
            self.assertEqual(f.read(), "new")
        with open(os.path.join(self.project, "research", "a.md")) as f:
            self.assertEqual(f.read(), "old")

    def test_a_data_folder_that_is_a_symlink_is_never_written_through(self):
        import shutil
        outside = os.path.join(self.tmp if hasattr(self, "tmp") else os.path.dirname(self.project), "outdata-link-target")
        os.makedirs(outside, exist_ok=True)
        self.put("research/a.md", "keep me here")
        shutil.rmtree(os.path.join(self.project, ".studio"), ignore_errors=True)
        os.symlink(outside, os.path.join(self.project, ".studio"))
        with self.assertRaises(sl.StudioError):
            layout.move(self.project, apply=True, config=self.config)
        self.assertTrue(os.path.exists(os.path.join(self.project, "research", "a.md")))
        self.assertEqual(os.listdir(outside), [])

    def test_a_symlink_above_the_well_known_folder_is_never_followed(self):
        outside = os.path.join(self.tmp, "elsewhere-docs")
        os.makedirs(os.path.join(outside, "plans"))
        with open(os.path.join(outside, "plans", "z.md"), "w") as f:
            f.write("z")
        os.symlink(outside, os.path.join(self.project, "docs"))
        self.assertEqual(layout.scan(self.project, self.config)["count"], 0)
        layout.move(self.project, apply=True, config=self.config)
        self.assertTrue(os.path.exists(os.path.join(outside, "plans", "z.md")))

    def test_a_symlinked_target_folder_below_the_data_folder_refuses_before_anything_moves(self):
        outside = os.path.join(self.tmp, "elsewhere-plans")
        os.makedirs(outside)
        self.put("docs/plans/a.md", "keep me here")
        self.put("research/b.md", "keep me too")
        os.symlink(outside, os.path.join(self.project, ".studio", "plans"))
        with self.assertRaises(sl.StudioError):
            layout.move(self.project, apply=True, config=self.config)
        self.assertEqual(os.listdir(outside), [])
        self.assertTrue(os.path.exists(os.path.join(self.project, "docs", "plans", "a.md")))
        self.assertTrue(os.path.exists(os.path.join(self.project, "research", "b.md")))

    def test_a_failure_midway_leaves_an_undo_record_for_what_already_moved(self):
        self.put("docs/plans/a.md", "A")
        self.put("docs/plans/b.md", "B")
        real, calls = os.rename, []

        def flaky(a, b):
            calls.append(a)
            if len(calls) == 2:
                raise OSError("cross-device")
            return real(a, b)
        layout.os.rename = flaky
        self.addCleanup(lambda: setattr(layout.os, "rename", real))
        with self.assertRaises(OSError):
            layout.move(self.project, apply=True, config=self.config)
        layout.os.rename = real
        self.assertTrue(os.path.exists(os.path.join(self.project, ".studio", "plans", "a.md")))
        self.assertEqual(layout.undo(self.project), 1)
        self.assertTrue(os.path.exists(os.path.join(self.project, "docs", "plans", "a.md")))
        self.assertTrue(os.path.exists(os.path.join(self.project, "docs", "plans", "b.md")))

    def test_undo_never_moves_a_document_back_through_a_symlink(self):
        self.put("docs/plans/a.md", "A")
        layout.move(self.project, apply=True, config=self.config)
        outside = os.path.join(self.tmp, "elsewhere-undo")
        os.makedirs(outside)
        os.rmdir(os.path.join(self.project, "docs", "plans")) if os.path.isdir(os.path.join(self.project, "docs", "plans")) else None
        __import__("shutil").rmtree(os.path.join(self.project, "docs"), ignore_errors=True)
        os.symlink(outside, os.path.join(self.project, "docs"))
        self.assertEqual(layout.undo(self.project), 0)
        self.assertEqual(os.listdir(outside), [])
        self.assertTrue(os.path.exists(os.path.join(self.project, ".studio", "plans", "a.md")))

    def test_a_git_project_keeps_history_and_undo_restores_the_index(self):
        subprocess.run(["git", "init", "-q", self.project], check=True)
        self.put("docs/plans/a.md", "A")
        subprocess.run(["git", "-C", self.project, "add", "."], check=True)
        subprocess.run(["git", "-C", self.project, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "x"], check=True)
        layout.move(self.project, apply=True, config=self.config)
        status = subprocess.run(["git", "-C", self.project, "status", "--short"], capture_output=True, text=True).stdout
        self.assertIn("R  docs/plans/a.md -> .studio/plans/a.md", status)
        layout.undo(self.project)
        status = subprocess.run(["git", "-C", self.project, "status", "--short"], capture_output=True, text=True).stdout
        self.assertNotIn("a.md", status.replace("??", ""), status)

    def test_a_project_can_keep_its_own_layout(self):
        self.put("docs/plans/a.md")
        with open(os.path.join(self.project, ".studio", "project.json"), "w") as f:
            json.dump({"layout": "keep"}, f)
        self.assertEqual(layout.scan(self.project, self.config)["count"], 0)
        self.assertIsNone(layout.advice(self.project, "docs/plans/b.md", self.config))
        self.assertEqual(layout.scan(self.project, {"layout": "off"})["count"], 0)

    def test_symlinks_are_not_followed_or_moved(self):
        outside = os.path.join(self.tmp, "elsewhere")
        self.put("docs/plans/real.md")
        os.makedirs(outside)
        with open(os.path.join(outside, "z.md"), "w") as f:
            f.write("z")
        os.symlink(os.path.join(outside, "z.md"), os.path.join(self.project, "docs", "plans", "link.md"))
        os.symlink(outside, os.path.join(self.project, "research"))
        got = [i["from"] for i in layout.scan(self.project, self.config)["items"]]
        self.assertEqual(got, ["docs/plans/real.md"])

    def test_the_advice_names_where_a_document_about_to_be_saved_belongs(self):
        got = layout.advice(self.project, os.path.join(self.project, "docs", "plans", "new.md"), self.config)
        self.assertEqual(got, {"kind": "plans", "to": ".studio/plans/new.md"})
        for ok in (".studio/plans/new.md", "docs/manual.md", "src/app.py", "docs/plans/data.json"):
            self.assertIsNone(layout.advice(self.project, ok, self.config), ok)
        self.assertIsNone(layout.advice(self.project, "/etc/hosts", self.config))
        self.assertIsNone(layout.advice(self.project, "../outside/docs/plans/a.md", self.config))


if __name__ == "__main__":
    import unittest
    unittest.main()
