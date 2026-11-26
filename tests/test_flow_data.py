"""R-286 Task 1 (flow data) and R-288's ledger CLI: `started_at` (first start, kept), `blocked` {reason, since, by},
`appetite` (unknown when unset, plan fix I4), project priority in the portfolio list (I5), `ledger list --json`,
`ledger show`, `ledger block/unblock/appetite` and an optional `lane_type` on the session pointer."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class FlowCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-flow0001")
        sl.activate(self.project, "s-flow0002")
        data = sl.load_ledger(self.project)
        self.a = sl.add_item(data, "uno", ["legacy"], title="Primera tarea")["id"]
        self.b = sl.add_item(data, "dos", ["legacy"])["id"]
        sl.save_ledger(self.project, data)

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def change(self, fn, *args, **kw):
        data = sl.load_ledger(self.project)
        out = fn(*args, data=data, **kw)
        sl.save_ledger(self.project, data)
        return out

    def start(self, item_id, session):
        data = sl.load_ledger(self.project)
        sl.start_item(self.project, data, item_id, session)
        sl.save_ledger(self.project, data)

    def status(self, item_id, status, **kw):
        data = sl.load_ledger(self.project)
        if status == "done":
            kw.setdefault("outcome", "You see it work")      # R-1136 gate: a close names its outcome (or `done --outcome`)
        sl.set_status(self.project, data, item_id, status, **kw)
        sl.save_ledger(self.project, data)

    def block(self, item_id, reason, by=None):
        data = sl.load_ledger(self.project)
        sl.block_item(self.project, data, item_id, reason, by=by)
        sl.save_ledger(self.project, data)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env,
                              capture_output=True, text=True)

    def ok(self, *args):
        r = self.cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class StartedAtTest(FlowCase):
    def test_started_at_survives_done(self):
        self.start(self.a, "s-flow0001")
        first = self.item(self.a)["started_at"]
        self.assertEqual(first, self.item(self.a)["doing_since"])
        self.status(self.a, "done", evidence="tests pass")
        it = self.item(self.a)
        self.assertEqual(it["started_at"], first)
        self.assertNotIn("doing_since", it)

    def test_started_at_kept_across_release_and_restart(self):
        self.start(self.a, "s-flow0001")
        first = self.item(self.a)["started_at"]
        self.status(self.a, "open")                                 # released: the first start stays
        self.assertEqual(self.item(self.a)["started_at"], first)
        self.start(self.a, "s-flow0002")
        self.assertEqual(self.item(self.a)["started_at"], first)

    def test_no_backfill_on_load(self):
        data = sl.load_ledger(self.project)
        it = sl.find_item(data, self.b)
        it.update(status="doing", doing_by="s-flow0001", doing_since="2026-10-23T10:00:00.000Z")
        sl.save_ledger(self.project, data)
        self.assertNotIn("started_at", self.item(self.b))


class BlockedTest(FlowCase):
    def test_block_needs_reason(self):
        for reason in ("", "   ", None):
            with self.assertRaises(sl.StudioError):
                self.block(self.a, reason)
        self.assertNotIn("blocked", self.item(self.a))

    def test_block_records_reason_since_and_by(self):
        self.block(self.a, "espera a la API", by="I-3")
        b = self.item(self.a)["blocked"]
        self.assertEqual((b["reason"], b["by"]), ("espera a la API", "I-3"))
        self.assertRegex(b["since"], r"^\d{4}-\d\d-\d\dT")
        self.assertEqual(self.item(self.a)["status"], "open")       # a field, not a status

    def test_closed_items_cannot_be_blocked(self):
        self.status(self.a, "done", evidence="ok")
        with self.assertRaises(sl.StudioError):
            self.block(self.a, "tarde")

    def test_blocking_a_blocked_item_keeps_the_first_reason_unless_replace(self):
        self.block(self.a, "espera a la API", by="I-3")
        first = self.item(self.a)["blocked"]
        with self.assertRaises(sl.StudioError) as cm:
            self.block(self.a, "otra cosa")
        self.assertIn("already blocked: espera a la API", str(cm.exception))
        self.assertEqual(self.item(self.a)["blocked"], first)
        r = self.cli("ledger", "block", self.a, "--reason", "otra cosa")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("espera a la API", r.stderr)
        self.ok("ledger", "block", self.a, "--reason", "otra cosa", "--replace")
        self.assertEqual(self.item(self.a)["blocked"]["reason"], "otra cosa")
        with open(os.path.join(sl.studio_dir(self.project), "audit.log"), encoding="utf-8") as f:
            self.assertIn("%s block reason replaced: espera a la API -> otra cosa" % self.a, f.read())

    def test_done_clears_blocked(self):
        self.block(self.a, "espera")
        self.status(self.a, "done", evidence="ok")
        self.assertNotIn("blocked", self.item(self.a))

    def test_unblock_clears_and_audits_the_duration(self):
        self.block(self.a, "espera")
        data = sl.load_ledger(self.project)
        sl.unblock_item(self.project, data, self.a)
        sl.save_ledger(self.project, data)
        self.assertNotIn("blocked", self.item(self.a))
        with open(os.path.join(sl.studio_dir(self.project), "audit.log"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"%s unblocked after \d+ min" % self.a)

    def test_unblock_of_an_unblocked_item_is_refused(self):
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.unblock_item(self.project, data, self.a)

    def test_validate_rejects_bad_blocked_and_appetite(self):
        for bad in ({"blocked": {"reason": ""}}, {"blocked": "x"}, {"blocked": {"since": "2026"}},
                    {"appetite": "huge"}, {"appetite": 3}):
            data = sl.load_ledger(self.project)
            sl.find_item(data, self.a).update(bad)
            with self.assertRaises(sl.StudioError, msg=bad):
                sl.validate_ledger(data)

    def test_the_view_marks_a_blocked_item(self):
        self.block(self.a, "espera a la API")
        with open(os.path.join(sl.studio_dir(self.project), "ledger.md"), encoding="utf-8") as f:
            self.assertIn("open (blocked: espera a la API)", f.read())


class AppetiteTest(FlowCase):
    def test_unset_appetite_is_unknown_not_medium(self):
        self.assertIsNone(sl.appetite_days(self.item(self.a)))

    def test_set_appetite_sizes(self):
        data = sl.load_ledger(self.project)
        for size, days in (("small", 0.5), ("medium", 1), ("large", 7)):
            sl.set_appetite(data, self.a, size)
            self.assertEqual(sl.appetite_days(sl.find_item(data, self.a)), days)
        with self.assertRaises(sl.StudioError):
            sl.set_appetite(data, self.a, "enorme")


class PriorityTest(FlowCase):
    def test_priority_default_normal_and_set_in_the_portfolio_list(self):
        import portfolio
        self.assertEqual(portfolio.project_priority(self.project), "normal")
        portfolio.add(self.project)
        portfolio.set_priority(self.project, "high")
        self.assertEqual(portfolio.project_priority(self.project), "high")
        saved = json.load(open(os.path.join(self.home, "portfolio.json")))
        self.assertEqual(saved["projects"][0]["priority"], "high")
        self.assertNotIn("priority", sl.load_project_config(self.project))      # never in project.json
        with self.assertRaises(sl.StudioError):
            portfolio.set_priority(self.project, "urgente")

    def test_an_unknown_stored_value_reads_as_normal(self):
        import portfolio
        portfolio.add(self.project)
        data = portfolio.load()
        data["projects"][0]["priority"] = "ya"
        portfolio.save(data)
        self.assertEqual(portfolio.project_priority(self.project), "normal")


    def test_cli_priority_shows_and_sets(self):
        env = dict(os.environ, STUDIO_HOME=self.home)
        run = lambda *a: subprocess.run([sys.executable, CLI, "portfolio", *a], env=env, capture_output=True, text=True)
        self.assertEqual(run("add", self.project).returncode, 0)
        self.assertEqual(run("priority", self.project).stdout.strip(), "normal")
        self.assertEqual(run("priority", self.project, "low").returncode, 0)
        self.assertEqual(run("priority", self.project).stdout.strip(), "low")
        self.assertNotEqual(run("priority", self.project, "ya").returncode, 0)


class LedgerCliTest(FlowCase):
    def test_list_json_has_the_agreed_fields(self):
        self.start(self.a, "s-flow0001")
        self.block(self.b, "espera", by="I-2")
        rows = json.loads(self.ok("ledger", "list", "--json"))
        by = {r["id"]: r for r in rows}
        for key in ("id", "status", "phase", "title", "doing_by", "doing_since", "blocked"):
            self.assertIn(key, by[self.a])
        self.assertEqual(by[self.a]["title"], "Primera tarea")
        self.assertEqual(by[self.a]["doing_by"], "s-flow0001")
        self.assertIsNone(by[self.a]["blocked"])
        self.assertEqual(by[self.b]["blocked"]["reason"], "espera")
        self.assertIsNone(by[self.b]["doing_by"])

    def test_list_json_filters(self):
        self.start(self.a, "s-flow0001")
        self.status(self.b, "done", evidence="ok")
        self.assertEqual([r["id"] for r in json.loads(self.ok("ledger", "list", "--json", "--open"))], [self.a])
        self.assertEqual([r["id"] for r in json.loads(self.ok("ledger", "list", "--json", "--status", "done"))],
                         [self.b])
        self.assertNotEqual(self.cli("ledger", "list", "--status", "hecho").returncode, 0)

    def test_list_text_is_unchanged_and_marks_blocked(self):
        self.block(self.a, "espera")
        out = self.ok("ledger", "list")
        self.assertIn("%s [open] dos" % self.b, out)
        self.assertIn("%s [open, blocked] uno" % self.a, out)

    def test_show(self):
        self.block(self.a, "espera a la API", by="I-3")
        it = json.loads(self.ok("ledger", "show", self.a, "--json"))
        self.assertEqual((it["id"], it["text"], it["blocked"]["reason"]), (self.a, "uno", "espera a la API"))
        out = self.ok("ledger", "show", self.a)
        self.assertIn("Primera tarea", out)
        self.assertIn("espera a la API", out)
        self.assertNotEqual(self.cli("ledger", "show", "R-999").returncode, 0)

    def test_cli_block_unblock_appetite(self):
        self.assertNotEqual(self.cli("ledger", "block", self.a, "--reason", " ").returncode, 0)
        self.ok("ledger", "block", self.a, "--reason", "espera", "--by", "I-1")
        self.assertEqual(self.item(self.a)["blocked"]["by"], "I-1")
        self.ok("ledger", "unblock", self.a)
        self.assertNotIn("blocked", self.item(self.a))
        self.ok("ledger", "appetite", self.a, "small")
        self.assertEqual(self.item(self.a)["appetite"], "small")
        self.assertNotEqual(self.cli("ledger", "appetite", self.a, "xl").returncode, 0)


class LaneTypeTest(FlowCase):
    def test_lane_type_is_optional_and_survives_a_touch(self):
        sl.write_pointer("s-flow0003", self.project, lane_type="lane")
        self.assertEqual(sl.read_pointer("s-flow0003")["lane_type"], "lane")
        sl.touch_pointer("s-flow0003", at=10 ** 10)
        self.assertEqual(sl.read_pointer("s-flow0003")["lane_type"], "lane")
        sl.write_pointer("s-flow0004", self.project)
        self.assertNotIn("lane_type", sl.read_pointer("s-flow0004"))
