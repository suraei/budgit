"""R-1077: a protected project's real-data folder that sits OUTSIDE the project root is enforced like one inside it. Synthetic
fixture only: a project named acme with the pattern '~/Library/Application Support/Acme', a synthetic HOME in a temp folder that
holds that folder, and a `git worktree add` checkout of the project elsewhere in the temp dir. The hook is driven in-process
(hook.on_pre_tool_use), once per shape, under both bindings (the bound project is the repo, or the worktree) and both cwds."""
import json
import os
import unittest
from unittest import mock

import hook
import studiolib as sl
from test_guard_trust import GuardCase, git


class OutOfRootDataTest(GuardCase):
    PATTERN = "~/Library/Application Support/Acme"

    def setUp(self):
        super().setUp()
        repo = os.path.join(self.tmp, "Dev", "acme")
        os.makedirs(repo)
        git(repo, "init", "-q")
        sl.activate(repo, bind=False)
        self.set_patterns(repo, [self.PATTERN])
        open(os.path.join(repo, "f.txt"), "w").write("1")
        git(repo, "add", "-f", "-A")
        git(repo, "commit", "-q", "-m", "init")
        wt = os.path.join(self.tmp, "worktrees", "acme-r77")
        os.makedirs(os.path.dirname(wt))
        git(repo, "worktree", "add", "-q", "-b", "r77", wt)
        self.data = os.path.realpath(os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Acme"))
        os.makedirs(self.data)
        open(os.path.join(self.data, "x"), "w").write("secret")
        self.repo, self.wt = os.path.realpath(repo), os.path.realpath(wt)
        os.symlink(self.data, os.path.join(self.wt, "link"))
        self.rel = os.path.relpath(self.data, self.wt)           # climbs out of the worktree to the data folder

    def set_patterns(self, repo, patterns):
        pj = os.path.join(sl.studio_dir(repo), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = patterns
        json.dump(cfg, open(pj, "w"))

    def hook_says(self, tool, cwd, project, agent=False, **inp):
        hook._OUT.clear()
        payload = {"tool_name": tool, "cwd": cwd, "tool_input": inp}
        if agent:
            payload.update(agent_id="a1", session_id="s1")
        hook.on_pre_tool_use(payload, project)
        got = (hook._OUT.get("hookSpecificOutput") or {}).get("permissionDecision")
        hook._OUT.clear()
        return got

    def bash(self, cmd, cwd, project, **kw):
        return self.hook_says("Bash", cwd, project, command=cmd, **kw)

    def every_binding(self):
        return [(project, cwd) for project in (self.repo, self.wt) for cwd in (self.repo, self.wt)]

    def assert_stopped(self, cmd):
        for project, cwd in self.every_binding():
            self.assertIn(self.bash(cmd, cwd, project), ("ask", "deny"), (cmd, project, cwd))

    # the 8 shapes ---------------------------------------------------------------------------------------------------

    def test_shape_1_touch_in_the_data_folder_asks(self):
        self.assert_stopped('touch "%s/x"' % self.data)

    def test_shape_2_cat_of_a_file_in_the_data_folder_asks(self):
        self.assert_stopped('cat "%s/x"' % self.data)

    def test_shape_3_cp_into_the_data_folder_asks(self):
        self.assert_stopped('cp %s/f.txt "%s/x"' % (self.repo, self.data))
        self.assert_stopped('cp "%s/x" /private/tmp/r1077-copy' % self.data)          # a copy OUT of it reads it

    def test_shape_4_a_path_that_climbs_out_of_the_worktree_asks(self):
        self.assert_stopped('touch "%s/%s/x"' % (self.wt, self.rel))
        self.assert_stopped('cat "%s/%s/x"' % (self.wt, self.rel))

    def test_shape_5_a_symlink_in_the_worktree_that_points_at_the_data_asks(self):
        self.assert_stopped("touch %s/link/y" % self.wt)
        self.assert_stopped("cat %s/link/x" % self.wt)
        self.assert_stopped("cp %s/f.txt %s/link/z" % (self.repo, self.wt))

    def test_shape_6_a_glob_through_that_symlink_asks(self):
        self.assert_stopped("cat %s/lin*/x" % self.wt)
        self.assert_stopped("touch %s/lin*/x" % self.wt)

    def test_shape_7_write_to_the_data_folder_asks(self):
        for project, cwd in self.every_binding():
            for path in (os.path.join(self.data, "x"), os.path.join(self.data, "new.txt"), "%s/link/n" % self.wt):
                self.assertIn(self.hook_says("Write", cwd, project, file_path=path, content="1"), ("ask", "deny"), (path, project, cwd))

    def test_shape_8_edit_of_a_file_in_the_data_folder_asks(self):
        for project, cwd in self.every_binding():
            self.assertIn(self.hook_says("Edit", cwd, project, file_path=os.path.join(self.data, "x"),
                                         old_string="secret", new_string="other"), ("ask", "deny"), (project, cwd))

    # neutral and parity ---------------------------------------------------------------------------------------------

    def test_neutral_an_ordinary_path_outside_the_root_stays_allowed(self):
        for project, cwd in self.every_binding():
            self.assertIsNone(self.bash("touch /private/tmp/r1077-neutral", cwd, project), (project, cwd))
            self.assertIsNone(self.bash("cat /private/tmp/r1077-neutral", cwd, project), (project, cwd))
            self.assertIsNone(self.bash("cp %s/f.txt /private/tmp/r1077-neutral" % self.repo, cwd, project), (project, cwd))
            self.assertIsNone(self.hook_says("Write", cwd, project, file_path="/private/tmp/r1077-neutral", content="1"))
            self.assertIsNone(self.bash("cat %s/Library/other.txt" % os.path.expanduser("~"), cwd, project), (project, cwd))

    def test_write_and_edit_give_the_same_decision_as_bash_for_the_same_path(self):
        data_x, other = os.path.join(self.data, "x"), "/private/tmp/r1077-neutral"
        for path in (data_x, other, "%s/link/x" % self.wt, "%s/%s/x" % (self.wt, self.rel), self.wt + "/f.txt"):
            for project, cwd in self.every_binding():
                bash = self.bash('touch "%s"' % path, cwd, project)
                self.assertEqual(self.hook_says("Write", cwd, project, file_path=path, content="1"), bash, (path, project, cwd))
                self.assertEqual(self.hook_says("Edit", cwd, project, file_path=path, old_string="a", new_string="b"), bash,
                                 (path, project, cwd))

    def test_a_subagent_is_denied_where_the_coordinator_is_asked(self):
        self.assertEqual(self.bash('touch "%s/x"' % self.data, self.repo, self.repo), "ask")
        self.assertEqual(self.bash('touch "%s/x"' % self.data, self.repo, self.repo, agent=True), "deny")

    def test_an_absolute_pattern_and_a_glob_pattern_are_enforced_the_same_way(self):
        for pattern in (self.data, self.data + "/**", os.path.join(self.wt, "link")):
            self.set_patterns(self.repo, [pattern])
            self.assertEqual(self.bash('touch "%s/x"' % self.data, self.repo, self.repo), "ask", pattern)
            self.assertEqual(self.bash('cat "%s/x"' % self.data, self.repo, self.repo), "ask", pattern)
            self.assertEqual(self.hook_says("Write", self.repo, self.repo, file_path=self.data + "/n", content="1"), "ask", pattern)
            self.assertIsNone(self.bash("cat /private/tmp/r1077-neutral", self.repo, self.repo), pattern)

    def test_the_pattern_expands_with_the_home_the_hook_sees(self):
        self.assertEqual(sl._out_of_root_patterns(self.repo, [self.PATTERN]), [sl._fold(self.data)])
        other_home = os.path.join(self.tmp, "other-home")
        os.makedirs(os.path.join(other_home, "Library", "Application Support", "Acme"))
        with mock.patch.dict(os.environ, {"HOME": other_home}):
            moved = os.path.realpath(os.path.join(other_home, "Library", "Application Support", "Acme"))
            self.assertEqual(sl._out_of_root_patterns(self.repo, [self.PATTERN]), [sl._fold(moved)])
            self.assertIsNone(self.bash('cat "%s/x"' % self.data, self.repo, self.repo))      # the old HOME's folder is no longer it
            self.assertEqual(self.bash('cat "%s/x"' % moved, self.repo, self.repo), "ask")

    def test_patterns_inside_the_root_or_not_paths_are_left_to_the_in_root_rules(self):
        self.assertEqual(sl._out_of_root_patterns(self.repo, ["data/real/**", ".", "*.csv", self.repo + "/data"]), [])
        self.assertEqual(sl._out_of_root_patterns(self.repo, ["../shared"]), [sl._fold(os.path.dirname(self.repo) + "/shared")])

    def test_a_symlinked_folder_in_front_of_the_pattern_is_followed(self):
        folder = os.path.join(self.tmp, "alias")
        os.symlink(os.path.join(os.path.expanduser("~"), "Library"), folder)
        self.set_patterns(self.repo, [folder + "/Application Support/Acme"])
        self.assertEqual(self.bash('cat "%s/x"' % self.data, self.repo, self.repo), "ask")
        self.assertEqual(self.bash('cat "%s/Application Support/Acme/x"' % folder, self.repo, self.repo), "ask")

    def test_fail_closed_an_error_in_the_new_code_asks(self):
        with mock.patch.object(sl, "_out_of_root_hit", side_effect=RuntimeError("boom")):
            self.assertEqual(self.bash('cat "%s/x"' % self.data, self.repo, self.repo), "ask")
            self.assertEqual(self.bash('touch "%s/x"' % self.data, self.repo, self.repo), "ask")
            self.assertEqual(self.hook_says("Write", self.repo, self.repo, file_path=self.data + "/n", content="1"), "ask")

    # round 2: ancestors, trust, cd chains, wrapper folders, root-holding patterns ----------------------------------------

    def asks(self, cmd, cwd=None, project=None):
        return self.bash(cmd, cwd or self.repo, project or self.repo) in ("ask", "deny")

    def test_r2_1_a_destructive_verb_on_a_folder_above_the_data_asks(self):
        lib = os.path.dirname(os.path.dirname(self.data))
        for cmd in ("rm -rf ~/Library", "rm -rf %s" % lib, "find ~/Library -name x -delete", "find %s -delete" % lib,
                    "mv ~/Library/Application\\ Support ~/m", 'mv "%s" /private/tmp/r1077-m' % os.path.dirname(self.data),
                    "rm -r ~", "touch ~/Library"):
            self.assertTrue(self.asks(cmd), cmd)

    def test_r2_1_a_recursive_read_or_copy_of_a_folder_above_the_data_asks(self):
        lib = os.path.dirname(os.path.dirname(self.data))
        for cmd in ("grep -r secret ~/Library", "grep -rn secret %s" % lib, "rg secret ~/Library", "cp -R ~/Library /private/tmp/c",
                    "cp -a %s /private/tmp/c" % lib, "tar cf /private/tmp/a.tgz ~/Library", "rsync -a ~/Library/ /private/tmp/c/",
                    "find ~/Library -name x", "find %s -type f" % lib, "zip -r /private/tmp/a.zip ~/Library"):
            self.assertTrue(self.asks(cmd), cmd)

    def test_r2_1_plain_commands_on_a_folder_above_the_data_stay_allowed(self):
        for cmd in ("ls ~", "cd ~", "cat ~/.zshrc", "ls ~/Library", "ls -la ~/Library/Application\\ Support", "cd ~/Library",
                    "grep secret ~/.zshrc", "cat ~/Library/other.txt", "ls /", "cd /"):
            self.assertFalse(self.asks(cmd), cmd)

    def test_r2_2_trust_mode_and_the_git_task_runner_exceptions_do_not_drop_it(self):
        with mock.patch.object(sl, "trust_work_on", return_value=True):
            for project, cwd in self.every_binding():
                for cmd in ('cat "%s/x"' % self.data, 'git -C "%s" show HEAD:x' % self.data, 'git -C "%s" log' % self.data,
                            'du "%s"' % self.data, 'tree "%s"' % self.data, 'make -C "%s"' % self.data,
                            'cd "%s" && git show HEAD:x' % self.data):
                    self.assertIn(self.bash(cmd, cwd, project), ("ask", "deny"), (cmd, project, cwd))
            self.assertIsNone(self.bash("git log --oneline -3", self.repo, self.repo))
            self.assertIsNone(self.bash("cat /private/tmp/r1077-neutral", self.repo, self.repo))

    def test_r2_3_a_cd_chain_into_the_parent_then_a_relative_operand_asks(self):
        parent = os.path.dirname(self.data)
        for cmd in ('cd "%s" && cat Acme/x' % parent, 'cd "%s" && touch Acme/y' % parent, "cd ~/Library/Application\\ Support && cat Acme/x",
                    'cd "%s" && grep -r s Acme' % parent, 'cd "%s"; cat ./Acme/x' % parent):
            for project, cwd in self.every_binding():
                self.assertIn(self.bash(cmd, cwd, project), ("ask", "deny"), (cmd, project, cwd))
        self.assertFalse(self.asks('cd "%s" && cat other.txt' % parent))          # a name that is not the data stays allowed

    def test_r2_3_a_relative_operand_after_a_lost_folder_is_asked_never_skipped(self):
        for cmd in ("cd - && cat Acme/x", "cd ${NOWHERE} && cat Acme/x", "cd $(pwd)/.. && cat Acme/x", "pushd && cat Acme/x"):
            self.assertTrue(self.asks(cmd), cmd)
        self.assertFalse(self.asks("cat /private/tmp/r1077-neutral"))             # nothing lost: an absolute neutral path passes

    def test_r2_4_a_wrapper_folder_that_names_the_data_asks(self):
        for cmd in ('env -C "%s" cat x' % self.data, 'git -C "%s" status' % self.data, 'git -C "%s" show HEAD:x' % self.data,
                    'tar -C "%s" -cf /private/tmp/a.tgz .' % self.data, 'tar -C"%s" -cf /private/tmp/a.tgz .' % self.data,
                    'env --chdir="%s" cat x' % self.data, 'git --git-dir="%s/.git" log' % self.data,
                    'cp -t "%s" %s/f.txt' % (self.data, self.repo), 'rsync -a %s/f.txt "%s/"' % (self.repo, self.data),
                    'find "%s" -name x' % self.data, 'xargs -a "%s/x" cat' % self.data):
            for project, cwd in self.every_binding():
                self.assertIn(self.bash(cmd, cwd, project), ("ask", "deny"), (cmd, project, cwd))

    def test_r3_1_a_pattern_that_holds_the_project_is_judged_outside_the_root_only(self):
        alias = os.path.join(self.tmp, "alias")
        os.symlink(os.path.dirname(self.repo), alias)
        sibling = os.path.join(os.path.dirname(self.repo), "acme2")
        for pattern in ("~/Dev/*", "~/Dev/ac*", "~/Dev/*/", "~/Dev", "~/Dev/**", os.path.dirname(self.repo) + "/*", alias + "/*"):
            self.set_patterns(self.repo, [pattern])
            with mock.patch.dict(os.environ, {"HOME": self.tmp}):
                self.assertTrue(sl._out_of_root_patterns(self.repo, [pattern]), pattern)           # it stays in the list
                self.assertEqual(self.bash("touch %s/f" % sibling, self.repo, self.repo), "ask", pattern)    # a covered sibling asks
                self.assertEqual(self.hook_says("Write", self.repo, self.repo, file_path=sibling + "/f", content="1"), "ask", pattern)
                # inside a normal checkout main's decision stands: nothing asks for a pattern it never read
                self.assertIsNone(self.bash("touch %s/f2.txt" % self.repo, self.repo, self.repo), pattern)
                self.assertIsNone(self.hook_says("Write", self.repo, self.repo, file_path=self.repo + "/f.txt", content="1"), pattern)
                self.assertIsNone(self.bash("touch %s/f2.txt" % self.wt, self.wt, self.repo), pattern)   # a proven code worktree stays code

    def test_r3_2_a_folder_option_that_is_the_data_or_above_it_makes_relative_words_asks(self):
        lib = os.path.dirname(os.path.dirname(self.data))
        for cmd in ("env -C %s cat Acme/x" % lib, "make -C %s build" % lib, "git -C %s show HEAD:Acme/x" % lib,
                    "unzip a.zip -d %s" % lib, "tar -C %s -xf a.tar sub" % lib, "env --chdir=%s cat Acme/x" % lib,
                    'git -C "%s" show HEAD:x' % self.data, "git --git-dir=%s log" % lib):
            self.assertTrue(self.asks(cmd), cmd)
        self.assertFalse(self.asks("ls -d /private/tmp"))                                  # `-d` of ls is a flag, not a folder

    def test_r3_3_input_redirects_and_loop_lists_and_echo_into_xargs_are_judged(self):
        for cmd in ('cat < "%s/x"' % self.data, 'for f in "%s"/*; do cat "$f"; done' % self.data,
                    'for f in "%s/x"; do cat "$f"; done' % self.data, 'select f in "%s/x"; do cat "$f"; done' % self.data,
                    'while read l; do echo $l; done < "%s/x"' % self.data, 'echo "%s/x" | xargs cat' % self.data,
                    'printf "%%s\\n" "%s/x" | xargs cat' % self.data):
            self.assertTrue(self.asks(cmd), cmd)
        self.assertFalse(self.asks("cat < /private/tmp/r1077-neutral"))
        self.assertFalse(self.asks("cat <<EOF\nhello\nEOF"))

    def test_r3_4_any_word_in_a_pipeline_with_xargs_that_hits_data_or_a_folder_above_asks(self):
        lib = os.path.dirname(os.path.dirname(self.data))
        for cmd in ("echo | xargs grep -r s ~/Library", "ls %s | xargs -I{} cat %s/{}/x" % (lib, lib), "ls ~/Library | xargs cat",
                    'echo "%s" | xargs ls' % self.data):
            self.assertTrue(self.asks(cmd), cmd)

    def test_r3_5_the_yes_memory_keeps_a_read_apart_from_a_write_and_a_delete(self):
        grep = sl.real_data_check("Bash", {"command": "grep -r x ~/Library"}, self.repo, cwd=self.repo, session="s1")
        rm = sl.real_data_check("Bash", {"command": "rm -rf ~/Library"}, self.repo, cwd=self.repo, session="s1")
        lost = sl.real_data_check("Bash", {"command": "cd - && cat Acme/x"}, self.repo, cwd=self.repo, session="s1")
        self.assertEqual(grep[0][0], "ask")
        self.assertTrue(any(k.startswith("oor-read:") for k in grep[1]), grep[1])
        self.assertTrue(any(k.startswith("oor-write:") for k in rm[1]), rm[1])
        self.assertTrue(any(k.startswith("oor-doubt:") for k in lost[1]), lost[1])
        with mock.patch.object(sl, "real_data_yes_keys", return_value=set(grep[1])):
            self.assertTrue(sl.real_data_yes_covers("s1", self.repo, grep[1]))
            self.assertFalse(sl.real_data_yes_covers("s1", self.repo, rm[1]))              # a yes to the grep never covers the rm
            self.assertFalse(sl.real_data_yes_covers("s1", self.repo, lost[1]))
        with mock.patch.object(sl, "real_data_yes_keys", return_value=set(lost[1])):
            self.assertFalse(sl.real_data_yes_covers("s1", self.repo, rm[1]))

    def test_r3_9_a_yes_to_a_read_through_xargs_or_a_for_list_never_covers_a_delete(self):
        lib = os.path.dirname(os.path.dirname(self.data))
        keys = lambda cmd: sl.real_data_check("Bash", {"command": cmd}, self.repo, cwd=self.repo, session="s1")
        cat = keys("ls %s | xargs cat" % lib)
        rm = keys("ls %s | xargs rm -rf" % lib)
        loop = keys('for f in "%s"/*; do rm "$f"; done' % self.data)
        loop_cat = keys('for f in "%s"/*; do cat "$f"; done' % self.data)
        for got in (cat, rm, loop):
            self.assertEqual(got[0][0], "ask")
            self.assertTrue(any(k.startswith("oor-write:") for k in got[1]), got[1])
            self.assertFalse(any(k.startswith("oor-read:") for k in got[1]), got[1])
        with mock.patch.object(sl, "real_data_yes_keys", return_value=set(cat[1])):
            self.assertTrue(sl.real_data_yes_covers("s1", self.repo, cat[1]))
            self.assertFalse(sl.real_data_yes_covers("s1", self.repo, rm[1]))
            self.assertFalse(sl.real_data_yes_covers("s1", self.repo, loop[1]))
            self.assertFalse(sl.real_data_yes_covers("s1", self.repo, loop_cat[1]))

    def test_r3_6_more_verbs_over_a_folder_above_the_data_ask(self):
        for cmd in ("xattr -r -c ~/Library", "chflags -R nohidden ~/Library", "ls -R ~/Library", "unzip a.zip -d ~/Library"):
            self.assertTrue(self.asks(cmd), cmd)
        for cmd in ("ls -la ~/Library", "xattr ~/.zshrc", "ls -d ~/Library"):
            self.assertFalse(self.asks(cmd), cmd)

    def test_r3_7_an_absolute_pattern_is_resolved_by_realpath_before_it_is_compared(self):
        self.set_patterns(self.repo, ["/tmp/r1077-acme-data"])
        want = sl._fold(os.path.join(os.path.realpath("/tmp"), "r1077-acme-data"))
        self.assertEqual(sl._out_of_root_patterns(self.repo, ["/tmp/r1077-acme-data"]), [want])
        self.assertTrue(self.asks("touch %s/x" % os.path.realpath("/tmp/r1077-acme-data")))
        self.assertTrue(self.asks("touch /tmp/r1077-acme-data/x"))
        self.assertFalse(self.asks("touch /private/tmp/r1077-neutral"))

    def test_r3_8_an_error_while_judging_a_word_asks(self):
        import realdata
        ctx = realdata.Ctx(self.repo, [self.PATTERN], self.repo)
        with mock.patch.object(sl, "_out_of_root_word_level", side_effect=RuntimeError("boom")):
            self.assertEqual(realdata.level("%s/x" % self.data, ctx), "exact")
            self.assertEqual(self.bash('cat "%s/x"' % self.data, self.repo, self.repo), "ask")
            self.assertEqual(self.bash("cat x", self.repo, self.repo), "ask")

    def test_r2_6_an_error_before_the_operand_loop_asks(self):
        with mock.patch.object(sl, "_out_of_root_patterns", side_effect=RuntimeError("boom")):
            self.assertEqual(self.bash('cat "%s/x"' % self.data, self.repo, self.repo), "ask")
            self.assertEqual(self.bash('touch "%s/x"' % self.data, self.repo, self.repo), "ask")

    def test_words_the_guard_cannot_evaluate_stay_with_the_literal_name_scan(self):
        # `${HOME}`, `$VAR` and `~name` are not expanded by the new code; the literal-name scan (_mentions_literal, kept as it
        # is until R-1074 retires it) asks for a write that names the folder, exactly as main did
        cmd = "touch ${HOME}/Library/Application\\ Support/Acme/x"
        self.assertFalse(sl._out_of_root_hit("${HOME}/Library/Application Support/Acme/x", self.repo, [self.PATTERN], self.repo))
        self.assertEqual(self.bash(cmd, self.repo, self.repo), "ask")
        with mock.patch.object(sl, "_out_of_root_hit", return_value=False):
            self.assertEqual(self.bash(cmd, self.repo, self.repo), "ask")


if __name__ == "__main__":
    unittest.main()
