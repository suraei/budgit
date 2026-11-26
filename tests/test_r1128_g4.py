"""R-1128 hotfix, group G4: a scheduled task's instruction file ships and is checked; no shipped instruction asks for a write
the guards deny, or names a skill that may be absent."""
import os
import re
import sys
import tempfile
import unittest

from helpers import BudgitTestCase
import jobs
import jobhealth


class InstructionFiles(BudgitTestCase):
    def test_EveryModelJobHasAnInstructionFileNextToTheScripts(self):
        # the export copies plugin/scripts whole, so a file under it ships; the old folder next to scripts/ did not
        scripts = os.path.dirname(os.path.abspath(jobs.__file__))
        for name in jobs.MODEL_JOBS:
            path = jobs.prompt_path(name)
            self.assertTrue(os.path.isfile(path), name)
            self.assertTrue(path.startswith(scripts + os.sep), name)
            self.assertTrue(jobs.prompt_text(name).startswith(("[budgit-job]",)) or "[budgit-job]" in jobs.prompt_text(name))

    def test_MissingInstructionFileIsAProblemInPlainWords(self):
        found = jobhealth.problems(now=1000.0, state={}, sched={"standup": 999.0}, prompt_exists=lambda p: False)
        self.assertEqual([(n, k) for n, k, _ in found], [("standup", "noprompt")])
        text = jobhealth.line(now=1000.0, state={}, sched={"standup": 999.0}, lang="en", prompt_exists=lambda p: False)
        self.assertIn("instruction file is missing", text)
        es = jobhealth.line(now=1000.0, state={}, sched={"keepalive": 999.0}, lang="es", prompt_exists=lambda p: False)
        self.assertIn("falta su archivo de instrucciones", es)

    def test_CleanInstallWithTheFilesPresentReportsNothing(self):
        self.assertEqual(jobhealth.problems(now=1000.0, state={}, sched={"standup": 999.0}), [])

    def test_ASyntheticCleanInstallResolvesEveryPrompt(self):
        # copy only plugin/scripts (what the export ships) into a scratch folder and resolve each prompt from there
        import shutil
        src = os.path.dirname(os.path.abspath(jobs.__file__))
        dst = os.path.join(self.tmp, "clean", "scripts")
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
        for name in jobs.MODEL_JOBS:
            self.assertTrue(os.path.isfile(os.path.join(dst, "prompts", name + ".md")), name)


class NoContradictingInstructions(BudgitTestCase):
    def test_CheckpointInstructionWriteIsAllowedByEveryGuard(self):
        import studiolib as sl
        import writeguard
        self.init_project()
        path = os.path.join(self.project, ".budgit", "progress.md")
        with open(path, "w") as f:
            f.write("old\n")
        import i18n
        for lang in ("en", "es"):
            self.assertIn(".budgit/progress.md", i18n.t("r1284.checkpoint", lang))
        for tool, ti in (("Write", {"file_path": path, "content": "new\n"}),
                         ("Edit", {"file_path": path, "old_string": "old", "new_string": "new"})):
            self.assertIsNone(sl.verify_forgery_decision(tool, ti, None, self.project), tool)
            self.assertIsNone(sl.home_write_check(tool, ti, self.project, None)[0], tool)
            self.assertIsNone(sl.contract_lock_decision(tool, ti, self.project, cwd=self.project), tool)
            self.assertIsNone(sl.real_data_check(tool, ti, self.project, cwd=self.project)[0], tool)
            self.assertIsNone(writeguard.persistence_decision(tool, ti, self.project), tool)

    def test_OtherBudgitFilesStayProtected(self):
        import studiolib as sl
        self.init_project()
        verify = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        got = sl.verify_forgery_decision("Write", {"file_path": verify, "content": "PASS\n"}, None, self.project)
        self.assertIsNotNone(got)

    def test_EverySkillNamedInAShippedInstructionIsMarkedOptional(self):
        import general
        for text in (general.HUMANIZE_ROUTE, general.CLIENT_ROUTE, general.GATE_INSTRUCTIONS):
            for m in re.finditer(r"stop-slop", text):
                tail = text[m.start():m.start() + 160]
                self.assertRegex(tail, r"if (?:it is )?installed|if it is not")


if __name__ == "__main__":
    unittest.main()
