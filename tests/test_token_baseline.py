"""R-849 item A: tools/token_baseline.py measures from transcripts (fixture JSONL)."""
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("token_baseline", os.path.join(ROOT, "tools", "token_baseline.py"))
tb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tb)


def turn(mid, inp, cc, cr, out, tool_id=None):
    content = [{"type": "tool_use", "id": tool_id}] if tool_id else [{"type": "text", "text": "x"}]
    return {"type": "assistant", "uuid": "u" + mid, "message": {
        "id": mid, "model": "m1", "content": content,
        "usage": {"input_tokens": inp, "cache_creation_input_tokens": cc,
                  "cache_read_input_tokens": cr, "output_tokens": out}}}


class TokenBaseline(unittest.TestCase):
    def test_first_turn_context_total_and_per_call(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "agent-a1.jsonl")
            recs = [
                {"type": "user", "message": {"role": "user", "content": "brief"}},
                turn("m1", 10, 1000, 4000, 20, "t1"),
                turn("m1", 10, 1000, 4000, 20, "t1"),  # streaming repeat, counted once
                turn("m2", 5, 0, 5000, 30, "t2"),
                turn("m3", 5, 0, 5100, 40),
            ]
            with open(p, "w") as fh:
                for r in recs:
                    fh.write(json.dumps(r) + "\n")
            with open(os.path.join(d, "agent-a1.meta.json"), "w") as fh:
                json.dump({"agentType": "studio:builder"}, fh)
            r = tb.read_transcript(p)
            self.assertEqual(r["first_context"], 5010)
            self.assertEqual(r["calls"], 2)
            self.assertEqual(r["total"], 5030 + 5035 + 5145)
            rows = tb.summarize(tb.find_transcripts(d))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["agent"], "studio:builder")
            self.assertEqual(rows[0]["first_context"], 5010)
            self.assertEqual(rows[0]["per_call"], (5030 + 5035 + 5145) // 2)
            self.assertIn("studio:builder", tb.render(rows))
            self.assertNotIn("brief", tb.render(rows))


if __name__ == "__main__":
    unittest.main()
