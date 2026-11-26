"""R-988: opt-in `refuse_rm` setting; a PreToolUse check refuses rm-like deletions and names the Trash move."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import norm

REFUSED = [
    "rm file.txt",
    "rm -rf build",
    "/bin/rm x",
    "unlink x",
    "rmdir d",
    "srm x",
    "shred -u x",
    "find . -name '*.pyc' -delete",
    "find . -name x -exec rm {} \\;",
    "find . -name x -exec sh -c 'rm {}' \\;",
    "ls | xargs rm",
    "echo a | xargs -n1 rm -f",
    "for f in a b; do rm $f; done",
    "while read f; do rm \"$f\"; done < list",
    "bash -c 'rm x'",
    "sh -c \"cd d && rm x\"",
    "cd d && rm x",
    "true; rm x",
    "ls | rm x",
    "sudo rm -rf /x",
    "sudo -u root rm x",
    "env A=1 rm x",
    "A=1 rm x",
    "nice -n 10 rm x",
    "echo $(rm x)",
    "eval 'rm x'",
    "sudo -u git rm f",
    "sudo -u git bash -c 'rm f'",
    "env -u PATH rm f",
    "nice -n 5 rm f",
    "timeout -s KILL 5 rm f",
    "busybox rm f",
    "toybox rm f",
    "git clean -fdx",
    "git clean --force",
    "env --unknown-opt rm f",
    "sudo --foo rm f",
    "timeout --foo 5 rm f",
    "nice --weird bash -c 'rm f'",
]
ALLOWED = [
    "git rm file.txt",
    "git commit -m 'rm the old file'",
    "git -C /x rm --cached f",
    "echo rm",
    "echo \"rm -rf /\"",
    "grep rm file",
    "ls /usr/local/firmware",
    "cat form.txt",
    "python3 rmtree_notes.py",
    "trash x",
    "mv x ~/.Trash/",
    "find . -name '*.py'",
    "find . -exec grep rm {} \\;",
    "sudo ls /x",
    "ls | xargs grep rm",
    "cat <<'EOF'\nrm x\nEOF",
    "git clean -n",
    "sudo -u git ls f",
    "env --unknown-opt ls",
    "nice --weird make",
    "timeout --foo 5 ls",
    "sudo --foo ls",
    "env --ignore-environment ls",
    "sudo --preserve-env ls",
    "timeout --kill-after=5 10 ls",
]


def run(command, text='{"refuse_rm": true}'):
    with tempfile.TemporaryDirectory() as home:
        if text is not None:
            with open(os.path.join(home, "config.json"), "w") as fh:
                fh.write(text)
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": home, "BUDGIT_LANG": "en"}):
            return norm.decision("Bash", {"command": command}, home)


class NoRmFormsTest(unittest.TestCase):
    def test_each_refused_form_is_denied_with_the_trash_alternative(self):
        for cmd in REFUSED:
            with self.subTest(cmd=cmd):
                d = run(cmd)
                self.assertIsNotNone(d, cmd)
                self.assertEqual(d[0], "deny")
                self.assertIn("Trash", d[1])

    def test_each_allowed_form_passes(self):
        for cmd in ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertIsNone(run(cmd), cmd)

    def test_the_message_names_the_platform_trash_at_run_time(self):
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIn("~/.Trash/", run("rm x")[1])
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIn(".local/share/Trash", run("rm x")[1])
        with mock.patch.object(sys, "platform", "win32"):
            self.assertIn("archive", run("rm x")[1])

    def test_the_message_is_in_spanish_when_the_language_is_spanish(self):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, "config.json"), "w") as fh:
                fh.write('{"refuse_rm": true}')
            with mock.patch.dict(os.environ, {"BUDGIT_HOME": home, "BUDGIT_LANG": "es"}):
                d = norm.decision("Bash", {"command": "rm x"}, home)
        self.assertIn("papelera", d[1].lower())

    def test_other_tools_are_never_judged(self):
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": tempfile.gettempdir()}):
            self.assertIsNone(norm.decision("Write", {"command": "rm x"}, "/tmp", enabled=True))


class NoRmSettingTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIsNone(run("rm x", None))
        self.assertIsNone(run("rm x", json.dumps({"codex": "off"})))

    def test_only_a_literal_true_turns_it_on(self):
        for value in ('"true"', "1", '"on"', "false", "null"):
            self.assertIsNone(run("rm x", '{"refuse_rm": %s}' % value), value)
        self.assertEqual(run("rm x", '{"refuse_rm": true}')[0], "deny")

    def test_unreadable_config_is_off_it_is_opt_in(self):
        self.assertIsNone(run("rm x", '{"refuse_rm": true,'))
        self.assertIsNone(run("rm x", "[]"))

    def test_a_failing_setting_read_is_off(self):
        with mock.patch("studiolib.refuse_rm", side_effect=RuntimeError("boom")):
            self.assertIsNone(norm.decision("Bash", {"command": "rm x"}, "/tmp"))

    def test_enabled_argument_overrides_the_config(self):
        self.assertEqual(norm.decision("Bash", {"command": "rm x"}, "/tmp", enabled=True)[0], "deny")
        self.assertIsNone(norm.decision("Bash", {"command": "rm x"}, "/tmp", enabled=False))


CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")


class RmGuardCommandTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def cli(self, *args, lang="en"):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.home, env=env, capture_output=True, text=True)

    def decision(self):
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": self.home}):
            return norm.decision("Bash", {"command": "rm x"}, self.home)

    def saved(self):
        with open(os.path.join(self.home, "config.json")) as fh:
            return json.load(fh)

    def test_rm_guard_on_off_round_trip(self):
        self.assertIsNone(self.decision())
        r = self.cli("rm-guard", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(self.saved()["refuse_rm"], True)
        self.assertEqual(self.decision()[0], "deny")
        r = self.cli("rm-guard", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(self.saved()["refuse_rm"], False)
        self.assertIsNone(self.decision())

    def test_bare_rm_guard_shows_the_state_in_both_languages(self):
        self.assertIn("off", self.cli("rm-guard").stdout)
        self.cli("rm-guard", "on")
        self.assertIn("refused (on)", self.cli("rm-guard").stdout)
        self.assertIn("se rechazan (activado)", self.cli("rm-guard", lang="es").stdout)

    def test_an_invalid_value_exits_non_zero_and_changes_nothing(self):
        self.cli("rm-guard", "on")
        before = self.saved()
        r = self.cli("rm-guard", "maybe")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.saved(), before)


class HookWiringTest(unittest.TestCase):
    def test_hook_runs_the_norm_check_in_the_bash_branch(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "scripts", "hook.py")
        with open(src) as fh:
            text = fh.read()
        self.assertIn('("no-rm", closed("deny", norm.decision', text)


if __name__ == "__main__":
    unittest.main()
