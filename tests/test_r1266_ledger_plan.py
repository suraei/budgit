"""R-1266: `budgit ledger plan`. One shared outcome function (ledgerplan.build); text and json render the same dict."""
import calendar
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import StudioTestCase, ROOT
import i18n
import ledgerplan as lp
import studiolib as sl
import taskrank

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")

NOW = calendar.timegm((2026, 10, 27, 12, 0, 0))


def stamp(minutes_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - minutes_ago * 60))


def item(n, text="plain words", **extra):
    it = {"id": "R-%d" % n, "status": "open", "title": text, "text": text, "created": stamp(0)}
    it.update(extra)
    return it


def done(n, minutes, area="zorb", appetite="small"):
    return {"id": "R-%d" % n, "status": "done", "title": "d", "text": "d", "created": stamp(9000), "area": area,
            "appetite": appetite, "started_at": stamp(minutes + 10), "done_at": stamp(10)}


def history(minutes_list, first=900, **kw):
    return [done(first + i, m, **kw) for i, m in enumerate(minutes_list)]


def build(items, **kw):
    kw.setdefault("step0", False)
    return lp.build("/nonexistent", {"items": items}, at=NOW, lanes=kw.pop("lanes", 2), **kw)


class ScoreAndOrder(unittest.TestCase):
    def test_score_is_n_over_p50_pins_first(self):
        pinned = item(3, "zorb pinned", rank={"pos": 1, "reason": "her pin", "at": stamp(0), "by": None})
        data = [item(1, "zorb bug", flags=["bug"], area="zorb", appetite="small"), item(2, "quux plain",
                area="zorb", appetite="small"), pinned] + history([60] * 5)
        plan = build(data)
        self.assertEqual([r["id"] for r in plan["rows"]], ["R-3", "R-1", "R-2"])
        r1 = [r for r in plan["rows"] if r["id"] == "R-1"][0]
        self.assertEqual(r1["n"], 60 + 0 * 5)
        self.assertAlmostEqual(r1["score"], 60 / 60.0)
        self.assertEqual(r1["p50"], {"minutes": 60, "n": 5, "mark": "own"})
        self.assertEqual(plan["header"]["order_basis"], "N/p50")

    def test_p50_filters_and_global_estimate_mixed_rows(self):
        data = history([10, 20, 30, 40, 50]) + [done(950, -5), done(951, 25 * 60)] + history([100, 200], first=960,
                area="quux", appetite="medium")
        own = lp.p50({"items": data}, "zorb", "small")
        self.assertEqual((own["minutes"], own["n"], own["mark"]), (30, 5, "own"))
        thin = lp.p50({"items": data}, "quux", "medium")
        self.assertEqual((thin["minutes"], thin["n"], thin["mark"]), (40, 2, "global"))
        self.assertEqual(lp.p50({"items": []}, "zorb", "small"), {"minutes": None, "n": 0, "mark": None})
        text = lp.render(build([item(1, area="quux", appetite="medium")] + data), lang="en")
        self.assertIn("global estimate, n=2 own", text)

    def test_blocked_by_cited_and_blocked_on_text(self):
        data = [item(1, "zorb blocked on R-2 later", flags=["bug"]), item(2, "quux base"),
                item(3, "zorb waits", blocked={"reason": "needs R-4 first", "since": stamp(5)}), item(4, "quux four"),
                item(5, "see R-2 for context only"),
                item(6, "zorb asks", blocked={"reason": "q", "since": stamp(5), "on": "user", "question": "q"})]
        plan = build(data)
        reasons = {r["id"]: r["reason"] for r in plan["not_scheduled"]}
        self.assertIn("R-2", reasons["R-1"])
        self.assertIn("R-4", reasons["R-3"])
        self.assertNotIn("R-5", reasons)
        self.assertIn("R-6", [r["id"] for r in plan["class_b"]])
        by_id = {r["id"]: r for r in plan["rows"]}
        self.assertEqual(by_id["R-2"]["n"], 60)             # the blocker takes the N of what it blocks
        self.assertNotIn("R-1", by_id)

    def test_class_a_bug_unknown_after_class_b_no_lane(self):
        data = [item(1, "plain zorb"), item(2, "the 2 h alarm never starts, a broken function"),
                item(3, "zorb waits for your yes before any build"),
                item(4, "quux late", appetite="small", due="2026-10-01")]
        plan = build(data)
        classes = {r["id"]: r["class"] for r in plan["rows"]}
        self.assertEqual(classes, {"R-1": "unknown", "R-2": "A", "R-4": "unknown"})   # due or late is not class
        self.assertEqual(plan["rows"][0]["id"], "R-2")
        self.assertEqual([r["id"] for r in plan["class_b"]], ["R-3"])

    def test_size_or_id_key_refused(self):
        with self.assertRaises(sl.StudioError) as ctx:
            lp.order_key({"id": "R-1", "idx": 0, "appetite": "small"})
        self.assertIn(i18n.t("ledger.plan_refuse_key"), str(ctx.exception))

    def test_json_text_same_numbers_es_en_and_last_line(self):
        data = [item(1, "zorb bug", flags=["bug"], area="zorb", appetite="small"), item(2, "quux plain")] \
            + history([30] * 5)
        plan = build(data)
        json.dumps(plan)                                                        # the same dict is the --json output
        for lang in ("en", "es"):
            text = lp.render(plan, lang=lang)
            for r in plan["rows"]:
                self.assertIn("%.3f" % r["score"], text)
            self.assertEqual(text.splitlines()[-1], i18n.t("ledger.plan_critic_last", lang=lang))
        self.assertEqual(i18n.t("ledger.plan_critic_last", lang="en"), "not shown to you until a plan-critic PASS")
        self.assertIn("built = named test and symbol present, not done", lp.render(plan, lang="en"))
        self.assertIn("sin estimación", lp.render(build([item(1, "quux plain")]), lang="es"))


def make_repo(branch="main", files=None):
    root = tempfile.mkdtemp(prefix="r1266-zorb-")
    git = ["git", "-C", root]
    subprocess.run(git + ["init", "-q", "-b", branch], check=True)
    for rel, body in (files or {}).items():
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w") as f:
            f.write(body)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["-c", "user.name=zorb", "-c", "user.email=quux@example.invalid", "commit", "-q", "-m", "base"],
                   check=True)
    return root


REPO = {"plugin/zorb.py": "def quux_fn():\n    pass\n", "tests/test_quux.py": "def test_quux_works():\n    pass\n"}


class Step0(unittest.TestCase):
    def test_step0_symbol_only_is_partial(self):
        root = make_repo(files=REPO)
        both = item(1, "build `quux_fn`, proven by test_quux_works")
        symbol_only = item(2, "build `quux_fn` and test_not_there")
        none = item(3, "build `zorb_gone`")
        self.assertEqual(lp.default_probe(root, both, 5)["verdict"], "built")
        self.assertEqual(lp.default_probe(root, symbol_only, 5), {"verdict": "partial", "missing": "test"})
        self.assertEqual(lp.default_probe(root, item(4, "build `quux_fn`"), 5)["verdict"], "partial")   # no named test at all
        self.assertEqual(lp.default_probe(root, none, 5)["verdict"], "none")
        plan = lp.build(root, {"items": [both, symbol_only, none]}, at=NOW, lanes=2)
        self.assertEqual([v["id"] for v in plan["verify_list"]], ["R-1"])
        self.assertEqual({r["id"]: r["step0"]["verdict"] for r in plan["rows"]}, {"R-2": "partial", "R-3": "none"})

    def test_step0_argv_safe_and_budget(self):
        bad = item(1, "`-rf` `--flag` `` `%s` `quux\nline` `bad\0tok` `ok_symbol()` test_ok_one R-77" % ("x" * 81))
        tests, syms = lp.step0_tokens(bad)
        self.assertEqual((tests, syms), (["test_ok_one"], ["ok_symbol"]))
        seen = []

        def fake(argv, **kw):
            seen.append((argv, kw))
            return subprocess.CompletedProcess(argv, 1, "", "")
        from unittest import mock
        with mock.patch.object(lp.subprocess, "run", fake):
            lp.default_probe("/nonexistent", bad, 3)
        argv, kw = seen[0]
        self.assertEqual(argv[:2], ["git", "-C"])
        self.assertFalse(kw.get("shell"))
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "-e"], ["test_ok_one", "ok_symbol"])
        self.assertEqual(argv[-4:], ["main", "--", "plugin/", "tests/"])
        self.assertEqual(kw["timeout"], 3)
        with mock.patch.object(lp.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)):
            self.assertEqual(lp.default_probe("/nonexistent", bad, 3)["verdict"], "not_run")
        self.assertEqual(lp.default_probe(make_repo(branch="trunk", files=REPO), item(2, "`quux_fn`"), 5)["verdict"], "unknown")
        plan = lp.build("/nonexistent", {"items": [item(1, "`quux_fn` one"), item(2, "`quux_fn` two")]}, at=NOW, budget_s=0)
        self.assertEqual({r["step0"]["verdict"] for r in plan["rows"]}, {"not_run"})
        self.assertEqual(plan["header"]["step0_stopped"], {"row": 1, "of": 2})
        self.assertIn("step 0 stopped at row 1 of 2", lp.render(plan, lang="en"))

    def test_built_unmerged_stub_goes_to_verify_list(self):
        from unittest import mock
        data = {"items": [item(1, "zorb built"), item(2, "quux open")]}
        seen = []

        def stub(project, items):
            seen.append([it["id"] for it in items])
            return {"R-1"}
        with mock.patch.object(sl, "built_unmerged", stub, create=True):
            plan = lp.build("/nonexistent", data, at=NOW, step0=False)
        self.assertEqual(seen, [["R-1", "R-2"]])
        self.assertEqual([(v["id"], v["reason"]) for v in plan["verify_list"]], [("R-1", "built_unmerged")])
        self.assertEqual([r["id"] for r in plan["rows"]], ["R-2"])
        self.assertIn("built, awaiting merge", lp.render(plan, lang="en"))
        self.assertEqual([r["id"] for r in lp.build("/nonexistent", data, at=NOW, step0=False)["rows"]], ["R-1", "R-2"])

    def test_verify_list_changes_no_status(self):
        data = {"items": [item(1, "zorb `quux_fn` test_quux_works")]}
        before = copy.deepcopy(data)
        answers = iter([{"verdict": "built", "missing": ""}, {"verdict": "none", "missing": ""}])
        plan = lp.build("/nonexistent", data, at=NOW, probe=lambda p, it, t: next(answers))
        self.assertEqual(data, before)
        self.assertEqual(plan["rows"], [])
        self.assertEqual([v["id"] for v in plan["verify_list"]], ["R-1"])
        self.assertNotRegex(lp.render(plan, lang="en").replace("not done", ""), r"\bdone\b")
        again = lp.build("/nonexistent", data, at=NOW, probe=lambda p, it, t: next(answers))     # the verifier said not built
        self.assertEqual([r["id"] for r in again["rows"]], ["R-1"])
        self.assertEqual(again["verify_list"], [])
        self.assertEqual(data, before)


class SpanishWords(unittest.TestCase):
    """The class, wait and blocker keywords of every language live in the i18n catalog; Spanish titles read like English ones."""

    def test_spanish_bug_wait_and_blocker_words(self):
        data = [item(1, "plain zorb"), item(2, "la alarma de 2 h nunca arranca, función rota"),
                item(3, "zorb espera su sí antes de construir"), item(4, "quux fallo de rendimiento, bloqueado por R-1"),
                item(5, "zorb necesita R-1 antes")]
        plan = build(data)
        self.assertEqual([r["id"] for r in plan["class_b"]], ["R-3"])
        reasons = {r["id"]: r["blocked_by"] for r in plan["not_scheduled"]}
        self.assertEqual(reasons, {"R-4": ["R-1"], "R-5": ["R-1"]})
        classes = {r["id"]: r["class"] for r in plan["rows"]}
        self.assertEqual(classes, {"R-1": "unknown", "R-2": "A"})
        self.assertEqual(lp.item_class(item(6, "quux regresión tras el cambio")), "A")
        self.assertEqual([r["id"] for r in build([item(7, "zorb necesita tu sí antes")])["class_b"]], ["R-7"])


class ClassWords(unittest.TestCase):
    """The class of a row: A (bug, slow, failing), B (new function, idea, docs: needs the user's yes), unknown (no signal)."""

    def test_english_wait_words_without_owner_voice(self):
        data = [item(1, "plain zorb"), item(2, "quux waits for your yes first"), item(3, "zorb needs your ok to start")]
        self.assertEqual([r["id"] for r in build(data)["class_b"]], ["R-2", "R-3"])

    def test_class_a_performance_and_failure_words_in_both_languages(self):
        for text in ("quux suite 52 min: serial run", "quux suite por debajo de 5 min", "zorb glob ejecutan en la guardia",
                     "quux falso aviso por nombre", "zorb no aparece a nadie", "quux sigue preguntando", "zorb under 5 KB",
                     "quux timeout in the runner", "zorb leak of a name", "quux fuga de nombres", "zorb refuses a sha"):
            self.assertEqual(lp.item_class(item(1, text)), "A", text)

    def test_stale_alone_is_no_class_a_signal(self):
        self.assertEqual(lp.item_class(item(1, "stale zorb report")), "unknown")

    def test_class_b_new_function_idea_docs_and_unknown_only_without_signal(self):
        for text in ("zorb new report", "quux añadir un informe", "zorb idea: docs", "quux diseño nuevo", "zorb investigar mejora visual"):
            self.assertEqual(lp.item_class(item(1, text)), "B", text)
        self.assertEqual(lp.item_class(item(1, "plain zorb")), "unknown")
        for text in ("guard zorb quux", "reglas de la guardia zorb", "hooks and tests of quux", "pruebas del carril quux"):
            self.assertEqual(lp.item_class(item(1, text)), "unknown", text)                    # a topic noun alone is no signal
        self.assertEqual(lp.item_class(dict(item(2, "plain"), text="a new idea")), "B")          # the text field is read
        self.assertEqual(lp.item_class(item(3, "plain", area="research")), "B")                  # the area field is read

    def test_class_b_row_never_ranks_above_class_a_row(self):
        data = [item(1, "zorb new report", appetite="small", due="2026-10-01"), item(2, "quux broken function"),
                item(3, "zorb plain"), item(4, "quux idea for docs")]
        data += [{"id": "R-%d" % i, "status": "open", "title": "zorb new thing", "text": "x", "created": stamp(0)} for i in range(5, 9)]
        rows = build(data)["rows"]
        classes = [r["class"] for r in rows]
        self.assertEqual(classes[0], "A")
        self.assertIn("B", classes)
        self.assertNotIn("A", classes[classes.index("B"):])
        self.assertIn("[needs your yes]", lp.render(build(data), lang="en"))


class Lanes(unittest.TestCase):
    def test_lane_cap_none_uses_flag_or_two(self):
        go = {"verdict": "go", "lane_cap": None}
        self.assertEqual(lp.lanes_from_admit(go, None), (2, None))
        self.assertEqual(lp.lanes_from_admit(go, 3), (3, None))
        self.assertEqual(lp.lanes_from_admit({"verdict": "go", "lane_cap": 1}, 3), (1, None))     # a number caps the lanes
        self.assertEqual(lp.lanes_from_admit({"verdict": "go", "lane_cap": 1}, None), (1, None))
        self.assertEqual(lp.lanes_from_admit({"verdict": "go", "lane_cap": 3}, None), (2, None))
        self.assertEqual(lp.lanes_from_admit({"verdict": "refuse", "lane_cap": None, "reason": "busy zorb"}, 3), (0, "busy zorb"))
        self.assertEqual(lp.lanes_from_admit(None, None), (2, None))                              # no answer: no admit cap

    def test_batches_by_area_unknown_alone_overlap_marked(self):
        data = [item(n, "plain %d" % n, area="zorb") for n in range(1, 8)] + [item(8, "quux a", area="quux"), item(9, "quux b", area="quux"),
                item(10, "no area one"), item(11, "no area two")]
        plan = build(data, lanes=2)
        batches = {b["id"]: b for lane in plan["lanes"] for b in lane["batches"]}
        self.assertTrue(all(len(b["rows"]) <= 6 for b in batches.values()))
        zorb = [b for b in batches.values() if b["area"] == "zorb"]
        self.assertEqual(sorted(len(b["rows"]) for b in zorb), [1, 6])
        lane_of = {b["id"]: lane["lane"] for lane in plan["lanes"] for b in lane["batches"]}
        self.assertEqual(len({lane_of[b["id"]] for b in zorb}), 1)                               # same area: one lane, one after the other
        unknown = [b for b in batches.values() if b["area"] is None]
        self.assertEqual([len(b["rows"]) for b in unknown], [1, 1])                              # never merged
        cells = {o["batch"]: o for o in plan["overlap"]}
        self.assertTrue(all(cells[b["id"]]["unknown"] for b in unknown))
        self.assertIn("PM runs an overlap check before dispatch", lp.render(plan, lang="en"))
        self.assertEqual(build(data, lanes=0)["lanes"], [])
        self.assertEqual(len(build(data, lanes=0)["rows"]), 11)
        blocked = build([item(1, "zorb blocked on R-2"), item(2, "quux base")])
        self.assertIn("bloqueada por R-2", lp.render(blocked, lang="es"))


class ClassField(StudioTestCase):
    """The class is set where items are born: flags bug and feature, `ledger add --class`, flags before words."""

    def run_cli(self, *a):
        return subprocess.run([sys.executable, CLI] + list(a), cwd=self.project, capture_output=True, text=True)

    def add(self, text, *extra):
        return self.run_cli("ledger", "add", "--text", text, "--phase", "mvp", "--appetite", "small", "--area", "zorb", *extra)

    def test_bug_and_feature_flags_exclude_each_other(self):
        data = {"items": [item(1)]}
        taskrank.flag(data, "R-1", "bug")
        self.assertEqual(data["items"][0]["flags"], ["bug"])
        taskrank.flag(data, "R-1", "feature")
        self.assertEqual(data["items"][0]["flags"], ["feature"])          # setting one clears the other
        taskrank.flag(data, "R-1", "bug")
        self.assertEqual(data["items"][0]["flags"], ["bug"])
        taskrank.flag(data, "R-1", "bug", on=False)
        self.assertNotIn("flags", data["items"][0])
        taskrank.flag(data, "R-1", "feature")
        taskrank.flag(data, "R-1", "feature", on=False)
        self.assertNotIn("flags", data["items"][0])

    def test_item_class_reads_flags_before_words(self):
        self.assertEqual(lp.item_class(item(1, "zorb broken function", flags=["feature"])), "B")
        self.assertEqual(lp.item_class(item(2, "zorb new report", flags=["bug"])), "A")
        self.assertEqual(lp.item_class(item(3, "plain zorb", flags=["feature"])), "B")
        self.assertEqual(lp.item_class(item(4, "zorb broken function")), "A")                    # no flag: the words

    def test_add_class_stores_the_flag_and_help_shows_the_choice(self):
        self.init_project()
        self.assertEqual(self.add("plain zorb", "--class", "feature").returncode, 0)
        self.assertEqual(self.add("plain quux", "--class", "bug").returncode, 0)
        self.assertEqual(self.run_cli("ledger", "add", "--class", "other", "--text", "x").returncode, 2)
        data = sl.load_ledger(self.project)
        self.assertEqual([it.get("flags") for it in data["items"]], [["feature"], ["bug"]])
        self.assertIn("--class{bug,feature}", self.run_cli("ledger", "add", "--help").stdout.replace(" ", ""))
        self.assertIn("feature", self.run_cli("ledger", "flag", "--help").stdout)
        self.assertEqual(self.run_cli("ledger", "flag", "R-1", "bug").returncode, 0)
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["flags"], ["bug"])

    def test_add_without_class_warns_only_when_words_give_none(self):
        self.init_project()
        warn = i18n.t("ledger.class_unknown")
        out = self.add("plain zorb")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(warn, out.stdout)
        self.assertNotIn("flags", sl.load_ledger(self.project)["items"][0])
        out = self.add("zorb broken function")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn(warn, out.stdout)
        self.assertNotIn(warn, self.add("plain quux", "--class", "bug").stdout)
        self.assertIn("class unknown", i18n.t("ledger.class_unknown", lang="en"))
        self.assertIn("clase", i18n.t("ledger.class_unknown", lang="es"))


if __name__ == "__main__":
    unittest.main()
