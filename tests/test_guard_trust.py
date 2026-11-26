"""R-335: the real-data guard stops asking about commands that only say WHERE they run (git -C <root>, make -C, npm
--prefix, cd <root> &&) and judges git by its real targets (the pathspec); and `budgit trust work` is a remembered
per-project yes for commands that only name the project as a whole. Every real target still asks."""
import json
import os
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT

import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, "-c", "user.email=t@users.noreply.github.com", "-c", "user.name=t"]
                          + list(args), capture_output=True, text=True, check=True).stdout


class GuardCase(StudioTestCase):
    """A temp project that is a git repo with protected data.json and data/real/**, plus plain docs."""

    def setUp(self):
        super().setUp()
        fake_home = os.path.join(self.tmp, "fake-user-home")        # hermetic: no real ~/.gitconfig, ~/.budgit, ~/.claude
        os.makedirs(fake_home, exist_ok=True)
        old = os.environ.get("HOME")
        os.environ["HOME"] = fake_home
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old is not None else os.environ.pop("HOME", None))
        # R-1021: the verdict must not depend on the live session (a terminal shell sets PAGER, which makes the guard
        # ask for plain git reads); clear every variable `_git_env_clean` reads, restore after.
        for name in [k for k in os.environ if k in sl._GIT_ENV_BAD or k.startswith(("GIT_CONFIG", "GIT_TRACE"))]:
            self.addCleanup(os.environ.__setitem__, name, os.environ.pop(name))
        sl.activate(self.project, bind=False)
        pj =os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = ["data.json", "data/real/**", "Caf\u00e9/**"]
        json.dump(cfg, open(pj, "w"))
        for rel in ("data.json", "data/real/x.csv", "Caf\u00e9/x.csv", "docs/a.md", "docs/b.md", "src/app.py"):
            path = os.path.join(self.project, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").write("one\n")
        git(self.project, "init", "-q")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-q", "-m", "init")
        self.root = os.path.realpath(self.project)

    def transcript(self, text="/budgit-trust work"):
        """A fake session transcript under a temp CLAUDE_CONFIG_DIR whose last user message is `text`."""
        cfg = os.path.join(self.tmp, "claude-cfg")
        if os.environ.get("CLAUDE_CONFIG_DIR") != cfg:
            os.environ["CLAUDE_CONFIG_DIR"] = cfg
            self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        path = os.path.join(cfg, "projects", "-p", "s.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = [{"type": "user", "message": {"role": "user", "content": "hello"}},
                {"type": "assistant", "message": {"role": "assistant", "content": "hi"}},
                {"type": "user", "message": {"role": "user", "content": text}}]
        open(path, "w").write("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def grant(self, text="/budgit-trust work"):
        return sl.grant_trust(self.project, self.transcript(text))

    def d(self, cmd, cwd=None):
        return sl.real_data_decision("Bash", {"command": cmd}, self.project, cwd=cwd or self.project)

    def edit(self, rel, text="two\n"):
        open(os.path.join(self.project, rel), "w").write(text)

    def asks(self, *cmds, cwd=None):
        for cmd in cmds:
            got = self.d(cmd, cwd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)

    def passes(self, *cmds, cwd=None):
        for cmd in cmds:
            self.assertIsNone(self.d(cmd, cwd), cmd)

    def needs_flag(self, *cmds, cwd=None):
        """The flagless twin: a work-tree status or diff declines with the submodule rule."""
        import i18n
        for cmd in cmds:
            got = self.d(cmd, cwd)
            self.assertIsNotNone(got, cmd)
            self.assertEqual(got[0], "ask", cmd)
            self.assertTrue(got[1].startswith(i18n.t("guard.r1294_needs_ignore_submodules")), "%s -> %r" % (cmd, got[1]))


class FalseAskGoneTest(GuardCase):
    def test_the_reported_command_no_longer_asks(self):
        # before: this command was answered with an ask about protected real data in the root
        self.passes("git -C %s commit -m x -- docs/a.md docs/b.md" % self.root)

    def test_git_dash_c_with_pathspec_outside_protected(self):
        self.passes("git -C %s add docs/a.md" % self.root,
                    "git -C %s commit -F /tmp/msg.txt -- docs/a.md" % self.root,
                    "git -C %s status --ignore-submodules=all" % self.root,
                    "git -C %s log --oneline" % self.root)
        self.needs_flag("git -C %s status" % self.root)
        # round 3: only add and commit are judged by their pathspec; every other writing verb keeps the base verdict
        # R-493: git rm/mv/restore of an unprotected file writes no protected data; a whole-tree checkout still asks
        self.passes("git -C %s rm --cached docs/a.md" % self.root, "git -C %s mv docs/a.md docs/c.md" % self.root,
                    "git -C %s restore docs/a.md" % self.root)
        self.asks("git -C %s checkout -- docs/a.md" % self.root, "git -C %s restore data.json" % self.root)

    def test_git_dir_and_work_tree_options(self):
        # round 3: a location the guard cannot verify is not a free pass (the base asked, it still asks)
        self.asks("git --git-dir %s/.git --work-tree %s commit -m x -- docs/a.md" % (self.root, self.root))
        self.passes("git -C %s tag -l" % self.root)         # R-493: a tag list reads .git, not the working files

    def test_other_tools_with_a_location_option(self):
        # R-418: a runner pointed at the project folder is the ordinary place for it to work: it passes; a folder above asks
        for cmd in ("make -C %s test" % self.root, "npm --prefix %s run build" % self.root):
            self.assertIsNone(self.d(cmd), cmd)
        # the `--option=<root>` spelling still asks (the option-given target scan), and so does a folder above
        self.asks("make --directory=%s test" % self.root, "npm --prefix=%s test" % self.root,
                  "make -C %s/.. test" % self.root, "npm --prefix=%s/.. test" % self.root)

    def test_cd_into_the_root_then_work_on_other_files(self):
        # round 3: `cd <root>` is no longer neutral (tar/unzip/cp then write into the cwd); the base verdict stands
        # R-493: naming the root only as a cwd is not a write; the pathspec decides (cd <root> && git add data.json still asks)
        self.passes("cd %s && git commit -m x -- docs/a.md" % self.root,
                    "cd %s && git add docs/a.md && git commit -m x -- docs/a.md" % self.root,
                    "cd %s && ls" % self.root, cwd=os.path.join(self.root, "src"))
        self.asks("cd %s && git add data.json" % self.root, "cd %s && rm data.json" % self.root)

    def test_relative_paths_resolve_against_the_chosen_dir(self):
        self.passes("git -C %s commit -m x -- a.md" % os.path.join(self.root, "docs"), cwd=self.tmp)
        self.passes("cd docs && git commit -m x -- a.md")

    def test_a_commit_message_that_mentions_a_protected_name_is_not_a_target(self):
        self.passes("git -C %s commit -m 'rewrite data.json notes' -- docs/a.md" % self.root)

    def test_plain_commit_with_nothing_protected_staged(self):
        self.edit("docs/a.md")
        git(self.project, "add", "docs/a.md")
        self.passes("git -C %s commit -m x" % self.root, "git commit -m x")

    def test_commit_a_with_only_unprotected_changes(self):
        self.edit("docs/a.md")
        # `-a` lists the TRACKED files from the index (no filter can run), so a tracked protected file makes it ask
        self.asks("git -C %s commit -am x" % self.root, "git -C %s commit -a -m x" % self.root)

    def test_plain_commit_with_nothing_staged(self):
        self.passes("git -C %s commit -m x" % self.root)


class RealTargetsStillAskTest(GuardCase):
    def test_pathspec_that_is_protected_or_an_ancestor(self):
        r = self.root
        self.asks("git -C %s commit -m x -- data.json" % r,
                  "git -C %s commit -m x -- docs/a.md data.json" % r,
                  "git -C %s commit -m x -- data/real/x.csv" % r,
                  "git -C %s commit -m x -- data" % r,
                  "git -C %s commit -m x -- ." % r,
                  "git -C %s commit -m x -- %s" % (r, r),
                  "git -C %s add data.json" % r,
                  "git -C %s add 'data/*'" % r,
                  "git -C %s add ." % r,
                  "git -C %s rm data.json" % r,
                  "git -C %s mv data.json old.json" % r,
                  "git -C %s checkout -- data.json" % r,
                  "git -C %s checkout ." % r,
                  "git -C %s restore data.json" % r,
                  "git -C %s restore --staged ." % r,
                  "cd %s && git add data.json" % r,
                  "git --git-dir=%s/.git --work-tree=%s add data.json" % (r, r))

    def test_wide_add(self):
        self.asks("git -C %s add -A" % self.root, "git -C %s add --all" % self.root, "git add -A", "git add .")

    def test_commit_with_a_protected_file_staged(self):
        self.edit("data.json")
        git(self.project, "add", "data.json")
        self.asks("git -C %s commit -m x" % self.root, "git commit -m x")

    def test_commit_a_with_a_tracked_protected_file_modified(self):
        self.edit("data/real/x.csv")
        self.asks("git -C %s commit -am x" % self.root, "git commit -a -m x")

    def test_commit_a_where_git_cannot_answer_fails_closed(self):
        self.asks("git -C %s commit -am x" % os.path.join(self.tmp, "nowhere"), cwd=self.root)  # not a repo here

    def test_pathspec_the_guard_cannot_read_fails_closed(self):
        r = self.root
        self.asks("git -C %s commit -m x --pathspec-from-file=list.txt" % r,
                  "git -C %s add $(cat list.txt)" % r,
                  "git -C %s add ':(exclude)docs'" % r)

    def test_project_wide_family(self):
        r = self.root
        self.asks("git clean -fd", "git -C %s clean -fd" % r, "git stash -u", "git -C %s stash --include-untracked" % r,
                  "find %s -delete" % r, "find . -name '*.tmp' -exec rm {} +", "cd %s && git clean -fdx" % r)

    def test_writes_into_protected_paths(self):
        r = self.root
        self.asks("rm data.json", "echo {} > data.json", "echo x >> %s/data/real/x.csv" % r, "sed -i '' s/a/b/ data.json",
                  "python3 -c \"open('data.json','w')\"", "cp /tmp/x data.json", "truncate -s0 data/real/x.csv",
                  "cat x > %s" % r, "cd data/real && python3 wipe.py", "make -C data/real clean")
        for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("MultiEdit", "file_path")):
            got = sl.real_data_decision(tool, {key: os.path.join(r, "data.json")}, self.project)
            self.assertEqual(got[0], "ask", tool)

    def test_naming_the_root_itself_in_a_destructive_command(self):
        r = self.root
        self.asks("rm -rf %s" % r, "mv %s /tmp/gone" % r, "rsync -a --delete /tmp/src/ %s/" % r, "rm -r .")

    def test_unknown_commands_with_the_root_as_an_argument_ask_without_a_grant(self):
        # R-493: naming the root as a program's argument, or archiving it, writes nothing; handing it the real file asks
        self.passes("tar czf /tmp/x.tgz %s" % self.root)
        self.asks("python3 job.py %s" % self.root, "python3 job.py %s/data.json" % self.root, "some-new-tool %s/data/real/x.csv" % self.root)

    def test_reads_still_pass(self):
        self.passes("cat data.json", "grep x data.json", "ls data/real", "cp data.json /tmp/c.json")


class RoundTwoRegressionsTest(GuardCase):
    """The verifier's FAIL (D2/D3) and the review's gaps (G): each of these must ask."""

    def test_shell_syntax_around_a_protected_pathspec(self):
        self.asks("(git restore data.json)", "git restore data.json>/dev/null", "git restore data.json&",
                  "git restore data.json & echo done", "git add docs/a.md >data.json", "git add docs/a.md > data.json",
                  "{ git restore data.json; }", "git add docs/a.md 2>data/real/x.csv")

    def test_git_location_from_the_environment_or_config_from_outside(self):
        r, out = self.root, self.tmp
        self.asks("GIT_WORK_TREE=%s GIT_DIR=%s/.git git add data.json" % (r, r),
                  "env GIT_WORK_TREE=%s GIT_DIR=%s/.git git restore data.json" % (r, r),
                  "git -c core.worktree=%s add data.json" % r,
                  "git -c core.worktree=$W add data.json",
                  "GIT_WORK_TREE=$W git add data.json", cwd=out)

    def test_environment_location_makes_the_index_check_unsure(self):
        self.passes("GIT_DIR=%s/.git git add docs/a.md" % self.root, cwd=self.tmp)
        self.asks("GIT_DIR=%s/.git GIT_WORK_TREE=%s git commit -m x" % (self.root, self.root), cwd=self.tmp)

    def test_commit_include_with_a_protected_file_staged(self):
        self.edit("data.json")
        git(self.project, "add", "data.json")
        self.asks("git -C %s commit -i -m x -- docs/a.md" % self.root, "git commit --include -m x -- docs/a.md")
        self.passes("git -C %s commit -m x -- docs/a.md" % self.root)       # --only: the index is not committed

    def test_the_protected_compare_ignores_case(self):
        self.asks("git -C %s add DATA.JSON" % self.root, "rm DATA.json", "rm %s" % self.root.upper() + "/data.json")

    def test_a_dropped_dash_c_keeps_the_cwd_base(self):
        self.asks("git -C %s add data.json" % os.path.join(self.root, "docs"))      # cwd = the root, as git is told otherwise

    def test_the_staged_files_are_listed_from_the_dash_c_work_tree(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        git(other, "init", "-q")
        open(os.path.join(other, "data.json"), "w").close()
        git(other, "add", "data.json")
        self.passes("git -C %s commit -m x" % other)                 # the staged data.json is not this project's
        self.edit("data.json")
        git(self.project, "add", "data.json")
        self.asks("git -C %s commit -m x" % self.root, cwd=self.tmp)  # nor is the cwd the place to look

    def test_a_git_dir_outside_the_project_is_not_forwarded_and_the_guard_asks(self):
        self.asks("git --git-dir=%s/.git --work-tree=%s commit -m x" % (os.path.join(self.tmp, "other"), self.root))

    def test_git_listing_is_hardened(self):
        calls = []
        real = sl._subprocess.run

        def spy(argv, *a, **kw):
            calls.append((argv, kw.get("env")))
            return real(argv, *a, **kw)

        os.environ["GIT_DIR"] = os.path.join(self.tmp, "elsewhere")
        sl._subprocess.run = spy
        try:
            self.d("git commit -m x")
        finally:
            sl._subprocess.run = real
            del os.environ["GIT_DIR"]
        self.assertTrue(calls)
        for argv, env in calls:
            self.assertEqual(argv[:6], ["git", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
                                        "--no-optional-locks"])
            self.assertFalse([k for k in env if k.startswith("GIT_")])

    def test_find_delete_or_exec_over_the_root_from_any_cwd(self):
        r = self.root
        self.asks("find %s -name '*.tmp' -delete" % r, "find %s -exec rm {} +" % r, "find %s/docs -delete" % r,
                  "find / -name x -delete", cwd=self.tmp)
        self.passes("find %s -name '*.py'" % r, "find /usr/share -name x -delete", cwd=self.tmp)

    def test_shell_dash_c_with_a_literal_string_is_judged_inside(self):
        self.asks("bash -c 'rm data.json'", "sh -c \"git restore data.json\"", "zsh -lc 'echo x > data.json'",
                  "bash -c 'cd docs; git add ../data.json'", "bash -c \"bash -c 'rm data.json'\"")
        self.asks("bash -c 'cd docs; git add data.json'")
        self.passes("bash -c 'git commit -m x -- docs/a.md'", "sh -c 'ls docs'")

    def test_rsync_remove_source_files_is_destructive(self):
        self.asks("rsync -a --remove-source-files %s/ /tmp/out/" % self.root, "rsync -a --delete-after %s/ /tmp/o/" % self.root)
        self.passes("rsync -a %s/ /tmp/out/" % self.root)


class ReadOnlyGitNeverAsksTest(GuardCase):
    """R-342: read-only git never asks, with -C <root>, chained with ; && || or piped; a writing option still does."""

    def setUp(self):
        super().setUp()
        git(self.project, "branch", "-q", "feat")

    def test_the_reported_chain_returns_none(self):
        r = self.root
        self.passes("git -C %s log --oneline -3 main; git -C %s merge-base --is-ancestor main feat && echo FF-OK || echo DIVERGED; "
                    "git -C %s log --oneline main..feat | wc -l; git -C %s log --oneline feat..main" % (r, r, r, r))

    def test_each_read_only_subcommand_with_dash_c_the_root(self):
        r = self.root
        for bare in ("diff", "diff --stat HEAD", "status", "status --short"):
            self.needs_flag("git -C %s %s" % (r, bare))
        for sub in ("log --oneline -3", "show HEAD", "show --stat HEAD", "diff --ignore-submodules=all",
                    "diff --stat HEAD --ignore-submodules=all", "diff --cached", "status --ignore-submodules=all",
                    "status --short --ignore-submodules=all", "merge-base main feat", "merge-base --is-ancestor main feat", "rev-parse HEAD",
                    "rev-parse --show-toplevel", "rev-list --count HEAD", "branch --list", "branch -a", "branch -v",
                    "branch -vv", "ls-files", "ls-files -m", "cat-file -p HEAD", "cat-file -t HEAD", "blame docs/a.md",
                    "describe --always", "shortlog -sn HEAD"):
            self.passes("git -C %s %s" % (r, sub))
            self.passes("git -C %s %s | wc -l" % (r, sub), "git -C %s %s | head -5" % (r, sub),
                        "git -C %s %s | tail -2" % (r, sub), "git -C %s %s | grep x" % (r, sub),
                        "git -C %s %s; echo done" % (r, sub), "git -C %s %s && echo ok || echo no" % (r, sub))

    def test_without_dash_c_and_from_another_cwd(self):
        self.passes("git log --oneline -3; git status --ignore-submodules=all | head -3",
                    "git diff --stat --ignore-submodules=all | wc -l")
        self.needs_flag("git log --oneline -3; git status | head -3", "git diff --stat | wc -l")
        self.passes("git -C %s log --oneline" % self.root, cwd=self.tmp)

    def test_a_pathspec_in_a_read_names_data_without_asking(self):
        self.passes("git -C %s log --oneline -- data.json" % self.root,
                    "git -C %s diff --ignore-submodules=all -- data.json" % self.root,
                    "git -C %s show HEAD:data.json | head" % self.root, "git -C %s blame data.json" % self.root)
        self.needs_flag("git -C %s diff -- data.json" % self.root)

    def test_a_writing_option_or_redirect_still_asks(self):
        r = self.root
        self.passes("git -C %s diff --ignore-submodules=all --output=%s/out.patch" % (r, r))        # R-493: the output file is not protected data
        self.needs_flag("git -C %s diff --output=%s/out.patch" % (r, r))
        self.asks("git -C %s log --output=data.json" % r,
                  "git -C %s diff -o data.json" % r, "git -C %s log --oneline > data.json" % r,
                  "git -C %s status | tee data/real/x.csv" % r, "git -C %s diff >> %s/data/real/x.csv" % (r, r))
        self.asks("git -C %s log --oneline > %s" % (r, r))
        self.asks("git -C %s status; rm data.json" % r, "git -C %s status && git add data.json" % r)


class BudgitBookkeepingNeverAsksTest(GuardCase):
    """R-355: the real budgit running a subcommand that writes only inside <project>/.budgit never asks about real data,
    with or without the standing yes. Writers of real files, unknown subcommands, look-alikes and chained segments do."""

    def setUp(self):
        super().setUp()
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.path.dirname(CLI) + os.pathsep + self._path
        self.addCleanup(os.environ.__setitem__, "PATH", self._path)

    def both(self, check):
        check()
        self.grant()
        check()
        sl.revoke_trust(self.project)

    def test_her_exact_command(self):
        r = self.root
        self.both(lambda: self.passes('budgit --project %s ledger ack I-22 --note "x"' % r,
                                      '%s --project %s ledger ack I-22 --note "x"' % (CLI, r)))

    def test_each_bookkeeping_subcommand(self):
        r = self.root
        subs = ("ledger add --text hi", "ledger link R-1 --src I-1", "ledger set R-1 doing", "ledger start R-1",
                "ledger phase mvp R-1", "ledger done R-1 --evidence docs/a.md", "ledger ack I-1 I-2 --note x",
                "ledger verify R-1", "ledger drop R-1 --by-user x", "ledger list --open", "ledger accept-edit",
                "ledger repair-done-at", "status", "status --line", "status --brote --live", "budget", "denies --since 7d")
        def check():
            for sub in subs:
                self.passes("budgit --project %s %s" % (r, sub), "budgit %s --project %s" % (sub, r),
                            "budgit --project=%s %s" % (r, sub), "budgit %s --project=%s" % (sub, r))
        self.both(check)

    def test_project_flag_between_subcommand_and_op_and_from_inside_the_root(self):
        self.passes("budgit ledger --project %s ack I-1 --note x" % self.root, "budgit ledger list", cwd=self.root)

    def test_writers_of_real_files_and_unknown_subcommands_still_ask(self):
        r = self.root
        def check():
            self.asks(*("budgit --project %s %s" % (r, sub) for sub in (
                "migrate", "prepare", "export --out %s" % r, "export", "layout apply", "import-md", "new x", "activate",
                "lint --fix", "scout", "frobnicate", "ledger", "ledger frobnicate", "ledger --note x ack I-1", "")))
        self.both(check)

    def test_a_project_that_is_protected_data_asks(self):
        self.asks("budgit --project %s/data/real ledger list" % self.root, "budgit --project %s/data/real status" % self.root,
                  "budgit --project %s --project %s/data/real status" % (self.root, self.root))
        self.asks("budgit ledger list", cwd=os.path.join(self.root, "data", "real"))

    def test_a_look_alike_or_a_symlink_to_something_else_asks(self):
        r = self.root
        fake_dir = os.path.join(self.tmp, "fake")
        os.makedirs(fake_dir)
        fake = os.path.join(fake_dir, "budgit")
        open(fake, "w").write("#!/bin/sh\nrm -rf data\n")
        os.chmod(fake, 0o755)
        link = os.path.join(fake_dir, "linked")
        os.makedirs(link)
        os.symlink(fake, os.path.join(link, "budgit"))
        mine = os.path.join(self.tmp, "mine")
        os.makedirs(mine)
        os.symlink(CLI, os.path.join(mine, "budgit"))
        self.asks("%s --project %s ledger ack I-1 --note x" % (fake, r), "%s --project %s ledger ack I-1 --note x" % (os.path.join(link, "budgit"), r))
        self.passes("%s --project %s ledger ack I-1 --note x" % (os.path.join(mine, "budgit"), r))     # R-1135 (a), I-2041: a link TO the real one passes
        os.environ["PATH"] = fake_dir + os.pathsep + os.environ["PATH"]
        self.asks("budgit --project %s ledger ack I-1 --note x" % r)                                   # PATH finds the look-alike first
        self.asks("cd %s && ./budgit --project %s status" % (fake_dir, r))

    def test_a_leading_assignment_or_a_substitution_asks(self):
        r = self.root
        self.asks("PYTHONPATH=/tmp/x budgit --project %s status" % r, "budgit --project %s ledger ack I-1 --note \"$(rm -rf data)\"" % r,
                  "budgit --project %s ledger ack I-1 --note `rm -rf data`" % r, "budgit --project %s status > data.json" % r)

    def test_chained_segments_are_judged_on_their_own(self):
        r = self.root
        ack = 'budgit --project %s ledger ack I-1 --note "x"' % r
        def check():
            self.passes(ack)
            self.asks(ack + " && " + ack, ack + " | head -3", ack + "; budgit --project %s ledger list" % r,   # sole command only
                      ack + "; rm -rf %s/data" % r, ack + " && rm data.json", "rm -rf %s/data; " % r + ack,
                      ack + " | tee data.json", ack + " ; budgit --project %s migrate" % r, ack + " > data.json")
        self.both(check)


class TrustGrantTest(GuardCase):
    def test_grant_on_off_and_per_project(self):
        self.assertFalse(sl.trust_work_on(self.project))
        self.grant()
        self.assertTrue(sl.trust_work_on(self.project))
        self.assertTrue(sl.trust_work_on(self.project + os.sep))            # same project, other spelling
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self.assertFalse(sl.trust_work_on(other))
        self.assertTrue(sl.revoke_trust(self.project))
        self.assertFalse(sl.trust_work_on(self.project))
        self.assertFalse(sl.revoke_trust(self.project))                     # nothing left to revoke

    def test_trust_granted_is_the_public_helper_for_other_guards(self):
        self.assertIs(sl.trust_granted(self.project), False)
        self.grant()
        self.assertIs(sl.trust_granted(self.project), True)
        self.assertIs(sl.trust_granted(os.path.join(self.tmp, "nobody")), False)
        sl.revoke_trust(self.project)
        self.assertIs(sl.trust_granted(self.project), False)

    def test_the_texts_say_the_grant_covers_scripts_and_state_the_limit(self):
        import i18n
        for lang, words, limit in (("es", ("programas que escriba la sesión", "datos reales sin preguntar"),
                                    "un programa que corra como tú siempre puede editar tus archivos"),
                                   ("en", ("programs the session writes", "real data without asking"),
                                    "a program running as you can always edit your files")):
            for key in ("trust.offer", "trust.on"):
                text = i18n.t(key, lang=lang, project="p")
                for w in words:
                    self.assertIn(w, text, (lang, key))
            for key in ("trust.on", "trust.off", "guard.home_write"):
                self.assertIn(limit, i18n.t(key, lang=lang, project="p", home="h"), (lang, key))

    def test_the_grant_is_stored_in_the_user_data_dir_never_in_the_project(self):
        self.grant()
        path = sl.trust_path()
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.path.dirname(path), sl.home_state())
        self.assertFalse(os.path.realpath(path).startswith(self.root + os.sep))
        self.assertIn(self.root, open(path).read())                         # keyed by the project's realpath

    def test_a_broken_grant_file_means_no_grant(self):
        os.makedirs(self.home, exist_ok=True)
        open(sl.trust_path(), "w").write("{nope")
        self.assertFalse(sl.trust_work_on(self.project))
        open(sl.trust_path(), "w").write('["work"]')
        self.assertFalse(sl.trust_work_on(self.project))

    def test_with_the_grant_readers_of_the_whole_project_pass(self):
        r = self.root
        self.grant()
        self.passes("du -sh %s" % r, "tree %s" % r, "git diff --stat --ignore-submodules=all %s" % r,
                    "git -C %s commit -m x -- docs/a.md" % r)
        self.needs_flag("git diff --stat %s" % r)           # the grant does not waive the flag

    def test_without_the_grant_those_readers_pass_too(self):
        self.passes("du -sh %s" % self.root, "tree %s" % self.root)      # R-493: a reader never needed the standing yes

    def test_with_the_grant_anything_that_writes_onto_the_root_still_asks(self):
        r = self.root
        self.grant()
        self.passes("tar czf /tmp/x.tgz %s" % r)             # R-493: an archive of the root only reads it
        self.asks("python3 job.py %s" % r, "some-new-tool --in %s" % r)      # a program whose writes cannot be read still asks
        self.passes("cp /tmp/x %s" % r)           # R-493: a new unprotected name dropped into the root overwrites no protected data
        self.asks("cp /tmp/data.json %s" % r, "rsync -a /tmp/src/ %s/" % r, "tar xf /tmp/x.tgz -C %s" % r,
                  "unzip /tmp/x.zip -d %s" % r, "ditto /tmp/src %s" % r, "install -d %s" % r, "ln -s /tmp/x %s" % r,
                  "touch %s" % r, "dd if=/dev/zero of=%s" % r, "truncate -s0 %s" % r, "mv /tmp/x %s" % r,
                  "git diff --output=%s %s" % (r, r))

    def _drop(self, *names):
        """A folder outside the project that holds these files (R-558)."""
        d = os.path.join(self.tmp, "dropsrc%d" % len(os.listdir(self.tmp)))
        for n in names:
            os.makedirs(os.path.dirname(os.path.join(d, n)), exist_ok=True)
            open(os.path.join(d, n), "w").write("x\n")
        return d

    def test_a_copy_with_a_trailing_slash_source_is_judged_by_the_files_inside(self):
        r = self.root
        d = self._drop("data.json", "notes.txt")
        cmds = ["cp -R %s/ %s" % (d, r), "cp -R %s/. %s" % (d, r), "cp -a %s/ %s" % (d, r), "cp -a %s/. %s/" % (d, r),
                "cp -r %s/ %s/" % (d, r), "rsync -a %s/ %s" % (d, r), "ditto %s %s" % (d, r), "ditto %s/ %s" % (d, r)]
        self.asks(*cmds)                                   # without the grant
        self.grant()
        self.asks(*cmds)                                   # with it

    def test_a_trailing_slash_source_holding_a_protected_folder_asks(self):
        d = self._drop("data/real/y.csv")
        self.grant()
        self.asks("cp -R %s/ %s" % (d, self.root))

    def test_a_trailing_slash_source_that_cannot_be_listed_asks(self):
        self.grant()
        self.asks("cp -R %s/nowhere/ %s" % (self.tmp, self.root), "cp -R %s/*/ %s" % (self.tmp, self.root),
                  "cp -R $SRC/ %s" % self.root)

    def test_a_trailing_slash_source_of_new_unprotected_names_still_passes(self):
        d = self._drop("notes.txt", "sub/more.txt")
        self.grant()
        self.passes("cp -R %s/ %s" % (d, self.root), "cp -R %s/. %s" % (d, self.root))
        self.passes("cp /tmp/x %s" % self.root)           # R-493 stays

    # ---- R-558 round 2 (security review: HOLD) ----

    def _named(self, name, *files):
        """A folder called `name` (outside the project) holding these files."""
        top = os.path.join(self.tmp, "named%d" % len(os.listdir(self.tmp)))
        d = os.path.join(top, name)
        for n in files:
            os.makedirs(os.path.dirname(os.path.join(d, n)), exist_ok=True)
            open(os.path.join(d, n), "w").write("x\n")
        return d

    def test_a_trailing_slash_source_is_also_judged_by_its_own_name(self):
        # GNU cp copies `X/` to <target>/X, so a folder called like protected data asks even when its files look harmless
        self.grant()
        for name in ("data", "data.json", "Café"):
            d = self._named(name, "notes.txt")
            self.asks("cp -R %s/ %s" % (d, self.root), "cp -a %s/ %s/" % (d, self.root), "cp -r %s/ %s" % (d, self.root))
        self.passes("cp -R %s/. %s" % (self._named("data", "notes.txt"), self.root))      # `/.` is the contents only

    def test_a_contents_copy_asks_when_an_earlier_part_of_the_command_can_write(self):
        self.grant()
        stage = self._drop("notes.txt")
        r = self.root
        # round 3: the listing may only ADD an ask, so a contents copy passes only as the one plain command; any chain asks
        self.passes("cp -R %s/ %s" % (stage, r))
        self.asks("ls %s && cp -R %s/ %s" % (stage, stage, r),
                  "echo hi; cp -R %s/ %s" % (stage, r), "cat %s/notes.txt | wc -l; cp -R %s/ %s" % (stage, stage, r))
        self.asks("cp x %s/data.json && cp -R %s/ %s" % (stage, stage, r),
                  "echo x > %s/data.json && cp -R %s/ %s" % (stage, stage, r),
                  "mkdir -p %s/s && cp -R %s/ %s" % (stage, stage, r),
                  "touch %s/data.json; cp -a %s/. %s" % (stage, stage, r),
                  "python3 fill.py && cp -R %s/ %s" % (stage, r),
                  "cp -R %s/ %s && cp -R %s/ %s" % (stage, r, stage, r),
                  "tar xf a.tar -C %s && ditto %s %s" % (stage, stage, r),
                  "git clone x %s/g && rsync -a %s/ %s" % (stage, stage, r))

    def test_a_symlinked_folder_inside_a_dereferenced_source_asks(self):
        self.grant()
        d = self._drop("notes.txt")
        target = self._drop("real/x.csv")
        os.symlink(target, os.path.join(d, "data"))
        r = self.root
        self.asks("cp -RL %s/ %s" % (d, r), "cp -R -L %s/ %s" % (d, r), "cp -rL %s/. %s" % (d, r),
                  "cp --dereference -R %s/ %s" % (d, r), "cp -RH %s/ %s" % (d, r), "cp -LR %s/ %s" % (d, r),
                  "cp --deref -R %s/ %s" % (d, r))
        self.passes("cp -R %s/ %s" % (d, r))                # not followed without -L / -H: the link is copied as a link

    def test_no_target_directory_is_a_contents_copy(self):
        self.grant()
        r = self.root
        bad = self._drop("data.json")
        self.asks("cp -RT %s %s" % (bad, r), "cp -R -T %s %s" % (bad, r), "cp -RT %s/ %s" % (bad, r),
                  "cp --no-target-directory -R %s %s" % (bad, r), "cp -R --no-t %s %s" % (bad, r),
                  "cp -aT %s %s" % (bad, r))
        self.asks("cp -RT %s %s" % (self._drop("notes.txt"), r))      # round 4: -T is not in the allow-list, so it asks

    def test_target_directory_combined_with_other_short_flags(self):
        self.grant()
        r = self.root
        named = self._named("data", "notes.txt")
        bad = self._drop("data.json")
        self.asks("cp -Rt %s %s" % (r, named), "cp -aRt %s %s" % (r, named), "cp -Rt%s %s" % (r, named),
                  "cp -Rt %s %s/" % (r, bad), "cp -t%s -R %s/" % (r, bad), "cp --target=%s -R %s/" % (r, bad),
                  "cp --target-directory=%s -R %s" % (r, named), "cp -R --t %s %s/" % (r, bad))
        self.asks("cp -Rt %s %s" % (r, self._drop("notes.txt")))     # a target folder that IS the project always asked

    def test_abbreviated_long_options_count_as_recursive(self):
        self.grant()
        r = self.root
        bad = self._drop("data.json")
        named = self._named("data", "notes.txt")
        self.asks("cp --rec %s/ %s" % (bad, r), "cp --recur %s/ %s" % (bad, r), "cp --arc %s/ %s" % (bad, r),
                  "cp --ar %s/ %s" % (bad, r), "cp --rec %s %s" % (named, r), "cp --arch %s %s" % (named, r))

    def test_a_copy_into_a_folder_above_the_project_is_judged(self):
        # R-562: <parent> holds the project, so a folder named like the project lands ON the project
        self.grant()
        parent, name = os.path.dirname(self.root), os.path.basename(self.root)
        bak = os.path.join(self.tmp, "bak")
        os.makedirs(os.path.join(bak, name))
        open(os.path.join(bak, name, "data.json"), "w").write("x\n")
        harmless = os.path.join(self.tmp, "plain")
        os.makedirs(harmless)
        open(os.path.join(harmless, "notes.txt"), "w").write("x\n")
        self.asks("cp -R %s/%s %s" % (bak, name, parent), "cp -a %s/%s %s/" % (bak, name, parent),
                  "cp -R %s/%s/ %s" % (bak, name, parent),                   # X/ lands in <parent>/X
                  "cp -R %s/ %s" % (bak, parent),                            # its files land in <parent>/<project>/
                  "cp -R %s/. %s" % (bak, parent), "cp -Rt %s %s/%s" % (parent, bak, name),
                  "ditto %s %s" % (bak, parent), "rsync -a %s/%s %s" % (bak, name, parent),
                  "rsync -a %s/ %s" % (bak, parent), "rsync -a --delete %s/ %s" % (harmless, parent),
                  "cp -R %s/%s %s/.." % (bak, name, self.root), "cp -R %s/%s %s/docs/../.." % (bak, name, self.root))
        self.passes("cp -R %s %s" % (harmless, parent), "cp %s/notes.txt %s" % (harmless, parent),
                    "cp -R %s/ %s" % (harmless, parent))          # round 4: rsync into a folder above the project always asks

    def test_a_contents_copy_with_several_sources_asks(self):
        self.grant()
        a, r = self._drop("notes.txt"), self.root
        self.asks("cp -R %s/ %s/docs/ %s" % (a, r, r), "cp -R %s/ %s/. %s" % (a, a, r))

    def test_with_the_grant_every_real_target_and_the_family_still_ask(self):
        r = self.root
        self.grant()
        self.asks("rm data.json", "echo {} > data.json", "python3 job.py %s/data.json" % r, "python3 job.py data/real",
                  "python3 job.py 'data/*'", "sed -i '' s/a/b/ data.json", "python3 -c \"open('data.json','w')\"",
                  "git -C %s commit -m x -- data.json" % r, "git -C %s add -A" % r, "git add .", "git clean -fd",
                  "git stash -u", "find %s -delete" % r, "find . -exec rm {} +",
                  "rm -rf %s" % r, "mv %s /tmp/gone" % r, "rsync -a --delete /tmp/src/ %s/" % r,
                  "cat x > %s" % r, "echo x > data/real/x.csv", "du -sh data.json > %s" % r)
        self.passes("cat data.json", "ls data/real")

    def test_with_the_grant_staged_protected_files_still_ask(self):
        self.grant()
        self.edit("data.json")
        git(self.project, "add", "data.json")
        self.asks("git commit -m x")
        sl.revoke_trust(self.project)
        self.asks("git commit -m x")

    def test_with_the_grant_edit_and_write_of_protected_files_still_ask(self):
        self.grant()
        for tool in ("Edit", "Write"):
            self.assertEqual(sl.real_data_decision(tool, {"file_path": os.path.join(self.root, "data.json")},
                                                   self.project)[0], "ask")

    def test_after_revoke_the_old_asks_come_back(self):
        self.grant()
        self.passes("du -sh %s" % self.root)
        sl.revoke_trust(self.project)
        self.passes("du -sh %s" % self.root)                            # R-493: a reader passes with or without the grant
        self.asks("rm data.json")

    def test_the_first_ask_without_a_grant_offers_it_once(self):
        first = self.d("rm data.json")[1]
        self.assertIn("/budgit-trust work", first)
        self.assertIn("datos reales", first)                                # BUDGIT_LANG=es in the fixtures
        self.assertLess(len(first), 640)
        self.assertNotIn("/budgit-trust work", self.d("rm data.json")[1])   # said once per project
        other = os.path.join(self.tmp, "o2")
        os.makedirs(other)
        sl.activate(other, bind=False)
        pj = os.path.join(sl.studio_dir(other), "project.json")
        cfg = json.load(open(pj)); cfg["protected_paths"] = ["d.json"]; json.dump(cfg, open(pj, "w"))
        got = sl.real_data_decision("Bash", {"command": "rm d.json"}, other, cwd=other)
        self.assertIn("/budgit-trust work", got[1])

    def test_the_offered_flag_is_its_own_file_and_never_revives_a_revoked_grant(self):
        self.d("rm data.json")
        self.assertTrue(os.path.isfile(os.path.join(self.home, sl.OFFERED_FILE)))
        self.assertFalse(os.path.exists(sl.trust_path()))                   # offering wrote no grant
        self.grant()
        sl.revoke_trust(self.project)
        self.d("rm data.json")
        self.assertFalse(sl.trust_work_on(self.project))
        self.assertNotIn("offered", open(sl.trust_path()).read())

    def test_no_offer_when_the_grant_is_on(self):
        self.grant()
        self.assertNotIn("/budgit-trust work", self.d("rm data.json")[1])

    def test_english_offer(self):
        os.environ["BUDGIT_LANG"] = "en"
        got = self.d("rm data.json")[1]
        self.assertIn("/budgit-trust work", got)
        self.assertIn("real data", got)


class TrustCliTest(GuardCase):
    def cli(self, *args, lang="es"):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.tmp, env=env, capture_output=True, text=True)

    def test_work_from_the_cli_writes_nothing_and_explains(self):
        r = self.cli("trust", "work", "--project", self.project)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("/budgit-trust work", r.stderr)
        self.assertFalse(sl.trust_work_on(self.project))
        self.assertFalse(os.path.exists(sl.trust_path()))

    def test_status_and_revoke(self):
        r = self.cli("trust", "status", "--project", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("desactivada", r.stdout)
        self.assertIn("/budgit-trust work", r.stdout)
        self.assertIn("siempre puede editar tus archivos", r.stdout)
        self.grant()
        r = self.cli("trust", "status", "--project", self.project)
        self.assertIn("activada", r.stdout)
        self.assertNotIn("desactivada", r.stdout)
        self.assertIn("datos reales", r.stdout)
        self.assertIn("/budgit-trust revoke", r.stdout)
        r = self.cli("trust", "revoke", "--project", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(sl.trust_work_on(self.project))
        self.assertIn("desactivada", self.cli("trust", "status", "--project", self.project).stdout)

    def test_english(self):
        self.grant()
        out = self.cli("trust", "status", "--project", self.project, lang="en").stdout
        self.assertIn("real data", out)
        self.assertIn("/budgit-trust revoke", out)
        self.assertIn("always edit your files", out)
        out = self.cli("trust", "revoke", "--project", self.project, lang="en").stdout
        self.assertIn("removed", out)

    def test_not_a_project(self):
        r = self.cli("trust", "status", "--project", self.tmp)
        self.assertEqual(r.returncode, 1)

    def test_both_languages_have_the_same_trust_strings(self):
        import i18n
        keys = [k for k in i18n.CATALOG["en"] if k.startswith(("trust.", "guard.trust", "guard.home"))]
        self.assertGreaterEqual(len(keys), 8)
        for k in keys:
            self.assertIn(k, i18n.CATALOG["es"])

    def test_the_slash_command_only_shows_the_status(self):
        text = open(os.path.join(ROOT, "plugin", "commands", "budgit-trust.md"), encoding="utf-8").read()
        self.assertIn("budgit trust status", text)
        self.assertIn("allowed-tools: Bash(budgit trust status:*)", text)
        self.assertNotIn("allowed-tools: Bash(budgit trust work", text)


class HookCase2(GuardCase):
    def fire(self, event, payload, **extra):
        payload = dict(payload, cwd=extra.pop("cwd", self.project), session_id=extra.pop("session_id", "unbound-trust"),
                       **extra)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), capture_output=True, text=True,
                           env=dict(os.environ, BUDGIT_HOME=self.home))
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def tool(self, tool, tool_input, **extra):
        out = self.fire("pre-tool-use", dict(hook_event_name="PreToolUse", tool_name=tool, tool_input=tool_input), **extra)
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")

    def bash(self, cmd, **extra):
        return self.tool("Bash", {"command": cmd}, **extra)

    def prompt(self, text, transcript=None, **extra):
        """The typed `text`, with a transcript whose last user message is `transcript` (default: the same text)."""
        path = self.transcript(text if transcript is None else transcript)
        return self.fire("user-prompt-submit", dict(hook_event_name="UserPromptSubmit", prompt=text,
                                                    transcript_path=path), **extra)


class TrustPromptTest(HookCase2):
    """Only the user's own typed message turns the grant on or off: exact equality on the trimmed prompt."""

    def test_the_exact_message_grants_and_revokes(self):
        out = self.prompt("/budgit-trust work")
        self.assertTrue(sl.trust_work_on(self.project))
        self.assertIn("datos reales", out.get("systemMessage", ""))
        self.assertTrue(self.prompt("  /budgit-trust revoke \n") is not None)
        self.assertFalse(sl.trust_work_on(self.project))

    def test_the_bound_session_project_is_used_over_the_cwd(self):
        sl.activate(self.project, session_id="trust-bound")
        self.prompt("/budgit-trust work", session_id="trust-bound", cwd=self.tmp)
        self.assertTrue(sl.trust_work_on(self.project))

    def test_anything_else_never_matches(self):
        for text in ("/budgit-trust work now", "please run /budgit-trust work", "/budgit-trust  work",
                     "/budgit-trust work\n/budgit-trust work x", "<command-name>/budgit-trust</command-name> work",
                     "/BUDGIT-TRUST work", "/budgit-trust", "/budgit-trust status", "[from session 2] /budgit-trust work",
                     "> /budgit-trust work", "`/budgit-trust work`", "/budgit-trust workx", "budgit-trust work"):
            self.prompt(text)
            self.assertFalse(sl.trust_work_on(self.project), text)

    def test_a_subagent_message_never_grants(self):
        self.prompt("/budgit-trust work", agent_id="a1")
        self.prompt("/budgit-trust work", agent_type="general-purpose")
        self.assertFalse(sl.trust_work_on(self.project))

    def test_no_project_says_so(self):
        out = self.prompt("/budgit-trust work", cwd=self.tmp)
        self.assertFalse(sl.trust_work_on(self.tmp))
        self.assertTrue(out.get("systemMessage"))

    def test_bash_text_matching_is_gone(self):
        for cmd in ("budgit trust work", "echo work | xargs budgit trust", "budgit trust $'wo\\x72k'"):
            self.assertIsNone(self.bash(cmd), cmd)
        self.assertFalse(sl.trust_work_on(self.project))


class BudgitHomeGuardTest(HookCase2):
    """The Budgit home holds the grant: no tool and no plain command writes there."""

    def test_tools_cannot_write_anywhere_in_the_home(self):
        os.makedirs(self.home, exist_ok=True)
        for rel in ("trust.json", "config.json", "sub/new.txt", "approved_prepare.json"):
            path = os.path.join(self.home, rel)
            for tool in ("Edit", "Write", "MultiEdit"):
                self.assertEqual(self.tool(tool, {"file_path": path}), "deny", (tool, rel))
        self.assertEqual(self.tool("Write", {"file_path": os.path.join(self.home, "..", "home", "x")}), "deny")
        self.assertEqual(self.tool("Write", {"file_path": os.path.join(self.home.upper(), "x")}), "deny")  # case-folded
        self.assertEqual(self.tool("Write", {"file_path": "x"}, cwd=self.home), "deny")
        link = os.path.join(self.tmp, "alias")
        os.symlink(self.home, link)
        self.assertEqual(self.tool("Write", {"file_path": os.path.join(link, "x")}), "deny")
        self.assertIsNone(self.tool("Write", {"file_path": os.path.join(self.tmp, "elsewhere.txt")}))

    def test_plain_and_disguised_bash_writes_are_denied(self):
        h, t = self.home, os.path.join(self.home, "trust.json")
        os.makedirs(h, exist_ok=True)
        for cmd in ("echo '{}' > %s" % t, "echo '{}' >> %s" % t, "cp /tmp/x %s" % t, "mv /tmp/x %s" % h, "tee %s < /tmp/x" % t,
                    "sed -i '' s/a/b/ %s" % t, "rm %s" % t, "rm -rf %s" % h, "python3 -c \"open('%s','w')\"" % t,
                    "cd %s && echo {} > trust.json" % h, "cd %s && rm -f *" % h, "cd %s && python3 evil.py" % h,
                    "echo {} > %s/tr''ust.js\"on\"" % h, "echo {} > %s/tru\\st.json" % h,
                    "echo {} > %s/trust.js*" % h, "echo {} > %s/tru*" % h, "touch %s/trust.json" % h,
                    "find %s -name 'trust*' -delete" % h,
                    "cp /tmp/x %s/./trust.json" % h, "cp /tmp/x %s//trust.json" % h, "dd of=%s" % t):
            self.assertEqual(self.bash(cmd), "deny", cmd)
        self.assertIsNone(self.bash("cat %s" % t))
        self.assertIsNone(self.bash("ls %s" % h))
        self.assertIsNone(self.bash("grep work %s" % t))

    def test_tilde_and_home_variable_resolve(self):
        old = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp                       # the Budgit home is ~/home for this test
        try:
            for cmd in ("echo {} > ~/home/trust.json", "echo {} > $HOME/home/trust.json", "echo {} > ${HOME}/home/trust.json",
                        "echo {} > \"$HOME\"/home/trust.json", "cp /tmp/x ~/home/../home/trust.json"):
                got = sl.home_write_decision("Bash", {"command": cmd}, self.tmp)
                self.assertEqual((got or ("none",))[0], "deny", cmd)
            self.assertIsNone(sl.home_write_decision("Bash", {"command": "cat ~/home/trust.json"}, self.tmp))
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old

    def test_unreadable_words_next_to_the_home_ask(self):
        h = self.home
        for cmd in ("echo {} > $X/trust.json", "echo {} > %s/$NAME" % h, "cp /tmp/x $(cat /tmp/where)/trust.json",
                    "echo {} > \"$(pwd)\"/trust.json"):
            self.assertEqual(self.bash(cmd), "ask", cmd)

    def test_a_word_the_glob_engine_rejects_or_a_plain_echo_is_left_alone(self):
        for cmd in ("cat 'a[z-a]' >/dev/null", "echo $STUDIO_HOME", "echo $HOME $X", "printf '%s' \"$HOME\""):
            self.assertIsNone(self.bash(cmd), cmd)

    def test_ordinary_commands_are_left_alone(self):
        for cmd in ("echo $X > out.txt", "rm -rf build/*", "git log --grep 'trust work' -- docs/a.md", "ls ~",
                    "cp a.txt b.txt", "echo trust > notes.txt", "cat trust.json", "echo {} > trust.json"):
            self.assertIsNone(self.bash(cmd), cmd)          # a project's own trust.json is not the Budgit home


class RoundThreeTest(GuardCase):
    """R-558 / R-562 round 3 (security review of 0ddb3b62: HOLD). The contents listing may only ADD an ask; a contents copy
    passes only as ONE plain foreground cp / ditto / rsync; every write key the command reader resolves for a copy verb is a
    hard ask wherever the protected word sits; find -exec, env -S, rsync options after the operands, -S, cd -, popd."""

    def _folder(self, name, *files):
        top = os.path.join(self.tmp, "r3-%d" % len(os.listdir(self.tmp)))
        d = os.path.join(top, name)
        os.makedirs(d, exist_ok=True)
        for n in files:
            os.makedirs(os.path.dirname(os.path.join(d, n)), exist_ok=True)
            open(os.path.join(d, n), "w").write("x\n")
        return d

    def _both(self, method, *cmds, cwd=None):
        """The same expectation without the grant and with it."""
        method(*cmds, cwd=cwd)
        self.grant()
        method(*cmds, cwd=cwd)

    # ---- fix 1: the sole-simple rule (H1, H2) ----

    def test_a_contents_copy_passes_only_as_one_plain_command(self):
        s, r = self._folder("stage", "notes.txt"), self.root
        self.grant()
        self.passes("cp -R %s/ %s" % (s, r), "cp -R %s/. %s" % (s, r), "cp -a %s/ %s" % (s, r),
                    "  cp -R %s/ %s  " % (s, r), "cp -R %s/ %s\n" % (s, r))
        self.asks(
            "for i in 1 2; do cp -R %s/ %s; cp x %s/data.json; done" % (s, r, s),          # H1: the loop runs the write first
            "sleep 2 && cp -R %s/ %s & cp x %s/data.json" % (s, r, s),                        # H1: a background copy
            "cp x %s/data.json && bash -c 'cp -R %s/ %s'" % (s, s, r),                        # H2: a shell gets a fresh context
            "cp x %s/data.json && sh -c \"cp -R %s/ %s\"" % (s, s, r),
            "cp x %s/data.json && bash <<'EOF'\ncp -R %s/ %s\nEOF" % (s, s, r),
            "bash -c 'cp -R %s/ %s'" % (s, r),
            "ls %s && cp -R %s/ %s" % (s, s, r), "echo hi; cp -R %s/ %s" % (s, r),
            "cp -R %s/ %s && echo done" % (s, r), "cp -R %s/ %s | cat" % (s, r), "cp -R %s/ %s &" % (s, r),
            "cp -R %s/ %s > /dev/null" % (s, r), "(cp -R %s/ %s)" % (s, r), "{ cp -R %s/ %s; }" % (s, r),
            "env cp -R %s/ %s" % (s, r), "time cp -R %s/ %s" % (s, r), "nice cp -R %s/ %s" % (s, r),
            "echo %s | xargs -I{} cp -R {}/ %s" % (s, r), "cp -R $(echo %s)/ %s" % (s, r),
            "for d in %s; do cp -R $d/ %s; done" % (s, r), "FOO=1 cp -R %s/ %s" % (s, r), "! cp -R %s/ %s" % (s, r),
            "cp -R %s/ %s || true" % (s, r), "rsync -a %s/ %s; echo" % (s, r), "ditto %s %s && true" % (s, r))

    def test_the_contents_listing_only_adds_an_ask(self):
        d = self._folder("stage", "data.json", "notes.txt")
        self._both(self.asks, "cp -R %s/ %s" % (d, self.root))
        self.asks("true && cp -R %s/ %s" % (d, self.root))

    # ---- H3: find runs a command ----

    def test_find_exec_is_judged_by_the_command_it_runs(self):
        s, r = self._folder("src", "notes.txt"), self.root
        cmds = []
        for prim in ("-exec", "-execdir", "-ok", "-okdir"):
            cmds += ["find %s %s cp {} %s/data.json \\;" % (s, prim, r),
                     "find %s -type f %s cp {} %s/data.json ';'" % (s, prim, r),
                     "find %s %s mv {} %s/ \\;" % (s, prim, r), "find %s %s cp -t %s {} +" % (s, prim, r),
                     "find %s %s sh -c 'cp \"$1\" %s/data.json' _ {} \\;" % (s, prim, r)]
        cmds += ["find %s -name x -exec cp {} %s/data/real \\;" % (s, r),
                 "find %s -exec ln -sf {} %s/data.json \\;" % (s, r)]
        self._both(self.asks, *cmds)
        self.passes("find %s -name x -exec cp {} /tmp/out-r3 \\;" % s, "find %s -exec grep -l x {} \\;" % s,
                    "find %s -exec ls -l {} +" % s, "find %s -name '*.md' -exec cat {} \\;" % s)

    # ---- H4: a write key is a hard ask wherever the protected word sits; M5: rsync options after the operands ----

    def test_a_protected_target_before_a_trailing_option_asks(self):
        r = self.root
        cmds = ["rsync /tmp/x %s/data.json -v" % r, "rsync -a /tmp/x %s/data.json --exclude foo" % r,
                "rsync /tmp/x %s/data.json -e ssh" % r, "rsync /tmp/x %s/data.json --delete --info progress2" % r,
                "rsync /tmp/x %s/data.json --chmod=644" % r, "rsync /tmp/x %s/data.json --filter '- *.tmp'" % r,
                "rsync /tmp/x %s/data.json -ae ssh" % r, "rsync /tmp/x %s/data.json --rsync-path rsync" % r,
                "cp /tmp/x %s/data.json -v" % r, "cp /tmp/x %s/data.json --verbose" % r, "cp -f /tmp/x %s/data/real -p" % r,
                "ln -sf /tmp/x %s/data.json -v" % r, "ln -s /tmp/x %s/data.json --verbose" % r,
                "install /tmp/x %s/data.json -v" % r, "install /tmp/x %s/data.json -m 644" % r]
        self._both(self.asks, *cmds)

    # ---- H5: env -S ----

    def test_env_split_string_is_judged_as_a_command(self):
        r = self.root
        cmds = ["env -S 'cp /tmp/x %s/data.json'" % r, "env --split-string='cp /tmp/x %s/data.json'" % r,
                "env --split-string 'cp /tmp/x %s/data.json'" % r, "env -S'cp /tmp/x %s/data.json'" % r,
                "env -iS 'cp /tmp/x %s/data.json'" % r, "env -S 'FOO=1 cp /tmp/x %s/data.json'" % r,
                "env -u HOME -S 'rm %s/data.json'" % r, "env -S 'tee %s/data.json'" % r]
        self._both(self.asks, *cmds)
        self.passes("env -S 'echo hi'", "env -S 'cp /tmp/x /tmp/y-r3'")

    # ---- M1: rsync follows links ----

    def test_rsync_that_follows_links_asks_for_a_symlinked_folder(self):
        parent = os.path.dirname(self.root)
        d = self._folder("bak", "notes.txt")
        real = self._folder("real", "x.csv")
        os.symlink(real, os.path.join(d, "link"))
        flags = ["-aL", "-a --copy-links", "-rk", "-r --copy-dirlinks", "-rL", "-aK", "-a --keep-dirlinks", "-rtk"]
        cmds = ["rsync %s %s/ %s" % (f, d, parent) for f in flags]
        self._both(self.asks, *cmds)
        self._both(self.asks, "rsync -a %s/ %s" % (d, parent))     # round 4: rsync into a folder above always asks

    # ---- M2: the copy verb word test, tar / unzip into a folder above the project ----

    def test_a_copy_verb_hidden_from_a_word_test_is_still_judged(self):
        parent, name = os.path.dirname(self.root), os.path.basename(self.root)
        bak = os.path.join(self.tmp, "bak3")
        os.makedirs(os.path.join(bak, name))
        open(os.path.join(bak, name, "data.json"), "w").write("x\n")
        cmds = ["c''p -R %s/%s %s" % (bak, name, parent), "gcp -R %s/%s %s" % (bak, name, parent),
                "\\cp -R %s/%s %s" % (bak, name, parent), "\"cp\" -R %s/%s %s" % (bak, name, parent),
                "/bin/cp -R %s/%s %s" % (bak, name, parent), "command cp -R %s/%s %s" % (bak, name, parent),
                "gcp -a %s/%s %s" % (bak, name, parent), "/usr/bin/ditto %s %s" % (bak, parent)]
        self._both(self.asks, *cmds)

    def test_extracting_into_a_folder_above_the_project_asks(self):
        parent = os.path.dirname(self.root)
        cmds = ["tar -xf /tmp/a.tar -C %s" % parent, "tar -C %s -xf /tmp/a.tar" % parent, "tar xzf /tmp/a.tgz -C %s/" % parent,
                "unzip /tmp/a.zip -d %s" % parent, "unzip -d %s /tmp/a.zip" % parent,
                "tar -xf /tmp/a.tar -C %s/%s/.." % (parent, "zz"), "bsdtar -xf /tmp/a.tar -C %s" % parent]
        self._both(self.asks, *cmds)
        self.passes("tar -xf /tmp/a.tar -C /tmp/r3-out", "unzip /tmp/a.zip -d /tmp/r3-out")

    # ---- M3: cd -, popd, pushd lose the folder ----

    def test_a_relative_target_after_a_lost_folder_asks(self):
        r = self.root
        name = os.path.basename(r)
        bak = os.path.join(self.tmp, "bak4")
        os.makedirs(os.path.join(bak, name))
        open(os.path.join(bak, name, "data.json"), "w").write("x\n")
        cmds = ["cd .. && pushd /tmp && popd && cp -R %s/%s ." % (bak, name),
                "cd .. && cd /tmp && cd - && cp -R %s/%s ." % (bak, name),
                "pushd /tmp && popd && cp /tmp/x data.json", "cd /tmp && cd - && cp /tmp/x data.json",
                "cd /tmp && cd - && rm data.json", "cd /tmp; popd; cp /tmp/x data/real/x.csv"]
        self._both(self.asks, *cmds, cwd=r)

    # ---- M4: GNU -S takes a value ----

    def test_cp_suffix_takes_a_value(self):
        r = self.root
        d = self._folder("data", "notes.txt")
        cmds = ["cp -RSt %s/ %s" % (d, r), "cp -R -S -t %s/ %s" % (d, r), "cp -R -S .bak %s %s" % (d, r),
                "cp -RS.bak %s %s" % (d, r), "cp -R --suffix=.bak %s %s" % (d, r), "cp -R --suffix .bak %s/ %s" % (d, r),
                "cp -R --suf=.bak %s %s" % (d, r), "mv -S .b %s %s" % (d, r), "cp -S x /tmp/y %s/data.json" % r,
                "cp -S t -t %s /tmp/data.json" % r]
        self._both(self.asks, *cmds)

    # ---- L1: readers that can write or run code ----

    def test_readers_that_can_run_code_or_write_ask(self):
        r = self.root
        cmds = ["rg --pre ./fill.sh x %s/data.json" % r, "rg --pre=./fill.sh x %s/data.json" % r,
                "rg --hostname-bin ./h x %s/data.json" % r, "file -C -m %s/data.json" % r,
                "compgen -C 'cp x %s/data.json'" % r, "history -w %s/data.json" % r,
                "enable -f ./l.so x %s/data.json" % r, "hash -p ./evil cp %s/data.json" % r,
                "let 'a[$(cp x %s/data.json)]=1'" % r, "[[ -n $(cp x %s/data.json) ]]" % r]
        self._both(self.asks, *cmds)
        self.passes("rg -n x %s/docs" % self.root, "file %s/docs/a.md" % self.root, "cat %s/docs/a.md" % self.root)

    # ---- L2: a target that ends in .. ----

    def test_a_target_ending_in_dotdot_with_an_unreadable_prefix_asks(self):
        bak = self._folder(os.path.basename(self.root), "data.json")
        cmds = ["cp -R %s \"$CLAUDE_PROJECT_DIR/..\"" % bak, "cp -R %s \"$UNKNOWN/..\"" % bak, "cp -R %s $X/../.." % bak,
                "cp -R %s \"$X/.\"" % bak]
        self._both(self.asks, *cmds)


class RoundFourTest(GuardCase):
    """R-558 / R-562 round 4 (security review of 80f6b554: HOLD, N1-N5). After three rounds of option deny-lists the trusted
    path is an ALLOW-LIST: a contents copy into the root or a folder above it passes only as one plain foreground `cp` with
    options from -R -r -a -p -v -f -n -X (combined groups of those letters) or `ditto` with no option or -V. Everything else
    asks: any other option, a long option, rsync, gcp, --parents."""

    def _folder(self, name, *files):
        top = os.path.join(self.tmp, "r4-%d" % len(os.listdir(self.tmp)))
        d = os.path.join(top, name)
        os.makedirs(d, exist_ok=True)
        for n in files:
            os.makedirs(os.path.dirname(os.path.join(d, n)), exist_ok=True)
            open(os.path.join(d, n), "w").write("x\n")
        return d

    def _both(self, method, *cmds, cwd=None):
        method(*cmds, cwd=cwd)
        self.grant()
        method(*cmds, cwd=cwd)

    def _bak(self):
        """A backup folder that holds a folder named like the project, with a protected file inside."""
        name = os.path.basename(self.root)
        bak = os.path.join(self.tmp, "r4bak%d" % len(os.listdir(self.tmp)))
        os.makedirs(os.path.join(bak, name))
        open(os.path.join(bak, name, "data.json"), "w").write("x\n")
        return bak, name

    # ---- the allow-list ----

    def test_the_allow_listed_contents_copies_still_pass(self):
        s, r, p = self._folder("stage", "notes.txt"), self.root, os.path.dirname(self.root)
        self.grant()
        self.passes("cp -R %s/ %s" % (s, r), "cp -a %s/. %s" % (s, r),
                    "cp -Rpv %s/ %s" % (s, r), "cp -rfn %s/ %s" % (s, r), "cp -R -v %s/ %s" % (s, r),
                    "cp -RX %s/ %s/" % (s, r), "cp -R %s/ %s" % (s, p))
        # ditto into the project folder is asked about by the older archive scan when the cwd is the project (as before)
        self.passes("ditto %s %s" % (s, r), "ditto -V %s %s" % (s, r), "ditto %s %s" % (s, p), cwd=self.tmp)

    def test_any_other_option_or_verb_on_a_contents_copy_asks(self):
        s, r, p = self._folder("stage", "notes.txt"), self.root, os.path.dirname(self.root)
        cmds = ["cp -RT %s %s" % (s, r), "cp -R -T %s %s" % (s, r), "cp -RL %s/ %s" % (s, r), "cp -RH %s/ %s" % (s, r),
                "cp --recursive %s/ %s" % (s, r), "cp -R -- %s/ %s" % (s, r), "cp -Ri %s/ %s" % (s, r),
                "cp -Rl %s/ %s" % (s, r), "cp -Rs %s/ %s" % (s, r), "cp -Ru %s/ %s" % (s, r),
                "cp -R --preserve=all %s/ %s" % (s, r), "cp -R --no-clobber %s/ %s" % (s, r), "cp -Rb %s/ %s" % (s, r),
                "cp -RS .b %s/ %s" % (s, r), "cp -R --backup %s/ %s" % (s, r), "cp -R --update %s/ %s" % (s, r),
                "cp -R -t %s %s/" % (r, s), "cp -R %s/ %s" % (s, p) + " -L",
                "ditto -rsrc %s %s" % (s, r), "ditto -k %s %s" % (s, r), "ditto --norsrc %s %s" % (s, r),
                "ditto -V -V %s %s" % (s, r), "ditto -x %s %s" % (s, r),
                "gcp -R %s/ %s" % (s, r), "gcp -a %s/. %s" % (s, p), "rsync -a %s/ %s" % (s, r), "rsync -a %s/ %s" % (s, p),
                "rsync -r %s/ %s" % (s, p), "rsync -aR %s/ %s" % (s, p), "rsync -a --delete %s/ %s" % (s, p)]
        self._both(self.asks, *cmds)

    # ---- N1: rsync -R / --relative / --files-from and cp --parents ----

    def test_relative_and_parents_copies_into_the_root_or_above_ask(self):
        bak, name = self._bak()
        r, p = self.root, os.path.dirname(self.root)
        lst = os.path.join(self.tmp, "list-r4.txt")
        open(lst, "w").write("%s/data.json\n" % name)
        cmds = ["rsync -aR %s/./%s/ %s" % (bak, name, p), "rsync -a --relative %s/./%s/ %s" % (bak, name, p),
                "rsync -aR %s/./%s %s" % (bak, name, p), "rsync -a --rel %s/./%s %s" % (bak, name, p),
                "rsync -a --files-from=%s %s/ %s" % (lst, bak, p), "rsync -a --files-from %s %s/ %s" % (lst, bak, p),
                "rsync -R %s/./%s/data.json %s" % (bak, name, p), "rsync -tR %s/./%s/ %s" % (bak, name, p),
                "cp --parents %s/%s/data.json %s" % (bak, name, p), "gcp --parents %s/%s/data.json %s" % (bak, name, p),
                "cp -R --parents %s/%s/data.json %s" % (bak, name, p), "cp --par %s/notes.txt %s" % (bak, p),
                "cp --parents %s/notes.txt %s" % (bak, r), "gcp --parents %s/notes.txt %s" % (bak, r),
                "cp --parents -t %s %s/notes.txt" % (p, bak),
                "cd %s && gcp --parents %s/data.json %s" % (bak, name, p)]
        self._both(self.asks, *cmds)

    # ---- N2: rsync options that write a file ----

    RSYNC_FILE_OPTS = ["--log-file", "--write-batch", "--only-write-batch", "--backup-dir", "--partial-dir", "--temp-dir"]

    def test_rsync_file_writing_options_are_targets_in_both_forms(self):
        r = self.root
        cmds = []
        for opt in self.RSYNC_FILE_OPTS:
            for prot in ("%s/data.json" % r, "%s/data/real/x.csv" % r, "%s/data" % r):
                cmds += ["rsync -a %s %s /tmp/a-r4 /tmp/b-r4" % (opt, prot), "rsync -a %s=%s /tmp/a-r4 /tmp/b-r4" % (opt, prot),
                         "rsync %s %s -a /tmp/a-r4 /tmp/b-r4" % (opt, prot), "rsync -an %s %s /tmp/a-r4 /tmp/b-r4" % (opt, prot)]
        cmds += ["rsync -a --log-fi %s/data.json /tmp/a-r4 /tmp/b-r4" % r, "rsync -a --temp %s/data /tmp/a-r4 /tmp/b-r4" % r,
                 "rsync -a -T %s/data /tmp/a-r4 /tmp/b-r4" % r, "rsync -aT %s/data /tmp/a-r4 /tmp/b-r4" % r,
                 "rsync -aT%s/data /tmp/a-r4 /tmp/b-r4" % r, "rsync -a --partial-dir=%s/data /tmp/a-r4 /tmp/b-r4" % r]
        self._both(self.asks, *cmds)
        self.passes("rsync -a --log-file /tmp/log-r4.txt /tmp/a-r4 /tmp/b-r4", "rsync -a --temp-dir /tmp /tmp/a-r4 /tmp/b-r4",
                    "rsync -a --write-batch=/tmp/batch-r4 /tmp/a-r4 /tmp/b-r4", "rsync -aT /tmp /tmp/a-r4 /tmp/b-r4",
                    "rsync -a --exclude data.json /tmp/a-r4 /tmp/b-r4")

    def test_the_older_scan_reads_the_separate_word_form_too(self):
        patterns = sl.load_project_config(self.project).get("protected_paths") or []
        r, reason = self.root, "r %s %s"
        for opt in self.RSYNC_FILE_OPTS + ["-T"]:
            for cmd in ("rsync -a %s %s/data /tmp/a-r4 /tmp/b-r4" % (opt, r),):
                got = sl._strict_extras(cmd, self.project, patterns, self.project, reason)
                self.assertIsNotNone(got, cmd)
        self.assertIsNone(sl._strict_extras("rsync -a --log-file /tmp/l-r4 /tmp/a-r4 /tmp/b-r4", self.project, patterns, self.project, reason))

    # ---- N3: pushd / cd forms that lose the folder ----

    def _lost_cmds(self, tail):
        """Command lines that end in a folder the guard cannot know, from the project folder, then `tail`."""
        return ["cd .. && cd /tmp && cd - && " + tail, "cd .. && pushd /tmp && pushd && " + tail,
                "cd .. && pushd /tmp && pushd +1 && " + tail, "cd .. && pushd /tmp && pushd -1 && " + tail,
                "cd .. && cd /tmp && cd ~- && " + tail, "cd .. && cd /tmp && cd ~+ && " + tail,
                "cd .. && cd /tmp && cd ~-1 && " + tail, "cd .. && pushd -n /tmp && " + tail]

    def test_a_folder_lost_by_pushd_or_a_tilde_form_makes_a_relative_write_ask(self):
        r = self.root
        cmds = []
        for tail in ("cp /tmp/x data.json", "rm data.json", "cp /tmp/x data/real/x.csv"):
            cmds += self._lost_cmds(tail)
        self._both(self.asks, *cmds, cwd=r)
        self.passes("pushd /tmp && cp /tmp/x notes-r4.txt", "cd /tmp && cp /tmp/x notes-r4.txt", cwd=r)

    def test_a_pushd_n_does_not_change_the_folder(self):
        self.asks("pushd -n /tmp && cp /tmp/x data.json", cwd=self.root)

    def test_a_contents_copy_into_an_unreadable_folder_is_judged_by_the_names_inside(self):
        name = os.path.basename(self.root)
        bad, good = self._folder("stage", "data.json", "notes.txt"), self._folder("stage", "notes.txt")
        r = self.root
        for lose in ("cd .. && cd /tmp && cd - && ", "cd .. && pushd /tmp && pushd && ", "cd .. && cd /tmp && cd ~- && "):
            self._both(self.asks, lose + "cp -R %s/ %s" % (bad, name), lose + "cp -a %s/. %s" % (bad, name),
                       lose + "cp %s/data.json %s" % (bad, name), cwd=r)
            # after a lost folder a contents copy cannot be listed as one plain command, and a word naming the project asks
            self._both(self.asks, lose + "cp -R %s/ %s" % (good, name), cwd=r)
        # an unreadable variable folder whose own name is the project's: the names of the sources count, not only the tail
        self._both(self.asks, "cp -R %s/ \"$OUT_R4/%s\"" % (bad, name), "cp %s/data.json \"$OUT_R4/%s\"" % (bad, name), cwd=r)
        self.passes("cp %s/notes.txt \"$OUT_R4/%s\"" % (good, name), "cp -R %s/ \"$OUT_R4/dest\"" % good, cwd=r)

    def test_a_delete_of_the_project_after_a_lost_folder_asks(self):
        name = os.path.basename(self.root)
        self.asks(*self._lost_cmds("rm -rf %s" % name), cwd=self.root)

    # ---- N4: hard asks have their own memory class ----

    SESSION = "sess-r558-r4-aaaa1111"

    def _check(self, cmd):
        return sl.real_data_check("Bash", {"command": cmd}, self.project, self.project, self.SESSION)

    def _yes(self, cmd):
        got, keys = self._check(cmd)
        self.assertEqual(got[0], "ask", cmd)
        self.assertTrue(keys, cmd)
        self.assertTrue(sl.note_real_data_ask(self.SESSION, "Bash", {"command": cmd}, keys))
        self.assertTrue(sl.promote_real_data_yes(self.SESSION, "Bash", {"command": cmd}))
        return keys

    def test_hard_asks_use_the_contents_and_above_classes(self):
        s, p = self._folder("stage", "notes.txt"), os.path.dirname(self.root)
        _g, keys = self._check("bash -c 'cp -R %s/ %s'" % (s, self.root))
        self.assertEqual(keys, ["contents:cp:" + sl._fold(self.root)])
        _g, keys = self._check("rsync -a %s/ %s" % (s, p))
        self.assertEqual(keys, ["above:rsync:" + sl._fold(p)])
        _g, keys = self._check("cp --parents %s/notes.txt %s" % (s, self.root))
        self.assertEqual(keys, ["contents:cp:" + sl._fold(self.root)])

    def test_a_yes_to_an_unrelated_ambiguous_ask_does_not_pass_a_hard_ask(self):
        s, p, r = self._folder("stage", "notes.txt"), os.path.dirname(self.root), self.root
        self._yes("cp x $UNKNOWN_R4/data.json")                                       # an `ambiguous:<root>` ask
        for cmd in ("bash -c 'cp -R %s/ %s'" % (s, r), "rsync -a %s/ %s" % (s, p), "cp --parents %s/notes.txt %s" % (s, r),
                    "ditto -k %s %s" % (s, r), "cd .. && cd /tmp && cd - && cp -R %s/ %s" % (self._folder("b", "data.json"), os.path.basename(r))):
            self.assertEqual(self._check(cmd)[0][0], "ask", cmd)

    def test_a_yes_to_a_hard_ask_covers_its_own_class_only(self):
        s, p, r = self._folder("stage", "notes.txt"), os.path.dirname(self.root), self.root
        self._yes("bash -c 'cp -R %s/ %s'" % (s, r))
        self.assertIsNone(self._check("bash -c 'cp -R %s/ %s'" % (s, r))[0])
        self.assertEqual(self._check("rsync -a %s/ %s" % (s, p))[0][0], "ask")        # `above:` is another class
        self.assertEqual(self._check("cp x $UNKNOWN_R4/data.json")[0][0], "ask")        # so is `ambiguous:`
        self._yes("rsync -a %s/ %s" % (s, p))
        self.assertIsNone(self._check("rsync -a %s/ %s" % (self._folder("other", "n.txt"), p))[0])


class RoundFiveTest(GuardCase):
    """R-558 round 5 (Codex review of the round-4 delta, checked claim by claim): yes coverage is isolated between the hard
    classes and `ambiguous`, a known cd into protected data stays one of the folders a lost cwd can be, and rsync -e / --rsh
    is judged as the command it runs."""

    def _folder(self, name, *files):
        top = os.path.join(self.tmp, "r5-%d" % len(os.listdir(self.tmp)))
        d = os.path.join(top, name)
        os.makedirs(d, exist_ok=True)
        for n in files:
            open(os.path.join(d, n), "w").write("x\n")
        return d

    def _check(self, cmd, session):
        return sl.real_data_check("Bash", {"command": cmd}, self.project, self.project, session)

    def _yes(self, cmd, session):
        got, keys = self._check(cmd, session)
        self.assertEqual(got[0], "ask", cmd)
        self.assertTrue(sl.note_real_data_ask(session, "Bash", {"command": cmd}, keys))
        self.assertTrue(sl.promote_real_data_yes(session, "Bash", {"command": cmd}))
        return keys

    def test_a_yes_covers_the_same_ask_and_no_other_class(self):
        s, r = self._folder("stage", "notes.txt"), self.root
        p = os.path.dirname(r)
        asks = {"above-p": "rsync -a %s/ %s" % (s, p), "above-gp": "rsync -a %s/ %s" % (s, os.path.dirname(p)),
                "contents": "bash -c 'cp -R %s/ %s'" % (s, r), "ambiguous": "cp x $UNKNOWN_R5/data.json"}
        keys = {name: self._check(cmd, "sess-r5-probe-0001")[1] for name, cmd in asks.items()}
        self.assertEqual(keys["above-p"], ["above:rsync:" + sl._fold(p)])
        self.assertEqual(keys["above-gp"], ["above:rsync:" + sl._fold(os.path.dirname(p))])
        self.assertEqual(keys["contents"], ["contents:cp:" + sl._fold(r)])
        self.assertIn("ambiguous:" + sl._fold(r), keys["ambiguous"])
        for n, (first, cmd) in enumerate(asks.items()):
            session = "sess-r5-iso-%04d" % n
            self._yes(cmd, session)
            self.assertIsNone(self._check(cmd, session)[0], first)                    # the same ask is covered
            self.assertTrue(sl.real_data_yes_covers(session, self.project, keys[first]))
            for other, ocmd in asks.items():
                if other != first:
                    self.assertEqual(self._check(ocmd, session)[0][0], "ask", "%s after a yes to %s" % (other, first))
                    self.assertFalse(sl.real_data_yes_covers(session, self.project, keys[other]), other)

    def test_a_known_cd_into_protected_data_is_one_of_the_folders_a_lost_cwd_can_be(self):
        patterns = sl.load_project_config(self.project).get("protected_paths") or []
        for cmd in ("cd %s/data/real && rm x.csv" % self.root, "cd %s/data/real && cd - && rm x.csv" % self.root):
            got = sl._strict_extras(cmd, self.project, patterns, self.tmp, "r %s %s")
            self.assertIsNotNone(got, cmd)

    def test_popd_loses_the_folder_in_both_readers(self):
        patterns = sl.load_project_config(self.project).get("protected_paths") or []
        self.asks("pushd /tmp && popd && cp /tmp/x data.json", "cd /tmp && popd; rm data.json", cwd=self.root)
        self.assertTrue(sl._cd_loses_folder("popd", []))
        self.assertIsNotNone(sl._strict_extras("cd .. && pushd /tmp && popd && rm -rf %s" % os.path.basename(self.root),
                                               self.project, patterns, self.root, "r %s %s"))

    def test_rsync_remote_shell_is_judged_as_the_command_it_runs(self):
        r, a = self.root, "/tmp/a-r5"
        cmds = ["rsync -a -e 'cp x %s/data.json' %s host:/b" % (r, a), "rsync -a --rsh='cp x %s/data.json' %s host:/b" % (r, a),
                "rsync -a --rsh 'cp x %s/data.json' %s host:/b" % (r, a), "rsync -ae 'cp x %s/data.json' %s host:/b" % (r, a),
                "rsync -an -e 'rm %s/data.json' %s host:/b" % (r, a), "rsync -e'tee %s/data.json' %s host:/b" % (r, a),
                "rsync %s host:/b -e 'cp x %s/data.json'" % (a, r)]
        for grant in (False, True):
            if grant:
                self.grant()
            self.asks(*cmds)
        self.passes("rsync -a -e ssh %s host:/b" % a, "rsync -a --rsh='ssh -p 22' %s host:/b" % a)

    def test_rsync_runner_abbreviations_are_judged_too(self):
        # The splitter reads `--rs` as a valued abbreviation, so its value must be judged as the command it may run, as
        # are the values of `--rsync-path`, the program the remote shell starts.
        r, a = self.root, "/tmp/a-r5"
        self.asks("rsync -a --rs='cp x %s/data.json' %s host:/b" % (r, a),
                  "rsync -a --rs 'cp x %s/data.json' %s host:/b" % (r, a),
                  "rsync -a --rsync-path='cp x %s/data.json' %s host:/b" % (r, a),
                  "rsync -a --rsync-p 'rm %s/data.json' %s host:/b" % (r, a))
        self.passes("rsync -a --rsync-path='sudo rsync' %s host:/b" % a)


class RoundSixTest(GuardCase):
    """R-558 round 6 (security review r4/r5: HOLD, H1-H4, M1, L2). Root cause: the reader kept a folder it could not be sure
    of and then dropped the base guard's ask. A cd moves the tracked folder only when it surely ran and succeeded in this
    shell (a top-level `cd X && ...` with a literal X); everything else leaves the folder lost, and a lost folder never turns
    an ask into a pass."""

    def _folder(self, name, *files):
        top = os.path.join(self.tmp, "r6-%d" % len(os.listdir(self.tmp)))
        d = os.path.join(top, name)
        os.makedirs(d, exist_ok=True)
        for n in files:
            open(os.path.join(d, n), "w").write("x\n")
        return d

    def _check(self, cmd, session):
        return sl.real_data_check("Bash", {"command": cmd}, self.project, self.project, session)

    def _yes(self, cmd, session):
        got, keys = self._check(cmd, session)
        self.assertEqual(got[0], "ask", cmd)
        self.assertTrue(sl.note_real_data_ask(session, "Bash", {"command": cmd}, keys))
        self.assertTrue(sl.promote_real_data_yes(session, "Bash", {"command": cmd}))
        return keys

    def _both(self, method, *cmds):
        method(*cmds, cwd=self.root)
        self.grant()
        method(*cmds, cwd=self.root)

    # ---- H1: a cd that may not run, may fail, or runs in another shell does not move the folder ----

    def test_a_cd_in_a_subshell_or_one_that_may_fail_never_moves_the_folder(self):
        w = "cp /tmp/y.csv data/real/"
        self._both(self.asks, "(cd /tmp); " + w, "cd /tmp/nope-r6; " + w, "x=$(cd /tmp); " + w, "x=`cd /tmp`; " + w,
                   "echo \"$(cd /tmp)\"; " + w, "cd /tmp/nope-r6\n" + w, "cd /tmp/nope-r6 || " + w, "cd /tmp & " + w,
                   "true | cd /tmp && " + w, "cd /tmp | true && " + w, "cd /tmp/nope-r6 && true; " + w,
                   "if cd /tmp/nope-r6; then true; fi; " + w, "{ cd /tmp/nope-r6; true; } && " + w,
                   "sudo cd /tmp && " + w, "env cd /tmp && " + w, "nice cd /tmp && " + w, "/usr/bin/cd /tmp && " + w,
                   "(cd /tmp && true) && " + w, "bash -c 'cd /tmp' && " + w)

    # ---- H2: words that name a folder the guard cannot know ----

    def test_tilde_stack_words_zsh_stack_cd_two_operands_and_variables_lose_the_folder(self):
        t = " && cp /tmp/x.csv a.csv"
        self._both(self.asks, "cd ~+/data/real" + t, "cd docs && cd ~-/data/real" + t, "cd ~0/data/real" + t,
                   "cd ~+0/data/real" + t, "pushd ~+/data/real" + t, "pushd data/real && pushd /tmp && cd +1" + t,
                   "pushd data/real && pushd /tmp && cd -1" + t, "cd docs && cd docs data/real" + t,
                   "D=data/real; cd \"$D\"" + t, "cd data/real/$NOPE_R6" + t)
        for args in (["~+/x"], ["~-/x"], ["~1/x"], ["+1"], ["-2"], ["a", "b"], ["$D"], ["`pwd`"]):
            self.assertTrue(sl._cd_loses_folder("cd", args), args)
        for args in (["/tmp"], ["docs"], ["~/x"], ["-"], [], ["-P", "/tmp"]):
            self.assertEqual(sl._cd_loses_folder("cd", args), args == ["-"], args)

    # ---- H4 / item 3: rsync folder options are read from the destination; -M values are judged ----

    def test_rsync_dir_options_are_read_from_the_destination_and_remote_options_are_judged(self):
        r, s = self.root, self._folder("src", "n.txt")
        os.makedirs(os.path.join(r, "sub"), exist_ok=True)
        self._both(self.asks, "rsync -ab --backup-dir=../data/real %s/ %s/sub/" % (s, r),
                   "rsync -ab --backup-dir ../data/real %s/ %s/sub/" % (s, r),
                   "rsync -a -T ../data/real %s/ %s/sub/" % (s, r), "rsync -aT../data/real %s/ %s/sub/" % (s, r),
                   "rsync -a --temp-dir=../data/real %s/ %s/sub/" % (s, r),
                   "rsync -a --partial-dir=../data/real %s/ %s/sub/" % (s, r),
                   "rsync -a --part ../data/real %s/ %s/sub/" % (s, r),
                   "rsync -a -M--log-file=%s/data.json %s/ /tmp/out-r6/" % (r, s),
                   "rsync -a -M --log-file=data.json %s/ /tmp/out-r6/" % s,
                   "rsync -a --remote-option=--backup-dir=../data/real -b %s/ %s/sub/" % (s, r),
                   "rsync -a --remote-o --temp-dir=../data/real %s/ %s/sub/" % (s, r))
        self.passes("rsync -ab --backup-dir=../bak-r6 %s/ %s/sub/" % (s, r), "rsync -a %s/ /tmp/out-r6/" % s, cwd=r)

    # ---- H3 / item 4: with a remote shell set, host:path is also a local path ----

    def test_a_remote_operand_is_judged_as_local_when_a_remote_shell_is_set(self):
        r = self.root
        self._both(self.asks, "rsync -a -e env /tmp/x.csv A=b:%s/data/real/" % r,
                   "rsync -a --rsh=env /tmp/x.csv h:%s/data/real/" % r, "rsync -a --rs env /tmp/x.csv h:data/real/",
                   "RSYNC_RSH=env rsync -a /tmp/x.csv A=b:%s/data/real/" % r,
                   "export RSYNC_RSH=env; rsync -a /tmp/x.csv h:data/real/",
                   "env RSYNC_RSH=env rsync -a /tmp/x.csv h:data.json",
                   "rsync -ab -e env --backup-dir=../data/real /tmp/x.csv h:%s/sub/" % r)
        self.passes("rsync -a -e ssh /tmp/x.csv host:/b", "rsync -a /tmp/x.csv host:data/real/", cwd=r)

    # ---- M1 / item 5: a yes for one verb does not cover another ----

    def test_hard_ask_keys_carry_the_verb(self):
        s, r = self._folder("stage", "notes.txt"), self.root
        p = os.path.dirname(r)
        self.assertEqual(self._check("rsync -a %s/ %s" % (s, p), "sess-r6-k-0001")[1], ["above:rsync:" + sl._fold(p)])
        self.assertEqual(self._check("bash -c 'cp -R %s/ %s'" % (s, r), "sess-r6-k-0001")[1],
                         ["contents:cp:" + sl._fold(r)])
        sess = "sess-r6-verb-0001"
        tarball = os.path.join(self.tmp, "r6.tar")
        subprocess.run(["tar", "-cf", tarball, "-C", s, "notes.txt"], check=True)
        self._yes("tar -xf %s -C %s" % (tarball, p), sess)
        self.assertEqual(self._check("rsync -aR %s/ %s" % (s, p), sess)[0][0], "ask")
        self.assertEqual(self._check("rsync -a %s/ %s" % (s, p), sess)[0][0], "ask")
        self._yes("rsync -a %s/ %s" % (s, p), sess)
        self.assertIsNone(self._check("rsync -a %s/ %s" % (s, p), sess)[0])

    # ---- L2 ----

    def test_rsync_daemon_asks(self):
        self._both(self.asks, "rsync --daemon --config=/tmp/r6.conf", "rsync --daem --no-detach")

    # ---- harmless rows that must keep passing ----

    def test_harmless_cd_and_copy_rows_still_pass(self):
        r = self.root
        os.makedirs(os.path.join(r, "sub"), exist_ok=True)
        os.makedirs(os.path.join(r, "build"), exist_ok=True)
        os.makedirs(os.path.join(r, "src2"), exist_ok=True)
        for grant in (False, True):
            if grant:
                self.grant()
            self.passes("cd sub && cp /tmp/a.csv b.csv", "(cd build && make)", "cd /tmp && ls", "cp -R src2/ dst/",
                        "rsync -a src2/ /tmp/out/", "cd sub && cp /tmp/a.csv b.csv && ls data/real",
                        "cd /tmp && ls %s/data/real" % r, cwd=r)


class RoundSevenTest(GuardCase):
    """R-558 round 7 (security review r6: HOLD, 6 high). Six rounds of blocklist patches each missed a shell form that made
    the reader believe a `cd` surely ran. The design is now an allowlist: the folder moves only on ONE certain shape, and any
    other cd-like word on the line leaves the folder lost (sticky), which keeps the base guard's ask and gives it a memory
    key of its own."""

    def _check(self, cmd, session):
        return sl.real_data_check("Bash", {"command": cmd}, self.project, self.project, session)

    def _yes(self, cmd, session):
        got, keys = self._check(cmd, session)
        self.assertEqual(got[0], "ask", cmd)
        self.assertTrue(sl.note_real_data_ask(session, "Bash", {"command": cmd}, keys))
        self.assertTrue(sl.promote_real_data_yes(session, "Bash", {"command": cmd}))
        return keys

    def _both(self, method, *cmds):
        method(*cmds, cwd=self.root)
        self.grant()
        method(*cmds, cwd=self.root)

    def _judge(self, cmd):
        import realdata
        return realdata.judge(cmd, self.project, sl.load_project_config(self.project).get("protected_paths") or [], self.root)

    def test_every_round_six_command_asks(self):
        r = self.root
        os.makedirs(os.path.join(r, "sub"), exist_ok=True)
        self._both(self.asks,
                   "! cd /tmp/nope && cp /tmp/y.csv data/real/",
                   "f() { cd /tmp/nope && true } && cp /tmp/y.csv data/real/",
                   "( case a in a) true;; esac; cd /tmp/nope && true ) && cp /tmp/y.csv data/real/",
                   "X=../data/real; rsync -ab --backup-dir=$X /tmp/src/ %s/sub/" % r,
                   "rsync -a -e env /tmp/x.csv A=b::m/real/",
                   "D=%s/data; cp /tmp/x.csv ~D/real/y.csv" % r,
                   "CDPATH+=:%s/data; cd real && cp /tmp/x.csv y.csv" % r,
                   "cdpath=(%s/data); cd real && cp /tmp/x.csv y.csv" % r,
                   "cd d?ta && cp /tmp/x.csv real/y.csv",
                   "rsync -a -M--backup-dir -M../data/real /tmp/src/ %s/sub/" % r)

    def test_rsync_items(self):
        r = self.root
        os.makedirs(os.path.join(r, "sub"), exist_ok=True)
        self._both(self.asks,
                   "X=../data/real; rsync -ab --backup-dir=$X /tmp/src/ %s/sub/" % r,
                   "rsync -a --partial-dir=$NOPE_R7 /tmp/src/ %s/sub/" % r,
                   "rsync -a -T `echo x` /tmp/src/ %s/sub/" % r,
                   "rsync -a --temp-dir=~D/x /tmp/src/ %s/sub/" % r,
                   "rsync -a --backup-dir=*/real /tmp/src/ %s/sub/" % r,
                   "rsync -a -M--backup-dir=$NOPE_R7 /tmp/src/ %s/sub/" % r,
                   "rsync -a -e env /tmp/x.csv h::m/real/", "rsync -a --rsh=env /tmp/x.csv rsync://h/m/real/",
                   "RSYNC_RSH=env rsync -a /tmp/x.csv h::m/real/", "rsync -a --rs env /tmp/x.csv h::m",
                   "rsync -a -M--backup-dir -M../data/real /tmp/src/ %s/sub/" % r,
                   "rsync -a -M --backup-dir -M ../data/real /tmp/src/ %s/sub/" % r,
                   "rsync -a --remote-option=--backup-dir --remote-option=../data/real /tmp/src/ %s/sub/" % r)
        self.passes("rsync -a --backup-dir=../bak-r7 /tmp/src/ %s/sub/" % r, "rsync -a /tmp/x.csv h::m/real/",
                    "rsync -a -e ssh /tmp/x.csv host:/b", cwd=r)

    def test_tilde_names_and_globs_are_unreadable_in_a_target_and_a_cd_word(self):
        r = self.root
        self._both(self.asks,
                   "D=%s/data; cp /tmp/x.csv ~D/real/y.csv" % r, "D=%s/data; mv /tmp/x.csv ~D/real/y.csv" % r,
                   "D=%s/data; touch ~D/real/y.csv" % r, "D=%s/data; echo x > ~D/real/y.csv" % r,
                   "D=%s/data; cd ~D && cp /tmp/x.csv real/y.csv" % r, "cd d?ta && cp /tmp/x.csv real/y.csv",
                   "cd d*a && cp /tmp/x.csv real/y.csv", "cd d[a]ta && cp /tmp/x.csv real/y.csv",
                   "cd {data,docs} && cp /tmp/x.csv real/y.csv")
        self.assertIsNone(self._judge("cd /tmp && ls").doubts or None)

    def test_cdpath_in_any_spelling_loses_the_folder(self):
        r = self.root
        t = " && cp /tmp/x.csv y.csv"
        self._both(self.asks, "CDPATH=%s/data; cd real" % r + t, "CDPATH+=:%s/data; cd real" % r + t,
                   "export CDPATH=%s/data; cd real" % r + t, "cdpath=(%s/data); cd real" % r + t,
                   "set -A cdpath %s/data; cd real" % r + t, "typeset -a cdpath; cdpath+=%s/data; cd real" % r + t,
                   "CdPath=%s/data cd real" % r + t)
        for text in ("CDPATH=/x cd d && ls", "CdPath=/x cd d && ls","cdpath=(/x); cd d && ls", "set -A cdpath /x; cd d && ls", "CDPATH+=:/x; cd d && ls"):
            self.assertTrue(self._judge(text).unsure, text)

    def test_the_folder_moves_only_on_one_certain_cd(self):
        j = self._judge
        for text in ("cd sub && ls", "cd -- sub && ls", "X=1 cd sub && ls", "cd /tmp && ls", "cd ../data && ls && ls", "cd sub && a | b && c"):
            self.assertFalse(j(text).unsure, text)
        for text in ("builtin cd sub && ls", "command cd sub && ls", "/usr/bin/cd sub && ls", "sudo cd sub && ls", "env cd sub && ls",
                     "cd sub; ls", "cd sub\nls", "cd sub || ls", "cd sub & ls", "cd sub | ls", "cd sub", "true && cd sub && ls",
                     "cd -P sub && ls", "cd -L sub && ls", "cd -- -x && ls", "cd + && ls", "cd +1 && ls", "cd - && ls",
                     "cd a b && ls", "cd && ls", "cd ~ && ls", "cd ~/x && ls", "cd ~D && ls", "cd $D && ls", "cd `pwd` && ls",
                     "cd $(pwd) && ls", "cd s* && ls", "cd s? && ls", "cd s[a] && ls", "cd {a,b} && ls", "cd (a) && ls",
                     "cd a!b && ls", "cd sub && ls; ls", "cd sub && ls || ls", "cd sub && ls & ls", "cd sub && ls\nls",
                     "cd sub && cd sub2 && ls", "cd sub && pushd sub2 && ls", "cd sub && popd && ls", "pushd sub && ls",
                     "popd && ls", "chdir sub && ls", "! cd sub && ls", "(cd sub && ls)", "{ cd sub && ls; }", "( cd sub ) && ls",
                     "f() { cd sub; }; f && ls", "if cd sub; then ls; fi", "while cd sub; do ls; done", "for x in a; do cd sub; done",
                     "case a in a) cd sub;; esac", "ls && cd sub && ls", "ls; cd sub && ls", "ls || cd sub && ls", "echo $(cd sub) && ls",
                     "echo \"$(cd sub)\" && ls", "echo `cd sub` && ls", "cd sub && echo cd", "cd sub >/dev/null && ls",
                     "cd sub < /dev/null && ls", "cd sub &>/dev/null && ls", "cd sub &&\nls"):
            self.assertTrue(j(text).unsure, text)

    def test_shape_changes_never_pass_when_the_ask_would_drop(self):
        # the base guard asks because data/real appears; the reader may drop that ask only if the folder is sure
        self._both(self.asks, "builtin cd /tmp && cp /tmp/y.csv data/real/", "cd /tmp; cp /tmp/y.csv data/real/", "true && cd /tmp && cp /tmp/y.csv data/real/",
                   "cd -P /tmp && cp /tmp/y.csv data/real/", "cd /tmp && cp /tmp/y.csv data/real/; true", "cd /tmp && cp /tmp/y.csv data/real/ || true",
                   "cd /tmp && cd /tmp && cp /tmp/y.csv data/real/", "(cd build && make) && cp /tmp/y.csv data/real/",
                   "cd /tmp & cp /tmp/y.csv data/real/", "command cd /tmp && cp /tmp/y.csv data/real/", "pushd /tmp && cp /tmp/y.csv data/real/")
        self._both(self.asks, "builtin cd data && cp /tmp/x.csv real/y.csv", "cd data; cp /tmp/x.csv real/y.csv",
                   "true && cd data && cp /tmp/x.csv real/y.csv", "cd -P data && cp /tmp/x.csv real/y.csv")

    def test_a_lost_folder_ask_has_its_own_memory_key(self):
        r, sess = self.root, "sess-r7-key-0001"
        lost, other = "builtin cd /tmp && cp /tmp/y.csv data/real/", "cp /tmp/x.csv $NOPE_R7/real/y.csv"
        keys = self._check(lost, sess)[1]
        self.assertIn("lost:" + sl._fold(r), keys)
        self.assertEqual(self._check(other, sess)[0][0], "ask")
        self._yes(other, sess)                                    # a yes to an unrelated ambiguous ask ...
        self.assertEqual(self._check(lost, sess)[0][0], "ask")    # ... never covers the lost folder
        self._yes(lost, sess)
        self.assertIsNone(self._check(lost, sess)[0])
        self.assertEqual(self._check("cd /tmp; cp /tmp/y.csv data/real/", "sess-r7-key-0002")[0][0], "ask")
        # and the lost key is kept next to the other classes when a command has both
        both = self._check("cd /tmp; cp /tmp/x.csv $NOPE_R7/real/y.csv", "sess-r7-key-0003")[1]
        self.assertIn("lost:" + sl._fold(r), both)

    def test_base_guard_reads_a_cd_word_with_a_glob_or_a_tilde_name(self):
        for args in (["d?ta"], ["d*"], ["d[a]ta"], ["~D"], ["~D/x"], ["~root"], ["a!b"], ["{a,b}"]):
            self.assertTrue(sl._cd_loses_folder("cd", args), args)
        for args in (["/tmp"], ["docs"], ["~/x"], ["~"], [], ["-P", "/tmp"]):
            self.assertFalse(sl._cd_loses_folder("cd", args), args)
        patterns = sl.load_project_config(self.project).get("protected_paths") or []
        got = sl._strict_extras("cd d?ta && cp /tmp/x.csv real/y.csv", self.project, patterns, self.root, "r %s %s")
        self.assertIsNotNone(got)

    def test_harmless_rows_still_pass_with_and_without_the_standing_yes(self):
        r = self.root
        for d in ("sub", "build", "src2"):
            os.makedirs(os.path.join(r, d), exist_ok=True)
        for grant in (False, True):
            if grant:
                self.grant()
            self.passes("cd sub && cp /tmp/a.csv b.csv", "(cd build && make)", "cd /tmp && ls", "cp -R src2/ dst/",
                        "rsync -a src2/ /tmp/out/", "cd sub && cp /tmp/a.csv b.csv && ls data/real",
                        "cd /tmp && ls %s/data/real" % r, "cd /tmp && cp /tmp/y.csv data/real/", "cd sub && ls && ls data/real",
                        "rsync -a --backup-dir=bak src2/ /tmp/out/", cwd=r)


if __name__ == "__main__":
    unittest.main()
