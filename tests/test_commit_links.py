"""R-362 (gap G8): the ledger and git are linked without the hook writing anything.

* `budgit release-check` works out the item<->commit links itself from `git log main`, and fails when an item named by a main
  commit that says fix/build/merge is still open or in progress more than a day later, unless it is `later` with a reason.
* The `commit-msg` hook only warns when the subject names no `R-n`; it never writes the ledger (a worktree holds a stale copy).
Every test builds its own temporary repository."""
import os
import subprocess
import sys
import time
from unittest import mock

from helpers import StudioTestCase, ROOT
import ledgergraph as lg
import studiolib as sl
import watermark

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.org"}
DAY = 86400


class RepoBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.init_project()
        sl.activate(self.project, "s1")

    def git(self, *args, days_ago=0, check=True):
        when = "%d +0000" % (time.time() - days_ago * DAY)
        env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        r = subprocess.run(["git", "-C", self.project, *args], capture_output=True, text=True, env=env)
        if check:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def repo(self, branch="main"):
        self.git("init", "-q", "-b", branch)
        self.git("commit", "-q", "--allow-empty", "-m", "base", days_ago=90)

    def commit(self, message, days_ago=0):
        self.git("commit", "-q", "--allow-empty", "-m", message, days_ago=days_ago)

    def items(self, *specs):
        """specs: (text, status[, phase[, reason]]). Returns the ids."""
        data = sl.load_ledger(self.project)
        ids = []
        for text, status, *rest in specs:
            it = sl.add_item(data, text, ["I-1"])
            it["status"] = status
            if status in ("done", "verified"):
                it["evidence"] = "ran: ok"
            if rest:
                sl.set_phase(data, [it["id"]], rest[0], reason=rest[1] if len(rest) > 1 else None)
            ids.append(it["id"])
        sl.save_ledger(self.project, data)
        return ids

    def drift(self, ids=None, **kw):
        data = sl.load_ledger(self.project)
        return lg.drift(self.project, sl.release_scope(data, ids), **kw)

    def drifted(self, **kw):
        return [i for i, _sha, _subject in self.drift(**kw)["drift"]]

    def cli(self, *args, lang="en"):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args, "--project", self.project], cwd=self.tmp, env=env, capture_output=True, text=True)

    def check(self, *args, lang="en"):
        return self.cli("release-check", *args, lang=lang)

    def short_head(self):
        return self.git("rev-parse", "--short=8", "HEAD").stdout.strip()


class DriftTest(RepoBase):
    def test_an_open_item_named_by_an_old_fix_commit_drifts_with_its_commit(self):
        a, = self.items(("built but never closed", "open"))
        self.repo()
        self.commit("%s: fix the thing" % a, days_ago=3)
        self.assertEqual(self.drift()["drift"], [(a, self.short_head(), "%s: fix the thing" % a)])

    def test_a_git_timeout_is_its_own_answer_and_counts_the_items_left_unchecked(self):
        a, = self.items(("parked without a reason", "open", "later"))
        self.repo()
        with mock.patch.object(lg.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 20)):
            d = self.drift()
        self.assertEqual((d["git"], d["candidates"], d["drift"]), ("timed-out", 1, []))

    def test_an_item_in_progress_drifts_too(self):
        a, = self.items(("being worked", "doing"))
        self.repo()
        self.commit("%s: build it" % a, days_ago=2)
        self.assertEqual(self.drifted(), [a])

    def test_a_commit_younger_than_a_day_gives_the_item_time(self):
        a, = self.items(("just fixed", "open"))
        self.repo()
        self.commit("%s: fix it" % a, days_ago=0.5)
        self.assertEqual(self.drifted(), [])

    def test_only_a_subject_that_says_fix_build_or_merge_counts(self):
        a, b, c, d = self.items(("one", "open"), ("two", "open"), ("three", "open"), ("four", "open"))
        self.repo()
        self.commit("%s: half of it" % a, days_ago=3)
        self.commit("%s: fixture for the tests" % b, days_ago=3)         # words that merely contain "fix"
        self.commit("%s: prefix the names" % c, days_ago=3)
        self.commit("%s: refactor, builder pattern" % d, days_ago=3)
        self.assertEqual(self.drifted(), [])

    def test_every_spelling_of_the_three_verbs_counts(self):
        ids = self.items(*[("item %d" % n, "open") for n in range(6)])
        self.repo()
        for i, subject in zip(ids, ["%s fixes the bug", "%s: Fixed", "%s: BUILD the panel", "%s: built", "%s: merge into main", "%s merged"]):
            self.commit(subject % i, days_ago=3)
        self.assertEqual(sorted(self.drifted()), sorted(ids))

    def test_a_done_verified_or_dropped_item_never_drifts(self):
        a, b, c = self.items(("closed", "done"), ("proven", "verified"), ("gone", "dropped"))
        self.repo()
        for i in (a, b, c):
            self.commit("%s: fix" % i, days_ago=3)
        self.assertEqual(self.drifted(), [])

    def test_a_parked_item_drifts_unless_it_says_why(self):
        bare, why, blank = self.items(("parked, no reason", "open", "later"), ("parked with a reason", "open", "later", "waits for R-9"),
                                      ("parked, blank reason", "open", "later", "   "))
        self.repo()
        for i in (bare, why, blank):
            self.commit("%s: fix" % i, days_ago=3)
        self.assertEqual(sorted(self.drifted()), sorted([bare, blank]))

    def test_a_reason_belongs_to_later_and_leaves_with_it(self):
        a, = self.items(("parked", "open", "later", "waits for the beta"))
        data = sl.load_ledger(self.project)
        self.assertEqual(sl.later_reason(sl.find_item(data, a)), "waits for the beta")
        sl.set_phase(data, [a], "mvp")
        self.assertNotIn("later_reason", sl.find_item(data, a))
        sl.set_phase(data, [a], "later")                              # parked again without a reason: none is invented
        self.assertEqual(sl.later_reason(sl.find_item(data, a)), "")
        sl.set_phase(data, [a], "later", reason="second look")
        sl.set_phase(data, [a], "later")                              # re-parking keeps the reason already given
        self.assertEqual(sl.later_reason(sl.find_item(data, a)), "second look")

    def test_a_reason_must_be_text_and_secrets_in_it_are_masked(self):
        a, = self.items(("parked", "open", "later"))
        data = sl.load_ledger(self.project)
        sl.find_item(data, a)["later_reason"] = 5
        with self.assertRaises(sl.StudioError):
            sl.save_ledger(self.project, data)
        sl.set_phase(data, [a], "later", reason="token ghp_" + "a" * 36 + " leaked")
        self.assertNotIn("ghp_", sl.find_item(data, a)["later_reason"])

    def test_the_newest_old_commit_is_the_one_reported_and_each_item_once(self):
        a, = self.items(("many commits", "open"))
        self.repo()
        self.commit("%s: fix one" % a, days_ago=5)
        self.commit("%s: fix two" % a, days_ago=3)
        self.commit("%s: fix three, today" % a, days_ago=0)
        self.assertEqual([(i, m) for i, _s, m in self.drift()["drift"]], [(a, "%s: fix two" % a)])

    def test_a_commit_naming_several_items_flags_each(self):
        a, b = self.items(("one", "open"), ("two", "open"))
        self.repo()
        self.commit("%s %s: fix both" % (a, b), days_ago=3)
        self.assertEqual(self.drifted(), [a, b])

    def test_a_body_that_names_an_item_is_not_a_link(self):
        a, = self.items(("only in a body", "open"))
        self.repo()
        self.git("commit", "-q", "--allow-empty", "-m", "fix a typo", "-m", "this also fixes %s" % a, days_ago=3)
        self.assertEqual(self.drifted(), [])

    def test_only_main_counts_not_a_side_branch_until_it_is_merged(self):
        a, = self.items(("on a branch", "open"))
        self.repo()
        self.git("checkout", "-q", "-b", "claude/side")
        self.commit("%s: fix" % a, days_ago=3)
        self.git("checkout", "-q", "main")
        self.assertEqual(self.drifted(), [])
        self.git("merge", "-q", "--no-ff", "claude/side", "-m", "Merge side branch, build %s" % a, days_ago=2)
        self.assertEqual([(i, m) for i, _s, m in self.drift()["drift"]], [(a, "Merge side branch, build %s" % a)])

    def test_the_current_branch_does_not_matter_main_does(self):
        a, = self.items(("fixed on main", "open"))
        self.repo()
        self.commit("%s: fix" % a, days_ago=3)
        self.git("checkout", "-q", "-b", "claude/other")
        self.assertEqual(self.drifted(), [a])

    def test_master_stands_in_when_there_is_no_main(self):
        a, = self.items(("old default branch", "open"))
        self.repo(branch="master")
        self.commit("%s: fix" % a, days_ago=3)
        d = self.drift()
        self.assertEqual((d["branch"], [i for i, _s, _m in d["drift"]]), ("master", [a]))

    def test_commits_older_than_the_window_are_left_alone(self):
        a, b = self.items(("ancient", "open"), ("recent enough", "open"))
        self.repo()
        self.commit("%s: fix" % a, days_ago=45)
        self.commit("%s: fix" % b, days_ago=10)
        self.assertEqual(self.drifted(), [b])
        self.assertEqual(self.drifted(window_days=60), [a, b])

    def test_the_release_request_limits_what_is_looked_at(self):
        a, b, c = self.items(("asked for", "open"), ("not asked for", "open"), ("in progress anyway", "doing"))
        self.repo()
        for i in (a, b, c):
            self.commit("%s: fix" % i, days_ago=3)
        self.assertEqual(self.drifted(ids={a}), [a, c])

    def test_a_folder_that_is_not_a_repo_or_has_no_main_is_a_note_not_a_crash(self):
        self.items(("nothing to read", "open"))
        self.assertEqual(self.drift()["git"], "not-a-repo")
        self.git("init", "-q", "-b", "feature")
        d = self.drift()
        self.assertEqual((d["git"], d["drift"]), ("git-failed", []))

    def test_an_item_id_inside_a_longer_word_is_not_an_item(self):
        self.items(("real item", "open"))            # R-1
        self.repo()
        self.commit("fix XR-1 and R-10 and R-1x", days_ago=3)
        self.assertEqual(self.drifted(), [])

    def test_now_can_be_injected(self):
        a, = self.items(("clock", "open"))
        self.repo()
        self.commit("%s: fix" % a)
        self.assertEqual(self.drifted(), [])
        self.assertEqual(self.drifted(now=time.time() + 2 * DAY), [a])


class ReleaseCheckDriftTest(RepoBase):
    def test_a_drifted_item_fails_the_check_and_the_message_lists_it_with_its_commit(self):
        self.items(("closed long ago", "done"))
        b, = self.items(("never closed", "open", "later"))
        self.repo()
        self.commit("%s: fix the thing" % b, days_ago=3)
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Ledger drift", r.stdout)
        self.assertIn("%s [open]" % b, r.stdout)
        self.assertIn("%s %s: fix the thing" % (self.short_head(), b), r.stdout)
        self.assertIn("--reason", r.stdout)
        self.assertNotIn("Every item is built or deferred", r.stdout)

    def test_a_git_that_times_out_neither_passes_the_release_nor_blames_the_main_branch(self):
        import shutil
        self.items(("parked without a reason", "open", "later"))
        self.repo()
        shim_dir = os.path.join(self.tmp, "shim")
        os.makedirs(shim_dir)
        real = shutil.which("git")
        with open(os.path.join(shim_dir, "git"), "w") as f:
            f.write('#!/bin/sh\nfor a in "$@"; do [ "$a" = log ] && sleep 40; done\nexec %s "$@"\n' % real)
        os.chmod(os.path.join(shim_dir, "git"), 0o755)
        with mock.patch.dict(os.environ, {"PATH": shim_dir + os.pathsep + os.environ["PATH"]}):
            r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("git timed out", r.stdout)
        self.assertNotIn("could not read the main branch", r.stdout)
        self.assertNotIn("Every item is built or deferred", r.stdout)

    def test_the_same_item_parked_with_a_reason_passes(self):
        b, = self.items(("never closed", "open", "later"))
        self.repo()
        self.commit("%s: fix the thing" % b, days_ago=3)
        self.assertEqual(self.check().returncode, 1)
        r = self.cli("ledger", "phase", "later", b, "--reason", "waits for the beta")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["later_reason"], "waits for the beta")
        ok = self.check()
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertNotIn("Ledger drift", ok.stdout)
        self.assertIn("Every item is built or deferred", ok.stdout)

    def test_closing_the_item_with_evidence_passes(self):
        b, = self.items(("never closed", "open", "later"))
        self.repo()
        self.commit("%s: fix the thing" % b, days_ago=3)
        data = sl.load_ledger(self.project)
        item = sl.find_item(data, b)
        item["status"], item["evidence"] = "done", "ran: ok"
        sl.save_ledger(self.project, data)
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_clean_ledger_passes_with_or_without_a_repo(self):
        self.items(("closed", "done"))
        self.assertEqual(self.check().returncode, 0)
        self.repo()
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Ledger drift", r.stdout)

    def test_a_git_repo_with_no_main_says_the_drift_check_was_skipped(self):
        self.items(("closed", "done"))
        self.git("init", "-q", "-b", "feature")
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Drift check skipped", r.stdout)

    def test_drift_only_fails_for_the_requested_items(self):
        a, = self.items(("asked", "done"))
        b, = self.items(("other drift", "open", "later"))
        self.repo()
        self.commit("%s: fix" % b, days_ago=3)
        self.assertEqual(self.check("--items", a).returncode, 0)
        self.assertEqual(self.check("--items", a, b).returncode, 1)

    def test_the_message_follows_the_language(self):
        b, = self.items(("never closed", "open", "later"))
        self.repo()
        self.commit("%s: fix the thing" % b, days_ago=3)
        r = self.check(lang="es")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Desfase del ledger", r.stdout)
        self.assertIn("--reason", r.stdout)

    def test_the_run_leaves_the_ledger_and_the_repo_as_they_were(self):
        b, = self.items(("never closed", "open", "later"))
        self.repo()
        self.commit("%s: fix" % b, days_ago=3)
        snap = lambda: (open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "rb").read(),
                        self.git("status", "--porcelain").stdout, self.git("rev-parse", "HEAD").stdout)
        before = snap()
        self.check()
        self.assertEqual(before, snap())

    def test_many_drifted_items_are_capped_in_the_listing_but_all_fail(self):
        ids = self.items(*[("item %d" % n, "open", "later") for n in range(sl.MAX_LISTED + 3)])
        self.repo()
        for i in ids:
            self.commit("%s: fix" % i, days_ago=3)
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("… +3", r.stdout)


class CommitMsgWarningTest(RepoBase):
    def setUp(self):
        super().setUp()
        self.git("init", "-q", "-b", "main")

    def hook(self, lang="en", mode="strip-only"):
        os.makedirs(os.path.join(self.project, ".git", "hooks"), exist_ok=True)
        path = os.path.join(self.project, ".git", "hooks", "commit-msg")
        with open(path, "w") as f:
            f.write(watermark.hook_text("https://github.com/acme/x", mode, lang))
        os.chmod(path, 0o755)

    def commit_msg(self, *messages):
        with open(os.path.join(self.project, "f.txt"), "a") as f:
            f.write("x\n")
        self.git("add", "f.txt")
        args = ["commit", "-q"]
        for m in messages:
            args += ["-m", m]
        return self.git(*args)

    def ledger_bytes(self):
        d = sl.studio_dir(self.project)
        return {n: open(os.path.join(d, n), "rb").read() for n in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, n))}

    def test_a_subject_with_no_item_warns_on_stderr_and_the_commit_still_lands(self):
        self.hook()
        r = self.commit_msg("tidy the readme")
        self.assertIn("names no ledger item", r.stderr)
        self.assertEqual(self.git("log", "-1", "--format=%s").stdout.strip(), "tidy the readme")

    def test_a_subject_that_names_an_item_is_silent(self):
        self.hook()
        for subject in ("R-12 fix the thing", "fix the thing (R-12)", "fix: R-12.", "R-7, R-8: two at once"):
            self.assertEqual(self.commit_msg(subject).stderr, "", subject)

    def test_an_item_in_the_body_does_not_count(self):
        self.hook()
        self.assertIn("names no ledger item", self.commit_msg("tidy up", "this is part of R-12").stderr)

    def test_lookalikes_do_not_count(self):
        self.hook()
        for subject in ("fix XR-12", "fix R-", "fix R-x", "fix R12", "fix R-12abc"):
            self.assertIn("names no ledger item", self.commit_msg(subject).stderr, subject)

    def test_merge_revert_and_fixup_subjects_are_left_alone(self):
        self.hook()
        for subject in ("Merge branch 'x' into main", "Revert \"something\"", "fixup! earlier", "squash! earlier"):
            self.assertEqual(self.commit_msg(subject).stderr, "", subject)

    def test_a_project_without_a_ledger_is_not_nagged(self):
        os.remove(os.path.join(sl.studio_dir(self.project), "ledger.json"))
        self.hook()
        self.assertEqual(self.commit_msg("tidy the readme").stderr, "")

    def test_the_hook_never_writes_the_ledger(self):
        self.hook()
        before = self.ledger_bytes()
        self.commit_msg("tidy the readme")
        self.commit_msg("R-1 fix")
        self.assertEqual(self.ledger_bytes(), before)

    def test_the_watermark_strip_and_trailer_keep_working_next_to_the_warning(self):
        self.hook(mode="trailer")
        r = self.commit_msg("tidy the readme", "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>")
        self.assertIn("names no ledger item", r.stderr)
        message = self.git("log", "-1", "--format=%B").stdout
        self.assertNotIn("Claude", message)
        self.assertEqual(message.count("Built-with: Budgit (https://github.com/acme/x)"), 1, message)
        self.assertEqual(self.commit_msg("R-3 ok", "Co-Authored-By: Claude <x@y.z>").stderr, "")

    def test_the_warning_follows_the_language_and_the_script_parses(self):
        self.hook(lang="es")
        self.assertIn("no nombra ninguna tarea", self.commit_msg("arreglo").stderr)
        for lang in ("en", "es"):
            path = os.path.join(self.tmp, "h.sh")
            with open(path, "w") as f:
                f.write(watermark.hook_text("https://github.com/acme/x", "trailer", lang))
            self.assertEqual(subprocess.run(["sh", "-n", path], capture_output=True, text=True).returncode, 0)

    def test_an_installed_older_hook_is_upgraded_in_place(self):
        lines = watermark.hook_text("https://github.com/acme/x", "strip-only", "en").split("\n")
        old = "\n".join(l for l in lines[:lines.index("# R-362: warn (never block, never write the ledger) when the subject names no ledger item.")]) + "\nexit 0\n"
        os.makedirs(os.path.join(self.project, ".git", "hooks"), exist_ok=True)
        with open(os.path.join(self.project, ".git", "hooks", "commit-msg"), "w") as f:
            f.write(old)
        self.assertEqual(watermark.install(self.project, "strip-only", url=""), "already installed")
        with open(os.path.join(self.project, ".git", "hooks", "commit-msg")) as f:
            self.assertIn("R-362", f.read())


class VerifierFindingsTest(RepoBase):
    """Round 1 of the fresh verifier: a project inside another repo, a window busier than any commit cap, spellings, a shallow clone."""

    def test_a_project_inside_somebody_elses_repository_is_not_checked_against_that_log(self):
        outer = os.path.join(self.tmp, "outer")
        inner = os.path.join(outer, "app")
        os.makedirs(inner)
        r = lambda *a: subprocess.run(["git", "-C", outer, *a], capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r("init", "-q", "-b", "main").returncode, 0)
        self.project = inner
        self.init_project()
        sl.activate(self.project, "s1")
        a, = self.items(("belongs to the inner project", "open"))
        when = "%d +0000" % (time.time() - 3 * DAY)
        env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        subprocess.run(["git", "-C", outer, "commit", "-q", "--allow-empty", "-m", "%s fix, another project's work" % a], env=env, check=True)
        self.assertEqual(self.drift()["git"], "not-a-repo")
        self.assertEqual(self.drifted(), [])

    def test_a_busy_window_does_not_hide_drift(self):
        a, = self.items(("old and unclosed", "open"))
        self.repo()
        old = int(time.time() - 3 * DAY)
        stream = ["commit refs/heads/main\ncommitter t <t@x.org> %d +0000\ndata 14\n%s: fix once\n" % (old, a)]
        stream += ["commit refs/heads/main\ncommitter t <t@x.org> %d +0000\ndata 5\nbusy\n" % (old + n) for n in range(1, 5200)]
        r = subprocess.run(["git", "-C", self.project, "fast-import", "--quiet", "--force"], input="\n".join(stream) + "\n",
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.drifted(), [a])

    def test_hotfix_bugfix_rebuild_and_rebuilt_count(self):
        ids = self.items(*[("item %d" % n, "open") for n in range(4)])
        self.repo()
        for i, subject in zip(ids, ["%s hotfix", "%s bugfix", "%s: rebuild", "%s rebuilt"]):
            self.commit(subject % i, days_ago=3)
        self.assertEqual(sorted(self.drifted()), sorted(ids))

    def test_a_control_character_in_a_subject_does_not_cut_the_commit_short(self):
        a, b = self.items(("one", "open"), ("two", "open"))
        self.repo()
        self.commit("tidy \x1e then %s: fix" % a, days_ago=3)
        self.commit("%s: fix \x1f and more" % b, days_ago=3)
        self.assertEqual(sorted(self.drifted()), sorted([a, b]))

    def test_a_shallow_clone_gets_a_note(self):
        a, = self.items(("old", "open"))
        self.repo()
        self.commit("%s: fix" % a, days_ago=3)
        self.commit("later work", days_ago=1)
        clone = os.path.join(self.tmp, "clone")
        subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + self.project, clone], check=True, env=dict(os.environ),
                       capture_output=True)
        self.project = clone
        self.init_project()
        d = self.drift()
        self.assertTrue(d["shallow"])
        self.assertEqual(self.check().returncode, 0)
        self.assertIn("shallow clone", self.check().stdout)


class VerifierRoundTwoTest(RepoBase):
    def test_an_old_dated_tip_above_a_newer_commit_does_not_hide_drift(self):
        a, = self.items(("hidden by a clock-skewed tip", "open"))
        self.repo()
        now = int(time.time())
        stream = ["commit refs/heads/main\ncommitter t <t@x.org> %d +0000\ndata 14\n%s: fix once\n" % (now - 3 * DAY, a),
                  "commit refs/heads/main\ncommitter t <t@x.org> %d +0000\ndata 8\nold tip\n" % (now - 40 * DAY)]
        r = subprocess.run(["git", "-C", self.project, "fast-import", "--quiet", "--force"], input="\n".join(stream) + "\n",
                           capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.drifted(), [a])

    def test_a_path_that_differs_only_by_case_is_still_the_repo(self):
        a, = self.items(("case folded path", "open"))
        self.repo()
        self.commit("%s: fix" % a, days_ago=3)
        swapped = self.project.swapcase()
        if not os.path.exists(swapped):
            self.skipTest("this file system is case-sensitive")
        data = sl.load_ledger(self.project)
        d = lg.drift(swapped, sl.release_scope(data))
        self.assertEqual([i for i, _s, _m in d["drift"]], [a])
