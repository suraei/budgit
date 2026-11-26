"""R-283 S5: renewal by handoff and echo (`budgit handoff write/check`), `ledger handover`, and the two nudges
(renew at about 70 % of the window, and "this session has no task"). Fake names only."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT
from test_context_nudge import usage_line
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
OLD, NEW = "s-oldchat01", "s-newchat01"
WORDS = "pon el boton en azul, sin cambiar el menu"


def git(path, *args, **env):
    r = subprocess.run(["git", "-C", path, *args], env=dict(os.environ, **GIT_ENV, **env), capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


class HandoffCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        git(self.project, "init", "-q", "-b", "main")
        sl.activate(self.project, OLD)
        self.inbox_id = sl.append_inbox(self.project, WORDS, OLD, "manual")
        other = sl.append_inbox(self.project, "otra cosa distinta", OLD, "manual")
        with sl.locked(self.project):
            data = sl.load_ledger(self.project)
            sl.add_item(data, "boton azul", [self.inbox_id], title="Boton azul")
            sl.add_item(data, "otra cosa", [other], title="Otra")
            sl.save_ledger(self.project, data)
        self.cli("ledger", "start", "R-1", sid=OLD)
        git(self.project, "add", "-A")
        before = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))   # a commit from before the chat began
        git(self.project, "commit", "-q", "-m", "base", GIT_COMMITTER_DATE=before, GIT_AUTHOR_DATE=before)
        later = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 3600))   # after the bind, whatever the clock
        with open(os.path.join(self.project, "feature.txt"), "w") as f:
            f.write("x")
        git(self.project, "add", "feature.txt")
        git(self.project, "commit", "-q", "-m", "boton azul hecho", GIT_COMMITTER_DATE=later, GIT_AUTHOR_DATE=later)
        self.sha = git(self.project, "rev-parse", "--short", "HEAD")
        with open(os.path.join(self.project, "loose-file.txt"), "w") as f:
            f.write("x")

    def cli(self, *args, sid=None, ok=True):
        env = dict(os.environ, **GIT_ENV, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if sid:
            env["CLAUDE_CODE_SESSION_ID"] = sid
        r = subprocess.run([sys.executable, CLI, *args, "--project", self.project], env=env, capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def echo(self, text, name="echo.md"):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def write(self, lane="old"):
        self.cli("handoff", "write", "--lane", lane, sid=OLD)
        folder = os.path.join(sl.studio_dir(self.project), "private", "handoff")
        return open(os.path.join(folder, lane + ".md")).read(), json.load(open(os.path.join(folder, lane + ".facts.json")))

    def full_echo(self):
        return "Sigo R-1 (decision I-1: %s), commit %s en main, y falta guardar loose-file.txt." % (WORDS, self.sha)


class WriteTest(HandoffCase):
    def test_the_handoff_holds_what_can_be_read_and_leaves_marked_blanks(self):
        text, facts = self.write()
        self.assertIn("R-1 Boton azul", text)
        self.assertNotIn("R-2", text)                              # a task another chat holds, or nobody does, is not carried
        self.assertIn("I-1: " + WORDS, text)                       # her words verbatim
        self.assertIn(self.sha + " boton azul hecho", text)
        self.assertNotIn(" base", text)                            # a commit from before the chat began
        self.assertIn("Branch: main", text)
        self.assertIn("loose-file.txt", text)
        self.assertIn("<!-- FILL", text)
        self.assertEqual(facts["facts"], {"tasks": ["R-1"], "decisions": ["I-1"], "commits": [self.sha], "branch": ["main"],
                                          "uncommitted": ["loose-file.txt"]})

    def test_the_handoff_never_shows_up_in_git(self):
        self.write()
        git(self.project, "add", "-A")
        self.assertNotIn("handoff", git(self.project, "diff", "--cached", "--name-only"))

    def test_a_private_folder_that_is_a_link_is_never_written_through(self):
        outside = os.path.join(self.tmp, "outside-private")
        os.makedirs(outside)
        private = os.path.join(sl.studio_dir(self.project), "private")
        os.makedirs(private, exist_ok=True)
        os.rmdir(private) if not os.listdir(private) else __import__("shutil").rmtree(private)
        os.symlink(outside, private)
        r = self.cli("handoff", "write", sid=OLD, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(outside), [])

    def test_a_lane_name_cannot_walk_out_of_the_folder(self):
        r = self.cli("handoff", "write", "--lane", "../../evil", sid=OLD, ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.project, "evil.md")))


class WriteEdgeTest(HandoffCase):
    def test_file_names_are_the_real_names_not_git_quoted_ones(self):
        for name in ("ünï.txt", "with space.txt"):
            with open(os.path.join(self.project, name), "w") as f:
                f.write("x")
        git(self.project, "mv", "feature.txt", "new name.txt")
        text, facts = self.write()
        self.assertEqual(sorted(facts["facts"]["uncommitted"]), ["loose-file.txt", "new name.txt", "with space.txt", "ünï.txt"])
        echo = "R-1 I-1 %s main loose-file.txt ünï.txt with space.txt new name.txt" % self.sha
        r = self.cli("handoff", "check", "old", "--echo", self.echo(echo))
        self.assertIn("se puede archivar", r.stdout)

    def test_a_project_inside_a_bigger_repo_lists_only_its_own_files_relative_to_it(self):
        sub = os.path.join(self.project, "sub")
        os.makedirs(sub)
        sl.ensure_studio_dir(sub)
        sl.save_ledger(sub, sl.empty_ledger())
        with open(os.path.join(sub, "mine.txt"), "w") as f:
            f.write("x")
        env = dict(os.environ, **GIT_ENV, STUDIO_HOME=self.home)
        env["CLAUDE_CODE_SESSION_ID"] = OLD
        sl.activate(sub, OLD)
        r = subprocess.run([sys.executable, CLI, "handoff", "write", "--lane", "sub", "--project", sub], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        facts = json.load(open(os.path.join(sl.studio_dir(sub), "private", "handoff", "sub.facts.json")))["facts"]
        self.assertEqual(sorted(facts["uncommitted"]), [".gitignore", "mine.txt"])      # not loose-file.txt of the repo root

    def test_a_second_write_keeps_what_was_filled_in_and_refreshes_the_facts(self):
        text, facts = self.write()
        path = os.path.join(sl.studio_dir(self.project), "private", "handoff", "old.md")
        filled = open(path).read().replace("<!-- FILL: what the next chat does first, in order -->", "1. run the tests")
        with open(path, "w") as f:
            f.write(filled)
        with open(os.path.join(self.project, "newer.txt"), "w") as f:
            f.write("x")
        text, facts = self.write()
        self.assertIn("1. run the tests", text)
        self.assertIn("newer.txt", text)
        self.assertIn("newer.txt", facts["facts"]["uncommitted"])
        self.assertEqual(text.count("## Next steps"), 1)

    def test_a_pointer_with_a_ts_that_is_not_a_date_still_writes_and_says_the_start_is_unknown(self):
        for bad in (5, ["x"], "not a date"):
            rec = sl.read_pointer(OLD)
            rec["ts"] = bad
            with open(sl.pointer_path(OLD), "w") as f:
                json.dump(rec, f)
            text, facts = self.write()
            self.assertIn("start of this chat is unknown", text)
            self.assertEqual(facts["facts"]["commits"], [])

    def test_a_long_commit_list_says_it_is_cut(self):
        for n in range(sl_max() + 2):
            git(self.project, "commit", "-q", "--allow-empty", "-m", "c%d" % n,
                GIT_COMMITTER_DATE=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 7200)))
        text, facts = self.write()
        self.assertIn("see `git log`", text)
        self.assertEqual(len(facts["facts"]["commits"]), sl_max())


def sl_max():
    import handoff
    return handoff.MAX_COMMITS


class CheckTest(HandoffCase):
    def test_a_complete_echo_lets_the_old_chat_go(self):
        self.write()
        r = self.cli("handoff", "check", "old", "--echo", self.echo(self.full_echo()))
        self.assertIn("se puede archivar", r.stdout)

    def test_each_missing_fact_is_named_and_the_exit_is_non_zero(self):
        self.write()
        for drop, group in (("R-1", "R-1"), (self.sha, self.sha), ("I-1", "I-1"), ("main", "main"), ("loose-file.txt", "loose-file.txt")):
            text = self.full_echo().replace(drop, "xx")
            r = self.cli("handoff", "check", "old", "--echo", self.echo(text), ok=False)
            self.assertEqual(r.returncode, 1, drop)
            self.assertIn(group, r.stderr, drop)

    def test_r_1_is_not_said_by_r_12_nor_a_sha_by_a_longer_word(self):
        self.write()
        text = self.full_echo().replace("R-1 ", "R-12 ")
        self.assertEqual(self.cli("handoff", "check", "old", "--echo", self.echo(text), ok=False).returncode, 1)

    def test_editing_the_handoff_text_cannot_weaken_the_check(self):
        self.write()
        path = os.path.join(sl.studio_dir(self.project), "private", "handoff", "old.md")
        with open(path, "w") as f:
            f.write("nothing here")
        r = self.cli("handoff", "check", "old", "--echo", self.echo("nothing"), ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("R-1", r.stderr)

    def test_unfilled_blanks_are_a_warning_not_a_failure(self):
        self.write()
        r = self.cli("handoff", "check", "old", "--echo", self.echo(self.full_echo()))
        self.assertIn("huecos sin rellenar", r.stderr)

    def test_an_echo_that_is_not_text_or_facts_of_the_wrong_shape_are_errors_not_tracebacks(self):
        self.write()
        bad = os.path.join(self.tmp, "bin.md")
        with open(bad, "wb") as f:
            f.write(b"\xff\xfe R-1")
        r = self.cli("handoff", "check", "old", "--echo", bad, ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        facts = os.path.join(sl.studio_dir(self.project), "private", "handoff", "old.facts.json")
        for shape in ("[]", '{"facts": "R-1"}', '{"facts": null}', '{"facts": {"tasks": "R-1"}}', '{"facts": {"tasks": [1]}}'):
            with open(facts, "w") as f:
                f.write(shape)
            r = self.cli("handoff", "check", "old", "--echo", self.echo(self.full_echo()), ok=False)
            self.assertEqual(r.returncode, 1, shape)
            self.assertNotIn("Traceback", r.stderr, shape)

    def test_no_handoff_or_an_empty_echo_is_an_error(self):
        r = self.cli("handoff", "check", "ghost", "--echo", self.echo("x"), ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ghost", r.stderr)
        self.write()
        r = self.cli("handoff", "check", "old", "--echo", self.echo("  "), ok=False)
        self.assertEqual(r.returncode, 1)


class HandoverTest(HandoffCase):
    def handover(self, *extra, ok=True):
        return self.cli("ledger", "handover", "--from", OLD, "--to", NEW, *extra, ok=ok)

    def owner(self, item="R-1"):
        return sl.find_item(sl.load_ledger(self.project), item).get("doing_by")

    def test_the_claims_move_to_the_bound_successor(self):
        sl.activate(self.project, NEW)
        r = self.handover()
        self.assertIn("R-1", r.stdout)
        self.assertEqual(self.owner(), NEW)
        self.assertEqual(sl.find_item(sl.load_ledger(self.project), "R-1")["status"], "doing")

    def test_a_successor_that_is_not_bound_here_is_refused_and_nothing_moves(self):
        r = self.handover(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.owner(), OLD)

    def test_only_what_the_old_chat_holds_can_move(self):
        sl.activate(self.project, NEW)
        r = self.handover("--item", "R-2", ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("R-2", r.stderr)
        self.assertEqual(self.owner(), OLD)
        r = self.cli("ledger", "handover", "--from", NEW, "--to", OLD, ok=False)
        self.assertEqual(r.returncode, 1)                           # NEW holds nothing

    def test_same_session_is_refused(self):
        r = self.cli("ledger", "handover", "--from", OLD, "--to", OLD, ok=False)
        self.assertEqual(r.returncode, 1)


class NudgeTest(HandoffCase):
    def hook(self, sid, ctx=None):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            f.write(usage_line(ctx or 50000) + "\n")
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, "user-prompt-submit"], env=env, capture_output=True, text=True,
                           input=json.dumps({"session_id": sid, "prompt": "hola", "transcript_path": path}))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["systemMessage"] if r.stdout.strip() and "systemMessage" in r.stdout else ""

    def test_near_the_end_of_the_window_the_nudge_says_how_to_renew(self):
        msg = self.hook(OLD, 720000)
        self.assertIn("720k", msg)
        self.assertIn("handoff write", msg)
        self.assertEqual(self.hook(OLD, 720000), "")               # once

    def test_a_chat_with_a_claim_is_never_told_it_has_no_task(self):
        for _ in range(5):
            self.assertNotIn("no tiene tarea", self.hook(OLD))

    def test_a_bound_chat_without_a_claim_hears_it_once_after_a_few_prompts(self):
        sl.activate(self.project, NEW)
        said = [self.hook(NEW) for _ in range(6)]
        self.assertEqual(sum("no tiene tarea" in m for m in said), 1)
        self.assertIn("no tiene tarea", said[sl.NOTASK_TURNS - 1])
        self.assertIn("ledger start", said[sl.NOTASK_TURNS - 1])

    def test_the_project_manager_and_the_team_manager_are_not_nudged(self):
        sl.activate(self.project, NEW)
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "portfolio.json"), "w") as f:
            json.dump({"version": 1, "projects": [{"path": self.project, "pm": NEW}]}, f)
        self.assertFalse(any("no tiene tarea" in self.hook(NEW) for _ in range(5)))

    def test_an_unbound_chat_is_not_nudged(self):
        self.assertFalse(any("no tiene tarea" in self.hook("s-nobody001") for _ in range(5)))
