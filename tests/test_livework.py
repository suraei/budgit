"""R-199 step 1: the live feed (`status --brote --live`), the project registry, live sessions, `done_at`.
Only fake names, paths and titles. The user home is a temp folder (HOME is patched for the test and its child runs)."""
import calendar
import json
import os
import re
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import StudioTestCase, ROOT
import i18n
import livework
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
V2_KEYS = ['counts', 'doing', 'done_recent', 'done_total', 'fresh', 'h5', 'idle', 'lang', 'later', 'projection',
           'queue', 'queue_recent', 'resets_at', 'runs_out', 'stale', 'state', 't', 'total', 'used', 'version']
V3_KEYS = ['paused', 'projects', 'quiet_sessions', 'recent_done', 'sources', 'totals', 'triage', 'watch', 'week', 'working']
SECRETS = ("messagingSocketPath", "bridgeSessionId", "hostSessionId", "peerFeatures", "peerProtocol",
           "SECRET-SOCKET-PATH", "SECRET-BRIDGE", "SECRET-HOST", "SECRET-FEATURES", "SECRET-PROTOCOL")


def ps_start(pid):
    """The process start time as Claude Code writes procStart (UTC, ctime layout), measured independently with ps."""
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                         env=dict(os.environ, TZ="UTC", LC_ALL="C")).stdout.strip()
    return " ".join(out.split())


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


class LiveCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        base = os.path.realpath(self.tmp)
        self.uhome = os.path.join(base, "uhome")
        os.makedirs(os.path.join(self.uhome, ".claude", "sessions"))
        self.sessions_dir = os.path.join(self.uhome, ".claude", "sessions")
        self.bhome = os.path.join(self.uhome, ".budgit-home")
        self._saved = {k: os.environ.get(k) for k in ("HOME", "STUDIO_HOME", "BUDGIT_HOME", "CLAUDE_CONFIG_DIR")}
        os.environ["HOME"] = self.uhome
        os.environ["STUDIO_HOME"] = self.bhome
        os.environ.pop("BUDGIT_HOME", None)
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.uhome, ".claude")
        self.sleeper = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (self.sleeper.kill(), self.sleeper.wait()))
        self.pid = self.sleeper.pid
        self.pstart = ps_start(self.pid)
        self.n = 0

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    # ---- fixtures
    def config(self, hub=None, root=None):
        os.makedirs(self.bhome, exist_ok=True)
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"hub": hub, "projects_root": root}, f)

    def mk(self, path, items=(), phase="build"):
        os.makedirs(os.path.join(path, ".studio"), exist_ok=True)
        data = sl.empty_ledger()
        for spec in items:
            id_, st = spec[0], spec[1]
            it = {"id": id_, "src": [], "text": "texto de %s" % id_, "title": "Tarea %s" % id_, "status": st, "outcome": "You see it work",
                  "evidence": "e" if st in ("done", "verified") else "", "phase": phase,
                  "created": "2026-10-01T00:00:00.000Z", "updated": "2026-10-01T00:00:00.000Z"}
            it.update(spec[2] if len(spec) > 2 else {})
            data["items"].append(it)
        sl.save_ledger(path, data)
        return path

    def pointer(self, sid, project):
        os.makedirs(os.path.join(self.bhome, "sessions"), exist_ok=True)
        stamp = sl.now()
        with open(os.path.join(self.bhome, "sessions", sid + ".json"), "w") as f:
            json.dump({"project": project, "ts": stamp, "last_seen": stamp}, f)

    def session(self, sid, cwd, status="busy", name=None, pid=None, **extra):
        self.n += 1
        rec = {"pid": pid or self.pid, "sessionId": sid, "cwd": cwd, "name": name or ("Sesion " + sid),
               "status": status, "statusUpdatedAt": 1_792_000_000_000 + self.n * 1000, "procStart": self.pstart,
               "kind": "interactive", "pidDomain": "darwin",
               "messagingSocketPath": "SECRET-SOCKET-PATH", "bridgeSessionId": "SECRET-BRIDGE",
               "hostSessionId": "SECRET-HOST", "peerFeatures": ["SECRET-FEATURES"], "peerProtocol": "SECRET-PROTOCOL"}
        rec.update(extra)
        rec = {k: v for k, v in rec.items() if v is not None}
        with open(os.path.join(self.sessions_dir, "%s-%d.json" % (pid or self.pid, self.n)), "w") as f:
            json.dump(rec, f)
        return rec

    def cli(self, *args, project=None):
        cmd = [sys.executable, CLI, "status", "--brote", *args, "--project", project]
        return subprocess.run(cmd, env=dict(os.environ), capture_output=True, text=True)

    def feed(self, project, live=True):
        r = self.cli(*(["--live"] if live else []), project=project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip(), "empty feed: " + r.stderr)
        return json.loads(r.stdout)

    def proj(self, *parts):
        return os.path.join(self.uhome, "Documents", *parts)

    def by_id(self, out):
        return {p["id"]: p for p in out["projects"]}


class FeedContractTest(LiveCase):
    def test_plain_brote_keeps_exactly_the_v2_keys(self):
        hub = self.mk(self.proj("hubby"))
        self.config(hub=hub)
        self.session("s-aaaa1111", hub)
        out = self.feed(hub, live=False)
        self.assertEqual(sorted(set(out) - {"update"}), V2_KEYS)
        self.assertEqual(out["version"], 2)
        for k in ("SECRET-SOCKET-PATH", "Sesion s-aaaa1111"):
            self.assertNotIn(k, json.dumps(out))

    def test_live_adds_the_v3_keys_and_keeps_every_v2_key(self):
        hub = self.mk(self.proj("hubby"))
        self.config(hub=hub)
        out = self.feed(hub)
        self.assertEqual(sorted(set(out) - {"update"} - set(V2_KEYS)), V3_KEYS)
        self.assertTrue(set(V2_KEYS) <= set(out))
        self.assertEqual(out["version"], 3)
        self.assertEqual(len(out["week"]), 7)
        self.assertTrue(all(isinstance(n, int) for n in out["week"]))

    def test_the_new_strings_exist_in_both_languages(self):
        for k in ("book.now_session", "book.waiting_permission", "book.waiting_question", "book.waiting_other",
                  "book.quiet_sessions", "book.project_other", "book.no_live_data"):
            for lang in ("en", "es"):
                self.assertTrue(i18n.CATALOG[lang].get(k), (lang, k))
        self.assertEqual(sorted(i18n.CATALOG["en"]), sorted(i18n.CATALOG["es"]))


class SessionsTest(LiveCase):
    def setUp(self):
        super().setUp()
        self.hub = self.mk(self.proj("hubby"))
        self.config(hub=self.hub)

    def test_only_the_allow_listed_fields_leave_the_module_and_the_feed(self):
        self.session("s-aaaa1111", self.hub, status="waiting", waitingFor="permission prompt", extra_unknown="SECRET-X")
        found = livework.live_sessions()["sessions"]
        self.assertEqual(len(found), 1)
        text = json.dumps(found)
        for bad in SECRETS + ("SECRET-X",):
            self.assertNotIn(bad, text)
        r = self.cli("--live", project=self.hub)
        for bad in SECRETS + ("SECRET-X", "hostSessionId", "pidDomain"):
            self.assertNotIn(bad, r.stdout)

    def test_only_json_files_are_read(self):
        with open(os.path.join(self.sessions_dir, "123.abcdef.key"), "w") as f:
            f.write(json.dumps({"pid": self.pid, "sessionId": "s-key", "cwd": self.hub, "status": "busy",
                                "pidDomain": "darwin", "name": "Clave"}))
        self.assertEqual(livework.live_sessions()["sessions"], [])

    def test_waiting_for_is_mapped_to_a_fixed_set(self):
        for sid, raw in (("s-perm0001", "permission prompt"), ("s-ques0001", "input needed"),
                         ("s-othr0001", "something new <SECRET-RAW-TEXT>")):
            self.session(sid, self.hub, status="waiting", waitingFor=raw)
        self.session("s-busy0001", self.hub, status="busy")
        out = self.feed(self.hub)
        got = {r["session"]: r["waiting_for"] for r in out["working"]}
        self.assertEqual(got, {"s-perm00": "permission", "s-ques00": "question", "s-othr00": "other",
                               "s-busy00": None})
        self.assertNotIn("SECRET-RAW-TEXT", json.dumps(out))

    def test_status_since_is_in_seconds_and_working_order_is_waiting_claims_busy(self):
        self.pointer("s-claim001", self.hub)
        self.mk(self.hub, [("R-1", "doing", {"doing_by": "s-claim001", "doing_since": "2026-10-20T10:00:00.000Z"})])
        self.session("s-busy0001", self.hub, status="busy")
        self.session("s-claim001", self.hub, status="busy")
        self.session("s-wait0001", self.hub, status="waiting", waitingFor="input needed")
        out = self.feed(self.hub)
        self.assertEqual([(r["kind"], r["session_status"]) for r in out["working"]],
                         [("session", "waiting"), ("claim", "busy"), ("session", "busy")])
        wait = out["working"][0]
        self.assertEqual(wait["since"], 1_792_000_000 + 3)          # statusUpdatedAt of the third session, ms -> s
        self.assertEqual(wait["project"], "hubby")
        claim = out["working"][1]
        self.assertEqual((claim["item"], claim["title"], claim["session"]), ("R-1", "Tarea R-1", "s-claim0"))
        self.assertEqual(claim["since"], livework.epoch("2026-10-20T10:00:00.000Z"))

    def test_idle_sessions_are_a_count_not_rows(self):
        self.session("s-idle0001", self.hub, status="idle")
        self.session("s-idle0002", self.hub, status="idle")
        out = self.feed(self.hub)
        self.assertEqual((out["working"], out["quiet_sessions"]), ([], 2))
        self.assertEqual(out["projects"][0]["live"], 2)
        self.assertEqual(out["sources"]["sessions"], "ok")

    def test_a_dead_pid_is_not_a_session(self):
        done = subprocess.Popen(["true"])
        done.wait()
        self.session("s-dead0001", self.hub, pid=done.pid)
        out = self.feed(self.hub)
        self.assertEqual((out["working"], out["quiet_sessions"]), ([], 0))

    def test_a_pid_domain_other_than_darwin_is_unverified_never_a_now_row(self):
        self.session("s-lnx00001", self.hub, status="busy", pidDomain="linux")
        self.session("s-nodom001", self.hub, status="busy", pidDomain=None)
        out = self.feed(self.hub)
        self.assertEqual(out["working"], [])
        self.assertEqual(out["quiet_sessions"], 2)
        found = livework.live_sessions()["sessions"]
        self.assertTrue(all(s["verified"] is False for s in found))

    @unittest.skipUnless(sys.platform == "darwin", "the kernel start time check is macOS only")
    def test_proc_start_must_match_the_kernel_start_time(self):
        self.session("s-good0001", self.hub, status="busy")
        self.session("s-reuse001", self.hub, status="busy", procStart="Mon Jan  1 00:00:00 2024")
        got = {s["session_id"]: s["verified"] for s in livework.live_sessions()["sessions"]}
        self.assertEqual(got, {"s-good0001": True, "s-reuse001": False})
        out = self.feed(self.hub)
        self.assertEqual([r["session"] for r in out["working"]], ["s-good00"])

    def test_without_a_kernel_answer_a_busy_older_than_12_hours_is_unverified(self):
        self.session("s-old00001", self.hub, status="busy", statusUpdatedAt=int((time.time() - 13 * 3600) * 1000))
        self.session("s-new00001", self.hub, status="busy", statusUpdatedAt=int((time.time() - 3600) * 1000))
        self.session("s-idleold1", self.hub, status="idle", statusUpdatedAt=int((time.time() - 30 * 3600) * 1000))
        with mock.patch.object(livework, "kernel_start", return_value=None):
            got = {s["session_id"]: s["verified"] for s in livework.live_sessions()["sessions"]}
        self.assertEqual(got, {"s-old00001": False, "s-new00001": True, "s-idleold1": True})

    def test_a_missing_sessions_folder_is_absent_and_the_feed_still_answers(self):
        os.rmdir(self.sessions_dir)
        out = self.feed(self.hub)
        self.assertEqual(out["sources"]["sessions"], "absent")
        self.assertEqual(out["working"], [])


class AttributionTest(LiveCase):
    def test_pointer_project_first_then_the_cwd_walk_up_then_other(self):
        a = self.mk(self.proj("alpha"))
        b = self.mk(self.proj("beta"))
        self.config(hub=a)
        self.pointer("s-ptr00001", b)
        self.session("s-ptr00001", os.path.join(a, "sub"))          # the pointer wins over the cwd
        os.makedirs(os.path.join(a, "deep", "er"))
        self.session("s-cwd00001", os.path.join(a, "deep", "er"))   # no pointer: walk up to alpha
        self.session("s-none0001", self.uhome)                      # nothing: other
        out = self.feed(a)
        by = {r["session"]: r["project"] for r in out["working"]}
        self.assertEqual(by, {"s-ptr000": "beta", "s-cwd000": "alpha", "s-none00": None})
        live = {p["id"]: p["live"] for p in out["projects"]}
        self.assertEqual((live["alpha"], live["beta"]), (1, 1))


class RegistryTest(LiveCase):
    def test_merges_pointers_hub_projects_root_and_session_cwds_and_needs_a_ledger(self):
        hub = self.mk(self.proj("hubby"))
        root = os.path.join(self.uhome, "work")
        child = self.mk(os.path.join(root, "kid"))
        os.makedirs(os.path.join(root, "no-ledger"))
        viaptr = self.mk(os.path.join(self.uhome, "ptrproj"))
        viacwd = self.mk(os.path.join(self.uhome, "cwdproj"))
        os.makedirs(os.path.join(self.uhome, "plain"))
        self.config(hub=hub, root=root)
        self.pointer("s-ptr00001", viaptr)
        self.pointer("s-ptr00002", os.path.join(self.uhome, "plain"))       # no ledger: not a project
        self.session("s-cwd00001", os.path.join(viacwd, "src"))
        reg = livework.all_projects()
        self.assertEqual(sorted(p["id"] for p in reg["projects"]), ["cwdproj", "hubby", "kid", "ptrproj"])
        self.assertTrue([p for p in reg["projects"] if p["id"] == "hubby"][0]["hub"])
        self.assertEqual((reg["skipped"], reg["capped"]), (0, False))

    def test_protected_folders_are_skipped_and_counted(self):
        hub = self.mk(self.proj("hubby"))
        self.config(hub=hub)
        for i, parts in enumerate((("Desktop", "pa"), ("Downloads", "pb"), ("Library", "Mobile Documents", "pc"))):
            self.pointer("s-prot000%d" % i, self.mk(os.path.join(self.uhome, *parts)))
        out = self.feed(hub)
        self.assertEqual([p["id"] for p in out["projects"]], ["hubby"])
        self.assertEqual(out["sources"]["skipped"], 3)
        self.assertEqual(out["sources"]["projects"], 1)
        for path in out["watch"]["files"]:
            self.assertNotIn("Desktop", path)

    def test_is_protected_covers_volumes_and_the_three_home_places(self):
        self.assertTrue(livework.is_protected("/Volumes/Disk/x", self.uhome))
        for sub in ("Desktop", "Downloads", os.path.join("Library", "Mobile Documents")):
            self.assertTrue(livework.is_protected(os.path.join(self.uhome, sub, "p"), self.uhome))
        self.assertFalse(livework.is_protected(os.path.join(self.uhome, "Documents", "p"), self.uhome))
        self.assertFalse(livework.is_protected(os.path.join(self.uhome, "Desktops", "p"), self.uhome))

    def test_a_git_worktree_folds_into_its_main_project(self):
        main = self.mk(self.proj("alpha"))
        os.makedirs(os.path.join(main, ".git", "worktrees", "wt1"))
        wt = self.mk(self.proj("alpha-r4"))
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % os.path.join(main, ".git", "worktrees", "wt1"))
        os.makedirs(os.path.join(wt, "sub"))
        self.config(hub=main)
        self.pointer("s-wt000001", wt)                                # even a pointer bound to the worktree folds
        self.session("s-wt000001", os.path.join(wt, "sub"))
        self.session("s-wt000002", os.path.join(wt, "sub"), status="idle")
        reg = livework.all_projects()
        self.assertEqual([p["id"] for p in reg["projects"]], ["alpha"])
        out = self.feed(main)
        self.assertEqual([p["id"] for p in out["projects"]], ["alpha"])
        self.assertEqual(out["projects"][0]["live"], 2)
        self.assertEqual([r["project"] for r in out["working"]], ["alpha"])
        self.assertTrue(all(wt not in p for p in out["watch"]["files"]))

    def test_duplicate_basenames_get_a_stable_hash_suffix(self):
        hub = self.mk(self.proj("one", "app"))
        other = self.mk(self.proj("two", "app"))
        self.config(hub=hub)
        self.pointer("s-dup00001", other)
        ids = [p["id"] for p in livework.all_projects()["projects"]]
        self.assertEqual(ids[0], "app")
        self.assertRegex(ids[1], r"^app~[0-9a-f]{4}$")
        self.assertEqual(ids, [p["id"] for p in livework.all_projects()["projects"]])
        self.assertEqual({p["name"] for p in livework.all_projects()["projects"]}, {"app"})

    def test_at_most_12_projects_live_ones_first(self):
        hub = self.mk(self.proj("hubby"))
        self.config(hub=hub, root=self.proj("many"))
        for i in range(14):
            self.mk(self.proj("many", "p%02d" % i))
        quiet_old = self.proj("many", "p03")
        os.utime(os.path.join(quiet_old, ".studio", "ledger.json"), (1, 1))
        self.session("s-live0001", quiet_old, status="idle")
        out = self.feed(hub)
        self.assertEqual(len(out["projects"]), 12)
        self.assertTrue(out["sources"]["capped"])
        self.assertEqual(out["sources"]["projects"], 12)
        self.assertIn("hubby", self.by_id(out))
        self.assertIn("p03", self.by_id(out))                       # live beats newer ledgers


class ClaimsTest(LiveCase):
    def setUp(self):
        super().setUp()
        self.hub = self.mk(self.proj("hubby"))
        self.config(hub=self.hub)

    def claim(self, sid="s-gone0001", item="R-1"):
        self.mk(self.hub, [(item, "doing", {"doing_by": sid, "doing_since": "2026-10-20T10:00:00.000Z"})])

    def test_a_claim_whose_claimant_is_gone_goes_to_paused_not_now(self):
        self.pointer("s-gone0001", self.hub)
        self.claim()
        self.session("s-other001", self.hub, status="idle")
        out = self.feed(self.hub)
        self.assertEqual(out["working"], [])
        self.assertEqual(out["paused"], [{"project": "hubby", "item": "R-1", "title": "Tarea R-1", "session": "s-gone00",
                                          "reason": "claimant_gone", "since": livework.epoch("2026-10-20T10:00:00.000Z")}])
        self.assertEqual(out["doing"][0]["id"], "R-1")               # the v2 hub list keeps its meaning

    def test_an_unverified_claimant_pauses_the_claim_as_idle(self):
        self.claim("s-lnx00001")
        self.session("s-lnx00001", self.hub, status="busy", pidDomain="linux")
        out = self.feed(self.hub)
        self.assertEqual((out["working"], [p["reason"] for p in out["paused"]]), ([], ["idle"]))

    def test_a_claim_held_by_an_idle_session_is_paused_not_now(self):
        self.claim("s-idle0001")
        self.session("s-idle0001", self.hub, status="idle")
        out = self.feed(self.hub)
        self.assertEqual(out["working"], [])
        self.assertEqual([(p["item"], p["reason"], p["session"]) for p in out["paused"]], [("R-1", "idle", "s-idle00")])
        self.assertEqual(out["quiet_sessions"], 1)

    def test_a_claim_held_by_a_busy_or_waiting_session_stays_in_now(self):
        self.mk(self.hub, [("R-1", "doing", {"doing_by": "s-busy0001", "doing_since": "2026-10-20T10:00:00.000Z"}),
                           ("R-2", "doing", {"doing_by": "s-wait0001", "doing_since": "2026-10-20T11:00:00.000Z"})])
        self.session("s-busy0001", self.hub, status="busy")
        self.session("s-wait0001", self.hub, status="waiting", waitingFor="approve tool use")
        out = self.feed(self.hub)
        self.assertEqual(sorted(r["item"] for r in out["working"]), ["R-1", "R-2"])
        self.assertEqual(out["paused"], [])

    def test_without_session_data_the_twelve_hour_rule_decides(self):
        os.rmdir(self.sessions_dir)
        self.pointer("s-live0001", self.hub)
        self.claim("s-live0001")
        out = self.feed(self.hub)
        self.assertEqual(out["sources"]["sessions"], "absent")
        self.assertEqual([(r["kind"], r["item"]) for r in out["working"]], [("claim", "R-1")])
        self.assertEqual(out["working"][0]["session_status"], None)
        self.assertEqual(out["paused"], [])

    def test_plain_brote_does_not_change_the_claim_state(self):
        self.pointer("s-gone0001", self.hub)
        self.claim()
        out = self.feed(self.hub, live=False)
        self.assertEqual(out["doing"][0]["state"], "live")           # v2 meaning untouched

    def test_caps_working_20_paused_10(self):
        for i in range(25):
            self.session("s-busy%04d" % i, self.hub, status="busy", name="N" * 200)
        items = [("R-%d" % n, "doing", {"doing_by": "s-gone%04d" % n, "doing_since": "2026-10-20T10:00:00.000Z"})
                 for n in range(1, 13)]
        self.mk(self.hub, items)
        out = self.feed(self.hub)
        self.assertEqual(len(out["working"]), 20)
        self.assertEqual(len(out["paused"]), 10)
        self.assertTrue(all(len(r["title"]) <= 90 for r in out["working"]))


class ProjectsRowsTest(LiveCase):
    def test_queue_recent_3_per_project_8_for_the_hub_and_counts(self):
        hub = self.mk(self.proj("hubby"), [("R-%d" % n, "open") for n in range(1, 21)] + [("R-30", "doing"), ("R-31", "done")])
        other = self.mk(self.proj("other"), [("R-%d" % n, "open") for n in range(1, 11)] + [("R-12", "open", {"phase": "later"})])
        self.config(hub=hub)
        self.pointer("s-oth00001", other)
        by = self.by_id(self.feed(hub))
        self.assertEqual([r["id"] for r in by["hubby"]["queue_recent"]], ["R-%d" % n for n in range(20, 12, -1)])
        self.assertEqual([r["id"] for r in by["other"]["queue_recent"]], ["R-10", "R-9", "R-8"])
        self.assertEqual(by["hubby"]["counts"], {"doing": 1, "next": 20, "later": 0, "done": 1})
        self.assertEqual(by["other"]["counts"], {"doing": 0, "next": 10, "later": 1, "done": 0})
        self.assertEqual(set(by["hubby"]), {"id", "name", "hub", "live", "error", "counts", "queue_recent", "last_change"})
        self.assertTrue(by["hubby"]["hub"] and not by["other"]["hub"])
        self.assertIsInstance(by["other"]["last_change"], int)

    def test_a_broken_ledger_is_an_error_on_that_project_and_not_the_feed(self):
        hub = self.mk(self.proj("hubby"), [("R-1", "open")])
        bad = self.mk(self.proj("badone"))
        with open(os.path.join(bad, ".studio", "ledger.json"), "w") as f:
            f.write("{ not json")
        self.config(hub=hub)
        self.pointer("s-bad00001", bad)
        out = self.feed(hub)
        by = self.by_id(out)
        self.assertTrue(by["badone"]["error"])
        self.assertIsNone(by["badone"]["counts"])
        self.assertEqual(by["badone"]["queue_recent"], [])
        self.assertIsNone(by["hubby"]["error"])
        self.assertEqual(by["hubby"]["counts"]["next"], 1)

    def test_watch_paths_are_absolute_real_under_home_capped_and_never_pointers(self):
        hub = self.mk(self.proj("hubby"))
        self.config(hub=hub)
        self.pointer("s-ptr00001", hub)
        for i in range(70):
            self.session("s-many%04d" % i, hub, status="idle")
        out = self.feed(hub)
        files = out["watch"]["files"]
        self.assertLessEqual(len(files), 64)
        self.assertEqual(out["watch"]["check_s"], 2)
        self.assertIn(os.path.join(hub, ".studio", "ledger.json"), files)
        self.assertIn(self.sessions_dir, files)
        self.assertTrue(any(f.startswith(self.sessions_dir + os.sep) and f.endswith(".json") for f in files))
        for f in files:
            self.assertTrue(os.path.isabs(f))
            self.assertEqual(f, os.path.realpath(f))
            self.assertTrue(f.startswith(self.uhome + os.sep), f)
            self.assertNotIn(self.bhome, f)                        # the pointer files are never watched
        self.assertEqual(len(files), len(set(files)))

    def test_the_whole_feed_stays_under_30_kb_at_the_caps(self):
        hub = self.mk(self.proj("hubby"), [("R-%d" % n, "open", {"title": "T" * 90}) for n in range(1, 40)]
                      + [("R-%d" % n, "done", {"title": "D" * 90, "done_at": iso(time.time() - n * 60)}) for n in range(50, 60)])
        self.config(hub=hub, root=self.proj("many"))
        for i in range(14):
            self.mk(self.proj("many", "project-number-%02d" % i), [("R-%d" % n, "open", {"title": "Q" * 90}) for n in range(1, 8)])
        for i in range(30):
            self.session("s-size%04d" % i, self.proj("many", "project-number-%02d" % (i % 14)), status="busy", name="S" * 120)
        self.mk(hub, [("R-%d" % n, "doing", {"doing_by": "s-gone%04d" % n, "doing_since": "2026-10-20T10:00:00.000Z",
                                              "title": "C" * 90}) for n in range(1, 15)])
        r = self.cli("--live", project=hub)
        # the spec's "about 30 KB" at every cap; the round-3 panel's words (R-200, ~1.2 KB of `t`) took it to 32 KB
        self.assertLess(len(r.stdout.encode("utf-8")), 32_000, len(r.stdout))
        self.assertEqual(len(json.loads(r.stdout)["projects"]), 12)


class WeekAndDoneTest(LiveCase):
    def test_week_counts_monday_first_local_time_with_the_updated_fallback(self):
        # Wednesday 2026-10-21 12:00 local
        now = time.mktime((2026, 10, 21, 12, 0, 0, 0, 0, -1))
        at = lambda d, h=10: time.mktime((2026, 10, d, h, 0, 0, 0, 0, -1))
        rows = [("a", "R-1", "x", at(19)), ("a", "R-2", "x", at(19, 23)), ("a", "R-3", "x", at(21)),
                ("b", "R-4", "x", at(18, 23)),            # Sunday before: out
                ("b", "R-5", "x", at(25))]                # Sunday after: day 6
        self.assertEqual(livework.week_counts(rows, now), [2, 0, 1, 0, 0, 0, 1])

    def test_done_at_wins_over_updated_and_updated_is_the_fallback(self):
        t = time.time()
        hub = self.mk(self.proj("hubby"), [
            ("R-1", "done", {"done_at": iso(t - 10), "updated": "2020-01-01T00:00:00.000Z", "title": "Con done_at"}),
            ("R-2", "verified", {"updated": iso(t - 5), "title": "Legado"}),
            ("R-3", "done", {"done_at": iso(t - 30 * 86400), "updated": iso(t), "title": "Viejo"}),
            ("R-4", "open", {"updated": iso(t)}),
            ("R-5", "dropped", {"updated": iso(t)})])
        other = self.mk(self.proj("other"), [("R-1", "done", {"done_at": iso(t - 60), "title": "De otro"})])
        self.config(hub=hub)
        self.pointer("s-oth00001", other)
        out = self.feed(hub)
        rd = out["recent_done"]
        self.assertEqual([(r["project"], r["item"], r["title"]) for r in rd],
                         [("hubby", "R-2", "Legado"), ("hubby", "R-1", "Con done_at"), ("other", "R-1", "De otro"),
                          ("hubby", "R-3", "Viejo")])
        self.assertEqual(set(rd[0]), {"project", "item", "title", "done_at"})
        self.assertRegex(rd[0]["done_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")      # the panel parses an ISO string, not a number
        self.assertAlmostEqual(calendar.timegm(time.strptime(rd[0]["done_at"], "%Y-%m-%dT%H:%M:%SZ")), t - 5, delta=2)
        local = time.localtime(t)
        week_start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday - local.tm_wday, 0, 0, 0, 0, 0, -1))
        in_week = [x for x in (t - 5, t - 10, t - 60) if x >= week_start]
        self.assertEqual(sum(out["week"]), len(in_week))     # "Viejo" (30 days ago) never counts; open/dropped never
        self.assertGreaterEqual(out["week"][local.tm_wday], 1)

    def test_recent_done_is_capped_at_6(self):
        t = time.time()
        hub = self.mk(self.proj("hubby"), [("R-%d" % n, "done", {"done_at": iso(t - n * 100)}) for n in range(1, 10)])
        self.config(hub=hub)
        rd = self.feed(hub)["recent_done"]
        self.assertEqual([r["item"] for r in rd], ["R-%d" % n for n in range(1, 7)])


class DoneAtTest(LiveCase):
    def setUp(self):
        super().setUp()
        self.p = self.mk(self.proj("hubby"), [("R-1", "open"), ("R-2", "open")])

    def test_done_writes_done_at_once_and_never_moves_it(self):
        data = sl.load_ledger(self.p)
        item = sl.set_status(self.p, data, "R-1", "doing")
        self.assertNotIn("done_at", item)
        item = sl.set_status(self.p, data, "R-1", "done", evidence="test output")
        self.assertRegex(item["done_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        first = item["done_at"]
        item["done_at"] = "2020-01-01T00:00:00.000Z"                # whatever is stored stays
        item = sl.set_status(self.p, data, "R-1", "done", evidence="more evidence")
        self.assertEqual(item["done_at"], "2020-01-01T00:00:00.000Z")
        self.assertNotEqual(first, "2020-01-01T00:00:00.000Z")
        item = sl.set_status(self.p, data, "R-1", "open")
        item = sl.set_status(self.p, data, "R-1", "done", evidence="again")
        self.assertEqual(item["done_at"], "2020-01-01T00:00:00.000Z")

    def test_verify_stamps_done_at_when_missing_and_keeps_an_existing_one(self):
        data = sl.load_ledger(self.p)
        os.makedirs(os.path.join(sl.studio_dir(self.p), "verify"), exist_ok=True)
        for n in ("R-1", "R-2"):
            with open(os.path.join(sl.studio_dir(self.p), "verify", n + ".md"), "w") as f:
                f.write("PASS\n")
        data["items"][1]["done_at"] = "2021-02-03T04:05:06.000Z"
        with mock.patch.object(sl, "_check_prepared"), mock.patch.object(sl, "report_sealed", return_value=True):
            one = sl.set_status(self.p, data, "R-1", "verified")
            two = sl.set_status(self.p, data, "R-2", "verified")
        self.assertRegex(one["done_at"], r"^\d{4}-\d\d-\d\dT")
        self.assertEqual(two["done_at"], "2021-02-03T04:05:06.000Z")

    def test_closing_an_item_that_was_already_closed_keeps_its_old_time_and_never_stamps_now(self):
        # legacy items closed before done_at existed: a later `verify` (or any touch) must not make them "closed today"
        data = sl.load_ledger(self.p)
        old = "2026-10-01T08:09:10.000Z"
        for n in (0, 1):
            data["items"][n].update(status="done", evidence="e", updated=old)
        os.makedirs(os.path.join(sl.studio_dir(self.p), "verify"), exist_ok=True)
        for n in ("R-1", "R-2"):
            with open(os.path.join(sl.studio_dir(self.p), "verify", n + ".md"), "w") as f:
                f.write("PASS\n")
        with mock.patch.object(sl, "_check_prepared"), mock.patch.object(sl, "report_sealed", return_value=True):
            one = sl.set_status(self.p, data, "R-1", "verified")
            two = sl.set_status(self.p, data, "R-2", "done", evidence="again")
        self.assertEqual(one["done_at"], old)
        self.assertEqual(two["done_at"], old)
        self.assertNotEqual(one["updated"], old)                  # `updated` still moves, as before

    def test_open_and_doing_do_not_get_done_at(self):
        data = sl.load_ledger(self.p)
        for st in ("doing", "open"):
            self.assertNotIn("done_at", sl.set_status(self.p, data, "R-2", st))

class PrivateProjectsTest(LiveCase):
    """A protected project (config `protected_dirs`, or `"private": true` in its own project.json) shows counts only:
    no item title, no session title, no folder name, no path, in any field of the feed or of `status --brote`."""
    SECRET = "SECRETO"

    def private_project(self, *parts, protect_in_project=False):
        t = time.time()
        path = self.mk(os.path.join(self.uhome, *parts), [
            ("R-1", "doing", {"title": "SECRETO claim", "doing_by": "s-secr0001", "doing_since": iso(t - 60)}),
            ("R-2", "open", {"title": "SECRETO queued"}),
            ("R-3", "done", {"title": "SECRETO finished", "done_at": iso(t - 30)}),
            ("R-4", "open", {"title": "SECRETO later", "phase": "later"})])
        if protect_in_project:
            with open(os.path.join(sl.studio_dir(path), "project.json"), "w") as f:
                json.dump({"private": True}, f)
        self.session("s-secr0001", path, name="SECRETO session")
        return path

    def assertClean(self, out):
        dump = json.dumps(out, ensure_ascii=False)
        for word in (self.SECRET, "zzcaja", "Vaultdir"):
            self.assertNotIn(word, dump)

    def setup_hub(self, **cfg):
        hub = self.mk(self.proj("hubby"), [("R-1", "open", {"title": "Hub visible"})])
        os.makedirs(self.bhome, exist_ok=True)
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump(dict({"hub": hub, "projects_root": None}, **cfg), f)
        return hub

    def test_a_project_in_protected_dirs_has_counts_and_no_names(self):
        hub = self.setup_hub(protected_dirs=["~/Vaultdir"])
        self.private_project("Vaultdir", "zzcaja")
        out = self.feed(hub)
        self.assertClean(out)
        row = [p for p in out["projects"] if p.get("private")][0]
        self.assertEqual((row["counts"]["doing"], row["counts"]["next"], row["counts"]["later"], row["counts"]["done"]),
                         (1, 1, 1, 1))
        self.assertEqual(row["queue_recent"], [])
        self.assertTrue(row["id"].startswith("private") and row["name"] == "private")
        self.assertEqual(sum(out["week"]), 1)                 # the done item still counts in the week
        self.assertEqual(out["recent_done"], [])
        self.assertEqual([w for w in out["working"] if w["project"] == row["id"]], [])
        self.assertEqual(row["live"], 1)

    def test_the_marker_in_the_project_survives_a_move(self):
        hub = self.setup_hub(protected_dirs=["~/OldPlace"])               # stale: the folder moved elsewhere
        self.private_project("Elsewhere", "zzcaja", protect_in_project=True)
        out = self.feed(hub)
        self.assertClean(out)
        self.assertEqual([p["name"] for p in out["projects"] if p.get("private")], ["private"])

    def test_protected_paths_alone_is_the_guard_list_and_not_privacy(self):
        hub = self.setup_hub()
        path = self.mk(self.proj("guarded"), [("R-1", "open", {"title": "Titulo guardado"})])
        with open(os.path.join(sl.studio_dir(path), "project.json"), "w") as f:
            json.dump({"protected_paths": ["data/"]}, f)
        self.assertFalse(livework.is_private(path))
        self.pointer("s-grd00001", path)
        self.assertEqual([q["title"] for q in self.by_id(self.feed(hub))["guarded"]["queue_recent"]], ["Titulo guardado"])

    def test_both_config_names_are_read_and_a_tilde_is_expanded(self):
        hub = self.setup_hub()
        for name, d in ((".budgit", "~/Vaultdir"), (".studio", "~/Other")):
            os.makedirs(os.path.join(self.uhome, name), exist_ok=True)
            with open(os.path.join(self.uhome, name, "config.json"), "w") as f:
                json.dump({"protected_dirs": [d]}, f)
        self.private_project("Vaultdir", "zzcaja")
        self.assertClean(self.feed(hub))
        self.assertTrue(livework.is_private(os.path.join(self.uhome, "Other", "x")))
        self.assertTrue(livework.is_private(os.path.join(self.uhome, "OTHER", "x")))      # APFS folds case
        self.assertFalse(livework.is_private(os.path.join(self.uhome, "Others", "x")))

    def test_a_session_inside_a_protected_folder_without_a_ledger_is_hidden(self):
        hub = self.setup_hub(protected_dirs=["~/Vaultdir"])
        folder = os.path.join(self.uhome, "Vaultdir", "zzcaja")
        os.makedirs(folder)
        self.session("s-nolg0001", folder, name="SECRETO loose session")
        out = self.feed(hub)
        self.assertClean(out)
        self.assertEqual([w for w in out["working"] if w["kind"] == "session"], [])

    def test_a_session_under_the_protected_home_folders_is_hidden_without_a_config_entry(self):
        # Desktop, Downloads, iCloud and /Volumes are protected places even when `protected_dirs` does not list them
        hub = self.setup_hub()
        folders = [os.path.join(self.uhome, "Desktop", "zzcaja"), os.path.join(self.uhome, "Downloads", "zzcaja"),
                   os.path.join(self.uhome, "Library", "Mobile Documents", "com~apple~CloudDocs", "zzcaja"),
                   os.path.join(self.uhome, "desktop", "zzcaja", "sub")]         # APFS folds case
        for n, folder in enumerate(folders):
            os.makedirs(folder, exist_ok=True)
            self.session("s-prot000%d" % n, folder, name="SECRETO nombre %d" % n)
        self.session("s-vol00001", "/Volumes/SECRETO-disk/zzcaja", name="SECRETO volume")
        self.session("s-open0001", self.proj("hubby"), name="Sesion visible")
        out = self.feed(hub)
        self.assertClean(out)
        self.assertEqual([w["session_name"] for w in out["working"] if w["kind"] == "session"], ["Sesion visible"])
        self.assertEqual(out["quiet_sessions"], 0)

    def test_the_watch_list_never_names_a_protected_ledger(self):
        hub = self.setup_hub(protected_dirs=["~/Vaultdir"])
        self.private_project("Vaultdir", "zzcaja")
        self.assertClean(self.feed(hub)["watch"])

    def test_status_brote_of_a_protected_project_is_counts_only(self):
        hub = self.setup_hub(protected_dirs=["~/Vaultdir"])
        path = self.private_project("Vaultdir", "zzcaja")
        for live in (False, True):
            r = self.cli(*(["--live"] if live else []), project=path)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertClean(out)
            self.assertEqual((out["counts"]["doing"], out["done_total"]), (1, 1))
            self.assertTrue(out["version"] in (2, 3))

    def test_two_work_data_folders_are_covered_alike_and_a_sibling_is_not(self):
        hub = self.setup_hub(protected_dirs=["~/Projects/Equipo", "~/Projects/anon-x"])
        dev = os.path.join(self.uhome, "Projects")
        withledger = self.private_project("Projects", "Equipo", "zzcaja")
        loose = os.path.join(dev, "anon-x", "Vaultdir")
        os.makedirs(loose)
        self.session("s-anon0001", loose, name="SECRETO anon session")
        sibling = self.mk(os.path.join(dev, "Equipo2"), [("R-1", "open", {"title": "Titulo hermano"})])
        self.pointer("s-sib00001", sibling)
        out = self.feed(hub)
        self.assertClean(out)
        self.assertEqual([w for w in out["working"] if w["kind"] == "session"], [])
        self.assertEqual([q["title"] for q in self.by_id(out)["Equipo2"]["queue_recent"]], ["Titulo hermano"])
        self.assertTrue(livework.is_private(withledger) and livework.is_private(loose))
        self.assertFalse(livework.is_private(sibling))

    def test_unprotected_projects_are_unchanged(self):
        hub = self.setup_hub(protected_dirs=["~/Vaultdir"])
        pub = self.mk(self.proj("open"), [("R-9", "open", {"title": "Titulo publico"}),
                                          ("R-8", "done", {"title": "Hecho publico", "done_at": iso(time.time() - 5)})])
        self.pointer("s-pub00001", pub)
        out = self.feed(hub)
        row = self.by_id(out)["open"]
        self.assertEqual([q["title"] for q in row["queue_recent"]], ["Titulo publico"])
        self.assertEqual([r["title"] for r in out["recent_done"]], ["Hecho publico"])
        self.assertFalse(row.get("private"))
        r = self.cli(project=hub)
        self.assertIn("Hub visible", r.stdout)


class PrivateTitlesTest(LiveCase):
    """A title in an ordinary ledger that names a protected project shows as "private": the names come from the
    `protected_dirs` of the config and from private projects' folder names, never from reading those folders."""

    def setUp(self):
        super().setUp()
        t = time.time()
        self.hub = self.mk(self.proj("hubby"), [
            ("R-1", "open", {"title": "Revisar Equipo antes del viernes", "text": "pedir a Equipo"}),
            ("R-2", "open", {"title": "Titulo normal sobre dev tools"}),
            ("R-3", "done", {"title": "Cerrar equipo-main", "done_at": iso(t - 20)}),
            ("R-4", "done", {"title": "Hecho normal", "done_at": iso(t - 10)}),
            ("R-5", "doing", {"title": "Equipo claim", "doing_by": "s-busy0001", "doing_since": iso(t - 60)}),
            ("R-6", "open", {"title": "Mover zzcaja a otro sitio"}),
            ("R-7", "open", {"title": "Zed notes pendientes"}),
            ("R-8", "open", {"title": "Zedoary no es un nombre protegido"}),
            ("R-9", "open", {"title": "Pedir permisos a K.O.L.A"})])
        os.makedirs(self.bhome, exist_ok=True)
        with open(os.path.join(self.bhome, "config.json"), "w") as f:       # none of these folders exists: nothing can be read
            json.dump({"hub": self.hub, "projects_root": None,
                       "protected_dirs": ["~/Projects/Equipo", "~/Dev", "~/Dev/Zed", "~/Dev/K-O-L-A"]}, f)
        self.marked = self.mk(os.path.join(self.uhome, "Elsewhere", "zzcaja"), [("R-1", "open", {"title": "x"})])
        with open(os.path.join(sl.studio_dir(self.marked), "project.json"), "w") as f:
            json.dump({"private": True}, f)
        self.pointer("s-zz000001", self.marked)
        self.session("s-busy0001", self.hub, status="busy", name="Sesion de Equipo")
        self.session("s-busy0002", self.hub, status="busy", name="Sesion normal")

    def titles(self, rows):
        return {r["item"] if "item" in r else r["id"]: r["title"] for r in rows}

    def test_feed_titles_naming_a_protected_project_read_private(self):
        out = self.feed(self.hub)
        hub = self.by_id(out)["hubby"]
        queue = self.titles(hub["queue_recent"])
        self.assertEqual(queue["R-1"], "private")
        self.assertEqual(queue["R-2"], "Titulo normal sobre dev tools")          # `Dev` is a generic folder name: no match
        self.assertEqual(queue["R-6"], "private")                                # the folder name of a private project
        self.assertEqual(queue["R-7"], "private")                                # a 3-letter name matches as a whole word
        self.assertEqual(queue["R-8"], "Zedoary no es un nombre protegido")
        self.assertEqual(queue["R-9"], "private")                                # separators do not hide a name
        done = self.titles(out["recent_done"])
        self.assertEqual((done["R-3"], done["R-4"]), ("private", "Hecho normal"))
        claim = [w for w in out["working"] if w.get("item") == "R-5"][0]
        self.assertEqual((claim["title"], claim["session_name"]), ("private", "private"))
        row = [w for w in out["working"] if w["kind"] == "session"]
        self.assertEqual([(w["title"], w["session_name"]) for w in row], [("Sesion normal", "Sesion normal")])
        dump = json.dumps(out, ensure_ascii=False)
        for word in ("Equipo", "zzcaja", "K.O.L.A"):
            self.assertNotIn(word, dump)

    def test_status_brote_v2_lists_are_shielded_too(self):
        for live in (False, True):
            r = self.cli(*(["--live"] if live else []), project=self.hub)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            q = {i["id"]: i for i in out["queue_recent"]}
            self.assertEqual((q["R-1"]["title"], q["R-1"]["text"]), ("private", "private"))
            self.assertEqual(q["R-2"]["title"], "Titulo normal sobre dev tools")
            self.assertEqual({i["id"]: i["title"] for i in out["done_recent"]}.get("R-3", "private"), "private")
            self.assertEqual([i["title"] for i in out["doing"]], ["private"])
            self.assertNotIn("Equipo", json.dumps(out["queue"] + out["queue_recent"] + out["doing"] + out["done_recent"],
                                                  ensure_ascii=False))

    def test_an_idle_claimants_paused_row_is_shielded_too(self):
        self.mk(self.hub, [("R-5", "doing", {"title": "Equipo claim", "doing_by": "s-idle0001", "doing_since": iso(time.time() - 60)})])
        self.session("s-idle0001", self.hub, status="idle", name="Sesion de Equipo")
        out = self.feed(self.hub)
        self.assertEqual([(p["item"], p["title"], p["reason"]) for p in out["paused"]], [("R-5", "private", "idle")])
        self.assertNotIn("Equipo", json.dumps(out["paused"] + out["working"], ensure_ascii=False))

    def test_a_title_holding_an_alias_reads_private_in_every_feed_list(self):
        t = time.time()
        self.mk(self.hub, [("R-1", "open", {"title": "Revisar protected-2 antes", "text": "ver protected-2"}),
                           ("R-4", "done", {"title": "Cerrado protected-1 ya", "done_at": iso(t - 10)}),
                           ("R-5", "doing", {"title": "protected-1 busy", "doing_by": "s-busy0001", "doing_since": iso(t - 60)}),
                           ("R-6", "doing", {"title": "protected-2 idle", "doing_by": "s-idle0001", "doing_since": iso(t - 50)}),
                           ("R-7", "open", {"title": "Titulo normal"})])
        self.session("s-idle0001", self.hub, status="idle")
        out = self.feed(self.hub)
        self.assertEqual({p["item"]: p["title"] for p in out["paused"]}, {"R-6": "private"})
        claim = [w for w in out["working"] if w.get("item") == "R-5"][0]
        self.assertEqual(claim["title"], "private")
        self.assertEqual({d["item"]: d["title"] for d in out["recent_done"]}["R-4"], "private")
        queue = self.titles(self.by_id(out)["hubby"]["queue_recent"])
        self.assertEqual((queue["R-1"], queue["R-7"]), ("private", "Titulo normal"))
        self.assertNotIn("protected-", json.dumps(out, ensure_ascii=False))
        r = self.cli(project=self.hub)
        q = {i["id"]: i for i in json.loads(r.stdout)["queue_recent"]}
        self.assertEqual((q["R-1"]["title"], q["R-1"]["text"]), ("private", "private"))

    def test_the_alias_shield_matches_whole_tokens_only(self):
        for text in ("see protected-2 now", "Protected-12", "protected-1-2"):
            self.assertEqual(livework.shield(text, [], alias=True), "private", text)
        for text in ("unprotected-1 case", "protected-x", "protected", "xprotected-3", "protected-12x", "protected-3abc"):
            self.assertEqual(livework.shield(text, [], alias=True), text, text)
        self.assertEqual(livework.shield("see protected-2 now", []), "see protected-2 now")     # retext callers keep it

    def test_a_made_up_name_in_an_ordinary_project_reads_private_in_claim_paused_and_done_rows(self):
        t = time.time()
        self.mk(self.hub, [("R-4", "done", {"title": "Cerrar zzcaja", "done_at": iso(t - 10)}),
                           ("R-5", "doing", {"title": "zzcaja busy", "doing_by": "s-busy0001", "doing_since": iso(t - 60)}),
                           ("R-6", "doing", {"title": "zzcaja idle", "doing_by": "s-idle0001", "doing_since": iso(t - 50)})])
        self.session("s-idle0001", self.hub, status="idle")
        out = self.feed(self.hub)
        self.assertEqual({p["item"]: p["title"] for p in out["paused"]}, {"R-6": "private"})
        self.assertEqual([w["title"] for w in out["working"] if w.get("item") == "R-5"], ["private"])
        self.assertEqual({d["item"]: d["title"] for d in out["recent_done"]}["R-4"], "private")
        self.assertNotIn("zzcaja", json.dumps(out, ensure_ascii=False))

    def test_a_private_project_gives_counts_only_whatever_its_titles_say(self):
        self.mk(self.marked, [("R-1", "open", {"title": "Titulo neutro uno"}), ("R-2", "doing", {"title": "Titulo neutro dos"}),
                              ("R-3", "done", {"title": "Titulo neutro tres", "done_at": iso(time.time() - 10)})])
        out = self.feed(self.hub)
        rows = [p for p in out["projects"] if p.get("private")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["queue_recent"], [])
        self.assertIsNotNone(rows[0]["counts"])
        self.assertNotIn("Titulo neutro", json.dumps(out, ensure_ascii=False))

    def test_a_failing_feed_leaves_no_title_in_the_v2_lists(self):
        v2 = {"queue_recent": [{"id": "R-1", "title": "zzcaja", "text": "zzcaja"}], "done_recent": [{"id": "R-2", "title": "x"}],
              "doing": [{"id": "R-3", "title": "y"}], "queue": [{"id": "R-4", "title": "z"}], "later": []}
        with mock.patch.object(livework, "live_sessions", side_effect=RuntimeError("boom")):
            feed = livework.live_feed(self.hub, v2=v2)
        self.assertEqual(feed["sources"]["claims"], "unreadable")
        self.assertEqual([v2[k] for k in ("queue_recent", "done_recent", "doing", "queue", "later")], [[]] * 5)

    def test_names_come_from_config_strings_and_a_generic_folder_name_is_skipped(self):
        names = livework.private_names()
        self.assertTrue({"equipo", "zed", "k o l a"} <= set(names))
        self.assertNotIn("dev", names)
        self.assertEqual(livework.shield("hola equipo", names), "private")
        self.assertEqual(livework.shield("nada que ocultar", names), "nada que ocultar")


if __name__ == "__main__":
    unittest.main()
