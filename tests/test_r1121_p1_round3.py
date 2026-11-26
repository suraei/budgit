"""R-1121 P1 round 3: the children Budgit starts under `python3 -I -S` must still import (a failed import in a detached child is
swallowed by its own `except Exception`, so it would be silent), the runner pattern knows the flags, and every spawned
script gets past its imports. Fake homes and temp folders only."""
import json
import os
import re
import shutil
import subprocess
import unittest

from helpers import ROOT, StudioTestCase

PLUGIN = os.path.join(ROOT, "plugin")
SCRIPTS = os.path.join(PLUGIN, "scripts")
IMPORT_ERRORS = ("ModuleNotFoundError", "ImportError")


class RoundThreeTest(StudioTestCase):
    def env(self, **extra):
        base = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        fake = os.path.join(self.tmp, "fakehome")
        os.makedirs(fake, exist_ok=True)
        base.update(HOME=fake, BUDGIT_HOME=self.home, BUDGIT_FLOAT_AUTO="off")
        base.update(extra)
        return base

    def run_py(self, args, env=None, stdin=None, cwd=None):
        return subprocess.run(["python3", "-I", "-S"] + list(args), env=env or self.env(), input=stdin, capture_output=True,
                              text=True, timeout=60, cwd=cwd or self.tmp)

    def public_manifest(self):
        path = os.path.join(self.tmp, "public-plugin.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": "99.0.0"}, f)
        return "file://" + path

    def cache(self):
        return os.path.join(self.home, "update-check.json")

    def test_refresh_child_imports_studiolib_under_dash_I_dash_S(self):
        """The REAL child, no mock: it must write the cache (it can only do that after `import studiolib`)."""
        self.assertFalse(os.path.exists(self.cache()))
        r = self.run_py([os.path.join(SCRIPTS, "updatelib.py"), "--refresh-cache"], env=self.env(BUDGIT_UPDATE_URL=self.public_manifest()))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(self.cache()), "the child wrote no cache: its import failed silently " + r.stderr)
        self.assertEqual(json.load(open(self.cache()))["version"], "99.0.0")

    def test_refresh_child_without_the_sys_path_line_is_silently_dead_red(self):
        """Red first: the same child with the sys.path line cut out writes nothing (the error is swallowed)."""
        copy = os.path.join(self.tmp, "scripts-copy")
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("__pycache__"))
        path = os.path.join(copy, "updatelib.py")
        text = open(path, encoding="utf-8").read()
        cut = "\n".join(ln for ln in text.splitlines() if "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))" not in ln)
        self.assertNotEqual(cut, text)
        open(path, "w", encoding="utf-8").write(cut + "\n")
        r = self.run_py([path, "--refresh-cache"], env=self.env(BUDGIT_UPDATE_URL=self.public_manifest()))
        self.assertFalse(os.path.exists(self.cache()), "expected the old, broken child to write nothing")

    def test_budgit_test_entry_gets_past_its_imports(self):
        """`budgit test` runs tools/budgit_test.py in the plain form (M2); it must load: only its imports run, never its main."""
        entry = os.path.join(ROOT, "tools", "budgit_test.py")
        code = ("import importlib.util, sys\n"
                "s = importlib.util.spec_from_file_location('budgit_test_entry', sys.argv[1])\n"
                "m = importlib.util.module_from_spec(s)\n"
                "s.loader.exec_module(m)\n"
                "print('loaded', m.EXIT_DATALESS)\n")
        r = subprocess.run(["python3", "-c", code, entry], env=self.env(), capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("loaded", r.stdout)

    def test_runner_pattern_accepts_interpreter_flags(self):
        import suitejobs
        old = re.compile(r"(?i)^\S*python[\d.]*\s+\S*suitejobs\.py run \S+ -- ")        # the pre-round-3 pattern (red)
        flagged = "python3 -I -S /x/suitejobs.py run /r/job.json -- python3 t.py"
        plain = "python3 /x/suitejobs.py run /r/job.json -- python3 t.py"
        self.assertIsNone(old.match(flagged))
        self.assertTrue(suitejobs.RUNNER_SHAPE.match(flagged))
        self.assertTrue(suitejobs.RUNNER_SHAPE.match(plain))
        self.assertTrue(suitejobs.RUNNER_SHAPE.match("/opt/homebrew/bin/python3.15 -I -S -B /x/suitejobs.py run /r/j.json -- x"))
        self.assertIsNone(suitejobs.RUNNER_SHAPE.match("python3 -I -S /x/other.py run /r/job.json -- x"))
        rows = [{"owner": "s", "pid": 7, "start": 10, "command": flagged}]
        self.assertEqual(len(suitejobs.runner_rows(rows, "s", None)), 1)

    def test_every_spawned_script_starts_under_dash_I_dash_S_without_an_import_error(self):
        event = json.dumps({"session_id": "r1121", "cwd": self.tmp, "hook_event_name": "Stop"})
        runs = [
            ([os.path.join(SCRIPTS, "updatelib.py")], None),                         # imports only (no --refresh-cache)
            ([os.path.join(SCRIPTS, "suitejobs.py")], None),                         # prints its usage line
            (["-B", os.path.join(SCRIPTS, "floatctl.py"), "status"], None),
            ([os.path.join(SCRIPTS, "hook.py"), "stop"], event),
            ([os.path.join(PLUGIN, "bin", "budgit"), "--help"], None),
        ]
        for args, stdin in runs:
            r = self.run_py(args, stdin=stdin)
            for word in IMPORT_ERRORS:
                self.assertNotIn(word, r.stderr, "%s\n%s" % (args, r.stderr))
            self.assertNotIn("Traceback", r.stderr, "%s\n%s" % (args, r.stderr))

    def test_vendored_run_target_starts_under_its_real_command_without_an_import_error(self):
        import vendor
        proj = os.path.join(self.tmp, "proj")
        runtime = os.path.join(proj, ".claude", "budgit")
        shutil.copytree(SCRIPTS, os.path.join(runtime, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
        event = json.dumps({"session_id": "r1121", "cwd": proj, "hook_event_name": "Stop"})
        r = subprocess.run(["/bin/sh", "-c", vendor.RUN + "stop"], input=event, env=self.env(CLAUDE_PROJECT_DIR=proj),
                           capture_output=True, text=True, timeout=60, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in IMPORT_ERRORS:
            self.assertNotIn(word, r.stderr)
        self.assertNotIn("error", open(os.path.join(self.home, "hooks.log")).read().lower() if os.path.exists(os.path.join(self.home, "hooks.log")) else "")


if __name__ == "__main__":
    unittest.main()
