"""R-1128 / R-1136: one test per tester symptom, through the same door the tester used (the hook as a subprocess with real hook
JSON on stdin, the budgit CLI as a subprocess). Direct function calls and injected inputs do not count here."""
import glob
import os
import re
import unittest

from outcome_harness import OutcomeCase, git

ROUND3 = "tester-shape row for round 3 (the real-machine shape): the PM reruns it after the merge"


class Symptom2SearchRoot(OutcomeCase):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.bhome, "config.json"), "w") as f:      # hotfix 1 ships the search guard off: this row turns it on
            f.write('{"search_guard": "on"}')

    def test_symptom2_search_from_root_or_home_is_denied_and_inside_project_is_allowed(self):
        for tool, inp in (("Bash", {"command": "find / -name x"}),
                          ("Grep", {"pattern": "x", "path": "/"}),
                          ("Glob", {"pattern": "*.py", "path": "~"})):
            out = self.run_hook("pre-tool-use", {"tool_name": tool, "tool_input": inp})
            self.assertEqual(self.decision(out), "deny", (tool, inp, out, self.last_hook.stderr))
        for tool, inp in (("Bash", {"command": "find . -name README.md"}),
                          ("Grep", {"pattern": "hello", "path": self.project}),
                          ("Glob", {"pattern": "*.md", "path": self.project})):
            out = self.run_hook("pre-tool-use", {"tool_name": tool, "tool_input": inp})
            self.assertNotEqual(self.decision(out), "deny", (tool, inp, out))

    def test_symptom2_tester_shape_search_through_a_parent_folder(self):
        link = os.path.join(self.tmp, "parent-link")
        os.symlink(self.tmp, link)
        for cmd in ("find .. -name x", "find ../.. -name x", "find %s -name x" % link, "find %s/proj/.. -name x" % link):
            out = self.run_hook("pre-tool-use", {"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(self.decision(out), "deny", (cmd, out, self.last_hook.stderr))


class Symptom4bSecrets(OutcomeCase):
    def test_symptom4b_secrets_without_scanners_is_skipped_exit_3_never_clean(self):
        empty = os.path.join(self.tmp, "emptybin")
        os.makedirs(empty)
        r = self.run_cli(["secrets", self.project], path=empty)
        self.assertEqual(r.returncode, 3, (r.stdout, r.stderr))
        self.assertIn("skipped", r.stdout)
        self.assertNotIn("clean", r.stdout.lower().replace("not clean", ""))   # "not clean" is the honest wording


class Symptom5Superseded(OutcomeCase):
    def test_symptom5_old_fail_report_then_fix_commit_shows_superseded(self):
        old = git(self.project, "rev-parse", "HEAD")
        with open(os.path.join(self.project, "README.md"), "w") as f:
            f.write("fixed\n")
        git(self.project, "commit", "-q", "-a", "-m", "fix")
        fix = git(self.project, "rev-parse", "HEAD")
        self.activate()
        r = self.run_cli(["ledger", "add", "--text", "make it work", "--src", "legacy", "--phase", "mvp",
                          "--appetite", "small", "--title", "make it work"])
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        r = self.run_cli(["ledger", "outcome", "R-1", "the user sees it work"])
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        vdir = os.path.join(self.project, ".budgit", "verify")
        os.makedirs(vdir, exist_ok=True)
        with open(os.path.join(vdir, "R-1.md"), "w") as f:
            f.write("FAIL\nreviewer: v1\n%s\nround: 1\n" % old)
        done = self.run_cli(["ledger", "done", "R-1", "--evidence", "%s test_it" % fix])
        status = self.run_cli(["status"])
        shown = status.stdout + status.stderr + done.stdout + done.stderr
        self.assertIn("superseded", shown, shown)


class Symptom6DoneProof(OutcomeCase):
    def _add(self):
        self.activate()
        r = self.run_cli(["ledger", "add", "--text", "x", "--src", "legacy", "--phase", "mvp", "--appetite", "small",
                          "--title", "x"])
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
        r = self.run_cli(["ledger", "outcome", "R-1", "the user sees x work"])   # R-1136: done needs an outcome line
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))

    def test_symptom6_done_accepts_a_js_test_id(self):
        self._add()
        sha = git(self.project, "rev-parse", "HEAD")
        r = self.run_cli(["ledger", "done", "R-1", "--evidence", "%s src/a.test.js > adds" % sha])
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))

    def test_symptom6_done_accepts_a_rule_only_proof(self):
        self._add()
        r = self.run_cli(["ledger", "done", "R-1", "--evidence", "rule: plugin/rules/team.md:42 check: the guard test"])
        self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))


class Symptom7Checkpoint(OutcomeCase):
    def test_symptom7_the_digest_write_the_shipped_prompt_asks_for_is_allowed_with_no_ask(self):
        from unittest import mock
        prompt = open(os.path.join(self.export, "plugin", "scripts", "prompts", "standup.md"), encoding="utf-8").read()
        self.assertIn("<job folder>/standup/", prompt)
        self.assertNotIn("<Budgit home>/standup", prompt)
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            import jobs
            folder = jobs.job_dir("standup", create=True)
        target = os.path.join(folder, "standup", "2026-11-15.md")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        out = self.run_hook("pre-tool-use", {"tool_name": "Write", "tool_input": {"file_path": target, "content": "digest"}})
        self.assertNotIn(self.decision(out), ("deny", "ask"), (out, self.last_hook.stderr))
        self.assertFalse(os.path.exists(os.path.join(self.bhome, "standup")), "nothing is written into Budgit home")

    def test_symptom7_every_skill_a_shipped_prompt_names_resolves_in_the_export_or_is_optional(self):
        plugin = os.path.join(self.export, "plugin")
        known = {os.path.basename(p) for p in glob.glob(os.path.join(plugin, "skills", "*"))}
        known |= {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(plugin, "agents", "*.md"))}
        known |= {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(plugin, "commands", "*.md"))}
        bad = []
        for path in glob.glob(os.path.join(plugin, "scripts", "prompts", "*.md")):
            text = open(path, encoding="utf-8").read()
            for line in text.splitlines():
                for name in re.findall(r"\bskill\s+`?([\w:-]+)`?|`([\w-]+)` skill", line, re.I):
                    n = (name[0] or name[1]).split(":")[-1]
                    if n not in known and "optional" not in line.lower():
                        bad.append((os.path.basename(path), n))
        self.assertEqual(bad, [])


class Symptom9NoRealDataAlarm(OutcomeCase):
    def _quiet(self, out):
        self.assertEqual(self.decision(out), "none" if self.decision(out) == "none" else "allow", out)
        self.assertNotIn("real data", str(out).lower())
        self.assertNotIn("real-data", str(out).lower())

    def test_symptom9_grep_color_in_a_css_file_raises_no_real_data_alarm(self):
        out = self.run_hook("pre-tool-use", {"tool_name": "Bash", "tool_input": {"command": 'grep -rn "color" assets/style.css'}})
        self._quiet(out)
        out = self.run_hook("pre-tool-use", {"tool_name": "Grep", "tool_input": {
            "pattern": "color", "path": os.path.join(self.project, "assets", "style.css")}})
        self._quiet(out)


if __name__ == "__main__":
    unittest.main()
