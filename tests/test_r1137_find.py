"""R-1137 items 2 and 3: `rules show --grep` and `ledger list` filters / --by-area, through the real CLI."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class FindCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s-find0001")
        data = sl.load_ledger(self.project)
        specs = [("Search guard blocks grep on the ledger", "mvp"), ("Daily stand-up job", ""),
                 ("Slow startup", "mvp"), ("Old release notes", ""), ("Zorbex roadmap", "")]
        for title, phase in specs:
            sl.add_item(data, "body of " + title, ["legacy"], phase=phase, title=title)
        data["items"][1]["status"] = "doing"
        data["items"][2]["changes"] = [{"at": "2026-11-01T00:00:00Z", "src": [], "what": "añadido caché al arranque"}]
        data["items"][3]["status"] = "done"
        sl.save_ledger(self.project, data)
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"protected_dirs": ["/somewhere/Zorbex"]}, f)

    def cli(self, *args):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        r = subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def ids(self, *args):
        got = json.loads(self.cli("ledger", "list", "--open", "--json", *args))
        return [i["id"] for i in (got["items"] if isinstance(got, dict) else got)]      # R-1183: a filtered call returns {"items", "totals"}


class RulesShowTest(FindCase):
    def test_o3_find_the_rule_on_pushing_in_one_call(self):
        out = self.cli("rules", "show", "--grep", "push")
        self.assertIn("permissions:", out)
        self.assertIn("push", out.lower())
        self.assertLess(len(out.encode()), 2048)

    def test_show_prints_body_only_no_header(self):
        out = self.cli("rules", "show", "permissions")
        self.assertNotIn("## ", out)
        self.assertTrue(out.strip().startswith("-"))

    def test_grep_in_one_topic_and_all_topics_with_folding(self):
        one = self.cli("rules", "show", "talking", "--grep", "USUARIO")
        self.assertTrue(one.strip() and not any(x.startswith("talking:") for x in one.splitlines()))      # R-1183: bodies only in one topic
        allt = self.cli("rules", "show", "--grep", "PUSH")
        self.assertEqual(self.cli("rules", "show", "--grep", "push"), allt)
        self.assertGreater(len({x.split(":")[0] for x in self.cli("rules", "show", "--grep", "user").splitlines()}), 2)
        self.assertEqual(len(self.cli("rules", "show", "--grep", "ünïcödé-none").strip().splitlines()), 1)      # R-1183: one no-match line


class LedgerFindTest(FindCase):
    def test_o3_open_items_about_the_search_guard_in_one_call(self):
        out = self.cli("ledger", "list", "--open", "--json", "--text", "search guard")
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["R-1"])
        self.assertLess(len(out.encode()), 2048)

    def test_o3_item_for_the_stand_up_in_one_call(self):
        out = self.cli("ledger", "list", "--open", "--json", "--text", "STAND-UP")
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["R-2"])
        self.assertLess(len(out.encode()), 2048)

    def test_each_filter_alone(self):
        self.assertEqual(self.ids("--area", "security"), ["R-1"])
        self.assertEqual(self.ids("--status", "doing"), ["R-2"])
        self.assertEqual(self.ids("--phase", "mvp"), ["R-1", "R-3"])
        self.assertEqual(self.ids("--text", "ANADIDO CACHE"), ["R-3"])        # change notes, case and accents folded
        self.assertEqual(self.ids("--text", "añadido caché"), ["R-3"])

    def test_filters_combined(self):
        self.assertEqual(self.ids("--phase", "mvp", "--area", "performance"), ["R-3"])
        self.assertEqual(self.ids("--phase", "mvp", "--text", "guard"), ["R-1"])
        self.assertEqual(self.ids("--status", "doing", "--phase", "mvp"), [])

    def test_by_area_counts(self):
        out = self.cli("ledger", "list", "--open", "--by-area")
        lines = out.split("\n")[:-1]
        self.assertEqual(sorted(x.split()[0] for x in lines), ["jobs", "other", "performance", "security"])      # R-1183 text format
        self.assertTrue(all("next 1" in x or "doing 1" in x for x in lines))
        got = json.loads(self.cli("ledger", "list", "--open", "--by-area", "--json"))
        self.assertEqual({a: c["doing"] + c["next"] + c["later"] for a, c in got["by_area"].items()},
                         {"security": 1, "jobs": 1, "performance": 1, "other": 1})      # R-1183: {"by_area", "totals"}

    def test_protected_name_stays_masked_in_json_and_text(self):
        out = self.cli("ledger", "list", "--open", "--json", "--text", "zorbex")
        self.assertNotIn("Zorbex", out)
        self.assertEqual(json.loads(out)["items"][0]["title"], "private")
        plain = self.cli("ledger", "list", "--open", "--text", "zorbex")
        self.assertNotIn("Zorbex", plain)
        self.assertNotIn("zorbex", plain.lower())
