"""R-1061: a literal trailer in a read-only git verb is a search term, not a message.
Only the one strict read-only shape skips the literal scan; everything else keeps the pre-R-1061 deny."""
import unittest

import helpers  # noqa: F401  (puts plugin/scripts on sys.path)
import attribution

LIT = "Co-" + "Authored-By: Cla" + "ude"
L = "git log --format='%s'" % LIT


def decide(command):
    return attribution.decision("Bash", {"command": command}, "/tmp", True)


class ReadVerbsCase(unittest.TestCase):
    def deny(self, cmd):
        got = decide(cmd)
        self.assertIsNotNone(got, cmd)
        self.assertEqual(got[0], "deny", cmd)

    def test_the_refused_search_passes(self):
        cmd = "git -C /Users/x/repo log -i -E --grep='%s' --oneline" % LIT
        self.assertIsNone(decide(cmd))

    def test_allowed_shapes_pass(self):
        for cmd in ("git -C /r log --grep='%s' | wc -l" % LIT,
                    "git log --grep='%s' | grep x | head -3" % LIT,
                    "git log --grep='%s' 2>&1 | head" % LIT,
                    "git log --grep='%s' > /dev/null" % LIT,
                    "git log --grep='%s' 2>/dev/null" % LIT,
                    "git --no-pager log --grep='%s'" % LIT,
                    "git show HEAD", "git grep '%s'" % LIT):
            self.assertIsNone(decide(cmd), cmd)

    def test_every_read_verb_passes(self):
        for verb in attribution.READ_VERBS:
            self.assertIsNone(decide("git %s '%s'" % (verb, LIT)), verb)

    def test_commit_and_tag_messages_still_deny(self):
        self.deny("git commit -m '%s'" % LIT)
        self.deny("git tag -m '%s' v1" % LIT)
        self.deny("git notes add -m '%s'" % LIT)

    def test_alias_or_config_before_a_read_verb_still_judged(self):
        self.deny("git -c alias.x='!git commit -m \"%s\"' log" % LIT)
        self.deny("git -c trailer.x.key='%s' log" % LIT)

    def test_unknown_verb_with_literal_still_denies(self):
        self.deny("git frobnicate '%s'" % LIT)

    def test_read_verb_that_writes_or_runs_is_judged(self):
        self.deny("git log --output=m -1 --format='%s'" % LIT)
        self.deny("git log --outp=m -1 --format='%s'" % LIT)
        self.deny("git diff --output m '%s'" % LIT)
        self.deny("git grep -Oless '%s'" % LIT)
        self.deny("git grep -inOless '%s'" % LIT)
        self.deny("git grep --open-files-in-pager=less '%s'" % LIT)
        self.deny("git diff --ext-diff '%s'" % LIT)
        self.deny("git log --textconv '%s'" % LIT)

    def test_stdout_to_a_file_or_a_writer_is_judged(self):
        for tail in ("> m", ">> m", "| tee m", "| tee -a m", "| git commit -F -", "| head -3 | tee m",
                     "| sort -o m", "| uniq - m", ">&3", "&> m", "| less -o m"):
            self.deny("%s %s" % (L, tail))
        self.deny(">m %s" % L)
        self.deny("%s > m && git commit -F m" % L)

    def test_shell_wrappers_and_joins_are_judged(self):
        self.deny('sh -c "%s > m"' % L)
        self.deny('bash -c "%s | tee m"' % L)
        self.deny('eval "%s > m"' % L)
        self.deny("%s && echo ok" % L)
        self.deny("%s ; git status" % L)
        self.deny("%s || true" % L)
        self.deny("%s &" % L)
        self.deny("%s\ngit status" % L)
        self.deny("echo $(%s)" % L)
        self.deny("%s | cat `echo m`" % L)

    def test_shell_expansion_and_less_are_judged(self):
        self.deny("git log {--output=m,} --format='%s'" % LIT)
        self.deny("git log --o${u}utput=m --format='%s'" % LIT)
        self.deny("git log $'--output=m' --format='%s'" % LIT)
        self.deny("git -C ~/r log --format='%s'" % LIT)
        self.deny("%s | less" % L)
        self.deny("%s | more" % L)

    def test_plain_pattern_with_no_special_character_passes(self):
        self.assertIsNone(decide("git log -i -E --grep=fix.typo --oneline"))
        self.assertIsNone(decide("git -C /r log -i -E --grep='%s' --oneline | wc -l" % LIT))

    def test_global_options_and_env_prefixes_are_judged(self):
        self.deny("git --attr-source log commit -m '%s'" % LIT)
        self.deny("GIT_EXTERNAL_DIFF=x git diff --ext-diff '%s'" % LIT)
        self.deny("GIT_PAGER=x git -p log '%s'" % LIT)
        self.deny("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.x GIT_CONFIG_VALUE_0=y git log '%s'" % LIT)
        for opt in ("-p", "--paginate", "--git-dir=x", "--exec-path=x", "--bogus"):
            self.deny("git %s log --format='%s'" % (opt, LIT))


if __name__ == "__main__":
    unittest.main()
