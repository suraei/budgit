"""R-908 (verdict survives the call cap), R-750/R-904 (cap reads the brief; maxTurns warning), R-900 (commit chained)."""
import os, tempfile
from helpers import StudioTestCase
import agentcap
import commitguard


class VerdictSurvivesCapTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.proj = os.path.realpath(tempfile.mkdtemp())
        os.makedirs(os.path.join(self.proj, ".budgit", "verify"))
        os.makedirs(os.path.join(self.proj, ".git"))
        self.report = os.path.join(self.proj, ".budgit", "verify", "R-9.md")

    def call(self, n, agent_type="studio:verifier", tool="Bash", ti=None, agent="v1"):
        out = None
        for _ in range(n):
            out = agentcap.check({"agent_id": agent, "agent_type": agent_type, "tool_name": tool,
                                  "tool_input": ti or {"command": "ls"}, "cwd": self.proj}, 60)
        return out

    def over_cap(self, agent_type="studio:verifier", agent="v1"):
        self.call(80, agent_type=agent_type, agent=agent)       # cap 60 + hard margin 20: the next call is refused

    def test_verifier_at_cap_can_write_its_verify_report(self):
        self.over_cap()
        self.assertEqual(self.call(1, tool="Bash")[0], "deny")
        for tool in ("Write", "Edit"):
            self.assertIsNone(self.call(1, tool=tool, ti={"file_path": self.report}))

    def test_verifier_at_cap_cannot_run_bash_or_write_elsewhere(self):
        self.over_cap()
        self.assertEqual(self.call(1, tool="Bash")[0], "deny")
        self.assertEqual(self.call(1, tool="Write", ti={"file_path": os.path.join(self.proj, "src.py")})[0], "deny")
        self.assertEqual(self.call(1, tool="Write", ti={"file_path": os.path.join(self.proj, ".budgit", "verify", "sub", "R-9.md")})[0], "deny")
        self.assertEqual(self.call(1, tool="Write", ti={"file_path": os.path.join(self.proj, ".budgit", "verify", "R-9.txt")})[0], "deny")

    def test_builder_at_cap_cannot_write_a_verify_report(self):
        self.over_cap("studio:builder", "b1")
        out = self.call(1, "studio:builder", "Write", {"file_path": self.report}, "b1")
        self.assertEqual(out[0], "deny")

    def test_warning_appears_at_cap_minus_ten(self):
        self.assertIsNone(self.call(49))
        kind, text = self.call(1)                                # call 50 = 60 - 10
        self.assertEqual(kind, "note")
        self.assertIn(".budgit/verify/", text)
        self.assertEqual(agentcap.REPORT_MARGIN, 10)

    def write(self, path, agent="v1"):
        return self.call(1, tool="Write", ti={"file_path": path}, agent=agent)

    def test_exemption_is_bound_to_the_own_project(self):
        self.over_cap()
        other = os.path.realpath(tempfile.mkdtemp())
        os.makedirs(os.path.join(other, ".budgit", "verify"))
        os.makedirs(os.path.join(other, ".git"))
        self.assertEqual(self.write(os.path.join(other, ".budgit", "verify", "R-9.md"))[0], "deny")
        loose = os.path.realpath(tempfile.mkdtemp())            # a /tmp folder in no repository
        os.makedirs(os.path.join(loose, ".budgit", "verify"))
        self.assertEqual(self.write(os.path.join(loose, ".budgit", "verify", "R-9.md"))[0], "deny")
        self.assertIsNone(self.write(self.report))

    def test_exemption_allowed_names(self):
        self.over_cap()
        v = os.path.join(self.proj, ".budgit", "verify")
        for ok in ("R-12.md", "RELEASE-0.13.9.md", "RC-0.13.9.10.md"):
            self.assertIsNone(self.write(os.path.join(v, ok)), ok)

    def test_exemption_refused_names_and_folders(self):
        self.over_cap()
        v = os.path.join(self.proj, ".budgit", "verify")
        self.assertEqual(self.write(os.path.join(v, "notes.md"))[0], "deny")
        self.assertEqual(self.write(os.path.join(v, "R-x.md"))[0], "deny")
        self.assertEqual(self.write(os.path.join(self.proj, ".budgit", "notes", "R-1.md"))[0], "deny")   # kills the last == "verify" mutation

    def test_at_most_three_report_writes_past_the_cap(self):
        self.over_cap()
        for _ in range(3):
            self.assertIsNone(self.write(self.report))
        self.assertEqual(self.write(self.report)[0], "deny")

    def test_no_report_warning_for_a_builder(self):
        self.assertIsNone(self.call(50, "studio:builder", "Bash", None, "b2"))


class BriefBudgetTest(StudioTestCase):
    def test_budget_line_forms(self):
        for text, n in (("Budget: 50 tool calls", 50), ("Budget: 45", 45), ("Call budget: 30", 30), ("Budget: 20 more", 20)):
            self.assertEqual(int(agentcap._BRIEF_BUDGET.search("x\n" + text).group(1)), n, text)
        self.assertIsNone(agentcap._BRIEF_BUDGET.search("no line here"))

    def test_loose_budget_lines_set_nothing(self):
        for text in ("Budget 2026 notes from the retro", "Budget: 2 hours", "Budget: items x 8", "Budget is 40"):
            self.assertIsNone(agentcap._BRIEF_BUDGET.search("x\n" + text), text)

    def test_real_resume_lines_are_read(self):
        for text, n in (("Budget: 30 more tool calls. Re-verify the R-908/R-750/R-904/R-900 batch at f34df0dc.", 30),
                        ("Budget: 30 more calls", 30), ("Budget: 12 more tool calls. Busy has cleared.", 12)):
            self.assertEqual(int(agentcap._BRIEF_BUDGET.search("x\n" + text).group(1)), n, text)

    def test_round_start_prints_the_budget_read(self):
        out = agentcap.check({"agent_id": "r1", "tool_name": "Bash"}, 60)
        self.assertIn("budget read: none, brief default 60", out[1])
        import json
        tr = os.path.join(tempfile.mkdtemp(), "agent-r2.jsonl")
        with open(tr, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "Budget: 50 tool calls"}}) + "\n")
        out = agentcap.check({"agent_id": "r2", "tool_name": "Bash", "agent_transcript_path": tr}, 60)
        self.assertIn("budget read: 50", out[1])
        self.assertIsNone(agentcap.check({"agent_id": "r2", "tool_name": "Bash", "agent_transcript_path": tr}, 60))

    def test_warning_at_80_percent_once_per_round(self):
        import json
        tdir = tempfile.mkdtemp()
        tr = os.path.join(tdir, "agent-w1.jsonl")
        with open(tr, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "Budget: 50 tool calls"}}) + "\n")
        notes = []
        for _ in range(50):
            out = agentcap.check({"agent_id": "w1", "tool_name": "Bash", "agent_transcript_path": tr}, 60)
            notes.append(out)
        self.assertEqual([i + 1 for i, o in enumerate(notes) if o and "80 % of your budget used" in o[1]], [40])
        self.assertIn("80 % of your budget used (40 of 50): stop starting new work", notes[39][1])

    def test_launch_warning_when_budget_above_maxturns(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "security-reviewer.md"), "w") as f:
            f.write("---\nname: security-reviewer\nmaxTurns: 25\n---\nbody\n")
        w = agentcap.launch_warning({"subagent_type": "studio:security-reviewer", "prompt": "Budget: 45 tool calls"}, d)
        self.assertIn("maxTurns 25", w)
        self.assertIsNone(agentcap.launch_warning({"subagent_type": "studio:security-reviewer", "prompt": "Budget: 20"}, d))
        self.assertIsNone(agentcap.launch_warning({"subagent_type": "studio:security-reviewer", "prompt": "no line"}, d))
        self.assertIsNone(agentcap.launch_warning({"subagent_type": "studio:builder", "prompt": "Budget: 45"}, d))

    def test_real_security_reviewer_definition_has_maxturns(self):
        self.assertEqual(agentcap.max_turns("studio:security-reviewer"), 25)

    def test_without_a_line_today_behaviour_stays(self):
        out = None
        for _ in range(55):
            out = agentcap.check({"agent_id": "n1", "tool_name": "Bash"}, 60)
        self.assertEqual(out[0], "note")                         # cap - 5


class CommitChainTest(StudioTestCase):
    def d(self, cmd, agent="a1"):
        return commitguard.decision("Bash", {"command": cmd}, agent)

    def test_bare_commit_refused_for_a_subagent(self):
        self.assertEqual(self.d("git commit -m x")[0], "deny")
        self.assertEqual(self.d("git -C /w commit -m x")[0], "deny")
        self.assertIn("<tests> && git commit", self.d("git commit -m x")[1])

    def test_chained_after_tests_allowed(self):
        self.assertIsNone(self.d("python3 -m unittest t && git commit -m x"))
        self.assertIsNone(self.d("pytest -q && git -C /w commit -m 'a && b'"))
        self.assertIsNone(self.d("budgit test && git add f && git commit -m x"))

    def test_real_a4cc00ab_command_is_allowed(self):
        cmd = ("nice -n 10 lockf -k /private/tmp/budgit-suite-studio.lock env PYTHONPATH=/w/tests:/w/plugin/scripts "
               "python3 -W ignore -m unittest test_r853_codex_limit test_x && git -C /w commit -am \"msg\"")
        self.assertIsNone(self.d(cmd))

    def test_other_runners_allowed(self):
        for cmd in ("python3 tools/runtests.py && git commit -m x", "python3.12 -B -m unittest t && git commit -m x",
                    "./run_tests.sh && git commit -m x", "npm test && git commit -m x", "pnpm run test && git commit -m x",
                    "cargo test && git commit -m x", "go test ./... && git commit -m x", "make check && git commit -m x",
                    "timeout 300 pytest -q && git commit -m x", "budgit test && git commit -m x"):
            self.assertIsNone(self.d(cmd), cmd)

    def test_non_runners_and_broken_chains_refused(self):
        for cmd in ("echo pytest && git commit -m x", "printf 'python3 -m unittest' && git commit -m x",
                    "true && git commit -m x", ": && git commit -m x", "pytest || git commit -m x",
                    "pytest ; git commit -m x", "pytest | tail -3 && git commit -m x", "python3 -c 'print(1)' && git commit -m x",
                    "git -C /w commit -am x", "npm install && git commit -m x"):
            self.assertEqual(self.d(cmd)[0], "deny", cmd)

    def test_shell_test_builtin_is_not_a_runner(self):
        for cmd in ("test -n x && git commit -m x", "[ -n x ] && git commit -m x", "[[ -n x ]] && git commit -m x",
                    "/bin/test -n x && git commit -m x", "/usr/bin/[ -n x ] && git commit -m x",
                    "python3 -c \"print('test')\" && git commit -m x"):
            self.assertEqual(self.d(cmd)[0], "deny", cmd)

    def test_semicolon_or_or_refused(self):
        self.assertEqual(self.d("python3 -m unittest t ; git commit -m x")[0], "deny")
        self.assertEqual(self.d("python3 -m unittest t || git commit -m x")[0], "deny")

    def test_coordinator_commit_allowed(self):
        self.assertIsNone(self.d("git commit -m x", agent=None))
