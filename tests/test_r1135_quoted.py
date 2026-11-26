"""R-1135: guard false positives. A `;`, `|`, `&` or line break INSIDE the quoted note of `budgit ledger outcome|add|retext|finding`
is part of the sentence: `_base_segments` must not cut the note there and read a path quoted in it as a protected word. A real
write target (redirect, cp, a pipe into a writer, a substitution, a path word, `--file`) still asks, and a command that is not
that one plain call keeps the old cut (a quote can hold a command line for eval or ssh).

Diagnosis, recorded for the card (synthetic project, protected_paths data/real/**):
  (b) `budgit ledger outcome R-12 "moved data/real/x.csv parsing to tools/x.py"` already passes both guards; only a sentence with
      a `;` / `|` / `&` inside the quotes asks, from the real-data guard, layer "protected word": _base_segments (studiolib)
      cut the line at every `;` `|` `&` newline even inside quotes (_SEGMENT_SPLIT) and read `data/real/x.csv` in one piece as a
      protected word. The own CLI by absolute path (or a PATH entry that is the plugin's own file) was cleared afterwards by
      _budgit_exception; a bare `budgit` that _is_own_budgit does not accept (a link, a missing name) was not.
      A backtick in the double quotes is a real substitution: _strict_extras asks, and keeps asking.
  (a) `budgit denies --project <root> --since 7d`: the same layer; `--project <root>` is a protected word (the folder holds
      protected data) and only _budgit_exception clears it, only for a program _is_own_budgit accepts. Not changed here."""
import os
import shutil
import unittest

from test_guard_trust import GuardCase, CLI
import studiolib as sl

NOTE = ' ledger outcome R-12 "ran cat data/real/x.csv | head; ok"'
NBSP, VTAB = chr(0xA0), chr(0x0B)          # hidden characters are built here, never written as the literal character


class QuotedNoteTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.bindir = os.path.join(self.tmp, "pathbin")
        os.makedirs(self.bindir)
        self.old_path = os.environ.get("PATH")
        self.addCleanup(self.restore_path)

    def restore_path(self):
        if self.old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self.old_path

    def path_with(self, how):
        """`budgit` as the shell finds it: `own` = the plugin's bin folder on PATH, `link` = a symlink to the CLI, `copy` = a
        look-alike copy, `none` = no `budgit` on PATH at all."""
        target = os.path.join(self.bindir, "budgit")
        if os.path.lexists(target):
            os.remove(target)
        if how == "own":
            os.environ["PATH"] = os.path.dirname(CLI) + os.pathsep + (self.old_path or os.defpath)
        elif how == "none":
            os.environ["PATH"] = self.bindir
        else:
            os.symlink(CLI, target) if how == "link" else shutil.copy(CLI, target)
            os.environ["PATH"] = self.bindir + os.pathsep + (self.old_path or os.defpath)
        return "budgit"

    def test_a_quoted_semicolon_or_pipe_in_the_note_is_not_a_cut(self):
        for how in ("link",):          # R-1294 slice 2: only the own CLI (here a PATH link to it) reads the note as one word; none / copy now ask
            cli = self.path_with(how)
            self.passes(cli + NOTE,
                        cli + ' ledger outcome R-12 "wrote data/real/x.csv; see ~/.studio/ledger.md and .studio/ledger.json"',
                        cli + ' ledger add --title "fix data/real/x.csv; later" --text "a | b data/real/y.csv" --appetite small --area main',
                        cli + ' ledger retext R-12 "see data/real/x.csv && data/real/y.csv are quoted"',
                        cli + ' ledger finding add R-12 "read data/real/x.csv; looks fine"',
                        cli + ' ledger outcome R-12 "line one data/real/x.csv\nline two"')

    def test_the_own_cli_found_on_path_or_by_absolute_path_still_passes(self):
        self.path_with("own")
        self.passes("budgit" + NOTE, CLI + NOTE, CLI + ' ledger outcome R-12 "moved data/real/x.csv parsing to tools/x.py"')

    def test_a_look_alike_reads_the_note_as_it_reads_a_plain_note_on_main(self):
        cli = self.path_with("copy")
        self.passes(cli + ' ledger outcome R-12 "moved data/real/x.csv parsing to tools/x.py"')

    def test_a_redirect_after_the_note_still_asks(self):
        for how in ("none", "copy"):
            cli = self.path_with(how)
            self.asks(cli + ' ledger outcome R-12 "x" > data/real/y.csv',
                      cli + ' ledger outcome R-12 "see data/real/x.csv; ok" > data/real/y.csv',
                      cli + ' ledger outcome R-12 "ok" >> data/real/y.csv')
        self.asks(CLI + ' ledger outcome R-12 "x" > data/real/y.csv')

    def test_a_pipe_into_a_writer_still_asks(self):
        cli = self.path_with("none")
        self.asks(cli + ' ledger outcome R-12 "ok; data/real/x.csv" | tee data/real/y.csv')

    def test_an_unquoted_semicolon_then_a_copy_onto_protected_data_still_asks(self):
        cli = self.path_with("none")
        self.asks("cp a data/real/y.csv",
                  cli + ' ledger outcome R-12 "a; b"; cp a data/real/y.csv',
                  cli + ' ledger outcome R-12 "a; b" && cp a data/real/y.csv',
                  cli + ' ledger outcome R-12 "a; b"\ncp a data/real/y.csv')

    def test_a_substitution_in_the_note_still_asks(self):
        cli = self.path_with("none")
        self.asks(cli + ' ledger outcome R-12 "tick $(cat data/real/x.csv; true)"',
                  cli + ' ledger outcome R-12 "tick `cat data/real/x.csv`; done"',
                  cli + ' ledger outcome R-12 "see <(cat data/real/x.csv); ok"')

    def test_a_protected_path_as_a_word_of_its_own_still_asks(self):
        cli = self.path_with("none")
        self.asks(cli + " ledger outcome R-12 data/real/x.csv; true",
                  cli + ' ledger add --file data/real/x.csv --title "a; b"')

    def test_a_note_that_starts_with_the_protected_path_asks_as_on_main(self):
        cli = self.path_with("none")
        self.asks(cli + ' ledger retext R-12 "data/real/x.csv && data/real/y.csv are quoted"')

    def test_a_protected_path_with_a_space_in_it_asks_as_on_main(self):
        cli = self.path_with("none")
        self.asks(cli + ' ledger outcome R-1 "data/real/new "',
                  cli + ' ledger outcome R-1 "data/real/my file.csv"',
                  cli + ' ledger outcome R-1 "data/real/my%sfile.csv"' % NBSP,
                  cli + ' ledger outcome R-1 "data/real/my%sfile.csv"' % VTAB,
                  cli + ' ledger outcome R-1 data/real/my\\ file.csv',
                  cli + ' ledger add --title "data/real/my file.csv"',
                  cli + ' ledger finding add R-1 "data/real/new "')

    def test_a_link_named_budgit_to_a_writer_asks_for_a_protected_path_with_a_space(self):
        lb = os.path.join(self.tmp, "lb")
        os.makedirs(lb)
        link = os.path.join(lb, "budgit")
        os.symlink("/usr/bin/touch", link)                # never executed: only the guard verdict is read
        os.environ["PATH"] = lb + os.pathsep + (self.old_path or os.defpath)
        self.asks(link + ' ledger outcome R-1 "data/real/new "', link + ' ledger outcome R-1 "data/real/my file.csv"',
                  "budgit" + ' ledger outcome R-1 "data/real/new "')

    def test_a_quote_that_holds_a_command_line_is_still_cut(self):
        self.path_with("none")
        self.asks('eval "echo ok; cp a data/real/y.csv"', 'ssh host "echo ok; cp a data/real/y.csv"')

    def test_other_ledger_subcommands_keep_the_old_cut(self):
        cli = self.path_with("none")
        self.asks(cli + ' ledger export "ran cat data/real/x.csv | head; ok"')

    def test_the_home_guard_still_passes_the_note(self):
        cli = self.path_with("none")
        got = sl.home_write_check("Bash", {"command": cli + ' ledger outcome R-12 "see ~/.studio/x; data/real/x.csv"'}, self.project)[0]
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()
