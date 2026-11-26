"""R-335 class 6, reworked by R-418: a task runner or an installer (make, npm, cargo, pip, go, gradle ...) runs commands that
live in a file the guard never reads. It passes silently, with or without the standing yes, and asks only on a concrete danger
signal: the folder it runs in is protected data / inside it / above the project, a word or an environment assignment reaches
protected data or a folder above the project, or the line also carries a form the other scans judge."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import GuardCase, sl  # noqa: E402

RUNS = (
    "make", "make clean", "make -C docs all", "gmake test", "/usr/bin/make build", "npm run build", "npm test", "npm start",
    "npm install", "npm run-script clean", "pnpm run build", "pnpm install", "pnpm test", "yarn build", "yarn install",
    "yarn run clean", "bun run build", "npx prettier --check src", "just clean", "rake db:reset", "./gradlew clean",
    "gradle build", "mvn clean install", "./mvnw verify", "tox", "nox -s tests", "invoke clean", "task build", "cargo build",
    "cargo run", "cargo test", "go generate ./cmd", "go run main.go", "go build ./...", "go test ./...",
    "poetry run python x.py", "uv run script.py", "cmake --build build", "ninja", "pip install -r requirements.txt",
    "pip3 install -e .", "swift build", "xcodebuild -scheme App build", "dotnet build", "bundle install", "composer install",
    "cd docs && make", "echo hi && npm run build", "FOO=1 make", "make -C src", "npm run build -- --out-dir ../dist",
    "npm test -- --grep thing",
)
QUIET = ("make --version", "make --help", "npm --version", "npm -v", "npm help", "yarn --version", "cargo --version",
         "go version", "just --list", "npm config get registry")
# A concrete danger signal: a protected folder, the folder above the project, a word that reaches protected data.
DANGER = (
    "cd .. && make", "cd %(root)s/.. && make", "cd / && npm test", "cd data && make", "make -C ..", "make -C ../..", "npm install /",
    "npm test --prefix ..", "pip install ..",
    "DATA=data.json make", "OUT=%(root)s/data.json npm run build", "make clean && rm data.json", "npm run build > data.json",
    "make && rm -rf build/$UNSET", "cd $UNSET_DIR && make", "cd - && make",
    # the spellings a first look misses: attached option value, KEY=value, a variable set on the same line
    "make -C..", "make -C%(root)s/..", "make DIR=/", "make OUT=%(root)s/..", "x=..; make -C $x", "x=..; npm install --prefix $x",
    "x=data/real; make -C $x", "make -C data/real", "npm --prefix=data test", "DIR=.. make",
)
# the same signals hold under the standing yes (the unresolved delete is the yes's to lift)
DANGER_UNDER_THE_YES = tuple(c for c in DANGER if "$UNSET" not in c and "rm" not in c.split() and "> data.json" not in c)


class TaskRunnerTest(GuardCase):
    def test_a_task_runner_or_installer_passes_in_the_project(self):
        for cmd in RUNS:
            self.assertIsNone(self.d(cmd), cmd)

    def test_version_and_help_do_not_ask(self):
        for cmd in QUIET:
            self.assertIsNone(self.d(cmd), cmd)

    def test_they_also_pass_under_the_standing_yes(self):
        self.assertTrue(self.grant())
        for cmd in RUNS:
            self.assertIsNone(self.d(cmd), cmd)

    def test_a_danger_signal_asks(self):
        for cmd in DANGER:
            line = cmd % {"root": self.root} if "%(root)s" in cmd else cmd
            got = self.d(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_the_danger_signals_hold_under_the_standing_yes(self):
        self.assertTrue(self.grant())
        for cmd in DANGER_UNDER_THE_YES:
            line = cmd % {"root": self.root} if "%(root)s" in cmd else cmd
            got = self.d(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_a_runner_started_above_the_project_asks_from_the_working_folder_too(self):
        parent = os.path.dirname(self.root)
        for cmd in ("make", "npm test", "cargo build", "pip install -r requirements.txt"):
            got = self.d(cmd, cwd=parent)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
            self.assertIn(cmd.split()[0], got[1])

    def test_a_runner_in_the_protected_folder_asks_and_in_another_folder_of_the_project_passes(self):
        os.makedirs(os.path.join(self.root, "data"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        self.assertEqual(self.d("make", cwd=os.path.join(self.root, "data"))[0], "ask")
        self.assertIsNone(self.d("make", cwd=os.path.join(self.root, "src")))

    def test_a_runner_that_names_protected_data_still_asks_under_the_yes(self):
        self.assertTrue(self.grant())
        self.assertEqual(self.d("make clean && rm data.json")[0], "ask")

    def test_a_project_without_protected_data_is_not_asked(self):
        import json
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = []
        json.dump(cfg, open(pj, "w"))
        self.assertIsNone(self.d("make clean"))

    def test_the_ask_says_why(self):
        got = self.d("cd .. && make")
        self.assertIn("make", got[1])


if __name__ == "__main__":
    unittest.main()
