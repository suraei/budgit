"""R-335 security review findings: the grant proof re-checks the typed text; the home guard reads heredocs, pipes into a
shell or interpreter, eval, a line continuation and the scripts an interpreter runs; MCP tools are read for a path under any
key and for command-like arguments."""
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import GuardCase, sl  # noqa: E402


class GrantProofTest(GuardCase):
    def test_a_grant_pointing_at_an_ordinary_user_line_is_not_a_grant(self):
        self.assertTrue(self.grant())
        self.assertTrue(sl.trust_work_on(self.project))
        path = sl.trust_path()
        data = json.load(open(path))
        entry = data["work"][next(iter(data["work"]))]
        with open(entry["transcript"], "rb") as fh:
            first = fh.readline().rstrip(b"\n")                      # the user line "hello"
        entry["offset"], entry["line_sha"] = 0, hashlib.sha256(first).hexdigest()
        json.dump(data, open(path, "w"))
        self.assertFalse(sl.trust_work_on(self.project))


class HomeGuardProgramTest(GuardCase):
    def budgit_home(self):
        return sl.home_state()

    def deny(self, cmd, cwd=None):
        got = sl.home_write_decision("Bash", {"command": cmd}, cwd or self.project)
        self.assertIsNotNone(got, cmd)
        self.assertEqual(got[0], "deny", cmd)

    def test_code_fed_through_a_heredoc_or_a_pipe_that_writes_a_home_is_denied(self):
        h = self.budgit_home()
        for cmd in ("python3 - <<'PY'\nopen('%s/trust.json','w').write('x')\nPY" % h,
                    "node <<'JS'\nrequire('fs').writeFileSync('%s/trust.json','x')\nJS" % h,
                    "ruby <<'RB'\nFile.write('%s/trust.json','x')\nRB" % h,
                    "perl <<'PL'\nopen F, '>%s/trust.json'\nPL" % h,
                    "echo 'echo 1 > %s/trust.json' | sh" % h,
                    "echo \"open('%s/trust.json','w')\" | python3" % h,
                    "eval 'echo 1 > %s/trust.json'" % h,
                    "echo x >\\\n %s/trust.json" % h):
            self.deny(cmd)

    def test_ordinary_heredocs_and_pipes_are_left_alone(self):
        h = self.budgit_home()
        for cmd in ("python3 - <<'PY'\nprint(1)\nPY", "echo hi | python3 -c 'print(1)'", "echo hi | cat"):
            self.assertIsNone(sl.home_write_decision("Bash", {"command": cmd}, self.project), cmd)

    def test_a_script_or_module_that_names_a_home_and_writes_is_denied(self):
        with open(os.path.join(self.project, "s.py"), "w") as fh:
            fh.write("open(%r, 'w').write('x')\n" % (self.budgit_home() + "/trust.json"))
        for cmd in ("python3 s.py", "python3 -m s", "python3 -c 'import s'", "cat s.py | python3", "python3 < s.py"):
            self.deny(cmd)
        with open(os.path.join(self.project, "t.py"), "w") as fh:
            fh.write("print(1)\n")
        for cmd in ("python3 t.py", "python3 -m t", "cat t.py | python3"):
            self.assertIsNone(sl.home_write_decision("Bash", {"command": cmd}, self.project), cmd)


class SessionScriptReadTest(GuardCase):
    def test_a_module_or_piped_script_the_session_wrote_is_read_before_it_runs(self):
        path = os.path.join(self.project, "s.py")
        with open(path, "w") as fh:
            fh.write("import os\nos.remove('da' + 'ta.json')\n")
        sl.note_session_write("sess-1", path)
        for cmd in ("python3 s.py", "python3 -m s", "python3 -c 'import s'", "cat s.py | python3", "python3 < s.py"):
            got = sl.real_data_decision("Bash", {"command": cmd}, self.project, cwd=self.project, session="sess-1")
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
        with open(os.path.join(self.project, "t.py"), "w") as fh:
            fh.write("print(1)\n")
        sl.note_session_write("sess-1", os.path.join(self.project, "t.py"))
        for cmd in ("python3 t.py", "python3 -m t", "cat t.py | python3"):
            self.assertIsNone(sl.real_data_decision("Bash", {"command": cmd}, self.project, cwd=self.project, session="sess-1"), cmd)


class McpArgumentsTest(GuardCase):
    def decide(self, tool, args):
        return sl.mcp_file_decision(tool, args, self.project, self.project)

    def test_a_path_under_any_key_is_judged(self):
        for tool, args in (("mcp__fs__write_file", {"output_path": "data.json"}), ("mcp__fs__write_file", {"target_file": "data.json"}),
                           ("mcp__fs__write_file", {"files": [{"p": "data.json"}]}),
                           ("mcp__fs__rename_file", {"new_name": "data.json"}),
                           ("mcp__fs__write_file", {"name": "data/real/x.csv"}),
                           ("mcp__fs__readwrite", {"path": "data.json"})):
            got = self.decide(tool, args)
            self.assertIsNotNone(got, (tool, args))
            self.assertEqual(got[0], "ask", (tool, args))

    def test_a_shell_style_tool_is_judged_as_a_command(self):
        for tool, args in (("mcp__sh__run_command", {"command": "echo x > data.json"}), ("mcp__sh__execute", {"cmd": "rm data.json"}),
                           ("mcp__sh__run", {"args": ["rm", "data.json"]}), ("mcp__x__get_thing", {"command": "rm data.json"})):
            got = self.decide(tool, args)
            self.assertIsNotNone(got, (tool, args))
            self.assertEqual(got[0], "ask", (tool, args))
        got = self.decide("mcp__sh__execute", {"command": "echo x > %s/trust.json" % sl.home_state()})
        self.assertEqual(got[0], "deny")

    def test_plain_calls_are_left_alone(self):
        for tool, args in (("mcp__fs__read_file", {"path": "docs/a.md"}), ("mcp__fs__write_file", {"path": "docs/a.md", "content": "data.json notes"}),
                           ("mcp__sh__run", {"command": "ls docs"}), ("mcp__fs__list_directory", {"path": "docs"})):
            self.assertIsNone(self.decide(tool, args), (tool, args))


class TrustCommandTest(unittest.TestCase):
    def test_the_trust_command_cannot_be_invoked_by_the_model(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "commands", "budgit-trust.md")
        head = open(path).read().split("---")[1]
        self.assertIn("disable-model-invocation: true", head)


if __name__ == "__main__":
    unittest.main()
