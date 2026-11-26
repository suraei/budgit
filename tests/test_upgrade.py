"""R-283 S4: `budgit upgrade` — four idempotent steps (u1 data folder, u2 CLAUDE.md names, u3 project.json defaults, u4 card
stamp), check/apply/undo, skip-not-force, one commit per step, protected output as codes only, release-check compat guard.
Real git fixtures, fake names only."""
import hashlib
import json
import os
import subprocess
import sys
import time

from loadcal import load_factor
from test_livework import CLI
from test_portfolio import PortfolioCase, SECRET
import card
import i18n
import studiolib as sl
import upgrade

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
OLD_RULES = "# Rules\nUse studio-kickoff and `studio activate`; the Budgit data lives in .studio/ledger.json.\nSee src/.studio/ for the other tool.\n"
NEW_RULES = "# Rules\nUse budgit-kickoff and `budgit activate`; the Budgit data lives in .budgit/ledger.json.\nSee src/.studio/ for the other tool.\n"


def git(path, *args, check=True):
    r = subprocess.run(["git", "-C", path, *args], env=dict(os.environ, **GIT_ENV), capture_output=True, text=True)
    if check:
        assert r.returncode == 0, (args, r.stderr)
    return r.stdout


def tree(path, skip=()):
    """sha256 over every file's relative path, mode and bytes (and every empty folder), so a byte or a mode that moves shows."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        h.update(("D" + os.path.relpath(root, path) + "\n").encode())
        for name in sorted(files):
            full = os.path.join(root, name)
            h.update(("F" + os.path.relpath(full, path) + oct(os.lstat(full).st_mode & 0o7777) + "\n").encode())
            if not os.path.islink(full):
                with open(full, "rb") as f:
                    h.update(f.read())
    return h.hexdigest()


def age(path, seconds=3600):
    past = time.time() - seconds
    for root, dirs, files in os.walk(path):
        for name in files + dirs:
            try:
                os.utime(os.path.join(root, name), (past, past), follow_symlinks=False)
            except OSError:
                pass


class UpgradeCase(PortfolioCase):
    def setUp(self):
        super().setUp()
        saved = {k: os.environ.get(k) for k in GIT_ENV}
        os.environ.update(GIT_ENV)                                  # the in-process calls commit as the subprocesses do
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in saved.items()])

    def repo(self, name="alpha", rules=OLD_RULES, committed=True, project_json=True, items=(("R-1", "open"),)):
        root = self.proj("Dev")
        path = os.path.join(root, name)
        os.makedirs(path, exist_ok=True)
        git(path, "init", "-q", "-b", "main")
        self.mk(path, list(items))
        if rules is not None:
            with open(os.path.join(path, "CLAUDE.md"), "w") as f:
                f.write(rules)
        if project_json:
            with open(os.path.join(path, ".studio", "project.json"), "w") as f:
                json.dump({"name": "Alfa", "verify": {"checks": ["true"]}}, f)
        with open(os.path.join(path, ".gitignore"), "w") as f:
            f.write(".studio/ledger.json\n.studio/private/\n")
        if committed:
            git(path, "add", "-A")
            git(path, "commit", "-q", "-m", "first")
        age(path)
        self.config(hub=path, root=root)
        return path

    def save(self, path, message="more"):
        """Commit what the test just wrote and age it, so the project is quiet and clean again."""
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", message, check=False)
        age(path)

    def up(self, *args, project=None, cwd=None, ok=(0,)):
        cmd = [sys.executable, CLI, "upgrade", *args] + (["--project", project] if project else [])
        r = subprocess.run(cmd, env=dict(os.environ, **GIT_ENV), capture_output=True, text=True, cwd=cwd or self.uhome)
        self.assertIn(r.returncode, ok, r.stdout + r.stderr)
        return r

    def data(self, *args, project=None, ok=(0, 2)):
        return json.loads(self.up("--json", *args, project=project, ok=ok).stdout)

    def row(self, out, name="alpha"):
        return next(r for r in out["rows"] if r["id"] == name)

    def states(self, path):
        return {s["id"]: s["state"] for s in upgrade.check(path)}

    def apply(self, path):
        return self.up("--apply", "--all", ok=(0, 1))

    def commits(self, path):
        return git(path, "log", "--format=%s%n%b---").strip()


class CheckTest(UpgradeCase):
    def test_check_changes_nothing_in_the_project_nor_the_git_dir_nor_the_home_and_says_2(self):
        path = self.repo()
        before = (tree(path), tree(self.bhome) if os.path.isdir(self.bhome) else None)
        r = self.up("--check", "--all", ok=(2,))
        self.assertIn("Alfa", r.stdout)
        self.assertEqual((tree(path), tree(self.bhome) if os.path.isdir(self.bhome) else None), before)

    def test_check_is_the_default_and_json_names_each_step(self):
        path = self.repo()
        out = self.data("--all")
        steps = self.row(out)["steps"]
        self.assertEqual([s["id"] for s in steps], ["u1", "u2", "u3", "u4"])
        self.assertEqual(steps[0]["state"], "pending")
        self.assertEqual([s["state"] for s in steps[1:]], ["blocked"] * 3)       # they wait for the data folder
        self.assertEqual(out["exit"], 2)

    def test_exit_0_once_everything_is_current(self):
        path = self.repo()
        self.apply(path)
        r = self.up("--check", "--all")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.row(self.data("--all"))["status"], "current")

    def test_off_projects_are_listed_not_touched_and_unmanaged_folders_are_not_managed(self):
        path = self.repo()
        os.makedirs(os.path.join(os.path.dirname(path), "taller"))
        self.assertEqual(self.up("--check", "--all", ok=(2,)).returncode, 2)
        self.run_cli("off", path, "--reason", "paused")
        before = tree(path)
        out = self.data("--all", ok=(0,))
        self.assertEqual(self.row(out)["status"], "off")
        self.assertEqual(self.row(out, "taller")["status"], "unmanaged")
        self.apply(path)
        self.assertEqual(tree(path), before)

    def test_attention_does_not_make_a_project_behind(self):
        path = self.repo()
        with open(os.path.join(path, ".studio", "project.json"), "w") as f:
            json.dump({"rigor": "standard", "protected_paths": [], "verify": {"prepare": [], "checks": []}, "lenses": []}, f)
        self.save(path)
        self.apply(path)
        step = next(s for s in upgrade.check(path) if s["id"] == "u3")
        self.assertEqual(step["state"], "current")
        self.assertEqual(sorted(step["attention"]), ["no_checks", "no_name"])
        self.assertEqual(self.up("--check", "--all").returncode, 0)


class U1Test(UpgradeCase):
    def test_apply_renames_the_folder_writes_the_marker_and_makes_one_commit_with_explicit_paths(self):
        path = self.repo()
        before = git(path, "rev-list", "--count", "HEAD").strip()
        self.apply(path)
        self.assertTrue(os.path.isdir(os.path.join(path, ".budgit")))
        self.assertFalse(os.path.lexists(os.path.join(path, ".studio")))
        self.assertTrue(os.path.isfile(os.path.join(path, ".budgit", sl.MIGRATED_MARKER)))
        self.assertIn(".budgit/private/", open(os.path.join(path, ".gitignore")).read())
        log = self.commits(path)
        self.assertIn("budgit upgrade u1", log)
        self.assertNotIn("Co-Authored-By", log)
        self.assertNotIn("Generated with", log)
        # u1, u2, u3 and u4 each make a commit only when git tracks something that changed
        self.assertGreater(int(git(path, "rev-list", "--count", "HEAD").strip()), int(before))
        self.assertEqual(git(path, "status", "--porcelain").strip(), "")

    def test_a_second_apply_does_nothing(self):
        path = self.repo()
        self.apply(path)
        before, head = tree(path), git(path, "rev-parse", "HEAD")
        self.apply(path)
        self.assertEqual((tree(path), git(path, "rev-parse", "HEAD")), (before, head))

    def test_undo_u1_puts_the_tree_back_byte_for_byte_and_commits_the_undo(self):
        path = self.repo()
        before = tree(path, skip=(".git",))
        steps = ["u4", "u3", "u2", "u1"]
        self.apply(path)
        for sid in steps:
            self.assertEqual(self.up("--undo", sid, "--project", path, ok=(0,)).returncode, 0, sid)
        self.assertEqual(tree(path, skip=(".git",)), before)
        self.assertEqual(self.states(path)["u1"], "pending")
        self.assertEqual(git(path, "status", "--porcelain").strip(), "")

    def test_undo_u1_is_refused_while_a_later_step_is_still_done(self):
        path = self.repo()
        self.apply(path)
        r = self.up("--undo", "u1", "--project", path, ok=(1,))
        self.assertIn("u2", r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(path, ".budgit")))

    def test_undo_twice_and_undo_of_a_step_never_run_say_there_is_nothing_to_undo(self):
        path = self.repo()
        self.apply(path)
        for sid in ("u4", "u3", "u2", "u1"):
            self.up("--undo", sid, "--project", path)
        r = self.up("--undo", "u1", "--project", path, ok=(1,))
        self.assertIn("nada que deshacer", r.stderr)
        r = self.up("--undo", "u9", "--project", path, ok=(1,))
        self.assertIn("paso desconocido", r.stderr)

    def test_undo_is_refused_when_the_touched_files_changed_since(self):
        path = self.repo()
        self.apply(path)
        with open(os.path.join(path, "CLAUDE.md"), "a") as f:
            f.write("a line she wrote after the upgrade\n")
        before = tree(path)
        r = self.up("--undo", "u2", "--project", path, ok=(1,))
        self.assertIn("cambiaron", r.stderr)
        self.assertEqual(tree(path), before)

    def test_a_stray_budgit_verify_folder_is_merged_and_comes_back_on_undo(self):
        path = self.repo()
        os.makedirs(os.path.join(path, ".studio", "verify"))
        with open(os.path.join(path, ".studio", "verify", "R-1.md"), "w") as f:
            f.write("PASS\n")
        os.makedirs(os.path.join(path, ".budgit", "verify", "sub"))
        with open(os.path.join(path, ".budgit", "verify", "R-2.md"), "w") as f:
            f.write("PASS two\n")
        with open(os.path.join(path, ".budgit", "verify", "sub", "R-3.md"), "w") as f:
            f.write("PASS three\n")
        with open(os.path.join(path, ".budgit", "verify", "R-1.md"), "w") as f:
            f.write("PASS\n")                                                   # the same bytes as the one already there
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "verify")
        age(path)
        before = tree(path, skip=(".git",))
        self.assertEqual(self.states(path)["u1"], "pending")
        self.apply(path)
        verify = os.path.join(path, ".budgit", "verify")
        self.assertEqual(open(os.path.join(verify, "R-2.md")).read(), "PASS two\n")
        self.assertEqual(open(os.path.join(verify, "sub", "R-3.md")).read(), "PASS three\n")
        for sid in ("u4", "u3", "u2", "u1"):
            self.up("--undo", sid, "--project", path)
        self.assertEqual(tree(path, skip=(".git",)), before)

    def test_an_os_error_half_way_through_the_merge_puts_everything_back(self):
        path = self.repo()
        sub = os.path.join(path, ".studio", "verify", "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "keep.md"), "w") as f:
            f.write("PASS\n")
        os.makedirs(os.path.join(path, ".budgit", "verify", "sub"))
        with open(os.path.join(path, ".budgit", "verify", "a.md"), "w") as f:
            f.write("a\n")
        with open(os.path.join(path, ".budgit", "verify", "sub", "b.md"), "w") as f:
            f.write("b\n")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "verify")
        age(path)
        before = tree(path, skip=(".git",))
        os.chmod(sub, 0o555)                                  # the copy into sub/ fails with PermissionError
        self.addCleanup(lambda: os.path.exists(sub) and os.chmod(sub, 0o755))
        r = self.apply(path)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.states(path)["u1"], "pending")
        self.assertFalse(os.path.exists(os.path.join(path, ".studio", "verify", "a.md")))
        self.assertFalse(os.path.exists(os.path.join(path, ".studio", ".lock")))
        os.chmod(sub, 0o755)
        self.assertEqual(tree(path, skip=(".git",)), before)

    def test_a_stray_file_with_other_bytes_refuses_the_step_and_changes_nothing(self):
        path = self.repo()
        os.makedirs(os.path.join(path, ".studio", "verify"))
        with open(os.path.join(path, ".studio", "verify", "R-1.md"), "w") as f:
            f.write("PASS\n")
        os.makedirs(os.path.join(path, ".budgit", "verify"))
        with open(os.path.join(path, ".budgit", "verify", "R-1.md"), "w") as f:
            f.write("FAIL\n")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "verify")
        age(path)
        before = tree(path)
        self.assertEqual(self.states(path)["u1"], "refused")
        out = json.loads(self.up("--apply", "--all", "--json", ok=(0, 1)).stdout)
        self.assertEqual(self.row(out)["skipped"], "collision")
        self.assertEqual(tree(path), before)

    def test_two_data_folders_that_both_hold_a_ledger_are_refused(self):
        path = self.repo()
        os.makedirs(os.path.join(path, ".budgit"))
        with open(os.path.join(path, ".budgit", "ledger.json"), "w") as f:
            f.write("{}")
        age(path)
        before = tree(path)
        self.assertEqual(upgrade.check(path)[0]["reason"], "both")
        self.apply(path)
        self.assertEqual(tree(path), before)

    def test_a_symlinked_data_folder_is_refused(self):
        path = self.repo()
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.rename(os.path.join(path, ".studio"), elsewhere)
        os.symlink(elsewhere, os.path.join(path, ".studio"))
        self.assertEqual(upgrade.check(path)[0]["reason"], "symlink")

    def test_a_project_that_is_not_in_git_still_upgrades_without_a_commit(self):
        root = self.proj("Dev")
        path = self.mk(os.path.join(root, "plain"), [("R-1", "open")])
        with open(os.path.join(path, "CLAUDE.md"), "w") as f:
            f.write(OLD_RULES)
        age(path)
        self.config(hub=path, root=root)
        before = tree(path)
        self.apply(path)
        self.assertEqual(open(os.path.join(path, "CLAUDE.md")).read(), NEW_RULES)
        self.assertFalse(os.path.exists(os.path.join(path, ".git")))
        for sid in ("u4", "u3", "u2", "u1"):
            self.up("--undo", sid, "--project", path)
        self.assertEqual(tree(path), before)


class U2Test(UpgradeCase):
    def test_only_lines_that_name_budgit_change_and_other_tools_paths_stay(self):
        text, n = upgrade.rewrite_names(OLD_RULES)
        self.assertEqual(text, NEW_RULES)
        self.assertEqual(n, 1)
        self.assertEqual(upgrade.rewrite_names(NEW_RULES), (NEW_RULES, 0))        # idempotent

    def test_apply_rewrites_the_file_keeping_its_mode_and_diff_shows_the_change_first(self):
        path = self.repo()
        os.chmod(os.path.join(path, "CLAUDE.md"), 0o644)
        shown = self.up("--diff", "--project", path).stdout
        self.assertIn("-Use studio-kickoff", shown)
        self.assertIn("+Use budgit-kickoff", shown)
        self.assertEqual(open(os.path.join(path, "CLAUDE.md")).read(), OLD_RULES)         # --diff changed nothing
        self.apply(path)
        self.assertEqual(open(os.path.join(path, "CLAUDE.md")).read(), NEW_RULES)
        self.assertEqual(os.stat(os.path.join(path, "CLAUDE.md")).st_mode & 0o7777, 0o644)

    def test_no_claude_md_is_nothing_to_do_and_a_symlink_or_binary_file_is_refused(self):
        path = self.repo(rules=None)
        self.assertEqual(upgrade.check_u2(path)["state"], "current")
        os.symlink("/dev/null", os.path.join(path, "CLAUDE.md"))
        self.assertEqual(upgrade.check_u2(path), {"state": "refused", "reason": "symlink"})
        os.remove(os.path.join(path, "CLAUDE.md"))
        with open(os.path.join(path, "CLAUDE.md"), "wb") as f:
            f.write(b"\xff\xfe studio activate")
        self.assertEqual(upgrade.check_u2(path), {"state": "refused", "reason": "unreadable"})


class U3Test(UpgradeCase):
    def test_missing_defaults_are_added_and_existing_values_never_overwritten(self):
        path = self.repo()
        with open(os.path.join(path, ".studio", "project.json"), "w") as f:
            json.dump({"name": "Alfa", "rigor": "strict", "verify": {"checks": ["make test"]}, "extra": 7}, f)
        self.save(path)
        self.apply(path)
        cfg = json.load(open(os.path.join(path, ".budgit", "project.json")))
        self.assertEqual(cfg["rigor"], "strict")
        self.assertEqual(cfg["verify"]["checks"], ["make test"])
        self.assertEqual(cfg["extra"], 7)
        self.assertEqual(cfg["protected_paths"], [])
        self.assertEqual(cfg["verify"]["prepare"], [])
        self.assertEqual(cfg["lenses"], [])

    def test_an_unreadable_project_json_is_refused_not_replaced(self):
        path = self.repo()
        with open(os.path.join(path, ".studio", "project.json"), "w") as f:
            f.write("{not json")
        self.save(path)
        self.assertEqual(self.states(path)["u1"], "pending")
        self.apply(path)
        self.assertEqual(self.states(path)["u3"], "refused")
        self.assertEqual(open(os.path.join(path, ".budgit", "project.json")).read(), "{not json")

    def _u3(self, raw):
        path = self.repo()
        with open(os.path.join(path, ".studio", "project.json"), "wb") as f:
            f.write(raw)
        self.save(path)
        self.apply(path)
        with open(os.path.join(path, ".budgit", "project.json"), "rb") as f:
            return path, f.read()

    def test_u3_keeps_the_files_own_style_and_only_adds_keys(self):
        raw = '{\n\t"name": "Ca\\u00f1a \u00e9",\n\t"rigor": "strict",\n\t"z": 1.50\n}'.encode("utf-8")   # tabs, \\u escape, no final newline
        path, out = self._u3(raw)
        text = out.decode("utf-8")
        self.assertTrue(text.startswith('{\n\t"name": "Ca\\u00f1a \\u00e9",\n\t"rigor": "strict",\n\t"z": 1.5,\n\t"'), text)
        self.assertFalse(text.endswith("\n"))
        self.assertEqual(json.loads(text)["name"], "Ca\u00f1a \u00e9")
        self.assertEqual(json.loads(text)["verify"]["prepare"], [])

    def test_u3_keeps_a_compact_file_compact(self):
        path, out = self._u3(b'{"name":"Alfa","rigor":"strict"}\n')
        self.assertTrue(out.startswith(b'{"name":"Alfa","rigor":"strict","'), out)
        self.assertNotIn(b"\n", out.strip())
        self.assertTrue(out.endswith(b"\n"))

    def test_a_project_json_with_a_bom_is_read_and_keeps_its_bom(self):
        path, out = self._u3(b'\xef\xbb\xbf{\n  "name": "Alfa",\n  "rigor": "strict"\n}\n')
        self.assertTrue(out.startswith(b"\xef\xbb\xbf{"))
        self.assertEqual(json.loads(out[3:])["rigor"], "strict")
        self.assertEqual(self.states(path)["u3"], "current")
        self.assertEqual(sl.load_project_config(path)["rigor"], "strict")        # the rest of Budgit reads it too

    def test_a_project_json_with_a_raw_non_utf8_byte_is_refused_without_a_traceback(self):
        path = self.repo()
        with open(os.path.join(path, ".studio", "project.json"), "wb") as f:
            f.write(b'{"name": "\xff"}')
        self.save(path)
        r = self.apply(path)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.states(path)["u3"], "refused")
        self.assertEqual(open(os.path.join(path, ".budgit", "project.json"), "rb").read(), b'{"name": "\xff"}')

    def test_a_lone_surrogate_escape_is_refused_too(self):
        path = self.repo()
        with open(os.path.join(path, ".studio", "project.json"), "w") as f:
            f.write('{"name": "\\ud800"}')
        self.save(path)
        r = self.apply(path)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.states(path)["u3"], "refused")


class U4Test(UpgradeCase):
    def test_the_card_records_the_running_version_and_the_rules_version(self):
        path = self.repo()
        self.apply(path)
        rec = card.read(path)
        self.assertEqual(rec["rules_version"], upgrade.RULES_VERSION)
        self.assertEqual(rec["budgit_version"], upgrade._running())
        self.assertEqual(upgrade.check_u4(path)["state"], "current")

    def test_a_different_version_makes_it_pending_again(self):
        path = self.repo()
        self.apply(path)
        card.stamp(path, budgit_version="0.0.1", rules_version="0")
        self.assertEqual(upgrade.check_u4(path)["state"], "pending")


class GateTest(UpgradeCase):
    def test_a_chat_open_in_the_project_or_a_worktree_skips_it_and_nothing_changes(self):
        path = self.repo()
        self.session("s-livechat01", path)
        before = tree(path)
        out = json.loads(self.up("--apply", "--all", "--json", ok=(0,)).stdout)
        self.assertEqual(self.row(out)["skipped"], "chat")
        self.assertEqual(self.row(out)["status"], "skipped")
        self.assertEqual(tree(path), before)

    def test_a_pointer_seen_in_the_last_five_minutes_skips_it(self):
        path = self.repo()
        self.pointer("s-recent0001", path)
        out = json.loads(self.up("--apply", "--all", "--json", ok=(0,)).stdout)
        self.assertEqual(self.row(out)["skipped"], "recent")

    def test_an_ended_or_old_pointer_does_not(self):
        path = self.repo()
        self.pointer("s-oldone0001", path)
        rec_path = os.path.join(self.bhome, "sessions", "s-oldone0001.json")
        rec = json.load(open(rec_path))
        rec["last_seen"] = rec["ts"] = "2020-01-01T00:00:00.000Z"
        json.dump(rec, open(rec_path, "w"))
        self.apply(path)
        self.assertEqual(self.states(path)["u1"], "current")

    def test_a_recent_write_in_the_data_folder_skips_u1(self):
        path = self.repo()
        os.utime(os.path.join(path, ".studio", "ledger.json"))
        out = json.loads(self.up("--apply", "--all", "--json", ok=(0,)).stdout)
        self.assertEqual(self.row(out)["skipped"], "recent")
        self.assertTrue(os.path.isdir(os.path.join(path, ".studio")))

    def test_uncommitted_changes_in_a_path_the_step_touches_skip_it_but_other_paths_do_not(self):
        path = self.repo()
        self.apply(path)
        with open(os.path.join(path, "CLAUDE.md"), "w") as f:
            f.write(OLD_RULES)
        self.save(path, "old rules back")
        with open(os.path.join(path, "CLAUDE.md"), "a") as f:
            f.write("unsaved\n")
        with open(os.path.join(path, "elsewhere.txt"), "w") as f:
            f.write("x")
        age(path)
        out = json.loads(self.up("--apply", "--all", "--json", ok=(0,)).stdout)
        self.assertEqual(self.row(out)["skipped"], "dirty")
        self.assertIn("unsaved", open(os.path.join(path, "CLAUDE.md")).read())
        self.assertNotIn("budgit-kickoff", open(os.path.join(path, "CLAUDE.md")).read())
        self.assertEqual(open(os.path.join(path, "elsewhere.txt")).read(), "x")

    def test_a_staged_change_in_a_touched_path_skips_it_and_the_index_is_left_alone(self):
        path = self.repo()
        self.apply(path)
        with open(os.path.join(path, "CLAUDE.md"), "w") as f:
            f.write(OLD_RULES)
        git(path, "add", "CLAUDE.md")
        git(path, "commit", "-q", "-m", "old rules back")
        with open(os.path.join(path, "other.txt"), "w") as f:
            f.write("staged")
        git(path, "add", "other.txt")
        age(path)
        self.apply(path)
        self.assertIn("A  other.txt", git(path, "status", "--porcelain"))           # still staged, not in the commit
        self.assertNotIn("other.txt", git(path, "show", "--stat", "--format=", "HEAD"))
        self.assertEqual(open(os.path.join(path, "CLAUDE.md")).read(), NEW_RULES)

    def test_steps_after_a_skipped_one_do_not_run(self):
        path = self.repo()
        self.session("s-livechat01", path)
        self.apply(path)
        self.assertEqual(set(self.states(path).values()), {"pending", "blocked"})

    def test_a_failed_step_is_undone_and_nothing_stays_half_done(self):
        path = self.repo()
        real = upgrade.CHECKS["u1"]
        upgrade.CHECKS["u1"] = lambda project: {"state": "pending", "changes": 1}      # the check after the step still finds work
        self.addCleanup(lambda: upgrade.CHECKS.__setitem__("u1", real))
        before = tree(path)
        res = upgrade.apply_project(path)
        self.assertEqual(res["skipped"], "failed")
        self.assertEqual(tree(path), before)

    def test_a_commit_that_cannot_be_made_undoes_the_step(self):
        path = self.repo()
        with open(os.path.join(path, ".git", "index.lock"), "w") as f:
            f.write("")                                                              # git cannot take the index: the commit fails
        before = tree(path, skip=(".git",))
        res = upgrade.apply_project(path)
        self.assertEqual(res["skipped"], "commit")
        self.assertEqual(tree(path, skip=(".git",)), before)
        self.assertEqual(self.states(path)["u1"], "pending")

    def test_with_no_git_identity_the_project_is_upgraded_and_the_commit_is_skipped(self):
        path = self.repo()
        saved = {k: os.environ.pop(k, None) for k in GIT_ENV}
        os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        try:
            res = upgrade.apply_project(path)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertIsNone(res["skipped"])
        self.assertEqual({s["state"] for s in res["steps"]}, {"applied"})
        self.assertEqual(res["steps"][0]["commit"], "noident")
        self.assertEqual(git(path, "rev-list", "--count", "HEAD").strip(), "1")


class ProtectedTest(UpgradeCase):
    def setup_protected(self):
        prot = self.mk(os.path.join(self.uhome, "Cofre"), [("R-1", "open")])
        git(prot, "init", "-q", "-b", "main")
        with open(os.path.join(prot, "CLAUDE.md"), "w") as f:
            f.write(OLD_RULES + "# %s\n" % SECRET)
        with open(os.path.join(prot, ".studio", "project.json"), "w") as f:
            json.dump({"name": SECRET, "private": True}, f)
        with open(os.path.join(prot, SECRET + ".txt"), "w") as f:
            f.write(SECRET)
        git(prot, "checkout", "-q", "-b", "branch-" + SECRET)
        age(prot)
        root = self.proj("Dev")
        hub = self.repo()
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"hub": hub, "projects_root": root, "protected_dirs": [prot]}, f)
        self.ok("add", prot)
        return prot

    def test_from_outside_check_apply_and_json_show_step_ids_and_codes_only(self):
        prot = self.setup_protected()
        for args in (["--check", "--all"], ["--check", "--all", "--json"], ["--apply", "--all"], ["--apply", "--all", "--json"]):
            r = self.up(*args, ok=(0, 1, 2))
            for text in (r.stdout, r.stderr):
                self.assertNotIn(SECRET, text, args)
                self.assertNotIn("cofre", text.lower(), args)
                self.assertNotIn("branch-", text, args)
                self.assertNotIn("kickoff", text.lower(), args)
        rows = [r for r in self.data("--all", ok=(0, 2))["rows"] if r["protected"]]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["id"].startswith("private~"))
        self.assertNotIn("name", rows[0])
        for step in rows[0].get("steps", []):
            self.assertLessEqual(set(step), {"id", "state", "reason", "commit"})

    def test_diff_and_undo_by_name_from_outside_show_nothing_of_a_protected_project(self):
        prot = self.setup_protected()
        rows = [r for r in self.data("--all", ok=(0, 2))["rows"] if r["protected"]]
        r = self.up("--diff", rows[0]["id"], ok=(1,))
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertNotIn("studio-kickoff", r.stdout)
        r = self.up("--undo", "u1", rows[0]["id"], ok=(1,))
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertRegex(r.stderr.strip(), r"^[a-z_]+$")                            # a code, not a sentence

    def test_inside_its_own_chat_the_diff_is_shown(self):
        prot = self.setup_protected()
        r = self.up("--diff", project=prot)
        self.assertIn("budgit-kickoff", r.stdout)


class I18nTest(UpgradeCase):
    def test_every_key_exists_in_english_and_spanish(self):
        keys = [k for k in i18n.CATALOG["en"] if k.startswith("upgrade.")]
        self.assertTrue(keys)
        for k in keys:
            self.assertIn(k, i18n.CATALOG["es"], k)

    def test_the_text_output_is_in_the_chosen_language(self):
        self.repo()
        self.assertIn("atrasado", self.up("--check", "--all", ok=(2,)).stdout)
        out = subprocess.run([sys.executable, CLI, "upgrade", "--check", "--all"], env=dict(os.environ, BUDGIT_LANG="en"),
                             capture_output=True, text=True, cwd=self.uhome).stdout
        self.assertIn("behind", out)


class CompatGuardTest(UpgradeCase):
    def release(self, dropped, plugin=None):
        root = plugin or os.path.join(self.tmp, "plugin")
        os.makedirs(root, exist_ok=True)
        if dropped is not None:
            with open(os.path.join(root, "compat.json"), "w") as f:
                json.dump({"dropped": dropped}, f)
        return root

    def release_check(self, dropped, project, plugin=None):
        """`budgit release-check` run in this process with compat.json naming `dropped`: (exit code, output)."""
        import argparse
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("budgit_cli_upgrade", CLI)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli_upgrade", loader))
        loader.exec_module(mod)
        real = upgrade.compat_dropped
        upgrade.compat_dropped = lambda root=None: real(self.release(dropped, plugin))
        self.addCleanup(lambda: setattr(upgrade, "compat_dropped", real))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod.cmd_release_check(argparse.Namespace(project=project, items=None, since=None))
        return code, out.getvalue()

    def test_without_a_compat_file_or_with_nothing_dropped_it_adds_nothing(self):
        path = self.repo(items=(("R-1", "done"),))
        self.assertEqual(upgrade.compat_dropped(self.release(None)), [])
        self.assertEqual(upgrade.compat_dropped(self.release([])), [])
        self.assertEqual(upgrade.compat_problems([]), [])
        code, out = self.release_check([], path)
        self.assertEqual(code, 0)
        self.assertNotIn("compatibilidad", out)

    def test_a_dropped_path_blocks_the_release_while_a_project_still_needs_the_step_and_names_it(self):
        path = self.repo()
        problems = upgrade.compat_problems(["dotstudio"])
        self.assertEqual((problems[0]["kind"], problems[0]["step"], problems[0]["projects"]), ("behind", "u1", ["Alfa"]))
        code, out = self.release_check(["dotstudio"], path)
        self.assertEqual(code, 1)
        self.assertIn("Alfa", out)
        self.assertIn("u1", out)

    def test_a_skipped_project_still_blocks_and_an_off_one_does_not(self):
        path = self.repo()
        self.session("s-livechat01", path)
        self.apply(path)
        self.assertTrue(upgrade.compat_problems(["dotstudio"]))
        self.run_cli("off", path, "--reason", "paused")
        self.assertEqual(upgrade.compat_problems(["dotstudio"]), [])

    def test_everything_upgraded_means_the_release_is_clear(self):
        path = self.repo(items=(("R-1", "done"),))
        self.apply(path)
        self.assertEqual(upgrade.compat_problems(["dotstudio", "studio-names"]), [])
        code, out = self.release_check(["dotstudio", "studio-names"], path)
        self.assertEqual(code, 0)

    def test_an_unknown_name_or_one_no_step_can_guard_fails_closed(self):
        path = self.repo()
        self.apply(path)
        self.assertEqual(upgrade.compat_problems(["nonsense"])[0]["kind"], "unguarded")
        self.assertEqual(upgrade.compat_problems(["studio-env"])[0]["kind"], "unguarded")
        self.assertEqual(self.release_check(["studio-env"], path)[0], 1)

    def test_a_protected_project_is_counted_never_named(self):
        prot = self.mk(os.path.join(self.uhome, "Cofre"), [("R-1", "open")])
        os.makedirs(self.bhome, exist_ok=True)
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"protected_dirs": [prot]}, f)
        self.ok("add", prot)
        problems = upgrade.compat_problems(["dotstudio"])
        self.assertEqual((len(problems), problems[0]["projects"], problems[0]["private"]), (1, [], 1))
        code, out = self.release_check(["dotstudio"], prot)
        self.assertNotIn("cofre", out.lower())
        self.assertIn("+1", out)

    def test_a_broken_compat_file_is_an_error_not_silence(self):
        root = os.path.join(self.tmp, "plugin")
        os.makedirs(root)
        with open(os.path.join(root, "compat.json"), "w") as f:
            f.write("{broken")
        with self.assertRaises(sl.StudioError):
            upgrade.compat_dropped(root)


class PerfTest(UpgradeCase):
    def test_check_for_ten_projects_one_with_a_hundred_dirty_paths_is_fast(self):
        root = self.proj("Dev")
        first = None
        for n in range(10):
            path = self.mk(os.path.join(root, "p%02d" % n), [("R-1", "open")])
            git(path, "init", "-q", "-b", "main")
            git(path, "add", "-A")
            git(path, "commit", "-q", "-m", "first")
            first = first or path
        for n in range(100):
            with open(os.path.join(first, "dirty%03d.txt" % n), "w") as f:
                f.write("x")
        self.config(hub=first, root=root)
        start = time.monotonic()
        self.up("--check", "--all", ok=(2,))
        self.assertLess(time.monotonic() - start, 3.0 * load_factor())     # real-deadline budget 3 s scaled by the same-run calibration (R-1282)


class RenderTest(UpgradeCase):
    def test_a_refused_step_says_why_in_words_and_a_protected_one_only_by_code(self):
        path = self.repo()
        os.makedirs(os.path.join(path, ".budgit"))
        with open(os.path.join(path, ".budgit", "ledger.json"), "w") as f:
            f.write("{}")
        age(path)
        out = self.up("--check", "--all", ok=(2,)).stdout
        self.assertIn("las dos carpetas de datos", out)
        text = upgrade.render({"mode": "check", "rows": [{"id": "private~ab12cd", "protected": True, "channel": "current",
                                                          "status": "behind", "steps": [{"id": "u1", "state": "refused",
                                                                                         "reason": "both"}]}]}, "es")
        self.assertIn("[both]", text)
        self.assertNotIn("carpetas", text)

    def test_a_protected_project_level_skip_is_shown_by_code_too(self):
        for reason in ("chat", "recent", "dirty", "time", "failed"):
            text = upgrade.render({"mode": "apply", "rows": [{"id": "private~ab12cd", "protected": True, "channel": "current",
                                                              "status": "skipped", "skipped": reason, "steps": []}]}, "es")
            self.assertIn("[%s]" % reason, text)
            self.assertEqual(text.count("["), 1)                          # no sentence in words


class RaceTest(UpgradeCase):
    def test_a_run_that_waited_for_the_lock_finds_the_step_done_and_saves_no_second_record(self):
        path = self.repo()
        self.apply(path)
        record = upgrade._record_path(path, "u1")
        saved = open(record).read()
        ran = {}
        self.assertIsNone(upgrade._run_step(path, "u1", time.time(), ran))
        self.assertEqual(open(record).read(), saved)                 # the undo record of the real run is untouched
        self.assertEqual(ran, {})

    def test_the_budget_stops_apply_between_projects_never_inside_one(self):
        path = self.repo()
        real = upgrade.BUDGET_S
        upgrade.BUDGET_S = -1
        self.addCleanup(lambda: setattr(upgrade, "BUDGET_S", real))
        before = tree(path)
        out = upgrade.run("apply")
        self.assertEqual(out["rows"][0]["skipped"], "time")
        self.assertEqual(tree(path), before)
