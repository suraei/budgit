"""`budgit merge-close`: work that landed on main closes its item. A commit names an item when its subject starts with
`R-n` (or `R-n, R-m:`), or its body has a `Closes R-n` line. The item closes (verified, with the commit as evidence) only when
it has a PASS report sealed by the verifier; the others are listed with the reason. A dry run unless --apply."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com")


class MergeCloseTest(StudioTestCase):
    def git(self, *args):
        r = subprocess.run(["git", "-C", self.project, *args], env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, message):
        self.n += 1
        with open(os.path.join(self.project, "f%d.txt" % self.n), "w") as f:
            f.write(str(self.n))
        self.git("add", "f%d.txt" % self.n)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "--short", "HEAD")

    def report(self, item_id, first, seal=True):
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        os.makedirs(vdir, exist_ok=True)
        path = os.path.join(vdir, item_id + ".md")
        with open(path, "w") as f:
            f.write(first + "\nevidence\n")
        if seal:
            sl.write_seal(path)

    def setUp(self):
        super().setUp()
        self.n = 0
        self.init_project()
        self.git("init", "-q", "-b", "main")
        data = sl.load_ledger(self.project)
        for n in range(1, 7):
            sl.add_item(data, "item %d" % n, ["legacy"])
        sl.set_status(self.project, data, "R-2", "doing")
        sl.set_status(self.project, data, "R-5", "done", evidence="tests green", outcome="You see it work")
        sl.save_ledger(self.project, data)
        self.sha = {1: self.commit("R-1: first thing"), 23: self.commit("R-2, R-3: the pair"),
                    4: self.commit("R-4: the fourth\n\nmentions R-6 in passing"),
                    0: self.commit("Cleanup that talks about R-6 in the middle"),
                    5: self.commit("Unrelated title\n\nCloses R-5")}
        self.report("R-1", "PASS")
        self.report("R-2", "PASS", seal=False)
        self.report("R-4", "FAIL")
        self.report("R-5", "PASS")
        self.report("R-6", "PASS")

    def run_cli(self, *args, rc=0):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, CLI, "merge-close", *args, "--project", self.project], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, rc, r.stderr)
        self.last_stderr = r.stderr
        return r.stdout

    def status(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def test_a_dry_run_lists_and_writes_nothing(self):
        out = json.loads(self.run_cli("--json"))
        self.assertEqual([c["id"] for c in out["close"]], ["R-1", "R-5"])
        self.assertEqual({c["id"]: c["reason"] for c in out["cannot"]},
                         {"R-2": "the PASS report was not written by budgit:verifier on this machine",
                          "R-3": "no verify report", "R-4": "the verify report is not PASS"})
        self.assertEqual(self.status("R-1")["status"], "open")
        self.assertEqual(self.status("R-5")["status"], "done")

    def test_apply_closes_with_the_commit_as_evidence_and_only_those(self):
        self.run_cli("--apply")
        r1, r5 = self.status("R-1"), self.status("R-5")
        self.assertEqual((r1["status"], r5["status"]), ("verified", "verified"))
        self.assertIn(self.sha[1], r1["evidence"])
        self.assertIn(self.sha[5], r5["evidence"])
        self.assertTrue(r1.get("done_at"))
        for item_id, state in (("R-2", "doing"), ("R-3", "open"), ("R-4", "open"), ("R-6", "open")):
            self.assertEqual(self.status(item_id)["status"], state, item_id)
        again = json.loads(self.run_cli("--json"))
        self.assertEqual(again["close"], [])                      # nothing is closed twice
        self.assertEqual([c["id"] for c in again["cannot"]], ["R-2", "R-3", "R-4"])

    def other_worktree(self):
        """A second checkout of the repository, where a verifier session writes its report."""
        wt = os.path.join(self.tmp, "wt")
        self.git("worktree", "add", "-q", "-b", "side", wt)
        self.addCleanup(subprocess.run, ["git", "-C", self.project, "worktree", "remove", "--force", wt],
                        capture_output=True)
        return wt

    def worktree_report(self, wt, item_id, first, seal=True):
        vdir = os.path.join(sl.studio_dir(wt), "verify")
        os.makedirs(vdir, exist_ok=True)
        path = os.path.join(vdir, item_id + ".md")
        with open(path, "w") as f:
            f.write(first + "\nevidence\n")
        if seal:
            sl.write_seal(path)
        return path

    def test_a_report_sealed_in_another_worktree_is_copied_in_and_sealed_again(self):
        wt = self.other_worktree()
        self.worktree_report(wt, "R-3", "PASS")                        # R-3 has no report in the project
        self.worktree_report(wt, "R-2", "PASS", seal=False)            # copied by hand: no seal, no close
        self.worktree_report(wt, "R-4", "PASS")                        # newer PASS over the project's FAIL
        out = json.loads(self.run_cli("--json"))
        self.assertEqual([c["id"] for c in out["close"]], ["R-1", "R-3", "R-4", "R-5"])
        self.assertTrue(all(c.get("source") for c in out["close"] if c["id"] in ("R-3", "R-4")))
        self.assertEqual({c["id"]: c["reason"] for c in out["cannot"]},
                         {"R-2": "the PASS report was not written by budgit:verifier on this machine"})
        self.run_cli("--apply")
        own = os.path.join(sl.studio_dir(self.project), "verify", "R-3.md")
        self.assertTrue(sl.report_sealed(own))
        self.assertEqual(self.status("R-3")["status"], "verified")
        self.assertIn("copied from the sealed report", self.status("R-3")["evidence"])
        self.assertEqual(self.status("R-2")["status"], "doing")

    def test_a_worktree_report_older_than_the_projects_own_failure_does_not_close(self):
        wt = self.other_worktree()
        path = self.worktree_report(wt, "R-4", "PASS")
        self.report("R-4", "FAIL")                                     # sealed after it: the later seal decides
        os.utime(path, (4102444800, 4102444800))                       # a file time (the year 2100) decides nothing
        out = json.loads(self.run_cli("--json"))
        self.assertEqual({c["id"]: c["reason"] for c in out["cannot"]}["R-4"], "the verify report is not PASS")

    def test_the_copy_is_byte_for_byte_and_a_report_that_is_not_utf8_breaks_nothing(self):
        wt = self.other_worktree()
        path = os.path.join(sl.studio_dir(wt), "verify", "R-3.md")
        os.makedirs(os.path.dirname(path))
        raw = b"PASS\r\nline two\rcr only\n\xff\xfe not utf-8 \x00 no newline at the end"
        with open(path, "wb") as f:
            f.write(raw)
        sl.write_seal(path)
        out = json.loads(self.run_cli("--json"))                       # no crash on the bytes
        self.assertIn("R-3", [c["id"] for c in out["close"]])
        self.run_cli("--apply")
        with open(os.path.join(sl.studio_dir(self.project), "verify", "R-3.md"), "rb") as f:
            self.assertEqual(f.read(), raw)
        self.assertTrue(sl.report_sealed(os.path.join(sl.studio_dir(self.project), "verify", "R-3.md")))

    def test_a_linked_verify_folder_in_a_worktree_lends_nothing(self):
        wt = self.other_worktree()
        elsewhere = os.path.join(self.tmp, "other-project", "verify")
        os.makedirs(elsewhere)
        stolen = os.path.join(elsewhere, "R-3.md")
        with open(stolen, "w") as f:
            f.write("PASS\nsomeone else's report\n")
        sl.write_seal(stolen)
        os.makedirs(sl.studio_dir(wt), exist_ok=True)
        os.symlink(elsewhere, os.path.join(sl.studio_dir(wt), "verify"))
        out = json.loads(self.run_cli("--json"))
        self.assertIn("R-3", {c["id"] for c in out["cannot"]})
        self.assertNotIn("R-3", [c["id"] for c in out["close"]])

    def test_a_worktree_that_is_a_link_to_another_project_lends_nothing(self):
        wt = self.other_worktree()
        other = os.path.join(self.tmp, "other-project")
        stolen = os.path.join(other, os.path.basename(sl.studio_dir(wt)), "verify", "R-3.md")
        os.makedirs(os.path.dirname(stolen))
        with open(stolen, "w") as f:
            f.write("PASS\nsomeone else's report\n")
        sl.write_seal(stolen)
        subprocess.run(["rm", "-rf", wt], check=True)
        os.symlink(other, wt)                                          # git still lists the path
        out = json.loads(self.run_cli("--json"))
        self.assertNotIn("R-3", [c["id"] for c in out["close"]])

    def test_a_failed_copy_is_refused_and_listed_as_not_closed_and_the_batch_goes_on(self):
        import mergeclose
        from unittest import mock
        wt = self.other_worktree()
        self.worktree_report(wt, "R-3", "PASS")
        data = sl.load_ledger(self.project)
        out = mergeclose.plan(self.project, data)
        with mock.patch("tempfile.mkstemp", side_effect=PermissionError("disk says no")):
            refused = mergeclose.apply(self.project, data, out["close"])
        mergeclose.demote(out, refused)
        sl.save_ledger(self.project, data)
        self.assertNotIn("R-3", [c["id"] for c in out["close"]])
        self.assertIn("R-3", [c["id"] for c in out["cannot"]])
        self.assertEqual(self.status("R-3")["status"], "open")
        self.assertEqual(self.status("R-1")["status"], "verified")

    def test_apply_exits_non_zero_when_a_copy_was_refused_and_the_reason_is_plain(self):
        wt = self.other_worktree()
        self.worktree_report(wt, "R-3", "PASS")
        blocker = os.path.join(sl.studio_dir(self.project), "verify", "R-3.md")
        os.makedirs(blocker)                                           # a non-empty folder where the report goes
        with open(os.path.join(blocker, "x"), "w") as f:
            f.write("x")
        out = json.loads(self.run_cli("--apply", "--json", rc=1))
        reason = {c["id"]: c["reason"] for c in out["cannot"]}["R-3"]
        self.assertEqual(reason, "R-3: the report could not be copied (Is a directory)")
        self.assertNotIn("Errno", reason)
        self.assertNotIn(self.tmp, reason)
        self.assertNotIn("Errno", self.last_stderr)
        self.assertEqual(self.status("R-3")["status"], "open")
        self.assertEqual(self.status("R-1")["status"], "verified")      # the batch went on
        self.run_cli("--apply", "--item", "R-1")                        # nothing refused: exit 0

    def test_a_failed_seal_leaves_no_unsealed_copy_and_restores_what_was_there(self):
        import mergeclose
        from unittest import mock
        wt = self.other_worktree()
        self.worktree_report(wt, "R-3", "PASS")                        # no report of its own
        self.worktree_report(wt, "R-4", "PASS")                        # a sealed FAIL of its own under it
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        data = sl.load_ledger(self.project)
        out = mergeclose.plan(self.project, data)
        with open(os.path.join(vdir, "R-4.md"), "rb") as f:
            before = f.read()
        with mock.patch.object(sl, "write_seal", side_effect=OSError(28, "No space left on device")):
            refused = mergeclose.apply(self.project, data, out["close"])
        self.assertEqual({r["id"] for r in refused}, {"R-3", "R-4"})
        self.assertFalse(os.path.exists(os.path.join(vdir, "R-3.md")))
        with open(os.path.join(vdir, "R-4.md"), "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(vdir) if n.endswith(".tmp")], [])

    def test_a_linked_verify_folder_in_the_project_is_not_written_through(self):
        import mergeclose
        wt = self.other_worktree()
        self.worktree_report(wt, "R-3", "PASS")
        data = sl.load_ledger(self.project)
        out = mergeclose.plan(self.project, data)
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.rename(vdir, elsewhere)
        os.symlink(elsewhere, vdir)
        row = [r for r in out["close"] if r["id"] == "R-3"][0]
        with self.assertRaises(sl.StudioError):
            mergeclose._copy_in(self.project, row)
        self.assertFalse(os.path.exists(os.path.join(elsewhere, "R-3.md")))

    def test_a_project_whose_data_folder_is_a_link_still_closes_on_its_own_sealed_report(self):
        import mergeclose
        sdir = sl.studio_dir(self.project)
        elsewhere = os.path.join(self.tmp, "moved-data")
        os.rename(sdir, elsewhere)
        os.symlink(elsewhere, sdir)
        sealed = os.path.join(elsewhere, "verify", "R-1.md")
        sl.write_seal(sealed)                                          # sealed under the path it is read through
        passing, first, _ = mergeclose._own_state(self.project, "R-1")
        self.assertTrue(passing)

    def test_a_report_that_changes_between_the_plan_and_apply_is_not_copied(self):
        wt = self.other_worktree()
        path = self.worktree_report(wt, "R-3", "PASS")
        planned = sl.load_ledger(self.project)
        import mergeclose
        out = mergeclose.plan(self.project, planned)
        self.assertIn("R-3", [c["id"] for c in out["close"]])
        with open(path, "w") as f:
            f.write("PASS\nforged after the seal\n")                    # swapped before apply, never sealed
        mergeclose.apply(self.project, planned, out["close"])
        sl.save_ledger(self.project, planned)
        self.assertEqual(self.status("R-3")["status"], "open")
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(self.project), "verify", "R-3.md")))
        self.assertEqual(self.status("R-1")["status"], "verified")      # the others still close

    def test_a_ref_or_since_that_starts_with_a_dash_is_refused_and_writes_nothing(self):
        planted = os.path.join(self.tmp, "planted.txt")
        data = sl.load_ledger(self.project)
        import mergeclose
        for ref, since in (("--output=" + planted, None), ("main", "--output=" + planted), ("-n1", None)):
            with self.assertRaises(sl.StudioError, msg=repr((ref, since))):
                mergeclose.plan(self.project, data, ref=ref, since=since)
        self.assertFalse(os.path.exists(planted))
        self.run_cli("--ref=--output=" + planted, rc=1)
        self.assertFalse(os.path.exists(planted))

    def test_one_item_and_a_range(self):
        out = json.loads(self.run_cli("--json", "--item", "R-5"))
        self.assertEqual([c["id"] for c in out["close"]], ["R-5"])
        self.assertEqual(out["cannot"], [])
        out = json.loads(self.run_cli("--json", "--since", self.sha[4]))        # only what landed after that commit
        self.assertEqual([c["id"] for c in out["close"]], ["R-5"])
        self.assertEqual(out["cannot"], [])

    def test_the_text_output_names_both_lists(self):
        out = self.run_cli()
        self.assertIn("would close", out)
        self.assertIn("R-1", out)
        self.assertIn("cannot close", out)
        self.assertIn("no verify report", out)
        self.assertIn("--apply", out)
