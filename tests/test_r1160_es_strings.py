"""R-1160: with the language set to es, the first-run commands print Spanish; en output stays as it was."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import budget
import i18n

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
GIT = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.org",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.org"}
EN_ONLY = ("is not set up on this machine", "no Budgit project here", "config.json written", "created (git with",
           "hub card:", "this session: unbound", "items:", "untracked inputs", "budget: normal (none)", "main model:",
           "Subagents:", "Unchanged at every level", "no sessions with agents", "level used for limits", "no budget data")


class EsStrings(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.user_home = os.path.join(self.tmp, "userhome")
        os.makedirs(self.user_home)

    def cli(self, lang, *args, cwd=None, expect=0):
        e = dict(os.environ, HOME=self.user_home, BUDGIT_HOME=self.home, BUDGIT_LANG=lang, **GIT)
        for k in ("BUDGIT_SESSION", "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
            e.pop(k, None)
        r = subprocess.run([sys.executable, CLI, *args], cwd=cwd or self.tmp, env=e, capture_output=True, text=True)
        self.assertEqual(r.returncode, expect, "%s\n%s%s" % (args, r.stdout, r.stderr))
        return r.stdout + r.stderr

    def run_all(self, lang):
        hub, root = os.path.join(self.tmp, "hub"), os.path.join(self.tmp, "projects")
        out = {}
        out["hub"] = self.cli(lang, "hub", expect=1)
        out["status_none"] = self.cli(lang, "status", expect=1)
        out["setup"] = self.cli(lang, "setup", "--hub", hub, "--projects-root", root)
        out["new"] = self.cli(lang, "new", "demo")
        proj = os.path.join(root, "demo")
        out["status"] = self.cli(lang, "status", cwd=proj)
        out["add"] = self.cli(lang, "ledger", "add", "--phase", "mvp", "--appetite", "small", "--area", "main", "--text", "a landing page", cwd=proj)
        out["budget"] = self.cli(lang, "budget", cwd=proj)
        out["comply"] = self.cli(lang, "comply", cwd=proj)
        return out, hub, root

    def test_en_lines_are_exactly_today_text(self):
        out, hub, root = self.run_all("en")
        proj = os.path.join(os.path.realpath(root), "demo")
        self.assertEqual(out["hub"],"budgit: Budgit is not set up on this machine: run `budgit setup --hub <dir> --projects-root <dir>` (reference/setup.md)\n")
        self.assertEqual(out["status_none"], "budgit: no Budgit project here — run: budgit activate <project dir>\n")
        self.assertIn("config.json written; hub %s (.budgit/research/INDEX.md" % os.path.realpath(hub), out["setup"])
        self.assertIn("budgit setup: ", out["setup"])
        self.assertIn("budgit: project %s created (git with noreply identity, .budgit/, CLAUDE.md)\n" % proj, out["new"])
        self.assertIn("hub card: ", out["new"])
        self.assertIn("  — run `budgit activate ", out["new"])
        self.assertIn("in the session\n", out["new"])
        for line in ("this session: unbound", "items: 0 (0 open/doing)", "untracked inputs (all sessions): 0"):
            self.assertIn(line + "\n", out["status"])
        self.assertTrue(out["status"].startswith("project: "))
        self.assertIn("R-1 -> open\n", out["add"])
        lines = out["budget"].split("\n")
        self.assertEqual(lines[0], "budget: normal (none) — no budget data (no live 5-hour/weekly snapshot)")
        self.assertTrue(lines[1].startswith("main model: opus for plan/design/QA judgement; build sessions: sonnet (executing a reviewed plan"))
        self.assertTrue(lines[2].startswith("budgit budget: normal. Subagents: at most 3 in parallel, ~60 tool calls each; research: reuse saved research first"))
        self.assertTrue(lines[3].startswith("Unchanged at every level: input ledger capture, phase gates"))
        self.assertEqual(out["budget"].rstrip("\n"), budget.policy_text("normal").join(
            ["budget: normal (none) — no budget data (no live 5-hour/weekly snapshot)\n", ""]))
        self.assertEqual(out["comply"], "no sessions with agents or violations in range (0 quiet)\n"
                         "level used for limits: current · rules: reference/budget.md · 0 model tokens\n")

    def test_es_has_no_english_line_left(self):
        out, hub, root = self.run_all("es")
        for name, text in out.items():
            if name == "add":
                continue
            for phrase in EN_ONLY:
                self.assertNotIn(phrase, text, name)
        self.assertIn("Budgit no está configurado en este equipo", out["hub"])
        self.assertIn("aquí no hay ningún proyecto de Budgit", out["status_none"])
        self.assertIn("escrito; hub", out["setup"])
        self.assertIn("creado (git con identidad noreply", out["new"])
        self.assertIn("ficha del hub:", out["new"])
        for line in ("esta sesión: sin vincular", "elementos: 0 (0 abiertos/en curso)", "entradas sin seguimiento (todas las sesiones): 0"):
            self.assertIn(line + "\n", out["status"])
        self.assertTrue(out["status"].startswith("proyecto en uso: "))
        self.assertIn("R-1 -> open\n", out["add"])        # the status word after the arrow is ledger data, as in `ledger list` [open]
        b = out["budget"].split("\n")
        self.assertTrue(b[0].startswith("presupuesto: normal (none) — sin datos de presupuesto"))
        self.assertTrue(b[1].startswith("modelo principal: opus"))
        self.assertIn("Subagentes: como máximo 3", b[2])
        self.assertTrue(b[3].startswith("No cambia en ningún nivel"))
        self.assertIn("no hay sesiones con agentes", out["comply"])
        self.assertIn("nivel usado para los límites: current", out["comply"])

    def test_the_catalog_texts_the_en_budget_keeps_match_the_policy_table(self):
        en = i18n.CATALOG["en"]
        for level, p in budget.POLICY.items():
            self.assertEqual(en["r1160.research." + level], p["research"])
            self.assertEqual(en["r1160.defer." + level], p["defer"])
            self.assertEqual(en["r1160.other." + level], p["other_llm"])
        self.assertEqual(en["r1160.budget_invariants"], budget.INVARIANTS)
        self.assertEqual(en["r1160.budget_note"], budget.BUILD_NOTE)
        self.assertEqual(en["r1160.budget_roles"], budget.ROLES)
        for level in budget.LEVELS:
            self.assertEqual(budget._policy_text_in(level, "en"), budget.policy_text(level))
