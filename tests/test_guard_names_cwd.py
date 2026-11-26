"""R-335 class 1 follow-ups: a variable followed by a quote or backslash, names read at run time (`while read`), inline
programs whose file-system call is not on the verb list, and the folder a command line has moved to (`cd ..`, $PWD)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import GuardCase  # noqa: E402

QUOTED_VARIABLE = (
    'x=da; rm $x"ta.json"',
    'x=da; rm "$x"ta.json',
    "x=da; rm $x'ta.json'",
    "x=da; rm $x\\ta.json",
    'x=d; rm $x"ata.json"',
    'for n in da; do rm $n"ta.json"; done',
    'x=da; mv a.txt $x"ta.json"',
    'x=da; cp /dev/null $x"ta.json"',
    'x=da; ln -sf a.txt $x"ta.json"',
    'x=da; truncate -s0 $x"ta.json"',
    'x=da; tee $x"ta.json" < docs/a.md',
    'x=da; echo hi > $x"ta.json"',
    'x=da; : > $x"ta.json"',
)

READ_AT_RUN_TIME = (
    'ls | while read f; do rm "$f"; done',
    'while read f; do rm "$f"; done < list.txt',
    'ls | while IFS= read -r f; do rm "$f"; done',
    'until ! read f; do rm "$f"; done < list.txt',
)

INLINE = (
    "ruby -e 'File.delete(\"da\"+\"ta.json\")'",
    "ruby -e 'File.delete(%w[da ta.json].join)'",
    "deno eval 'Deno.removeSync(\"da\"+\"ta.json\")'",
    "python3 -c \"import os; os.replace('x','d' 'ata.json')\"",
    "python3 -c \"import os; os.symlink('x','d' 'ata.json')\"",
    "node -e \"require('child_process').execSync('r'+'m d'+'ata.json')\"",
    "node -e \"require('fs').rmSync(['da','ta.json'].join(''))\"",
    "node -e \"require('fs').rmSync('.',{recursive:true})\"",
    "node --print \"require('fs').unlinkSync('da'+'ta.json')\"",
    "julia -e 'rm(\"da\"*\"ta.json\")'",
    "elixir -e 'File.rm(\"da\" <> \"ta.json\")'",
    "kotlin -e 'java.io.File(\"da\"+\"ta.json\").delete()'",
    "scala -e 'new java.io.File(\"da\"+\"ta.json\").delete()'",
    "sqlite3 db \"select writefile('da'||'ta.json','x')\"",
    "R -e 'unlink(paste0(\"da\",\"ta.json\"))'",
    "awk 'BEGIN { while ((\"ls\" | getline f) > 0) system(\"rm \" f) }'",
    "python3 -c \"import shutil,os; shutil.rmtree(os.getcwd())\"",
)

PLAIN = (
    'for f in build/*; do rm -rf build; done',
    'rm -rf build',
    'rm build/o',
    'x=docs; ls "$x"',
    'cp docs/a.md ./b.md',
    "python3 -c \"print('a'+'b')\"",
    "python3 -c \"import os; print(os.path.join('a','b'))\"",
    "node -e \"console.log([1,2].join(','))\"",
    "awk '{print $1}' docs/a.md",
    'rm -rf docs/tmp',
    'cd docs && rm -rf tmp',
)

CWD = (
    "cd .. && rm -rf %(name)s",
    "(cd .. && rm -rf %(name)s)",
    "pushd .. && rm -rf %(name)s",
    "cd %(root)s/.. && rm -rf %(name)s",
    "rm -rf $PWD",
    'rm -rf "$PWD"',
    "rm -rf $PWD/*",
    "rm -rf $PWD/.",
    "rm -rf $PWD/..",
    'rm -rf "$PWD/"',
    "rm -rf ${PWD}",
    "rm -rf ~+",
    "rm -rf $OLDPWD",
)


# One table for the whole class: a command that can write or delete, whose target is not a literal the guard resolves.
# Every row must ask without the standing yes, and pass with it.
UNRESOLVED_TARGET = (
    "rm $UNSET_NAME", 'rm "$UNSET_NAME/x"', "rm $HOME/.cache/x", "cp docs/a.md $TARGET", "mv docs/a.md ${DEST}/b.md",
    "echo hi > $OUT", "echo hi >> \"$OUT\"", "tee $OUT < docs/a.md", "touch $NAME", "mkdir $DIR", "chmod 600 $F",
    "truncate -s0 $F", "dd if=/dev/zero of=$F count=1", "install -m 644 docs/a.md $D", "rsync docs/a.md $D/", "ln -sf docs/a.md $L",
    "sed -i s/a/b/ $F", "rm $(ls)", "rm `ls`", "rm $(cat in.txt)", "cp docs/a.md $(pwd)/b.md", "rm {a,b}.json", "rm $'\\x61'",
    "cat in.txt | xargs rm", "cat in.txt | xargs -n1 sh -c 'rm \"$0\"'", "find docs -name '*.tmp' | xargs -I{} mv {} /tmp",
    "xargs -a in.txt rm", 'ls | while read f; do rm "$f"; done', 'for f in docs/*; do rm "$f"; done',
    "eval rm x", "echo rm x | sh", "bash <(echo rm x)", "cd $UNSET_DIR && rm x",
    "cd - && rm x", "cd $(mktemp -d) && rm x", "rm -rf $PWD", "cd .. && rm -rf %(name)s",
    "tar xf a.tar -C $D", "unzip a.zip -d $D", "tar xf $A",
    "python3 -c \"open(input(),'w').write('x')\"", "python3 -c \"import os; os.remove(open('l').read())\"",
    "node -e \"require('fs').writeFileSync(process.argv[1],'x')\" $F", "ruby -e 'File.write(ARGV[0],1)' $F",
    "perl -e 'unlink @ARGV' $F", "awk 'BEGIN{print 1 > ARGV[1]}' $F", "php -r 'unlink($argv[1]);' $F",
)

# R-418: a variable set by a plain assignment on the same line is read, and its value judged like a path written in clear.
RESOLVED = (
    "x=docs; rm $x/old.txt", "x=docs && rm $x/old.txt", 'x=docs; rm "$x/old.txt"', "x=docs; rm ${x}/old.txt", "x=a; rm ${x}b",
    "x=docs; y=$x/old; rm $y.txt", "x=docs; mv $x/a.md $x/b.md", "x=out; mkdir $x; touch $x/f", "x=docs; echo hi > $x/n.md",
    "cd docs; x=a; rm $x", "x=build; rm -rf $x",
)
# ... and every way to make the value something else, or to reach protected data, or the project, still asks.
RESOLVED_BUT_DANGEROUS = (
    "x=data; rm $x.json", 'x=da; rm $x"ta.json"', "x=data.json; rm $x", "x=docs; x=data.json; rm $x", "x=..; rm -rf $x", "x=.; rm -rf $x",
    "x=docs; rm -rf $x/..", "x=/; rm -rf $x", "x=%(root)s; rm -rf $x", "x=docs; for x in data.json; do rm $x; done",
    "x=docs; read x; rm $x", "x=docs; export x=data.json; rm $x", "x=docs; true || x=data.json; rm $x",
    "x=docs; false && x=data.json; rm $x", "x=docs; echo ${x:=data.json}; rm $x", "x=docs; (x=data.json; rm $x)",
    "x=docs\nx=data.json\nrm $x", "x=docs; IFS=/; rm $x", "x=docs | rm $x/a", "x=docs & rm $x/a", "x=$(echo data.json); rm $x",
    "x=docs; declare x=data.json; rm $x", "x=docs; eval x=data.json; rm $x", "x=docs; printf -v x data.json; rm $x",
    "x=docs; unset x; rm $x/old.txt", "x=docs; rm $y/old.txt", "x=docs; rm $x/$y", "x=docs; x+=/../data.json; rm $x",
    "x=docs; cd data; y=real; rm $y/x.csv", "x=docs; { x=data.json; }; rm $x", "x=docs; while read x; do :; done; rm $x",
    "x=1 > $OUT", "x=1 > data.json", "x=docs; y=1 > $x/../data.json",
    "x=~+; rm -rf $x", "x=~+/data.json; rm $x", "x=~+/data/real; rm -rf $x", "x=~-; mv $x /tmp/z",
    "HOME=%(root)s/src; x=~/../data.json; rm $x", "HOME=%(root)s/src; cd ~/../data; rm real/x.csv",
    "export HOME=%(root)s/src; rm ~/../data.json", "CDPATH=%(root)s; cd data; rm real/x.csv", "PWD=/; rm -rf $PWD",
    "x='a b'; rm $x", "x=*.json; rm $x", 'x=a; rm $x"b"',
)


class UnresolvedTargetTest(GuardCase):
    def test_a_variable_set_on_the_same_line_is_read(self):
        for cmd in RESOLVED:
            self.assertIsNone(self.d(cmd, cwd=self.root), cmd)

    def test_a_read_variable_cannot_hide_protected_data_or_the_project(self):
        for cmd in RESOLVED_BUT_DANGEROUS:
            line = cmd % {"root": self.root} if "%(root)s" in cmd else cmd
            got = self.d(line, cwd=self.root)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_every_write_with_a_target_the_guard_cannot_resolve_asks(self):
        for cmd in UNRESOLVED_TARGET:
            line = cmd % {"name": os.path.basename(self.root)} if "%(name)s" in cmd else cmd
            got = self.d(line, cwd=self.root)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_under_the_standing_yes_the_table_passes_except_deleting_the_project(self):
        self.assertTrue(self.grant())
        for cmd in UNRESOLVED_TARGET:
            if "%(name)s" in cmd or "PWD" in cmd or cmd.startswith(("tar ", "unzip ")):     # extracting at the root asks even then
                continue
            self.assertIsNone(self.d(cmd, cwd=self.root), cmd)


class NamesAndFolderTest(GuardCase):
    def test_a_variable_followed_by_a_quote_is_still_read(self):
        for cmd in QUOTED_VARIABLE:
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def test_names_read_at_run_time_by_a_loop_ask(self):
        for cmd in READ_AT_RUN_TIME:
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def test_inline_programs_with_any_file_call_and_a_built_name_ask(self):
        for cmd in INLINE:
            got = self.d(cmd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def test_ordinary_commands_are_left_alone(self):
        for cmd in PLAIN:
            self.assertIsNone(self.d(cmd), cmd)

    def test_deleting_the_project_after_a_cd_or_through_pwd_asks(self):
        for cmd in CWD:
            line = cmd % {"name": os.path.basename(self.root), "root": self.root}
            got = self.d(line, cwd=self.root)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_deleting_inside_the_project_after_a_cd_does_not_ask(self):
        self.assertIsNone(self.d("cd docs && rm -rf tmp", cwd=self.root))
        self.assertIsNone(self.d("cd docs && mv a.md b.md", cwd=self.root))

    def test_tar_extracting_with_absolute_or_rewritten_names_asks_anywhere(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        for cmd in ("tar xPf a.tar", "tar -xPf a.tar", "tar xf a.tar --absolute-names", "tar xf a.tar --transform='s,^,/x/,'",
                    "tar xf a.tar --xform=s,a,b,", "bsdtar -xf a.tar -s ',^,/x/,'"):
            got = self.d(cmd, cwd=elsewhere)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
        for cmd in ("tar xf a.tar", "tar xzf a.tgz --strip-components=1", "tar tf a.tar"):
            self.assertIsNone(self.d(cmd, cwd=elsewhere), cmd)

    def test_a_commit_message_read_from_protected_data_asks(self):
        for cmd in ("git -C %s commit -F data.json", "git -C %s commit --file data.json", "git -C %s commit --file=data.json",
                    "git -C %s commit -t data/real/x.csv", "git -C %s commit --template data/real/x.csv",
                    "git -C %s commit -Fdata.json"):
            got = self.d(cmd % self.root)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
        for cmd in ("git -C %s commit -F docs/a.md", "cat docs/a.md | git -C %s commit -F -", "git -C %s commit -m x"):
            self.assertIsNone(self.d(cmd % self.root), cmd)

    def test_the_in_place_flag_of_perl_ruby_and_awk_is_a_write(self):
        for cmd in ("perl -pi -e 's/a/b/' $F", "perl -i.bak -pe 's/a/b/' docs/a.md", "ruby -pi -e '$_.upcase!' $F",
                    "ruby -i -ne 'print' docs/a.md", "gawk -i inplace '{print}' $F"):
            got = self.d(cmd, cwd=self.root)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
        for cmd in ("perl -ne 'print' docs/a.md", "ruby -e 'puts 1'", "awk '{print $1}' docs/a.md"):
            self.assertIsNone(self.d(cmd, cwd=self.root), cmd)

    def test_a_lone_ampersand_ends_a_command(self):
        for cmd in ("ls & rm data.json", "ls docs & rm data/real/x.csv", "sleep 0 & echo x > data.json", "true & mv data.json /tmp"):
            got = self.d(cmd, cwd=self.root)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
        for cmd in ("ls docs 2>&1 | head -3", "echo hi &> /dev/null", "ls docs >&2", "cat docs/a.md |& head -1", "ls docs & ls src"):
            self.assertIsNone(self.d(cmd, cwd=self.root), cmd)

    def test_under_the_standing_yes_these_pass(self):
        self.assertTrue(self.grant())
        for cmd in ('x=da; rm $x"ta.json"', 'ls | while read f; do rm "$f"; done',
                    "ruby -e 'File.delete(\"da\"+\"ta.json\")'"):
            self.assertIsNone(self.d(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
