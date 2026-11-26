"""R-1173c (I-1771, I-1774): the verify-folder guard judges write targets only: a redirect target or a tool's file path. The word
`verify` inside a quoted argument value is text. Real hook as a subprocess; temp HOME, temp hub, temp project with a data folder;
made-up ids. Every test name is the invariant it pins."""
import os

from test_r1173a_ledger_no_yes import Base, CLI, protect


class QuotedWordTest(Base):
    def target(self, name="notes.md"):
        return os.path.join(self.project, name)

    def link_then_note(self, target, why='"Non-blocking leftovers of the cleanup (verify PASS 32 of 32)"'):
        """The refused shape of 15:50: a Budgit call whose quoted text says `verify`, then a real redirect to another file."""
        return ("%s ledger link R-1 --src I-2 --change \"Folded from the rules audit: more\" --why %s 2>&1 | tail -1; "
                "printf '%%s\\n' \"- 15:50 (date) TM: rules cleanup PASS 32/32. more\" >> %s") % (CLI, why, target)

    def test_a_quoted_word_verify_in_a_budgit_text_value_followed_by_a_redirect_to_another_file_is_not_refused(self):
        for protected in (False, True):
            if protected:
                protect(self.home, self.project)
            for target in (self.target(), self.target(os.path.join(".budgit", "progress-pm.md"))):
                for lane in (False, True):
                    got = self.bash(self.link_then_note(target), lane)
                    self.assertNotEqual(got[0], "deny", (protected, target, lane, got))
                    self.assertNotIn("only budgit:verifier", got[1])

    def test_a_close_with_a_verify_path_in_its_evidence_and_a_later_redirect_to_another_file_is_not_refused(self):
        cmd = ("%s ledger done R-1 --evidence \"verify .budgit/verify/R-1.md PASS; %s test_thing\" --outcome 'x'; "
               "printf 'x' >> %s") % (CLI, self.sha, self.target())
        for lane in (False, True):
            got = self.bash(cmd, lane)
            self.assertNotIn("only budgit:verifier", got[1], (lane, got))

    def test_a_bare_budgit_name_on_a_path_without_it_is_judged_like_the_own_call(self):
        cmd = self.link_then_note(self.target()).replace(CLI, "budgit", 1)
        env = dict(self.env(), PATH="/usr/bin:/bin")
        self.env = lambda: env
        got = self.bash(cmd, False)
        self.assertNotIn("only budgit:verifier", got[1], got)

    def path_with_budgit(self, link):
        binp = os.path.join(self.tmp, "bin")
        os.makedirs(binp, exist_ok=True)
        dest = os.path.join(binp, "budgit")
        if link:
            os.symlink(CLI, dest)
        else:
            with open(CLI) as src, open(dest, "w") as out:
                out.write(src.read())
            os.chmod(dest, 0o755)
        env = dict(self.env(), PATH=binp + ":/usr/bin:/bin")
        self.env = lambda: env

    def test_a_bare_budgit_that_is_a_link_to_the_own_file_is_the_own_call_and_a_copy_is_not(self):
        cmd = self.link_then_note(self.target()).replace(CLI, "budgit", 1)
        self.path_with_budgit(link=True)
        for lane in (False, True):
            got = self.bash(cmd, lane)
            self.assertNotIn("only budgit:verifier", got[1], ("link", lane, got))
        os.unlink(os.path.join(self.tmp, "bin", "budgit"))
        self.path_with_budgit(link=False)
        got = self.bash(cmd, False)
        self.assertEqual(got[0], "deny", ("copy", got))

    def test_a_text_value_of_another_flag_with_a_verify_path_is_text_not_a_target(self):
        for flag in ("--why", "--change", "--note", "--reason"):
            cmd = "%s ledger link R-1 --src I-2 %s \"see .budgit/verify/R-1.md and verify PASS\"; echo x >> %s" % (CLI, flag, self.target())
            got = self.bash(cmd, False)
            self.assertNotIn("only budgit:verifier", got[1], (flag, got))

    def test_a_redirect_into_the_verify_folder_stays_refused(self):
        verify = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        for cmd in ("echo x > %s" % verify, "echo x >> %s" % verify, "printf x >> %s" % verify,
                    "echo y | tee %s" % verify, "cp a %s" % verify, "mv a %s" % verify, "ln -s a %s" % verify,
                    "echo x > .budgit/verify/R-1.md", "printf x >> .budgit/verify/R-1.md",
                    # a Budgit call with a quoted word does not shield a real write after it
                    self.link_then_note(verify), self.link_then_note(".budgit/verify/R-1.md")):
            for lane in (False, True):
                got = self.bash(cmd, lane)
                self.assertEqual(got[0], "deny", (cmd, lane, got))

    def test_a_redirect_target_built_from_a_variable_or_a_substitution_is_not_allowed(self):
        for cmd in ("D=verify; echo x > %s/.budgit/$D/R-1.md" % self.project,
                    "echo x > %s/.budgit/$(echo verify)/R-1.md" % self.project,
                    "echo x > `echo %s/.budgit/verify/R-1.md`" % self.project):
            for lane in (False, True):
                got = self.bash(cmd, lane)
                self.assertNotEqual(got[0], "allow", (cmd, lane, got))

    def test_a_write_or_edit_tool_call_into_the_verify_folder_stays_refused(self):
        path = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        for tool, tin in (("Write", {"file_path": path, "content": "PASS"}),
                          ("Edit", {"file_path": path, "old_string": "a", "new_string": "b"})):
            for lane in (False, True):
                got = self.hook(tool, tin, lane)
                self.assertEqual(got[0], "deny", (tool, lane, got))

    def test_a_path_value_of_a_budgit_flag_that_names_the_verify_folder_stays_refused(self):
        verify = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        for cmd in ("%s ledger link R-1 --src I-2 --why \"%s\"" % (CLI, verify),
                    "%s ledger done R-1 --evidence x --out %s" % (CLI, verify)):
            got = self.bash(cmd, False)
            self.assertEqual(got[0], "deny", (cmd, got))

    def test_ledger_evidence_reads_the_report_it_is_pointed_at(self):
        """The report path of `ledger evidence --report` is what is read, not written: the call passes (a later write still does not)."""
        verify = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        for cmd in ("%s ledger evidence R-1 --sha %s --report %s" % (CLI, self.sha, verify),
                    "%s ledger evidence R-1 --sha %s --report \".budgit/verify/R-1.md\"" % (CLI, self.sha)):
            got = self.bash(cmd, False)
            self.assertNotEqual(got[0], "deny", (cmd, got))
            self.assertEqual(self.bash(cmd + "; echo x > " + verify, False)[0], "deny", cmd)


class StudioFolderProjectTest(QuotedWordTest):
    """The live project keeps its data in `.studio`: the same shapes, with the progress file inside that folder."""
    DATA = ".studio"

    def setUp(self):
        super().setUp()
        for rel in (os.path.join(".budgit", "verify"), ".budgit"):
            path = os.path.join(self.project, rel)
            if os.path.isdir(path) and not os.listdir(path):
                os.rmdir(path)
        os.makedirs(os.path.join(self.project, ".studio", "verify"), exist_ok=True)

    def test_the_live_shape_with_the_progress_file_inside_the_studio_folder_is_not_refused(self):
        target = os.path.join(self.project, ".studio", "progress-pm.md")
        for lane in (False, True):
            got = self.bash(self.link_then_note(target), lane)
            self.assertNotIn("only budgit:verifier", got[1], (lane, got))
