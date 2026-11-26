"""R-283 S1: the team structure (Team Manager, Project Managers, lanes), the one-PM-per-project rule (R-280) and one
Definition of Workflow (R-284, R-285) ship in the kickoff skill, so every PM reads them, not only one session's memory."""
import os
import re
import unittest

from helpers import ROOT

K = os.path.join(ROOT, "skills", "budgit-kickoff")


def read(rel):
    with open(os.path.join(K, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


def section(text, title):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(title), text, re.S | re.M)
    return m.group(1) if m else ""


class RoutingSessionsTest(unittest.TestCase):
    def setUp(self):
        self.text = read("reference/routing.md")
        self.sessions = section(self.text, "Sessions")

    def test_sessions_section_states_the_one_pm_rule(self):
        s = self.sessions
        self.assertTrue(s, "routing.md has a `## Sessions` section")
        self.assertIn("One Project Manager (PM) per project", s)
        self.assertIn("opened in the project's own folder", s)
        self.assertIn("`PM · <project>`", s)

    def test_sessions_section_covers_lanes_archive_and_renewal(self):
        s = self.sessions
        self.assertIn("started by its PM", s)
        self.assertIn("archived", s)
        self.assertIn("handoff", s)
        self.assertIn("reference/portfolio.md", s)

    def test_a_lane_waiting_only_on_her_is_archived_after_4_hours(self):
        s = self.sessions                                   # R-297
        self.assertIn("more than 4 hours", s)
        self.assertIn('"Wait for you" list', s)
        self.assertIn("a new lane opens from that handoff", s)

    def test_every_lane_brief_claims_a_task_in_every_project(self):
        brief = section(self.text, "In every lane brief")
        self.assertIn("`budgit ledger start R-n`", brief)
        self.assertIn("in every project", brief)
        self.assertIn("`budgit ledger add`", brief)


class PortfolioReferenceTest(unittest.TestCase):
    def setUp(self):
        self.text = read("reference/portfolio.md")

    def test_three_levels_each_with_talks_decides_never(self):
        for level in ("Team Manager", "Project Manager", "Lane"):
            self.assertRegex(self.text, r"\| \*\*%s\*\* \|" % level, level)
        for column in ("Talks to", "Decides", "Never"):
            self.assertIn(column, self.text)

    def test_definition_of_workflow_separates_tasks_from_chats(self):
        dow = section(self.text, "Definition of Workflow")
        self.assertTrue(dow)
        for word in ("task", "chat", "project", "in progress", "to do", "idea", "working", "waiting", "resting"):
            self.assertIn(word, dow, word)
        self.assertIn("A chat is never \"in progress\"", dow)
        self.assertIn("names its project", dow)
        self.assertIn("lives in its ledger", dow)

    def test_no_task_is_shown_and_nudged_not_blocked(self):
        self.assertIn("\"no task\"", self.text)
        self.assertIn("never blocked", self.text)

    def test_flow_is_continuous_with_no_weekly_meeting(self):
        flow = section(self.text, "How work flows")
        self.assertIn("No weekly meeting", flow)
        self.assertIn("pulls the next task", flow)

    def test_renewal_and_sweep_procedures(self):
        self.assertTrue(section(self.text, "Renewal"))
        self.assertTrue(section(self.text, "Sweeps"))

    def test_generic_wording(self):
        self.assertNotRegex(self.text, r"\b(she|her|hers|he|him|his)\b")
        self.assertNotRegex(self.text, r"\b20\d\d-\d\d-\d\d\b")
        self.assertNotRegex(self.text, r"\b[RIL]-\d{1,4}\b")


class CoordinationPointsToPortfolioTest(unittest.TestCase):
    def test_sweeps_use_the_portfolio_not_the_hub_folder(self):
        sweeps = section(read("reference/coordination.md"), "Sweeps")
        self.assertIn("`budgit portfolio`", sweeps)
        self.assertNotIn("The projects folder under `budgit hub` lists them", sweeps)
        self.assertIn("reference/portfolio.md", read("reference/coordination.md"))


if __name__ == "__main__":
    unittest.main()
