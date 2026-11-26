"""R-232: Brote's director (state machine) and the event channel hook. The node tests drive the real sprites.js, personality.js and director.js."""
import os, subprocess, tempfile, unittest, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOAT = ROOT / "tools/brote/float"
HOOK = ROOT / "plugin/hooks/brote-event.sh"
HOOKS = ROOT / "plugin/hooks/hooks.json"


class DirectorNodeTest(unittest.TestCase):
    def test_node_suite(self):
        r = subprocess.run(["node", "--test", str(FLOAT / "director.test.mjs")], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-1000:])
        self.assertIn("fail 0", r.stdout)

    def test_personality_is_a_valid_copy(self):
        r = subprocess.run(["node", str(FLOAT / "personality-export.mjs"), "--check"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((FLOAT / "personality.js").read_text().startswith("/* Generated from"))


class EventHookTest(unittest.TestCase):
    def home(self, d, make=True):
        p = Path(d) / "Library/Application Support/BroteWindow/events.log"
        if make:
            p.parent.mkdir(parents=True)
        return p

    def test_the_shipped_hook_reads_no_path_from_the_environment(self):
        self.assertNotIn("BROTE_EVENTS", HOOK.read_text())
        self.assertNotIn("BROTE_EVENTS", HOOKS.read_text())

    def test_a_file_over_256_kb_stops_growing(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.home(d); ev.write_text("pre abc Bash\n" * 25000)   # about 325 KB
            before = ev.stat().st_size
            self.run_hook("stop", '{"session_id":"abc"}\n', ev)
            self.run_hook("prompt", '{"session_id":"abc"}\n', ev)
            self.assertEqual(ev.stat().st_size, before)
            ev.write_text("pre abc Bash\n")                              # small again: appends
            self.run_hook("stop", '{"session_id":"abc"}\n', ev)
            self.assertEqual(ev.read_text().splitlines()[-1], "stop abc")

    # the hook reads one fixed path under $HOME and nothing from the environment: the tests give it a home of its own
    def run_hook(self, kind, stdin, events):
        env = dict(os.environ, HOME=str(events.parents[3]))
        return subprocess.run(["/bin/sh", str(HOOK), kind], input=stdin, capture_output=True, text=True, env=env, timeout=10)

    def test_no_events_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.home(d, make=False)
            r = self.run_hook("pre", '{"session_id":"abc","tool_name":"Bash"}\n', ev)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(ev.exists())

    def test_appends_only_kind_session_tool(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.home(d); ev.write_text("")
            self.run_hook("pre", '{"session_id":"abc-123","tool_name":"Bash","tool_input":{"command":"secret rm -rf","tool_name":"Evil"}}\n', ev)
            self.run_hook("stop", '{"session_id":"abc-123","last_assistant_message":"private text"}\n', ev)
            self.run_hook("bogus", '{"session_id":"x"}\n', ev)
            self.assertEqual(ev.read_text().splitlines(), ["pre abc-123 Bash", "stop abc-123"])

    def test_junk_input_exits_zero_and_writes_nothing_unsafe(self):
        with tempfile.TemporaryDirectory() as d:
            ev = self.home(d); ev.write_text("")
            r = self.run_hook("post", '{"session_id":"a;rm -rf /","tool_name":"B$(x)"}\n', ev)
            self.assertEqual(r.returncode, 0)
            for line in ev.read_text().splitlines():
                self.assertRegex(line, r"^(prompt|pre|post|fail|stop|perm) [A-Za-z0-9_-]{0,12}( [A-Za-z0-9_]{1,48})?$")

    def test_hooks_json_registers_the_channel_with_a_guard(self):
        cfg = json.loads(HOOKS.read_text())["hooks"]
        for ev in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "Notification"):
            cmds = [h["command"] for g in cfg[ev] for h in g["hooks"] if "brote-event.sh" in h.get("command", "")]
            self.assertEqual(len(cmds), 1, ev)
            self.assertIn("BroteWindow/events.log", cmds[0])

    def test_vendor_leaves_the_brote_channel_out_of_cloud_projects(self):
        import sys
        sys.path.insert(0, str(ROOT / "plugin/scripts"))
        import vendor
        groups = vendor.hook_groups()
        self.assertNotIn("Notification", groups)   # only the Brote channel uses it
        for gs in groups.values():
            for g in gs:
                for h in g["hooks"]:
                    self.assertNotIn("brote-event", h["command"])

    def test_cost_when_the_app_is_not_running(self):
        import re, statistics, time
        cmd = [h["command"] for g in json.loads(HOOKS.read_text())["hooks"]["PreToolUse"] for h in g["hooks"] if "brote-event" in h["command"]][0]
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT / "plugin"), HOME=tempfile.mkdtemp())
        import sys
        sys.path.insert(0, str(ROOT / "tests"))
        from helpers import fastest_child

        def run():
            r = subprocess.run(["/bin/sh", "-c", cmd], input='{"session_id":"a","tool_name":"Bash"}', capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0)
        best = fastest_child(run, 0.06, runs=20)
        print("brote hook, app not running: quickest of up to 20 runs used %.1f ms of CPU" % (best * 1000))
        self.assertLess(best, 0.06)


if __name__ == "__main__":
    unittest.main()
