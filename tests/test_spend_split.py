"""R-273 / R-274: tools/tokens/spend_split.py answers where the spend goes: by cost component, by context size, by answer length, and
how much of it sits in sessions that read a PDF. A zero-token meter over Claude Code transcripts, read-only."""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import time
import unittest

from helpers import ROOT

TOOL = os.path.join(ROOT, "tools", "tokens")
sys.path.insert(0, TOOL)
import spend_split as ss


def rec(mid, ctx_in=0, cw=0, cr=0, out=0, content=None, model="claude-sonnet-5-5", ts="2026-10-25T10:00:00Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "content": content or [{"type": "text", "text": "hi"}],
            "usage": {"input_tokens": ctx_in, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": out}}}


class SpendSplitTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, project, sid, records, sub=None):
        d = os.path.join(self.root, project)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        for name, srecs in (sub or {}).items():
            sd = os.path.join(d, sid, "subagents")
            os.makedirs(sd, exist_ok=True)
            with open(os.path.join(sd, "agent-%s.jsonl" % name), "w") as f:
                for r in srecs:
                    f.write(json.dumps(r) + "\n")

    def test_components_are_weighted_with_the_price_ratios(self):
        self.write("p", "s1", [rec("m1", ctx_in=1000, cr=10000, out=100)])
        r = ss.split(self.root, days=7)
        self.assertEqual(r["components"]["input"], 1000.0)
        self.assertEqual(r["components"]["cache_read"], 1000.0)       # 10000 x 0.1
        self.assertEqual(r["components"]["output"], 500.0)            # 100 x 5
        self.assertEqual(r["weighted"], 2500.0)

    def test_one_call_logged_per_content_block_is_counted_once(self):
        a = rec("m1", cr=1000, out=10, content=[{"type": "thinking", "thinking": "..."}])
        b = rec("m1", cr=1000, out=10, content=[{"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}}])
        self.write("p", "s1", [a, b])
        r = ss.split(self.root, days=7)
        self.assertEqual(r["calls"], 1)
        self.assertEqual(r["answers"]["tool_calls"], 1)                # the tool_use block in the second record marks the call

    def test_context_buckets_show_where_long_chats_cost(self):
        self.write("p", "s1", [rec("a", cr=50_000), rec("b", cr=150_000), rec("c", cr=300_000), rec("d", cr=500_000)])
        b = {x["label"]: x for x in ss.split(self.root, days=7)["context"]}
        self.assertEqual([b[k]["calls"] for k in ("<=100k", "100-200k", "200-400k", ">400k")], [1, 1, 1, 1])
        self.assertAlmostEqual(b[">400k"]["share"], 50_000 / (5_000 + 15_000 + 30_000 + 50_000), places=3)

    def test_what_if_every_call_stayed_under_200k_shows_the_saving_from_compacting(self):
        self.write("p", "s1", [rec("a", cr=100_000), rec("b", cr=500_000)])        # 10k + 50k weighted; b's excess over 200k = 300k x 0.1
        r = ss.split(self.root, days=7)
        self.assertAlmostEqual(r["cap_saving"], 30_000 / 60_000, places=3)

    def test_answers_split_text_only_from_tool_calls_and_count_the_long_ones(self):
        tool = [{"type": "tool_use", "name": "Bash", "input": {}}]
        self.write("p", "s1", [rec("a", out=200), rec("b", out=2000), rec("c", out=3000, content=tool)])
        a = ss.split(self.root, days=7)["answers"]
        self.assertEqual((a["text_calls"], a["tool_calls"], a["long_text_calls"]), (2, 1, 1))
        self.assertEqual(a["text_median"], 1100)

    def test_a_session_that_read_a_pdf_is_flagged_and_its_spend_counted(self):
        pdf = [{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/report.pdf"}}]
        self.write("p", "s1", [rec("a", cr=10_000, content=pdf)])
        self.write("p", "s2", [rec("b", cr=10_000)])
        p = ss.split(self.root, days=7)["pdf"]
        self.assertEqual((p["sessions"], p["reads"]), (1, 1))
        self.assertAlmostEqual(p["share"], 0.5, places=3)

    def test_a_pdf_document_block_in_a_user_message_counts(self):
        user = {"type": "user", "message": {"role": "user", "content": [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf"}}]}}
        self.write("p", "s1", [user, rec("a", cr=10_000)])
        self.assertEqual(ss.split(self.root, days=7)["pdf"]["reads"], 1)

    def test_subagents_are_counted_and_split_from_the_main_session(self):
        self.write("p", "s1", [rec("a", cr=10_000)], sub={"x": [rec("z", cr=30_000)]})
        r = ss.split(self.root, days=7)
        self.assertEqual(r["calls"], 2)
        self.assertAlmostEqual(r["subagent_share"], 0.75, places=3)

    def test_project_filter_and_age_window(self):
        self.write("-Users-x-Documents-Dev-studio", "s1", [rec("a", cr=10_000)])
        self.write("-Users-x-Documents-Dev-other", "s2", [rec("b", cr=10_000)])
        old = os.path.join(self.root, "-Users-x-Documents-Dev-studio", "s1.jsonl")
        self.assertEqual(ss.split(self.root, days=7, project="Dev-studio")["calls"], 1)
        os.utime(old, (time.time() - 30 * 86400,) * 2)
        self.assertEqual(ss.split(self.root, days=7, project="Dev-studio")["calls"], 0)

    def test_an_empty_or_broken_transcript_does_not_crash(self):
        d = os.path.join(self.root, "p")
        os.makedirs(d)
        open(os.path.join(d, "s1.jsonl"), "w").write("not json\n{}\n")
        r = ss.split(self.root, days=7)
        self.assertEqual(r["calls"], 0)
        self.assertIn("0 calls", ss.render(r))

    def test_the_command_line_prints_a_report_and_writes_nothing(self):
        self.write("p", "s1", [rec("a", cr=10_000, out=50)])
        before = sorted(os.listdir(os.path.join(self.root, "p")))
        r = subprocess.run([sys.executable, os.path.join(TOOL, "spend_split.py"), self.root, "--days", "7"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cache_read", r.stdout)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "p"))), before)


if __name__ == "__main__":
    unittest.main()
