"""R-493 / I-582: Budgit's own folders (.studio/**, .budgit/**) are never protected real data, whatever the project's patterns
say; only the project's actual data store (`plans/*.handles.json` here) is. Synthetic project: protected plans/*.handles.json
and plans/real/**, plus Budgit's own .studio/plans and .budgit/plans."""
import json
import os
import unittest

from test_guard_trust import GuardCase

import studiolib as sl


class PlansCase(GuardCase):
    def setUp(self):
        super().setUp()
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        with open(pj) as fh:
            cfg = json.load(fh)
        cfg["protected_paths"] = ["plans/*.handles.json", "plans/real/**", "**/plans/*.handles.json"]
        with open(pj, "w") as fh:
            json.dump(cfg, fh)
        for rel in ("plans/a.handles.json", "plans/real/x.csv", "plans/notes.md", ".studio/plans/x.md", ".budgit/plans/y.md"):
            path = os.path.join(self.project, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("one\n")

    def tool(self, name, rel, **extra):
        return sl.real_data_decision(name, dict({"file_path": os.path.join(self.root, rel)}, **extra), self.project, cwd=self.project)


class OwnFoldersAreNotRealDataTest(PlansCase):
    def test_write_and_edit_of_budgits_own_plans_pass(self):
        for rel in (".studio/plans/x.md", ".studio/plans/new.md", ".budgit/plans/y.md", ".budgit/ledger.json"):
            self.assertIsNone(self.tool("Write", rel, content="x"), rel)
            self.assertIsNone(self.tool("Edit", rel, old_string="a", new_string="b"), rel)

    def test_bash_writes_into_budgits_own_folders_pass(self):
        r = self.root
        self.passes("printf x > %s/.studio/plans/x.md" % r, "echo x >> %s/.budgit/plans/y.md" % r,
                    "cp /tmp/a %s/.studio/plans/z.md" % r, "rm %s/.studio/plans/x.md" % r,
                    "cd %s/.studio/plans && ls" % r)

    def test_budgit_folders_stay_out_even_when_a_pattern_would_cover_them(self):
        self.assertIsNone(self.d("printf x > %s/.studio/plans/a.handles.json" % self.root))

    def test_the_real_data_store_is_still_guarded(self):
        for rel in ("plans/a.handles.json", "plans/real/x.csv"):
            for name in ("Write", "Edit"):
                got = self.tool(name, rel, content="x")
                self.assertIsNotNone(got, rel)
                self.assertEqual(got[0], "ask", rel)
        self.asks("printf x > %s/plans/a.handles.json" % self.root, "rm %s/plans/real/x.csv" % self.root,
                  "sed -i s/a/b/ %s/plans/a.handles.json" % self.root)

    def test_a_plans_folder_beside_the_store_follows_todays_rules(self):
        self.passes("printf x > %s/plans/notes.md" % self.root)


if __name__ == "__main__":
    unittest.main()
