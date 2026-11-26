"""R-712: scheduled jobs survive a plugin update (plugin/scripts/installstep.py, housekeeping.launcher_text/repair_job)."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts plugin/scripts on sys.path)
import housekeeping
import installstep
import studiolib as sl

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugin")
ENV_KEYS = ("HOME", "BUDGIT_HOME", "STUDIO_HOME", "CLAUDE_CONFIG_DIR", "BUDGIT_LAUNCH_AGENTS_DIR", "BUDGIT_HOUSEKEEPING_LAUNCHCTL", "BUDGIT_LANG")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = {k: os.environ.get(k) for k in ENV_KEYS}
        self.addCleanup(self.restore)
        self.cfg = os.path.join(self.tmp, ".claude")
        self.bhome = os.path.join(self.tmp, ".budgit")
        self.agents = os.path.join(self.tmp, "agents")
        os.makedirs(self.cfg)
        os.makedirs(self.bhome)
        os.environ.pop("STUDIO_HOME", None)
        os.environ.update(HOME=self.tmp, BUDGIT_HOME=self.bhome, CLAUDE_CONFIG_DIR=self.cfg, BUDGIT_LAUNCH_AGENTS_DIR=self.agents,
                          BUDGIT_HOUSEKEEPING_LAUNCHCTL="off", BUDGIT_LANG="en")
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"projects_root": os.path.join(self.tmp, "projects")}, f)
        os.makedirs(os.path.join(self.tmp, "projects"))
        self.calls = []
        patcher = mock.patch.object(housekeeping, "_launchctl", lambda *a, **k: self.calls.append(a) or 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def restore(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def install(self, version, register=True):
        root = os.path.join(self.cfg, "plugins", "cache", "mkt", "budgit", version)
        shutil.copytree(PLUGIN, root, symlinks=True)
        manifest = os.path.join(root, ".claude-plugin", "plugin.json")
        with open(manifest) as f:
            data = json.load(f)
        data["version"] = version
        with open(manifest, "w") as f:
            json.dump(data, f)
        if register:
            with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
                json.dump({"plugins": {"budgit@mkt": [{"installPath": root, "version": version}]}}, f)
        return root

    def legacy_plist(self, old_root, env=True):
        os.makedirs(self.agents, exist_ok=True)
        path = os.path.join(self.agents, housekeeping.LABEL + ".plist")
        log = os.path.join(self.bhome, "housekeeping.log")
        data = {"Label": housekeeping.LABEL, "ProgramArguments": [sys.executable, os.path.join(old_root, "bin", "budgit"), "housekeeping", "--apply", "--quiet"],
                "StartCalendarInterval": {"Hour": 9, "Minute": 30}, "Nice": 10, "LowPriorityIO": True,
                "StandardOutPath": log, "StandardErrorPath": log,
                "EnvironmentVariables": {"BUDGIT_HOME": self.bhome} if env else {}}
        with open(path, "wb") as f:
            plistlib.dump(data, f)
        return path

    def plist(self):
        with open(os.path.join(self.agents, housekeeping.LABEL + ".plist"), "rb") as f:
            return plistlib.load(f)

    def hooks_log(self):
        try:
            return open(os.path.join(self.bhome, "hooks.log")).read()
        except OSError:
            return ""


class UpdateReplay(Base):
    def start_session(self, root):
        env = dict(os.environ)
        r = subprocess.run([sys.executable, os.path.join(root, "scripts", "hook.py"), "session-start"], input=json.dumps({"session_id": "s1", "cwd": self.tmp}),
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_update_replay_jobs_run_with_no_command(self):
        old = self.install("0.13.5")
        self.legacy_plist(old)
        new = self.install("0.13.6")
        shutil.rmtree(old)
        out = self.start_session(new)
        plist = self.plist()
        launcher = os.path.join(self.bhome, "bin", "budgit-job")
        self.assertEqual(plist["ProgramArguments"][:2], ["/bin/sh", launcher])
        self.assertTrue(os.path.isfile(launcher))
        self.assertTrue(os.path.isdir(os.path.dirname(plist["StandardOutPath"])))
        env = dict(plist["EnvironmentVariables"], PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=self.tmp)
        run = subprocess.run(plist["ProgramArguments"], env=env, cwd="/", capture_output=True, text=True, timeout=120)
        busy_skip = run.returncode == 11 and "skipped: machine busy" in run.stderr
        self.assertTrue(run.returncode == 0 or busy_skip, run.stderr)
        probe = subprocess.run(["/bin/sh", launcher, "status", "--probe"], env=env, cwd="/", capture_output=True, text=True, timeout=60)
        self.assertIn("0.13.6", probe.stdout)
        self.assertEqual(self.hooks_log().count("jobs-v1 re-registered"), 1)
        self.assertNotIn("scheduled jobs", out)

    def test_second_start_is_a_no_op(self):
        old = self.install("0.13.5")
        self.legacy_plist(old)
        new = self.install("0.13.6")
        shutil.rmtree(old)
        self.start_session(new)
        watched = [os.path.join(self.bhome, "install.json"), os.path.join(self.bhome, "bin", "budgit-job"), os.path.join(self.agents, housekeeping.LABEL + ".plist")]
        before = [os.stat(p).st_mtime_ns for p in watched]
        self.start_session(new)
        self.assertEqual(before, [os.stat(p).st_mtime_ns for p in watched])


class StepTest(Base):
    def test_no_plist_means_nothing_is_created(self):
        root = self.install("0.13.6")
        self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        self.assertFalse(os.path.exists(self.agents))
        self.assertFalse(os.path.exists(os.path.join(self.bhome, "bin")))
        self.assertEqual(installstep.record_read()["steps"], {"jobs-v1": "0.13.6", "checks-v1": "0.13.6"})

    def test_untrusted_roots_are_never_recorded(self):
        loose = self.install("0.13.6", register=False)
        self.assertIsNone(installstep.run(loose, "0.13.6", sys.executable))
        root = self.install("0.13.7")
        with mock.patch.object(sl, "vendored", lambda: True):
            self.assertIsNone(installstep.run(root, "0.13.7", sys.executable))
        os.chmod(root, 0o775)
        self.assertIsNone(installstep.run(root, "0.13.7", sys.executable))
        self.assertIsNone(installstep.record_read())

    def test_an_older_chat_never_pulls_the_job_back(self):
        old = self.install("0.13.5", register=False)
        new = self.install("0.13.6")
        installstep.run(new, "0.13.6", sys.executable)
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"plugins": {"budgit@mkt": [{"installPath": new, "version": "0.13.6"}, {"installPath": old, "version": "0.13.5"}]}}, f)
        installstep.run(old, "0.13.5", sys.executable)
        self.assertEqual(installstep.record_read()["root"], new)

    def test_a_missing_root_is_replaced_by_the_running_one(self):
        old = self.install("0.13.5")
        installstep.run(old, "0.13.5", sys.executable)
        new = self.install("0.13.6")
        shutil.rmtree(old)
        installstep.run(new, "0.13.6", sys.executable)
        self.assertEqual(installstep.record_read()["root"], new)

    def test_env_agents_dir_never_reaches_launchctl(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        os.environ["BUDGIT_HOUSEKEEPING_LAUNCHCTL"] = "on"
        line = installstep.run(root, "0.13.6", sys.executable)
        self.assertIn("could not repair", line)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.plist()["ProgramArguments"][0], sys.executable)

    def test_a_project_set_home_cannot_move_the_job(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        other = os.path.join(self.tmp, "evil")
        os.makedirs(other)
        os.environ["BUDGIT_HOME"] = other
        installstep.run(root, "0.13.6", sys.executable)
        self.assertEqual(self.plist()["ProgramArguments"][0], sys.executable)
        self.assertFalse(os.path.exists(os.path.join(other, "bin")))

    def test_symlinks_are_refused(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.bhome, "bin"))
        self.assertIn("could not repair", installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertEqual(self.plist()["ProgramArguments"][0], sys.executable)

    def test_a_busy_lock_skips(self):
        import fcntl
        root = self.install("0.13.6")
        fd = os.open(os.path.join(self.bhome, installstep.LOCK), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        self.assertIsNone(installstep.record_read())

    def test_a_failed_step_is_retried_and_others_run(self):
        root = self.install("0.13.6")
        done = []

        def bad(r, p):
            raise RuntimeError("boom")
        with mock.patch.object(installstep, "STEPS", [("a", bad), ("b", lambda r, p: done.append(1) or [])]):
            self.assertIn("boom", installstep.run(root, "0.13.6", sys.executable))
            self.assertEqual(installstep.record_read()["steps"], {"b": "0.13.6"})
        good = []
        with mock.patch.object(installstep, "STEPS", [("a", lambda r, p: good.append(1) or []), ("b", lambda r, p: done.append(2) or [])]):
            self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual((good, done), ([1], [1]))

    def test_a_gone_log_folder_is_repaired(self):
        root = self.install("0.13.6")
        path = self.legacy_plist(root)
        data = self.plist()
        data["StandardOutPath"] = data["StandardErrorPath"] = os.path.join(self.tmp, ".studio-gone", "housekeeping.log")
        with open(path, "wb") as f:
            plistlib.dump(data, f)
        installstep.run(root, "0.13.6", sys.executable)
        self.assertEqual(self.plist()["StandardOutPath"], os.path.join(self.bhome, "housekeeping.log"))
        self.assertTrue(self.calls)


class MoreStepTest(Base):
    def test_off_macos_is_a_no_op(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        before = open(os.path.join(self.agents, housekeeping.LABEL + ".plist"), "rb").read()
        del os.environ["BUDGIT_LAUNCH_AGENTS_DIR"]
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual(open(os.path.join(self.agents, housekeeping.LABEL + ".plist"), "rb").read(), before)
        self.assertFalse(os.path.exists(os.path.join(self.bhome, "bin")))
        self.assertEqual(self.calls, [])

    def test_a_marketplaces_copy_is_never_recorded(self):
        root = os.path.join(self.cfg, "plugins", "marketplaces", "mkt", "budgit")
        shutil.copytree(PLUGIN, root, symlinks=True)
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"plugins": {"budgit@mkt": [{"installPath": root, "version": "0.13.6"}]}}, f)
        self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        self.assertIsNone(installstep.record_read())

    def test_a_symlinked_home_is_refused(self):
        root = self.install("0.13.6")
        real = os.path.join(self.tmp, "realhome")
        os.makedirs(real)
        link = os.path.join(self.tmp, "linkhome")
        os.symlink(real, link)
        os.environ["BUDGIT_HOME"] = link
        self.legacy_plist(root)
        data = self.plist()
        data["EnvironmentVariables"] = {"BUDGIT_HOME": link}
        with open(os.path.join(self.agents, housekeeping.LABEL + ".plist"), "wb") as f:
            plistlib.dump(data, f)
        self.assertIn("could not repair", installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual(os.listdir(real), [])
        self.assertEqual(self.plist()["ProgramArguments"][0], sys.executable)

    def test_a_symlinked_launcher_is_refused(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as f:
            f.write("keep")
        os.makedirs(os.path.join(self.bhome, "bin"))
        os.symlink(victim, os.path.join(self.bhome, "bin", "budgit-job"))
        self.assertIn("could not repair", installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual(open(victim).read(), "keep")
        self.assertEqual(self.plist()["ProgramArguments"][0], sys.executable)

    def test_second_start_makes_no_launchctl_call(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        installstep.run(root, "0.13.6", sys.executable)
        self.assertTrue(self.calls)
        self.calls.clear()
        self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual(self.calls, [])


class ReviewFixTest(Base):
    def test_a_recorded_higher_root_that_is_no_longer_registered_is_replaced(self):
        high = self.install("0.13.9")
        installstep.run(high, "0.13.9", sys.executable)
        self.assertEqual(installstep.record_read()["root"], high)
        low = self.install("0.13.6")                          # the registry now names only the lower root
        installstep.run(low, "0.13.6", sys.executable)
        self.assertEqual(installstep.record_read()["root"], low)
        self.assertEqual(installstep.record_read()["version"], "0.13.6")

    def test_a_failed_bootstrap_is_retried_and_the_step_stays_not_done(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        calls = []

        def broken(*args, **kw):
            calls.append((args, kw))
            return 1 if args[0] == "bootstrap" else 0
        with mock.patch.object(housekeeping, "_launchctl", broken):
            self.assertIn("could not repair", installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual([c[0][0] for c in calls], ["bootout", "bootstrap", "bootstrap"])      # one retry
        self.assertLessEqual(sum(c[1]["timeout"] for c in calls), 6)                             # well under the 10 s hook timeout
        self.assertNotIn("jobs-v1", installstep.record_read()["steps"])
        calls.clear()
        with mock.patch.object(housekeeping, "_launchctl", lambda *a, **k: calls.append(a) or 0):
            self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        self.assertEqual([c[0] for c in calls], ["bootout", "bootstrap"])                        # loaded again, though the plist was already new
        self.assertEqual(installstep.record_read()["steps"], {"jobs-v1": "0.13.6", "checks-v1": "0.13.6"})

    def test_an_interrupted_reload_is_redone_on_the_next_run(self):
        root = self.install("0.13.6")
        self.legacy_plist(root)
        installstep.run(root, "0.13.6", sys.executable)
        marker = os.path.join(self.bhome, housekeeping.RELOAD_MARK + "-" + housekeeping.LABEL)
        with open(marker, "w") as f:
            f.write("reload\n")                               # the hook was killed after bootout
        self.calls.clear()
        job = housekeeping.JOBS["cleanup"]
        self.assertEqual(housekeeping.repair_job(job, root, sys.executable), "changed")
        self.assertEqual([c[0] for c in self.calls], ["bootout", "bootstrap"])
        self.assertFalse(os.path.exists(marker))

    def test_repair_rebuilds_the_plist_from_the_kept_keys_only(self):
        root = self.install("0.13.6")
        path = self.legacy_plist(root)
        data = self.plist()
        data["Program"] = "/tmp/evil"
        data["WorkingDirectory"] = "/tmp"
        data["EnvironmentVariables"] = {"BUDGIT_HOME": self.bhome, "DYLD_INSERT_LIBRARIES": "/tmp/x.dylib"}
        with open(path, "wb") as f:
            plistlib.dump(data, f)
        installstep.run(root, "0.13.6", sys.executable)
        plist = self.plist()
        self.assertNotIn("Program", plist)
        self.assertNotIn("WorkingDirectory", plist)
        self.assertEqual(plist["EnvironmentVariables"], {"BUDGIT_HOME": self.bhome})
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 9, "Minute": 30})
        self.assertEqual(plist["Nice"], 10)

    def test_without_fcntl_the_step_is_a_silent_no_op(self):
        import importlib
        root = self.install("0.13.6")
        with mock.patch.dict(sys.modules, {"fcntl": None}):
            importlib.reload(installstep)                  # the module itself imports without fcntl
            self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        importlib.reload(installstep)
        self.assertIsNone(installstep.record_read())
        self.assertEqual(self.hooks_log(), "")

    def test_a_job_is_not_scheduled_from_a_home_the_environment_names(self):
        root = self.install("0.13.6")
        del os.environ["BUDGIT_LAUNCH_AGENTS_DIR"]            # not test mode
        os.environ["BUDGIT_HOUSEKEEPING_LAUNCHCTL"] = "off"
        with mock.patch.object(sys, "platform", "darwin"):
            code, key, fields = housekeeping.schedule("on", os.path.join(root, "bin", "budgit"))
        self.assertEqual((code, key), (1, "hk.sched_failed"))
        self.assertIn("BUDGIT_HOME", fields["why"])
        self.assertIsNone(installstep.record_read())

    def test_test_mode_never_reaches_the_real_launch_agents(self):
        real_home = os.path.join(self.tmp, "realhome")
        real_agents = os.path.join(real_home, "Library", "LaunchAgents")
        os.makedirs(real_agents)
        os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = real_agents          # the pair plus BUDGIT_HOME, as a project's settings could set them
        os.environ["BUDGIT_HOUSEKEEPING_LAUNCHCTL"] = "off"
        root = self.install("0.13.6")
        fake_pw = mock.Mock(pw_dir=real_home)
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            self.assertFalse(housekeeping.test_mode())
            code, key, fields = housekeeping.schedule("on", os.path.join(root, "bin", "budgit"))
        self.assertEqual((code, key), (1, "hk.sched_failed"))
        self.assertEqual(os.listdir(real_agents), [])
        self.assertFalse(os.path.exists(os.path.join(self.bhome, "bin")))
        self.assertIsNone(installstep.record_read())

    def fake_user(self):
        """A pretend passwd home inside the temp tree, with its LaunchAgents and .claude folders."""
        real_home = os.path.join(self.tmp, "realhome")
        os.makedirs(os.path.join(real_home, "Library", "LaunchAgents"))
        os.makedirs(os.path.join(real_home, ".claude", "plugins"))
        return real_home, mock.Mock(pw_dir=real_home)

    def leave_test_mode(self, *names):
        for name in ("BUDGIT_LAUNCH_AGENTS_DIR", "BUDGIT_HOUSEKEEPING_LAUNCHCTL") + names:
            os.environ.pop(name, None)

    def test_test_mode_is_an_allow_list_by_file_identity(self):
        real_home, fake_pw = self.fake_user()
        real_agents = os.path.join(real_home, "Library", "LaunchAgents")
        os.environ["BUDGIT_HOUSEKEEPING_LAUNCHCTL"] = "off"
        variants = []
        if os.path.exists(os.path.join(real_home, "LIBRARY", "launchagents")):             # a case-folding disk (macOS default)
            variants.append(os.path.join(real_home, "LIBRARY", "launchagents"))
        if os.path.exists("/System/Volumes/Data" + real_home):                              # the firmlink to the same folder
            variants.append("/System/Volumes/Data" + real_home + "/Library/LaunchAgents")
        with mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            for path in [real_agents] + variants:
                os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = path
                self.assertFalse(housekeeping.test_mode(), path)
            os.makedirs(self.agents)
            os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = self.agents                            # a temp-dir folder is test mode
            self.assertTrue(housekeeping.test_mode())
            os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = "/usr/local/agents"                    # anywhere else is not
            self.assertFalse(housekeeping.test_mode())

    def test_home_overridden_to_a_project_folder_gets_no_job(self):
        real_home, fake_pw = self.fake_user()
        root = self.install("0.13.6")                                  # registered in $HOME/.claude, which a project controls
        self.leave_test_mode("BUDGIT_HOME", "CLAUDE_CONFIG_DIR")
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            code, key, fields = housekeeping.schedule("on", os.path.join(root, "bin", "budgit"))
        self.assertEqual((code, key), (1, "hk.sched_failed"))
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(os.path.join(real_home, "Library", "LaunchAgents")), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Library")))

    def test_home_set_to_a_project_below_the_users_home_gets_no_job(self):
        real_home, fake_pw = self.fake_user()
        project = os.path.join(real_home, "proj")
        os.makedirs(project)
        self.cfg = os.path.join(real_home, ".claude")                  # the user's real registry, so only the state home is wrong
        root = self.install("0.13.6")
        self.leave_test_mode("BUDGIT_HOME", "CLAUDE_CONFIG_DIR")
        os.environ["HOME"] = project                                   # a project sets $HOME: the state home would be <project>/.budgit
        plist = os.path.join(real_home, "Library", "LaunchAgents", housekeeping.LABEL + ".plist")
        with open(plist, "wb") as f:
            plistlib.dump({"Label": housekeeping.LABEL, "ProgramArguments": [sys.executable, os.path.join(root, "bin", "budgit")],
                           "StartCalendarInterval": {"Hour": 9, "Minute": 30}, "EnvironmentVariables": {}}, f)
        with open(plist, "rb") as f:
            before = f.read()
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            code, key, fields = housekeeping.schedule("on", os.path.join(root, "bin", "budgit"))
            self.assertEqual((code, key), (1, "hk.sched_failed"))
            with self.assertRaises(housekeeping.JobError):
                housekeeping.repair_job(housekeeping.JOBS["cleanup"], root, sys.executable)
            self.assertIsNone(installstep.run(root, "0.13.6", sys.executable))
        with open(plist, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(project), [])                      # no .budgit, no launcher, no record
        self.assertEqual(os.listdir(os.path.join(real_home, "Library", "LaunchAgents")), [housekeeping.LABEL + ".plist"])

    def test_an_agents_folder_elsewhere_without_launchctl_off_is_refused(self):
        real_home, fake_pw = self.fake_user()
        root = self.install("0.13.6")
        self.leave_test_mode("BUDGIT_HOME", "CLAUDE_CONFIG_DIR")
        os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = self.agents
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            code, key, fields = housekeeping.schedule("on", os.path.join(root, "bin", "budgit"))
        self.assertEqual((code, key), (1, "hk.sched_failed"))
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.agents))

    def test_schedule_on_refuses_when_claude_config_dir_is_set(self):
        real_home, fake_pw = self.fake_user()
        root = self.install("0.13.6")
        self.leave_test_mode("BUDGIT_HOME")                            # CLAUDE_CONFIG_DIR stays set
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            code, key, fields = housekeeping.schedule("on", os.path.join(root, "bin", "budgit"))
        self.assertEqual((code, key), (1, "hk.sched_failed"))
        self.assertIn("CLAUDE_CONFIG_DIR", fields["why"])
        self.assertEqual(self.calls, [])
        self.assertIsNone(installstep.record_read())

    def test_a_recorded_root_outside_the_users_plugin_folder_is_dropped(self):
        real_home, fake_pw = self.fake_user()
        outside = self.install("0.13.9")                               # registered, higher, but under a project-set config folder
        self.leave_test_mode()
        rec = {"root": outside, "version": "0.13.9"}
        with mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            self.assertTrue(installstep._forward("0.13.6", rec))
            self.cfg = os.path.join(real_home, ".claude")              # the same record, under the user's own plugin folder
            os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
            inside = self.install("0.13.9")
            self.assertFalse(installstep._forward("0.13.6", {"root": inside, "version": "0.13.9"}))

    def test_bad_kept_plist_values_fall_back_to_the_defaults(self):
        root = self.install("0.13.6")
        path = self.legacy_plist(root)
        data = self.plist()
        data["StartCalendarInterval"] = {}                             # would run the job every minute
        data["Nice"] = "high"
        data["LowPriorityIO"] = "yes"
        with open(path, "wb") as f:
            plistlib.dump(data, f)
        installstep.run(root, "0.13.6", sys.executable)
        plist = self.plist()
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 9, "Minute": 30})
        self.assertEqual(plist["Nice"], 10)
        self.assertIs(plist["LowPriorityIO"], True)
        data["Nice"] = 99
        data["StartCalendarInterval"] = {"Hour": "9"}
        with open(path, "wb") as f:
            plistlib.dump(data, f)
        installstep.run(self.install("0.13.7"), "0.13.7", sys.executable)
        self.assertEqual(self.plist()["Nice"], 10)
        self.assertEqual(self.plist()["StartCalendarInterval"], {"Hour": 9, "Minute": 30})

    def test_the_base_interpreter_is_saved(self):
        with mock.patch.object(sys, "base_executable", "/opt/base/python3", create=True), mock.patch.object(sys, "executable", "/proj/.venv/bin/python"):
            with mock.patch("os.path.realpath", lambda p: p):
                self.assertEqual(installstep.interpreter(), "/opt/base/python3")

    def make_python(self, mode):
        path = os.path.join(self.tmp, "py%o" % mode)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(path, mode)
        return path

    def test_an_interpreter_writable_by_group_or_others_is_refused(self):
        root = self.install("0.13.6")
        for mode in (0o775, 0o757):
            self.assertFalse(installstep.safe_interpreter(self.make_python(mode)), oct(mode))
        good = self.make_python(0o755)
        self.assertTrue(installstep.safe_interpreter(good))
        installstep.run(root, "0.13.6", self.make_python(0o775))
        self.assertEqual(installstep.record_read()["python"], "")                                # the launcher falls back
        newer = self.install("0.13.7")
        installstep.run(newer, "0.13.7", good)
        self.assertEqual(installstep.record_read()["python"], good)

    def test_an_interpreter_owned_by_someone_else_is_refused(self):
        good = self.make_python(0o755)
        with mock.patch.object(os, "getuid", lambda: os.stat(good).st_uid + 1234):
            self.assertFalse(installstep.safe_interpreter(good))

    def test_the_launcher_is_written_with_its_mode_set_before_the_rename(self):
        seen = []
        real = os.replace

        def replace(src, dst):
            seen.append(os.stat(src).st_mode & 0o777)
            return real(src, dst)
        target = os.path.join(self.tmp, "launcher")
        with mock.patch.object(os, "replace", replace):
            sl.atomic_write(target, "x", 0o700)
        self.assertEqual(seen, [0o700])

    def test_a_normal_user_can_schedule_and_the_update_step_rewrites_the_launcher(self):
        real_home, fake_pw = self.fake_user()
        self.cfg = os.path.join(real_home, ".claude")                  # the registry of the (fake) passwd home
        old = self.install("0.13.5")
        self.leave_test_mode("BUDGIT_HOME", "CLAUDE_CONFIG_DIR", "STUDIO_HOME")
        os.environ["HOME"] = real_home
        agents = os.path.join(real_home, "Library", "LaunchAgents")
        state = os.path.join(real_home, ".budgit")
        launcher = os.path.join(state, "bin", "budgit-job")
        with mock.patch.object(sys, "platform", "darwin"), mock.patch("pwd.getpwuid", lambda uid: fake_pw):
            self.assertFalse(housekeeping.test_mode())
            code, key, fields = housekeeping.schedule("on", os.path.join(old, "bin", "budgit"))
            self.assertNotEqual((code, key), (1, "hk.sched_failed"), fields)
            self.assertEqual(code, 0, (key, fields))
            with open(os.path.join(agents, housekeeping.LABEL + ".plist"), "rb") as f:
                plist = plistlib.load(f)
            self.assertEqual(plist["ProgramArguments"][:2], ["/bin/sh", launcher])
            self.assertTrue(os.path.isfile(launcher))
            with open(launcher) as f:
                self.assertIn(old, f.read())
            scheduled_calls = len(self.calls)
            new = self.install("0.13.6")                               # the update: a higher root, now the only registered one
            shutil.rmtree(old)
            installstep.run(new, "0.13.6", sys.executable)
            with open(launcher) as f:
                text = f.read()
            self.assertIn(new, text)
            self.assertNotIn(old, text)
            self.assertLessEqual(len(self.calls) - scheduled_calls, 2)  # at most bootout plus bootstrap
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".budgit", "bin")))


class LauncherTest(unittest.TestCase):
    def run_launcher(self, text):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "budgit-job")
        with open(path, "w") as f:
            f.write(text)
        return subprocess.run(["/bin/sh", path, "hello"], capture_output=True, text=True, cwd="/", timeout=30), tmp

    def test_launcher_quoting_and_python_fallback(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "it's a root")
        os.makedirs(os.path.join(root, "bin"))
        with open(os.path.join(root, "bin", "budgit"), "w") as f:
            f.write("import sys\nprint('ran', sys.argv[1])\n")
        r, _ = self.run_launcher(housekeeping.launcher_text(root, "/nowhere/python3"))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ran hello"), r.stderr)
        with self.assertRaises(housekeeping.JobError):
            housekeeping.launcher_text(root + "\nrm", sys.executable)

    def test_launcher_with_a_missing_root_logs_and_exits_75(self):
        r, _ = self.run_launcher(housekeeping.launcher_text("/nowhere/root", sys.executable))
        self.assertEqual(r.returncode, 75)
        self.assertIn("the plugin moved", r.stderr)


if __name__ == "__main__":
    unittest.main()
