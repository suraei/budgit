"""R-1173a: working on the project's own task list (close with proof, fold, read, ask for help) needs no yes, for the main
session and a lane, protected projects included; help is never refused; the verify-folder guard judges real write targets
only; a close with no code (--decision, --rule), `ledger due` and `ledger evidence`. Real hook and real CLI as subprocesses;
temp HOME, temp hub, temp git repo with main; made-up names. Every test name is the invariant it pins."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT, protect as _protect
import studiolib as sl


def protect(home, *dirs):
    """A protected project as the user has one: the folder is in `protected_dirs` and the whole project is real data."""
    _protect(home, *dirs)
    path = os.path.join(sl.studio_dir(dirs[0]), "project.json")
    cfg = json.load(open(path)) if os.path.exists(path) else {}
    cfg["protected_paths"] = ["data.json", "data/real/**", ".budgit/**"]
    with open(path, "w") as f:
        json.dump(cfg, f)
    for rel in ("data.json", "data/real/x.csv"):
        full = os.path.join(dirs[0], rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write("one\n")

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com")
OUTCOME = "You see it"


class Base(StudioTestCase):
    DATA = ".budgit"

    def setUp(self):
        super().setUp()
        self.fake_home = os.path.join(self.tmp, "fake-user-home")
        os.makedirs(self.fake_home)
        self.init_project()
        sl.activate(self.project, "s1")        # the hook finds the project through this chat; without it no project guard runs
        os.makedirs(os.path.join(self.project, ".budgit", "verify"), exist_ok=True)
        self.git("init", "-q", "-b", "main")
        data = sl.load_ledger(self.project)
        for n in range(1, 5):
            sl.add_item(data, "item %d" % n, ["legacy"])
            sl.set_status(self.project, data, "R-%d" % n, "doing")
        sl.save_ledger(self.project, data)
        self.sha = self.commit("work")

    def git(self, *args):
        r = subprocess.run(["git", "-C", self.project, *args], env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, message):
        with open(os.path.join(self.project, "f.txt"), "a") as f:
            f.write(message)
        self.git("add", "f.txt")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")

    def env(self):
        env = dict(os.environ, HOME=self.fake_home, STUDIO_HOME=self.home, BUDGIT_LANG="en", BUDGIT_ADMIT_ADVICE="off")
        for k in ("BUDGIT_HOME", "CLAUDE_CODE_SESSION_ID", "BUDGIT_SESSION"):
            env.pop(k, None)
        return env

    def cli(self, *args):
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=self.env(), capture_output=True, text=True)

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def audit_lines(self, needle):
        text = sl.read_text(os.path.join(sl.studio_dir(self.project), "audit.log"))
        return [l for l in text.splitlines() if needle in l]

    def hook(self, tool, tool_input, lane, cwd=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "session_id": "s1",
                   "cwd": cwd or self.project}
        if lane:
            payload.update(agent_id="a1", agent_type="budgit:builder")
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), env=self.env(),
                           capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr)
        try:
            out = json.loads(r.stdout).get("hookSpecificOutput", {}) if r.stdout.strip() else {}
        except ValueError:
            out = {}
        return out.get("permissionDecision", "allow"), out.get("permissionDecisionReason", "")

    def bash(self, command, lane=False):
        return self.hook("Bash", {"command": command}, lane)

    def own(self, *words, project=True):
        """A command line for the guard (it is never run): the own CLI, `--project P` first as the real refusals had it."""
        return " ".join([CLI] + (["--project", self.project] if project else []) + list(words))

    def each(self, check):
        for protected in (False, True):
            if protected:
                protect(self.home, self.project)
            for lane in (False, True):
                check(lane, protected)


class TaskListNeedsNoYesTest(Base):
    def test_done_is_allowed_by_the_hook_for_a_lane_and_the_main_session_in_a_normal_and_a_protected_project(self):
        def check(lane, protected):
            got = self.bash(self.own("ledger", "done", "R-1", "--evidence", "'%s test_thing'" % self.sha, "--outcome", "'You see it'"), lane)
            self.assertEqual(got[0], "allow", (lane, protected, got))
            got = self.bash(self.own("ledger", "done", "R-1", "--evidence", "'%s test_thing'" % self.sha, "--outcome", "'x'", project=False), lane)
            self.assertEqual(got[0], "allow", (lane, protected, "no --project", got))
        self.each(check)

    def test_fold_is_allowed_by_the_hook_for_a_lane_and_the_main_session_in_a_normal_and_a_protected_project(self):
        def check(lane, protected):
            got = self.bash(self.own("ledger", "fold", "R-2", "--into", "R-3", "--reason", "'same thing'"), lane)
            self.assertEqual(got[0], "allow", (lane, protected, got))
        self.each(check)

    def test_ledger_reads_due_evidence_and_the_no_code_close_are_allowed_in_a_protected_project(self):
        def check(lane, protected):
            for words in (("ledger", "list", "--open"), ("ledger", "show", "R-1"), ("ledger", "due", "R-1", "2026-12-24"),
                          ("ledger", "evidence", "R-1", "--sha", self.sha, "--test", "test_thing"),
                          ("ledger", "done", "R-1", "--decision", "I-1", "--outcome", "'x'"),
                          ("ledger", "done", "R-1", "--rule", "rules.md:2", "--outcome", "'x'")):
                got = self.bash(self.own(*words), lane)
                self.assertEqual(got[0], "allow", (words, lane, protected, got))
        self.each(check)

    def test_help_calls_are_allowed_for_a_lane_and_the_main_session_in_a_protected_project(self):
        protect(self.home, self.project)
        for words, with_project in ((["--help"], False), (["-h"], False), (["ledger", "--help"], False), (["ledger", "-h"], False),
                                    (["ledger", "done", "--help"], False), (["ledger", "fold", "-h"], False),
                                    (["ledger", "--help"], True), (["ledger", "done", "--help"], True), (["--help"], True)):
            for lane in (False, True):
                got = self.bash(self.own(*words, project=with_project), lane)
                self.assertEqual(got[0], "allow", (words, with_project, lane, got))

    def test_a_done_with_a_file_inside_the_project_data_folder_is_allowed_and_one_outside_is_not(self):
        protect(self.home, self.project)
        inside = os.path.join(self.project, ".budgit", "batch.txt")
        got = self.bash(self.own("ledger", "done", "--file", inside), False)
        self.assertEqual(got[0], "allow", got)
        got = self.bash(self.own("ledger", "done", "--file", os.path.join(self.project, "data", "real", "x.csv")), False)
        self.assertNotEqual(got[0], "allow", got)

    def test_only_the_bound_project_counts_for_task_data_even_when_another_is_registered(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self.assertFalse(sl._task_project_ok(other, self.project))
        self.assertTrue(sl._task_project_ok(self.project, self.project))
        os.makedirs(sl.home_state(), exist_ok=True)
        with open(os.path.join(sl.home_state(), "portfolio.json"), "w") as f:
            json.dump({"projects": [{"path": other}]}, f)
        self.assertFalse(sl._task_project_ok(other, self.project))      # I-1771: registered is not bound

    def test_a_lane_bound_to_one_project_asks_for_another_registered_protected_project_and_passes_on_its_own(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(os.path.join(other, ".budgit"))
        protect(self.home, self.project, other)
        with open(os.path.join(other, ".budgit", "project.json"), "w") as f:
            json.dump({"protected_paths": ["**"]}, f)
        os.makedirs(sl.home_state(), exist_ok=True)
        with open(os.path.join(sl.home_state(), "portfolio.json"), "w") as f:
            json.dump({"projects": [{"path": other}]}, f)
        foreign = " ".join([CLI, "--project", other, "ledger", "done", "R-1", "--evidence", "'%s test_thing'" % self.sha])
        own = " ".join([CLI, "--project", self.project, "ledger", "done", "R-1", "--evidence", "'%s test_thing'" % self.sha])
        # the real-data guard judges only the bound project's own patterns, so it never asked about another project's data (also at
        # 2f3a1744); what this item pins is that the task-data exception does not reach another project
        self.assertFalse(sl._task_call(foreign.split(), self.project, self.project))
        self.assertTrue(sl._task_call(own.split(), self.project, self.project))
        for lane in (True, False):
            self.assertEqual(self.bash(own, lane)[0], "allow", ("own", lane))

    def test_any_other_command_on_a_protected_project_keeps_the_protected_data_refusal(self):
        protect(self.home, self.project)
        for lane in (False, True):
            got = self.bash("cp /etc/hosts %s/data.json" % self.project, lane)
            self.assertNotEqual(got[0], "allow", (lane, got))
            got = self.bash("cat > %s/data/real/new.csv" % self.project, lane)
            self.assertNotEqual(got[0], "allow", ("cat redirect", lane, got))

    def test_a_close_chained_with_a_write_to_protected_data_is_still_refused(self):
        protect(self.home, self.project)
        cmd = self.own("ledger", "done", "R-1", "--evidence", "'%s test_thing'" % self.sha, "--outcome", "'x'") + \
            " && cp /etc/hosts %s/data.json" % self.project
        for lane in (False, True):
            got = self.bash(cmd, lane)
            self.assertNotEqual(got[0], "allow", (lane, got))

    def test_an_abbreviated_project_or_file_flag_is_refused_by_the_hook_and_by_the_guard_rule(self):
        protect(self.home, self.project)
        real = os.path.join(self.project, "data", "real")
        for cmd in (" ".join([CLI, "--proj", real, "ledger", "list"]), " ".join([CLI, "--proj=" + real, "ledger", "list"]),
                    " ".join([CLI, "ledger", "done", "--fil", os.path.join(real, "x.csv")]),
                    " ".join([CLI, "ledger", "done", "--fil=" + os.path.join(real, "x.csv")]),
                    " ".join([CLI, "ledger", "done", "--filex", os.path.join(real, "x.csv")])):
            self.assertFalse(sl._task_call(cmd.split(), self.project, self.project), cmd)
            self.assertFalse(sl.is_own_ledger_close(cmd), cmd)
            for lane in (False, True):
                got = self.bash(cmd, lane)
                self.assertNotEqual(got[0], "allow", (cmd, lane, got))

    def test_help_of_a_command_that_runs_code_on_help_is_not_a_task_call(self):
        for words in (["brote", "uninstall", "--help"], ["float", "--help"], ["admit", "-h"]):
            self.assertFalse(sl._task_call([CLI] + words, self.project, self.project), words)
        for words in (["--help"], ["ledger", "--help"], ["ledger", "done", "-h"]):
            self.assertTrue(sl._task_call([CLI] + words, self.project, self.project), words)

    def test_a_project_value_that_is_not_registered_and_is_protected_data_is_refused_by_the_hook(self):
        protect(self.home, self.project)
        cmd = " ".join([CLI, "--project", os.path.join(self.project, "data", "real"), "ledger", "list"])
        for lane in (False, True):
            got = self.bash(cmd, lane)
            self.assertNotEqual(got[0], "allow", (lane, got))


class VerifyWordTest(Base):
    def test_the_r746_shape_evidence_text_naming_the_verify_path_is_not_refused_by_the_main_session_at_the_project_root(self):
        cmd = self.own("ledger", "done", "R-1", "--evidence",
                       '"verify .budgit/verify/R-1.md PASS; security PASS round 3; merged tip %s test_thing"' % self.sha,
                       project=False)
        for protected in (False, True):
            if protected:
                protect(self.home, self.project)
            got = self.bash(cmd, False)
            self.assertEqual(got[0], "allow", (protected, got))
            got = self.bash(self.own("ledger", "done", "R-1", "--evidence", '"%s test_thing (.budgit/verify/R-1.md) ok"' % self.sha), False)
            self.assertEqual(got[0], "allow", (protected, got))

    def test_printf_and_echo_text_naming_the_verify_path_appended_to_another_file_is_not_refused(self):
        for cmd in ('printf "%%s\\n" "see .budgit/verify/R-1.md" >> %s/notes.txt' % self.project,
                    'echo "R-1 proof (.budgit/verify/R-1.md)" >> %s/notes.txt' % self.project):
            for lane in (False, True):
                got = self.bash(cmd, lane)
                self.assertEqual(got[0], "allow", (cmd, lane, got))

    def test_a_real_redirect_into_the_verify_folder_is_still_refused(self):
        for lane in (False, True):
            got = self.bash("echo x > %s/.budgit/verify/R-1.md" % self.project, lane)
            self.assertEqual(got[0], "deny", (lane, got))
            self.assertIn("only budgit:verifier writes .budgit/verify/", got[1])

    def test_a_write_tool_call_into_the_verify_folder_is_still_refused(self):
        path = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        for lane in (False, True):
            got = self.hook("Write", {"file_path": path, "content": "PASS"}, lane)
            self.assertEqual(got[0], "deny", (lane, got))

    def test_a_compound_command_with_a_close_and_a_redirect_into_the_verify_folder_is_still_refused(self):
        cmd = self.own("ledger", "done", "R-1", "--evidence", "'%s test_thing'" % self.sha) + \
            " ; echo x > %s/.budgit/verify/R-1.md" % self.project
        for lane in (False, True):
            got = self.bash(cmd, lane)
            self.assertEqual(got[0], "deny", (lane, got))

    def test_a_verify_path_as_a_non_text_value_of_the_close_is_still_refused(self):
        cmd = self.own("ledger", "evidence", "R-1", "--sha", self.sha, "--report", "%s/.budgit/verify/R-1.md" % self.project)
        got = self.bash(cmd, False)
        self.assertEqual(got[0], "deny", got)


class CliCloseTest(Base):
    def test_done_closes_with_a_sha_and_a_test_and_writes_one_audit_line(self):
        r = self.cli("ledger", "done", "R-1", "--evidence", "%s test_thing" % self.sha, "--outcome", OUTCOME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.item("R-1")["status"], "done")
        lines = self.audit_lines("R-1 done")
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("proof %s" % self.sha, lines[0])

    def test_done_without_a_sha_and_a_test_is_still_refused(self):
        r = self.cli("ledger", "done", "R-1", "--evidence", "it works", "--outcome", OUTCOME)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.item("R-1")["status"], "doing")

    def test_fold_folds_and_writes_one_audit_line(self):
        r = self.cli("ledger", "fold", "R-2", "--into", "R-3", "--reason", "same thing")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.item("R-2")["status"], "folded")
        self.assertEqual(len(self.audit_lines("R-2 folded into R-3")), 1)

    def test_decision_close_needs_an_input_that_exists_and_writes_one_audit_line(self):
        sl.append_inbox(self.project, "we decided the thing", "s1", "prompt")
        r = self.cli("ledger", "done", "R-1", "--decision", "I-1", "--outcome", OUTCOME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.item("R-1")["status"], "done")
        self.assertIn("decision I-1", self.item("R-1")["evidence"])
        self.assertEqual(len(self.audit_lines("R-1 done: proof decision I-1")), 1)
        r = self.cli("ledger", "done", "R-2", "--decision", "I-99", "--outcome", OUTCOME)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("I-99 is not an input in the inbox", r.stdout + r.stderr)
        self.assertEqual(self.item("R-2")["status"], "doing")

    def test_rule_close_needs_an_existing_file_and_a_line_in_range(self):
        with open(os.path.join(self.project, "rules.md"), "w") as f:
            f.write("one\ntwo\n")
        r = self.cli("ledger", "done", "R-1", "--rule", "rules.md:2", "--outcome", OUTCOME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.item("R-1")["status"], "done")
        for bad in ("nowhere.md:1", "rules.md:99", "rules.md", "../outside.md:1"):
            r = self.cli("ledger", "done", "R-2", "--rule", bad, "--outcome", OUTCOME)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("--rule needs file:line", r.stdout + r.stderr)
        self.assertEqual(self.item("R-2")["status"], "doing")

    def test_two_proofs_at_once_are_refused(self):
        r = self.cli("ledger", "done", "R-1", "--decision", "I-1", "--rule", "rules.md:1", "--outcome", OUTCOME)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("give only one proof", r.stdout + r.stderr)

    def test_a_batch_file_row_can_close_with_a_decision_or_a_rule(self):
        sl.append_inbox(self.project, "we decided the thing", "s1", "prompt")
        with open(os.path.join(self.project, "rules.md"), "w") as f:
            f.write("one\n")
        batch = os.path.join(self.project, ".budgit", "batch.txt")
        with open(batch, "w") as f:
            f.write("R-1\tdecision I-1\t%s\nR-2\trule rules.md:1\t%s\nR-3\tdecision I-77\t%s\n" % (OUTCOME, OUTCOME, OUTCOME))
        r = self.cli("ledger", "done", "--file", batch)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("R-1: ok, done", r.stdout)
        self.assertIn("R-2: ok, done", r.stdout)
        self.assertIn("R-3: refused", r.stdout)
        self.assertEqual((self.item("R-1")["status"], self.item("R-2")["status"], self.item("R-3")["status"]), ("done", "done", "doing"))

    def test_due_redates_writes_one_audit_line_and_refuses_bad_input(self):
        r = self.cli("ledger", "due", "R-1", "2026-12-24")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.item("R-1")["due"], "2026-12-24")
        self.assertEqual(len(self.audit_lines("R-1 due")), 1)
        for bad in (("R-1", "tomorrow"), ("R-1", "2026-02-30"), ("R-99", "2026-12-24")):
            self.assertNotEqual(self.cli("ledger", "due", *bad).returncode, 0, bad)

    def test_evidence_adds_proof_without_closing_and_refuses_a_sha_that_is_not_a_commit(self):
        r = self.cli("ledger", "evidence", "R-1", "--sha", self.sha, "--test", "test_thing")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.sha, self.item("R-1")["evidence"])
        self.assertEqual(self.item("R-1")["status"], "doing")
        self.assertEqual(len(self.audit_lines("R-1 evidence added")), 1)
        r = self.cli("ledger", "evidence", "R-1", "--sha", "deadbeef")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is not a commit of this project's repo", r.stdout + r.stderr)

    def prepare_no_code(self):
        sl.append_inbox(self.project, "we decided the thing", "s1", "prompt")
        with open(os.path.join(self.project, "rules.md"), "w") as f:
            f.write("one\n")

    def test_a_code_item_with_a_commit_naming_it_is_refused_with_decision_and_with_rule(self):
        self.prepare_no_code()
        self.commit("R-2: built the thing")
        for flag, value in (("--decision", "I-1"), ("--rule", "rules.md:1")):
            r = self.cli("ledger", "done", "R-2", flag, value, "--outcome", OUTCOME)
            self.assertNotEqual(r.returncode, 0, flag)
            self.assertIn("R-2 was built", r.stdout + r.stderr)
            self.assertIn('--evidence "<sha> <test>"', r.stdout + r.stderr)
        self.assertEqual(self.item("R-2")["status"], "doing")

    def test_an_item_with_a_sha_in_its_evidence_is_refused_with_decision(self):
        self.prepare_no_code()
        self.assertEqual(self.cli("ledger", "evidence", "R-3", "--sha", self.sha).returncode, 0)
        r = self.cli("ledger", "done", "R-3", "--decision", "I-1", "--outcome", OUTCOME)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("R-3 was built", r.stdout + r.stderr)
        self.assertEqual(self.item("R-3")["status"], "doing")

    def test_a_decision_item_with_no_commit_closes_and_a_commit_naming_another_item_does_not_block_it(self):
        self.prepare_no_code()
        self.commit("R-3: built another thing")
        r = self.cli("ledger", "done", "R-4", "--decision", "I-1", "--outcome", OUTCOME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.item("R-4")["status"], "done")

    def test_the_commit_match_is_a_whole_item_id_r12_is_not_r123(self):
        self.prepare_no_code()
        data = sl.load_ledger(self.project)
        for _ in range(5, 13):
            sl.add_item(data, "more", ["legacy"])
        sl.save_ledger(self.project, data)
        self.commit("R-123: a commit of another item")
        r = self.cli("ledger", "done", "R-12", "--decision", "I-1", "--outcome", OUTCOME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.commit("R-11 built")
        r = self.cli("ledger", "done", "R-11", "--decision", "I-1", "--outcome", OUTCOME)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("R-11 was built", r.stdout + r.stderr)

    def test_a_batch_row_error_never_echoes_the_rows_own_token(self):
        batch = os.path.join(self.project, ".budgit", "batch.txt")
        with open(batch, "w") as f:
            f.write("R-1\tdecision %s\t%s\nR-2\trule %s\t%s\n" % (self.TOKEN24, OUTCOME, self.TOKEN24R, OUTCOME))
        r = self.cli("ledger", "done", "--file", batch)
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        for token in (self.TOKEN24, self.TOKEN24R):
            self.assertEqual(len(token), 24)
            leaked = [token[i:i + 6] for i in range(len(token) - 5) if token[i:i + 6] in out]
            self.assertEqual(leaked, [], "part of the row's token is in the output")
        self.assertIn("R-1: refused, the proof in this row is not valid", r.stdout)

    TOKEN24 = "I-" + "q7Zk3vXp" + "9LmW2sRt" + "5YbN8c"                       # built from pieces: no literal key-shaped string in the file
    TOKEN24R = "zq9XkV7m" + "Pw3LbT5n" + "Rj8Y" + ":9" + "xx"

    def test_the_audit_line_of_done_is_redacted_before_it_is_cut(self):
        token = "sk-" + "A" * 30
        evidence = "x" * 70 + " " + token          # the token starts at character 71 and runs past the cut at 80
        data = sl.load_ledger(self.project)
        sl.done_item(self.project, data, "R-1", evidence, outcome=OUTCOME, no_code=True)
        sl.save_ledger(self.project, data)
        line = " ".join(self.audit_lines("R-1 done"))
        self.assertNotIn("AAAA", line)
        self.assertIn("proof " + "x" * 70, line)

    def test_a_project_where_git_cannot_answer_counts_every_item_as_built(self):
        nogit = os.path.join(self.tmp, "nogit")
        os.makedirs(nogit)
        self.assertTrue(sl._is_code_item(nogit, {"id": "R-1"}))
        self.assertFalse(sl._is_code_item(self.project, {"id": "R-1"}))
        self.assertTrue(sl._is_code_item(self.project, {"id": "R-1", "changes": 5}))

    def test_audit_lines_and_stored_proof_of_the_new_commands_are_redacted(self):
        token = "sk-" + "A" * 30
        r = self.cli("ledger", "evidence", "R-1", "--sha", self.sha, "--test", token, "--report", "/x/" + token)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(token, " ".join(self.audit_lines("R-1 evidence")))
        self.assertNotIn(token, self.item("R-1")["evidence"])
        name = token + ".md"
        with open(os.path.join(self.project, name), "w") as f:
            f.write("one\n")
        r = self.cli("ledger", "done", "R-2", "--rule", name + ":1", "--outcome", OUTCOME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(token, " ".join(self.audit_lines("R-2 done")))
        self.assertNotIn(token, self.item("R-2")["evidence"])

    def test_a_batch_file_row_for_a_code_item_is_refused_with_decision(self):
        self.prepare_no_code()
        self.commit("R-2: built the thing")
        batch = os.path.join(self.project, ".budgit", "batch.txt")
        with open(batch, "w") as f:
            f.write("R-2\tdecision I-1\t%s\nR-4\tdecision I-1\t%s\n" % (OUTCOME, OUTCOME))
        r = self.cli("ledger", "done", "--file", batch)
        self.assertIn("R-2: refused, R-2 was built", r.stdout)
        self.assertIn("R-4: ok, done", r.stdout)
