"""R-292 plan B, step 3: the SessionStart / SessionEnd hooks that keep Brote's floating window "with Claude".

A hook's stdout enters the chat, so these two print nothing, always exit 0 and are fast. While the float is off they touch nothing.
"""
import json
import os
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
HOOKS = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]


def commands(event):
    return [h["command"] for g in HOOKS.get(event, []) for h in g["hooks"]]


class Wiring(unittest.TestCase):
    def test_session_start_runs_ensure_and_session_end_runs_session_end(self):
        ens = [c for c in commands("SessionStart") if "floatctl.py" in c]
        end = [c for c in commands("SessionEnd") if "floatctl.py" in c]
        self.assertEqual(len(ens), 1)
        self.assertEqual(len(end), 1)
        self.assertTrue(ens[0].rstrip().endswith("ensure"), ens[0])
        self.assertTrue(end[0].rstrip().endswith("session-end"), end[0])
        for c in ens + end:
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}/scripts/floatctl.py"', c, "quoted, from the plugin root")
            self.assertNotIn("open ", c)

    def test_the_existing_hooks_are_still_there(self):
        self.assertTrue(any("hook.py" in c and "session-start" in c for c in commands("SessionStart")))
        self.assertTrue(any("hook.py" in c and "session-end" in c for c in commands("SessionEnd")))

    def test_timeouts_are_short(self):
        for ev in ("SessionStart", "SessionEnd"):
            for g in HOOKS[ev]:
                for h in g["hooks"]:
                    if "floatctl.py" in h["command"]:
                        self.assertLessEqual(h["timeout"], 10)


class Silent(unittest.TestCase):
    def run_hook(self, event):
        tmp = Path(tempfile.mkdtemp(prefix="float-hook-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        (tmp / "home").mkdir()
        (tmp / "cfg").mkdir()
        cmd = [c for c in commands(event) if "floatctl.py" in c][0]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp / "home"), "CLAUDE_CONFIG_DIR": str(tmp / "cfg"), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
        # CPU seconds of the hook, not wall time: the hook runs as a child process, so this is the RUSAGE_CHILDREN delta (a loaded full run stretches wall time, not CPU)
        c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        r = subprocess.run(["/bin/sh", "-c", cmd], input=json.dumps({"session_id": "abcdef0123456789"}), capture_output=True, text=True, env=env, timeout=20)
        c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        return r, (c1.ru_utime - c0.ru_utime) + (c1.ru_stime - c0.ru_stime), tmp

    def test_both_hooks_print_nothing_exit_zero_and_leave_no_trace_while_float_is_off(self):
        for event in ("SessionStart", "SessionEnd"):
            r, secs, tmp = self.run_hook(event)
            self.assertEqual((r.returncode, r.stdout), (0, ""), event + ": " + r.stderr)
            self.assertLess(secs, 1.0, event)
            self.assertEqual(list((tmp / "home").iterdir()), [], event + " must not create anything")


if __name__ == "__main__":
    unittest.main()
