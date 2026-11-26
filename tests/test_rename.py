"""R-120 (docs/plans/2026-10-21-r120-rename-budgit.md): the data-folder resolver, `budgit migrate`, the guard for a
shadowing `.budgit`, the old-CLI shim, agent namespaces, session-start conflict note, the export's both-ledgers check and
the case-insensitive grep allow-list. The guard/quarantine bypass suites run on both names in test_both_names.py."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest

from helpers import ROOT, StudioTestCase
import studiolib as sl

BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")
SHIM = os.path.join(ROOT, "plugin", "bin", "studio")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


def age(path, seconds):
    """Pretend every file under `path` was last written `seconds` ago."""
    t = time.time() - seconds
    for d, dirs, files in os.walk(path):
        for n in files + dirs:
            os.utime(os.path.join(d, n), (t, t))
    os.utime(path, (t, t))


class ResolverTest(StudioTestCase):
    def test_only_the_old_folder_is_read_and_written(self):
        os.makedirs(os.path.join(self.project, ".studio"))
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".studio"))
        sl.save_ledger(self.project, sl.empty_ledger())
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".studio", "ledger.json")))
        self.assertFalse(os.path.exists(os.path.join(self.project, ".budgit")))

    def test_nothing_yet_creates_the_new_folder(self):
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".budgit"))

    def test_budgit_folder_with_the_marker_wins(self):
        touch(os.path.join(self.project, ".studio", "ledger.json"))
        touch(os.path.join(self.project, ".budgit", sl.MIGRATED_MARKER))
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".budgit"))

    def test_budgit_folder_with_a_ledger_counts_without_the_marker(self):
        touch(os.path.join(self.project, ".budgit", "ledger.json"), "{}")
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".budgit"))

    def test_an_empty_or_stray_budgit_never_shadows_the_old_folder(self):
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")
        os.makedirs(os.path.join(self.project, ".budgit"))
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".studio"))
        touch(os.path.join(self.project, ".budgit", "notes.txt"))             # marker-less, ledger-less
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".studio"))

    def test_two_live_ledgers_without_a_marker_keep_the_old_one(self):
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")
        touch(os.path.join(self.project, ".budgit", "ledger.json"), "{}")
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".studio"))

    def test_an_old_folder_without_a_ledger_never_outranks_a_valid_budgit(self):
        touch(os.path.join(self.project, ".budgit", "ledger.json"), "{}")
        os.makedirs(os.path.join(self.project, ".studio"))                       # a stale session recreated it, empty
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".budgit"))
        touch(os.path.join(self.project, ".studio", "hooks.log"))
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".budgit"))
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")        # a real old ledger and no marker: old wins
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".studio"))

    def test_ledger_check_still_reads_the_old_ledger_when_a_stray_folder_appears(self):
        self.init_project()                                                    # .studio with an empty ledger
        sl.save_ledger(self.project, dict(sl.empty_ledger(), items=[{"id": "R-1", "text": "keep me", "status": "open", "src": "t", "evidence": None}]))
        os.makedirs(os.path.join(self.project, ".budgit"))
        self.assertEqual([i["id"] for i in sl.load_ledger(self.project)["items"]], ["R-1"])

    def test_home_state_precedence(self):
        saved = {k: os.environ.get(k) for k in ("BUDGIT_HOME", "STUDIO_HOME", "HOME")}
        try:
            os.environ["HOME"] = self.tmp
            os.environ.pop("BUDGIT_HOME", None)
            os.environ["STUDIO_HOME"] = os.path.join(self.tmp, "old-home")
            self.assertEqual(sl.home_state(), os.path.join(self.tmp, "old-home"))     # STUDIO_HOME fallback
            os.environ["BUDGIT_HOME"] = os.path.join(self.tmp, "new-home")
            self.assertEqual(sl.home_state(), os.path.join(self.tmp, "new-home"))     # BUDGIT_HOME first
            del os.environ["BUDGIT_HOME"], os.environ["STUDIO_HOME"]
            self.assertEqual(sl.home_state(), os.path.join(self.tmp, ".budgit"))      # nothing there: the new one
            os.makedirs(os.path.join(self.tmp, ".studio"))
            self.assertEqual(sl.home_state(), os.path.join(self.tmp, ".studio"))      # old state still read
            touch(os.path.join(self.tmp, ".budgit", sl.MIGRATED_MARKER))
            self.assertEqual(sl.home_state(), os.path.join(self.tmp, ".budgit"))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_every_budgit_env_var_falls_back_to_its_old_name(self):
        for name in ("HOME", "COMPACT_WINDOW", "BIN", "CODEX_BIN", "LIVE", "CHROME", "IMPECCABLE"):
            os.environ.pop("BUDGIT_" + name, None)
            os.environ["STUDIO_" + name] = "old"
            try:
                self.assertEqual(sl.env(name), "old", name)
                os.environ["BUDGIT_" + name] = "new"
                self.assertEqual(sl.env(name), "new", name)
            finally:
                os.environ.pop("BUDGIT_" + name, None)
                os.environ.pop("STUDIO_" + name, None)
        self.assertIsNone(sl.env("NOPE"))
        self.assertEqual(sl.env("NOPE", "d"), "d")


class MigrateTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")
        touch(os.path.join(self.project, ".studio", "verify", "R-1.md"), "PASS\n")
        age(os.path.join(self.project, ".studio"), 3600)

    def test_only_the_old_folder_is_renamed_in_one_step_with_one_line(self):
        line = sl.migrate_dir(self.project)
        self.assertEqual(line.count("\n"), 0)                                  # no git here: one line
        self.assertIn("migrated", line)
        self.assertFalse(os.path.exists(os.path.join(self.project, ".studio")))
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".budgit", "verify", "R-1.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".budgit", sl.MIGRATED_MARKER)))
        self.assertEqual(sl.studio_dir(self.project), os.path.join(self.project, ".budgit"))

    def test_nothing_to_migrate_returns_none(self):
        os.rename(os.path.join(self.project, ".studio"), os.path.join(self.project, "elsewhere"))
        self.assertIsNone(sl.migrate_dir(self.project))

    def test_a_recent_write_by_another_session_refuses_unless_forced(self):
        touch(os.path.join(self.project, ".studio", "hooks.log"), "now\n")
        with self.assertRaises(sl.StudioError) as ctx:
            sl.migrate_dir(self.project)
        self.assertIn("--force", str(ctx.exception))
        self.assertTrue(os.path.isdir(os.path.join(self.project, ".studio")))      # nothing moved
        self.assertTrue(sl.migrate_dir(self.project, force=True))
        self.assertTrue(os.path.isdir(os.path.join(self.project, ".budgit")))

    def test_both_folders_refuse_and_move_nothing(self):
        os.makedirs(os.path.join(self.project, ".budgit"))
        with self.assertRaises(sl.StudioError):
            sl.migrate_dir(self.project, force=True)
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".studio", "ledger.json")))
        self.assertEqual(os.listdir(os.path.join(self.project, ".budgit")), [])

    def test_a_symlinked_old_folder_refuses(self):
        target = os.path.join(self.tmp, "real")
        os.rename(os.path.join(self.project, ".studio"), target)
        os.symlink(target, os.path.join(self.project, ".studio"))
        with self.assertRaises(sl.StudioError):
            sl.migrate_dir(self.project, force=True)
        self.assertTrue(os.path.islink(os.path.join(self.project, ".studio")))

    def test_in_a_git_repo_the_gitignore_gets_the_new_lines_and_a_commit_hint(self):
        subprocess.run(["git", "init", "-q", self.project], check=True)
        line = sl.migrate_dir(self.project)
        self.assertIn("git -C", line)
        with open(os.path.join(self.project, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read().splitlines()
        for want in sl.gitignore_lines(".budgit"):
            self.assertIn(want, ignored)

    def test_custom_old_gitignore_lines_move_to_the_new_name(self):
        subprocess.run(["git", "init", "-q", self.project], check=True)
        with open(os.path.join(self.project, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("node_modules\n.studio/private/\n/.studio/scratch/\n# .studio/commented/\n.studio/\n")
        line = sl.migrate_dir(self.project)
        with open(os.path.join(self.project, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read().splitlines()
        for want in (".budgit/private/", "/.budgit/scratch/", ".budgit/"):
            self.assertIn(want, ignored)
        self.assertNotIn(".budgit/commented/", ignored)
        self.assertEqual(len(ignored), len(set(ignored)))                      # no duplicates
        self.assertNotIn("add -A", line)
        self.assertIn("--ignore-unmatch", line)                                # works even when nothing was tracked

    def test_whole_folder_and_glob_ignore_forms_move_too_and_private_files_stay_unstaged(self):
        git = ["git", "-C", self.project]
        subprocess.run(git[:1] + ["init", "-q", self.project], check=True)
        touch(os.path.join(self.project, ".studio", "private", "retro.md"), "secret\n")
        age(os.path.join(self.project, ".studio"), 3600)
        for form in (".studio", "/.studio", "**/.studio/private/", "!.studio/keep"):
            with open(os.path.join(self.project, ".gitignore"), "w", encoding="utf-8") as f:
                f.write(form + "\n")
            with open(os.path.join(self.project, ".git", "info", "exclude"), "a", encoding="utf-8") as f:
                f.write(".studio/excluded/\n")
            sl.migrate_dir(self.project, force=True)
            with open(os.path.join(self.project, ".gitignore"), encoding="utf-8") as f:
                ignored = f.read().splitlines()
            self.assertIn(form.replace(".studio", ".budgit"), ignored, form)
            subprocess.run(git + ["rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", ".studio"], check=True)
            subprocess.run(git + ["add", ".gitignore"], check=True)
            subprocess.run(git + ["add", ".budgit"], capture_output=True)                  # exits 1 when the whole folder is ignored
            staged = subprocess.run(git + ["diff", "--cached", "--name-only"], capture_output=True, text=True).stdout
            if form != "!.studio/keep":                       # a negation alone ignores nothing; the others must hide private/
                self.assertNotIn("private/retro.md", staged, form)
            subprocess.run(git + ["reset", "-q"], check=True)
            os.rename(os.path.join(self.project, ".budgit"), os.path.join(self.project, ".studio"))
            age(os.path.join(self.project, ".studio"), 3600)
        with open(os.path.join(self.project, ".git", "info", "exclude"), encoding="utf-8") as f:
            self.assertIn(".budgit/excluded/", f.read().splitlines())                      # exclude file twins too

    def test_a_report_sealed_under_the_old_name_still_verifies_after_the_move(self):
        report = os.path.join(self.project, ".studio", "verify", "R-1.md")
        sealed_key = sl._seal_key(report)
        sl.migrate_dir(self.project)
        moved = os.path.join(self.project, ".budgit", "verify", "R-1.md")
        self.assertEqual(sl._seal_key(moved), sealed_key)


class CliTest(StudioTestCase):
    def run_cli(self, exe, *args, cwd=None):
        env = dict(os.environ, BUDGIT_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, exe] + list(args), cwd=cwd or self.project, env=env,
                              capture_output=True, text=True)

    def test_budgit_help_works(self):
        r = self.run_cli(BUDGIT, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("budgit", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_the_old_name_still_works_and_prints_the_deprecation_line_once(self):
        r = self.run_cli(SHIM, "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("renamed to `budgit`"), 1)
        self.assertIn("budgit", r.stdout)

    def test_migrate_command_renames_and_reports(self):
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")
        age(os.path.join(self.project, ".studio"), 3600)
        r = self.run_cli(BUDGIT, "migrate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("migrated", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".budgit", "ledger.json")))

    def test_migrate_refuses_while_a_session_wrote_recently(self):
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")
        r = self.run_cli(BUDGIT, "migrate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--force", r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.project, ".studio")))
        r = self.run_cli(BUDGIT, "migrate", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_migrate_with_nothing_to_do_says_so(self):
        r = self.run_cli(BUDGIT, "migrate")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to migrate", r.stdout)

    def test_a_new_project_gets_the_new_folder(self):
        r = self.run_cli(BUDGIT, "activate", self.project, "--no-bind")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".budgit", "ledger.json")))
        self.assertFalse(os.path.exists(os.path.join(self.project, ".studio")))

    def test_a_stray_budgit_in_a_subfolder_does_not_hide_the_project_above(self):
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")
        age(os.path.join(self.project, ".studio"), 3600)
        sub = os.path.join(self.project, "pkg", "deep")
        os.makedirs(os.path.join(sub, ".budgit"))                                # empty: not valid data
        r = self.run_cli(BUDGIT, "migrate", cwd=sub)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.project, ".budgit", "ledger.json")))
        self.assertEqual(os.listdir(os.path.join(sub, ".budgit")), [])

    def test_migrate_all_moves_the_hub_and_every_project_and_reports_the_ones_it_refuses(self):
        dev = os.path.join(self.tmp, "dev")
        for name in ("a", "b", "c"):
            touch(os.path.join(dev, name, ".studio", "ledger.json"), "{}")
            age(os.path.join(dev, name, ".studio"), 3600)
        touch(os.path.join(dev, "c", ".studio", "hooks.log"), "just now\n")      # another session is writing in c
        hub = os.path.join(self.tmp, "hub")
        touch(os.path.join(hub, ".studio", "ledger.json"), "{}")
        age(os.path.join(hub, ".studio"), 3600)
        touch(os.path.join(self.home, "config.json"), json.dumps({"hub": hub, "projects_root": dev}))
        r = self.run_cli(BUDGIT, "migrate", "--all")
        self.assertEqual(r.returncode, 1)                                          # c refused
        self.assertIn("--force", r.stderr)
        for path in (os.path.join(dev, "a"), os.path.join(dev, "b"), hub):
            self.assertTrue(os.path.isfile(os.path.join(path, ".budgit", sl.MIGRATED_MARKER)), path)
            self.assertFalse(os.path.exists(os.path.join(path, ".studio")), path)
        self.assertTrue(os.path.isdir(os.path.join(dev, "c", ".studio")))
        self.assertEqual(self.run_cli(BUDGIT, "migrate", "--all", "--force").returncode, 0)
        self.assertTrue(os.path.isdir(os.path.join(dev, "c", ".budgit")))

    def test_register_jsx_leaves_the_folder_names_to_the_cli(self):      # R-238: one rule, in Python (`find_project`)
        with open(os.path.join(ROOT, "plugin", "hooks", "register.jsx"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("hasProject", js)
        self.assertNotIn("/.budgit'", js)
        self.assertNotIn("/.studio'", js)


class HookCase(StudioTestCase):
    def fire(self, event, payload, **env):
        payload.setdefault("session_id", "unbound-r120")
        payload.setdefault("cwd", self.project)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), capture_output=True, text=True,
                           env=dict(os.environ, BUDGIT_HOME=self.home, **env))
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def decision(self, tool, tool_input, agent_type=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
        if agent_type:
            payload.update(agent_type=agent_type, agent_id="a1")
        return (self.fire("pre-tool-use", payload).get("hookSpecificOutput") or {}).get("permissionDecision")


class ShadowGuardTest(HookCase):
    def setUp(self):
        super().setUp()
        self.init_project()                                                     # a live .studio

    def test_mkdir_of_budgit_beside_the_live_old_folder_is_denied(self):
        for cmd in ("mkdir .budgit", "mkdir -p .budgit/verify", "touch .budgit/MIGRATED",
                    "ln -s /tmp/x .budgit", "install -d %s" % os.path.join(self.project, ".budgit")):
            self.assertEqual(self.decision("Bash", {"command": cmd}), "deny", cmd)

    def test_writing_a_file_into_a_new_budgit_beside_the_old_folder_is_denied(self):
        self.assertEqual(self.decision("Write", {"file_path": os.path.join(self.project, ".budgit", "ledger.json"),
                                                 "content": "{}"}), "deny")

    def test_after_migrate_the_new_folder_is_normal_again(self):
        age(os.path.join(self.project, ".studio"), 3600)
        sl.migrate_dir(self.project)
        self.assertNotEqual(self.decision("Bash", {"command": "mkdir -p .budgit/notes"}), "deny")

    def test_creating_the_old_folder_beside_a_migrated_one_is_denied(self):
        touch(os.path.join(self.project, ".budgit", "ledger.json"), "{}")
        shutil.rmtree(os.path.join(self.project, ".studio"))
        for cmd in ("mkdir .studio", "mkdir -p .studio/verify", "touch .studio/ledger.json", "ln -s /tmp/x .studio"):
            self.assertEqual(self.decision("Bash", {"command": cmd}), "deny", cmd)
        self.assertEqual(self.decision("Write", {"file_path": os.path.join(self.project, ".studio", "ledger.json"),
                                                 "content": "{}"}), "deny")

    def test_the_old_folder_with_its_ledger_stays_writable_while_both_are_live(self):
        touch(os.path.join(self.project, ".budgit", "ledger.json"), "{}")        # .studio keeps its ledger.json
        self.assertNotEqual(self.decision("Bash", {"command": "touch .studio/notes.txt"}), "deny")

    def test_other_directories_are_unaffected(self):
        self.assertNotEqual(self.decision("Bash", {"command": "mkdir -p src/.budgit-cache"}), "deny")
        self.assertNotEqual(self.decision("Bash", {"command": "mkdir build"}), "deny")


class SealAndAgentNamesTest(HookCase):
    def test_budgit_hook_forgery_is_denied_like_the_old_name(self):
        for cmd in ("BUDGIT_HOME=/tmp/x budgit ledger verify R-1",
                    "STUDIO_HOME=/tmp/x python3 plugin/bin/studio ledger verify R-1",
                    "BUDGIT_HOME=/tmp/x python3 plugin/bin/budgit ledger verify R-1"):
            self.assertEqual(self.decision("Bash", {"command": cmd}), "deny", cmd)

    def test_prepare_approve_asks_for_both_cli_names(self):
        for cmd in ("budgit prepare --approve", "studio prepare --approve"):
            self.assertEqual(self.decision("Bash", {"command": cmd}), "ask", cmd)

    def test_canon_agent_maps_both_namespaces_to_the_new_one(self):
        self.assertEqual(sl.canon_agent("budgit:verifier"), "budgit:verifier")
        self.assertEqual(sl.canon_agent("studio:verifier"), "budgit:verifier")
        self.assertEqual(sl.canon_agent("other:verifier"), "other:verifier")
        self.assertEqual(sl.canon_agent("budgit:nonsense"), "budgit:nonsense")
        self.assertEqual(sl.canon_agent("budgit-verifier"), "budgit-verifier")   # dash names only under a vendored runtime

    def test_only_the_scout_reader_reads_the_quarantine_under_either_name(self):
        raw = os.path.join(self.project, ".budgit", "research", "raw", "x.json")
        touch(raw, "{}")
        touch(os.path.join(self.project, ".budgit", "MIGRATED"))
        self.assertEqual(self.decision("Read", {"file_path": raw}), "deny")
        self.assertEqual(self.decision("Read", {"file_path": raw}, "budgit:researcher"), "deny")
        self.assertIsNone(self.decision("Read", {"file_path": raw}, "budgit:scout-reader"))
        self.assertIsNone(self.decision("Read", {"file_path": raw}, "studio:scout-reader"))


class SessionStartConflictTest(HookCase):
    def test_a_reappeared_old_folder_is_reported_with_its_inbox_count(self):
        touch(os.path.join(self.project, ".budgit", "ledger.json"), "{}")
        touch(os.path.join(self.project, ".budgit", sl.MIGRATED_MARKER))
        touch(os.path.join(self.project, ".studio", "inbox.jsonl"), '{"a":1}\n{"a":2}\n{"a":3}\n')
        line = sl.data_dir_conflict(self.project)
        self.assertIn(".studio", line)
        self.assertIn("3 inbox line", line)

    def test_no_conflict_with_one_folder(self):
        touch(os.path.join(self.project, ".studio", "ledger.json"), "{}")
        self.assertIsNone(sl.data_dir_conflict(self.project))

    def test_session_start_prints_the_note(self):
        touch(os.path.join(self.project, ".budgit", sl.MIGRATED_MARKER))
        sl.save_ledger(self.project, sl.empty_ledger())
        touch(os.path.join(self.project, ".studio", "inbox.jsonl"), '{"a":1}\n')
        import hook
        hook._OUT.clear()
        hook._START[:] = []
        hook.on_session_start({"session_id": "s1"}, self.project)
        hook._finish_start({"session_id": "s1"}, self.project)       # R-1137: the sections are fitted to the role's budget and emitted once
        context = hook._OUT["hookSpecificOutput"]["additionalContext"]
        hook._OUT.clear()
        self.assertIn("1 inbox line", context)


class ExportReadsBothLedgersTest(StudioTestCase):
    def test_her_words_check_reads_old_and_new_projects(self):
        import export
        dev = os.path.join(self.tmp, "dev")
        touch(os.path.join(dev, "old", ".studio", "ledger.json"),
              json.dumps({"items": [{"id": "R-1", "text": "alpha bravo charlie delta echo foxtrot"}]}))
        touch(os.path.join(dev, "new", ".budgit", "ledger.json"),
              json.dumps({"items": [{"id": "R-1", "text": "golf hotel india juliet kilo lima"}]}))
        touch(os.path.join(dev, "new", ".budgit", sl.MIGRATED_MARKER))
        texts = export._ledger_texts({"projects_root": dev})
        self.assertIn("alpha bravo charlie delta echo foxtrot", texts)
        self.assertIn("golf hotel india juliet kilo lima", texts)


class FlipIdentityTest(StudioTestCase):
    """tools/flip-identity.py: the one step that changes the plugin id and the hub marketplace name."""
    SCRIPT = os.path.join(ROOT, "tools", "flip-identity.py")

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "hub")
        for rel in (("plugin", ".claude-plugin", "plugin.json"), (".claude-plugin", "marketplace.json")):
            src = os.path.join(ROOT, *rel)
            dst = os.path.join(self.root, *rel)
            os.makedirs(os.path.dirname(dst))
            with open(src, encoding="utf-8") as f:
                data = json.load(f)
            data["name"] = {"plugin.json": "studio", "marketplace.json": "studio-hub"}[rel[-1]]      # the fixture is the OLD identity: the real tree flipped for good in R-191
            for entry in data.get("plugins", []):
                entry["name"] = "studio"
            with open(dst, "w", encoding="utf-8") as g:
                json.dump(data, g)

    def run_flip(self, *args):
        return subprocess.run([sys.executable, self.SCRIPT, "--root", self.root] + list(args), capture_output=True,
                              text=True)

    def names(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("flip_identity", self.SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.names(self.root)

    def test_the_fixture_is_the_old_identity(self):
        cur = self.names()
        self.assertEqual((cur["plugin"], cur["marketplace"], cur["listed"]), ("studio", "studio-hub", ["studio"]))

    @unittest.skip("0.13.2 migration (R-296, R-298): the identity flip and folder rename ship in 0.13.2, not 0.13.1")
    def test_the_source_tree_carries_the_budgit_identity(self):
        # R-191: the host prefixes the status line with the plugin name, so the source is named like the export
        import importlib.util
        spec = importlib.util.spec_from_file_location("flip_identity_real", self.SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cur = mod.names(ROOT)
        self.assertEqual((cur["plugin"], cur["marketplace"], cur["listed"]), ("budgit", "budgit-hub", ["budgit"]))

    def test_flip_renames_both_files_and_is_idempotent(self):
        r = self.run_flip()
        self.assertEqual(r.returncode, 0, r.stderr)
        cur = self.names()
        self.assertEqual((cur["plugin"], cur["marketplace"], cur["listed"]), ("budgit", "budgit-hub", ["budgit"]))
        self.assertIn("nothing to change", self.run_flip().stdout)

    def test_check_changes_nothing_and_undo_restores(self):
        before = self.names()
        self.assertIn("plugin: studio", self.run_flip("--check").stdout)
        self.assertEqual(self.names(), before)
        self.run_flip()
        self.run_flip("--undo")
        self.assertEqual(self.names(), before)

    def test_description_and_version_are_left_alone(self):
        path = os.path.join(self.root, "plugin", ".claude-plugin", "plugin.json")
        with open(path, encoding="utf-8") as f:
            before = json.load(f)
        self.run_flip()
        with open(path, encoding="utf-8") as f:
            after = json.load(f)
        self.assertEqual({k: v for k, v in before.items() if k != "name"}, {k: v for k, v in after.items() if k != "name"})


class AllowRulesTest(StudioTestCase):
    """tools/migrate-allow-rules.py adds the budgit twin of each Bash(studio …) rule and keeps the old ones."""
    SCRIPT = os.path.join(ROOT, "tools", "migrate-allow-rules.py")

    def setUp(self):
        super().setUp()
        self.file = os.path.join(self.tmp, "settings.json")
        self.settings = {"model": "x", "permissions": {"allow": ["Bash(studio ledger:*)", "Read(//tmp/**)",
                                                                  "Bash(studio status:*)"]}}
        with open(self.file, "w") as f:
            json.dump(self.settings, f)

    def run_script(self, *args):
        return subprocess.run([sys.executable, self.SCRIPT, "--file", self.file] + list(args), capture_output=True,
                              text=True)

    def test_dry_run_changes_nothing(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Bash(budgit ledger:*)", r.stdout)
        with open(self.file) as f:
            self.assertEqual(json.load(f), self.settings)
        self.assertFalse(os.path.exists(self.file + ".bak-r120"))

    def test_apply_adds_twins_keeps_old_rules_and_backs_up(self):
        self.assertEqual(self.run_script("--apply").returncode, 0)
        with open(self.file) as f:
            got = json.load(f)
        self.assertEqual(got["permissions"]["allow"], self.settings["permissions"]["allow"] +
                         ["Bash(budgit ledger:*)", "Bash(budgit status:*)"])
        self.assertEqual(got["model"], "x")
        with open(self.file + ".bak-r120") as f:
            self.assertEqual(json.load(f), self.settings)

    def test_second_run_finds_nothing_to_add(self):
        self.run_script("--apply")
        self.assertIn("nothing to add", self.run_script("--apply").stdout)


class PipedNameFilterTest(HookCase):
    """A `| grep -v <name> | xargs` stage only hides the quarantine when it drops the folder the raw files sit in."""

    def raw_denied(self, cmd):
        import hook
        return hook.raw_reason({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.project}) is not None

    def put_raw(self, folder):
        touch(os.path.join(self.project, folder, "research", "raw", "2026-10-19-q-web.json"), '{"t": "SYNTH"}')

    def test_raw_under_the_old_folder(self):
        self.init_project()
        self.put_raw(".studio")
        touch(os.path.join(self.project, "src", "a.py"), "x\n")
        self.assertTrue(self.raw_denied("find . -type f | grep -v budgit | xargs cat"))
        self.assertTrue(self.raw_denied("find . -type f | grep -v '.budgit' | xargs grep SYNTH"))
        self.assertFalse(self.raw_denied("find . -type f | grep -v studio | xargs cat"))
        self.assertFalse(self.raw_denied("find . -type f | grep -v -E 'studio|budgit' | xargs cat"))

    def test_raw_under_the_new_folder(self):
        self.init_project()
        self.put_raw(".budgit")
        self.assertTrue(self.raw_denied("find . -type f | grep -v studio | xargs cat"))
        self.assertFalse(self.raw_denied("find . -type f | grep -v budgit | xargs cat"))

    def test_raw_under_both_folders_needs_both_names(self):
        self.init_project()
        self.put_raw(".studio")
        self.put_raw(".budgit")
        self.assertTrue(self.raw_denied("find . -type f | grep -v budgit | xargs cat"))
        self.assertTrue(self.raw_denied("find . -type f | grep -v studio | xargs cat"))
        self.assertFalse(self.raw_denied("find . -type f | grep -v -E 'studio|budgit' | xargs cat"))


class GrepAllowListTest(unittest.TestCase):
    """A case-insensitive grep for `studio` in plugin/, skills/ and share/ finds only allow-listed hits: internal
    identifiers (studiolib, StudioError, studio_dir …) and the compat paths (old folder, old env vars, old CLI/agent
    names, old plugin id until the identity flip). Anything else is a product name that was missed."""
    INTERNAL = re.compile(r"\w*(?:studiolib|StudioError|studio_)\w*|\b\w+_studio\w*")
    COMPAT = re.compile(
        r"\.studio\b|STUDIO_|\bstudio:|\bstudio-|bin/studio|[\"']studio[\"']|OLD_|CLI_NAMES|compat|before 0\.12|"
        r"pre-0\.12|old name|renamed to|command -v studio|\(\"studio\", \"budgit\"\)|\"studio\", \"budgit\"|"
        r"a studio around Claude Code|studio\|budgit|budgit\|studio|\"name\": \"studio\"|\bstudio\b.{0,12}\bX\b|`studio`|studio …", re.I)

    def test_only_allow_listed_hits_remain(self):
        stray = []
        for top in ("plugin", "skills", "share"):
            for d, dirs, files in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [x for x in dirs if x != "__pycache__" and os.path.join(d, x) != os.path.join(ROOT, "plugin", "float")]
                for name in files:                                    # plugin/float is the signed bundle: its bytes cannot change, its old-name checks are deliberate
                    path = os.path.join(d, name)
                    if name.endswith((".svg", ".pyc")) or name == "CHANGELOG.md" or os.path.islink(path):
                        continue
                    with open(path, encoding="utf-8", errors="replace") as f:
                        for n, line in enumerate(f, 1):
                            rest = self.INTERNAL.sub("", line)
                            if re.search(r"(?<![A-Za-z])studio", rest, re.I) and not self.COMPAT.search(rest):
                                stray.append("%s:%d: %s" % (os.path.relpath(path, ROOT), n, line.strip()[:120]))
        self.assertEqual(stray, [], "a product-name `studio` slipped through:\n" + "\n".join(stray))


if __name__ == "__main__":
    unittest.main()
