import json, os, subprocess, sys
from helpers import StudioTestCase, ROOT, without_time_line
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


def usage_line(ctx):
    return json.dumps({"type": "assistant", "message": {"id": "m%d" % ctx, "usage": {
        "input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": ctx - 1002,
        "output_tokens": 50}}})


class ContextNudgeTest(StudioTestCase):
    """Zero-token nudge: she sees how big the context is before each message costs 5-7x more."""

    def run_hook(self, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, "user-prompt-submit"], input=json.dumps(payload),
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = without_time_line(json.loads(r.stdout)) if r.stdout.strip() else None   # R-1159: the time line is not under test here
        return out or None

    def transcript(self, *ctxs):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "hola"}}) + "\n")
            for c in ctxs:
                f.write(usage_line(c) + "\n")
        return path

    def test_small_context_is_silent(self):
        out = self.run_hook({"session_id": "u1", "prompt": "x", "transcript_path": self.transcript(50000)})
        self.assertIsNone(out)

    def test_warns_once_per_threshold_in_unbound_sessions(self):
        p = {"session_id": "u1", "prompt": "x", "transcript_path": self.transcript(20000, 350000)}
        out = self.run_hook(p)
        self.assertIn("350k", out["systemMessage"])
        self.assertIn("/compact", out["systemMessage"])
        self.assertNotIn("hookSpecificOutput", out)       # nothing enters the model's context
        self.assertIsNone(self.run_hook(p))              # same threshold: quiet
        p["transcript_path"] = self.transcript(650000)
        self.assertIn("650k", self.run_hook(p)["systemMessage"])

    def test_bound_session_gets_both_ledger_notice_and_nudge_in_one_json(self):
        sl.activate(self.project, "b1")
        out = self.run_hook({"session_id": "b1", "prompt": "nueva idea",
                             "transcript_path": self.transcript(400000)})
        self.assertIn("additionalContext", out["hookSpecificOutput"])
        self.assertIn("400k", out["systemMessage"])

    def test_bad_inputs_are_silent(self):
        for payload in ({"session_id": "../evil", "prompt": "x", "transcript_path": self.transcript(700000)},
                        {"session_id": "u2", "prompt": "x", "transcript_path": "/nonexistent.jsonl"},
                        {"session_id": "u3", "prompt": "x"}):
            self.assertIsNone(self.run_hook(payload))
