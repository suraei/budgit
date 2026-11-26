"""R-174 security review of the export tree: the cheap hardening it asked for, one test each."""
import os
import re
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl
import watermark

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
GIT = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.org",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.org"}


class SecurityReviewTest(StudioTestCase):
    def test_codex_review_refuses_a_base_that_looks_like_a_git_option(self):
        sl.activate(self.project, bind=False)
        target = os.path.join(self.tmp, "overwritten")
        r = subprocess.run([sys.executable, CLI, "codex", "review", "--base=--output=" + target], cwd=self.project,
                           env=dict(os.environ, BUDGIT_HOME=self.home, **GIT), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("must not start with -", r.stderr + r.stdout)
        self.assertFalse(os.path.exists(target))

    def test_the_watermark_marker_counts_only_on_the_second_line(self):
        hook = os.path.join(self.tmp, "commit-msg")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\n# my own hook\n# budgit-watermark is only mentioned here\nexit 0\n")
        self.assertIsNone(watermark._ours(hook))
        with open(hook, "w") as f:
            f.write(watermark.hook_text("https://github.com/acme/x", "trailer"))
        self.assertIsNotNone(watermark._ours(hook))

    def test_the_env_file_path_line_cannot_expand_anything(self):
        import hook
        line = hook.path_export_line('/tmp/a"b$(touch x)`id`\\z\nexport X=1/bin')
        self.assertTrue(line.startswith('export PATH="') and line.endswith(':$PATH"\n'))
        inner = line[len('export PATH="'):-len(':$PATH"\n')]
        for bad in ('"', "$", "`", "\\", "\n"):
            self.assertNotIn(bad, inner)

    def test_image_generation_checks_the_prompt_like_the_text_lane(self):
        with self.assertRaisesRegex(sl.StudioError, "secret"):
            sl.generate_image(self.project, "a logo; key sk-ant-api03-%s" % ("a" * 40), "logo")

    def test_shipped_files_name_no_private_research_notes_and_no_owner_voice(self):
        pattern = re.compile(r"research/(?:\w+/)?20\d\d-\d\d-\d\d-(?!example)")
        bad = []
        for top in ("plugin", "skills", os.path.join("tools", "brote", "float")):
            for dirpath, dirs, files in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [d for d in dirs if d not in ("build", "__pycache__", "tests")]
                for name in files:
                    if name == "CHANGELOG.md" or name.endswith((".png", ".svg", ".json", ".es.md")):
                        continue
                    path = os.path.join(dirpath, name)
                    try:
                        text = open(path, encoding="utf-8").read()
                    except (OSError, UnicodeDecodeError):
                        continue
                    if pattern.search(text):
                        bad.append(os.path.relpath(path, ROOT))
        swift = open(os.path.join(ROOT, "tools", "brote", "float", "brote.swift"), encoding="utf-8").read()
        if re.search(r"\bher (?:rows|words|hand)", swift):
            bad.append("tools/brote/float/brote.swift: owner voice")
        skill = open(os.path.join(ROOT, "skills", "budgit-kickoff", "SKILL.md"), encoding="utf-8").read()
        if re.search(r"«(?:[^»\s]+\s+){2,}[^»\s]+»", skill) or "«ahora»" in skill:
            bad.append("SKILL.md: a quoted Spanish phrase")
        self.assertEqual(bad, [])


class OfficialReviewNearMissTest(StudioTestCase):
    """The /security-review pass named two near-misses; both are closed."""

    def test_a_manual_inbox_entry_cannot_be_the_users_word_for_a_drop(self):
        sl.activate(self.project, bind=False)
        with sl.locked(self.project):
            data = sl.load_ledger(self.project)
            manual = sl.append_inbox(self.project, "drop R-1", "s1", "manual")
            sl.add_item(data, "keep me", [manual])
            said = sl.append_inbox(self.project, "please drop it", "s1", "prompt")
            with self.assertRaisesRegex(sl.StudioError, "typed by the user"):
                sl.set_status(self.project, data, "R-1", "dropped", by_user=manual)
            self.assertEqual(sl.set_status(self.project, data, "R-1", "dropped", by_user=said)["dropped_by"], said)

    def test_the_url_checks_reject_a_trailing_newline(self):
        self.assertEqual(watermark.hook_text("https://github.com/a/b\n", "trailer").count("Built-with"), 0)
        self.assertEqual(watermark._URL.fullmatch("https://github.com/a/b\n"), None)
