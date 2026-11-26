"""R-1360: the read-loop guard (studiolib._var_args_ok, R-1199 / R-1217) refuses a loop whose body can set the loop variable again. It
reads the builtin words (read, export, declare, local ...) only as shell words, so a folder name that holds such a word (a checkout
called `...-read-rows`) no longer refuses a plain read loop. A real builtin on the line stays refused."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import studiolib as sl  # noqa: E402

PATHS = ("/Users/x/wt/r1360b-read-rows/plugin/bin/budgit", "/Users/x/local/plugin/bin/budgit", "/Users/x/export-wt/plugin/bin/budgit",
         "/Users/x/declare/plugin/bin/budgit", "/Users/x/readonly/unset_me/plugin/bin/budgit", "/Users/x/a.read/b/budgit")


def ok(cli, tail, args):
    """The line `for i in R-1 R-2; do <cli> <tail> | ...` through the guard's own check; tokens are the call words."""
    text = "for i in R-1 R-2; do %s %s" % (cli, tail)
    return sl._var_args_ok("ledger show" if args[0] == "show" else "ledger list", args[1:], [cli], 0, text)


class LoopWords(unittest.TestCase):
    def test_a_budgit_path_with_a_builtin_word_in_a_folder_passes_for_the_four_loop_forms(self):
        for cli in PATHS:
            self.assertTrue(ok(cli, "ledger show $i | head -5; done", ("show", "$i")), cli)
            self.assertTrue(ok(cli, "ledger show $i; done", ("show", "$i")), cli)
            self.assertTrue(ok(cli, "ledger list --limit 5 $i | head -5; done", ("list", "--limit", "5", "$i")), cli)
            self.assertTrue(ok(cli, "ledger show $i | head -5; done | sort -n", ("show", "$i")), cli)

    def test_a_real_builtin_that_can_set_the_loop_variable_stays_refused(self):
        cli = PATHS[0]
        for body in ("read i %s ledger show $i; done", "export i=x %s ledger show $i; done", "do read i %s ledger show $i; done",
                     "&& read i %s ledger show $i; done", "| read i %s ledger show $i; done", "'read' i %s ledger show $i; done",
                     "\\read i %s ledger show $i; done", "declare i=1 %s ledger show $i; done", "local i %s ledger show $i; done"):
            text = "for i in R-1 R-2; do " + (body % cli)
            self.assertFalse(sl._var_args_ok("ledger show", ["$i"], [cli], 0, text), text)

    def test_a_read_that_names_the_loop_variable_by_value_stays_refused_for_a_helper(self):
        cli = PATHS[0]                                    # `read $i` can set the variable that $i's value names
        for body in ("read $i; %s ledger show $i; done", "read ${i}; %s ledger show $i; done", "do read $i %s ledger show $i; done",
                     "read -r $i; %s ledger show $i; done"):
            text = "for i in R-1 R-2; do " + (body % cli)
            self.assertFalse(sl._var_args_ok("ledger show", ["$i"], [cli], 0, text), text)

    def test_a_builtin_word_after_a_separator_or_a_quote_is_still_a_shell_word(self):
        pat = sl._LOOP_BUILTIN
        for text in ("do read -r i x", "a; read i", "a && read i", "a | read i", "(read i", "`read i", "\"read\" i", "'export' i", "\\local i",
                     "read i", "do printf -v i x", "a\tdeclare i"):
            self.assertIsNotNone(pat.search(text), text)
        for text in ("/x/read-rows/budgit show i", "/x/local/b show i", "a/export/b i", "x=read i", "name.read i", "dir-read i"):
            self.assertIsNone(pat.search(text), text)

    def test_a_builtin_after_a_case_pattern_paren_stays_refused(self):
        self.assertIsNotNone(sl._LOOP_BUILTIN.search("1)read i"))
        cli = PATHS[0]
        text = "for i in a b; do case 1 in 1)read i;; esac; %s ledger show $i; done" % cli
        self.assertFalse(sl._var_args_ok("ledger show", ["$i"], [cli], 0, text), text)


if __name__ == "__main__":
    unittest.main()
