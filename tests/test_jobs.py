"""R-788 stage 2: precheck skips (E8), busy with a fake sampler (E11), setup --answer through the CLI. Written, not yet run."""
import os
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT
import admit
import jobs
import postupdate

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
FREE = lambda: 8000


class PrecheckTest(StudioTestCase):
    def test_PrecheckSkips(self):
        calls = []
        ok = lambda: (False, "free")
        self.assertEqual(jobs.precheck("keepalive", level="critical", busy=ok, claude=sys.executable, todo=lambda n: True)[0], False)
        self.assertEqual(jobs.precheck("standup", level="tight", busy=ok, claude=sys.executable, todo=lambda n: False)[0], False)
        self.assertEqual(jobs.precheck("keepalive", level="tight", busy=ok, claude=sys.executable, todo=lambda n: False)[0], True)
        self.assertEqual(jobs.precheck("keepalive", level="normal", busy=lambda: (True, "cpu"), claude=sys.executable, todo=lambda n: False)[0], False)
        self.assertEqual(jobs.precheck("keepalive", level="normal", busy=ok, claude=sys.executable, todo=lambda n: True)[1], "nothing to do")
        code = jobs.run("keepalive", runner=lambda *a, **k: calls.append(a), level="critical")
        self.assertEqual((code, calls), (jobs.SKIPPED, []))            # zero model calls; a skip is never success (R-874)

    def test_NoClaudeOnPath(self):
        self.assertIsNone(jobs.resolve_claude(which=lambda name: None))
        ok, reason = jobs.precheck("keepalive", level="normal", busy=lambda: (False, "free"), claude="", todo=lambda n: False)
        self.assertFalse(ok)
        self.assertIn("claude", reason)
        calls = []
        self.assertEqual(jobs.run("keepalive", runner=lambda *a, **k: calls.append(a), level="normal", busy=lambda: (False, "free"), claude="/nonexistent/claude"), jobs.SKIPPED)
        self.assertEqual(calls, [])

    def test_PrecheckNoModel(self):
        for name in jobs.MODEL_JOBS:
            text = jobs.prompt_text(name)
            self.assertNotRegex(text, r"[0-9a-f]{8}-[0-9a-f]{4}-")      # no typed session ids
            self.assertNotIn("/Users/", text)


class BusyTest(unittest.TestCase):
    def run_busy(self, samples, free=8000):
        it = iter(samples)
        slept = []
        busy = admit.is_busy(sampler=lambda: next(it), sleep=slept.append, window=60, free=lambda: free)
        return busy, slept

    def test_BusyInjected(self):
        (busy, why), slept = self.run_busy([95] * 12)
        self.assertTrue(busy, why)
        self.assertEqual(len(slept), 11)                                # the window ran on the injected clock
        self.assertFalse(self.run_busy([10] * 12)[0][0])
        self.assertFalse(self.run_busy([100, 100, 100, 100, 100] + [5] * 7)[0][0])   # a median, not a peak
        self.assertTrue(self.run_busy([5] * 12, free=500)[0][0])        # memory under 1 GB
        self.assertEqual(admit.cpu_median(lambda: None, sleep=lambda s: None, window=10), None)


class SetupAnswerCli(StudioTestCase):
    def cli(self, *args):
        # housekeeping.agents_dir reads the passwd home, not $HOME, so the real machine's launch agents must be shut out by this variable
        agents = getattr(self, "agents", None) or os.path.join(self.tmp, "agents")
        env = dict(os.environ, BUDGIT_HOME=self.home, STUDIO_HOME=self.home, BUDGIT_LANG="en", HOME=os.path.join(self.tmp, "uh"),
                   BUDGIT_LAUNCH_AGENTS_DIR=agents)
        return subprocess.run([sys.executable, BUDGIT, "setup", *args], capture_output=True, text=True, env=env)

    def test_answer_records_without_sl_setup_and_a_no_creates_nothing(self):
        r = self.cli("--answer", "structure=yes", "--answer", "keepalive=no")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.home, "config.json")))     # never went through sl.setup
        self.assertEqual(postupdate.load()["answers"], {"structure": "yes", "keepalive": "no"})
        self.assertFalse(os.path.exists(jobs.path()))

    def test_none_and_later_apply_to_the_unanswered_jobs_and_bad_input_is_refused(self):
        self.assertEqual(self.cli("--answer", "cleanup=no").returncode, 0)
        self.assertEqual(self.cli("--answer", "jobs=later").returncode, 0)
        got = postupdate.load()["answers"]
        self.assertEqual((got["cleanup"], got["keepalive"], got["standup"]), ("no", "later", "later"))
        self.assertNotEqual(self.cli("--answer", "keepalive=maybe").returncode, 0)
        self.assertNotEqual(self.cli("--answer", "jobs=yes").returncode, 0)

    def test_jobs_later_skips_only_jobs_with_an_agent_in_the_injected_dir(self):
        self.agents = os.path.join(self.tmp, "agents-with-keepalive")
        os.makedirs(self.agents)
        with open(os.path.join(self.agents, "com.budgit.keepalive.plist"), "w") as f:
            f.write("<plist/>")
        self.assertEqual(self.cli("--answer", "jobs=later").returncode, 0)
        got = postupdate.load()["answers"]
        self.assertNotIn("keepalive", got)                               # a machine with the agent counts as yes
        self.assertEqual((got["cleanup"], got["standup"]), ("later", "later"))
        self.agents = None
        self.assertEqual(self.cli("--answer", "jobs=later").returncode, 0)
        self.assertEqual(postupdate.load()["answers"]["keepalive"], "later")   # an empty injected dir: the real machine does not matter


if __name__ == "__main__":
    unittest.main()
