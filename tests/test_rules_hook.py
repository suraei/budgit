"""R-788 step 3: SessionStart injection per role, compact resend, one question directive (E1, E4, R7-style clock)."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import postupdate
import portfolio
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


class RulesHook(StudioTestCase):
    def start(self, sid, source="startup", cwd=None):
        env = dict(os.environ, HOME=os.path.join(self.tmp, "uh"), BUDGIT_HOME=self.home, BUDGIT_LANG="en")
        env.pop("BUDGIT_SESSION", None)
        r = subprocess.run([sys.executable, HOOK, "session-start"], input=json.dumps(
            {"session_id": sid, "source": source, "cwd": cwd or os.path.join(self.tmp, "nowhere")}),
            capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_plain_chat_gets_core_only_and_one_question_directive_with_the_nudge(self):
        out = self.start("11111111-1111-4111-8111-111111111111")
        self.assertIn("Budgit rules (core)", out)
        self.assertNotIn("- release:", out)
        self.assertEqual(out.count("ask the user once"), 1)
        self.assertEqual(out.count("not set up on this machine"), 1)

    def test_same_session_silent_second_time_and_rules_resent_on_compact(self):
        sid = "22222222-2222-4222-8222-222222222222"
        self.start(sid)
        again = self.start(sid, source="compact")
        self.assertIn("Budgit rules (core)", again)
        self.assertNotIn("ask the user once", again)
        self.assertNotIn("Tell the user once", again)

    def test_coordinator_digest_for_the_tm_and_off_topic_omitted(self):
        sid = "33333333-3333-4333-8333-333333333333"
        portfolio.set_tm(sid)
        postupdate.set_topic("jobs", True)
        out = self.start(sid)
        self.assertIn("Budgit rules (coordinator)", out)
        self.assertNotIn("- jobs:", out)

    def test_answered_keys_are_never_asked_and_rules_off_injects_nothing(self):
        for k in postupdate.KEYS:
            postupdate.answer(k, "no")
        out = self.start("44444444-4444-4444-8444-444444444444")
        self.assertNotIn("ask the user once", out)
        postupdate.update(lambda d: d["rules"].__setitem__("enabled", False))
        self.assertNotIn("Budgit rules", self.start("55555555-5555-4555-8555-555555555555"))

    def test_later_is_asked_again_after_the_window(self):
        postupdate.answer("standup", "later")        # R-583 change: keepalive is default-on, never asked
        self.assertTrue(postupdate.claim("a", 1000.0))
        self.assertFalse(postupdate.claim("b", 1000.0 + postupdate.ASK_WINDOW_S - 1))
        self.assertTrue(postupdate.claim("b", 1000.0 + postupdate.ASK_WINDOW_S + 1))
        self.assertIn("standup", postupdate.unanswered())
