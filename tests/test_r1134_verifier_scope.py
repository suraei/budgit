"""R-1134 item 3: the verifier agent file states the rerun scope."""
import unittest
from pathlib import Path

TEXT = (Path(__file__).resolve().parent.parent / "plugin" / "agents" / "verifier.md").read_text(encoding="utf-8")


def step_4b():
    start = TEXT.index("4b. Rerun scope")
    return TEXT[start:TEXT.index("\n5. ", start)]


class VerifierScope(unittest.TestCase):
    def test_step_exists_and_round_one_full(self):
        self.assertIn("Round 1 checks everything", step_4b())

    def test_four_rerun_parts(self):
        s = step_4b()
        for part in ("(a) every check that failed or was unchecked", "(b)", "git diff --name-only <old>..<new>",
                     "(c) the OUTCOME row, always", "(d) the selective tests"):
            self.assertIn(part, s)

    def test_carried_rows_marked(self):
        self.assertIn("carried from <old sha>", step_4b())

    def test_carry_nothing_conditions(self):
        s = step_4b()
        for cond in ("not an ancestor", "a hook, a guard", "the ledger code", "shared helpers", "full recheck"):
            self.assertIn(cond, s)

    def test_full_suite_once_per_rc(self):
        self.assertIn("once per release candidate", step_4b())

    def test_scope_line(self):
        self.assertIn("`scope: full | changed-only (<k> re-run, <c> carried from <old sha>)`", TEXT)

    def test_existing_rules_kept(self):
        for s in ("Outcome first", "Budgit hook seals", "line 4 is `round: <n>`", "On a refusal by a guard"):
            self.assertIn(s, TEXT)


if __name__ == "__main__":
    unittest.main()
