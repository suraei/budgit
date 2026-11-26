"""R-1005: one shell variable pattern (`_SHELL_VAR_REF`) serves the home guard and the verify guard, and two agreeing PASS
verify reports that name no sha are still accepted."""
import os

from test_guard_trust import GuardCase
from helpers import StudioTestCase

import studiolib as sl


class ShellVarRefTest(GuardCase):
    def verdict(self, cmd):
        got = sl.home_write_decision("Bash", {"command": cmd}, self.project)
        return got[0] if got else None

    def test_one_definition_serves_both_callers(self):
        with open(sl.__file__) as fh:
            src = fh.read()
        self.assertEqual(src.count("_SHELL_VAR_REF = re.compile("), 1)
        self.assertNotIn("_HOME_VAR_REF", src)
        self.assertNotIn("_VERIFY_VAR_REF", src)

    def test_braced_operator_forms_of_a_home_variable_deny(self):
        # main (R-996) denies the two assigned forms; the two unassigned forms are unreadable to the guard and ask
        for cmd, want in (("P=$HOME; echo x > ${P:-/x}/.studio/trust.json", "deny"),
                          ("P=$HOME; echo x > ${P%/}/.studio/trust.json", "deny"),
                          ("echo x > ${HOME:-/x}/.studio/trust.json", "ask"),
                          ("echo x > ${HOME%/}/.studio/trust.json", "ask")):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.verdict(cmd), want, cmd)

    def test_plain_and_braced_names_still_deny(self):
        for cmd in ("P=$HOME; echo x > $P/.studio/trust.json", "P=$HOME; echo x > ${P}/.studio/trust.json"):
            self.assertEqual(self.verdict(cmd), "deny", cmd)


class AgreeingReportsTest(StudioTestCase):
    def test_two_agreeing_pass_reports_naming_no_sha_are_accepted(self):
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "x", ["I-1"])
        for d in sl.verify_dirs(self.project):
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "R-1.md")
            with open(path, "w") as f:
                f.write("PASS\nchecks\n")
            sl.write_seal(path)
        sl.set_status(self.project, data, "R-1", "verified", evidence="3d5f69d2 test_x")
        self.assertEqual(data["items"][0]["status"], "verified")
