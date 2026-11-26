"""R-1065: a scanner that prints non-UTF-8 bytes or breaks must never crash the secrets gate or pass it."""
import os
import re
import stat
import tempfile
import unittest
from unittest import mock

import secretscan

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "scripts")
SCANNER_MODULES = ["secretscan.py", "auditmcp.py"]


def _fake(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class SecretsDecodeTest(unittest.TestCase):
    def test_run_survives_non_utf8_output(self):
        with tempfile.TemporaryDirectory() as d:
            fake = _fake(d, "fakescan", "#!/bin/sh\nprintf 'leak \\377 here\\n'\nexit 1\n")
            code, out = secretscan._run([fake], d)
        self.assertEqual(code, 1)
        self.assertIn("\ufffd", out)

    def test_scanner_that_cannot_run_is_a_failed_gate(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = secretscan._run([os.path.join(d, "missing-scanner")], d)
            self.assertEqual(code, 125)
            self.assertIn("could not run", out)
            with mock.patch.object(secretscan.subprocess, "run", side_effect=RuntimeError("boom")):
                code, out = secretscan._run(["x"], d)
            self.assertEqual(code, 125)

    def test_odd_exit_and_crash_give_failed_gate_not_pass(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "package-lock.json"), "w") as f:
                f.write("{}")
            odd = _fake(d, "gitleaks", "#!/bin/sh\nprintf '\\377'\nexit 7\n")
            with mock.patch.object(secretscan.shutil, "which", return_value=odd), \
                    mock.patch.object(secretscan, "_run", wraps=secretscan._run) as run:
                real = secretscan._run
                run.side_effect = lambda argv, cwd: real([odd] + argv[1:], cwd) if argv[0] == "gitleaks" else (125, "boom")
                self.assertEqual(secretscan.scan(d), 2)
            with mock.patch.object(secretscan.shutil, "which", return_value="/x"), \
                    mock.patch.object(secretscan.subprocess, "run", side_effect=OSError("nope")):
                self.assertEqual(secretscan.scan(d), 2)

    def test_no_text_mode_call_without_errors(self):
        for name in SCANNER_MODULES:
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
                src = f.read()
            for m in re.finditer(r"subprocess\.(run|check_output|Popen)\(", src):
                depth, i = 1, m.end()
                while depth and i < len(src):
                    depth += {"(": 1, ")": -1}.get(src[i], 0)
                    i += 1
                call = src[m.start():i]
                if re.search(r"text\s*=\s*True|universal_newlines\s*=\s*True", call):
                    self.assertIn("errors=", call, "%s: %s" % (name, call))


if __name__ == "__main__":
    unittest.main()
