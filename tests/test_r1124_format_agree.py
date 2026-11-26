"""The two copies of the verify-file format (plugin verifyfile.check and the legal skill's read_verify) must agree on pass or fail."""
import datetime
import importlib.util
import json
import os
import tempfile
import time
import unittest

import verifyfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "legalcheck_under_test", os.path.join(ROOT, "skills", "legal-eu-es-fr", "scripts", "legalcheck.py"))
legalcheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legalcheck)


class FormatAgree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.project, ".budgit", "verify"))
        self.pop = os.path.join(self.project, "pop.csv")
        with open(self.pop, "w", encoding="utf-8") as f:
            f.write("id,amount\n" + "".join("%d,10\n" % i for i in range(5)))
        self.vpath = os.path.join(self.project, ".budgit", "verify", "case.md")

    def build(self, first="PASS", reviewer="reviewer: other-session", nm="5 of 5", rows=5, findings=None, age_days=0):
        lines = [first]
        if reviewer:
            lines.append(reviewer)
        lines += [nm, "population: %s rows=%d" % (self.pop, rows)]
        with open(self.vpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        if findings is not None:
            with open(os.path.splitext(self.vpath)[0] + ".findings.jsonl", "w", encoding="utf-8") as f:
                f.write(findings)
        if age_days:
            when = time.time() - age_days * 86400
            os.utime(self.vpath, (when, when))

    def both(self):
        plugin_ok, why = verifyfile.check(self.vpath, self.project, "this-session")
        probs, _n, _m = legalcheck.read_verify(self.vpath, self.pop, 5, datetime.date.today())
        return plugin_ok, not probs, why, probs

    def agree(self, expect):
        a, b, why, probs = self.both()
        self.assertEqual(a, b, "plugin=%s (%s) legal=%s (%s)" % (a, why, b, probs))
        self.assertEqual(a, expect, "%s %s" % (why, probs))

    def test_good_file(self):
        self.build()
        self.agree(True)

    def test_four_of_five(self):
        self.build(nm="4 of 5")
        self.agree(False)

    def test_fail_first_line(self):
        self.build(first="FAIL")
        self.agree(False)

    def test_no_reviewer(self):
        self.build(reviewer=None)
        self.agree(False)

    def test_rows_wrong(self):
        self.build(rows=4)
        self.agree(False)

    def test_open_finding(self):
        self.build(findings=json.dumps({"id": "f1", "item": "1", "text": "t", "status": "open", "evidence": ""}) + "\n")
        self.agree(False)

    def test_closed_finding_empty_evidence(self):
        self.build(findings=json.dumps({"id": "f1", "item": "1", "text": "t", "status": "closed", "evidence": ""}) + "\n")
        self.agree(False)

    def test_findings_row_does_not_parse(self):
        self.build(findings="this is not json\n")
        self.agree(False)

    def test_yesterday_mtime(self):
        self.build(age_days=1)
        self.agree(False)


if __name__ == "__main__":
    unittest.main()
