"""R-473: the Budgit-home write guard asks only when a command could really write into the Budgit home. Ordinary commands
(a variable set in the same command, Budgit's own bookkeeping, a note or ledger edit in a heredoc, a test run) are silent; what
the guard still cannot read asks once per session per class through the R-493 memory; a real home write denies every time."""
import json
import os
import unittest

from test_guard_trust import GuardCase, CLI

import studiolib as sl

SESSION = "sess-r473-aaaa1111"
OTHER = "sess-r473-bbbb2222"

SILENT = [
    "P={P}; B={B}; $B --project $P ledger start R-1",
    "P={P}; B=$P/plugin/bin/budgit; true; echo see ~/.studio",
    "P={P}; B={B}; $B --project $P ledger add --text 'the guard on ~/.studio and trust.json asks too often'",
    "{B} --project {P} ledger add --text 'the guard on ~/.studio asks'",
    "{B} --project {P} status",
    "W={P}; python3 -m unittest discover -s $W/tests",
    "W={P}; cd $W && python3 -m unittest discover -s tests",
    "W={P}; python3 -W ignore -m unittest discover -s $W/tests -p 'test_studiolib*.py'",
    "python3 -m pytest tests/test_hook.py",
    "cat > {P}/note.md <<'EOF'\nthe guard on ~/.studio and ~/.budgit trust.json asks too often\nEOF",
    "cat > {P}/note.md <<'EOF'\ndon't write ~/.studio/trust.json\nEOF",
    "python3 - <<'PY'\nimport json\np = '{P}/.studio/ledger.json'\nd = json.load(open(p))\nd['n'] = 'the home ~/.studio is guarded'\n"
    "json.dump(d, open(p, 'w'))\nPY",
    "python3 - <<'PY'\nimport json\np = '{P}/.studio/ledger.json'\nd = json.load(open(p))\njson.dump(d, open(p, 'w'))\nPY\n"
    "{B} --project {P} ledger accept-edit",
    "cd {P} && python3 - <<'PY'\nimport json\nd = json.load(open('.studio/ledger.json'))\njson.dump(d, open('.studio/ledger.json', 'w'))\nPY\n"
    "{B} --project {P} ledger accept-edit",
]

DENIED = [
    "echo x > {U}/.studio/trust.json", "echo x > ~/.budgit/trust.json", "echo x > $HOME/.studio/trust.json",
    "H=~/.studio; cp /tmp/p $H/trust.json", "H={H}; echo x > $H/trust.json", "cd ~ && echo x > .studio/x",
    "cp /tmp/p {U}/.stu*/trust.json", "eval \"cp /tmp/p {U}/.studio/trust.json\"",
    "cat > ~/.studio/trust.json <<EOF\nx\nEOF", "tee {H}/trust.json <<'EOF'\nx\nEOF",
    "python3 - <<'PY'\nopen('{U}/.studio/trust.json', 'w').write('x')\nPY",
    "python3 - <<'PY'\nimport os\nos.system('cp /tmp/p ~/.studio/trust.json')\nPY",
    "bash <<'EOF'\nrm ~/.studio/trust.json\nEOF",
    "HOME={H}; echo x > $HOME/real-data-yes/s.jsonl", "HOME={H}; echo x > $HOME/config.json",
    # the budgit CLI and a test run next to a real write
    "{B} --project {P} ledger status; echo x > {H}/trust.json",
    "python3 -m unittest discover -s {P}/tests; echo x > {H}/trust.json",
]

# A test runner chained with a write into the verifier's seal folder must still be refused by the guards.
SEAL_FORGERIES = {
    "unittest then echo into verify": "python3 -m unittest discover -s {P}/tests; echo PASS > {P}/.budgit/verify/R-1.md",
    "pytest and cp into verify": "python3 -m pytest {P}/tests && cp /tmp/x {P}/.budgit/verify/R-1.md",
    "unittest piped to tee in verify": "python3 -m unittest discover -s {P}/tests 2>&1 | tee {P}/.budgit/verify/R-1.md",
    "unittest redirect into verify": "python3 -m unittest discover -s {P}/tests > {P}/.budgit/verify/R-1.md",
    "variable path into verify": "V={P}/.budgit/verify; python3 -m pytest {P}/tests; echo PASS > $V/R-1.md",
    "unittest then seal hook by hand": "python3 -m unittest discover -s {P}/tests; python3 {P}/plugin/scripts/hook.py post-tool-use",
}

# Commands the guard cannot read but that are not a certain home write: they ask (not deny).
ASKERS = ["cp /tmp/p $X/.studio/trust.json", "echo hi > $X/note; ls ~/.studio", "touch $X/a; cat ~/.budgit/trust.json $Y"]


class HomeR473Case(GuardCase):
    def setUp(self):
        super().setUp()
        self.uhome = os.path.expanduser("~")

    def fill(self, cmd):
        return cmd.replace("{P}", self.root).replace("{B}", CLI).replace("{U}", self.uhome).replace("{H}", sl.home_state())

    def check(self, cmd, session=SESSION, cwd=None):
        return sl.home_write_check("Bash", {"command": self.fill(cmd)}, cwd or self.project, session=session)

    def asking(self):
        got = [(c, self.check(c)) for c in ASKERS]
        return [c for c, (d, keys) in got if d and d[0] == "ask" and keys]

    def say_yes(self, cmd, session=SESSION):
        """What the hook does: PreToolUse asks and notes the call, the user approves, PostToolUse of the same call promotes it."""
        got, keys = self.check(cmd, session)
        self.assertEqual(got[0], "ask")
        self.assertTrue(keys)
        self.assertTrue(sl.note_real_data_ask(session, "Bash", {"command": self.fill(cmd)}, keys))
        return sl.promote_real_data_yes(session, "Bash", {"command": self.fill(cmd)})


class SilentAndDeniedTest(HomeR473Case):
    def test_ordinary_commands_are_silent_at_the_project_and_elsewhere(self):
        for cmd in SILENT:
            for cwd in (self.project, self.tmp):
                self.assertEqual(self.check(cmd, cwd=cwd), (None, []), (cmd, cwd))

    def test_the_budgit_cli_via_a_variable_or_a_literal_path_is_bookkeeping(self):
        for cmd in ("B={B}; $B --project {P} ledger done R-1", "{B} --project {P} ledger add --text 'x ~/.studio'",
                    "P={P}; {B} --project $P ledger ack I-1"):
            self.assertEqual(self.check(cmd)[0], None, cmd)

    def test_a_ledger_edit_then_accept_edit_is_silent(self):
        self.assertEqual(self.check(SILENT[-1]), (None, []))

    def test_a_heredoc_body_that_only_mentions_a_home_writes_elsewhere(self):
        self.assertEqual(self.check(SILENT[9]), (None, []))
        self.assertEqual(self.check(SILENT[10]), (None, []))

    def test_a_real_home_write_denies_every_time(self):
        for cmd in DENIED:
            for cwd in (self.project, self.tmp):
                got, keys = self.check(cmd, cwd=cwd)
                self.assertEqual(got and got[0], "deny", (cmd, cwd))
                self.assertEqual(keys, [], cmd)

    def test_a_chained_test_run_does_not_hide_a_seal_forgery(self):
        for name, cmd in sorted(SEAL_FORGERIES.items()):
            cmd = self.fill(cmd)
            verdicts = [sl.verify_forgery_decision("Bash", {"command": cmd}, None, self.project),
                        sl.home_write_decision("Bash", {"command": cmd}, self.project)]
            self.assertTrue(any(v and v[0] == "deny" for v in verdicts), (name, verdicts))


class MemoryTest(HomeR473Case):
    def setUp(self):
        super().setUp()
        self.cmds = self.asking()
        if not self.cmds:
            self.fail("no unreadable-word command asks any more: update ASKERS")
        self.cmd = self.cmds[0]

    def test_the_first_ask_is_remembered_once_per_session_and_class(self):
        self.assertEqual(self.check(self.cmd)[0][0], "ask")
        self.assertTrue(self.say_yes(self.cmd))
        self.assertEqual(self.check(self.cmd), (None, []))

    def test_without_a_post_tool_use_the_ask_stays(self):
        got, keys = self.check(self.cmd)
        sl.note_real_data_ask(SESSION, "Bash", {"command": self.fill(self.cmd)}, keys)       # asked, and the user said no
        self.assertEqual(self.check(self.cmd)[0][0], "ask")
        self.assertEqual(sl.promote_real_data_yes(SESSION, "Bash", {"command": "ls other"}), [])
        self.assertEqual(self.check(self.cmd)[0][0], "ask")

    def test_another_session_asks_again(self):
        self.say_yes(self.cmd)
        self.assertEqual(self.check(self.cmd, session=OTHER)[0][0], "ask")

    def test_a_corrupt_or_forged_memory_asks_again(self):
        self.say_yes(self.cmd)
        path = os.path.join(sl.home_state(), sl.YES_DIR, SESSION + ".jsonl")
        self.assertEqual(self.check(self.cmd), (None, []))
        for text in ("not json at all\n", json.dumps({"k": "home:word"}) + "\n", "[1, 2]\n"):
            with open(path, "w") as fh:
                fh.write(text)
            self.assertEqual(self.check(self.cmd)[0][0], "ask", text)

    def test_a_forged_row_for_the_class_without_the_hooks_is_not_a_yes(self):
        got, keys = self.check(self.cmd)
        os.makedirs(os.path.join(sl.home_state(), sl.YES_DIR), exist_ok=True)
        with open(os.path.join(sl.home_state(), sl.YES_DIR, OTHER + ".jsonl"), "w") as fh:
            fh.write(json.dumps({"k": keys[0], "fp": "x" * 8}) + "\n")           # no timestamp: not a row the hooks wrote
        self.assertEqual(self.check(self.cmd, session=OTHER)[0][0], "ask")

    def test_a_yes_never_covers_a_deny(self):
        self.say_yes(self.cmd)
        for cmd in DENIED[:6]:
            self.assertEqual(self.check(cmd)[0][0], "deny", cmd)

    def test_the_agent_cannot_write_the_memory_files(self):
        for kind in (sl.YES_DIR, sl.PENDING_DIR):
            target = os.path.join(sl.home_state(), kind, SESSION + ".jsonl")
            for command in ("echo '{}' >> %s" % target, "HOME=%s; echo '{}' >> $HOME/%s/%s.jsonl" % (sl.home_state(), kind, SESSION)):
                got = sl._home_write_decision("Bash", {"command": command}, cwd=self.project)
                self.assertEqual(got[0], "deny", command)


class HomeBoundaryR733Test(HomeR473Case):
    """R-733: a home candidate matches whole path components, and `bash -n FILE` is a read of FILE."""

    def script(self, name, body):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(body)
        return path

    def jobs_script(self):
        job = os.path.join(self.uhome, ".budgit-jobs", "x")
        return self.script(".budgit-jobs/x/weekly.sh", 'JOBDIR=%s\nLOG="$JOBDIR/log"\nprintf "ran\\n" >> "$LOG"\n' % job)

    def trust_script(self):
        return self.script("s.sh", 'printf x >> %s\n' % os.path.join(self.uhome, ".budgit", "trust.json"))

    def test_A_bash_n_on_a_jobs_script_is_allowed(self):
        self.assertIsNone(self.check("bash -n %s" % self.jobs_script())[0])

    def test_B_bash_run_of_a_jobs_script_is_allowed_by_the_boundary(self):
        self.assertIsNone(self.check("bash %s" % self.jobs_script())[0])

    def test_C_controls_stay_denied(self):
        s = self.trust_script()
        self.assertEqual(self.check("bash %s" % s)[0][0], "deny")
        self.assertEqual(self.check("bash -n %s && bash %s" % (s, s))[0][0], "deny")
        self.assertEqual(self.check("bash -n %s > out" % s)[0][0], "deny")
        self.assertEqual(self.check("bash -n %s; rm y" % s)[0][0], "deny")

    def test_D_boundary_matches_whole_components(self):
        names = [os.path.join(self.uhome, ".budgit").lower()]
        yes = lambda t: sl._names_home_in(t, names)
        self.assertTrue(yes(names[0] + "/x"))
        self.assertTrue(yes("cat " + names[0]))
        self.assertTrue(yes("cat '" + names[0] + "'"))
        self.assertFalse(yes(names[0] + "-jobs/x"))
        self.assertFalse(yes(names[0] + "x"))
        self.assertFalse(yes(names[0] + ".bak"))
        self.assertFalse(yes(names[0] + "_old"))
        win = ["c:\\users\\u\\.budgit"]
        self.assertTrue(sl._names_home_in("type c:\\users\\u\\.budgit\\trust.json", win))
        self.assertFalse(sl._names_home_in("type c:\\users\\u\\.budgit-jobs\\x", win))
        studio = [os.path.join(self.uhome, ".studio").lower()]
        self.assertFalse(sl._names_home_in(studio[0] + "2/x", studio))

    def test_E_ordinary_build_script_is_allowed(self):
        build = self.script("proj/build.sh", "echo hi > out.txt\n")
        self.assertIsNone(self.check("bash %s" % build, cwd=os.path.dirname(build))[0])

    def test_F_syntax_check_shape(self):
        self.assertTrue(sl._is_syntax_check(["bash", "-n", "a.sh"]))
        self.assertTrue(sl._is_syntax_check(["/usr/bin/sh", "-n", "~/a-b.sh"]))
        for t in (["bash", "a.sh"], ["bash", "-n", "-x", "a.sh"], ["bash", "-n"], ["bash", "-n", "a", "b"], ["python3", "-n", "a"],
                  ["zsh", "-n", "a.sh"], ["bash", "-x", "a.sh"], ["bash", "-nx", "a.sh"], ["./bash", "-n", "a.sh"], ["/tmp/bash", "-n", "a.sh"], ["bash5", "-n", "a.sh"],
                  ["bash", "-n", "+n"], ["bash", "-n", "-x"], ["bash", "-n", "*"], ["bash", "-n", "a b"]):
            self.assertFalse(sl._is_syntax_check(t), t)

    def test_F2_every_system_shell_folder_is_accepted(self):
        for folder in ("/bin", "/usr/bin", "/opt/homebrew/bin", "/usr/local/bin"):
            for name in ("bash", "sh"):
                shell = folder + "/" + name
                self.assertTrue(sl._is_syntax_check([shell, "-n", "~/.budgit-jobs/legal/weekly-check.sh"]), shell)
                got = self.check("%s -n ~/.budgit-jobs/legal/weekly-check.sh" % shell)[0]
                self.assertIsNone(got, shell)

    def payload(self):
        return self.script("fake-user-home/.budgit/payload.sh", 'printf x >> %s\n' % os.path.join(self.uhome, ".budgit", "trust.json"))

    def test_G_lookalike_heads_and_prefixes_are_not_the_shortcut(self):
        p = self.payload()
        for cmd in ("./bash -n %s", "/tmp/bash -n %s", "PATH=/tmp/x bash -n %s", "env bash -n %s", "env PATH=. bash5 -n %s"):
            got = self.check(cmd % p)[0]
            self.assertEqual(got and got[0], "deny", cmd)

    def test_H_startup_file_variables_are_not_the_shortcut(self):
        p = self.payload()
        harmless = self.script("x.sh", "echo hi\n")
        for var in ("BASH_ENV", "ENV"):
            got = self.check("%s=%s bash -n %s" % (var, p, harmless))[0]
            self.assertEqual(got and got[0], "deny", var)

    def test_K_tilde_startup_file_values_are_expanded(self):
        """BASH_ENV=~/.budgit/p and ENV=~/.studio/p name the home through `~`. Residuals, R-722 class and not seen: a
        BASH_ENV or ENV inherited from the parent environment, and a bare `bash` resolved through the inherited PATH.
        `export BASH_ENV=~/.budgit/p; bash -n x.sh` is pinned in test_L."""
        self.assertEqual(self.check("BASH_ENV=~/.budgit/p bash -n x.sh")[0][0], "deny")
        self.assertEqual(self.check("ENV=~/.studio/p sh -n x.sh")[0][0], "deny")

    def test_L_export_then_bash_n_current_answer(self):
        got = self.check("export BASH_ENV=~/.budgit/p; bash -n x.sh")[0]
        self.assertEqual(got and got[0], "deny")

    def test_I_other_operand_and_flag_shapes_fall_through(self):
        # the shortcut would skip the script scan; these must still reach it
        p = self.payload()
        self.assertEqual(self.check("zsh -n %s" % p)[0][0], "deny")
        self.assertFalse(sl._is_syntax_check(["bash", "-n", "*"]))
        self.assertFalse(sl._is_syntax_check(["bash", "-n", "+n"]))

    def test_J_her_literal_forms_are_allowed(self):
        self.jobs_script()          # under the fake HOME: ~/.budgit-jobs/x/weekly.sh
        legal = self.script("fake-user-home/.budgit-jobs/legal/weekly-check.sh", 'LOG=~/.budgit-jobs/legal/log\nprintf "ran\\n" >> "$LOG"\n')
        self.assertTrue(os.path.isfile(legal))
        for cmd in ("bash -n ~/.budgit-jobs/legal/weekly-check.sh", "/bin/bash -n ~/.budgit-jobs/legal/weekly-check.sh"):
            self.assertIsNone(self.check(cmd)[0], cmd)


if __name__ == "__main__":
    unittest.main()
