"""T10: the release 0.12 evidence a person reads (string table, first-run transcript) and the release check."""
import os
import re
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT
import i18n
import vendor

RELEASE = os.path.join(ROOT, "tools", "release")


class ReleaseTest(unittest.TestCase):
    def test_release_check_accepts_the_current_version(self):
        version = vendor.plugin_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+")
        with open(os.path.join(ROOT, "skills", "budgit-kickoff", "CHANGELOG.md"), encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.startswith("- v")]
        self.assertTrue(lines[-1].startswith("- v%s " % version))        # export.release_check reads exactly this
        self.assertTrue(any(ln.startswith("- v0.12.0 ") and "First public release" in ln for ln in lines))


class CatalogTableTest(unittest.TestCase):
    def test_catalog_table_renders_en_and_es_columns(self):
        out = os.path.join(tempfile.mkdtemp(), "t.html")
        r = subprocess.run([sys.executable, os.path.join(RELEASE, "catalog_table.py"), out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("<th>English</th>", page)
        self.assertIn("<th>Español</th>", page)
        for key in i18n.CATALOG["en"]:                              # every key, no group left out
            self.assertIn(">%s<" % key, page, key)
        self.assertIn("Nothing in the ledger yet", page)
        self.assertIn("Todavía no hay nada en el registro", page)
        self.assertIn("Salir de Brote", page)


class FirstRunScriptTest(unittest.TestCase):
    def test_first_run_script_in_temp_home_has_no_traceback_or_owner_path(self):
        r = subprocess.run(["sh", os.path.join(RELEASE, "first_run.sh")], capture_output=True, text=True, timeout=300)
        text = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, text)
        self.assertNotIn("Traceback", text)
        self.assertNotIn("/Users/", text)
        self.assertNotRegex(text, r"/(?:private/)?var/folders")
        for step in ("$ budgit hub", "$ budgit setup", "$ budgit new demo", "$ budgit ledger add", "$ budgit budget", "$ budgit comply"):
            self.assertIn(step, text)
        self.assertIn('budgit ledger add --phase mvp --appetite small --area main --text "what you want built"', text)    # the empty ledger names the next command
        self.assertNotIn("MVP: not defined", text)                  # an empty project is not told to fix an MVP of nothing

    def test_the_script_names_no_language_when_asked_for_spanish(self):
        r = subprocess.run(["sh", os.path.join(RELEASE, "first_run.sh"), "es"], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Todavía no hay nada en el registro", r.stdout)
