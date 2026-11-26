"""R-1195 A (I-1774 class): a multi-line chain of `ledger appetite`, `ledger phase` and `ledger fold` on the project's OWN ledger
needs no yes. Real hook as a subprocess; temp HOME, temp hub, synthetic project. Every test name is the invariant it pins."""
import json
import os

from test_r1173_door_multiline import DoorBase
from test_r1173a_ledger_no_yes import CLI
import helpers
import studiolib as sl


def chain(word, extra=""):
    lines = ['%s ledger appetite R-1 small%s' % (word, extra),
             '%s ledger phase mvp R-1 R-2%s' % (word, extra),
             '%s ledger fold R-3 --into R-1 --outcome "You see it"%s' % (word, extra)]
    return "\n".join(lines)


class OwnChainTest(DoorBase):
    def test_a_multiline_chain_of_appetite_phase_and_fold_on_the_own_ledger_needs_no_yes(self):
        self.set_patterns([".budgit"])
        for lane in (False, True):
            got = self.bash(chain(CLI), lane)
            self.assertEqual(got[0], "allow", (lane, got))
            rooted = chain(CLI, " --project %s" % self.project)
            got = self.bash(rooted, lane)
            self.assertEqual(got[0], "allow", (lane, got))
        self.assertTrue(sl._own_task_lines(chain(CLI), self.project, [".budgit"], self.project))

    def test_a_single_line_appetite_or_phase_on_the_own_ledger_needs_no_yes(self):
        self.set_patterns([".budgit"])
        for line in chain(CLI).split("\n"):
            got = self.bash(line, False)
            self.assertEqual(got[0], "allow", (line, got))

    def test_the_same_chain_naming_another_project_still_asks(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(os.path.join(other, ".budgit"))
        helpers.protect(self.home, other)
        with open(os.path.join(other, ".budgit", "project.json"), "w") as f:
            json.dump({"protected_paths": ["**"]}, f)
        self.set_patterns([".budgit"])
        foreign = chain(CLI, " --project %s" % other)
        self.assertFalse(sl._own_task_lines(foreign, self.project, [".budgit"], self.project))
        self.assertFalse(sl._own_close_lines(foreign, self.project))          # on its own, not inside an `and`
        mixed = "\n".join(chain(CLI).split("\n")[:2] + [chain(CLI, " --project %s" % other).split("\n")[2]])
        self.assertFalse(sl._own_task_lines(mixed, self.project, [".budgit"], self.project))
        for lane in (False, True):       # the REAL hook verdict, as r1173 door does: a chain is judged as its lines alone are
            for cmd in (foreign, mixed):
                lines = cmd.split("\n")
                singles = [self.bash(l, lane)[0] == "allow" for l in lines]
                self.assertEqual(self.bash(cmd, lane)[0] == "allow", all(singles), (cmd[-60:], lane, singles))

    def test_a_chain_with_an_extra_non_ledger_line_keeps_todays_verdict(self):
        self.set_patterns(["clientes.xlsx"])
        target = os.path.join(self.project, "clientes.xlsx")
        base = chain(CLI).split("\n")
        without = "\n".join(l for l in base if " appetite " not in l and " phase " not in l)     # only the fold line
        for tail in ("echo x > " + target, "cp /etc/hosts " + target, "true"):
            cmd = chain(CLI) + "\n" + tail
            self.assertFalse(sl._own_task_lines(cmd, self.project, ["clientes.xlsx"], self.project), tail)
            for lane in (False, True):
                with_lines = self.bash(cmd, lane)
                plain = self.bash(without + "\n" + tail, lane)
                self.assertEqual(with_lines[0], plain[0], (tail, lane, with_lines, plain))      # appetite and phase change nothing
                if tail != "true":
                    self.assertNotEqual(with_lines[0], "allow", (tail, with_lines))
