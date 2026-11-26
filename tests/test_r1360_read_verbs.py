"""R-1360 task A: the read door's verbs `budgit repo-read grep|ls|lines`, their validator, protected reach and the guard.

The validator and guard tests run everywhere. Live tests need macOS and /usr/bin/sandbox-exec and skip with a reason line
elsewhere. Temp repos are built with the real producers (init, add, commit, mkfifo, link, symlink). No network, no clock asserts.
Protected project names are written protected-1 and protected-2.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import readdoor  # noqa: E402
import repo_read as rr  # noqa: E402
import studiolib as sl  # noqa: E402
from helpers import StudioTestCase, protect  # noqa: E402

GIT = "/usr/bin/git"
CAN_RUN = sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec")
SKIP = "R-1360 live-sandbox test needs macOS and /usr/bin/sandbox-exec (other systems get a clean refusal)"
BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
DOOR = "R-1360 read door: protected path"
P = rr.PREFIX


def git(args, cwd, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    env.update(env_extra or {})
    p = subprocess.run([GIT] + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError("setup %s: %s" % (args, p.stderr))
    return p.stdout


def put(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


class Table(unittest.TestCase):
    def test_the_tables_live_in_readdoor_and_both_modules_import_them(self):
        self.assertIs(rr._FLAGS, readdoor.FLAGS)
        self.assertIs(rr._REVISION, readdoor.WORD)
        self.assertIs(sl.readdoor, readdoor)
        for verb in ("grep", "ls", "lines"):
            self.assertIn(verb, readdoor.FLAGS)


class Validator(unittest.TestCase):
    def refused(self, args, text=None):
        with self.assertRaises(rr.Refused) as c:
            rr.parse(args)
        if text:
            self.assertIn(text, str(c.exception))

    def test_grep_needs_dash_e(self):
        self.refused(["grep", "beta"])
        self.refused(["grep", "-n"])
        self.refused(["grep", "-e"])
        self.refused(["grep", "-e", ""])

    def test_dash_e_always_consumes_the_next_word(self):
        for word in ("-f", "--no-index", "-O", "--", "--textconv", "-x"):
            repo, verb, flags, paths = rr.parse(["grep", "-e", word, "-n"])
            self.assertEqual((verb, flags, paths), ("grep", ["-e", word, "-n"], []), word)
        self.assertEqual(rr.parse(["grep", "-e", "--", "--", "a.txt"])[3], ["a.txt"])

    def test_grep_allowed_forms(self):
        for args in (["grep", "-e", "x"], ["grep", "-e", "x", "-e", "y", "-n", "-i", "-l"], ["grep", "-c", "-w", "-F", "-e", "x", "HEAD"],
                     ["grep", "-E", "-e", "a|b", "HEAD~1", "--", "src/a.py"], ["grep", "-e", 'say "hi"']):
            rr.parse(args)

    def test_grep_revision_is_strict_and_single(self):
        self.refused(["grep", "-e", "x", "HEAD", "main"])
        self.refused(["grep", "-e", "x", "--evil"])
        self.refused(["grep", "-e", "x", "/etc"])

    def test_ls_forms(self):
        self.assertEqual(rr.parse(["ls"])[1:], ("ls", [], []))
        self.assertEqual(rr.parse(["ls", "HEAD", "--", "src"])[1:], ("ls", ["HEAD"], ["src"]))
        self.refused(["ls", "-l"])
        self.refused(["ls", "HEAD", "main"])

    def test_m2_a_revision_with_a_colon_is_refused_for_grep_and_ls_only(self):
        for bad in ("HEAD:.studio/private", "HEAD:README.md", "main:sub"):
            self.refused(["ls", bad])
            self.refused(["grep", "-e", "x", bad])
        rr.parse(["ls", "HEAD"])
        rr.parse(["grep", "-e", "x", "HEAD"])
        rr.parse(["show", "HEAD:README.md"])
        rr.parse(["log", "HEAD:README.md"])

    def test_lines_forms(self):
        self.assertEqual(rr.parse(["lines", "a.txt", "1", "400"])[1:], ("lines", ["a.txt", "1", "400"], []))
        self.assertEqual(rr.parse(["lines", "--count", "a.txt"])[1:], ("lines", ["--count", "a.txt"], []))
        for bad in (["lines"], ["lines", "a.txt"], ["lines", "a.txt", "1"], ["lines", "a.txt", "x", "2"], ["lines", "a.txt", "0", "2"],
                    ["lines", "a.txt", "5", "2"], ["lines", "--count"], ["lines", "--count", "a", "b"], ["lines", "a.txt", "1", "2", "--", "x"]):
            self.refused(bad)

    def test_pathspec_magic_after_double_dash_is_refused(self):
        for verb in (["grep", "-e", "x"], ["ls"]):
            for magic in (":(top)private", ":/x", ":!x", ":(exclude)x", ":x"):
                self.refused(verb + ["--", magic])
                self.refused(verb + ["--", "ok.txt", magic])
        self.refused(["log", "--", ":(top)x"])


def _add_refused_flag_tests():
    for flag in ("-f", "-O", "--open-files-in-pager", "--recurse-submodules", "--textconv", "--no-index", "-P", "-h", "-r", "-z",
                 "--and", "--max-depth=1", "-C2", "-A", "--exclude-standard", "--untracked", "-I", "--no-recurse-submodules", "-o"):
        def t(self, flag=flag):
            self.refused(["grep", "-e", "x", flag])
        setattr(Validator, "test_grep_refuses_flag_%s" % flag.strip("-").replace("-", "_").replace("=", "_"), t)


_add_refused_flag_tests()


class ArgvAndOutput(unittest.TestCase):
    def test_grep_and_ls_argv_use_exclude_pathspecs_and_no_literal_env(self):
        ex = [":(exclude,top,icase,literal)protected-2"]
        argv = rr.git_argv("/usr/bin/git", "grep", ["-e", "x", "HEAD"], ["a.txt"], ex)
        self.assertEqual(argv[-3:], ["--", ":(literal)a.txt", ex[0]])
        argv = rr.git_argv("/usr/bin/git", "grep", ["-e", "x"], [], ex)
        self.assertEqual(argv[-2:], ["--", ex[0]])
        argv = rr.git_argv("/usr/bin/git", "ls", [], [], ex)
        self.assertEqual(argv[2:4], ["ls-files", "--"])
        self.assertEqual(argv[-1], ex[0])
        argv = rr.git_argv("/usr/bin/git", "ls", ["HEAD"], ["sub"], ex)
        self.assertEqual(argv[2:6], ["ls-tree", "-r", "--name-only", "--full-name"])
        self.assertEqual(argv[-3:], ["HEAD", "--", "sub"])
        self.assertNotIn("--textconv", argv)
        env = rr.verb_env("/tmp/x", "grep")
        self.assertNotIn("GIT_LITERAL_PATHSPECS", env)
        self.assertEqual(rr.verb_env("/tmp/x", "log")["GIT_LITERAL_PATHSPECS"], "1")

    def test_ls_tree_output_is_filtered_for_protected_paths(self):
        out = "a.txt\n.studio/private/s.txt\nx/.BUDGIT/Private/y\nprotected-2/z\nprotected-20/ok\n"
        got = rr.filter_listing(out, ["protected-2"])
        self.assertEqual(got, "a.txt\nprotected-20/ok\n")


class Base(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.base = os.path.realpath(self.tmp)
        p = mock.patch.object(rr, "registered_roots", lambda: [self.base])
        p.start()
        self.addCleanup(p.stop)
        self.n = 0
        self.markers = os.path.join(self.base, "markers")
        os.makedirs(self.markers)

    def repo(self, files):
        self.n += 1
        d = os.path.join(self.base, "repo%d" % self.n)
        os.makedirs(d)
        git(["init", "-q"], d)
        for k, v in files.items():
            put(os.path.join(d, k), v)
        git(["add", "-A"], d)
        git(["commit", "-q", "-m", "first"], d)
        return d

    def run_rr(self, repo, args, cwd=None):
        return rr.execute(["--repo", repo] + args, cwd or repo)

    def marker(self, name):
        return os.path.join(self.markers, name)

    def script(self, name, body="cat"):
        p = os.path.join(self.base, name + ".sh")
        put(p, "#!/bin/sh\ntouch %s\n%s\n" % (self.marker(name), body), 0o755)
        return p


FILES = {"a.txt": "alpha\nbeta\n", "sub/b.txt": "beta gamma\n", ".gitignore": "*.log\n"}


@unittest.skipUnless(CAN_RUN, SKIP)
class LiveVerbs(Base):
    def test_grep_work_tree_output_is_the_recorded_string(self):
        d = self.repo(FILES)
        self.assertEqual(self.run_rr(d, ["grep", "-n", "-e", "beta"]), (0, "a.txt:2:beta\nsub/b.txt:1:beta gamma\n", ""))

    def test_grep_with_a_revision_and_paths(self):
        d = self.repo(FILES)
        self.assertEqual(self.run_rr(d, ["grep", "-n", "-e", "beta", "HEAD"])[1], "HEAD:a.txt:2:beta\nHEAD:sub/b.txt:1:beta gamma\n")
        self.assertEqual(self.run_rr(d, ["grep", "-l", "-e", "beta", "--", "sub"])[1], "sub/b.txt\n")

    def test_grep_counts_and_ignores_case_and_a_pattern_with_a_double_quote(self):
        d = self.repo(dict(FILES, **{"q.txt": 'say "hi"\n'}))
        self.assertEqual(self.run_rr(d, ["grep", "-c", "-i", "-e", "ALPHA", "--", "a.txt"])[1], "a.txt:1\n")
        self.assertEqual(self.run_rr(d, ["grep", "-e", 'say "hi"'])[1], 'q.txt:say "hi"\n')

    def test_grep_a_pattern_that_starts_with_a_dash_is_not_a_flag(self):
        d = self.repo({"d.txt": "x -f y\n"})
        self.assertEqual(self.run_rr(d, ["grep", "-F", "-e", "-f"])[1], "d.txt:x -f y\n")

    def test_grep_without_a_match_is_an_answer_not_a_refusal(self):
        d = self.repo(FILES)
        self.assertEqual(self.run_rr(d, ["grep", "-e", "nomatch"]), (1, "", ""))

    def test_ls_lists_tracked_names_only(self):
        d = self.repo(FILES)
        put(os.path.join(d, "untracked.txt"), "u\n")
        self.assertEqual(self.run_rr(d, ["ls"]), (0, ".gitignore\na.txt\nsub/b.txt\n", ""))
        self.assertEqual(self.run_rr(d, ["ls", "HEAD"]), (0, ".gitignore\na.txt\nsub/b.txt\n", ""))
        self.assertEqual(self.run_rr(d, ["ls", "--", "sub"])[1], "sub/b.txt\n")
        self.assertEqual(self.run_rr(d, ["ls", "HEAD", "--", "sub"])[1], "sub/b.txt\n")

    def test_lines_prints_the_range_and_the_count(self):
        d = self.repo(FILES)
        self.assertEqual(self.run_rr(d, ["lines", "a.txt", "1", "1"]), (0, "alpha\n", ""))
        self.assertEqual(self.run_rr(d, ["lines", "a.txt", "2", "99"]), (0, "beta\n", ""))
        self.assertEqual(self.run_rr(d, ["lines", ".gitignore", "1", "400"]), (0, "*.log\n", ""))
        self.assertEqual(self.run_rr(d, ["lines", "--count", "a.txt"]), (0, "2\n", ""))
        self.assertEqual(self.run_rr(d, ["lines", "--count", "sub/b.txt"])[1], "1\n")

    def test_lines_prints_at_most_400_lines(self):
        d = self.repo({"big.txt": "".join("l%d\n" % i for i in range(1, 1001))})
        rc, out, err = self.run_rr(d, ["lines", "big.txt", "10", "1000"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[0], "l10")
        self.assertEqual(len(out.splitlines()), 400)
        self.assertEqual(out.splitlines()[-1], "l409")

    def test_lines_from_a_subfolder_reads_relative_to_it(self):
        d = self.repo(FILES)
        self.assertEqual(rr.execute(["--repo", os.path.join(d, "sub"), "lines", "b.txt", "1", "1"], d)[1], "beta gamma\n")

    def test_m2_ls_and_grep_of_rev_colon_path_print_no_name_from_the_protected_folder(self):
        d = self.repo(dict(FILES, **{".studio/private/hidden-name.txt": "topsecret\n"}))
        for args in (["ls", "HEAD:.studio/private"], ["grep", "-e", "topsecret", "HEAD:.studio/private"]):
            rc, out, err = self.run_rr(d, args)
            self.assertEqual((rc, out), (2, ""), args)
            self.assertNotIn("hidden-name", err)
            self.assertNotIn("topsecret", err)
        self.assertEqual(self.run_rr(d, ["ls", "HEAD"])[0], 0)

    def test_m3_lines_refuses_an_untracked_and_an_ignored_file(self):
        d = self.repo(dict(FILES, **{".gitignore": "key.pem\nignored.txt\n"}))
        for name in (".env", "key.pem", "ignored.txt", "untracked.txt"):
            put(os.path.join(d, name), "K=1\n")
        for name in ("ignored.txt", "untracked.txt"):
            rc, out, err = self.run_rr(d, ["lines", name, "1", "5"])
            self.assertEqual((rc, out), (2, ""), name)
            self.assertIn("tracked files only", err)
        for name in (".env", "key.pem"):
            for args in (["lines", name, "1", "5"], ["lines", "--count", name]):
                rc, out, err = self.run_rr(d, args)
                self.assertEqual((rc, out), (2, ""), args)
                self.assertNotIn("K=1", err)
        self.assertEqual(self.run_rr(d, ["lines", "a.txt", "1", "1"]), (0, "alpha\n", ""))

    def test_m3_lines_refuses_a_secret_name_even_when_tracked(self):
        names = (".env", ".env.local", "key.pem", "k.KEY", "a.p12", "a.pfx", "id_rsa", "id_rsa.pub", "id_ed25519", "credentials.json",
                 "my-secret-notes.txt", ".netrc")
        d = self.repo(dict(FILES, **{n: "K=1\n" for n in names}))
        self.assertIn(".env", self.run_rr(d, ["ls"])[1].split("\n"), "control: the secret names are tracked")
        for name in names:
            rc, out, err = self.run_rr(d, ["lines", name, "1", "5"])
            self.assertEqual((rc, out), (2, ""), name)
            self.assertNotIn("tracked files only", err, name)
            self.assertEqual(self.run_rr(d, ["lines", "--count", name])[0], 2, name)


@unittest.skipUnless(CAN_RUN, SKIP)
class LiveMarkers(Base):
    """grep, ls and lines never run a filter, a textconv or an fsmonitor program. The control: the plain program does."""

    def make(self):
        d = self.repo({"a.txt": "alpha\nbeta\n", ".gitattributes": "*.txt filter=f diff=t\n"})
        for key, name in (("filter.f.clean", "clean"), ("filter.f.smudge", "smudge"), ("diff.t.textconv", "textconv"),
                          ("core.fsmonitor", "fsmon")):
            git(["config", key, self.script(name)], d)
        for name in ("clean", "smudge", "textconv", "fsmon"):
            if os.path.exists(self.marker(name)):
                os.remove(self.marker(name))
        put(os.path.join(d, "a.txt"), "alpha\nbetx\n")
        os.utime(os.path.join(d, "a.txt"), (2000000000, 2000000000))
        return d

    def assert_none(self, d):
        for name in ("clean", "textconv", "fsmon"):
            self.assertFalse(os.path.exists(self.marker(name)), "ran: " + name)

    def test_control_the_plain_program_runs_them(self):
        d = self.make()
        env = {"PATH": "/usr/bin:/bin", "HOME": self.base, "GIT_CONFIG_GLOBAL": os.devnull}
        subprocess.run([GIT, "status"], cwd=d, env=env, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        subprocess.run([GIT, "grep", "--textconv", "-e", "beta"], cwd=d, env=env, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertTrue(os.path.exists(self.marker("fsmon")) or os.path.exists(self.marker("clean")), "control: status ran nothing")
        self.assertTrue(os.path.exists(self.marker("textconv")), "control: textconv did not run")

    def test_grep_runs_no_program(self):
        d = self.make()
        self.run_rr(d, ["grep", "-e", "beta"])
        self.run_rr(d, ["grep", "-e", "beta", "HEAD"])
        self.assert_none(d)

    def test_ls_runs_no_program(self):
        d = self.make()
        self.run_rr(d, ["ls"])
        self.run_rr(d, ["ls", "HEAD"])
        self.assert_none(d)

    def test_lines_runs_no_program(self):
        d = self.make()
        self.run_rr(d, ["lines", "a.txt", "1", "2"])
        self.run_rr(d, ["lines", "--count", "a.txt"])
        self.assert_none(d)


@unittest.skipUnless(CAN_RUN, SKIP)
class LiveRefusedPaths(Base):
    def refused(self, d, args, cwd=None, text=None):
        rc, out, err = self.run_rr(d, args, cwd)
        self.assertEqual((rc, out), (2, ""), (args, err))
        self.assertTrue(err.startswith(("R-1292 repo-read: ", "R-1360 read door: ")), err)
        if text:
            self.assertIn(text, err)

    def test_git_internals_are_refused(self):
        d = self.repo(FILES)
        for p in (".git/config", ".git/HEAD", "./.git/objects/../config"):
            self.refused(d, ["lines", p, "1", "5"])
            self.refused(d, ["lines", "--count", p])

    def test_a_fifo_is_refused_and_does_not_block(self):
        d = self.repo(FILES)
        os.mkfifo(os.path.join(d, "pipe"))
        self.refused(d, ["lines", "pipe", "1", "5"], text="regular file")

    def test_a_symlink_out_of_the_repo_is_refused(self):
        d = self.repo(FILES)
        out = os.path.join(self.base, "outside.txt")
        put(out, "secret\n")
        os.symlink(out, os.path.join(d, "link"))
        self.refused(d, ["lines", "link", "1", "1"])
        self.refused(d, ["lines", "../outside.txt", "1", "1"])

    def test_a_hard_link_is_refused(self):
        d = self.repo(FILES)
        os.link(os.path.join(d, "a.txt"), os.path.join(d, "hard.txt"))
        self.refused(d, ["lines", "hard.txt", "1", "1"], text="link")

    def test_a_missing_file_is_a_refusal(self):
        d = self.repo(FILES)
        self.refused(d, ["lines", "nope.txt", "1", "1"])

    def test_the_open_helper_refuses_a_symlink_swap_and_a_device(self):
        d = self.repo(FILES)
        real = os.path.join(d, "swap.txt")
        put(real, "x\n")
        os.remove(real)
        os.symlink(os.path.join(d, "a.txt"), real)             # the resolved path became a symlink between resolve and open
        with self.assertRaises(rr.Refused):
            rr.open_regular(real)
        with self.assertRaises(rr.Refused):
            rr.open_regular("/dev/null")
        f = rr.open_regular(os.path.join(d, "a.txt"))
        self.assertTrue(stat.S_ISREG(os.fstat(f.fileno()).st_mode))
        f.close()


@unittest.skipUnless(CAN_RUN, SKIP)
class LiveProtected(Base):
    def setUp(self):
        super().setUp()
        self.p1 = os.path.join(self.base, "repo1", "protected-1")
        self.p2 = os.path.join(self.base, "repo1", "protected-2")
        protect(self.home, self.p1, self.p2)
        self.d = self.repo({"a.txt": "needle\n", "protected-1/one.txt": "needle one\n", "protected-2/two.txt": "needle two\n",
                            ".studio/private/s.txt": "needle secret\n", "sub/.budgit/private/t.txt": "needle nested\n",
                            "sub/ok.txt": "needle ok\n"})
        self.assertEqual(self.d, os.path.join(self.base, "repo1"))

    def grep(self, extra, cwd=None):
        return self.run_rr(self.d, ["grep", "-l", "-e", "needle"] + extra, cwd)

    def test_grep_without_a_path_leaves_out_other_protected_projects_and_private(self):
        self.assertEqual(self.grep([])[1], "a.txt\nsub/ok.txt\n")

    def test_grep_with_a_revision_leaves_them_out(self):
        self.assertEqual(self.grep(["HEAD"])[1], "HEAD:a.txt\nHEAD:sub/ok.txt\n")

    def test_grep_with_paths_leaves_them_out(self):
        self.assertEqual(self.grep(["--", "sub", "a.txt"])[1], "a.txt\nsub/ok.txt\n")
        self.assertEqual(self.grep(["HEAD", "--", "sub"])[1], "HEAD:sub/ok.txt\n")

    def test_grep_inside_the_current_protected_project_stays_allowed(self):
        rc, out, err = self.grep([], cwd=self.p1)
        self.assertEqual(out, "a.txt\nprotected-1/one.txt\nsub/ok.txt\n")

    def test_ls_leaves_them_out_with_no_path_with_paths_and_with_a_revision(self):
        want = "a.txt\nsub/ok.txt\n"
        self.assertEqual(self.run_rr(self.d, ["ls"])[1], want)
        self.assertEqual(self.run_rr(self.d, ["ls", "HEAD"])[1], want)
        self.assertEqual(self.run_rr(self.d, ["ls", "--", "sub", "a.txt"])[1], want)
        self.assertEqual(self.run_rr(self.d, ["ls", "HEAD", "--", "sub", "a.txt"])[1], want)

    def test_naming_a_protected_path_is_refused_for_grep_and_ls(self):
        for rel in ("protected-2", "protected-2/two.txt", ".studio/private", "sub/.budgit/private/t.txt", "PROTECTED-2"):
            for args in (["grep", "-e", "needle", "--", rel], ["ls", "--", rel], ["ls", "HEAD", "--", rel]):
                rc, out, err = self.run_rr(self.d, args)
                self.assertEqual((rc, out, err), (2, "", DOOR), (args, err))

    def test_lines_of_a_protected_path_is_refused_case_folded(self):
        for rel in ("protected-2/two.txt", "PROTECTED-2/two.txt", ".studio/private/s.txt", ".STUDIO/Private/s.txt",
                    "sub/.budgit/private/t.txt", "protected-2/../protected-2/two.txt"):
            for args in (["lines", rel, "1", "5"], ["lines", "--count", rel]):
                self.assertEqual(self.run_rr(self.d, args), (2, "", DOOR), args)

    def test_lines_inside_the_current_protected_project_stays_allowed(self):
        self.assertEqual(self.run_rr(self.d, ["lines", "protected-1/one.txt", "1", "1"], cwd=self.p1), (0, "needle one\n", ""))
        self.assertEqual(self.run_rr(self.d, ["lines", "protected-2/two.txt", "1", "1"], cwd=self.p1), (2, "", DOOR))

    def test_pathspec_magic_is_refused_with_the_rule_name_first(self):
        rc, out, err = self.run_rr(self.d, ["grep", "-e", "needle", "--", ":(top)protected-2"])
        self.assertEqual((rc, out), (2, ""))
        self.assertTrue(err.startswith(P), err)

    def test_an_unreadable_protected_list_fails_closed(self):
        put(os.path.join(self.home, "config.json"), '{"protected_dirs": "nope"}')
        rc, out, err = self.grep([])
        self.assertEqual((rc, out, err), (2, "", DOOR))


class Guard(Base):
    def helper(self, command):
        return sl.real_data_check("Bash", {"command": command}, self.base, cwd=self.base, agent_id="agent-1")[0]

    def test_a_single_quoted_pattern_with_anything_inside_is_allowed(self):
        for pat in ("a;b", 'say "hi"', "$(id) `x` \\ ! { } * ? > < | & #", "it is ~x", "a  b"):
            self.assertIsNone(self.helper(BUDGIT + " repo-read grep -n -e '" + pat + "'"), pat)

    def test_the_new_verbs_are_allowed_for_a_helper(self):
        for tail in ("ls", "ls HEAD -- src", "lines a.txt 1 400", "lines --count a.txt", "grep -e x HEAD -- src"):
            self.assertIsNone(self.helper(BUDGIT + " repo-read " + tail), tail)

    def test_every_r1292_quoting_refusal_stays(self):
        nl, bq, bs = chr(10), chr(96), chr(92)
        for tail in ('grep -e "x"', 'grep -e "$(id)"', "grep -e " + bq + "id" + bq, "grep -e a" + bs + "b", "grep -e a!b", "grep -e a" + nl + "b",
                     "grep -e 'a" + nl + "b'", "grep -e x; id", "grep -e x && id", "grep -e x | cat", "grep -e x > f", "grep -e x &",
                     "ls ~x", "lines =x 1 2", "ls *", "ls {a,b}"):
            d = self.helper(BUDGIT + " repo-read " + tail)
            self.assertTrue(d and d[0] in ("deny", "ask"), "%r -> %r" % (tail, d))

    def test_refused_flags_carry_the_rule_name_first(self):
        for tail in ("grep -e x -f pats", "grep -e x -O", "grep -e x --open-files-in-pager=sh", "grep -e x --recurse-submodules",
                     "grep -e x --textconv", "grep -e x --no-index", "grep -n", "ls -l", "lines a 1", "grep -e x -- ':(top)x'"):
            d = self.helper(BUDGIT + " repo-read " + tail)
            self.assertEqual(d[0], "deny", tail)
            self.assertTrue(d[1].startswith(P), d[1])


class Replay(Base):
    """Corpus 1, 2 and 5 of the plan's Evidence section as hook payloads (the shape hook.py reads at main)."""

    def setUp(self):
        super().setUp()
        self.init_project()
        protect(self.home, self.project)             # protected-1: the real-data guard is on, as in the corpus

    def payload(self, command, agent="agent-1"):
        inp = {"session_id": "s-r1360", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.project}
        if agent:
            inp["agent_id"] = agent
            inp["agent_type"] = "studio:builder"
        return inp

    def decision(self, command, agent="agent-1"):
        import hook
        hook._OUT.clear()
        try:
            hook.on_pre_tool_use(self.payload(command, agent), self.project)
            return (hook._OUT.get("hookSpecificOutput") or {}).get("permissionDecision")
        finally:
            hook._OUT.clear()

    def test_corpus_5_a_helper_grep_with_a_double_quote_inside_single_quotes_is_allowed(self):
        self.assertIsNone(self.decision(BUDGIT + " repo-read grep -n -e 'say \"hi\"' -- tests"))
        self.assertEqual(self.decision(BUDGIT + ' repo-read grep -n -e "say" -- tests'), "deny")

    def test_corpus_1_the_helper_reads_go_through_the_door(self):
        for tail in ("status", "lines .gitignore 1 400", "lines --count plugin/scripts/repo_read.py", "ls"):
            self.assertIsNone(self.decision(BUDGIT + " repo-read " + tail), tail)

    def test_corpus_1_the_original_forms_are_not_opened_by_the_door(self):
        """The door judges only `budgit repo-read ...`: the original forms get no pass from it (their refusal comes from the other
        guards, which depend on the live checkout and are not rebuilt here)."""
        for command in ("git status", "cat .gitignore", "wc -l plugin/scripts/repo_read.py"):
            self.assertIsNone(sl._helper_budgit_read(command, self.project), command)

    def test_corpus_2_the_awk_with_braces_is_not_opened_by_the_door(self):
        self.assertIsNone(sl._helper_budgit_read("awk '{print}' plugin/scripts/repo_read.py", self.project))
        self.assertIsNone(self.decision(BUDGIT + " repo-read lines plugin/scripts/repo_read.py 1 400"))


if __name__ == "__main__":
    unittest.main()
