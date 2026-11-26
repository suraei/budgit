"""R-212 / R-232: approving the verify.prepare shell commands (they come from a repo file) takes the user at a terminal.
The model's Bash tool has no terminal, so it can no longer approve; the error prints the exact command for the user to
run (the desktop app's Run button on a bash block opens it in the Terminal panel)."""
import json, os, pty, select, subprocess, sys, time

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


class ApprovalTerminalBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, bind=False)
        self.pj = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(self.pj))
        cfg["verify"]["prepare"] = ["touch pwned"]
        json.dump(cfg, open(self.pj, "w"))
        self.env = dict(os.environ, BUDGIT_HOME=self.home)
        self.approvals = os.path.join(self.home, "approved_prepare.json")

    def run_cli(self, *args):          # piped stdio, like the model's Bash tool
        return subprocess.run([sys.executable, CLI, "--project", self.project] + list(args), env=self.env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def run_on_terminal(self, answer, *args):
        """The same CLI with a real terminal on stdin and stdout, the user typing `answer`."""
        master, slave = pty.openpty()
        proc = subprocess.Popen([sys.executable, CLI, "--project", self.project] + list(args), env=self.env,
                                stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        out, deadline, sent = b"", time.time() + 20, False
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                if not sent and b"[y/N]" in out:
                    os.write(master, answer.encode() + b"\n")
                    sent = True
            elif proc.poll() is not None:
                break
        proc.wait(timeout=10)
        os.close(master)
        return proc.returncode, out.decode("utf-8", "replace")


class TerminalOnlyTest(ApprovalTerminalBase):
    def test_piped_stdio_cannot_approve(self):
        r = self.run_cli("prepare", "--approve")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.approvals))
        self.assertIn("terminal", r.stderr)
        self.assertIn("prepare --approve", r.stderr)                 # the exact command to run

    def test_importing_the_function_cannot_approve_either(self):
        code = "import sys; sys.path.insert(0, %r); import studiolib as sl; sl.approve_prepare(%r)" % (
            os.path.join(ROOT, "plugin", "scripts"), self.project)
        r = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.approvals))

    def test_the_user_at_a_terminal_can_approve_after_reading_the_commands(self):
        code, out = self.run_on_terminal("y", "prepare", "--approve")
        self.assertEqual(code, 0, out)
        self.assertIn("touch pwned", out)                            # the commands are shown before the question
        self.assertTrue(os.path.isfile(self.approvals))
        self.assertTrue(sl.run_prepare(self.project)["ok"])

    def test_control_characters_in_a_command_are_shown_as_text_not_obeyed(self):     # R-248: \r or ESC cannot hide part of the command
        cfg = json.load(open(self.pj))
        cfg["verify"]["prepare"] = ["echo safe\r\x1b[2Kecho hidden\x1b]0;title\x07 && touch pwned2"]
        json.dump(cfg, open(self.pj, "w"))
        code, out = self.run_on_terminal("n", "prepare", "--approve")
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertEqual(out.count("\r"), out.count("\r\n"))                        # the pty's own line ends only, none inside the command
        self.assertIn("\\x1b", out)                                                  # the person sees that something is there
        self.assertIn("touch pwned2", out)

    def test_the_audit_line_of_an_approval_has_no_control_characters(self):         # R-248: the Stop hook shows it to the user
        cfg = json.load(open(self.pj))
        cfg["verify"]["prepare"] = ["echo a\x1b[2K\nforged line"]
        json.dump(cfg, open(self.pj, "w"))
        code, out = self.run_on_terminal("y", "prepare", "--approve")
        self.assertEqual(code, 0, out)
        log = open(os.path.join(sl.studio_dir(self.project), "audit.log"), encoding="utf-8").read()
        self.assertNotIn("\x1b", log)
        self.assertEqual(len([l for l in log.splitlines() if "prepare commands approved" in l]), 1)
        self.assertFalse(any(l.startswith("forged line") for l in log.splitlines()))

    def test_the_digest_that_gets_approved_is_shown(self):                         # R-248: what is hashed is what is on screen
        code, out = self.run_on_terminal("y", "prepare", "--approve")
        self.assertEqual(code, 0, out)
        digest = sl._prepare_digest(self.project, ["touch pwned"])
        self.assertIn(digest[:16], out)
        self.assertEqual(json.load(open(self.approvals))[os.path.realpath(self.project)], digest)    # the approval stays over the raw commands

    def test_anything_but_yes_approves_nothing(self):
        for answer in ("", "n", "no", "yess"):
            code, out = self.run_on_terminal(answer, "prepare", "--approve")
            self.assertNotEqual(code, 0, answer)
            self.assertFalse(os.path.exists(self.approvals), answer)

    def test_an_unapproved_prepare_prints_the_command_to_run(self):
        r = self.run_cli("prepare")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("python3 ", r.stderr)
        self.assertIn("--project", r.stderr)
        self.assertIn("prepare --approve", r.stderr)
        self.assertIn(os.path.join("plugin", "bin", "budgit"), r.stderr)


class GuardTest(ApprovalTerminalBase):
    def decision(self, tool, tool_input):
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], env=self.env, capture_output=True, text=True,
                           input=json.dumps({"session_id": "zz", "cwd": self.tmp, "tool_name": tool,
                                             "tool_input": tool_input}))
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        return out.get("hookSpecificOutput", {}).get("permissionDecision")

    def test_a_command_naming_the_approval_function_or_file_asks(self):
        cmd = "cat ~/.studio/approved_prepare.json"
        self.assertEqual(self.decision("Bash", {"command": cmd}), "ask", cmd)
        # a write into the Budgit home is denied outright, not asked
        cmd = "echo {} > ~/.budgit/approved_prepare.json"
        self.assertEqual(self.decision("Bash", {"command": cmd}), "deny", cmd)
        # R-335 round 3: running Budgit's library by hand is denied outright
        cmd = "python3 -c 'import studiolib as s; s.approve_prepare(\".\")'"
        self.assertEqual(self.decision("Bash", {"command": cmd}), "deny", cmd)

    def test_writing_the_approvals_file_is_denied(self):
        for tool in ("Write", "Edit"):
            path = os.path.join(self.home, "approved_prepare.json")
            self.assertEqual(self.decision(tool, {"file_path": path, "content": "{}", "old_string": "a",
                                                  "new_string": "b"}), "deny", tool)
