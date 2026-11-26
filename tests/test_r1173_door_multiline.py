"""R-1173 door (I-1774: task handling should not hit blocks): closing items in the project's OWN ledger with
ONE multi-line Bash call needs no yes, where each line alone needs none. Real hook as a subprocess, both guards (real data and
verify folder); temp HOME, temp hub, synthetic project; made-up names. Every test name is the invariant it pins."""
import json
import os

from helpers import protect as _protect
from test_r1173a_ledger_no_yes import Base, CLI
import studiolib as sl

VERIFY_FULL = ".budgit/verify/R-37.md"
VERIFY_SHORT = "verify/R-37.md"
APP_DATA = "~/Library/Application Support/SynthApp"
PATTERN_SETS = ([".budgit"], [".budgit/verify"], [".budgit/**"], ["**"], ["clientes.xlsx"], [APP_DATA])


def close_line(word, evidence, outcome="You see it"):
    return '%s ledger done R-37 --evidence "commit abc1234, merged def5678; %s PASS test_thing" --outcome "%s"' % (word, evidence, outcome)


def seven(word, evidence, outcome="You see it"):
    out = [close_line(word, evidence, outcome)]
    for k in range(2, 8):
        out.append('%s ledger done R-%d --decision I-%d --outcome "You see it"' % (word, 37 + k, 100 + k))
    return "\n".join(out)


class DoorBase(Base):
    def set_patterns(self, patterns, registered=True):
        if registered:
            _protect(self.home, self.project)                 # the project is in protected_dirs: the whole folder is real data
        path = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(path)) if os.path.exists(path) else {}
        cfg["protected_paths"] = patterns
        with open(path, "w") as f:
            json.dump(cfg, f)
        with open(os.path.join(self.project, "clientes.xlsx"), "w") as f:
            f.write("one\n")


class MultiLineCloseTest(DoorBase):
    def test_seven_own_closes_in_one_call_need_no_yes(self):
        for patterns in PATTERN_SETS:
            self.set_patterns(patterns)
            for evidence in (VERIFY_FULL, VERIFY_SHORT, "report"):
                for lane in (False, True):
                    got = self.bash(seven(CLI, evidence), lane)
                    self.assertEqual(got[0], "allow", (patterns, evidence, lane, got))
        self.set_patterns(["clientes.xlsx"])
        got = self.bash(seven(CLI, "report", outcome="clientes.xlsx refreshed"), False)      # a data file named in an outcome
        self.assertEqual(got[0], "allow", got)

    def test_seven_own_closes_pass_in_a_protected_dirs_project_with_an_external_app_data_path(self):
        self.set_patterns([APP_DATA], registered=True)
        for lane in (False, True):
            got = self.bash(seven(CLI, VERIFY_FULL), lane)
            self.assertEqual(got[0], "allow", (lane, got))
            got = self.bash(seven(CLI, VERIFY_SHORT), lane)
            self.assertEqual(got[0], "allow", (lane, got))

    def test_seven_own_closes_that_name_the_project_root_pass_in_a_protected_dirs_project_with_an_external_app_data_path(self):
        self.set_patterns([APP_DATA, ".budgit"], registered=True)
        rooted = seven("%s --project %s" % (CLI, self.project), VERIFY_FULL)
        for lane in (False, True):
            got = self.bash(rooted, lane)
            self.assertEqual(got[0], "allow", (lane, got))
            eq = rooted.replace("--project %s" % self.project, "--project=%s" % self.project)
            one = self.bash(eq.split("\n")[0], lane)
            got = self.bash(eq, lane)
            self.assertEqual(got[0] == "allow", one[0] == "allow", (lane, "=", one, got))     # as a single line is judged

    def test_a_bare_budgit_name_on_a_path_that_finds_the_own_file_passes_too(self):
        self.set_patterns([".budgit"])
        env = dict(self.env(), PATH=os.path.dirname(CLI) + ":/usr/bin:/bin")
        self.env = lambda: env
        got = self.bash(seven("budgit", VERIFY_FULL), False)
        self.assertEqual(got[0], "allow", got)

    def test_a_write_into_the_app_data_folder_still_asks(self):
        self.set_patterns([APP_DATA])
        app = os.path.join(self.fake_home, "Library", "Application Support", "SynthApp")
        os.makedirs(app)
        for cmd in ("echo x > '%s/data.db'" % app, seven(CLI, "report") + "\necho x > '%s/data.db'" % app):
            for lane in (False, True):
                got = self.bash(cmd, lane)
                self.assertNotEqual(got[0], "allow", (cmd[-40:], lane, got))

    def test_a_real_write_chained_with_own_closes_still_asks(self):
        self.set_patterns(["clientes.xlsx"])
        target = os.path.join(self.project, "clientes.xlsx")
        for tail in ("echo x > " + target, "cp /etc/hosts " + target, "echo x >> " + target):
            for sep in ("\n", " ; ", " && "):
                got = self.bash(seven(CLI, "report") + sep + tail, False)
                self.assertNotEqual(got[0], "allow", (tail, sep, got))

    def test_closes_naming_another_project_chained_still_ask(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(os.path.join(other, ".budgit"))
        _protect(self.home, other)
        with open(os.path.join(other, ".budgit", "project.json"), "w") as f:
            json.dump({"protected_paths": ["**"]}, f)
        self.set_patterns([".budgit"])
        foreign = '%s --project %s ledger done R-1 --decision I-1 --outcome "x"' % (CLI, other)
        lines = seven(CLI, "report").split("\n")
        self.assertFalse(sl._own_task_lines("\n".join(lines[:3] + [foreign]), self.project, [".budgit"], self.project))
        self.assertTrue(sl._own_task_lines("\n".join(lines[:3]), self.project, [".budgit"], self.project))
        # R-1177 is parked: the fix widens nothing. The multi-line call with a foreign line is judged as the foreign line alone is
        single = self.bash(foreign, False)
        multi = self.bash("\n".join(lines[:3] + [foreign]), False)
        self.assertEqual(single[0] == "allow", multi[0] == "allow", (single, multi))

    def test_a_cd_then_close_chain_is_judged_as_before(self):
        patterns = [".budgit", "data/real/**"]
        self.set_patterns(patterns)
        data = os.path.join(self.project, "data", "real")
        os.makedirs(data, exist_ok=True)
        cmd = "cd %s\n%s" % (data, seven(CLI, "report"))
        self.assertFalse(sl._own_task_lines(cmd, self.project, patterns, data))     # the new rule never takes a call with a cd
        got = self.bash(cmd, False)
        self.assertNotEqual(got[0], "allow", got)

    def test_a_verify_folder_redirect_on_any_line_stays_refused(self):
        verify = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        lines = seven(CLI, VERIFY_FULL).split("\n")
        for pos in (0, 3, 7):
            for tail in ("echo x > " + verify, "printf x >> " + verify, "echo y | tee " + verify):
                chained = lines[:pos] + [tail] + lines[pos:]
                for lane in (False, True):
                    got = self.bash("\n".join(chained), lane)
                    self.assertEqual(got[0], "deny", (pos, tail, lane, got))

    def test_a_close_with_an_operator_or_a_redirect_on_a_line_is_not_a_plain_own_line(self):
        for line in (close_line(CLI, "report") + " > out.txt", close_line(CLI, "report") + " ; true",
                     close_line(CLI, "report") + " | cat", close_line(CLI, "report") + " && true",
                     close_line(CLI, "report").replace("done R-37", "done $R"), "true"):
            cmd = seven(CLI, "report") + "\n" + line
            self.assertFalse(sl._own_task_lines(cmd, self.project, [".budgit"], self.project), line)
            self.assertFalse(sl._own_close_lines(cmd, self.project), line)


class SecurityRound2Test(DoorBase):
    def call(self, *words):
        return [CLI, "ledger", "done", "R-1", *words, "--evidence", "x"]

    def test_a_tilde_project_value_is_not_a_task_call(self):
        link = os.path.join(self.project, "~")
        os.makedirs(link)
        os.symlink(self.project, os.path.join(link, "B"))        # a planted `./~/B` that resolves to the bound project
        self.assertFalse(sl._task_project_ok("~/B", self.project, self.project))
        self.assertFalse(sl._task_project_ok("~", self.project, self.project))
        self.assertFalse(sl._task_call(self.call("--project", "~/B"), self.project, self.project))
        self.assertFalse(sl._task_call(self.call("--project=~/B"), self.project, self.project))
        self.assertTrue(sl._task_call(self.call("--project", self.project), self.project, self.project))

    def test_a_relative_project_value_resolves_against_the_call_folder(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        self.assertTrue(sl._task_project_ok(".", self.project, self.project))
        self.assertFalse(sl._task_project_ok(".", self.project, elsewhere))
        self.assertTrue(sl._task_project_ok("../proj", self.project, elsewhere))
        self.assertTrue(sl._task_call(self.call("--project", "."), self.project, self.project))
        self.assertTrue(sl._task_call(self.call("--project=."), self.project, self.project))
        self.assertFalse(sl._task_call(self.call("--project", "."), self.project, elsewhere))
        self.assertFalse(sl._task_call(self.call("--project=."), self.project, elsewhere))

    def test_a_joined_flag_value_with_a_space_naming_the_verify_folder_stays_refused(self):
        verify = ".budgit/verify/R 1.md"
        for flag in ("--out=\"%s\"" % verify, "--report=\"%s\"" % verify, "--out=\"%s/.budgit/verify/R 1.md\"" % self.project):
            toks = [CLI, "ledger", "evidence", "R-1", "--sha", self.sha, sl._lex_plain(flag)[0][0][0][0]]
            self.assertFalse(sl._budgit_values_clean(toks, self.project), flag)
            for lane in (False, True):
                got = self.bash("%s ledger evidence R-1 --sha %s %s" % (CLI, self.sha, flag), lane)
                self.assertEqual(got[0], "deny", (flag, lane, got))

    def test_a_value_with_a_leading_space_naming_the_verify_folder_stays_refused(self):
        toks = [CLI, "ledger", "evidence", "R-1", "--sha", self.sha, "--report", " .budgit/verify/R 1.md"]
        self.assertFalse(sl._budgit_values_clean(toks, self.project))
        toks[-1] = "see .budgit/verify/R-1.md and verify PASS"      # prose stays text
        self.assertTrue(sl._budgit_values_clean(toks, self.project))

    def test_one_plain_report_path_naming_the_verify_folder_is_read_not_written(self):
        base = [CLI, "ledger", "evidence", "R-1", "--sha", self.sha]
        self.assertTrue(sl._budgit_values_clean(base + ["--report=.budgit/verify/R-1.md"], self.project))
        self.assertTrue(sl._budgit_values_clean(base + ["--report", ".budgit/verify/R-1.md"], self.project))
