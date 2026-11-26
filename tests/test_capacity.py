"""R-286 task 3: the dynamic limit. total() (budget level -> T), split() (T between projects by priority), free()
(less what is stuck), the gate inside `ledger start`, `budgit capacity` and the cached fields the live feed carries."""
import json
import os
import subprocess
import sys
import time

from helpers import ROOT
from test_flow_data import FlowCase
import capacity
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
TODAY = "2026-10-29"


def proj(slug, priority="normal", ready=6, committed=True, paused=False, waited_s=0):
    return {"slug": slug, "priority": priority, "committed": committed, "ready": ready, "paused": paused, "waited_s": waited_s}


def slots(T, projects):
    return {k: v["slots"] for k, v in capacity.split(T, projects).items()}


class TotalSplitFreeTest(FlowCase):
    def test_examples_table(self):
        T, _ = capacity.total(None, "normal", 0, 0, None, None, TODAY)
        self.assertEqual(slots(T, [proj("budgit")]), {"budgit": 4})                                # 1 project, 6 ready
        T, _ = capacity.total(None, "ample", 0, 0, None, None, TODAY)
        self.assertEqual(slots(T, [proj("a", "high"), proj("b"), proj("c", "low")]), {"a": 3, "b": 2, "c": 1})
        T, _ = capacity.total(None, "normal", 0, 0, None, None, TODAY)
        self.assertEqual(slots(T, [proj(n) for n in "abcde"]), {n: 1 for n in "abcde"})            # 5 projects, normal
        T, _ = capacity.total(None, "tight", 0, 0, None, None, TODAY)
        five = slots(T, [proj("a", waited_s=1), proj("b", waited_s=2), proj("c", waited_s=900), proj("d", waited_s=3), proj("e", waited_s=4)])
        self.assertEqual(sorted(five.values()), [1, 1, 1, 1, 1])                                    # R-877: T=3 but every live project keeps its one lane
        self.assertEqual(five["c"], 1)
        T, _ = capacity.total(5, "tight", 12, 4, None, None, TODAY)                                 # a drop applies at once
        self.assertEqual(T, 3)
        self.assertEqual(capacity.free(3, 4, 0)[0], 0)                                              # nothing running is stopped, nothing new starts

    def test_raise_one_per_day_only_when_used(self):
        self.assertEqual(capacity.total(3, "normal", 0, 3, None, None, TODAY)[0], 4)
        self.assertEqual(capacity.total(3, "normal", 0, 1, None, None, TODAY), (3, "hold_use"))     # less than half in use
        self.assertEqual(capacity.total(3, "normal", 0, 3, None, TODAY, TODAY), (3, "hold_day"))    # already raised today
        self.assertEqual(capacity.total(5, "normal", 0, 5, None, None, TODAY)[0], 5)                # never above the ceiling

    def test_dead_band_holds(self):
        for pace in (6, 9):
            self.assertEqual(capacity.total(3, "normal", pace, 3, None, None, TODAY), (3, "hold_pace"))

    def test_projection_over_100_uses_lower_level(self):
        T, reason = capacity.total(None, "normal", 0, 0, 120, None, TODAY)
        self.assertEqual(T, 3)                                                                      # the tight ceiling
        self.assertIn("projected", reason)

    def test_water_filling_respects_caps_and_floors(self):
        self.assertEqual(slots(6, [proj("a", "high", ready=1), proj("b")]), {"a": 1, "b": 4})       # a is capped by what is ready
        self.assertEqual(slots(5, [proj("a", committed=False), proj("b")]), {"a": 0, "b": 4})       # nothing committed, nothing given
        self.assertEqual(slots(5, [proj("a", paused=True), proj("b")])["a"], 0)
        self.assertEqual(capacity.free(4, 1, 1), (2, "ok"))
        self.assertEqual(capacity.free(4, 1, 2), (0, "stuck"))


import unittest
from unittest import mock


class LiveWorkCountersTest(unittest.TestCase):
    """The counters reflect real, live work (synthetic projects proj-a and proj-b)."""

    def row(self, items, live_owners=("live",)):
        with mock.patch.object(capacity, "_live", lambda item, now: item.get("doing_by") in live_owners):
            return capacity._project_row("proj-a", {"path": "/proj-a"}, items, 1000.0, False)

    @staticmethod
    def item(item_id, status="open", **kw):
        return dict({"id": item_id, "status": status, "title": "t"}, **kw)

    def test_in_flight_counts_only_doing_items_with_a_live_session(self):
        got = self.row([self.item("A-1", "doing", doing_by="live"), self.item("A-2", "doing", doing_by="gone"),
                        self.item("A-3", "doing")])
        self.assertEqual(got["in_flight"], 1)

    def test_stuck_counts_only_doing_items_never_blocked_open_items(self):
        got = self.row([self.item("A-1", blocked=True), self.item("A-2", blocked=True)])
        self.assertEqual(got["stuck"], 0)
        got = self.row([self.item("A-1", blocked=True), self.item("A-2", "doing", doing_by="live", blocked=True)])
        self.assertEqual(got["stuck"], 1)

    def test_paused_project_has_no_floor_and_adds_nothing_to_in_use(self):
        paused = {"slug": "proj-a", "path": "/a", "priority": "normal", "paused": True, "committed": 2, "ready": 2,
                  "in_flight": 3, "stuck": 0, "waited_s": None, "private": False}
        live = dict(paused, slug="proj-b", path="/b", paused=False, in_flight=0)
        inputs = {"level": "normal", "pace": 0, "projected": None, "h5": None, "nodata": False}
        with mock.patch.object(capacity, "portfolio_snapshot", lambda now, current: ([paused, live], [])), \
                mock.patch.object(capacity, "load_state", lambda: {"T": 3}):
            got = capacity.compute(now=1000.0, inputs=inputs, write=False)
        self.assertEqual(got["T"], 3)                                          # in_use 0, so no raise from the paused lanes
        self.assertEqual(got["reason"], "hold_use")
        self.assertEqual((got["projects"]["proj-a"]["floor"], got["projects"]["proj-a"]["slots"]), (0, 0))

    def test_parallel_ready_skips_items_sharing_files_or_the_suite_lock(self):
        def it(item_id, files, suite=False):
            return {"id": item_id, "files": files, "suite": suite}
        projects = {
            "proj-a": {"free": 3, "paused": False, "private": False,
                       "ready_items": [it("A-1", ["a.py"]), it("A-2", ["a.py"]), it("A-3", ["b.py"], True), it("A-4", ["c.py"], True)]},
            "proj-b": {"free": 1, "paused": False, "private": False, "ready_items": [it("B-1", []), it("B-2", [])]},
            "proj-c": {"free": 0, "paused": False, "private": False, "ready_items": [it("C-1", [])]}}
        self.assertEqual(capacity.parallel_ready(projects), {"proj-a": ["A-1", "A-3"], "proj-b": ["B-1"]})

    def test_item_with_no_files_field_is_overlap_unknown_never_fitting(self):
        row = self.row([self.item("A-1"), self.item("A-2", files=[])])
        self.assertEqual([i["files"] for i in row["ready_items"]], [None, []])
        projects = {"proj-a": {"free": 2, "paused": False, "private": False, "ready_items": row["ready_items"]}}
        self.assertEqual(capacity.parallel_ready(projects), {"proj-a": ["A-2"]})
        self.assertEqual(capacity.overlap_unknown(projects), {"proj-a": ["A-1"]})

    def test_budgit_capacity_prints_the_fitting_line_and_the_overlap_unknown_line(self):
        import argparse
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("budgit_cli_capacity_test", CLI)
        cli = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(cli)
        ready = [{"id": "A-1", "files": ["a.py"], "suite": False}, {"id": "A-2", "files": None, "suite": False}]
        pic = {"T": 3, "reason": "start", "level": "normal", "nodata": False, "h5": None, "skipped": [],
               "projects": {"proj-a": {"used": 0, "slots": 3, "free": 3, "free_reason": "ok", "layer": "weights",
                                       "paused": False, "private": False, "ready_items": ready}}}
        out = io.StringIO()
        with mock.patch.object(capacity, "compute", lambda *a, **k: pic), contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_capacity(argparse.Namespace(json=False)), 0)
        lines = out.getvalue().splitlines()
        fitting = [x for x in lines if "A-1" in x]
        unknown = [x for x in lines if "A-2" in x]
        self.assertEqual((len(fitting), len(unknown)), (1, 1))
        self.assertNotIn("A-2", fitting[0])
        self.assertNotIn("A-1", unknown[0])
        self.assertTrue("unknown" in unknown[0] or "desconocido" in unknown[0], unknown[0])

    def test_admit_unreadable_memory_says_unknown_and_does_not_refuse(self):
        import admit
        blind = {"unknown": ["pressure", "free_true_mb"], "pressure": None, "free_true_mb": None, "compressor_mb": None}
        got = admit.decide({"cpu_load": 1.0, "cores": 8, "free_mb": None, "busy": 0, "mem": blind}, "lane", {})
        self.assertEqual(got["verdict"], "go")
        self.assertTrue("unknown" in got["reason"] or "desconocida" in got["reason"], got["reason"])

    def test_admit_judges_memory_by_pressure_and_compressor_not_free_share(self):
        import admit
        calm = {"pressure": 1, "free_true_mb": 200, "compressor_mb": 500, "total_mb": 16000}
        self.assertEqual(admit.decide({"cpu_load": 1.0, "cores": 8, "free_mb": 200, "busy": 0, "mem": calm}, "lane", {})["verdict"], "go")
        full = dict(calm, compressor_mb=12000, free_true_mb=6000)
        self.assertEqual(admit.decide({"cpu_load": 1.0, "cores": 8, "free_mb": 6000, "busy": 0, "mem": full}, "lane", {})["verdict"], "refuse")


class GateCase(FlowCase):
    def setUp(self):
        super().setUp()
        import budget
        import portfolio
        self.budget, self.portfolio = budget, portfolio
        portfolio.save({"version": 1, "projects": [portfolio._entry(os.path.realpath(self.project))]})
        budget.set_override("critical")                                                              # one slot for the one project

    def start_in(self, item_id, session="s-flow0001", **kw):
        data = sl.load_ledger(self.project)
        out = sl.start_item(self.project, data, item_id, session, **kw)
        sl.save_ledger(self.project, data)
        return out

    def run_cli(self, *args, session="s-flow0001"):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_CODE_SESSION_ID=session)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)

    def audit_text(self):
        try:
            with open(os.path.join(sl.studio_dir(self.project), "audit.log")) as f:
                return f.read()
        except OSError:
            return ""


class GateTest(GateCase):
    def test_a_start_over_the_slots_is_refused(self):
        self.start_in(self.a)
        with self.assertRaises(sl.StudioError) as ctx:
            self.start_in(self.b, "s-flow0002")
        self.assertIn("1/1", str(ctx.exception))
        self.assertEqual(self.item(self.b)["status"], "open")

    def test_same_owner_restart_not_gated(self):
        self.start_in(self.a)
        self.start_in(self.a)                                                                        # keep-doing_since path: no new slot
        self.assertEqual(self.item(self.a)["status"], "doing")
        self.start_in(self.a, "s-flow0002", take=True)                                               # taking over a live claim moves it, not adds one

    def test_resume_reclaim_not_gated(self):
        self.start_in(self.a)
        self.budget.set_override("critical")
        rec = sl.read_pointer("s-flow0002")
        rec["released"] = [self.b]
        sl.atomic_write(sl.pointer_path("s-flow0002"), json.dumps(rec) + "\n")
        self.start_in(self.b, "s-flow0002")                                                          # C1: the lane's own released item comes back

    def test_blocked_open_items_are_not_stuck_and_do_not_stop_starts(self):
        self.budget.set_override("ample")
        data = sl.load_ledger(self.project)
        extra = [sl.add_item(data, "x%d" % n, ["legacy"])["id"] for n in range(3)]
        sl.save_ledger(self.project, data)
        for item_id in extra[:2]:
            self.block(item_id, "waiting for her")
        self.start_in(self.a)                                                                        # stuck counts doing items only

    def _msg(self, fn):
        try:
            fn()
        except sl.StudioError as e:
            return str(e)
        return ""

    def test_no_budget_data_uses_normal_with_reason(self):
        os.remove(os.path.join(self.home, "budget.json"))
        pic = capacity.compute(write=False)
        self.assertEqual((pic["level"], pic["T"], pic["nodata"]), ("normal", 5, True))
        out = self.run_cli("capacity")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("sin datos de presupuesto", out.stdout)

    def test_start_in_a_protected_project_without_a_pointer_says_to_run_activate(self):
        data = sl.load_ledger(self.project)
        with mock.patch.object(sl, "_project_is_private", lambda p: True):
            with self.assertRaises(sl.StudioError) as caught:
                sl.start_item("/proj-protected", data, self.a, "s-flow0099")
        self.assertIn("budgit activate /proj-protected", str(caught.exception))
        with self.assertRaises(sl.StudioError) as plain:                                             # an ordinary project keeps its message
            sl.start_item(self.project, data, self.a, "s-flow0099")
        self.assertNotIn("budgit activate", str(plain.exception))

    def test_gate_fails_open_without_data(self):
        os.remove(self.portfolio.list_path())                                                        # no list at all
        self.start_in(self.a)
        self.start_in(self.b, "s-flow0002")                                                          # nothing to share, nothing refused
        os.remove(os.path.join(self.home, "budget.json"))
        self.assertIsNone(capacity.check_start(self.project, sl.load_ledger(self.project), {"id": self.a}, inputs={"bad": 1}))   # a broken input is not a refusal

    def test_bad_project_skipped(self):
        gone = os.path.join(self.tmp, "gone")
        broken = os.path.join(self.tmp, "broken")
        os.makedirs(os.path.join(broken, self.DATA))
        with open(os.path.join(broken, self.DATA, "ledger.json"), "w") as f:
            f.write("{not json")
        reg = self.portfolio.load()
        reg["projects"] += [self.portfolio._entry(gone), self.portfolio._entry(broken)]
        self.portfolio.save(reg)
        pic = capacity.compute(write=False)
        self.assertEqual(len(pic["skipped"]), 2)
        self.assertEqual(len(pic["projects"]), 1)
        self.start_in(self.a)                                                                        # the bad ones block nobody

    def test_h5_gate_refuses_start(self):
        self.budget.save({"limits": {"five_hour": {"used": 85.0, "resets_at": time.time() + 3600}}, "seen_at": time.time(),
                          "override": "ample"})
        msg = self._msg(lambda: self.start_in(self.a))
        self.assertIn("85", msg)
        self.assertIn("70", msg)
        self.assertEqual(self.item(self.a)["status"], "open")

    def test_r1104_five_hour_off_the_gate_does_not_refuse_on_five_hour(self):
        self.budget.save({"limits": {"five_hour": {"used": 85.0, "resets_at": time.time() + 3600}}, "seen_at": time.time(),
                          "override": "ample"})
        sl.set_five_hour_window("off")
        self.start_in(self.a)
        self.assertEqual(self.item(self.a)["status"], "doing")
        self.assertIsNone(capacity.budget_inputs()["h5"])

    def test_expedite_single_slot_and_logged(self):
        self.start_in(self.a)
        self.start_in(self.b, "s-flow0002", expedite=True)
        self.assertEqual(self.item(self.b)["status"], "doing")
        self.assertIn("expedite", self.audit_text())
        data = sl.load_ledger(self.project)
        third = sl.add_item(data, "tercera", ["legacy"])["id"]
        sl.save_ledger(self.project, data)
        self.assertIn("urgente", self._msg(lambda: self.start_in(third, "s-flow0001", expedite=True)))   # the slot is taken
        self.status(self.b, "done", evidence="ok")                                                   # closing it frees the slot
        self.start_in(third, "s-flow0001", expedite=True)

    def test_the_expedite_slot_remembers_the_project_by_a_hash_not_its_path(self):
        self.start_in(self.a)
        self.start_in(self.b, "s-flow0002", expedite=True)
        with open(os.path.join(self.home, "capacity.json"), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(os.path.realpath(self.project), raw)
        self.assertIn(sl.path_key(self.project), raw)

    def test_override_by_needs_real_input(self):
        self.start_in(self.a)
        self.assertRaises(sl.StudioError, self.start_in, self.b, "s-flow0002", override_by="I-999")          # not in the inbox
        manual = sl.append_inbox(self.project, "lo añade la PM", "s-flow0001", "manual")
        self.assertRaises(sl.StudioError, self.start_in, self.b, "s-flow0002", override_by=manual)           # not typed by her
        typed = sl.append_inbox(self.project, "hazlo ya", "s-flow0001", "prompt")
        self.start_in(self.b, "s-flow0002", override_by=typed)
        self.assertEqual(self.item(self.b)["status"], "doing")
        self.assertIn(typed, self.audit_text())

    def test_refusal_text_es_en(self):
        self.start_in(self.a)
        es = self._msg(lambda: self.start_in(self.b, "s-flow0002"))
        self.assertIn("cupo de", es)
        os.environ["BUDGIT_LANG"] = "en"
        en = self._msg(lambda: self.start_in(self.b, "s-flow0002"))
        self.assertIn("limit of", en)
        self.assertNotEqual(es, en)

    def test_cli_flags_and_capacity_command(self):
        out = self.run_cli("ledger", "start", self.a)
        self.assertEqual(out.returncode, 0, out.stderr)
        refused = self.run_cli("ledger", "start", self.b, session="s-flow0002")
        self.assertNotEqual(refused.returncode, 0)
        ok = self.run_cli("ledger", "start", self.b, "--expedite", session="s-flow0002")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        data = json.loads(self.run_cli("capacity", "--json").stdout)
        self.assertEqual(data["T"], 1)
        (row,) = data["projects"].values()
        self.assertEqual((row["slots"], row["used"]), (1, 2))
        text = self.run_cli("capacity").stdout
        self.assertIn("2/1", text)

    def test_status_json_capacity_fields(self):
        self.run_cli("capacity")                                                                     # fills the cache
        out = self.run_cli("status", "--brote", "--live")
        self.assertEqual(out.returncode, 0, out.stderr)
        cap = json.loads(out.stdout)["capacity"]
        self.assertEqual((cap["T"], cap["level"]), (1, "critical"))
        (row,) = cap["projects"].values()
        self.assertEqual(set(row), {"used", "slots"})
        plain = json.loads(self.run_cli("status", "--brote").stdout)
        self.assertNotIn("capacity", plain)                                                          # the v2 feed keeps its keys


class PrivacyTest(GateCase):
    def test_a_protected_project_shows_counts_and_ids_only(self):
        secret = os.path.join(self.tmp, "cofre-secreto")
        os.makedirs(secret)
        os.makedirs(os.path.join(secret, self.DATA))
        sl.save_ledger(secret, sl.empty_ledger())
        data = sl.load_ledger(secret)
        sl.add_item(data, "SECRETO-TITULO-XYZ", ["legacy"], title="SECRETO-TITULO-XYZ")
        sl.save_ledger(secret, data)                                                                 # the card is written with it
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": [os.path.realpath(secret)]}, f)
        reg = self.portfolio.load()
        reg["projects"].append(self.portfolio._entry(os.path.realpath(secret)))
        self.portfolio.save(reg)
        for args in (("capacity",), ("capacity", "--json")):
            out = self.run_cli(*args)
            self.assertEqual(out.returncode, 0, out.stderr)
            for needle in ("SECRETO", "cofre-secreto", "secreto"):
                self.assertNotIn(needle, out.stdout + out.stderr)
            self.assertIn("private~", out.stdout)
