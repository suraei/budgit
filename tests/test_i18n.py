"""R-118 / P1: every word the user reads at run time comes from plugin/scripts/i18n.py, in English and Spanish."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import StudioTestCase, ROOT
import budget
import i18n
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
PLUGIN = os.path.join(ROOT, "plugin")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


class CatalogTest(unittest.TestCase):
    def test_both_languages_have_exactly_the_same_keys(self):
        self.assertEqual(sorted(i18n.CATALOG["en"]), sorted(i18n.CATALOG["es"]))
        self.assertEqual(i18n.LANGS, ("en", "es"))

    def test_no_string_the_user_reads_carries_a_ledger_id(self):
        """An R-n is an id from our own ledger: it means nothing to another user."""
        ids = re.compile(r"\bR-\d+\b")
        for lang, table in i18n.CATALOG.items():
            for key, text in table.items():
                self.assertIsNone(ids.search(text), (lang, key))

    def test_both_languages_use_the_same_placeholders(self):
        for key, en in i18n.CATALOG["en"].items():
            self.assertEqual(sorted(PLACEHOLDER.findall(en)), sorted(PLACEHOLDER.findall(i18n.CATALOG["es"][key])), key)

    def test_the_approved_spanish_phrases_did_not_change(self):
        es = i18n.CATALOG["es"]
        self.assertEqual(es["status.nothing"], "nada pendiente")
        self.assertEqual(es["status.doing"].format(n=2), "2 en curso")
        self.assertEqual(es["brote.say.radiante.a"], "¡Me sobra energía!")
        self.assertEqual(es["brote.say.enfermo.a"], "Me encuentro fatal")
        self.assertEqual(es["brote.say.contento.b"].format(reset="lunes"), "Llegas al lunes.")
        self.assertEqual(es["book.later"], "Para más adelante")
        self.assertEqual(es["cmd.still_on"], "Brote se queda quieto.")

    def test_each_weekday_is_named_in_both_languages(self):
        self.assertEqual(i18n.DAYS["en"][0], "Sunday")
        self.assertEqual(i18n.DAYS["es"][3], "miércoles")
        # Python's tm_wday counts from Monday = 0
        self.assertEqual([i18n.day_name(w, "en") for w in range(7)],
                         ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
        self.assertEqual([i18n.day_name(w, "es") for w in range(7)],
                         ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"])


class DetectTest(unittest.TestCase):
    def test_english_when_nothing_says_otherwise(self):
        self.assertEqual(i18n.detect(env={}, config={}, claude_settings={}), "en")

    def test_order_is_env_then_config_then_claude_code(self):
        self.assertEqual(i18n.detect(env={"BUDGIT_LANG": "es"}, config={"lang": "en"}, claude_settings={"language": "en"}), "es")
        self.assertEqual(i18n.detect(env={}, config={"lang": "es"}, claude_settings={"language": "en"}), "es")
        self.assertEqual(i18n.detect(env={}, config={}, claude_settings={"language": "Spanish"}), "es")
        self.assertEqual(i18n.detect(env={"BUDGIT_LANG": "en"}, config={"lang": "es"}, claude_settings={}), "en")

    def test_language_names_are_understood(self):
        for value in ("es", "ES", "es-ES", "es_MX", "Spanish", "español", "Español (España)", "castellano"):
            self.assertEqual(i18n.normalize(value), "es", value)
        for value in ("en", "English", "en-GB", "Japanese", "fr", "", "   ", None, 7, ["es"]):
            self.assertEqual(i18n.normalize(value), "en", repr(value))

    def test_auto_and_garbage_fall_through_and_never_raise(self):
        self.assertEqual(i18n.detect(env={"BUDGIT_LANG": "auto"}, config={"lang": "auto"}, claude_settings={"language": "Spanish"}), "es")
        self.assertEqual(i18n.detect(env={}, config={"lang": 5}, claude_settings={"language": ["es"]}), "en")
        self.assertEqual(i18n.detect(env={}, config="nope", claude_settings=None), "en")
        with mock.patch.object(i18n, "_default_config", side_effect=RuntimeError("boom")):
            self.assertEqual(i18n.detect(env={}, claude_settings={}), "en")

    def test_reads_the_config_file_and_the_claude_code_settings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home, claude = os.path.join(tmp, "home"), os.path.join(tmp, "claude")
            os.makedirs(home)
            os.makedirs(claude)
            with open(os.path.join(claude, "settings.json"), "w") as f:
                json.dump({"language": "Spanish"}, f)
            clean = {k: v for k, v in os.environ.items() if k not in ("BUDGIT_LANG", "STUDIO_LANG")}
            with mock.patch.dict(os.environ, dict(clean, BUDGIT_HOME=home, CLAUDE_CONFIG_DIR=claude), clear=True):
                self.assertEqual(i18n.detect(), "es")                   # from Claude Code's settings
                with open(os.path.join(home, "config.json"), "w") as f:
                    json.dump({"lang": "en"}, f)
                self.assertEqual(i18n.detect(), "en")                   # config beats Claude Code
                with open(os.path.join(home, "config.json"), "w") as f:
                    f.write("{not json")
                self.assertEqual(i18n.detect(), "es")                   # a broken config is ignored, not fatal


class SystemLanguageTest(unittest.TestCase):
    """R-191 (her hand test): no lang in the config and none in Claude Code's settings used to mean English even on a Spanish Mac."""
    CASES = ["gl-FR", "es-FR", "fr-FR", "es-ES"]                 # her macOS AppleLanguages: gl is not in the catalog, es is

    def detect(self, system, env=None, config=None, claude=None):
        return i18n.detect(env={} if env is None else env, config={} if config is None else config,
                           claude_settings={} if claude is None else claude, system=system)

    def test_the_first_language_the_catalog_has_wins(self):
        self.assertEqual(self.detect(self.CASES), "es")
        self.assertEqual(self.detect(["en-US", "es-ES"]), "en")
        self.assertEqual(self.detect(["fr-FR", "de-DE"]), "en")
        self.assertEqual(self.detect([]), "en")
        self.assertEqual(self.detect(["pt-BR", "Spanish"]), "es")

    def test_everything_explicit_still_beats_the_system(self):
        self.assertEqual(self.detect(self.CASES, env={"BUDGIT_LANG": "en"}), "en")
        self.assertEqual(self.detect(self.CASES, config={"lang": "en"}), "en")
        self.assertEqual(self.detect(self.CASES, claude={"language": "English"}), "en")
        self.assertEqual(self.detect(["en-US"], claude={"language": "Spanish"}), "es")

    def test_unix_variables_when_the_caller_gave_an_env(self):
        self.assertEqual(i18n.detect(env={"LANG": "es_ES.UTF-8"}, config={}, claude_settings={}), "es")
        self.assertEqual(i18n.detect(env={"LC_ALL": "es_MX.UTF-8", "LANG": "en_US.UTF-8"}, config={}, claude_settings={}), "es")
        self.assertEqual(i18n.detect(env={"LANGUAGE": "gl:es:en", "LANG": "en_US.UTF-8"}, config={}, claude_settings={}), "es")
        for plain in ("C", "POSIX", "C.UTF-8", ""):
            self.assertEqual(i18n.detect(env={"LANG": plain}, config={}, claude_settings={}), "en", plain)

    def test_the_real_mac_preference_file_is_read_with_no_subprocess(self):
        import plistlib, tempfile
        with tempfile.TemporaryDirectory() as home:
            prefs = os.path.join(home, "Library", "Preferences")
            os.makedirs(prefs)
            with open(os.path.join(prefs, ".GlobalPreferences.plist"), "wb") as f:
                plistlib.dump({"AppleLanguages": self.CASES, "other": 1}, f, fmt=plistlib.FMT_BINARY)
            with mock.patch.dict(os.environ, {"HOME": home, "LANG": "en_US.UTF-8"}), \
                 mock.patch.object(i18n, "_default_config", return_value={}), mock.patch.object(i18n, "_default_claude_settings", return_value={}), \
                 mock.patch.dict(os.environ, {"BUDGIT_LANG": ""}):
                self.assertEqual(i18n.detect(), "es")                   # the app's own minimal env says en_US: the Mac's list is asked first
            with mock.patch.dict(os.environ, {"HOME": os.path.join(home, "nothing"), "LANG": "es_ES.UTF-8"}), \
                 mock.patch.object(i18n, "_default_config", return_value={}), mock.patch.object(i18n, "_default_claude_settings", return_value={}), \
                 mock.patch.dict(os.environ, {"BUDGIT_LANG": ""}):
                self.assertEqual(i18n.detect(), "es")                   # no preference file: the variables

    def test_a_broken_preference_file_never_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            prefs = os.path.join(home, "Library", "Preferences")
            os.makedirs(prefs)
            with open(os.path.join(prefs, ".GlobalPreferences.plist"), "wb") as f:
                f.write(b"not a plist")
            with mock.patch.dict(os.environ, {"HOME": home, "LANG": "C", "BUDGIT_LANG": ""}), \
                 mock.patch.object(i18n, "_default_config", return_value={}), mock.patch.object(i18n, "_default_claude_settings", return_value={}):
                self.assertEqual(i18n.detect(), "en")


class LookupTest(unittest.TestCase):
    def test_t_formats_and_names_the_language(self):
        self.assertEqual(i18n.t("status.doing", "en", n=3), "3 in progress")
        self.assertEqual(i18n.t("status.doing", "es", n=3), "3 en curso")

    def test_a_missing_spanish_string_falls_back_to_english(self):
        with mock.patch.dict(i18n.CATALOG["es"]):
            del i18n.CATALOG["es"]["status.nothing"]
            self.assertEqual(i18n.t("status.nothing", "es"), "nothing pending")

    def test_an_unknown_key_is_an_error_not_an_empty_string(self):
        with self.assertRaises(KeyError):
            i18n.t("no.such.key", "en")

    def test_a_missing_value_leaves_its_placeholder_visible(self):
        self.assertEqual(i18n.t("status.doing", "en"), "{n} in progress")

    def test_brote_strings_carry_everything_the_mod_prints(self):
        for lang in i18n.LANGS:
            strings = i18n.brote_strings(lang)
            self.assertEqual(strings["days"], i18n.DAYS[lang])
            for key in ("brote.say.justo.a", "brote.suffix.old", "book.now", "cmd.still_on", "book.button", "cmd.no_project",
                        "cmd.status_unavailable", "cmd.limits_live", "cmd.desc_status", "cmd.desc_brote", "cmd.desc_colour_probe", "cmd.colour_probe"):
                self.assertIn(key, strings, lang + " " + key)
            self.assertNotIn("budget.notice", strings)       # python-only strings stay in python

    def test_the_feed_carries_only_the_cmd_words_the_mod_reads(self):
        # the live feed has a 32 KB cap: terminal-only cmd.* texts (migrate, colour, update...) stay in python
        read = set()
        for path in ("plugin/hooks/register.jsx", "plugin/hooks/brote-client.js", "tools/brote/float/say.js",
                     "tools/brote/float/director.js", "tools/brote/float/brote.swift"):
            with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                read |= set(re.findall(r"""['"](cmd\.\w+)['"]""", f.read()))
        self.assertIn("cmd.still_on", read)
        for lang in i18n.LANGS:
            self.assertEqual(sorted(k for k in i18n.brote_strings(lang) if k.startswith("cmd.")), sorted(read), lang)


class RoutedStringsTest(StudioTestCase):
    """The strings the user reads come out in the language that detect() picks."""

    def cli(self, *args, lang=None):
        env = dict(os.environ, BUDGIT_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.pop("BUDGIT_LANG", None)
        for k in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):             # this machine's own language must not leak in (detect() asks the system last)
            env.pop(k, None)
        env["HOME"] = os.path.join(self.tmp, "nohome")
        if lang:
            env["BUDGIT_LANG"] = lang
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)

    def items(self, *statuses):
        self.assertEqual(self.cli("activate", self.project, "--no-bind").returncode, 0)
        data = sl.load_ledger(self.project)
        for n, st in enumerate(statuses, 1):
            data["items"].append({"id": "R-%d" % n, "src": [], "text": "t%d" % n, "status": st, "evidence": "",
                                  "phase": "build", "created": "x"})
        with sl.locked(self.project):
            sl.save_ledger(self.project, data)

    def week(self, used=5, days_ahead=3, five=None):
        args = ["budget", "record", "--seven-day", str(used), "--seven-day-resets", str(time.time() + days_ahead * 86400)]
        if five is not None:
            args += ["--five-hour", str(five), "--five-hour-resets", str(time.time() + 3600)]
        self.assertEqual(self.cli(*args).returncode, 0)

    def line(self, lang):
        r = self.cli("status", "--line", lang=lang)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_the_status_line_defaults_to_english(self):
        self.items()
        self.assertEqual(self.line(None), "proj · no tasks in Budgit")

    def test_the_status_line_in_both_languages(self):
        self.items("doing", "doing", "open")
        self.assertEqual(self.line("en"), "proj · tasks: \u273f 2 in progress \u2058 1 to do \u2727 0 ideas")
        self.assertEqual(self.line("es"), "proj · tareas: \u273f 2 en curso \u2058 1 por hacer \u2727 0 ideas")
        self.week(5, 3)
        self.assertEqual(self.line("en"), "proj · tasks: \u273f 2 in progress \u2058 1 to do \u2727 0 ideas \u250a \u25b1\u25b1\u25b1\u25b1\u25b1\u25b1 5 % \u00b7 you make it")
        self.assertEqual(self.line("es"), "proj · tareas: \u273f 2 en curso \u2058 1 por hacer \u2727 0 ideas \u250a \u25b1\u25b1\u25b1\u25b1\u25b1\u25b1 5 % \u00b7 llegas")

    def test_the_run_out_day_and_hour_follow_the_language(self):
        self.items("doing")
        self.week(90, 1)
        en, es = self.line("en"), self.line("es")
        self.assertRegex(en, r"\u00b7 ends (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2}h$")
        self.assertRegex(es, r"\u00b7 fin (lun|mar|mi\u00e9|jue|vie|s\u00e1b|dom) \d{2}h$")

    def test_the_longest_lines_still_fit_80_columns(self):
        self.items(*(["doing"] * 12 + ["open"] * 144))
        self.week(100, 3, five=95)
        for lang in i18n.LANGS:
            line = self.line(lang)
            self.assertLessEqual(len(line), 80, line)
            self.assertNotIn("5 h", line)

    def test_brote_json_carries_the_language_and_the_strings(self):
        self.items("doing")
        for lang in i18n.LANGS:
            r = self.cli("status", "--brote", lang=lang)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["lang"], lang)
            self.assertEqual(out["t"], i18n.brote_strings(lang))

    def test_the_budget_notice_follows_the_language(self):
        budget.set_override("tight")
        with mock.patch.dict(os.environ, {"BUDGIT_LANG": "en"}):
            msg, _ctx = budget.notice("sess-a")
        self.assertIn("budget TIGHT", msg)
        self.assertIn("<ample|normal|tight|critical> / auto", msg)
        with mock.patch.dict(os.environ, {"BUDGIT_LANG": "es"}):
            msg, _ctx = budget.notice("sess-b")
        self.assertIn("presupuesto AJUSTADO", msg)
        self.assertIn("<holgado|normal|ajustado|critico> / auto", msg)

    def test_budget_set_takes_english_and_spanish_names(self):
        for name, level in (("tight", "tight"), ("AJUSTADO", "tight"), ("holgado", "ample"), ("ample", "ample"),
                            ("crítico", "critical"), ("critico", "critical"), ("critical", "critical"), ("normal", "normal")):
            budget.set_override(name)
            self.assertEqual(budget.load()["override"], level, name)
        with self.assertRaises(sl.StudioError) as caught:
            budget.set_override("sideways")
        self.assertIn("tight", str(caught.exception))
        self.assertIn("ajustado", str(caught.exception))

    def test_the_context_nudge_follows_the_language(self):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"id": "m1", "usage": {
                "input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 348998, "output_tokens": 5}}}) + "\n")
        for lang, word in (("en", "context ~350k tokens"), ("es", "contexto ~350k tokens")):
            env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
            r = subprocess.run([sys.executable, HOOK, "user-prompt-submit"], env=env, capture_output=True, text=True,
                               input=json.dumps({"session_id": "u-" + lang, "prompt": "x", "transcript_path": path}))
            self.assertIn(word, json.loads(r.stdout)["systemMessage"], lang)

    def test_lang_command_shows_sets_and_clears_the_choice(self):
        shown = self.cli("lang", lang="es")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Idioma: es", shown.stdout)
        self.assertIn("Language: en", self.cli("lang", lang="en").stdout)
        saved = self.cli("lang", "es")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(json.load(open(os.path.join(self.home, "config.json")))["lang"], "es")
        self.items()
        self.assertEqual(self.line(None), "proj · sin tareas en Budgit")
        self.assertEqual(self.cli("lang", "auto").returncode, 0)
        self.assertEqual(self.line(None), "proj · no tasks in Budgit")
        bad = self.cli("lang", "klingon")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("klingon", bad.stderr)


class HygieneTest(unittest.TestCase):
    def test_spanish_text_lives_only_in_the_catalog(self):
        phrases = set()
        for key, es in i18n.CATALOG["es"].items():
            if es == i18n.CATALOG["en"][key]:
                continue
            body = PLACEHOLDER.sub("", es).strip(" ·,.:;!¡")
            if len(body) >= 8:
                phrases.add(body)
        phrases |= {d for d in i18n.DAYS["es"] if d not in i18n.DAYS["en"]} - {"martes", "sábado", "domingo"}
        files = []
        for base, dirs, names in os.walk(PLUGIN):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules") and os.path.join(base, d) != os.path.join(PLUGIN, "float")]   # the prebuilt window carries the catalog's menu words on purpose (test_float_bundle proves it matches its sources)
            files += [os.path.join(base, n) for n in names if n.endswith((".py", ".jsx", ".js", ".json", ".sh")) or n == "budgit"]
        self.assertGreater(len(files), 20)
        for path in files:
            if os.path.basename(path) in ("i18n.py", "floatctl.py"):      # floatctl keeps its own en/es table: it is also run alone, without the catalog
                continue
            text = open(path, encoding="utf-8", errors="replace").read()
            for phrase in phrases:
                if phrase in text:
                    self.fail("%s still carries %r outside the catalog" % (os.path.relpath(path, ROOT), phrase))

    @unittest.skipUnless(os.path.exists("/usr/bin/python3"), "no /usr/bin/python3")
    def test_i18n_compiles_under_the_system_python(self):
        r = subprocess.run(["/usr/bin/python3", "-c", "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')",
                            os.path.join(PLUGIN, "scripts", "i18n.py")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_catalog_adds_little_to_a_status_call(self):
        code = ("import sys,time; sys.path.insert(0, %r); import studiolib; t=time.perf_counter(); import i18n; i18n.detect(); "
                "i18n.t('status.nothing'); print(time.perf_counter()-t)" % os.path.join(PLUGIN, "scripts"))
        spent = []
        for _ in range(5):
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            spent.append(float(r.stdout))
        self.assertLess(sorted(spent)[2], 0.030, spent)      # median under the 30 ms budget (plan [perf])


if __name__ == "__main__":
    unittest.main()
