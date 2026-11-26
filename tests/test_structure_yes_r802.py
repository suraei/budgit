"""R-802: a yes to the team-structure question leaves a working lead chat, nothing for the user to type."""
import json
import os
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT
import portfolio

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
S = "11111111-0000-4000-8000-000000000000"
T = "22222222-0000-4000-8000-000000000000"
P = "33333333-0000-4000-8000-000000000000"
LEAD_LINE = "Budgit rules (coordinator)"


class StructureYes(StudioTestCase):
    def run_budgit(self, *args, session=None, cwd=None):
        env = dict(os.environ, HOME=os.path.join(self.tmp, "uh"), BUDGIT_HOME=self.home, BUDGIT_LANG="en")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([sys.executable, BUDGIT, *args], capture_output=True, text=True, env=env, cwd=cwd)

    def plain_folder(self):
        d = os.path.join(self.tmp, "plain")
        os.makedirs(d, exist_ok=True)
        return d

    def test_yes_in_a_project_chat_makes_it_that_projects_pm_not_the_lead(self):
        self.init_project()
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], S)
        self.assertIsNone(portfolio.tm_session())
        self.assertIn(os.path.realpath(self.project), r.stdout)
        self.assertIn("detach", r.stdout)

    def test_the_new_lead_chat_then_becomes_the_lead_and_keeps_the_pm(self):
        self.init_project()
        self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        r = self.run_budgit("setup", "--answer", "structure=yes", session=T, cwd=self.plain_folder())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), T)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], S)

    def test_yes_in_a_plain_chat_still_makes_it_the_lead(self):
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.plain_folder())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), S)

    def test_setup_doc_says_detach_and_offers_proposed_projects(self):
        with open(os.path.join(ROOT, "skills", "budgit-kickoff", "reference", "setup.md"), encoding="utf-8") as f:
            text = f.read()
        sec = text.split("## 4.", 1)[1]
        for needle in ("detach", "budgit portfolio pm .", "proposes", "only after the user's own yes"):
            self.assertIn(needle, sec)

    def start(self, sid):
        env = dict(os.environ, HOME=os.path.join(self.tmp, "uh"), BUDGIT_HOME=self.home, BUDGIT_LANG="en")
        r = subprocess.run([sys.executable, HOOK, "session-start"], capture_output=True, text=True, env=env,
                           input=json.dumps({"session_id": sid, "source": "startup", "cwd": os.path.join(self.tmp, "nowhere")}))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_structure_yes_makes_this_chat_the_lead(self):
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.plain_folder())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), S)
        self.assertIn("Team Manager chat: " + S[:8], r.stdout)
        self.assertIn(LEAD_LINE, self.start(S))

    def test_structure_yes_never_overwrites_a_lead(self):
        portfolio.set_tm(T)
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), T)

    def test_single_is_recorded_only_when_picked_and_offered_again(self):
        import postupdate
        r = self.run_budgit("setup", "--answer", "structure=single", session=S, cwd=self.plain_folder())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), S)
        self.assertEqual(postupdate.state("structure"), "single")
        self.assertIn("about: structure", self.start(T))

    def _pms(self):
        return {e["path"]: e.get("pm") for e in portfolio.registry()["projects"]}

    def test_existing_pm_is_never_replaced(self):
        self.init_project()
        portfolio.add(self.project, None)
        portfolio.set_pm(self.project, P)
        portfolio.set_tm(T)
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], P)
        self.assertIn("already has its lead chat (" + P[:8], r.stdout)
        self.assertEqual(portfolio.tm_session(), T)

    def test_kept_pm_without_a_lead_still_asks_for_the_lead_chat(self):
        self.init_project()
        portfolio.add(self.project, None)
        portfolio.set_pm(self.project, P)
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], P)
        self.assertIsNone(portfolio.tm_session())
        self.assertIn("open ONE new top-level chat", r.stdout)

    def test_yes_twice_is_idempotent(self):
        plain = self.plain_folder()
        self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=plain)
        before = self._pms()
        self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=plain)
        self.assertEqual(portfolio.tm_session(), S)
        self.assertEqual(self._pms(), before)
        self.init_project()
        self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], S)
        again = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], S)
        self.assertNotIn("open ONE new top-level chat", again.stdout)     # the TM from the first plain yes exists

    def test_yes_in_a_project_chat_with_a_lead_elsewhere_keeps_that_lead(self):
        self.init_project()
        portfolio.set_tm(T)
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), T)
        self.assertEqual(portfolio.find(portfolio.registry(), self.project)["pm"], S)
        self.assertNotIn("open ONE new top-level chat", r.stdout)
        self.assertIn(T[:8], r.stdout)

    def test_every_project_keeps_its_entry(self):
        self.init_project()
        b = os.path.join(self.tmp, "proj-b")
        c = os.path.join(self.tmp, "cofre")
        os.makedirs(b)
        os.makedirs(c)
        portfolio.add(self.project, None)
        portfolio.add(b, None)
        portfolio.add(c, None)
        portfolio.set_pm(b, P)
        self.assertEqual(self.run_budgit("portfolio", "protect", "cofre").returncode, 0)
        before = self._pms()
        self.assertEqual(len(before), 3)
        r = self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self._pms()
        self.assertEqual(set(after), set(before))
        for path in (b, c):
            real = os.path.realpath(path)
            self.assertEqual(after[real], before[real])
        self.assertEqual(after[portfolio.find(portfolio.registry(), self.project)["path"]], S)

    def test_setup_doc_says_nothing_is_lost(self):
        sec = self._section4()
        for needle in ("list_sessions", "never dropped silently", "none gets a second one", "archived or gone"):
            self.assertIn(needle, sec)

    def test_setup_doc_keeps_protected_titles_neutral(self):
        self.assertIn("private~hash", self._section4())

    def test_single_is_refused_for_any_other_key(self):
        r = self.run_budgit("setup", "--answer", "keepalive=single", session=S, cwd=self.plain_folder())
        self.assertNotEqual(r.returncode, 0)

    def test_postupdate_refuses_single_for_any_key_but_structure(self):
        import postupdate
        import studiolib as sl
        with self.assertRaises(sl.StudioError):
            postupdate.answer("keepalive", "single")
        postupdate.save({"rules": {"enabled": True, "off": [], "told": ""},
                         "answers": {"keepalive": "single", "structure": "single"}})
        answers = postupdate.load()["answers"]
        self.assertNotIn("keepalive", answers)
        self.assertEqual(answers["structure"], "single")
        postupdate.answer("structure", "single")
        self.assertEqual(postupdate.load()["answers"]["structure"], "single")

    def _section4(self):
        with open(os.path.join(ROOT, "skills", "budgit-kickoff", "reference", "setup.md"), encoding="utf-8") as f:
            return f.read().split("## 4.", 1)[1]

    def test_setup_doc_has_the_permission_card_and_no_silent_fallback(self):
        sec = self._section4()
        for needle in ("never fall back silently", "Permitir y montarlo", "Seguir en un solo chat", "structure=single",
                       "mcp__ccd_session__start_session", "Code tab", "no wildcard"):
            self.assertIn(needle, sec)
        self.assertNotIn("Without session tools: single mode", sec)

    def test_setup_doc_says_claude_opens_the_lead_itself(self):
        sec = self._section4()
        for needle in ("Claude itself opens ONE new top-level session", "detach_session", "The user does nothing for this step"):
            self.assertIn(needle, sec)

    def test_structure_yes_without_a_session_binds_nothing_and_says_so(self):
        r = self.run_budgit("setup", "--answer", "structure=yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(portfolio.tm_session())
        self.assertIn("next answer from inside a chat", r.stdout)

    def test_portfolio_pm_defaults_to_this_chat(self):
        proj = os.path.join(self.tmp, "proj-a")
        os.makedirs(proj)
        self.assertEqual(self.run_budgit("portfolio", "add", proj).returncode, 0)
        bad = self.run_budgit("portfolio", "pm", proj)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("session", bad.stdout + bad.stderr)
        r = self.run_budgit("portfolio", "pm", proj, session=P)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.find(portfolio.registry(), proj)["pm"], P)

    def test_structure_yes_then_a_project_chat_binds_as_pm(self):
        proj = os.path.join(self.tmp, "proj2")
        os.makedirs(proj)
        self.assertEqual(self.run_budgit("setup", "--answer", "structure=yes", session=S, cwd=self.plain_folder()).returncode, 0)
        self.assertEqual(self.run_budgit("portfolio", "add", proj).returncode, 0)
        r = self.run_budgit("portfolio", "pm", proj, session=P)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(portfolio.tm_session(), S)
        self.assertEqual(portfolio.find(portfolio.registry(), proj)["pm"], P)


if __name__ == "__main__":
    unittest.main()
