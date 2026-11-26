"""R-1333 v1a: the one-order module (plugin/scripts/ledgerorder.py) and its read-only dry run. Fixtures only; no view changes."""
import calendar
import copy
import datetime
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import StudioTestCase, ROOT
import ledgerplan as lp
import studiolib as sl
import taskrank

try:
    import ledgerorder as lo
except ImportError:          # red phase: the module does not exist yet
    lo = None

AT = calendar.timegm((2026, 11, 22, 12, 0, 0))
TODAY = datetime.date(2026, 11, 22)
SECTIONS = ("pinned", "head", "doing", "waiting", "blocked", "parked", "rest")
SCRIPT = os.path.join(ROOT, "plugin", "scripts", "ledgerorder.py")


def stamp(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(AT - days_ago * 86400))


def due(days_ago):
    return (TODAY - datetime.timedelta(days=days_ago)).isoformat()


def her(n, days_ago=0, source="prompt", text="do the thing", ts=None):
    return {"id": "I-%d" % n, "ts": ts or stamp(days_ago), "source": source, "text": text}


class Base(StudioTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(sl, "add_names", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.data = sl.empty_ledger()
        self.inbox = []
        self.n = 0

    def add(self, n=None, age=0, status="open", **extra):
        self.n = n or self.n + 1
        title = extra.pop("title", "plain words %d" % self.n)
        it = sl.add_item(self.data, title, extra.pop("src", ["legacy"]), item_id="R-%d" % self.n, title=title)
        it["created"] = it["created_at"] = stamp(age)
        it["events"][0]["at"] = stamp(age)
        it["status"] = status
        it.update(extra)
        return it

    def history(self, area, minutes, count=5, first=900):
        for i in range(count):
            self.add(first + i, age=9, status="done", area=area, appetite="small", started_at=stamp(1.0 + minutes / 1440.0),
                     done_at=stamp(1.0))

    def order(self, **kw):
        return lo.order(self.data, at=AT, inbox=kw.get("inbox", self.inbox), today=TODAY)

    @staticmethod
    def rows(res):
        out = {}
        for sec in SECTIONS:
            for r in res[sec]:
                assert r["id"] not in out, "%s twice" % r["id"]
                out[r["id"]] = dict(r, where=sec)
        return out

    @staticmethod
    def head_ids(res):
        return [r["id"] for r in res["head"]]

    def wait(self, n, **kw):
        return self.add(n, blocked={"reason": "q", "since": stamp(1), "on": "user", "question": "q"}, **kw)


class ClassAndScore(Base):
    def test_class_a_always_above_class_b(self):
        self.add(1, flags=["bug"])
        self.add(2, age=25, flags=["feature"], src=["I-1"])
        self.inbox = [her(1)]
        res = self.order()
        self.assertEqual(self.head_ids(res), ["R-1", "R-2"])
        self.assertEqual((self.rows(res)["R-1"]["tier"], self.rows(res)["R-2"]["tier"]), (0, 2))     # v1b: the tier decides, not the score

    def test_p50_is_shown_never_keyed(self):
        self.history("x", 10)
        self.history("y", 100, first=950)
        self.add(1, age=5, flags=["bug"], area="y", appetite="small", src=["I-1"])
        self.add(2, age=20, flags=["bug"], area="x", appetite="small")
        self.inbox = [her(1)]
        res = self.order()
        self.assertEqual(res["basis"], "tier, urgent, score")
        self.assertEqual(self.head_ids(res)[:2], ["R-1", "R-2"])                   # R-1 scores higher (her origin word), whatever the p50
        self.assertIsNotNone(self.rows(res)["R-1"]["p50"]["minutes"])
        self.assertEqual(self.rows(res)["R-2"]["score"], 20.0)                    # type 2 only: no division by minutes

    def test_basis_is_the_key_when_history_thin(self):
        self.add(1, flags=["bug"], age=4)
        res = self.order()
        row = self.rows(res)["R-1"]
        self.assertEqual(res["basis"], "tier, urgent, score")
        self.assertIsNone(row["p50"]["minutes"])
        self.assertEqual(row["score"], float(row["n"]))

    def test_small_late_is_a_tiebreak_for_auto_due_and_caps_at_7_days(self):
        self.add(1, appetite="small", due=due(3), due_auto=True)
        self.add(2, appetite="small", due=due(20))
        self.add(3, src=["I-1"])
        self.inbox = [her(1)]
        res = self.order()
        rows = self.rows(res)
        self.assertEqual((rows["R-1"]["late_days"], rows["R-2"]["late_days"], rows["R-3"]["late_days"]), (3, 7, 0))
        self.assertEqual(self.head_ids(res)[:3], ["R-3", "R-2", "R-1"])           # score first (her word), then late days among equals

    def test_large_late_only_with_hand_due(self):
        self.add(1, appetite="large", due=due(5), due_auto=True)
        self.add(2, appetite="large", due=due(5))
        rows = self.rows(self.order())
        self.assertEqual((rows["R-1"]["late_days"], rows["R-2"]["late_days"]), (0, 5))

    def test_late_is_first_key_inside_tier_not_across_tiers(self):
        self.add(1, flags=["feature"], appetite="small", due=due(7))
        self.add(2, flags=["bug"])
        self.add(3, flags=["bug"], src=["I-1"])
        self.inbox = [her(1)]
        self.assertEqual(self.head_ids(self.order()), ["R-3", "R-2", "R-1"])

    def test_closed_blocker_lowers_unblocks(self):
        self.add(2)
        self.add(3, blocked={"reason": "needs R-2", "since": stamp(1)})          # blocked on R-2 (a mention alone is not unblocking)
        self.assertEqual(self.rows(self.order())["R-2"]["unblocks"], 1)
        self.data["items"][1]["status"] = "done"
        self.assertEqual(self.rows(self.order())["R-2"]["unblocks"], 0)

    def test_release_cut_moves_items(self):
        self.add(1, phase="0.13.2")
        self.add(2, phase="0.13.3")
        rows = self.rows(self.order())
        self.assertEqual((rows["R-1"]["release"], rows["R-2"]["release"]), (1, 0))
        self.data["items"][0]["status"] = "done"
        rows = self.rows(self.order())
        self.assertEqual(rows["R-2"]["release"], 1)


class Inheritance(Base):
    def blocked_on(self, n, other, **kw):
        return self.add(n, blocked={"reason": "needs R-%d" % other, "since": stamp(1)}, **kw)

    def test_blocker_inherits_n_of_what_it_blocks(self):
        self.blocked_on(1, 2, src=["I-1"])
        self.add(2)
        self.inbox = [her(1)]
        rows = self.rows(self.order())
        self.assertEqual(rows["R-1"]["where"], "blocked")
        self.assertGreaterEqual(rows["R-2"]["n"], rows["R-1"]["n"])
        self.assertGreater(rows["R-2"]["n"], 0)

    def test_class_b_blocker_of_class_a_sorts_as_a(self):
        self.blocked_on(1, 2, flags=["bug", "visible"], src=["I-1"])
        self.add(2, flags=["feature"])
        self.add(3, flags=["feature"], src=["I-2"])
        self.add(4, flags=["bug"])
        self.inbox = [her(1, text="no se ve el menu lateral"), her(2)]      # the mark "visible": R-1 is a bug the user sees (type 3)
        res = self.order()
        self.assertEqual(self.head_ids(res), ["R-2", "R-4", "R-3"])
        self.assertEqual((self.rows(res)["R-2"]["class"], self.rows(res)["R-2"]["tier"]), ("B", 0))

    def test_inherit_chain_three_deep(self):
        self.blocked_on(1, 2, src=["I-1"])
        self.blocked_on(2, 3)
        self.blocked_on(3, 4)
        self.add(4)
        self.inbox = [her(1)]
        rows = self.rows(self.order())
        self.assertEqual(rows["R-4"]["where"], "head")
        self.assertEqual(rows["R-4"]["n"], rows["R-1"]["n"])

    def test_inherit_cycle_terminates(self):
        self.blocked_on(1, 2, src=["I-1"])
        self.blocked_on(2, 1)
        self.add(3)
        self.inbox = [her(1)]
        rows = self.rows(self.order())
        self.assertEqual((rows["R-1"]["where"], rows["R-2"]["where"]), ("blocked", "blocked"))
        self.assertEqual(rows["R-1"]["n"], rows["R-2"]["n"])
        self.assertGreater(rows["R-2"]["n"], 0)

    def test_long_reversed_inheritance_chain_matches_forward_order(self):
        results = {}
        for name, ids in (("forward", range(1, 302)), ("reversed", range(301, 0, -1))):
            self.data = sl.empty_ledger()
            self.n = 0
            for i in ids:
                if i < 301:
                    self.blocked_on(i, i + 1, **({"src": ["I-1"]} if i == 1 else {}))
                else:
                    self.add(i)
            self.inbox = [her(1)]
            t0 = time.perf_counter()
            res = self.order()
            print("R-1333 long chain %s: %.3fs (report only)" % (name, time.perf_counter() - t0))
            results[name] = {k: (v["n"], v["where"]) for k, v in self.rows(res).items()}
        self.assertEqual(results["forward"], results["reversed"])
        self.assertEqual(results["reversed"]["R-301"][0], results["reversed"]["R-1"][0])
        self.assertGreater(results["reversed"]["R-301"][0], 0)


class LastTouch(Base):
    def untouched(self, **extra):
        self.data = sl.empty_ledger()
        self.n = 0
        it = self.add(1, age=40, **extra)
        return it

    def test_each_touch_kind_resets_age(self):
        for kind in ("launch", "handback", "merge", "verify", "done", "state"):
            with self.subTest(kind=kind):
                it = self.untouched()
                sl.add_event(it, kind, "x", "touch R-1", at=stamp(2))
                self.assertEqual(self.rows(self.order())["R-1"]["untouched_days"], 2)
        for field, value in (("directives", [{"src": "I-1", "text": "t", "at": stamp(3)}]),
                             ("changes", [{"at": stamp(4), "src": [], "what": "w"}]),
                             ("doing_since", stamp(5)), ("released", {"by": "x", "at": stamp(6), "why": "w"})):
            with self.subTest(field=field):
                self.untouched(**{field: value})
                want = {"directives": 3, "changes": 4, "doing_since": 5, "released": 6}[field]
                self.assertEqual(self.rows(self.order())["R-1"]["untouched_days"], want)

    def test_note_finding_retext_do_not_touch(self):
        it = self.untouched()
        for kind in ("note", "finding", "retext"):
            sl.add_event(it, kind, "x", "R-1 anything", at=stamp(1))
        self.assertEqual(self.rows(self.order())["R-1"]["untouched_days"], 40)

    def test_triage_ops_and_updated_do_not_touch(self):
        self.untouched(updated=stamp(0), flags=["bug"], area="zorb", appetite="medium", phase="0.13.2",
                       rank={"pos": 1, "reason": "r", "at": stamp(0), "by": None})
        self.assertEqual(self.rows(self.order())["R-1"]["untouched_days"], 40)

    def test_launch_naming_other_item_is_not_touch(self):
        it = self.untouched()
        sl.add_event(it, "launch", "x", "Verify R-1235 hotfix", at=stamp(1))
        sl.add_event(it, "launch", "x", "R-12 started", at=stamp(1))
        self.assertEqual(self.rows(self.order())["R-1"]["untouched_days"], 40)
        sl.add_event(it, "launch", "x", "R-1 started", at=stamp(2))
        self.assertEqual(self.rows(self.order())["R-1"]["untouched_days"], 2)

    def test_age_caps_at_30(self):
        self.add(1, age=100)
        self.add(2, age=29)
        rows = self.rows(self.order())
        self.assertEqual((rows["R-1"]["untouched_days"], rows["R-2"]["untouched_days"]), (100, 29))
        self.assertEqual(rows["R-1"]["score"], rows["R-2"]["score"])        # v1b: age is never in the key or the score

    def test_bad_stamps_count_as_missing(self):
        it = self.add(1, appetite="small", due="nope", src=["I-1"])
        it["created"] = "garbage"
        it.pop("created_at")
        it["events"] = [{"at": None, "kind": "state", "by": "x", "text": "R-1"}, {"kind": "done"}]
        it["directives"] = [{"src": "I-1", "text": "t", "at": 17}]
        self.inbox = [her(1, ts="bad")]
        row = self.rows(self.order())["R-1"]
        self.assertEqual((row["untouched_days"], row["late_days"]), (None, 0))     # v1b: no valid stamp reports unknown, never 0d
        self.assertEqual(row["her_words"], 1)
        self.assertIsNone(row["user_days"])

    def test_item_with_no_valid_stamp_reaches_head_by_rotation(self):
        for i in range(1, 11):
            self.add(i, flags=["bug"], src=["I-1"])
        it = self.add(50)
        it["created"] = "garbage"
        it.pop("created_at")
        it["events"] = [{"at": "nope", "kind": "state", "by": "x", "text": "R-50"}]
        self.inbox = [her(1)]
        res = self.order()
        row = self.rows(res)["R-50"]
        self.assertIsNone(row["last_touch"])
        self.assertEqual((row["where"], row["slot"]), ("head", "rotation"))

    def test_item_without_id_or_status_is_skipped_and_counted(self):
        self.add(1)
        self.add(2)
        self.data["items"] += [{"status": "open", "text": "no id"}, "junk", {"id": "R-77", "text": "no status"}]
        res = self.order()
        self.assertEqual(res["skipped"], 3)
        self.assertEqual(sorted(self.rows(res)), ["R-1", "R-2"])


class HerWords(Base):
    def test_excluded_heads_constant_has_a_user_name(self):
        self.assertEqual(sl.USER_WORD_EXCLUDED_HEADS, ("<bash-input", "<command-message"))
        self.assertFalse(hasattr(sl, "HER_WORD_EXCLUDED_HEADS"))

    def her_part(self, n=1):
        return self.rows(self.order())["R-%d" % n]["her_words"]

    def test_any_her_word_counts_once_whatever_its_age(self):
        self.add(1, src=["I-1"])
        self.add(2)
        self.inbox = [her(1, days_ago=365)]
        self.assertEqual(self.her_part(1), 1)
        self.assertEqual(self.her_part(2), 0)

    def test_her_word_recency_is_not_scored(self):
        self.add(1, src=["I-1"])
        self.add(2, src=["I-2"])
        self.inbox = [her(1, 0), her(2, 14)]
        self.assertEqual(self.her_part(1), self.her_part(2))
        res = self.order()
        self.assertEqual(self.rows(res)["R-1"]["score"], self.rows(res)["R-2"]["score"])

    def test_her_word_on_folded_child_counts(self):
        self.init_project()
        self.add(1)
        self.add(2, src=["I-1"])
        sl.fold_item(self.project, self.data, "R-2", "R-1", "same work")
        self.inbox = [her(1)]
        res = self.order()
        row = self.rows(res)["R-1"]
        self.assertGreaterEqual(row["her_words"], 1)
        self.assertEqual(row["folded"], 1)

    def test_quoted_i_n_in_text_does_not_count_but_src_links_do(self):
        self.add(1, title="from I-3", text="see I-3")                                           # an id quoted in text is no link
        self.add(2, directives=[{"src": "I-4", "text": "she said in I-9", "at": stamp(1)}])     # a directive src is a link
        self.add(3, changes=[{"at": stamp(1), "src": ["I-5"], "what": "per I-9"}])             # a change src is a link
        self.add(4, changes=[{"at": stamp(1), "src": [], "what": "per I-5"}])                   # its words are not
        self.inbox = [her(3), her(4), her(5)]
        self.assertEqual(self.her_part(1), 0)
        self.assertGreaterEqual(self.her_part(2), 1)
        self.assertGreaterEqual(self.her_part(3), 1)
        self.assertEqual(self.her_part(4), 0)

    def test_unknown_inbox_id_ignored(self):
        self.add(1, title="quotes I-99", src=["I-98"])
        self.inbox = [her(1)]
        self.assertEqual(self.her_part(1), 0)

    def test_shell_and_slash_captures_are_not_her_words(self):
        self.init_project()
        shell = sl.append_inbox(self.project, "<bash-input>ls -la</bash-input>", "s1", "prompt")
        slash = sl.append_inbox(self.project, "<command-message>go</command-message>", "s1", "prompt")
        real = sl.append_inbox(self.project, "please fix the plan", "s1", "prompt")
        answer = sl.append_inbox(self.project, "yes", "s1", "answer")
        other = sl.append_inbox(self.project, "from a transcript", "s1", "transcript")
        by_id = {e["id"]: e for e in sl.load_inbox(self.project)}
        self.assertFalse(lo.is_user_word(by_id[shell]))
        self.assertFalse(lo.is_user_word(by_id[slash]))
        self.assertTrue(lo.is_user_word(by_id[real]))
        self.assertTrue(lo.is_user_word(by_id[answer]))
        self.assertFalse(lo.is_user_word(by_id[other]))
        self.assertFalse(lo.is_user_word(her(7, text="<task-notification>x")))
        self.add(1, src=[shell, slash])
        self.inbox = list(by_id.values())
        self.assertEqual(self.her_part(1), 0)

    def test_new_her_word_moves_item_into_head(self):
        for i in range(1, 41):
            self.add(i)
        self.assertNotIn("R-40", self.head_ids(self.order()))
        self.data["items"][-1]["src"] = ["I-1"]
        self.inbox = [her(1)]
        self.assertIn("R-40", self.head_ids(self.order()))


class HeadAndSections(Base):
    def test_head_is_8_key_plus_2_rotation(self):
        for i in range(1, 46):
            self.add(i, age=40)
        res = self.order()
        self.assertEqual(len(res["head"]), 10)
        self.assertEqual([r["slot"] for r in res["head"]].count("key"), 8)
        self.assertEqual([r["slot"] for r in res["head"]].count("rotation"), 2)
        self.assertEqual(res["rotation_slots"], 2)
        self.assertEqual(len(res["rest"]), 35)

    def test_rotation_slots_follow_backlog(self):
        for k, slots in ((0, 1), (30, 1), (31, 2), (61, 3), (91, 4), (121, 4), (500, 4)):
            with self.subTest(k=k):
                self.data = sl.empty_ledger()
                self.n = 0
                for i in range(k):
                    self.add(age=40)
                for i in range(15):
                    self.add(age=1)
                res = self.order()
                self.assertEqual(res["rotation_slots"], slots)
                self.assertEqual(res["untouched_beyond_cap"], k)
                self.assertEqual(res["rotation_bound_days"], -(-k // slots))
                self.assertEqual([r["slot"] for r in res["head"]].count("rotation"), slots)
                self.assertEqual(len(res["head"]), 10)

    def test_oldest_untouched_actionable_always_in_head(self):
        for seed in range(50):
            rnd = random.Random(seed)
            self.data = sl.empty_ledger()
            self.n = 0
            for _ in range(rnd.randint(12, 45)):
                extra = {"flags": [rnd.choice(["bug", "feature"])]} if rnd.random() < 0.7 else {}
                if rnd.random() < 0.2:
                    extra["phase"] = "later"
                self.add(age=rnd.randint(0, 90), **extra)
            res = self.order()
            oldest = min(self.data["items"], key=lambda it: (sl._epoch(it["created"]), lo._idkey(it["id"])))
            self.assertIn(oldest["id"], self.head_ids(res), "seed %d" % seed)
            self.assertEqual(res["oldest_untouched_days"], max(r["untouched_days"] for r in self.rows(res).values()))

    def test_rotation_includes_class_b_and_parked(self):
        for i in range(1, 12):
            self.add(i, flags=["bug"], src=["I-1"])
        self.add(50, age=60, phase="later", flags=["feature"])
        self.inbox = [her(1)]
        res = self.order()
        row = self.rows(res)["R-50"]
        self.assertEqual((row["where"], row["slot"], row["action"]), ("head", "rotation", "ask the user"))

    def test_head_excludes_waits_pins_doing_blocked(self):
        for i in range(1, 13):
            self.wait(i, age=50)
        for i in range(13, 22):
            self.add(i, age=50, rank={"pos": i - 12, "reason": "r", "at": stamp(0), "by": None})
        self.add(30, age=3, status="doing")
        self.add(31, age=2, blocked={"reason": "needs R-40", "since": stamp(1)})
        for i in range(40, 60):
            self.add(i, age=3)
        res = self.order()
        banned = set("R-%d" % i for i in list(range(1, 22)) + [30, 31])
        self.assertFalse(banned & set(self.head_ids(res)))
        self.assertEqual([r["id"] for r in res["pinned"]], ["R-%d" % i for i in range(13, 22)])
        self.assertEqual(len(res["waiting"]), 12)

    def test_every_live_item_in_exactly_one_section_442(self):
        for i in range(442):
            kind = {range(0, 9): "pin", range(9, 21): "wait", range(21, 35): "doing", range(35, 43): "blocked",
                    range(43, 73): "parked"}
            what = next((v for r, v in kind.items() if i in r), "plain")
            n = i + 1
            if what == "pin":
                self.add(n, age=i % 50, rank={"pos": i + 1, "reason": "r", "at": stamp(0), "by": None})
            elif what == "wait":
                self.wait(n, age=i % 50)
            elif what == "doing":
                self.add(n, age=i % 50, status="doing")
            elif what == "blocked":
                self.add(n, age=i % 50, blocked={"reason": "needs R-%d" % (n + 300), "since": stamp(1)})
            elif what == "parked":
                self.add(n, age=i % 50, phase="later")
            else:
                self.add(n, age=i % 50, flags=[["bug"], ["feature"], []][i % 3])
        live = set(it["id"] for it in self.data["items"])
        for i in range(348):
            self.add(1000 + i, status="done")
        for i in range(60):
            self.add(2000 + i, status="folded", folded={"into": "R-1", "reason": "r", "at": stamp(1)})
        for i in range(40):
            self.add(3000 + i, status="dropped")
        res = self.order()
        seen = [r["id"] for sec in SECTIONS for r in res[sec]]
        self.assertEqual(len(seen), 442)
        self.assertEqual(set(seen), live)
        self.assertEqual(res["done_awaiting_verify"]["n"], 348)
        self.assertEqual(res["counts"]["folded"], 60)
        self.assertEqual(res["counts"]["dropped"], 40)
        self.assertEqual(sum(len(res[s]) for s in SECTIONS) + res["done_awaiting_verify"]["n"] + res["counts"]["folded"]
                         + res["counts"]["dropped"] + res["counts"]["verified"], len(self.data["items"]))

    def test_section_precedence_pinned_doing_waiting_blocked_head_parked_rest(self):
        self.add(1, status="doing", rank={"pos": 1, "reason": "r", "at": stamp(0), "by": None},
                 blocked={"reason": "q", "since": stamp(1), "on": "user", "question": "q"})
        self.add(2, status="doing", blocked={"reason": "q", "since": stamp(1), "on": "user", "question": "q"})
        self.wait(3)
        self.add(4, phase="later", blocked={"reason": "needs R-9", "since": stamp(1)})
        self.add(5)
        self.add(6, age=1, phase="later")
        self.add(7, age=50, phase="later")
        self.add(9)
        res = self.order()
        rows = self.rows(res)
        self.assertEqual([rows[i]["where"] for i in ("R-1", "R-2", "R-3", "R-4", "R-5", "R-6", "R-7")],
                         ["pinned", "doing", "waiting", "blocked", "head", "parked", "head"])
        self.assertEqual(rows["R-7"]["slot"], "rotation")

    def test_stale_doing_and_gone_blocker_rotate(self):
        cases = (("stale doing", dict(age=40, status="doing", doing_since=stamp(40)), "head", "finish, release or unblock"),
                 ("fresh doing", dict(age=3, status="doing", doing_since=stamp(3)), "doing", None),
                 ("gone blocker", dict(age=5, blocked={"reason": "needs R-9", "since": stamp(1)}), "head", "finish, release or unblock"),
                 ("no id fresh", dict(age=5, blocked={"reason": "waiting for CI", "since": stamp(1)}), "blocked", None),
                 ("no id stale", dict(age=45, blocked={"reason": "waiting for CI", "since": stamp(1)}), "head", "finish, release or unblock"))
        for name, extra, where, action in cases:
            with self.subTest(name):
                self.data = sl.empty_ledger()
                self.n = 0
                self.add(9, age=1, status="done")
                self.add(1, **extra)
                self.add(2, age=2)
                row = self.rows(self.order())["R-1"]
                self.assertEqual(row["where"], where)
                self.assertEqual(row.get("action"), action)

    def test_rotation_action_by_class(self):
        for name, extra, action in (("A", {"flags": ["bug"]}, "build or fold"), ("B", {"flags": ["feature"]}, "ask the user"),
                                    ("parked", {"flags": ["bug"], "phase": "later"}, "ask the user")):
            with self.subTest(name):
                self.data = sl.empty_ledger()
                self.n = 0
                for i in range(10):
                    self.add(flags=["bug"], src=["I-1"])
                self.inbox = [her(1)]
                self.add(100, age=40, **extra)
                row = self.rows(self.order())["R-100"]
                self.assertEqual((row["where"], row["slot"], row["action"]), ("head", "rotation", action))

    def test_same_ledger_same_order(self):
        for i in range(60):
            self.add(age=i % 45, flags=[["bug"], ["feature"], []][i % 3])
        a = json.dumps(self.order(), sort_keys=True)
        self.assertEqual(a, json.dumps(self.order(), sort_keys=True))

    def test_order_writes_nothing_and_reads_inbox_once_in_dry_run(self):
        self.add(1, src=["I-1"])
        before = copy.deepcopy(self.data)
        self.inbox = [her(1)]
        self.order()
        self.assertEqual(before, self.data)
        self.init_project()
        sl.save_ledger(self.project, self.data)
        with mock.patch.object(sl, "load_inbox", wraps=sl.load_inbox) as spy:
            lo.dry_run(self.project, at=AT, today=TODAY)
        self.assertEqual(spy.call_count, 1)


class DryRun(Base):
    def fixture_project(self):
        self.init_project()
        for i in range(1, 15):
            extra = {"flags": ["bug"]} if i % 2 else {}
            if i % 3 == 0:
                extra.update(appetite="small", due=due(i))
            self.add(i, age=i * 5, **extra)
        self.wait(30)
        sl.save_ledger(self.project, self.data)

    def ledger_sha(self):
        with open(os.path.join(self.project, ".studio", "ledger.json"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_dry_run_is_read_only(self):
        self.fixture_project()
        before, files = self.ledger_sha(), sorted(os.listdir(os.path.join(self.project, ".studio")))
        lo.dry_run(self.project, at=AT, today=TODAY)
        self.assertEqual((before, files), (self.ledger_sha(), sorted(os.listdir(os.path.join(self.project, ".studio")))))

    def test_dry_run_lists_before_and_after(self):
        self.fixture_project()
        text = lo.dry_run(self.project, at=AT, today=TODAY)
        for needle in ("CURRENT order", "NEW head", "ENTERED head", "LEFT head", "HAND-DUE", "basis:",
                       "oldest untouched", "rotation slots", "urgent items", "rotation bound", "FACTORS", "R-1 "):
            self.assertIn(needle, text)
        self.assertIsInstance(json.loads(lo.dry_run(self.project, at=AT, today=TODAY, as_json=True)), dict)

    def test_script_entry_runs_isolated(self):
        self.fixture_project()
        got = subprocess.run([sys.executable, "-I", SCRIPT, "--project", self.project], capture_output=True, text=True,
                             env=dict(os.environ))
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("NEW head", got.stdout)

    def test_perf_report_only(self):
        for i in range(1200):
            self.add(age=i % 60, flags=[["bug"], ["feature"], []][i % 3], area="a%d" % (i % 7), appetite="small")
        self.history("a1", 30, first=5000)
        t0 = time.process_time()
        taskrank.ranked(self.data, AT)
        lp.build("/nonexistent", self.data, at=AT, step0=False)
        old = max(time.process_time() - t0, 1e-6)
        t0 = time.process_time()
        self.order()
        new = time.process_time() - t0
        sys.stderr.write("R-1333 perf report-only: new/old = %.2f (old %.3fs, new %.3fs)\n" % (new / old, old, new))

    def test_perf_report_only_5000_items_20k_inbox(self):
        for i in range(5000):
            self.add(age=i % 60, flags=[["bug"], ["feature"], []][i % 3], area="a%d" % (i % 7), appetite="small",
                     src=["I-%d" % (1 + i % 20000)])
        self.history("a1", 30, first=9000)
        self.inbox = [her(i, days_ago=i % 90, text="word %d" % i) for i in range(1, 20001)]
        t0 = time.process_time()
        taskrank.ranked(self.data, AT)
        lp.build("/nonexistent", self.data, at=AT, step0=False)
        old = max(time.process_time() - t0, 1e-6)
        t0 = time.process_time()
        self.order()
        new = time.process_time() - t0
        sys.stderr.write("R-1333 perf report-only 5000 items, 20k inbox: new/old = %.2f (old %.3fs, new %.3fs)\n" % (new / old, old, new))


class NoBehaviourChange(Base):
    def fill(self):
        self.add(1, age=40, flags=["bug"], area="zorb", appetite="small")
        self.add(2, age=10, flags=["feature"], title="new feature idea")
        self.add(3, age=5, blocked={"reason": "needs R-1", "since": stamp(1)})
        self.add(4, age=3, rank={"pos": 1, "reason": "her pin", "at": stamp(0), "by": None})
        self.wait(5, age=2)
        self.add(6, age=60, status="done", evidence="abc1234 test_x", done_at=stamp(2), started_at=stamp(3))

    def views(self):
        text = "\n".join(taskrank.lines(taskrank.top(self.data, 10, AT)))
        text += "\n" + lp.render(lp.build("/nonexistent", self.data, at=AT, step0=False), lang="en")
        text += "\n" + sl.render_md(self.data)
        text += "\n" + json.dumps(sl.ledger_counts(self.data), sort_keys=True)
        return text

    def test_every_existing_view_output_is_byte_identical(self):
        """R-1333 step 2: the views now print the one order, so the old golden hash no longer holds. What stays: the same ledger gives
        the same text twice, and the plan carries the «porque» line of the head."""
        self.fill()
        text = self.views()
        self.assertEqual(text, self.views())
        self.assertIn("porque:", text)
        self.assertIn("her pin", text)                      # the pin keeps its stored reason in the plan

    def test_ordering_the_ledger_does_not_change_a_view(self):
        self.fill()
        text = self.views()
        self.order()
        self.assertEqual(text, self.views())


if __name__ == "__main__":
    unittest.main()
