"""R-901: the Budgit-home guard decides on what a command writes (bin/budgit CLI_WRITES), and a subagent's ask is denied and queued.
Every test name below is cited by the R-901 section of docs/spec.md."""
import datetime
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_guard_trust import GuardCase, CLI, HOOK
import studiolib as sl

PY = os.path.realpath(sys.executable)


def load_cli():
    loader = importlib.machinery.SourceFileLoader("budgit_cli_r901", CLI)
    spec = importlib.util.spec_from_loader("budgit_cli_r901", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def leaves(parser, prefix=()):
    """Every invocable parser path: a parser without sub-commands, and a parent whose sub-command is optional."""
    import argparse
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    out = []
    if not subs:
        return [" ".join(prefix)] if prefix else []
    if prefix and not subs[0].required:
        out.append(" ".join(prefix))
    for name, child in subs[0].choices.items():
        out += leaves(child, prefix + (name,))
    return out


def snapshot(root):
    seen = {}
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            full = os.path.join(base, name)
            seen[os.path.relpath(full, root)] = (os.path.getsize(full) if os.path.isfile(full) else -1,
                                                 os.path.getmtime(full) if os.path.isfile(full) else 0)
    return seen


class TableTest(unittest.TestCase):
    def test_every_parser_subcommand_has_a_cli_writes_entry(self):
        mod = load_cli()
        table = sl._cli_table()
        # R-1106: `legal profile save` has three words; the guard looks up two, so it has no row and the home default applies (tested in test_r1106)
        missing = [k for k in leaves(mod.build_parser()) if k not in table and k != "legal profile save"]
        self.assertEqual(missing, [], "subcommands without a CLI_WRITES entry")
        extra = {"away note", "away status", "rm-guard status", "skills coverage", "capacity set", "rules show"}     # `away` is one parser leaf; the table splits it by what each form writes; `capacity set` is a positional action of the `capacity` leaf (R-1288)
        self.assertEqual([k for k in table if k not in leaves(mod.build_parser()) and k not in extra], [], "entries for no subcommand")

    def test_entries_are_well_formed_and_home_fixed_names_files_relative_to_the_home(self):
        for key, (kind, files, paths) in sl._cli_table().items():
            self.assertIn(kind, ("none", "project", "home-fixed", "suite", "home"), key)    # R-1101: "suite" = the narrow run / wait class
            self.assertTrue(bool(files) == (kind == "home-fixed"), key)
            for f in files:
                self.assertFalse(os.path.isabs(f) or "/" in f or "\\" in f or ".." in f, key)
            self.assertTrue(all(p == "@" or p.startswith("--") for p in paths), key)

    def test_the_table_path_does_not_depend_on_posix_python_paths(self):
        # the lookup is pure on the sub-command words; only the interpreter pin looks at python paths, and it accepts the running interpreter
        self.assertEqual(sl._cli_table()["ledger match"][0], "home-fixed")
        self.assertTrue(all("/" not in k and "\\" not in k for k in sl._cli_table()))
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "Scripts", "python.exe")
            os.makedirs(os.path.dirname(fake))
            open(fake, "w").close()
            with mock.patch.object(sys, "executable", fake):
                self.assertTrue(sl._pinned_python(fake))      # a venv / conda / Windows-style interpreter is the running one
        # real Windows (py -3 ...\budgit, PowerShell, junctions): UNTESTED


class RecordingHomeTest(GuardCase):
    """Runs the read-only subcommands for real with BUDGIT_HOME on a temp folder and diffs the folder."""

    def setUp(self):
        super().setUp()
        self.bhome = os.path.join(self.tmp, "budgit-home")
        os.makedirs(self.bhome)
        self.env = dict(os.environ, BUDGIT_HOME=self.bhome, STUDIO_HOME=self.bhome, HOME=os.path.join(self.tmp, "fake-user-home"))
        self.env.pop("BUDGIT_SESSION", None)
        # R-1000: a stub codex child that reports its usage limit (never the real codex); the lane is switched on in this home
        with open(os.path.join(self.bhome, "config.json"), "w") as fh:
            json.dump({"codex": "on"}, fh)
        day = datetime.date.today() + datetime.timedelta(days=30)
        self.stub = os.path.join(self.tmp, "stub-codex")
        with open(self.stub, "w") as fh:
            fh.write("#!/bin/sh\necho \"You've hit your usage limit. Upgrade to Plus to continue using Codex, or try again at %s %d%s, %d.\" >&2\nexit 1\n"
                     % (day.strftime("%b"), day.day, "th", day.year))
        os.chmod(self.stub, 0o755)
        self.env["STUDIO_CODEX_BIN"] = self.stub
        self.diff = os.path.join(self.tmp, "r1000.diff")
        with open(self.diff, "w") as fh:
            fh.write("diff --git a/x b/x\n+hello\n")
        # R-1014: stub external reviewers on PATH for `ext-review` (never the real gemini or codex)
        self.stubbin = os.path.join(self.tmp, "stubbin")
        os.makedirs(self.stubbin)
        with open(os.path.join(self.stubbin, "gemini"), "w") as fh:
            fh.write("#!/bin/sh\ncat >/dev/null\necho 'LGTM: no findings'\nexit 0\n")
        os.chmod(os.path.join(self.stubbin, "gemini"), 0o755)
        with open(os.path.join(self.stubbin, "codex"), "w") as fh:
            fh.write(open(self.stub).read())
        os.chmod(os.path.join(self.stubbin, "codex"), 0o755)
        self.env["PATH"] = self.stubbin + os.pathsep + self.env.get("PATH", "")
        self.varies_file = os.path.join(self.tmp, "r1014-varies.txt")
        with open(self.varies_file, "w") as fh:
            fh.write("nothing special here\n")

    def run_cli(self, *args):
        r = subprocess.run([PY, CLI, "--project", self.root] + list(args), env=self.env, capture_output=True, text=True,
                           cwd=self.root, timeout=120)
        return r

    def runs(self):
        """(table key, argv): one recorded run for every non-home entry (M2). Some end in an error exit (no hub, no seal, no
        inbox item): they still prove that the code path they reach leaves the home alone."""
        empty = os.path.join(self.tmp, "empty-scan")
        os.makedirs(empty, exist_ok=True)
        return [("ledger add", ["ledger", "add", "--appetite", "small", "--area", "main", "--phase", "mvp", "--title", "Alpha", "--text", "parked widget exporter"]),
                ("ledger add", ["ledger", "add", "--appetite", "small", "--area", "main", "--phase", "mvp", "--title", "Beta", "--text", "second thing to do"]),
                ("ledger area", ["ledger", "area", "R-1", "checkout"]),      # R-1157
                ("busy", ["busy", "--window", "1"]), ("asks", ["asks"]), ("denies", ["denies"]),
                ("denies", ["denies", "--fp", "deadbeef", "--note", "x"]),
                ("secrets", ["secrets", empty]), ("export", ["export", "--check", empty]),
                ("ledger list", ["ledger", "list"]), ("ledger show", ["ledger", "show", "R-1"]), ("ledger top", ["ledger", "top"]),
                ("ledger plan", ["ledger", "plan", "--no-step0"]),      # R-1266 (580d46b3): read-only; asks admit, writes nothing
                ("ledger timeline", ["ledger", "timeline", "R-1"]),
                ("ledger match", ["ledger", "match", "nothing like it xyz"]),
                ("ledger link", ["ledger", "link", "R-1", "--src", "I-1"]), ("ledger set", ["ledger", "set", "R-1", "open"]),
                ("ledger phase", ["ledger", "phase", "mvp", "R-1"]), ("ledger ack", ["ledger", "ack", "I-1", "--note", "x"]),
                ("ledger appetite", ["ledger", "appetite", "R-1", "small"]), ("ledger flag", ["ledger", "flag", "R-1", "bug"]),
                ("ledger rank", ["ledger", "rank", "R-1", "1", "--reason", "r"]),
                ("ledger fold", ["ledger", "fold", "R-2", "--into", "R-1", "--reason", "same"]),
                ("ledger outcome", ["ledger", "outcome", "R-1", "The user sees it work"]),
                ("ledger due", ["ledger", "due", "R-1", "2026-12-24"]),
                ("ledger evidence", ["ledger", "evidence", "R-1", "--sha", "deadbeef"]),
                ("ledger finding", ["ledger", "finding", "add", "R-1", "--sev", "LOW", "--class", "docs", "--text", "x"]),
                ("ledger findings", ["ledger", "findings", "R-1"]),
                ("progress", ["progress", "add", "--text", "x"]), ("friction", ["friction", "add", "--text", "x"]),      # R-1359
                ("ledger unfold", ["ledger", "unfold", "R-2"]),("ledger done", ["ledger", "done", "R-2", "--evidence", "x"]),
                ("ledger verify", ["ledger", "verify", "R-2"]), ("ledger drop", ["ledger", "drop", "R-2", "--by-user", "t"]),
                ("ledger accept-edit", ["ledger", "accept-edit"]), ("ledger repair-done-at", ["ledger", "repair-done-at"]),
                ("codex review", ["codex", "review", "--diff", self.diff]),
                ("budget", ["budget"]), ("comply", ["comply", "--days", "1", "--root", empty]),
                ("rm-guard status", ["rm-guard"]), ("prepare", ["prepare"]),("varies", ["varies", self.varies_file]),
                ("ext-review", ["ext-review", "--tool", "gemini", "--diff", self.diff, "--prompt", "p", "--out", os.path.join(self.tmp, "r1014-out", "v.txt")]),
                ("status", ["status"]), ("away note", ["away", "note", "a question"]), ("away status", ["away", "status"]),
                ("reports sweep", ["reports", "sweep", "--dry-run"]), ("reports sweep", ["reports", "sweep", "--days", "0"]),
                ("githooks install", ["githooks", "install", "--dry-run"]), ("githooks install", ["githooks", "install"]),   # R-1042: self.root is a temp git repo; the hooks go to its .git/hooks
                ("githooks status", ["githooks", "status"]),
                ("msgshape replay", ["msgshape", "replay", "--last", "1"]),   # R-1112: reads transcripts, prints numbers
                ("lint", ["lint", "--tracked"]), ("lint", ["lint", "--preview", self.varies_file]),    # R-1058: reads; --preview writes nothing
                ("humanize-lint", ["humanize-lint", self.varies_file]),
                ("repo-read", ["repo-read", "status"]),     # the sandboxed read; an unregistered repo is refused, nothing is written
                ("brain lint", ["brain", "lint"]), ("brain index", ["brain", "index"]), ("brain current", ["brain", "current", "x"]),
                ("brain impact", ["brain", "impact", "x"]), ("brain backlinks", ["brain", "backlinks", "x"]),
                ("audit-mcp", ["audit-mcp", empty]),     # R-1068: reads config files, writes nothing
                ("rules show", ["rules", "show", "--grep", "zzz"]),     # R-1360: prints the matching rule lines; writes nothing
                ("skills coverage", ["skills", "coverage", empty]),     # R-1211: lists folders under the given dir, prints; writes nothing
                ("names-check", ["names-check"]),   # R-1366: reads the tree, prints aliases only; writes nothing
                ("ci snapshot", ["ci", "snapshot"]),   # R-1366: commit-tree and update-ref in the project repo only; never the home
                ("secrets", ["secrets", self.varies_file])]    # R-1068: a plain scan of a file writes nothing

    def test_every_non_home_entry_has_a_recorded_run_that_leaves_the_home_untouched(self):
        table = sl._cli_table()
        runs = self.runs()
        wanted = {k for k, v in table.items() if v[0] not in ("home", "suite")}    # R-1101: the suite class runs the project's own suite; test_r1101_suite_wait covers its guard
        self.assertEqual(wanted - {k for k, _ in runs}, set(), "an entry that is not home has no recorded run")
        for key, args in runs:
            before = snapshot(self.bhome)
            r = self.run_cli(*args)
            self.assertNotIn("Traceback", r.stderr, (key, args, r.stderr))
            after = snapshot(self.bhome)
            new = set(after) - set(before)
            changed = {p for p in before if p in after and before[p] != after[p]}
            allowed = set(table[key][1]) if table[key][0] == "home-fixed" else set()
            self.assertEqual((new | changed) - allowed, set(), (key, args, r.stderr))

    def test_a_parked_hit_of_ledger_match_writes_only_the_files_the_table_names(self):
        self.assertEqual(self.run_cli("ledger", "add", "--appetite", "small", "--area", "main", "--phase", "mvp", "--title", "Alpha", "--text", "parked widget exporter").returncode, 0)
        r = self.run_cli("ledger", "phase", "later", "R-1", "--reason", "parked on purpose")
        self.assertEqual(r.returncode, 0, r.stderr)
        before = set(snapshot(self.bhome))
        r = self.run_cli("ledger", "match", "parked widget exporter")
        self.assertEqual(r.returncode, 0, r.stderr)
        new = set(snapshot(self.bhome)) - before
        self.assertTrue(new, "the parked hit should be noted in the home (else this test proves nothing)")
        declared = set(sl._cli_table()["ledger match"][1])
        self.assertTrue({os.path.basename(p) for p in new} <= declared, (new, declared))
        self.assertTrue(all(os.path.dirname(p) == "" for p in new), new)       # directly in the home, nowhere deeper


    def test_r1000_a_codex_review_that_hits_its_usage_limit_writes_only_codex_limit_json_in_the_home(self):
        before = set(snapshot(self.bhome))
        r = self.run_cli("codex", "review", "--diff", self.diff)
        self.assertNotEqual(r.returncode, 0)                  # a limit hit is an error exit, not a report
        self.assertNotIn("Traceback", r.stderr)
        new = set(snapshot(self.bhome)) - before
        self.assertEqual(new, {"codex-limit.json"}, (new, r.stdout, r.stderr))

    def test_r1014_ext_review_that_hits_a_codex_usage_limit_writes_only_codex_limit_json_in_the_home(self):
        before = set(snapshot(self.bhome))
        r = self.run_cli("ext-review", "--tool", "codex", "--diff", self.diff, "--prompt", "p", "--out", os.path.join(self.tmp, "r1014-out2", "v.txt"))
        self.assertNotIn("Traceback", r.stderr)
        new = set(snapshot(self.bhome)) - before
        self.assertEqual(new, {"codex-limit.json"}, (new, r.stdout, r.stderr))

    def test_r1014_a_bare_budget_and_comply_write_only_budget_json_and_budget_lock_in_the_home(self):
        for args in (["budget"], ["comply", "--days", "1", "--root", self.tmp]):
            before = set(snapshot(self.bhome))
            r = self.run_cli(*args)
            self.assertNotIn("Traceback", r.stderr, (args, r.stderr))
            new = set(snapshot(self.bhome)) - before
            self.assertTrue(new <= {"budget.json", "budget.lock"}, (args, new))


class RoleFormTest(GuardCase):
    """role (PM, subagent, verifier) x form (bare, absolute, pinned python3, python3 from PATH)."""

    TEXT = "'match this: ~/.studio/x and the home .budgit note'"

    def verdict(self, cmd, role):
        agent = sl.VERIFIER if role == "verifier" else None
        got = sl.home_write_check("Bash", {"command": cmd}, self.root, agent, subagent=role != "pm")[0]
        return got[0] if got else None

    def forms(self, sub):
        bindir = os.path.dirname(CLI)
        return {"bare": ("budgit " + sub, {"PATH": bindir + os.pathsep + os.environ.get("PATH", "")}),
                "absolute": (CLI + " " + sub, {}),
                "pinned python3": (PY + " " + CLI + " " + sub, {}),
                "python3 from PATH": ("python3 " + CLI + " " + sub, {})}

    def test_a_read_only_subcommand_passes_in_every_role_and_form_but_the_unpinned_python(self):
        for role in ("pm", "subagent", "verifier"):
            for form, (cmd, env) in self.forms("ledger match " + self.TEXT).items():
                with mock.patch.dict(os.environ, env):
                    got = self.verdict(cmd, role)
                if form == "python3 from PATH":
                    self.assertIsNotNone(got, (role, form))          # a bare interpreter name is never trusted (R-856)
                else:
                    self.assertIsNone(got, (role, form))

    def test_a_home_subcommand_asks_the_coordinator_in_the_python_form_and_denies_a_subagent_in_every_form(self):
        for form, (cmd, env) in self.forms("trust work").items():
            with mock.patch.dict(os.environ, env):
                if form == "pinned python3":
                    self.assertEqual(self.verdict(cmd, "pm"), "ask", form)
                if form in ("bare", "absolute", "pinned python3"):
                    self.assertEqual(self.verdict(cmd, "subagent"), "deny", form)
                    self.assertEqual(self.verdict(cmd, "verifier"), "deny", form)
                if form in ("bare", "absolute"):          # the coordinator's plain call is judged as before R-901 (no new ask)
                    self.assertIsNone(self.verdict(cmd, "pm"), form)

    def test_r1068_secrets_and_audit_mcp_are_not_home_writes_for_a_subagent_but_trust_and_unknown_still_are(self):
        f = os.path.join(self.root, "scan-me.txt")          # inside the project: a path in the folder that holds the Budgit home asks
        for sub in ("secrets " + f, "audit-mcp " + self.root):
            self.assertIsNone(self.verdict(CLI + " " + sub, "subagent"), sub)
        self.assertEqual(self.verdict(CLI + " trust work", "subagent"), "deny")
        self.assertEqual(self.verdict(CLI + " nosuchcommand", "subagent"), "deny")

    def test_r1068_audit_mcp_aimed_at_the_budgit_home_still_asks_or_denies_for_a_subagent(self):
        h = sl.home_state()
        for sub in (".studio", ".budgit"):
            got = self.verdict("%s audit-mcp %s" % (CLI, os.path.join(h, sub)), "subagent")
            self.assertIn(got, ("ask", "deny"), sub)
        self.assertIsNone(self.verdict("%s audit-mcp %s" % (CLI, self.root), "subagent"))

    def test_r1068_another_checkouts_cli_is_still_refused_for_a_subagent_even_for_secrets(self):
        other = os.path.join(self.root, "other-checkout", "plugin", "bin")
        os.makedirs(other)
        stub = os.path.join(other, "budgit")
        with open(stub, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(stub, 0o755)
        self.assertEqual(self.verdict(stub + " secrets " + self.root, "subagent"), "deny")

    def test_an_unknown_subcommand_and_a_missing_subcommand_read_as_home(self):
        self.assertEqual(self.verdict(CLI + " nosuchcommand", "subagent"), "deny")
        self.assertEqual(self.verdict(CLI + " ledger", "subagent"), "deny")

    def test_a_path_argument_in_the_home_is_still_refused(self):
        h = sl.home_state()
        for cmd in ("%s --project %s ledger match x" % (CLI, h), "%s --project=%s/p ledger match x" % (CLI, h)):
            self.assertIn(self.verdict(cmd, "pm"), ("deny", "ask"), cmd)
        for cmd in ("%s ledger match x > %s/log" % (CLI, h),):
            self.assertIsNotNone(self.verdict(cmd, "pm"), cmd)

    def test_replay_deny_52e0f3bc_python3_cli_ledger_match_from_the_pm_passes(self):
        cmd = "%s %s ledger match 'R-894 change 4: budgit-home guard, ~/.studio, BUDGIT_HOME, trust.json'" % (PY, CLI)
        self.assertIsNone(self.verdict(cmd, "pm"))
        self.assertIsNone(self.verdict("%s %s busy" % (PY, CLI), "subagent"))


class VerifyForgeryReplayTest(GuardCase):
    def decide(self, cmd, who="general-purpose"):
        return sl.verify_forgery_decision("Bash", {"command": cmd}, who, self.project)

    def test_replay_eb0ef717_evidence_naming_the_verify_path_passes_when_the_hook_cannot_find_budgit(self):
        cmd = 'budgit ledger done R-746 --evidence "verify .budgit/verify/R-746.md PASS; security PASS round 3; full suite green"'
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"PATH": empty}):
            self.assertIsNone(self.decide(cmd))
            self.assertIsNone(self.decide(cmd, None))
        own = os.path.dirname(CLI)
        with mock.patch.dict(os.environ, {"PATH": own + os.pathsep + os.environ.get("PATH", "")}):
            self.assertIsNone(self.decide(cmd))

    def test_free_text_flags_are_not_write_targets_but_real_forgeries_still_deny(self):
        for who in ("general-purpose", None):
            for cmd in ('%s ledger done R-1 --text "see .budgit/verify/R-1.md"' % CLI,
                        '%s ledger done R-1 --note "see .budgit/verify/R-1.md"' % CLI):
                self.assertIsNone(self.decide(cmd, who), cmd)
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"PATH": empty}):
            for cmd in ('budgit ledger done R-1 --evidence ok --out .budgit/verify/R-1.md',
                        'budgit ledger done R-1 --evidence "ok" > .budgit/verify/R-1.md',
                        'budgit ledger done R-1 --evidence "`echo PASS > .budgit/verify/R-1.md`"',
                        "echo PASS > .budgit/verify/R-1.md", "fakebudgit ledger done R-1 --evidence 'x .budgit/verify/R-1.md'"):
                got = self.decide(cmd)
                self.assertTrue(got and got[0] == "deny", cmd)

    def test_a_budgit_found_on_the_hook_path_must_be_the_own_file(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "budgit")
            open(fake, "w").write("#!/bin/sh\n")
            os.chmod(fake, 0o755)
            with mock.patch.dict(os.environ, {"PATH": d}):
                self.assertFalse(sl._is_own_budgit_name("budgit"))


class DenyAndQueueTest(GuardCase):
    def run_hook(self, cmd, agent_id):
        bhome = os.path.join(self.tmp, "hook-home")
        os.makedirs(bhome, exist_ok=True)
        env = dict(os.environ, BUDGIT_HOME=bhome, STUDIO_HOME=bhome, HOME=os.path.join(self.tmp, "fake-user-home"))
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "s-r901", "cwd": self.root}
        if agent_id:
            payload["agent_id"] = agent_id
            payload["agent_type"] = "general-purpose"
        r = subprocess.run([PY, HOOK, "pre-tool-use"], input=json.dumps(payload), env=env, capture_output=True, text=True, cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
        queued = ""
        for base, _d, files in os.walk(bhome):
            for name in files:
                with open(os.path.join(base, name), errors="replace") as fh:
                    queued += fh.read()
        return out, queued

    def test_a_subagent_ask_is_denied_with_a_queued_message_and_an_entry_in_the_away_queue(self):
        out, queued = self.run_hook("echo 'unclosed ~/x", "agent-1")      # a line the guard cannot read: an ask
        self.assertEqual(out.get("permissionDecision"), "deny")
        self.assertIn("queued for your coordinator", out.get("permissionDecisionReason", ""))
        self.assertIn("do not reword", out.get("permissionDecisionReason", ""))
        self.assertIn('"kind": "subagent-ask"', queued)

    def test_l4_through_the_hook_one_repeated_subagent_ask_is_queued_once(self):
        self.run_hook("echo 'unclosed ~/y", "agent-1")
        out, queued = self.run_hook("echo 'unclosed ~/y", "agent-1")
        self.assertEqual(out.get("permissionDecision"), "deny")
        self.assertEqual(queued.count('"kind": "subagent-ask"'), 1)

    def test_the_coordinator_still_gets_the_ask_and_nothing_is_queued(self):
        out, queued = self.run_hook("echo 'unclosed ~/x", None)
        self.assertEqual(out.get("permissionDecision"), "ask")
        self.assertNotIn("subagent-ask", queued)


class ReviewRound1Test(GuardCase):
    """The security review's findings M1, L1, L2, L3, L4 (R-901 round 4)."""

    def verdict(self, cmd, role, cwd=None):
        got = sl.home_write_check("Bash", {"command": cmd}, cwd or self.root, None, subagent=role == "subagent")[0]
        return got[0] if got else None

    def test_l3_a_typo_kind_or_a_malformed_entry_reads_as_home(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "budgit")
            with open(fake, "w") as fh:
                fh.write('CLI_WRITES = {"ledger match": "nnone", "busy": ("home-fixed", (), ()), "ledger list": ("project", (), ()),'
                         ' "ledger top": ("none", ("x",), ()), "denies": 7}\n')
            with mock.patch.object(sl, "_own_budgit", lambda: fake):
                t = sl._cli_table()
            self.assertEqual([t[k][0] for k in ("ledger match", "busy", "ledger top", "denies")], ["home"] * 4)
            self.assertEqual(t["ledger list"][0], "project")

    def test_m1_a_subagent_runs_no_home_subcommand_in_any_wrapped_or_book_form(self):
        cases = ["command %s trust work", "env %s trust work", "nice %s sweep", "nice -n 10 %s sweep", "exec %s trust work",
                 "time %s trust work", "xargs %s trust work", "%s trust work $(true)", "%s ledger start R-1", "FOO=1 %s trust work",
                 "%s trust work", PY + " %s trust work", "%s ledger handover --from a --to b"]
        for form in cases:
            cmd = form % CLI
            self.assertEqual(self.verdict(cmd, "subagent"), "deny", cmd)
            self.assertNotEqual(self.verdict(cmd, "pm"), "deny", cmd)          # the coordinator is judged as before (or asked)
        for ok in ("%s ledger match x", "%s busy", "%s ledger list", PY + " %s ledger show R-1"):
            self.assertIsNone(self.verdict(ok % CLI, "subagent"), ok)
        self.assertIsNone(self.verdict("grep budgit file", "subagent"))           # a word read by a text reader, not a command
        self.assertIsNone(self.verdict("git log --grep budgit", "subagent"))

    def test_m1_allow_list_a_subagent_is_denied_every_wrapper_around_a_budgit_word(self):
        forms = ["timeout 5 budgit trust work", "lockf -k /tmp/l budgit trust work", "caffeinate budgit trust work",
                 "script -q /dev/null budgit away on", "stdbuf -oL budgit trust work", "arch -arm64 budgit trust work",
                 "find . -exec budgit trust work \\;", "xargs budgit trust work"]
        for cmd in forms:
            self.assertEqual(self.verdict(cmd, "subagent"), "deny", cmd)
            wrapped = cmd.replace("budgit", CLI, 1)
            self.assertEqual(self.verdict(wrapped, "subagent"), "deny", wrapped)
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):          # no `budgit` on the hook's PATH: the bare name is the own CLI
            for ok in ("budgit busy", "budgit ledger match x", "grep budgit file", "cat budgit.txt"):
                self.assertIsNone(self.verdict(ok, "subagent"), ok)

    def test_away_note_and_status_are_home_fixed_only_in_their_exact_shapes(self):
        shapes = ["%s away note --clear", "%s away status --clear", "%s away note x --cl", "%s away note -- --clear",
                  "%s --project /x away note --clear", "%s away status x",
                  "%s away note {-,-}-clear", "%s away note {--clear,x}",          # brace
                  "%s away note *clear", "%s away note ?-clear", "%s away note [-]-clear",      # glob kinds
                  "%s away note ~", "%s away note ~/x", "%s away note $'--clear'",
                  '%s away note "{a,b}"', '%s away note "a$b"', '%s away note "a`b`"', '%s away note "a\nb"',
                  "%s away note x1(P:--clear:)", "%s away note x1(:s/x1/--clear/)", "%s away note x1(e:'touch PWNED':)",   # zsh qualifiers
                  '%s away note "queued text"', '%s away note "¿Apruebas X?"', "%s away note a question"]       # round 9: coordinator-only
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            for form in shapes:
                for cli in ("budgit", CLI, PY + " " + CLI):
                    cmd = form % cli
                    self.assertEqual(self.verdict(cmd, "subagent"), "deny", cmd)
            for ok in ("budgit away", "budgit away status", CLI + " away", CLI + " away status"):
                self.assertIsNone(self.verdict(ok, "subagent"), ok)

    def test_a_denied_away_note_tells_the_subagent_to_hand_the_question_back(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            got = sl.home_write_check("Bash", {"command": 'budgit away note "¿Apruebas X?"'}, self.root, None, subagent=True)[0]
        self.assertEqual(got[0], "deny")
        self.assertIn("`away note` is for the coordinator; put the question in your hand-back", got[1])

    def test_finding_a_another_checkouts_cli_is_refused_for_a_subagent_with_a_plain_text(self):
        other = "/tmp/some-worktree/plugin/bin/budgit"
        got = sl.home_write_check("Bash", {"command": other + " export /x --out /y"}, self.root, None, subagent=True)[0]
        self.assertEqual(got[0], "deny")
        self.assertIn("another checkout's budgit CLI is refused for a subagent", got[1])
        self.assertNotIn("use ", got[1].lower().replace("used", ""))              # no rewording hint

    def test_away_off_on_clear_are_home_for_a_subagent_only_note_and_bare_away_are_home_fixed(self):
        for cmd in ("%s away off", "%s away on", "%s away --clear", "%s --project /x away --clear"):
            self.assertEqual(self.verdict(cmd % CLI, "subagent"), "deny", cmd)
        self.assertEqual(self.verdict(("%s away note a question") % CLI, "subagent"), "deny")      # round 9
        self.assertIsNone(self.verdict("%s away" % CLI, "subagent"))
        t = sl._cli_table()
        self.assertEqual([t[k][0] for k in ("away", "away note", "away status")], ["home", "home-fixed", "home-fixed"])

    def test_l4_a_session_cannot_queue_more_than_the_note_cap(self):
        import away
        with tempfile.TemporaryDirectory() as home:
            for i in range(away.NOTE_CAP_PER_SESSION):
                self.assertTrue(away.note("question %d" % i, "note", "s1", home=home))
            self.assertFalse(away.note("one too many", "note", "s1", home=home))
            self.assertTrue(away.note("another session", "note", "s2", home=home))
            self.assertEqual(away.NOTE_CAP_PER_SESSION, 20)

    def test_l2_a_variable_or_a_home_folder_is_not_trusted(self):
        h = sl.home_state()
        os.makedirs(h, exist_ok=True)
        cmd = "X='--project /Users/x'; %s ledger add --text hi $X" % CLI
        self.assertEqual(self.verdict(cmd, "subagent"), "deny")
        words = (CLI + " ledger add --text x").split()
        self.assertIsNone(sl._cli_call(words, list(words), "$X", self.root))
        self.assertIsNone(sl._cli_call(words, list(words), "", h))                # the command's own folder is the home
        self.assertIsNotNone(sl._cli_call(words, list(words), "", self.root))
        self.assertEqual(self.verdict("cd %s && %s ledger add --text x" % (h, CLI), "subagent"), "deny")

    def test_l1_a_symlinked_folder_in_the_cli_path_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            link = os.path.join(d, "bin")
            os.symlink(os.path.dirname(CLI), link)
            words = [os.path.join(link, "budgit"), "ledger", "match", "x"]
            self.assertIsNone(sl._cli_call(words, list(words), "", self.root))
            words = [CLI, "ledger", "match", "x"]
            self.assertIsNotNone(sl._cli_call(words, list(words), "", self.root))

    def test_l4_a_repeated_subagent_ask_is_queued_once_per_session_and_command(self):
        import away
        with tempfile.TemporaryDirectory() as home:
            put = lambda s, fp: away.note("subagent-ask for Bash: x", "subagent-ask", s, home=home, fp=fp)
            self.assertTrue(put("s1", "fp-a"))
            self.assertFalse(put("s1", "fp-a"))
            self.assertTrue(put("s1", "fp-b"))
            self.assertTrue(put("s2", "fp-a"))
            self.assertTrue(away.note("user note", "note", "s1", home=home))
            self.assertEqual(len(away._queue_lines(home)), 4)


if __name__ == "__main__":
    unittest.main()
