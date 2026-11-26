import copy, json, os, re, unittest

from helpers import ROOT
import skillselect as sel

REGISTRY_PATH = os.path.join(ROOT, "skills", "budgit-kickoff", "reference", "skills-registry.json")
KICKOFF = os.path.join(ROOT, "skills", "budgit-kickoff")


def row(fit, check=0, receipt=0, **kw):
    out = {"fit": fit, "check": check, "receipt": receipt}
    out.update(kw)
    return out


def skill(id, fits, role=("author",), cost=0, verdict="PASS", agents=0, **kw):
    e = {"id": id, "kind": "skill", "status": "installed", "enabled": True, "read": "full", "role": list(role),
         "security": {"verdict": verdict}, "cost": {"class": cost, "agents": agents}, "fits": fits, "not_for": [],
         "requires": {}, "fallback": ["lens:design.md"], "load": "full"}
    e.update(kw)
    return e


def fixture():
    return {
        "version": "test.1",
        "surfaces": {"app-ui": {"generic_ok": True}, "pixel": {"generic_ok": False}, "terminal": {"generic_ok": False}},
        "jobs": {
            "design/build": {"role": "author", "continuity": True, "bakeoff": True, "checks": ["design/critique", "design/lint"],
                            "policy_kind": "app-screen"},
            "design/critique": {"role": "checker"}, "design/lint": {"role": "instrument"},
            "writing/prose": {"role": "author"},
        },
        "budget_policy": {
            "levels": {"ample": {"B": 0, "agent_cap": 4, "checkers": 2, "debate": True},
                       "normal": {"B": 1, "agent_cap": 3, "checkers": 1, "debate": False},
                       "tight": {"B": 2, "agent_cap": 2, "checkers": 0, "debate": False},
                       "critical": {"B": 3, "agent_cap": 0, "checkers": 0, "debate": False}},
            "matrix": [{"kind": "app-screen", "cells": {"ample": {"text": "debate on pillar", "needs_d39": True},
                                                        "normal": {"text": "author plus one checker", "needs_d39": False}}}],
            "provisional_defaults": {"design/app-ui": {"routine": "taste", "pillar": "imp"}},
        },
        "gaps": {"surface:pixel": {"rules": ["rule/pixel-art-1x"]}, "area:design": {"lens": "lens:design.md"}},
        "entries": [
            # two authors tied on evidence, one cheap (taste) and one costly (imp)
            skill("imp", {"design/build": row(3, 1, 1), "design/critique": row(3, 1, 1, agents=2), "design/lint": row(3, 1, 1, cost=3)},
                  role=("author", "checker", "instrument"), cost=3, verdict="PASS-WITH-CONDITIONS",
                  flags={"binary_unaudited": True}, requires={"binary": True}, not_for=["terminal", "pixel"]),
            skill("taste", {"design/build": row(3, 1, 1), "design/critique": row(2, 0, 1), "design/lint": row(3, 1, 1)},
                  role=("author", "checker", "instrument"), cost=2, verdict="PASS-WITH-CONDITIONS", requires={"node": True},
                  not_for=["terminal", "pixel"]),
            skill("template", {"design/critique": row(2)}, role=("checker",), cost=0),
            skill("a11y", {"design/critique": row(1)}, role=("checker",), cost=0),
        ],
    }


class SelectorTest(unittest.TestCase):
    def setUp(self):
        self.reg = fixture()
        self.env = {"node": "v22", "binary_allowed": True}

    def pick(self, job, **traits):
        return sel.select(self.reg, job, traits, self.env)

    def test_score_formula_matches_the_plan_numbers(self):
        # plan 6: a routine critique under a tight budget scores 4 for the costly skill and 6 for the template
        r = self.pick("design/critique", stakes="routine", budget="tight")
        self.assertEqual(r["scores"]["imp"], 4)
        self.assertEqual(r["scores"]["template"], 6)
        self.assertEqual(r["primary"], "template")
        # the same critique on a pillar screen with a normal budget: 10 and 6
        r = self.pick("design/critique", stakes="pillar", budget="normal")
        self.assertEqual((r["scores"]["imp"], r["scores"]["template"]), (10, 6))
        self.assertEqual(r["primary"], "imp")

    def test_budget_level_sets_B_and_ample_ignores_cost(self):
        for level, b in (("ample", 0), ("normal", 1), ("tight", 2), ("critical", 3)):
            self.assertEqual(self.pick("design/critique", budget=level)["B"], b)
        r = self.pick("design/critique", stakes="routine", budget="ample")
        self.assertEqual(r["scores"]["imp"], 3 * 3 + 3 - 0 - 2)

    def test_stakes_pillar_doubles_the_check_terms(self):
        self.assertEqual(self.pick("design/critique", stakes="pillar")["S"], 2)
        self.assertEqual(self.pick("design/critique")["S"], 1)

    def test_fit_below_two_is_never_primary(self):
        r = self.pick("design/critique", budget="normal")
        self.assertNotIn("a11y", r["scores"])
        self.assertNotEqual(r["primary"], "a11y")

    def test_every_hard_filter_lists_its_reason(self):
        cases = [
            ("disabled", lambda e: e.update(enabled=False), {}),
            ("hold", lambda e: e["security"].update(verdict="HOLD"), {}),
            ("status:watch", lambda e: e.update(status="watch"), {}),
            ("status:rejected", lambda e: e.update(status="rejected"), {}),
            ("update_pending", lambda e: e.update(upstream={"state": "update-pending", "open_condition": "review"}), {}),
            ("requires:binary", lambda e: None, {"binary_allowed": False}),
            ("requires:node", None, {"node": None}),
            ("drifted", lambda e: None, {"state": {"imp": "drifted"}}),
            ("missing", lambda e: None, {"state": {"imp": "missing"}}),
            ("project_rule:no_claude_footer", lambda e: e.update(conflicts_rules=["no_claude_footer"]), {"project_rules": ["no_claude_footer"]}),
            ("requires:codex", lambda e: e["requires"].update(codex=True), {"codex": False}),
            ("requires:network", lambda e: e["requires"].update(network=True), {"network": False}),
            ("requires:key", lambda e: e["requires"].update(keys=["K"]), {}),
        ]
        for code, mutate, env_change in cases:
            reg = fixture()
            who = "taste" if code == "requires:node" else "imp"
            if mutate:
                mutate(next(e for e in reg["entries"] if e["id"] == who))
            env = dict(self.env, **env_change)
            r = sel.select(reg, "design/build", {"surface": "app-ui", "budget": "normal"}, env)
            self.assertIn({"skill": who, "reason": code}, r["rejected"], code)
            self.assertNotEqual(r["primary"], who, code)

    def test_unregistered_skills_are_listed_and_never_selected(self):
        r = sel.select(self.reg, "design/build", {"budget": "normal"}, dict(self.env, unregistered=["mystery"]))
        self.assertIn({"skill": "mystery", "reason": "unregistered"}, r["rejected"])
        self.assertNotEqual(r["primary"], "mystery")

    def test_not_for_blocks_a_surface_and_is_listed(self):
        r = self.pick("design/build", surface="pixel")
        self.assertIsNone(r["primary"])
        self.assertIn({"skill": "imp", "reason": "not_for:pixel"}, r["rejected"])
        self.assertIn({"skill": "taste", "reason": "not_for:pixel"}, r["rejected"])

    def test_requires_pr_needs_a_pr(self):
        self.reg["entries"].append(skill("prbot", {"design/critique": row(3, 1, 1)}, role=("checker",), requires={"pr": True}))
        self.assertIn({"skill": "prbot", "reason": "requires:pr"}, self.pick("design/critique", where="local")["rejected"])
        self.assertEqual(self.pick("design/critique", where="pr", stakes="pillar")["primary"], "prbot")

    def test_agent_cap_includes_the_skills_own_agents(self):
        r = self.pick("design/critique", budget="critical")        # cap 0: imp spawns two agents
        self.assertIn({"skill": "imp", "reason": "agent_cap"}, r["rejected"])
        self.assertEqual(r["primary"], "template")

    def test_read_description_is_never_primary(self):
        self.reg["entries"].append(skill("peek", {"design/critique": row(3, 1, 1)}, role=("checker",), read="description"))
        r = self.pick("design/critique", stakes="pillar")
        self.assertNotEqual(r["primary"], "peek")
        self.assertIn({"skill": "peek", "reason": "read_description"}, r["rejected"])
        for flag in (False, "description"):
            self.reg["entries"][-1]["read"] = flag
            self.assertNotEqual(self.pick("design/critique", stakes="pillar")["primary"], "peek")

    def test_tie_on_evidence_without_a_default_asks_for_a_bakeoff(self):
        self.reg["budget_policy"]["provisional_defaults"] = {}
        r = self.pick("design/build", surface="app-ui", budget="normal")
        self.assertEqual(r["bakeoff"], "needed")
        self.assertFalse(r["provisional"])
        self.assertIsNotNone(r["primary"])

    def test_tie_on_evidence_with_a_default_returns_it_provisionally(self):
        r = self.pick("design/build", surface="app-ui", budget="normal", stakes="routine")
        self.assertEqual((r["primary"], r["provisional"], r["bakeoff"]), ("taste", True, "needed"))
        r = self.pick("design/build", surface="app-ui", budget="ample", stakes="pillar")
        self.assertEqual((r["primary"], r["provisional"]), ("imp", True))

    def test_continuity_binds_the_author_role_only(self):
        r = self.pick("design/build", surface="app-ui", author="imp", budget="tight")
        self.assertEqual((r["primary"], r["bakeoff"], r["provisional"]), ("imp", None, False))
        self.assertIn("continues", r["why"])
        # a checker job ignores the author field
        self.assertEqual(self.pick("design/critique", author="imp", budget="tight", stakes="routine")["primary"], "template")

    def test_continuity_does_not_save_a_filtered_author(self):
        env = dict(self.env, state={"imp": "drifted"})
        r = sel.select(self.reg, "design/build", {"surface": "app-ui", "author": "imp"}, env)
        self.assertEqual(r["primary"], "taste")
        self.assertIn({"skill": "imp", "reason": "drifted"}, r["rejected"])

    def test_tie_band_orders_by_risk_then_cost(self):
        reg = fixture()
        reg["entries"] = [skill("a", {"w": row(3, 1, 1)}, role=("checker",), cost=1, verdict="PASS-WITH-CONDITIONS"),
                          skill("b", {"w": row(3, 1, 1)}, role=("checker",), cost=2, verdict="PASS"),
                          skill("c", {"w": row(3, 1, 0)}, role=("checker",), cost=0, verdict="PASS-WITH-CONDITIONS")]
        reg["jobs"]["w"] = {"role": "checker"}
        r = sel.select(reg, "w", {"budget": "normal"}, {})
        # scores: a 9+3-1-1=10, b 9+3-2-0=10, c 9+2-0-1=10: all inside the band; risk 0 wins
        self.assertEqual(r["primary"], "b")

    def test_gap_names_the_project_fallback_and_a_watch_row(self):
        r = self.pick("design/build", surface="pixel")
        self.assertEqual(r["gap"]["fallback"], ["rule/pixel-art-1x", "lens:design.md"])
        self.assertEqual(r["gap"]["watch_row"], "gap/design/build/pixel")
        self.assertEqual(r["fallback_used"], "lens:design.md")

    def test_generic_fits_do_not_apply_to_a_pixel_surface(self):
        self.reg["entries"].append(skill("pixelfan", {"design/critique": row(3, 1, 1)}, role=("checker",)))
        self.assertIsNone(self.pick("design/critique", surface="pixel")["primary"])
        self.assertIn("pixelfan", self.pick("design/critique", surface="app-ui", stakes="pillar")["scores"])

    def test_surface_aliases(self):
        self.assertEqual(sel.normalise({"surface": "pixel-sprite"})["surface"], "pixel")
        self.assertEqual(sel.normalise({"surface": "macos-native"})["surface"], "native-macos")
        self.assertEqual(sel.normalise({"surface": "Status Line"})["surface"], "terminal")

    def test_surface_specific_fit_beats_generic(self):
        reg = fixture()
        reg["entries"][2]["fits"]["design/app-ui/critique"] = row(3, 1, 1)
        r = sel.select(reg, "design/critique", {"surface": "app-ui", "budget": "normal"}, self.env)
        self.assertEqual(r["scores"]["template"], 9 + 3 - 0 - 0)

    def test_language_variant_of_a_fit_wins_over_the_generic_row(self):
        reg = fixture()
        reg["entries"] = [skill("p", {"writing/prose": row(3, 1, 1), "writing/prose-es": row(1)})]
        self.assertEqual(sel.select(reg, "writing/prose", {"language": "en"}, {})["primary"], "p")
        self.assertIsNone(sel.select(reg, "writing/prose", {"language": "es"}, {})["primary"])

    def test_log_line_has_the_decision_fields(self):
        line = sel.log_line(self.pick("design/critique", budget="tight"), "2026-10-26T10:00:00Z")
        for key in ("ts", "job", "traits", "picked", "role", "why", "beat", "rejected", "companions", "fallback_used", "gap", "registry",
                    "budget", "via", "debate", "winner", "provisional", "bakeoff", "agent_runs", "nodes"):
            self.assertIn(key, line)
        self.assertEqual(line["nodes"][0]["tokens"], None)
        json.dumps(line)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.reg = fixture()
        self.env = {"node": "v22", "binary_allowed": True}

    def plan(self, **traits):
        traits.setdefault("surface", "app-ui")
        return sel.plan(self.reg, "design/build", traits, self.env)

    def test_ample_pillar_gives_an_author_a_different_checker_and_a_debate(self):
        p = self.plan(budget="ample", stakes="pillar")
        self.assertEqual(p["primary"], "imp")
        self.assertTrue(p["checkers"])
        self.assertNotIn("imp", [c["skill"] for c in p["checkers"]])
        self.assertTrue(p["debate"])
        self.assertIn("debate", p["needs_d39"])

    def test_normal_gives_one_paid_checker_and_no_debate(self):
        p = self.plan(budget="normal", stakes="pillar")
        paid = [c for c in p["checkers"] if c["job"] == "design/critique"]
        self.assertLessEqual(len(paid), 1)
        self.assertFalse(p["debate"])

    def test_a_checker_equal_to_the_author_is_rejected_as_same_skill(self):
        p = self.plan(budget="ample", stakes="pillar", author="imp")
        self.assertIn({"skill": "imp", "reason": "same_skill", "job": "design/critique"}, p["rejected"])

    def test_critical_returns_no_agents(self):
        p = self.plan(budget="critical", stakes="pillar")
        self.assertEqual(p["agent_runs"], 0)
        self.assertEqual(p["agent_cap"], 0)
        self.assertFalse(p["debate"])
        self.assertTrue(all(c["agents"] == 0 for c in p["checkers"]))

    def test_tight_runs_instruments_only(self):
        p = self.plan(budget="tight")
        self.assertEqual({c["job"] for c in p["checkers"]} - {"design/lint"}, set())

    def test_agent_runs_never_exceed_the_cap_at_any_level(self):
        for level in sel.LEVELS:
            for stakes in ("routine", "pillar"):
                p = self.plan(budget=level, stakes=stakes)
                self.assertLessEqual(p["agent_runs"], p["agent_cap"], (level, stakes))

    def test_debate_only_at_ample_and_pillar(self):
        for level in sel.LEVELS:
            for stakes in ("routine", "pillar"):
                self.assertEqual(self.plan(budget=level, stakes=stakes)["debate"], level == "ample" and stakes == "pillar", (level, stakes))

    def test_policy_row_is_a_recommendation_that_marks_what_needs_d39(self):
        p = self.plan(budget="ample", stakes="pillar")
        self.assertEqual(p["recommendations"][0]["text"], "debate on pillar")
        self.assertTrue(p["recommendations"][0]["needs_d39"])
        self.assertFalse(self.plan(budget="normal")["recommendations"][0]["needs_d39"])

    def test_gap_plan_has_no_checkers(self):
        p = self.plan(surface="pixel")
        self.assertIsNone(p["primary"])
        self.assertEqual(p["checkers"], [])


class RealRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            cls.reg = json.load(f)
        cls.env = {"node": "v22", "chrome": True, "binary_allowed": True, "network": True, "browser_tool": True, "codex": False,
                   "project_rules": ["no_claude_footer"]}

    def sel(self, job, **traits):
        return sel.select(self.reg, job, traits, self.env)

    def test_impeccable_is_never_primary_for_terminal_pixel_or_native_macos(self):       # section 11 check 8
        for surface in ("terminal", "pixel", "native-macos", "pixel-sprite", "macos-native", "status-line"):
            for job in ("design/shape", "design/build", "design/polish", "design/critique", "design/a11y", "design/lint", "design/tokens",
                        "motion/hero", "motion/micro-interaction", "writing/ui-copy"):
                for level in sel.LEVELS:
                    for stakes in ("routine", "pillar"):
                        r = self.sel(job, surface=surface, budget=level, stakes=stakes)
                        self.assertNotEqual(r["primary"], "impeccable", (surface, job, level, stakes))

    def test_pixel_and_terminal_design_jobs_are_gaps_with_project_rules(self):
        r = self.sel("design/build", surface="pixel")
        self.assertIsNone(r["primary"])
        self.assertIn("rule/pixel-art-1x", r["gap"]["fallback"])
        self.assertEqual(r["gap"]["fallback"][-1], "lens:design.md")
        r = self.sel("design/build", surface="terminal")
        self.assertIn("rule/terminal-status-line", r["gap"]["fallback"])

    def test_tight_routine_critique_prefers_the_zero_cost_template(self):               # section 11 check 9
        r = self.sel("design/critique", surface="app-ui", stakes="routine", budget="tight")
        self.assertEqual(r["primary"], "design:design-critique")
        self.assertEqual(r["scores"]["impeccable"], 4)
        self.assertEqual(r["scores"]["design:design-critique"], 6)
        entry = next(e for e in self.reg["entries"] if e["id"] == r["primary"])
        self.assertEqual((entry["cost"]["class"], entry["cost"]["agents"]), (0, 0))

    def test_pillar_critique_with_a_normal_budget_scores_ten_against_six(self):
        r = self.sel("design/critique", surface="app-ui", stakes="pillar", budget="normal")
        self.assertEqual((r["primary"], r["scores"]["impeccable"], r["scores"]["design:design-critique"]), ("impeccable", 10, 6))

    def test_app_screen_tie_is_provisional_and_follows_the_default_table(self):
        r = self.sel("design/build", surface="app-ui", stakes="routine", budget="normal")
        self.assertEqual((r["primary"], r["provisional"], r["bakeoff"]), ("taste-v2", True, "needed"))
        r = self.sel("design/build", surface="app-ui", stakes="pillar", budget="ample")
        self.assertEqual((r["primary"], r["provisional"]), ("impeccable", True))

    def test_spanish_text_is_left_to_the_project_writing_lens(self):
        r = self.sel("writing/public-prose", language="es")
        self.assertIsNone(r["primary"])
        self.assertEqual(r["gap"]["fallback"][-1], "lens:writing.md")

    def test_english_public_prose_goes_to_the_humanize_gate(self):
        self.assertEqual(self.sel("writing/public-prose", language="en")["primary"], "budgit-humanize")

    def test_code_review_plugin_is_blocked_by_the_footer_rule(self):
        r = self.sel("process/review", where="pr")
        self.assertIn({"skill": "code-review-plugin", "reason": "project_rule:no_claude_footer"}, r["rejected"])
        self.assertEqual(r["primary"], "code-review")

    def test_security_guidance_is_never_the_reviewer(self):
        for job in ("security/own-diff", "security/third-party", "security/release"):
            self.assertNotEqual(self.sel(job)["primary"], "security-guidance")

    def test_unread_skills_are_never_primary(self):
        unread = {e["id"] for e in self.reg["entries"] if e.get("read") in (False, "description")}
        self.assertTrue(unread)
        for job in self.reg["jobs"]:
            for level in sel.LEVELS:
                self.assertNotIn(self.sel(job, budget=level)["primary"], unread, job)

    def test_every_job_resolves_without_error_on_every_surface_and_level(self):
        for job in self.reg["jobs"]:
            for surface in list(self.reg["surfaces"]) + [None]:
                for level in sel.LEVELS:
                    for stakes in ("routine", "pillar"):
                        traits = {"budget": level, "stakes": stakes}
                        if surface:
                            traits["surface"] = surface
                        p = sel.plan(self.reg, job, traits, self.env)
                        self.assertLessEqual(p["agent_runs"], p["agent_cap"], (job, surface, level, stakes))
                        self.assertTrue(p["primary"] or p["gap"], (job, surface))

    def test_critical_level_never_runs_an_agent_for_any_job(self):
        for job in self.reg["jobs"]:
            for surface in list(self.reg["surfaces"]) + [None]:
                traits = {"budget": "critical", "stakes": "pillar"}
                if surface:
                    traits["surface"] = surface
                p = sel.plan(self.reg, job, traits, self.env)
                self.assertEqual(p["agent_runs"], 0, (job, surface))
                self.assertFalse(p["debate"])


class RegistryShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            cls.reg = json.load(f)
        cls.ids = [e["id"] for e in cls.reg["entries"]]

    def test_ids_are_unique(self):
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_every_entry_has_the_fields_the_selector_reads(self):
        for e in self.reg["entries"]:
            for key in ("id", "kind", "status", "enabled", "fits", "cost", "security", "fallback", "load", "role"):
                self.assertIn(key, e, e["id"])
            self.assertIn(e["status"], ("installed", "active", "watch", "rejected", "idea-taken", "unresolved", "draft"), e["id"])
            self.assertIn(e.get("read", "full"), ("full", "description", False), e["id"])
            self.assertIn(e["load"], ("full", "name-only", "user-only", "off"), e["id"])
            for r in e["role"]:
                self.assertIn(r, ("author", "checker", "library", "instrument"), e["id"])
            for key, fit in e["fits"].items():
                self.assertIn(int(fit["fit"]), (0, 1, 2, 3), (e["id"], key))

    def test_every_fallback_names_a_registry_entry_or_a_project_text(self):
        known = set(self.ids)
        for e in self.reg["entries"]:
            for fb in e.get("fallback") or []:
                if fb.startswith("lens:"):
                    self.assertTrue(os.path.isfile(os.path.join(KICKOFF, "lenses", fb[5:])), (e["id"], fb))
                elif fb.endswith(".md"):
                    self.assertTrue(os.path.isfile(os.path.join(KICKOFF, fb)), (e["id"], fb))
                else:
                    self.assertIn(fb, known | {a for x in self.reg["entries"] for a in x.get("aliases", [])}, (e["id"], fb))

    def test_gap_tables_point_at_real_rule_files_and_lenses(self):
        for key, g in self.reg["gaps"].items():
            for rule in g.get("rules", []):
                self.assertTrue(os.path.isfile(os.path.join(KICKOFF, "reference", "rules", rule[len("rule/"):] + ".md")), (key, rule))
            if g.get("lens"):
                path = g["lens"][5:] if g["lens"].startswith("lens:") else g["lens"]
                self.assertTrue(os.path.isfile(os.path.join(KICKOFF, "lenses" if g["lens"].startswith("lens:") else "", path)), key)

    def test_jobs_name_known_checks_and_policy_kinds(self):
        kinds = {r["kind"] for r in self.reg["budget_policy"]["matrix"]}
        for name, job in self.reg["jobs"].items():
            for c in job.get("checks", []):
                self.assertIn(c, self.reg["jobs"], name)
            pk = job.get("policy_kind")
            for k in (pk.values() if isinstance(pk, dict) else [pk] if pk else []):
                self.assertIn(k, kinds, name)

    def test_every_matrix_cell_has_english_and_spanish_text(self):
        for row in self.reg["budget_policy"]["matrix"]:
            self.assertEqual(set(row["cells"]), set(sel.LEVELS), row["kind"])
            for cell in row["cells"].values():
                self.assertTrue(cell["text"] and cell["text_es"])

    def test_cells_that_name_a_debate_are_marked_needs_d39(self):
        flagged = 0
        for row in self.reg["budget_policy"]["matrix"]:
            for cell in row["cells"].values():
                if re.search(r"debate", re.sub(r"\bno debate\b", "", cell["text"])):
                    self.assertTrue(cell["needs_d39"], cell["text"])
                    flagged += 1
        self.assertGreater(flagged, 1)

    def test_the_public_parked_skills_are_name_only(self):
        """19 on the owner's machine: seventeen ship; the two legacy taste skills live in the private overlay (see TestPublicRegistry)."""
        parked = [e["id"] for e in self.reg["entries"] if e.get("load_proposed") == "name-only"]
        for expected in ["gsap-frameworks", "gsap-performance", "gsap-plugins", "gsap-react", "gsap-utils", "threejs-animation",
                         "threejs-fundamentals", "threejs-react-three-fiber"]:
            self.assertIn(expected, parked)
        self.assertEqual(len(parked), 17)
        for keep in ("gsap-core", "gsap-timeline", "gsap-scrolltrigger", "impeccable", "taste-v2", "diagram-design"):
            self.assertNotIn(keep, parked)

    def test_security_guidance_is_layer1_only_with_its_condition(self):
        e = next(e for e in self.reg["entries"] if e["id"] == "security-guidance")
        self.assertEqual(e["enabled"], "layer1-only")
        self.assertIn({"var": "ENABLE_CODE_SECURITY_REVIEW", "equals": "0", "condition": "layer1-only"}, e["env_checks"])

    def test_the_six_rules_the_humanize_row_and_the_legal_row_are_present(self):
        for rid in ("rule/pixel-art-1x", "rule/frame-timing", "rule/terminal-status-line", "rule/readme-structure", "rule/svg-light-dark",
                    "rule/odd-data-probes", "budgit-humanize"):
            self.assertIn(rid, self.ids)
        self.assertNotIn("legal-eu-es-fr", self.ids)        # private by decision: it lives in the user's overlay
        for e in self.reg["entries"]:
            if e["id"].startswith("rule/"):
                self.assertFalse(e["enabled"] is True and e["status"] in sel.SELECTABLE, e["id"])

    def test_no_private_voice_in_the_shipped_registry(self):
        text = json.dumps(self.reg)
        self.assertIsNone(re.search(r"\b(she|her|hers)\b", text, re.I))
        self.assertNotIn("/Users/", text)

    def test_path_risk_classes_are_data(self):
        for cls in ("auth", "tokens", "crypto", "subprocess", "eval", "network", "sql", "permissions", "hooks", "settings", "dependencies"):
            self.assertTrue(self.reg["path_risk"][cls], cls)


if __name__ == "__main__":
    unittest.main()
