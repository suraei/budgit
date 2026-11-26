"""R-335: the usability cost of the guard, counted. A fixed list of ordinary development commands is run through the base
guard (commit 820f2cb) and the current one, in a project with protected data: how many ask before, after, and after with the
standing yes (`/budgit-trust work`). The numbers are the answer to "how much more does it ask", so a change in them is a
decision, not an accident: update EXPECTED together with the plan. The differential corpus is counted too and printed."""
import os
import unittest

from test_guard_trust import GuardCase, CLI, git
import test_guard_differential as diff
import studiolib as sl

ORDINARY = (
    "ls -la", "git status", "git diff", "git log --oneline -5", "git add docs/a.md", "git commit -m x", "cat docs/a.md",
    "grep -rn foo src", "python3 -m pytest -q", "python3 src/app.py", "npm test", "npm run build", "make", "cargo build",
    "rm -rf build", "rm build/o", "rm docs/old.txt", "cp docs/a.md docs/b.md", "mv docs/a.md docs/c.md", "mkdir -p out/x",
    "touch docs/new.md", "echo hi > docs/new.md", "echo hi >> docs/log.txt", "sed -i s/a/b/ docs/a.md", "chmod +x src/run.sh",
    "tar czf /tmp/o.tgz docs", "tar xzf /tmp/o.tgz -C /tmp/x", "unzip a.zip -d /tmp/x", "find . -name '*.pyc'",
    "find docs -name '*.tmp' -delete", "for f in docs/*.md; do wc -l $f; done", "for f in build/*; do rm \"$f\"; done",
    "cd docs && ls", "cd docs && rm -rf tmp", "python3 -c \"print(1+1)\"",
    "python3 -c \"import json;print(json.load(open('docs/a.json')))\"", "python3 -c \"open('out.txt','w').write('x')\"",
    "node -e \"console.log(1)\"", "node -e \"require('fs').writeFileSync('o.txt','x')\"", "ruby -e 'puts 1'",
    "awk '{print $1}' docs/a.md", "x=docs; ls $x", "x=docs; rm $x/old.txt", "rm $HOME/.cache/x", "ls | xargs wc -l",
    "ls | xargs rm", "git ls-files | xargs grep foo", "echo $(date) > docs/stamp.txt", "bash docs/run.sh",
    "bash -c 'echo hi'", "curl -o /tmp/x https://example.com", "pip install -r requirements.txt", "docker ps", "tree docs",
    "du -sh .", "head -5 docs/a.md", "sort docs/a.md | uniq -c", "diff docs/a.md docs/b.md", "open docs/a.md",
    "pip install -e .", "go test ./...", "swift build", "xcodebuild -scheme App build", "x=docs; rm $x/old.txt",
    "python3 -c \"open('out.txt','w').write('x')\"", "tar xzf /tmp/o.tgz -C /tmp/x",
)

# (asks in the base guard, asks now, asks now with the standing yes), cwd = the project. R-418: runners and installers
# (npm test, npm run build, make, cargo build, pip install, go test ...) pass with no grant; an unresolved delete still asks.
# R-1294 (the submodule rule): a work-tree status or diff in a protected repo asks unless it carries --ignore-submodules=all, also
# under the standing yes. That is two more asks in the project and two more with the yes, reviewed here as a decision.
SUBMODULE_RULE_ASKS = {"git status", "git diff"}
EXPECTED = (3, 6, 3)      # R-493: one of the five ordinary commands no longer asks; R-1294: + the two submodule-rule asks
STILL_ASK_WITH_THE_YES = {"find docs -name '*.tmp' -delete"} | SUBMODULE_RULE_ASKS


class AskCountTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.base = diff.load_base()
        if self.base is None:
            self.skipTest("commit %s is not in this checkout" % diff.BASE_COMMIT)
        git(self.project, "branch", "-q", "feat")
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.path.dirname(CLI) + os.pathsep + self._path
        self.addCleanup(os.environ.__setitem__, "PATH", self._path)

    def asks(self, mod, cmds, cwd):
        out = []
        for cmd in cmds:
            got = mod.real_data_decision("Bash", {"command": cmd}, self.project, cwd=cwd)
            if got and got[0] == "ask":
                out.append(cmd)
        return out

    def test_the_ordinary_commands_ask_as_many_times_as_decided(self):
        before = self.asks(self.base, ORDINARY, self.project)
        after = self.asks(sl, ORDINARY, self.project)
        self.assertTrue(self.grant())
        with_yes = self.asks(sl, ORDINARY, self.project)
        print("\nORDINARY %d commands, cwd=project: asks before %d, after %d, after with the standing yes %d"
              % (len(ORDINARY), len(before), len(after), len(with_yes)))
        for cmd in after:
            if cmd not in before:
                print("  NEW ASK: %s%s" % (cmd, "" if cmd in with_yes else "   (passes with the standing yes)"))
        self.assertEqual((len(before), len(after), len(with_yes)), EXPECTED)
        self.assertEqual(set(with_yes), STILL_ASK_WITH_THE_YES)
        for cmd in SUBMODULE_RULE_ASKS:       # each one asks for the named reason, not by accident
            got = sl.real_data_decision("Bash", {"command": cmd}, self.project, cwd=self.project)
            self.assertEqual(got[0], "ask", cmd)
            self.assertIn("ignore-submodules=all", got[1], cmd)

    def test_the_differential_corpus_count_is_printed(self):
        cmds = diff.corpus(self.root, CLI)
        for cwd, label in ((self.project, "cwd=project"), (self.tmp, "cwd=elsewhere")):
            before = set(self.asks(self.base, cmds, cwd))
            after = set(self.asks(sl, cmds, cwd))
            print("\nCORPUS %d commands, %s: asks before %d, after %d, newly asking %d"
                  % (len(cmds), label, len(before), len(after), len(after - before)))
        self.assertGreaterEqual(len(cmds), 150)


if __name__ == "__main__":
    unittest.main()
