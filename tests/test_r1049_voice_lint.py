"""R-1049: the owner-voice rule of the export is checked at lint time and by the selective test map, from one rule.
Everything runs in tmp folders; the real tree is only read by the map test (names, never a scan of its text)."""
import os
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import export  # noqa: E402
import lint  # noqa: E402
import runtests as rt  # noqa: E402

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
VOICE = 'def f():\n    """Allowed when her note says so."""\n    return 1\n'
CLEAN = 'def f():\n    """Allowed when the user has said so."""\n    return 1\n'


class VoiceLintCase(StudioTestCase):
    def put(self, rel, text):
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def cli(self, *args):
        return subprocess.run([sys.executable, CLI, "lint", "--repo", self.project] + list(args),
                              capture_output=True, text=True, env=dict(os.environ, STUDIO_HOME=self.home))


class VoiceLintTest(VoiceLintCase):
    def test_a_shipped_file_with_her_in_a_docstring_fails_lint(self):
        self.put("plugin/scripts/mod.py", VOICE)
        r = self.cli()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("plugin/scripts/mod.py:2:0: owner-voice her", r.stdout)

    def test_the_same_word_in_a_non_shipped_file_passes(self):
        self.put("tests/test_x.py", VOICE)
        self.put(".studio/notes.md", "she said so; her words\n")
        self.put("skills/other/SKILL.md", "her\n")       # a skills/ folder the exporter does not ship
        r = self.cli(self.project)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 findings", r.stdout)
        self.assertEqual(lint.voice_findings([self.project], self.project), [])

    def test_a_clean_tree_passes(self):
        self.put("plugin/scripts/mod.py", CLEAN)
        self.put("tests/test_x.py", VOICE)
        r = self.cli()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 findings", r.stdout)

    def test_a_voice_ok_line_and_an_explicit_path_follow_the_export_rule(self):
        ok = self.put("plugin/scripts/ok.py", "x = 'her'  # voice-ok: the rule itself\n")
        bad = self.put("plugin/scripts/bad.py", "is_her_prompt = 1\n")
        self.assertEqual(lint.voice_findings([ok], self.project), [])
        got = lint.voice_findings([bad], self.project)
        self.assertEqual([(f.line, f.kind, f.detail) for f in got], [(1, "owner-voice", "is_her_prompt")])

    def test_lint_uses_the_export_predicate_and_rule_not_a_copy(self):
        self.put("plugin/scripts/mod.py", VOICE)
        path = os.path.join(os.path.realpath(self.project), "plugin", "scripts", "mod.py")
        shipped = export.shipped_text_files(os.path.realpath(self.project))
        self.assertEqual(shipped, [path])
        want = [(h.line, h.term) for h in export.voice_hits(shipped, os.path.realpath(self.project))]
        self.assertEqual([(f.line, f.detail) for f in lint.voice_findings([self.project], self.project)], want)
        self.assertNotIn("VOICE_RE", open(os.path.join(ROOT, "plugin", "scripts", "lint.py")).read())

    def test_tracked_mode_scans_the_shipped_files_too(self):
        self.put("plugin/scripts/mod.py", VOICE)
        self.put("tests/test_x.py", VOICE)
        subprocess.run(["git", "-C", self.project, "init", "-q"], check=True)
        subprocess.run(["git", "-C", self.project, "add", "-A"], check=True)
        r = self.cli("--tracked")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("plugin/scripts/mod.py:2:0: owner-voice her", r.stdout)
        self.assertNotIn("tests/test_x.py", r.stdout)


class VoiceMapTest(unittest.TestCase):
    def test_the_map_selects_the_voice_test_for_a_plugin_scripts_change(self):
        for f in ("plugin/scripts/zzz_new.py", "plugin/scripts/studiolib.py"):
            sel, why = rt.select([f])
            self.assertIsNotNone(sel, f + ": " + why)
            self.assertIn("test_public_voice", sel, f)

    def test_the_voice_tests_are_found_by_what_they_call(self):
        found = rt.voice_tests({
            "a": "export.voice_hits(export.shipped_text_files(ROOT), ROOT)", "b": "voice_hits only", "c": "nothing"})
        self.assertEqual(found, {"a"})
        self.assertIn("test_public_voice", rt.voice_tests(
            {m: open(os.path.join(rt.TESTS, m + ".py"), errors="replace").read() for m in rt.discover([])}))

    def test_a_tests_only_change_does_not_select_the_voice_test(self):
        sel, _ = rt.select(["tests/test_runtests_home.py"])
        self.assertEqual(sel, ["test_runtests_home"])


if __name__ == "__main__":
    unittest.main()
