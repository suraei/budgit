"""R-1294 fix round 3: a work-tree diff with a refused submodule form (--submodule, =log, =diff) asks with the nested-repo rule
when it runs from the project folder with no -C, the same as the -C form. Before, the pre-pass skipped the refused form and the
reader never ran, because that line has no base verdict."""
import unittest

import i18n
import studiolib as sl
import test_r1294_read_exception as base

NESTED = "guard.r1294_nested"
NEEDS = base.NEEDS


class Round3(base.Case):
    def line(self, text):
        return sl.real_data_check("Bash", {"command": text}, self.project, cwd=self.project)[0]

    def test_s1_a_refused_submodule_form_without_c_asks_with_the_nested_rule(self):
        for form in ("--submodule=log", "--submodule", "--submodule=diff"):
            text = "git diff " + form
            got = self.line(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got[0], "ask", text)
            self.assertTrue(got[1].startswith(i18n.t(NESTED)), "%s -> %r" % (text, got[1]))

    def test_s1_a_refused_form_still_asks_with_the_flag_or_a_path(self):
        for text in ("git diff --submodule=log --ignore-submodules=all", "git diff --submodule=log -- docs",
                     "git diff --ignore-submodules=all --submodule"):
            got = self.line(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got[0], "ask", text)

    def test_s2_the_c_forms_keep_asking_with_the_nested_rule(self):
        for form in ("--submodule=log", "--submodule", "--submodule=diff"):
            text = "git -C %s diff %s" % (self.root, form)
            got = self.line(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got[0], "ask", text)
            self.assertTrue(got[1].startswith(i18n.t(NESTED)), "%s -> %r" % (text, got[1]))

    def test_s3_a_line_with_no_submodule_flag_keeps_its_verdict(self):
        got = self.line("git diff")
        self.assertIsNotNone(got)
        self.assertTrue(got[1].startswith(i18n.t(NEEDS)), got[1])
        self.assertIsNone(self.line("git diff --ignore-submodules=all"))
        self.assertIsNone(self.line("git diff --submodule=short --ignore-submodules=all"))
        self.assertIsNone(self.line("git diff --cached"))


if __name__ == "__main__":
    unittest.main()
