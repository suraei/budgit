"""R-384: `budgit tm log TEXT` appends one dated line to the TM's running log. Temp project only."""
import os
import shutil
import re
import subprocess
import sys
import threading

from helpers import BudgitTestCase, ROOT
import studiolib as sl
import tmlog

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} (.*)$")


class TmLogTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.log = os.path.join(sl.studio_dir(self.project), "private", "tm", "status.md")

    def lines(self):
        with open(self.log, encoding="utf-8") as f:
            return f.read().splitlines()

    def cli(self, *args, expect=0, stdin=None):
        r = subprocess.run([sys.executable, BUDGIT, "tm", *args, "--project", self.project], capture_output=True, text=True,
                           input=stdin, env=dict(os.environ, STUDIO_HOME=self.home, BUDGIT_LANG="en"))
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r

    def test_a_line_is_dated_and_appended_after_the_ones_already_there(self):
        self.cli("log", "R-382", "settled", "in", "lane", "3")
        self.cli("log", "second line")
        got = self.lines()
        self.assertEqual([LINE.match(l).group(1) for l in got], ["R-382 settled in lane 3", "second line"])

    def test_the_command_says_where_it_wrote(self):
        self.assertIn(self.log, self.cli("log", "x").stdout)

    def test_newlines_collapse_into_one_line_and_stdin_works(self):
        self.cli("log", "-", stdin="first\nsecond   third\n")
        self.assertEqual([LINE.match(l).group(1) for l in self.lines()], ["first second third"])

    def test_an_empty_text_is_refused_and_writes_nothing(self):
        r = self.cli("log", "-", stdin="  \n", expect=64)
        self.assertIn("nothing to log", r.stdout)
        self.assertFalse(os.path.exists(self.log))

    def test_private_is_git_ignored(self):
        self.cli("log", "x")
        with open(os.path.join(self.project, ".gitignore")) as f:
            self.assertIn("/private/", f.read())

    def test_a_symlinked_log_is_not_followed(self):
        os.makedirs(os.path.dirname(self.log))
        target = os.path.join(self.tmp, "elsewhere.md")
        open(target, "w").close()
        os.symlink(target, self.log)
        self.cli("log", "x", expect=64)
        self.assertEqual(os.path.getsize(target), 0)

    def test_a_symlinked_private_or_tm_folder_is_refused(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        private = os.path.dirname(os.path.dirname(self.log))
        shutil.rmtree(private, ignore_errors=True)               # the ledger writer already left its card folder here
        os.symlink(elsewhere, private)
        self.cli("log", "x", expect=64)
        self.assertEqual(os.listdir(elsewhere), [])
        os.remove(private)
        os.makedirs(private)
        os.symlink(elsewhere, os.path.join(private, "tm"))
        self.cli("log", "x", expect=64)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_text_starting_with_a_dash_goes_after_double_dash(self):
        r = subprocess.run([sys.executable, BUDGIT, "tm", "log", "--project", self.project, "--", "-x done"], capture_output=True,
                           text=True, env=dict(os.environ, STUDIO_HOME=self.home, BUDGIT_LANG="en"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(LINE.match(self.lines()[-1]).group(1), "-x done")

    def test_concurrent_loggers_never_split_a_line(self):
        def one(i):
            tmlog.append(self.project, "line number %03d %s" % (i, "x" * 200))
        threads = [threading.Thread(target=one, args=(i,)) for i in range(40)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        got = self.lines()
        self.assertEqual(len(got), 40)
        self.assertEqual(sorted(LINE.match(l).group(1)[:16] for l in got), sorted("line number %03d " % i for i in range(40)))


if __name__ == "__main__":
    import unittest
    unittest.main()
