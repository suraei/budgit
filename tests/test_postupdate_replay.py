"""R-788 update replay on a throwaway HOME: R1-R7 of the design. Written, not yet run. Uses the SessionStart hook as a user's update would."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT
from loadcal import load_factor
import portfolio
import postupdate

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
ASK = "ask the user once"


class Replay(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.agents = os.path.join(self.tmp, "agents")
        os.makedirs(self.agents)
        self.n = 0
        self.hook = HOOK
        self.cfg = None

    def register(self, version="0.13.9"):
        """A copy of the plugin that Claude Code's registry names (as test_housekeeping.ScheduleTest does): a job is only set up from such a copy."""
        self.cfg = os.path.join(self.tmp, "claude-cfg")
        root = os.path.join(self.cfg, "plugins", "cache", "mkt", "budgit", version)
        shutil.copytree(os.path.join(ROOT, "plugin"), root, symlinks=True)
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"plugins": {"budgit@mkt": [{"installPath": root, "version": version}]}}, f)
        self.hook = os.path.join(root, "scripts", "hook.py")

    def env(self):
        env = dict(os.environ, HOME=os.path.join(self.tmp, "uh"), BUDGIT_HOME=self.home, BUDGIT_LANG="en", BUDGIT_LAUNCH_AGENTS_DIR=self.agents,
                   BUDGIT_HOUSEKEEPING_LAUNCHCTL="off")
        if self.cfg:
            env["CLAUDE_CONFIG_DIR"] = self.cfg
        return env

    def session(self, sid=None):
        """One SessionStart as a new session; the clock moves past the 120 s claim window by editing the claim."""
        self.n += 1
        sid = sid or "%08d-0000-4000-8000-000000000000" % self.n
        claim = os.path.join(self.home, "setup-asked.json")
        if os.path.exists(claim):
            os.utime(claim, None)
            with open(claim, "w") as f:
                json.dump({"session": "old", "ts": 1}, f)
        r = subprocess.run([sys.executable, self.hook, "session-start"], capture_output=True, text=True, env=self.env(),
                           input=json.dumps({"session_id": sid, "source": "startup", "cwd": os.path.join(self.tmp, "nowhere")}))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def answer(self, *pairs):
        args = [x for p in pairs for x in ("--answer", p)]
        return subprocess.run([sys.executable, BUDGIT, "setup", *args], capture_output=True, text=True, env=self.env())

    def test_R1_rules_are_injected_for_a_user_who_had_none(self):
        self.assertIn("Budgit rules (core)", self.session())

    def test_R2_the_structure_is_offered(self):
        # R-583 change: also asserts keep-alive is no longer asked (it is on by default); cleanup and standup still are
        question = self.session().split(ASK)[1]
        self.assertIn("structure", question)
        self.assertIn("cleanup", question)
        self.assertIn("standup", question)
        self.assertNotIn("keepalive", question)

    def test_R3_later_none_and_no_schedule_nothing(self):
        # R-583 change: limited to cleanup and standup; keep-alive is default-on and has its own R8/R9
        self.answer("structure=later", "jobs=later")
        self.answer("jobs=no")
        self.answer("cleanup=no", "standup=no")
        self.assertEqual(os.listdir(self.agents), [])

    def test_R8_default_keepalive_one_plist_and_the_off_line_once_over_two_sessions(self):
        self.register()
        first = self.session()
        plists = [f for f in os.listdir(self.agents) if f.startswith("com.budgit.keepalive")]
        self.assertEqual(len(plists), 1, os.listdir(self.agents))
        self.assertEqual(first.count("budgit setup --answer keepalive=no"), 1)
        self.assertIn("budgit setup --answer away=no", first)
        second = self.session()
        self.assertNotIn("budgit setup --answer keepalive=no", second)
        self.assertIn("Budgit rules (core)", second)
        self.assertEqual(len([f for f in os.listdir(self.agents) if f.startswith("com.budgit.keepalive")]), 1)

    def test_R9_keepalive_no_stored_means_no_plist(self):
        self.register()
        self.answer("keepalive=no")
        out = self.session()
        self.assertEqual([f for f in os.listdir(self.agents) if f.startswith("com.budgit.keepalive")], [])
        self.assertNotIn("keep-alive", out)

    def test_hook_time_with_default_keepalive_is_far_under_the_timeout(self):
        import resource
        import time
        self.register()
        w0 = time.time()
        c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.session()
        c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = (c1.ru_utime + c1.ru_stime) - (c0.ru_utime + c0.ru_stime)
        self.assertLess(cpu, 5.0)                      # hook timeout is 10 s: half of it, CPU of the hook process
        self.assertLess(time.time() - w0, 8.0 * load_factor())     # real-deadline budget 8 s scaled by the same-run calibration (R-1282)

    def test_R4_an_existing_cleanup_agent_is_not_asked_about_or_duplicated(self):
        with open(os.path.join(self.agents, "com.budgit.housekeeping.plist"), "wb") as f:
            plistlib.dump({"Label": "com.budgit.housekeeping"}, f)
        old = os.environ.get("BUDGIT_LAUNCH_AGENTS_DIR")
        os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = self.agents
        self.addCleanup(lambda: os.environ.pop("BUDGIT_LAUNCH_AGENTS_DIR", None) if old is None else os.environ.__setitem__("BUDGIT_LAUNCH_AGENTS_DIR", old))
        self.assertNotIn("cleanup", postupdate.unanswered())
        self.assertEqual(os.listdir(self.agents), ["com.budgit.housekeeping.plist"])

    def test_R5_exactly_one_question_line_per_session(self):
        self.assertEqual(self.session().count(ASK), 1)
        self.assertEqual(self.session("00000099-0000-4000-8000-000000000000").count(ASK), 1)

    def test_R6_previous_version_with_no_config_json(self):
        self.assertFalse(os.path.exists(os.path.join(self.home, "config.json")))
        out = self.session()
        self.assertEqual(out.count(ASK), 1)

    def test_R7_later_twice_then_yes_the_job_exists_once_and_the_next_session_is_silent(self):
        portfolio.set_tm("77777777-7777-4777-8777-777777777777")          # structure counts as yes from the machine
        self.answer("cleanup=no", "keepalive=no", "standup=later")
        self.assertIn(ASK, self.session())
        self.answer("standup=later")
        self.assertIn(ASK, self.session())
        r = self.answer("standup=yes")                                    # may be refused on a copy that is not the registered install
        self.assertEqual(postupdate.load()["answers"]["standup"], "yes", r.stdout + r.stderr)
        self.assertLessEqual(len([f for f in os.listdir(self.agents) if f.startswith("com.budgit.standup")]), 1)
        self.assertNotIn(ASK, self.session())


if __name__ == "__main__":
    unittest.main()
