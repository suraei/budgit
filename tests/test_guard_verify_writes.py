"""R-386: the Bash half of the verify-forgery guard blocks a WRITE into a verify folder, not every command that mentions one.
A command passes only when it is read by a plain reader (short head list, literal write targets) and no target is inside a
verify folder, the seals file, a data folder, or a folder that holds one. Anything the reader cannot settle keeps the old deny.
The differential half: every spelling of "write into the verify folder" that the guard denied before R-386 is still denied
(run against the commit before R-386: 1597 generated commands, 69 newly allowed, all of them reads or writes elsewhere)."""
import os
import unittest

from helpers import StudioTestCase
import studiolib as sl


class VerifyWritesTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        for d in (".budgit/verify", ".studio/verify", "sub", "other"):
            os.makedirs(os.path.join(self.project, d), exist_ok=True)
        for link, target in (("lnk", ".budgit/verify"), ("lnk2", ".budgit"), ("lnk3", "other")):
            os.symlink(target, os.path.join(self.project, link))

    def d(self, command):
        return sl.verify_forgery_decision("Bash", {"command": command}, "general-purpose", self.project)

    MENTION = "ls .budgit/verify; "           # a command that names the folder is the guard's business; the write may be spelled any way

    def assertDenied(self, command):
        got = self.d(command if command.startswith("ls .budgit/verify") else self.MENTION + command)
        self.assertIsNotNone(got, command)
        self.assertEqual(got[0], "deny", command)

    # every spelling of a path inside (or taking in) a verify folder
    INSIDE = (".budgit/verify/R-1.md", ".studio/verify/R-1.md", "./.budgit/verify/R-1.md", ".budgit//verify/R-1.md",
              ".budgit/x/../verify/R-1.md", '".budgit"/verify/R-1.md', "'.bud'git/verify/R-1.md", ".BUDGIT/Verify/R-1.md",
              ".budgit/verify", ".budgit/verify/", ".budgit", ".studio", "lnk/R-1.md", "lnk2/verify/R-1.md",
              ".budgit/ver*/R-1.md", ".budgit/verif?/x", ".budg*/verify/x", ".bud\\git/verify/R-1.md", "sub/../.budgit/verify/R-1.md",
              ".budgit/verify/../verify/R-1.md", "$X/R-1.md", "$(echo .budgit)/verify/R-1.md", "{.budgit,x}/verify/R-1.md")
    # commands where `{p}` is a write target
    WRITES = ("echo x > {p}", "echo x >> {p}", "echo x >{p}", "echo x 1>{p}", "echo x &>{p}", "echo x | tee {p}", "echo x | tee -a {p}",
              "cp a {p}", "cp -t {p} a", "mv a {p}", "mv {p} a", "touch {p}", "truncate -s 0 {p}", "install a {p}", "ln -s a {p}",
              "ln -s {p} a", "rm {p}", "rm -rf {p}", "cat a > {p}", "printf x > {p}; ls", "ls && echo x > {p}",
              "echo x > /dev/null; echo y > {p}", "FOO=1 echo x > {p}", "echo x 2>&1 > {p}", "echo x >| {p}", "ls > {p}",
              "dd if=a of={p}", "sed -i s/a/b/ {p}", "python3 -c 'open(1)' > {p}", "tar xf a.tar -C {p}", "echo x > a; cp a {p}",
              "echo x > {p} # note")

    def test_every_write_into_a_verify_folder_stays_denied(self):
        for tmpl in self.WRITES:
            for p in self.INSIDE:
                self.assertDenied(tmpl.format(p=p))

    def test_forms_the_reader_cannot_settle_stay_denied(self):
        for cmd in ("cd .budgit && echo x > verify/R-1.md", "cd sub && ls ../.budgit/verify > o.txt", "echo x > $F",
                    "echo $(cat .budgit/verify/R-1.md) > o.txt", "tee .budgit/verify/R-1.md <<EOF\nx\nEOF", "cat > .budgit/verify/R-1.md <<'EOF'\nx\nEOF", "(echo x > .budgit/verify/R-1.md)",
                    "{ echo x; } > .budgit/verify/R-1.md", "echo x>.budgit/verify/R-1.md", 'echo x >".budgit/verify/R-1.md"',
                    "echo x >'.budgit'/verify/R-1.md", "echo x 2>.budgit/verify/R-1.md", "echo x >&.budgit/verify/R-1.md",
                    "echo x >& .budgit/verify/R-1.md", 'echo "x" > .budgit/ver\\\nify/R-1.md', "tee .budgit/verify/R-1.md < a",
                    "cat a | tee -a ./.budgit/verify/R-1.md >/dev/null", "exec 3>.budgit/verify/R-1.md",
                    "echo x | tee  '.budgit/verify/R-1.md'", "echo hi;echo x>>.budgit/verify/R-1.md", "ls .budgit/verify > $OUT",
                    "ls .budgit/verify > out.*", "printf 'a; b' > .budgit/verify/R-1.md", "echo 'unbalanced > .budgit/verify/R-1.md",
                    "rg --pre 'tee .budgit/verify/R-1.md' x .", "awk '{print > \".budgit/verify/R-1.md\"}' a"):
            self.assertDenied(cmd)

    def test_review_findings_stay_denied(self):
        """security review of R-386: GNU `-tDIR`, a relative symlink target, `~+` / `~-`, a head that is not the program it names."""
        for cmd in ("cp -t.budgit/verify forged.md", "install -Dt.budgit/verify f", "mv -t.budgit/verify f", "ln -sft.budgit/verify f",
                    "cp -rt.studio/verify f", "ln -s verify .budgit/lnk9", "ln -s ../verify .budgit/sub/lnk", "ln -s ../.budgit lnk9",
                    "cp x ~+/verify/R-1.md", "echo PASS > ~-/verify/R-1.md", "cp x ~root/verify/R-1.md",
                    "PATH=sub tee .budgit/verify/R-1.md", "LD_PRELOAD=x cp a .budgit/verify/R-1.md", "./tee .budgit/verify/R-1.md",
                    "/tmp/evil/cp a .budgit/verify/R-1.md", "sub/touch .budgit/verify/R-1.md"):
            self.assertDenied(cmd)

    def test_a_write_to_the_working_folder_or_above_it_takes_the_reports_along(self):
        for p in (".", "..", "./", "sub/.."):
            for tmpl in ("rm -rf {p}", "mv {p} ../old", "cp -r a {p}", "install a {p}", "ln -s a {p}"):
                self.assertDenied(tmpl.format(p=p))

    def test_a_folder_that_holds_a_data_folder_is_not_a_plain_target(self):
        os.makedirs(os.path.join(self.project, "sub", ".budgit", "verify"))
        self.assertDenied("rm -rf sub")
        self.assertDenied("mv sub elsewhere")

    PASSES = (
        # reading and listing the folder, with the output going elsewhere
        "ls .budgit/verify > out.txt", "ls .studio/verify >> sub/out.md", "cat .budgit/verify/R-1.md | grep PASS > notes.txt",
        "grep -n PASS .budgit/verify/R-1.md > /tmp/out.md", "grep -c '>' .budgit/verify/R-1.md", "diff .budgit/verify/R-1.md a > d.txt",
        "ls .budgit/verify 2>&1 | head", "ls .budgit/verify 2>/dev/null > list.txt", "wc -l .budgit/verify/*.md > counts.txt",
        "find .budgit/verify -name '*.md' > found.txt", "tail -n 3 .budgit/verify/R-1.md &> tail.txt",
        # a path that is only text
        "printf 'saw .budgit/verify/R-1.md\\n' >> notes.txt", "echo 'report .budgit/verify/R-340.md is PASS' > sub/log.md",
        "echo \"see .studio/verify/R-1.md\" | tee notes.txt", "echo 'x > y' .budgit/verify/R-1.md",
        # writes that land elsewhere, even through a link to somewhere else
        "ls .budgit/verify; touch sub/marker", "ls .budgit/verify && cp a other/b", "cat .budgit/verify/R-1.md > lnk3/x.md",
        "ls .budgit/verify > ./notes.txt", "LC_ALL=C echo .budgit/verify > notes.txt",
        "/bin/ls .budgit/verify > out.txt", "ls .budgit/verify; cp -t other a",
    )

    def test_a_read_or_a_text_mention_with_its_output_elsewhere_passes(self):
        for cmd in self.PASSES:
            self.assertIsNone(self.d(cmd), cmd)

    def test_the_verifier_itself_is_never_asked(self):
        got = sl.verify_forgery_decision("Bash", {"command": "echo x > .budgit/verify/R-1.md"}, "studio:verifier", self.project)
        self.assertIsNone(got)

    def test_second_review_payloads_stay_denied(self):
        # R-386 re-review: zsh clobber redirects, extended-glob characters, a link made in the same line, writers the pattern missed
        for cmd in ("echo PASS >! .budgit/verify/R-1.md", "echo PASS >>! .budgit/verify/R-1.md", "echo PASS &>! .budgit/verify/R-1.md",
                    "echo PASS > .budgit/verif#y/R-1.md", "echo PASS > .budgit/^x/verify/R-1.md",
                    "mkdir -p .budgit/x; ln -s ../verify .budgit/x; echo PASS > .budgit/x/verify/R-1.md",
                    "ln -s ../verify -t .budgit/x", "cp -s .budgit/verify x", "cp -sf .budgit/verify x", "cp --symbolic-link .budgit/verify x",
                    "path=/nonexistent tee .budgit/verify/R-1.md", "CDPATH=x tee .budgit/verify/R-1.md",
                    "sed -Ei 's/FAIL/PASS/' .budgit/verify/R-1.md", "sed --in-place s/a/b/ .budgit/verify/R-1.md",
                    "sed -e x -i s/a/b/ .budgit/verify/R-1.md", "sort -o .budgit/verify/R-1.md a", "base64 -D -i a -o .budgit/verify/R-1.md",
                    "awk -i inplace '{print}' .budgit/verify/R-1.md", "xxd -r a .budgit/verify/R-1.md"):
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_a_reader_given_by_a_local_path_is_not_a_reader(self):
        self.assertFalse(sl._reads_only("./cat approve_prepare"))
        self.assertTrue(sl._reads_only("/bin/cat README.md"))

    def test_third_review_payloads_stay_denied(self):
        # R-386 third review: HOME moved, writers the denylist never named, zsh modules, spellings of the folder without the word
        for cmd in ("HOME=.budgit/verify; echo forged | tee ~/R-1.md", "HOME=.budgit/verify; echo x > ~/R-1.md",
                    "HOME=.budgit/verify; cp x ~/R-1.md", "V=verify; tee .budgit/$V/R-1.md",
                    "sort --output=.budgit/verify/R-1.md f", "sort -ro .budgit/verify/R-1.md f", "sort -o.budgit/verify/R-1.md f",
                    "base64 -D -i f --output .budgit/verify/R-1.md", "gsed -i s/a/b/ .budgit/verify/R-1.md",
                    "gawk -i inplace x .budgit/verify/R-1.md", "gtee .budgit/verify/R-1.md", "sed --i s/a/b/ .budgit/verify/R-1.md",
                    "awk -iinplace x .budgit/verify/R-1.md",
                    "find . -maxdepth 0 -fprintf .budgit/verify/R-1.md 'PASS\\n'", "find . -maxdepth 0 -fprint .budgit/verify/R-1.md",
                    "sed -n 'w .budgit/verify/R-1.md' forged.md", "sed 's/a/b/w .budgit/verify/R-1.md' forged.md",
                    "plutil -convert xml1 -o .budgit/verify/R-1.md f", "iconv -o .budgit/verify/R-1.md f", "curl -so .budgit/verify/R-1.md u",
                    "uniq f .budgit/verify/R-1.md", "split f .budgit/verify/R-1.", "git diff --output=.budgit/verify/R-1.md",
                    "zmodload zsh/mapfile; mapfile[.budgit/verify/R-1.md]=forged",
                    "zmodload zsh/system; sysopen -w -o creat -u 3 .budgit/verify/R-1.md; syswrite -u 3 forged",
                    "zmodload -F zsh/files b:zf_mv; zf_mv a .budgit/verify/R-1.md",
                    "tee .budgit/{verify,x}/R-1.md", "tee .budgit/(verify|x)/R-1.md", "tee .budgit/verif\\y/R-1.md"):
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_plain_reads_of_the_reports_still_pass(self):
        for cmd in ("cat .budgit/verify/R-1.md | sed -n 1,5p", "cat .budgit/verify/R-1.md | sed -n /PASS/p", "ls .budgit/verify | sort",
                    "ls .budgit/verify | sort -r | head -3", "grep -c PASS .budgit/verify/R-1.md; wc -l .budgit/verify/R-1.md",
                    "head -1 .budgit/verify/R-1.md | cut -d' ' -f1", "git log --oneline -- .budgit/verify; ls .budgit/verify"):
            self.assertIsNone(self.d(cmd), cmd)

    def test_fourth_review_payloads_stay_denied_with_no_mention_prefix(self):
        # R-386 fourth review: mention detection itself (case, variables, cd, links, classes), programs run by git/tar/sort/perl env
        here = self.project
        for cmd in ("echo PASS | tee .BUDGIT/{verify,verify}/R-1.md", "V=verify; tee .BUDGIT/$V/R-1.md",
                    "D=.budgit; V=verify; echo PASS > $D/$V/R-1.md", "cd %s/.budgit && cp /tmp/R-1.md verify/" % here,
                    "pushd .budgit && echo PASS > verify/R-1.md", "cp /tmp/R-1.md .[[:alpha:]]udgit/verify/",
                    "ln -s .budgit L; cp /tmp/R-1.md L/verify/", "cp /tmp/R-1.md .budgit/`echo verify`/",
                    "git grep -O'cp /tmp/R-1.md .budgit/verify/ #' -e x", "git fetch --upload-pack='cp /tmp/R-1.md .budgit/verify/ #' .",
                    "git -c core.pager='cp /tmp/R-1.md .budgit/verify/' log -p -- .budgit/verify",
                    "tar xf a.tar -C out --use-compress-program='sh -c \"cp /tmp/R-1.md .budgit/verify/\"'",
                    "rsync -e 'sh -c \"cp /tmp/R-1.md .budgit/verify/\"' .budgit/verify/ out/",
                    "PERL5OPT='-Mbase;system(q(cp),q(/tmp/R-1.md),q(.budgit/verify/))' shasum f; ls .budgit/verify",
                    "TMPDIR=.budgit/verify sort bigfile; ls .budgit/verify",
                    "sort -S 1M --compress-program='cp /tmp/R-1.md .budgit/verify/ #' bigfile",
                    "sort -T .budgit/verify bigfile; ls .budgit/verify",
                    "test 'a[$(cp /tmp/R-1.md .budgit/verify/)]' -eq 0", "printf -v 'a[$(cp x .budgit/verify/R-1.md)]' %s x; ls .budgit/verify"):
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_reads_that_name_a_data_folder_without_the_reports_still_pass(self):
        for cmd in ("python3 -c \"print(open('.budgit/ledger.json'))\"", "echo $(cat .budgit/progress.md)",
                    "budgit ledger verify R-1", "cp -r src/verify out/", "git commit -m 'note on .budgit and verify'"):
            got = self.d(cmd)
            self.assertIsNone(got, cmd)


if __name__ == "__main__":
    unittest.main()
