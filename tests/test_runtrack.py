"""R-1213: live item state from agent launches and hand-backs, and the timeline."""
import json
import os
import unittest

from helpers import StudioTestCase
import studiolib as sl
import runtrack

T0 = "2026-11-18T10:00:00.000Z"
T1 = "2026-11-18T10:12:30.000Z"
T2 = "2026-11-18T10:20:00.000Z"
BUILDER = "studio:builder"


class RunTrackCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s-pm")
        data = sl.load_ledger(self.project)
        self.a = sl.add_item(data, "uno", ["legacy"])["id"]
        self.b = sl.add_item(data, "dos", ["legacy"])["id"]
        sl.save_ledger(self.project, data)
        self.brief_dir = os.path.join(self.tmp, "scratchpad", "briefs")
        os.makedirs(self.brief_dir)

    def tr(self, *ids, name="t"):
        """An agent transcript whose first user message is the launch prompt naming these items."""
        path = os.path.join(self.tmp, "%s-%s.jsonl" % (name, "-".join(ids)))
        with open(path, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "# %s\nbrief" % " ".join(ids)}}) + "\n")
        return path

    def run_cli(self, *args):
        import subprocess
        import sys
        from helpers import ROOT
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG="en", HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, os.path.join(ROOT, "plugin", "bin", "budgit")] + list(args),
                              cwd=self.project, env=env, capture_output=True, text=True)

    def test_two_builders_the_first_handback_leaves_the_other_item_whole(self):
        self.launch("# %s\n" % self.a, desc="one")
        self.launch("# %s\n" % self.b, desc="two")
        self.assertEqual(runtrack.on_handback(self.project, BUILDER, "done", at=T1, names=[], transcript=self.tr(self.a)),
                         [self.a])
        self.assertNotIn("doing_by", self.item(self.a))
        self.assertEqual(self.item(self.b)["doing_by"], "studio:builder (two)")
        self.assertEqual(len(self.item(self.b)["runs"]), 1)

    def test_no_transcript_clears_nothing(self):
        self.launch("# %s\n" % self.a)
        self.launch("# %s\n" % self.b)
        for tr in (None, os.path.join(self.tmp, "missing.jsonl")):
            self.assertEqual(runtrack.on_handback(self.project, BUILDER, "done", at=T1, names=[], transcript=tr), [])
        self.assertTrue(self.item(self.a)["doing_by"] and self.item(self.b)["doing_by"])

    def test_timeline_cli_text_and_json(self):
        self.launch("# %s\n" % self.a)
        r = self.run_cli("ledger", "timeline", self.a)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("launch", r.stdout)
        r = self.run_cli("ledger", "timeline", self.a, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("launch", [e["kind"] for e in json.loads(r.stdout)])

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def launch(self, brief, desc="build it", at=T0, kind=BUILDER):
        return runtrack.on_launch(self.project, {"prompt": brief, "subagent_type": kind, "description": desc}, at=at, names=[])

    def kinds(self, item_id):
        return [e["kind"] for e in self.item(item_id).get("events", [])]

    def integrity_ok(self):
        self.assertFalse(sl.ledger_tampered(self.project))

    def test_launch_heading_sets_doing_and_one_launch_event(self):
        brief = "# %s part 1: live state\ngates: verify\n" % self.a + "filler\n" * 40 + "also mentions %s here\n" % self.b
        self.assertEqual(self.launch(brief), [self.a])
        it = self.item(self.a)
        self.assertEqual((it["status"], it["doing_since"]), ("doing", T0))
        self.assertEqual(it["doing_by"], "studio:builder (build it)")
        self.assertEqual(self.kinds(self.a).count("launch"), 1)
        late = self.item(self.b)                       # named only on line 44: not an item of the run
        self.assertEqual(late["status"], "open")
        self.assertNotIn("doing_by", late)
        self.integrity_ok()

    def test_launch_reads_the_brief_file_the_prompt_names(self):
        path = os.path.join(self.brief_dir, "b.md")
        with open(path, "w") as f:
            f.write("# %s the work\nbody\n" % self.b)
        self.assertEqual(self.launch("Read and follow %s now" % path), [self.b])
        self.assertEqual(self.item(self.b)["status"], "doing")

    def test_launch_never_reopens_a_verified_item(self):
        data = sl.load_ledger(self.project)
        data["items"][0]["status"] = "verified"
        sl.save_ledger(self.project, data)
        self.launch("# %s again\n" % self.a)
        it = self.item(self.a)
        self.assertEqual(it["status"], "verified")
        self.assertNotIn("doing_by", it)
        self.assertNotIn("doing_since", it)

    def test_handback_clears_doing_by_and_state_stays_doing(self):
        self.launch("# %s\n" % self.a)
        self.assertEqual(runtrack.on_handback(self.project, BUILDER, "Built it, sha abc\nmore", at=T1, names=[], transcript=self.tr(self.a)), [self.a])
        it = self.item(self.a)
        self.assertEqual(it["status"], "doing")
        self.assertNotIn("doing_by", it)
        self.assertEqual(it["events"][-1]["kind"], "handback")
        self.assertEqual(it["events"][-1]["text"], "Built it, sha abc")
        self.integrity_ok()

    def test_two_runs_first_handback_keeps_the_second_run(self):
        self.launch("# %s\n" % self.a, desc="first", at=T0)
        self.launch("# %s\n" % self.a, desc="second", at=T1)
        runtrack.on_handback(self.project, BUILDER, "done one", at=T2, names=[], transcript=self.tr(self.a))
        it = self.item(self.a)
        self.assertEqual(it["doing_by"], "studio:builder (second)")
        runtrack.on_handback(self.project, BUILDER, "done two", at=T2, names=[], transcript=self.tr(self.a))
        self.assertNotIn("doing_by", self.item(self.a))

    def test_done_sets_closed_at_and_event_add_sets_created_at(self):
        it = self.item(self.a)
        self.assertEqual(it["created_at"], it["created"])
        self.assertEqual(self.kinds(self.a), ["note"])
        data = sl.load_ledger(self.project)
        sl.set_status(self.project, data, self.a, "done", evidence="abc1234 test_x", outcome="works")
        done = sl.find_item(data, self.a)
        self.assertTrue(done["closed_at"])
        self.assertEqual(done["events"][-1]["kind"], "done")
        sl.set_status(self.project, data, self.b, "doing")
        self.assertEqual(sl.find_item(data, self.b)["events"][-1]["kind"], "state")

    def test_timeline_text_json_and_elapsed_in_both_languages(self):
        self.launch("# %s\n" % self.a, at=T0)
        runtrack.on_handback(self.project, BUILDER, "report line", at=T1, names=[], transcript=self.tr(self.a))
        it = self.item(self.a)
        en = "\n".join(runtrack.timeline_lines(it, "en"))
        es = "\n".join(runtrack.timeline_lines(it, "es"))
        self.assertIn("launch", en)
        self.assertIn("handback", en)
        self.assertIn("12m 30s from launch to hand-back", en)
        self.assertIn("12m 30s desde el lanzamiento hasta la entrega", es)
        rows = runtrack.ordered(it)
        self.assertEqual([r["kind"] for r in rows if r["kind"] != "note"], ["state", "launch", "handback"])
        json.dumps(rows)
        self.launch("# %s\n" % self.a, at=T2)
        self.assertIn("no hand-back yet", "\n".join(runtrack.timeline_lines(self.item(self.a), "en")))

    def test_private_name_in_the_report_line_is_shielded(self):
        self.launch("# %s\n" % self.a)
        runtrack.on_handback(self.project, BUILDER, "Fixed the Secretproj import", at=T1, names=["secretproj"],
                            transcript=self.tr(self.a))
        text = self.item(self.a)["events"][-1]["text"]
        self.assertEqual(text, "private")
        self.assertNotIn("Secretproj", json.dumps(self.item(self.a)))

    def test_malformed_brief_or_missing_ledger_never_blocks_the_launch(self):
        import hook
        for bad in (None, {"prompt": None}, {"prompt": 7}, "text", {"prompt": "no ids at all"}):
            self.assertEqual(runtrack.launch_ids(self.project, bad), [])      # no id, no error, nothing to track
        os.remove(os.path.join(sl.studio_dir(self.project), "ledger.json"))
        with self.assertRaises(sl.StudioError):
            runtrack.on_launch(self.project, {"prompt": "# %s\n" % self.a}, names=[])
        todo = []
        hook.on_pre_tool_use({"tool_name": "Agent", "tool_input": {"prompt": "# %s\n" % self.a, "subagent_type": BUILDER}},
                             self.project, todo)
        for book in todo:
            book()                      # the booked tracking logs its error and returns; the launch decision is already out

    def test_launch_event_text_is_shielded_and_redacted(self):
        secret = "sk-" + "a1B2c3D4" * 5
        runtrack.on_launch(self.project, {"prompt": "# %s\n" % self.a, "subagent_type": BUILDER,
                                          "description": "port Secretproj with key %s" % secret},
                           at=T0, names=["secretproj"])
        raw = json.dumps(self.item(self.a))
        self.assertNotIn("Secretproj", raw)
        self.assertNotIn(secret, raw)

    def test_tampered_ledger_is_never_written_by_launch_or_handback(self):
        self.launch("# %s\n" % self.a)
        hash_path = os.path.join(sl.studio_dir(self.project), "ledger.sha256")
        with open(hash_path, "w") as f:
            f.write("0" * 64 + "\n")
        before = sl.read_text(os.path.join(sl.studio_dir(self.project), "ledger.json"))
        self.assertEqual(self.launch("# %s\n" % self.b), [])
        self.assertEqual(runtrack.on_handback(self.project, BUILDER, "done", at=T1, names=[], transcript=self.tr(self.a)), [])
        self.assertEqual(sl.read_text(os.path.join(sl.studio_dir(self.project), "ledger.json")), before)
        self.assertTrue(sl.ledger_tampered(self.project))

    def test_a_long_run_of_slashes_is_scanned_fast(self):
        import time
        start = time.process_time()                                      # CPU time, not wall (R-1282)
        self.assertEqual(runtrack.launch_ids(self.project, {"prompt": "/" * 200000}), [])
        self.assertLess(time.process_time() - start, 1.0)

    def test_timeline_prints_one_clean_line_per_field(self):
        item = {"id": self.a, "events": [
            {"at": T0, "kind": "launch", "by": "bu\nilder\x1b[2J (x)", "text": "t"},
            {"at": T1, "kind": "note\nFORGED\x07", "by": "x", "text": "t"},
            {"at": T2, "kind": "handback", "by": "bu\nilder", "text": "t"}]}
        lines = runtrack.timeline_lines(item, "en")
        for line in lines:
            self.assertNotIn("\n", line)
            self.assertFalse([c for c in line if ord(c) < 32], line)
        self.assertEqual(len(lines), 4)

    def test_colon_run_is_scanned_fast_and_separators_match(self):
        import time
        start = time.process_time()                                      # CPU time, not wall (R-1282)
        runtrack.launch_ids(self.project, {"prompt": "/:" * 10000})
        self.assertLess(time.process_time() - start, 0.2)
        for n, pre in enumerate(("x=", "x:", '"', "(")):
            path = os.path.join(self.brief_dir, "p%d.md" % n)
            with open(path, "w") as f:
                f.write("# %s\n" % self.b)
            self.assertEqual(runtrack.launch_ids(self.project, {"prompt": "zz\n" * 6 + "see " + pre + path}), [self.b], pre)
        glued = os.path.join(self.brief_dir, "g.md")
        with open(glued, "w") as f:
            f.write("# %s\n" % self.b)
        self.assertEqual(runtrack.launch_ids(self.project, {"prompt": "zz\n" * 6 + "see x" + glued}), [])

    def test_at_most_ten_ids_change_per_launch(self):
        data = sl.load_ledger(self.project)
        ids = [sl.add_item(data, "x%d" % n, ["legacy"])["id"] for n in range(30)]
        sl.save_ledger(self.project, data)
        brief = "".join("# %s\n" % i for i in ids)
        self.assertEqual(len(self.launch(brief)), 10)
        doing = [i for i in ids if self.item(i)["status"] == "doing"]
        self.assertEqual(len(doing), 10)

    def test_events_are_capped_and_keep_the_first(self):
        data = sl.load_ledger(self.project)
        it = data["items"][0]
        for n in range(300):
            sl.add_event(it, "note", "t", "n%d" % n)
        self.assertEqual(len(it["events"]), sl.EVENTS_KEEP)
        self.assertEqual(it["events"][0]["text"], "added")
        self.assertEqual(it["events"][-1]["text"], "n299")


if __name__ == "__main__":
    unittest.main()
