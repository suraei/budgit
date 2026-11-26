"""A PreToolUse hook never waits on the project lock: the guards decide without it, and only the audit line needs it
(waited for at most LOCK_WAIT_S, then skipped with a note while the decision stands)."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
from loadcal import load_factor
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
HOLDER = ("import fcntl, os, sys, time\n"
          "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
          "fcntl.flock(fd, fcntl.LOCK_EX)\n"
          "print('held', flush=True)\n"
          "time.sleep(float(sys.argv[2]))\n")


class LockBusyTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s1")
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        with open(pj) as f:
            cfg = json.load(f)
        cfg["protected_paths"] = ["data.json"]
        with open(pj, "w") as f:
            json.dump(cfg, f)
        self.holder = None

    def tearDown(self):
        if self.holder:
            self.holder.kill()
            self.holder.wait()
        super().tearDown()

    def hold(self, seconds):
        self.holder = subprocess.Popen([sys.executable, "-c", HOLDER, os.path.join(sl.studio_dir(self.project), ".lock"),
                                        str(seconds)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(self.holder.stdout.readline().strip(), "held")

    def edit(self):
        payload = {"session_id": "s1", "tool_name": "Edit", "cwd": self.project,
                   "tool_input": {"file_path": os.path.join(self.project, "data.json")}}
        env = dict(os.environ, STUDIO_HOME=self.home)
        start = time.monotonic()
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout), time.monotonic() - start

    def audit(self):
        try:
            return sl.read_text(os.path.join(sl.studio_dir(self.project), "audit.log"))
        except OSError:
            return ""

    def test_a_free_lock_decides_and_writes_the_audit_line(self):
        out, _ = self.edit()
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("guard-ask Edit", self.audit())

    def test_a_lock_held_for_30_seconds_does_not_hold_the_decision_back(self):
        self.hold(30)
        out, took = self.edit()
        self.assertLess(took, 3.5 * load_factor())     # real-deadline budget 3.5 s scaled by the same-run calibration (R-1282)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")      # the same decision as with a free lock
        self.assertIn("protected real data", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("lock was busy", out.get("systemMessage", ""))
        self.assertNotIn("guard-ask", self.audit())                                     # the bookkeeping was skipped
        with open(os.path.join(sl.home_state(), "hooks.log"), encoding="utf-8") as f:
            self.assertIn("project lock busy", f.read())

    def test_locked_with_a_timeout_gives_up_and_try_locked_says_so(self):
        self.hold(30)
        start = time.monotonic()
        with self.assertRaises(sl.LockBusy):
            with sl.locked(self.project, timeout=0.3):
                pass
        with sl.try_locked(self.project, 0.3) as got:
            self.assertFalse(got)
        self.assertLess(time.monotonic() - start, 2 * load_factor())     # real-deadline budget 2 s scaled by the same-run calibration (R-1282)

    def test_try_locked_takes_a_free_lock(self):
        with sl.try_locked(self.project, 0.3) as got:
            self.assertTrue(got)


class BusyEventsTest(LockBusyTest):
    def fire(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        start = time.monotonic()
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(dict(payload, session_id="s1")), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (json.loads(r.stdout) if r.stdout.strip() else None), time.monotonic() - start

    def release(self):
        self.holder.kill()
        self.holder.wait()
        self.holder = None

    def inbox_texts(self):
        with sl.locked(self.project):                       # whoever takes the lock next merges the spool
            return [e["text"] for e in sl.load_inbox(self.project)]

    def test_a_prompt_with_the_lock_held_returns_fast_and_reaches_the_inbox_later(self):
        self.hold(30)
        _, took = self.fire("user-prompt-submit", {"prompt": "primera"})
        self.assertLess(took, 3.5 * load_factor())     # real-deadline budget 3.5 s scaled by the same-run calibration (R-1282)
        self.fire("user-prompt-submit", {"prompt": "segunda"})
        self.assertEqual(sl.load_inbox(self.project), [])         # not merged yet: nobody held the lock
        self.release()
        self.assertEqual(self.inbox_texts(), ["primera", "segunda"])   # in the order they came
        self.assertEqual(self.inbox_texts(), ["primera", "segunda"])   # and merged once
        self.assertFalse(os.path.exists(os.path.join(sl.studio_dir(self.project), "inbox.spool")))

    def test_the_merge_skips_what_is_already_in_the_inbox(self):
        sl.append_inbox(self.project, "hola", "s1", "prompt", ts="2026-10-27T10:00:00.000Z")
        sl.append_text(os.path.join(sl.studio_dir(self.project), "inbox.spool"),
                       json.dumps({"ts": "2026-10-27T10:00:00.000Z", "session": "s1", "source": "prompt", "ref": None,
                                   "text": "hola"}) + "\n")
        self.assertEqual(self.inbox_texts(), ["hola"])

    def test_an_answer_to_a_question_is_kept_too(self):
        self.hold(30)
        _, took = self.fire("post-tool-use", {"tool_name": "AskUserQuestion", "tool_use_id": "toolu_1",
                                              "tool_input": {}, "tool_response": {"answers": {"¿Plataforma?": "PWA"}}})
        self.assertLess(took, 3.5 * load_factor())     # real-deadline budget 3.5 s scaled by the same-run calibration (R-1282)
        self.release()
        texts = self.inbox_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("PWA", texts[0])

    def test_a_verifier_report_is_sealed_even_with_the_lock_held(self):
        report = os.path.join(sl.studio_dir(self.project), "verify", "R-1.md")
        os.makedirs(os.path.dirname(report), exist_ok=True)
        with open(report, "w") as f:
            f.write("PASS\nchecked\n")
        self.hold(30)
        _, took = self.fire("post-tool-use", {"tool_name": "Write", "agent_type": "budgit:verifier", "cwd": self.project,
                                              "tool_input": {"file_path": report}})
        self.assertLess(took, 3.5 * load_factor())     # real-deadline budget 3.5 s scaled by the same-run calibration (R-1282)
        self.assertTrue(sl.report_sealed(report))

    def test_a_spool_left_by_a_crashed_drainer_is_merged_and_removed(self):
        spool = os.path.join(sl.studio_dir(self.project), "inbox.spool")
        def line(text, ts):
            return json.dumps({"ts": ts, "session": "s1", "source": "prompt", "ref": None, "text": text}) + "\n"
        sl.append_text(spool + ".99999", line("de un drenaje roto", "2026-10-27T10:00:00.000Z"))
        sl.append_text(spool + ".99999.5", line("de otro drenaje roto", "2026-10-27T10:00:01.000Z"))
        sl.append_text(spool, line("reciente", "2026-10-27T10:00:02.000Z"))
        self.assertEqual(self.inbox_texts(), ["de un drenaje roto", "de otro drenaje roto", "reciente"])
        self.assertEqual([n for n in os.listdir(sl.studio_dir(self.project)) if n.startswith("inbox.spool")], [])

    def test_session_start_stop_and_end_answer_fast_with_the_lock_held(self):
        self.hold(30)
        for event, payload in (("session-start", {"source": "startup"}), ("stop", {"stop_hook_active": False}),
                               ("session-end", {"reason": "other"}), ("post-compact", {"trigger": "auto"})):
            _, took = self.fire(event, payload)
            self.assertLess(took, 3.5 * load_factor(), event)
        with open(os.path.join(sl.home_state(), "hooks.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("session-start: project lock busy", log)
        self.assertIn("stop: project lock busy", log)
