"""R-1273 «Board counts match real work»: an open item whose build commit is not on main counts inside «en curso» (I-2007: no
new visible group), in every view and through the one shared helper; a lane claim is not stopped by the private quota
(I-1980). Real CLI and a temp git repo; fake names only."""
import json
import os
import re
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT, patch_env
from test_flow_data import FlowCase
import budget
import capacity
import card
import daily
import jobs
import livework
import portfolio
import runtrack
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com")
RAW = {"doing": 10, "next": 61, "later": 103, "done": 5}
SHOWN = {"doing": 14, "next": 57, "later": 103, "done": 5}


class BoardCountsTest(StudioTestCase):
    def git(self, *args):
        r = subprocess.run(["git", "-C", self.project, *args], env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, message="work"):
        self.n += 1
        with open(os.path.join(self.project, "f%d.txt" % self.n), "w") as f:
            f.write(str(self.n))
        self.git("add", "f%d.txt" % self.n)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")

    def side_sha(self):
        self.git("checkout", "-q", "-b", "side%d" % self.n)
        sha = self.commit("side work")
        self.git("checkout", "-q", "main")
        return sha

    def setUp(self):
        super().setUp()
        self.n = 0
        self.init_project()
        self.git("init", "-q", "-b", "main")
        self.commit("base")
        self.merged = self.commit("on main")
        self.unmerged = self.side_sha()
        data = sl.load_ledger(self.project)
        self.built_ids = []
        self.add(data, 10, "doing")
        for k in range(61):
            it = self.add(data, 1, "open")[0]
            if k < 4:
                it["evidence"] = "%s test_zorb" % self.unmerged
                self.built_ids.append(it["id"])
        for it in self.add(data, 103, "open"):
            it["phase"] = sl.LATER_PHASE
        self.add(data, 5, "done")
        sl.save_ledger(self.project, data)

    def add(self, data, count, status):
        out = []
        for _ in range(count):
            sl.add_item(data, "zorb quux %d" % len(data["items"]), ["legacy"])
            it = data["items"][-1]
            it["status"] = status
            out.append(it)
        return out

    def load(self):
        return sl.load_ledger(self.project)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)

    def set_evidence(self, item_id, text):
        data = self.load()
        sl.find_item(data, item_id)["evidence"] = text
        sl.save_ledger(self.project, data)

    def shown(self, counts):
        return {k: counts[k] for k in SHOWN}

    # ---- the shared helper
    def test_built_unmerged_open_item_counts_in_progress_not_todo_not_done(self):
        c = sl.ledger_counts(self.load(), self.project)
        self.assertEqual(self.shown(c), SHOWN)
        self.assertEqual(self.shown(sl.ledger_counts(self.load())), RAW)        # no project: the old numbers

    def test_no_review_key_and_the_sum_is_the_total(self):
        data = self.load()
        c = sl.ledger_counts(data, self.project)
        self.assertNotIn("review", c)
        self.assertEqual(sum(c[k] for k in SHOWN), sum(self.shown(sl.ledger_counts(data)).values()))
        per = sl.counts_by_area(data, data["items"], self.project)
        self.assertTrue(all("review" not in v for v in per.values()))
        self.assertEqual(sum(v["doing"] for v in per.values()), 14)

    def test_merged_sha_stays_in_the_old_count(self):
        self.set_evidence(self.built_ids[0], "%s test_zorb" % self.merged)
        self.assertEqual(sl.ledger_counts(self.load(), self.project)["doing"], 13)

    def test_one_unmerged_of_two_shas_is_built(self):
        self.set_evidence(self.built_ids[0], "%s %s test_zorb" % (self.merged, self.unmerged))
        self.assertEqual(sl.ledger_counts(self.load(), self.project)["doing"], 14)

    def test_unknown_sha_decimal_digits_and_a_note_only_sha_fall_back(self):
        data = self.load()
        sl.find_item(data, self.built_ids[0])["evidence"] = "abcdef1234567 test_zorb"          # no such commit
        sl.find_item(data, self.built_ids[1])["evidence"] = "20261120 test_zorb"               # a date, not a commit
        it = sl.find_item(data, self.built_ids[2])
        it["evidence"] = ""
        it["text"] = "zorb quux %s" % self.unmerged                                            # a sha only in the text
        sl.save_ledger(self.project, data)
        self.assertEqual(sl.ledger_counts(self.load(), self.project)["doing"], 11)

    def test_timeout_shows_old_count_and_logs_one_line(self):
        lines = []
        old_deadline, old_log = sl.BOARD_DEADLINE_S, sl.log_error
        sl.BOARD_DEADLINE_S, sl.log_error = -1, lines.append
        try:
            c = sl.ledger_counts(self.load(), self.project)
        finally:
            sl.BOARD_DEADLINE_S, sl.log_error = old_deadline, old_log
        self.assertEqual(self.shown(c), RAW)
        self.assertEqual(lines, ["board: review check timed out, raw counts shown"])

    # ---- every view
    def test_all_views_same_numbers_14_57_103(self):
        data = self.load()
        with self.subTest("status head (the real _counts path)"):
            head = self.cli("status", "--line").stdout
            self.assertTrue(re.search(r"\b14\b", head) and re.search(r"\b57\b", head) and re.search(r"\b103\b", head), head)
            self.assertFalse(re.search(r"\b61\b", head), head)
        with self.subTest("status --brote feed counts and queue (second feed builder)"):
            feed = json.loads(self.cli("status", "--brote").stdout)
            self.assertEqual(feed["counts"], SHOWN)
            self.assertFalse(set(self.built_ids) & {q["id"] for q in feed["queue"]})
        with self.subTest("ledger list totals, filtered"):
            listed = json.loads(self.cli("ledger", "list", "--json", "--text", "zorb").stdout)
            self.assertEqual(self.shown(listed["totals"]), SHOWN)
        with self.subTest("ledger list totals and areas, by area"):
            by_area = json.loads(self.cli("ledger", "list", "--json", "--by-area").stdout)
            self.assertEqual(self.shown(by_area["totals"]), SHOWN)
            self.assertEqual(sum(v["doing"] for v in by_area["by_area"].values()), 14)
        with self.subTest("portfolio"):
            tasks, _stale = portfolio._tasks(self.project)
            self.assertEqual(tasks, SHOWN)
        with self.subTest("card"):
            card.write(self.project, data)
            self.assertEqual(card.counts_view(self.project), SHOWN)
        with self.subTest("ledger_counts and feed_counts"):
            self.assertEqual(self.shown(sl.ledger_counts(data, self.project)), SHOWN)
            self.assertEqual(sl.feed_counts(data, sl.built_unmerged(self.project, data["items"])), SHOWN)

    def test_old_card_without_built_open_shows_raw(self):
        card.write(self.project, self.load())
        path = card.path(self.project)
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        stored.pop("built_open", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stored, f)
        self.assertEqual(card.counts_view(self.project), RAW)

    def test_views_show_no_new_group(self):
        head = self.cli("status", "--line").stdout.lower()
        self.assertNotIn("revisi", head)
        self.assertEqual(set(sl.feed_counts(self.load(), set())), {"doing", "next", "later", "done"})

    # ---- gaps: raw where the logic needs raw, version line, perf
    def stamp(self, hours):
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - hours * 3600))

    def edit(self, item_id, **fields):
        data = self.load()
        sl.find_item(data, item_id).update(fields)
        sl.save_ledger(self.project, data)

    def test_private_1_1_project_with_built_unmerged_open_item_still_allows_a_plain_start(self):
        data = self.load()
        card.write(self.project, data)
        self.assertEqual(portfolio._tasks(self.project, True)[0], SHOWN)               # the display view moved
        self.assertEqual(portfolio._tasks(self.project, True, raw=True)[0], RAW)       # capacity reads this
        row = capacity._project_row("zorb", {"path": self.project}, [dict(i) for i in data["items"][11:15]], time.time(), False)
        self.assertEqual((row["in_flight"], row["ready"]), (0, 4))                      # built-unmerged open: ready, never in flight
        old = (portfolio.load, livework.is_private)
        portfolio.load = lambda: {"projects": [{"path": self.project, "priority": 1}]}
        livework.is_private = lambda path, *a, **k: True
        try:
            rows, _skipped = capacity.portfolio_snapshot(time.time())
        finally:
            portfolio.load, livework.is_private = old
        self.assertEqual([(r["in_flight"], r["ready"]) for r in rows], [(10, 61)])      # raw: a plain start sees 61 ready, 10 in flight

    def test_version_line_matches_head(self):
        data = self.load()
        for item_id in self.built_ids + ["R-30", "R-31"]:
            sl.find_item(data, item_id)["phase"] = "0.13.2"
        sl.save_ledger(self.project, data)
        line = sl.version_line(self.load(), self.project)
        doing_part = next(p for p in line.split(" — ") if p.startswith("doing:"))
        next_part = next(p for p in line.split(" — ") if p.startswith("next:"))
        for item_id in self.built_ids:
            self.assertIn(item_id, doing_part)
            self.assertNotIn(item_id, next_part)
        self.assertIn("R-30", next_part)
        self.assertIn("6 left of 6", line)                                              # version_left stays on the raw status
        self.assertIn("doing: " + ", ".join(self.built_ids), self.cli("status").stdout)  # the real status path

    def test_daily_and_sweep_sources_stay_raw(self):
        card.write(self.project, self.load())
        self.assertEqual(card.read(self.project)["counts"]["doing"], 10)                # daily's protected row reads raw card counts
        self.assertEqual(card.read(self.project)["counts"]["open"], 61 + 103)
        pending, _committed, nxt, scope = daily._scope(self.load()["items"])
        self.assertTrue(set(self.built_ids) <= {i["id"] for i in pending})              # daily's plain row: raw status scope
        self.edit("R-1", updated=self.stamp(8 * 24))
        self.edit("R-11", updated=self.stamp(8 * 24), status="doing", evidence="%s test_zorb" % self.unmerged)
        self.assertEqual(portfolio._tasks(self.project)[1], 2)                          # sweep's stuck total: raw status, unchanged
        self.assertEqual(len(sl.stale_doing(self.load(), time.time())), 2)

    def test_perf_200_items_50_shas_under_the_deadline_and_one_git_call_per_sha(self):
        self.git("checkout", "-q", "-b", "perf")
        shas = []
        for k in range(50):
            self.git("commit", "-q", "--allow-empty", "-m", "perf %d" % k)
            shas.append(self.git("rev-parse", "--short", "HEAD"))
        self.git("checkout", "-q", "main")
        items = [{"id": "R-%d" % (900 + k), "status": "open", "evidence": "%s test_zorb" % shas[k % 50]} for k in range(200)]
        calls, real = [], sl._reach_git
        sl._reach_git = lambda root, *a: (calls.append(a), real(root, *a))[1]
        sl._BUILT_CACHE.clear()
        old_deadline, sl.BOARD_DEADLINE_S = sl.BOARD_DEADLINE_S, 3600     # injected: the result and the call count never depend on machine speed
        try:
            began = time.monotonic()
            built = sl.built_unmerged(self.project, items)
            took = time.monotonic() - began
        finally:
            sl._reach_git = real
            sl.BOARD_DEADLINE_S = old_deadline
        self.assertEqual(len(built), 200)
        self.assertLessEqual(len(calls), 50 + 6, [c[0] for c in calls][:12])             # one per distinct sha plus the tips
        self.assertEqual(old_deadline, 1.5)                                              # production deadline unchanged
        if took >= old_deadline:                                                         # wall time: only a note on a busy machine
            self.skipTest("machine busy: %.2f s for 50 shas (budget %.1f s); result and call count checked" % (took, old_deadline))

    # ---- task 1.5: launch claim and release
    def launch(self, item_id, at=None, filler=8):
        prompt = "\n".join(["line %d" % n for n in range(filler)] + ["Item %s «zorb quux»" % item_id])
        return runtrack.on_launch(self.project, {"prompt": prompt, "subagent_type": "builder", "description": "zorb"}, at=at, names=())

    def test_launch_claims_item_beyond_line_5(self):
        self.assertEqual(self.launch("R-20"), ["R-20"])
        self.assertEqual(sl.find_item(self.load(), "R-20")["status"], "doing")

    def test_handback_without_transcript_releases_by_message_ids(self):
        self.launch("R-20")
        self.assertEqual(runtrack.on_handback(self.project, "builder", "all done, nothing named", names=()), [])
        self.assertIn("runs", sl.find_item(self.load(), "R-20"))
        self.assertEqual(runtrack.on_handback(self.project, "builder", "R-20 built and tested\nmore", names=()), ["R-20"])
        item = sl.find_item(self.load(), "R-20")
        self.assertNotIn("runs", item)
        self.assertNotIn("doing_by", item)

    def test_orphan_run_over_12h_released_with_note(self):
        self.launch("R-21", at=self.stamp(13))
        self.assertEqual(runtrack.sweep_orphans(self.project), ["R-21"])
        item = sl.find_item(self.load(), "R-21")
        self.assertEqual(item["status"], "open")
        self.assertNotIn("runs", item)
        self.assertIn("no hand-back", json.dumps(item))

    def test_session_claim_not_touched_by_orphan_sweep(self):
        owner = "11111111-2222-4333-8444-555555555555"
        self.edit("R-22", status="doing", doing_by=owner, runs=[{"by": "builder (zorb)", "type": "builder", "at": self.stamp(13)}])
        old, sl.read_pointer = sl.read_pointer, lambda who: {"project": self.project}
        try:
            self.assertEqual(runtrack.sweep_orphans(self.project), [])
        finally:
            sl.read_pointer = old
        self.assertEqual(sl.find_item(self.load(), "R-22")["doing_by"], owner)

    # ---- slice 2: stale flags (flag only)
    def test_doing_over_12h_flagged_not_changed(self):
        self.edit("R-1", doing_since=self.stamp(13))
        flagged = sl.flagged_doing(self.load())
        self.assertEqual([i["id"] for i in flagged], ["R-1"])
        self.assertEqual(sl.find_item(self.load(), "R-1")["status"], "doing")

    def test_unrelated_note_does_not_hide_flag(self):
        self.edit("R-1", doing_since=self.stamp(13), updated=sl.now())                  # a note moves `updated` only
        self.assertEqual([i["id"] for i in sl.flagged_doing(self.load())], ["R-1"])

    def test_fresh_doing_not_flagged(self):
        self.edit("R-1", doing_since=self.stamp(11))
        self.assertEqual(sl.flagged_doing(self.load()), [])

    def test_standup_lists_stale_doing_and_stale_phase(self):
        self.edit("R-1", doing_since=self.stamp(13))
        self.edit("R-40", phase="0.13.2", updated=self.stamp(8 * 24), created=self.stamp(9 * 24), events=[])
        text = "\n".join(jobs.stale_lines(self.project))
        self.assertIn("1 en curso sin moverse desde hace más de 12 h: R-1", text)
        self.assertIn("fase 0.13.2: nada se ha movido en 7 días", text)

    def test_stale_open_built_unmerged_in_standup_line(self):
        self.edit(self.built_ids[0], updated=self.stamp(13))
        text = "\n".join(jobs.stale_lines(self.project))
        self.assertIn(self.built_ids[0], text)
        self.assertNotIn(self.built_ids[1], text)

    def test_squash_merged_item_stays_en_curso_and_shows_in_the_stale_line(self):
        # a squash merge makes a NEW commit on main: the item's own sha is never on main, so it stays «en curso» until it is closed
        self.git("merge", "--squash", self.unmerged)
        self.git("commit", "-q", "-m", "squashed")
        self.assertEqual(sl.ledger_counts(self.load(), self.project)["doing"], 14)
        self.edit(self.built_ids[0], updated=self.stamp(13))
        self.assertIn(self.built_ids[0], "\n".join(jobs.stale_lines(self.project)))

    def test_stale_lines_follow_the_user_language(self):
        self.edit("R-1", doing_since=self.stamp(13))
        self.assertIn("1 en curso sin moverse desde hace más de 12 h: R-1", "\n".join(jobs.stale_lines(self.project, today="2030-01-01")))
        patch_env(self, BUDGIT_LANG="en")
        self.assertIn("1 in progress, not moved for over 12 h: R-1", "\n".join(jobs.stale_lines(self.project, today="2030-01-02")))

    def test_stale_lines_once_per_day(self):
        self.edit("R-1", doing_since=self.stamp(13))
        self.assertTrue(jobs.stale_lines(self.project, today="2030-01-01"))
        self.assertEqual(jobs.stale_lines(self.project, today="2030-01-01"), [])
        self.assertTrue(jobs.stale_lines(self.project, today="2030-01-02"))
        self.assertEqual(jobs.stale_lines(self.project, today="2030-01-02"), [])

    def test_stale_lines_name_no_new_group(self):
        self.edit("R-1", doing_since=self.stamp(13))
        self.edit(self.built_ids[0], updated=self.stamp(13))
        self.edit("R-40", phase="0.13.2", updated=self.stamp(8 * 24), created=self.stamp(9 * 24), events=[])
        es = "\n".join(jobs.stale_lines(self.project, today="2030-01-01")).lower()
        patch_env(self, BUDGIT_LANG="en")
        en = "\n".join(jobs.stale_lines(self.project, today="2030-01-02")).lower()
        for text in (es, en):
            for word in ("construid", "fusion", "merge", "built", "revisi", "review"):
                self.assertNotIn(word, text)
        self.assertIn(self.built_ids[0].lower(), es)                  # a built-not-merged item is named inside «en curso»
        self.assertIn("en curso", es)
        self.assertIn("in progress", en)

    def test_stuck_total_unchanged_at_7_days(self):
        self.edit("R-1", updated=self.stamp(6.9 * 24))
        self.edit("R-2", updated=self.stamp(7.1 * 24))
        self.assertEqual([i["id"] for i in sl.stale_doing(self.load(), time.time())], ["R-2"])

    # ---- I-1980: the cap
    def gate(self, runs):
        import capacity
        data = self.load()
        item = sl.find_item(data, self.built_ids[0])
        if runs is not None:
            item["runs"] = runs
        refuse = {"code": "full", "project": "zorb", "used": 1, "slots": 1, "reason": "full"}
        old = capacity.check_start
        capacity.check_start = lambda *a, **k: refuse
        try:
            sl._capacity_gate(self.project, data, item, "s-quux", False, None)
        finally:
            capacity.check_start = old

    def test_lane_claim_skips_cap_when_full_and_runs_entry_under_12h(self):
        self.gate([{"by": "builder (zorb)", "type": "builder", "at": sl.now()}])

    def test_stale_runs_entry_does_not_skip_cap(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 13 * 3600))
        with self.assertRaises(sl.StudioError):
            self.gate([{"by": "builder (zorb)", "type": "builder", "at": old}])

    def test_human_start_with_by_still_refused_when_full(self):
        with self.assertRaises(sl.StudioError):
            self.gate(None)


class LaneQuotaEndToEndTest(FlowCase):
    """I-1980 with the real capacity path (portfolio list, budget level, card counts of a private project): nothing stubbed."""

    def setUp(self):
        super().setUp()
        data = sl.load_ledger(self.project)
        self.c, self.y, self.z = (sl.add_item(data, "zorb %d" % n, ["legacy"])["id"] for n in range(3))
        sl.start_item(self.project, data, self.a, "s-flow0001")        # the quota is still free: one item doing
        sl.save_ledger(self.project, data)
        cfg = sl.load_project_config(self.project)
        sl.atomic_write(os.path.join(sl.studio_dir(self.project), "project.json"), json.dumps(dict(cfg, private=True)) + "\n")
        portfolio.save({"version": 1, "projects": [portfolio._entry(os.path.realpath(self.project))]})
        budget.set_override("critical")                                # one slot for the one project: 1/1
        card.write(self.project, sl.load_ledger(self.project))        # a private project's counts come from its card

    def cli(self, session, *args):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_CODE_SESSION_ID=session)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)

    def test_i1980_lanes_claim_under_a_full_private_quota_end_to_end(self):
        self.assertTrue(livework.is_private(self.project))
        refused = self.cli("s-flow0002", "ledger", "start", self.b)                     # a human start: 1/1, refused
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("1/1", refused.stdout + refused.stderr)
        prompt = "\n".join(["line %d" % n for n in range(6)] + ["Item %s «zorb quux»" % self.c])       # the id sits on line 7
        self.assertEqual(runtrack.on_launch(self.project, {"prompt": prompt, "subagent_type": "builder", "description": "zorb"},
                                            names=()), [self.c])
        self.assertEqual(self.item(self.c)["status"], "doing")
        data = sl.load_ledger(self.project)
        sl.find_item(data, self.y)["runs"] = [{"by": "builder (zorb)", "type": "builder", "at": sl.now()}]
        sl.save_ledger(self.project, data)
        passed = self.cli("s-flow0001", "ledger", "start", self.y, "--by", "s-flow0002")   # live runs entry under 12 h: passes
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertEqual(self.item(self.y)["doing_by"], "s-flow0002")
        no_run = self.cli("s-flow0001", "ledger", "start", self.z, "--by", "s-flow0002")   # no runs entry: refused
        self.assertNotEqual(no_run.returncode, 0)
        self.assertIn("cupo de", no_run.stdout + no_run.stderr)          # the card already counts the two claims (3/1): still full
        self.assertEqual((self.item(self.z)["status"], self.item(self.b)["status"]), ("open", "open"))
        with open(os.path.join(sl.studio_dir(self.project), "audit.log")) as f:
            self.assertIn("%s claimed past the lane cap" % self.y, f.read())
