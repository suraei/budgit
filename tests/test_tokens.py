import json, os, sys, tempfile, unittest
from helpers import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools", "tokens"))
import tokens  # noqa: E402


def rec(mid, model="claude-opus-5-5", out=10, cr=100, cw1h=0, inp=2, ts="2026-10-12T10:00:00.000Z"):
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "usage": {
        "input_tokens": inp, "cache_creation_input_tokens": cw1h, "cache_read_input_tokens": cr,
        "output_tokens": out, "cache_creation": {"ephemeral_1h_input_tokens": cw1h, "ephemeral_5m_input_tokens": 0}}}})


class TokensTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        proj = os.path.join(self.root, "-Users-x-Documents-Dev")
        os.makedirs(os.path.join(proj, "s1", "subagents"))
        with open(os.path.join(proj, "s1.jsonl"), "w") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": "Studio team"}) + "\n")
            # one API call logged twice (two content blocks) must count once
            f.write(rec("m1", cw1h=1000) + "\n" + rec("m1", cw1h=1000) + "\n" + rec("m2") + "\n")
        with open(os.path.join(proj, "s1", "subagents", "agent-a.jsonl"), "w") as f:
            f.write(rec("x1", model="claude-sonnet-5-5", out=100) + "\n")
        json.dump({"agentType": "budgit:researcher"},
                  open(os.path.join(proj, "s1", "subagents", "agent-a.meta.json"), "w"))

    def test_project_name_drops_the_current_homes_documents_prefix_built_at_run_time(self):
        proj = os.path.join(self.root, os.path.expanduser("~").replace(os.sep, "-") + "-Documents-Dev")
        os.makedirs(proj)
        with open(os.path.join(proj, "s9.jsonl"), "w") as f:
            f.write(rec("p1") + "\n")
        sessions, _ = tokens.scan(self.root, days=30)
        self.assertIn("Dev", [s["project"] for s in sessions])

    def test_forked_session_does_not_double_count_parent_calls(self):
        proj = os.path.join(self.root, "-Users-x-Documents-Dev")
        with open(os.path.join(proj, "s2.jsonl"), "w") as f:
            f.write(rec("m1", cw1h=1000) + "\n" + rec("m9") + "\n")   # fork: copies m1, adds m9
        sessions, _ = tokens.scan(self.root, days=30)
        calls = sum(m["calls"] for s in sessions for m in s["models"].values())
        self.assertEqual(calls, 3)   # m1, m2, m9

    def test_by_day_attributes_spend_to_the_record_timestamp_not_the_file_mtime(self):
        proj = os.path.join(self.root, "-Users-x-Documents-Dev")
        with open(os.path.join(proj, "s3.jsonl"), "w") as f:
            f.write(rec("d1", ts="2026-10-11T23:59:00.000Z") + "\n" + rec("d2", ts="2026-10-12T00:01:00.000Z") + "\n"
                    + rec("d3", ts="2026-10-12T00:02:00.000Z") + "\n")
        days = tokens.by_day(self.root, days=3650)
        self.assertEqual(days["2026-10-11"]["calls"], 1)
        self.assertEqual(days["2026-10-12"]["calls"], 2 + 3)   # s3: d2,d3 + s1: m1,m2 and the subagent x1

    def test_top_agents_ranks_individual_subagents_by_spend_with_calls_and_avg_context(self):
        rows = tokens.top_agents(self.root, days=3650)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["calls"], 1)
        self.assertEqual(rows[0]["kind"], "budgit:researcher")
        self.assertEqual(rows[0]["session"], "Studio team")

    def test_dedupes_calls_weights_cache_and_attributes_subagents(self):
        sessions, agents = tokens.scan(self.root, days=30)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        opus = s["models"]["claude-opus-5-5"]
        self.assertEqual(opus["calls"], 2)
        self.assertEqual(opus["cw1h"], 1000)
        # m1: 2 + 1000*2 + 100*0.1 + 10*5 ; m2: 2 + 10 + 50
        self.assertAlmostEqual(opus["weighted"], 2062 + 62)
        self.assertEqual(s["title"], "Studio team")
        self.assertEqual(agents["budgit:researcher (sonnet-5-5)"]["output"], 100)
        self.assertIn("Studio team", tokens.report(sessions, agents, 5))


if __name__ == "__main__":
    unittest.main()
