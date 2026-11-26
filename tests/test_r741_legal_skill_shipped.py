"""R-741 (her «Sí, en Budgit»): the legal-eu-es-fr skill ships in the public export; its hub-only cases/ folder and registry
draft stay out, and no report, snapshot or other state of the skill is ever in the shipped set."""
import os
from unittest import mock

from helpers import StudioTestCase, ROOT
import export

SKILL = os.path.join(ROOT, "skills", "legal-eu-es-fr")
STATE_DIRS = ("cases", "reports", "report", "state", "cache", "logs", "snapshot")


class LegalSkillShippedTest(StudioTestCase):
    def _shipped(self):
        return {os.path.relpath(p, SKILL) for p in export.shipped_text_files(ROOT) if p.startswith(SKILL + os.sep)}

    def test_the_shipped_file_set_contains_the_skill(self):
        rels = self._shipped()
        self.assertIn("SKILL.md", rels)
        self.assertIn(os.path.join("scripts", "legalcheck.py"), rels)
        self.assertIn(os.path.join("rules", "eu.md"), rels)

    def test_the_shipped_file_set_holds_no_reports_state_cases_or_registry_draft(self):
        rels = self._shipped()
        self.assertTrue(rels)
        for rel in rels:
            self.assertNotIn(rel.split(os.sep)[0], STATE_DIRS, rel)
            self.assertNotEqual(rel, "registry-entry.json")
        self.assertTrue(os.path.isdir(os.path.join(SKILL, "cases")), "the hub keeps its cases")

    def test_the_staged_export_has_the_skill_and_passes_the_public_rules(self):
        stage = os.path.join(self.tmp, "stage")
        os.makedirs(stage)
        export.build(stage, "0.0.0", {})
        out = os.path.join(stage, "plugin", "skills", "legal-eu-es-fr")
        self.assertTrue(os.path.isfile(os.path.join(out, "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(out, "scripts", "legalcheck.py")))
        self.assertTrue(os.path.isfile(os.path.join(out, "NOTICE")))
        for role in ("coordinator.md", "checker.md", "eu.md", "es.md", "fr.md", "licences.md"):
            self.assertTrue(os.path.isfile(os.path.join(out, "roles", role)), role)
        self.assertFalse(os.path.exists(os.path.join(out, "cases")))
        self.assertFalse(os.path.exists(os.path.join(out, "registry-entry.json")))
        self.assertEqual(export.public_rules(stage), [])

    def test_the_export_refuses_when_the_legal_skill_is_missing(self):
        stage = os.path.join(self.tmp, "stage-missing")
        os.makedirs(stage)
        with mock.patch.object(export, "LEGAL_SKILL", "no-such-skill"):
            with self.assertRaises(export.ExportRefused) as ctx:
                export.build(stage, "0.0.0", {})
        self.assertIn("no-such-skill skill was not found", str(ctx.exception))

    def test_the_omit_list_is_case_insensitive_and_matches_at_any_depth(self):
        hub = os.path.join(self.tmp, "hub")
        skill = os.path.join(hub, "skills", "legal-eu-es-fr")
        files = ("SKILL.md", "sub/ok.md", "Cases/a.md", "sub/cases/x.md", "deep/er/CASES/y.md", "sub/Registry-Entry.json")
        for rel in files:
            path = os.path.join(skill, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("x\n")
        keep = {os.path.join("SKILL.md"), os.path.join("sub", "ok.md")}
        shipped = {os.path.relpath(p, skill) for p in export.shipped_text_files(hub) if p.startswith(skill + os.sep)}
        self.assertEqual(shipped, keep)
        dst = os.path.join(self.tmp, "copy")
        export._copy_tree(skill, dst, {}, export.SKILL_OMIT["legal-eu-es-fr"])
        copied = {os.path.relpath(os.path.join(d, f), dst) for d, _dirs, names in os.walk(dst) for f in names}
        self.assertEqual(copied, keep)
