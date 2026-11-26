"""R-1259 (I-1983): tools/runtests.py, run alone on a green run, writes a receipt in the worktree's own git dir; commitguard lets a
subagent make a plain `git commit` when that receipt names the index tree, this git dir and the pinned runner, and is under 30 minutes old.
Every test runs in temp repos with a fake HOME and fake names (zorb, quux)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from helpers import StudioTestCase

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import commitguard  # noqa: E402
import hook  # noqa: E402
import runtests as rt  # noqa: E402
import studiolib as sl  # noqa: E402

NOW = 1_000_000_000.0
NAME = "budgit-test-receipt.json"


class RepoCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.gh = os.path.join(self.tmp, "githome")               # git's own HOME; the Budgit home is StudioTestCase's self.home
        os.makedirs(self.gh)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=self.gh, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        self.repo = self.make_repo("repo")

    def receipt_file(self, repo=None):
        """R-1259 MED-1: the receipt lives under the Budgit home, one file per checkout (named by a hash of its git dir)."""
        return sl.receipt_path(self.gitdir(repo or self.repo))

    def git(self, repo, *args):
        r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, (args, r.stderr))
        return r.stdout.strip()

    def make_repo(self, name):
        path = os.path.realpath(os.path.join(self.tmp, name))
        os.makedirs(path)
        subprocess.run(["git", "init", "-q", path], check=True, env=self.env)
        self.git(path, "config", "user.name", "Quux")
        self.git(path, "config", "user.email", "quux@users.noreply.github.com")
        self.put(path, "zorb.txt", "zorb\n")
        self.git(path, "add", "zorb.txt")
        for marker in ("tools/runtests.py", "plugin/scripts/commitguard.py"):     # R-1360 C3: these repos stand in for the Budgit checkout
            os.makedirs(os.path.dirname(os.path.join(path, marker)), exist_ok=True)
            self.put(path, marker, "# stub\n")
            self.git(path, "add", marker)
        self.git(path, "commit", "-q", "-m", "zorb")
        return path

    def put(self, repo, name, text):
        with open(os.path.join(repo, name), "w") as f:
            f.write(text)

    def gitdir(self, repo):
        return os.path.realpath(self.git(repo, "rev-parse", "--absolute-git-dir"))

    def receipt(self, repo=None, raw=None, **over):
        """A receipt for the repo's index tree as it is now (or the raw text given)."""
        repo = repo or self.repo
        gd = self.gitdir(repo)
        data = {"v": 1, "tree": self.git(repo, "write-tree"), "gitdir": gd, "runner_sha256": sl.RUNTESTS_SHA256,
                "modules": ["test_zorb", "test_quux"], "need": [], "head": self.git(repo, "rev-parse", "HEAD"), "time": NOW}
        data.update(over)
        path = sl.receipt_path(gd)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(raw if raw is not None else json.dumps(data))
        return path

    def with_runner(self, repo, tests):
        """A checkout that holds the pinned runner and these test files (name -> text), committed; the guard reads its selection."""
        os.makedirs(os.path.join(repo, "tools"), exist_ok=True)
        os.makedirs(os.path.join(repo, "tests"), exist_ok=True)
        shutil.copy(os.path.realpath(rt.__file__), os.path.join(repo, "tools", "runtests.py"))
        self.git(repo, "add", "tools/runtests.py")
        for name, text in tests.items():
            self.put(repo, "tests/" + name + ".py", text)
            self.git(repo, "add", "tests/" + name + ".py")
        self.git(repo, "commit", "-q", "-m", "runner and tests")


class ReceiptGuardTest(RepoCase):
    def d(self, cmd, cwd=None, agent="a1", now=NOW + 60):
        return commitguard.decision("Bash", {"command": cmd}, agent, cwd if cwd is not None else self.repo, now)

    def denied(self, cmd, **kw):
        out = self.d(cmd, **kw)
        self.assertIsNotNone(out, cmd)
        self.assertEqual(out[0], "deny", cmd)
        return out[1]

    def test_same_tree_plain_commit_passes(self):
        self.receipt()
        self.assertIsNone(self.d("git commit -m zorb"))
        self.assertIsNone(self.d("git -C %s commit -m zorb" % self.repo))

    def test_edited_after_run_is_refused(self):
        self.receipt()
        self.put(self.repo, "quux.txt", "quux\n")
        self.git(self.repo, "add", "quux.txt")
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb"))

    def test_checkout_other_branch_tree_refused_same_tree_passes(self):
        self.git(self.repo, "checkout", "-q", "-b", "other")
        self.put(self.repo, "quux.txt", "quux\n")
        self.git(self.repo, "add", "quux.txt")
        self.git(self.repo, "commit", "-q", "-m", "quux")
        self.git(self.repo, "checkout", "-q", "-")
        self.receipt()
        self.assertIsNone(self.d("git commit -m zorb"))
        self.git(self.repo, "checkout", "-q", "other")
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb"))
        self.git(self.repo, "checkout", "-q", "-")
        self.assertIsNone(self.d("git commit -m zorb"))

    def test_stale_over_30_min_refused_29_passes_future_refused(self):
        self.receipt()
        self.assertIsNone(self.d("git commit -m zorb", now=NOW + 29 * 60))
        self.assertIsNone(self.d("git commit -m zorb", now=NOW + 30 * 60))
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb", now=NOW + 30 * 60 + 1))
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb", now=NOW - 5))

    def test_garbled_receipt_refused(self):
        good = {"v": 1, "tree": self.git(self.repo, "write-tree"), "gitdir": self.gitdir(self.repo),
                "runner_sha256": sl.RUNTESTS_SHA256, "modules": ["test_zorb"], "need": [], "head": self.git(self.repo, "rev-parse", "HEAD"), "time": NOW}
        bad = [json.dumps(dict(good, head=5)), json.dumps({k: v for k, v in good.items() if k != "head"}),json.dumps(dict(good, need="test_zorb")), json.dumps(dict(good, need=[1])), json.dumps({k: v for k, v in good.items() if k != "need"}),
"", "{not json", "[]", "null", json.dumps(dict(good, extra=1)), json.dumps({k: v for k, v in good.items() if k != "tree"}),
               json.dumps(dict(good, modules=[])), json.dumps(dict(good, modules="test_zorb")), json.dumps(dict(good, modules=[1])),
               json.dumps(dict(good, time="now")), json.dumps(dict(good, time=True)), json.dumps(dict(good, tree="xyz")),
               json.dumps(dict(good, v=2)), json.dumps(dict(good, tree="0" * 40))]
        for raw in bad:
            self.receipt(raw=raw)
            self.assertIn("no fresh green receipt", self.denied("git commit -m zorb"), raw)
        self.receipt(raw=json.dumps(good))
        self.assertIsNone(self.d("git commit -m zorb"))

    def test_receipt_from_another_worktree_refused(self):
        wt = os.path.join(self.tmp, "wt2")
        self.git(self.repo, "worktree", "add", "-q", "--detach", wt, "HEAD")
        wt = os.path.realpath(wt)
        mine = self.receipt()
        with open(mine) as f:
            text = f.read()
        gd = self.gitdir(wt)
        self.assertNotEqual(gd, self.gitdir(self.repo))
        self.assertNotEqual(self.receipt_file(wt), mine)         # one file per checkout: worktrees never collide
        with open(self.receipt_file(wt), "w") as f:
            f.write(text)                                    # the same tree, copied from the other worktree
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb", cwd=wt))
        self.receipt(repo=wt)
        self.assertIsNone(self.d("git commit -m zorb", cwd=wt))

    def test_head_moved_since_the_run_refused(self):
        self.receipt()
        self.assertIsNone(self.d("git commit -m zorb"))
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "moved")     # the same tree, a new HEAD
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb"))
        self.receipt()
        self.assertIsNone(self.d("git commit -m zorb"))

    def test_other_runner_hash_refused(self):
        self.receipt(runner_sha256="0" * 64)
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb"))

    def test_nothing_staged_judged_by_receipt(self):
        self.assertIn("no fresh green receipt", self.denied("git commit -m zorb"))
        self.receipt()
        self.assertIsNone(self.d("git commit -m zorb"))               # git itself then says "nothing to commit"

    def test_author_noreply_commit_form_passes(self):
        self.receipt()
        self.put(self.repo, "msg.txt", "zorb\n")
        for cmd in ('git commit -m "zorb (R-1)" --author "Quux <quux@users.noreply.github.com>"',
                    'git commit -m zorb --author="Quux <quux@users.noreply.github.com>"',
                    "git commit --message=zorb -q", "git commit -F msg.txt --no-edit", "git commit --quiet -m a -m b",
                    "git commit --file msg.txt"):
            self.assertIsNone(self.d(cmd), cmd)

    def test_author_with_dollar_or_backslash_falls_back_to_chain(self):
        self.receipt()
        for cmd in ('git commit -m zorb --author "Quux $x <q@users.noreply.github.com>"', "git commit -m zorb --author 'Quux\\x'",
                    "git commit -m zorb --author=`echo q`", 'git commit -m "zorb $HOME"'):
            self.assertIn("<tests> && git commit", self.denied(cmd), cmd)

    def test_substitution_staging_a_file_refused(self):
        self.receipt()
        for cmd in ('git commit -m "$(git add zorb.txt)"', "git commit -m `git add zorb.txt`", "git commit -m x <(git add zorb.txt)",
                    "git commit -m x >(cat)", "git commit -m x > out.txt", "git commit -m x < in.txt", "git commit -m x <<EOF"):
            self.assertIn("<tests> && git commit", self.denied(cmd), cmd)

    def test_git_index_file_prefix_refused(self):
        self.receipt()
        for cmd in ("GIT_INDEX_FILE=x git commit -m a", "git --git-dir=/x commit -m a", "git --work-tree=/x commit -m a",
                    "git -c core.hooksPath=/x commit -m a", "env git commit -m a", "nice git commit -m a", "/usr/bin/git commit -m a",
                    "git -C %s -C /x commit -m a" % self.repo, "timeout 5 git commit -m a"):
            self.assertIn("<tests> && git commit", self.denied(cmd), cmd)

    def test_amend_all_pathspec_add_chain_refused(self):
        self.receipt()
        for cmd in ("git commit -a -m x", "git commit --amend -m x", "git commit -m x -- zorb.txt", "git commit -m x zorb.txt",
                    "git add zorb.txt && git commit -m x", "git commit -m x; git commit -m y", "git commit -n -m x",
                    "git commit --no-verify -m x", "git commit -am x", "git commit -i -m x", "git commit -o -m x"):
            self.assertIn("<tests> && git commit", self.denied(cmd), cmd)

    def test_unquoted_glob_or_tilde_in_the_command_refused(self):
        self.receipt()
        for cmd in ("git commit -m *", "git commit -m x?", "git commit -m ~", "git commit -m {a,b}"):
            self.assertIn("<tests> && git commit", self.denied(cmd), cmd)

    def test_dash_c_must_be_the_cwds_own_repo(self):
        other = self.make_repo("other")
        self.receipt(repo=other)
        self.receipt()
        sub = os.path.join(self.repo, "sub")
        os.makedirs(sub)
        self.assertIn("<tests> && git commit", self.denied("git -C %s commit -m a" % other))
        self.assertIsNone(self.d("git -C %s commit -m a" % self.repo, cwd=sub))
        self.assertIsNone(self.d("git commit -m a", cwd=sub))

    def test_one_line_fix_message(self):
        why = self.denied("git commit -m zorb")
        self.assertNotIn("\n", why)
        self.assertIn("runtests.py", why)
        self.assertIn("alone", why)
        self.assertIn("<tests> && git commit", why)

    def test_git_missing_or_timeout_gives_tree_unreadable_line(self):
        self.receipt()
        for exc in (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 5)):
            with mock.patch.object(commitguard.subprocess, "run", side_effect=exc):
                why = self.denied("git commit -m zorb")
            self.assertIn("could not read the tree, run again", why)
            self.assertNotIn("\n", why)

    def test_cwd_none_keeps_old_refusal(self):
        self.receipt()
        out = commitguard.decision("Bash", {"command": "git commit -m zorb"}, "a1", None, NOW + 60)
        self.assertEqual(out[0], "deny")
        self.assertEqual(out[1], commitguard.REASON)

    def test_coordinator_is_never_affected(self):
        self.assertIsNone(self.d("git commit -m zorb", agent=None))

    def test_accept_note_lists_modules(self):
        self.receipt()
        note = commitguard.accept_note("Bash", {"command": "git commit -m zorb"}, "a1", self.repo, NOW + 60)
        self.assertIn("2 modules", note)
        self.assertIn("test_zorb", note)
        self.assertIn("test_quux", note)
        self.assertIsNone(commitguard.accept_note("Bash", {"command": "git commit -m zorb"}, "a1", self.repo, NOW + 99999))
        self.assertIsNone(commitguard.accept_note("Bash", {"command": "git commit -m zorb"}, None, self.repo, NOW + 60))
        self.assertIsNone(commitguard.accept_note("Bash", {"command": "pytest && git commit -m zorb"}, "a1", self.repo, NOW + 60))

    def test_chained_form_still_passes_without_a_receipt(self):
        self.assertIsNone(self.d("python3 -m unittest test_zorb && git commit -m zorb"))
        self.assertIsNone(self.d("npm test && git commit -m zorb"))

    def test_js_chains_behaviour_unchanged(self):
        self.assertIsNone(self.d("npm test && git commit -m zorb"))
        self.assertIsNone(self.d("pnpm run test && git commit -m zorb"))
        self.assertIsNone(self.d("node --test t.js && git commit -m zorb", now=NOW + 99999))     # R-1326: node --test is a runner now
        self.assertEqual(self.d("node t.js && git commit -m zorb", now=NOW + 99999)[0], "deny")     # a plain node script still is not


class HookTest(RepoCase):
    def call(self, cmd, agent="a1"):
        hook._OUT.clear()
        inp = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.repo, "session_id": "s-r1259"}
        if agent:
            inp["agent_id"] = agent
        hook.on_pre_tool_use(inp, None)
        out = dict(hook._OUT.get("hookSpecificOutput") or {})
        hook._OUT.clear()
        return out

    def test_accepted_commit_adds_context_line_and_is_not_an_ask(self):
        self.receipt(time=time.time())
        with mock.patch.object(commitguard, "receipt_ok", wraps=commitguard.receipt_ok) as ok:
            out = self.call("git commit -m zorb")
        self.assertNotIn("permissionDecision", out, out)                  # no deny and no ask
        self.assertIn("commit accepted on a green run of 2 modules", out.get("additionalContext", ""))
        self.assertEqual(ok.call_count, 1)                                # one receipt read for the verdict and the note

    def test_commit_without_receipt_is_denied_with_the_fix_line(self):
        out = self.call("git commit -m zorb")
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        self.assertIn("no fresh green receipt", out["permissionDecisionReason"])

    def test_coordinator_commit_gets_no_note(self):
        self.receipt(time=time.time())
        out = self.call("git commit -m zorb", agent=None)
        self.assertNotIn("commit accepted", out.get("additionalContext", ""))


class RunnerReceiptTest(RepoCase):
    def run_main(self, rc=0, during=None, args=("--workers", "1")):
        def fake(run):
            if during:
                during()
            return rc
        out = io.StringIO()
        with mock.patch.object(rt, "ROOT", self.repo), mock.patch.object(rt, "discover", return_value=["test_zorb"]), \
                mock.patch.object(rt, "choose_workers", return_value=(1, "test")), mock.patch.object(rt, "supervise", side_effect=fake), \
                contextlib.redirect_stdout(out):
            got = rt.main(["runtests.py"] + list(args))
        self.assertEqual(got, rc)
        return out.getvalue()

    def path(self):
        return self.receipt_file()

    def test_green_run_writes_receipt(self):
        self.run_main()
        with open(self.path()) as f:
            data = json.load(f)
        self.assertEqual(data["tree"], self.git(self.repo, "write-tree"))
        self.assertEqual(data["gitdir"], self.gitdir(self.repo))
        self.assertEqual(data["modules"], ["test_zorb"])
        self.assertEqual(sorted(data), ["gitdir", "head", "modules", "need", "runner_sha256", "time", "tree", "v"])
        self.assertEqual(data["head"], self.git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(data["need"], [])                                       # nothing staged: no test file is needed

    def test_runner_records_the_selection_for_the_staged_files(self):
        self.put(self.repo, "quux.txt", "quux\n")
        self.git(self.repo, "add", "quux.txt")
        with mock.patch.object(rt, "select", return_value=(["test_quux"], "x")) as sel:
            self.run_main()
        sel.assert_called_once_with(["quux.txt"])
        with open(self.path()) as f:
            data = json.load(f)
        self.assertEqual(data["need"], ["test_quux"])
        self.assertEqual(data["modules"], ["test_zorb"])

    def test_runner_and_guard_agree_on_receipt_path(self):
        import inspect
        self.assertNotIn("studiolib", inspect.getsource(rt.write_receipt))    # no checkout code is imported to find the path
        gd = self.gitdir(self.repo)
        self.assertEqual(rt.receipt_path(gd), sl.receipt_path(gd))             # StudioTestCase's STUDIO_HOME
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": os.path.join(self.tmp, "bh")}):
            self.assertEqual(rt.receipt_path(gd), sl.receipt_path(gd))
        fake = os.path.join(self.tmp, "fakeuser")
        for sub, marker in ((".budgit", "ledger.json"), (".studio", "ledger.json"), (".studio", None)):
            os.makedirs(os.path.join(fake, sub), exist_ok=True)
            if marker:
                self.put(os.path.join(fake, sub), marker, "{}")
            with mock.patch.dict(os.environ, {"HOME": fake}):
                os.environ.pop("BUDGIT_HOME", None)
                os.environ.pop("STUDIO_HOME", None)
                self.assertEqual(rt.receipt_path(gd), sl.receipt_path(gd), (sub, marker))
        self.assertFalse(os.path.exists(os.path.join(self.repo, NAME)))          # never in the tree
        self.assertFalse(os.path.exists(os.path.join(self.gitdir(self.repo), NAME)))     # nor in the git dir any more
        self.assertTrue(os.path.realpath(self.path()).startswith(os.path.realpath(sl.home_state()) + os.sep))   # under the Budgit home

    def test_run_with_module_argument_leaves_no_receipt(self):
        for args in (("--workers", "1", "test_zorb"), ("--workers", "1", "--files", "zorb.txt")):
            with mock.patch.object(rt, "select", return_value=(["test_zorb"], "x")):
                out = self.run_main(args=args)
            self.assertFalse(os.path.exists(self.path()), args)
            self.assertIn("receipt not written", out)
        with mock.patch.object(rt, "changed_files", return_value=["zorb.txt"]), mock.patch.object(rt, "select", return_value=(["test_zorb"], "x")):
            self.run_main(args=("--workers", "1", "--changed"))      # the runner's own selection is the default one
        with open(self.path()) as f:
            self.assertEqual(json.load(f)["modules"], ["test_zorb"])

    def test_red_run_writes_none(self):
        self.run_main(rc=1)
        self.assertFalse(os.path.exists(self.path()))

    def test_aborted_and_empty_run_write_none(self):
        for rc in (2, 3, 4):
            self.run_main(rc=rc)
            self.assertFalse(os.path.exists(self.path()), rc)
        with mock.patch.object(rt, "ROOT", self.repo), mock.patch.object(rt, "discover", return_value=[]), \
                mock.patch.object(rt, "select", return_value=([], "none")), mock.patch.object(rt, "choose_workers", return_value=(1, "t")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rt.main(["runtests.py", "--files", "zorb.txt"]), 0)
        self.assertFalse(os.path.exists(self.path()))

    def test_dirty_tree_writes_none(self):
        self.put(self.repo, "zorb.txt", "edited, not staged\n")
        self.assertIn("receipt not written", self.run_main())
        self.assertFalse(os.path.exists(self.path()))

    def test_tracked_file_edited_during_run_writes_none(self):
        self.run_main(during=lambda: self.put(self.repo, "zorb.txt", "edited during\n"))
        self.assertFalse(os.path.exists(self.path()))

    def test_tree_changed_during_run_writes_none(self):
        def stage():
            self.put(self.repo, "quux.txt", "quux\n")
            self.git(self.repo, "add", "quux.txt")
        self.run_main(during=stage)
        self.assertFalse(os.path.exists(self.path()))

    def test_untracked_file_made_by_the_run_does_not_block_the_receipt(self):
        self.run_main(during=lambda: self.put(self.repo, "suite-times.jsonl", "x\n"))    # the runner itself writes such a file
        self.assertTrue(os.path.exists(self.path()))

    def test_subdirectory_and_git_env_set_write_the_right_receipt(self):
        other = self.make_repo("elsewhere")
        sub = os.path.join(self.repo, "sub")
        os.makedirs(sub)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(sub)
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(other, ".git"), "GIT_INDEX_FILE": os.path.join(self.tmp, "nope"),
                                          "GIT_WORK_TREE": other}):
            self.run_main()
        with open(self.path()) as f:
            self.assertEqual(json.load(f)["tree"], self.git(self.repo, "write-tree"))
        self.assertFalse(os.path.exists(self.receipt_file(other)))

    def test_unreadable_git_writes_none_and_does_not_crash(self):
        with mock.patch.object(rt.subprocess, "run", side_effect=FileNotFoundError("git")):
            out = io.StringIO()
            with mock.patch.object(rt, "discover", return_value=["test_zorb"]), mock.patch.object(rt, "choose_workers", return_value=(1, "t")), \
                    mock.patch.object(rt, "supervise", return_value=0), contextlib.redirect_stdout(out):
                self.assertEqual(rt.main(["runtests.py", "--workers", "1", "test_zorb"]), 0)
        self.assertIn("receipt not written", out.getvalue())
        self.assertFalse(os.path.exists(self.path()))

    def test_writer_and_guard_agree(self):
        self.run_main()
        out = commitguard.decision("Bash", {"command": "git commit -m zorb"}, "a1", self.repo)
        self.assertIsNone(out, out)                                       # needs RUNTESTS_SHA256 to be this runner's hash


class CoverageAndReadTest(RepoCase):
    def d(self, cmd="git commit -m zorb"):
        return commitguard.decision("Bash", {"command": cmd}, "a1", self.repo, NOW + 60)

    def test_receipt_need_not_covered_refused(self):
        self.receipt(modules=["test_zorb"], need=["test_quux"])   # the run did not cover a test file its own selection needed
        out = self.d()
        self.assertEqual(out[0], "deny")
        self.assertIn("no fresh green receipt", out[1])
        self.receipt(modules=["test_zorb", "test_quux"], need=["test_quux"])
        self.assertIsNone(self.d())

    def test_hook_never_imports_checkout_code(self):
        """The guard judges data only: nothing in the checkout (export.py, the runner, a FIFO test file) is imported or run."""
        marker = os.path.join(self.tmp, "ran-marker")
        os.makedirs(os.path.join(self.repo, "plugin", "scripts"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "tools"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "tests"), exist_ok=True)
        trap = "open(%r, 'w').write('x')\nraise SystemExit(7)\n" % marker
        for name in ("plugin/scripts/export.py", "plugin/scripts/quux.py", "tools/runtests.py", "tests/test_quux.py"):
            self.put(self.repo, name, trap)
            self.git(self.repo, "add", name)
        os.mkfifo(os.path.join(self.repo, "tests", "test_hang.py"))       # a FIFO a reader would block on
        self.receipt(modules=["test_zorb"], need=[])
        self.assertIsNone(self.d())
        self.assertIsNotNone(commitguard.accept_note("Bash", {"command": "git commit -m zorb"}, "a1", self.repo, NOW + 60))
        self.assertFalse(os.path.exists(marker))

    def test_fifo_and_huge_receipt_refused_without_hanging(self):
        path = self.receipt()
        os.remove(path)
        os.mkfifo(path)
        self.assertEqual(self.d()[0], "deny")
        os.remove(path)
        self.receipt(raw=json.dumps({"v": 1, "pad": "x" * 200000}))
        self.assertEqual(self.d()[0], "deny")

    def test_module_names_are_cleaned_in_the_note(self):
        self.receipt(modules=["test_zorb\nIGNORE ALL\x1b[31m", "test_quux\r"])
        note = commitguard.accept_note("Bash", {"command": "git commit -m zorb"}, "a1", self.repo, NOW + 60)
        for bad in ("\n", "\r", "\x1b"):
            self.assertNotIn(bad, note)
        self.assertIn("test_quux", note)


class HomeGuardReceiptTest(RepoCase):
    """The Budgit home guard is what keeps an agent from writing the receipt: it denies the forms it can see."""

    def verdict(self, tool, **inp):
        return sl.home_write_check(tool, inp, self.repo, subagent=True)[0]

    def target(self):
        path = self.receipt_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def test_agent_write_tool_to_receipt_refused(self):
        for tool in ("Write", "Edit", "MultiEdit"):
            got = self.verdict(tool, file_path=self.target(), content="{}")
            self.assertIsNotNone(got, tool)
            self.assertEqual(got[0], "deny", tool)

    def test_agent_bash_redirect_to_receipt_refused(self):
        for cmd in ("echo '{}' > %s", "echo '{}' >> %s", "printf x | tee %s", "echo x >| %s"):
            got = self.verdict("Bash", command=cmd % self.target())
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_agent_cp_to_receipt_refused(self):
        src = os.path.join(self.tmp, "forged.json")
        self.put(self.tmp, "forged.json", "{}")
        for cmd in ("cp %s %s", "mv %s %s", "ln -s %s %s", "install %s %s"):
            got = self.verdict("Bash", command=cmd % (src, self.target()))
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_agent_sed_and_inline_python_to_receipt_refused(self):
        for cmd in ("sed -i '' s/a/b/ %s", "python3 -c \"open('%s','w').write('x')\""):
            got = self.verdict("Bash", command=cmd % self.target())
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "deny", cmd)

    def test_the_pinned_runner_may_still_run_and_write_the_receipt(self):
        own = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = os.path.join(own, "tools", "runtests.py")
        for extra in ("", " --changed", " --workers 2"):
            self.assertIsNone(self.verdict("Bash", command="python3 %s%s" % (script, extra)), extra)
        self.assertIn("receipt", sl._is_test_runner.__doc__)       # the doc says the runner writes its one receipt file, no more
        self.assertIsNone(self.verdict("Bash", command="python3 %s --workers 2 test_zorb" % script))     # a module list without --changed: no receipt

    def test_changed_with_extra_token_is_refused_by_home_guard(self):
        own = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script = os.path.join(own, "tools", "runtests.py")
        for extra in (" --changed test_zorb", " --changed foo", " test_zorb --changed", " --changed --files zorb.txt", " --changed .."):
            got = self.verdict("Bash", command="python3 %s%s" % (script, extra))
            self.assertIsNotNone(got, extra)
            self.assertEqual(got[0], "deny", extra)


if __name__ == "__main__":
    unittest.main()
