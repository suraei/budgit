"""R-1360 F13: a technician runs `budgit suite` for main or a lane worktree with no click.

Fixture (synthetic names, temp HOME): a real `git init` hub "main" with plugin/bin/budgit and the pinned tools/runtests.py, lanes added
with `git worktree add` under <home>/studio-wt, and a fake INSTALLED plugin copy (no .git) that plays the running hook: its own binary
is the installed one. Causes on the old code: 1a = another checkout's CLI refused as "other"; 1b = the installed copy has no .git, so no
lane is a hub worktree; 1c = any --project (or a suite without --cwd-project) turns the row into "home".

Allowed set (pinned by test_the_allowed_set_is_the_corpus_forms): main CLI with --project <main|lane> [--background]; the installed CLI
with --cwd-project --background from a lane; the installed CLI with --project <lane>. Kept beyond the literal corpus, on purpose: the
installed CLI with --project <main> (the same project the main CLI may name) and the class flags --rerun/--wait/--report (the exact
R-1101 flags the cwd form already had; they change queueing and the cache, never a write path).

Isolation of each new check: every refused row has an allowed twin that differs only by that check's input (hub pin: twin with the
pinned runner; protected: twin not listed; digest: approval present; studio-wt parent: lane vs elsewhere; islink / hard link: the
pristine file). The `os.path.realpath(root) != root` test is not isolated on purpose: `suite_repo_root(root) != root` refuses the same
inputs (it returns the realpath), so a row cannot tell them apart; the rows below pin the combined behaviour.
"""
import os
import shutil
import subprocess
import unittest
from unittest import mock

import studiolib as sl
from helpers import StudioTestCase, patch_env

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_CLI = os.path.join(REPO, "plugin", "bin", "budgit")
REAL_RUNNER = os.path.join(REPO, "tools", "runtests.py")
GIT = ["git", "-c", "user.name=acme", "-c", "user.email=acme@example.com", "-c", "commit.gpgsign=false"]
JOB = "20261113-101010-abcdef"


def run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class SuiteNoClick(StudioTestCase):
    def make_hub(self, path, runner=True):
        os.makedirs(os.path.join(path, "plugin", "bin"))
        shutil.copy(REAL_CLI, os.path.join(path, "plugin", "bin", "budgit"))
        adds = ["plugin"]
        if runner:
            os.makedirs(os.path.join(path, "tools"))
            shutil.copy(REAL_RUNNER, os.path.join(path, "tools", "runtests.py"))
            adds.append("tools")
        run(GIT + ["init", "-q", "-b", "main"], path)
        run(GIT + ["add"] + adds, path)
        run(GIT + ["commit", "-q", "-m", "init"], path)
        return path

    def setUp(self):
        super().setUp()
        self.base = base = os.path.realpath(self.tmp)
        self.home = os.path.join(base, "home")
        os.makedirs(os.path.join(self.home, "studio-wt"))
        patch_env(self, HOME=self.home, STUDIO_HOME=os.path.join(base, "bhome"), BUDGIT_HOME=None)
        self.main = self.make_hub(os.path.join(base, "acme-main"))
        self.lane = os.path.join(self.home, "studio-wt", "lane1")
        run(GIT + ["worktree", "add", "-q", "-b", "lane1", self.lane], self.main)
        self.elsewhere = os.path.join(base, "elsewhere-wt")            # a real worktree of main, but not under studio-wt
        run(GIT + ["worktree", "add", "-q", "-b", "else1", self.elsewhere], self.main)
        self.inst = os.path.join(base, "inst", "0.13.9.44")             # the installed copy: no .git, plays the running hook
        os.makedirs(os.path.join(self.inst, "bin"))
        shutil.copy(REAL_CLI, os.path.join(self.inst, "bin", "budgit"))
        self.inst_cli = os.path.join(self.inst, "bin", "budgit")
        self.outside = os.path.join(base, "outside")                   # a plain folder
        os.makedirs(os.path.join(self.outside, "plugin", "bin"))
        shutil.copy(REAL_CLI, os.path.join(self.outside, "plugin", "bin", "budgit"))
        self.link = os.path.join(self.home, "studio-wt", "link1")
        os.symlink(self.lane, self.link)
        self.main_cli = os.path.join(self.main, "plugin", "bin", "budgit")
        self.lane_cli = os.path.join(self.lane, "plugin", "bin", "budgit")
        self.other_cli = os.path.join(self.outside, "plugin", "bin", "budgit")
        self.cfg = {"hub": self.main, "protected_dirs": []}               # round 4: the configured Budgit hub is main; a test names another repo the hub on purpose
        for p in (mock.patch.object(sl, "_own_budgit", lambda: self.inst_cli), mock.patch.object(sl, "studio_root", lambda: self.inst),
                  mock.patch.object(sl, "load_project_config", lambda *_a, **_k: {}),
                  mock.patch.object(sl, "load_config", lambda *_a, **_k: self.cfg)):
            p.start()
            self.addCleanup(p.stop)

    def decide(self, command, cwd=None, project=None):
        return sl._home_write_decision("Bash", {"command": command}, cwd=cwd or self.lane, box={"project": project or self.main},
                                       subagent=True)

    def assertAllowed(self, command, cwd=None, project=None):
        got = self.decide(command, cwd, project)
        self.assertFalse(got and got[0] in ("deny", "ask"), (command, got))

    def assertDenied(self, command, cwd=None, project=None):
        got = self.decide(command, cwd, project)
        self.assertTrue(got and got[0] == "deny", (command, got))

    # ---- allowed (each fails on f158cc68; the cause is named) -------------------------------------------------------------
    def test_corpus_main_cli_suite_project_main(self):                  # 23 Nov 23:15: causes 1a, 1b, 1c
        self.assertAllowed("%s suite --project %s" % (self.main_cli, self.main))
        self.assertAllowed("%s suite --project %s --background" % (self.main_cli, self.main), self.main)

    def test_corpus_installed_cli_suite_cwd_project_background_from_a_lane(self):   # F9: cause 1b (the installed copy has no .git)
        self.assertAllowed("%s suite --cwd-project --background" % self.inst_cli)

    def test_main_cli_runs_suite_for_a_lane(self):                       # causes 1a, 1c
        self.assertAllowed("%s suite --project %s" % (self.main_cli, self.lane))
        self.assertAllowed("%s suite --project %s --background" % (self.main_cli, self.lane))

    def test_installed_cli_suite_project_lane_and_main(self):            # causes 1b, 1c
        self.assertAllowed("%s suite --project %s" % (self.inst_cli, self.lane))
        self.assertAllowed("%s suite --project %s --background" % (self.inst_cli, self.main))

    def test_the_allowed_set_is_the_corpus_forms(self):
        main, lane, inst, mc = self.main, self.lane, self.inst_cli, self.main_cli
        allowed = [(mc, "suite --project %s" % main), (mc, "suite --project %s --background" % main),
                   (mc, "suite --project %s" % lane), (mc, "suite --project %s --background" % lane),
                   (inst, "suite --cwd-project --background"), (inst, "suite --project %s" % lane),
                   (inst, "suite --project %s" % main),                                            # kept: see the module doc
                   (inst, "suite --cwd-project"), (inst, "suite --cwd-project --rerun --wait"), (inst, "suite --cwd-project --report"),
                   (mc, "suite --project %s --rerun --wait" % lane)]                                # kept: the R-1101 class flags
        for cli, line in allowed:
            self.assertAllowed("%s %s" % (cli, line))
        wider = [(mc, "suite --cwd-project"), (mc, "suite --cwd-project --background"), (mc, "suite --project=%s" % lane),
                 (mc, "suite"), (mc, "wait " + JOB), (mc, "test"), (mc, "ledger add x"), (mc, "status"), (mc, "names-check"),
                 (self.lane_cli, "suite --project %s" % lane), (self.lane_cli, "suite --project %s --background" % self.main),
                 (self.lane_cli, "suite --cwd-project"), (inst, "suite"), (inst, "suite --project=%s" % lane),
                 (inst, "suite --project %s --cwd-project" % lane), (inst, "suite --release"), (inst, "suite --cwd-project -- echo hi")]
        for cli, line in wider:
            self.assertDenied("%s %s" % (cli, line))

    # ---- M2: a lane's own CLI is unpinned builder code ----------------------------------------------------------------------
    def test_a_lane_cli_is_refused_in_every_form(self):
        for line in ("suite --project %s" % self.lane, "suite --project %s" % self.main, "suite --cwd-project",
                     "suite --cwd-project --background", "suite --project %s --background" % self.lane):
            self.assertDenied("%s %s" % (self.lane_cli, line))

    # ---- M1: the bound project must be the Budgit hub ----------------------------------------------------------------------
    def test_a_bound_non_hub_repo_is_refused_and_its_hub_twin_is_allowed(self):
        nohub = self.make_hub(os.path.join(self.base, "acme-nohub"), runner=False)
        twin = self.make_hub(os.path.join(self.base, "acme-twin"))
        self.cfg["hub"] = twin                                           # round 4: the twin is allowed only because the config names it the hub
        for repo, ok in ((twin, True), (nohub, False)):
            lines = ("%s suite --project %s" % (self.inst_cli, repo), "%s suite --cwd-project" % self.inst_cli,
                     "%s suite --project %s" % (os.path.join(repo, "plugin", "bin", "budgit"), repo))
            for line in lines:
                (self.assertAllowed if ok else self.assertDenied)(line, cwd=repo, project=repo)

    def test_the_runner_pin_must_match_exactly(self):
        twin = self.make_hub(os.path.join(self.base, "acme-twin"))
        self.cfg["hub"] = twin                                           # round 4: named the hub, so only the runner's content or form can refuse it
        line = "%s suite --project %s" % (self.inst_cli, twin)
        self.assertAllowed(line, cwd=twin, project=twin)
        runner = os.path.join(twin, "tools", "runtests.py")
        with open(runner, "a") as fh:
            fh.write("# edited\n")
        self.assertDenied(line, cwd=twin, project=twin)                  # a different hash is not the hub
        os.remove(runner)
        os.symlink(REAL_RUNNER, runner)
        self.assertDenied(line, cwd=twin, project=twin)                  # the right content behind a link is not the file
        os.remove(runner)
        self.assertDenied(line, cwd=twin, project=twin)

    def test_a_separate_repo_with_a_copied_runner_is_not_the_hub(self):       # round 4 M1 check 1: realpath(project) == the configured hub
        twin = self.make_hub(os.path.join(self.base, "acme-twin"))          # the pinned runner copied, committed: same bytes as main's
        lines = ("%s suite --project %s" % (self.inst_cli, twin), "%s suite --cwd-project" % self.inst_cli,
                 "%s suite --project %s" % (os.path.join(twin, "plugin", "bin", "budgit"), twin))
        for line in lines:
            self.assertDenied(line, cwd=twin, project=twin)                  # the config names main the hub, not the twin
        self.cfg["hub"] = twin
        for line in lines:
            self.assertAllowed(line, cwd=twin, project=twin)                 # twin of the row: the only change is the configured hub
        self.cfg["hub"] = None
        self.assertDenied(lines[0], cwd=twin, project=twin)                  # no hub set: fail closed

    def test_a_tools_symlink_is_refused_even_for_the_configured_hub(self):  # round 4 M1 check 2: realpath(runner) == <project>/tools/runtests.py
        plain = self.make_hub(os.path.join(self.base, "acme-plain"))
        linked = self.make_hub(os.path.join(self.base, "acme-linked"), runner=False)
        os.symlink(os.path.join(self.main, "tools"), os.path.join(linked, "tools"))    # `tools` is a link to the real runner's folder
        for repo, ok in ((plain, True), (linked, False)):
            self.cfg["hub"] = repo                                           # named the hub: the content hash and the hub path both pass
            line = "%s suite --project %s" % (self.inst_cli, repo)
            (self.assertAllowed if ok else self.assertDenied)(line, cwd=repo, project=repo)

    def test_a_protected_bound_project_is_refused_and_its_unlisted_twin_is_allowed(self):
        twin = self.make_hub(os.path.join(self.base, "acme-twin"))
        line = "%s suite --project %s" % (self.inst_cli, twin)
        self.cfg["hub"] = twin                                           # round 4: named the hub, so only the protected list can refuse it
        self.assertAllowed(line, cwd=twin, project=twin)
        for cfg in ({"protected_dirs": [twin]}, {"protected_dirs": [os.path.dirname(twin)]}, {"protected_dirs": [5]}, {"protected_dirs": "x"}):
            with mock.patch.object(sl, "load_config", lambda *_a, _c=dict(cfg, hub=twin), **_k: _c):
                self.assertDenied(line, cwd=twin, project=twin)
                self.assertDenied("%s suite --cwd-project" % self.inst_cli, cwd=twin, project=twin)
        with mock.patch.object(sl, "load_config", lambda *_a, **_k: {"hub": twin, "protected_dirs": [self.outside]}):
            self.assertAllowed(line, cwd=twin, project=twin)             # another entry does not matter

    # ---- the file at plugin/bin/budgit -------------------------------------------------------------------------------------
    def test_a_symlink_or_hard_link_at_the_main_cli_is_refused(self):
        for kind in ("pristine", "symlink", "hardlink"):
            twin = self.make_hub(os.path.join(self.base, "acme-" + kind))
            cli = os.path.join(twin, "plugin", "bin", "budgit")
            self.cfg["hub"] = twin                                       # round 4: named the hub, so only the file at plugin/bin/budgit can refuse it
            if kind != "pristine":
                os.remove(cli)
                (os.symlink if kind == "symlink" else os.link)(self.main_cli, cli)
            line = "%s suite --project %s" % (cli, twin)
            (self.assertAllowed if kind == "pristine" else self.assertDenied)(line, cwd=twin, project=twin)

    def test_a_second_name_for_the_main_cli_file_is_refused(self):
        self.assertAllowed("%s suite --project %s" % (self.main_cli, self.lane))
        os.link(self.main_cli, os.path.join(self.base, "spare-budgit"))   # the main file now has two names
        self.assertDenied("%s suite --project %s" % (self.main_cli, self.lane))

    # ---- still refused -----------------------------------------------------------------------------------------------------
    def test_chained_redirected_env_prefixed_are_refused(self):
        base = "%s suite --project %s" % (self.main_cli, self.lane)
        for tail in (" && echo hi", " ; echo hi", " | cat", " > out.txt", " >> out.txt", " 2>&1", " < in.txt", " `echo hi`", " $(echo hi)",
                     " {a,b}", " *", " ?", " [a]", "\r echo hi", "\t--background", "\n echo hi"):
            self.assertDenied(base + tail)
        self.assertDenied("echo hi && " + base)
        self.assertDenied("FOO=1 " + base)
        self.assertDenied("env FOO=1 " + base)
        self.assertDenied("%s suite --cwd-project --background && echo hi" % self.inst_cli)
        self.assertDenied("FOO=1 %s suite --cwd-project" % self.inst_cli)
        self.assertDenied("%s suite --project $HOME/studio-wt/lane1" % self.main_cli)
        self.assertDenied("%s\tsuite --project %s" % (self.main_cli, self.lane))
        self.assertDenied("%s suite --project\t%s" % (self.inst_cli, self.lane))

    def test_each_refused_character_alone_turns_an_allowed_call_into_home(self):
        words = [self.inst_cli, "suite", "--project", self.lane]
        base = " ".join(words)
        ok = sl._cli_call(words, words, base, self.lane, None, project=self.main, user_home=self.home)
        self.assertEqual(ok[1][0], "suite")
        for ch in ";|&<>`\n\r\t$(){}*?[":
            call = sl._cli_call(words, words, base + " " + ch, self.lane, None, project=self.main, user_home=self.home)
            self.assertTrue(call is None or call[1][0] == "home", repr(ch))

    def test_a_lookalike_binary_is_refused(self):
        self.assertDenied("%s suite --project %s" % (self.other_cli, self.lane))
        self.assertDenied("%s suite --project %s" % (self.other_cli, self.outside))
        self.assertDenied("%s suite --project %s" % (os.path.join(self.elsewhere, "plugin", "bin", "budgit"), self.elsewhere))   # a worktree outside studio-wt
        self.assertDenied("%s suite --project %s" % (os.path.join(self.main, "plugin", "bin", "..", "bin", "budgit"), self.main))
        self.assertDenied("%s suite --project %s" % (os.path.join(self.link, "plugin", "bin", "budgit"), self.lane))
        self.assertDenied("%s suite --project %s" % (self.main_cli.replace("/plugin/", "/PLUGIN/"), self.lane))      # case variants
        self.assertDenied("%s suite --project %s" % (self.main_cli.replace("/acme-main/", "/ACME-MAIN/"), self.lane))
        words = [self.main_cli.replace("/budgit", "/BUDGIT"), "suite", "--project", self.lane]      # not named budgit: no suite class (the general rules judge it)
        self.assertIsNone(sl._cli_call(words, words, " ".join(words), self.lane, None, project=self.main, user_home=self.home))

    def test_project_must_be_main_or_a_real_direct_lane(self):
        for path in (self.outside, self.elsewhere, self.home, os.path.join(self.lane, "plugin"), self.link,
                     self.lane + "/../lane1", self.lane + "/", os.path.join(self.lane, ".."), "relative/lane1", "/",
                     self.lane.replace("/studio-wt/", "/STUDIO-WT/"), self.lane.replace("/lane1", "/LANE1"),
                     self.main.replace("/acme-main", "/ACME-MAIN")):
            self.assertDenied("%s suite --project %s" % (self.main_cli, path))
            self.assertDenied("%s suite --project %s" % (self.inst_cli, path))

    def test_the_studio_wt_parent_check_is_what_refuses_a_real_worktree_elsewhere(self):
        self.assertAllowed("%s suite --project %s" % (self.inst_cli, self.lane))          # twin: a real worktree of main under studio-wt
        self.assertDenied("%s suite --project %s" % (self.inst_cli, self.elsewhere))      # same repo, same common dir, same approval: only the parent differs

    def test_abbreviations_and_stray_words_are_refused(self):
        for cli in (self.main_cli, self.inst_cli):
            for tail in ("--proj %s" % self.lane, "--pro %s" % self.lane, "--project %s --back" % self.lane, "--project %s --cwd" % self.lane,
                         "--project %s --cwd-project" % self.lane, "--project %s --project %s" % (self.lane, self.main),
                         "--project %s --release" % self.lane, "--project %s -- echo hi" % self.lane, "--project %s extra" % self.lane,
                         "--project %s --BACKGROUND" % self.lane, "--PROJECT %s" % self.lane):
                self.assertDenied("%s suite %s" % (cli, tail))
        for tail in ("--cwd", "--cwd-proj", "--back", "--cwd-project --back", "--cwd-project --CWD-PROJECT", "--background"):
            self.assertDenied("%s suite %s" % (self.inst_cli, tail))
        self.assertDenied("%s suite" % self.main_cli)                    # neither marker

    def test_any_other_verb_on_the_main_cli_is_refused(self):
        for line in ("ledger add x", "wait " + JOB, "test", "procs --apply", "housekeeping", "rules off x", "status", "names-check",
                     "--help", "suite --help", "ledger add x --project %s" % self.lane):
            self.assertDenied("%s %s" % (self.main_cli, line))

    def test_the_digest_check_is_what_refuses_an_unapproved_test_command(self):
        cfg = {"test_command": "sh acme-tests.sh"}
        with mock.patch.object(sl, "load_project_config", lambda *_a, **_k: cfg), mock.patch.object(sl, "_load_approvals", return_value={}), \
                mock.patch.object(sl, "trust_granted", return_value=True):
            self.assertDenied("%s suite --project %s" % (self.main_cli, self.lane))
            self.assertDenied("%s suite --cwd-project" % self.inst_cli)
        digest = {self.lane + "|test_command": sl._test_digest(self.lane, "sh acme-tests.sh")}
        with mock.patch.object(sl, "load_project_config", lambda *_a, **_k: cfg), mock.patch.object(sl, "_load_approvals", return_value=digest):
            self.assertAllowed("%s suite --project %s" % (self.main_cli, self.lane))     # twin: approval present
            self.assertAllowed("%s suite --cwd-project" % self.inst_cli)
            self.assertDenied("%s suite --project %s" % (self.main_cli, self.main))      # another folder, another approval

    # ---- receipts ------------------------------------------------------------------------------------------------------------
    def test_the_suite_form_never_gets_a_write_path_and_the_runner_pin_is_untouched(self):
        words = [self.main_cli, "suite", "--project", self.lane, "--background"]
        call = sl._cli_call(words, words, " ".join(words), self.lane, None, project=self.main, user_home=self.home)
        self.assertEqual(call[0], "suite")
        self.assertEqual(call[1], ("suite", (), ()))                     # no path spec: no value of the line is a write target
        self.assertEqual(call[2], [self.lane])                           # the only path is the project, checked as a read target
        for stray in ("--receipt", "--out", "--file", "--record"):
            self.assertDenied("%s suite --project %s %s %s/receipt" % (self.main_cli, self.lane, stray, self.home))
        self.assertEqual(sl.RUNTESTS_SHA256, "176b5c9664fe231004efb7e24ef0d04a917618f80e7338a87b0199a9804c2867")
        self.assertEqual(sl._cli_table()["suite"], ("suite", (), ()))


if __name__ == "__main__":
    unittest.main()
