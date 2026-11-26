"""R-1014: budgit's own CLI is judged by what it writes under the Budgit home. `--help` never writes; the read-only forms
(varies, ext-review, bare budget, bare comply, bare rm-guard) pass for a subagent; user-only settings stay refused."""
import os
import unittest

from test_guard_trust import GuardCase, CLI
import studiolib as sl


class OwnCliRowsTest(GuardCase):
    def verdict(self, cmd, role="subagent"):
        agent = sl.VERIFIER if role == "verifier" else None
        got = sl.home_write_check("Bash", {"command": cmd}, self.root, agent, subagent=role != "pm")[0]
        return got[0] if got else None

    def test_help_and_h_pass_for_a_subagent_on_every_subcommand_of_the_table(self):
        keys = [k for k in sl._cli_table() if k != "rm-guard status" and k not in sl._NO_HELP_SHORTCUT]
        for key in keys:
            for flag in ("--help", "-h"):
                for role in ("subagent", "verifier"):
                    self.assertIsNone(self.verdict("%s %s %s" % (CLI, key, flag), role), (key, flag, role))

    def test_help_passes_for_rm_guard_ext_review_and_comply_by_name(self):
        for cmd in ("rm-guard --help", "ext-review --help", "ext-review -h", "comply --help", "prepare --help", "trust --help", "budget set --help"):
            self.assertIsNone(self.verdict(CLI + " " + cmd), cmd)

    def test_r2_remainder_commands_never_get_the_help_override(self):
        for cmd in ("brote on --help", "brote on -h", "float on --help", "float on -h", "brote --help", "brote off --help"):
            for role in ("subagent", "verifier"):
                self.assertEqual(self.verdict(CLI + " " + cmd, role), "deny", (cmd, role))

    def test_r2_the_passthrough_set_equals_the_remainder_subcommands_of_the_real_parser(self):
        import argparse
        from test_r901_guard_writes import load_cli
        found = set()

        def walk(parser, prefix):
            for act in parser._actions:
                if isinstance(act, argparse._SubParsersAction):
                    for name, child in act.choices.items():
                        walk(child, prefix + (name,))
                elif act.nargs in (argparse.REMAINDER, argparse.PARSER) and prefix:
                    found.add(" ".join(prefix))
        walk(load_cli().build_parser(), ())
        self.assertEqual(found, set(sl._HELP_PASSTHROUGH))     # a nested one ("cmd sub") would show up here too and fail

    def test_r3_the_intercept_set_equals_what_main_handles_before_argparse(self):
        import inspect
        import re
        from test_r901_guard_writes import load_cli
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(load_cli().main)))
        body = tree.body[0].body
        cut = next(i for i, st in enumerate(body) if any(isinstance(n, ast.Attribute) and n.attr == "parse_args" for n in ast.walk(st)))
        pre = body[:cut]
        # R-1030: before argparse only the argv default, plain ifs and imports may stand; anything else (a dict dispatch, a call) fails
        self.assertTrue(all(isinstance(st, (ast.Assign, ast.If, ast.Import, ast.Expr)) for st in pre), [type(s).__name__ for s in pre])
        words = {n.value for st in pre[1:] for n in ast.walk(st)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str) and not n.value.startswith("-") and not n.value.startswith("R-")}
        self.assertEqual(words, set(sl._HELP_INTERCEPTED))      # any quote style, subscript, slice, `in`, dict key: a constant is a constant

    def test_r3_only_the_exact_help_shapes_pass(self):
        for cmd in ("admit --slots 1 --kind --help -- touch /tmp/x", "admit --slots 1 --kind -h", "admit --kind --help --slots 1",
                    "admit --help", "admit -h", "budget set --help tight", "brote --help", "float -h", "trust --help --",
                    "trust -h --", "away --help --", "rm-guard on --help x"):
            self.assertEqual(self.verdict(CLI + " " + cmd), "deny", cmd)
        for cmd in ("varies --help", "budget --help", "budget set --help", "ledger add -h", "comply -h"):
            self.assertIsNone(self.verdict(CLI + " " + cmd), cmd)

    def test_r2_bare_rows_take_no_extra_word(self):
        for cmd in ("budget -- set tight", "budget -- auto", "budget se tight", "budget extra", "comply -- x", "comply --da 3",
                    "comply extra", "rm-guard status on", "rm-guard -- on"):
            self.assertEqual(self.verdict(CLI + " " + cmd), "deny", cmd)

    def test_r2_varies_help_passes(self):
        self.assertIsNone(self.verdict(CLI + " varies --help"))

    def test_help_does_not_open_a_project_home_refusal(self):
        home = sl.home_state()
        self.assertEqual(self.verdict("%s --project %s rm-guard --help" % (CLI, home)), "deny")
        self.assertEqual(self.verdict("%s rm-guard --help --project %s" % (CLI, home)), "deny")

    def test_help_after_a_double_dash_is_not_help(self):
        self.assertEqual(self.verdict(CLI + " trust work -- --help"), "deny")

    def test_varies_ext_review_budget_comply_and_bare_rm_guard_pass_for_a_subagent(self):
        out = os.path.join(self.tmp, "o.txt")
        for cmd in ("varies /tmp/some-file.txt", "budget", "comply", "comply --days 3 --level normal", "rm-guard", "rm-guard status",
                    "ext-review --tool gemini --diff /tmp/d.diff --prompt p --out " + out):
            for role in ("subagent", "verifier"):
                self.assertIsNone(self.verdict(CLI + " " + cmd, role), (cmd, role))

    def test_ext_review_out_inside_the_home_is_still_refused(self):
        out = os.path.join(sl.home_state(), "x.txt")
        self.assertEqual(self.verdict(CLI + " ext-review --tool gemini --diff /tmp/d.diff --prompt p --out " + out), "deny")

    def test_rm_guard_on_off_budget_set_and_prepare_approve_stay_refused_for_a_subagent(self):
        for cmd in ("rm-guard on", "rm-guard off", "budget set tight", "budget auto",
                    "budget record --five-hour 1 --five-hour-resets 2", "prepare --approve", "prepare --appro", "trust work", "trust revoke"):
            self.assertEqual(self.verdict(CLI + " " + cmd), "deny", cmd)

    def test_bare_prepare_passes_for_a_subagent_but_not_on_a_home_project(self):
        for role in ("subagent", "verifier"):
            self.assertIsNone(self.verdict(CLI + " prepare", role), role)
            self.assertIsNone(self.verdict("%s --project %s prepare" % (CLI, self.root), role), role)
        self.assertEqual(self.verdict("%s prepare --project %s" % (CLI, sl.home_state())), "deny")

    def test_the_new_rows_name_the_files_they_write(self):
        t = sl._cli_table()
        self.assertEqual(t["budget"], ("home-fixed", ("budget.json", "budget.lock"), ()))
        self.assertEqual(t["comply"], ("home-fixed", ("budget.json", "budget.lock"), ()))
        self.assertEqual(t["ext-review"], ("home-fixed", ("codex-limit.json",), ("--out",)))
        self.assertEqual(t["varies"][0], "none")
        self.assertEqual(t["rm-guard status"][0], "none")
        self.assertEqual(t["rm-guard"][0], "home")

    def test_r1032_reports_sweep_is_a_project_write_not_a_home_write(self):
        self.assertEqual(sl._cli_table()["reports sweep"], ("project", (), ()))
        for role in ("subagent", "verifier"):
            self.assertIsNone(self.verdict("%s reports sweep --dry-run" % CLI, role), role)
            self.assertIsNone(self.verdict("%s --project %s reports sweep --days 3" % (CLI, self.root), role), role)
            self.assertEqual(self.verdict("%s reports sweep --project %s" % (CLI, sl.home_state()), role), "deny", role)
            self.assertEqual(self.verdict("%s reports" % CLI, role), "deny", role)       # a bare `reports` is not a table row: fails closed as home


if __name__ == "__main__":
    unittest.main()
