"""R-1054: an outside review tool starts in an empty private folder with a minimal environment.

Only fake `gemini` and `codex` scripts, placed first on PATH in a tmp dir, are ever run. A fake gets the path of its record file
baked into its own script, so the real environment allowlist is never widened for a test."""
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import extreview as ev

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "plugin", "scripts")
RECORD = ('pwd -P > "@REC@"\n'
          'ls -A . | wc -l >> "@REC@"\n'
          'ls -ld . | cut -c1-10 >> "@REC@"\n')
ENVREC = 'env >> "@REC@"\n'
LEAKY = {"GIT_DIR": "/leak/git", "GIT_WORK_TREE": "/leak/tree", "GEMINI_CLI_INCLUDE_DIRECTORIES": "/leak/inc",
         "FOO_PATH": "/leak/foo", "OLDPWD": "/leak/old"}
KEPT = {"GEMINI_API_KEY": "key-123", "HTTPS_PROXY": "http://proxy.test:3128"}


class ExtReviewCwdTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        self.rec = os.path.join(self.d, "record.txt")
        self.caller = os.path.join(self.d, "caller")
        os.makedirs(self.caller)
        with open(os.path.join(self.caller, "secret-name.txt"), "w") as f:
            f.write("x")
        self._old = {k: os.environ.get(k) for k in ("PATH", "STUDIO_HOME") + tuple(LEAKY) + tuple(KEPT)}
        os.environ["STUDIO_HOME"] = os.path.join(self.d, "home")
        os.environ["PATH"] = self.bin + os.pathsep + os.environ.get("PATH", "")
        self.diff = os.path.join(self.d, "d.diff")
        with open(self.diff, "w") as f:
            f.write("diff --git a/x b/x\n+hello\n")
        self.out = os.path.join(self.d, "verdict.txt")
        self.lines = []

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._t.cleanup()

    def fake(self, tool, body):
        p = os.path.join(self.bin, tool)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body.replace("@REC@", self.rec))
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    def review(self, tool, timeout=30):
        return ev.review(tool, self.diff, "review this", self.out, timeout, grace=0.5,
                         say=self.lines.append, cwd=self.caller)

    def recorded(self):
        with open(self.rec) as f:
            return f.read().split("\n")

    def run_recording(self, tool):
        self.fake(tool, RECORD + ENVREC + "cat > /dev/null\necho 'verdict: fine'\n")
        self.assertEqual(self.review(tool), ev.EXIT_OK)
        lines = self.recorded()
        envd = dict(l.split("=", 1) for l in lines[3:] if "=" in l)
        return lines, envd

    def test_the_tool_does_not_start_in_the_callers_folder_or_the_repo(self):
        for tool in ("gemini", "codex"):
            where = self.run_recording(tool)[0][0]
            for bad in (self.caller, REPO, os.getcwd()):
                self.assertNotEqual(where, os.path.realpath(bad), tool)
                self.assertFalse(where.startswith(os.path.realpath(bad) + os.sep), tool)

    def test_the_folder_is_empty_when_the_tool_starts_and_private(self):
        for tool in ("gemini", "codex"):
            rec = self.run_recording(tool)[0]
            self.assertEqual(rec[1].strip(), "0", tool)
            self.assertEqual(rec[2], "drwx------", tool)

    def test_the_folder_lies_under_the_wrappers_tmp_dir(self):
        for tool in ("gemini", "codex"):
            where = self.run_recording(tool)[0][0]
            root = os.path.dirname(where)
            self.assertTrue(os.path.basename(root).startswith("budgit-extreview-"), where)
            self.assertEqual(root, os.path.realpath(os.path.join(tempfile.gettempdir(), os.path.basename(root))))

    def test_the_tmp_dir_is_gone_after_a_normal_run_and_after_a_timeout(self):
        where = self.run_recording("gemini")[0][0]
        self.assertFalse(os.path.exists(os.path.dirname(where)))
        os.remove(self.rec)
        self.fake("codex", RECORD + "sleep 300\n")
        self.assertEqual(self.review("codex", timeout=1.5), ev.EXIT_TIMEOUT)
        where = self.recorded()[0]
        self.assertFalse(os.path.exists(os.path.dirname(where)))

    def test_the_diff_still_arrives_on_stdin(self):
        self.fake("gemini", 'cat > "@REC@"\necho ok\n')
        self.assertEqual(self.review("gemini"), ev.EXIT_OK)
        self.assertIn("+hello", open(self.rec).read())

    def test_pwd_is_the_empty_folder_and_oldpwd_is_absent(self):
        os.environ["OLDPWD"] = LEAKY["OLDPWD"]
        for tool in ("gemini", "codex"):
            lines, envd = self.run_recording(tool)
            self.assertEqual(os.path.realpath(envd["PWD"]), lines[0], tool)
            self.assertTrue(envd["PWD"].endswith(os.sep + "work"), tool)
            self.assertNotIn("OLDPWD", envd, tool)

    def test_path_bearing_variables_of_the_parent_never_reach_the_tool(self):
        os.environ.update(LEAKY)
        for tool in ("gemini", "codex"):
            envd = self.run_recording(tool)[1]
            for k in LEAKY:
                self.assertNotIn(k, envd, tool)
            self.assertFalse(any("/leak/" in v for v in envd.values()), tool)

    def test_the_auth_and_proxy_variables_still_reach_the_tool(self):
        os.environ.update(KEPT)
        for tool in ("gemini", "codex"):
            envd = self.run_recording(tool)[1]
            for k, v in KEPT.items():
                self.assertEqual(envd.get(k), v, tool)
            self.assertIn("PATH", envd)

    def test_a_stop_signal_removes_the_tmp_tree_while_the_tool_runs(self):
        self.fake("gemini", RECORD + "sleep 300\n")
        code = ("import sys, extreview as ev\n"
                "sys.exit(ev.review('gemini', sys.argv[1], 'p', sys.argv[2], 60, grace=0.5, say=lambda s: None))\n")
        env = dict(os.environ, PYTHONPATH=SCRIPTS)
        proc = subprocess.Popen([sys.executable, "-W", "ignore", "-c", code, self.diff, self.out], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            end = time.monotonic() + 20
            while time.monotonic() < end and not (os.path.exists(self.rec) and len(self.recorded()) >= 4):
                time.sleep(0.1)
            root = os.path.dirname(self.recorded()[0])
            self.assertTrue(os.path.isdir(root))
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=20)
            self.assertFalse(os.path.exists(root))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()
