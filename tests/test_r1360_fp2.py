"""R-1360 follow-up (two false positives of the Bash guard).
1. plugin-merge: a read-only grep or rg option cluster that holds a letter outside the old short list (`-o` and others) gave no pattern
   slot, so a pattern holding the program word was judged as a command. Every output-only letter now keeps the slot; a real merge
   in the same line, a pattern file (-f), a pattern given by -e in a cluster and a substitution stay refused.
2. verify-forgery: `shasum <verify file> ... && budgit ledger <read verb>` was refused because the own budgit CLI was not a reader in
   `_cd_then_readers_only`. The reads are the plugin's own `_CLI_READS`; a write verb and every write into a verify folder stay refused.
The program word is spelled g+it so the file never holds it."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import mergeguard  # noqa: E402
from helpers import StudioTestCase  # noqa: E402
import studiolib as sl  # noqa: E402

G = "g" + "it"


class GrepClusterIsReadOnly(unittest.TestCase):
    def setUp(self):
        self.main = os.path.realpath(tempfile.mkdtemp(prefix="r1360fp2-"))
        self.addCleanup(shutil.rmtree, self.main, ignore_errors=True)
        subprocess.run((G, "init", "-q"), cwd=self.main, check=True, capture_output=True)
        self.file = os.path.join(self.main, "plugin", "scripts", "a.py")
        patch = mock.patch.object(mergeguard, "checkout_root", return_value=self.main)
        patch.start()
        self.addCleanup(patch.stop)

    def judge(self, command):
        return mergeguard.decision("Bash", {"command": command}, self.main)

    def allowed(self, command):
        self.assertIsNone(self.judge(command), command)

    def refused(self, command):
        d = self.judge(command)
        self.assertEqual(d[0] if d else None, "deny", (command, d))

    def test_allow_o_cluster_with_the_word_in_the_pattern(self):
        self.allowed("grep -nioE '%s|y' %s" % (G, self.file))
        self.allowed("grep -nioE 'x|y' plugin/scripts/a.py")

    def test_allow_other_cluster_shapes(self):
        self.allowed("grep -rnoiE '%s|y' %s" % (G, self.file))
        self.allowed("grep -c -o '%s' %s" % (G, self.file))
        self.allowed("grep -ohq '%s' %s" % (G, self.file))
        self.allowed("grep -abxIUL '%s' %s" % (G, self.file))
        self.allowed("rg -o '%s' %s" % (G, self.file))

    def test_every_read_only_letter_keeps_the_slot(self):
        for letter in "abchHiIlLnNoPqrRsSuUvVwxZEFG":
            self.allowed("grep -%s '%s' %s" % (letter, G, self.file))

    def test_refuse_a_real_merge_after_the_cluster(self):
        self.refused("grep -nioE x %s && %s merge feature" % (self.file, G))
        self.refused("grep -nioE x %s | %s push origin" % (self.file, G))
        self.refused("grep -nioE x %s; %s merge feature" % (self.file, G))
        self.refused("grep -c -o x %s && %s pull origin main" % (self.file, G))

    def test_refuse_the_word_as_a_path_or_wrapped(self):
        self.refused("grep -nioE x %s" % G)
        self.refused("env grep -nioE '%s' %s" % (G, self.file))

    def test_refuse_pattern_source_and_substitution_in_a_cluster(self):
        self.refused("grep -nof patterns.txt %s %s" % (G, self.file))
        self.refused("grep -noe %s %s" % (G, self.file))
        self.refused("grep -nioE \"$(%s status)\" %s" % (G, self.file))

    def test_refuse_an_unknown_option(self):
        self.refused("rg --pre %s -o x %s" % (G, self.file))


class ReaderChainThenReadOnlyBudgit(StudioTestCase):
    def setUp(self):
        super().setUp()
        bindir = tempfile.mkdtemp(prefix="r1360fp2-path-")
        self.addCleanup(shutil.rmtree, bindir, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"PATH": bindir})     # no `budgit` on the hook's PATH: the bare name is the own CLI
        env.start()
        self.addCleanup(env.stop)
        for d in (".budgit/verify", ".studio/verify"):
            os.makedirs(os.path.join(self.project, d), exist_ok=True)

    def d(self, command):
        return sl.verify_forgery_decision("Bash", {"command": command}, "general-purpose", self.project)

    def allowed(self, command):
        self.assertIsNone(self.d(command), command)

    def denied(self, command):
        got = self.d(command)
        self.assertIsNotNone(got, command)
        self.assertEqual(got[0], "deny", command)

    def test_allow_shasum_then_a_read_only_ledger_verb(self):
        self.allowed("shasum -a 256 .studio/verify/R-1.md a.txt && budgit --project p ledger show R-1")
        self.allowed("shasum -a 256 .budgit/verify/R-1.md && budgit ledger list")
        self.allowed("cat .studio/verify/R-1.md | head -3 && budgit --project p ledger findings")
        self.allowed("shasum .studio/verify/R-1.md && budgit ledger top")

    def test_a_write_verb_keeps_todays_verdict(self):
        base = "shasum -a 256 .studio/verify/R-1.md && budgit ledger %s"
        # verdicts read at aabb360d (before any R-1360 fp2 change), pinned unconditionally: evidence and fold deny, `done R-1` was ALLOWED
        # there already (reported to the PM, code not changed)
        self.denied(base % "evidence R-1 --note x")
        self.denied(base % "fold R-1")
        self.assertIsNone(self.d(base % "done R-1"), base % "done R-1")

    def test_a_write_is_not_a_read_verb(self):
        self.denied("shasum .studio/verify/R-1.md && budgit ledger add --text x > .studio/verify/R-1.md")

    def test_writes_into_a_verify_folder_stay_refused(self):
        for cmd in ("shasum a && budgit ledger show R-1 > .studio/verify/R-1.md",
                    "shasum a && budgit ledger show R-1 | tee .budgit/verify/R-1.md",
                    "shasum a && budgit ledger show R-1; cp a .studio/verify/R-1.md",
                    "shasum a && budgit ledger show R-1; mv a .budgit/verify/R-1.md",
                    "shasum .studio/verify/R-1.md && sed -i s/a/b/ .studio/verify/R-1.md",
                    "budgit ledger show R-1 >> .budgit/verify/R-1.md"):
            self.denied(cmd)

    def test_a_budgit_that_is_not_a_read_stays_refused(self):
        self.denied("shasum .studio/verify/R-1.md && budgit ledger show R-1 $(echo x)")
        self.denied("shasum .studio/verify/R-1.md && budgit prepare --approve")


if __name__ == "__main__":
    unittest.main()
