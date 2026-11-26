"""R-1128 hotfix 1, guards part (I-1687, I-1689). Synthetic scratch homes and projects only.
Item 1: a search rooted at `/`, `~`, `$HOME` or above the project is refused by name; searches inside the project pass.
Item 2: a read inside the project never fires the real-data guard; real data outside still does.
Item 2b: every refusal names the matched thing and a way out; the read-only table passes or stays refused for a named reason."""
import json
import os
import subprocess
import sys

from test_guard_trust import GuardCase, HOOK
import searchguard
import studiolib as sl

PY = os.path.realpath(sys.executable)


class G2Case(GuardCase):
    def setUp(self):
        super().setUp()
        from unittest import mock
        patch = mock.patch.object(searchguard, "SEARCH_GUARD_DEFAULT", True)   # hotfix 1 ships the guard OFF: these rows turn it on
        patch.start()
        self.addCleanup(patch.stop)
        os.makedirs(self.home, exist_ok=True)
        cfg_path = os.path.join(self.home, "config.json")
        cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
        cfg["search_guard"] = "on"                                              # the real-hook rows read the home config
        with open(cfg_path, "w") as fh:
            json.dump(cfg, fh)
        self.user = os.path.join(self.tmp, "fake-user-home")
        sl.write_pointer("s-g2", self.root)
        self.dash = os.path.join(self.root, "dashboard", "static")
        os.makedirs(self.dash, exist_ok=True)
        with open(os.path.join(self.dash, "style.css"), "w") as fh:
            fh.write(".a { color: red; }\n")
        self.wt = os.path.join(self.tmp, "wt-of-project")
        os.makedirs(self.wt)

    def hook(self, tool, tool_input, cwd=None, agent=False, lang="en", home=None):
        env = dict(os.environ, HOME=home or self.user, STUDIO_HOME=self.home, PWD=cwd or self.root, BUDGIT_LANG=lang)
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "session_id": "s-g2",
                   "cwd": cwd or self.root}
        if agent:
            payload.update(agent_id="agent-1", agent_type="general-purpose")
        r = subprocess.run([PY, HOOK, "pre-tool-use"], input=json.dumps(payload), env=env, capture_output=True, text=True,
                           cwd=cwd or self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
        return out.get("permissionDecision"), out.get("permissionDecisionReason", "")


class SearchRootTest(G2Case):
    # --- item 1: refused roots
    def test_find_rooted_at_slash_is_refused_naming_the_root(self):
        decision, text = self.hook("Bash", {"command": "find / -name '*.css'"})
        self.assertEqual(decision, "deny", text)
        self.assertIn("/", text)
        self.assertIn("project folder", text)

    def test_find_rg_fd_grep_rooted_at_home_or_users_or_parent_are_refused(self):
        parent = os.path.dirname(self.root)
        for cmd in ("find ~ -name x", "find $HOME -name x", "rg foo ~", "fd foo %s" % os.path.dirname(os.path.dirname(self.root)),
                    "grep -rn foo %s" % parent, "find %s -name x" % self.user, "find ../.. -name x", "find ../../.. -name x"):
            decision, text = self.hook("Bash", {"command": cmd})
            self.assertEqual(decision, "deny", cmd)
            self.assertIn("project folder", text, cmd)

    def test_grep_and_glob_tools_rooted_above_the_project_are_refused(self):
        for tool, inp in (("Grep", {"pattern": "x", "path": "/"}), ("Grep", {"pattern": "x", "path": self.user}),
                          ("Glob", {"pattern": "**/*.css", "path": os.path.dirname(self.root)}),
                          ("Glob", {"pattern": "/**/*.css"}), ("Glob", {"pattern": "~/**/*.css"})):
            decision, text = self.hook(tool, inp)
            self.assertEqual(decision, "deny", (tool, inp))

    def test_a_symlink_inside_the_project_pointing_above_it_is_refused(self):
        os.symlink(self.user, os.path.join(self.root, "homelink"))
        decision, text = self.hook("Bash", {"command": "find homelink -name x"})
        self.assertEqual(decision, "deny", text)

    def test_the_refusal_is_in_the_users_language(self):
        _, es = self.hook("Bash", {"command": "find / -name x"}, lang="es")
        _, en = self.hook("Bash", {"command": "find / -name x"}, lang="en")
        self.assertIn("carpeta del proyecto", es)
        self.assertIn("project folder", en)

    # --- item 1: allowed shapes
    def test_searches_inside_the_project_pass(self):
        for cmd in ("find . -name '*.css'", "find dashboard -name '*.css'", "grep -rn color dashboard", "rg color src",
                    "fd style dashboard", "find %s -name x" % self.root, "grep -rn color %s/src" % self.root,
                    "grep -n color dashboard/static/style.css"):
            decision, text = self.hook("Bash", {"command": cmd})
            self.assertIsNone(decision, (cmd, text))
        for tool, inp in (("Grep", {"pattern": "x", "path": self.root}), ("Grep", {"pattern": "x"}),
                          ("Glob", {"pattern": "**/*.css"}), ("Glob", {"pattern": "*.css", "path": self.dash})):
            decision, text = self.hook(tool, inp)
            self.assertIsNone(decision, (tool, inp, text))

    def test_a_search_in_the_session_scratchpad_or_an_allowed_path_passes(self):
        scratch = os.path.join(self.tmp, "scratchpad")          # a sibling of the project: not above it, so it passes
        os.makedirs(scratch)
        self.assertIsNone(searchguard.decision("Bash", {"command": "find %s -name x" % scratch}, self.root, self.root,
                                               allowed=[], env={"HOME": self.user}))
        parent = os.path.dirname(self.root)                     # above the project: refused unless it is in the allowed list
        cmd = {"command": "find %s -name x" % parent}
        self.assertIsNotNone(searchguard.decision("Bash", cmd, self.root, self.root, allowed=[], env={"HOME": self.user}))
        # (a home folder of its own, outside `parent`) an allowed entry above the project never opens it, see safe_allowed
        self.assertIsNotNone(searchguard.decision("Bash", cmd, self.root, self.root, allowed=[parent],
                                                  env={"HOME": "/nonexistent-r1128-home"}))

    def test_a_worktree_of_the_project_passes(self):
        self.assertIsNone(searchguard.decision("Bash", {"command": "find %s -name x" % self.wt}, self.root, self.root,
                                               allowed=[], env={"HOME": self.user}, worktrees=[self.wt]))

    def test_the_guard_fails_closed_on_an_unreadable_call(self):
        self.assertIsNotNone(searchguard.decision("Bash", {"command": "find 'unclosed /"}, self.root, self.root,
                                                  allowed=[], env={"HOME": self.user}))


class RealDataFalseAlarmTest(G2Case):
    def check(self, command, cwd=None):
        return sl.real_data_check("Bash", {"command": command}, self.project, cwd=cwd or self.root)[0]

    def test_reads_of_source_files_inside_the_project_never_fire(self):
        for rel in ("dashboard/static/style.css", "src/app.py", "docs/a.md"):
            for verb in ("grep -n one", "cat", "sed -n 1,5p", "head -5"):
                self.assertIsNone(self.check("%s %s" % (verb, rel)), (verb, rel))
                self.assertIsNone(self.check("%s %s/%s" % (verb, self.root, rel)), (verb, rel))

    def test_the_stylesheet_case_the_tester_hit(self):
        # a grep inside a dashboard stylesheet, reached by a relative path from a deeper folder and by a symlinked root
        link = os.path.join(self.tmp, "linked-root")
        os.symlink(self.root, link)
        cases = (("grep -n color style.css", self.dash), ("grep -n color ../static/style.css", self.dash),
                 ("grep -n 'a' %s/dashboard/static/style.css" % link, self.root),
                 ("grep -n color dashboard/static/*.css", self.root), ("grep -c '{' dashboard/static/style.css", self.root),
                 ("grep -n 'color: red' \"%s/dashboard/static/style.css\"" % self.root, self.root))
        for cmd, cwd in cases:
            self.assertIsNone(self.check(cmd, cwd), cmd)

    def test_real_data_inside_the_project_still_fires(self):
        for cmd in ("echo x > data/real/x.csv", "rm data.json", "cp src/app.py data/real/x.csv"):
            self.assertIsNotNone(self.check(cmd), cmd)

    def test_a_symlink_in_the_project_to_real_data_outside_still_fires(self):
        outside = os.path.join(self.tmp, "outside-data")
        os.makedirs(outside)
        open(os.path.join(outside, "real.csv"), "w").write("x\n")
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = list(cfg["protected_paths"]) + [os.path.join(os.path.realpath(outside), "**")]
        json.dump(cfg, open(pj, "w"))
        os.symlink(outside, os.path.join(self.root, "reallink"))
        self.assertIsNotNone(self.check("cp src/app.py reallink/real.csv"))
        climb = os.path.relpath(os.path.join(outside, "real.csv"), self.dash)       # a relative path that climbs out
        self.assertIn("..", climb)
        self.assertIsNotNone(self.check("rm " + climb, self.dash))


class RefusalTextTest(G2Case):
    def test_no_refusal_text_carries_the_stale_home_path(self):
        for name in ("deny_root", "deny_unreadable"):
            for lang in ("en", "es"):
                self.assertNotIn("~/.studio", searchguard.text(name, lang, root="/", home=self.home))

    def test_the_real_data_refusal_names_the_path_and_the_rule(self):
        decision, text = self.hook("Bash", {"command": "echo x > data/real/x.csv"})
        self.assertIn(decision, ("ask", "deny"), text)
        self.assertIn("data/real/x.csv", text)
        self.assertIn("protected", text)
        self.assertNotIn("~/.studio", text)

    def test_read_only_commands_table_pass_or_stay_refused_for_a_named_reason(self):
        table = (  # (command, expected decision or None, reason when refused)
            ("cat dashboard/static/style.css", None, ""),
            ("grep -n color dashboard/static/style.css", None, ""),
            ("sed -n 1,3p src/app.py", None, ""),
            ("head -3 docs/a.md", None, ""),
            ("ls -la docs", None, ""),
            ("wc -l docs/a.md docs/b.md", None, ""),
            ("git log --oneline -3", None, ""),
            ("git status --short --ignore-submodules=all", None, ""),
            ("find . -name '*.md'", None, ""),
            # outside-review finding 3: stays refused by the home guard (inline code that imports Budgit's own library can write
            # the home); a narrower allow path needs its own security round (not done in this hotfix)
            ("python3 -I -S -B -c \"import studiolib\"", "deny", "inline code importing Budgit's library, home guard"),
            ("python3 -m unittest discover -s tests -p 'test_x.py'", None, ""),
            ("cat data/real/x.csv", None, ""),             # a plain read inside the bound project never fires (writes do)
            ("find / -name '*.css'", "deny", "search root above the project"),
            ("grep -r foo ~", "deny", "search root is the home folder"),
            ("find ../.. -name x", "deny", "search root climbs out of the project"),
        )
        for cmd, want, why in table:
            decision, text = self.hook("Bash", {"command": cmd}, agent=True)
            if want is None:
                self.assertIsNone(decision, (cmd, text))
            else:
                self.assertEqual(decision, "deny", (cmd, why, text))


class SearchRootRound2Test(G2Case):
    """Security round 2 (review r1, H1 to H4 and the mediums): the reviewer's exact command shapes must all be refused, the
    allowed shapes must stay allowed."""

    def d(self, cmd, allowed=None, cwd=None):
        return searchguard.decision("Bash", {"command": cmd}, self.root, cwd or self.root, allowed=allowed or [],
                                    env={"HOME": self.user})

    def refused(self, cmds, **kw):
        for cmd in cmds:
            self.assertIsNotNone(self.d(cmd, **kw), cmd)

    def passes(self, cmds, **kw):
        for cmd in cmds:
            self.assertIsNone(self.d(cmd, **kw), cmd)

    def test_h1_a_cd_pushd_or_substitution_before_a_search_moves_its_root(self):
        self.refused(["cd / && find . -name x", "pushd / ; grep -r x .", "x=$(cd / && find . -name y)", "cd .. && find . -name x",
                      "cd $UNKNOWN_DIR && find .", "(cd / ; find .)", "cd ~ && rg foo .", "cd && find . -name x",
                      "cd - && find .", "cd -P / && find .", "env -C / find .", "git -C / grep --no-index x .",
                      "cd / ; cd %s ; find ." % self.root])
        self.passes(["cd dashboard && find . -name x", "cd dashboard; grep -rn color .", "cd %s && find . -name x" % self.dash,
                     "cd /tmp && ls -la", "cd src && find . -name x"])

    def test_h2_unknown_or_boolean_flags_never_hide_a_root(self):
        self.refused(["grep -r -E pat /", "rg -S pat /", "rg -x pat ~", "rg --files /", "ag -g x /", "grep -rI pat /",
                      "rg --some-new-flag pat /", "fd -H x /", "fd --base-directory / x", "fd x --search-path /",
                      "grep -rn -A3 pat /", "grep -d recurse pat /", "grep --directories=recurse pat /",
                      "grep -r -e pat /", "rg -e pat /", "ag -A 2 pat /", "rg -t py pat /", "rg -g '*.py' pat ~"])
        self.passes(["grep -rn -E color dashboard", "rg -S color dashboard", "rg -x color src", "rg --files dashboard",
                     "ag -g style dashboard", "grep -rn '/' dashboard", "fd -H style dashboard", "rg -t py color src",
                     "grep -r -A3 color dashboard", "grep -n color dashboard/static/style.css"])

    def test_h3_wrappers_shells_and_substitutions_are_read(self):
        self.refused(["timeout 5 find /", "nice -n5 find /", "env -i find /", "xargs -I{} find {}", "bash -lc 'find /'",
                      "echo `find /`", "echo $(find /)", "echo \"$(find /)\"", "sudo -u x find /", "nohup find / &",
                      "stdbuf -o0 grep -r x /", "command find /", "{ find / ; }", "if true; then find /; fi",
                      "sh -c \"bash -c 'find /'\"", "zsh -c 'cd / && find .'", "echo x | xargs find", "xargs rg foo",
                      "cat <<EOF\n$(find /)\nEOF", "timeout 5 sh -c 'find /'", "eval 'find /'", "env FOO=1 timeout 3 rg x ~"])
        self.passes(["timeout 5 find dashboard -name x", "nice -n5 grep -rn x dashboard", "bash -lc 'find dashboard'",
                     "find . -name x | xargs grep -l foo", "echo hi", "git commit -m \"it is a grep fix\"",
                     "env -i rg color src", "cd dashboard && timeout 5 find ."])
        # round 3b: moved from passes to refused (a heredoc or $(...) beside a searcher word is unreadable)
        self.refused(["git commit -m \"$(cat <<'EOF'\nit's a grep fix\nEOF\n)\""])

    def test_h4_the_allowed_list_never_widens_to_the_disk_the_home_folder_or_above(self):
        parent = os.path.dirname(self.root)
        uh = os.path.realpath(self.user)
        for entry in ("/", self.user, os.path.dirname(self.user), "~", parent):
            self.assertIsNotNone(self.d("find / -name x", allowed=[entry]), entry)
            self.assertEqual(searchguard.safe_allowed([entry], uh, self.root), [], entry)
        self.assertIsNotNone(self.d("find %s -name x" % self.user, allowed=["~"]))
        sub = os.path.join(uh, "notes")
        self.assertEqual(searchguard.safe_allowed(["~/notes"], uh, self.root), [sub])
        self.assertIsNone(self.d("find %s -name x" % sub, allowed=["~/notes"]))
        self.assertEqual(searchguard.safe_allowed([5, None, ""], uh, self.root), [])

    def test_h4_an_edit_of_search_roots_allowed_in_project_json_asks(self):
        pj = os.path.join(self.root, ".budgit", "project.json")
        cfg = json.load(open(pj))
        cfg["search_roots_allowed"] = ["/"]
        ask = sl.contract_lock_decision("Write", {"file_path": pj, "content": json.dumps(cfg)}, self.root, self.root)
        self.assertEqual(ask[0], "ask", ask)
        self.assertIn("search_roots_allowed", ask[1])
        cfg.pop("search_roots_allowed")
        same = sl.contract_lock_decision("Write", {"file_path": pj, "content": json.dumps(cfg)}, self.root, self.root)
        self.assertIsNone(same)

    def test_m_find_macos_leading_options_braces_and_quoted_parens(self):
        self.refused(["find -s -x -E -D -f / -name x", "find -f / -name x", "find -E / -regex x", "find -x / -name x",
                      "find {/,.} -name x", "find \"(\" / -name x", "find \\( / \\) -name x", "find . {/,src} -name x",
                      "find {/,.}/ -name x"])
        self.passes(["find -s . -name x", "find -E . -regex x", "find . \\( -name a -o -name b \\)", "find . ! -name x",
                     "find {src,dashboard} -name x"])

    def test_m_more_searchers_are_covered(self):
        self.refused(["ls -R /", "ls -lR ~", "ls --recursive /", "du /", "du -sh ~", "tree /", "git grep --no-index x /",
                      "grep -d recurse x /", "zgrep -r x /", "ugrep -r x /", "ug x /", "ack foo /", "git grep --no-index x ~"])
        self.passes(["ls -R dashboard", "ls /", "du -sh dashboard", "tree dashboard", "git grep x", "ugrep -r x src",
                     "zgrep x dashboard/static/style.css.gz"])

    def test_the_allowed_shapes_in_a_worktree_and_the_scratchpad_still_pass(self):
        scratch = os.path.join(self.tmp, "scratchpad")
        os.makedirs(scratch, exist_ok=True)
        self.passes(["cd %s && find . -name x" % self.wt, "find . -name x"], cwd=self.wt)
        self.passes(["cd %s && grep -rn x ." % scratch, "rg x %s" % scratch])
        self.passes(["find . -name x"], cwd=self.wt)


class AllowedAncestorTest(G2Case):
    """An allowed root never opens a folder above the project or a worktree, the same way it never opens home."""

    def test_r3_an_allowed_folder_above_the_project_or_a_worktree_is_dropped_the_rest_is_kept(self):
        h = "/nonexistent-r1128-home"
        parent = os.path.dirname(self.root)
        other = os.path.join(parent, "Other")                       # a sibling folder, not above the project
        inner = os.path.join(self.root, "dashboard")
        wt_parent = os.path.dirname(os.path.realpath(self.wt))
        self.assertEqual(searchguard.safe_allowed([parent], h, self.root), [])
        self.assertEqual(searchguard.safe_allowed([wt_parent], h, self.root, [self.wt]), [])
        self.assertEqual(searchguard.safe_allowed([self.root, inner, other, "dashboard"], h, self.root),
                         [self.root, os.path.realpath(inner), os.path.realpath(other), os.path.realpath(inner)])
        self.assertEqual(searchguard.safe_allowed([self.wt], h, self.root, [self.wt]), [os.path.realpath(self.wt)])

    def test_r3_real_door_a_grep_above_the_project_is_denied_though_project_json_allows_the_parent(self):
        parent = os.path.dirname(self.root)
        pj = os.path.join(self.root, ".budgit", "project.json")
        cfg = json.load(open(pj))
        cfg["search_roots_allowed"] = [parent]
        with open(pj, "w") as fh:
            json.dump(cfg, fh)
        decision, text = self.hook("Grep", {"pattern": "x", "path": parent}, home="/nonexistent-r1128-home")
        self.assertEqual(decision, "deny", text)
        self.assertIn("above the project", text)


class RealHookAllowListTest(G2Case):
    """Round 3b through the real door: hook.py pre-tool-use as a subprocess. A command with a searcher word that is not made of
    the forms the reader names is denied; the two plain shapes pass."""

    def denied(self, cmd):
        decision, text = self.hook("Bash", {"command": cmd})
        self.assertEqual(decision, "deny", (cmd, text))

    def test_r3b_real_hook_dollar_single_quote_cannot_hide_a_search_of_the_disk(self):
        self.denied("cat $'\\'' ; grep -r x / ; cat \\'")

    def test_r3b_real_hook_quoted_heredoc_delimiter_with_a_space_cannot_hide_a_search(self):
        self.denied("cat <<'E OF'\nEOF\ngrep -r x /\nE OF")

    def test_r3b_real_hook_dollar_double_quote_is_unreadable(self):
        self.denied('grep -rn $"x" dashboard')

    def test_r3b_real_hook_process_substitution_is_unreadable(self):
        self.denied("grep -rn x <(echo dashboard)")

    def test_r3b_real_hook_backslash_newline_continuation_is_unreadable(self):
        self.denied("grep -rn x dashboard \\\n  src")

    def test_r3b_real_hook_an_escaped_quote_outside_quotes_does_not_drift_the_quote_state(self):
        self.denied("cat \\' ; grep -r x / ; cat \\'")
        self.denied('cat \\" ; grep -r x / ; cat \\"')

    def test_r3b_real_hook_an_escaped_separator_is_a_literal_word(self):
        for cmd in ("grep -rn x \\; /", "grep -rn x \\| /", "grep -rn x \\& ~"):       # one grep whose roots include / or ~
            self.denied(cmd)
        for cmd in ("echo a \\; grep -r y /", "echo a \\| grep -r y /", "echo a \\& grep -r y /",   # one echo, nothing searched
                    "grep -rn \\$x dashboard"):
            decision, text = self.hook("Bash", {"command": cmd})
            self.assertIsNone(decision, (cmd, text))

    def test_r3b_real_hook_plain_searches_inside_the_project_still_pass(self):
        for cmd in ('grep -rn "word" src', "find . -name '*.py' | head"):
            decision, text = self.hook("Bash", {"command": cmd})
            self.assertIsNone(decision, (cmd, text))


class SearchRootRound3Test(SearchRootRound2Test):
    """Security round 3 (review r2, H1 to H3, M3, LOW): fail closed on a hidden command name, never on plain text."""

    def test_r3_h1_quotes_and_backslashes_inside_the_command_name_are_removed_before_matching(self):
        self.refused(['fi""nd /', "f\\ind /", "'fi'nd /", '"find" /', "\\find /", "fi''nd ~"])

    def test_r3_h2_a_hidden_or_unknown_command_name_is_refused_when_a_searcher_word_is_in_the_command(self):
        self.refused(["busybox find /", "toybox find /", "timeout 5 busybox find /", 'env -S "find /"', "x=find; $x /",
                      "$'find' /", "$(printf find) /", "find${IFS}/", "/usr/bin/fin? /", "/usr/bin/[f]ind /",
                      "{f,}ind /", "`printf find` /", "x=find; timeout 5 $x /", "$(printf '\\x66ind') /", "busybox grep -r x /"])
        self.passes(["busybox find dashboard -name x", "env | grep PATH", "echo $HOME", "x=3; echo $x | grep 3",
                     "$HOME/bin/tool | grep x", "ls $HOME/../x.txt", "bash script.sh | grep x",
                     "find . -name '*.py' 2>/dev/null | head", "grep -rn 'a b' dashboard > /dev/null 2>&1",
                     "grep -rn x dashboard &> /dev/null", "grep -n x < dashboard/static/style.css"])
        # round 3b: moved from passes to refused (parentheses and `;;`, a `$` inside double quotes, a heredoc with $(...))
        self.refused(["case $1 in *) grep -rn x dashboard;; esac", "timeout 5 ./run.sh \"$X\" | grep y",
                      "git commit -m \"$(cat <<'EOF'\nfind the bug in $HOME handling\nEOF\n)\""])

    def test_r3b_redirections_and_roots_the_reader_cannot_know_are_unreadable(self):
        self.refused(["find . -name x >| out", "grep -rn x dashboard <> f", "find . 3<&0", "find . > $X", "find . > \"$X\"",
                      "find . 2>&1 | tee >(cat)", "find $X -name y", "rg foo ${X}", "grep -rn x $UNKNOWN", "find . >",
                      "grep -rn x dashboard < $F"])
        self.passes(["find . -name y 2>&1 | head", "grep -rn x dashboard 2>/dev/null"])

    def test_r3b_allow_list_a_command_with_a_searcher_word_is_read_only_when_made_of_known_forms(self):
        self.refused(["cat $'\\'' ; grep -r x / ; cat \\'", "cat <<'E OF'\nEOF\ngrep -r x /\nE OF",
                      'grep -rn $"x" dashboard', "grep -rn x <(echo dashboard)", "grep -rn x dashboard \\\n  src",
                      "grep -rn x dashboard # note", "grep -rn x dashboard &", "(grep -rn x dashboard)", "echo `ls`",
                      "grep -rn x dashboard <<< hi", "grep -rn \"a\\b\" dashboard"])
        self.passes(["grep -rn \"word\" dashboard", "find . -name '*.py' | head", "ls -la\nls dashboard",
                     "find . -name a -o -name b && echo ok || echo no", "find dashboard -exec grep x {} \\;"])

    def test_r3b_an_escaped_separator_is_a_literal_word_never_a_separator(self):
        # the shell reads \; \| \& as ordinary characters of the word, so these are ONE command each
        self.passes(["echo a \\; grep -r y /", "echo a \\| grep -r y /", "echo a \\& grep -r y /", "echo \\&\\& ls -R /",
                     "grep -rn \\$x dashboard", "find . -name x \\; grep -r y /"])    # find stops reading roots at -name
        self.refused(["grep -rn x \\; /", "grep -rn x \\| /", "grep -rn x \\& ~", "find dashboard / -name \\;"])

    def test_r3_h3_a_shell_fed_by_stdin_or_dash_s_is_refused_when_a_searcher_word_is_in_the_command(self):
        self.refused(["echo 'find /' | sh", 'bash <<< "find /"', "bash <<EOF\nfind /\nEOF", "ls | sh -s", "echo find / | bash -s",
                      "echo 'find /' | busybox sh", "source <(echo find /)", "ls; . /dev/stdin", "zsh < cmds.txt; ls -R /",
                      "x=find; eval \"$x /\"", "ls | bash -l"])
        self.passes(["echo hi | sh", "bash <<< 'echo hi'", "sh script.sh", "bash -c 'find dashboard'"])

    def test_r3_m3_the_working_folder_is_above_only_when_it_is_outside_the_project(self):
        sub = os.path.join(self.root, "dashboard")
        self.passes(["rg x ..", "find .. -name x", "grep -rn x ../dashboard"], cwd=sub)
        self.refused(["rg x ../..", "find ../.. -name x", "find / -name x"], cwd=sub)
        self.refused(["rg x .."], cwd=self.root)

    def test_r3_low_find_after_a_double_dash_reads_the_slash_as_a_root(self):
        self.refused(["find -- /", "find -H -- ~ -name x"])
        self.passes(["find -- dashboard -name x"])

    def test_r3_the_switch_is_read_from_budgits_own_config_and_ships_off(self):
        from unittest import mock
        self.refused(["find / -name x"])
        with mock.patch.object(searchguard, "SEARCH_GUARD_DEFAULT", False):       # the shipped default
            with mock.patch.object(sl, "_home_config", return_value={}):
                self.passes(["find / -name x"])
            with mock.patch.object(sl, "_home_config", return_value={"search_guard": "on"}):
                self.refused(["find / -name x"])
        with mock.patch.object(sl, "_home_config", return_value={"search_guard": "off"}):
            self.passes(["find / -name x"])
