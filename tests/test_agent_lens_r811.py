"""R-811: plan-critic and verifier carry the "what varies and what is assumed" lens, and the
checker flags a hard-coded local figure but not a run-time reading."""
import os
import subprocess
import sys
import tempfile
import unittest
from helpers import ROOT
import varies

HEADING = "## What varies and what is assumed"
# one keyword group per duty: list, tag, walk, assumptions, unasked question
DUTIES = {
    "list": ["changes per user and machine", "at run time"],
    "tag": ["measured on this machine", "true everywhere"],
    "walk": ["3 different users", "what breaks"],
    "assumptions": ["assumptions", "false"],
    "question": ["question nobody asked"],
}


def agent_text(name, root=ROOT):
    with open(os.path.join(root, "plugin", "agents", name), encoding="utf-8") as f:
        return f.read()


def missing_duties(text):
    if HEADING not in text:
        return ["heading"]
    section = text.split(HEADING, 1)[1]
    return [d for d, kws in DUTIES.items() if not all(k in section for k in kws)]


class AgentLensTest(unittest.TestCase):
    def test_both_agents_have_heading_and_five_duties(self):
        for name in ("plan-critic.md", "verifier.md"):
            self.assertEqual(missing_duties(agent_text(name)), [], name)

    def test_removing_walk_duty_is_detected(self):
        text = agent_text("verifier.md").replace("3 different users", "some users")
        self.assertEqual(missing_duties(text), ["walk"])

    def test_hard_coded_worker_count_is_flagged(self):
        hits = varies.find_local_constants("use 8 workers (my Mac has 8 cores)")
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(varies.find_local_constants("This machine has 16 GB.")), 1)

    def test_run_time_reading_is_not_flagged(self):
        self.assertEqual(varies.find_local_constants(
            "workers = usable CPUs from os.process_cpu_count()"), [])
        self.assertEqual(varies.find_local_constants(
            "My Mac has 8 cores, so read os.cpu_count() at run time."), [])

    def test_spec_limit_without_local_marker_is_not_flagged(self):
        self.assertEqual(varies.find_local_constants("The request times out after 30 s."), [])

    def test_spanish_units_are_flagged(self):
        self.assertTrue(varies.find_local_constants("en mi caso 8 núcleos"))
        self.assertTrue(varies.find_local_constants("Mi máquina: 8 hilos"))

    def test_first_person_is_flagged(self):
        self.assertTrue(varies.find_local_constants("I have 8 cores"))
        self.assertTrue(varies.find_local_constants("I've got 16 GB"))

    def test_the_bare_word_runtime_does_not_suppress_a_hit(self):
        self.assertTrue(varies.find_local_constants("runtime: use 8 workers on my Mac"))
        self.assertEqual(varies.find_local_constants(
            "Mi máquina tiene 8 núcleos, detected at run time"), [])

    def test_here_is_the_plan_is_not_a_marker(self):
        self.assertEqual(varies.find_local_constants("Here is the plan: retry after 3 s"), [])
        self.assertEqual(varies.find_local_constants("Here are 3 s of slack."), [])
        self.assertTrue(varies.find_local_constants("Here we use 8 workers."))

    def test_budgit_varies_command_reports_and_sets_exit_code(self):
        budgit = os.path.join(ROOT, "plugin", "bin", "budgit")
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.md")
            ok = os.path.join(d, "ok.md")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("use 8 workers (my Mac has 8 cores)\n")
            with open(ok, "w", encoding="utf-8") as f:
                f.write("workers = usable CPUs from os.process_cpu_count()\n")
            r = subprocess.run([sys.executable, budgit, "varies", bad], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("LOCAL CONSTANT", r.stdout)
            r = subprocess.run([sys.executable, budgit, "varies", ok], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_budgit_varies_on_a_missing_file_exits_2_with_one_error_line(self):
        budgit = os.path.join(ROOT, "plugin", "bin", "budgit")
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, budgit, "varies", os.path.join(d, "nope.md")],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn("cannot read", r.stderr)

    def test_no_agent_file_names_the_repo_relative_scripts_path(self):
        agents = os.path.join(ROOT, "plugin", "agents")
        for name in os.listdir(agents):
            self.assertNotIn("plugin/scripts/", agent_text(name), name)

    def test_agents_call_budgit_varies(self):
        for name in ("plan-critic.md", "verifier.md"):
            self.assertIn("budgit varies", agent_text(name), name)

    def test_other_shapes_are_flagged(self):
        self.assertTrue(varies.find_local_constants("In my case workers: 8\nnext line"))
        self.assertTrue(varies.find_local_constants("En mi caso 16 GB de memoria."))


if __name__ == "__main__":
    unittest.main()
