"""R-1178 (her I-1783): a coordinator answers first and delegates. Warn at N tool calls once per prompt; refuse browser clicks
from a TM or PM main session; the role text names the delegate path."""
import json
import os
import subprocess
import sys
from unittest import mock

from helpers import StudioTestCase, ROOT
import replyfirst
import roles

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
SID = "00000042-2222-3333-4444-555555555555"
TM = {"role": "TM", "name": "hub", "path": None}


def call(tool="Bash", **extra):
    inp = {"session_id": SID, "tool_name": tool, "tool_input": {}}
    inp.update(extra)
    return inp


class WarnTest(StudioTestCase):
    def role(self, info):
        patcher = mock.patch.object(roles, "detect", return_value=info)
        patcher.start()
        self.addCleanup(patcher.stop)
        registered = (SID, []) if info and info["role"] == "TM" else (None, [{"path": "/x", "pm": SID}]) if info and info["role"] == "PM" else (None, [])
        entries = mock.patch.object(roles, "_entries", return_value=registered)
        entries.start()
        self.addCleanup(entries.stop)

    def test_warns_at_n_and_not_before(self):  # SHALL warn at N calls and not before
        self.role(TM)
        outs = [replyfirst.on_call(call(), None, "en") for _ in range(6)]
        self.assertEqual(outs[:5], [None] * 5)
        self.assertIn("answer now in one line", outs[5])
        self.assertIn("6 tool calls", outs[5])

    def test_once_per_prompt_and_reset_on_new_prompt(self):  # SHALL once per prompt, reset on a new prompt
        self.role(TM)
        for _ in range(6):
            replyfirst.on_call(call(), None, "en")
        self.assertEqual([replyfirst.on_call(call(), None, "en") for _ in range(10)], [None] * 10)
        replyfirst.on_prompt(SID)
        outs = [replyfirst.on_call(call(), None, "en") for _ in range(6)]
        self.assertEqual(outs[:5], [None] * 5)
        self.assertIsNotNone(outs[5])

    def test_the_limit_is_a_config_key(self):  # SHALL config key
        self.role(TM)
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"reply_first_calls": 2}, f)
        self.assertIsNone(replyfirst.on_call(call(), None, "en"))
        self.assertIsNotNone(replyfirst.on_call(call(), None, "en"))

    def test_spanish_text_exists(self):
        self.role(TM)
        out = [replyfirst.on_call(call(), None, "es") for _ in range(6)][-1]
        self.assertIn("respóndele", out)

    def test_subagents_and_lane_and_unknown_roles_are_not_counted(self):  # SHALL no warning outside a coordinator
        self.role(TM)
        for _ in range(10):
            self.assertIsNone(replyfirst.on_call(call(agent_id="a1"), None, "en"))
        for info in ({"role": "lane", "name": "p", "path": "/x"}, None):
            self.role(info)
            for _ in range(10):
                self.assertIsNone(replyfirst.on_call(call(), None, "en"))

    def test_a_pm_is_a_coordinator(self):
        self.role({"role": "PM", "name": "p", "path": "/x"})
        outs = [replyfirst.on_call(call(), None, "en") for _ in range(6)]
        self.assertIsNotNone(outs[5])


class BrowserTest(StudioTestCase):
    def role(self, info):
        patcher = mock.patch.object(roles, "detect", return_value=info)
        patcher.start()
        self.addCleanup(patcher.stop)
        registered = (SID, []) if info and info["role"] == "TM" else (None, [{"path": "/x", "pm": SID}]) if info and info["role"] == "PM" else (None, [])
        entries = mock.patch.object(roles, "_entries", return_value=registered)
        entries.start()
        self.addCleanup(entries.stop)

    def test_a_coordinator_is_refused_every_click_type_and_form_input(self):  # SHALL browser refusal for a coordinator
        self.role(TM)
        for prefix in replyfirst.BROWSER_PREFIXES:
            for action in replyfirst.BROWSER_ACTIONS:
                out = replyfirst.browser_refusal(call(prefix + "computer", tool_input={"action": action}), None, "en")
                self.assertIn("background agent", out, (prefix, action))
            self.assertIn("background agent", replyfirst.browser_refusal(call(prefix + "form_input"), None, "en"))
        self.assertIn("agente en segundo plano",
                      replyfirst.browser_refusal(call("mcp__Claude_Browser__computer", tool_input={"action": "type"}), None, "es"))

    def test_a_coordinator_is_refused_javascript_tool_a_subagent_and_a_lane_are_not(self):  # SHALL javascript_tool refused
        self.role(TM)
        for prefix in replyfirst.BROWSER_PREFIXES:
            inp = call(prefix + "javascript_tool", tool_input={"action": "javascript_exec", "text": "document.body.click()"})
            self.assertIn("background agent", replyfirst.browser_refusal(inp, None, "en"), prefix)
            self.assertIn("agente en segundo plano", replyfirst.browser_refusal(inp, None, "es"), prefix)
            inp["agent_id"] = "a1"
            self.assertIsNone(replyfirst.browser_refusal(inp, None, "en"))
        for info in ({"role": "lane", "name": "p", "path": "/x"}, None):
            self.role(info)
            self.assertIsNone(replyfirst.browser_refusal(call("mcp__Claude_Browser__javascript_tool"), None))

    def test_a_main_session_in_an_unregistered_worktree_is_neither_warned_nor_refused(self):  # SHALL only a registered TM or PM
        pm = {"role": "PM", "name": "p", "path": "/x"}      # what roles.classify says for a top-level session of a project with no PM
        patcher = mock.patch.object(roles, "detect", return_value=pm)
        patcher.start()
        self.addCleanup(patcher.stop)
        other = mock.patch.object(roles, "_entries", return_value=("some-other-tm", [{"path": "/x", "pm": "some-other-pm"}]))
        other.start()
        self.addCleanup(other.stop)
        click = call("mcp__Claude_Browser__computer", tool_input={"action": "left_click"})
        self.assertIsNone(replyfirst.browser_refusal(click, None, "en"))
        self.assertIsNone(replyfirst.browser_refusal(call("mcp__claude-in-chrome__javascript_tool"), None, "en"))
        self.assertEqual([replyfirst.on_call(call(), None, "en") for _ in range(10)], [None] * 10)

    def test_reads_and_screenshots_stay_allowed(self):
        self.role(TM)
        for action in ("screenshot", "scroll", "zoom", "wait", "mouse_move"):
            self.assertIsNone(replyfirst.browser_refusal(call("mcp__Claude_Browser__computer", tool_input={"action": action}), None))
        self.assertIsNone(replyfirst.browser_refusal(call("mcp__claude-in-chrome__read_page"), None))
        self.assertIsNone(replyfirst.browser_refusal(call("Bash"), None))

    def test_no_refusal_for_a_subagent_or_a_lane_role(self):  # SHALL subagents and lane roles unchanged
        click = {"action": "left_click"}
        self.role(TM)
        self.assertIsNone(replyfirst.browser_refusal(call("mcp__Claude_Browser__computer", tool_input=click, agent_id="a1"), None))
        self.assertIsNone(replyfirst.browser_refusal(call("mcp__Claude_Browser__computer", tool_input=click, agent_type="x"), None))
        for info in ({"role": "lane", "name": "p", "path": "/x"}, None):
            self.role(info)
            self.assertIsNone(replyfirst.browser_refusal(call("mcp__Claude_Browser__computer", tool_input=click), None))


class HookTest(StudioTestCase):
    """The same through the real hook: the TM is the session the portfolio names."""

    def setUp(self):
        super().setUp()
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "portfolio.json"), "w", encoding="utf-8") as f:
            json.dump({"tm": SID, "projects": []}, f)

    def run_hook(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_hook_denies_a_tm_click_and_lets_a_subagent_through(self):
        payload = call("mcp__Claude_Browser__computer", tool_input={"action": "left_click"}, cwd=self.tmp)
        out = self.run_hook("pre-tool-use", payload)
        self.assertIn('"permissionDecision": "deny"', out)
        payload["agent_id"] = "a1"
        self.assertNotIn("deny", self.run_hook("pre-tool-use", payload))

    def test_the_hook_warns_once_and_a_new_prompt_resets(self):
        outs = [self.run_hook("pre-tool-use", call("Read", cwd=self.tmp)) for _ in range(8)]
        warned = [i for i, o in enumerate(outs) if "tool calls since" in o or "llamadas a herramientas" in o]
        self.assertEqual(warned, [5])
        self.run_hook("user-prompt-submit", {"session_id": SID, "prompt": "hola", "cwd": self.tmp})
        outs = [self.run_hook("pre-tool-use", call("Read", cwd=self.tmp)) for _ in range(6)]
        self.assertEqual([i for i, o in enumerate(outs) if "llamadas a herramientas" in o], [5])


class RoleTextTest(StudioTestCase):
    def test_the_tm_and_pm_blocks_name_the_delegate_path(self):  # SHALL role line present
        for role in ("TM", "PM"):
            out = roles.block({"role": role, "name": "p", "path": "/x"})
            self.assertIn("background agent", out, role)
            self.assertIn("one line first", out, role)
        self.assertNotIn("one line first", roles.block({"role": "lane", "name": "p", "path": "/x"}))
