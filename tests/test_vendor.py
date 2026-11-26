"""`budgit vendor`: the runtime, agents, skill and hooks copied into a project so Claude Code cloud sessions
(claude.ai/code), which load no plugins, still get the guards. Nothing here runs in a real cloud VM: the cloud is
simulated with CLAUDE_CODE_REMOTE=true and a hook command run through `sh -c` exactly like Claude Code runs it."""
import json, os, re, subprocess, sys
from unittest import mock

from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
PLUGIN_HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
VERSION = json.load(open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json")))["version"]
# every hook in plugin/hooks/hooks.json except the local-only Brote / floating-app ones, which a cloud copy does not carry
HOOK_COUNT = sum(1 for groups in json.load(open(os.path.join(ROOT, "plugin", "hooks", "hooks.json")))["hooks"].values()
                 for g in groups for h in g["hooks"]
                 if "hooks/brote-event.sh" not in h.get("command", "") and "scripts/floatctl.py" not in h.get("command", ""))
AGENTS = ("builder", "persona-sim", "plan-critic", "researcher", "scout-reader", "security-reviewer", "verifier")


class VendorCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", self.project], check=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        self.env["STUDIO_HOME"] = self.home

    def vendor(self, *extra, project=None):
        return subprocess.run([sys.executable, CLI, "vendor", project or self.project] + list(extra),
                              env=self.env, capture_output=True, text=True, cwd=self.tmp)

    def path(self, *rel):
        return os.path.join(self.project, *rel)

    def settings(self):
        with open(self.path(".claude", "settings.json"), encoding="utf-8") as f:
            return json.load(f)

    def ours(self, settings=None):
        out = []
        for event, groups in (settings or self.settings())["hooks"].items():
            for g in groups:
                for h in g["hooks"]:
                    if ".claude/budgit/scripts/hook.py" in h["command"]:
                        out.append((event, g, h))
        return out

    def run_hook_command(self, command, payload, remote):
        env = dict(self.env, CLAUDE_PROJECT_DIR=self.project)
        if remote:
            env["CLAUDE_CODE_REMOTE"] = "true"
        return subprocess.run(["sh", "-c", command], input=json.dumps(payload), env=env, capture_output=True,
                              text=True, cwd=self.project)


class VendorFilesTest(VendorCase):
    def test_copies_runtime_agents_skill_and_version(self):
        r = self.vendor()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        studio = self.path(".claude", "budgit")
        for rel in ("scripts/hook.py", "scripts/studiolib.py", "scripts/rawguard.py", "scripts/writeguard.py", "bin/budgit"):
            self.assertTrue(os.path.isfile(os.path.join(studio, rel)), rel)
        self.assertTrue(os.access(os.path.join(studio, "bin", "budgit"), os.X_OK))
        self.assertEqual(open(os.path.join(studio, "VERSION")).read().strip(), VERSION)
        for name in AGENTS:
            text = open(self.path(".claude", "agents", "budgit-%s.md" % name), encoding="utf-8").read()
            self.assertRegex(text, r"(?m)^name: budgit-%s$" % name)
        self.assertTrue(os.path.isfile(self.path(".claude", "skills", "budgit-kickoff", "SKILL.md")))
        self.assertTrue(os.path.isdir(self.path(".claude", "skills", "budgit-kickoff", "phases")))
        self.assertIn("commit", r.stdout.lower())     # the reminder to commit + push

    def test_nothing_private_is_copied(self):
        self.vendor()
        studio = self.path(".claude", "budgit")
        self.assertEqual(sorted(os.listdir(studio)), [".gitignore", "VERSION", "bin", "scripts"])
        for _, dirs, files in os.walk(self.path(".claude")):
            self.assertNotIn("tests", dirs)
            self.assertNotIn("__pycache__", dirs)
            self.assertNotIn(".studio", dirs)
            self.assertFalse([f for f in files if f.endswith(".pyc")])
        self.assertFalse(os.path.exists(self.path(".studio")))

    def test_agent_and_skill_docs_say_studio_dash_in_cloud(self):
        self.vendor()
        text = open(self.path(".claude", "agents", "budgit-researcher.md"), encoding="utf-8").read()
        self.assertNotRegex(text, r"budgit:(%s)" % "|".join(AGENTS))
        self.assertIn("budgit-scout-reader", text)
        skill = open(self.path(".claude", "skills", "budgit-kickoff", "SKILL.md"), encoding="utf-8").read()
        self.assertNotRegex(skill, r"budgit:(%s)" % "|".join(AGENTS))
        self.assertIn("cloud", skill.lower())
        self.assertIn("budgit-verifier", skill)

    def test_source_skill_notes_the_cloud_names(self):
        src = open(os.path.join(ROOT, "skills", "budgit-kickoff", "SKILL.md"), encoding="utf-8").read()
        self.assertIn("budgit-verifier", src)

    def test_second_run_changes_nothing(self):
        self.vendor()
        snap = self.snapshot()
        r = self.vendor()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), snap)

    def snapshot(self):
        out = {}
        for d, _, files in os.walk(self.path(".claude")):
            for f in files:
                p = os.path.join(d, f)
                out[os.path.relpath(p, self.project)] = open(p, "rb").read()
        return out

    def test_secret_in_the_vendored_files_aborts_and_writes_nothing(self):
        import vendor
        with mock.patch("secretscan.scan", return_value=1):
            with self.assertRaises(sl.StudioError) as ctx:
                vendor.vendor(self.project)
        self.assertIn("secret", str(ctx.exception).lower())
        self.assertFalse(os.path.exists(self.path(".claude")))


class VendorSafetyTest(VendorCase):
    def test_refuses_a_non_git_directory(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        r = self.vendor(project=plain)
        self.assertEqual(r.returncode, 1)
        self.assertIn("git", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(plain, ".claude")))

    def test_refuses_a_subfolder_of_a_repo(self):
        sub = self.path("pkg")
        os.makedirs(sub)
        r = self.vendor(project=sub)
        self.assertEqual(r.returncode, 1)
        self.assertIn("root", r.stderr)

    def test_refuses_the_studio_repo_itself(self):
        r = self.vendor(project=ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Budgit repo", r.stderr)

    def test_refuses_another_checkout_of_the_studio_plugin(self):
        os.makedirs(self.path("plugin", ".claude-plugin"))
        with open(self.path("plugin", ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "budgit", "version": "0.0.1"}, f)
        r = self.vendor()
        self.assertEqual(r.returncode, 1)
        self.assertIn("Budgit repo", r.stderr)


class VendorSettingsTest(VendorCase):
    def test_merge_keeps_existing_keys_and_hooks_and_is_idempotent(self):
        os.makedirs(self.path(".claude"))
        mine = {"permissions": {"allow": ["Bash(ls:*)"]}, "env": {"A": "1"},
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}],
                          "Notification": [{"hooks": [{"type": "command", "command": "echo n"}]}]}}
        with open(self.path(".claude", "settings.json"), "w") as f:
            json.dump(mine, f)
        self.assertEqual(self.vendor().returncode, 0)
        first = open(self.path(".claude", "settings.json"), "rb").read()
        s = json.loads(first)
        self.assertEqual(s["permissions"], mine["permissions"])
        self.assertEqual(s["env"], mine["env"])
        self.assertEqual(s["hooks"]["Notification"], mine["hooks"]["Notification"])
        self.assertIn(mine["hooks"]["PreToolUse"][0], s["hooks"]["PreToolUse"])
        self.assertEqual(len(self.ours(s)), HOOK_COUNT)
        self.assertEqual(self.vendor().returncode, 0)
        self.assertEqual(open(self.path(".claude", "settings.json"), "rb").read(), first)

    def test_rerun_replaces_only_our_hooks(self):
        self.vendor()
        s = self.settings()
        s["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = \
            s["hooks"]["PreToolUse"][0]["hooks"][0]["command"].replace("pre-tool-use", "OLD")
        s["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo keep"}]})
        with open(self.path(".claude", "settings.json"), "w") as f:
            json.dump(s, f)
        self.vendor()
        s = self.settings()
        self.assertFalse([1 for _, _, h in self.ours(s) if "OLD" in h["command"]])
        self.assertEqual(len(self.ours(s)), HOOK_COUNT)
        self.assertIn({"hooks": [{"type": "command", "command": "echo keep"}]}, s["hooks"]["Stop"])

    def test_revendoring_a_pre_0_12_copy_leaves_exactly_one_hook_set_and_no_old_files(self):
        """R-120: a project vendored by 0.11.x (.claude/studio, studio-X agents, studio-kickoff) is replaced, not doubled."""
        self.vendor()
        s = self.settings()
        old = {event: [{"hooks": [dict(h, command=h["command"].replace(".claude/budgit", ".claude/studio"))
                                  for h in g["hooks"]]} for g in groups] for event, groups in s["hooks"].items()}
        for rel, text in ((".claude/studio/VERSION", "0.11.49\n"), (".claude/studio/bin/studio", "#!/usr/bin/env python3\n"),
                          (".claude/agents/studio-verifier.md", "old\n"), (".claude/skills/studio-kickoff/SKILL.md", "old\n")):
            os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
            with open(self.path(rel), "w") as f:
                f.write(text)
        with open(self.path(".claude", "settings.json"), "w") as f:
            json.dump({"hooks": old}, f)
        r = self.vendor()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        s = self.settings()
        self.assertEqual(len(self.ours(s)), HOOK_COUNT)
        self.assertFalse([1 for groups in s["hooks"].values() for g in groups for h in g["hooks"]
                          if ".claude/studio/" in h["command"]])
        for rel in (".claude/studio", ".claude/agents/studio-verifier.md", ".claude/skills/studio-kickoff"):
            self.assertFalse(os.path.exists(self.path(rel)), rel)

    def test_check_reads_the_old_version_file_of_a_pre_0_12_copy(self):
        os.makedirs(self.path(".claude", "studio"))
        with open(self.path(".claude", "studio", "VERSION"), "w") as f:
            f.write("0.0.1\n")
        r = self.vendor("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("0.0.1", r.stdout + r.stderr)

    def test_every_plugin_hook_is_vendored_with_the_same_args(self):
        self.vendor()
        plugin = json.load(open(os.path.join(ROOT, "plugin", "hooks", "hooks.json")))
        want = set()
        for event, groups in plugin["hooks"].items():
            for g in groups:
                for h in g["hooks"]:
                    if "brote-event.sh" in h["command"] or "scripts/floatctl.py" in h["command"]:
                        continue   # R-232/R-310: local floating-app channel and lifecycle, not vendored into cloud projects
                    want.add((event, g.get("matcher"), h["command"].rsplit(" ", 1)[1], h["timeout"]))
        got = set()
        for event, g, h in self.ours():
            got.add((event, g.get("matcher"), h["command"].rsplit(" ", 1)[1], h["timeout"]))
        self.assertEqual(got, want)

    def test_guards_fail_closed_in_the_cloud_too(self):
        """R-53, her choice 2026-10-20: a PreToolUse guard that cannot run blocks the call (onFailure, 2.1.295+)."""
        self.vendor()
        pre = [h for event, g, h in self.ours() if event == "PreToolUse"]
        self.assertTrue(pre)
        self.assertTrue(all(h.get("onFailure") == "block" for h in pre), pre)

    def test_invalid_existing_settings_json_is_left_alone(self):
        os.makedirs(self.path(".claude"))
        with open(self.path(".claude", "settings.json"), "w") as f:
            f.write("{not json")
        r = self.vendor()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(open(self.path(".claude", "settings.json")).read(), "{not json")


class VendoredHookTest(VendorCase):
    PRE_DENY = {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "unbound-vendor",
                "tool_input": {"file_path": ".studio/verify/R-1.md", "content": "PASS\n"}}

    def pre_command(self):
        return [h["command"] for e, _, h in self.ours() if e == "PreToolUse"][0]

    def test_noop_outside_the_cloud(self):
        self.vendor()
        payload = dict(self.PRE_DENY, cwd=self.project)
        r = self.run_hook_command(self.pre_command(), payload, remote=False)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_in_the_cloud_it_returns_the_plugin_decision(self):
        self.vendor()
        payload = dict(self.PRE_DENY, cwd=self.project)
        r = self.run_hook_command(self.pre_command(), payload, remote=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        plugin = subprocess.run([sys.executable, PLUGIN_HOOK, "pre-tool-use"], input=json.dumps(payload),
                                env=dict(self.env), capture_output=True, text=True)
        self.assertEqual(json.loads(sl.dash_names(plugin.stdout)), out)   # R-67: same decision, cloud agent names

    def test_vendored_scripts_find_their_root_without_plugin_root(self):
        self.vendor()
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", self.env)
        code = ("import sys; sys.path.insert(0, %r); import studiolib as sl; print(sl.studio_root())"
                % self.path(".claude", "budgit", "scripts"))
        r = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True)
        self.assertEqual(os.path.realpath(r.stdout.strip()), os.path.realpath(self.path(".claude", "budgit")))

    def test_session_start_persists_the_cli_on_path_and_tells_the_model(self):
        self.vendor()
        envfile = os.path.join(self.tmp, "envfile")
        cmd = [h["command"] for e, _, h in self.ours() if e == "SessionStart"][0]
        env = dict(self.env, CLAUDE_PROJECT_DIR=self.project, CLAUDE_CODE_REMOTE="true", CLAUDE_ENV_FILE=envfile)
        payload = {"hook_event_name": "SessionStart", "session_id": "unbound-vendor", "cwd": self.project}
        for _ in range(2):
            r = subprocess.run(["sh", "-c", cmd], input=json.dumps(payload), env=env, capture_output=True,
                               text=True, cwd=self.project)
            self.assertEqual(r.returncode, 0, r.stderr)
        text = open(envfile).read()
        self.assertEqual(text.count("export PATH="), 1)         # idempotent
        self.assertIn(os.path.join(".claude", "budgit", "bin"), text)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(os.path.join(".claude", "budgit", "bin", "budgit"), ctx)
        self.assertIn("budgit-verifier", ctx)

    def test_session_start_without_env_file_still_names_the_cli(self):
        self.vendor()
        cmd = [h["command"] for e, _, h in self.ours() if e == "SessionStart"][0]
        r = self.run_hook_command(cmd, {"hook_event_name": "SessionStart", "session_id": "unbound-vendor",
                                        "cwd": self.project}, remote=True)
        self.assertIn(os.path.join(".claude", "budgit", "bin", "budgit"),
                      json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_session_start_outside_the_cloud_adds_no_cloud_note(self):
        payload = {"hook_event_name": "SessionStart", "session_id": "unbound-vendor", "cwd": self.project}
        r = subprocess.run([sys.executable, PLUGIN_HOOK, "session-start"], input=json.dumps(payload),
                           env=self.env, capture_output=True, text=True)
        self.assertNotIn("budgit-verifier", r.stdout)


class DualIdentityTest(VendorCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.path(".studio"), exist_ok=True)

    def hook(self, script, event, payload):
        r = subprocess.run([sys.executable, script, event], input=json.dumps(payload),
                           env=dict(self.env), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def decision(self, script, tool, ti, agent_type):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti, "cwd": self.project,
                   "session_id": "unbound-dual"}
        if agent_type:
            payload.update(agent_type=agent_type, agent_id="a1")
        out = self.hook(script, "pre-tool-use", payload)
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")

    def scripts(self):
        self.vendor()
        return [PLUGIN_HOOK, self.path(".claude", "budgit", "scripts", "hook.py")]

    def test_r67_deny_texts_name_the_agents_the_session_has(self):
        """R-67 (cloud smoke 2026-10-20): the vendored guard told the model to dispatch budgit:scout-reader, an agent
        the cloud session does not have; there it is budgit-scout-reader. The plugin keeps the colon names."""
        raw = self.path(".studio", "research", "raw", "x.json")
        plugin, vendored = self.scripts()
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": raw},
                   "cwd": self.project, "session_id": "unbound-dual"}
        cloud = (self.hook(vendored, "pre-tool-use", payload).get("hookSpecificOutput") or {})
        local = (self.hook(plugin, "pre-tool-use", payload).get("hookSpecificOutput") or {})
        self.assertEqual(cloud.get("permissionDecision"), "deny")
        self.assertIn("budgit-scout-reader", cloud["permissionDecisionReason"])
        self.assertNotIn("budgit:", cloud["permissionDecisionReason"])
        self.assertIn("budgit:scout-reader", local["permissionDecisionReason"])

    def test_canon_agent_maps_only_the_studio_agents(self):
        with mock.patch.object(sl, "vendored", return_value=True):
            self._canon_maps()

    def test_plugin_runtime_never_maps_the_dash_names(self):
        # locally any repo's .claude/agents/budgit-verifier.md would otherwise pass as the plugin's budgit:verifier
        self.assertFalse(sl.vendored())
        for name in AGENTS:
            self.assertEqual(sl.canon_agent("budgit-" + name), "budgit-" + name)
        report = os.path.join(self.project, ".studio", "verify", "R-1.md")
        self.assertEqual(sl.verify_forgery_decision("Write", {"file_path": report}, "budgit-verifier",
                                                    self.project)[0], "deny")

    def _canon_maps(self):
        for name in AGENTS:
            self.assertEqual(sl.canon_agent("budgit-" + name), "budgit:" + name)
            self.assertEqual(sl.canon_agent("budgit:" + name), "budgit:" + name)
        for other in (None, "", "budgit-evil", "budgit-verifier2", "verifier", "general-purpose", "x-budgit-verifier"):
            self.assertEqual(sl.canon_agent(other), other)

    @mock.patch.object(sl, "vendored", return_value=True)
    def test_verify_forgery_accepts_both_verifier_names(self, _v):
        report = os.path.join(self.project, ".studio", "verify", "R-1.md")
        for who in ("budgit:verifier", "budgit-verifier"):
            self.assertIsNone(sl.verify_forgery_decision("Write", {"file_path": report}, who, self.project), who)
        for who in (None, "budgit-researcher", "budgit-evil", "verifier"):
            self.assertEqual(sl.verify_forgery_decision("Write", {"file_path": report}, who, self.project)[0],
                             "deny", who)

    def test_raw_store_reader_is_accepted_under_both_names_by_plugin_and_vendored_hook(self):
        raw = os.path.join(self.project, ".studio", "research", "raw", "x.json")
        plugin, vendored = self.scripts()
        self.assertEqual(self.decision(plugin, "Read", {"file_path": raw}, "budgit-scout-reader"), "deny")
        self.assertIsNone(self.decision(vendored, "Read", {"file_path": raw}, "budgit-scout-reader"))
        for script in (plugin, vendored):
            self.assertEqual(self.decision(script, "Read", {"file_path": raw}, None), "deny")
            self.assertEqual(self.decision(script, "Read", {"file_path": raw}, "budgit-researcher"), "deny")
            self.assertEqual(self.decision(script, "Read", {"file_path": raw}, "budgit-evil"), "deny")
            self.assertIsNone(self.decision(script, "Read", {"file_path": raw}, "budgit:scout-reader"))

    def test_verify_report_write_under_both_names(self):
        report = os.path.join(self.project, ".studio", "verify", "R-1.md")
        plugin, vendored = self.scripts()
        self.assertEqual(self.decision(plugin, "Write", {"file_path": report}, "budgit-verifier"), "deny")
        self.assertIsNone(self.decision(vendored, "Write", {"file_path": report}, "budgit-verifier"))
        for script in (plugin, vendored):
            self.assertEqual(self.decision(script, "Write", {"file_path": report}, None), "deny")
            self.assertEqual(self.decision(script, "Write", {"file_path": report}, "budgit-researcher"), "deny")
            self.assertIsNone(self.decision(script, "Write", {"file_path": report}, "budgit:verifier"))

    def test_the_verifier_seal_is_written_for_the_cloud_name(self):
        report = os.path.join(self.project, ".studio", "verify", "R-1.md")
        os.makedirs(os.path.dirname(report))
        open(report, "w").write("PASS\n")
        seals = os.path.join(self.home, "verify-seals.jsonl")
        post = {"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": report},
                "tool_response": {"success": True}, "cwd": self.project, "session_id": "unbound-dual"}
        vendored = self.scripts()[1]
        self.hook(vendored, "post-tool-use", dict(post, agent_type="budgit-researcher"))
        self.hook(PLUGIN_HOOK, "post-tool-use", dict(post, agent_type="budgit-verifier"))   # plugin: not an identity
        self.assertFalse(os.path.exists(seals))
        self.hook(vendored, "post-tool-use", dict(post, agent_type="budgit-verifier"))
        self.assertEqual(len(open(seals).read().splitlines()), 1)


class VendorCheckTest(VendorCase):
    def test_check_is_quiet_and_zero_when_versions_match(self):
        self.vendor()
        r = self.vendor("--check")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_check_reports_drift_in_one_line_and_exit_1(self):
        self.vendor()
        with open(self.path(".claude", "budgit", "VERSION"), "w") as f:
            f.write("0.0.1\n")
        r = self.vendor("--check")
        self.assertEqual(r.returncode, 1)
        text = (r.stdout + r.stderr).strip()
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn("0.0.1", text)
        self.assertIn(VERSION, text)
        self.assertIn("re-vendor", text)

    def test_check_when_never_vendored(self):
        r = self.vendor("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("re-vendor", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.path(".claude")))      # --check never writes
