"""[perf] R-91: `studio retro-mine --days 30` over ~5k prompts / ~300 MB of synthetic transcripts must finish < 60 s.

Not part of `unittest discover` (no test_ prefix): run it by hand, `python3 tests/bench_retromine.py`.
RETROMINE_BENCH_MB shrinks the corpus for a quick check (default 300). Synthetic text only.
"""
import json, os, random, resource, shutil, subprocess, sys, tempfile, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
LIMIT_S = 60
PROMPTS, SESSIONS = 5000, 50


def generate(root, megabytes, seed=7):
    rng = random.Random(seed)
    vocab = ["w%d" % i for i in range(3000)]
    weights = [1.0 / (i + 1) for i in range(len(vocab))]   # Zipf-ish: a few very common tokens, a long rare tail
    per_prompt = max(megabytes * 1024 * 1024 // PROMPTS, 200)
    pad = "x" * per_prompt
    for s in range(SESSIONS):
        d = os.path.join(root, "-bench-proj%d" % (s % 5))
        os.makedirs(d, exist_ok=True)
        texts = []
        with open(os.path.join(d, "bench%03d.jsonl" % s), "w") as f:
            for i in range(PROMPTS // SESSIONS):
                ts = "2026-10-%02dT%02d:%02d:00Z" % (10 + s % 10, i % 24, (i * 7) % 60)
                text = " ".join(rng.choices(vocab, weights, k=rng.randint(10, 25)))
                if i % 20 == 19:
                    text = texts[i - 15]   # said twice, hours later: exercises the pair search
                texts.append(text)
                if i % 17 == 0:
                    text += " ese gris no me gusta, hazlo de nuevo"
                f.write(json.dumps({"type": "user", "timestamp": ts, "origin": {"kind": "human"},
                                    "message": {"role": "user", "content": text}}) + "\n")
                f.write(json.dumps({"type": "assistant", "timestamp": ts, "message": {
                    "id": "m%d" % i, "model": "claude-opus-5-5",
                    "content": [{"type": "tool_use", "id": "t%d" % i, "name": "Read", "input": {"file_path": "x"}}]}}) + "\n")
                f.write(json.dumps({"type": "user", "timestamp": ts, "toolUseResult": {"ok": True},
                                    "message": {"content": [{"type": "tool_result", "tool_use_id": "t%d" % i,
                                                             "content": pad}]}}) + "\n")


class RetroMineBench(unittest.TestCase):
    def test_days_30_under_a_minute(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "projects")
            generate(root, int(os.environ.get("RETROMINE_BENCH_MB", "300")))
            out = os.path.join(tmp, "report.md")
            # bound on our own computation: CPU time of the child (RUSAGE_CHILDREN delta), not wall seconds (R-1282)
            def child_cpu():
                u = resource.getrusage(resource.RUSAGE_CHILDREN)
                return u.ru_utime + u.ru_stime
            c0 = child_cpu()
            r = subprocess.run([sys.executable, CLI, "retro-mine", "--root", root, "--days", "30", "--out", out],
                               capture_output=True, text=True, env=dict(os.environ, STUDIO_HOME=os.path.join(tmp, "h")))
            took = child_cpu() - c0
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("your prompts %d" % PROMPTS, r.stdout)
            print("retro-mine bench: %.1f s for %d prompts" % (took, PROMPTS))
            self.assertLess(took, LIMIT_S)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
