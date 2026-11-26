"""R-335 classes 1 (script file) and 2 (symlink swaps): a program the session wrote to a FILE and then runs is read by the
guard before it runs; a symlink that makes protected data reachable under another name asks. Both pass under the yes."""
import json
import os
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import GuardCase, HOOK, git, sl  # noqa: E402

BUILDS = "import os\np = 'da' + 'ta.json'\nopen(p, 'w').write('x')\n"
NAMES = "open('data.json', 'w').write('x')\n"
SHELL = "rm \"$(echo da)ta.json\"\n"
NODE = "const fs = require('fs'); fs.writeFileSync(['da','ta.json'].join(''), 'x');\n"


class ScriptFileTest(GuardCase):
    def script(self, rel, text, age=0, mode=0o644):
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)
        os.chmod(path, mode)
        if age:
            old = time.time() - age
            os.utime(path, (old, old))
        return path

    def test_a_fresh_script_that_builds_names_and_writes_asks_before_it_runs(self):
        self.script("docs/evil.py", BUILDS)
        self.script("docs/evil.sh", SHELL, mode=0o755)
        self.script("docs/e.js", NODE)
        for cmd in ("python3 docs/evil.py", "cd docs && python3 evil.py", "python3 ./docs/evil.py", "bash docs/evil.sh",
                    "sh docs/evil.sh", "./docs/evil.sh", "source docs/evil.sh", ". docs/evil.sh", "node docs/e.js",
                    "FOO=1 python3 docs/evil.py", "echo hi && python3 docs/evil.py", "python3 -u docs/evil.py"):
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def test_a_script_that_names_protected_data_and_writes_asks_though_the_command_does_not(self):
        self.script("docs/names.py", NAMES)
        self.assertEqual(self.d("python3 docs/names.py")[0], "ask")

    def test_a_plain_or_old_or_missing_script_is_left_alone(self):
        self.script("docs/ok.py", "print('hi')\n")
        self.script("docs/old.py", BUILDS, age=3 * 86400)
        for cmd in ("python3 docs/ok.py", "python3 docs/old.py", "python3 docs/nope.py", "python3 src/app.py"):
            self.assertIsNone(self.d(cmd), cmd)

    def test_under_the_standing_yes_a_script_passes(self):
        self.script("docs/evil.py", BUILDS)
        self.assertTrue(self.grant())
        self.assertIsNone(self.d("python3 docs/evil.py"))

    def test_the_script_is_read_not_run_and_a_huge_or_binary_file_is_skipped(self):
        self.script("docs/big.py", BUILDS + "#" * 400000)
        open(os.path.join(self.project, "docs", "bin.py"), "wb").write(b"\0\1\2" + BUILDS.encode())
        for cmd in ("python3 docs/big.py", "python3 docs/bin.py"):
            self.assertIsNone(self.d(cmd), cmd)


class SessionLedgerTest(GuardCase):
    SID = "ledger-chat"

    def commit_script(self, rel, text, age=0):
        """Commit a script; `age` seconds ago for both the commit time and the file's modification time."""
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)
        git(self.project, "add", rel)
        when = "%d +0000" % (time.time() - age)
        subprocess.run(["git", "-C", self.project, "-c", "user.email=t@users.noreply.github.com", "-c", "user.name=t",
                        "commit", "-q", "-m", "add " + rel], check=True, capture_output=True,
                       env=dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when))
        if age:
            os.utime(path, (time.time() - age, time.time() - age))
        return path

    def decide(self, cmd):
        return sl.real_data_decision("Bash", {"command": cmd}, self.project, cwd=self.project, session=self.SID)

    def test_a_script_committed_long_ago_and_not_edited_since_is_not_read(self):
        self.commit_script("docs/tracked.py", BUILDS, age=3 * 86400)
        self.assertIsNone(self.decide("python3 docs/tracked.py"))

    def test_a_script_committed_in_the_last_hours_is_read_whoever_wrote_it(self):
        self.commit_script("docs/tracked.py", BUILDS)
        self.assertEqual(self.decide("python3 docs/tracked.py")[0], "ask")

    def test_a_committed_script_edited_through_the_shell_is_read_before_it_runs(self):
        path = self.commit_script("docs/tracked.py", "print('hi')\n", age=3 * 86400)
        self.assertIsNone(self.decide("python3 docs/tracked.py"))
        subprocess.run(["sed", "-i.bak", "s/print('hi')/%s/" % "import os; p = 'da' + 'ta.json'; open(p, 'w').write('x')",
                        path], check=True)
        os.remove(path + ".bak")
        self.assertEqual(self.decide("python3 docs/tracked.py")[0], "ask")
        self.assertEqual(self.decide("cd docs && python3 tracked.py")[0], "ask")
        self.assertEqual(self.decide("python3 ./docs/tracked.py")[0], "ask")

    def test_a_script_outside_any_repo_edited_through_the_shell_is_read(self):
        loose = os.path.join(self.tmp, "loose.py")
        open(loose, "w").write("print('hi')\n")
        old = time.time() - 3 * 86400
        os.utime(loose, (old, old))
        self.assertIsNone(self.decide("python3 %s" % loose))
        subprocess.run(["sed", "-i.bak", "s/print('hi')/%s/" % "import os; p = 'da' + 'ta.json'; open(p, 'w').write('x')",
                        loose], check=True)
        self.assertEqual(self.decide("python3 %s" % loose)[0], "ask")

    def test_a_script_whose_history_cannot_be_read_is_treated_as_edited(self):
        path = self.commit_script("docs/tracked.py", BUILDS, age=3 * 86400)
        self.assertIsNone(self.decide("python3 docs/tracked.py"))
        old = sl._last_commit_time
        sl._last_commit_time = lambda full: 0
        try:
            self.assertEqual(self.decide("python3 docs/tracked.py")[0], "ask")
        finally:
            sl._last_commit_time = old

    def test_a_committed_script_the_chat_wrote_or_edited_is_read_before_it_runs(self):
        path = self.commit_script("docs/tracked.py", BUILDS, age=3 * 86400)
        sl.note_session_write(self.SID, path)
        self.assertEqual(self.decide("python3 docs/tracked.py")[0], "ask")
        self.assertIsNone(sl.real_data_decision("Bash", {"command": "python3 docs/tracked.py"}, self.project,
                                                cwd=self.project, session="another-chat"))

    def test_an_old_script_the_chat_wrote_is_still_read(self):
        path = os.path.join(self.project, "docs", "old.py")
        open(path, "w").write(BUILDS)
        old = time.time() - 5 * 86400
        os.utime(path, (old, old))
        sl.note_session_write(self.SID, path)
        self.assertEqual(self.decide("python3 docs/old.py")[0], "ask")

    def test_the_post_tool_use_hook_keeps_the_ledger(self):
        path = os.path.join(self.project, "docs", "w.py")
        open(path, "w").write(BUILDS)
        old = time.time() - 5 * 86400
        os.utime(path, (old, old))
        r = subprocess.run([sys.executable, HOOK, "post-tool-use"], capture_output=True, text=True,
                           env=dict(os.environ, BUDGIT_HOME=self.home),
                           input=json.dumps({"session_id": self.SID, "cwd": self.project, "tool_name": "Write",
                                             "tool_input": {"file_path": "docs/w.py", "content": BUILDS}}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.decide("python3 docs/w.py")[0], "ask")

    def test_budgits_own_program_is_never_read_as_a_script(self):
        self.assertIsNone(self.decide("%s --project %s ledger ack I-22 --note x" % (sl._own_budgit(), self.project)))


class SymlinkReachTest(GuardCase):
    def test_a_link_that_makes_the_protected_folder_reachable_asks(self):
        for cmd in ("ln -s . docs/loop", "ln -s .. docs/up", "ln -s %s docs/root" % self.root, "ln -sf ../ docs/up",
                    "ln -s data docs/d", "ln -s data/real docs/r"):
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def test_a_link_to_a_plain_file_or_folder_is_left_alone(self):
        for cmd in ("ln -s a.md docs/b", "ln -sf docs/a.md docs/b", "ln -s src docs/s", "ln docs/a.md docs/c"):
            self.assertIsNone(self.d(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
