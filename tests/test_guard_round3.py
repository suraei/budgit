"""R-335 round 3: one test per CLASS the round-2 review and verification found (security HOLD, verifier FAIL)."""
import json
import os
import shutil
import unittest

from helpers import ROOT
from test_guard_trust import GuardCase, HookCase2, CLI, HOOK, git
import studiolib as sl


class HomeCase(GuardCase):
    def setUp(self):
        super().setUp()
        self.uhome = os.path.join(self.tmp, "fake-user-home")
        os.makedirs(self.home, exist_ok=True)

    def hw(self, cmd, cwd=None):
        got = sl.home_write_decision("Bash", {"command": cmd}, cwd or self.project)
        return got[0] if got else None


class HomeIntegrityTest(HomeCase):
    def test_every_redirect_form_into_the_home_is_denied(self):
        t = os.path.join(self.home, "trust.json")
        for cmd in ("echo x > %s" % t, "echo x >> %s" % t, "echo x >| %s" % t, "echo x 1>| %s" % t, "echo x &> %s" % t,
                    "echo x &>> %s" % t, "echo x 2> %s" % t, "echo x 2>> %s" % t, "echo x >%s" % t, "echo x >|%s" % t,
                    "cat <<EOF > %s\n{}\nEOF" % t, "echo x | tee %s" % t, "set -o noclobber; echo x >| %s" % t):
            self.assertEqual(self.hw(cmd), "deny", cmd)

    def test_both_home_candidates_and_budgit_home_are_covered(self):
        for rel in (".budgit/trust.json", ".studio/trust.json", ".budgit/MIGRATED", ".studio/config.json"):
            path = os.path.join(self.uhome, rel)
            for cmd in ("echo x > %s" % path, "cp /tmp/x %s" % path, "touch %s" % path, "mkdir -p %s" % os.path.dirname(path),
                        "ln -s /tmp/x %s" % path, "install /tmp/x %s" % path, "dd of=%s" % path, "rsync /tmp/x %s" % path,
                        "sed -i '' s/a/b/ %s" % path, "chmod 600 %s" % path, "mv /tmp/x %s" % path, "rm %s" % path):
                self.assertEqual(self.hw(cmd), "deny", cmd)
        self.assertEqual(self.hw("echo x > ~/.budgit/trust.json"), "deny")
        self.assertEqual(self.hw("echo x > $HOME/.studio/trust.json"), "deny")
        other = os.path.join(self.tmp, "elsewhere-home")
        before = os.environ.get("BUDGIT_HOME")
        os.environ["BUDGIT_HOME"] = other
        try:
            self.assertEqual(self.hw("echo x > %s/trust.json" % other), "deny")
        finally:                                        # put back exactly what was there, even "nothing"
            if before is None:
                os.environ.pop("BUDGIT_HOME", None)
            else:
                os.environ["BUDGIT_HOME"] = before

    def test_a_bare_cd_a_cd_dash_and_pushd_make_the_cwd_unknown_or_home(self):
        for cmd in ("cd && cp /tmp/p .budgit/trust.json", "cd -- && cp /tmp/p .budgit/trust.json",
                    "cd ~ && cp /tmp/p .budgit/trust.json", "pushd ~ && cp /tmp/p .studio/trust.json",
                    "cd ~ && echo x > .budgit/trust.json", "cd ~/.budgit && touch trust.json",
                    "cd ~/.budgit; cp /tmp/p ./trust.json", "(cd ~ && cp /tmp/p .budgit/trust.json)"):
            self.assertEqual(self.hw(cmd), "deny", cmd)
        for cmd in ("cd - && cp /tmp/p .budgit/trust.json", "cd $PREV && cp /tmp/p trust.json", "popd && cp /tmp/p x",
                    "cd - && echo x > trust.json"):
            self.assertEqual(self.hw(cmd), "ask", cmd)

    def test_symlinks_that_exist_are_followed_at_the_target_and_homes_are_not_relocated(self):
        os.makedirs(os.path.join(self.uhome, ".budgit"), exist_ok=True)
        real = os.path.join(self.uhome, ".budgit", "trust.json")
        open(real, "w").close()
        link = os.path.join(self.tmp, "innocent")
        os.symlink(real, link)
        self.assertEqual(self.hw("echo x > %s" % link), "deny")                       # file symlink at the redirect
        dirlink = os.path.join(self.tmp, "dirlink")
        os.symlink(os.path.join(self.uhome, ".budgit"), dirlink)
        self.assertEqual(self.hw("echo x > %s/trust.json" % dirlink), "deny")
        self.assertEqual(self.hw("ln -s ~ %s/h && echo x > %s/h/.studio/trust.json" % (self.tmp, self.tmp)), "ask")
        self.assertEqual(self.hw("tar xf a.tar -C ~"), "ask")                         # a folder that holds a home
        self.assertEqual(self.hw("unzip a.zip -d %s" % self.uhome), "ask")
        self.assertEqual(self.hw("mkdir %s" % os.path.join(self.uhome, ".budgit", "x")), "deny")

    def test_inline_code_that_can_build_a_path_and_substitutions_next_to_a_home_ask(self):
        for cmd in ("python3 -c \"import os;open(os.path.expanduser('~')+'/.bud'+'git/trust.json','w')\"",
                    "perl -e 'open F,\">$ENV{HOME}/.budgit/trust.json\"'", "ruby -e 'File.write(Dir.home+\"/x\",1)'",
                    "cp /tmp/p $(echo ~/.budgit/trust.json)", "cp /tmp/p `echo ~/.budgit`/trust.json"):
            self.assertIn(self.hw(cmd), ("ask", "deny"), cmd)

    def test_ordinary_work_is_left_alone(self):
        for cmd in ("ls ~", "cat ~/.budgit/trust.json", "cd /tmp && cp a b", "echo hi > out.txt", "git status",
                    "grep -n x plugin/scripts/hook.py", "cat plugin/scripts/studiolib.py | head", "cp a.txt b.txt",
                    "mkdir -p /tmp/newdir", "python3 -m unittest discover -s tests", "cd .. && ls"):
            self.assertIsNone(self.hw(cmd), cmd)

    def test_a_guard_that_crashes_asks_instead_of_letting_the_write_through(self):
        real = sl._shell_parts
        sl._shell_parts = lambda c: 1 / 0
        try:
            self.assertEqual(self.hw("echo x > y"), "ask")
        finally:
            sl._shell_parts = real

    def test_the_session_transcripts_are_write_protected(self):
        path = self.transcript()
        self.assertEqual(self.hw("echo x >> %s" % path), "deny")
        self.assertEqual(sl.home_write_decision("Write", {"file_path": path}, self.project)[0], "deny")
        memory = os.path.join(os.path.dirname(path), "memory", "MEMORY.md")
        self.assertIsNone(sl.home_write_decision("Write", {"file_path": memory}, self.project))


class ForgeryTest(HomeCase):
    def test_running_the_hook_the_library_or_claude_p_is_denied(self):
        for cmd in ("echo '{\"prompt\":\"/budgit-trust work\"}' | python3 plugin/scripts/hook.py user-prompt-submit",
                    "python3 %s user-prompt-submit < /tmp/p.json" % HOOK, "python hook.py user-prompt-submit",
                    "./hook.py user-prompt-submit", "python3 -c 'import studiolib; studiolib.grant_trust(\"x\")'",
                    "python3 -m studiolib", "PYTHONPATH=plugin/scripts python3 -c \"import hook\"",
                    "claude -p '/budgit-trust work'", "claude --print '/budgit-trust work'", "env python3 hook.py x",
                    "echo '{}' | xargs python3 plugin/scripts/studiolib.py", "bash -c 'python3 hook.py'",
                    "node -e \"require('child_process').exec('python3 hook.py')\""):
            self.assertEqual(self.hw(cmd), "deny", cmd)


class GrantSecondFactorTest(HookCase2):
    def test_the_typed_message_must_be_the_transcripts_last_user_line(self):
        self.prompt("/budgit-trust work", transcript="something else I typed")
        self.assertFalse(sl.trust_work_on(self.project))
        out = self.prompt("/budgit-trust work", transcript="/budgit-trust work")
        self.assertTrue(sl.trust_work_on(self.project))
        data = json.load(open(sl.trust_path()))["work"][sl._trust_key(self.project)]
        self.assertTrue(data["line_sha"] and data["transcript"].endswith("s.jsonl") and "offset" in data)

    def test_no_transcript_or_one_outside_the_claude_projects_folder_never_grants(self):
        self.assertFalse(sl.grant_trust(self.project))
        self.assertFalse(sl.grant_trust(self.project, None))
        outside = os.path.join(self.tmp, "fake.jsonl")
        open(outside, "w").write(json.dumps({"type": "user", "message": {"content": "/budgit-trust work"}}) + "\n")
        self.assertFalse(sl.grant_trust(self.project, outside))
        self.assertFalse(sl.grant_trust(self.project, os.path.join(self.tmp, "missing.jsonl")))
        self.assertFalse(sl.trust_work_on(self.project))

    def test_the_wrapped_slash_command_form_of_the_transcript_counts(self):
        wrapped = ("<command-message>budgit-trust</command-message>\n<command-name>/budgit-trust</command-name>\n"
                   "<command-args>work</command-args>")
        self.assertTrue(sl.grant_trust(self.project, self.transcript(wrapped)))
        sl.revoke_trust(self.project)
        self.assertFalse(sl.grant_trust(self.project, self.transcript("/budgit-trust work now")))

    def test_a_grant_file_without_a_matching_transcript_line_is_no_grant(self):
        self.grant()
        self.assertTrue(sl.trust_work_on(self.project))
        path = self.transcript("hello")                      # the transcript is rewritten: the proof line is gone
        self.assertFalse(sl.trust_work_on(self.project))
        sl._save_dict(sl.trust_path(), {"work": {sl._trust_key(self.project): {"project": self.project}}})
        self.assertFalse(sl.trust_work_on(self.project))     # a hand-written grant has no proof

    def test_tool_results_and_sidechain_lines_are_not_the_typed_message(self):
        path = self.transcript()
        with open(path, "a") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}) + "\n")
            fh.write(json.dumps({"type": "user", "isSidechain": True, "message": {"content": "agent text"}}) + "\n")
        self.assertTrue(sl.grant_trust(self.project, path))
        with open(path, "a") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "next thing"}}) + "\n")
        self.assertFalse(sl.grant_trust(self.project, path))


class GitReaderClassTest(GuardCase):
    def setUp(self):
        super().setUp()
        git(self.project, "branch", "-q", "feat")

    def test_a_reader_with_an_exec_hook_config_env_or_lexing_trick_asks(self):
        r = self.root
        self.asks("git -C %s -c core.fsmonitor='sh x.sh' status" % r, "git -C %s -c diff.external=x diff" % r,
                  "git -C %s grep -O'sh x.sh' one" % r, "git -C %s grep -Osh one" % r,
                  "git -C %s grep --open-files-in-pager=sh one" % r, "GIT_EXTERNAL_DIFF='sh x.sh' git -C %s diff" % r,
                  "PAGER='sh x.sh' git -C %s log" % r, "LESS=x git -C %s log" % r, "EDITOR=x git -C %s log" % r,
                  "GIT_PAGER=x git -C %s log" % r, "git -C %s --paginate log" % r, "git -C %s -p log" % r,
                  "git -C %s --exec-path=/tmp/x status" % r, "git -C %s --config-env=core.pager=X log" % r,
                  "git -C %s log $'--out\\x70ut=data.json'" % r, "git -C %s log --outp=data.json" % r,
                  "git -C %s diff --ext-diff" % r, "git -C %s log -p --ext-diff" % r, "git -C %s show --textconv HEAD" % r,
                  "git -C %s diff --textconv" % r, "git -C %s log -- 'd*'" % r, "git -C %s log -- *.json" % r,
                  "git -C %s log -- ~/x" % r, "git -C %s log -- $HOME" % r, "git -C %s log -- {a,b}" % r,
                  "git -C %s log `echo -o` x" % r, "git -C %s branch newbranch" % r, "git -C %s branch -D feat" % r,
                  "git -C %s tag newtag" % r, "git -C %s describe --dirty" % r, "git -C %s unknown-verb" % r)

    def test_a_planted_repo_config_that_names_a_program_makes_every_read_ask(self):
        cfg = os.path.join(self.project, ".git", "config")
        original = open(cfg).read()
        for planted in ("[diff]\n\texternal = sh x.sh\n", "[core]\n\tfsmonitor = sh x.sh\n", "[diff \"d\"]\n\ttextconv = sh x\n",
                        "[filter \"f\"]\n\tclean = sh x\n", "[core]\n\thooksPath = /tmp/h\n", "[include]\n\tpath = /tmp/x\n"):
            open(cfg, "w").write(original + planted)
            # R-1294: status and diff carry --ignore-submodules=all, so they ask for the planted config, not for the submodule rule
            self.asks("git -C %s status --ignore-submodules=all" % self.root, "git -C %s diff --ignore-submodules=all" % self.root,
                      "git -C %s log" % self.root)
        open(cfg, "w").write(original)
        self.passes("git -C %s status --ignore-submodules=all" % self.root)

    def test_a_dangerous_variable_in_the_sessions_own_environment_makes_reads_ask(self):
        os.environ["GIT_EXTERNAL_DIFF"] = "sh x.sh"
        self.addCleanup(os.environ.pop, "GIT_EXTERNAL_DIFF", None)
        self.asks("git -C %s diff --ignore-submodules=all" % self.root)     # R-1294: ask for the variable, not the submodule rule

    def test_a_trace_variable_in_the_sessions_own_environment_makes_reads_ask(self):
        # GIT_TRACE and GIT_TRACE2* write a log to the path they hold: a plain read would write protected data
        for name in ("GIT_TRACE", "GIT_TRACE2", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF", "GIT_TRACE_PACKET", "GIT_TRACE_CURL"):
            os.environ[name] = os.path.join(self.root, "data.json")
            try:
                self.asks("git -C %s status --ignore-submodules=all" % self.root, "git -C %s log" % self.root)
            finally:
                os.environ.pop(name, None)
        self.passes("git -C %s status --ignore-submodules=all" % self.root)

    def test_every_tree_rewriting_or_unknown_verb_asks_with_dash_c(self):
        r = self.root
        for verb in ("reset --hard", "checkout -f main", "checkout --force main", "checkout -B x", "stash", "stash pop", "merge feat",
                     "rebase main", "pull", "apply p.patch", "revert HEAD", "push", "config user.name x", "switch feat", "am p.mbox",
                     "branch -D feat", "gc", "update-ref -d refs/heads/feat", "cherry-pick HEAD", "worktree add ../w",
                     "submodule update", "clean -fd", "restore .", "rm -r .", "mv data.json y"):
            self.asks("git -C %s %s" % (r, verb))

    def test_the_guards_own_listing_reads_the_index_only_and_never_runs_a_filter(self):
        mark = os.path.join(self.project, "FILTER-RAN")
        git(self.project, "config", "filter.x.clean", "sh -c 'touch %s; cat'" % mark)
        open(os.path.join(self.project, ".gitattributes"), "w").write("* filter=x\n")
        self.edit("docs/a.md", "changed\n")
        self.asks("git -C %s commit -am x" % self.root, "git -C %s add -u" % self.root)   # tracked protected files: ask
        self.assertFalse(os.path.exists(mark))
        calls = []
        real = sl._subprocess.run
        sl._subprocess.run = lambda argv, *a, **kw: (calls.append(argv), real(argv, *a, **kw))[1]
        try:
            self.d("git commit -am x")
            self.d("git commit -m x")
        finally:
            sl._subprocess.run = real
        verbs = {tuple(c[6:8]) for c in calls}
        self.assertTrue(all(c[:6] == ["git", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
                                      "--no-optional-locks"] for c in calls))
        self.assertTrue(verbs <= {("rev-parse", "--show-toplevel"), ("ls-files", "-z"), ("diff", "--cached")}, verbs)


class ExtractAndTargetOptionTest(GuardCase):
    def test_archives_and_option_given_targets_into_the_root_ask(self):
        r = self.root
        self.asks("cd %s && tar xf a.tar" % r, "cd %s && unzip -o a.zip" % r, "cp -t %s x.json" % r, "cp -t data x.json",
                  "cp --target-directory=%s x.json" % r, "cp --target-directory %s x.json" % r, "7z x a.7z -o%s" % r,
                  "mv -t %s x" % r, "install -t %s x" % r, "ditto a %s" % r, "dd if=/dev/zero of=%s/data.json" % r,
                  "tar xf a.tar -C %s" % r, "unzip a.zip -d %s" % r)
        self.asks("tar xf a.tar", "tar -xzf a.tgz", "unzip a.zip", cwd=self.root)         # extracted in the protected folder
        self.passes("tar czf /tmp/out.tgz /tmp/x", "unzip -l a.zip", cwd=self.tmp)

    def test_a_protected_path_spelled_in_the_other_unicode_form_is_still_protected(self):
        import unicodedata
        nfd, nfc = unicodedata.normalize("NFD", "Caf\u00e9"), unicodedata.normalize("NFC", "Caf\u00e9")
        self.assertNotEqual(nfd, nfc)
        for spelling in (nfd, nfc):
            self.asks("rm %s/x.csv" % spelling, "cp /tmp/x %s/x.csv" % spelling, "echo x > %s/x.csv" % spelling,
                      "rm %s/*.csv" % spelling, "cp /tmp/x %s/%s/x.csv" % (self.root, spelling),
                      "git -C %s restore %s/x.csv" % (self.root, spelling))
        self.assertTrue(sl._protected_match_raw(nfd + "/x.csv", self.project, ["Caf\u00e9/**"]))
        self.assertTrue(sl._protected_match_raw(nfc + "/x.csv", self.project, [nfd + "/**"]))
        self.assertTrue(sl._mentions_literal("rm " + nfd + ".csv", [nfc + ".csv"]))

    def test_every_compare_folds_the_same_way_case_eszett_and_unicode_form(self):
        import json as _json
        import unicodedata
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = _json.load(open(pj))
        cfg["protected_paths"] = list(cfg["protected_paths"]) + ["Stra\u00dfe/**", "Stra\u00dfe.csv"]
        _json.dump(cfg, open(pj, "w"))
        os.makedirs(os.path.join(self.project, "Stra\u00dfe"), exist_ok=True)
        open(os.path.join(self.project, "Stra\u00dfe", "x.csv"), "w").write("one\n")
        for spelling in ("Stra\u00dfe", "STRASSE", "strasse", "Strasse", "STRA\u1e9eE", "stra\u00dfe"):
            self.asks("rm %s/x.csv" % spelling, "cp /tmp/x %s/x.csv" % spelling, "rm %s/*.csv" % spelling, "rm %s.csv" % spelling,
                      "cp /tmp/x %s/%s/x.csv" % (self.root, spelling))
            self.assertTrue(sl._protected_match_raw(spelling + "/x.csv", self.project, ["Stra\u00dfe/**"]), spelling)
            self.assertTrue(sl._mentions_literal("rm " + spelling + ".csv", ["Stra\u00dfe.csv"]), spelling)
        for spelling in ("CAF\u00c9", "caf\u00e9", unicodedata.normalize("NFD", "CAF\u00c9")):
            self.asks("rm %s/x.csv" % spelling, "rm %s/*.csv" % spelling, "cp /tmp/x %s/%s/x.csv" % (self.root, spelling))
        for spelling in ("DATA.JSON", "Data.Json"):
            self.asks("rm %s" % spelling, "cp /tmp/x %s" % spelling)
        self.passes("rm docs/a.md", "rm %s/docs/a.md" % self.root)

    def test_a_working_folder_spelled_in_another_case_is_still_the_project(self):
        other = self.root.upper()
        if other == self.root or not os.path.isdir(other):
            self.skipTest("a case-sensitive filesystem: the upper-case spelling is another folder")
        self.assertEqual(sl.real_data_decision("Bash", {"command": "git clean -fdx"}, self.project, cwd=other)[0], "ask")

    def test_substitution_next_to_protected_data_asks(self):
        self.asks("cat $(cp x data.json)", "cat <(cp x data.json)", "echo `rm data.json`", "cat $(echo data/real)")
        self.passes("echo $(date)", "git commit -m \"$(echo hello)\" -- docs/a.md")


class BudgitIdentityTest(GuardCase):
    def setUp(self):
        super().setUp()
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.path.dirname(CLI) + os.pathsep + self._path
        self.addCleanup(os.environ.__setitem__, "PATH", self._path)

    def test_only_the_sole_simple_command_by_the_exact_file_passes(self):
        r = self.root
        ack = "budgit --project %s ledger ack I-1 --note x" % r
        self.passes(ack, "%s --project %s ledger ack I-1" % (CLI, r))
        self.asks("PATH=/tmp:$PATH; " + ack, "budgit() { rm data.json; }; " + ack, "cd %s && budgit ledger ack I-1" % r,
                  "xargs " + ack, "env " + ack, "PATH=/x " + ack, "X=1 " + ack, ack + " && true", ack + " | cat",
                  "(" + ack + ")", "sh %s --project %s status" % (CLI, r), "budgit --project %s status --limits-json x" % r,
                  "budgit --project %s budget set low" % r, "budgit --project %s budget record" % r,
                  "budgit --project %s budget auto" % r,
                  "budgit --project %s ledger ack I-1 %s/data.json" % (r, r), "budgit --project %s trust work" % r)
        # I-1771 / I-1774: `ledger done` on the project's own ledger is task data; `--evidence data.json` is only text the CLI
        # stores (it never opens the file), so this call passes where it used to ask
        self.passes("budgit --project %s ledger done R-1 --evidence data.json" % r)

    def test_a_cache_sibling_a_copy_and_a_fold_collision_directory_are_look_alikes(self):
        plugin = os.path.join(self.tmp, "plugins", "cache", "m", "p")
        for ver in ("1.0.0", "1.0.1"):
            os.makedirs(os.path.join(plugin, ver, "bin"))
            shutil.copy(CLI, os.path.join(plugin, ver, "bin", "budgit"))
        sibling = os.path.join(plugin, "1.0.0", "bin", "budgit")
        self.asks("%s --project %s ledger ack I-1" % (sibling, self.root))
        folded = os.path.join(self.tmp, os.path.dirname(CLI).upper().lstrip("/"))
        os.makedirs(folded, exist_ok=True)
        shutil.copy(CLI, os.path.join(folded, "budgit"))
        self.asks("%s --project %s ledger ack I-1" % (os.path.join(folded, "budgit"), self.root))


class TrustReadersTest(GuardCase):
    def test_open_and_code_are_not_readers_under_the_grant(self):
        self.grant()
        self.asks("open %s" % self.root, "code %s" % self.root, "tree -o out.txt %s" % self.root)
        self.passes("du -sh %s" % self.root, "tree %s" % self.root)


if __name__ == "__main__":
    unittest.main()
