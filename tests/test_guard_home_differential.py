"""R-473: the Budgit-home write guard, compared with itself at its base (R-493, commit 53f996d5). Over a corpus of ordinary and
hostile commands, the new guard must never ask or deny what the base let through (0 newly asking, 0 newly denied), must never
soften a deny into an ask, and may only soften a verdict for the reviewed commands in ALLOWED_BY_FAMILY['R-473 ...']."""
import importlib.util
import os
import subprocess
import tempfile
import unittest

from helpers import ROOT
from test_guard_trust import GuardCase, CLI
import test_guard_differential as diff
import studiolib as sl

BASE = "53f996d5"
RANK = {None: 0, "ask": 1, "deny": 2}


def load_base():
    got = subprocess.run(["git", "-C", ROOT, "show", "%s:plugin/scripts/studiolib.py" % BASE], capture_output=True, text=True)
    if got.returncode:
        return None
    path = os.path.join(tempfile.mkdtemp(prefix="base-home-"), "studiolib_home_base.py")
    with open(path, "w") as fh:
        fh.write(got.stdout)
    spec = importlib.util.spec_from_file_location("studiolib_home_base", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# {P} the project, {B} the Budgit CLI, {U} the user's home, {H} the Budgit home. Each entry is a command the base asked about or
# denied for no reason and the new guard lets through; each was read by hand: it writes nothing in the Budgit home.
ALLOWED_BY_FAMILY = {
    "R-473 variables set in the same command, Budgit bookkeeping, heredoc data, test runners, ledger edits": [
        "P={P}; B={B}; $B --project $P ledger start R-1",
        "P={P}; B={B}; $B --project $P ledger add --text 'the guard on ~/.studio and trust.json asks too often'",
        "W={P}; python3 -m unittest discover -s $W/tests",
        "W={P}; cd $W && python3 -m unittest discover -s tests",
        "python3 -W ignore -m unittest discover -s {P}/tests -p 'test_studiolib*.py'",
        "W={P}; python3 -W ignore -m unittest discover -s $W/tests -p 'test_studiolib*.py'",
        "python3 -m pytest tests/test_hook.py",
        "cat > {P}/note.md <<'EOF'\nthe guard on ~/.studio and ~/.budgit trust.json asks too often\nEOF",
        "cat > {P}/note.md <<'EOF'\ndon't write ~/.studio/trust.json\nEOF",
        "python3 -c \"import json;d=json.load(open('{P}/.studio/ledger.json'));json.dump(d,open('{P}/.studio/ledger.json','w'))\"",
        # the base denied this: Budgit's own CLI script, named on the line, was read as a program that names a home
        "cd {P} && python3 - <<'PY'\nimport json\nd = json.load(open('.studio/ledger.json'))\njson.dump(d, open('.studio/ledger.json', 'w'))\nPY\n"
        "{B} --project {P} ledger accept-edit",
    ],
}
# Silent in the new guard (the base was silent too in this test setup, or asked only by cwd): pinned so they stay silent.
STAYS_SILENT = [
    "{B} --project {P} ledger add --text 'the guard on ~/.studio asks'",
    "python3 - <<'PY'\nimport json\np = '{P}/.studio/ledger.json'\nd = json.load(open(p))\nd['n'] = 'the home ~/.studio is guarded'\n"
    "json.dump(d, open(p, 'w'))\nPY",
]
ALLOWED = {c for v in ALLOWED_BY_FAMILY.values() for c in v}

NEVER_WEAKER = [
    "echo x > {U}/.studio/trust.json", "echo x > ~/.budgit/trust.json", "echo x > $HOME/.studio/trust.json",
    "H=~/.studio; cp /tmp/p $H/trust.json", "H={U}/.budgit; echo x > $H/trust.json", "cd ~ && echo x > .studio/x",
    "cd {U} && cp /tmp/p .budgit/trust.json", "cp /tmp/p {U}/.stu*/trust.json", "cd $UNSET && cp /tmp/p trust.json",
    "cp /tmp/p $(echo ~/.budgit/trust.json)", "eval \"cp /tmp/p {U}/.studio/trust.json\"",
    "cat > ~/.studio/trust.json <<EOF\nx\nEOF", "tee {H}/trust.json <<'EOF'\nx\nEOF",
    "python3 - <<'PY'\nopen('{U}/.studio/trust.json', 'w').write('x')\nPY",
    "python3 - <<'PY'\nimport os\nopen(os.path.expanduser('~') + '/.stu' + 'dio/trust.json', 'w').write('x')\nPY",
    "python3 -c \"import os;open(os.path.expanduser('~')+'/.bud'+'git/trust.json','w')\"",
    "bash <<'EOF'\nrm ~/.studio/trust.json\nEOF", "cat <<'EOF' | sh\nrm ~/.studio/trust.json\nEOF",
    "python3 -m studiolib", "python3 -c 'import studiolib; studiolib.grant_trust(1)'", "python3 hook.py user-prompt-submit",
    "{B} --project {U} ledger add --text x", "{B} --project {U}/.studio ledger add --text x",
]
# A write whose $HOME / ~ was reassigned in the same command resolves against the NEW value. The base let these through, so
# they are strengthened on purpose (outside the "0 newly denied" count) and must deny at every cwd.
MUST_DENY = [
    "HOME={H}; echo x > $HOME/real-data-yes/s.jsonl", "HOME={H}; echo x > $HOME/config.json",
    "HOME={H}; echo x > ~/real-data-pending/s.jsonl", "HOME=$UNSET; echo x > $HOME/config.json",
    "export HOME={H}; cp /tmp/p $HOME/trust.json", "HOME={U} cp /tmp/p $HOME/.studio/trust.json",
    # found by R-473: a program fed through a heredoc that runs a command on `~/.studio` (the base only saw the spelled-out path)
    "python3 - <<'PY'\nimport os\nos.system('cp /tmp/p ~/.studio/trust.json')\nPY",
    # R-515: the redirect of a cd/pushd/popd step was skipped with the step
    "cd /tmp > ~/.studio/x", "pushd /tmp >> ~/.budgit/x", "popd 2> ~/.studio/x",
]


class HomeDifferentialTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.base = load_base()
        if self.base is None:
            self.skipTest("commit %s is not in this checkout" % BASE)
        self.uhome = os.path.expanduser("~")

    def fill(self, cmd):
        return cmd.replace("{P}", self.root).replace("{B}", CLI).replace("{U}", self.uhome).replace("{H}", sl.home_state())

    def verdict(self, mod, cmd, cwd):
        got = mod.home_write_decision("Bash", {"command": cmd}, cwd)
        return got[0] if got else None

    def test_zero_newly_asking_zero_newly_denied_and_only_reviewed_commands_loosen(self):
        cmds = [self.fill(c) for c in diff.corpus(self.root, CLI)] + [self.fill(c) for c in NEVER_WEAKER + sorted(ALLOWED) + STAYS_SILENT]
        newly_asking = newly_denied = 0
        loosened, bad = {}, []
        for cwd in (self.project, self.tmp, self.uhome):
            for cmd in cmds:
                old, new = self.verdict(self.base, cmd, cwd), self.verdict(sl, cmd, cwd)
                if old is None and new == "ask":
                    newly_asking += 1
                if old is None and new == "deny":
                    newly_denied += 1
                if RANK[new] < RANK[old]:
                    key = cmd.replace(self.root, "{P}").replace(CLI, "{B}")
                    loosened.setdefault(key, set()).add((old, new, cwd == self.project))
                    if key not in ALLOWED:
                        bad.append((cmd, old, new, cwd))
        if os.environ.get("DIFF_DUMP"):
            print("\nHOME CORPUS %d x 3 cwds; LOOSENED %d" % (len(cmds), len(loosened)))
            for key in sorted(loosened):
                print("  LOOSE", repr(key), sorted(loosened[key]))
        self.assertEqual((newly_asking, newly_denied), (0, 0))
        self.assertEqual(bad, [], "softened, and not in the reviewed R-473 list")
        self.assertEqual(sorted(ALLOWED - set(loosened)), [], "an R-473 entry that loosens nothing")

    def test_the_ordinary_bookkeeping_and_data_shapes_are_silent_at_every_cwd(self):
        for cmd in map(self.fill, sorted(ALLOWED) + STAYS_SILENT):
            for cwd in (self.project, self.tmp):
                self.assertIsNone(self.verdict(sl, cmd, cwd), (cmd, cwd))

    def test_the_home_reassign_commands_are_denied_at_every_cwd(self):
        for cmd in map(self.fill, MUST_DENY):
            for cwd in (self.project, self.tmp, self.uhome):
                self.assertEqual(self.verdict(sl, cmd, cwd), "deny", (cmd, cwd))

    def test_a_deny_never_becomes_an_ask(self):
        for cmd in map(self.fill, NEVER_WEAKER):
            old, new = self.verdict(self.base, cmd, self.project), self.verdict(sl, cmd, self.project)
            self.assertGreaterEqual(RANK[new], RANK[old], cmd)


if __name__ == "__main__":
    unittest.main()
