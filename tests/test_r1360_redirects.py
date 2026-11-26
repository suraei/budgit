"""R-1360 task D: a stdout or stderr redirect into the CALLING session's own scratchpad is not a write to a guarded place, in the
foreground and in the background; every other target stays refused. The decisions come from hook.on_pre_tool_use with temp
homes and a temp scratchpad (the real <tmp>/claude-<uid>/<project>/<session>/scratchpad shape). Protected projects are
written protected-1 and protected-2.

Two forms were refused only because of the redirect: the pinned runner `python3 -I <wt>/tools/runtests.py --changed > F 2>&1`
(friction 669) and a test run with `2>&1` chained to the commit (friction 846: `&` of `2>&1` split the chain). Both pass when
every redirect target is a plain file in the caller's own scratchpad.
"""
import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import commitguard  # noqa: E402
import studiolib as sl  # noqa: E402
from helpers import StudioTestCase, patch_env, protect  # noqa: E402

SESSION = "sess-r1360d"
OTHER = "sess-other-r1360d"
RUNNER = os.path.join(ROOT, "tools", "runtests.py")
GATE = os.path.join(ROOT, "tools", "gate_test.py")


class Redirects(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.fake_home = os.path.join(self.tmp, "userhome")
        os.makedirs(self.fake_home)
        patch_env(self, HOME=self.fake_home)
        protect(self.home, self.project)
        self.root = "/private/tmp/claude-%d/p1360d-%d" % (os.getuid(), os.getpid())
        self.scratch = os.path.join(self.root, SESSION, "scratchpad")
        self.sibling = os.path.join(self.root, OTHER, "scratchpad")
        for d in (self.scratch, self.sibling):
            os.makedirs(d, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.out = os.path.join(self.scratch, "out.txt")

    def decide(self, command, background=False, agent=None, session=SESSION):
        import hook
        tool_input = {"command": command}
        if background:
            tool_input["run_in_background"] = True
        inp = {"session_id": session, "tool_name": "Bash", "tool_input": tool_input, "cwd": self.project}
        if agent:
            inp["agent_id"] = agent
            inp["agent_type"] = "studio:builder"
        hook._OUT.clear()
        try:
            hook.on_pre_tool_use(inp, self.project)
            out = hook._OUT.get("hookSpecificOutput") or {}
            return out.get("permissionDecision"), out.get("permissionDecisionReason") or ""
        finally:
            hook._OUT.clear()

    def allowed(self, command, **kw):
        got = self.decide(command, **kw)
        self.assertIsNone(got[0], "%r -> %r" % (command, got))

    def refused(self, command, **kw):
        got = self.decide(command, **kw)
        self.assertIn(got[0], ("deny", "ask"), "%r -> %r" % (command, got))
        return got

    def runner(self, tail):
        return "python3 -I %s --changed %s" % (RUNNER, tail)

    # a plain redirect into the scratchpad (already allowed: these stay allowed)
    def test_scratchpad_redirect_is_allowed_in_the_foreground(self):
        for cmd in ("echo hi > %s", "echo hi >> %s", "echo hi > %s 2>&1", "echo hi 2> %s", "echo hi &> %s"):
            self.allowed(cmd % self.out)

    def test_scratchpad_redirect_is_allowed_in_the_background(self):
        for cmd in ("echo hi > %s", "echo hi >> %s", "echo hi > %s 2>&1"):
            self.allowed(cmd % self.out, background=True)

    def test_scratchpad_redirect_is_allowed_for_a_subagent(self):
        self.allowed("echo hi > %s 2>&1" % self.out, agent="agent-1")
        self.allowed("echo hi > %s 2>&1" % self.out, agent="agent-1", background=True)

    # the pinned runner with a scratchpad redirect (friction 669)
    def test_the_pinned_runner_alone_is_allowed(self):
        self.allowed(self.runner(""))

    def test_the_pinned_runner_with_a_scratchpad_redirect_is_allowed_foreground_and_background(self):
        for tail in ("> %s", ">> %s", "> %s 2>&1", "2> %s"):
            self.allowed(self.runner(tail % self.out))
            self.allowed(self.runner(tail % self.out), background=True)
            self.allowed(self.runner(tail % self.out), agent="agent-1")

    def test_the_pinned_runner_redirect_to_home_the_data_folder_or_the_project_is_refused(self):
        targets = (os.path.join(self.fake_home, "x.txt"), os.path.join(self.fake_home, ".studio", "x.txt"),
                   os.path.join(self.home, "x.txt"), os.path.join(self.project, "x.txt"),
                   os.path.join(self.project, self.DATA, "x.txt"), "$HOME/x.txt", "~/x.txt")
        for t in targets:
            self.refused(self.runner("> %s 2>&1" % t))
            self.refused(self.runner("> %s 2>&1" % t), background=True)

    def test_the_pinned_runner_redirect_to_a_symlinked_scratchpad_file_is_refused(self):
        os.symlink(os.path.join(self.fake_home, "victim.txt"), os.path.join(self.scratch, "link.txt"))
        self.refused(self.runner("> %s" % os.path.join(self.scratch, "link.txt")))
        self.refused(self.runner("> %s" % os.path.join(self.scratch, "link.txt")), background=True)
        os.symlink(self.fake_home, os.path.join(self.scratch, "dir"))
        self.refused(self.runner("> %s" % os.path.join(self.scratch, "dir", "x.txt")))

    def test_the_pinned_runner_redirect_with_dotdot_or_another_sessions_scratchpad_is_refused(self):
        self.refused(self.runner("> %s/../../x.txt" % self.scratch))
        self.refused(self.runner("> %s/sub/../../x.txt" % self.scratch), background=True)
        self.refused(self.runner("> %s" % os.path.join(self.sibling, "x.txt")))

    def test_the_pinned_runner_with_a_built_target_or_a_second_command_is_refused(self):
        self.refused(self.runner("> %s/$(echo x).txt" % self.scratch))
        self.refused(self.runner("> %s ; echo hi" % self.out))
        self.refused(self.runner("> %s && echo hi > %s" % (self.out, os.path.join(self.fake_home, "x.txt"))))
        self.refused(self.runner("> %s | tee %s" % (self.out, os.path.join(self.fake_home, "x.txt"))))

    def test_the_pinned_runner_with_a_malformed_or_chained_redirect_is_refused(self):
        home_file = os.path.join(self.fake_home, "x.txt")
        for tail in ("> %s>%s" % (self.out, home_file), ">&%s" % home_file, "> %s\n> %s" % (self.out, home_file),
                     "> '%s" % self.out, "> %s\nrm %s" % (self.out, home_file), "> %s/ 2>&1x" % self.scratch,
                     "> %s `echo`" % self.out):
            self.refused(self.runner(tail))
            self.refused(self.runner(tail), background=True)

    # R-1360 H1: a redirect the shell cannot lex (an unclosed quote) is never a scratchpad redirect. Before the fix the command was
    # refused on a worktree whose name held the word "hook" (the unreadable-command fallback of the persistence guard matched
    # that word in the runner path) and passed on any other path; the paths below do not contain it.
    def test_h1_a_redirect_with_an_unclosed_or_glued_quote_is_refused_for_the_runner_in_every_mode(self):
        o = self.out
        for tail in ("> '%s" % o, '> "%s' % o, "2>'%s" % o, "&>'%s" % o, "> '%s'x" % o, '2> "%s' % o, ">> '%s" % o):
            for kw in ({}, {"background": True}, {"agent": "agent-1"}):
                self.refused(self.runner(tail), **kw)

    def test_h1_an_unclosed_quote_redirect_on_a_plain_command_is_refused(self):
        self.refused("echo hi > '%s" % self.out)
        self.refused("echo hi 2>'%s" % self.out, agent="agent-1")

    def test_h1_the_scratch_exemption_leaves_the_text_when_a_quote_is_open_or_a_redirect_is_left(self):
        o = self.out
        for text in ("x > '%s" % o, 'x > "%s' % o, "x > '%s'x" % o, "x > %s > %s/../y" % (o, self.scratch), "x < %s" % o):
            self.assertEqual(sl._without_scratch_redirects(text, SESSION), text, text)
        self.assertEqual(sl._without_scratch_redirects("x > %s 2>&1" % o, SESSION).strip(), "x")

    def test_a_scratchpad_target_of_a_session_with_no_valid_id_is_refused(self):
        self.refused(self.runner("> %s" % self.out), session="")
        self.refused(self.runner("> %s" % self.out), session="../x")

    def test_a_pinned_runner_near_miss_names_the_exact_allowed_form(self):
        got = self.refused(self.runner("> %s 2>&1" % os.path.join(self.fake_home, "x.txt")))
        self.assertIn("python3 -I <worktree>/tools/runtests.py --changed", got[1])
        self.assertIn("scratchpad", got[1])

    # a test run with 2>&1 chained to the commit (friction 846)
    # these call the commit guard itself: through the hook the plugin-merge and attribution guards refuse a commit of this
    # repo for their own reasons, which is not what is under test
    def judge(self, command):
        return commitguard.judge("Bash", {"command": command}, "agent-1", self.project)

    def commit_chain(self, tests):
        return self.judge("%s && /usr/bin/git -C %s commit -F %s" % (tests, ROOT, os.path.join(self.scratch, "m.txt")))

    def test_a_test_run_with_a_scratchpad_redirect_chained_to_the_commit_is_allowed(self):
        self.assertEqual(self.commit_chain("python3 -I %s test_a > %s 2>&1" % (GATE, self.out))[0], "pass")
        self.assertEqual(self.commit_chain("python3 -I %s test_a &> %s" % (GATE, self.out))[0], "pass")

    def test_a_commit_chained_only_to_a_redirect_is_still_refused(self):
        self.assertEqual(self.commit_chain("echo ok > %s 2>&1" % self.out)[0], "deny")
        self.assertEqual(self.judge("python3 -I %s test_a ; /usr/bin/git -C %s commit -F %s" % (GATE, ROOT, self.out))[0], "deny")
        self.assertEqual(self.judge("python3 -I %s test_a & /usr/bin/git -C %s commit -F %s" % (GATE, ROOT, self.out))[0], "deny")

    def test_split_chain_keeps_a_redirect_whole_and_still_splits_a_background_ampersand(self):
        self.assertEqual([s for s, _ in commitguard.split_chain("t > f 2>&1 && c")], ["t > f 2>&1 ", " c"])
        self.assertEqual([s for s, _ in commitguard.split_chain("t &> f && c")], ["t &> f ", " c"])
        self.assertEqual(len(commitguard.split_chain("t & c")), 2)
        self.assertEqual(len(commitguard.split_chain("t && c")), 2)

    def test_the_commit_refusal_names_the_exact_allowed_form(self):
        for rel in ("tools/runtests.py", "plugin/scripts/commitguard.py"):      # since C3 only a Budgit checkout gets the gate text
            path = os.path.join(self.project, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write("# marker\n")
        got =self.judge("/usr/bin/git -C %s commit -F %s" % (ROOT, self.out))
        self.assertEqual(got[0], "deny")
        self.assertIn("gate_test.py", got[1])
        self.assertIn("commit -F <file>", got[1])
        self.assertIn("nothing after the commit", got[1])

    # the write corpus stays refused
    def test_write_corpus_stays_refused(self):
        for cmd in ("echo hi > %s" % os.path.join(self.fake_home, ".studio", "ledger.json"),
                    "echo hi > %s" % os.path.join(self.fake_home, ".budgit", "x"),
                    "echo hi > %s" % os.path.join(self.home, "config.json"),
                    "echo hi >> %s" % os.path.join(self.home, "config.json"),
                    "echo hi | tee %s" % os.path.join(self.home, "config.json"),
                    "rm %s" % os.path.join(self.home, "config.json"),
                    "echo hi > ~/.studio/x", "echo hi > ~/.budgit/x"):
            self.refused(cmd)
            self.refused(cmd, background=True)


if __name__ == "__main__":
    unittest.main()
