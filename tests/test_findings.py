"""R-1213 part 2: structured findings on ledger items."""
import json
import unittest

from helpers import StudioTestCase
import studiolib as sl
import findings
import runtrack

T0 = "2026-11-18T10:00:00.000Z"
T1 = "2026-11-18T10:05:00.000Z"
T2 = "2026-11-18T10:10:00.000Z"
T3 = "2026-11-18T10:20:00.000Z"
VERIFIER = "studio:verifier"


class FindingsCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s-pm")
        data = sl.load_ledger(self.project)
        self.a = sl.add_item(data, "uno", ["legacy"])["id"]
        self.b = sl.add_item(data, "dos", ["legacy"])["id"]
        sl.save_ledger(self.project, data)

    def write(self, fn):
        data = sl.load_ledger(self.project)
        out = fn(data)
        sl.save_ledger(self.project, data)
        self.assertFalse(sl.ledger_tampered(self.project))      # the integrity file matches after each write
        return out

    def item(self, item_id=None):
        return sl.find_item(sl.load_ledger(self.project), item_id or self.a)

    def add(self, sev="HIGH", cls="security", text="a hole", at=T1, item_id=None):
        return self.write(lambda d: findings.add(d, item_id or self.a, sev, cls, text, at=at, names=[]))

    def test_round_trip_and_json_counts(self):
        f1 = self.add("HIGH", "security")
        f2 = self.add("LOW", "docs", "typo")
        self.assertEqual((f1, f2), ("F1", "F2"))
        self.write(lambda d: findings.set_state(d, self.a, "F2", "fixed", "abc1234", at=T2))
        self.write(lambda d: findings.set_state(d, self.a, "F1", "deferred", self.b, at=T2))
        self.write(lambda d: findings.set_state(d, self.a, "F1", "open", at=T2))
        found = findings.listing(sl.load_ledger(self.project), self.a, names=[])
        self.assertEqual([r["id"] for r in found], ["F1", "F2"])           # open first
        self.assertEqual(found[1]["state"], "fixed")
        c = findings.counts(found)
        self.assertEqual(c["severity"], {"HIGH": 1, "LOW": 1})
        self.assertEqual(c["class"], {"security": 1, "docs": 1})
        self.assertEqual(c["state"], {"open": 1, "fixed": 1})
        self.assertEqual([e["kind"] for e in self.item()["events"]].count("finding"), 5)
        self.assertTrue(findings.lines(found))

    def test_unknown_class_bad_sha_and_missing_deferred_target_refused(self):
        with self.assertRaises(sl.StudioError) as cm:
            self.add(cls="vibes")
        self.assertIn("correctness", str(cm.exception))
        self.add()
        with self.assertRaises(sl.StudioError):
            self.write(lambda d: findings.set_state(d, self.a, "F1", "fixed", "not-a-sha"))
        with self.assertRaises(sl.StudioError):
            self.write(lambda d: findings.set_state(d, self.a, "F1", "deferred", "R-9999"))
        self.assertEqual(self.item()["findings"][0]["state"], "open")

    def test_done_refused_with_open_high_allowed_with_open_low_and_warns(self):
        self.add("HIGH")
        with self.assertRaises(sl.StudioError) as cm:
            sl.done_item(self.project, sl.load_ledger(self.project), self.a, "decision I-1", no_code=True)
        self.assertIn("HIGH", str(cm.exception))      # locale-independent parts only
        self.assertIn("1", str(cm.exception))
        self.write(lambda d: findings.set_state(d, self.a, "F1", "fixed", "abc1234"))
        self.add("LOW", "ux", "pad")
        warn = findings.done_gate(self.item())
        self.assertIn("1 LOW", warn)
        self.assertIsNone(findings.done_gate(self.item(self.b)))

    def test_hold_without_findings_fails_with_findings_passes_pass_needs_none(self):
        launch = lambda at: runtrack.on_launch(self.project, {"prompt": "# %s\n" % self.a, "subagent_type": VERIFIER,
                                                              "description": "verify"}, at=at, names=[])
        launch(T0)
        it = self.item()
        self.assertIn("HOLD", findings.handback_gap(it, VERIFIER, "HOLD: two problems"))
        self.assertIn("FAIL", findings.handback_gap(it, VERIFIER, "**FAIL**\nx"))
        self.assertIsNone(findings.handback_gap(it, VERIFIER, "PASS\nall good"))
        self.add(at=T1)
        self.assertIsNone(findings.handback_gap(self.item(), VERIFIER, "HOLD: two problems"))

    def test_findings_from_an_earlier_run_do_not_satisfy_a_new_hold(self):
        self.add(at=T0)
        runtrack.on_launch(self.project, {"prompt": "# %s\n" % self.a, "subagent_type": VERIFIER, "description": "round 2"},
                           at=T2, names=[])
        self.assertIsNotNone(findings.handback_gap(self.item(), VERIFIER, "HOLD"))
        runtrack.on_handback(self.project, VERIFIER, "HOLD: again", at=T3, names=[], transcript=self.tr(self.a))
        self.assertIn("note", [e["kind"] for e in self.item()["events"]])
        self.assertFalse(sl.ledger_tampered(self.project))

    def test_handback_without_launch_event_is_skipped_logged_and_writes_nothing(self):
        from unittest import mock
        it = self.item()
        before = json.dumps(it, sort_keys=True)
        with mock.patch.object(sl, "log_error") as log:
            self.assertIsNone(findings.handback_gap(it, VERIFIER, "HOLD: no launch seen"))
        self.assertTrue(log.called)
        self.assertEqual(json.dumps(it, sort_keys=True), before)
        self.assertEqual(json.dumps(self.item(), sort_keys=True), before)

    def repo_sha(self):
        import os
        import subprocess
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x.y", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@x.y", GIT_CONFIG_GLOBAL="/dev/null")
        g = lambda *a: subprocess.run(["git", "-C", self.project, *a], env=env, capture_output=True, text=True, check=True).stdout.strip()
        g("init", "-q")
        g("commit", "-q", "--allow-empty", "-m", "one")
        return g("rev-parse", "HEAD")

    def test_fixed_needs_a_commit_that_exists_in_the_repo(self):
        self.add()
        sha = self.repo_sha()
        with self.assertRaises(sl.StudioError):
            self.write(lambda d: findings.set_state(d, self.a, "F1", "fixed", "deadbeefcafe", project=self.project))
        self.assertEqual(self.item()["findings"][0]["state"], "open")
        self.write(lambda d: findings.set_state(d, self.a, "F1", "fixed", sha[:10], project=self.project))
        self.assertEqual(self.item()["findings"][0]["state"], "fixed")

    def test_guard_refuses_finding_set_and_allows_finding_add_for_a_subagent(self):
        import os
        from unittest import mock
        def chk(c):
            with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                return sl.home_write_check("Bash", {"command": c}, self.project, None, subagent=True)[0]
        got = chk("budgit ledger finding set R-1 F1 fixed abc1234")
        self.assertEqual(got[0], "deny")
        got = chk('budgit ledger finding add R-1 --sev LOW --class docs --text "x"')
        self.assertTrue(got is None or got[0] != "deny", got)

    def test_finding_help_passes_for_subagent_and_verifier_but_set_stays_denied(self):
        import os
        from unittest import mock
        def chk(c, verifier=False):
            agent = sl.VERIFIER if verifier else None
            with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                return sl.home_write_check("Bash", {"command": c}, self.project, agent, subagent=True)[0]
        for verifier in (False, True):
            for cmd in ("budgit ledger finding --help", "budgit ledger finding -h", "budgit ledger finding set --help"):
                got = chk(cmd, verifier)
                self.assertTrue(got is None or got[0] != "deny", (cmd, verifier, got))
        self.assertEqual(chk("budgit ledger finding set R-1 F1 open")[0], "deny")

    def test_verified_and_merge_close_refuse_an_open_high_and_change_nothing(self):
        import os
        import mergeclose
        self.add("HIGH")

        def snap():
            return [(p, open(p, "rb").read()) for p in sorted(
                os.path.join(r, f) for r, _, fs in os.walk(sl.studio_dir(self.project)) for f in fs)]
        before = snap()
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.set_status(self.project, data, self.a, "verified", evidence="x")
        row = {"id": self.a, "commit": "abc1234def", "subject": "x"}
        refused = mergeclose.apply(self.project, data, [row])
        self.assertEqual([r["id"] for r in refused], [self.a])
        self.assertEqual(snap(), before)
        self.assertFalse(sl.ledger_tampered(self.project))

    def test_open_low_only_warns_and_does_not_refuse(self):
        self.add("LOW", "docs", "pad")
        self.assertIn("LOW", findings.done_gate(self.item()))

    def test_an_exception_in_handback_gap_still_books_and_clears_the_claim(self):
        from unittest import mock
        runtrack.on_launch(self.project, {"prompt": "# %s\n" % self.a, "subagent_type": VERIFIER, "description": "v"}, at=T0, names=[])
        with mock.patch.object(findings, "handback_gap", side_effect=RuntimeError("boom")):
            got = runtrack.on_handback(self.project, VERIFIER, "HOLD: x", at=T3, names=[], transcript=self.tr(self.a))
        self.assertEqual(got, [self.a])
        it = self.item()
        self.assertIn("handback", [e["kind"] for e in it["events"]])
        self.assertFalse(it.get("runs"))

    def test_listing_shields_by(self):
        self.write(lambda d: findings.add(d, self.a, "LOW", "docs", "t", at=T1, by="Maria", names=[]))
        found = findings.listing(sl.load_ledger(self.project), self.a, names=["maria"])
        self.assertEqual(found[0]["by"], "private")

    def tr(self, *ids):
        import os
        path = os.path.join(self.tmp, "t-%s.jsonl" % "-".join(ids))
        with open(path, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "# %s\nbrief" % " ".join(ids)}}) + "\n")
        return path


if __name__ == "__main__":
    unittest.main()
