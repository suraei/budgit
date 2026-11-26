"""R-558 round 7 security review: the guard on this branch is run next to the guard at its merge-base with main, over the
cd / cp / rsync shapes the reviewer named. Nothing the merge-base asked about may pass now, and every write into protected
data asks. The merge-base guard runs in a child process from a `git archive` of its plugin/scripts, so the two copies of
studiolib and realdata never mix in one interpreter."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT
from test_guard_trust import GuardCase
import studiolib as sl

# {R} the protected root, {H} a harmless folder outside it, {K} a symlink outside the root that points at {R}/docs
WRITES = [
    "cd {R}/docs && cp -R {H}/ ../data/real/",
    "cd {R} && cp /tmp/x.csv data/real/$Y",
    "cd {R} && mv $A $B",
    "cd {R} && cp /tmp/x.csv da*a/real/y.csv",
    "cd {R}/src && cp /tmp/x ../data/real/y.csv",
    "cd /tmp/nope && true; cp /tmp/x data/real/",
    "cd /tmp/nope && true\ncp /tmp/x data/real/",
    "cd {K} && cp /tmp/x ../data.json",
    "cd {K} && cp -R {H}/ ../data/real/",
    "cd {R} && rsync -ab --backup-dir=../real {H}/ data/sub/",
    "cd {R} && rsync -a --backup-dir=../real {H}/ data/sub/",
    "cd - && cp /tmp/x.csv data.json",
]
OTHERS = [
    "cd - && cp /tmp/x.csv y.csv",
    "cd /tmp/nope && true | cp /tmp/x data/real/y.csv",
    "cd {R}/docs && cp {H}/notes.txt b.md",
]

PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import studiolib as sl
cases = json.loads(sys.stdin.read())
out = []
for cmd, cwd in cases:
    got = sl.real_data_decision("Bash", {"command": cmd}, sys.argv[2], cwd=cwd)
    out.append(got[0] if got else None)
print(json.dumps(out))
"""


def merge_base():
    got = subprocess.run(["git", "-C", ROOT, "merge-base", "HEAD", "main"], capture_output=True, text=True)
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True)
    if got.returncode or head.returncode or got.stdout.strip() == head.stdout.strip():
        return None
    return got.stdout.strip()


class MergeBaseGuardTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.base = merge_base()
        if self.base is None:
            self.skipTest("no merge-base with main that differs from HEAD")
        self.scripts = tempfile.mkdtemp(prefix="r558-base-")
        tar = subprocess.run(["git", "-C", ROOT, "archive", self.base, "plugin/scripts"], capture_output=True)
        self.assertEqual(tar.returncode, 0)
        subprocess.run(["tar", "-x", "-C", self.scripts], input=tar.stdout, check=True)
        self.harmless = os.path.join(self.tmp, "harmless")
        os.makedirs(self.harmless)
        open(os.path.join(self.harmless, "notes.txt"), "w").write("x\n")
        self.link = os.path.join(self.tmp, "docslink")
        os.symlink(os.path.join(self.root, "docs"), self.link)

    def fill(self, cmd):
        return cmd.replace("{R}", self.root).replace("{H}", self.harmless).replace("{K}", self.link)

    def base_verdicts(self, cases):
        got = subprocess.run([sys.executable, "-I", "-c", PROBE, os.path.join(self.scripts, "plugin", "scripts"), self.project],
                             input=json.dumps(cases), capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(got.returncode, 0, got.stderr[-2000:])
        return json.loads(got.stdout)

    def test_every_write_asks_on_this_branch(self):
        for cmd in WRITES:
            full = self.fill(cmd)
            got = sl.real_data_decision("Bash", {"command": full}, self.project, cwd=self.project)
            self.assertIsNotNone(got, full)
            self.assertEqual(got[0], "ask", full)

    def test_nothing_the_merge_base_asked_passes_now(self):
        cases = [(self.fill(c), cwd) for c in WRITES + OTHERS for cwd in (self.project, self.tmp)]
        base = self.base_verdicts(cases)
        self.assertGreaterEqual(sum(1 for v in base if v), len(WRITES), "the merge-base guard must really run and ask")
        loosened = []
        for (cmd, cwd), old in zip(cases, base):
            got = sl.real_data_decision("Bash", {"command": cmd}, self.project, cwd=cwd)
            if old and not got:
                loosened.append((cmd, cwd))
        self.assertEqual(loosened, [])


if __name__ == "__main__":
    unittest.main()
