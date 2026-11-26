"""R-1101 / R-1078 A: `budgit suite --background` + `budgit wait JOB` (a long suite a subagent can follow past the 600 s cap of a
foreground call), the Stop hook that still blocks while the job lives, and the narrow "suite" row class of CLI_WRITES that lets a
subagent run suite / wait (round 2: not `test`, no --project or --release, hub repo only, digest approval, nonce exit, fail-closed
hook). Synthetic names (acme); the jobs sleep a few seconds, never a real 15-minute wait."""
import calendar
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

import procclose
import studiolib as sl
import suitejobs
from helpers import StudioTestCase, patch_env

PY = sys.executable
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "plugin", "bin", "budgit")
SESSION = "sess-acme"
FAKE_JOB = "20261113-101010-abcdef"
FAKE_JOB2 = "20261113-101011-abcdef"
DAMAGED = "20261113-101012-abcdef"
NONCE = "n-acme"
REAL_MARKER = suitejobs.session_marker


def sleeper(seconds, code=0, say="hello-acme"):
    return [PY, "-c", "import sys,time; print(%r); time.sleep(%s); sys.exit(%d)" % (say, seconds, code)]


def dead_pid():
    p = subprocess.Popen([PY, "-c", "pass"])
    p.wait()
    return p.pid


class JobCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.bhome = os.path.join(self.tmp, "bhome")
        os.makedirs(self.bhome, exist_ok=True)
        patch_env(self, STUDIO_HOME=self.bhome, BUDGIT_HOME=None, BUDGIT_OWNER=SESSION)
        marker = mock.patch.object(suitejobs, "session_marker", lambda: SESSION)
        marker.start()
        self.addCleanup(marker.stop)

    def write_job(self, job, pid, began, owner=SESSION, nonce=NONCE):
        suitejobs.ensure_dir()
        rec_path, log_path, exit_path = suitejobs._paths(job)
        suitejobs._atomic(rec_path, json.dumps({"job": job, "pid": pid, "pid_start": began, "owner": owner, "nonce": nonce, "exit": exit_path,
                                                "log": log_path, "started": "2026-11-13T10:10:10", "project": self.tmp}))

    def write_exit(self, job, code, nonce=NONCE):
        suitejobs._atomic(suitejobs._paths(job)[2], json.dumps({"code": code, "nonce": nonce}))


class WaitTest(JobCase):
    def test_a_job_that_outlives_the_first_wait_finishes_across_two_waits(self):
        job = suitejobs.start(sleeper(4), self.tmp)
        text, code = suitejobs.report(job, 1)
        self.assertEqual(code, 3, text)
        self.assertTrue(text.startswith("running"), text)
        self.assertIn("budgit wait %s" % job, text)
        text, code = suitejobs.report(job, 60)
        self.assertEqual(code, 0, text)
        self.assertTrue(text.startswith("passed"), text)
        self.assertIn("hello-acme", text)

    def test_a_failed_job_reports_failed_with_its_exit_code_and_tail(self):
        job = suitejobs.start(sleeper(0, code=7, say="boom-acme"), self.tmp)
        text, code = suitejobs.report(job, 60)
        self.assertEqual(code, 1, text)
        self.assertTrue(text.startswith("failed"), text)
        self.assertIn("exit code: 7", text)
        self.assertIn("boom-acme", text)

    def test_the_job_file_is_private_and_records_the_owner_pid_start_time_and_nonce(self):
        job = suitejobs.start(sleeper(0), self.tmp)
        self.assertEqual(os.stat(suitejobs._paths(job)[0]).st_mode & 0o777, 0o600)
        rec = suitejobs.read_job(job)
        self.assertEqual(rec["owner"], SESSION)
        self.assertIsNotNone(rec["pid_start"])
        self.assertTrue(len(rec["nonce"]) >= 32)
        self.assertTrue(os.path.isfile(rec["log"]))
        suitejobs.report(job, 60)

    def test_wait_refuses_a_job_of_another_session(self):
        job = suitejobs.start(sleeper(2), self.tmp)
        with mock.patch.object(suitejobs, "session_marker", lambda: "sess-other"):
            text, code = suitejobs.report(job, 1)
        self.assertEqual(code, 2, text)
        self.assertIn("another session", text)
        self.assertEqual(suitejobs.report(job, 60)[1], 0)         # the owner still follows it to the end

    def test_wait_refuses_a_reused_pid(self):
        self.write_job(FAKE_JOB, os.getpid(), time.time() - 5000)   # a live pid that did not start at the recorded time
        text, code = suitejobs.report(FAKE_JOB, 1)
        self.assertEqual(code, 2, text)
        self.assertIn("pid reuse", text)

    def test_max_above_540_is_refused(self):
        text, code = suitejobs.report(FAKE_JOB, 541)
        self.assertEqual(code, 64, text)
        self.assertIn("540", text)
        r = subprocess.run([PY, CLI, "wait", FAKE_JOB, "--max", "541"], env=dict(os.environ), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 64, r.stdout + r.stderr)

    def test_wait_through_the_cli_refuses_an_unknown_job(self):
        r = subprocess.run([PY, CLI, "wait", FAKE_JOB, "--max", "1"], env=dict(os.environ), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


class RoundTwoJobTest(JobCase):
    def test_m2_a_start_time_that_cannot_be_read_kills_the_child_and_fails(self):
        made, real = [], subprocess.Popen

        def spy(*a, **k):
            p = real(*a, **k)
            made.append(p)
            return p
        with mock.patch.object(suitejobs.subprocess, "Popen", spy), mock.patch.object(suitejobs, "pid_start", lambda pid: None):
            with self.assertRaises(suitejobs.JobError):
                suitejobs.start(sleeper(30), self.tmp)
        self.assertIsNotNone(made[0].poll(), "the runner must be dead")

    def test_m2_a_record_that_cannot_be_written_kills_the_child_and_fails(self):
        made, real = [], subprocess.Popen

        def spy(*a, **k):
            p = real(*a, **k)
            made.append(p)
            return p
        with mock.patch.object(suitejobs.subprocess, "Popen", spy), mock.patch.object(suitejobs, "_atomic", mock.Mock(side_effect=OSError("disk full"))):
            with self.assertRaises(suitejobs.JobError):
                suitejobs.start(sleeper(30), self.tmp)
        self.assertIsNotNone(made[0].poll(), "the runner must be dead")

    def test_m2_a_record_with_no_start_time_counts_as_live_in_the_stop_hook(self):
        began = calendar.timegm((2026, 11, 13, 10, 0, 30))
        self.write_job(FAKE_JOB, 424242, None)
        row = {"pid": 424242, "ppid": 1, "pgid": 1, "start": began, "command": "x", "owner": SESSION, "env_seen": True}
        self.assertEqual([r["job"] for r in suitejobs.live_jobs([row], SESSION, None)], [FAKE_JOB])

    def test_m2_a_session_with_no_marker_cannot_start_a_job_or_follow_one(self):
        self.write_job(FAKE_JOB, os.getpid(), suitejobs.pid_start(os.getpid()))
        with mock.patch.object(suitejobs, "session_marker", lambda: None):
            with self.assertRaises(suitejobs.JobError):
                suitejobs.start(sleeper(1), self.tmp)
            self.assertEqual(suitejobs.report(FAKE_JOB, 0)[1], 2)

    def test_m2_the_owner_is_the_marker_the_process_table_shows_not_an_exported_variable(self):
        with mock.patch.dict(os.environ, {"BUDGIT_OWNER": "forged"}):
            job = suitejobs.start(sleeper(0), self.tmp)
        self.assertEqual(suitejobs.read_job(job)["owner"], SESSION)
        suitejobs.report(job, 60)
        table = [{"pid": os.getpid(), "ppid": 5, "owner": None}, {"pid": 5, "ppid": 1, "owner": "sess-acme"}]
        with mock.patch.object(procclose, "read_table", lambda: table), mock.patch.dict(os.environ, {"BUDGIT_OWNER": "forged"}):
            self.assertEqual(REAL_MARKER(), "sess-acme")

    def test_l1_an_exit_file_counts_only_with_the_nonce_and_once_the_pid_has_ended(self):
        self.write_job(FAKE_JOB, os.getpid(), suitejobs.pid_start(os.getpid()))
        self.write_exit(FAKE_JOB, 0, nonce="wrong")
        self.assertEqual(suitejobs.report(FAKE_JOB, 0)[1], 3)          # forged, pid alive
        self.write_exit(FAKE_JOB, 0)
        self.assertEqual(suitejobs.report(FAKE_JOB, 0)[1], 3)          # right nonce but the pid has not ended
        self.write_job(FAKE_JOB2, dead_pid(), time.time())
        self.write_exit(FAKE_JOB2, 0)
        self.assertEqual(suitejobs.report(FAKE_JOB2, 0)[1], 0)         # right nonce, ended
        self.write_exit(FAKE_JOB2, 0, nonce="wrong")
        text, code = suitejobs.report(FAKE_JOB2, 0)
        self.assertEqual(code, 1, text)                                # ended with no valid result: killed, never "passed"
        self.assertIn("killed", text)

    def test_l1_the_runner_writes_the_records_nonce_and_keeps_it_from_the_suite(self):
        job = suitejobs.start([PY, "-c", "import os; print('nonce=%s' % os.environ.get('BUDGIT_JOB_NONCE'))"], self.tmp)
        text, code = suitejobs.report(job, 60)
        self.assertEqual(code, 0, text)
        self.assertIn("nonce=None", text)
        with open(suitejobs._paths(job)[2]) as fh:
            self.assertEqual(json.load(fh)["nonce"], suitejobs.read_job(job)["nonce"])

    def test_l2_the_tail_is_read_from_the_end_and_control_characters_are_shown_as_text(self):
        suitejobs.ensure_dir()
        with open(suitejobs._paths(FAKE_JOB)[1], "wb") as fh:
            fh.write(b"A" * 1000000 + b"\n" + b"".join(b"line %d\n" % i for i in range(100)) + b"\x1b[31mred\x1b[0m\n")
        text = suitejobs.tail(FAKE_JOB)
        self.assertNotIn("\x1b", text)
        self.assertIn("\\x1b[31mred", text)
        self.assertEqual(len(text.splitlines()), 40)
        self.assertIn("line 99", text)
        self.assertNotIn("AAAA", text)

    def test_l3_a_linked_or_open_jobs_folder_is_refused(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, suitejobs.jobs_dir())
        with self.assertRaises(suitejobs.JobError):
            suitejobs.start(sleeper(1), self.tmp)
        self.assertEqual(suitejobs.report(FAKE_JOB, 0)[1], 2)

    def test_l3_a_folder_others_can_open_is_refused(self):
        suitejobs.ensure_dir()
        os.chmod(suitejobs.jobs_dir(), 0o755)
        with self.assertRaises(suitejobs.JobError):
            suitejobs.ensure_dir()

    def test_l3_a_record_that_recurses_too_deep_is_a_job_error_not_a_crash(self):
        suitejobs.ensure_dir()
        with open(suitejobs._paths(DAMAGED)[0], "w") as fh:
            fh.write("[" * 200000)
        with self.assertRaises(suitejobs.JobError):
            suitejobs.read_job(DAMAGED)


class StopHookTest(JobCase):
    def setUp(self):
        super().setUp()
        self.t0 = calendar.timegm((2026, 11, 13, 10, 0, 0))
        self.transcript = os.path.join(self.tmp, "agent.jsonl")
        with open(self.transcript, "w") as fh:
            fh.write(json.dumps({"timestamp": "2026-11-13T10:00:00", "message": {"content": []}}) + "\n")
        self.began = self.t0 + 30
        self.write_job(FAKE_JOB, 424242, self.began)
        self.row = {"pid": 424242, "ppid": 1, "pgid": 424242, "start": self.began, "command": "python3 suitejobs.py run x --", "owner": SESSION, "env_seen": True}
        self.inp = {"session_id": SESSION, "agent_transcript_path": self.transcript, "agent_id": "a-acme"}

    def stop(self, table):
        return procclose.on_subagent_stop(dict(self.inp), reader=lambda: table, me=os.getpid(), holds=[])

    def test_the_stop_hook_blocks_with_a_live_job_and_names_the_remedy(self):
        out = self.stop([self.row])
        self.assertEqual(out["decision"], "block", out)
        self.assertIn("budgit wait %s" % FAKE_JOB, out["reason"])
        self.assertIn("until it ends", out["reason"])

    def test_the_stop_hook_allows_the_subagent_to_end_once_the_job_has_ended(self):
        self.assertEqual(self.stop([self.row])["decision"], "block")
        self.write_exit(FAKE_JOB, 0)                                   # the runner wrote its result
        self.assertIsNone(self.stop([self.row]))

    def test_the_stop_hook_allows_the_end_when_the_job_process_is_gone_or_is_another_sessions(self):
        self.assertIsNone(self.stop([]))
        self.assertIsNone(self.stop([dict(self.row, owner="sess-other")]))
        self.assertIsNone(self.stop([dict(self.row, start=self.began + 500)]))        # a reused pid

    def test_l3_the_stop_hook_fails_closed_when_the_jobs_cannot_be_read(self):
        self.assertIsNone(self.stop([]))
        with open(suitejobs._paths(DAMAGED)[0], "w") as fh:
            fh.write("[" * 200000)
        out = self.stop([])
        self.assertEqual(out["decision"], "block", out)
        self.assertIn("budgit wait", out["reason"])
        os.unlink(suitejobs._paths(DAMAGED)[0])
        os.chmod(suitejobs.jobs_dir(), 0o755)
        self.inp["agent_id"] = "a-acme-2"                              # the first block is once per agent; a second agent gets its own
        out = self.stop([])
        self.assertEqual(out["decision"], "block", out)
        self.assertIn("budgit wait", out["reason"])


class GuardRowTest(JobCase):
    def decision(self, line, cwd=REPO):
        if line.split()[0] == "suite" and "--cwd-project" not in line:
            line += " --cwd-project"                                   # round 3: the marker flag a subagent's suite must carry
        return sl._home_write_decision("Bash", {"command": sl._own_budgit() + " " + line}, cwd=cwd, subagent=True)

    def assertAllowed(self, line, cwd=REPO):
        with mock.patch.object(sl, "load_project_config", return_value={}):
            got = self.decision(line, cwd)
        self.assertFalse(got and got[0] in ("deny", "ask"), (line, got))

    def assertDenied(self, line, cwd=REPO):
        got = self.decision(line, cwd)
        self.assertTrue(got and got[0] == "deny", (line, got))

    def test_a_subagent_may_call_suite_and_wait_but_test_and_another_home_verb_are_still_denied(self):
        for line in ("suite", "suite --background", "suite --rerun --wait", "wait %s" % FAKE_JOB, "wait %s --max 60" % FAKE_JOB):
            self.assertAllowed(line)
        self.assertAllowed("suite", os.path.join(REPO, "tests"))       # a folder inside the repo
        for line in ("test", "test --full", "procs", "procs --apply", "housekeeping", "rules off x"):
            self.assertDenied(line)

    def test_the_suite_class_is_exact_flags_only_and_refuses_project_and_release(self):
        for line in ("suite -- echo hi", "suite --back", "suite --release", "suite --project %s" % REPO, "suite --project=%s" % REPO,
                     "wait", "wait %s extra-word" % FAKE_JOB, "wait ../x", "wait %s --project %s" % (FAKE_JOB, REPO)):
            self.assertDenied(line)
        table = sl._cli_table()
        self.assertEqual([table[k][0] for k in ("suite", "wait", "test")], ["suite", "suite", "home"])

    def test_suite_runs_only_in_this_hub_repo_or_one_of_its_worktrees(self):
        other = os.path.join(self.tmp, "acme-other")
        os.makedirs(other)
        subprocess.run(["git", "init", "-q", other], check=True, capture_output=True)
        with mock.patch.object(sl, "load_project_config", return_value={}):
            self.assertDenied("suite", self.tmp)                       # no repo at all
            self.assertDenied("suite", other)                          # another repo
            self.assertDenied("suite --background", other)
            self.assertAllowed("wait %s" % FAKE_JOB, other)            # wait runs no code: any folder

    def test_a_test_command_needs_the_digest_approval_even_under_the_standing_yes(self):
        cfg = {"test_command": "sh acme-tests.sh"}
        digest = {os.path.realpath(REPO) + "|test_command": sl._test_digest(REPO, "sh acme-tests.sh")}
        with mock.patch.object(sl, "load_project_config", return_value=cfg), mock.patch.object(sl, "trust_granted", return_value=True), \
                mock.patch.object(sl, "_load_approvals", return_value={}):
            self.assertDenied("suite")
        with mock.patch.object(sl, "load_project_config", return_value=cfg), mock.patch.object(sl, "_load_approvals", return_value=digest):
            got = self.decision("suite")
        self.assertFalse(got and got[0] in ("deny", "ask"), got)


class EndToEndTest(JobCase):
    """`budgit suite --background` through the real CLI against a synthetic hub-like git worktree with a stub test_command."""

    def git(self, cwd, *args):
        subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args), check=True, capture_output=True)

    def test_suite_background_then_wait_reaches_passed(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patch_env(self, HOME=home, BUDGIT_SUITE_LOCK=os.path.join(self.tmp, "slot.lock"), BUDGIT_SUITE_PRIORITY="off",
                  BUDGIT_SUITE_LOCK_DIR=os.path.join(self.tmp, "locks"), BUDGIT_SUITE_TEST_CPU="0",   # admission reads an idle machine
                  BUDGIT_TEST_MEM='{"pressure": 1, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100}')   # and a calm one
        root = os.path.join(self.tmp, "Dev", "acme-hub")
        os.makedirs(os.path.join(root, "tools"))
        root = os.path.realpath(root)
        with open(os.path.join(root, "tools", "stub_tests.sh"), "w") as fh:
            fh.write("sleep 2\necho stub-ran-acme > ran.marker\n")
        self.git(root, "init", "-q", "-b", "main")
        sl.activate(root, bind=False)
        pj = os.path.join(sl.studio_dir(root), "project.json")
        with open(pj) as fh:
            cfg = json.load(fh)
        cfg["test_command"] = "sh tools/stub_tests.sh"
        with open(pj, "w") as fh:
            json.dump(cfg, fh)
        self.git(root, "add", "-A")
        self.git(root, "add", "-f", os.path.relpath(sl.studio_dir(root), root))
        self.git(root, "commit", "-q", "-m", "init")
        sl._write_approval(root, [], "sh tools/stub_tests.sh")        # the user approves this exact command once
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID="")
        env.pop("BUDGIT_SUITE_QUEUE_HELD", None)
        r = subprocess.run([PY, CLI, "suite", "--background"], cwd=root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        job = r.stdout.splitlines()[0]
        self.assertRegex(job, suitejobs.JOB_ID)
        first = subprocess.run([PY, CLI, "wait", job, "--max", "0"], cwd=root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)        # still running right after the start
        done = subprocess.run([PY, CLI, "wait", job, "--max", "120"], cwd=root, env=env, capture_output=True, text=True, timeout=200)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("passed"), done.stdout)
        self.assertTrue(os.path.isfile(os.path.join(root, "ran.marker")))


class RoundThreeTest(JobCase):
    """Round 3: the guard and the CLI judge one project; a real worktree check; a loud second stop."""

    def setUp(self):
        super().setUp()
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        patch_env(self, HOME=home, BUDGIT_SUITE_LOCK=os.path.join(self.tmp, "slot.lock"), BUDGIT_SUITE_PRIORITY="off",
                  BUDGIT_SUITE_LOCK_DIR=os.path.join(self.tmp, "locks"), BUDGIT_SUITE_TEST_CPU="0",   # admission reads an idle machine
                  BUDGIT_TEST_MEM='{"pressure": 1, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100}')   # and a calm one
        self.env = dict(os.environ, CLAUDE_CODE_SESSION_ID="")
        self.env.pop("BUDGIT_SUITE_QUEUE_HELD", None)

    def git(self, cwd, *args):
        subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid"] + list(args), check=True, capture_output=True)

    def make_repo(self, name, body):
        root = os.path.join(self.tmp, "Dev", name)
        os.makedirs(os.path.join(root, "tools"))
        root = os.path.realpath(root)
        with open(os.path.join(root, "tools", "stub.sh"), "w") as fh:
            fh.write(body)
        self.git(root, "init", "-q", "-b", "main")
        sl.activate(root, bind=False)
        pj = os.path.join(sl.studio_dir(root), "project.json")
        with open(pj) as fh:
            cfg = json.load(fh)
        cfg["test_command"] = "sh tools/stub.sh"
        with open(pj, "w") as fh:
            json.dump(cfg, fh)
        self.git(root, "add", "-A")
        self.git(root, "add", "-f", os.path.relpath(sl.studio_dir(root), root))
        self.git(root, "commit", "-q", "-m", "init")
        return root

    def cli(self, cwd, env, *args):
        return subprocess.run([PY, CLI] + list(args), cwd=cwd, env=env, capture_output=True, text=True, timeout=200)

    def test_m_b_a_bound_session_does_not_lend_its_project_to_a_subagents_suite(self):
        bound = self.make_repo("acme-bound", "echo bound > bound.marker\n")
        sl._write_approval(bound, [], "sh tools/stub.sh")                   # the bound project's command is approved and trusted
        sid = "11111111-2222-3333-4444-555555555555"
        sl.activate(bound, session_id=sid, bind=True)
        hub = self.make_repo("acme-hub", "echo evil > evil.marker\n")      # same command text, another realpath: no approval
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=sid)
        r = self.cli(hub, env, "suite", "--cwd-project")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(bound, "bound.marker")), "the bound project's suite ran")
        self.assertFalse(os.path.exists(os.path.join(hub, "evil.marker")), "the unapproved command ran")
        r = self.cli(hub, env, "suite", "--cwd-project", "--background")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(bound, "bound.marker")))
        control = self.cli(hub, env, "suite")                               # without the flag the session's project is used: why the guard demands it
        self.assertEqual(control.returncode, 0, control.stdout + control.stderr)
        self.assertTrue(os.path.exists(os.path.join(bound, "bound.marker")))

    def test_m_b_the_standing_yes_does_not_count_for_the_strict_suite_command(self):
        import maincheck
        hub = self.make_repo("acme-hub", "true\n")
        with mock.patch.object(sl, "trust_granted", return_value=True):
            self.assertEqual(maincheck.suite_command(hub), "sh tools/stub.sh")
            with self.assertRaises(maincheck.SuiteUnavailable):
                maincheck.suite_command(hub, strict=True)

    def test_m_b_cwd_project_runs_an_approved_repo_top_in_the_background_to_passed(self):
        hub = self.make_repo("acme-hub", "echo ok > ran.marker\n")
        sl._write_approval(hub, [], "sh tools/stub.sh")
        r = self.cli(os.path.join(hub, "tools"), self.env, "suite", "--cwd-project", "--background")   # from a subfolder: the repo top is used
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        done = self.cli(hub, self.env, "wait", r.stdout.splitlines()[0], "--max", "120")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.exists(os.path.join(hub, "ran.marker")))

    def test_m_b_the_guard_admits_suite_only_with_the_cwd_project_marker(self):
        with mock.patch.object(sl, "load_project_config", return_value={}):
            bare = sl._home_write_decision("Bash", {"command": sl._own_budgit() + " suite"}, cwd=REPO, subagent=True)
            marked = sl._home_write_decision("Bash", {"command": sl._own_budgit() + " suite --cwd-project"}, cwd=REPO, subagent=True)
        self.assertTrue(bare and bare[0] == "deny", bare)
        self.assertFalse(marked and marked[0] in ("deny", "ask"), marked)

    def test_l_new_a_git_symlink_or_a_forged_gitdir_is_not_a_checkout(self):
        real = self.make_repo("acme-real", "true\n")
        wt = os.path.join(self.tmp, "acme-wt")
        self.git(real, "worktree", "add", "-q", "-b", "claude/acme", wt)
        wt = os.path.realpath(wt)
        self.assertEqual(sl.suite_repo_root(real), real)
        self.assertEqual(sl.suite_repo_root(wt), wt)                        # a genuine worktree passes
        forged = os.path.join(self.tmp, "acme-forged")
        os.makedirs(forged)
        with open(os.path.join(wt, ".git")) as src, open(os.path.join(forged, ".git"), "w") as dst:
            dst.write(src.read())                                           # the gitdir line of a real worktree, copied
        self.assertIsNone(sl.suite_repo_root(forged))
        linked = os.path.join(self.tmp, "acme-link")
        os.makedirs(linked)
        os.symlink(os.path.join(real, ".git"), os.path.join(linked, ".git"))
        self.assertIsNone(sl.suite_repo_root(linked))
        self.assertFalse(sl._suite_cwd_ok(forged, "suite"))
        self.assertFalse(sl._suite_cwd_ok(linked, "suite"))

    def test_l_q4_the_second_stop_after_a_jobs_read_error_says_fail_and_closes_the_session_runners(self):
        t0 = calendar.timegm((2026, 11, 13, 10, 0, 0))
        transcript = os.path.join(self.tmp, "agent-q4.jsonl")
        with open(transcript, "w") as fh:
            fh.write(json.dumps({"timestamp": "2026-11-13T10:00:00", "message": {"content": []}}) + "\n")
        suitejobs.ensure_dir()
        with open(suitejobs._paths(DAMAGED)[0], "w") as fh:
            fh.write("[" * 200000)                                          # the jobs cannot be read
        base = {"ppid": 1, "start": t0 + 30, "owner": SESSION, "env_seen": True}
        runner = dict(base, pid=424243, pgid=424243, command="/usr/bin/python3 /x/suitejobs.py run /x/y.json -- python3 a b")
        other = dict(base, pid=424244, pgid=424244, command="sleep 99 99")
        inp = {"session_id": SESSION, "agent_transcript_path": transcript, "agent_id": "a-q4"}
        sent = []
        kw = dict(me=os.getpid(), holds=[], kill=lambda p, s: sent.append(p), killpg=lambda p, s: sent.append(p), sleep=lambda s: None)
        first = procclose.on_subagent_stop(dict(inp), reader=lambda: [runner, other], **kw)
        self.assertEqual(first["decision"], "block", first)
        second = procclose.on_subagent_stop(dict(inp), reader=lambda: [runner, other], **kw)
        self.assertIn("FAIL", second["systemMessage"], second)
        self.assertIn(424243, sent)
        self.assertNotIn(424244, sent)
        self.assertNotIn(-1, sent)


if __name__ == "__main__":
    unittest.main()
