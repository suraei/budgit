"""R-1345: a project with no repo closes verified items with a checkable proof (path plus hash, or a PASS report).
A project with a repo keeps today's rules: a commit sha and a test name, nothing else."""
import contextlib
import hashlib
import io
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
import studiolib as sl
import i18n

OUTCOME = "You see it work"


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class NoGitCloseTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        self.data = sl.load_ledger(self.project)
        sl.add_item(self.data, "x", ["I-1"])
        self.out = os.path.join(self.project, "notes", "out.txt")
        os.makedirs(os.path.dirname(self.out))
        with open(self.out, "w") as f:
            f.write("the checked result\n")
        self.vdir = sl.verify_dirs(self.project)
        for d in self.vdir:
            os.makedirs(d, exist_ok=True)

    def report(self, which, verdict, body="checks\nnotes\n"):
        path = os.path.join(self.vdir[which], "R-1.md")
        with open(path, "w") as f:
            f.write("%s\n%s" % (verdict, body))
        sl.write_seal(path)
        return path

    def done(self, evidence):
        return sl.done_item(self.project, self.data, "R-1", evidence, outcome=OUTCOME)

    def test_no_git_project_closes_with_report_proof(self):
        path = self.report(0, "PASS")
        item = self.done("report: %s check: the report lists every check" % os.path.relpath(path, os.path.realpath(self.project)))
        self.assertEqual(item["status"], "done")
        self.assertIn(sha256_of(path), item["evidence"])          # checkable again later: path plus hash
        self.assertIn("no repo", item["evidence"])                # the record says the reachability check was skipped

    def test_no_git_project_closes_with_file_hash_proof(self):
        digest = sha256_of(self.out)
        item = self.done("file: notes/out.txt sha256: %s test_thing" % digest)
        self.assertEqual(item["status"], "done")
        self.assertIn(digest, item["evidence"])
        self.assertIn("no repo", item["evidence"])

    def test_no_git_file_hash_mismatch_refused(self):
        digest = sha256_of(self.out)
        with open(self.out, "w") as f:
            f.write("changed after the hash was taken\n")
        with self.assertRaises(sl.StudioError):
            self.done("file: notes/out.txt sha256: %s test_thing" % digest)
        self.assertEqual(self.data["items"][0]["status"], "open")

    def test_no_git_report_not_pass_refused(self):
        path = self.report(0, "FAIL")
        with self.assertRaises(sl.StudioError):
            self.done("report: %s check: looked at it" % os.path.relpath(path, os.path.realpath(self.project)))

    def test_no_git_proof_outside_the_project_refused(self):
        other = os.path.join(self.tmp, "elsewhere.txt")
        with open(other, "w") as f:
            f.write("x")
        with self.assertRaises(sl.StudioError):
            self.done("file: %s sha256: %s test_thing" % (other, sha256_of(other)))

    def test_no_git_file_proof_needs_a_test_or_check(self):
        with self.assertRaises(sl.StudioError):
            self.done("file: notes/out.txt sha256: %s" % sha256_of(self.out))

    def outside(self):
        path = os.path.join(self.tmp, "outside.txt")
        with open(path, "w") as f:
            f.write("outside the project\n")
        return path

    def test_no_git_unsealed_report_refused(self):
        path = os.path.join(self.vdir[0], "R-1.md")
        with open(path, "w") as f:
            f.write("PASS\nwritten by hand, never sealed\n")
        with self.assertRaises(sl.StudioError):
            self.done("report: %s check: looked at it" % os.path.relpath(path, os.path.realpath(self.project)))
        self.assertEqual(self.data["items"][0]["status"], "open")

    def test_no_git_symlink_proof_refused(self):
        outside = self.outside()
        out_link = os.path.join(self.project, "notes", "out_link.txt")
        in_link = os.path.join(self.project, "notes", "in_link.txt")
        os.symlink(outside, out_link)
        os.symlink(self.out, in_link)                              # even a link that stays inside is refused
        for link, target in (("notes/out_link.txt", outside), ("notes/in_link.txt", self.out)):
            with self.assertRaises(sl.StudioError):
                self.done("file: %s sha256: %s test_thing" % (link, sha256_of(target)))
        with self.assertRaises(sl.StudioError):
            self.done("report: notes/out_link.txt check: looked at it")

    def test_no_git_fifo_proof_refused(self):
        fifo = os.path.join(self.project, "notes", "pipe.txt")
        os.mkfifo(fifo)
        start = time.time()
        with self.assertRaises(sl.StudioError):
            self.done("file: notes/pipe.txt sha256: %s test_thing" % ("0" * 64))
        with self.assertRaises(sl.StudioError):
            self.done("report: notes/pipe.txt check: looked at it")
        self.assertLess(time.time() - start, 5)                    # it never blocks on the pipe

    def test_no_git_dotdot_proof_refused(self):
        outside = self.outside()
        digest = sha256_of(outside)
        for raw in ("../outside.txt", "notes/../../outside.txt", "notes/../.."):
            with self.assertRaises(sl.StudioError):
                self.done("file: %s sha256: %s test_thing" % (raw, digest))
        with self.assertRaises(sl.StudioError):
            self.done("report: ../outside.txt check: looked at it")

    def test_no_git_free_text_report_word_does_not_block(self):
        item = self.done("file: notes/out.txt sha256: %s test_thing; the report: all green" % sha256_of(self.out))
        self.assertEqual(item["status"], "done")
        self.assertIsNone(sl.check_done_proof("abcdef1 test_thing the report: fine", self.project))

    def test_no_git_a_very_long_token_returns_fast(self):
        big = "a" * 200000
        digest = "b" * 64
        start = time.time()
        for text in ("file: %s sha256: %s test_x" % (big, digest), "report: %s check: x" % ("/" * 200000), big, "file:" + " " * 200000 + "x"):
            with self.assertRaises(sl.StudioError):
                sl.check_done_proof(text, self.project)
        sl.done_claim_line(self.project, {"id": "R-1", "outcome": "o", "evidence": big + " sha256 " + digest})
        self.assertLess(time.time() - start, 1)

    def test_no_git_cli_done_closes_with_a_sealed_report(self):
        path = self.report(0, "PASS")
        sl.save_ledger(self.project, self.data)
        env = dict(os.environ, STUDIO_HOME=self.home, HOME=self.tmp)
        env.pop("BUDGIT_HOME", None)
        rel = os.path.relpath(path, os.path.realpath(self.project))
        cli = os.path.join(ROOT, "plugin", "bin", "budgit")
        r = subprocess.run([sys.executable, cli, "ledger", "done", "R-1", "--evidence", "report: %s check: the report lists every check" % rel,
                            "--outcome", OUTCOME, "--project", self.project], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        item = sl.find_item(sl.load_ledger(self.project), "R-1")
        self.assertEqual(item["status"], "done")
        self.assertIn(sha256_of(path), item["evidence"])
        self.assertIn("no repo", item["evidence"])

    def test_no_git_verify_stamp_accepts_report_hash(self):
        self.report(0, "FAIL")
        good = self.report(1, "PASS", "checks\nmore\n")
        evidence = "report: %s sha256: %s check: the report lists every check" % (good, sha256_of(good))
        with contextlib.redirect_stdout(io.StringIO()):
            sl.check_done_proof(evidence, self.project)
            sl.set_status(self.project, self.data, "R-1", "verified", evidence=evidence)
        self.assertEqual(self.data["items"][0]["status"], "verified")

    def test_no_git_refusal_keeps_the_old_message(self):
        for text in ("commit 1e2084f3 merged", "abc123 test_x", "test_something passes", ""):
            with self.assertRaises(sl.StudioError) as caught:
                sl.check_done_proof(text, self.project)
            message = str(caught.exception)
            self.assertTrue(message.startswith(sl.DONE_PROOF_MSG), message)      # old callers still match
            self.assertIn("sha256", message)                                     # and the new forms are named

    def test_the_test_name_pattern_still_matches_normal_runners(self):
        for text in ("abcdef1 dashboard.spec.js > renders", "web/app.test.ts renders", "tests/test_x.py::test_it",
                     "test_mod.Case.test_it", "FooTest.bar passed", "ran test_thing"):
            self.assertTrue(sl._PROOF_TEST.search(text), text)
        for text in ("no runner named here", "a" * 5000, "x.spec.pdf"):
            self.assertFalse(sl._PROOF_TEST.search(text), text)

    def test_nogit_messages_in_every_language(self):
        keys = [k for k in i18n.CATALOG["en"] if k.startswith("ledger.nogit_")]
        self.assertTrue(keys)
        for lang in i18n.LANGS:
            for k in keys:
                self.assertIn(k, i18n.CATALOG[lang])
        self.assertIn("sha256", i18n.CATALOG["en"]["ledger.nogit_proof_missing"])
        self.assertNotEqual(i18n.CATALOG["en"]["ledger.nogit_proof_missing"], i18n.CATALOG["es"]["ledger.nogit_proof_missing"])


class GitProjectKeepsItsRulesTest(StudioTestCase):
    def test_git_project_still_needs_a_commit_sha(self):
        self.init_project()
        os.makedirs(os.path.join(self.project, ".git"))           # a repo marker is enough for the detector
        report = os.path.join(self.project, "r.md")
        with open(report, "w") as f:
            f.write("PASS\nchecks\n")
        for text in ("report: r.md check: looked fine",
                     "file: r.md sha256: %s test_thing" % sha256_of(report)):
            with self.assertRaises(sl.StudioError) as caught:
                sl.check_done_proof(text, self.project)
            self.assertEqual(str(caught.exception), sl.DONE_PROOF_MSG)
        self.assertIsNone(sl.check_done_proof("abcdef1 test_thing", self.project))

    def test_detector_sees_a_repo_above_the_project(self):
        self.init_project()
        self.assertFalse(sl.is_git_project(self.project))
        os.makedirs(os.path.join(self.tmp, ".git"))
        self.assertTrue(sl.is_git_project(self.project))
