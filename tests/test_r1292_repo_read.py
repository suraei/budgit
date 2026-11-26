"""R-1292: the helper-safe repo read command (`budgit repo-read`), its shared validator and the guard side.

Live-sandbox tests need macOS and /usr/bin/sandbox-exec; elsewhere they skip with a reason line. Every program run goes
through subprocess argv lists. Temp repos are built with the real git producers. Control characters are built with chr().
Every "no program ran" test has a control: the plain program, outside the sandbox and without overrides, DOES create the marker.
"""
import os
import pty
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import repo_read as rr  # noqa: E402
import studiolib as sl  # noqa: E402

REAL_ROOTS = getattr(rr, "registered_roots", None)
GIT = "/usr/bin/git"
CAN_RUN = sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec")
SKIP = "R-1292 live-sandbox test needs macOS and /usr/bin/sandbox-exec (other systems get a clean refusal)"
P = rr.PREFIX
BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")


def _main_checkout():
    """The checkout this worktree belongs to, found from git's common dir at run time ('' when it cannot be found)."""
    p = subprocess.run([GIT, "-C", ROOT, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True)
    common = p.stdout.strip() if p.returncode == 0 else ""
    return os.path.dirname(common) if os.path.basename(common) == ".git" else ""


STUDIO_REPO = _main_checkout()
STUDIO_HOOK = os.path.join(STUDIO_REPO, ".git", "hooks", "commit-msg")
PLAIN_ENV = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}


def git(args, cwd, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.com"}
    env.update(env_extra or {})
    p = subprocess.run([GIT] + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError("setup %s: %s" % (args, p.stderr))
    return p.stdout


def write(path, text, mode=None):
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = os.path.realpath(tempfile.mkdtemp(prefix="r1292-"))
        cls.addClassCleanup(shutil.rmtree, cls.base, True)
        cls.markers = os.path.join(cls.base, "markers")
        os.mkdir(cls.markers)
        cls.n = 0
        had, old = hasattr(rr, "registered_roots"), getattr(rr, "registered_roots", None)
        rr.registered_roots = lambda: [cls.base]          # budgit's registry, injected: only this temp folder is a project

        def restore():
            if had:
                rr.registered_roots = old
            else:
                del rr.registered_roots
        cls.addClassCleanup(restore)

    def outside_dir(self):
        d = os.path.realpath(tempfile.mkdtemp(prefix="r1292-out-"))
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def repo(self, files, attrs=None):
        type(self).n += 1
        d = os.path.join(self.base, "r%d" % self.n)
        os.mkdir(d)
        git(["init", "-q"], d)
        for k, v in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, k)), exist_ok=True)
            write(os.path.join(d, k), v)
        if attrs:
            write(os.path.join(d, ".gitattributes"), attrs)
        git(["add", "-A"], d)
        git(["commit", "-q", "-m", "first"], d)
        return d

    def dirty(self, repo, name, text):
        """Same-size change with a new mtime: git must re-hash the file, so a clean filter would run."""
        path = os.path.join(repo, name)
        write(path, text)
        os.utime(path, (2000000000, 2000000000))

    def marker(self, name):
        return os.path.join(self.markers, name)

    def clear(self, *names):
        for n in names:
            if os.path.exists(self.marker(n)):
                os.remove(self.marker(n))

    def script(self, name, body="cat"):
        p = os.path.join(self.base, name + ".sh")
        write(p, "#!/bin/sh\ntouch %s\n%s\n" % (self.marker(name), body), 0o755)
        return p


class Validator(unittest.TestCase):
    """The shared validator, on every platform."""

    def refused(self, args, text=None):
        with self.assertRaises(rr.Refused) as c:
            rr.parse(args)
        if text:
            self.assertIn(text, str(c.exception))

    def test_every_refused_flag_and_abbreviation(self):
        for verb in ("status", "log", "diff", "show", "merge-base"):
            for flag in ("-c", "--output=x", "--outp=x", "-O", "-Ofile", "--ext-diff", "--textconv", "--remerge-diff",
                         "--diff-merges=on", "--submodule", "--ignore-submodules", "--show-signature", "--no-index", "-C", "-M",
                         "-B", "-C5", "--exec-path", "--work-tree=/", "--git-dir=/", "--stat-w", "--onelin", "--max", "--ext"):
                self.refused([verb, flag], None)
        self.refused(["config", "--list"])
        self.refused(["commit"])
        self.refused([])

    def test_format_G_placeholders(self):
        for fmt in ("%G?", "%+G?", "%-G?", "% G?", "%GG", "%GS", "%GK", "%h%G?", "%(describe)", "%h %(trailers)"):
            self.refused(["log", "--format=" + fmt])
            self.refused(["show", "--pretty=" + fmt])
        for fmt in ("%h %s", "oneline", "short", "medium", "full", "fuller", "reference", "email", "raw", "tformat:%H"):
            rr.parse(["log", "--format=" + fmt])

    def test_repo_pretty_alias_refused(self):
        self.refused(["log", "--pretty=myalias"])

    def test_revisions_strict(self):
        for ok in ("HEAD", "HEAD~3", "HEAD^", "a1b2c3d", "main..feature", "main...feature", "@{u}", "HEAD:README.md", "origin/main"):
            rr.parse(["log", ok])
        for bad in ("-x", "--evil", "/etc/passwd", "$(id)", "a b", "x;y", ".hidden"):
            self.refused(["log", bad])

    def test_positional_with_a_traversal_segment_is_refused(self):
        for bad in ("docs/../../../etc/hosts", "../x", "a/..", "HEAD:../x", "docs/.."):
            for verb in ("diff", "log", "show"):
                self.refused([verb, bad])
        self.refused(["diff", "docs/../../o1", "docs/../../o2"])
        self.refused(["diff", "/etc/hosts", "/etc/hosts"])

    def test_wrapper_always_passes_double_dash_after_the_revisions(self):
        for verb in ("log", "diff", "show"):
            argv = rr.git_argv("/usr/bin/git", verb, ["a1b2c3d", "HEAD"])
            self.assertEqual(argv[-1], "--", argv)
        argv = rr.git_argv("/usr/bin/git", "diff", ["HEAD"], ["x.txt"])
        self.assertEqual(argv[-2:], ["--", "x.txt"])

    def test_paths_only_after_double_dash(self):
        rr.parse(["log", "--", "src/a.py"])
        self.assertEqual(rr.parse(["log", "--", "src/a.py"])[3], ["src/a.py"])
        self.refused(["status", "foo"])

    def test_repo_goes_before_verb(self):
        self.assertEqual(rr.parse(["--repo", "/x", "status"])[0], "/x")
        self.refused(["status", "--repo", "/x"])

    def test_allowed_forms(self):
        for args in (["status", "-s", "-b"], ["status", "--porcelain=v2"], ["status", "-uno"], ["log", "-n", "5", "--oneline"],
                     ["log", "-5"], ["log", "--grep=a b", "--since=1.day"], ["diff", "--cached", "--stat"], ["diff", "-U3"],
                     ["show", "HEAD", "--name-only"], ["merge-base", "--is-ancestor", "a1b2", "HEAD"], ["log", "-Sfoo", "-p"]):
            rr.parse(args)

    def test_wrapper_adds_the_safety_flags(self):
        argv = rr.git_argv("/usr/bin/git", *rr.parse(["diff"])[1:3])
        self.assertIn("--no-pager", argv)
        for f in ("--no-ext-diff", "--no-textconv", "--ignore-submodules=all"):
            self.assertIn(f, argv)

    def test_off_macos_refuses_and_runs_nothing(self):
        old = rr.IS_MAC
        rr.IS_MAC = False
        try:
            rc, out, err = rr.execute(["status"], os.getcwd())
        finally:
            rr.IS_MAC = old
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith(P), err)

    def test_the_registry_reads_the_portfolio_paths(self):
        import portfolio
        base = os.path.realpath(tempfile.gettempdir())
        old = portfolio.entries_by_path
        portfolio.entries_by_path = lambda: {base: {"path": base}}
        try:
            self.assertEqual(REAL_ROOTS(), [base])
        finally:
            portfolio.entries_by_path = old


class Guard(Base):
    """The guard side: one shared validator, a helper only."""

    def helper(self, command, project=None):
        return sl.real_data_check("Bash", {"command": command}, project or self.base, cwd=self.base, agent_id="agent-1")[0]

    def test_wrapped_status_allowed_for_a_helper_in_protected_too(self):
        d = self.helper(BUDGIT + " repo-read status")
        self.assertIsNone(d, d)

    def test_chained_and_unsafe_forms_refused(self):
        nl, bq, bs = chr(10), chr(96), chr(92)
        for tail in ("status; id", "status && id", "status | cat", "status > f", "status &", "status $(id)", "status " + bq + "id" + bq,
                     'log --grep="$(id)"', 'log --grep="' + bq + 'id' + bq + '"', 'log --grep="${IFS}"', "log --grep=a" + bs + "b",
                     "log --grep=a!b", "log --grep=a" + nl + "b", 'log "x"', "log ~x", "log =x", "status {a,b}", "status *"):
            d = self.helper(BUDGIT + " repo-read " + tail)
            self.assertTrue(d and d[0] in ("deny", "ask"), "%r -> %r" % (tail, d))

    def test_single_quoted_semicolon_is_one_word(self):
        self.assertIsNone(self.helper(BUDGIT + " repo-read log --grep='a;b'"))

    def test_refused_args_carry_the_rule_name_first(self):
        for tail in ("-c core.pager=x status", "log --output=x", "diff --ext-diff", "status --repo x", "diff docs/../../a docs/../../b"):
            d = self.helper(BUDGIT + " repo-read " + tail)
            self.assertEqual(d[0], "deny", tail)
            self.assertTrue(d[1].startswith(P), d[1])

    def test_bare_program_call_is_not_allowed_for_a_helper(self):
        self.assertIsNone(sl._helper_budgit_read("git status", self.base), "a bare program call stays with the old judgement")

    def test_a_non_helper_is_unchanged(self):
        d = sl.real_data_check("Bash", {"command": BUDGIT + " repo-read log --output=x"}, self.base, cwd=self.base)[0]
        self.assertFalse(d and d[1].startswith(P))

    def test_budgit_closed_read_set_for_helpers(self):
        for tail in ("status", "help", "ledger list", "ledger show R-12"):
            self.assertIsNone(self.helper(BUDGIT + " " + tail), tail)
        for tail in ("ledger done R-1 x", "status --project /x", "ledger show R-1 --file x", "update"):
            self.assertIsNone(sl._helper_budgit_read(BUDGIT + " " + tail, self.base), tail)


class Hook(unittest.TestCase):
    def test_hook_passes_the_helper_id_to_the_real_data_guard(self):
        text = open(os.path.join(ROOT, "plugin", "scripts", "hook.py")).read()
        self.assertIn('agent_id=inp.get("agent_id")', text)


@unittest.skipUnless(CAN_RUN, SKIP)
class Live(Base):
    def run_rr(self, repo, args):
        return rr.execute(["--repo", repo] + args, repo)

    def plain(self, repo, args, env_extra=None):
        env = dict(PLAIN_ENV, HOME=self.base)
        env.update(env_extra or {})
        return subprocess.run([GIT] + args, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def raw(self, repo, args, extra_env=None, drop=()):
        """The program under the bare profile: scratch env, NO overrides, NO extra flags."""
        env = rr.base_env(tempfile.mkdtemp(dir=self.base))
        env.update(extra_env or {})
        for k in drop:
            env.pop(k, None)
        return rr.sandbox_run([rr.program()] + args, repo, env, rr.build_profile(rr.program()))

    def tty_run(self, argv, repo, env):
        """A pager starts only when stdout is a terminal: give the program a pty."""
        master, slave = pty.openpty()
        try:
            subprocess.run(argv, cwd=repo, env=dict(env, TERM="xterm"), stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.DEVNULL, timeout=30)
        finally:
            os.close(master)
            os.close(slave)

    def no_program_ran(self, names, repo, rr_args, raw_args, plain_args, raw_env=None, plain_env=None, drop=(), tty=False):
        """repo-read and the bare profile leave every marker absent; the plain program, same repo, creates every one."""
        self.clear(*names)
        self.run_rr(repo, rr_args)
        for n in names:
            self.assertFalse(os.path.exists(self.marker(n)), "repo-read ran a program: " + n)
        if tty:
            self.tty_run(["/usr/bin/sandbox-exec", "-p", rr.build_profile(rr.program()), rr.program()] + raw_args, repo,
                         rr.base_env(tempfile.mkdtemp(dir=self.base)))
        else:
            self.raw(repo, raw_args, raw_env, drop)
        for n in names:
            self.assertFalse(os.path.exists(self.marker(n)), "the bare profile let a program run: " + n)
        if tty:
            self.tty_run([GIT] + plain_args, repo, dict(PLAIN_ENV, HOME=self.base))
        else:
            self.plain(repo, plain_args, plain_env)
        for n in names:
            self.assertTrue(os.path.exists(self.marker(n)), "control: the plain program did not run %s, so the test could not fail" % n)

    # spike checks, folded in
    def test_self_check_passes_and_reports_program(self):
        info = rr.self_check()
        self.assertTrue(info["program"].startswith("/"))
        self.assertTrue(info["version"])

    def test_spike_sandbox_exec_allow_default_runs_true(self):
        p = subprocess.run(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"], capture_output=True)
        self.assertEqual(p.returncode, 0)

    def test_spike_exec_of_true_inside_profile_is_denied_rc71(self):
        prof = rr.build_profile(rr.program())
        p = subprocess.run(["/usr/bin/sandbox-exec", "-p", prof, "/usr/bin/true"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 71)
        self.assertIn("execvp() of", p.stderr)
        self.assertNotIn("sandbox_apply", p.stderr)

    def test_spike_read_verbs_work_and_libexec_is_not_in_the_profile(self):
        d = self.repo({"a.txt": "one\n"})
        write(os.path.join(d, "a.txt"), "two\n")
        self.assertNotIn("git-core", rr.build_profile(rr.program()))
        for args, needle in ((["status", "--short"], " M a.txt"), (["log", "-p", "-1"], "+one"), (["diff"], "+two"),
                             (["show", "HEAD"], "+one")):
            rc, out, err = self.run_rr(d, args)
            self.assertEqual(rc, 0, err)
            self.assertIn(needle, out)

    # one marker test per program kind, each with a control
    def test_filter_clean_marker(self):
        d = self.repo({"f.txt": "a\n"}, "f.txt filter=mf\n")
        git(["config", "filter.mf.clean", self.script("clean")], d)
        self.dirty(d, "f.txt", "b\n")
        self.no_program_ran(["clean"], d, ["status"], ["status"], ["status"])

    def test_filter_named_with_equals_x_eq_y(self):
        d = self.repo({"f.txt": "a\n"}, "f.txt filter=x=y\n")
        git(["config", "filter.x=y.clean", self.script("xy")], d)
        self.dirty(d, "f.txt", "b\n")
        self.no_program_ran(["xy"], d, ["status"], ["status"], ["status"])

    def test_filter_process_protocol(self):
        d = self.repo({"f.txt": "a\n"}, "f.txt filter=mp\n")
        git(["config", "filter.mp.process", self.script("proc")], d)
        self.dirty(d, "f.txt", "b\n")
        self.no_program_ran(["proc"], d, ["status"], ["status"], ["status"])

    def test_textconv_leaves_no_marker_and_no_converted_output(self):
        d = self.repo({"f.txt": "alpha\n"}, "f.txt diff=md\n")
        git(["config", "diff.md.textconv", self.script("tc", 'echo CONVERTED; cat "$1"')], d)
        write(os.path.join(d, "f.txt"), "beta\n")
        self.no_program_ran(["tc"], d, ["diff"], ["--no-pager", "diff"], ["diff"])
        rc, out, err = self.run_rr(d, ["diff"])
        self.assertNotIn("CONVERTED", out)
        self.assertIn("+beta", out)
        self.assertIn("CONVERTED", self.plain(d, ["diff"]).stdout)

    def test_textconv_whose_command_is_the_program_path_writes_nothing(self):
        d = self.repo({"f.txt": "alpha\n"}, "f.txt diff=md\n")
        git(["config", "diff.md.textconv", "%s hash-object -w" % rr.program()], d)
        write(os.path.join(d, "f.txt"), "beta\n")
        count = lambda: self.plain(d, ["count-objects"]).stdout
        before = count()
        self.raw(d, ["--no-pager", "diff"])
        self.assertEqual(count(), before, "the bare profile let the program write an object")
        self.run_rr(d, ["diff"])
        self.assertEqual(count(), before)
        self.plain(d, ["diff"])
        self.assertNotEqual(count(), before, "control: the plain program did not write, so the test could not fail")

    def test_external_diff_and_driver_command(self):
        d = self.repo({"f.txt": "alpha\n", "g.txt": "gamma\n"}, "f.txt diff=md\n")
        git(["config", "diff.md.command", self.script("drv")], d)
        git(["config", "diff.external", self.script("ext")], d)
        write(os.path.join(d, "f.txt"), "beta\n")
        write(os.path.join(d, "g.txt"), "delta\n")
        self.no_program_ran(["drv", "ext"], d, ["diff"], ["--no-pager", "diff"], ["diff"])

    def test_merge_driver_config_path_and_refused_flag(self):
        d = self.repo({"m.txt": "1\n2\n3\n"}, "m.txt merge=mdrv\n")
        git(["checkout", "-q", "-b", "side"], d)
        write(os.path.join(d, "m.txt"), "1\n2\n3\nside\n")
        git(["commit", "-qam", "side"], d)
        git(["checkout", "-q", "-"], d)
        write(os.path.join(d, "m.txt"), "top\n1\n2\n3\n")
        git(["commit", "-qam", "main"], d)
        git(["merge", "-q", "--no-edit", "side"], d)
        git(["config", "merge.mdrv.driver", self.script("mrg") + " %A"], d)
        rc, out, err = self.run_rr(d, ["show", "--remerge-diff", "HEAD"])
        self.assertTrue(err.startswith(P) and rc != 0)
        self.no_program_ran(["mrg"], d, ["show", "HEAD"], ["--no-pager", "show", "--remerge-diff", "HEAD"],
                            ["--no-pager", "show", "--remerge-diff", "HEAD"])

    def test_pager_in_config_worktree(self):
        d = self.repo({"a.txt": "one\n"})
        git(["config", "extensions.worktreeConfig", "true"], d)
        git(["config", "core.pager", self.script("pgr")], d)
        self.no_program_ran(["pgr"], d, ["log", "-p"], ["log"], ["log"], tty=True)

    def test_fsmonitor_in_config_worktree(self):
        d = self.repo({"a.txt": "one\n"})
        git(["config", "extensions.worktreeConfig", "true"], d)
        git(["config", "--worktree", "core.fsmonitor", self.script("fsm")], d)
        self.dirty(d, "a.txt", "two\n")
        self.no_program_ran(["fsm"], d, ["status"], ["status"], ["status"])

    def test_fsmonitor_true_without_the_override(self):
        d = self.repo({"a.txt": "one\n"})
        git(["config", "core.fsmonitor", self.script("fsmtrue")], d)
        self.dirty(d, "a.txt", "two\n")
        self.no_program_ran(["fsmtrue"], d, ["status"], ["status"], ["status"])

    def test_gpg_x509_refused_flag_and_config(self):
        d = self.repo({"a.txt": "one\n"})
        tree = git(["rev-parse", "HEAD^{tree}"], d).strip()
        parent = git(["rev-parse", "HEAD"], d).strip()
        body = ("tree %s\nparent %s\nauthor t <t@example.com> 1700000000 +0000\ncommitter t <t@example.com> 1700000000 +0000\n"
                "gpgsig -----BEGIN SIGNED MESSAGE-----\n AAAA\n -----END SIGNED MESSAGE-----\n\nsigned\n") % (tree, parent)
        write(os.path.join(self.base, "signed.txt"), body)
        git(["update-ref", "HEAD", git(["hash-object", "-t", "commit", "-w", "--literally", os.path.join(self.base, "signed.txt")], d).strip()], d)
        git(["config", "gpg.format", "x509"], d)
        git(["config", "gpg.x509.program", self.script("gpgx")], d)
        rc, out, err = self.run_rr(d, ["log", "--format=%G?"])
        self.assertTrue(rc != 0 and err.startswith(P) and out == "")
        self.no_program_ran(["gpgx"], d, ["log", "-1"], ["--no-pager", "log", "--show-signature", "-1"],
                            ["--no-pager", "log", "--show-signature", "-1"])

    def test_ext_promisor_remote_runs_nothing_and_touches_no_network(self):
        d = self.repo({"a.txt": "one\n"})
        blob = git(["rev-parse", "HEAD:a.txt"], d).strip()
        git(["config", "core.repositoryformatversion", "1"], d)
        git(["config", "extensions.partialClone", "origin"], d)
        git(["config", "remote.origin.promisor", "true"], d)
        git(["config", "remote.origin.url", "ext::" + self.script("promisor", "exit 0")], d)
        loose = os.path.join(d, ".git", "objects", blob[:2], blob[2:])
        os.chmod(loose, 0o644)
        os.remove(loose)                                    # the object is missing: a lazy fetch would go to the promisor
        allow = {"GIT_ALLOW_PROTOCOL": "ext"}
        self.no_program_ran(["promisor"], d, ["show", "HEAD:a.txt"], ["--no-pager", "show", "HEAD:a.txt"], ["--no-pager", "show", "HEAD:a.txt"],
                            raw_env=allow, plain_env=allow, drop=("GIT_NO_LAZY_FETCH",))

    def listener(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        srv.settimeout(3)
        hits = []

        def accept():
            try:
                c, _ = srv.accept()
                hits.append(1)
                c.close()
            except OSError:
                pass
        t = threading.Thread(target=accept)
        t.start()
        return srv.getsockname()[1], hits, t, srv

    def test_network_listener_gets_no_connection_and_the_control_does(self):
        d = self.repo({"a.txt": "one\n"})
        port, hits, t, srv = self.listener()
        self.raw(d, ["ls-remote", "git://127.0.0.1:%d/x" % port])
        t.join()
        srv.close()
        self.assertEqual(hits, [], "the bare profile let a connection through")
        port, hits, t, srv = self.listener()
        self.plain(d, ["ls-remote", "git://127.0.0.1:%d/x" % port])
        t.join()
        srv.close()
        self.assertEqual(hits, [1], "control: the plain program did not connect, so the test could not fail")

    def test_embedded_gitlink_with_and_without_repo(self):
        outer = self.repo({"a.txt": "one\n"})
        inner = os.path.join(outer, "inner")
        os.mkdir(inner)
        git(["init", "-q"], inner)
        write(os.path.join(inner, "x.txt"), "x\n")
        git(["add", "-A"], inner)
        git(["commit", "-q", "-m", "in"], inner)
        git(["config", "core.fsmonitor", self.script("nested")], inner)
        git(["config", "core.pager", self.script("nestedpg")], inner)
        git(["add", "inner"], outer)
        git(["commit", "-q", "-m", "gitlink"], outer)
        self.dirty(inner, "x.txt", "y\n")
        write(os.path.join(outer, "a.txt"), "two\n")
        self.clear("nested", "nestedpg")
        self.plain(inner, ["status"])
        self.assertTrue(os.path.exists(self.marker("nested")), "control: the nested config did not run unsandboxed")
        self.tty_run([GIT, "log"], inner, dict(PLAIN_ENV, HOME=self.base))
        self.assertTrue(os.path.exists(self.marker("nestedpg")), "control: the nested pager did not run unsandboxed")
        self.clear("nested", "nestedpg")
        for where in (inner, outer):
            for verb in (["status"], ["diff"], ["log", "-p"]):
                rr.execute(["--repo", where] + verb, outer)
                rr.execute(verb, where)
        rc, out, err = rr.execute(["--repo", outer, "status"], inner)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(self.marker("nested")), "nested fsmonitor ran")
        self.assertFalse(os.path.exists(self.marker("nestedpg")), "nested pager ran")

    # scope
    def test_diff_traversal_pair_refused_and_the_plain_program_would_print_the_outside_files(self):
        d = self.repo({"docs/x.txt": "x\n"})
        write(os.path.join(self.base, "o1.txt"), "one\n")
        write(os.path.join(self.base, "o2.txt"), "two\n")
        args = ["diff", "docs/../../o1.txt", "docs/../../o2.txt"]
        rc, out, err = rr.execute(["--repo", d] + args, d)
        self.assertTrue(rc != 0 and out == "" and err.startswith(P), (rc, out, err))
        control = self.plain(d, args)
        self.assertIn("+two", control.stdout, "control: plain no-index did not print the outside file, so the test could not fail")

    def test_diff_with_an_absolute_or_existing_path_is_refused(self):
        d = self.repo({"a.txt": "one\n", "b.txt": "two\n"})
        outside = self.outside_dir()
        write(os.path.join(outside, "p"), "p\n")
        for args in (["diff", "/etc/hosts", "/etc/hosts"], ["diff", os.path.join(outside, "p"), os.path.join(outside, "p")],
                     ["diff", "a.txt", "b.txt"]):
            rc, out, err = rr.execute(["--repo", d] + args, d)
            self.assertTrue(rc != 0 and out == "" and err.startswith(P), (args, rc, out, err))

    def test_a_marker_folder_with_a_repo_is_not_a_known_project(self):
        spoof = self.outside_dir()
        os.mkdir(os.path.join(spoof, ".studio"))
        os.mkdir(os.path.join(spoof, ".budgit"))
        git(["init", "-q"], spoof)
        rc, out, err = rr.execute(["--repo", spoof, "status"], spoof)
        self.assertTrue(rc != 0 and err.startswith(P) and "project" in err, err)

    def test_a_registered_root_and_its_linked_worktree_pass(self):
        a = self.repo({"a.txt": "one\n"})
        wt = os.path.join(self.outside_dir(), "wt")           # outside the registered root: only the worktree link vouches for it
        git(["worktree", "add", "-q", wt, "-b", "wtb"], a)
        write(os.path.join(wt, "n.txt"), "n\n")
        for where in (a, wt):
            rc, out, err = rr.execute(["--repo", where, "status", "--short"], where)
            self.assertEqual(rc, 0, err)
        self.assertIn("n.txt", rr.execute(["--repo", wt, "status", "--short"], wt)[1])

    def test_a_planted_commondir_does_not_borrow_a_registered_repo(self):
        a = self.repo({"a.txt": "one\n"})
        planted = self.outside_dir()
        git(["init", "-q"], planted)
        write(os.path.join(planted, ".git", "commondir"), os.path.join(a, ".git") + "\n")
        rc, out, err = rr.execute(["--repo", planted, "status"], planted)
        self.assertTrue(rc != 0 and err.startswith(P), err)

    def test_outside_a_registered_project_is_refused(self):
        out_dir = self.outside_dir()
        git(["init", "-q"], out_dir)
        rc, out, err = rr.execute(["--repo", out_dir, "status"], out_dir)
        self.assertTrue(rc != 0 and err.startswith(P), err)

    def test_symlink_out_of_project_is_resolved_before_the_test(self):
        out_dir = self.outside_dir()
        git(["init", "-q"], out_dir)
        link = os.path.join(self.base, "sneaky")
        os.symlink(out_dir, link)
        rc, out, err = rr.execute(["--repo", link, "status"], self.base)
        self.assertTrue(rc != 0 and err.startswith(P), err)

    def test_diff_outside_any_repo_is_refused(self):
        out_dir = self.outside_dir()
        rc, out, err = rr.execute(["--repo", out_dir, "diff"], out_dir)
        self.assertTrue(rc != 0 and err.startswith(P), err)

    def test_path_outside_toplevel_refused(self):
        d = self.repo({"a.txt": "one\n"})
        rc, out, err = self.run_rr(d, ["log", "--", "../../etc/passwd"])
        self.assertTrue(rc != 0 and err.startswith(P), err)

    # output and limits
    def test_nonzero_rc_discards_stdout_and_quotes_stderr(self):
        d = self.repo({"a.txt": "one\n"})
        rc, out, err = self.run_rr(d, ["log", "nosuchrev1"])
        self.assertNotEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith(P), err)

    def test_merge_base_is_ancestor_false_is_not_a_refusal(self):
        d = self.repo({"a.txt": "one\n"})
        git(["commit", "-q", "--allow-empty", "-m", "two"], d)
        rc, out, err = self.run_rr(d, ["merge-base", "--is-ancestor", "HEAD", "HEAD~1"])
        self.assertEqual((rc, out), (1, ""))
        self.assertFalse(err.startswith(P))

    def test_output_cap_is_a_refusal(self):
        d = self.repo({"a.txt": "x" * 4000 + "\n"})
        old, check = rr.MAX_OUT, rr.self_check
        rr.MAX_OUT, rr.self_check = 100, lambda: {"program": rr.program(), "version": ""}
        try:
            rc, out, err = self.run_rr(d, ["show", "HEAD"])
        finally:
            rr.MAX_OUT, rr.self_check = old, check
        self.assertTrue(rc != 0 and out == "" and "output" in err and err.startswith(P), err)

    def _with_fake_final_run(self, d, final):
        old, check, calls = rr.sandbox_run, rr.self_check, []
        rr.self_check = lambda: {"program": rr.program(), "version": ""}

        def fake(argv, cwd, env, profile):
            calls.append(argv)
            return old(argv, cwd, env, profile) if len(calls) == 1 else final()     # the repo check runs for real
        rr.sandbox_run = fake
        try:
            return self.run_rr(d, ["log", "-p"]), len(calls)
        finally:
            rr.sandbox_run, rr.self_check = old, check

    def test_timeout_in_the_real_run_is_a_refusal(self):
        def final():
            raise rr.Refused("timed out after %s s" % rr.TIMEOUT_S)
        (rc, out, err), n = self._with_fake_final_run(self.repo({"a.txt": "one\n"}), final)
        self.assertEqual(n, 2)
        self.assertTrue(rc != 0 and out == "" and "timed out" in err and err.startswith(P), err)

    def test_a_hung_child_is_killed_with_its_process_group(self):
        pidfile = os.path.join(self.base, "child.pid")
        old = rr.TIMEOUT_S
        rr.TIMEOUT_S = 1.0
        try:
            with self.assertRaises(rr.Refused) as c:
                rr.sandbox_run(["/bin/sh", "-c", "sleep 60 & echo $! > %s; wait" % pidfile], self.base, {"PATH": "/usr/bin:/bin"},
                               "(version 1)(allow default)")
        finally:
            rr.TIMEOUT_S = old
        self.assertIn("timed out", str(c.exception))
        pid = int(open(pidfile).read().strip())
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_rc0_with_cannot_exec_on_stderr_is_a_refusal_quoting_the_first_line(self):
        final = lambda: (0, "partial output", "error: cannot exec 'x': Operation not permitted\nsecond line")
        (rc, out, err), n = self._with_fake_final_run(self.repo({"a.txt": "one\n"}), final)
        self.assertTrue(rc != 0 and out == "" and err == P + "error: cannot exec 'x': Operation not permitted", err)

    def test_failed_probe_refuses_and_runs_nothing(self):
        d = self.repo({"a.txt": "one\n"})
        old = rr.build_profile
        rr.build_profile = lambda program: "(version 1)(allow default)"      # the deny probe now sees rc 0
        try:
            rc, out, err = self.run_rr(d, ["status"])
        finally:
            rr.build_profile = old
        self.assertTrue(rc != 0 and out == "" and "sandbox cannot apply" in err and err.startswith(P), err)

    @unittest.skipUnless(os.path.exists(STUDIO_HOOK), "the studio commit-msg hook file is not on this machine")
    def test_real_shape_repo_matches_a_plain_status(self):
        origin = self.repo({"a.txt": "one\n"})
        d = os.path.join(self.base, "clone")
        git(["clone", "-q", "--shared", origin, d], self.base)
        git(["config", "extensions.worktreeConfig", "true"], d)
        git(["worktree", "add", "-q", os.path.join(self.base, "wt"), "-b", "wtb"], d)
        os.makedirs(os.path.join(d, ".git", "hooks"), exist_ok=True)
        shutil.copy(STUDIO_HOOK, os.path.join(d, ".git", "hooks", "commit-msg"))
        write(os.path.join(d, "untracked.txt"), "u\n")
        write(os.path.join(self.base, "wt", "new.txt"), "n\n")
        for where, needle in ((d, "untracked.txt"), (os.path.join(self.base, "wt"), "new.txt")):
            rc, out, err = rr.execute(["--repo", where, "status", "--short"], where)
            plain = self.plain(where, ["status", "--short"], {"GIT_OPTIONAL_LOCKS": "0"}).stdout
            self.assertEqual((rc, out), (0, plain), err)
            self.assertIn(needle, out)

    def test_the_real_studio_repos_read_only_smoke_and_timing(self):
        wheres = [ROOT] + ([STUDIO_REPO] if os.path.isdir(STUDIO_REPO) else [])
        saved = rr.registered_roots
        rr.registered_roots = lambda: wheres
        self.addCleanup(setattr, rr, "registered_roots", saved)
        for where in wheres:
            t0 = time.time()
            rc, out, err = rr.execute(["--repo", where, "status", "--short"], where)
            wrapped = time.time() - t0
            t0 = time.time()
            plain = self.plain(where, ["status", "--short"], {"GIT_OPTIONAL_LOCKS": "0"})
            print("PERF %s wrapped status %.3fs, plain status %.3fs (measurement only)" % (where, wrapped, time.time() - t0))
            self.assertEqual((rc, out), (0, plain.stdout), err)

    def test_program_path_is_resolved_once_in_python_and_exists(self):
        self.assertTrue(os.path.isfile(rr.program()) and os.access(rr.program(), os.X_OK))
        self.assertIn('(allow process-exec (literal "%s"))' % rr.program(), rr.build_profile(rr.program()))


if __name__ == "__main__":
    unittest.main()
