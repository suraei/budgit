"""R-1158: a brief's call cap is checked against similar past runs; 80 % of the budget says stop; over-budget runs per PM.
The hook runs as a real subprocess with a temp home (STUDIO_HOME from StudioTestCase)."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
import agentcap
import comply
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
SECRET = "PROMPT-TEXT-THAT-MUST-NOT-BE-RECORDED"


class PastRunsTest(StudioTestCase):
    def run_hook(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def runs_file(self):
        return os.path.join(sl.home_state(), "agent-calls", "runs.jsonl")

    def seed(self, rows):
        os.makedirs(os.path.dirname(self.runs_file()), exist_ok=True)
        with open(self.runs_file(), "a") as f:
            for r in rows:
                row = {"type": "budgit:builder", "desc": "Add budget advice to the hook", "budget": 25, "calls": 30,
                       "date": time.strftime("%Y-%m-%d"), "owner": "sess-aaaaaa"}
                row.update(r)
                f.write(json.dumps(row) + "\n")

    def start(self, kind="budgit:builder", prompt="Budget: 25\nbuild it", desc="Budget advice for the cap hook"):
        out = self.run_hook("pre-tool-use", {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": "sess-aaaaaa",
                                              "tool_input": {"subagent_type": kind, "description": desc, "prompt": prompt}})
        return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")

    def test_a_round_end_writes_one_record_line_without_prompt_text(self):
        self.start(prompt="Budget: 25\n" + SECRET)                      # the launch is noted
        base = os.path.join(sl.home_state(), "agent-calls")
        with open(os.path.join(base, "ag1"), "w") as f:
            f.write("33")
        with open(os.path.join(base, "ag1.budget"), "w") as f:
            f.write("25")
        self.run_hook("subagent-stop", {"hook_event_name": "SubagentStop", "agent_id": "ag1", "agent_type": "budgit:builder",
                                         "session_id": "sess-aaaaaa", "last_assistant_message": "done"})
        with open(self.runs_file()) as f:
            raw = f.read()
        lines = raw.splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(set(rec), {"type", "desc", "budget", "calls", "date", "owner"})
        self.assertEqual((rec["type"], rec["budget"], rec["calls"], rec["owner"]), ("budgit:builder", 25, 33, "sess-aaaaaa"))
        self.assertEqual(rec["desc"], "Budget advice for the cap hook")
        self.assertNotIn(SECRET, raw)
        self.assertEqual(rec["date"], time.strftime("%Y-%m-%d"))
        self.assertFalse(os.path.exists(os.path.join(base, "ag1")))     # the counter still goes

    def test_three_similar_runs_give_the_advice_line_with_suggest_48(self):
        self.seed([{"calls": 20}, {"calls": 30}, {"calls": 40}])
        self.assertIn("budgit: similar past runs (3) used median 30, p80 40 calls; your brief says 25; suggest Budget: 48",
                      self.start())

    def test_a_brief_at_or_above_p80_gets_the_line_without_a_suggestion(self):
        self.seed([{"calls": 20}, {"calls": 30}, {"calls": 40}])
        ctx = self.start(prompt="Budget: 50\nbuild it")
        self.assertIn("similar past runs (3) used median 30, p80 40 calls; your brief says 50", ctx)
        self.assertNotIn("suggest Budget", ctx)

    def test_two_similar_runs_say_nothing(self):
        self.seed([{"calls": 20}, {"calls": 30}])
        self.assertNotIn("similar past runs", self.start())

    def test_runs_of_a_different_agent_type_say_nothing(self):
        self.seed([{"calls": 20}, {"calls": 30}, {"calls": 40}])
        self.assertNotIn("similar past runs", self.start(kind="budgit:verifier"))

    def test_runs_sharing_one_word_only_do_not_count(self):
        self.seed([{"calls": 20, "desc": "Budget thing"}, {"calls": 30, "desc": "Budget other"}, {"calls": 40, "desc": "Budget more"}])
        self.assertNotIn("similar past runs", self.start())

    def test_the_80_percent_note_appears_once_at_the_exact_call_and_n_still_says_report_now(self):
        sess = os.path.join(self.home, "proj", "sess80")
        os.makedirs(os.path.join(sess, "subagents"))
        with open(os.path.join(sess, "subagents", "agent-w80.jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "Call budget: 10"}}) + "\n")
        texts = []
        for _ in range(10):
            out = self.run_hook("pre-tool-use", {"hook_event_name": "PreToolUse", "agent_id": "w80", "tool_name": "TodoWrite",
                                                  "tool_input": {}, "transcript_path": sess + ".jsonl", "cwd": self.project})
            texts.append((out.get("hookSpecificOutput") or {}).get("additionalContext", ""))
        note = ("budgit: 80 % of your budget used (8 of 10): stop starting new work; finish the current step, "
                "commit green work, and report what is done and what is left.")
        self.assertEqual([i + 1 for i, t in enumerate(texts) if "80 % of your budget used" in t], [8])
        self.assertIn(note, texts[7])
        self.assertIn("call budget 10 reached", texts[9])                 # N: unchanged
        self.assertEqual((10 * agentcap.WARN_PERCENT) // 100, 8)

    def test_over_budget_runs_are_counted_per_pm_session_for_7_days(self):
        owner = "sess-abc123456"
        self.seed([{"owner": owner, "budget": 25, "calls": 40}] * 3 + [{"owner": owner, "budget": 25, "calls": 20}] * 8
                  + [{"owner": owner, "budget": 25, "calls": 99, "date": "2000-01-01"}]       # older than 7 days: not counted
                  + [{"owner": "other-zzzzzz", "budget": 0, "calls": 50}])                     # no stated budget: never over
        self.assertIn("over budget: 3 of 11 runs (PM session 123456)", comply.over_budget_lines())
        self.assertIn("over budget: 0 of 1 runs (PM session zzzzzz)", comply.over_budget_lines())
        self.assertIn("over budget: 3 of 11 runs (PM session 123456)", comply.table([]))

    def good(self, calls):
        return json.dumps({"type": "budgit:builder", "desc": "Add budget advice to the hook", "budget": 25, "calls": calls,
                           "date": time.strftime("%Y-%m-%d"), "owner": "sess-aaaaaa"}) + "\n"

    def write_raw(self, chunks, name="runs.jsonl"):
        os.makedirs(os.path.dirname(self.runs_file()), exist_ok=True)
        with open(os.path.join(os.path.dirname(self.runs_file()), name), "wb") as f:
            for c in chunks:
                f.write(c)

    def test_a_corrupt_record_file_still_gives_the_advice_from_its_good_lines(self):
        self.write_raw([b"\xff\xfe\x80 not utf-8\n", b"{broken json\n", self.good(20).encode(), b"\xc3\x28\n",
                        self.good(30).encode(), b"[1, 2]\n", self.good(40).encode()])
        self.assertIn("similar past runs (3) used median 30, p80 40 calls; your brief says 25; suggest Budget: 48", self.start())

    def test_only_the_tail_of_a_big_record_is_read(self):
        junk = (b"x" * 99 + b"\n") * 20000                                   # about 2 MB of lines that do not parse
        self.write_raw([self.good(20).encode(), self.good(30).encode(), self.good(40).encode(), junk])
        self.assertGreater(os.path.getsize(self.runs_file()), 2 * agentcap.TAIL_BYTES)
        self.assertNotIn("similar past runs", self.start())                 # the good lines sit before the tail: not read
        self.write_raw([junk, self.good(20).encode(), self.good(30).encode(), self.good(40).encode()])
        self.assertIn("similar past runs (3) used median 30, p80 40", self.start())

    def test_a_record_over_1_mb_is_rotated_at_write_time(self):
        self.write_raw([(b"y" * 99 + b"\n") * 11000])                       # just over 1 MB
        base = os.path.join(sl.home_state(), "agent-calls")
        old = os.path.getsize(self.runs_file())
        self.assertGreater(old, agentcap.ROTATE_BYTES)
        with open(os.path.join(base, "ag2"), "w") as f:
            f.write("12")
        self.run_hook("subagent-stop", {"hook_event_name": "SubagentStop", "agent_id": "ag2", "agent_type": "budgit:builder",
                                         "session_id": "sess-aaaaaa"})
        self.assertEqual(os.path.getsize(self.runs_file() + ".1"), old)
        with open(self.runs_file()) as f:
            self.assertEqual(len(f.read().splitlines()), 1)

    def test_the_newest_launch_note_is_used(self):
        for d in ("Older launch of the hook", "Newest launch of the hook"):
            self.start(desc=d)
        with open(os.path.join(sl.home_state(), "agent-calls", "ag3"), "w") as f:
            f.write("5")
        self.run_hook("subagent-stop", {"hook_event_name": "SubagentStop", "agent_id": "ag3", "agent_type": "budgit:builder",
                                         "session_id": "sess-aaaaaa"})
        with open(self.runs_file()) as f:
            self.assertEqual(json.loads(f.readlines()[-1])["desc"], "Newest launch of the hook")
