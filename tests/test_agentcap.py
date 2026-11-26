"""R-63 (her I-46/I-47): the per-agent tool-call ceiling is enforced, not just written in the brief.
cap-5 calls -> a note to wrap up; past cap+20 every tool but Write (to save the report) is denied."""
import io, json, os, sys, contextlib
from helpers import StudioTestCase, ROOT, codex_on
import agentcap


class AgentCapTest(StudioTestCase):
    def call(self, n, agent="a1b2c3", tool="Bash", cap=60):
        out = None
        for _ in range(n):
            out = agentcap.check({"agent_id": agent, "tool_name": tool}, cap)
        return out

    def test_main_session_calls_are_not_counted(self):
        self.assertIsNone(agentcap.check({"tool_name": "Bash"}, 60))
        self.assertFalse(os.path.isdir(os.path.join(self.home, "agent-calls")))

    def test_soft_note_at_cap_minus_five(self):
        self.assertIsNone(self.call(54))
        kind, text = self.call(1)
        self.assertEqual(kind, "note")
        self.assertIn("work 55, waits 0", text)

    def test_waits_are_free_and_shown_apart(self):
        waits = [("Bash", {"command": "sleep 30"}),
                 ("Bash", {"command": "tail -n 5 /private/tmp/x/tasks/abc.output"}),
                 ("TaskOutput", {"task_id": "t"}), ("BashOutput", {"bash_id": "b"})]
        for tool, inp in waits:
            for _ in range(20):
                self.assertIsNone(agentcap.check({"agent_id": "w1", "tool_name": tool, "tool_input": inp}, 60))
        self.assertIsNone(self.call(54, agent="w1"))      # 80 waits, still no note
        kind, text = self.call(1, agent="w1")
        self.assertEqual(kind, "note")
        self.assertIn("work 55, waits 80", text)

    def test_a_real_command_is_work_not_a_wait(self):
        self.assertFalse(agentcap.is_wait("Bash", {"command": "sleep 5; rm x"}))
        self.assertFalse(agentcap.is_wait("Bash", {"command": "pytest -q"}))
        self.assertTrue(agentcap.is_wait("Bash", {"command": "sleep 5"}))

    def test_only_one_plain_shape_is_a_wait(self):
        for cmd in ("cat a.output|sh", "cat /tasks/x;pytest", "tail -f x.output;curl${IFS}y",
                    "cat /tasks/$(rm${IFS}-rf${IFS}z)", "cat /tasks/x>/repo/src/app.py",
                    "cat /repo/tasks/main.py", "head -n 400 /repo/plugin/tasks/build.py",
                    "sleep 5;ls", "sleep 5 ", "cat /p/tasks/a.output extra", "cat /p/tasks/a.output\nls",
                    "cat /p/tasks/a.output`id`", "sleep 1.5", "tail -f /p/tasks/a.output"):
            self.assertFalse(agentcap.is_wait("Bash", {"command": cmd}), cmd)
        for cmd in ("sleep 30", "cat /p/tasks/a.output", "tail -n 30 /p/tasks/a-b_1.output",
                    "head -20 /p/tasks/a.output"):
            self.assertTrue(agentcap.is_wait("Bash", {"command": cmd}), cmd)

    def test_a_tool_use_id_repeated_in_a_second_record_counts_once(self):
        import comply
        rec = {"type": "assistant", "message": {"id": "m", "content": [
            {"type": "tool_use", "id": "toolu_x", "name": "Read", "input": {"file_path": "/a"}}]}}
        seen = set()
        self.assertEqual(len(list(comply.own_calls(rec, seen))), 1)
        self.assertEqual(len(list(comply.own_calls(rec, seen))), 0)

    def test_a_stale_two_field_state_for_a_missing_file_does_not_crash(self):
        import introspect
        main = os.path.join(self.tmp, "s7.jsonl")
        with open(main, "w") as f:
            f.write("")
        self.assertIsNone(introspect.live_check("s7", main, level="normal"))
        data = introspect._load("s7")
        data["inc"].setdefault("subs", {})["/gone/agent-zz.jsonl"] = [10, 5]
        data["inc"]["subs"]["/gone/agent-yy.jsonl"] = [10, 5, 2]
        introspect._save("s7", data)
        self.assertIsNone(introspect.live_check("s7", main, level="normal"))
        self.assertEqual(len(introspect._load("s7")["inc"]["subs"]["/gone/agent-zz.jsonl"]), 4)

    def test_transcript_audit_counts_work_only_and_the_warning_shows_both(self):
        import comply, introspect
        base = {"type": "assistant", "message": {"id": "m", "content": []}}
        counter = [0]

        def use(name, inp):
            counter[0] += 1
            rec = json.loads(json.dumps(base))
            rec["message"]["content"] = [{"type": "tool_use", "id": "toolu_%d" % counter[0], "name": name, "input": inp}]
            return rec
        recs = []
        for r in ([use("Edit", {"file_path": "/x"}) for _ in range(55)]
                  + [use("Bash", {"command": "sleep 20"}) for _ in range(10)]
                  + [use("TaskOutput", {}) for _ in range(5)]):
            recs += [r, r]            # real shape: every tool_use id is written in two records
        main = os.path.join(self.tmp, "s9.jsonl")
        sub = os.path.join(self.tmp, "s9", "subagents")
        os.makedirs(sub)
        agent = os.path.join(sub, "agent-ab12.jsonl")
        with open(main, "w") as f:
            f.write("")
        with open(agent, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")      # 55 work, 15 waits; 140 raw records
        self.assertEqual(comply.audit(main, level="normal")["agents_over_call_budget"], 0)
        self.assertIsNone(introspect.live_check("s9", main, level="normal"))      # baseline
        with open(agent, "a") as f:
            f.write("\n".join(json.dumps(use("Read", {"file_path": "/y"})) for _ in range(6)) + "\n")
        self.assertEqual(comply.audit(main, level="normal")["agents_over_call_budget"], 1)   # 61 work > 60
        note = introspect.live_check("s9", main, level="normal")
        self.assertIn("agents over call budget (work 61, waits 15)", note)

    def test_hard_stop_past_cap_plus_twenty_except_write(self):
        self.call(80)
        kind, text = self.call(1)
        self.assertEqual(kind, "deny")
        self.assertIn("ceiling", text)
        wt = os.path.realpath(os.path.join(self.home, "lwt"))     # R-868: only <cwd>/progress*.md, cwd a linked worktree
        os.makedirs(os.path.join(wt, "gd"))
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % os.path.join(wt, "gd"))
        with open(os.path.join(wt, "gd", "HEAD"), "w") as f:
            f.write("ref: refs/heads/claude/x\n")
        ok = {"agent_id": "a1b2c3", "tool_name": "Write", "cwd": wt, "tool_input": {"file_path": wt + "/progress.md"}}
        self.assertIsNone(agentcap.check(ok, 60))
        self.assertEqual(agentcap.check({"agent_id": "a1b2c3", "tool_name": "Write"}, 60)[0], "deny")

    def test_agents_are_counted_separately_and_reset(self):
        self.call(70, agent="x1")
        self.assertIn("budget read: none", self.call(1, agent="y2")[1])      # the first call of a round only prints the budget read
        agentcap.reset("x1")
        self.assertIn("budget read: none", self.call(1, agent="x1")[1])      # a reset starts a new round

    def test_odd_ids_cannot_escape_the_folder(self):
        agentcap.check({"agent_id": "../../etc/x", "tool_name": "Bash"}, 60)
        files = os.listdir(os.path.join(self.home, "agent-calls"))
        self.assertEqual(len(files), 1)
        self.assertNotIn("/", files[0])

    def test_hook_wires_it(self):
        import subprocess
        env = dict(os.environ, STUDIO_HOME=self.home)
        os.makedirs(os.path.join(self.home, "agent-calls"), exist_ok=True)
        with open(os.path.join(self.home, "agent-calls", "zz9"), "w") as f:
            f.write("200")
        inp = {"session_id": "s", "agent_id": "zz9", "agent_type": "general-purpose", "tool_name": "Grep",
               "tool_input": {"pattern": "x", "path": self.project}, "cwd": self.project}
        r = subprocess.run([sys.executable, os.path.join(ROOT, "plugin", "scripts", "hook.py"), "pre-tool-use"],
                           input=json.dumps(inp), capture_output=True, text=True, env=env)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")


    def test_r64_subagent_stop_clears_the_counter_in_an_unbound_session_too(self):
        """R-64: SubagentStop returned before agentcap.reset in unbound sessions, so counters piled up."""
        import subprocess
        env = dict(os.environ, STUDIO_HOME=self.home)
        os.makedirs(os.path.join(self.home, "agent-calls"), exist_ok=True)
        counter = os.path.join(self.home, "agent-calls", "zz8")
        with open(counter, "w") as f:
            f.write("12")
        inp = {"session_id": "unbound-r64", "agent_id": "zz8", "agent_type": "general-purpose", "cwd": self.project,
               "hook_event_name": "SubagentStop", "last_assistant_message": "done"}
        r = subprocess.run([sys.executable, os.path.join(ROOT, "plugin", "scripts", "hook.py"), "subagent-stop"],
                           input=json.dumps(inp), capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(counter))

class CodexFirstNudgeTest(StudioTestCase):
    """R-63: before an Opus review/verifier round, the cheap ChatGPT-lane review runs first (0 uses in 3 days)."""

    def test_judgement_dispatch_without_a_recent_codex_review_is_nudged(self):
        import codexlane
        self.init_project()
        codex_on(self.home)
        self.assertIn("budgit codex review", codexlane.review_first_note(self.project, "budgit:verifier"))
        self.assertIsNone(codexlane.review_first_note(self.project, "studio:researcher"))
        self.assertIsNone(codexlane.review_first_note(None, "studio:verifier"))
        rdir = os.path.join(self.project, ".studio", "reports")
        os.makedirs(rdir, exist_ok=True)
        open(os.path.join(rdir, "20261020-120000-codex-review-default.md"), "w").close()
        self.assertIsNone(codexlane.review_first_note(self.project, "studio:verifier"))
        old = os.path.join(rdir, "20261020-120000-codex-review-default.md")
        os.utime(old, (1, 1))
        self.assertIn("budgit codex review", codexlane.review_first_note(self.project, "budgit:plan-critic"))


class BuildSessionsOnSonnetTest(StudioTestCase):
    """R-63: execution-only (build) sessions run on sonnet; opus stays for plan/design/QA judgement."""

    def test_every_level_names_the_build_model(self):
        import budget
        for level in budget.LEVELS:
            line = budget.short_line(level, "x")
            self.assertIn("build sessions: sonnet", line)
            self.assertIn("build sessions: sonnet", budget.models_text(level))
        self.assertIn("mechanical haiku", budget.short_line("normal", "x"))
