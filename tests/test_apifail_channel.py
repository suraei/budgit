"""R-242: when Claude stops answering (Claude Code runs StopFailure instead of Stop), the channel writes one `apifail` line
carrying only the error CLASS from a fixed list. No message text, no details, nothing the API said, ever."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "plugin/hooks/brote-event.sh"
HOOKS = ROOT / "plugin/hooks/hooks.json"

CLASSES = ("rate_limit", "overloaded", "authentication_failed", "oauth_org_not_allowed", "account_on_hold", "billing_error",
           "invalid_request", "model_not_found", "server_error", "max_output_tokens", "cloud_credential_error", "unknown")


def stop_failure(error, **extra):
    """The StopFailure input as the hooks docs describe it (one JSON line on stdin)."""
    body = {"session_id": "0123456789abcdef-tail", "hook_event_name": "StopFailure", "error": error,
            "error_details": "429 Too Many Requests", "last_assistant_message": "API Error: Rate limit reached"}
    body.update(extra)
    return json.dumps(body, separators=(",", ":")) + "\n"           # compact, as Claude Code writes it


class ApiFailChannelTest(unittest.TestCase):
    def events(self, d, make=True):
        p = Path(d) / "Library/Application Support/BroteWindow/events.log"
        if make:
            p.parent.mkdir(parents=True)
            p.write_text("")
        return p

    def run_hook(self, stdin, ev, kind="apifail"):
        env = dict(os.environ, HOME=str(ev.parents[3]))
        return subprocess.run(["/bin/sh", str(HOOK), kind], input=stdin, capture_output=True, text=True, env=env, timeout=10)

    def test_every_class_in_the_list_is_written_as_it_is(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            for cls in CLASSES:
                self.run_hook(stop_failure(cls), ev)
            self.assertEqual(ev.read_text().splitlines(), ["apifail 0123456789ab %s" % c for c in CLASSES])

    def test_a_class_outside_the_list_or_with_other_characters_becomes_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            for bad in ("brand_new_error", "RATE_LIMIT", "rate limit", "rate_limit;touch x", "$(id)", "", "a" * 80, "rate_limit\\u0000"):
                self.run_hook(stop_failure(bad), ev)
            self.assertEqual(set(ev.read_text().splitlines()), {"apifail 0123456789ab unknown"})

    def test_a_missing_error_field_becomes_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            self.run_hook('{"session_id":"abc","hook_event_name":"StopFailure"}\n', ev)
            self.assertEqual(ev.read_text().splitlines(), ["apifail abc unknown"])

    def test_no_message_text_or_details_ever_reach_the_log(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            self.run_hook(stop_failure("billing_error", error_details="card ending 4242 declined for org Acme",
                                       last_assistant_message="Your organization has no access: pay invoice 99"), ev)
            text = ev.read_text()
            for word in ("4242", "Acme", "invoice", "organization", "429", "API Error"):
                self.assertNotIn(word, text)
            self.assertEqual(text.splitlines(), ["apifail 0123456789ab billing_error"])

    def test_text_that_imitates_the_error_field_cannot_forge_a_class(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            forged = 'Claude said "error":"rate_limit" in the middle of a sentence'
            self.run_hook(stop_failure("server_error", last_assistant_message=forged), ev)
            self.assertEqual(ev.read_text().splitlines(), ["apifail 0123456789ab server_error"])

    def test_junk_or_missing_input_exits_zero_and_writes_nothing_unsafe(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            for junk in ("", "not json at all\n", '{"session_id":"a;rm -rf /","error":"rate_limit"}\n', "\x00\x01\x02\n", '{"error":"rate_limit"}\n'):
                r = self.run_hook(junk, ev)
                self.assertEqual(r.returncode, 0)
                self.assertEqual(r.stdout + r.stderr, "")
            for line in ev.read_text().splitlines():
                self.assertRegex(line, r"^apifail [A-Za-z0-9_-]{1,12} [a-z_]{1,32}$")
            self.assertEqual(ev.read_text(), "")

    def test_without_the_events_file_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d, make=False)
            r = self.run_hook(stop_failure("rate_limit"), ev)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(ev.exists())

    def test_a_file_over_256_kb_stops_growing(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            ev.write_text("pre abc Bash\n" * 25000)
            before = ev.stat().st_size
            self.run_hook(stop_failure("rate_limit"), ev)
            self.assertEqual(ev.stat().st_size, before)

    def test_other_kinds_are_unchanged_by_the_new_one(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.events(d)
            self.run_hook('{"session_id":"abc","tool_name":"Bash","error":"rate_limit"}\n', ev, kind="pre")
            self.run_hook('{"session_id":"abc","error":"rate_limit"}\n', ev, kind="stop")
            self.assertEqual(ev.read_text().splitlines(), ["pre abc Bash", "stop abc"])      # no class on any other kind


class ApiFailRegistrationTest(unittest.TestCase):
    def entries(self):
        return [h["command"] for g in json.loads(HOOKS.read_text())["hooks"].get("StopFailure", []) for h in g["hooks"]]

    def test_stopfailure_runs_the_channel_with_the_same_guard_as_the_others(self):
        cmds = [c for c in self.entries() if "brote-event.sh" in c]
        self.assertEqual(len(cmds), 1)
        self.assertTrue(cmds[0].endswith(" apifail"), cmds[0])
        cfg = json.loads(HOOKS.read_text())["hooks"]
        stop = [h["command"] for g in cfg["Stop"] for h in g["hooks"] if "brote-event.sh" in h["command"]][0]
        self.assertEqual(cmds[0].rsplit(" ", 1)[0], stop.rsplit(" ", 1)[0])                    # identical guard prefix, only the kind differs

    def test_the_entry_is_short_and_writes_nothing_but_the_channel(self):
        cfg = json.loads(HOOKS.read_text())["hooks"]["StopFailure"]
        self.assertEqual(sum(len(g["hooks"]) for g in cfg), 1)
        for g in cfg:
            self.assertLessEqual(set(g), {"matcher", "hooks"})
            for h in g["hooks"]:
                self.assertLessEqual(h.get("timeout", 60), 5)
                self.assertLessEqual(set(h), {"type", "command", "timeout"})

    def test_cloud_projects_never_get_the_channel(self):
        sys.path.insert(0, str(ROOT / "plugin/scripts"))
        import vendor
        groups = vendor.hook_groups()
        self.assertNotIn("StopFailure", groups)

    def test_plugin_validates(self):
        import shutil
        claude = shutil.which("claude")
        if not claude:
            self.skipTest("claude CLI not on PATH")
        r = subprocess.run([claude, "plugin", "validate", str(ROOT / "plugin")], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])
        self.assertNotRegex(r.stdout + r.stderr, r"(?i)unknown (hook )?event")


if __name__ == "__main__":
    unittest.main()
