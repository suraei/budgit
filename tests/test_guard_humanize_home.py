"""The humanize flags live in the Budgit home (`<home>/humanize/<session>.flag|.client|.gate|.done`). Only the hooks write
them, as plain Python; the model may not write, delete or move them with any tool, whoever it is, or the client-text check
could be skipped by deleting its gate."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import HookCase2, sl  # noqa: E402


class HumanizeFolderTest(HookCase2):
    def setUp(self):
        super().setUp()
        self.folder = os.path.join(self.home, "humanize")
        os.makedirs(self.folder, exist_ok=True)
        self.flag = os.path.join(self.folder, "sess-1.flag")
        open(self.flag, "w").close()
        sl.activate(self.project, session_id="hum-chat")

    def decide(self, tool, tool_input, agent_type=None):
        extra = {"session_id": "hum-chat"}
        if agent_type:
            extra["agent_type"] = agent_type
        return self.tool(tool, tool_input, **extra)

    def test_every_write_tool_into_the_folder_is_denied_for_every_actor(self):
        for actor in (None, "general-purpose", "budgit:verifier", "studio:verifier"):
            for ext in ("flag", "client", "gate", "done"):
                path = os.path.join(self.folder, "sess-1.%s" % ext)
                for tool, ti in (("Write", {"file_path": path, "content": "x"}),
                                 ("Edit", {"file_path": path, "old_string": "a", "new_string": "b"}),
                                 ("MultiEdit", {"file_path": path, "edits": []}),
                                 ("NotebookEdit", {"notebook_path": path, "new_source": "x"})):
                    self.assertEqual(self.decide(tool, ti, actor), "deny", (actor, tool, ext))

    def test_shell_writes_deletes_and_moves_in_the_folder_are_denied_for_every_actor(self):
        f, d = self.flag, self.folder
        for actor in (None, "budgit:verifier"):
            for cmd in ("rm %s" % f, "rm -f %s/*.flag" % d, "rm -rf %s" % d, "mv %s /tmp/x" % f, "mv /tmp/x %s/new.gate" % d,
                        "echo 1 > %s/sess-1.gate" % d, "echo 1 >> %s" % f, "touch %s/other.done" % d, "truncate -s0 %s" % f,
                        "cp /tmp/x %s/sess-2.flag" % d, "ln -s /tmp/x %s/sess-3.flag" % d, "tee %s < /tmp/x" % f,
                        "python3 -c \"import os; os.remove('%s')\"" % f, "cd %s && rm sess-1.flag" % d, "cd %s && : > sess-1.flag" % d,
                        "find %s -delete" % d, "unlink %s" % f, "sed -i s/a/b/ %s" % f, "dd of=%s if=/dev/null" % f):
                self.assertIn(self.decide("Bash", {"command": cmd}, actor), ("deny", "ask"), (actor, cmd))
            for cmd in ("rm %s" % f, "echo 1 > %s/sess-1.gate" % d, "mv %s /tmp/x" % f, "cd %s && rm sess-1.flag" % d):
                self.assertEqual(self.decide("Bash", {"command": cmd}, actor), "deny", (actor, cmd))

    def test_a_command_nested_in_a_shell_string_an_interpreter_or_xargs_is_judged_the_same(self):
        f = self.flag
        for actor in (None, "budgit:verifier"):
            for cmd in ("sh -c 'rm %s'" % f, "bash -c 'rm -f %s'" % f, "zsh -c 'mv %s /tmp/x'" % f, "env sh -c 'rm %s'" % f,
                        "sh -c \"echo 1 > %s/sess-1.gate\"" % self.folder, "bash -c 'sh -c \"rm %s\"'" % f,
                        "echo %s | xargs rm" % f, "ls %s | xargs rm" % self.folder, "echo %s | xargs -n1 mv -t /tmp" % f,
                        "awk 'BEGIN{system(\"rm %s\")}'" % f,
                        "osascript -e 'do shell script \"rm %s\"'" % f,
                        "python3 -c \"import os; os.remove('%s')\"" % f,
                        "ruby -e 'File.delete(\"%s\")'" % f,
                        "perl -e 'unlink q(%s)'" % f):
                self.assertEqual(self.decide("Bash", {"command": cmd}, actor), "deny", (actor, cmd))

    def test_reading_the_folder_and_writing_elsewhere_are_left_alone(self):
        self.assertIsNone(self.decide("Read", {"file_path": self.flag}))
        self.assertIsNone(self.decide("Write", {"file_path": os.path.join(self.project, "docs", "a.md"), "content": "x"}))
        self.assertIsNone(self.decide("Bash", {"command": "ls %s" % self.folder}))


if __name__ == "__main__":
    unittest.main()
