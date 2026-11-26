"""R-1195: `ledger flag` and `ledger appetite` take a --file of `id<TAB>value` lines; each line goes through the same
per-item path as the single form, one line per id, a non-zero exit when any line is refused."""
import os
import tempfile

from test_fold import FoldCase


def _rm(path):
    if os.path.exists(path):
        os.remove(path)


class BatchFlagAppetiteTest(FoldCase):
    def file(self, text):
        fd, path = tempfile.mkstemp(suffix=".tsv", dir=self.project)
        self.addCleanup(_rm, path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_flag_file_sets_the_class_like_the_single_form(self):
        self.ok("ledger", "flag", "R-1", "bug")
        path = self.file("R-2\tbug\nR-3\tfeature\textra column ignored\n")
        out = self.ok("ledger", "flag", "--file", path)
        self.assertIn("R-2: ok", out)
        self.assertIn("R-3: ok", out)
        self.assertIn("2 ok, 0 refused", out)
        self.assertEqual(self.item("R-2").get("flags"), self.item("R-1").get("flags"))
        self.assertEqual(self.item("R-3").get("flags"), ["feature"])

    def test_appetite_file_sets_the_size_like_the_single_form(self):
        self.ok("ledger", "appetite", "R-1", "large")
        path = self.file("R-2\tlarge\nR-3\tsmall\textra\n")
        out = self.ok("ledger", "appetite", "--file", path)
        self.assertIn("2 ok, 0 refused", out)
        self.assertEqual(self.item("R-2")["appetite"], self.item("R-1")["appetite"])
        self.assertEqual(self.item("R-3")["appetite"], "small")

    def test_blank_and_comment_lines_are_skipped(self):
        path = self.file("\n# a comment\nR-2\tmedium\n   \n")
        out = self.ok("ledger", "appetite", "--file", path)
        self.assertIn("1 ok, 0 refused", out)
        self.assertEqual(self.item("R-2")["appetite"], "medium")

    def test_a_bad_value_and_an_unknown_id_are_refused_per_line_and_the_rest_apply(self):
        path = self.file("R-2\thuge\nR-99\tsmall\nR-3\tsmall\n")
        r = self.cli("ledger", "appetite", "--file", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("R-2: refused", r.stdout)
        self.assertIn("R-99: refused", r.stdout)
        self.assertIn("R-3: ok", r.stdout)
        self.assertIn("1 ok, 2 refused", r.stdout)
        self.assertEqual(self.item("R-3")["appetite"], "small")
        self.assertNotEqual(self.item("R-2").get("appetite"), "huge")

    def test_flag_bad_value_is_refused_per_line_and_the_rest_apply(self):
        path = self.file("R-2\tweird\nR-3\tbug\n")
        r = self.cli("ledger", "flag", "--file", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("R-2: refused", r.stdout)
        self.assertIn("R-3: ok", r.stdout)
        self.assertEqual(self.item("R-3").get("flags"), ["bug"])

    def test_the_exit_code_is_zero_when_every_line_is_ok(self):
        path = self.file("R-2\tbug\n")
        self.assertEqual(self.cli("ledger", "flag", "--file", path).returncode, 0)

    def test_file_together_with_positionals_is_an_error(self):
        path = self.file("R-2\tbug\n")
        for args in (("ledger", "flag", "R-1", "bug", "--file", path),
                     ("ledger", "appetite", "R-1", "small", "--file", path)):
            r = self.cli(*args)
            self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("flags", self.item("R-1"))
        self.assertNotIn("R-1: ok", r.stdout)

    def test_neither_form_is_an_error(self):
        self.assertNotEqual(self.cli("ledger", "flag").returncode, 0)
        self.assertNotEqual(self.cli("ledger", "appetite").returncode, 0)
