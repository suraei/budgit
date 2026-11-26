"""R-915 (2): one role table (default model + ceiling); the PreToolUse(Agent) hook sets a missing model= to the role default,
lowers a dearer one to the ceiling, lets `ESCALATE:` lift the ceiling one step (logged), and never touches a fork."""
import json, os, subprocess, sys, time

from helpers import StudioTestCase, ROOT
import budget

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


class ModelPerRoleTest(StudioTestCase):
    def run_hook(self, tool_input, tid="toolu_r1"):
        payload = {"session_id": "s1", "tool_name": "Agent", "tool_use_id": tid, "tool_input": tool_input, "cwd": self.tmp}
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), capture_output=True, text=True,
                           env=dict(os.environ, STUDIO_HOME=self.home, BUDGIT_ADMIT_ADVICE="off"), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def test_no_model_on_a_builder_gets_the_builder_default(self):
        budget.set_override("ample")
        out = self.run_hook({"prompt": "p", "subagent_type": "budgit:builder"})
        self.assertEqual(out["updatedInput"]["model"], budget.ROLE_TABLE["work"][0])
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    def test_opus_on_a_builder_is_lowered(self):
        budget.set_override("ample")
        out = self.run_hook({"prompt": "p", "subagent_type": "budgit:builder", "model": "opus"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    def test_opus_on_a_verifier_passes_at_normal_and_is_lowered_when_tight(self):
        budget.set_override("normal")
        self.assertIsNone(self.run_hook({"prompt": "p", "subagent_type": "budgit:verifier", "model": "opus"}))
        budget.set_override("tight")
        out = self.run_hook({"prompt": "p", "subagent_type": "budgit:verifier", "model": "opus"}, tid="toolu_r2")
        self.assertEqual(out["updatedInput"]["model"], "sonnet")
        # ESCALATE lifts the tight ceiling one step, and is logged
        self.assertIsNone(self.run_hook({"prompt": "p\nESCALATE: sonnet missed it twice", "subagent_type": "budgit:verifier",
                                         "model": "opus"}, tid="toolu_r6"))
        with open(os.path.join(self.home, "model-escalations.log")) as f:
            self.assertIn("toolu_r6", f.read())

    def test_critical_is_the_strictest_level_for_every_role(self):
        budget.set_override("critical")
        out = self.run_hook({"prompt": "p", "subagent_type": "budgit:builder", "model": "opus"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")
        for kind in ("budgit:builder", "general-purpose", "budgit:verifier", "Explore", "other:thing"):
            self.assertLessEqual(budget.tier(budget.ceiling_for(kind, "critical")), budget.tier(budget.ceiling_for(kind, "normal")), kind)

    def test_inherit_gets_the_role_default_and_fork_stays_untouched(self):
        budget.set_override("normal")
        out = self.run_hook({"prompt": "p", "subagent_type": "budgit:builder", "model": "inherit"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")
        self.assertIsNone(self.run_hook({"prompt": "p", "subagent_type": "fork", "model": "inherit"}, tid="toolu_r8"))

    def read_log(self, name):
        try:
            with open(os.path.join(self.home, name)) as f:
                return f.read()
        except OSError:
            return ""

    def run_logs(self):
        return [json.loads(l) for l in self.read_log("model-runs.log").splitlines() if l.strip()]

    def run_stop(self, transcript, agent_id="a1"):
        payload = {"session_id": "s1", "agent_id": agent_id, "agent_transcript_path": transcript, "cwd": self.tmp}
        r = subprocess.run([sys.executable, HOOK, "subagent-stop"], input=json.dumps(payload), capture_output=True, text=True,
                           env=dict(os.environ, STUDIO_HOME=self.home, BUDGIT_ADMIT_ADVICE="off"), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_escalation_log_only_when_kept(self):
        budget.set_override("normal")
        out = self.run_hook({"prompt": "p\nESCALATE: need it", "subagent_type": "budgit:builder", "model": "fable"}, tid="toolu_r9")
        self.assertEqual(out["updatedInput"]["model"], "opus")
        self.assertNotIn("toolu_r9", self.read_log("model-escalations.log"))      # lowered: not an escalation that held
        self.assertIsNone(self.run_hook({"prompt": "p\nESCALATE: need it", "subagent_type": "budgit:builder", "model": "opus"},
                                        tid="toolu_r10"))
        self.assertIn("toolu_r10 opus opus", self.read_log("model-escalations.log"))

    def test_lowered_note_with_escalate_says_one_step(self):
        budget.set_override("normal")
        out = self.run_hook({"prompt": "p\nESCALATE: need it", "subagent_type": "budgit:builder", "model": "fable"})
        note = out["additionalContext"]
        self.assertIn("lifts one step", note)
        self.assertIn("work -> opus", note)
        self.assertIn("ESCALATE-FABLE: <why>", note)
        self.assertNotIn("put `ESCALATE: <why>`", note)           # it already has the marker: no advice to add it
        plain = self.run_hook({"prompt": "p", "subagent_type": "budgit:builder", "model": "fable"}, tid="toolu_r11")
        self.assertIn("ESCALATE: <why>", plain["additionalContext"])

    def test_run_log_line_per_agent_call(self):
        budget.set_override("normal")
        secret = "SECRET-PROMPT-BODY"
        self.run_hook({"prompt": secret + "\nESCALATE: need it", "subagent_type": "budgit:builder", "model": "fable"}, tid="toolu_a")
        self.run_hook({"prompt": "p", "subagent_type": "budgit:builder", "model": "sonnet"}, tid="toolu_b")
        self.assertNotIn(secret, self.read_log("model-runs.log"))
        rows = {r["tool_use_id"]: r for r in self.run_logs()}
        self.assertEqual(set(rows), {"toolu_a", "toolu_b"})
        a = rows["toolu_a"]
        self.assertEqual((a["session"], a["role"], a["level"], a["requested"], a["passed"], a["marker"]),
                         ("s1", "work", "normal", "fable", "opus", "ESC"))
        self.assertEqual(a["reason_len"], len("need it"))
        self.assertEqual((rows["toolu_b"]["marker"], rows["toolu_b"]["passed"]), ("none", "sonnet"))

    def transcript(self, prompt, models):
        path = os.path.join(self.tmp, "agent.jsonl")
        recs = [{"type": "user", "message": {"role": "user", "content": prompt}}]
        recs += [{"type": "assistant", "message": {"model": m, "content": [{"type": "text", "text": "ok"}]}} for m in models]
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
        return path

    def test_served_model_recorded_and_mismatch(self):
        budget.set_override("normal")
        self.run_hook({"prompt": "do it", "subagent_type": "budgit:builder", "model": "fable"}, tid="toolu_s")
        self.run_stop(self.transcript("do it", ["claude-opus-5-5", "claude-opus-5-5"]))
        served = [r for r in self.run_logs() if r.get("served")]
        self.assertEqual(len(served), 1)
        self.assertEqual((served[0]["tool_use_id"], served[0]["served"], served[0]["mismatch"]), ("toolu_s", "claude-opus-5-5", 1))

    def test_served_unknown_with_a_named_reason_when_the_transcript_is_missing(self):
        budget.set_override("normal")
        self.run_hook({"prompt": "do it", "subagent_type": "budgit:builder", "model": "opus"}, tid="toolu_u")
        self.run_stop(self.transcript("do it", ["claude-opus-5-5"]))
        self.run_hook({"prompt": "other", "subagent_type": "budgit:builder", "model": "opus"}, tid="toolu_v")
        self.run_stop(os.path.join(self.tmp, "nowhere.jsonl"), agent_id="a2")
        rows = {r["agent_id"]: r for r in self.run_logs() if r.get("served")}
        self.assertEqual((rows["a1"]["tool_use_id"], rows["a1"]["mismatch"]), ("toolu_u", 0))
        self.assertEqual((rows["a2"]["served"], rows["a2"]["reason"], rows["a2"]["mismatch"]), ("unknown", "no-transcript", 0))

    def test_fable_without_marker_lowered(self):
        budget.set_override("normal")
        out = self.run_hook({"prompt": "p", "subagent_type": "budgit:builder", "model": "fable"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    def test_judgement_plain_escalate_keeps_fable(self):
        budget.set_override("normal")
        self.assertIsNone(self.run_hook({"prompt": "p\nESCALATE: need it", "subagent_type": "budgit:verifier", "model": "fable"}))

    def test_mechanical_escalate_opus_is_lowered_to_sonnet(self):
        budget.set_override("normal")
        out = self.run_hook({"prompt": "p\nESCALATE: need it", "subagent_type": "Explore", "model": "opus"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    FABLE = "ESCALATE-FABLE: direction trial needs top model\nReply OK"

    def test_escalate_fable_keeps_fable_for_work(self):
        budget.set_override("normal")
        self.assertIsNone(self.run_hook({"prompt": self.FABLE, "subagent_type": "general-purpose", "model": "fable"}))
        self.assertEqual(self.run_logs()[0]["marker"], "ESC-FABLE")

    def test_escalate_fable_keeps_fable_for_director(self):
        budget.set_override("normal")
        self.assertIsNone(self.run_hook({"prompt": self.FABLE, "subagent_type": "director", "model": "fable"}))

    def test_escalate_fable_short_reason_lowered(self):
        budget.set_override("normal")
        out = self.run_hook({"prompt": "ESCALATE-FABLE: x", "subagent_type": "general-purpose", "model": "fable"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    def test_de_and_no_escalate_fable_never_match(self):
        budget.set_override("normal")
        for i, text in enumerate(("DE-ESCALATE-FABLE: direction trial needs top model", "NOESCALATE-FABLE: direction trial needs top",
                                  "NO-ESCALATE-FABLE: direction trial needs top")):
            out = self.run_hook({"prompt": text, "subagent_type": "general-purpose", "model": "fable"}, tid="toolu_n%d" % i)
            self.assertEqual(out["updatedInput"]["model"], "sonnet", text)

    def test_escalate_fable_reason_is_counted_after_stripping(self):
        budget.set_override("normal")
        out = self.run_hook({"prompt": "ESCALATE-FABLE: a" + " " * 7, "subagent_type": "general-purpose", "model": "fable"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    def lowered_fable(self, prompt):
        budget.set_override("normal")
        out = self.run_hook({"prompt": prompt, "subagent_type": "general-purpose", "model": "fable"})
        return out and out["updatedInput"]["model"]

    def test_the_lowered_note_pasted_as_a_prompt_does_not_escalate(self):
        note = ("budgit: model=fable lowered to opus (work role, budget normal). ESCALATE lifts one step (work -> opus); for fable use "
                "`ESCALATE-FABLE: <why>` with a reason on the same line.")
        self.assertEqual(self.lowered_fable(note), "sonnet")

    def test_a_marker_in_backticks_or_a_fence_or_a_quote_does_not_escalate(self):
        reason = "direction trial needs top model"
        for text in ("`ESCALATE-FABLE: %s`" % reason, "```\nESCALATE-FABLE: %s\n```" % reason,
                     "> ESCALATE-FABLE: %s" % reason, "see ESCALATE-FABLE: %s" % reason):
            self.assertEqual(self.lowered_fable(text), "sonnet", text)

    def test_invisible_characters_and_an_angle_bracket_are_no_reason(self):
        for text in ("ESCALATE-FABLE: " + chr(0x200B) * 8, "ESCALATE-FABLE: <why> and more words"):
            self.assertEqual(self.lowered_fable(text), "sonnet", text)

    def test_a_real_reason_at_the_start_of_the_first_line_keeps_fable(self):
        self.assertIsNone(self.lowered_fable("  ESCALATE-FABLE: direction trial needs top model\nReply OK"))

    def test_tilde_fences_and_longer_fences_hold_the_marker_back(self):
        reason = "ESCALATE-FABLE: direction trial needs top model"
        for text in ("~~~\n%s\n~~~" % reason, "```\n    ```\n%s\n```" % reason, "````\n```\n%s\n````" % reason,
                     "    %s" % reason, "\t%s" % reason):
            self.assertEqual(self.lowered_fable(text), "sonnet", text)

    def test_a_reason_of_invisible_fillers_is_no_reason(self):
        self.assertEqual(self.lowered_fable("ESCALATE-FABLE: " + chr(0x3164) * 8), "sonnet")
        self.assertEqual(self.lowered_fable("ESCALATE-FABLE: ab, ...... !!"), "sonnet")      # fewer than 3 letters or digits

    def test_a_fence_closer_holds_only_whitespace_after_its_run(self):
        self.assertEqual(self.lowered_fable("```\n```js\nESCALATE-FABLE: direction trial needs top model\n```"), "sonnet")

    def test_combining_marks_do_not_count_toward_the_reason(self):
        self.assertEqual(self.lowered_fable("ESCALATE-FABLE: abc" + chr(0xFE0F) * 5), "sonnet")

    def test_only_a_newline_ends_a_line(self):
        self.assertEqual(self.lowered_fable("see" + chr(0x85) + "ESCALATE-FABLE: direction trial needs top model"), "sonnet")

    def test_the_cap_fallback_tolerates_a_non_dict_input(self):
        import hook
        from unittest import mock
        sent = []
        with mock.patch.object(hook, "emit", sent.append):
            hook.fill_agent_model("not a dict")
        self.assertEqual(sent, [])

    def test_an_error_on_the_decision_path_lowers_to_the_cheapest_tier(self):
        import hook
        from unittest import mock
        sent = []
        with mock.patch.object(budget, "current", side_effect=RuntimeError("boom")), mock.patch.object(hook, "emit", sent.append):
            hook.fill_agent_model({"session_id": "s1", "tool_use_id": "t1",
                                   "tool_input": {"prompt": "p", "subagent_type": "general-purpose", "model": "fable"}})
        self.assertEqual(sent[0]["hookSpecificOutput"]["updatedInput"]["model"], "haiku")

    def test_a_fifo_as_the_transcript_path_returns_in_time(self):
        fifo = os.path.join(self.tmp, "agent.fifo")
        os.mkfifo(fifo)
        code = ("import sys, time, budget\nstart = time.time()\nbudget.record_served('s1', 'a1', sys.argv[1])\n"
                "print(time.time() - start)\n")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), STUDIO_HOME=self.home)
        done = subprocess.run([sys.executable, "-c", code, fifo], env=env, timeout=60, check=True, capture_output=True, text=True)
        self.assertLess(float(done.stdout), 2)      # the call itself, not the interpreter start under load
        row = [r for r in self.run_logs() if r.get("served")][0]
        self.assertEqual((row["served"], row["reason"]), ("unknown", "not-regular-file"))

    def test_served_not_the_passed_family_sets_cap_ignored(self):
        budget.set_override("normal")
        self.run_hook({"prompt": "first", "subagent_type": "budgit:builder", "model": "fable"}, tid="toolu_c1")
        self.run_hook({"prompt": "second", "subagent_type": "budgit:builder", "model": "fable"}, tid="toolu_c2")
        self.run_stop(self.transcript("first", ["claude-opus-5-5"]), agent_id="a1")
        self.run_stop(self.transcript("second", ["claude-sonnet-5-5"]), agent_id="a2")
        rows = {r["tool_use_id"]: r for r in self.run_logs() if r.get("served")}
        self.assertEqual((rows["toolu_c1"]["cap_ignored"], rows["toolu_c1"]["mismatch"]), (1, 1))
        self.assertEqual((rows["toolu_c2"]["cap_ignored"], rows["toolu_c2"]["mismatch"]), (0, 1))

    def test_a_symlinked_run_log_never_stops_the_cap(self):
        budget.set_override("normal")
        os.symlink(os.path.join(self.tmp, "elsewhere.log"), os.path.join(self.home, "model-runs.log"))
        out = self.run_hook({"prompt": "p", "subagent_type": "general-purpose", "model": "fable"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    def test_a_read_only_log_folder_never_stops_the_cap(self):
        budget.set_override("normal")
        os.chmod(self.home, 0o500)
        try:
            out = self.run_hook({"prompt": "p\nESCALATE: x", "subagent_type": "general-purpose", "model": "fable"})
        finally:
            os.chmod(self.home, 0o700)
        self.assertEqual(out["updatedInput"]["model"], "opus")

    def test_the_run_log_rotates_once_over_a_megabyte(self):
        budget.set_override("normal")
        path = os.path.join(self.home, "model-runs.log")
        with open(path, "w") as f:
            f.write("x" * (1024 * 1024 + 1))
        self.run_hook({"prompt": "p", "subagent_type": "general-purpose", "model": "sonnet"}, tid="toolu_rot")
        self.assertTrue(os.path.exists(path + ".1"))
        self.assertLess(os.path.getsize(path), 4096)
        self.assertEqual([r["tool_use_id"] for r in self.run_logs()], ["toolu_rot"])

    def test_two_writers_rotating_at_once_lose_no_line(self):
        trials, lines = 40, 40
        base = os.path.join(self.tmp, "race")
        for i in range(trials):
            os.makedirs(os.path.join(base, "t%d" % i))
            with open(os.path.join(base, "t%d" % i, "model-runs.log"), "w") as f:
                f.write(("x" * 99 + "\n") * lines)
        worker = ("import sys, time, os, budget\n"
                  "base, start, trials, who = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), sys.argv[4]\n"
                  "budget.RUNS_MAX_BYTES = 2000\n"
                  "for i in range(trials):\n"
                  "    path = os.path.join(base, 't%d' % i, 'model-runs.log')\n"
                  "    budget._runs_path = lambda path=path: path\n"
                  "    time.sleep(max(0, start + i * 0.05 - time.time()))\n"
                  "    budget._append_run({'w': who})\n")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), STUDIO_HOME=self.home)
        start = time.time() + 1.0
        procs = [subprocess.Popen([sys.executable, "-c", worker, base, str(start), str(trials), w], env=env) for w in "ab"]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        for i in range(trials):
            path = os.path.join(base, "t%d" % i, "model-runs.log")
            total = 0
            for p in (path, path + ".1"):
                if os.path.exists(p):
                    with open(p) as f:
                        total += len(f.read().splitlines())
            self.assertEqual(total, lines + 2, "trial %d lost lines" % i)

    def test_escalate_lifts_a_builder_one_step_only(self):
        budget.set_override("normal")
        self.assertIsNone(self.run_hook({"prompt": "p\nESCALATE: x", "subagent_type": "budgit:builder", "model": "opus"}))
        out = self.run_hook({"prompt": "p\nESCALATE: x", "subagent_type": "budgit:builder", "model": "fable"}, tid="toolu_r7")
        self.assertEqual(out["updatedInput"]["model"], "opus")

    def test_general_purpose_with_all_tools_gets_the_tables_model(self):
        budget.set_override("ample")
        out = self.run_hook({"prompt": "x\nALL-TOOLS: needs the browser", "subagent_type": "general-purpose"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")
        out = self.run_hook({"prompt": "x\nALL-TOOLS: needs the browser", "subagent_type": "general-purpose", "model": "opus"},
                            tid="toolu_r3")
        self.assertEqual(out["updatedInput"]["model"], "sonnet")

    def test_fork_is_untouched(self):
        budget.set_override("ample")
        self.assertIsNone(self.run_hook({"prompt": "p", "subagent_type": "fork"}))
        self.assertIsNone(self.run_hook({"prompt": "p", "subagent_type": "fork", "model": "opus"}, tid="toolu_r4"))

    def test_an_unknown_agent_type_gets_the_safest_default(self):
        budget.set_override("ample")
        out = self.run_hook({"prompt": "p", "subagent_type": "other-plugin:thing"})
        self.assertEqual(out["updatedInput"]["model"], "sonnet")
        out = self.run_hook({"prompt": "p", "subagent_type": "other-plugin:thing", "model": "opus"}, tid="toolu_r5")
        self.assertEqual(out["updatedInput"]["model"], "sonnet")
