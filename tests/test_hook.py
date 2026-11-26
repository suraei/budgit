import json, os, shutil, subprocess, sys
from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
FIX = os.path.join(ROOT, "tests", "fixtures", "transcript_basic.jsonl")


class HookTest(StudioTestCase):
    def run_hook(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload),
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(all(ord(c) < 128 for c in r.stdout), "stdout must be ASCII-safe")
        return json.loads(r.stdout) if r.stdout.strip() else None

    def bind(self, session="s1"):
        sl.activate(self.project, session)
        self.transcript = os.path.join(self.tmp, "t.jsonl")
        shutil.copy(FIX, self.transcript)

    def track_all(self, session="s1"):
        data = sl.load_ledger(self.project)
        ids = [e["id"] for e in sl.untracked(self.project, data, session=session)]
        sl.add_item(data, "app recetas", ids[:1])
        if ids[1:]:
            sl.ack(data, ids[1:], "folded into R-1")
        sl.save_ledger(self.project, data)

    def test_unbound_session_is_silent_noop(self):
        self.assertIsNone(self.run_hook("stop", {"session_id": "zzz", "stop_hook_active": False}))
        out = self.run_hook("user-prompt-submit", {"session_id": "zzz", "prompt": "hola"})   # R-1159: only the local-time line
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(len(text.splitlines()), 1)
        self.assertRegex(text, r"^budgit: (local time|hora local) \d\d:\d\d ")
        self.assertEqual(sl.load_inbox(self.project), [])

    def test_pointer_to_deleted_project_is_noop(self):
        self.bind()
        shutil.rmtree(self.project)
        self.assertIsNone(self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False}))

    def test_prompt_is_captured_and_notice_returned(self):
        self.bind()
        out = self.run_hook("user-prompt-submit", {"session_id": "s1", "prompt": "añade login"})
        self.assertEqual(sl.load_inbox(self.project)[0]["text"], "añade login")
        self.assertIn("I-1", out["hookSpecificOutput"]["additionalContext"])

    def test_answer_captured_by_post_tool_use(self):
        self.bind()
        out = self.run_hook("post-tool-use", {
            "session_id": "s1", "tool_name": "AskUserQuestion", "tool_use_id": "toolu_x",
            "tool_input": {"questions": [{"question": "¿Plataforma?"}]},
            "tool_response": {"answers": {"¿Plataforma?": "PWA"}}})
        entry = sl.load_inbox(self.project)[0]
        self.assertEqual((entry["source"], entry["ref"]), ("answer", "toolu_x"))
        self.assertIn("PWA", entry["text"])
        self.assertIn("I-1", out["hookSpecificOutput"]["additionalContext"])

    def test_stop_blocks_on_untracked_then_allows_when_tracked(self):
        self.bind()
        out = self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False,
                                     "transcript_path": self.transcript})
        self.assertEqual(out["decision"], "block")
        self.assertIn("I-1", out["reason"])
        self.track_all()
        self.assertIsNone(self.run_hook("stop", {"session_id": "s1", "stop_hook_active": True,
                                                 "transcript_path": self.transcript}))

    def test_other_sessions_inputs_do_not_block(self):
        self.bind()
        sl.append_inbox(self.project, "mensaje de otra sesión", "s2", "prompt")
        self.assertIsNone(self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False}))

    def test_stall_guard_lets_turn_end(self):
        self.bind()
        payload = {"session_id": "s1", "transcript_path": self.transcript}
        first = self.run_hook("stop", dict(payload, stop_hook_active=False))
        self.assertEqual(first["decision"], "block")
        second = self.run_hook("stop", dict(payload, stop_hook_active=True))
        self.assertNotIn("decision", second or {})
        self.assertIn("stalled", second["systemMessage"])

    def test_a_project_asks_once_per_item_even_if_the_host_never_reports_a_continued_stop(self):
        self.bind()
        payload = {"session_id": "s1", "transcript_path": self.transcript, "stop_hook_active": False}
        first = self.run_hook("stop", payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn("once per item", first["reason"])
        before = [e["id"] for e in sl.untracked(self.project, sl.load_ledger(self.project), session="s1")]
        for _ in range(3):                                     # the host says nothing about the continued Stop: no loop
            again = self.run_hook("stop", payload)
            self.assertNotIn("decision", again or {})
        self.assertEqual([e["id"] for e in sl.untracked(self.project, sl.load_ledger(self.project), session="s1")], before)
        sl.append_inbox(self.project, "otra cosa que apuntar", "s1", "prompt")     # a new item is asked about, once
        second = self.run_hook("stop", payload)
        new_id = "I-%d" % (len(before) + 1)
        self.assertEqual(second["decision"], "block")
        self.assertIn(new_id + " untracked", second["reason"])
        self.assertNotIn("I-1 untracked", second["reason"])
        self.assertNotIn("decision", self.run_hook("stop", payload) or {})

    def test_a_problem_that_comes_back_after_being_fixed_is_asked_about_again(self):
        self.bind()
        payload = {"session_id": "s1", "transcript_path": self.transcript, "stop_hook_active": False}
        self.assertEqual(self.run_hook("stop", payload)["decision"], "block")     # reads the transcript into the inbox
        self.track_all()

        def status(value):
            data = sl.load_ledger(self.project)
            data["items"][0]["status"] = value
            sl.save_ledger(self.project, data)

        status("done")                                         # done without evidence
        self.assertIn("without evidence", self.run_hook("stop", payload)["reason"])
        self.assertNotIn("decision", self.run_hook("stop", payload) or {})
        status("open")
        self.assertIsNone(self.run_hook("stop", payload))      # all clear: the memory of what was asked is dropped
        status("done")
        self.assertIn("without evidence", self.run_hook("stop", payload)["reason"])

    def test_drops_are_announced_once(self):
        self.bind()
        sl.append_inbox(self.project, "quita el código de barras", "s1", "prompt")
        data = sl.load_ledger(self.project)
        sl.add_item(data, "barcode", ["I-1"])
        sl.set_status(self.project, data, "R-1", "dropped", by_user="I-1")
        sl.save_ledger(self.project, data)
        out = self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False})
        self.assertIn("R-1", out["systemMessage"])
        self.assertIsNone(self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False}))

    def test_accept_edit_and_verify_are_announced_to_her(self):
        self.bind()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "x", ["legacy"])
        sl.save_ledger(self.project, data)
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "a") as f:
            f.write(" ")
        sl.accept_edit(self.project)
        vdir = os.path.join(sl.studio_dir(self.project), "verify")
        os.makedirs(vdir)
        with open(os.path.join(vdir, "R-1.md"), "w") as f:
            f.write("PASS\n")
        sl.write_seal(os.path.join(vdir, "R-1.md"))
        data = sl.load_ledger(self.project)
        sl.set_status(self.project, data, "R-1", "verified")
        sl.save_ledger(self.project, data)
        out = self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False})
        self.assertIn("accept-edit", out["systemMessage"])
        self.assertIn("R-1 verified", out["systemMessage"])

    def test_tampered_ledger_reported_not_crashing(self):
        self.bind()
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "a") as f:
            f.write(" ")
        out = self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False})
        self.assertIn("accept-edit", out["reason"])

    def test_corrupt_ledger_fails_open(self):
        self.bind()
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "w") as f:
            f.write("{broken")
        out = self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False})
        self.assertNotIn("decision", out)  # never blocks on its own error
        self.assertIn("enforcement off", out["systemMessage"])
        self.assertTrue(os.path.isfile(os.path.join(self.home, "hooks.log")))

    def test_session_start_summarises(self):
        self.bind()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "pantalla de perfil dietético", ["legacy"])
        sl.save_ledger(self.project, data)
        out = self.run_hook("session-start", {"session_id": "s1", "source": "compact"})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("R-1", ctx)
        self.assertIn("budgit ledger", ctx)

    def test_post_compact_logs(self):
        self.bind()
        self.run_hook("post-compact", {"session_id": "s1", "trigger": "auto", "compact_summary": "sum"})
        log = open(os.path.join(sl.studio_dir(self.project), "compactions.log"), encoding="utf-8").read()
        self.assertIn("sum", log)

    def test_a_real_data_guard_that_raises_asks_instead_of_letting_the_call_through(self):
        import hook
        sl.activate(self.project, bind=False)
        real = sl.real_data_check                    # R-493: the hook calls real_data_check (decision + memory keys)
        def real_data_check(*a, **k):
            raise RuntimeError("boom")
        sl.real_data_check = real_data_check
        self.addCleanup(lambda: setattr(sl, "real_data_check", real))
        hook._OUT.clear()
        self.addCleanup(hook._OUT.clear)
        hook.on_pre_tool_use({"tool_name": "Write", "cwd": self.project,
                              "tool_input": {"file_path": os.path.join(self.project, "a.txt"), "content": "x"}}, self.project)
        out = hook._OUT["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("real_data_decision", out["permissionDecisionReason"])
