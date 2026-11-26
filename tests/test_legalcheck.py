"""R-272: the mechanical half of the legal skill (skills/legal-eu-es-fr).
Rule tables are linted, stale rules are listed, a drafted answer cannot be green on an unverified source,
and the weekly diff only reports: it never edits a rule. No network: the fetcher is a fake."""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

from helpers import ROOT

SKILL = os.path.join(ROOT, "skills", "legal-eu-es-fr")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import legalcheck as lc  # noqa: E402

SCRIPT = os.path.join(SKILL, "scripts", "legalcheck.py")
TODAY = dt.date(2026, 10, 26)

HEADER = ("| id | rule | instrument | url | status | checked | review_by | tag |\n"
          "|---|---|---|---|---|---|---|---|\n")
ROW_OK = ("| EU-GDPR-HOUSEHOLD | Purely personal or household activity is outside the GDPR. | GDPR art. 2(2)(c) "
          "| https://eur-lex.europa.eu/eli/reg/2016/679/oj | in force | 2026-10-26 | 2027-04-24 | V |\n")
ROW_OLD = ("| FR-EINV-RECEIVE | Every VAT-registered business must be able to receive e-invoices. | CGI art. 289 bis "
           "| https://www.economie.gouv.fr/facturation | applies from 2026-09-01 | 2026-01-10 | 2026-03-01 | V |\n")
ROW_S = ("| ES-VERIFACTU-DATES | Verifactu applies from 2027-01-01 or 2027-07-01. | RDL 15/2025 "
         "| https://www.boe.es/buscar/act.php?id=BOE-A-2025-24440 | applies from 2027-01-01 | 2026-10-26 | 2026-11-25 | S |\n")


def rules(*rows):
    return lc.parse_rules(HEADER + "".join(rows))


def codes(problems):
    return sorted({p.code for p in problems})


DISCLAIMER = "Orientación general, no asesoramiento jurídico ni fiscal."


def answer(verdict, *cites, extra=""):
    lines = [DISCLAIMER, "", "Veredicto: %s" % verdict, ""] + list(cites) + [extra]
    return "\n".join(lines) + "\n"


class RulesTableTest(unittest.TestCase):
    def test_parses_rows(self):
        rs = rules(ROW_OK, ROW_S)
        self.assertEqual([r.id for r in rs], ["EU-GDPR-HOUSEHOLD", "ES-VERIFACTU-DATES"])
        self.assertEqual(rs[0].checked, dt.date(2026, 10, 26))
        self.assertEqual(rs[1].tag, "S")

    def test_clean_table_lints_clean(self):
        self.assertEqual(lc.lint_rules(rules(ROW_OK, ROW_S)), [])

    def test_duplicate_id(self):
        self.assertIn("duplicate-id", codes(lc.lint_rules(rules(ROW_OK, ROW_OK))))

    def test_bad_date_and_bad_tag(self):
        row = ROW_OK.replace("2026-10-26", "26/10/2026").replace("| V |", "| M |")
        self.assertEqual(codes(lc.lint_rules(rules(row))), ["bad-date", "bad-tag"])

    def test_review_before_checked(self):
        row = ROW_OK.replace("2027-04-24", "2026-01-01")
        self.assertIn("review-before-checked", codes(lc.lint_rules(rules(row))))

    def test_verified_rule_needs_an_official_host(self):
        row = ROW_OK.replace("https://eur-lex.europa.eu/eli/reg/2016/679/oj", "https://www.lexology.com/x")
        self.assertIn("non-official-url", codes(lc.lint_rules(rules(row))))

    def test_lookalike_host_is_not_official(self):
        row = ROW_OK.replace("eur-lex.europa.eu", "eur-lex.europa.eu.example.com")
        self.assertIn("non-official-url", codes(lc.lint_rules(rules(row))))

    def test_r1105_service_public_gouv_hosts_are_official_and_lookalikes_are_not(self):
        for url in ("https://service-public.gouv.fr/x", "https://www.service-public.gouv.fr/x",
                    "https://entreprendre.service-public.gouv.fr/vosdroits/F1", "https://www.service-public.fr/x"):
            self.assertTrue(lc.official(url), url)
        for url in ("https://service-public.gouv.fr.evil.example/x", "https://evilservice-public.gouv.fr/x",
                    "http://service-public.gouv.fr/x"):
            self.assertFalse(lc.official(url), url)

    def test_bad_id(self):
        row = ROW_OK.replace("EU-GDPR-HOUSEHOLD", "gdpr household")
        self.assertIn("bad-id", codes(lc.lint_rules(rules(row))))

    def test_missing_field(self):
        row = ROW_OK.replace("| in force |", "|  |")
        self.assertIn("missing-field", codes(lc.lint_rules(rules(row))))


class StaleTest(unittest.TestCase):
    def test_fresh_only_when_verified_and_not_past_review(self):
        ok, old, sec = rules(ROW_OK, ROW_OLD, ROW_S)
        self.assertTrue(lc.fresh(ok, TODAY))
        self.assertFalse(lc.fresh(old, TODAY))
        self.assertFalse(lc.fresh(sec, TODAY))

    def test_stale_lists_reasons(self):
        out = dict((r.id, why) for r, why in lc.stale(rules(ROW_OK, ROW_OLD, ROW_S), TODAY))
        self.assertEqual(sorted(out), ["ES-VERIFACTU-DATES", "FR-EINV-RECEIVE"])
        self.assertIn("review_by", out["FR-EINV-RECEIVE"])
        self.assertIn("tag S", out["ES-VERIFACTU-DATES"])


class AnswerCheckTest(unittest.TestCase):
    RS = None

    @classmethod
    def setUpClass(cls):
        cls.RS = rules(ROW_OK, ROW_OLD, ROW_S)

    def check(self, text):
        return codes(lc.check_answer(text, self.RS, TODAY))

    def test_green_with_fresh_rule_passes(self):
        self.assertEqual(self.check(answer("VERDE", "- GDPR art. 2(2)(c) [ficha EU-GDPR-HOUSEHOLD]")), [])

    def test_green_with_source_verified_today_passes(self):
        cite = "- https://www.cnil.fr/fr/focus-interet-legitime-collecte-par-moissonnage [verificado hoy 2026-10-26]"
        self.assertEqual(self.check(answer("VERDE", cite)), [])

    def test_unverified_cannot_be_green(self):
        got = self.check(answer("VERDE", "- [ficha EU-GDPR-HOUSEHOLD]", "- LOPDGDD art. 7 [sin verificar]"))
        self.assertEqual(got, ["green-unverified"])

    def test_from_memory_counts_as_unverified(self):
        got = self.check(answer("VERDE", "- [ficha EU-GDPR-HOUSEHOLD]", "- Reglamento 2016/679 art. 5 [de memoria, verificar]"))
        self.assertEqual(got, ["green-unverified"])

    def test_stale_or_secondary_rule_cannot_be_green(self):
        self.assertEqual(self.check(answer("VERDE", "- [ficha FR-EINV-RECEIVE]")), ["green-unverified"])
        self.assertEqual(self.check(answer("VERDE", "- [ficha ES-VERIFACTU-DATES]")), ["green-unverified"])

    def test_verified_on_another_day_is_not_today(self):
        cite = "- https://www.boe.es/buscar/act.php?id=BOE-A-2018-16673 [verificado hoy 2026-10-01]"
        self.assertEqual(self.check(answer("VERDE", cite)), ["green-unverified"])

    def test_amber_may_rest_on_unverified(self):
        self.assertEqual(self.check(answer("ÁMBAR", "- LOPDGDD art. 7 [sin verificar]")), [])

    def test_split_cannot_be_green(self):
        got = self.check(answer("VERDE", "- [ficha EU-GDPR-HOUSEHOLD]", extra="Dividido: ES y FR leen distinto."))
        self.assertEqual(got, ["green-split"])

    def test_green_needs_a_source(self):
        self.assertEqual(self.check(answer("VERDE")), ["green-without-source", "no-citation"])

    def test_missing_disclaimer_and_verdict(self):
        got = self.check("Todo bien.\n- [ficha EU-GDPR-HOUSEHOLD]\n")
        self.assertEqual(got, ["no-disclaimer", "no-verdict"])

    def test_unknown_rule(self):
        self.assertIn("unknown-rule", self.check(answer("ÁMBAR", "- [ficha EU-NOPE]")))

    def test_bare_url_needs_a_tag(self):
        got = self.check(answer("ÁMBAR", "- [ficha EU-GDPR-HOUSEHOLD]", "- ver https://www.boe.es/x"))
        self.assertEqual(got, ["bare-url"])

    def test_verified_tag_on_non_official_host(self):
        cite = "- https://www.lexology.com/library/x [verificado hoy 2026-10-26]"
        self.assertIn("non-official-url", self.check(answer("ÁMBAR", cite)))

    def test_french_and_english_answers(self):
        fr = ("Information générale, pas un conseil juridique ni fiscal.\n\nVerdict : VERT\n\n"
              "- https://www.cnil.fr/fr/x [vérifié aujourd'hui 2026-10-26]\n")
        en = ("General guidance, not legal or tax advice.\n\nVerdict: GREEN\n\n- [rule EU-GDPR-HOUSEHOLD]\n")
        self.assertEqual(self.check(fr), [])
        self.assertEqual(self.check(en), [])
        self.assertEqual(self.check(en.replace("[rule EU-GDPR-HOUSEHOLD]", "[unverified]")), ["green-unverified"])


class FakeFetch:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append(url)
        body = self.pages.get(url)
        if body is None:
            raise OSError("unreachable")
        return body


BOE_URL = "https://www.boe.es/buscar/act.php?id=BOE-A-2025-24440"
EUR_URL = "https://eur-lex.europa.eu/eli/reg/2016/679/oj"
BOE_API = lc.probe(BOE_URL)[0]
EUR_API = lc.probe(EUR_URL)[0]
FEED = "https://www.cnil.fr/fr/rss.xml"


def boe(stamp):
    return '{"status": {"code": "200"}, "data": [{"fecha_actualizacion": "%s", "identificador": "BOE-A-2025-24440"}]}' % stamp


def sparql(*celex):
    rows = ", ".join('{"celex": {"type": "literal", "value": "%s"}}' % c for c in celex)
    return '{"head": {"vars": ["celex"]}, "results": {"bindings": [%s]}}' % rows
RSS = ("<rss><channel><item><title>A</title><link>https://www.cnil.fr/a</link></item>"
       "<item><title>B</title><link>https://www.cnil.fr/b</link></item></channel></rss>")


class ProbeTest(unittest.TestCase):
    def test_boe_goes_to_the_open_data_api(self):
        url, headers, kind = lc.probe("https://www.boe.es/buscar/act.php?id=BOE-A-2018-16673")
        self.assertEqual(url, "https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/BOE-A-2018-16673/metadatos")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(kind, "boe")

    def test_eurlex_goes_to_cellar_sparql(self):
        for src, celex in (("https://eur-lex.europa.eu/eli/reg/2016/679/oj", "32016R0679"),
                           ("https://eur-lex.europa.eu/eli/dir/1996/9/oj", "31996L0009"),
                           ("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32019L0790", "32019L0790")):
            url, _h, kind = lc.probe(src)
            self.assertTrue(url.startswith("https://publications.europa.eu/webapi/rdf/sparql?"), src)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["query"][0]
            # anchored on the act itself: a prefix scan over every CELEX took 40-50 s live and timed out
            self.assertIn('"%s"^^xsd:string' % celex, query)
            self.assertIn("act_consolidated_based_on_resource_legal", query)
            self.assertNotIn("STRSTARTS", query)
            self.assertEqual(kind, "eu-consolidated")

    def test_case_law_is_checked_for_existence(self):
        url, _h, kind = lc.probe("https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:62014CJ0030")
        self.assertEqual(kind, "eu-exists")
        self.assertEqual(lc.marker_for(kind, url, sparql("62014CJ0030")), "exists")

    def test_markers(self):
        self.assertEqual(lc.marker_for("boe", "", boe("20261008T073957Z")), "date:20261008T073957Z")
        self.assertEqual(lc.marker_for("eu-consolidated", "", sparql("02024R1689-20240712", "02024R1689-20260727")),
                         "consolidated:02024R1689-20260727")
        self.assertEqual(lc.marker_for("eu-consolidated", "", sparql()), "consolidated:none")
        with self.assertRaises(ValueError):
            lc.marker_for("boe", "", "<html>error</html>")


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.rs = rules(ROW_OK, ROW_S)
        self.snapshot = lc.empty_snapshot()

    def run_diff(self, pages, feeds=(FEED,)):
        return lc.diff(self.rs, list(feeds), self.snapshot, FakeFetch(pages), TODAY)

    def test_first_run_sets_baselines_and_reports_new(self):
        rep = self.run_diff({BOE_API: boe("20251203T000000Z"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        self.assertEqual(sorted(e["status"] for e in rep["sources"]), ["new", "new"])
        self.assertEqual(len(rep["feed_items"]), 2)
        self.assertIn(BOE_URL, self.snapshot["sources"])
        self.assertEqual(self.snapshot["last_run"], "2026-10-26")

    def test_changed_source_is_reported_and_baseline_kept(self):
        self.run_diff({BOE_API: boe("20251203T000000Z"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        before = dict(self.snapshot["sources"][BOE_URL])
        rep = self.run_diff({BOE_API: boe("20261008T073957Z"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        status = {e["url"]: e["status"] for e in rep["sources"]}
        self.assertEqual(status[BOE_URL], "changed")
        self.assertEqual(status[EUR_URL], "same")
        self.assertEqual(self.snapshot["sources"][BOE_URL]["marker"], before["marker"])   # waits for her yes
        changed = [e for e in rep["sources"] if e["status"] == "changed"][0]
        self.assertEqual(changed["rules"], ["ES-VERIFACTU-DATES"])
        self.assertEqual(rep["feed_items"], [])   # already seen

    def test_accept_moves_the_baseline(self):
        self.run_diff({BOE_API: boe("20251203T000000Z"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        self.run_diff({BOE_API: boe("20261008T073957Z"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        done = lc.accept(self.snapshot, ["ES-VERIFACTU-DATES"], self.rs)
        self.assertEqual(done, [BOE_URL])
        rep = self.run_diff({BOE_API: boe("20261008T073957Z"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        self.assertEqual({e["status"] for e in rep["sources"]}, {"same"})

    def test_unreachable_is_reported_not_fatal(self):
        rep = self.run_diff({EUR_API: sparql("02016R0679-20160504")})
        status = {e["url"]: e["status"] for e in rep["sources"]}
        self.assertEqual(status[BOE_URL], "unreachable")
        self.assertEqual(rep["feed_errors"], [FEED])

    def test_markup_and_scripts_do_not_count_as_change(self):
        url = "https://www.cnil.fr/fr/x"
        self.assertEqual(lc.marker(url, "<p>v1</p><script>t=1</script>"),
                         lc.marker(url, "<div>  v1 </div><script>t=2</script>"))

    def test_new_eu_consolidation_is_a_change(self):
        self.run_diff({BOE_API: boe("1"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        rep = self.run_diff({BOE_API: boe("1"), EUR_API: sparql("02016R0679-20160504", "02016R0679-20270101"), FEED: RSS})
        status = {e["url"]: e["status"] for e in rep["sources"]}
        self.assertEqual(status[EUR_URL], "changed")

    def test_legifrance_is_manual(self):
        lf = "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041464195"
        self.rs = rules(ROW_OK.replace("https://eur-lex.europa.eu/eli/reg/2016/679/oj", lf))
        fetch = FakeFetch({})
        rep = lc.diff(self.rs, [], self.snapshot, fetch, TODAY)
        self.assertTrue(rep["sources"][0]["status"].startswith("manual"))
        self.assertEqual(fetch.calls, [])

    def test_date_markers(self):
        boe = "<p>Última actualización publicada el 03/12/2025</p><p>random 1</p>"
        self.assertEqual(lc.marker(BOE_URL, boe), "date:03/12/2025")
        lf = "<span>Version en vigueur depuis le 01 janvier 2026</span>"
        self.assertEqual(lc.marker("https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI1", lf),
                         "date:01 janvier 2026")

    def test_report_lists_stale_rules_and_never_touches_rules(self):
        rep = self.run_diff({BOE_API: boe("20251203T000000Z"), EUR_API: sparql("02016R0679-20160504"), FEED: RSS})
        self.assertEqual([s["id"] for s in rep["stale"]], ["ES-VERIFACTU-DATES"])
        text = lc.render_report(rep)
        self.assertIn("ES-VERIFACTU-DATES", text)
        self.assertIn("no rule was edited", text.lower())

    def test_due_after_seven_days(self):
        self.assertTrue(lc.due(self.snapshot, TODAY))
        self.snapshot["last_run"] = "2026-10-20"
        self.assertFalse(lc.due(self.snapshot, TODAY))
        self.snapshot["last_run"] = "2026-10-19"
        self.assertTrue(lc.due(self.snapshot, TODAY))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class CliTest(unittest.TestCase):
    def run_cli(self, *args, home=None):
        env = dict(os.environ, LEGAL_HOME=home or tempfile.mkdtemp())
        return subprocess.run([sys.executable, "-I", SCRIPT] + list(args), capture_output=True, text=True, env=env)

    def test_shipped_rules_lint_clean(self):
        r = self.run_cli("lint")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_answer_command_exit_codes(self):
        d = tempfile.mkdtemp()
        bad = os.path.join(d, "a.md")
        with open(bad, "w", encoding="utf-8") as f:
            f.write(answer("VERDE", "- LOPDGDD art. 7 [sin verificar]"))
        r = self.run_cli("answer", bad, "--today", "2026-10-26")
        self.assertEqual(r.returncode, 1)
        self.assertIn("green-unverified", r.stdout)

    def test_diff_offline_writes_nothing_to_rules(self):
        home = tempfile.mkdtemp()
        rules_dir = os.path.join(SKILL, "rules")
        before = {n: read(os.path.join(rules_dir, n)) for n in os.listdir(rules_dir)}
        r = self.run_cli("diff", "--offline", "--today", "2026-10-26", home=home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = {n: read(os.path.join(rules_dir, n)) for n in os.listdir(rules_dir)}
        self.assertEqual(before, after)
        with open(os.path.join(home, "snapshot.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["sources"], {})   # offline: nothing fetched, no baseline invented


class FetchTest(unittest.TestCase):
    def test_refuses_a_non_official_host(self):
        with self.assertRaises(ValueError):
            lc.http_fetch("https://example.com/")

    def test_a_redirect_may_not_leave_the_official_hosts(self):
        import urllib.request
        handler = lc._OfficialRedirects()
        req = urllib.request.Request("https://www.boe.es/a")
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/b")
        self.assertIsNotNone(handler.redirect_request(req, None, 302, "Found", {}, "https://www.boe.es/b"))


class CasesTest(unittest.TestCase):
    """Her seven situations (cases/*.md): each model answer passes the checker against the shipped rules."""

    def test_every_case_answer_passes_the_checker(self):
        rules = lc.load_rules()
        files = sorted(f for f in os.listdir(os.path.join(SKILL, "cases")) if f.endswith(".md"))
        self.assertEqual(len(files), 7)
        for f in files:
            with open(os.path.join(SKILL, "cases", f), encoding="utf-8") as fh:
                head, sep, answer = fh.read().partition("## Respuesta modelo")
            self.assertTrue(sep, f)
            self.assertIn("Golden checks:", head, f)
            # R-1125 #9: with no profile person type, a restricted row only adds the soft AMBER note "ask the person type"
            self.assertEqual([p.code for p in lc.hard_problems(lc.check_answer(answer, rules, TODAY))], [], f)

    def test_high_stakes_cases_are_not_green(self):
        for f, verdict in (("4-crypto.md", "ÁMBAR"), ("5-budgit-licences.md", "ROJO"), ("6-onphone-osint.md", "ROJO")):
            with open(os.path.join(SKILL, "cases", f), encoding="utf-8") as fh:
                self.assertIn("Veredicto: " + verdict, fh.read(), f)


class BundleTest(unittest.TestCase):
    def test_skill_ships_with_cases_and_registry_entry_left_out(self):
        """R-741 (her «Sí, en Budgit»): the skill ships in the public export; cases/ and registry-entry.json stay in the hub."""
        sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
        import export
        self.assertIn(os.path.join("skills", "legal-eu-es-fr"), export.SHIPPED_DIRS)
        self.assertEqual(export.SKILL_OMIT["legal-eu-es-fr"], ("cases", "registry-entry.json"))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "plugin", "scripts", "legalcheck.py")))

    def test_no_agpl_or_nc_text_in_the_skill(self):
        for base, _dirs, files in os.walk(SKILL):
            for name in files:
                if not name.endswith((".md", ".py", ".json")):
                    continue
                with open(os.path.join(base, name), encoding="utf-8") as f:
                    text = f.read()
                self.assertNotIn("GNU AFFERO GENERAL PUBLIC LICENSE\n", text, name)
                self.assertNotIn("Attribution-NonCommercial-ShareAlike 4.0 International Public License", text, name)


if __name__ == "__main__":
    unittest.main()
