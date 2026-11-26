"""R-1195 C (her I-1799): an item closes itself at merge when a verifier PASS for the merged tip exists. Real temporary git repos."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
from loadcal import load_factor
import studiolib as sl
import mergereconcile

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
HOLDER = ("import fcntl, os, sys, time\n"
          "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
          "fcntl.flock(fd, fcntl.LOCK_EX)\n"
          "print('held', flush=True)\n"
          "time.sleep(float(sys.argv[2]))\n")

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null")


class CloseAtMergeTest(StudioTestCase):
    def git(self, *args):
        r = subprocess.run(["git", "-C", self.project, *args], env=GIT_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def write(self, name, text):
        path = os.path.join(self.project, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def branch(self, name, subject, files=None):
        """A branch with one commit (a code file and a tests/ file), merged with a merge commit. -> (tip sha, merge sha)."""
        self.git("checkout", "-q", "-b", name)
        for fname, text in (files or {"code_%s.py" % name: "x = 1\n", "tests/test_%s.py" % name: "pass\n"}).items():
            self.write(fname, text)
        self.git("add", "-A", ".")
        self.git("commit", "-q", "-m", "work on " + name)
        tip = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", subject, name)
        return tip, self.git("rev-parse", "HEAD")

    def report(self, item_id, first, sha, seal=True):
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        os.makedirs(vdir, exist_ok=True)
        path = os.path.join(vdir, item_id + ".md")
        with open(path, "w") as f:
            f.write("%s\nchecked test_%s_behaves\n%s\n" % (first, item_id[2:], sha))
        if seal:
            sl.write_seal(path)
        return path

    def item(self, item_id):
        return sl.find_item(sl.load_ledger(self.project), item_id)

    def setUp(self):
        super().setUp()
        self.init_project()
        self.git("init", "-q", "-b", "main")
        self.write("README.txt", "hi\n")
        self.git("add", "README.txt")
        self.git("commit", "-q", "-m", "start")
        data = sl.load_ledger(self.project)
        for n in range(1, 5):
            sl.add_item(data, "item %d" % n, ["legacy"])
        sl.save_ledger(self.project, data)
        mergereconcile.reconcile(self.project)           # the first run only lists: from here on, merges can close

    def forget_last(self):
        state = sl.load_state(self.project)
        state.pop("merge_reconcile", None)
        sl.save_state(self.project, state)

    def test_a_part_c_pass_closes_nothing_and_status_shows_the_partial_line_while_an_unscoped_pass_closes(self):
        tip, merge = self.branch("a", "Merge R-1 and R-2: both")
        path = self.report("R-1", "PASS", tip)
        with open(path, "w") as f:
            f.write("PASS\n17 of 17, part C\n%s\n" % tip)
        sl.write_seal(path)
        self.report("R-2", "PASS", tip)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-2"])
        self.assertEqual(self.item("R-1")["status"], "open")
        self.assertEqual(mergereconcile.waiting_lines(self.project, sl.load_ledger(self.project)),
                         ["merged, partial PASS (part C), waiting for the whole item: R-1 (%s)" % merge[:8]])

    def test_a_scope_line_decides_whole_closes_and_part_c_does_not(self):
        tip, _ = self.branch("a", "Merge R-1 and R-2: both")
        for n, scope in (("R-1", "scope: whole"), ("R-2", "scope: part C")):
            path = self.report(n, "PASS", tip)
            with open(path, "w") as f:
                f.write("PASS\n%s\n%s\n" % (scope, tip))
            sl.write_seal(path)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-1"])
        self.assertEqual(self.item("R-2")["status"], "open")

    def real_header(self, item_id, tip, scope_line):
        path = self.report(item_id, "PASS", tip)
        with open(path, "w") as f:
            f.write("PASS\nreviewer: verifier-r1195c-round2\n%s\nround: 2\n%s\n\nbody text with part Z in it\n" % (tip, scope_line))
        sl.write_seal(path)

    def test_the_scope_is_read_from_the_whole_header_block_in_the_real_report_shape(self):
        tip, merge = self.branch("a", "Merge R-1 and R-2: both")
        self.real_header("R-1", tip, "17 of 17, part C")
        self.real_header("R-2", tip, "scope: whole")
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-2"])
        self.assertEqual(self.item("R-1")["status"], "open")
        self.assertEqual(mergereconcile.waiting_lines(self.project, sl.load_ledger(self.project)),
                         ["merged, partial PASS (part C), waiting for the whole item: R-1 (%s)" % merge[:8]])

    def test_the_verifier_instructions_say_line_five_is_the_scope_line(self):
        with open(os.path.join(ROOT, "plugin", "agents", "verifier.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("`scope: whole`", text)
        self.assertIn("`scope: part X`", text)
        self.assertIn("`scope: phase N`", text)

    def test_the_first_run_closes_nothing_and_lists_old_merges_and_a_later_merge_closes(self):
        old_tip, old_merge = self.branch("old", "Merge R-1: old work")
        self.report("R-1", "PASS", old_tip)
        self.forget_last()
        out = mergereconcile.reconcile(self.project)
        self.assertEqual(out["closed"], [])
        self.assertEqual(self.item("R-1")["status"], "open")
        self.assertEqual(mergereconcile.waiting_lines(self.project, sl.load_ledger(self.project)),
                         ["merged, waiting for a verifier PASS: R-1 (%s)" % old_merge[:8]])
        self.assertEqual(mergereconcile.reconcile(self.project, force=True)["closed"], [])      # still only listed
        tip, _ = self.branch("new", "Merge R-2: new work")
        self.report("R-2", "PASS", tip)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-2"])
        self.assertEqual(self.item("R-1")["status"], "open")

    def test_merge_with_a_pass_report_closes_the_item_with_sha_tests_and_verify_file(self):
        tip, merge = self.branch("a", "Merge R-1: the first thing")
        self.report("R-1", "PASS", tip)
        out = mergereconcile.reconcile(self.project)
        it = self.item("R-1")
        self.assertEqual(out["closed"], ["R-1"])
        self.assertEqual(it["status"], "done")
        self.assertIn(merge[:12], it["evidence"])
        self.assertIn("test_1_behaves", it["evidence"])
        self.assertIn("verify/R-1.md", it["evidence"])

    def test_the_tests_files_of_the_merge_name_the_tests_when_the_report_has_none(self):
        tip, merge = self.branch("a", "Merge R-1: the first thing")
        path = self.report("R-1", "PASS", tip)
        with open(path, "w") as f:
            f.write("PASS\nall fine\n%s\n" % tip)
        sl.write_seal(path)
        mergereconcile.reconcile(self.project)
        self.assertEqual(self.item("R-1")["status"], "done")
        self.assertIn("test_a.py", self.item("R-1")["evidence"])

    def test_no_report_keeps_the_item_open_and_status_lists_it(self):
        tip, merge = self.branch("a", "Merge R-1: the first thing")
        out = mergereconcile.reconcile(self.project)
        self.assertEqual(out["closed"], [])
        self.assertEqual(self.item("R-1")["status"], "open")
        self.assertEqual(mergereconcile.waiting_lines(self.project, sl.load_ledger(self.project)),
                         ["merged, waiting for a verifier PASS: R-1 (%s)" % merge[:8]])
        self.report("R-1", "PASS", tip)                      # the PASS comes later: the next run closes it
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-1"])
        self.assertEqual(mergereconcile.waiting_lines(self.project, sl.load_ledger(self.project)), [])

    def test_a_fail_report_or_a_pass_for_another_sha_with_later_code_changes_does_not_close(self):
        tip, _ = self.branch("a", "Merge R-1: first")
        self.report("R-1", "FAIL", tip)
        first_branch_tip = self.git("rev-parse", "HEAD~1")        # the commit before the merge on main: not on the branch
        tip2, _ = self.branch("b", "Merge R-2: second")
        self.report("R-2", "PASS", first_branch_tip)             # a PASS for a sha that is not the tip, nor an ancestor of it
        out = mergereconcile.reconcile(self.project)
        self.assertEqual(out["closed"], [])
        # a PASS for an ancestor of the tip, with a code change after it
        self.git("checkout", "-q", "-b", "c")
        self.write("c1.py", "1\n")
        self.git("add", "-A", ".")
        self.git("commit", "-q", "-m", "c1")
        old = self.git("rev-parse", "HEAD")
        self.write("c2.py", "2\n")
        self.git("add", "-A", ".")
        self.git("commit", "-q", "-m", "c2")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge R-3: third", "c")
        self.report("R-3", "PASS", old)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], [])
        for n in ("R-1", "R-2", "R-3"):
            self.assertEqual(self.item(n)["status"], "open", n)

    def test_a_pass_for_an_ancestor_with_only_notes_after_it_closes(self):
        self.git("checkout", "-q", "-b", "d")
        self.write("d.py", "1\n")
        self.write("tests/test_d.py", "1\n")
        self.git("add", "-A", ".")
        self.git("commit", "-q", "-m", "d1")
        old = self.git("rev-parse", "HEAD")
        self.write("notes.md", "a note\n")
        self.git("add", "-A", ".")
        self.git("commit", "-q", "-m", "notes")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge R-1: d", "d")
        self.report("R-1", "PASS", old)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-1"])

    def test_a_merge_naming_two_items_closes_each_one_only_on_its_own_pass(self):
        tip, _ = self.branch("a", "Merge R-1 and R-2: both")
        self.report("R-1", "PASS", tip)
        out = mergereconcile.reconcile(self.project)
        self.assertEqual(out["closed"], ["R-1"])
        self.assertEqual(self.item("R-1")["status"], "done")
        self.assertEqual(self.item("R-2")["status"], "open")
        self.assertEqual([i for i, _ in out["waiting"]], ["R-2"])

    def test_an_item_already_done_folded_or_dropped_is_untouched(self):
        tip, _ = self.branch("a", "Merge R-1, R-2, R-3, R-4: all")
        data = sl.load_ledger(self.project)
        sl.set_status(self.project, data, "R-1", "done", evidence="abc1234 test_x", outcome="You see it work")
        sl.fold_item(self.project, data, "R-2", "R-4", "part of it")
        data["items"][2]["status"] = "dropped"
        sl.save_ledger(self.project, data)
        for n in ("R-1", "R-2", "R-3"):
            self.report(n, "PASS", tip)
        before = {n: json.dumps(self.item(n), sort_keys=True) for n in ("R-1", "R-2", "R-3")}
        out = mergereconcile.reconcile(self.project)
        self.assertEqual(out["closed"], [])
        self.assertEqual(before, {n: json.dumps(self.item(n), sort_keys=True) for n in ("R-1", "R-2", "R-3")})

    def test_a_second_run_changes_nothing(self):
        tip, _ = self.branch("a", "Merge R-1: first")
        self.report("R-1", "PASS", tip)
        mergereconcile.reconcile(self.project)
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "rb") as f:
            ledger = f.read()
        with open(os.path.join(sl.studio_dir(self.project), "state.json"), "rb") as f:
            state = f.read()
        again = mergereconcile.reconcile(self.project)
        self.assertEqual(again["closed"], [])
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "rb") as f:
            self.assertEqual(f.read(), ledger)
        with open(os.path.join(sl.studio_dir(self.project), "state.json"), "rb") as f:
            self.assertEqual(f.read(), state)

    def test_an_error_inside_reconcile_closes_nothing_and_logs_one_line(self):
        tip, _ = self.branch("a", "Merge R-1 and R-2: both")
        self.report("R-1", "PASS", tip)
        bad = self.report("R-2", "PASS", tip)
        os.chmod(bad, 0)                                      # a report that cannot be read
        out = mergereconcile.reconcile(self.project)
        self.assertTrue(out["error"])
        self.assertEqual(out["closed"], [])
        self.assertEqual(self.item("R-1")["status"], "open")
        with open(os.path.join(sl.home_state(), "hooks.log"), encoding="utf-8") as f:
            self.assertEqual(f.read().count(" merge reconcile\n"), 1)

    # ---- review round: sealed PASS, cheap hook, bounded reads, hex-checked state, clean text, code vs notes, PASS word, batches ----
    def hooks_log(self):
        try:
            with open(os.path.join(sl.home_state(), "hooks.log"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def test_an_unsealed_pass_for_the_right_sha_closes_nothing_and_status_lists_it_as_waiting(self):
        tip, merge = self.branch("a", "Merge R-1: the first thing")
        self.report("R-1", "PASS", tip, seal=False)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], [])
        self.assertEqual(self.item("R-1")["status"], "open")
        self.assertEqual(mergereconcile.waiting_lines(self.project, sl.load_ledger(self.project)),
                         ["merged, waiting for a verifier PASS: R-1 (%s)" % merge[:8]])

    def test_post_tool_use_makes_zero_git_calls_for_a_non_git_command_and_for_a_merge_that_left_head_alone(self):
        import hook
        from unittest import mock
        sl.activate(self.project, "s1")
        self.branch("a", "Merge R-1: the first thing")
        mergereconcile.reconcile(self.project)                   # the last-checked sha is now the tip
        payload = self.merge_payload()
        calls = []
        real = mergereconcile._git
        with mock.patch.object(mergereconcile, "_git", side_effect=lambda *a: calls.append(a) or real(*a)):
            hook.on_post_tool_use(dict(payload, tool_input={"command": "ls -la"}), self.project)
            hook.on_post_tool_use(payload, self.project)         # a git merge, HEAD unchanged
        self.assertEqual(calls, [])

    def test_a_report_over_256_kb_counts_as_no_pass_with_one_log_line_and_pending_is_capped_at_200(self):
        tip, _ = self.branch("a", "Merge R-1: the first thing")
        path = self.report("R-1", "PASS", tip)
        with open(path, "ab") as f:
            f.write(b"x" * (300 * 1024))
        sl.write_seal(path)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], [])
        self.assertEqual(self.item("R-1")["status"], "open")
        self.assertEqual(self.hooks_log().count("counts as no PASS"), 1)
        data = sl.load_ledger(self.project)
        for n in range(5, 206):
            sl.add_item(data, "item %d" % n, ["legacy"])
        sl.save_ledger(self.project, data)
        state = sl.load_state(self.project)
        state["merge_reconcile"]["pending"] = {"R-%d" % n: {"merge": "a" * 40, "tip": "b" * 40, "subject": "Merge R-%d" % n}
                                               for n in range(5, 206)}
        sl.save_state(self.project, state)
        mergereconcile.reconcile(self.project)
        pending = sl.load_state(self.project)["merge_reconcile"]["pending"]
        self.assertEqual(len(pending), 200)
        self.assertNotIn("R-5", pending)
        self.assertEqual(self.hooks_log().count("pending list over"), 1)

    def test_a_malformed_sha_in_the_state_never_reaches_git_and_the_entry_is_dropped_with_a_log_line(self):
        self.branch("a", "Merge R-1: the first thing")
        state = sl.load_state(self.project)
        state["merge_reconcile"] = {"last": "--output=/tmp/x", "pending": {
            "R-2": {"merge": "a" * 40, "tip": "--upload-pack=x", "subject": "s"},
            "R-3": {"merge": "a" * 40, "tip": "b" * 40, "subject": "Merge R-3"}}}
        sl.save_state(self.project, state)
        from unittest import mock
        seen = []
        real = mergereconcile._git
        with mock.patch.object(mergereconcile, "_git", side_effect=lambda *a: seen.append(a) or real(*a)):
            out = mergereconcile.reconcile(self.project)
        self.assertFalse(out["error"])
        self.assertFalse([a for a in seen if any(str(x).startswith("-") and "output" in str(x) or "upload-pack" in str(x) for x in a)])
        log = self.hooks_log()
        self.assertEqual(log.count("dropped a malformed last-checked sha"), 1)
        self.assertEqual(log.count("dropped a malformed pending entry"), 1)
        self.assertNotIn("R-2", sl.load_state(self.project)["merge_reconcile"]["pending"])

    def test_control_characters_are_stripped_from_subject_and_evidence(self):
        tip, _ = self.branch("a", "Merge R-1: a\x07b\x1b[31mc\x7fd")
        self.report("R-1", "PASS", tip)
        mergereconcile.reconcile(self.project)
        it = self.item("R-1")
        self.assertEqual(it["status"], "done")
        for text in (it["evidence"], it["outcome"]):
            self.assertFalse([c for c in text if ord(c) < 0x20 or ord(c) == 0x7f], repr(text))

    def test_a_md_under_plugin_after_the_report_sha_is_code_and_blocks_while_docs_and_top_level_md_do_not(self):
        for name, path, closes in (("p", "plugin/readme.md", False), ("q", "docs/guide.md", True), ("r", "TOP.md", True)):
            self.git("checkout", "-q", "-b", name)
            self.write("%s.py" % name, "1\n")
            self.write("tests/test_%s.py" % name, "1\n")
            self.git("add", "-A", ".")
            self.git("commit", "-q", "-m", "code " + name)
            old = self.git("rev-parse", "HEAD")
            self.write(path, "text\n")
            self.git("add", "-A", ".")
            self.git("commit", "-q", "-m", "text " + name)
            self.git("checkout", "-q", "main")
            n = {"p": "R-1", "q": "R-2", "r": "R-3"}[name]
            self.git("merge", "-q", "--no-ff", "-m", "Merge %s: %s" % (n, name), name)
            self.report(n, "PASS", old)
            mergereconcile.reconcile(self.project)
            self.assertEqual(self.item(n)["status"] == "done", closes, path)

    def test_only_the_word_pass_counts_as_a_pass(self):
        tip, _ = self.branch("a", "Merge R-1 and R-2: both")
        self.report("R-1", "PASSWORD", tip)
        self.report("R-2", "PASS: all green", tip)
        self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-2"])
        self.assertEqual(self.item("R-1")["status"], "open")

    def test_more_than_the_batch_of_merges_continue_on_the_next_trigger_and_none_is_skipped(self):
        from unittest import mock
        mergereconcile.reconcile(self.project)                   # the last-checked sha is the start commit
        tips = {}
        for n in (1, 2, 3):
            tips[n], _ = self.branch("b%d" % n, "Merge R-%d: part %d" % (n, n))
            self.report("R-%d" % n, "PASS", tips[n])
        with mock.patch.object(mergereconcile, "BATCH", 2):
            self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-1", "R-2"])
            self.assertEqual(self.item("R-3")["status"], "open")
            self.assertEqual(mergereconcile.reconcile(self.project)["closed"], ["R-3"])

    def test_a_first_run_looks_back_at_the_newest_batch_and_logs_how_many_older_merges_were_left_out(self):
        from unittest import mock
        for n in (1, 2, 3):
            tip, _ = self.branch("b%d" % n, "Merge R-%d: part %d" % (n, n))
            self.report("R-%d" % n, "PASS", tip)
        self.forget_last()
        with mock.patch.object(mergereconcile, "BATCH", 2):
            out = mergereconcile.reconcile(self.project)
        self.assertEqual((out["closed"], [i for i, _ in out["waiting"]]), ([], ["R-2", "R-3"]))     # the first run only lists
        self.assertEqual(self.hooks_log().count("1 older merges were left out"), 1)

    # ---- end to end: the three triggers, through the real hook script and CLI in a child process ----
    def run_hook(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        start = time.monotonic()
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return time.monotonic() - start

    def merge_payload(self):
        return {"session_id": "s1", "tool_name": "Bash", "cwd": self.project, "agent_type": "",
                "tool_input": {"command": "git merge --no-ff -m 'Merge R-1: x' a"}, "tool_response": {"stdout": ""}}

    def merged_with_pass(self, with_pass=True):
        sl.activate(self.project, "s1")
        tip, merge = self.branch("a", "Merge R-1: the first thing")
        if with_pass:
            self.report("R-1", "PASS", tip)
        return merge

    def test_trigger_post_tool_use_bash_merge_closes_the_item(self):
        merge = self.merged_with_pass()
        self.run_hook("post-tool-use", self.merge_payload())
        self.assertEqual(self.item("R-1")["status"], "done")
        self.assertIn(merge[:12], self.item("R-1")["evidence"])

    def test_trigger_session_start_closes_a_merge_made_outside_any_session(self):
        self.merged_with_pass()
        self.assertEqual(self.item("R-1")["status"], "open")
        self.run_hook("session-start", {"session_id": "s1", "cwd": self.project, "source": "startup"})
        self.assertEqual(self.item("R-1")["status"], "done")

    def test_trigger_budgit_status_closes_it_or_lists_it_as_waiting(self):
        merge = self.merged_with_pass(with_pass=False)
        env = dict(os.environ, STUDIO_HOME=self.home)
        cli = os.path.join(ROOT, "plugin", "bin", "budgit")
        run = lambda: subprocess.run([sys.executable, cli, "status", "--project", self.project], env=env, capture_output=True, text=True)
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged, waiting for a verifier PASS: R-1 (%s)" % merge[:8], r.stdout)
        self.assertEqual(self.item("R-1")["status"], "open")
        self.report("R-1", "PASS", self.git("rev-parse", "a"))
        r = run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("waiting for a verifier PASS", r.stdout)
        self.assertEqual(self.item("R-1")["status"], "done")

    def test_trigger_under_a_held_lock_closes_nothing_does_not_hang_and_the_next_trigger_picks_it_up(self):
        self.merged_with_pass()
        holder = subprocess.Popen([sys.executable, "-c", HOLDER, os.path.join(sl.studio_dir(self.project), ".lock"), "30"],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            took = self.run_hook("post-tool-use", self.merge_payload())
            self.assertLess(took, 6 * load_factor())     # real-deadline budget 6 s scaled by the same-run calibration (R-1282)
            self.assertEqual(self.item("R-1")["status"], "open")
        finally:
            holder.kill()
            holder.wait()
        self.run_hook("session-start", {"session_id": "s1", "cwd": self.project, "source": "startup"})
        self.assertEqual(self.item("R-1")["status"], "done")

    def test_only_git_merge_pull_and_commit_are_triggers(self):
        self.assertTrue(mergereconcile.is_merge_command("git merge --no-ff x"))
        self.assertTrue(mergereconcile.is_merge_command("git -C /p commit -m x"))
        self.assertTrue(mergereconcile.is_merge_command("git pull"))
        self.assertFalse(mergereconcile.is_merge_command("git status"))
        self.assertFalse(mergereconcile.is_merge_command("ls"))
