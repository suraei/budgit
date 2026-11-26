"""R-1128 hotfix 1, security round 3, part B (I-1689, I-1684). Synthetic scratch homes and projects only.
H4: the fallback guard reads wrappers, -c strings, eval, substitutions and stdin-fed shells. M1: a Bash route to project.json asks.
M2: Bash writes into a protected folder are denied by the fallback. M5: a refusal never prints a protected folder's name.
LOW: the counter file refuses a symlink; the event the fallback handles is the one hooks.json passes."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

from test_guard_trust import GuardCase
from helpers import ROOT
import hookfallback
import studiolib as sl

SCRIPTS = os.path.join(ROOT, "plugin", "scripts")


class FallbackCase(unittest.TestCase):
    """hookfallback called directly, with a scratch home and a scratch working folder."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.work = os.path.join(self.tmp, "work")
        self.secret = os.path.join(self.tmp, "zq-private-vault")
        for d in (self.home, self.work, self.secret):
            os.makedirs(d)
        self.write_config([self.secret])
        old = {k: os.environ.get(k) for k in ("BUDGIT_HOME", "STUDIO_HOME")}
        os.environ["BUDGIT_HOME"] = self.home
        os.environ.pop("STUDIO_HOME", None)
        for k, v in old.items():
            self.addCleanup(os.environ.pop, k, None) if v is None else self.addCleanup(os.environ.__setitem__, k, v)

    def write_config(self, dirs):
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": dirs}, f)

    def out(self, command, cwd=None):
        return hookfallback.decide("pre-tool-use", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd or self.work})

    def denied(self, command, cwd=None):
        return (self.out(command, cwd).get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"

    def deny_all(self, commands, cwd=None):
        for cmd in commands:
            self.assertTrue(self.denied(cmd, cwd), cmd)

    def allow_all(self, commands, cwd=None):
        for cmd in commands:
            self.assertFalse(self.denied(cmd, cwd), cmd)


class H4WrapperTest(FallbackCase):
    def test_h4_shell_dash_c_and_eval_strings_are_read(self):
        self.deny_all(['bash -c "git push"', "sh -c 'git reset --hard HEAD'", "zsh -lc 'rm -rf x'", 'dash -c "git stash"',
                       "sh -c \"bash -c 'git push'\"", 'eval "git push"', "eval git push", "eval 'eval \"git push\"'",
                       "bash -o pipefail -c 'git clean -fd'", "/bin/bash -ic 'git push'", "sudo bash -c 'git push'"])

    def test_h4_wrappers_with_their_value_flags_are_skipped(self):
        self.deny_all(["timeout 5 git push", "timeout -s KILL 5 git push", "timeout --kill-after=3 5 git push",
                       "sudo -u x git push", "sudo -u x -g y rm -rf z", "nice -n5 git reset --hard", "nice -n 5 git reset --hard",
                       "nohup git push", "stdbuf -o0 git push", "stdbuf -o 0 git push", "setsid git push", "env -i git push",
                       "env -u FOO git push", "env FOO=1 BAR=2 git stash pop", "command git push", "command -p git push",
                       "doas -u x git clean -fd", "time git push", "exec git push", "busybox rm -rf x",
                       "env -S 'git push'", "xargs git push", "echo x | xargs -I{} git push {}", "xargs -n1 rm -rf",
                       "timeout 5 nice -n3 sudo -u x git push", "find . -exec git push {} \\;", "echo a | xargs -P 4 -n 1 rm -rf"])

    def test_h4_a_command_name_the_shell_builds_is_refused(self):
        self.deny_all(["x=git; $x push", "$(printf git) push", "`echo git` push", "git${IFS}push", "$HOME/bin/tool", "${X} push",
                       "$'git' push", "\"$X\" push", "/usr/bin/gi? push", "/bin/r* -rf x", "echo ok && `echo rm` -rf x"])

    def test_h4_a_substitution_anywhere_is_read(self):
        self.deny_all(["echo `git push`", "echo $(git push)", "echo \"$(git push)\"", "echo $(echo $(git push))",
                       "cat <(git push)", "echo `echo \\`git push\\``", "VAR=$(git push) true", "ls >(git push)"])

    def test_h4_a_shell_fed_from_stdin_is_refused(self):
        self.deny_all(["echo 'git push' | sh", "echo 'git push' | bash", "bash <<< \"git push\"", "bash <<EOF\ngit push\nEOF",
                       "sh -s", "cat x | zsh", "echo Z2l0IHB1c2g= | base64 -d | sh", "bash -s -- a", "sh /dev/stdin", "bash -",
                       "bash <(echo git push)"])

    def test_h4_quoting_newlines_and_keywords_do_not_hide_a_shape(self):
        self.deny_all(['gi""t push', "g\\it push", "'git' push", "\"git\" 'push'", "ls\ngit push", "ls\\\n; git push",
                       "if true; then git push; fi", "(git push)", "{ git push; }", "true && (cd x; git push)",
                       "while true; do git stash; done", "echo a #\ngit push", "# x\ngit push", "git -c alias.p=push p",
                       "python3 -c \"import os; os.system('git push')\"", "ssh host 'git push'", "git -C /x $SUB",
                       "git -c core.x=1 --no-pager push"])

    def test_h4_an_unclosed_quote_or_substitution_still_finds_the_shape(self):
        self.deny_all(["git push 'x", "echo $(git push", "rm -rf \"x", "echo `git push"])

    def test_h4_plain_commands_still_pass(self):
        self.allow_all(["git status", "ls -la", "git log --oneline", "timeout 5 ls", "env FOO=1 git status", "sudo -u x ls",
                        "git commit -m \"fix git push bug\"", "bash script.sh", "echo hi", "[ -f x ] && echo y",
                        "cat .budgit/project.json", "nice -n5 make test", "git diff | head", "xargs grep foo", "echo $HOME",
                        "git commit -m \"$(cat <<'EOF'\nfix the thing\nEOF\n)\"", "command -v git", "sed -n 1,3p a.txt"])

    def test_h4_garbage_input_never_raises(self):
        for command in (None, 5, [], {}, "", " ", "\x00", "git \x00 push", "((((", "$(", "`", "'", "\"", "\\", "&&", ";;;", "|",
                        "x" * 20000, "$(" * 300 + ")" * 300, "env " * 500 + "git push", "a\n" * 5000, "\udcff push"):
            try:
                hookfallback.decide("pre-tool-use", {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.work})
            except Exception as err:
                self.fail("%r raised %r" % (command if not isinstance(command, str) else command[:30], err))
        for payload in (None, 5, [], "x", {"tool_name": None}, {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": 5},
                        {"tool_name": "Write", "tool_input": None}, {"tool_name": "Write", "tool_input": {"file_path": 5}},
                        {"tool_name": "Write", "tool_input": {"file_path": "a\x00b"}, "cwd": self.work},
                        {"tool_name": "Bash", "tool_input": {"command": "echo x > a\x00b"}, "cwd": 7}):
            hookfallback.decide("pre-tool-use", payload)

    def test_h4_end_to_end_through_hook_py_with_a_broken_library(self):
        scripts = os.path.join(self.tmp, "scripts")
        os.makedirs(scripts)
        for name in ("hook.py", "hookfallback.py"):
            shutil.copy(os.path.join(SCRIPTS, name), scripts)
        with open(os.path.join(scripts, "studiolib.py"), "w") as f:
            f.write("raise ImportError('broken on purpose')\n")
        env = dict(os.environ, BUDGIT_HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        env.pop("STUDIO_HOME", None)

        def fire(command):
            r = subprocess.run([sys.executable, os.path.join(scripts, "hook.py"), "pre-tool-use"], env=env, cwd=self.work,
                               input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.work}),
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return (json.loads(r.stdout).get("hookSpecificOutput") or {}).get("permissionDecision")
        for cmd in ('bash -c "git push"', "timeout 5 git push", "sudo -u x git push", "eval 'git push'", "echo `git push`",
                    "echo 'git push' | sh", "echo x > %s/a.txt" % self.secret):
            self.assertEqual(fire(cmd), "deny", cmd)
        self.assertIsNone(fire("git status"))


class M2BashWriteTest(FallbackCase):
    def test_m2_bash_writes_into_a_protected_folder_are_denied(self):
        s = self.secret
        self.deny_all(["echo x > %s/a" % s, "echo x >> %s/a" % s, "echo x>%s/a" % s, "echo x >| %s/a" % s, "echo x &> %s/a" % s,
                       "echo x 2> %s/a" % s, "echo x | tee %s/a" % s, "echo x | tee -a %s/a" % s, "cp a %s/a" % s,
                       "cp -r a b %s/" % s, "cp -t %s a" % s, "cp --target-directory=%s a" % s, "mv a %s/a" % s, "mv %s/a b" % s,
                       "sed -i s/a/b/ %s/a" % s, "sed -i.bak s/a/b/ %s/a" % s, "sed --in-place s/a/b/ %s/a" % s,
                       "dd if=/dev/zero of=%s/a" % s, "sudo tee %s/a" % s, "bash -c 'echo x > %s/a'" % s, "echo $(echo x > %s/a)" % s,
                       "touch %s/a" % s, "rm %s/a" % s, "mkdir %s/d" % s, "install a %s/a" % s, "cd %s && echo x > a" % s,
                       "cd %s && touch a" % s, "echo x > $UNKNOWN/a"])

    def test_m2_a_symlink_a_dotdot_and_a_glob_do_not_hide_the_target(self):
        link = os.path.join(self.work, "link")
        os.symlink(self.secret, link)
        open(os.path.join(self.secret, "keep.txt"), "w").close()
        self.deny_all(["echo x > link/a", "echo x > ./link/./a","echo x > %s/../zq-private-vault/a" % self.secret,
                       "rm %s/*.txt" % self.secret, "echo x > %s/ke*" % self.secret, "cd link && echo x > a", "cd .. && cp a work/link/b"])

    @unittest.skipUnless(sys.platform == "darwin", "case folding is a macOS rule")
    def test_m2_a_differently_cased_path_is_the_same_folder_on_macos(self):
        self.deny_all(["echo x > %s/a" % self.secret.upper(), "echo x > %s/a" % self.secret.title(), "tee %s/a" % self.secret.swapcase()])
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            key = "notebook_path" if tool == "NotebookEdit" else "file_path"
            hit = hookfallback.decide("pre-tool-use", {"tool_name": tool, "tool_input": {key: self.secret.upper() + "/a"}, "cwd": self.work})
            self.assertEqual(hit["hookSpecificOutput"]["permissionDecision"], "deny", tool)

    def test_m2_a_protected_pattern_with_a_glob_is_matched_through_a_symlinked_folder_and_case(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(proj, ".budgit"))
        os.makedirs(os.path.join(proj, "Vault"))
        with open(os.path.join(proj, ".budgit", "project.json"), "w") as f:
            json.dump({"protected_paths": ["Vault/*.csv", "plain"]}, f)
        alias = os.path.join(self.tmp, "alias")
        os.symlink(proj, alias)
        self.assertTrue(self.denied("echo x > Vault/a.csv", cwd=proj))
        self.assertTrue(self.denied("echo x > Vault/a.csv", cwd=alias))
        self.assertTrue(self.denied("tee plain/a", cwd=alias))
        if sys.platform == "darwin":
            self.assertTrue(self.denied("echo x > vault/A.CSV", cwd=proj))
        self.assertFalse(self.denied("echo x > Vault/a.txt", cwd=proj))

    def test_m2_other_writes_reads_and_descriptors_pass(self):
        with open(os.path.join(self.secret, "a"), "w") as f:
            f.write("x")
        self.allow_all(["echo x > a.txt", "echo x >> %s/a.txt" % self.work, "cp a b", "cp %s/a ." % self.secret, "cat %s/a" % self.secret,
                        "ls %s" % self.secret, "echo x > /dev/null", "ls 2>&1", "ls 2>/dev/null", "echo x >&2", "sed -n 1p a", "sed s/a/b/ a",
                        "tee a.txt", "git status", "dd if=%s/a of=out.bin" % self.secret])

    def test_m2_any_error_while_checking_a_write_tool_is_a_deny(self):
        real = hookfallback.write_denied
        self.addCleanup(setattr, hookfallback, "write_denied", real)

        def boom(*args):
            raise RuntimeError("boom")
        hookfallback.write_denied = boom
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            out = hookfallback.decide("pre-tool-use", {"tool_name": tool, "tool_input": {"file_path": "/x/a"}, "cwd": self.work})
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", tool)
        hookfallback.write_denied = real
        for bad in ("a\x00b", "\x00", self.secret + "/a\x00"):
            out = hookfallback.decide("pre-tool-use", {"tool_name": "Write", "tool_input": {"file_path": bad}, "cwd": self.work})
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", repr(bad))
        out = self.out("echo x > a\x00b")                    # a Bash write with an unreadable target: the check itself fails
        self.assertIn("systemMessage", out)

    def test_m2_an_unreadable_list_denies_a_bash_write_outside_the_working_folder_but_not_inside(self):
        with open(os.path.join(self.home, "config.json"), "w") as f:
            f.write("{not json")
        self.assertTrue(self.denied("echo x > %s/elsewhere.txt" % self.tmp))
        self.allow_all(["echo x > inside.txt", "echo x > /dev/null"])

    def test_m2_the_reduced_text_says_bash_writes_are_checked_only_for_protected_folders(self):
        text = hookfallback.REDUCED
        self.assertIn("Bash write is checked only for protected folders", text)
        self.assertIn("enforcement reduced", self.out("git status")["systemMessage"])

    def test_m5_a_fallback_refusal_never_prints_a_protected_folder_name(self):
        for cmd in ("echo x > %s/a" % self.secret, "cp a %s/a" % self.secret, "git push"):
            text = json.dumps(self.out(cmd))
            self.assertNotIn("zq-private-vault", text, cmd)
        for tool in ("Write", "Edit"):
            text = json.dumps(hookfallback.decide("pre-tool-use", {"tool_name": tool, "tool_input": {"file_path": self.secret + "/a"},
                                                                    "cwd": self.work}))
            self.assertNotIn("zq-private-vault", text)
            self.assertIn("protected folder", text)


class LowTest(FallbackCase):
    def test_low_the_counter_file_is_not_written_through_a_symlink(self):
        target = os.path.join(self.tmp, "victim.txt")
        with open(target, "w") as f:
            f.write("keep")
        os.symlink(target, os.path.join(self.home, "hook-fallback.count"))
        hookfallback.count()
        with open(target) as f:
            self.assertEqual(f.read(), "keep")

    def test_low_the_counter_counts_runs_and_never_raises(self):
        hookfallback.count()
        hookfallback.count()
        with open(os.path.join(self.home, "hook-fallback.count")) as f:
            self.assertEqual(f.read(), "2")
        os.environ["BUDGIT_HOME"] = os.path.join(self.tmp, "file-not-folder")
        with open(os.environ["BUDGIT_HOME"], "w") as f:
            f.write("x")
        hookfallback.count()

    def test_low_the_event_the_fallback_handles_is_the_one_hooks_json_passes(self):
        with open(os.path.join(ROOT, "plugin", "hooks", "hooks.json")) as f:
            hooks = json.load(f)
        passed = {}
        for group, entries in hooks["hooks"].items():
            for entry in entries:
                for h in entry.get("hooks", []):
                    m = re.search(r"scripts/hook\.py\"? ([a-z-]+)", h.get("command", ""))
                    if m:
                        passed.setdefault(group, set()).add(m.group(1))
        self.assertIn(hookfallback.PRE_EVENT, passed["PreToolUse"])
        # a write tool is judged only on the event the PreToolUse group passes; every other event gets the reduced text only
        for event in set().union(*passed.values()) - {hookfallback.PRE_EVENT}:
            out = hookfallback.decide(event, {"tool_name": "Bash", "tool_input": {"command": "git push"}})
            self.assertNotIn("hookSpecificOutput", out, event)
        out = hookfallback.decide(hookfallback.PRE_EVENT, {"tool_name": "Bash", "tool_input": {"command": "git push"}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")


class M1BashRouteTest(GuardCase):
    def ask(self, command):
        return sl.contract_lock_decision("Bash", {"command": command}, self.root, self.root)

    def test_m1_a_bash_command_naming_project_json_and_search_roots_allowed_asks(self):
        for cmd in ("grep search_roots_allowed .budgit/project.json", "echo '{\"search_roots_allowed\": [\"/\"]}' > .budgit/project.json",
                    "python3 -c \"import json; d=json.load(open('.budgit/project.json')); d['search_roots_allowed']=['/']\"",
                    "sed -i 's/\"x\"/\"search_roots_allowed\": [\"~\"]/' .budgit/project.json"):
            ask = self.ask(cmd)
            self.assertEqual(ask[0] if ask else None, "ask", cmd)
            self.assertIn("search_roots_allowed", ask[1])

    def test_m1_every_write_construct_on_project_json_asks(self):
        for cmd in ("sed -i '' 's/a/b/' .budgit/project.json", "sed -i.bak s/a/b/ .budgit/project.json", "tee .budgit/project.json < x",
                    "echo {} > .budgit/project.json", "echo {} >> .budgit/project.json", "cat x >| .budgit/project.json",
                    "python3 - .budgit/project.json", "perl -pi -e 's/a/b/' .budgit/project.json", "cp /tmp/x .budgit/project.json",
                    "mv /tmp/x .budgit/project.json", "dd of=.budgit/project.json", "install x .budgit/project.json",
                    "echo {} > .budgit/proj\"ect.js\"on", "echo {} > .budgit/pro\\ject.json", "echo {} > .budgit/p*.json",
                    "echo {} > .studio/project.json", "ruby -e 1 .budgit/project.json", "node x.js .budgit/project.json",
                    "cd .budgit && echo {} > project.json", "bash -c 'echo {} > .budgit/project.json'", "truncate -s 0 .budgit/project.json",
                    "echo x${IFS}> .budgit/project.json", "ln -sf x .budgit/project.json", "rm .budgit/project.json"):
            ask = self.ask(cmd)
            self.assertEqual(ask[0] if ask else None, "ask", cmd)

    def test_m1_reads_and_other_files_do_not_ask(self):
        for cmd in ("cat .budgit/project.json", "ls .budgit", "git status", "grep -n search_roots_allowed plugin/scripts/searchguard.py",
                    "cat .budgit/project.json 2>/dev/null", "cat .budgit/project.json 2>&1", "echo x > notes.txt", "head -3 .budgit/project.json",
                    "sed -n 1p .budgit/project.json", "python3 -m unittest", "jq . .budgit/project.json"):
            self.assertIsNone(self.ask(cmd), cmd)

    def test_m1_the_ask_comes_through_the_hook_for_a_bound_session(self):
        import json as _json
        sl.write_pointer("s-m1", self.root)
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s-m1", "cwd": self.root,
                   "tool_input": {"command": "sed -i 's/a/b/' .budgit/project.json"}}
        env = dict(os.environ, STUDIO_HOME=self.home, PWD=self.root)
        hook = os.path.join(SCRIPTS, "hook.py")
        r = subprocess.run([os.path.realpath(sys.executable), hook, "pre-tool-use"], input=_json.dumps(payload), env=env,
                           capture_output=True, text=True, cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = _json.loads(r.stdout)["hookSpecificOutput"]
        self.assertIn(out["permissionDecision"], ("ask", "deny"), out)

    def test_m1_the_edit_route_still_asks(self):
        pj = os.path.join(self.root, ".budgit", "project.json")
        cfg = json.load(open(pj))
        cfg["search_roots_allowed"] = ["/"]
        ask = sl.contract_lock_decision("Write", {"file_path": pj, "content": json.dumps(cfg)}, self.root, self.root)
        self.assertEqual(ask[0], "ask")


class M5OorDetailTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.vault = os.path.join(self.tmp, "zq-private-vault")
        os.makedirs(os.path.join(self.vault, "sub"))
        os.makedirs(os.path.join(self.tmp, "plain-notes"))
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": [self.vault]}, f)

    def into(self, paths=(), doubts=()):
        return types.SimpleNamespace(oor_keys=[("oor-write", p) for p in paths], doubts=list(doubts))

    def test_m5_a_path_inside_a_protected_folder_is_never_printed(self):
        text = sl._oor_detail(self.into([os.path.join(self.vault, "sub", "a.csv")]))
        self.assertNotIn("zq-private-vault", text)
        self.assertNotIn("a.csv", text)
        self.assertIn("a protected folder", text)

    def test_m5_the_protected_name_is_hidden_wherever_it_sits_in_the_text(self):
        for path in (os.path.join(self.vault, "x"), "/elsewhere/ZQ-Private-Vault/x", self.vault.upper() + "/y", "../zq-private-vault/z"):
            self.assertNotIn("private", sl._oor_detail(self.into([path])).lower(), path)
        text = sl._oor_detail(self.into([], ["a copy into zq-private-vault/sub"]))
        self.assertNotIn("zq-private-vault", text)
        self.assertIn("a protected folder", text)

    def test_m5_a_path_outside_every_protected_folder_is_still_shown(self):
        outside = os.path.join(self.tmp, "plain-notes", "a.csv")
        text = sl._oor_detail(self.into([outside]))
        self.assertIn("plain-notes", text)
        self.assertIn("matched path", text)
        self.assertIn("matched: a copy to ../x", sl._oor_detail(self.into([], ["a copy to ../x"])))
        self.assertEqual(sl._oor_detail(self.into()), "rule: no real data outside the project folder")

    def test_m5_an_unreadable_protected_list_hides_the_path(self):
        with open(os.path.join(self.home, "config.json"), "w") as f:
            f.write("{not json")
        text = sl._oor_detail(self.into([os.path.join(self.tmp, "plain-notes", "a.csv")]))
        self.assertNotIn("plain-notes", text)

    def test_m5_a_real_refusal_through_the_hook_prints_no_protected_name(self):
        sl.write_pointer("s-m5", self.root)
        cmd = "cp %s/sub/a.csv %s/copy.csv" % (self.vault, self.root)
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s-m5", "cwd": self.root,
                   "tool_input": {"command": "cp data.json %s/sub/b.csv" % self.vault}}
        env = dict(os.environ, STUDIO_HOME=self.home, PWD=self.root)
        for command in (cmd, payload["tool_input"]["command"]):
            payload["tool_input"]["command"] = command
            r = subprocess.run([os.path.realpath(sys.executable), os.path.join(SCRIPTS, "hook.py"), "pre-tool-use"],
                               input=json.dumps(payload), env=env, capture_output=True, text=True, cwd=self.root)
            self.assertEqual(r.returncode, 0, r.stderr)
            reason = (json.loads(r.stdout).get("hookSpecificOutput") or {}).get("permissionDecisionReason", "") if r.stdout.strip() else ""
            self.assertNotIn("zq-private-vault", reason)


if __name__ == "__main__":
    unittest.main()
