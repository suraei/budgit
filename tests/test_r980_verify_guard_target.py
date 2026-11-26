"""R-980 r3: the verify-folder guard stays an ALLOW list. The three real commands of a non-verifier that only NAME the folder
(a read-only head of a report, a printf that appends to the progress file, the own Budgit CLI with the path in its --text)
pass; every write into the folder, every git verb, runner and unknown head that merely carries the path in a message option
stays refused, and the verifier agent still writes there."""
import os
from unittest import mock

from helpers import StudioTestCase
import studiolib as sl


class VerifyGuardTargetTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        for d in (".budgit/verify", ".studio"):
            os.makedirs(os.path.join(self.project, d), exist_ok=True)
        self.v = os.path.join(self.project, ".budgit", "verify")
        self.own = sl._own_budgit()

    def d(self, command, agent="general-purpose"):
        return sl.verify_forgery_decision("Bash", {"command": command}, agent, self.project)

    def assertDenied(self, cmd):
        got = self.d(cmd)
        self.assertIsNotNone(got, cmd)
        self.assertEqual(got[0], "deny", cmd)

    # ---- the three real commands -------------------------------------------------------------------------------
    def test_a_head_of_a_report_by_absolute_path_is_allowed(self):
        self.assertIsNone(self.d("head -1 %s/R-979.md" % self.v))

    def test_a_read_only_heads_from_the_allow_list_are_allowed(self):
        for head in ("head -1", "tail -2", "cat", "wc -l", "grep -c PASS", "ls", "stat", "diff /dev/null", "cmp /dev/null",
                     "sha256sum", "shasum", "md5"):
            self.assertIsNone(self.d("%s %s/R-979.md" % (head, self.v)), head)

    def test_b_printf_naming_the_folder_appended_to_the_progress_file_absolute_is_allowed(self):
        self.assertIsNone(self.d("printf '%%s\\n' '- 15:3x: R-979 PASS, report in .budgit/verify/R-979.md' >> %s/.studio/progress-tm.md"
                                 % self.project))

    def test_b_printf_naming_the_folder_appended_to_the_progress_file_relative_is_allowed(self):
        self.assertIsNone(self.d("printf '%s\\n' '- 15:3x: R-979 PASS, report in .budgit/verify/R-979.md' >> .studio/progress-tm.md"))

    def test_c_own_budgit_cli_with_the_path_only_in_text_is_allowed(self):
        line = ('ledger add --phase 0.13.9.11 --title x --text "see .budgit/verify/R-979.md" --src tm:2026-11-10')
        self.assertIsNone(self.d(self.own + " " + line))
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            self.assertIsNone(self.d("budgit " + line))

    # ---- what stays refused ------------------------------------------------------------------------------------
    def test_a_read_head_with_a_redirect_into_the_folder_stays_refused(self):
        self.assertDenied("head -1 %s/R-979.md > %s/R-1.md" % (self.v, self.v))
        self.assertDenied("printf x >> %s/R-1.md" % self.v)
        self.assertDenied("printf '%%s' 'see .budgit/verify/R-979.md' >> %s/R-1.md" % self.v)
        self.assertDenied("printf '%s' x >> .budgit/verify/R-1.md")

    def test_a_git_verbs_with_a_message_naming_the_folder_stay_refused(self):
        p = self.v + "/R-1.md"
        for cmd in ("git merge -m 'see %s' old", "git merge --message='see %s' old", "git pull -m 'see %s'",
                    "git rebase -m 'see %s' old", "git cherry-pick -m 1 --message='see %s' abc", "git am --message-id 'see %s'",
                    "git reset --hard -m 'see %s'", "git switch -m 'see %s' old", "git clone -m 'see %s' a b",
                    "git worktree add -m 'see %s' w old", "git stash push -m 'see %s'", "git revert -m 1 'see %s'"):
            self.assertDenied(cmd % p)

    def test_a_runners_with_a_text_option_naming_the_folder_stay_refused(self):
        p = ".budgit/verify/R-1.md"
        for head in ("make", "npm", "cargo", "go", "uv", "swift", "foo", "someother"):
            for opt in ("--note='see %s'", "--note=%s", "--title 'see %s'", "--text='see %s'", "-m 'see %s'"):
                self.assertDenied(("%s run " + opt) % (head, p))

    def test_a_non_own_budgit_stays_refused_with_the_path_in_text_or_evidence(self):
        p = self.v + "/R-979.md"
        for cmd in ('fakebudgit ledger add --text "see %s"' % p, "/x/fakebudgit ledger done R-1 --evidence 'x %s'" % p,
                    "./budgit ledger done R-1 --evidence 'x %s'" % p, "/x/mybudgit ledger add --text 'x %s'" % p,
                    "budgit-x ledger done R-1 --evidence 'x %s'" % p, "/x/budgit ledger done R-1 --evidence 'x %s'" % p):
            self.assertDenied(cmd)

    def test_a_non_own_budgit_found_on_the_path_stays_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "budgit")
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(fake, 0o755)
            with mock.patch.dict(os.environ, {"PATH": d}):
                self.assertDenied('budgit ledger done R-1 --evidence "see %s/R-979.md"' % self.v)
                self.assertDenied('budgit ledger add --text "see %s/R-979.md"' % self.v)

    def test_own_budgit_with_a_path_as_a_non_text_value_stays_refused(self):
        self.assertDenied(self.own + " ledger add --text x --out " + self.v + "/R-1.md")

    def test_writers_and_interpreters_stay_refused(self):
        t = self.v + "/R-1.md"
        for cmd in ("echo x > " + t, "printf x >> " + t, "echo x 2> " + t, "tee " + t, "cp /tmp/a " + t, "mv /tmp/a " + t,
                    "dd if=/dev/zero of=" + t, "dd of=" + t + " if=/dev/null", "sed -i '' s/a/b/ " + t, "sed -i s/a/b/ " + t,
                    "perl -i -pe 's/a/b/' " + t, 'sh -c "echo x > %s"' % t, 'bash -c "budgit ledger add --text %s"' % t,
                    "eval budgit ledger add --text " + t, "xargs tee " + t, "echo x | xargs tee " + t,
                    "python3 -c 'open(\"%s\", \"w\")'" % t, "python3 -c 'open(1)' --text " + t, "install /tmp/a " + t,
                    "ln -s /tmp/a " + t, "rsync /tmp/a " + t, "touch " + t, "truncate -s 0 " + t,
                    "git -C %s checkout -- .budgit/verify/R-1.md" % self.project,
                    "git -C %s restore .budgit/verify/R-1.md" % self.project,
                    "foo --out " + t, "foo --out=" + t, "foo --text x --out " + t, "printf x > ~/.budgit/verify/R-1.md"):
            self.assertDenied(cmd)

    def test_tilde_forms_stay_refused(self):
        for w in ("~+/.budgit/verify/R-1.md", "~-/.budgit/verify/R-1.md", "~root/.budgit/verify/R-1.md",
                  "~/.budgit/verify/R-1.md"):
            self.assertDenied("printf x > " + w)
            self.assertDenied("cp /tmp/a " + w)

    def test_write_tools_into_the_folder_stay_refused(self):
        for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("NotebookEdit", "notebook_path")):
            got = sl.verify_forgery_decision(tool, {key: self.v + "/R-1.md"}, "general-purpose", self.project)
            self.assertIsNotNone(got, tool)
            self.assertEqual(got[0], "deny", tool)

    def test_the_verifier_can_still_write_there(self):
        t = self.v + "/R-1.md"
        self.assertIsNone(self.d("echo x > " + t, agent="studio:verifier"))
        self.assertIsNone(sl.verify_forgery_decision("Write", {"file_path": t}, "studio:verifier", self.project))

    def test_interpreters_regex_still_matches_swift_and_uv(self):
        for name in ("swift", "uv", "python3", "node", "perl", "ruby", "osascript"):
            self.assertTrue(sl._INTERPRETERS.match(name), name)
        with open(sl.__file__) as fh:
            self.assertEqual(sum(1 for line in fh if line.startswith("_INTERPRETERS =")), 1)
