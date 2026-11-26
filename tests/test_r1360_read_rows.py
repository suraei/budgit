"""R-1360 task B: the closed list of budgit read verbs a helper may run (readdoor.READ_ROWS) and the dry-run row table
(readdoor.DRY_ROWS), plus the guard that uses them.

One test per row (allowed form, a refused extra flag, a refused path argument), one per dry-run machinery rule, the snapshot
proof harness (HOME, data folder, project dir, temp dir, temp ~/.claude: file list, size and mtime) and guard tests that call
hook.on_pre_tool_use with a helper payload, as test_r1360_read_verbs.py does. No budgit CLI is run here.
Protected project names are written protected-1 and protected-2.
"""
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import readdoor  # noqa: E402
import studiolib as sl  # noqa: E402
from helpers import StudioTestCase, protect  # noqa: E402

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
DOOR = "R-1360 read door: "
NO_DRY_ROW = "R-1360 read door: no dry-run row"

# name -> (allowed forms, a form with one extra flag, a form with a path argument)
READ_CASES = {
    "status": ([["status"]], ["status", "--json"], ["status", "/tmp/x"]),
    "ledger list": ([["ledger", "list"], ["ledger", "list", "--open"]], ["ledger", "list", "--json"], ["ledger", "list", "../x"]),
    "ledger show": ([["ledger", "show", "R-12"], ["ledger", "show", "I-7"]], ["ledger", "show", "R-12", "--json"], ["ledger", "show", "a/b"]),
    "ledger top": ([["ledger", "top"]], ["ledger", "top", "-n", "3"], ["ledger", "top", "/tmp/x"]),
    "rules show": ([["rules", "show", "briefs"], ["rules", "show", "ledger-triage"]], ["rules", "show", "briefs", "--grep", "x"],
                   ["rules", "show", "../briefs"]),
    "skills why": ([["skills", "why", "--no-log"], ["skills", "why", "--no-log", "--job", "design-build"]],
                   ["skills", "why", "--no-log", "--json"], ["skills", "why", "--no-log", "--job", "a/b"]),
    "help flag": ([["--help"]], ["--help", "--json"], ["--help", "/tmp/x"]),
}


class Rows(unittest.TestCase):
    def refused(self, args, text=DOOR):
        with self.assertRaises(readdoor.Refused) as c:
            readdoor.read_row(args)
        self.assertTrue(str(c.exception).startswith(text), str(c.exception))

    def test_the_rows_live_in_readdoor_and_studiolib_only_imports_them(self):
        self.assertIs(sl.readdoor, readdoor)
        self.assertEqual(sorted(r["name"] for r in readdoor.READ_ROWS), sorted(READ_CASES))
        self.assertFalse(hasattr(sl, "_HELPER_BUDGIT_READS"), "the closed list is the readdoor table, not a second copy")

    def test_a_verb_with_no_row_is_not_a_read_row(self):
        for args in (["ledger", "add", "--text", "x"], ["ledger", "done", "R-1", "--evidence", "x"], ["housekeeping"], ["update"],
                     ["ledger", "list-all"], ["status-x"]):
            self.assertIsNone(readdoor.read_row(args), args)

    def test_no_row_for_a_verb_that_does_not_exist(self):
        """`budgit help` is not a verb in plugin/bin/budgit (only the argparse --help), so it has no row."""
        self.assertIsNone(readdoor.read_row(["help"]))
        self.assertNotIn("help", [r["name"] for r in readdoor.READ_ROWS])
        text = open(BUDGIT, encoding="utf-8").read()
        self.assertNotRegex(text, r'add_parser\("help"')

    def test_every_row_verb_exists_in_the_cli(self):
        text = open(BUDGIT, encoding="utf-8").read()
        for verb in ("status", "ledger", "rules", "skills"):
            self.assertRegex(text, r'add_parser\("%s"' % verb)
        for op in ("list", "show", "top", "match"):
            self.assertRegex(text, r'ops\.add_parser\("%s"' % op)
        self.assertIn('"why"', open(os.path.join(ROOT, "plugin", "scripts", "skills.py"), encoding="utf-8").read())

    def test_text_file_and_out_are_refused_everywhere(self):
        for flag in ("--text-file", "--out"):
            for case in READ_CASES.values():
                self.refused(case[0][0] + [flag, "x"], DOOR)

    def test_a_word_is_lowercase_digits_dash_at_most_40(self):
        for good in ("a", "x-1", "a" * 40, "0"):
            self.assertEqual(readdoor.read_row(["rules", "show", good]), "rules show", good)
        for bad in ("", "a" * 41, "A", "a_b", "a.b", "..", "a/b", "-a", "a b", "é"):
            self.refused(["rules", "show", bad])
        self.refused(["skills", "why", "--no-log", "--job", "x" * 41])
        self.refused(["skills", "why", "--no-log", "--job", ".."])

    def test_ids_have_a_fixed_shape(self):
        for bad in ("R-", "R-1x", "r-1", "X-1", "R-1-2", "R-١٢"):
            self.refused(["ledger", "show", bad])

    def test_ledger_match_has_no_read_row_because_it_writes_triggers_json(self):
        self.assertIsNone(readdoor.read_row(["ledger", "match", "I-1"]))

    def test_a_duplicated_flag_is_refused(self):
        self.refused(["ledger", "list", "--open", "--open"])
        self.refused(["skills", "why", "--no-log", "--no-log"])
        self.refused(["skills", "why", "--no-log", "--job", "a", "--job", "b"])

    def test_the_equals_form_and_a_missing_value_are_refused(self):
        self.refused(["skills", "why", "--no-log", "--job=a"])
        self.refused(["skills", "why", "--no-log", "--job"])
        self.refused(["ledger", "list", "--open=1"])

    def test_skills_why_needs_no_log(self):
        self.refused(["skills", "why"])
        self.refused(["skills", "why", "--job", "a"])

    def test_a_missing_or_extra_positional_is_refused(self):
        self.refused(["ledger", "show"])
        self.refused(["ledger", "show", "R-1", "R-2"])
        self.refused(["rules", "show"])
        self.refused(["status", "x"])

    def test_the_global_project_flag_is_refused(self):
        for case in READ_CASES.values():
            self.refused(case[0][0] + ["--project", "x"])


def _add_row_tests():
    for name, (allowed, flag_form, path_form) in READ_CASES.items():
        slug = name.replace(" ", "_")

        def t_ok(self, allowed=allowed, name=name):
            for args in allowed:
                self.assertEqual(readdoor.read_row(args), name, args)

        def t_flag(self, flag_form=flag_form):
            self.refused(flag_form)

        def t_path(self, path_form=path_form):
            self.refused(path_form)
        setattr(Rows, "test_row_%s_allowed_form" % slug, t_ok)
        setattr(Rows, "test_row_%s_refuses_an_extra_flag" % slug, t_flag)
        setattr(Rows, "test_row_%s_refuses_a_path_argument" % slug, t_path)


_add_row_tests()

TEST_ROW = {"argv": ("reports", "sweep", "--dry-run"), "owner": "PM Budgit", "test": "test_a_dry_run_row_allows_its_exact_argv"}


class DryRows(unittest.TestCase):
    def table(self):
        return mock.patch.object(readdoor, "DRY_ROWS", (TEST_ROW,))

    def refused(self, args, text=DOOR):
        with self.assertRaises(readdoor.Refused) as c:
            readdoor.dry_run_check(args)
        self.assertTrue(str(c.exception).startswith(text), str(c.exception))

    def test_a_dry_run_row_allows_its_exact_argv(self):
        with self.table():
            self.assertEqual(readdoor.dry_run_check(["reports", "sweep", "--dry-run"]), TEST_ROW)

    def test_dry_run_equals_false_is_refused(self):
        with self.table():
            for form in ("--dry-run=false", "--dry-run=0", "--dry-run=true", "--dry-run="):
                self.refused(["reports", "sweep", form])

    def test_dry_run_with_apply_or_yes_is_refused(self):
        with self.table():
            for extra in ("--apply", "--yes", "--apply=1"):
                self.refused(["reports", "sweep", "--dry-run", extra])
                self.refused(["reports", "sweep", extra, "--dry-run"])

    def test_a_flag_outside_the_row_is_refused(self):
        with self.table():
            self.refused(["reports", "sweep", "--dry-run", "--days", "3"])
            self.refused(["reports", "sweep", "--dry-run", "--dry-run"])
            self.refused(["reports", "sweep", "--dry-run", "/tmp/x"])

    def test_a_verb_with_no_row_is_refused_with_the_rule_name_first(self):
        with self.table():
            for args in (["housekeeping", "--dry-run"], ["relocate", "plan", "--dry-run"], ["githooks", "install", "--dry-run"],
                         ["ledger", "done", "R-1", "--dry-run"], ["procs", "--dry-run"]):
                self.refused(args, NO_DRY_ROW)

    def test_the_real_table_row_shapes_and_owners(self):
        """A row is the dry form of its verb: the argv that writes nothing, no --apply, --yes or --dry-run=value in it."""
        for row in readdoor.DRY_ROWS:
            self.assertFalse([a for a in row["argv"] if a in ("--apply", "--yes") or a.startswith(("--apply=", "--yes=", "--dry-run="))], row)
            self.assertTrue(row["owner"].strip(), row)
            self.assertTrue(hasattr(ProofHarness, row["test"]), row)

    def test_the_real_table_has_no_row_yet(self):
        """housekeeping (its status check refreshes a worktree index) and `relocate plan` (it touches the source repo's .git
        folder) were left out: their snapshot proof showed a change. The --dry-run verbs have no row yet either."""
        self.assertEqual(readdoor.DRY_ROWS, ())
        for args in (["housekeeping"], ["relocate", "plan"], ["relocate", "plan", "--yes"]):
            self.assertIsNone(readdoor.dry_form_check(args), args)

    def test_the_dry_form_is_the_exact_argv_and_apply_or_yes_is_refused(self):
        with mock.patch.object(readdoor, "DRY_ROWS", (FORM_ROW,)):
            self.assertEqual(readdoor.dry_form_check(["relocate", "plan"])["argv"], ("relocate", "plan"))
            for args in (["relocate", "plan", "--yes"], ["relocate", "plan", "--apply=1"]):
                with self.assertRaises(readdoor.Refused, msg=args) as c:
                    readdoor.dry_form_check(args)
                self.assertTrue(str(c.exception).startswith(DOOR), str(c.exception))
            for args in (["housekeeping"], ["housekeeping", "--apply"], ["relocate", "apply"], ["relocate", "plan", "--root", "x"],
                         ["relocate"], ["reports", "sweep"], ["procs"], ["status"]):
                self.assertIsNone(readdoor.dry_form_check(args), args)

    def test_every_real_row_has_a_proof_fixture(self):
        for row in readdoor.DRY_ROWS:
            self.assertIn(row["argv"], PROOF_FIXTURES, "a dry-run row needs a fixture that puts the verb in a state where it would act")


def snapshot(*roots):
    """Every entry under each root: kind, size, mtime_ns. Logs, stamps and empty folders included."""
    out = {}
    for root in roots:
        if not os.path.lexists(root):
            out[root] = None
            continue
        for dirpath, dirs, files in os.walk(root):
            for name in sorted(dirs + files):
                path = os.path.join(dirpath, name)
                st = os.lstat(path)
                out[path] = (st.st_mode, st.st_size, st.st_mtime_ns)
        st = os.lstat(root)
        out[root] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return out


# A flagless row, used only to keep the dry-form mechanism covered while the real table is empty (the real table has no such row).
FORM_ROW = {"argv": ("relocate", "plan"), "owner": "PM Budgit", "test": "test_every_dry_run_row_changes_nothing_in_home_data_project_temp_and_claude_folders"}

PROOF_FIXTURES = {}   # row argv -> builds the state where the verb would act; returns a word the output must hold (empty: no real row)


class ProofHarness(StudioTestCase):
    """The snapshot proof: the exact row form runs the real CLI in a temp HOME, data folder, project and ~/.claude, and nothing changes."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.realpath(self.tmp)                 # the CLI prints real paths
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "proj")
        self.uhome = os.path.join(self.tmp, "uhome")
        os.makedirs(os.path.join(self.uhome, ".claude"))
        os.makedirs(os.path.join(self.tmp, "scratch"))
        os.makedirs(self.home, exist_ok=True)
        os.makedirs(self.project, exist_ok=True)
        self.roots = [self.uhome, self.home, self.project, os.path.join(self.uhome, ".claude")]
        from helpers import patch_env
        patch_env(self, STUDIO_HOME=self.home)
        self.git_env = {"PATH": "/usr/bin:/bin", "HOME": self.uhome, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def cli_env(self):
        return dict(self.git_env, STUDIO_HOME=self.home, CLAUDE_CONFIG_DIR=os.path.join(self.uhome, ".claude"),
                    TMPDIR=os.path.join(self.tmp, "scratch"), BUDGIT_LANG="en", BUDGIT_BUSY_WINDOW="1", BUDGIT_ADMIT_ADVICE="off")

    def run_cli(self, argv, cwd=None):
        import subprocess
        return subprocess.run([sys.executable, "-I", "-W", "ignore", BUDGIT] + list(argv), cwd=cwd or self.tmp, env=self.cli_env(),
                              capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)

    def test_the_snapshot_sees_a_new_file_a_changed_size_a_touched_mtime_and_a_new_log(self):
        base = snapshot(self.tmp)
        with open(os.path.join(self.roots[1], "log.jsonl"), "w") as f:
            f.write("x")
        grown = snapshot(self.tmp)
        self.assertNotEqual(base, grown)
        with open(os.path.join(self.roots[1], "log.jsonl"), "a") as f:
            f.write("y")
        self.assertNotEqual(grown, snapshot(self.tmp))
        again = snapshot(self.tmp)
        os.utime(os.path.join(self.roots[1], "log.jsonl"), ns=(1, 1))
        self.assertNotEqual(again, snapshot(self.tmp))
        os.makedirs(os.path.join(self.roots[3], "stamps"))
        self.assertNotEqual(snapshot(self.tmp), again)

    def test_a_read_only_action_leaves_the_snapshot_unchanged(self):
        with open(os.path.join(self.roots[2], "a.txt"), "w") as f:
            f.write("a")
        before = snapshot(self.tmp)
        with open(os.path.join(self.roots[2], "a.txt")) as f:
            f.read()
        os.listdir(self.roots[0])
        self.assertEqual(before, snapshot(self.tmp))

    def prove(self, argv, fixture_word=None, expect=None):
        before = snapshot(self.tmp)
        r = self.run_cli(argv, cwd=self.tmp)
        after = snapshot(self.tmp)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        diff = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        self.assertEqual(diff, [], "the dry form changed: %s" % diff)
        return r

    def test_every_dry_run_row_changes_nothing_in_home_data_project_temp_and_claude_folders(self):
        """The row's exact argv runs the real CLI in temps, in the state where the verb would act (the output proves it), and the
        snapshot of HOME, the data folder, the project, the temp dir and ~/.claude is identical before and after."""
        for row in readdoor.DRY_ROWS:
            word = PROOF_FIXTURES[row["argv"]](self)
            r = self.prove(list(row["argv"]))
            self.assertIn(word, r.stdout, "the fixture must put the verb where it would act: " + r.stdout)

    def test_rules_show_changes_nothing_in_the_temp_home(self):
        import rules
        topic = rules.topic_ids()[0]
        for argv in (["rules", "show", topic], ["rules", "show", "--grep", "zzz"]):
            r = self.prove(argv)
            self.assertTrue(r.stdout.strip(), argv)


class Guard(StudioTestCase):
    """The helper's own judgement through hook.on_pre_tool_use, with the real-data guard on (a protected project)."""

    def setUp(self):
        super().setUp()
        self.init_project()
        protect(self.home, self.project)             # protected-1

    def payload(self, command, agent="agent-1"):
        inp = {"session_id": "s-r1360b", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.project}
        if agent:
            inp["agent_id"] = agent
            inp["agent_type"] = "studio:builder"
        return inp

    def decision(self, command, agent="agent-1"):
        import hook
        hook._OUT.clear()
        try:
            hook.on_pre_tool_use(self.payload(command, agent), self.project)
            out = hook._OUT.get("hookSpecificOutput") or {}
            return out.get("permissionDecision"), out.get("permissionDecisionReason") or ""
        finally:
            hook._OUT.clear()

    def door(self, tail):
        return sl._helper_budgit_read(BUDGIT + " " + tail, self.project)

    def test_every_row_allowed_form_is_allowed_for_a_helper_through_the_hook(self):
        """The door opens every row. `rules` and `skills` are still class "home" in the CLI writes table (plugin/bin/budgit), so the
        budgit-home guard denies them for a helper on its own; that table is not part of this task (see the report)."""
        for name, (allowed, _flag, _path) in READ_CASES.items():
            for args in allowed:
                self.assertEqual(self.door(" ".join(args)), ("allow",), args)
                if args[0] != "skills":
                    self.assertEqual(self.decision(BUDGIT + " " + " ".join(args)), (None, ""), args)

    def test_a_row_form_with_an_extra_flag_or_a_path_is_not_opened_by_the_door(self):
        for name, (_allowed, flag_form, path_form) in READ_CASES.items():
            for args in (flag_form, path_form):
                self.assertNotEqual(self.door(" ".join(args)), ("allow",), args)

    def test_text_file_and_out_are_never_opened_for_a_helper(self):
        for tail in ("status --text-file x", "ledger list --out x", "ledger show R-1 --out /tmp/x", "skills why --no-log --text-file x"):
            self.assertNotEqual(self.door(tail), ("allow",), tail)
            self.assertEqual(self.door(tail)[0], "deny", tail)
            self.assertTrue(self.door(tail)[1].startswith(DOOR), tail)
            self.assertEqual(self.decision(BUDGIT + " " + tail)[0], "deny", tail)

    def test_ledger_add_and_done_are_never_opened_by_the_door(self):
        """The door only allows rows. Whether a helper's ledger add or done is refused is decided by the old guards (the
        project class passes a helper today: see the report); the door adds no allowance and no ask of its own."""
        for tail in ("ledger add --text x --title t", "ledger done R-1 --evidence ok"):
            self.assertIsNone(self.door(tail), tail)

    def test_dry_run_never_makes_the_door_stricter_than_the_same_command_without_it(self):
        """R-1360 L2, direction 1: a --dry-run for a verb without a row is no longer a door deny; the door gives no verdict (the old
        judgement stands), and a read verb that the door opens without the flag gets the same verdict with it through the hook."""
        for tail in ("housekeeping --dry-run", "relocate plan --dry-run", "reports sweep --dry-run", "status --dry-run",
                     "housekeeping --dry-run=false", "housekeeping --dry-run --apply", "relocate apply --dry-run --yes"):
            self.assertIsNone(self.door(tail), tail)
        for tail in ("status", "ledger list"):
            self.assertEqual(self.decision(BUDGIT + " " + tail + " --dry-run")[0], self.decision(BUDGIT + " " + tail)[0], tail)

    def test_a_dry_run_row_is_still_allowed_and_a_value_form_is_not_opened(self):
        """R-1360 L2, direction 2: the exact row still opens; the value, --apply and --yes forms are never opened by the door."""
        with mock.patch.object(readdoor, "DRY_ROWS", (TEST_ROW,)):
            self.assertEqual(self.decision(BUDGIT + " reports sweep --dry-run"), (None, ""))
            for tail in ("reports sweep --dry-run=false", "reports sweep --dry-run --apply", "reports sweep --dry-run --yes"):
                self.assertNotEqual(self.door(tail), ("allow",), tail)

    def test_the_dry_form_row_is_allowed_for_a_helper_and_apply_or_yes_stays_refused(self):
        self.assertNotEqual(self.door("relocate plan"), ("allow",), "the real table has no row for relocate plan")
        self.assertNotEqual(self.door("housekeeping"), ("allow",), "housekeeping has no row")
        with mock.patch.object(readdoor, "DRY_ROWS", (FORM_ROW,)):
            self.assertEqual(self.door("relocate plan"), ("allow",))
            self.assertEqual(self.decision(BUDGIT + " relocate plan"), (None, ""))
            self.assertNotEqual(self.door("housekeeping"), ("allow",), "housekeeping has no row")
            for tail in ("relocate plan --yes", "relocate plan --apply"):
                got = self.door(tail)
                self.assertEqual(got[0], "deny", tail)
                self.assertTrue(got[1].startswith(DOOR), got)
                self.assertEqual(self.decision(BUDGIT + " " + tail)[0], "deny", tail)
            for tail in ("housekeeping --last", "housekeeping procs --stop", "housekeeping --schedule on", "relocate apply --yes",
                         "relocate plan --root x", "relocate undo x --yes", "housekeeping --only x", "housekeeping --apply"):
                self.assertNotEqual(self.door(tail), ("allow",), tail)
                self.assertEqual(self.decision(BUDGIT + " " + tail)[0], "deny", tail)

    def test_rules_show_is_class_none_and_every_other_rules_verb_stays_home(self):
        self.assertEqual(sl._cli_table()["rules show"][0], "none")
        self.assertEqual(sl._cli_table()["rules"][0], "home")
        self.assertEqual(self.decision(BUDGIT + " rules show briefs"), (None, ""))
        for tail in ("rules list", "rules off briefs", "rules on briefs", "rules edit briefs", "rules show briefs --text-file x"):
            self.assertEqual(self.decision(BUDGIT + " " + tail)[0], "deny", tail)

    def test_a_main_session_is_not_judged_by_the_helper_door(self):
        verdict, reason = self.decision(BUDGIT + " housekeeping --dry-run", agent=None)
        self.assertFalse(reason.startswith(DOOR), reason)

    def test_the_old_closed_set_still_opens_for_a_helper(self):
        for tail in ("status", "ledger list", "ledger show R-12"):
            self.assertEqual(self.door(tail), ("allow",), tail)


if __name__ == "__main__":
    unittest.main()
