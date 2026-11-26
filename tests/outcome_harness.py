"""R-1128 / R-1136: one outcome harness through the real doors. The tree is exported into a temp folder with the real export
function; the hook runs as a subprocess with a real PreToolUse JSON on stdin; the budgit CLI runs as a subprocess. HOME and
BUDGIT_HOME are temp folders, so nothing here reads the real home or a real ledger."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugin", "scripts"))
import export  # noqa: E402

_EXPORT = {}        # one export per process: it is the slow part


def exported_tree():
    if "dir" not in _EXPORT:
        base = tempfile.mkdtemp(prefix="outcome-export-")
        cfg = {"hub": os.path.join(base, "hub"), "projects_root": base, "share_author": "someone", "private_terms": []}
        os.makedirs(os.path.join(cfg["hub"], "projects"))
        out = os.path.join(base, "out")
        export.export(out, repo="someone/budgit", check_release=False, config=cfg)
        _EXPORT["dir"] = out
        _EXPORT["base"] = base
    return _EXPORT["dir"]


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


class OutcomeCase(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def setUp(self):
        self.export = exported_tree()
        self.tmp = tempfile.mkdtemp(prefix="outcome-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.bhome = os.path.join(self.tmp, "budgit-home")
        self.project = os.path.join(self.tmp, "proj")
        for d in (self.home, self.bhome, os.path.join(self.project, ".budgit")):
            os.makedirs(d)
        git(self.project, "init", "-q")
        with open(os.path.join(self.project, "README.md"), "w") as f:
            f.write("hello\n")
        os.makedirs(os.path.join(self.project, "assets"))
        with open(os.path.join(self.project, "assets", "style.css"), "w") as f:
            f.write("a { color: red; }\n")
        git(self.project, "add", ".")
        git(self.project, "commit", "-q", "-m", "first")

    def activate(self):
        """Make the temp project a Budgit project through the real `budgit activate`."""
        r = subprocess.run([sys.executable, os.path.join(self.export, "plugin", "bin", "budgit"), "activate", "--no-bind", self.project],
                           capture_output=True, text=True, env=self.env(), timeout=120)
        if r.returncode != 0:
            raise AssertionError(r.stdout + r.stderr)

    def env(self, extra=None, path=None):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "BUDGIT", "STUDIO"))}
        e.update(HOME=self.home, BUDGIT_HOME=self.bhome, BUDGIT_LANG="en", BUDGIT_ADMIT_ADVICE="off",
                 CLAUDE_CONFIG_DIR=os.path.join(self.tmp, "claude-config"))
        if path is not None:
            e["PATH"] = path
        e.update(extra or {})
        return e

    def run_hook(self, event, payload, extra_env=None):
        """The real hook script, a real hook JSON on stdin; returns the parsed JSON it printed ({} when it printed none)."""
        data = {"session_id": "outcome-session", "cwd": self.project, "hook_event_name": {
            "pre-tool-use": "PreToolUse", "post-tool-use": "PostToolUse"}.get(event, event)}
        data.update(payload)
        r = subprocess.run([sys.executable, os.path.join(self.export, "plugin", "scripts", "hook.py"), event],
                           input=json.dumps(data), capture_output=True, text=True, env=self.env(extra_env), timeout=120)
        self.last_hook = r
        out = r.stdout.strip()
        return json.loads(out.splitlines()[-1]) if out else {}

    def decision(self, out):
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision", "none")

    def run_cli(self, args, extra_env=None, path=None):
        """The exported bin/budgit, run with --project <temp project>; returns the CompletedProcess."""
        return subprocess.run([sys.executable, os.path.join(self.export, "plugin", "bin", "budgit")] + list(args)
                              + ["--project", self.project], capture_output=True, text=True,
                              env=self.env(extra_env, path), timeout=180)
