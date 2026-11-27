"""Release gate: update from the last shipped base to this tree on a throwaway HOME.

The old copy is the plugin tree at BASE_SHA (the last shipped version, BASE_VERSION), taken with `git archive`. Its SessionStart hook
runs as a user's session would (jobs on, launch agents folder in the temp dir, launchctl off, as test_postupdate_replay does). Then
the registry points at a copy of this checkout's plugin tree and the new hook runs. The guard cases use the checkout's guard code the
way test_r1135_link and test_pinned_runner_allow do. The next release changes the two constants below and nothing else."""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import unittest
import unittest.mock

from helpers import StudioTestCase, ROOT
from test_guard_trust import GuardCase, CLI
import studiolib as sl
import test_pinned_runner_allow as pra

BASE_VERSION = "0.13.9.27"
BASE_SHA = "f7305f43"          # the commit that set plugin.json to BASE_VERSION
NEW_VERSION = "0.13.9.48"
MAIN_ONLY = sys.platform == "darwin"
HOOK_REL = os.path.join("scripts", "hook.py")


def base_tree_available():
    r = subprocess.run(["git", "-C", ROOT, "cat-file", "-e", BASE_SHA + "^{commit}"], capture_output=True)
    return r.returncode == 0


class UpdateReplay(StudioTestCase):
    def setUp(self):
        super().setUp()
        if not base_tree_available():
            self.skipTest("the base commit %s is not in this clone (shallow clone?)" % BASE_SHA)
        self.uh = os.path.join(self.tmp, "uh")
        self.agents = os.path.join(self.tmp, "agents")
        self.cfg = os.path.join(self.tmp, "claude-cfg")
        os.makedirs(self.agents)          # as test_postupdate_replay: the HOME and the Claude folder are made by the first use
        for d in (self.uh, self.home, self.cfg, self.agents):
            self.assertTrue(os.path.realpath(d).startswith(os.path.realpath(self.tmp)), d)      # never her real folders
        self.n = 0
        self.old_root = self.install_base()
        self.new_root = self.install_new()

    def cache_root(self, version):
        return os.path.join(self.cfg, "plugins", "cache", "mkt", "budgit", version)

    def install_base(self):
        """The plugin tree at BASE_SHA, `git archive` piped into tarfile, under the cache folder. The archive umask is 022: the
        default 002 would leave the copy group-writable, and Claude Code's registry check does not trust such a folder."""
        out = subprocess.run(["git", "-C", ROOT, "-c", "tar.umask=022", "archive", BASE_SHA, "plugin"], capture_output=True, check=True).stdout
        root = self.cache_root(BASE_VERSION)
        os.makedirs(root)
        with tarfile.open(fileobj=io.BytesIO(out)) as tar:
            members = []
            for m in tar.getmembers():
                if m.name == "plugin" or not m.name.startswith("plugin/"):
                    continue
                m.name = m.name[len("plugin/"):]
                m.mode &= ~0o022                                    # never group or other writable, whatever the archive says
                members.append(m)
            real = os.path.realpath(root)
            for m in members:                                        # nothing may land outside the copy
                self.assertTrue(os.path.realpath(os.path.join(root, m.name)).startswith(real), m.name)
            try:
                tar.extractall(root, members=members, filter="tar")
            except TypeError:
                tar.extractall(root, members=members)
        for dirpath, dirnames, _ in os.walk(root):                   # the folders of the cache path up to the copy: also not writable by others
            os.chmod(dirpath, os.stat(dirpath).st_mode & ~0o022)
        return root

    def install_new(self):
        root = self.cache_root(NEW_VERSION)
        shutil.copytree(os.path.join(ROOT, "plugin"), root, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        return root

    def register(self, root, version):
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"plugins": {"budgit@mkt": [{"installPath": root, "version": version}]}}, f)

    def env(self):
        env = dict(os.environ, HOME=self.uh, BUDGIT_HOME=self.home, BUDGIT_LANG="en", BUDGIT_LAUNCH_AGENTS_DIR=self.agents,
                   BUDGIT_HOUSEKEEPING_LAUNCHCTL="off", CLAUDE_CONFIG_DIR=self.cfg)
        return env

    def session(self, root):
        """One SessionStart of the copy at root, as a new session."""
        self.n += 1
        claim = os.path.join(self.home, "setup-asked.json")
        if os.path.exists(claim):
            with open(claim, "w") as f:
                json.dump({"session": "old", "ts": 1}, f)
        r = subprocess.run([sys.executable, os.path.join(root, HOOK_REL), "session-start"], capture_output=True, text=True, env=self.env(),
                           input=json.dumps({"session_id": "%08d-0000-4000-8000-000000000000" % self.n, "source": "startup",
                                             "cwd": os.path.join(self.tmp, "nowhere")}))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def update(self):
        """Old session, then the registry moves to the new copy and the new session runs."""
        self.register(self.old_root, BASE_VERSION)
        self.old_out = self.session(self.old_root)
        self.old_launchers = self.launchers()
        self.old_launcher_text = [open(p).read() for p in self.old_launchers]
        self.register(self.new_root, NEW_VERSION)
        self.new_out = self.session(self.new_root)

    def why_old_wrote_none(self):
        """A failure message: the stored keep-alive answer ("yes": an app task covers it; "later": turn_on refused) and the old copy's turn_on text."""
        try:
            with open(os.path.join(self.home, "setup-answers.json")) as f:
                stored = json.load(f).get("answers", {}).get("keepalive")
        except (OSError, ValueError):
            stored = None
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import postupdate, jobs; postupdate.answer('keepalive', 'yes'); "
                "print(jobs.covering_task('keepalive')); print(jobs.turn_on('keepalive', sys.argv[2]))")
        r = subprocess.run([sys.executable, "-I", "-c", code, os.path.join(self.old_root, "scripts"), os.path.join(self.old_root, "bin", "budgit")],
                           capture_output=True, text=True, env=self.env())
        return "the %s copy wrote no keep-alive plist; stored answer %r; its covering task and turn_on say: %s %s" % (
            BASE_VERSION, stored, r.stdout.strip(), r.stderr.strip()[-300:])

    def plists(self):
        return sorted(f for f in os.listdir(self.agents) if f.endswith(".plist"))

    def launchers(self):
        """The fixed launcher shell file(s): <budgit home>/bin/*, which a job's plist runs and an update rewrites."""
        return sorted(os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(self.home, "bin")) for f in fs)

    def snapshot(self):
        snap = {}
        for p in self.plists():
            with open(os.path.join(self.agents, p), "rb") as f:
                snap[p] = f.read()
        for p in self.launchers():
            with open(p, "rb") as f:
                snap[p] = f.read()
        return snap

    @unittest.skipUnless(MAIN_ONLY, "the launch agent plists are written on macOS only")
    def test_the_old_copy_registers_its_job_before_the_update(self):
        self.register(self.old_root, BASE_VERSION)
        self.session(self.old_root)
        self.assertTrue([p for p in self.plists() if "keepalive" in p], self.why_old_wrote_none())
        self.assertTrue(any(self.old_root in open(p).read() for p in self.launchers()), self.launchers())

    @unittest.skipUnless(MAIN_ONLY, "the launch agent plists are written on macOS only")
    def test_after_the_update_the_launcher_points_at_the_new_copy_once(self):
        self.update()
        launchers = self.launchers()
        self.assertEqual(len(launchers), 1, launchers)
        self.assertEqual(self.old_launchers, launchers)                       # the same launcher file, rewritten in place
        self.assertEqual(len(self.old_launcher_text), 1, self.why_old_wrote_none())
        self.assertIn(self.old_root, self.old_launcher_text[0])               # before the update it ran the 0.13.9.27 copy
        self.assertNotIn(self.new_root, self.old_launcher_text[0])
        text = open(launchers[0]).read()
        self.assertIn(self.new_root, text)
        self.assertNotIn(self.old_root, text)
        self.assertEqual(len([p for p in self.plists() if "keepalive" in p]), 1, self.plists())

    @unittest.skipUnless(MAIN_ONLY, "the launch agent plists are written on macOS only")
    def test_a_second_session_start_after_the_update_changes_nothing(self):
        self.update()
        before = self.snapshot()
        self.session(self.new_root)
        self.assertEqual(self.snapshot(), before)

    def test_the_new_hook_runs_from_the_new_copy_not_the_old_one(self):
        self.update()
        self.assertNotIn(BASE_VERSION, self.new_out)
        self.assertFalse(os.path.exists(os.path.join(self.old_root, "scripts", "timelines.py")),
                         "the old copy should be the shipped base, without the new module")
        self.assertTrue(os.path.isfile(os.path.join(self.new_root, "scripts", "timelines.py")))
        with open(os.path.join(self.new_root, ".claude-plugin", "plugin.json")) as f:
            self.assertEqual(json.load(f)["version"], NEW_VERSION)

    def test_timelines_is_in_the_new_copy_and_imports(self):
        scripts = os.path.join(self.new_root, "scripts")
        self.assertTrue(os.path.isfile(os.path.join(scripts, "timelines.py")))
        r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import timelines; print(timelines.__file__)",
                            scripts], capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.realpath(r.stdout.strip()).startswith(os.path.realpath(self.new_root)), r.stdout)


class UpdateGuard(GuardCase):
    """The checkout's guard, with the cases the update must keep working: the link to the own budgit and the pinned --record run."""

    def setUp(self):
        super().setUp()
        self.assertTrue(os.path.realpath(os.path.expanduser("~")).startswith(os.path.realpath(self.tmp)))      # a throwaway HOME

    def verdict(self, cmd, subagent=True, project=None):
        kw = {} if project is None else {"project": project}
        got = sl.home_write_check("Bash", {"command": cmd}, self.root, None, subagent=subagent, **kw)[0]
        return got[0] if got else None

    # the same fake main checkout and worktree the pinned-runner tests build (borrowed, so the two cannot drift)
    fake = pra.PinnedRunnerAllow.fake
    use_root = pra.PinnedRunnerAllow.use_root
    main_checkout = pra.PinnedRunnerAllow.main_checkout
    worktree_of = pra.PinnedRunnerAllow.worktree_of

    def test_a_path_symlink_to_the_own_budgit_passes_a_read_of_the_bound_project(self):
        bindir = os.path.join(self.tmp, "linkbin")
        os.makedirs(bindir)
        os.symlink(CLI, os.path.join(bindir, "budgit"))
        old = os.environ.get("PATH")
        os.environ["PATH"] = bindir + os.pathsep + (old or os.defpath)
        self.addCleanup(lambda: os.environ.__setitem__("PATH", old) if old is not None else os.environ.pop("PATH", None))
        self.passes("budgit denies --project %s --since 7d" % self.project)

    def test_record_from_the_bound_main_checkout_passes(self):
        main = self.main_checkout()
        cmd = "python3 -I %s/tools/runtests.py --record" % main
        for who in (True, False):
            self.assertIsNone(self.verdict(cmd, who))

    def test_the_same_record_run_from_a_worktree_is_still_refused(self):
        main = self.main_checkout()
        wt = self.worktree_of(main, "replay-wt")
        self.assertNotEqual(self.verdict("python3 -I %s/tools/runtests.py --record" % wt, True), None)


if __name__ == "__main__":
    unittest.main()
