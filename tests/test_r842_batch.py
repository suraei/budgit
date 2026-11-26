"""Batch `ledger done` and `ledger fold` (several ids, or a --file of id<TAB>text lines): each id is judged by the same
gate as a single call, one summary line per id, a non-zero exit when any is refused, and a fold takes the target's held
folds along, straight to the new head."""
import os
import tempfile

import studiolib as sl
from test_fold import FoldCase

PROOF = "abcdef1 test_batch"


def _rm(path):
    """The project folder may already be gone when the cleanups run."""
    if os.path.exists(path):
        os.remove(path)


class _WithOutcomes(FoldCase):
    """R-1136 gate: an item closes only with an outcome line, so these fixture items carry one (the batch is judged on its
    own rules here; the outcome column is tested in test_r1139_batch_outcome)."""
    def setUp(self):
        super().setUp()
        data = self.data()
        for it in data["items"]:
            sl.set_outcome(data, it["id"], "You see %s work" % it["id"])
        sl.save_ledger(self.project, data)


class BatchDoneTest(_WithOutcomes):
    def file(self, text, outside=False):
        fd, path = tempfile.mkstemp(suffix=".tsv", dir=None if outside else self.project)
        self.addCleanup(_rm, path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_a_file_that_is_not_a_ledger_batch_never_shows_its_lines(self):
        secret = "sk-ant-SECRETSHAPED0123456789"
        path = self.file("%s\tx\n/Users/someone/hidden-client-folder/notes.txt\tproof\nR-1\tabcdef1 test_ok\n" % secret)
        r = self.cli("ledger", "done", "--file", path)
        self.assertNotEqual(r.returncode, 0)
        for text in (r.stdout, r.stderr):
            self.assertNotIn("SECRETSHAPED", text)
            self.assertNotIn("hidden-client-folder", text)
        self.assertIn("line 1: refused, not an item id", r.stdout)
        self.assertIn("line 2: refused, not an item id", r.stdout)
        self.assertIn("R-1: ok", r.stdout)

    def test_an_argument_that_is_not_an_item_id_is_not_echoed(self):
        r = self.cli("ledger", "done", "sk-ant-SECRETSHAPED0123", "R-1", "--evidence", PROOF)
        self.assertNotIn("SECRETSHAPED", r.stdout + r.stderr)
        self.assertIn("argument 1: refused, not an item id", r.stdout)

    def test_a_batch_file_outside_the_project_is_refused_without_its_content(self):
        path = self.file("R-1\tabcdef1 test_ok\nSECRETSHAPED\tx\n", outside=True)
        r = self.cli("ledger", "done", "--file", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("must be inside the project", r.stderr)
        self.assertNotIn("SECRETSHAPED", r.stdout + r.stderr)
        self.assertEqual(self.item("R-1")["status"], "open")

    def test_several_ids_close_with_one_proof_and_one_line_each(self):
        out = self.ok("ledger", "done", "R-1", "R-2", "--evidence", PROOF)
        self.assertIn("R-1: ok, done", out)
        self.assertIn("R-2: ok, done", out)
        self.assertIn("2 ok, 0 refused", out)
        self.assertEqual(self.item("R-1")["status"], "done")
        self.assertEqual(self.item("R-2")["status"], "done")

    def test_a_file_of_id_tab_proof_lines_gives_each_item_its_own_proof(self):
        path = self.file("# header\nR-1\tabcdef1 test_one\n\nR-2\t1234567 test_two\n")
        self.ok("ledger", "done", "--file", path)
        self.assertIn("test_one", self.item("R-1")["evidence"])
        self.assertIn("test_two", self.item("R-2")["evidence"])

    def test_a_refused_id_is_reported_and_exits_non_zero_without_stopping_the_rest(self):
        path = self.file("R-1\tabcdef1 test_one\nR-2\tno proof here\nR-3\t1234567 test_three\nR-99\t1234567 test_x\n")
        r = self.cli("ledger", "done", "--file", path)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("R-2: refused", r.stdout)
        self.assertIn("R-99: refused", r.stdout)
        self.assertIn("2 ok, 2 refused", r.stdout)
        self.assertEqual(self.item("R-1")["status"], "done")
        self.assertEqual(self.item("R-2")["status"], "open")
        self.assertEqual(self.item("R-3")["status"], "done")

    def test_the_batch_gate_is_the_single_call_gate(self):
        self.ok("ledger", "fold", "R-4", "--into", "R-1", "--reason", "dup")
        single = self.cli("ledger", "done", "R-4", "--evidence", PROOF)
        self.assertNotEqual(single.returncode, 0)
        batch = self.cli("ledger", "done", "R-4", "R-5", "--evidence", PROOF)
        self.assertNotEqual(batch.returncode, 0)
        self.assertIn("R-4: refused, ", batch.stdout)
        self.assertIn("ledger unfold R-n", single.stderr)
        self.assertIn("ledger unfold R-n", batch.stdout)       # the same refusal text
        self.assertEqual(self.item("R-5")["status"], "done")

    def test_an_id_listed_twice_is_closed_once(self):
        r = self.cli("ledger", "done", "R-1", "R-1", "--evidence", PROOF)
        self.assertIn("R-1: refused, listed twice", r.stdout)
        self.assertNotEqual(r.returncode, 0)


class BatchInProcessTest(FoldCase):
    """The attacks, run in-process on the library functions (never through the CLI)."""

    def make(self, text, folder=None, binary=False):
        fd, path = tempfile.mkstemp(suffix=".tsv", dir=folder or self.project)
        self.addCleanup(_rm, path)
        with os.fdopen(fd, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
            f.write(text)
        return path

    def test_a_symlink_inside_the_project_pointing_outside_is_refused(self):
        outside = self.make("R-1\tx\n", folder=tempfile.gettempdir())
        link = os.path.join(self.project, "link.tsv")
        os.symlink(outside, link)
        self.addCleanup(_rm, link)
        with self.assertRaises(sl.StudioError) as cm:
            sl.batch_file_path(self.project, link)
        self.assertNotIn("link.tsv", str(cm.exception))

    def test_a_dotdot_path_escaping_the_project_is_refused(self):
        outside = self.make("R-1\tx\n", folder=tempfile.gettempdir())
        sneaky = os.path.join(self.project, "..", os.path.basename(outside))
        with self.assertRaises(sl.StudioError):
            sl.batch_file_path(self.project, sneaky)

    def test_a_file_under_the_session_scratchpad_is_accepted(self):
        root = "/private/tmp/claude-%d" % os.getuid()
        os.makedirs(root, exist_ok=True)
        folder = tempfile.mkdtemp(dir=root)
        self.addCleanup(os.rmdir, folder)        # registered first, so it runs after the file is removed
        path = self.make("R-1\tx\n", folder=folder)
        self.assertEqual(sl.batch_file_path(self.project, path), os.path.realpath(path))

    def test_a_binary_file_gives_the_fixed_text(self):
        path = self.make(b"\xff\xfe\x00SECRETSHAPED\x80\x81", binary=True)
        with self.assertRaises(sl.StudioError) as cm:
            sl.parse_batch_file(path)
        self.assertEqual(str(cm.exception), "cannot read the batch file (missing, or not text)")

    def test_a_huge_line_is_never_echoed(self):
        path = self.make("SECRETSHAPED" * 9000 + "\tx\n")
        rows = sl.parse_batch_file(path)
        self.assertEqual(rows[0][0], None)
        self.assertEqual(rows[0][2], "line 1")
        out = sl.batch_apply(sl.load_ledger(self.project), rows, lambda i, t: None)
        self.assertEqual(out, [("line 1", "not an item id")])

    def test_a_secret_shaped_proof_with_no_sha_is_not_echoed_and_the_item_stays_open(self):
        data = sl.load_ledger(self.project)
        secret = "sk-ant-SECRETSHAPED0123456789"
        rows = sl.batch_apply(data, [("R-1", secret, "R-1")], lambda i, t: sl.done_item(self.project, data, i, t))
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0][1])
        self.assertNotIn("SECRETSHAPED", repr(rows))
        self.assertEqual(sl.find_item(data, "R-1")["status"], "open")

    def test_a_gate_error_that_quotes_the_text_is_scrubbed(self):
        def gate(i, t):
            raise sl.StudioError("bad proof %s here" % t)
        rows = sl.batch_apply(sl.load_ledger(self.project), [("R-1", "SECRETSHAPED", "R-1")], gate)
        self.assertEqual(rows, [("R-1", "bad proof <proof> here")])


class BatchFoldTest(_WithOutcomes):
    def test_several_ids_fold_into_one_target(self):
        out = self.ok("ledger", "fold", "R-2", "R-3", "--into", "R-1", "--reason", "same bug")
        self.assertIn("R-2: ok, folded into R-1", out)
        self.assertIn("2 ok, 0 refused", out)
        self.assertEqual(self.item("R-3")["folded"]["into"], "R-1")
        self.assertEqual(self.item("R-3")["folded"]["reason"], "same bug")

    def test_a_file_gives_each_fold_its_own_reason(self):
        fd, path = tempfile.mkstemp(suffix=".tsv", dir=self.project)
        self.addCleanup(_rm, path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("R-2\tfirst reason\nR-3\tsecond reason\n")
        self.ok("ledger", "fold", "--file", path, "--into", "R-1")
        self.assertEqual(self.item("R-2")["folded"]["reason"], "first reason")
        self.assertEqual(self.item("R-3")["folded"]["reason"], "second reason")

    def test_a_refused_fold_exits_non_zero_and_the_others_still_fold(self):
        self.ok("ledger", "done", "R-2", "--evidence", PROOF)
        r = self.cli("ledger", "fold", "R-2", "R-3", "R-1", "--into", "R-1", "--reason", "dup")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("R-2: refused", r.stdout)
        self.assertIn("R-1: refused", r.stdout)       # itself
        self.assertIn("R-3: ok", r.stdout)
        self.assertEqual(self.item("R-3")["status"], "folded")

    def test_a_target_that_holds_folds_carries_them_to_the_new_head_flatly(self):
        self.ok("ledger", "fold", "R-3", "--into", "R-1", "--reason", "part")
        self.ok("ledger", "fold", "R-5", "--into", "R-1", "--reason", "part too")
        out = self.ok("ledger", "fold", "R-1", "--into", "R-2", "--reason", "same thing")
        self.assertIn("R-1 -> folded into R-2", out)
        for held in ("R-3", "R-5"):
            self.assertEqual(self.item(held)["status"], "folded")
            self.assertEqual(self.item(held)["folded"]["into"], "R-2")      # not R-1: no chain
        self.assertEqual(self.item("R-3")["folded"]["reason"], "part")
        self.assertEqual([x["id"] for x in self.folded_into("R-2")], ["R-1", "R-3", "R-5"])

    def test_a_batch_fold_of_a_head_and_its_part_never_loses_the_part(self):
        self.ok("ledger", "fold", "R-3", "--into", "R-1", "--reason", "part")
        self.ok("ledger", "fold", "R-1", "R-4", "--into", "R-2", "--reason", "dup")
        self.assertEqual(self.item("R-3")["folded"]["into"], "R-2")
        self.assertEqual(self.item("R-1")["folded"]["into"], "R-2")

    def test_a_refused_fold_leaves_the_held_folds_where_they_were(self):
        self.ok("ledger", "fold", "R-3", "--into", "R-1", "--reason", "part")
        self.ok("ledger", "fold", "R-2", "--into", "R-4", "--reason", "x")
        r = self.cli("ledger", "fold", "R-1", "R-5", "--into", "R-2", "--reason", "dup")   # R-2 is folded: both refused
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.item("R-3")["folded"]["into"], "R-1")
        self.assertEqual(self.item("R-1")["status"], "open")

    def folded_into(self, item_id):
        import studiolib as sl
        return sl.folded_into(self.data(), item_id)
