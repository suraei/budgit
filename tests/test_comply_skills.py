import json, os, subprocess, sys

from helpers import StudioTestCase, ROOT
from test_comply import tool_use

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")

REGISTRY = {"entries": [
    {"id": "impeccable", "kind": "skill", "status": "installed", "enabled": True},
    {"id": "frontend-design:frontend-design", "aliases": ["frontend-design"], "kind": "skill", "status": "installed", "enabled": True},
    {"id": "hype-skill", "kind": "skill", "status": "rejected", "enabled": False},
    {"id": "unread-one", "kind": "skill", "status": "installed", "enabled": False, "read": False},
    {"id": "rule/pixel-art-1x", "kind": "rule", "status": "draft", "enabled": False},
]}


class ComplySkillsTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "projects")
        self.dir = os.path.join(self.root, "-Users-x-proj")
        os.makedirs(self.dir)

    def session(self, recs, sid="s1"):
        path = os.path.join(self.dir, sid + ".jsonl")
        with open(path, "w") as f:
            for i, (name, inp) in enumerate(recs):
                f.write(json.dumps(tool_use("m%d" % i, name, inp, "t%d" % i)) + "\n")
        return path

    def audit(self, recs):
        import comply
        return comply.audit(self.session(recs), level="normal", registry=REGISTRY)

    def test_a_skill_used_after_a_logged_why_is_clean(self):
        r = self.audit([("Bash", {"command": "budgit skills why --job design/build --traits surface=web"}),
                        ("Skill", {"skill": "impeccable"})])
        self.assertEqual((r["skills_unlogged"], r["skills_rejected"]), (0, 0))

    def test_a_skill_used_with_no_why_is_counted(self):
        r = self.audit([("Skill", {"skill": "impeccable"}), ("Skill", {"skill": "frontend-design"})])
        self.assertEqual(r["skills_unlogged"], 2)

    def test_a_why_that_came_after_the_skill_does_not_cover_it(self):
        r = self.audit([("Skill", {"skill": "impeccable"}), ("Bash", {"command": "budgit skills why --job design/build"})])
        self.assertEqual(r["skills_unlogged"], 1)

    def test_a_why_run_with_no_log_does_not_count_as_logged(self):
        r = self.audit([("Bash", {"command": "budgit skills why --job design/build --no-log"}), ("Skill", {"skill": "impeccable"})])
        self.assertEqual(r["skills_unlogged"], 1)

    def test_rejected_and_turned_off_skills_are_counted(self):
        r = self.audit([("Bash", {"command": "budgit skills why --job x"}), ("Skill", {"skill": "hype-skill"}), ("Skill", {"skill": "unread-one"})])
        self.assertEqual((r["skills_unlogged"], r["skills_rejected"]), (0, 2))

    def test_unknown_skills_and_rule_rows_are_not_counted(self):
        r = self.audit([("Skill", {"skill": "never-heard-of-it"}), ("Skill", {"skill": "rule/pixel-art-1x"}), ("Read", {"file_path": "x"})])
        self.assertEqual((r["skills_unlogged"], r["skills_rejected"]), (0, 0))

    def test_without_a_registry_the_counters_are_absent_and_the_old_ones_unchanged(self):
        import comply
        r = comply.audit(self.session([("Skill", {"skill": "impeccable"})]), level="normal")
        self.assertNotIn("skills_unlogged", r)
        self.assertEqual(comply.violations(r), 0)

    def test_cli_prints_the_skills_section_only_with_the_flag(self):
        self.session([("Skill", {"skill": "impeccable"}), ("Agent", {"prompt": "p", "subagent_type": "x", "model": "sonnet"})])
        env = dict(os.environ)
        base = [sys.executable, CLI, "comply", "--root", self.root, "--days", "30"]
        plain = subprocess.run(base, capture_output=True, text=True, env=env).stdout
        self.assertNotIn("skills", plain.split("\n")[0].lower() if plain else "")
        self.assertNotIn("rejected/off", plain)
        flagged = subprocess.run(base + ["--skills"], capture_output=True, text=True, env=env)
        self.assertEqual(flagged.returncode, 0, flagged.stderr)
        self.assertIn("skills", flagged.stdout)


if __name__ == "__main__":
    import unittest
    unittest.main()
