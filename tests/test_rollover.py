import json, os, stat, subprocess, sys
from helpers import StudioTestCase, ROOT
import studiolib as sl
from test_context_nudge import usage_line

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


class RolloverTest(StudioTestCase):
    """Automatic rollover: checkpoint before compaction, agent reports saved, state re-injected after."""

    def setUp(self):
        super().setUp()
        sl.activate(self.project, "b1")
        self.env = dict(os.environ, STUDIO_HOME=self.home, STUDIO_COMPACT_WINDOW="300000")

    def run_hook(self, event, payload):
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def transcript(self, ctx):
        path = os.path.join(self.tmp, "t%d.jsonl" % ctx)
        open(path, "w").write(json.dumps({"type": "user", "message": {"content": "hola"}}) + "\n"
                              + usage_line(ctx) + "\n")
        return path

    def prompt(self, ctx):
        return self.run_hook("user-prompt-submit", {"session_id": "b1", "prompt": "sigue",
                                                    "transcript_path": self.transcript(ctx)})

    def context_of(self, out):
        return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")

    # --- checkpoint -------------------------------------------------------
    def test_checkpoint_asked_once_near_the_window_then_rearmed_after_compaction(self):
        self.assertNotIn("checkpoint", self.context_of(self.prompt(100000)))
        first = self.context_of(self.prompt(210000))
        self.assertIn("checkpoint", first)
        self.assertIn("progress.md", first)
        self.assertNotIn("checkpoint", self.context_of(self.prompt(230000)))   # once per cycle
        self.prompt(60000)                                                      # compacted: context shrank
        self.assertIn("checkpoint", self.context_of(self.prompt(205000)))      # next cycle asks again

    def test_checkpoint_text_and_ledger_notice_both_reach_the_model(self):
        out = self.prompt(210000)       # ledger notice (her new prompt untracked) + checkpoint in one JSON
        ctx = self.context_of(out)
        self.assertIn("checkpoint", ctx)
        self.assertIn("not yet in the ledger", ctx)

    def test_unbound_sessions_get_no_checkpoint(self):
        out = self.run_hook("user-prompt-submit", {"session_id": "u9", "prompt": "x",
                                                   "transcript_path": self.transcript(250000)})
        self.assertNotIn("checkpoint", self.context_of(out))

    # --- agent reports (R-1032: agent dumps live under archive/agent-dumps/<date>/) -----------
    def dumps(self):
        root = os.path.join(sl.studio_dir(self.project), "archive", "agent-dumps")
        return [os.path.join(b, f) for b, _d, fs in os.walk(root) for f in fs if f.endswith(".md")]

    def test_subagent_report_is_saved_redacted_and_private(self):
        text = "Findings: 3 issues. token=ghp_" + "a" * 30 + " end"
        self.run_hook("subagent-stop", {"session_id": "b1", "agent_id": "ab12cd34ef", "agent_type": "studio:researcher",
                                        "last_assistant_message": text, "stop_hook_active": False})
        d = ""
        files = self.dumps()
        self.assertEqual(len(files), 1)
        self.assertIn("researcher", files[0])
        body = open(os.path.join(d, files[0])).read()
        self.assertIn("Findings: 3 issues", body)
        self.assertNotIn("ghp_" + "a" * 30, body)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, files[0])).st_mode), 0o600)

    def test_handback_report_is_saved_from_tool_input(self):
        self.run_hook("post-tool-use", {"session_id": "b1", "tool_name": "SubagentHandback",
                                        "tool_input": {"message": "Report: all green"}, "agent_id": "zz99",
                                        "agent_type": "Explore"})
        self.assertIn("Report: all green", open(self.dumps()[0]).read())

    def test_hostile_agent_names_cannot_escape_the_reports_folder(self):
        self.run_hook("subagent-stop", {"session_id": "b1", "agent_id": "../../etc/x", "agent_type": "../../evil",
                                        "last_assistant_message": "hi", "stop_hook_active": False})
        self.assertEqual(len(self.dumps()), 1)
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(self.project), "..", "..", "etc")))

    def test_symlinked_reports_folder_is_refused_and_nothing_lands_outside(self):
        # a cloned repo could ship .studio/reports -> elsewhere; the hook must not write through it
        outside = os.path.join(self.tmp, "outside"); os.makedirs(outside)
        os.symlink(outside, os.path.join(sl.studio_dir(self.project), "archive"))
        out = self.run_hook("subagent-stop", {"session_id": "b1", "agent_id": "a1", "agent_type": "x",
                                              "last_assistant_message": "secret findings", "stop_hook_active": False})
        self.assertEqual(os.listdir(outside), [])
        self.assertIn("hook error", out["systemMessage"])      # failing closed is visible, never silent

    def test_report_files_are_never_chmodded_through_a_link(self):
        outside = os.path.join(self.tmp, "victim.txt"); open(outside, "w").write("x"); os.chmod(outside, 0o644)
        d = os.path.join(sl.studio_dir(self.project), "reports"); os.makedirs(d)
        # the race: a link planted at the final name after the write must not be followed by chmod
        orig = os.chmod
        calls = []
        os.chmod = lambda *a, **k: calls.append(a)
        try:
            path = sl.save_report(self.project, "x", "a1", "hello")
        finally:
            os.chmod = orig
        self.assertEqual(calls, [])                       # mkstemp already creates 0600: no chmod at all
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(outside).st_mode), 0o644)

    def test_empty_reports_and_unbound_sessions_save_nothing(self):
        self.run_hook("subagent-stop", {"session_id": "b1", "agent_id": "a1", "agent_type": "x",
                                        "last_assistant_message": "  ", "stop_hook_active": False})
        self.run_hook("subagent-stop", {"session_id": "nobody", "agent_id": "a2", "agent_type": "x",
                                        "last_assistant_message": "hi", "stop_hook_active": False})
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(self.project), "reports")))

    # --- re-injection after compaction -----------------------------------
    def test_session_start_after_compact_lists_full_progress_and_recent_reports(self):
        sl.atomic_write(os.path.join(sl.studio_dir(self.project), "progress.md"),
                        "\n".join("line %d" % i for i in range(40)))
        self.run_hook("subagent-stop", {"session_id": "b1", "agent_id": "q1", "agent_type": "verifier",
                                        "last_assistant_message": "PASS", "stop_hook_active": False})
        out = self.run_hook("session-start", {"session_id": "b1", "source": "compact"})
        ctx = self.context_of(out)
        self.assertIn("last checkpoint: budgit status", ctx)         # R-1137: the role's start budget turns the long list into a pointer
        self.assertIn("saved agent reports: budgit status", ctx)
        whole = sl.session_summary(self.project, "b1", full=True)    # the unbudgeted summary still lists everything
        for need in ("line 0", "line 39", "reports/", "verifier"):
            self.assertIn(need, whole)
