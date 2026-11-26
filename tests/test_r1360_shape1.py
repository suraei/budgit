"""R-1360 F14 shape 1 (narrowed by the PM): braces inside balanced quotes are not a brace expansion when EVERY segment head runs no part of
its argument (grep, rg, ps plus head, tail, cat, wc, sort, uniq, cut, tr, nl, diff, jq, shasum). awk, sed, perl, python, node, ruby, env,
xargs, the shells and any other head keep today's raw judgement (they can run code); test_r1199_guards keeps its awk/sed rows. An open
quote, a wrapper or shell word, a git head and the program word inside quotes keep the refusal. The program word is spelled g+it."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import mergeguard  # noqa: E402

G = "g" + "it"
BRACE = "a line with a brace expansion or a backslash-newline, which can spell the word git"


class QuotedBraces(unittest.TestCase):
    def setUp(self):
        self.main = os.path.realpath(tempfile.mkdtemp(prefix="r1360s1-"))
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

    def refused(self, command, text=None):
        d = self.judge(command)
        self.assertEqual(d[0] if d else None, "deny", (command, d))
        if text:
            self.assertIn(text, d[1], command)

    def test_allow_a_quantifier_after_a_pipe(self):
        self.allowed("head -5 %s | grep -E '[0-9]{2}'" % self.file)
        self.allowed("grep -E '[[:digit:]]{2}' %s" % self.file)

    def test_allow_a_jq_object(self):
        self.allowed("cat %s | jq '{a: .b, c: .d}'" % self.file)

    def test_allow_a_range_quantifier_after_sort(self):
        self.allowed("sort %s | grep -E 'x{1,2}'" % self.file)

    def test_refuse_awk_sed_and_python_as_before(self):
        self.refused("awk '{print $1, $2}' %s" % self.file, BRACE)
        self.refused("sed -n '/x\\{2\\}/p' %s" % self.file, BRACE)
        self.refused("sed 's/a{1,2}/b/' %s" % self.file, BRACE)
        self.refused("python3 -I -c 'd = {\"a\": 1}; print(d)'", BRACE)
        self.refused("head -5 %s | awk '{print $1, $2}'" % self.file, BRACE)

    def test_refuse_a_runner_head_or_a_wrapper(self):
        self.refused("perl -ne 'print {a,b}' %s" % self.file, BRACE)
        self.refused("env grep -E 'x{1,2}' %s" % self.file, BRACE)
        self.refused("xargs -I{} echo '{a,b}'", BRACE)
        self.refused("sh -c 'echo {a,b}'")

    def test_refuse_a_sort_that_runs_a_program_or_writes(self):
        for opt in ("--compress-program=x", "--compress-prog x", "--comp x", "--output=out.txt", "--out out.txt", "-o out.txt", "-no out.txt"):
            self.refused("sort %s %s | grep -E 'x{1,2}'" % (opt, self.file), BRACE)

    def test_refuse_an_rg_preprocessor(self):
        self.refused("rg --pre cmd -E 'x{1,2}' %s" % self.file, BRACE)
        self.refused("rg --pre=cmd 'x{1,2}' %s" % self.file, BRACE)
        self.refused("rg --pre-glob '*.gz' 'x{1,2}' %s" % self.file, BRACE)

    # R-1360 F15 (security review of ce7ba7d0 and 2a7a5507)
    def test_f2_uniq_with_an_output_operand_is_not_masked(self):
        self.refused("uniq in.txt out.txt | grep -E 'x{1,2}'", BRACE)
        self.refused("uniq -c in.txt | grep -E 'x{1,2}'", BRACE)

    def test_f1_sort_scans_past_the_double_dash(self):
        self.refused("sort -T -- -o out %s | grep -E 'x{1,2}'" % self.file, BRACE)
        self.refused("sort -T -- --compress-program=X %s | grep -E 'x{1,2}'" % self.file, BRACE)

    def test_f5_an_assignment_prefix_turns_masking_off(self):
        self.refused("PERL5OPT=x shasum %s | grep -E 'x{1,2}'" % self.file, BRACE)
        self.refused("cat %s | RIPGREP_CONFIG_PATH=x grep -E 'x{1,2}'" % self.file, BRACE)

    def test_f4_a_head_is_a_bare_name_never_a_path(self):
        self.refused("./cat %s | grep -E 'x{1,2}'" % self.file, BRACE)
        self.refused("/tmp/grep -E 'x{1,2}' %s" % self.file, BRACE)
        self.refused("bin/head %s | grep -E 'x{1,2}'" % self.file, BRACE)

    def test_f3_exec_flags_of_rg_and_ugrep_turn_masking_off(self):
        self.refused("rg --hostname-bin x 'x{1,2}' %s" % self.file)        # also refused by the rg program rule, so no text pinned
        self.refused("grep --filter=x -E 'x{1,2}' %s" % self.file, BRACE)
        for opt in ("--pager=x", "--view=x", "--format-open=x", "--config=x", "--save-config=x", "-Q"):
            self.refused("grep %s -E 'x{1,2}' %s" % (opt, self.file), BRACE)

    def test_f6_diff_with_l_or_paginate_turns_masking_off(self):
        self.refused("diff -l a b | grep -E 'x{1,2}'", BRACE)
        self.refused("diff -ul a b | grep -E 'x{1,2}'", BRACE)
        self.refused("diff --paginate a b | grep -E 'x{1,2}'", BRACE)

    def test_f15b_rg_search_zip_turns_masking_off(self):
        for opt in ("-z", "-nz", "--search-zip", "--search-z", "--search-zip=x"):
            self.refused("rg %s 'x{1,2}' %s" % (opt, self.file), BRACE)       # a comma in the brace is what the brace rule judges: {2} alone is not one

    def test_f15c_diff_paginate_prefixes_and_q_clusters_turn_masking_off(self):
        for opt in ("--p", "--pag", "--paginate"):
            self.refused("diff %s a b | grep -E 'x{1,2}'" % opt, BRACE)
        self.refused("grep -nQ -E 'x{1,2}' %s" % self.file, BRACE)
        self.refused("grep -Q -E 'x{1,2}' %s" % self.file, BRACE)

    def test_f15c_allowed_rows_that_discriminate(self):
        """Each row holds a comma brace: it passes only because masking is on (a forced-off `_masking_allowed` fails every one)."""
        for line in ("rg -nS 'x{1,2}' %s" % self.file, "rg -r y 'x{1,2}' %s" % self.file,
                     "tail -n 5 %s | grep -E 'x{1,2}'" % self.file, "diff -u %s %s | grep -E 'x{1,2}'" % (self.file, self.file),
                     "sort -- %s | grep -E 'x{1,2}'" % self.file):
            with self.subTest(line=line.split(self.file)[0]):
                self.allowed(line)

    def test_f15_plain_readers_still_pass(self):
        self.allowed("rg -nS 'x{2}' %s" % self.file)
        self.allowed("rg -r y 'x{2}' %s" % self.file)
        self.allowed("tail -n 5 %s | grep -E 'x{2}'" % self.file)
        self.allowed("diff -u %s %s | grep -E 'x{2}'" % (self.file, self.file))

    def test_a_plain_sort_still_passes(self):
        self.allowed("sort -n -k2,2 %s | grep -E 'x{1,2}'" % self.file)

    def test_refuse_an_unbalanced_quote_with_braces(self):
        self.refused("grep -E 'x{1,2} %s" % self.file)
        self.refused("cat %s | jq \"{a: .b, c: .d} " % self.file)

    def test_refuse_a_real_merge_beside_quoted_braces(self):
        self.refused("%s merge feature && grep -E 'x{1,2}' %s" % (G, self.file))
        self.refused("grep -E 'x{1,2}' %s && %s pull origin main" % (self.file, G))
        self.refused("%s pull origin main; cat %s | jq '{a: .b, c: .d}'" % (G, self.file))

    def test_refuse_a_checkout_of_a_plugin_path_beside_quoted_braces(self):
        self.refused("%s checkout feature -- plugin/scripts/a.py && grep -E 'x{1,2}' %s" % (G, self.file))
        self.refused("grep -E 'x{1,2}' %s; %s checkout main -- plugin/" % (self.file, G))

    def test_refuse_quoted_text_that_holds_a_merge_command(self):
        self.refused("grep -E '{a,b}%s merge feature' %s" % (G, self.file), BRACE)
        self.refused("cat %s | jq '{a: \"%s checkout x\", b: 1}'" % (self.file, G), BRACE)

    def test_refuse_a_brace_expansion_outside_quotes(self):
        self.refused("grep -E 'x' %s{a,b}" % self.file)


if __name__ == "__main__":
    unittest.main()
