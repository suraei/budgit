"""R-1368: brain lint flags a plan file whose name breaks the one naming rule.

Rule: a file under <data>/plans is `YYYY-MM-DD-...` or lives in plans/briefs/ ; drafts live in <data>/archive.
Only files the repo tracks get an ERROR; untracked or ignored ones give one WARN with their count.
"""
import os
import subprocess
import unittest

from helpers import ROOT
from test_brain import BrainCase, note


class GitignoreTest(unittest.TestCase):
    def test_r1368_patterns_are_ignored(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            lines = {l.strip() for l in f}
        for pat in (".studio/plans/", ".studio/gold/", ".studio/friction.md", ".studio/progress-*.md"):
            self.assertIn(pat, lines)


class PlanNameTest(BrainCase):
    def track(self, *rels):
        """Put the files in a real temporary repo's index (init + add -f)."""
        if not os.path.isdir(os.path.join(self.project, ".git")):
            subprocess.run(["git", "init", "-q"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "add", "-f", "--"] + list(rels), cwd=self.project, check=True, capture_output=True)

    def issues(self):
        import brain
        return [i for i in brain.lint(self.brain()) if i.cls == "plan-name"]

    def plan_errors(self):
        return [i for i in self.issues() if i.level == "ERROR"]

    def test_dated_plan_passes(self):
        self.put(".studio/plans/2026-11-20-R-1259-receipt.md", "# plan\n")
        self.track(".studio/plans/2026-11-20-R-1259-receipt.md")
        self.assertEqual(self.issues(), [])

    def test_undated_tracked_plan_is_flagged(self):
        self.put(".studio/plans/R-1259-receipt.md", "# plan\n")
        self.track(".studio/plans/R-1259-receipt.md")
        errs = self.plan_errors()
        self.assertEqual([e.rel for e in errs], [".studio/plans/R-1259-receipt.md"])
        self.assertEqual(errs[0].level, "ERROR")

    def test_undated_untracked_plan_gives_no_error(self):
        self.put(".studio/plans/R-1259-receipt.md", "# plan\n")
        self.put(".studio/plans/notes.md", "# plan\n")
        self.assertEqual(self.plan_errors(), [])
        warns = self.issues()
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0].level, "WARN")
        self.assertIn("2", warns[0].msg)

    def test_untracked_next_to_tracked_gives_one_warn_no_error(self):
        self.put(".studio/plans/2026-11-20-R-1-a.md", "# plan\n")
        self.track(".studio/plans/2026-11-20-R-1-a.md")
        self.put(".studio/plans/R-2-b.md", "# plan\n")
        self.assertEqual(self.plan_errors(), [])
        self.assertEqual(len(self.issues()), 1)

    def test_briefs_subfolder_is_exempt(self):
        self.put(".studio/plans/briefs/R-1259-brief.md", "# brief\n")
        self.track(".studio/plans/briefs/R-1259-brief.md")
        self.assertEqual(self.issues(), [])

    def test_archive_folder_is_not_scanned(self):
        self.put(".studio/archive/R-1259-draft.v2.md", "# draft\n")
        self.track(".studio/archive/R-1259-draft.v2.md")
        self.assertEqual(self.issues(), [])

    def test_old_data_name_is_also_checked(self):
        self.put(".budgit/plans/notes.md", "# x\n")
        self.track(".budgit/plans/notes.md")
        self.assertEqual([e.rel for e in self.plan_errors()], [".budgit/plans/notes.md"])

    def test_date_without_dash_is_flagged(self):
        self.put(".studio/plans/20261120-x.md", "# x\n")
        self.track(".studio/plans/20261120-x.md")
        self.assertEqual(len(self.plan_errors()), 1)

    def test_other_extensions_in_plans_are_checked_too(self):
        self.put(".studio/plans/gates.sh", "echo\n")
        self.track(".studio/plans/gates.sh")
        self.assertEqual(len(self.plan_errors()), 1)

    def test_deeper_nesting_other_than_briefs_is_flagged(self):
        self.put(".studio/plans/sub/a.md", "# x\n")
        self.track(".studio/plans/sub/a.md")
        self.assertEqual(len(self.plan_errors()), 1)
