"""R-123: the mechanical checkpoint written by the PreCompact hook."""
import json, os, re, subprocess, sys

from helpers import StudioTestCase, ROOT
import studiolib as sl
import checkpoint

PLUGIN = os.path.join(ROOT, "plugin")
HOOK = os.path.join(PLUGIN, "scripts", "hook.py")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")


def cps(base):
    """A checkpoint store under a test's own temp folder (write_checkpoint and run require one)."""
    return os.path.join(base, "cps")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=GIT_ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout


class PreCompactTest(StudioTestCase):
    def run_hook(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def transcript(self, *calls):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for name, tin in calls:
                f.write(json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "text", "text": "x"}, {"type": "tool_use", "name": name, "input": tin}]}}) + "\n")
        return path

    # SHALL: the PreCompact hook entry exists and calls `hook.py pre-compact`
    def test_hook_entry_exists_and_calls_the_command(self):
        with open(os.path.join(PLUGIN, "hooks", "hooks.json"), encoding="utf-8") as f:
            groups = json.load(f)["hooks"]["PreCompact"]
        h = groups[0]["hooks"][0]
        self.assertTrue(h["command"].endswith('scripts/hook.py" pre-compact'), h["command"])
        self.assertLessEqual(h["timeout"], 30)
        self.assertNotEqual(h.get("onFailure"), "block")

    # SHALL: the checkpoint holds the open and doing items, the last calls, git status and branch of the project and of
    # a touched worktree, and the path and head of progress.md
    def test_checkpoint_holds_every_part(self):
        sl.activate(self.project, "s1")
        repo = self.project
        git(repo, "init", "-q", "-b", "main")
        with open(os.path.join(repo, "a.txt"), "w") as f:
            f.write("a")
        git(repo, "add", "a.txt")
        git(repo, "commit", "-q", "-m", "init")
        wt = os.path.join(self.tmp, "wt-one")
        git(repo, "worktree", "add", "-q", "-b", "feature-x", wt)
        with open(os.path.join(wt, "b.txt"), "w") as f:
            f.write("b")
        with open(os.path.join(repo, "dirty.txt"), "w") as f:
            f.write("d")
        sdir = sl.studio_dir(repo)
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, "progress.md"), "w", encoding="utf-8") as f:
            f.write("# progress\n- step one done\n")
        tr = self.transcript(("Edit", {"file_path": os.path.join(wt, "b.txt")}),
                             ("Bash", {"command": "curl -H 'Authorization: " + "Bearer " + "SECRET" + "VALUE' x"}))
        items = [{"id": "R-1", "status": "doing", "text": "build the thing"},
                 {"id": "R-2", "status": "open", "text": "second thing"},
                 {"id": "R-3", "status": "done", "text": "finished thing"}]
        path, err = checkpoint.run(repo, sdir, items, tr, "auto", "sess1234", store=cps(self.tmp))
        self.assertEqual(err, "")
        text = open(path, encoding="utf-8").read()
        self.assertIn("R-1 [doing] build the thing", text)
        self.assertIn("R-2 [open] second thing", text)
        self.assertNotIn("finished thing", text)
        self.assertIn("Edit `%s`" % os.path.join(wt, "b.txt"), text)
        self.assertIn("- Bash curl", text)
        self.assertNotIn("SECRETVALUE", text)
        self.assertIn("(branch main)", text)
        self.assertIn("?? dirty.txt", text)
        self.assertIn("(branch feature-x)", text)
        self.assertIn("?? b.txt", text)
        self.assertIn(os.path.join(sdir, "progress.md"), text)
        self.assertIn("- step one done", text)

    # SHALL: only a plain program name and known path fields reach the file; a secret with no keyword never does
    def test_secrets_without_keywords_never_reach_the_file(self):
        calls = [("Bash", {"command": "OPENAI_" + "KEY=sk" + "-live-LEAK1 curl x"}),
                 ("Bash", {"command": "DB_PASS=hunter2LEAK2 GH_PAT=ghp_LEAK3 psql"}),
                 ("Bash", {"command": "A=b B=c git status"}),
                 ("Bash", {"command": "$(cat /x/LEAK4) y"}),
                 ("Bash", {"command": '"sk-abcLEAK5" --flag'}),
                 ("Bash", {"command": "env SECRETVAL=LEAK6 curl x"}),
                 ("Bash", {"command": "sudo -u root LEAK7"}),
                 ("Agent", {"description": "use key sk-LEAK8", "prompt": "password LEAK9", "subagent_type": "x"}),
                 ("WebFetch", {"url": "https://x.invalid/?k=LEAK10", "prompt": "LEAK11"}),
                 ("Grep", {"pattern": "LEAK12", "path": "/p/src"}),
                 ("Write", {"file_path": "/p/f.txt", "content": "LEAK13"}),
                 ("bad name LEAK14", {"file_path": "/p/g.txt"}),
                 # round 3: quoting, escapes, continuation, newline tricks
                 ("Bash", {"command": 'PW="correct sklive-LEAK15 staple" ssh host'}),
                 ("Bash", {"command": "K='a sk-live-LEAK16 b' curl x"}),
                 ("Bash", {"command": "K=a\\ sk-live-LEAK17 curl x"}),
                 ("Bash", {"command": "ls \\\n sk-live-LEAK18"}),
                 ("Bash", {"command": "'unterminated sk-LEAK19"}),
                 ("Bash", {"command": "sk-live-LEAK20"}),
                 ("Bash", {"command": "env git status"}),
                 ("Bash", {"command": "sudo git status"}),
                 ("Read\n", {"file_path": "## Fake4 heading"}),
                 ("Read", {"file_path": "/p/ok.txt\n## Fake5 heading"}),
                 ("Read", {"file_path": "/p/`LEAK21`"}),
                 ("Read", {"file_path": "/p/" + "a" * 400})]
        sdir = os.path.join(self.tmp, "state")
        path, err = checkpoint.run(self.project, sdir, [], self.transcript(*calls), "auto", "s1", store=cps(sdir))
        self.assertEqual(err, "")
        text = open(path, encoding="utf-8").read()
        self.assertNotRegex(text, r"LEAK|sk-|sklive|hunter2|ghp_|OPENAI_KEY|DB_PASS|GH_PAT|Fake|ssh|psql|staple")
        section = text.split("## Last tool calls of this session\n", 1)[1].split("\n## ", 1)[0]
        self.assertTrue(all(l.startswith("- ") for l in section.strip().splitlines()), section)
        self.assertEqual(section.count("(tool)"), 2)
        self.assertIn("- Bash curl", text)
        self.assertIn("- Bash (other)", text)
        self.assertIn("- Grep `/p/src`", text)
        self.assertIn("- Write `/p/f.txt`", text)
        self.assertIn("- Read (path withheld)", text)

    # SHALL: a wrapper is never looked through: `env git status` and `sudo git status` show (other), not git
    def test_wrapper_commands_show_other(self):
        sdir = os.path.join(self.tmp, "state")
        tr = self.transcript(("Bash", {"command": "A=b B=c /usr/bin/git status"}), ("Bash", {"command": "env git status"}), ("Bash", {"command": "sudo git status"}),
                             ("Bash", {"command": "nohup git status"}))
        path, _ = checkpoint.run(self.project, sdir, [], tr, "auto", "s1", store=cps(sdir))
        section = open(path, encoding="utf-8").read().split("## Last tool calls of this session\n", 1)[1]
        self.assertEqual(section.split("\n## ", 1)[0].strip().splitlines(), ["- Bash git"] + ["- Bash (other)"] * 3)

    # SHALL: two checkpoints of one session in one second get two files, and the newest one sorts last
    def test_same_second_checkpoints_do_not_collide(self):
        sdir = os.path.join(self.tmp, "state")
        paths = [checkpoint.write_checkpoint(self.project, sdir, [], [], "x", session="s1", at=1_700_000_000, store=cps(sdir))
                 for _ in range(3)]
        self.assertEqual(len(set(paths)), 3)
        self.assertEqual(checkpoint.newest(cps(sdir)), paths[-1])

    # SHALL: what cannot be known is written as "not available: <reason>", never invented
    def test_missing_parts_are_stated_not_invented(self):
        sdir = os.path.join(self.tmp, "state")
        path, err = checkpoint.run(self.project, sdir, [], None, "manual", "", store=cps(sdir))
        self.assertEqual(err, "")
        text = open(path, encoding="utf-8").read()
        self.assertIn("not available: the hook input has no transcript_path", text)
        self.assertIn("not available: no readable progress.md", text)
        self.assertIn("project: not available: not a git work tree", text)
        self.assertIn("- none", text)

    # SHALL: a failing checkpoint still exits 0 and logs the error
    def test_failing_checkpoint_exits_zero_and_logs(self):
        sl.activate(self.project, "s1")
        os.makedirs(os.path.dirname(self.store()), exist_ok=True)
        with open(self.store(), "w") as f:                           # a file where the store folder must be
            f.write("in the way")
        out = self.run_hook("pre-compact", {"session_id": "s1", "trigger": "auto"})
        self.assertIsNone(out)
        log = open(os.path.join(sl.home_state(), "hooks.log"), encoding="utf-8").read()
        self.assertIn("pre-compact: checkpoint failed", log)

    def store(self):
        return checkpoint.store_dir(sl.home_state(), self.project)

    # SHALL: a checkpoint planted in the repo, or with a foreign name, or a symlink, is never pointed to
    def test_planted_checkpoints_are_never_pointed_to(self):
        sl.activate(self.project, "s1")
        planted = os.path.join(sl.studio_dir(self.project), "checkpoints")
        os.makedirs(planted)
        for name in ("checkpoint-zzINJECT-ignore-previous.md", "checkpoint-29991231T235959Z-s1-01.md"):
            with open(os.path.join(planted, name), "w") as f:
                f.write("INJECT")
        self.run_hook("pre-compact", {"session_id": "s1", "trigger": "auto"})
        store = self.store()
        with open(os.path.join(store, "checkpoint-zzINJECT-x.md"), "w") as f:       # foreign name, even in the store
            f.write("INJECT")
        victim = os.path.join(self.tmp, "victim.md")
        with open(victim, "w") as f:
            f.write("INJECT")
        try:
            os.symlink(victim, os.path.join(store, "checkpoint-29991231T235959Z-s1-01.md"))   # own name, but a link
        except (OSError, NotImplementedError):
            pass
        out = self.run_hook("session-start", {"session_id": "s1", "source": "compact"})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("INJECT", ctx)
        self.assertNotIn("29991231", ctx)
        self.assertIn(os.path.basename(checkpoint.newest(store)), ctx)
        self.assertTrue(checkpoint.newest(store).startswith(store))

    def test_this_sessions_checkpoint_is_preferred(self):
        store = os.path.join(self.tmp, "store")
        mine = checkpoint.write_checkpoint(self.project, self.tmp, [], [], "x", session="mine", at=1_700_000_000, store=store)
        checkpoint.write_checkpoint(self.project, self.tmp, [], [], "x", session="other", at=1_700_000_100, store=store)
        self.assertEqual(checkpoint.newest(store, "mine"), mine)
        self.assertNotEqual(checkpoint.newest(store), mine)

    # SHALL: progress.md that is a symlink, or leaves the state folder, is not read
    def test_symlinked_progress_is_not_read(self):
        state = os.path.join(self.tmp, "state")
        os.makedirs(state)
        secret = os.path.join(self.tmp, "private.txt")
        with open(secret, "w") as f:
            f.write("PRIVATE-KEY-LINE\n")
        try:
            os.symlink(secret, os.path.join(state, "progress.md"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        path, err = checkpoint.run(self.project, state, [], None, "auto", "s1", store=os.path.join(self.tmp, "st"))
        text = open(path, encoding="utf-8").read()
        self.assertNotIn("PRIVATE-KEY-LINE", text)
        self.assertIn("not available: no readable progress.md (progress.md is not a plain file", text)

    # SHALL: a ledger.json that is a symlink is refused and logged, and the checkpoint is still written
    def test_symlinked_state_folder_ledger_is_refused_but_checkpoint_is_written(self):
        # load_ledger refuses a ledger.json that is itself a link; the hook's own check matters when the whole state
        # folder is a link to an outside folder that holds a ledger.json
        sl.activate(self.project, "s1")
        data = sl.load_ledger(self.project)
        sl.add_item(data, "STATEDIRPRIVATE text", ["legacy"])
        sl.save_ledger(self.project, data)
        sdir = sl.studio_dir(self.project)
        outside = os.path.join(self.tmp, "outside-state")
        os.replace(sdir, outside)
        try:
            os.symlink(outside, sdir)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        import hook
        hook.on_pre_compact({"session_id": "s1", "trigger": "auto"}, self.project)
        path = checkpoint.newest(self.store(), "s1")
        self.assertIsNotNone(path)
        self.assertNotIn("STATEDIRPRIVATE", open(path, encoding="utf-8").read())
        log = open(os.path.join(sl.home_state(), "hooks.log"), encoding="utf-8").read()
        self.assertIn("pre-compact: ledger not readable", log)

    def test_symlinked_ledger_is_refused_but_checkpoint_is_written(self):
        sl.activate(self.project, "s1")
        data = sl.load_ledger(self.project)
        sl.add_item(data, "LEDGERPRIVATE text", ["legacy"])
        sl.save_ledger(self.project, data)
        lp = sl._ledger_path(self.project)
        moved = os.path.join(self.tmp, "elsewhere.json")
        os.replace(lp, moved)
        try:
            os.symlink(moved, lp)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        import hook
        hook.on_pre_compact({"session_id": "s1", "trigger": "auto"}, self.project)
        path = checkpoint.newest(self.store(), "s1")
        self.assertIsNotNone(path)
        text = open(path, encoding="utf-8").read()
        self.assertNotIn("LEDGERPRIVATE", text)
        self.assertIn("(0)", text)
        log = open(os.path.join(sl.home_state(), "hooks.log"), encoding="utf-8").read()
        self.assertIn("pre-compact: ledger not readable", log)

    # SHALL: session id and trigger that are not plain names are written as (withheld)
    def test_unsafe_session_and_trigger_are_withheld(self):
        store = os.path.join(self.tmp, "store")
        path = checkpoint.write_checkpoint(self.project, self.tmp, [], [], "x", trigger="auto\n## Fake6",
                                           session="s1\n## Fake7", store=store)
        text = open(path, encoding="utf-8").read()
        self.assertNotIn("Fake", text)
        self.assertIn("trigger: (withheld)", text)
        self.assertIn("session: (withheld)", text)

    # SHALL: the temporary file is created exclusively and without following a link
    def test_planted_tmp_symlink_is_not_followed(self):
        store = os.path.join(self.tmp, "store")
        os.makedirs(store)
        victim = os.path.join(self.tmp, "victim.txt")
        with open(victim, "w") as f:
            f.write("KEEP")
        name = "checkpoint-%s-s1-01.md" % checkpoint._stamp(1_700_000_000)
        try:
            os.symlink(victim, os.path.join(store, "%s.%d.tmp" % (name, os.getpid())))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        path, err = checkpoint.run(self.project, self.tmp, [], None, "auto", "s1", at=1_700_000_000, store=store)
        self.assertIsNone(path)
        self.assertEqual(open(victim).read(), "KEEP")

    # SHALL: every git call carries --no-optional-locks
    def test_git_calls_do_not_take_optional_locks(self):
        seen = []
        real = subprocess.run
        checkpoint.subprocess.run = lambda cmd, **kw: (seen.append(cmd), real(cmd, **kw))[1]
        try:
            checkpoint.git_state(self.project)
        finally:
            checkpoint.subprocess.run = real
        self.assertTrue(seen and all("--no-optional-locks" in c for c in seen), seen)

    def test_unbound_session_exits_zero(self):
        self.assertIsNone(self.run_hook("pre-compact", {"session_id": "nobody", "trigger": "auto"}))

    def test_hook_writes_a_checkpoint_for_a_bound_session(self):
        sl.activate(self.project, "s1")
        self.run_hook("pre-compact", {"session_id": "s1", "trigger": "manual"})
        self.assertIsNotNone(checkpoint.newest(self.store()))
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(self.project), "checkpoints")))
        self.assertFalse(checkpoint._inside(self.store(), self.project))     # outside the repo

    # SHALL: SessionStart after a compaction points to the newest checkpoint
    def test_session_start_compact_points_to_newest_checkpoint(self):
        sl.activate(self.project, "s1")
        self.run_hook("pre-compact", {"session_id": "s1", "trigger": "auto"})
        out = self.run_hook("session-start", {"session_id": "s1", "source": "compact"})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn(checkpoint.newest(self.store()), ctx)
        out = self.run_hook("session-start", {"session_id": "s1", "source": "startup"})
        self.assertNotIn("mechanical checkpoint", out["hookSpecificOutput"]["additionalContext"])

    def test_only_the_newest_twenty_are_kept(self):
        sdir = os.path.join(self.tmp, "state")
        made = [checkpoint.write_checkpoint(self.project, sdir, [], [], "x", at=1_700_000_000 + i, store=cps(sdir)) for i in range(23)]
        left = sorted(os.listdir(cps(sdir)))
        self.assertEqual(len(left), 20)                                        # the number is pinned, not KEEP
        self.assertEqual(left, sorted(os.path.basename(p) for p in made[3:]))  # the 3 oldest are gone, the rest stay
        self.assertEqual(checkpoint.newest(cps(sdir)), made[-1])
        self.assertTrue(os.path.exists(made[-1]))

    # SHALL: one bad transcript line never costs the checkpoint, and a large transcript is read in time
    def test_bad_transcript_line_is_skipped(self):
        path = os.path.join(self.tmp, "bad.jsonl")
        good = json.dumps({"message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/p/a.txt"}}]}})
        with open(path, "w", encoding="utf-8") as f:
            f.write(good + "\n" + "[" * 100000 + "\n" + '{"message": {"content": [{"type": "tool_use", "name": "Read", "input": 5}]}}\n'
                    + "not json\n" + '{"message": {"content": [null, 3, {"type": "tool_use", "name": ["x"]}]}}\n')
        calls, missing = checkpoint.recent_calls_from_transcript(path)
        self.assertEqual(missing, "")
        self.assertIn("Read `/p/a.txt`", [c[0] for c in calls])

    def test_large_transcript_cost_is_bounded_by_the_calls_shown(self):
        small, big = os.path.join(self.tmp, "s.jsonl"), os.path.join(self.tmp, "b.jsonl")
        line = json.dumps({"message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "git " + "x " * 4000}}]}}) + "\n"
        for p, n in ((small, 50), (big, 4000)):
            with open(p, "w", encoding="utf-8") as f:
                f.write(line * n)
        import time as _t
        def cost(p):
            best = 1e9
            for _ in range(3):
                t = _t.process_time()
                checkpoint.recent_calls_from_transcript(p)
                best = min(best, _t.process_time() - t)
            return best
        cost(small)                                                  # warm-up
        base, large = cost(small), cost(big)
        # shlex work is capped at CALLS lines for both; what grows is reading the tail (4000 lines max), not parsing all
        self.assertLess(large, max(base * 40, 0.5))

    # SHALL: the core is OS-neutral. Windows branch: a file name with no ':' and case-folded path compare;
    # POSIX branch: case-sensitive compare. Linux and macOS share the POSIX branch.
    def test_file_name_is_legal_on_windows(self):
        sdir = os.path.join(self.tmp, "state")
        path = checkpoint.write_checkpoint(self.project, sdir, [], [], "x", session="a:b/c\\d", store=cps(sdir))
        self.assertIsNone(re.search(r'[<>:"/\\|?*]', os.path.basename(path)), path)

    def test_windows_style_path_compare_is_case_folded(self):
        old = checkpoint.os.path.normcase
        try:
            checkpoint.os.path.normcase = lambda p: p.lower()          # what ntpath.normcase does
            self.assertTrue(checkpoint._inside(os.path.join(self.tmp, "WT", "f"), os.path.join(self.tmp, "wt")))
        finally:
            checkpoint.os.path.normcase = old

    def test_posix_path_compare_is_case_sensitive_and_prefix_safe(self):
        if os.name == "nt":
            self.skipTest("POSIX branch")
        self.assertFalse(checkpoint._inside(os.path.join(self.tmp, "WT", "f"), os.path.join(self.tmp, "wt")))
        self.assertFalse(checkpoint._inside(os.path.join(self.tmp, "wt-two", "f"), os.path.join(self.tmp, "wt")))
        self.assertTrue(checkpoint._inside(os.path.join(self.tmp, "wt", "f"), os.path.join(self.tmp, "wt")))

    def test_core_uses_no_platform_specific_tools(self):
        src = open(os.path.join(PLUGIN, "scripts", "checkpoint.py"), encoding="utf-8").read()
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("#", '"""')))
        for bad in ("/proc", "lockf", "fcntl", '"ps"', "os.fork"):
            self.assertNotIn(bad, code.replace("`ps`, `/proc`, `lockf`", ""))
