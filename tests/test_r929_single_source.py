import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
import agentcap

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
WARNING = ("R-929: the brief takes its rules from one source; name the user's earlier rules on this subject "
           "(I-n words, approved designs, code tables) too.")


def call(prompt):
    return agentcap.single_source_warning({"prompt": prompt})


class SingleSourceTest(StudioTestCase):
    def test_new_report_only_warns(self):
        self.assertEqual(call("Build X. Use the rules from the new report."), WARNING)

    def test_earlier_rules_pass(self):
        self.assertIsNone(call("Build X. Rules: her earlier I-1312 words plus the new report."))
        self.assertIsNone(call("Build X. Use the rules from the new report and the approved design."))

    def test_spanish_warns_then_passes(self):
        self.assertEqual(call("Reglas del informe nuevo."), WARNING)
        self.assertEqual(call("Haz X segun el nuevo informe."), WARNING)
        self.assertIsNone(call("Reglas del informe nuevo y las reglas anteriores (I-900)."))

    def test_no_phrase_passes(self):
        self.assertIsNone(call("Build X. Keep it small and test it."))
        self.assertIsNone(agentcap.single_source_warning("not a dict"))

    def test_card_text_is_checked(self):
        card = {"questions": [{"question": "Use criteria from the new report?", "options": [{"label": "Yes"}]}]}
        self.assertEqual(agentcap.single_source_warning(card), WARNING)

    def test_200k_is_linear(self):
        for text in ("rules from " * 20000, "reglas del " * 20000, "rules " * 40000 + "from", "según " * 40000):
            start = time.process_time()                                  # CPU time, not wall (R-1282)
            call(text[:200000])
            self.assertLess(time.process_time() - start, 1.0)

    def test_hook_warns_not_denies(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": "zzz",
                   "tool_input": {"subagent_type": "general-purpose", "prompt": "Build X. Use the rules from the new report."}}
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload),
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertIn(WARNING, out["additionalContext"])
        self.assertNotEqual(out.get("permissionDecision"), "deny")
