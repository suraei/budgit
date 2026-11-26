"""R-1128 hotfix 1 (a) the stdlib-only hook fallback when studiolib fails to load, (b) the session-start line for a project with
ledgers and no protected folder."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import StudioTestCase, ROOT

SCRIPTS = os.path.join(ROOT, "plugin", "scripts")


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.scripts = os.path.join(self.tmp, "scripts")
        os.makedirs(self.scripts)
        for name in ("hook.py", "hookfallback.py"):
            shutil.copy(os.path.join(SCRIPTS, name), self.scripts)
        with open(os.path.join(self.scripts, "studiolib.py"), "w") as f:
            f.write("raise ImportError('broken on purpose')\n")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.work, ".budgit"))          # hotfix 1 r4 M2: outside a real project nothing but git status passes

    def fire(self, payload, event="pre-tool-use"):
        env = dict(os.environ, BUDGIT_HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        env.pop("STUDIO_HOME", None)
        r = subprocess.run([sys.executable, os.path.join(self.scripts, "hook.py"), event], input=json.dumps(payload),
                           env=env, capture_output=True, text=True, cwd=self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def bash(self, command):
        return self.fire({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.work})

    def decision(self, out):
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")

    def count(self):
        with open(os.path.join(self.home, "hook-fallback.count")) as f:
            return int(f.read())

    def test_r1128_fallback_denies_each_destructive_shape(self):
        for cmd in ("git push", "git push origin main", "git push --force", "git push -f origin x", "git reset --hard HEAD~1",
                    "git checkout -f main", "git checkout --force main", "git clean -fd", "git stash", "git -C /x push",
                    "rm -rf /tmp/x", "rm -fr x", "rm -r -f x", "echo hi && git push", "env FOO=1 git stash pop"):
            self.assertEqual(self.decision(self.bash(cmd)), "deny", cmd)

    def test_r1128_fallback_allows_a_plain_read_and_says_enforcement_is_reduced(self):
        for out in (self.bash("git status"), self.bash("ls -la"), self.bash("git log --oneline"),
                    self.fire({"tool_name": "Read", "tool_input": {"file_path": os.path.join(self.work, "a.txt")}, "cwd": self.work})):
            self.assertIsNone(self.decision(out))
            self.assertIn("enforcement reduced", out["systemMessage"])
        outside = self.fire({"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}, "cwd": self.work})   # hotfix 1: outside the project asks
        self.assertIn(self.decision(outside), ("ask", "deny"))
        self.assertIn("enforcement reduced", outside["systemMessage"])
        nowhere = self.fire({"tool_name": "Bash", "tool_input": {"command": "ls -la"}, "cwd": self.tmp})      # no project here
        self.assertIn(self.decision(nowhere), ("ask", "deny"))
        status = self.fire({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": self.tmp})
        self.assertIsNone(self.decision(status))

    def test_r1128_fallback_denies_a_write_into_a_protected_folder_and_allows_others(self):
        secret = os.path.join(self.tmp, "secret-data")
        os.makedirs(secret)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": [secret]}, f)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            key = "notebook_path" if tool == "NotebookEdit" else "file_path"
            hit = self.fire({"tool_name": tool, "tool_input": {key: os.path.join(secret, "a.txt")}, "cwd": self.work})
            self.assertEqual(self.decision(hit), "deny", tool)
        ok = self.fire({"tool_name": "Write", "tool_input": {"file_path": os.path.join(self.work, "a.txt")}, "cwd": self.work})
        self.assertIsNone(self.decision(ok))

    def test_r1128_fallback_with_an_unreadable_config_denies_writes_outside_the_cwd_tree(self):
        with open(os.path.join(self.home, "config.json"), "w") as f:
            f.write("{not json")
        out = self.fire({"tool_name": "Write", "tool_input": {"file_path": os.path.join(self.tmp, "elsewhere.txt")}, "cwd": self.work})
        self.assertEqual(self.decision(out), "deny")
        inside = self.fire({"tool_name": "Write", "tool_input": {"file_path": os.path.join(self.work, "a.txt")}, "cwd": self.work})
        self.assertIsNone(self.decision(inside))

    def test_r1128_fallback_counts_every_run(self):
        self.bash("git status")
        self.bash("git push")
        self.fire({"session_id": "s"}, event="stop")
        self.assertEqual(self.count(), 3)

    def test_r1128_fallback_other_events_get_the_reduced_message_only(self):
        out = self.fire({"session_id": "s"}, event="user-prompt-submit")
        self.assertIn("enforcement reduced", out["systemMessage"])
        self.assertNotIn("hookSpecificOutput", out)

    def test_r1128_fallback_survives_garbage_input(self):
        env = dict(os.environ, BUDGIT_HOME=self.home)
        r = subprocess.run([sys.executable, os.path.join(self.scripts, "hook.py"), "pre-tool-use"], input="not json",
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("enforcement reduced", json.loads(r.stdout)["systemMessage"])


class NoProtectedLineTest(StudioTestCase):
    def line(self):
        import hook
        return hook.no_protected_line(self.project)

    def test_r1128_warns_when_ledgers_exist_and_nothing_is_protected(self):
        self.init_project()
        self.assertIn("portfolio add", self.line())

    def test_r1128_silent_when_a_protected_folder_exists(self):
        self.init_project()
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": [os.path.join(self.tmp, "x")]}, f)
        self.assertIsNone(self.line())

    def test_r1128_silent_when_no_ledger_exists(self):
        self.assertIsNone(self.line())


if __name__ == "__main__":
    unittest.main()
