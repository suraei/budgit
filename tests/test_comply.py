import json, os, subprocess, sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def tool_use(mid, name, inp, tid):
    return {"type": "assistant", "timestamp": "2026-10-19T10:00:00Z",
            "message": {"id": mid, "model": "claude-opus-5-5",
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def compact(ts):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": ts,
            "compactMetadata": {"trigger": "auto", "preTokens": 290000}}


class ComplyTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "projects")
        self.dir = os.path.join(self.root, "-Users-x-proj")
        os.makedirs(self.dir)

    def session(self, sid, recs, subagents=()):
        path = os.path.join(self.dir, sid + ".jsonl")
        with open(path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        sub = os.path.join(self.dir, sid, "subagents")
        os.makedirs(sub, exist_ok=True)
        for aid, kind, tid, n_tools in subagents:
            with open(os.path.join(sub, "agent-%s.jsonl" % aid), "w") as f:
                for i in range(n_tools):
                    f.write(json.dumps(tool_use("m%s%d" % (aid, i), "Read", {"file_path": "x"}, "t%d" % i)) + "\n")
            with open(os.path.join(sub, "agent-%s.meta.json" % aid), "w") as f:
                json.dump({"agentType": kind, "toolUseId": tid}, f)
        return path

    def test_compliant_session_has_no_violations(self):
        import comply
        path = self.session("s1", [
            tool_use("m1", "Agent", {"prompt": "p", "subagent_type": "studio:researcher", "model": "sonnet"}, "ta"),
            tool_use("m2", "Write", {"file_path": "/p/.studio/progress.md", "content": "x"}, "tw"),
            compact("2026-10-19T11:00:00Z"),
        ], subagents=[("aaaa1111", "studio:researcher", "ta", 10)])
        r = comply.audit(path, level="normal")
        self.assertEqual(r["agents"], 1)
        self.assertEqual(comply.violations(r), 0, r)

    def test_noncompliant_session_counts_each_rule(self):
        import comply
        path = self.session("s2", [
            tool_use("m1", "Agent", {"prompt": "p", "subagent_type": "general-purpose"}, "ta"),
            # four agents launched in one message (same message id) at normal (max 3)
            tool_use("m2", "Agent", {"prompt": "p", "model": "sonnet"}, "tb"),
            tool_use("m2", "Agent", {"prompt": "p", "model": "sonnet"}, "tc"),
            tool_use("m2", "Agent", {"prompt": "p", "model": "sonnet"}, "td"),
            tool_use("m2", "Agent", {"prompt": "p", "model": "sonnet"}, "te"),
            compact("2026-10-19T11:00:00Z"),   # no progress.md write before it
        ], subagents=[("bbbb2222", "general-purpose", "ta", 75)])
        r = comply.audit(path, level="normal")
        self.assertEqual(r["agents_without_model"], 1)
        self.assertEqual(r["bursts_over_limit"], 1)
        self.assertEqual(r["agents_over_call_budget"], 1)
        self.assertEqual(r["compactions_without_checkpoint"], 1)
        self.assertEqual(comply.violations(r), 4)

    def test_hook_filled_models_are_not_violations_but_are_shown(self):
        import comply, budget
        path = self.session("s8", [tool_use("m1", "Agent", {"prompt": "p"}, "ta"),
                                   tool_use("m2", "Agent", {"prompt": "p"}, "tb")])
        budget.record_fill("s8", "ta", "sonnet")
        r = comply.audit(path, level="normal")
        self.assertEqual((r["agents_without_model"], r["models_filled"]), (1, 1))
        self.assertIn("filled by hook", comply.table([dict(r, dir="d")]))

    def test_checkpoint_counts_only_since_previous_compaction(self):
        import comply
        path = self.session("s3", [
            tool_use("m1", "Edit", {"file_path": "/p/.studio/progress.md", "old_string": "a", "new_string": "b"}, "t1"),
            compact("2026-10-19T11:00:00Z"),
            tool_use("m2", "Read", {"file_path": "x"}, "t2"),
            compact("2026-10-19T12:00:00Z"),
        ])
        self.assertEqual(comply.audit(path)["compactions_without_checkpoint"], 1)

    def test_bound_project_checks_ledger_and_saved_research(self):
        import comply
        self.init_project()
        sl.activate(self.project, "s4")
        sl.append_inbox(self.project, "haz login", "s4", "prompt")
        path = self.session("s4", [
            tool_use("m1", "Agent", {"prompt": "p", "subagent_type": "studio:researcher", "model": "sonnet"}, "ta"),
        ], subagents=[("cccc3333", "studio:researcher", "ta", 5)])
        r = comply.audit(path)
        self.assertEqual(r["untracked_inputs"], 1)
        self.assertEqual(r["research_unsaved"], 1)
        sl.save_report(self.project, "studio:researcher", "cccc3333", "finding")
        self.assertEqual(comply.audit(path)["research_unsaved"], 0)

    def test_researcher_that_wrote_a_research_file_counts_as_saved(self):
        import comply
        self.init_project()
        sl.activate(self.project, "s6")
        path = self.session("s6", [
            tool_use("m1", "Agent", {"prompt": "p", "subagent_type": "studio:researcher", "model": "sonnet"}, "ta"),
        ], subagents=[("dddd4444", "studio:researcher", "ta", 3)])
        sub = os.path.join(self.dir, "s6", "subagents", "agent-dddd4444.jsonl")
        with open(sub, "a") as f:
            f.write(json.dumps(tool_use("mw", "Write", {"file_path": "/p/research/2026-x.md", "content": "c"}, "tw")) + "\n")
        self.assertEqual(comply.audit(path)["research_unsaved"], 0)

    def test_table_hides_quiet_sessions(self):
        import comply
        quiet = dict.fromkeys(comply.COUNTERS, 0)
        quiet.update(session="q1", dir="d", agents=0)
        loud = dict(quiet, session="l1", agents=2, agents_without_model=1)
        out = comply.table([quiet, loud])
        self.assertIn("l1", out)
        self.assertNotIn("q1", out)
        self.assertIn("1 quiet session", out)

    def set_exclude_dirs(self, *words):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"exclude_dirs": list(words)}, f)

    def test_no_directory_is_excluded_unless_the_user_lists_it(self):
        # R-212 #8: the exclusion is the user's own config.json `exclude_dirs`, empty by default
        import comply
        d = os.path.join(self.root, "-Users-x-Documents-CTFs")
        os.makedirs(d)
        with open(os.path.join(d, "z.jsonl"), "w") as f:
            f.write(json.dumps(tool_use("m1", "Agent", {"prompt": "p"}, "ta")) + "\n")
        self.assertEqual({r["dir"] for r in comply.scan(self.root, days=7)}, {"-Users-x-Documents-CTFs"})

    def test_a_single_word_given_as_text_still_excludes(self):
        import comply
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"exclude_dirs": "ctf"}, f)
        self.assertTrue(comply.excluded_dirs().search("-Users-x-ctf-box"))

    def test_directories_the_user_lists_in_exclude_dirs_are_never_listed(self):
        import comply
        self.set_exclude_dirs("ctf", "PenTest")
        for name in ("-Users-x-Documents-CTFs", "-Users-x-pentest-lab"):
            d = os.path.join(self.root, name)
            os.makedirs(d)
            with open(os.path.join(d, "z.jsonl"), "w") as f:
                f.write(json.dumps(tool_use("m1", "Agent", {"prompt": "p"}, "ta")) + "\n")
        self.session("s7", [tool_use("m1", "Agent", {"prompt": "p"}, "ta")])
        dirs = {r["dir"] for r in comply.scan(self.root, days=7)}
        self.assertEqual(dirs, {"-Users-x-proj"})

    def test_project_filter_keeps_only_that_projects_sessions(self):
        # a project's retro must see its own sessions: its Claude Code dir, or sessions bound to it elsewhere
        import comply
        self.init_project()
        own = os.path.join(self.root, comply.claude_dir_name(self.project))
        os.makedirs(own)
        with open(os.path.join(own, "o1.jsonl"), "w") as f:
            f.write(json.dumps(tool_use("m1", "Agent", {"prompt": "p"}, "ta")) + "\n")
        sl.activate(self.project, "b1")
        self.session("b1", [tool_use("m1", "Agent", {"prompt": "p"}, "ta")])
        self.session("x1", [tool_use("m1", "Agent", {"prompt": "p"}, "ta")])
        sids = {r["session"] for r in comply.scan(self.root, days=7, project=self.project)}
        self.assertEqual(sids, {"o1", "b1"})
        self.assertEqual(len(comply.scan(self.root, days=7)), 3)
        r = subprocess.run([sys.executable, CLI, "comply", "--root", self.root, "--project", self.project],
                           capture_output=True, text=True, env=dict(os.environ, STUDIO_HOME=self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("o1", r.stdout)
        self.assertNotRegex(r.stdout, r"(?m)^x1\s")                 # the session column: a temp folder name may hold "x1" by chance

    def test_cli_table(self):
        self.session("s5", [tool_use("m1", "Agent", {"prompt": "p"}, "ta")])
        r = subprocess.run([sys.executable, CLI, "comply", "--root", self.root, "--days", "7"],
                           capture_output=True, text=True, env=dict(os.environ, STUDIO_HOME=self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("agents without model=", r.stdout)
        self.assertIn("s5"[:2], r.stdout)
