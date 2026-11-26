"""R-1137 item 1 real-door measure. Skipped unless BUDGIT_MEASURE=1. Copies the REAL studio project state (git archive of main
plus the untracked .studio and .budgit minus verify) to a temp dir, runs the SessionStart hook of two plugins as a subprocess
per role, prints bytes and lines. Writes nothing in the real project."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HOME_DIR = os.path.expanduser("~")
MAIN = os.path.join(HOME_DIR, "Documents", "Dev", "studio")
PLUGINS = {"a_main": MAIN + "/plugin", "b_item1": os.path.join(HOME_DIR, "studio-wt", "r1137-item1", "plugin")}


@unittest.skipUnless(os.environ.get("BUDGIT_MEASURE") == "1", "measurement only: set BUDGIT_MEASURE=1")
class RealDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(dir="/private/tmp", prefix="r1137-realdoor-"))
        cls.project = os.path.join(cls.tmp, "proj")
        os.makedirs(cls.project)
        tar = subprocess.run(["git", "-C", MAIN, "archive", "b1a837d6"], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", cls.project], input=tar, check=True)
        for d in (".studio", ".budgit"):
            src = os.path.join(MAIN, d)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(cls.project, d), symlinks=True, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("verify"))
        cls.home = os.path.join(cls.tmp, "home")
        cls.bh = os.path.join(cls.home, ".budgit")
        os.makedirs(os.path.join(cls.bh, "sessions"))
        cls.hub = os.path.join(cls.tmp, "hub")
        os.makedirs(cls.hub)
        cls.env = dict(os.environ, HOME=cls.home, BUDGIT_HOME=cls.bh,
                       CLAUDE_CONFIG_DIR=os.path.join(cls.tmp, "claude-config"), BUDGIT_LANG="en")
        cls.env.pop("STUDIO_HOME", None)
        with open(os.path.join(cls.bh, "config.json"), "w") as f:
            json.dump({"hub": cls.hub}, f)
        with open(os.path.join(cls.bh, "portfolio.json"), "w") as f:
            json.dump({"tm": "tm-sess", "projects": [
                {"path": cls.project, "name": "proj", "pm": "pm-sess", "channel": "current", "reason": None}]}, f)
        for sid, parent in (("tm-sess", None), ("pm-sess", None), ("lane-sess", "pm-sess")):
            rec = {"project": cls.project, "ts": "2026-11-16T00:00:00", "last_seen": "2026-11-16T00:00:00"}
            if parent:
                rec["parent"] = parent
            with open(os.path.join(cls.bh, "sessions", sid + ".json"), "w") as f:
                json.dump(rec, f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def start(self, plugin, payload):
        hook = os.path.join(plugin, "scripts", "hook.py")
        r = subprocess.run([sys.executable, hook, "session-start"], input=json.dumps(payload), capture_output=True,
                           text=True, env=self.env, cwd=self.project)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""

    def test_measure(self):
        roles = (("TM", "tm-sess", self.hub, {}, 60), ("PM", "pm-sess", self.project, {}, 40),
                 ("lane", "lane-sess", self.project, {}, 25),
                 ("subagent", "lane-sess", self.project, {"agent_id": "ag1", "agent_type": "general-purpose"}, 15))
        for role, sid, cwd, extra, lines in roles:
            payload = dict({"session_id": sid, "cwd": cwd, "transcript_path": os.path.join(self.tmp, "t.jsonl"),
                            "source": os.environ.get("BUDGIT_SOURCE", "startup"), "hook_event_name": "SessionStart"}, **extra)
            for name, plugin in PLUGINS.items():
                ctx = self.start(plugin, payload)
                n, ln = len(ctx.encode()), len(ctx.splitlines())
                print("REALDOOR role=%s %s bytes=%d lines=%d" % (role, name, n, ln))
                if name == "a_main":
                    chunks = re.split(r"\n(?=budgit[ :(])", ctx)
                    top = sorted(chunks, key=lambda c: -len(c.encode()))[:5]
                    for c in top:
                        print("  SECTION role=%s bytes=%d lines=%d head=%r" % (role, len(c.encode()), len(c.splitlines()), c[:70]))
                    if role == "TM":
                        groups, cur = [], None
                        for l in ctx.splitlines():
                            if not l.startswith(" "):
                                cur = [l, 0, 0]
                                groups.append(cur)
                            cur[1] += len(l.encode()) + 1
                            cur[2] += 1
                        for g in sorted(groups, key=lambda g: -g[1])[:6]:
                            print("  GROUP bytes=%d lines=%d head=%r" % (g[1], g[2], g[0][:70]))
                else:
                    print("  PASS=%s" % (n < 4000 and ln <= lines))


if __name__ == "__main__":
    unittest.main()
