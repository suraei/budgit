"""R-1360 task F1: the default doors. The first try uses the door, so no refusal is needed to learn it: agent contracts send a commit
message through `commit -F <file>` and the free text of an own budgit verb through `--text-file <file>`, and the search guard's
refusal texts name the same commit door.

R-1360 task F2: the same default forms are replayed through the pre-tool guards (hook.on_pre_tool_use, the one in-process entry that
runs verify-forgery, home-write, commit-chain, merge, attribution, no-rm, commit-identity, approval, persistence, destructive-git,
real-data and contract-lock), as a subagent and as the coordinator: no deny and no ask."""
import glob
import os
import re
import tempfile
import unittest

from helpers import ROOT, StudioTestCase
import searchguard

AGENTS = os.path.join(ROOT, "plugin", "agents")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")       # this plugin's own CLI: a bare `budgit` off PATH is not recognised as it
COMMIT_M = re.compile(r"commit\b[^\n`]*\s(?:-m|--message)\b")
TEXT_VERBS = re.compile(r"budgit ledger (?:finding add|link)|budgit friction add")


def contracts():
    paths = sorted(glob.glob(os.path.join(AGENTS, "*.md")))
    out = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            out[os.path.basename(path)] = f.read()
    return out


class AgentContractDoorsTest(unittest.TestCase):
    def test_no_contract_holds_a_commit_dash_m_instruction(self):
        found = contracts()
        self.assertTrue(found)
        for name, text in found.items():
            with self.subTest(contract=name):
                self.assertIsNone(COMMIT_M.search(text), name)

    def test_the_builder_contract_names_commit_dash_f(self):
        self.assertIn("commit -F", contracts()["builder.md"])

    def test_verifier_and_security_reviewer_use_text_file_and_never_inline_text(self):
        found = contracts()
        for name in ("verifier.md", "security-reviewer.md"):
            with self.subTest(contract=name):
                self.assertIn("--text-file", found[name])
                self.assertNotIn('--text "', found[name])

    def test_every_contract_that_names_a_budgit_text_verb_names_text_file(self):
        for name, text in contracts().items():
            if TEXT_VERBS.search(text):
                with self.subTest(contract=name):
                    self.assertIn("--text-file", text)


class SearchGuardTextTest(unittest.TestCase):
    def test_the_refusal_texts_name_commit_dash_f_and_not_dash_m(self):
        source = os.path.join(ROOT, "plugin", "scripts", "searchguard.py")
        with open(source, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("git commit -F <file>", text)
        self.assertIn("git commit -F <archivo>", text)
        self.assertNotIn("git commit -m", text)


class DefaultDoorsReplayTest(StudioTestCase):
    """Each default-form command goes through every pre-tool guard in process; a refusal here is a door a first try would hit."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.project, ".budgit"), exist_ok=True)       # the deny log of the control case lands here
        self.repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.repo, "tools"), exist_ok=True)
        self.msg = os.path.join(self.repo, "msg.txt")
        with open(self.msg, "w", encoding="utf-8") as f:
            f.write("R-1: a plain one-line message\n")
        self.text = os.path.join(self.repo, "text.txt")
        with open(self.text, "w", encoding="utf-8") as f:
            f.write("a plain one-line text\n")

    def verdict(self, command, agent_id=None):
        import hook
        inp = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.project, "session_id": "s-f2"}
        if agent_id:
            inp["agent_id"] = agent_id
        hook._OUT.clear()
        try:
            hook.on_pre_tool_use(inp, self.project)
            out = dict(hook._OUT.get("hookSpecificOutput") or {})
        finally:
            hook._OUT.clear()
        return out.get("permissionDecision"), out.get("permissionDecisionReason")

    def assertOpenDoor(self, command):
        for agent_id in ("agent-f2", None):
            with self.subTest(command=command, agent_id=agent_id):
                got = self.verdict(command, agent_id)
                self.assertNotIn(got[0], ("deny", "ask"), "%s -> %s" % (command, got))

    def test_the_default_commit_through_a_message_file_passes_for_a_subagent_and_the_coordinator(self):
        self.assertOpenDoor("python3 -I -W ignore %s/tools/gate_test.py test_x && git -C %s commit -F %s" % (self.repo, self.repo, self.msg))

    def test_the_default_ledger_finding_add_through_a_text_file_passes(self):
        self.assertOpenDoor("%s ledger finding add R-1 --sev LOW --class docs --text-file %s" % (CLI, self.text))

    def test_the_default_friction_add_through_a_text_file_passes(self):
        self.assertOpenDoor("%s friction add --text-file %s" % (CLI, self.text))

    def test_the_default_ledger_link_through_a_text_file_passes(self):
        self.assertOpenDoor("%s ledger link R-1 --src I-1 --text-file %s" % (CLI, self.text))

    def test_negative_control_the_inline_dash_m_commit_is_still_refused_or_asked(self):
        got = self.verdict("git -C %s commit -m \"fix the git hook\"" % self.repo, "agent-f2")
        self.assertIn(got[0], ("deny", "ask"), got)


if __name__ == "__main__":
    unittest.main()
