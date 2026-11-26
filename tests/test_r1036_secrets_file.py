"""R-1036: `budgit secrets <file>` scans that one file; missing path = exit 2, no traceback; a directory is unchanged.
The fake key is joined at runtime so the repo holds no raw secret."""
import os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
FAKE_TOKEN = "gh" + "p_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5"      # GitHub-shaped, not a real token
NEED = unittest.skipUnless(shutil.which("gitleaks"), "gitleaks not installed")


def run(path):
    """The temp folder is the project: the CLI runs from inside it with no session bound, so the path is inside the project
    (the refusal for a path outside the project stays)."""
    cwd = path if os.path.isdir(path) else os.path.dirname(path)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    return subprocess.run([sys.executable, CLI, "secrets", path], capture_output=True, text=True, timeout=300, cwd=cwd, env=env)


class SecretsFile(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="r1036-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    @NEED
    def test_file_with_fake_key_is_found(self):
        p = self.write("leak.diff", "+token = \"%s\"\n" % FAKE_TOKEN)
        r = run(p)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("LEAKS FOUND", r.stdout)
        self.assertIn("osv-scanner: skipped (no lockfiles)", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertNotIn(FAKE_TOKEN, r.stdout + r.stderr)

    @NEED
    def test_clean_file_gives_no_leaks(self):
        p = self.write("clean.diff", "+print('hello')\n")
        r = run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("gitleaks: no leaks", r.stdout)
        self.assertIn("osv-scanner: skipped (no lockfiles)", r.stdout)

    def test_missing_path_exits_2_without_traceback(self):
        r = run(os.path.join(self.tmp, "nope.diff"))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("does not exist", r.stdout + r.stderr)

    @NEED
    def test_directory_is_unchanged(self):
        self.write("a.txt", "x = \"%s\"\n" % FAKE_TOKEN)
        r = run(self.tmp)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("(directory)", r.stdout)
        self.assertIn("LEAKS FOUND", r.stdout)
        os.remove(os.path.join(self.tmp, "a.txt"))
        self.write("b.txt", "nothing here\n")
        r = run(self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
