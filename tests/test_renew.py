"""R-834: renew in place. Preconditions and the handoff writer, with fakes (no app, no real handoff)."""
import unittest
from helpers import StudioTestCase
import renew


class RenewTest(StudioTestCase):
    def test_refuses_while_a_subagent_or_task_is_live_and_names_it_without_writing(self):
        wrote = []
        with self.assertRaises(renew.Refused) as cm:
            renew.prepare("p", "s1", "PM Budgit", live_subagents=["builder-1"], live_tasks=["suite"],
                          writer=lambda *a: wrote.append(a) or ("x", {}))
        self.assertIn("builder-1", str(cm.exception))
        self.assertIn("suite", str(cm.exception))
        self.assertEqual(wrote, [])

    def test_writes_the_handoff_then_returns_ordered_steps_keeping_the_title(self):
        calls = []
        path, steps = renew.prepare("p", "s1", "PM Budgit",
                                    writer=lambda p, s, lane: calls.append((p, s)) or ("/h/handoff.md", {}))
        self.assertEqual(calls, [("p", "s1")])
        self.assertEqual(path, "/h/handoff.md")
        text = "\n".join(steps)
        self.assertLess(text.index("Remote Control"), text.index("clear_session"))
        self.assertLess(text.index("clear_session"), text.index("atomic swap"))
        self.assertIn('exact title "PM Budgit"', text)
        self.assertIn("never two visible rows", text)


if __name__ == "__main__":
    unittest.main()
