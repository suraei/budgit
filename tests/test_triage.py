"""R-286 Task 2: merge at triage. `ledger match` finds the items an input may belong to, `ledger link --change --why`
logs a change on an existing item (reopening a done one and staling its verify receipt), and the status JSON says how
much of the last week's input was linked and which doing items keep being reshaped."""
import json
import os
import time

from test_flow_data import FlowCase
import studiolib as sl


def iso(days_ago, at=None):
    t = (at if at is not None else time.time()) - days_ago * 86400
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class TriageCase(FlowCase):
    def setUp(self):
        super().setUp()
        self.items = {}

    def add(self, text, title=None, status="open", phase="", done_days=None, src=None):
        data = sl.load_ledger(self.project)
        it = sl.add_item(data, text, src or ["legacy"], phase=phase, title=title, outcome="You see it work")
        if status in ("done", "verified"):
            it.update(status=status, evidence="ok", done_at=iso(done_days or 0), updated=iso(done_days or 0))
        elif status == "doing":
            it.update(status="doing", doing_by="s-flow0001", doing_since=iso(0))
        sl.save_ledger(self.project, data)
        return it["id"]

    def match(self, text):
        return sl.match_items(sl.load_ledger(self.project), text)

    def ledger_bytes(self):
        with open(os.path.join(self.project, self.DATA, "ledger.json"), "rb") as f:
            return f.read()


class MatchTest(TriageCase):
    def test_match_explicit_id_first(self):
        self.add("libreta con paneles de tareas", title="Libreta paneles")
        target = self.add("algo sin relacion alguna")
        found = self.match("mira %s y la libreta con paneles" % target)
        self.assertEqual(found[0]["id"], target)

    def test_match_accent_and_stopword_fold(self):
        want = self.add("Libreta: añadir un panel de ideas", title="Libreta: añadir panel")
        self.add("cambiar el color del botón rojo", title="Botón rojo")
        found = self.match("anadir la libreta")                       # no accent, with a stopword
        self.assertEqual([m["id"] for m in found][:1], [want])
        self.assertEqual(self.match("AÑADIR LIBRETA")[0]["id"], want)

    def test_a_score_of_zero_is_never_returned(self):
        self.add("libreta con paneles", title="Libreta")
        self.assertEqual(self.match("completamente distinto xyzzy"), [])
        self.assertEqual(self.match("   "), [])

    def test_at_most_three_best_first(self):
        for i in range(5):
            self.add("libreta paneles " + "extra " * i, title="L%d" % i)
        found = self.match("libreta paneles")
        self.assertEqual(len(found), 3)
        self.assertEqual([m["score"] for m in found], sorted((m["score"] for m in found), reverse=True))

    def test_match_excludes_old_done(self):
        old = self.add("libreta con paneles", status="done", done_days=20)
        recent = self.add("libreta con paneles", status="done", done_days=3)
        ids = [m["id"] for m in self.match("libreta paneles")]
        self.assertIn(recent, ids)
        self.assertNotIn(old, ids)

    def test_dropped_items_never_match(self):
        data = sl.load_ledger(self.project)
        it = sl.add_item(data, "libreta con paneles", ["legacy"])
        it["status"] = "dropped"
        sl.save_ledger(self.project, data)
        self.assertEqual(self.match("libreta paneles"), [])

    def test_match_flags_parked_and_records_hit(self):
        parked = self.add("libreta con paneles", phase="later")
        live = self.add("libreta con paneles", phase="mvp")
        found = {m["id"]: m for m in self.match("libreta paneles")}
        self.assertTrue(found[parked]["parked"])
        self.assertFalse(found[live]["parked"])
        out = self.ok("ledger", "match", "libreta paneles")
        self.assertIn(parked, out)
        with open(os.path.join(self.home, "triggers.json")) as f:
            hits = json.load(f)["parked_hits"]
        self.assertEqual(list(next(iter(hits.values()))), [parked])             # only the parked one is a hit

    def test_match_does_not_write_ledger(self):
        self.add("libreta con paneles", phase="later")
        before = self.ledger_bytes()
        self.ok("ledger", "match", "libreta paneles")
        self.ok("ledger", "match", "libreta paneles", "--json")
        self.assertEqual(self.ledger_bytes(), before)

    def test_cli_no_match_says_so_and_input_flag_reads_the_inbox(self):
        out = self.ok("ledger", "match", "nada de nada")
        self.assertTrue("No matches" in out or "Sin coincidencias" in out, out)
        want = self.add("libreta con paneles", title="Libreta")
        inbox_id = sl.append_inbox(self.project, "quiero ver la libreta con paneles", "s-flow0001", "manual")
        self.assertIn(want, self.ok("ledger", "match", "--input", inbox_id))
        r = self.cli("ledger", "match")
        self.assertNotEqual(r.returncode, 0)                                     # text or --input is required


class LinkChangeTest(TriageCase):
    def link_change(self, item_id, change="ahora con filtros", why="lo pidio ella", src=("I-9",)):
        data = sl.load_ledger(self.project)
        out = sl.link_change(self.project, data, item_id, list(src), change, why, by="I-9")
        sl.save_ledger(self.project, data)
        return out

    def test_link_change_logs_entry(self):
        item = self.add("libreta con paneles")
        self.link_change(item)
        it = self.item(item)
        self.assertIn("I-9", it["src"])
        entry = it["changes"][0]
        self.assertEqual((entry["what"], entry["why"], entry["by"], entry["src"]), ("ahora con filtros", "lo pidio ella", "I-9", ["I-9"]))
        self.assertRegex(entry["at"], r"^\d{4}-\d\d-\d\dT")

    def test_link_without_change_keeps_todays_behaviour(self):
        item = self.add("libreta con paneles")
        self.ok("ledger", "link", item, "--src", "I-4")
        it = self.item(item)
        self.assertIn("I-4", it["src"])
        self.assertNotIn("changes", it)

    def test_link_change_reopens_done_and_stales_receipt(self):
        item = self.add("libreta con paneles", status="done", done_days=2)
        vdir = os.path.join(self.project, self.DATA, "verify")
        os.makedirs(vdir, exist_ok=True)
        report = os.path.join(vdir, item + ".md")
        with open(report, "w") as f:
            f.write("PASS\n")
        out = self.link_change(item)
        self.assertTrue(out["reopened"])
        self.assertEqual(self.item(item)["status"], "open")
        self.assertFalse(os.path.exists(report))
        stale = [n for n in os.listdir(vdir) if n.startswith(item + ".stale-")]
        self.assertEqual(len(stale), 1)
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):                                  # the old PASS no longer verifies it
            sl.set_status(self.project, data, item, "verified")

    def test_a_second_reopen_the_same_day_keeps_both_receipts(self):
        item = self.add("libreta con paneles", status="done", done_days=2)
        vdir = os.path.join(self.project, self.DATA, "verify")
        os.makedirs(vdir, exist_ok=True)
        for _ in range(2):
            with open(os.path.join(vdir, item + ".md"), "w") as f:
                f.write("PASS\n")
            data = sl.load_ledger(self.project)
            sl.set_status(self.project, data, item, "done", evidence="again")
            sl.save_ledger(self.project, data)
            self.link_change(item)
        self.assertEqual(len([n for n in os.listdir(vdir) if ".stale-" in n]), 2)

    def test_link_change_reports_doing_owner(self):
        item = self.add("libreta con paneles", status="doing")
        out = self.link_change(item)
        self.assertEqual((out["owner"], out["reopened"]), ("s-flow0001", False))
        self.assertIn(out["claim"], ("live", "idle"))
        said = self.ok("ledger", "link", item, "--src", "I-5", "--change", "mas rapido", "--why", "pedido")
        self.assertIn("s-flow00", said)                                         # the owner line names the session

    def test_change_requires_why_cli(self):
        item = self.add("libreta con paneles")
        r = self.cli("ledger", "link", item, "--src", "I-5", "--change", "mas rapido")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("changes", self.item(item))
        r = self.cli("ledger", "link", item, "--src", "I-5", "--why", "solo el porque")
        self.assertNotEqual(r.returncode, 0)

    def test_a_dropped_item_is_not_changed(self):
        data = sl.load_ledger(self.project)
        it = sl.add_item(data, "libreta", ["legacy"])
        it["status"] = "dropped"
        sl.save_ledger(self.project, data)
        with self.assertRaises(sl.StudioError):
            self.link_change(it["id"])

    def test_a_bad_changes_field_fails_validation(self):
        data = sl.load_ledger(self.project)
        data["items"][0]["changes"] = "no es una lista"
        with self.assertRaises(sl.StudioError):
            sl.validate_ledger(data)


class StatsTest(TriageCase):
    def add_input(self, text, days_ago=0, source="prompt"):
        sid = sl.append_inbox(self.project, text, "s-flow0001", source)
        # make the stamp what the test wants: rewrite the inbox line
        path = os.path.join(self.project, self.DATA, "inbox.jsonl")
        rows = [json.loads(l) for l in open(path) if l.strip()]
        for r in rows:
            if r["id"] == sid:
                r["ts"] = iso(days_ago)
        with open(path, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        return sid

    def test_link_share_and_reshape_in_status_json(self):
        first = self.add_input("quiero una libreta nueva", days_ago=3)
        item = self.add("libreta nueva", src=[first])
        data = sl.load_ledger(self.project)
        sl.find_item(data, item)["created"] = iso(3)
        sl.save_ledger(self.project, data)
        second = self.add_input("la libreta debe tener filtros", days_ago=1)
        self.link_change_for(item, second)
        self.add_input("input viejo de hace dos semanas", days_ago=14)                   # outside the 7 days
        self.add_input("acto manual de la PM", days_ago=1, source="manual")              # not counted
        stats = sl.triage_stats(self.project, sl.load_ledger(self.project))
        self.assertEqual(stats["link_share_7d"], 0.5)                                    # one new, one linked
        self.assertEqual(stats["reshape"], [])
        for n in range(3):                                                               # three changes on a doing item
            self.link_change_for(item, "I-9%d" % n)
        self.start(item, "s-flow0001")
        data = sl.load_ledger(self.project)
        for c in sl.find_item(data, item)["changes"]:
            c["at"] = iso(0, time.time() + 60)
        sl.save_ledger(self.project, data)
        self.assertEqual(sl.triage_stats(self.project, sl.load_ledger(self.project))["reshape"], [item])
        out = self.cli("status", "--brote", "--live")
        self.assertEqual(out.returncode, 0, out.stderr)
        if out.stdout.strip():
            self.assertIn("triage", json.loads(out.stdout))                              # v3 only: the plain v2 feed keeps its keys
        plain = self.cli("status", "--brote")
        if plain.stdout.strip():
            self.assertNotIn("triage", json.loads(plain.stdout))

    def link_change_for(self, item, src):
        data = sl.load_ledger(self.project)
        sl.link_change(self.project, data, item, [src], "cambia", "motivo")
        sl.save_ledger(self.project, data)

    def test_link_share_is_null_with_no_inputs_and_a_doing_item_with_no_started_at_does_not_crash(self):
        stats = sl.triage_stats(self.project, sl.load_ledger(self.project))
        self.assertEqual(stats, {"link_share_7d": None, "reshape": []})
        item = self.add("hecho sin fecha", status="doing")                             # doing, no started_at: every change counts, nothing crashes
        data = sl.load_ledger(self.project)
        data["items"][-1]["changes"] = [{"at": iso(0), "src": [], "what": "a", "why": "b", "by": None}] * 5
        sl.save_ledger(self.project, data)
        self.assertEqual(sl.triage_stats(self.project, sl.load_ledger(self.project))["reshape"], [item])
