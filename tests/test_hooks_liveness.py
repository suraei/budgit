"""Every hook in hooks.json must be alive: valid config, a handler, and an effect that actually lands where
someone sees it (model context, her systemMessage, a block/deny decision, or the file it promises).
ECC shipped hooks that looked installed and did nothing (#2561, #3260, #3279); this test is the cure.
Adding a hook without a scenario below fails the inventory test on purpose."""
import json, os, re, shutil, subprocess, sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

PLUGIN = os.path.join(ROOT, "plugin")
CONFIG = os.path.join(PLUGIN, "hooks", "hooks.json")
FIX = os.path.join(ROOT, "tests", "fixtures", "transcript_basic.jsonl")
KNOWN_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop", "SessionStart",
                "SessionEnd", "PostCompact", "PreCompact", "Notification", "PostToolUseFailure", "StopFailure", "PermissionRequest"}
VISIBLE = {"systemMessage", "hookSpecificOutput", "decision", "reason", "continue", "stopReason",
           "suppressOutput"}


def load_config():
    with open(CONFIG) as f:
        return json.load(f)


def entries():
    for event, groups in load_config()["hooks"].items():
        for group in groups:
            for h in group["hooks"]:
                if "brote-event.sh" in h.get("command", ""):
                    continue   # R-232: the Brote event channel is not a hook.py handler; tests/test_brote_director.py covers it
                if "floatctl.py" in h.get("command", ""):
                    continue   # R-292: the floating window's SessionStart/SessionEnd hooks are floatctl.py, not hook.py; tests/test_float_hooks.py covers them
                yield event, group, h


class HookConfigTest(StudioTestCase):
    def test_only_known_keys(self):
        cfg = load_config()
        self.assertLessEqual(set(cfg), {"description", "modules", "hooks"})
        for event, groups in cfg["hooks"].items():
            self.assertIn(event, KNOWN_EVENTS)
            for group in groups:
                self.assertLessEqual(set(group), {"matcher", "hooks"}, "metadata in a matcher group (ECC #3053)")
                for h in group["hooks"]:
                    self.assertLessEqual(set(h), {"type", "command", "timeout", "onFailure"})   # onFailure: hooks docs, 2.1.295+

    def test_commands_resolve_and_timeouts_are_short(self):
        import hook
        for event, _, h in entries():
            self.assertEqual(h["type"], "command")
            self.assertLessEqual(h.get("timeout", 60), 30, event)
            m = re.search(r'"\$\{CLAUDE_PLUGIN_ROOT\}/([^"]+)"\s+(\S+)$', h["command"])
            self.assertTrue(m, h["command"])
            self.assertTrue(os.path.isfile(os.path.join(PLUGIN, m.group(1))), m.group(1))
            self.assertIn(m.group(2), hook.HANDLERS, "%s has no handler" % m.group(2))

    def test_matchers_cover_the_tools_the_handlers_need(self):
        groups = load_config()["hooks"]
        post = "|".join(g.get("matcher", "") for g in groups["PostToolUse"])
        pre = "|".join(g.get("matcher", "") for g in groups["PreToolUse"])
        for tool in ("AskUserQuestion", "Bash", "SubagentHandback"):
            self.assertIn(tool, post.split("|"))
        for tool in ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "Agent", "Task"):
            self.assertIn(tool, pre.split("|"))


class HookLivenessTest(StudioTestCase):
    """Run each configured command line (as Claude Code would) on a scenario that must produce an effect."""

    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s1")
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        shutil.copy(FIX, self.transcript)

    def run_entry(self, event, payload):
        cmds = [h["command"] for ev, _, h in entries() if ev == event]
        self.assertEqual(len(cmds), 1, event)
        cmd = cmds[0].replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN).replace("python3", sys.executable, 1)
        env = dict(os.environ, BUDGIT_HOME=self.home, CLAUDE_PLUGIN_ROOT=PLUGIN)
        r = subprocess.run(cmd, shell=True, input=json.dumps(payload), env=env, capture_output=True,
                           text=True, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.strip(), "", "a stderr-only message never reaches the model (ECC #3260)")
        out = json.loads(r.stdout) if r.stdout.strip() else None
        if out is not None:
            self.assertLessEqual(set(out), VISIBLE, out)
        return out

    def context(self, out):
        return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")

    def scenario_UserPromptSubmit(self):
        out = self.run_entry("UserPromptSubmit", {"session_id": "s1", "prompt": "añade login"})
        self.assertIn("I-1", self.context(out))

    def scenario_PostToolUse(self):
        out = self.run_entry("PostToolUse", {
            "session_id": "s1", "tool_name": "AskUserQuestion", "tool_use_id": "toolu_x",
            "tool_input": {"questions": [{"question": "¿Plataforma?"}]},
            "tool_response": {"answers": {"¿Plataforma?": "PWA"}}})
        self.assertRegex(self.context(out), r"captured as I-\d+")

    def scenario_PreToolUse(self):
        cfg = sl.load_project_config(self.project)
        cfg["protected_paths"] = ["data.json"]
        sl.atomic_write(os.path.join(sl.studio_dir(self.project), "project.json"), json.dumps(cfg))
        out = self.run_entry("PreToolUse", {
            "session_id": "s1", "tool_name": "Write", "cwd": self.project,
            "tool_input": {"file_path": os.path.join(self.project, "data.json"), "content": "{}"}})
        self.assertIn(out["hookSpecificOutput"]["permissionDecision"], ("deny", "ask"))

    def scenario_PermissionRequest(self):      # R-641: unchanged while she is here, a deny while she is away (tests/test_away_r641.py)
        import away
        payload = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.assertIsNone(self.run_entry("PermissionRequest", payload))
        self.addCleanup(away.clear)
        away.set_away("s1", home=self.home)
        out = self.run_entry("PermissionRequest", payload)
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def scenario_Stop(self):
        sl.append_inbox(self.project, "haz la app", "s1", "prompt")
        out = self.run_entry("Stop", {"session_id": "s1", "stop_hook_active": False,
                                      "transcript_path": self.transcript})
        self.assertEqual(out["decision"], "block")

    def scenario_SessionStart(self):
        out = self.run_entry("SessionStart", {"session_id": "s1", "source": "startup"})
        self.assertIn("budgit", self.context(out))

    def scenario_SessionEnd(self):
        data = sl.load_ledger(self.project)
        item = sl.add_item(data, "uno", ["legacy"])
        sl.start_item(self.project, data, item["id"], "s1")
        sl.save_ledger(self.project, data)
        self.run_entry("SessionEnd", {"session_id": "s1", "reason": "prompt_input_exit"})
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), item["id"])["status"], "open")

    def scenario_PostCompact(self):
        self.run_entry("PostCompact", {"session_id": "s1", "trigger": "auto", "compact_summary": "sum"})
        with open(os.path.join(sl.studio_dir(self.project), "compactions.log")) as f:
            self.assertIn("sum", f.read())

    def scenario_PreCompact(self):
        self.run_entry("PreCompact", {"session_id": "s1", "trigger": "auto", "transcript_path": self.transcript})
        import checkpoint
        folder = checkpoint.store_dir(sl.home_state(), self.project)
        self.assertTrue(any(n.startswith("checkpoint-") for n in os.listdir(folder)), os.listdir(folder))

    def scenario_SubagentStop(self):
        self.run_entry("SubagentStop", {"session_id": "s1", "agent_type": "budgit:researcher",
                                        "agent_id": "abc12345", "last_assistant_message": "finding X"})
        reports = sl.recent_reports(self.project)     # R-1032: the dump is in archive/agent-dumps/<date>/
        self.assertTrue(any("researcher" in r for r in reports), reports)

    def scenario_PostToolUseFailure(self):
        payload = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "make"}, "error": "Exit code 2"}
        for _ in range(2):
            self.run_entry("PostToolUseFailure", payload)
        self.assertIn("self-check", self.context(self.run_entry("PostToolUseFailure", payload)))

    def test_inventory_every_configured_event_has_a_scenario(self):
        events = {ev for ev, _g, _h in entries()}   # R-232: events that only carry the Brote channel have their own test (test_brote_director.py)
        missing = [ev for ev in load_config()["hooks"] if ev in events and not hasattr(self, "scenario_" + ev)]
        self.assertEqual(missing, [], "add a liveness scenario for each new hook")

    def test_every_configured_hook_is_alive(self):
        for ev in {e for e, _g, _h in entries()}:
            with self.subTest(event=ev):
                getattr(self, "scenario_" + ev)()
