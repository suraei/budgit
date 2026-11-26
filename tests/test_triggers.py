"""R-286 task 4: event triggers. pending() turns the portfolio into cards, select() shows at most 3 a day (urgent first,
a parked wave 1 card of 5 a day, backoff when ignored, a 7-day safety net). The CLI and the SessionStart line do the I/O."""
import json
import os
import subprocess
import sys
import time

from helpers import ROOT
from test_flow_data import FlowCase
import studiolib as sl
import triggers

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
DAY = 86400
NOW = 1_800_000_000.0


def proj(slug="budgit", **kw):
    base = {"slug": slug, "path": "/x/" + slug, "private": False, "paused": False, "free": 0, "ready": [], "doing": [], "aged": [],
            "blockers": [], "parked": [], "ready_n": 0, "hits": {}, "last_release": None}
    base.update(kw)
    base["ready_n"] = base["ready_n"] or len(base["ready"])
    return base


def cards_for(projects, state=None, now=NOW):
    return triggers.pending({"projects": projects, "skipped": []}, state if state is not None else {"keys": {}}, now)


def kinds(cards):
    return sorted(c["kind"] for c in cards)


class PendingTest(FlowCase):
    def test_age_card_skips_items_without_started_at(self):
        # Review Focus 1: the reader only ages a doing item that has a known appetite and a doing_since
        data = sl.load_ledger(self.project)
        old = sl.add_item(data, "viejo", ["legacy"])["id"]
        sl.save_ledger(self.project, data)
        self.start(old, "s-flow0001")
        data = sl.load_ledger(self.project)
        item = sl.find_item(data, old)
        item["doing_since"] = "2020-01-01T00:00:00.000Z"
        item.pop("started_at", None)                                                    # a closed-before-this-shipped style item
        sl.save_ledger(self.project, data)
        self.assertEqual(triggers._read_items(self.project, NOW)["aged"], [])           # no appetite: unknown, never aged
        sl.set_appetite(data, old, "small")
        sl.save_ledger(self.project, data)
        self.assertEqual(triggers._read_items(self.project, time.time())["aged"], [old])

    def test_blocker_grouped_by_reason_and_urgent_for_mvp(self):
        blockers = [{"id": "R-2", "reason": "esperando api", "mvp": False, "only_doing": False},
                    {"id": "R-5", "reason": "esperando api", "mvp": False, "only_doing": False},
                    {"id": "R-7", "reason": "otra cosa", "mvp": True, "only_doing": False}]
        cards = [c for c in cards_for([proj(blockers=blockers)]) if c["kind"] == "blocker"]
        self.assertEqual(len(cards), 2)
        grouped = next(c for c in cards if len(c["items"]) == 2)
        self.assertEqual((grouped["items"], grouped["urgent"], grouped["key"]), (["R-2", "R-5"], False, "blocker:budgit:R-2"))
        self.assertTrue(next(c for c in cards if c["items"] == ["R-7"])["urgent"])          # an MVP item is urgent
        self.assertNotIn("api", json.dumps(cards))                                           # the key never carries the reason text

    def test_next_empty_only_without_ready_items(self):
        self.assertEqual(kinds(cards_for([proj(free=1)])), ["next_empty"])
        self.assertEqual(kinds(cards_for([proj(free=1, ready=["R-1"])])), [])
        self.assertEqual(kinds(cards_for([proj(free=0)])), [])

    def test_release_card_carries_parked_review(self):
        parked = [{"id": "R-%d" % n, "at": NOW - 5 * DAY} for n in range(1, 4)]            # young: not due by age
        cards = cards_for([proj(parked=parked, last_release=NOW - 60)])
        self.assertEqual(kinds(cards), ["release"])
        self.assertEqual(cards[0]["items"], ["R-1", "R-2", "R-3"])
        self.assertEqual(cards[0]["default"], "keep_parked")
        state = triggers.mark_shown({"keys": {}}, [cards[0]["key"]], NOW)
        self.assertEqual(kinds(cards_for([proj(parked=parked, last_release=NOW - 60)], state)), [])        # announced once

    def test_parked_wave_five_per_card_one_per_day(self):
        # Review Focus 3: 97 items parked the same day cross 30 days together
        parked = [{"id": "R-%d" % n, "at": NOW - 31 * DAY} for n in range(1, 98)]
        cards = cards_for([proj(parked=parked)])
        (card,) = [c for c in cards if c["kind"] == "parked"]
        self.assertEqual(len(card["items"]), 5)
        two = cards_for([proj("a", parked=parked), proj("b", parked=parked)])
        show, later = triggers.select(two, {"keys": {}}, NOW)
        self.assertEqual(len([c for c in show if c["kind"] == "parked"]), 1)               # one parked card a day
        self.assertEqual(len([c for c in later if c["kind"] == "parked"]), 1)

    def test_parked_default_keep_never_drops(self):
        parked = [{"id": "R-1", "at": NOW - 100 * DAY}]
        for c in cards_for([proj(parked=parked, last_release=NOW - 1)]) + cards_for([proj(parked=parked)]):
            if c["kind"] in ("parked", "release"):
                self.assertEqual((c["default"], c["if_no_answer"]), ("keep_parked", "keep_parked"))
        hit = cards_for([proj(parked=[{"id": "R-9", "at": NOW - DAY}], hits={"R-9": NOW - 10})])      # new input matched a parked item
        self.assertEqual(kinds(hit), ["parked"])

    def test_sweep_after_seven_quiet_days(self):
        ready = proj(ready=["R-1"])
        self.assertEqual(kinds(cards_for([ready], {"keys": {}, "last_card_at": NOW - 6 * DAY})), [])
        self.assertEqual(kinds(cards_for([ready], {"keys": {}, "last_card_at": NOW - 7 * DAY})), ["sweep"])
        self.assertEqual(kinds(cards_for([proj()], {"keys": {}, "last_card_at": NOW - 9 * DAY})), [])   # nothing ready: no digest
        self.assertEqual(kinds(cards_for([ready], {"keys": {}})), [])                         # never initialised: no digest on day one

    def test_budget_drop_is_not_a_card(self):
        import budget
        budget.set_override("critical")
        _state, _w, cards = triggers.refresh(NOW, write=False)
        self.assertEqual(cards, [])


class FixesTest(FlowCase):
    """The verifier's blocking findings: the parked wave advances, junk cards.jsonl lines are tamed, writers are serialised."""
    def test_parked_wave_advances_when_shown(self):
        parked = [{"id": "R-%d" % n, "at": NOW - 31 * DAY} for n in range(1, 13)]
        state, seen = {"keys": {}}, []
        for day in range(3):
            now = NOW + day * DAY + 60
            (card,) = [c for c in cards_for([proj(parked=parked)], state, now) if c["kind"] == "parked"]
            seen += card["items"]
            triggers.mark_shown(state, [card["key"]], now, [card])
        self.assertEqual(len(seen), 12)
        self.assertEqual(len(set(seen)), 12)                                                  # five, five, two: no item twice
        self.assertEqual([c for c in cards_for([proj(parked=parked)], state, NOW + 3 * DAY) if c["kind"] == "parked"], [])
        later = NOW + 60 * DAY                                                                # the 90-day line brings them back
        self.assertEqual(len([c for c in cards_for([proj(parked=parked)], state, later) if c["kind"] == "parked"]), 1)

    def test_a_new_hit_brings_a_shown_parked_item_back(self):
        item = [{"id": "R-1", "at": NOW - 31 * DAY}]
        state = {"keys": {}}
        (card,) = cards_for([proj(parked=item)], state)
        triggers.mark_shown(state, [card["key"]], NOW, [card])
        self.assertEqual(cards_for([proj(parked=item)], state, NOW + 2 * DAY), [])
        again = cards_for([proj(parked=item, hits={"R-1": NOW + DAY})], state, NOW + 2 * DAY)
        self.assertEqual(kinds(again), ["parked"])

    def test_junk_cards_jsonl_lines_do_not_crash(self):
        import budget
        budget.set_override("normal")
        junk = [{"key": "choice:a:1", "kind": "choice", "project": "p", "items": "R-12", "expires_at": "tomorrow",
                 "created": "now", "urgent": "no", "confidence": "high", "question": 7},
                {"key": "approval:a:2", "kind": "approval", "items": ["R-1", 5], "urgent": True}, "not an object", {"kind": "choice"}]
        with open(os.path.join(self.home, "cards.jsonl"), "w") as f:
            f.write("{broken\n" + "".join(json.dumps(j) + "\n" for j in junk))
        cards = triggers.event_cards({"keys": {}}, NOW)
        by_key = {c["key"]: c for c in cards}
        self.assertEqual(sorted(by_key), ["approval:a:2", "choice:a:1"])
        self.assertEqual((by_key["choice:a:1"]["items"], by_key["choice:a:1"]["urgent"], by_key["choice:a:1"]["expires_at"]), ([], False, None))
        self.assertEqual(by_key["approval:a:2"]["items"], ["R-1"])
        self.assertIsNone(by_key["choice:a:1"]["question"])

    def test_wrong_typed_state_files_read_as_empty(self):
        import capacity
        for name, body in (("triggers.json", '{"keys": [1], "parked_seen": 3, "last_release": "x"}'),
                           ("capacity.json", '{"T": "five", "expedite": "x", "projects": []}')):
            os.makedirs(self.home, exist_ok=True)
            with open(os.path.join(self.home, name), "w") as f:
                f.write(body)
        self.assertEqual(triggers.load_state()["keys"], {})
        self.assertNotIn("T", capacity.load_state())
        self.assertEqual(triggers.select([], triggers.load_state(), NOW), ([], []))

    def test_parallel_shown_calls_all_land(self):
        import portfolio
        portfolio.save({"version": 1, "projects": [portfolio._entry(os.path.realpath(self.project))]})
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        procs = [subprocess.Popen([sys.executable, CLI, "triggers", "shown", "next_empty:p%d" % n, "--project", self.project], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for n in range(8)]
        for p in procs:
            p.wait()
        state = triggers.load_state()
        self.assertEqual(len(state["keys"]), 8)
        self.assertEqual(state["shown_today"], 8)


class SelectTest(FlowCase):
    def five(self):
        return [triggers._card("next_empty", "p%d" % n, [], "wait", 0.6, NOW) for n in range(5)]

    def test_daily_cap_three(self):
        cards = self.five()
        show, later = triggers.select(cards, {"keys": {}}, NOW)
        self.assertEqual((len(show), len(later)), (3, 2))
        state = triggers.mark_shown({"keys": {}}, [c["key"] for c in show], NOW)
        again, _ = triggers.select(cards, state, NOW + 60)
        self.assertEqual(again, [])                                                           # none due yet, the cap is spent
        tomorrow, _ = triggers.select(cards, state, NOW + DAY + 60)
        self.assertEqual(len(tomorrow), 3)
        self.assertEqual({c["key"] for c in later} <= {c["key"] for c in tomorrow}, True)    # the 2 held back come first

    def test_urgent_bypasses_cap_but_counts(self):
        cards = self.five()
        show, _ = triggers.select(cards, {"keys": {}}, NOW)
        state = triggers.mark_shown({"keys": {}}, [c["key"] for c in show], NOW)
        urgent = triggers._card("blocker", "p9", ["R-1"], "keep_going", 0.5, NOW, urgent=True)
        shown, held = triggers.select(cards + [urgent], state, NOW + 60)
        self.assertEqual([c["key"] for c in shown], [urgent["key"]])
        state = triggers.mark_shown(state, [urgent["key"]], NOW + 60)
        self.assertEqual(state["shown_today"], 4)                                             # it counted toward the day

    def test_backoff_halves_frequency(self):
        card = self.five()[0]
        state = {"keys": {}}
        triggers.mark_shown(state, [card["key"]], NOW)
        self.assertEqual(triggers.next_due(state["keys"][card["key"]]), NOW + DAY)
        triggers.mark_shown(state, [card["key"]], NOW + DAY)                                  # ignored: shown again unanswered
        self.assertEqual(state["keys"][card["key"]]["ignored"], 2)
        self.assertEqual(triggers.next_due(state["keys"][card["key"]]), NOW + DAY + 2 * DAY)  # ignored twice: 2 days after the last
        self.assertEqual(triggers.select([card], state, NOW + DAY + DAY)[0], [])
        self.assertEqual(len(triggers.select([card], state, NOW + 3 * DAY + 1)[0]), 1)

    def test_answered_card_is_quiet(self):
        card = self.five()[0]
        state = triggers.mark_answered({"keys": {}}, card["key"], NOW, "I-3")
        self.assertEqual(triggers.select([card], state, NOW + 30 * DAY)[0], [])

    def test_abstain_band_not_shown(self):
        sure = triggers._card("next_empty", "p", [], "wait", 0.9, NOW)
        unsure = triggers._card("parked", "p", ["R-1"], "keep_parked", 0.3, NOW)
        show, later = triggers.select([sure, unsure], {"keys": {}}, NOW)
        self.assertEqual([c["key"] for c in show], [unsure["key"]])                           # the sure no-op is only logged
        self.assertEqual(later, [])
        approval = triggers._card("approval", "p", ["R-1"], "wait", 0.95, NOW, key="approval:abc:1")
        self.assertEqual(len(triggers.select([approval], {"keys": {}}, NOW)[0]), 1)           # an approval never abstains

    def test_destructive_default_never_silent(self):
        # a destructive default (not a no-op) is shown whatever its confidence
        drop = triggers._card("parked", "p", ["R-1"], "drop", 0.99, NOW)
        self.assertEqual(len(triggers.select([drop], {"keys": {}}, NOW)[0]), 1)


class CliCase(FlowCase):
    def setUp(self):
        super().setUp()
        import budget
        import portfolio
        budget.set_override("normal")
        portfolio.save({"version": 1, "projects": [portfolio._entry(os.path.realpath(self.project))]})

    def run_cli(self, *args, project=None):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", project or self.project], env=env, capture_output=True, text=True)

    def ledger_bytes(self):
        with open(os.path.join(self.project, self.DATA, "ledger.json"), "rb") as f:
            return f.read()


class CliTest(CliCase):
    def test_triggers_cli_does_not_write_ledger(self):
        before = self.ledger_bytes()
        for args in (("triggers",), ("triggers", "--json"), ("triggers", "shown", "next_empty:x"),
                     ("triggers", "answered", "next_empty:x", "--input", "I-1")):
            self.run_cli(*args)
        self.assertEqual(self.ledger_bytes(), before)

    def test_cli_lists_then_records_shown_and_answered(self):
        data = sl.load_ledger(self.project)
        data["items"][0]["blocked"] = {"reason": "esperando", "since": sl.now(), "by": None}
        data["items"][0]["phase"] = "mvp"
        sl.save_ledger(self.project, data)
        out = self.run_cli("triggers", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        cards = json.loads(out.stdout)["show"]
        key = next(c["key"] for c in cards if c["kind"] == "blocker")
        self.assertTrue(next(c for c in cards if c["key"] == key)["urgent"])
        self.assertEqual(self.run_cli("triggers", "shown", key).returncode, 0)
        again = json.loads(self.run_cli("triggers", "--json").stdout)["show"]
        self.assertNotIn(key, [c["key"] for c in again])                                      # backing off for a day
        typed = sl.append_inbox(self.project, "ya lo miro", "s-flow0001", "prompt")
        manual = sl.append_inbox(self.project, "lo anota la PM", "s-flow0001", "manual")
        self.assertNotEqual(self.run_cli("triggers", "answered", key, "--input", manual).returncode, 0)
        done = self.run_cli("triggers", "answered", key, "--input", typed)
        self.assertEqual(done.returncode, 0, done.stderr)
        with open(os.path.join(self.home, "triggers.json")) as f:
            self.assertEqual(json.load(f)["keys"][key]["input"], typed)

    def test_text_output_and_no_decision_line(self):
        out = self.run_cli("triggers")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ninguna decisi", out.stdout)

    def test_a_protected_project_card_is_redacted_from_outside(self):
        secret = os.path.join(self.tmp, "cofre-secreto")
        os.makedirs(os.path.join(secret, self.DATA))
        sl.save_ledger(secret, sl.empty_ledger())
        data = sl.load_ledger(secret)
        sl.add_item(data, "SECRETO-TITULO-XYZ", ["legacy"], title="SECRETO-TITULO-XYZ")
        sl.save_ledger(secret, data)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"protected_dirs": [os.path.realpath(secret)]}, f)
        import portfolio
        reg = portfolio.load()
        reg["projects"].append(portfolio._entry(os.path.realpath(secret)))
        portfolio.save(reg)
        with open(os.path.join(self.home, "cards.jsonl"), "w") as f:
            f.write(json.dumps({"key": "choice:abc12345:1", "kind": "choice", "project": next(
                p["id"] for p in portfolio.view()["rows"] if p["protected"]), "items": ["R-1"], "default": "wait",
                "question": "SECRETO-TITULO-XYZ: sigo?", "lane": "abc12345"}) + "\n")
        out = self.run_cli("triggers", "--json")
        text = out.stdout + out.stderr
        self.assertEqual(out.returncode, 0, text)
        for needle in ("SECRETO", "cofre-secreto"):
            self.assertNotIn(needle, text)
        self.assertIn("private~", text)
        self.assertIn("private~", self.run_cli("triggers").stdout)


class SessionStartTest(CliCase):
    def hook(self, project):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_PROJECT_DIR=project)
        p = subprocess.run([sys.executable, HOOK, "session-start"], input=json.dumps({"session_id": "s-flow0001", "cwd": project,
                           "source": "startup"}), env=env, capture_output=True, text=True, cwd=project)
        return p.stdout

    def test_sessionstart_line_only_when_due(self):
        # nothing waits: no triggers line. The R-399 team-rules block legitimately lists a "- decisions:" topic, so the
        # check is on the triggers line itself (its "budgit triggers" pointer), not on the substring "decisi".
        self.assertNotIn("budgit triggers", self.hook(self.project))
        data = sl.load_ledger(self.project)
        data["items"][0]["blocked"] = {"reason": "esperando", "since": sl.now(), "by": None}
        data["items"][0]["phase"] = "mvp"
        sl.save_ledger(self.project, data)
        self.run_cli("triggers")                                                              # refreshes the cache
        out = self.hook(self.project)
        self.assertIn("1 decisi", out)
        self.assertIn("budgit triggers", out)

    def test_the_cache_and_the_hits_key_a_project_by_a_hash_never_by_its_path(self):
        self.run_cli("triggers")
        data = sl.load_ledger(self.project)
        sl.add_item(data, "libreta con paneles", ["legacy"])
        data["items"][-1]["phase"] = "later"
        sl.save_ledger(self.project, data)
        self.run_cli("ledger", "match", "libreta paneles")
        with open(os.path.join(self.home, "triggers.json"), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(os.path.realpath(self.project), raw)
        state = json.loads(raw)
        self.assertEqual(list(state["cache"]["slugs"]), [sl.path_key(self.project)])
        self.assertEqual(list(state["parked_hits"]), [sl.path_key(self.project)])
        self.assertEqual(triggers.world(NOW)["projects"][0]["hits"].keys(), state["parked_hits"][sl.path_key(self.project)].keys())

    def test_line_failure_prints_nothing_extra(self):
        with open(os.path.join(self.home, "triggers.json"), "w") as f:
            f.write("{broken")
        self.assertNotIn("Traceback", self.hook(self.project))
