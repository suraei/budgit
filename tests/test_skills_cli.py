import json, os, re, shutil, subprocess, sys, tempfile, unittest

from helpers import StudioTestCase, ROOT

import i18n
import skills as sk

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
REAL = os.path.join(ROOT, "skills", "budgit-kickoff", "reference", "skills-registry.json")
SECRET = "sk-live-never-print-this-0123456789"


def entry(id, **kw):
    e = {"id": id, "kind": "skill", "status": "installed", "enabled": True, "read": "full", "role": ["author"],
         "security": {"verdict": "PASS"}, "cost": {"class": 0, "agents": 0}, "not_for": [], "requires": {}, "fallback": [],
         "load": "full", "fits": {"design/build": {"fit": 3, "check": 1, "receipt": 1}},
         "install": {"scope": "user", "path": "~/.claude/skills/" + id}, "source": {}, "flags": {}}
    e.update(kw)
    return e


def registry(entries):
    return {"version": "test.1", "surfaces": {"app-ui": {"generic_ok": True}}, "jobs": {"design/build": {"role": "author", "continuity": True}, "design/critique": {"role": "checker"}},
            "budget_policy": {"levels": {"normal": {"B": 1, "agent_cap": 3, "checkers": 1, "debate": False}},
                              "matrix": [], "provisional_defaults": {}},
            "gaps": {"area:design": {"lens": "lens:design.md"}}, "entries": entries}


class CliCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.claude = os.environ["CLAUDE_CONFIG_DIR"]
        os.makedirs(self.claude)

    def put(self, rel, text, base=None):
        path = os.path.join(base or self.claude, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def skill(self, name, body="body"):
        self.put("skills/%s/SKILL.md" % name, "---\nname: %s\ndescription: does %s things\n---\n%s\n" % (name, name, body))
        return os.path.join(self.claude, "skills", name)

    def write_registry(self, reg):
        path = os.path.join(self.tmp, "registry.json")
        with open(path, "w") as f:
            json.dump(reg, f)
        return path

    def cli(self, *args, lang="es", project=True):
        env = dict(os.environ, BUDGIT_LANG=lang)
        for name in list(env):      # this machine's own settings must not decide a test
            if name == "BUDGIT_BUDGET" or name == "DO_NOT_TRACK" or name.startswith(("IMPECCABLE_", "SUPERPOWERS_", "MINE_", "SUITE_", "ENABLE_CODE")):
                env.pop(name)
        cmd = [sys.executable, CLI] + (["--project", self.project] if project else []) + list(args)
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=self.tmp)
        return r.returncode, r.stdout, r.stderr

    def snapshot(self, folder):
        out = {}
        for base, _, files in os.walk(folder):
            for name in files:
                full = os.path.join(base, name)
                out[full] = (os.path.getmtime(full), os.path.getsize(full))
        return out


class ListShowTest(CliCase):
    def test_list_prints_the_count_and_every_status_filter_works(self):
        rc, out, _ = self.cli("skills", "list", "--registry", REAL)
        self.assertEqual(rc, 0)
        self.assertRegex(out.split("\n")[0], r"^\d+ skills, registro ")
        self.assertIn("impeccable", out)
        rc, out, _ = self.cli("skills", "list", "--status", "watch", "--registry", REAL)
        self.assertEqual(rc, 0)
        self.assertNotIn("impeccable ", out)

    def test_list_for_a_job_shows_the_fit_column(self):
        rc, out, _ = self.cli("skills", "list", "--job", "design/critique", "--registry", REAL)
        self.assertEqual(rc, 0)
        self.assertIn("design:design-critique", out)

    def test_show_prints_the_entry_and_an_unknown_id_is_an_error_in_spanish(self):
        rc, out, _ = self.cli("skills", "show", "impeccable", "--registry", REAL)
        self.assertEqual(rc, 0)
        shown = json.loads(out)
        self.assertEqual(shown["id"], "impeccable")
        self.assertEqual(shown["sub_jobs"], sorted(shown["fits"]))
        self.assertIn("design/critique", shown["sub_jobs"])
        rc, out, err = self.cli("skills", "show", "no-such-skill", "--registry", REAL)
        self.assertNotEqual(rc, 0)
        self.assertIn("No hay ninguna skill con el id 'no-such-skill'", out + err)

    def test_english_output_when_the_language_is_english(self):
        rc, out, err = self.cli("skills", "show", "no-such-skill", "--registry", REAL, lang="en")
        self.assertIn("No skill with the id 'no-such-skill'", out + err)


class WhyTest(CliCase):
    def setUp(self):
        super().setUp()
        self.skill("alpha")
        self.skill("beta")
        alpha = entry("alpha", fits={"design/build": {"fit": 3, "check": 1, "receipt": 1}, "design/critique": {"fit": 2, "check": 1, "receipt": 0}},
                      role=["author", "checker"])
        beta = entry("beta", fits={"design/build": {"fit": 3, "check": 1, "receipt": 1}}, cost={"class": 2, "agents": 0})
        self.fixture = self.write_registry(registry([alpha, beta]))

    def test_why_logs_exactly_one_valid_json_line(self):
        self.init_project()
        rc, out, _ = self.cli("skills", "why", "--job", "design/build", "--traits", "surface=app-ui,stakes=pillar", "--level", "normal",
                              "--registry", self.fixture)
        self.assertEqual(rc, 0)
        self.assertIn("Elegida: ", out)
        log = os.path.join(self.project, self.DATA, "skills-log.jsonl")
        lines = open(log, encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        for key in ("ts", "job", "traits", "picked", "role", "why", "beat", "rejected", "companions", "fallback_used", "gap", "registry",
                    "budget", "via", "debate", "winner", "provisional", "bakeoff", "agent_runs", "nodes"):
            self.assertIn(key, rec)
        self.assertEqual(rec["job"], "design/build")
        self.assertEqual(rec["budget"], "normal")
        self.assertEqual(rec["picked"], "alpha")
        # a second call appends, it does not rewrite
        self.cli("skills", "why", "--job", "design/critique", "--registry", self.fixture)
        self.assertEqual(len(open(log, encoding="utf-8").read().splitlines()), 2)

    def test_no_log_flag_and_a_folder_without_data_write_nothing(self):
        self.init_project()
        self.cli("skills", "why", "--job", "design/build", "--no-log", "--registry", self.fixture)
        self.assertFalse(os.path.exists(os.path.join(self.project, self.DATA, "skills-log.jsonl")))
        bare = os.path.join(self.tmp, "bare")
        os.makedirs(bare)
        rc, out, _ = self.cli("--project", bare, "skills", "why", "--job", "design/build", "--registry", self.fixture, project=False)
        self.assertEqual(rc, 0)
        self.assertEqual(os.listdir(bare), [])

    def test_why_json_prints_the_record_that_is_logged(self):
        self.init_project()
        rc, out, _ = self.cli("skills", "why", "--job", "design/build", "--json", "--registry", self.fixture)
        rec = json.loads(out)
        logged = json.loads(open(os.path.join(self.project, self.DATA, "skills-log.jsonl")).read())
        self.assertEqual(rec, logged)

    def test_why_without_a_job_says_which_one_is_needed(self):
        rc, out, err = self.cli("skills", "why", "--registry", self.fixture)
        self.assertNotEqual(rc, 0)
        self.assertIn("--job", out + err)

    def test_a_gap_prints_the_project_fallback_and_opens_a_local_watch_row(self):
        self.init_project()
        rc, out, _ = self.cli("skills", "why", "--job", "design/build", "--traits", "surface=pixel", "--registry", REAL)
        self.assertEqual(rc, 0)
        self.assertIn("Ninguna skill registrada encaja", out)
        self.assertIn("rule/pixel-art-1x", out)
        self.assertIn("gap/design/build/pixel", out)
        gaps = json.load(open(os.path.join(self.project, self.DATA, "skills-gaps.json")))
        row = gaps["gap/design/build/pixel"]
        self.assertEqual((row["status"], row["count"]), ("unresolved", 1))
        self.cli("skills", "why", "--job", "design/build", "--traits", "surface=pixel", "--registry", REAL)
        gaps = json.load(open(os.path.join(self.project, self.DATA, "skills-gaps.json")))
        self.assertEqual(gaps["gap/design/build/pixel"]["count"], 2)

    def test_the_author_in_design_json_continues_and_is_logged(self):
        self.init_project()
        self.put("%s/design.json" % self.DATA, json.dumps({"author": "beta"}), base=self.project)
        rc, out, _ = self.cli("skills", "why", "--job", "design/build", "--traits", "surface=app-ui", "--registry", self.fixture, "--json")
        rec = json.loads(out)
        self.assertEqual(rec["picked"], "beta")
        self.assertIsNone(rec["bakeoff"])

    def test_the_output_never_names_a_secret_value_from_the_settings(self):
        self.init_project()
        self.put("settings.json", json.dumps({"env": {"API_TOKEN": SECRET, "IMPECCABLE_NO_TELEMETRY": "1"}}))
        for args in (("skills", "why", "--job", "design/build"), ("skills", "check"), ("skills", "load-plan")):
            rc, out, err = self.cli(*args, "--registry", self.fixture)
            self.assertNotIn(SECRET, out + err)
        log = open(os.path.join(self.project, self.DATA, "skills-log.jsonl")).read()
        self.assertNotIn(SECRET, log)


class CheckTest(CliCase):
    def setUp(self):
        super().setUp()
        self.mine = self.skill("mine")
        self.entry = entry("mine", source={"tree_sha256": sk.tree_hash(self.mine)}, flags={"hooks": False, "mcp": False, "script_hosts": []})
        self.reg = [self.entry]

    def check(self, extra=None, **kw):
        path = self.write_registry(registry(self.reg + (extra or [])))
        return self.cli("skills", "check", "--registry", path, "--json", **kw)

    def kinds(self, out):
        return sorted((f["kind"], f["id"]) for f in json.loads(out))

    def test_a_clean_install_reports_nothing(self):
        rc, out, _ = self.check()
        self.assertEqual((rc, json.loads(out)), (0, []))
        path = self.write_registry(registry(self.reg))
        rc, out, _ = self.cli("skills", "check", "--registry", path)
        self.assertIn("Skills: nada que avisar.", out)

    def test_a_changed_file_is_drifted_and_the_exit_code_is_one(self):
        self.put("skills/mine/SKILL.md", "---\nname: mine\ndescription: edited\n---\nnew\n")
        rc, out, _ = self.check()
        self.assertEqual(rc, 1)
        self.assertEqual(self.kinds(out), [("drifted", "mine")])

    def test_a_removed_skill_is_missing(self):
        shutil.rmtree(self.mine)
        rc, out, _ = self.check()
        self.assertEqual(rc, 1)
        self.assertEqual(self.kinds(out), [("missing", "mine")])

    def test_an_installed_skill_without_an_entry_is_unregistered(self):
        self.skill("stranger")
        rc, out, _ = self.check()
        self.assertEqual(rc, 0)
        self.assertEqual(self.kinds(out), [("unregistered", "stranger")])

    def test_a_new_hooks_folder_is_flags_changed(self):
        self.put("skills/mine/hooks/hooks.json", "{}")
        self.entry["source"]["tree_sha256"] = sk.tree_hash(self.mine)      # same review, but the folder grew a hook
        rc, out, _ = self.check()
        self.assertEqual(self.kinds(out), [("flags-changed", "mine")])

    def test_a_new_script_host_is_flags_changed_but_namespace_urls_are_not(self):
        self.put("skills/mine/run.js", "fetch('https://api.example.test/x'); const ns='http://www.w3.org/2000/svg';")
        self.entry["source"]["tree_sha256"] = sk.tree_hash(self.mine)
        rc, out, _ = self.check()
        found = json.loads(out)
        self.assertEqual([(f["kind"], f["id"]) for f in found], [("flags-changed", "mine")])
        self.assertIn("api.example.test", found[0]["detail"])
        self.assertNotIn("w3.org", found[0]["detail"])

    def test_env_report_names_the_setting_and_never_a_value(self):
        self.entry["env_checks"] = [{"var": "MINE_TELEMETRY", "any_of": ["MINE_NO_TELEMETRY", "DO_NOT_TRACK"], "condition": "telemetry-off"}]
        rc, out, _ = self.check()
        found = json.loads(out)
        self.assertEqual(found, [{"kind": "env", "id": "mine", "detail": "MINE_NO_TELEMETRY/DO_NOT_TRACK", "condition": "telemetry-off"}])
        self.put("settings.json", json.dumps({"env": {"DO_NOT_TRACK": "1"}}))
        self.assertEqual(json.loads(self.check()[1]), [])

    def test_equals_condition_compares_the_value_without_printing_it(self):
        self.entry["env_checks"] = [{"var": "REVIEW_SWITCH", "equals": "0", "condition": "layer1-only"}]
        self.put("settings.json", json.dumps({"env": {"REVIEW_SWITCH": SECRET}}))
        rc, out, err = self.check()
        self.assertEqual(self.kinds(out), [("env", "mine")])
        self.assertNotIn(SECRET, out + err)
        self.put("settings.json", json.dumps({"env": {"REVIEW_SWITCH": "0"}}))
        self.assertEqual(json.loads(self.check()[1]), [])

    def test_a_plugin_that_is_off_is_not_asked_for_its_settings(self):
        self.entry.update(plugin_key="mine@market", env_checks=[{"var": "MINE_NO_TELEMETRY"}])
        self.assertEqual(json.loads(self.check()[1]), [])
        self.put("settings.json", json.dumps({"enabledPlugins": {"mine@market": True}}))
        self.assertEqual(json.loads(self.check()[1])[0]["id"], "mine")

    def test_one_line_per_plugin_not_one_per_skill(self):
        for i in range(3):
            self.reg.append(entry("suite:s%d" % i, plugin_key="suite@market", env_checks=[{"var": "SUITE_NO_TELEMETRY"}], install={}))
        self.put("settings.json", json.dumps({"enabledPlugins": {"suite@market": True}}))
        found = json.loads(self.check()[1])
        self.assertEqual([f["id"] for f in found if f["kind"] == "env"], ["suite"])

    def test_a_changed_bundled_program_is_reported_only_when_it_differs(self):
        self.put("skills/mine/bin/tool", "binary-v1")
        self.entry["source"].update(tree_sha256=sk.tree_hash(self.mine), binary_sha256=sk.file_sha256(os.path.join(self.mine, "bin", "tool")))
        self.entry["install"]["binary_path"] = "bin/tool"
        self.assertEqual(json.loads(self.check()[1]), [])
        self.put("skills/mine/bin/tool", "binary-v2")
        self.entry["source"]["tree_sha256"] = sk.tree_hash(self.mine)
        self.assertEqual(self.kinds(self.check()[1]), [("binary", "mine")])


class LoadPlanTest(CliCase):
    def setUp(self):
        super().setUp()
        self.skill("big-one")
        self.skill("already-off")
        self.reg = [
            entry("big-one", load_proposed="name-only", description_chars=1400, load_reason="rarely used"),
            entry("already-off", load_proposed="name-only", description_chars=900),
            entry("plug:thing", load_proposed="name-only", description_chars=500, install={"scope": "plugin", "path": "x"}),
            entry("keep", description_chars=100),
        ]
        self.path = self.write_registry(registry(self.reg))

    def test_it_prints_the_block_for_user_skills_and_notes_plugin_skills(self):
        rc, out, _ = self.cli("skills", "load-plan", "--registry", self.path)
        self.assertEqual(rc, 0)
        self.assertIn('"big-one": "name-only"', out)
        self.assertIn('"already-off": "name-only"', out)
        self.assertNotIn('"plug:thing"', out)
        self.assertIn("plugin", out)
        self.assertNotIn("keep", out)
        self.assertIn("no se ha escrito nada", out)
        data = json.loads(self.cli("skills", "load-plan", "--json", "--registry", self.path)[1])
        self.assertEqual(data["block"], {"big-one": "name-only", "already-off": "name-only"})
        self.assertEqual(data["saved_chars"], (1400 - len("big-one")) + (900 - len("already-off")))

    def test_a_skill_already_set_is_left_out_of_the_block(self):
        self.put("settings.json", json.dumps({"skillOverrides": {"already-off": "name-only"}}))
        data = json.loads(self.cli("skills", "load-plan", "--json", "--registry", self.path)[1])
        self.assertEqual(list(data["block"]), ["big-one"])

    def test_it_writes_no_file_and_changes_no_settings(self):
        self.init_project()
        self.put("settings.json", json.dumps({"env": {"A": "1"}}))
        before_claude, before_project = self.snapshot(self.claude), self.snapshot(self.project)
        before_text = open(os.path.join(self.claude, "settings.json")).read()
        self.cli("skills", "load-plan", "--registry", self.path)
        self.cli("skills", "load-plan", "--json", "--registry", self.path)
        self.assertEqual(self.snapshot(self.claude), before_claude)
        self.assertEqual(self.snapshot(self.project), before_project)
        self.assertEqual(open(os.path.join(self.claude, "settings.json")).read(), before_text)

    def test_the_shipped_registry_proposes_seventeen_and_the_overlay_brings_the_owners_two(self):
        data = json.loads(self.cli("skills", "load-plan", "--json", "--registry", REAL)[1])
        self.assertEqual(len(data["rows"]), 17)
        for row in data["rows"]:
            self.assertEqual(row["proposed"], "name-only")
        legacy = [dict(entry(i), load_proposed="name-only") for i in ("design-taste-frontend", "high-end-visual-design")]
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, sk.OVERLAY_NAME), "w") as f:
            json.dump({"entries": legacy}, f)
        data = json.loads(self.cli("skills", "load-plan", "--json")[1])      # no --registry: shipped + overlay
        self.assertEqual(len(data["rows"]), 19)


class BuildTest(CliCase):
    def test_build_adds_unread_entries_for_unknown_installed_skills_and_writes_the_md(self):
        self.skill("newcomer")
        reg_path = self.write_registry(registry([entry("known")]))
        shutil.copy(reg_path, os.path.join(self.tmp, "keep.json"))
        rc, out, _ = self.cli("skills", "build", "--registry", reg_path)
        self.assertEqual(rc, 0)
        self.assertIn("1 skill(s) sin leer", out)
        self.assertEqual(json.load(open(reg_path)), registry([entry("known")]))          # the shipped file is never written
        overlay = json.load(open(os.path.join(self.tmp, sk.OVERLAY_NAME)))
        new = next(e for e in overlay["entries"] if e["id"] == "newcomer")
        self.assertEqual(new["visibility"], "private")
        self.assertEqual((new["read"], new["enabled"], new["security"]["verdict"]), (False, False, "unreviewed"))
        md = open(os.path.join(self.tmp, "skills-registry.md")).read()
        self.assertNotIn("newcomer", md)                     # the generated md lists public entries only
        self.assertIn("| known |", md)
        self.assertIn("Do not edit by hand", md)
        rc, out, _ = self.cli("skills", "build", "--registry", reg_path)
        self.assertIn("0 skill(s) sin leer", out)

    def test_a_seeded_skill_lands_in_the_overlay_switched_off_and_unread(self):
        self.skill("newcomer")
        reg_path = self.write_registry(registry([entry("known", fits={})]))
        self.cli("skills", "build", "--registry", reg_path)
        merged = sk.load_registry(reg_path, overlay=os.path.join(self.tmp, sk.OVERLAY_NAME))
        new = next(e for e in merged["entries"] if e["id"] == "newcomer")
        self.assertEqual((new["enabled"], new["read"], new["fits"], new["visibility"]), (False, False, {}, "private"))
        rc, out, _ = self.cli("skills", "why", "--job", "design/build", "--json", "--no-log", "--registry", reg_path)
        self.assertIsNone(json.loads(out)["picked"])

    def test_the_real_registry_md_is_up_to_date(self):
        rc, md = 0, sk.render_md(json.load(open(REAL)))
        self.assertEqual(md, open(os.path.join(os.path.dirname(REAL), "skills-registry.md"), encoding="utf-8").read())


class CatalogTest(unittest.TestCase):
    def test_every_key_the_module_names_exists_in_both_languages(self):
        src = open(os.path.join(ROOT, "plugin", "scripts", "skills.py"), encoding="utf-8").read()
        keys = set(re.findall(r'"(skills\.[a-z_.]+)"', src))
        for key in keys:
            if key.endswith((".", "_")):
                continue
            for lang in ("en", "es"):
                self.assertIn(key, i18n.CATALOG[lang], (key, lang))

    def test_the_two_catalogs_have_the_same_skills_keys(self):
        en = {k for k in i18n.CATALOG["en"] if k.startswith("skills.")}
        es = {k for k in i18n.CATALOG["es"] if k.startswith("skills.")}
        self.assertEqual(en, es)

    def test_prefix_keys_are_complete(self):
        for code in ("unregistered", "status", "disabled", "hold", "drifted", "missing", "update_pending", "requires", "not_for", "project_rule",
                     "same_skill", "agent_cap", "read_description"):
            self.assertIn("skills.reason." + code, i18n.CATALOG["en"])
        for code in ("continuity", "default", "score", "band", "gap"):
            self.assertIn("skills.why.code." + code, i18n.CATALOG["en"])
        for kind in ("drifted", "missing", "unregistered", "flags_changed", "env", "binary_sha256"):
            self.assertIn("skills.check." + kind, i18n.CATALOG["en"])

    def test_the_shipped_strings_use_no_private_voice(self):
        for lang in ("en", "es"):
            for key, text in i18n.CATALOG[lang].items():
                if key.startswith("skills."):
                    self.assertIsNone(re.search(r"\b(she|her|hers)\b|/Users/", text, re.I), key)


if __name__ == "__main__":
    unittest.main()


class PublicRegistryTest(unittest.TestCase):
    """The class behind the fiscal-es-fr / legal-eu-es-fr leak: what ships lists only skills a public user can get."""

    @classmethod
    def setUpClass(cls):
        with open(REAL, encoding="utf-8") as f:
            cls.reg = json.load(f)

    def test_the_exported_registry_holds_no_private_or_personally_installed_skill(self):
        for e in self.reg["entries"]:
            self.assertNotEqual(e.get("visibility"), "private", e["id"])
            self.assertFalse(sk.is_private(e), e["id"])      # user-level install from no public source
            install, source = e.get("install") or {}, e.get("source") or {}
            if (install.get("path") or "").startswith("~/.claude/skills/"):
                self.assertTrue(source.get("repo"), "%s is installed from ~/.claude/skills with no public source" % e["id"])

    def test_the_owners_personal_skills_are_not_in_the_exported_json_or_md(self):
        md = open(os.path.join(os.path.dirname(REAL), "skills-registry.md"), encoding="utf-8").read()
        text = open(REAL, encoding="utf-8").read() + md
        for name in ("fiscal-es-fr", "legal-eu-es-fr", "psychologist-analyst", "studio-kickoff"):
            self.assertNotIn(name, text)

    def test_the_overlay_is_merged_only_when_no_registry_file_is_named_and_never_overrides(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "ov.json")
        with open(path, "w") as f:
            json.dump({"entries": [entry("mine"), dict(entry("impeccable"), status="rejected")]}, f)
        merged = sk.load_registry(REAL, overlay=path)
        by_id = {e["id"]: e for e in merged["entries"]}
        self.assertEqual(by_id["mine"]["visibility"], "private")
        self.assertEqual(by_id["impeccable"]["status"], "installed")       # the shipped entry wins
        self.assertNotIn("mine", {e["id"] for e in sk.load_registry(REAL)["entries"]})
