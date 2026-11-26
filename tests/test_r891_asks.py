"""R-891 slice 1 / R-910: `budgit asks`, the read-only list of every ask the user made. Fixture projects only."""
import datetime
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import asks
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class AsksTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.n = 0

    def git(self, *args):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                            "-C", self.project, *args], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def say(self, source="prompt"):
        self.n += 1
        return sl.append_inbox(self.project, "ask number %d please" % self.n, "s1", source, ref="ref%d" % self.n)

    def build(self, items):
        """items: (id, title, source-or-None, fields)."""
        data = sl.load_ledger(self.project)
        for iid, title, source, fields in items:
            src = [self.say(source)] if source else ["legacy"]
            sl.add_item(data, title + " text", src, item_id=iid, title=title)
        sl.save_ledger(self.project, data)
        data = sl.load_ledger(self.project)
        for iid, _t, _s, fields in items:
            sl.find_item(data, iid).update(fields)
        sl.save_ledger(self.project, data)

    def rows(self, now=None, public=(0, 1, 0)):
        return {r["id"]: r for r in asks.compute(self.project, now, public)["rows"]}

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, "asks", *args, "--project", self.project], env=env, capture_output=True, text=True)

    def fixture(self):
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "start")
        self.git("tag", "v0.1.0")
        with open(os.path.join(self.project, "x.py"), "w", encoding="utf-8") as f:
            f.write("a = 1\n")
        self.git("add", "x.py")
        self.git("commit", "-q", "-m", "R-3: built thing")                 # starts with the id, changes a code file
        os.makedirs(os.path.join(self.project, "docs"), exist_ok=True)
        with open(os.path.join(self.project, "docs", "notes.md"), "w", encoding="utf-8") as f:
            f.write("park\n")
        self.git("add", "docs/notes.md")
        self.git("commit", "-q", "-m", "R-27 park stale doing items")      # starts with the id, but only a doc changes
        with open(os.path.join(self.project, "x.py"), "w", encoding="utf-8") as f:
            f.write("a = 2\n")
        self.git("add", "x.py")
        self.git("commit", "-q", "-m", "drop export --source (moved to R-30)")   # the R-861 shape: names the id, does not start with it
        self.git("branch", "claude/r13-thing")
        self.git("branch", "claude/r41-lane")
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        os.makedirs(vdir, exist_ok=True)
        with open(os.path.join(vdir, "R-4.md"), "w", encoding="utf-8") as f:
            f.write("PASS at abc\nbody\n")
        self.build([
            ("R-1", "open thing", "prompt", {}),
            ("R-2", "doing thing", "answer", {"status": "doing"}),
            ("R-3", "built thing", "prompt", {}),
            ("R-4", "verified thing", "prompt", {}),
            ("R-5", "done thing", "prompt", {"status": "done", "evidence": "t"}),
            ("R-99", "dropped target", "prompt", {"status": "dropped", "dropped_by": "I-1"}),
            ("R-27", "ledger only commit", "prompt", {}),
            ("R-30", "mentioned only", "prompt", {}),
            ("R-40", "bug no lane", "prompt", {"flags": ["bug"], "created": "2020-01-01T00:00:00.000Z"}),
            ("R-41", "bug with a lane", "prompt", {"flags": ["bug"], "created": "2020-01-01T00:00:00.000Z"}),
            ("R-42", "bug just made", "prompt", {"flags": ["bug"], "created": sl.now()}),
            ("R-43", "plain old", "prompt", {"created": "2020-01-01T00:00:00.000Z"}),
            ("R-6", "folded into dropped", "prompt", {"status": "folded", "folded": {"into": "R-99", "reason": "x"}}),
            ("R-12", "folded into lost", "prompt", {"status": "folded", "folded": {"into": "R-6", "reason": "x"}}),
            ("R-8", "target done", "prompt", {"status": "done", "evidence": "t"}),
            ("R-7", "empty action field", "prompt", {"status": "folded", "folded": {"into": "R-8", "reason": "x"}}),
            ("R-11", "agent note only", "manual", {}),
            ("R-13", "branch thing", "prompt", {}),
            ("R-14", "old phase thing", "prompt", {"phase": "0.1.0"}),
            ("R-15", "done in a cut phase", "prompt", {"phase": "0.1.0", "status": "done", "evidence": "t"}),
            ("R-16", "done for a later release", "prompt", {"phase": "9.9.9", "status": "done", "evidence": "t"}),
            ("R-17", "doing in a cut phase", "prompt", {"phase": "0.0.9", "status": "doing"}),
        ])

    def test_one_item_per_state(self):
        self.fixture()
        rows = self.rows()
        self.assertEqual(rows["R-1"]["state"], "not started")
        self.assertEqual(rows["R-2"]["state"], "in progress")
        self.assertEqual(rows["R-13"]["state"], "in progress")
        self.assertEqual(rows["R-3"]["state"], "built")
        self.assertEqual(rows["R-4"]["state"], "verified")
        self.assertEqual(rows["R-5"]["state"], "shipped")
        self.assertEqual(rows["R-6"]["state"], "lost")

    def test_built_needs_a_build_commit_not_a_mention(self):
        self.fixture()
        rows = self.rows()
        self.assertEqual(rows["R-3"]["state"], "built")                    # "R-3: ..." with x.py
        self.assertEqual(rows["R-27"]["state"], "not started")            # starts with the id, but only a doc changed
        self.assertEqual(rows["R-30"]["state"], "not started")            # "(moved to R-30)": a mention is no build

    def raw_inbox(self, entries):
        """Append inbox lines directly (append_inbox itself skips harness text)."""
        with open(sl._inbox_path(self.project), "a", encoding="utf-8") as f:
            for i, (source, text) in enumerate(entries):
                f.write(json.dumps({"id": "I-%d" % (9001 + self._raw + i), "source": source, "text": text, "session": "s"}) + "\n")
        self._raw += len(entries)

    _raw = 0

    def test_harness_noise_never_goes_into_lost(self):
        self.fixture()
        tags = ["<bash-input>", "<bash-stdout>", "<command-name>", "<command-message>", "<local-command-stdout>",
                "<system-reminder>", "<task-notification>", "<scheduled-task", "  \n<bash-input>"]   # literal; last has whitespace
        self.raw_inbox([("prompt", tag + " something the harness printed here") for tag in tags])
        self.raw_inbox([("prompt", "This session is being continued from a previous conversation: the summary follows with many words")])
        self.raw_inbox([("prompt", "arregla de verdad el campo de acción vacío"), ("prompt", "arregla de verdad el campo de acción vacío")])
        self.raw_inbox([("prompt", "<esto no es una etiqueta> sino una petición real mía")])
        result = asks.compute(self.project, None, (0, 1, 0))
        lost = {r["id"] for r in result["rows"] if r["status"] == "inbox"}
        n_tags = len(tags)
        self.assertEqual(len(result["harness_noise"]), n_tags + 1 + 1)             # tags, opener, the later duplicate
        self.assertIn("I-%d" % (9001 + n_tags + 1), lost)                           # the oldest copy stays a lost ask
        self.assertIn("I-%d" % (9001 + n_tags + 3), lost)                           # "<..." that is not a tag is her ask
        self.assertEqual(len(lost), 2)
        self.assertEqual(lost & set(result["harness_noise"]), set())

    def test_plain_word_openers_are_noise_and_a_mention_stays_lost(self):
        self.fixture()
        openers = ["Your questions have been answered", "The user answered", "Tool permission request",
                   "[Cross-session idle notice]", "Background (from the session", "The user doesn't want to proceed",
                   "[SYSTEM NOTIFICATION", "[Subagent hand-back]", "Another Claude session sent a message"]
        self.raw_inbox([("prompt", o + " and then many more words follow here") for o in openers])
        self.raw_inbox([("prompt", "  \n" + openers[1] + " with leading whitespace and many words")])
        self.raw_inbox([("answer", "the user answered nothing useful here, please fix the empty field"),
                        ("prompt", "the user answered? no, yo pregunté otra cosa distinta"),
                        ("prompt", "Tool Permission Request en mayúsculas no es el arnés, es mía")])
        result = asks.compute(self.project, None, (0, 1, 0))
        lost = {r["id"] for r in result["rows"] if r["status"] == "inbox"}
        n = len(openers) + 1
        self.assertEqual(len(result["harness_noise"]), n)
        self.assertEqual(lost & set(result["harness_noise"]), set())
        for k in range(3):
            self.assertIn("I-%d" % (9001 + n + k), lost)
        self.assertEqual(len(lost), 3)

    def test_inbox_counts_add_up(self):
        self.fixture()
        self.raw_inbox([("prompt", "<bash-input>ls</bash-input>"), ("prompt", "ok"), ("prompt", "una petición real que nadie recogió"),
                        ("prompt", "una petición real que nadie recogió")])
        result = asks.compute(self.project, None, (0, 1, 0))
        her = [e for e in sl.load_inbox(self.project) if e.get("source") in asks.HER_SOURCES]
        held = {s for it in sl.load_ledger(self.project)["items"] for s in it.get("src") or ()}
        lost = [r for r in result["rows"] if r["status"] == "inbox"]
        carried = [e for e in her if e["id"] in held]
        self.assertEqual(len(lost) + len(result["harness_noise"]) + len(result["short_replies"]) + len(carried), len(her))

    def test_lost_comes_from_the_inbox(self):
        self.fixture()
        orphan = sl.append_inbox(self.project, "please never forget the empty action field", "s1", "prompt", ref="orph")
        sl.append_inbox(self.project, "vale", "s1", "prompt", ref="short1")
        sl.append_inbox(self.project, "AskUserQuestion answer: {\"questions\": [\"pick\"]}", "s1", "answer", ref="pick1")
        sl.append_inbox(self.project, "agent note that nobody tracks at all", "s1", "manual", ref="man1")
        result = asks.compute(self.project, None, (0, 1, 0))
        rows = {r["id"]: r for r in result["rows"]}
        self.assertEqual(rows[orphan]["state"], "lost")
        self.assertEqual(rows[orphan]["status"], "inbox")
        self.assertEqual({r["text"][:2] for r in result["short_replies"]}, {"va", "As"})     # counted, never hidden
        self.assertEqual(len([r for r in rows.values() if r["status"] == "inbox"]), 1)      # the manual note is not her word
        self.assertEqual(result["summary"]["lost"], 3)                                       # R-6, R-12 and the orphan

    def test_an_id_removed_from_every_item_is_lost(self):
        self.fixture()
        gone = self.rows()["R-1"]["words"][0]
        data = sl.load_ledger(self.project)
        sl.find_item(data, "R-1")["src"] = ["legacy"]
        sl.save_ledger(self.project, data)
        self.assertEqual(self.rows()[gone]["state"], "lost")

    def test_dropped_items_with_her_words_are_listed_with_the_reason(self):
        self.fixture()
        row = self.rows()["R-99"]
        self.assertEqual(row["state"], "dropped")
        self.assertIn("I-1", row["reason"])

    def test_class_a_item_with_no_lane_for_24h(self):
        self.fixture()
        rows = self.rows()
        self.assertTrue(rows["R-40"]["no_lane_24h"])
        self.assertFalse(rows["R-41"]["no_lane_24h"])       # a branch names it
        self.assertFalse(rows["R-42"]["no_lane_24h"])       # younger than 24 h
        self.assertFalse(rows["R-43"]["no_lane_24h"])       # not a bug
        self.assertEqual(self.cli("--gate").returncode, 1)
        self.assertEqual(self.cli().returncode, 0)

    def test_open_or_doing_in_a_cut_phase_is_overdue(self):
        self.fixture()
        rows = self.rows()
        self.assertEqual(rows["R-14"]["state"], "overdue")
        self.assertEqual(rows["R-17"]["state"], "overdue")
        self.assertEqual(rows["R-15"]["state"], "shipped")
        self.assertEqual(rows["R-16"]["state"], "built")
        self.assertEqual(self.rows(public=None)["R-14"]["state"], "not started")     # public version unknown: nothing is cut
        self.assertEqual(asks.STATES[1], "overdue")

    def test_cut_version_comes_from_the_release_reports(self):
        self.fixture()
        self.assertIsNone(asks.cut_version(self.project))
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        for n in ("RC-0.13.9.9.md", "RELEASE-0.13.9.6.md", "RC-0.13.10.md", "R-5.md"):
            with open(os.path.join(vdir, n), "w", encoding="utf-8") as f:
                f.write("PASS\n")
        self.assertEqual(asks.cut_version(self.project), (0, 13, 10))

    def test_a_worktree_reads_the_main_checkouts_verify_reports(self):
        self.fixture()
        wt = os.path.join(self.home, "wt-copy")
        self.git("worktree", "add", "-q", "-b", "claude/r4-wt", wt)
        self.assertEqual(os.path.realpath(asks.main_root(wt)), os.path.realpath(self.project))
        self.assertFalse(os.path.exists(os.path.join(wt, ".budgit", "verify", "R-4.md")))
        self.assertTrue(asks._passes(wt, "R-4"))
        self.assertFalse(asks._passes(wt, "R-3"))

    def test_fold_into_dropped_or_lost_reads_as_lost(self):
        self.fixture()
        rows = self.rows()
        self.assertEqual(rows["R-6"]["state"], "lost")
        self.assertEqual(rows["R-6"]["into"], "R-99")
        self.assertEqual(rows["R-12"]["state"], "lost")

    def test_words_not_carried(self):
        self.fixture()
        rows = self.rows()
        self.assertTrue(rows["R-7"]["words_not_carried"])
        self.assertEqual(rows["R-7"]["state"], "shipped")
        data = sl.load_ledger(self.project)
        sl.find_item(data, "R-8")["evidence"] = "carries " + rows["R-7"]["words"][0]
        sl.save_ledger(self.project, data)
        self.assertFalse(self.rows()["R-7"]["words_not_carried"])

    def test_words_carried_when_the_target_src_holds_them(self):
        self.fixture()
        word = self.rows()["R-7"]["words"][0]
        self.assertTrue(self.rows()["R-7"]["words_not_carried"])
        data = sl.load_ledger(self.project)
        sl.find_item(data, "R-8")["src"].append(word)                      # what `ledger link R-8 --src I-n` writes
        sl.save_ledger(self.project, data)
        self.assertFalse(self.rows()["R-7"]["words_not_carried"])

    def test_manual_only_src_is_not_listed(self):
        self.fixture()
        self.assertNotIn("R-11", self.rows())

    def test_planned_with_a_time_nothing_behind_it(self):
        self.fixture()
        self.build([("R-20", "slice two", "prompt", {}), ("R-21", "slice three", "prompt", {})])
        sdir = sl.studio_dir(self.project)
        today = datetime.datetime(2030, 3, 4, 10, 0)
        with open(os.path.join(sdir, "progress-pm.md"), "w", encoding="utf-8") as f:
            f.write("R-1 at 07:00\n## 2030-03-03\n- R-21 at 08:00\n## 2030-03-04 plan\nR-20 slice 2 at 09:00\n"
                    "2030-03-04T09:00:00Z R-1 logged\nR-3 at 09:00\n## 2030-03-02\nR-1 at 08:00\n")
        flagged = [p["id"] for p in asks.compute(self.project, today, None)["planned"]]
        self.assertEqual(flagged, ["R-20"])  # undated, old-dated, a log stamp and a committed item are no plan for today
        before = datetime.datetime(2030, 3, 4, 8, 59)
        self.assertEqual(asks.compute(self.project, before, None)["planned"], [])
        self.assertEqual([p["time"] for p in asks.compute(self.project, today, None)["planned"]], ["09:00"])
        with open(os.path.join(sdir, "progress-pm.md"), "w", encoding="utf-8") as f:
            f.write("2030-03-04 R-20 at 09:00\n")                    # a line carrying today's date is a plan too
        self.assertEqual([p["id"] for p in asks.compute(self.project, today, None)["planned"]], ["R-20"])

    def test_planned_follows_the_clock_not_a_fixed_date(self):
        self.fixture()
        self.build([("R-20", "slice two", "prompt", {})])
        for day in (datetime.datetime(2031, 1, 2, 12, 0), datetime.datetime.now().replace(hour=23, minute=59)):
            with open(os.path.join(sl.studio_dir(self.project), "progress-pm.md"), "w", encoding="utf-8") as f:
                f.write("## %s\nR-20 at 09:00\n" % day.date().isoformat())
            self.assertEqual([p["id"] for p in asks.compute(self.project, day, None)["planned"]], ["R-20"])

    def test_json_shape_and_text_summary(self):
        self.fixture()
        r = self.cli("--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(set(out), {"rows", "summary", "short_replies", "harness_noise", "planned"})
        self.assertEqual(set(out["rows"][0]), {"id", "title", "state", "status", "phase", "words", "into", "words_not_carried",
                                               "reason", "no_lane_24h"})
        self.assertEqual(set(out["summary"]), set(asks.STATES))
        self.assertEqual(sum(out["summary"].values()), len(out["rows"]))
        text = self.cli()
        self.assertIn("Asks:", text.stdout.replace("Peticiones", "Asks"))
        only = json.loads(self.cli("--json", "--state", "lost").stdout)
        self.assertEqual({r["id"] for r in only["rows"]}, {"R-6", "R-12"})

    def test_read_only(self):
        self.fixture()
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        with open(path, "rb") as f:
            before = f.read()
        self.cli()
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
