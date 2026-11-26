"""R-618: `budgit test [--full]` hands over to the project's own pool entry (tools/budgit_test.py)."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def run(*args, cwd=None):
    env = dict(os.environ, BUDGIT_LANG="en")
    return subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, cwd=cwd, env=env, timeout=120)


class TestSubcommandTest(unittest.TestCase):
    def test_it_is_listed_with_its_full_flag(self):
        r = run("test", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--full", r.stdout)

    def test_it_runs_the_projects_pool_entry_with_the_full_flag(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tools"))
            os.makedirs(os.path.join(d, ".git"))
            os.makedirs(os.path.join(d, ".studio"))
            with open(os.path.join(d, ".studio", "project.json"), "w") as f:
                f.write("{}\n")
            with open(os.path.join(d, ".studio", "ledger.json"), "w") as f:          # what makes it a Budgit project
                f.write("{}\n")
            with open(os.path.join(d, "tools", "budgit_test.py"), "w") as f:
                f.write("import sys\nprint('ENTRY', sys.argv[1:])\nsys.exit(7)\n")
            r = run("test", "--full", "--project", d)
            self.assertIn("ENTRY ['--full']", r.stdout, r.stdout + r.stderr)
            self.assertEqual(r.returncode, 7)
            r = run("test", "--project", d)
            self.assertIn("ENTRY []", r.stdout, r.stdout + r.stderr)

    def test_the_cli_file_ships_the_command_in_the_export_list(self):
        with open(CLI, encoding="utf-8") as f:
            self.assertIn('add_parser("test"', f.read())


if __name__ == "__main__":
    unittest.main()
