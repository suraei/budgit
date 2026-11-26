import contextlib, io, os
from helpers import StudioTestCase
import studiolib as sl
import i18n

OLD = "bf310fee" + "a" * 32          # the stale sha (full)
NEW = "3d5f69d2" + "b" * 32          # the sha the evidence names (full)


class StaleVerifyTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.data = sl.load_ledger(self.project)
        sl.add_item(self.data, "x", ["I-1"])
        self.dirs = sl.verify_dirs(self.project)
        for d in self.dirs:
            os.makedirs(d, exist_ok=True)

    def report(self, which, verdict, sha):
        path = os.path.join(self.dirs[which], "R-1.md")
        with open(path, "w") as f:
            f.write("%s\nchecks\n%s\n" % (verdict, sha))
        sl.write_seal(path)

    def verify(self, sha, lang="en"):
        out = io.StringIO()
        old = os.environ.get("BUDGIT_LANG")
        os.environ["BUDGIT_LANG"] = lang
        try:
            with contextlib.redirect_stdout(out):
                sl.set_status(self.project, self.data, "R-1", "verified", evidence="%s test_x" % sha)
        finally:
            if old is None:
                os.environ.pop("BUDGIT_LANG", None)
            else:
                os.environ["BUDGIT_LANG"] = old
        return out.getvalue()

    def test_matching_pass_with_stale_fail_is_verified_and_names_stale(self):
        self.report(0, "FAIL", OLD)
        self.report(1, "PASS", NEW)
        out = self.verify(NEW[:8])
        self.assertEqual(self.data["items"][0]["status"], "verified")
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn(OLD[:8], out)
        self.assertNotIn("R-1", out)

    def test_stale_line_in_en_and_es_without_ledger_id(self):
        for lang in ("en", "es"):
            self.assertIn("{sha}", i18n.CATALOG[lang]["verify.stale_report"])
            self.assertNotIn("R-", i18n.t("verify.stale_report", lang, folder=".studio", sha="abc1234"))
        self.assertNotEqual(i18n.CATALOG["en"]["verify.stale_report"], i18n.CATALOG["es"]["verify.stale_report"])

    def test_short_report_sha_matches_full_evidence_sha(self):
        self.report(0, "FAIL", OLD[:8])
        self.report(1, "PASS", NEW[:8])
        self.verify(NEW)
        self.assertEqual(self.data["items"][0]["status"], "verified")

    def test_matching_pass_and_matching_fail_still_refused(self):
        self.report(0, "FAIL", NEW)
        self.report(1, "PASS", NEW)
        with self.assertRaises(sl.StudioError) as cm:
            self.verify(NEW)
        self.assertIn("verify reports disagree", str(cm.exception))

    def test_no_matching_report_refused_with_existing_message(self):
        self.report(0, "FAIL", OLD)
        self.report(1, "PASS", OLD)
        with self.assertRaises(sl.StudioError) as cm:
            self.verify(NEW)
        self.assertIn("verify reports disagree", str(cm.exception))

    def test_short_or_non_hex_line3_never_matches(self):
        for bad in ("a", "abcdef", "0", "zzzzzzzz"):
            self.report(0, "PASS", bad)
            path = os.path.join(self.dirs[0], "R-1.md")
            self.assertFalse(sl._report_sha_matches(path, ["abcdef1234567"]), bad)
            self.assertFalse(sl._report_sha_matches(path, ["0"]), bad)

    def test_short_evidence_sha_never_matches(self):
        self.report(0, "PASS", "abcdef1234567")
        path = os.path.join(self.dirs[0], "R-1.md")
        self.assertFalse(sl._report_sha_matches(path, ["a"]))
        self.assertFalse(sl._report_sha_matches(path, ["abcdef"]))

    def test_uppercase_seven_char_line3_matches(self):
        self.report(0, "PASS", "ABCDEF1")
        path = os.path.join(self.dirs[0], "R-1.md")
        self.assertTrue(sl._report_sha_matches(path, ["abcdef1234567"]))

    def test_two_pass_reports_for_wrong_sha_refused(self):
        self.report(0, "PASS", OLD)
        self.report(1, "PASS", OLD)
        with self.assertRaises(sl.StudioError):
            self.verify(NEW)
        self.assertNotEqual(self.data["items"][0]["status"], "verified")

    def test_single_report_behaves_like_today(self):
        self.report(1, "PASS", OLD)
        out = self.verify(NEW)
        self.assertEqual(out, "")
        self.assertEqual(self.data["items"][0]["status"], "verified")
