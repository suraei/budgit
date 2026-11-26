"""R-335 class 1 (shell part): a name the shell builds (a variable assigned in the same command line, a command
substitution, eval, a shell fed a program) in a writer asks without the standing yes; the same commands with plain names do not.
Also `tree -R`, which writes a file into every folder, is not a reader."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import GuardCase  # noqa: E402

BUILT = (
    "x=da; rm ${x}ta.json",
    "x=da; rm \"${x}ta.json\"",
    "x=da; echo hi > ${x}ta.json",
    "x=da && cp docs/a.md ${x}ta.json",
    "export x=da; mv ${x}ta.json docs/z",
    "for f in data; do rm $f.json; done",
    "rm \"$(printf 'da\\x74a.json')\"",
    "rm `echo da``echo ta.json`",
    "cat docs/a.md | tee $(echo data.json)",
    "x=da; sed -i s/a/b/ ${x}ta.json",
    "eval \"$(echo cm0gZGF0YS5qc29u | base64 -d)\"",
    "echo cm0gZGF0YS5qc29u | base64 -d | sh",
    "echo cm0gZGF0YS5qc29u | base64 -d | bash",
    "bash <(echo rm data.json)",
    "source <(echo rm data.json)",
    "rm $(cat in.txt)",
    "rm $(<in.txt)",
    "echo x > $(printf %b '\\x64ata.json')",
    "echo x > $(printf %b 'da\\x74a.json')",
    "rm {d,x}ata.json",
    "echo x > {d,x}ata.json",
    "rm data{.json,.bak}",
    "rm $'\\x64ata.json'",
    "echo x > $'\\144ata.json'",
    "printf x > $'\\x64ata.json'",
    "cat in.txt | xargs rm",
    "xargs -a in.txt rm",
    "echo data.json | xargs -I{} mv {} /tmp",
    "rm $HOME/.cache/x",
    "touch $NAME",
    "ls | xargs rm",
    'ls | while read f; do rm "$f"; done',
)

PLAIN = (
    "x=docs; ls $x",
    "x=docs; cat $x/a.md",
    "rm docs/old.txt",
    "cp docs/a.md docs/b.md",
    "echo hi > docs/new.md",
    "bash docs/run.sh",
    "bash -c 'echo hi'",
    "sh --version",
    "echo $(date) > docs/stamp.txt",
    "git commit -m \"$(cat docs/a.md)\" -- docs/a.md",
    "cat $(git ls-files docs)",
    "ls $(pwd)",
    "echo {a,b} > docs/ab.txt",
    "tar czf /tmp/o.tgz docs",
    "tar cf /tmp/o.tar docs",
    "tar tf /tmp/o.tar",
    "tar --create --file /tmp/o.tar docs",
    "tar -czf /tmp/o.tgz docs",
)


class ShellBuiltNameTest(GuardCase):
    def test_a_name_the_shell_builds_in_a_writer_asks(self):
        for cmd in BUILT:
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def test_plain_names_and_unrelated_variables_are_left_alone(self):
        for cmd in PLAIN:
            got = self.d(cmd)
            self.assertTrue(got is None or "built by the shell" not in got[1], (cmd, got))
            if cmd != "echo $(date) > docs/stamp.txt":
                self.assertIsNone(got, cmd)

    def test_under_the_standing_yes_the_built_names_pass(self):
        self.assertTrue(self.grant())
        for cmd in ("x=da; rm ${x}ta.json", "echo cm0gZGF0YS5qc29u | base64 -d | sh", "eval \"$(echo hi)\"",
                    "rm \"$(printf 'da\\x74a.json')\""):
            self.assertIsNone(self.d(cmd), cmd)


class TarModeTest(GuardCase):
    def test_extracting_asks_at_the_project_root_but_creating_and_listing_do_not(self):
        for cmd in ("tar xf a.tar", "tar -xzf a.tgz", "tar --extract -f a.tar", "tar xzf a.tgz -C .", "tar -C . -xf a.tar", "unzip a.zip"):
            self.assertEqual(self.d(cmd, cwd=self.root)[0], "ask", cmd)
        for cmd in ("tar czf /tmp/o.tgz docs", "tar tf a.tar", "tar -tzf a.tgz", "tar --list -f a.tar", "tar cf /tmp/o.tar src"):
            self.assertIsNone(self.d(cmd, cwd=self.root), cmd)


class OtherLanguagesTest(GuardCase):
    CODE = (
        "tclsh <<'T'\nset f [open [string cat da ta.json] w]\nputs $f x\nclose $f\nT",
        "echo 'set f [open [string cat da ta.json] w]; puts $f x' | tclsh",
        "Rscript -e 'writeLines(\"x\", paste0(\"da\",\"ta.json\"))'",
        "R -e 'writeLines(\"x\", paste0(\"da\",\"ta.json\"))'",
        "jshell -s <<< 'new java.io.FileWriter(\"da\"+\"ta.json\")'",
        "groovy -e 'new File(\"da\"+\"ta.json\").text = \"x\"'",
        "swift -e 'FileManager.default.createFile(atPath: \"d\"+\"ata.json\", contents: nil)'",
        "emacs --batch --eval '(write-region \"x\" nil (concat \"da\" \"ta.json\"))'",
        "sqlite3 :memory: '.shell rm da'\"ta.json\"",
    )

    def test_these_languages_are_judged_like_the_others(self):
        for cmd in self.CODE:
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def test_plain_use_of_them_is_left_alone(self):
        for cmd in ("Rscript -e 'print(1)'", "R --version", "java -version", "swift --version", "tclsh <<< 'puts hi'",
                    "sqlite3 docs/a.db 'select 1'", "emacs --version", "groovy -e 'println 1'"):
            self.assertIsNone(self.d(cmd), cmd)

    def test_a_java_or_tcl_file_the_session_wrote_is_read_before_it_runs(self):
        path = os.path.join(self.project, "docs", "W.java")
        open(path, "w").write('import java.io.*;\npublic class W { public static void main(String[] a) throws Exception { '
                              'new FileWriter("da" + "ta.json").write("x"); } }\n')
        self.assertEqual(self.d("java docs/W.java")[0], "ask")


class TreeRecursiveTest(GuardCase):
    def test_tree_with_the_recursive_option_is_not_a_reader_even_under_the_yes(self):
        self.assertTrue(self.grant())
        for cmd in ("tree -R -L 1 %s", "tree -R -L 1 -H . %s", "tree -aR %s", "tree -R %s"):
            self.assertEqual(self.d(cmd % self.root)[0], "ask", cmd)
        self.assertEqual(self.d("tree -R -L 1 .")[0], "ask")
        self.assertIsNone(self.d("tree -L 1 %s" % self.root))
        self.assertIsNone(self.d("tree %s" % self.root))


if __name__ == "__main__":
    unittest.main()
