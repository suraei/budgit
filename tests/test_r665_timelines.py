"""R-665: the hand-back timeline line, the claim released with it, the stand-up waste list and the keep-alive silence flag.
Fake project names only. The real on_subagent_stop path of hook.py is called."""
import json
import os
import sys
import time
from unittest import mock

from helpers import StudioTestCase
import hook
import studiolib as sl
import timelines
import jobs
import portfolio

NOW = 1_800_000_000.0
DAY_H = 86400.0        # seconds in a day, for the offsets below


def at(offset):
    return timelines._stamp(NOW + offset)


class TimelinesTest(StudioTestCase):
    def make_project(self, name, with_item=False):
        p = os.path.join(self.tmp, name)
        os.makedirs(p)
        sl.ensure_studio_dir(p)
        data = sl.empty_ledger()
        if with_item:
            sl.add_item(data, "build it", "I-1", item_id="R-5")
        sl.save_ledger(p, data)
        return p

    def transcript(self, prompt, calls=3, waits=0, seconds=120, denied=0, benign=False, failed=None):
        name = os.path.join(self.tmp, "agent-%d.jsonl" % len(os.listdir(self.tmp)))
        rows = [{"type": "user", "timestamp": at(0), "message": {"role": "user", "content": prompt}}]
        if failed:                                          # an is_error result of a failed command that merely quotes text
            rows.append({"type": "user", "timestamp": at(1), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "f1", "is_error": True, "content": failed}]}})
        if denied:                                          # a call the guard refused: a tool_result with is_error true
            rows.append({"type": "user", "timestamp": at(1), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "d1", "is_error": True,
                 "content": "PreToolUse:Bash hook error: budgit guard: this command is refused"}]}})
        if benign:                                          # the marker text in a result that is NOT an error: never a refusal
            rows.append({"type": "user", "timestamp": at(1), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "b1", "content": [{"type": "text", "text": "budgit guard: quoted in a file"}]}]}})
        for i in range(calls):
            tool = {"name": "Bash", "input": {"command": "sleep 5" if i < waits else "ls"}}
            rows.append({"type": "assistant", "timestamp": at(seconds * (i + 1) / calls),
                         "message": {"content": [dict(tool, type="tool_use", id="t%d" % i)]}})
        with open(name, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        return name

    def stop(self, project, prompt="task: R-5 build the thing\nBudget: 2\nrest", message="Built it.\nmore", **kw):
        inp = {"agent_id": "a%d" % len(os.listdir(self.tmp)), "agent_type": "budgit:builder", "last_assistant_message": message,
               "agent_transcript_path": self.transcript(prompt, **kw), "session_id": "s-1"}
        hook.on_subagent_stop(inp, project)
        return inp

    def lines(self, project):
        return timelines.read_lines(project)

    def test_one_line_per_handback(self):
        p = self.make_project("alpha")
        self.stop(p)
        self.stop(p, message="Second.")
        got = self.lines(p)
        self.assertEqual(len(got), 2)
        first = got[0]
        self.assertEqual((first["project"], first["agent"], first["task"], first["calls"], first["wall_s"], first["budget"],
                          first["result"]), ("alpha", "budgit:builder", "build the thing".join(("R-5 ", "")), 3, 120, 2, "Built it."))
        self.assertTrue(timelines._epoch(first["ts"]))

    def test_busy_lock_still_writes_line(self):
        p = self.make_project("alpha")
        inp = {"agent_id": "busy1", "agent_type": "budgit:builder", "last_assistant_message": "Built it under a busy lock.",
               "agent_transcript_path": self.transcript("task: R-5 build the thing\nBudget: 2"), "session_id": "s-1"}
        with sl.locked(p):                                  # the project lock is held: the hook takes the when_busy path
            with sl.try_locked(p, 0.1) as got:
                self.assertFalse(got)
            hook.when_busy("subagent-stop", inp, p)
        got = self.lines(p)
        self.assertEqual([r["result"] for r in got], ["Built it under a busy lock."])
        self.assertEqual(got[0]["calls"], 3)

    def test_no_project_writes_nothing(self):
        alpha = self.make_project("alpha")
        inp = {"agent_id": "x1", "agent_type": "budgit:builder", "last_assistant_message": "x",
               "agent_transcript_path": self.transcript("task: R-5 x")}
        self.assertIsNone(timelines.record(None, inp))
        self.assertIsNone(timelines.record("", inp))
        found = [n for _, _, names in os.walk(self.tmp) for n in names if n == timelines.NAME]      # fake home and projects
        self.assertEqual(found, [])
        self.assertFalse(os.path.exists(os.path.join(sl.home_state(), "hooks.log")), "the guard, not a swallowed error, stopped the write")
        self.assertEqual(self.lines(alpha), [])

    def test_clean_handback_is_not_refused_or_red(self):
        p = self.make_project("alpha")
        for msg in ("Done.\nRefused: none.", "Done.\nNothing refused.", "Done.\nanything refused: no",
                    "Done.\nGate, red first: Ran 12 tests, OK", "Done.\nThe red then green gate passed."):
            self.stop(p, message=msg)
        self.assertEqual([(r["refused"], r["red"]) for r in self.lines(p)], [(False, False)] * 5)

    def test_real_refusal_and_red_are_seen(self):
        p = self.make_project("alpha")
        cases = [("Stopped.\nRefused: the Bash call was denied by a guard", (True, False)),
                 ("Refused: the copy command", (True, False)),
                 ("Ran 3 tests\nFAILED (failures=1)", (False, True)),
                 ("FAILED (errors=2)\nfixed it\nRan 3 tests\nOK", (False, False)),
                 ("The gate is red.", (False, True))]
        for msg, _ in cases:                                # a refusal is in the transcript; the Refused: line is only the fallback
            self.stop(p, message=msg, denied=int("Refused:" in msg))
        self.assertEqual([(r["refused"], r["red"]) for r in self.lines(p)], [want for _, want in cases])

    def test_refusal_counted_from_transcript(self):
        prose = ("I refused to guess the value and asked.", "The Bash guard refused my copy command; I did not reshape it.",
                 "refused by the guard, not by the hook")
        p = self.make_project("alpha")
        for text in prose:                                  # prose alone, clean transcript: not a refusal
            self.stop(p, message="Done.\n" + text)
        for text in prose:                                  # the same prose, one denied tool_result in the transcript: a refusal
            self.stop(p, message="Done.\n" + text, denied=1)
        self.stop(p, message="Done. Clean hand-back text.", denied=1)       # the transcript alone carries the truth
        self.stop(p, message="Done.", benign=True)          # marker text in a result without is_error: not a refusal
        self.assertEqual([r["refused"] for r in self.lines(p)], [False] * 3 + [True] * 4 + [False])
        timelines.record(p, {"agent_type": "budgit:builder", "last_assistant_message": "Done.\nRefused: the copy command"})
        self.assertTrue(self.lines(p)[-1]["refused"])       # no transcript: the structured line is the fallback

    def test_quoted_markers_in_failed_results_are_not_refusals(self):
        p = self.make_project("alpha")
        self.stop(p, message="Done.", failed="Ran 3 tests\nFAIL: test_x\nAssertionError: budgit guard: this command is refused\nFAILED (failures=1)")
        self.stop(p, message="Done.", failed="grep: error\nlog line: PreToolUse:Bash hook error: budgit guard: quoted")
        self.stop(p, message="Done.", failed="3:PreToolUse:Bash hook error: budgit guard: quoted by a failed grep")
        self.assertEqual([r["refused"] for r in self.lines(p)], [False] * 3)

    def test_waste_minutes_never_exceed_wall(self):
        base = {"wall_s": 600, "calls": 20, "waits": 10, "budget": 10}
        for extra, cause in (({"refused": True, "red": True}, "refusals"), ({"red": True}, "red gates"), ({}, "over-budget calls")):
            got = timelines.waste_minutes(dict(base, **extra))
            self.assertLessEqual(sum(got.values()), 10 + 1e-9)
            self.assertGreater(got[cause], 0)
            self.assertEqual([c for c, m in got.items() if m > 0], [cause])           # one cause per hand-back
        self.assertEqual(timelines.waste_minutes({"wall_s": 600, "calls": 20, "waits": 10, "budget": 0})["idle waits"], 5.0)

    def test_timelines_file_is_ignored(self):
        p = self.make_project("alpha")
        sl.ensure_gitignore(p)
        with open(os.path.join(p, ".gitignore")) as f:
            self.assertTrue(any(l.rstrip("/").endswith("timelines.jsonl") for l in f.read().splitlines()))

    def test_cli_prints_standup_block(self):
        import argparse
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        from helpers import ROOT
        loader = importlib.machinery.SourceFileLoader("budgit_cli_r665", os.path.join(ROOT, "plugin", "bin", "budgit"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
        quiet, busy = self.make_project("quiet", with_item=True), self.make_project("busy", with_item=True)
        with open(timelines.path(busy), "w") as f:
            f.write(json.dumps({"ts": timelines._stamp(time.time() - 60), "wall_s": 600, "calls": 20, "waits": 0, "budget": 10}) + "\n")
        portfolio.save({"version": 1, "projects": [{"path": q, "name": None, "pm": None, "channel": "current", "reason": None}
                                                   for q in (quiet, busy)]})
        out = {}
        for op in ("standup", "silent"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(mod.cmd_timelines(argparse.Namespace(op=op)), 0)
            out[op] = buf.getvalue()
        self.assertIn("over-budget calls 5.0 min", out["standup"])
        self.assertIn("timelines: quiet has no timeline line in the last 24 h", out["silent"])
        self.assertNotIn("busy has no timeline", out["silent"])

    def test_write_failure_never_breaks_the_hook(self):
        p = self.make_project("alpha")
        with mock.patch.object(timelines, "_append", side_effect=OSError("disk full")):
            self.stop(p)                                    # raises nothing
        self.assertEqual(self.lines(p), [])
        with open(os.path.join(sl.home_state(), "hooks.log")) as f:
            self.assertIn("timelines: the hand-back line was not written", f.read())

    def test_claim_released_at_handback(self):
        p = self.make_project("alpha", with_item=True)
        import runtrack
        runtrack.on_launch(p, {"subagent_type": "budgit:builder", "description": "build", "prompt": "task: R-5 build the thing"}, names=())
        held = next(i for i in sl.load_ledger(p)["items"] if i["id"] == "R-5")
        self.assertTrue(held.get("doing_by"))
        self.stop(p, prompt="task: R-5 build the thing")
        item = next(i for i in sl.load_ledger(p)["items"] if i["id"] == "R-5")
        self.assertNotIn("doing_by", item)
        self.assertEqual(self.lines(p)[0]["released"], ["R-5"])
        q = self.make_project("beta", with_item=True)       # two runs hold R-5: one hand-back books an event but drops no claim
        for _ in range(2):
            runtrack.on_launch(q, {"subagent_type": "budgit:builder", "description": "build", "prompt": "task: R-5 build the thing"}, names=())
        self.stop(q, prompt="task: R-5 build the thing")
        still = next(i for i in sl.load_ledger(q)["items"] if i["id"] == "R-5")
        self.assertTrue(still.get("doing_by"))
        self.assertEqual(self.lines(q)[0]["released"], [])

    def test_standup_lists_top_three_waste(self):
        p = self.make_project("alpha")
        rows = [{"wall_s": 600, "calls": 20, "waits": 10, "budget": 10},                 # over 5 min, idle 5 min
                {"wall_s": 120, "calls": 4, "waits": 0, "budget": 0, "refused": True},   # refusal 2 min
                {"wall_s": 60, "calls": 4, "waits": 0, "budget": 0, "red": True},        # red 1 min
                {"wall_s": 6000, "calls": 4, "waits": 4, "budget": 0, "ts": at(-3 * DAY_H)}]  # older than 24 h: left out
        with open(timelines.path(p), "w") as f:
            for r in rows:
                f.write(json.dumps(dict({"ts": at(-60), "refused": False, "red": False}, **r)) + "\n")
        top = timelines.top_waste([p], now=NOW)
        self.assertEqual(top, [("over-budget calls", 5.0), ("refusals", 2.0), ("red gates", 1.0)])      # one cause per line: the idle share is not added
        self.assertEqual(len(timelines.top_waste([p], now=NOW, limit=3)), 3)
        self.assertIn("over-budget calls 5.0 min", timelines.standup_block([p], now=NOW))

    def test_keepalive_flags_silent_project(self):
        quiet, busy = self.make_project("quiet"), self.make_project("busy")
        with open(timelines.path(busy), "w") as f:
            f.write(json.dumps({"ts": at(-3600)}) + "\n")
        with open(timelines.path(quiet), "w") as f:
            f.write(json.dumps({"ts": at(-2 * DAY_H)}) + "\n")
        self.assertEqual(timelines.silent_lines([quiet, busy], now=NOW),
                         ["timelines: quiet has no timeline line in the last 24 h"])

    def job_prompt(self, name, projects):
        """The prompt the real jobs.run hands to the runner, with a fake home and fake registered projects."""
        portfolio.save({"version": 1, "projects": [{"path": p, "name": None, "pm": None, "channel": "current", "reason": None}
                                                   for p in projects]})
        seen = []
        runner = lambda cmd, **kw: seen.append(cmd) or mock.Mock(returncode=0)
        code = jobs.run(name, runner=runner, level="normal", busy=lambda: (False, "free"), claude=sys.executable, todo=lambda n: False)      # todo says whether there is nothing to do
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        self.assertNotIn(jobs.TIMELINES_MARK, seen[0][2])
        return seen[0][2]

    def test_standup_prompt_gets_waste_block(self):
        p = self.make_project("alpha", with_item=True)
        with open(timelines.path(p), "w") as f:
            f.write(json.dumps({"ts": timelines._stamp(time.time() - 60), "wall_s": 600, "calls": 20, "waits": 0, "budget": 10}) + "\n")
        self.assertIn("Waste, last 24 h (top 1 by minutes): over-budget calls 5.0 min", self.job_prompt("standup", [p]))
        self.assertIn("none recorded", self.job_prompt("standup", []))              # empty: a plain line, never the raw mark

    def test_keepalive_prompt_gets_silent_lines(self):
        quiet, busy = self.make_project("quiet", with_item=True), self.make_project("busy", with_item=True)
        with open(timelines.path(busy), "w") as f:
            f.write(json.dumps({"ts": timelines._stamp(time.time() - 60)}) + "\n")
        got = self.job_prompt("keepalive", [quiet, busy])
        self.assertIn("timelines: quiet has no timeline line in the last 24 h", got)
        self.assertNotIn("busy has no timeline", got)
        self.assertIn("no silent active project", self.job_prompt("keepalive", [busy]))

    def test_replay_fills_every_project(self):
        names = ["north", "south", "east"]
        projects = {n: self.make_project(n) for n in names}
        recorded = [("north", "Done A."), ("south", "Done B."), ("east", "Done C."), ("north", "Done D."), ("south", "Done B2.\nRefused: the cp call")]
        for n, msg in recorded:
            self.stop(projects[n], message=msg, denied=int("Refused:" in msg))
        for n in names:
            want = [m.splitlines()[0] for pn, m in recorded if pn == n]      # the result is the first line
            self.assertEqual([r["result"] for r in self.lines(projects[n])], want)
        self.assertTrue(self.lines(projects["south"])[1]["refused"])

    def registry(self, installs):
        """A temp CLAUDE_CONFIG_DIR whose registry lists this plugin once per (folder name, has_writer); -> (config dir, install paths)."""
        import updatelib
        name = updatelib._manifest(updatelib._plugin_root())[0]
        cfg = os.path.join(self.tmp, "claude-cfg")
        os.makedirs(os.path.join(cfg, "plugins"), exist_ok=True)
        paths, entries = [], []
        for folder, has_writer in installs:
            root = os.path.join(self.tmp, "cache", folder)
            os.makedirs(os.path.join(root, "scripts"))
            if has_writer:
                with open(os.path.join(root, "scripts", "timelines.py"), "w") as f:
                    f.write("# writer\n")
            paths.append(root)
            entries.append({"scope": "user", "installPath": root, "version": "1.0.0"})
        with open(os.path.join(cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"version": 2, "plugins": {name + "@fake-market": entries}}, f)
        return cfg

    def silent_with(self, cfg):
        quiet = self.make_project("quiet", with_item=True)
        portfolio.save({"version": 1, "projects": [{"path": quiet, "name": None, "pm": None, "channel": "current", "reason": None}]})
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": cfg}):
            return timelines.cli_text("silent")

    def test_silent_says_writer_missing_when_installed_copy_lacks_it(self):
        cfg = self.registry([("0.0.1", False)])
        self.assertFalse(timelines.writer_installed(cfg))
        self.assertEqual(self.silent_with(cfg), "timelines: writer not in the installed plugin")

    def test_silent_unchanged_when_installed_copy_has_writer(self):
        cfg = self.registry([("0.0.1", True)])
        self.assertTrue(timelines.writer_installed(cfg))
        self.assertEqual(self.silent_with(cfg), "timelines: quiet has no timeline line in the last 24 h")

    def test_writer_installed_true_without_registry(self):
        cfg = os.path.join(self.tmp, "empty-cfg")
        os.makedirs(cfg)
        self.assertTrue(timelines.writer_installed(cfg))
        self.assertEqual(self.silent_with(cfg), "timelines: quiet has no timeline line in the last 24 h")

    def test_writer_installed_true_when_any_entry_has_it(self):
        cfg = self.registry([("0.0.1", False), ("0.0.2", True)])
        self.assertTrue(timelines.writer_installed(cfg))
