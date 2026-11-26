"""R-283 S6: `budgit sweep` — the read-only hygiene list. Real git fixtures, injected ps/lsof, fake names only."""
import json
import os
import subprocess
import sys
import time

from test_livework import CLI
from test_portfolio import PortfolioCase, SECRET
import portfolio
import sweep
import studiolib as sl

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def git(path, *args):
    env = dict(os.environ, **GIT_ENV)
    r = subprocess.run(["git", "-C", path, *args], env=env, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


class SweepCase(PortfolioCase):
    def sweep_cli(self, *args, project=None, cwd=None):
        cmd = [sys.executable, CLI, "sweep", *args] + (["--project", project] if project else [])
        r = subprocess.run(cmd, env=dict(os.environ, **GIT_ENV), capture_output=True, text=True, cwd=cwd or self.uhome)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def data(self, *args, project=None):
        return json.loads(self.sweep_cli("--json", *args, project=project))

    def repo(self, name="alpha", items=(("R-1", "open"),)):
        root = self.proj("Dev")
        path = os.path.join(root, name)
        os.makedirs(path, exist_ok=True)
        git(path, "init", "-q", "-b", "main")
        self.mk(path, list(items))
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "first")
        self.config(hub=path, root=root)
        return path

    def wt(self, project, name, branch=None, old=True, commits=0):
        folder = os.path.join(self.uhome, "wts", name)
        git(project, "worktree", "add", "-q", "-b", branch or name, folder)
        for n in range(commits):
            with open(os.path.join(folder, "f%d.txt" % n), "w") as f:
                f.write("x")
            git(folder, "add", "-A")
            git(folder, "commit", "-q", "-m", "c%d" % n)
        if old:
            gitdir = git(folder, "rev-parse", "--absolute-git-dir").strip()
            past = time.time() - 3 * 86400
            os.utime(os.path.join(gitdir, "HEAD"), (past, past))
        return folder

    def wt_rows(self, out, name="alpha"):
        p = next(p for p in out["projects"] if p["name"] == name)
        return {r["name"]: r for r in p["worktrees"]}


class WorktreeTest(SweepCase):
    def test_a_merged_clean_old_worktree_is_removable(self):
        p = self.repo()
        self.wt(p, "done-lane")
        row = self.wt_rows(self.data(project=p))["done-lane"]
        self.assertEqual((row["state"], row["reason"]), ("removable", "merged"))

    def test_a_worktree_that_just_started_is_not_removable_even_when_clean_and_merged(self):
        p = self.repo()
        self.wt(p, "new-lane", old=False)
        row = self.wt_rows(self.data(project=p))["new-lane"]
        self.assertEqual((row["state"], row["reason"]), ("ask", "young"))

    def test_unmerged_commits_dirty_files_and_a_private_ledger_change_are_all_ask(self):
        p = self.repo()
        self.wt(p, "ahead", commits=1)
        dirty = self.wt(p, "dirtyone")
        with open(os.path.join(dirty, "scratch.txt"), "w") as f:
            f.write("x")
        data_only = self.wt(p, "dataonly")
        os.makedirs(os.path.join(data_only, ".studio", "private"), exist_ok=True)
        with open(os.path.join(data_only, ".studio", "private", "note.txt"), "w") as f:
            f.write("x")
        rows = self.wt_rows(self.data(project=p))
        self.assertEqual((rows["ahead"]["state"], rows["ahead"]["reason"]), ("ask", "unmerged"))
        self.assertEqual((rows["dirtyone"]["state"], rows["dirtyone"]["reason"]), ("ask", "dirty"))
        self.assertEqual(rows["dataonly"]["state"], "ask")         # not hidden by the data-folder exclusion
        self.assertIsInstance(rows["ahead"]["age_days"], int)

    def test_a_worktree_with_a_chat_in_it_is_ask(self):
        p = self.repo()
        folder = self.wt(p, "busy-lane")
        self.session("s-busy0001", os.path.join(folder, "sub"))
        row = self.wt_rows(self.data(project=p))["busy-lane"]
        self.assertEqual((row["state"], row["reason"]), ("ask", "live"))

    def test_a_detached_worktree_is_ask(self):
        p = self.repo()
        folder = os.path.join(self.uhome, "wts", "det")
        git(p, "worktree", "add", "-q", "--detach", folder)
        row = self.wt_rows(self.data(project=p))["det"]
        self.assertEqual(row["state"], "ask")

    def test_nothing_is_changed_by_the_sweep(self):
        p = self.repo()
        folder = self.wt(p, "done-lane")
        index = os.path.join(p, ".git", "index")
        git(p, "status")                                       # settle the index: the test's own status calls refresh it
        before = (os.stat(index).st_mtime_ns, git(p, "--no-optional-locks", "status", "--porcelain=v1", "--ignored"),
                  git(p, "worktree", "list"), open(sl._ledger_path(p)).read(), os.stat(sl._ledger_path(p)).st_mtime_ns)
        self.sweep_cli("--all")
        self.sweep_cli("--json", project=p)
        after = (os.stat(index).st_mtime_ns, git(p, "--no-optional-locks", "status", "--porcelain=v1", "--ignored"),
                 git(p, "worktree", "list"), open(sl._ledger_path(p)).read(), os.stat(sl._ledger_path(p)).st_mtime_ns)
        self.assertEqual(before, after)
        self.assertTrue(os.path.isdir(folder))

    def test_git_that_cannot_run_says_unknown_not_clean(self):
        p = self.repo()
        self.wt(p, "done-lane")
        real = sweep._run
        sweep._run = lambda argv, timeout=sweep.TIMEOUT_S: None
        try:
            out = sweep.collect(p)
        finally:
            sweep._run = real
        row = out["projects"][0]
        self.assertEqual(row["worktrees_status"], "unknown")
        self.assertEqual(row["worktrees"], [])

    def test_a_missing_project_folder_is_reported_not_an_error(self):
        p = self.repo()
        self.view()
        import shutil
        gone = self.mk(os.path.join(self.proj("Dev"), "gone"), [("R-1", "open")])
        self.ok("add", gone)
        shutil.rmtree(gone)
        out = self.data("--all")
        self.assertIn("missing", {x["status"] for x in out["projects"]})


class ClaimsTest(SweepCase):
    def test_a_doing_claim_with_no_change_for_a_week_is_listed_with_its_age(self):
        old = {"updated": "2026-01-01T00:00:00.000Z", "created": "2026-01-01T00:00:00.000Z"}
        p = self.repo(items=(("R-7", "doing", old), ("R-8", "doing", {"updated": sl.now(), "created": sl.now()})))
        row = self.data(project=p)["projects"][0]
        self.assertEqual([c["id"] for c in row["claims"]], ["R-7"])
        self.assertGreater(row["claims"][0]["age_days"], 30)
        text = self.sweep_cli(project=p)
        self.assertIn("R-7", text)
        self.assertNotIn("R-8", text)

    def test_a_stray_second_data_folder_and_the_dirty_count_are_shown(self):
        p = self.repo()
        os.makedirs(os.path.join(p, ".budgit"))
        with open(os.path.join(p, "loose.txt"), "w") as f:
            f.write("x")
        row = self.data(project=p)["projects"][0]
        self.assertTrue(row["stray_data"])
        self.assertGreaterEqual(row["dirty"], 1)


class ProtectedTest(SweepCase):
    def setUp(self):
        super().setUp()
        root = self.proj("Dev")
        self.hub = self.mk(os.path.join(root, "hubby"), [("R-1", "open")])
        self.vault = os.path.join(root, "zor-vault-%s" % SECRET)
        os.makedirs(self.vault)
        git(self.vault, "init", "-q", "-b", "main")
        old = {"updated": "2026-01-01T00:00:00.000Z", "created": "2026-01-01T00:00:00.000Z"}
        self.mk(self.vault, [("R-1", "doing", dict(old, title=SECRET, text=SECRET))])
        with open(os.path.join(self.vault, "CLAUDE.md"), "w") as f:
            f.write(SECRET)
        git(self.vault, "add", "-A")
        git(self.vault, "commit", "-q", "-m", "first")
        with open(os.path.join(self.vault, "file-%s.txt" % SECRET), "w") as f:
            f.write("x")
        self.lane = self.wt(self.vault, "lane-%s" % SECRET, branch="branch-%s" % SECRET)
        self.config(hub=self.hub, root=root)
        self.ok("add", self.vault)
        self.ok("protect", self.vault)
        self.session("s-secret001", self.lane, name="title " + SECRET)
        self.pointer_ended("s-ended0001", self.vault)

    def pointer_ended(self, sid, project, claim=None):
        os.makedirs(os.path.join(self.bhome, "sessions"), exist_ok=True)
        stamp = sl.now()
        with open(os.path.join(self.bhome, "sessions", sid + ".json"), "w") as f:
            json.dump({"project": project, "ts": stamp, "last_seen": stamp, "ended": stamp}, f)

    def test_a_protected_project_shows_counts_only_in_text_and_json(self):
        real = (sweep._ps, sweep._cwds, sweep._ports)
        sweep._ps = lambda: [(4321, 120, "node %s/node_modules/.bin/vite --branch branch-%s" % (self.lane, SECRET))]
        sweep._cwds = lambda pids: {4321: self.lane}
        sweep._ports = lambda pids: {4321: [5173]}
        try:
            out = sweep.collect(None, all_projects=True)
        finally:
            sweep._ps, sweep._cwds, sweep._ports = real
        blob = json.dumps(out) + sweep.render(out, "es") + sweep.render(out, "en")
        for secret in (SECRET, "zor-vault", os.path.basename(self.vault), "branch-", "file-"):
            self.assertNotIn(secret, blob)
        row = next(p for p in out["projects"] if p["protected"])
        self.assertEqual(row["claims"], {"stuck": 1})
        self.assertEqual(set(row["worktrees"]), {"removable", "ask"})
        self.assertEqual(out["processes"]["protected"], 1)
        self.assertEqual(out["processes"]["items"], [])
        self.assertEqual(out["pointers"]["ended_protected"], 1)
        self.assertEqual(out["pointers"]["ended"], [])

    def test_the_real_command_never_leaks_a_protected_name_or_a_session_title(self):
        for args in (("--all",), ("--all", "--json")):
            out = self.sweep_cli(*args)
            self.assertNotIn(SECRET, out)
            self.assertNotIn("zor-vault", out)


    def test_a_worktree_of_a_protected_project_is_that_project_for_a_pointer_and_for_the_current_folder(self):
        self.pointer_ended("s-lanept0001", self.lane)
        out = sweep.collect(None, all_projects=True)
        self.assertEqual(out["pointers"]["ended_protected"], 2)
        self.assertEqual(out["pointers"]["ended"], [])
        here = sweep.collect(self.lane)
        blob = json.dumps(here) + sweep.render(here, "es")
        self.assertNotIn(SECRET, blob)
        self.assertTrue(here["projects"][0]["protected"])
        self.assertEqual(here["projects"][0]["id"].split("~")[0], "private")


class PointerTest(SweepCase):
    def ptr(self, sid, project, ended=True):
        os.makedirs(os.path.join(self.bhome, "sessions"), exist_ok=True)
        stamp = sl.now()
        rec = {"project": project, "ts": stamp, "last_seen": stamp}
        if ended:
            rec["ended"] = stamp
        with open(os.path.join(self.bhome, "sessions", sid + ".json"), "w") as f:
            json.dump(rec, f)

    def test_ended_sessions_are_listed_and_only_those_without_a_claim_are_archivable(self):
        p = self.repo(items=(("R-1", "doing", {"doing_by": "s-holder001"}), ("R-2", "open")))
        self.session("s-live00001", p)
        self.ptr("s-live00001", p, ended=False)
        self.ptr("s-free00001", p)
        self.ptr("s-holder001", p)
        self.ptr("s-unsure001", p, ended=False)
        out = self.data("--all")["pointers"]
        by = {e["session"]: e for e in out["ended"]}
        self.assertEqual(set(by), {"s-free00001", "s-holder001"})
        self.assertTrue(by["s-free00001"]["archivable"])
        self.assertFalse(by["s-holder001"]["archivable"])
        self.assertEqual(out["unverified"], 1)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(self.data("--all")["archivable"], ["s-free00001"])

    def test_an_unreadable_live_list_makes_the_section_unknown_not_everyone_ended(self):
        p = self.repo()
        self.ptr("s-free00001", p)
        import shutil
        shutil.rmtree(self.sessions_dir)                     # the app's live list is absent
        out = self.data("--all")
        self.assertEqual(out["pointers"]["status"], "unknown")
        self.assertEqual(out["archivable"], [])


class ProcessTest(SweepCase):
    def run_sweep(self, p, ps, cwds, ports=None):
        real = (sweep._ps, sweep._cwds, sweep._ports)
        sweep._ps, sweep._cwds, sweep._ports = (lambda: ps), (lambda pids: cwds), (lambda pids: ports or {})
        try:
            return sweep.collect(None, all_projects=True)
        finally:
            sweep._ps, sweep._cwds, sweep._ports = real

    def test_dev_servers_and_test_runs_in_a_registered_project_are_listed_by_kind_without_their_command(self):
        p = self.repo()
        folder = self.wt(p, "lane1")
        self.session("s-lane00001", folder)
        out = self.run_sweep(p, [
            (101, 3700, "python3 -m http.server 8000 --bind SECRET-ARG"), (102, 59, "node /x/node_modules/.bin/vite"),
            (103, 90061, "python3 -m unittest discover"), (104, 5, "/usr/bin/invite-friends --now"),
            (105, 10, "vite-plugin-helper something"), (106, 7, "node /elsewhere/vite")],
            {101: p, 102: folder, 103: p + "2/other", 104: p, 105: p, 106: "/somewhere/else"},
            {101: [8000], 102: [5173]})
        items = {r["pid"]: r for r in out["processes"]["items"]}
        self.assertEqual(set(items), {101, 102})
        self.assertEqual((items[101]["kind"], items[101]["ports"], items[101]["project"]), ("http.server", [8000], "alpha"))
        self.assertEqual(items[102]["session"], "s-lane00001")
        self.assertIsNone(items[101]["session"])
        self.assertNotIn("SECRET-ARG", json.dumps(out))
        self.assertTrue(all("command" not in r and "cwd" not in r for r in items.values()))

    def test_a_process_with_no_known_folder_or_ps_that_fails_is_not_guessed(self):
        p = self.repo()
        out = self.run_sweep(p, [(201, 5, "python3 -m http.server")], {})
        self.assertEqual(out["processes"]["items"], [])
        real = sweep._ps
        sweep._ps = lambda: None
        try:
            self.assertEqual(sweep.collect(None, all_projects=True)["processes"]["status"], "unknown")
        finally:
            sweep._ps = real

    def test_the_sweeping_process_itself_is_never_listed(self):
        p = self.repo()
        out = self.run_sweep(p, [(os.getpid(), 5, "python3 -m unittest"), (os.getppid(), 5, "pytest")],
                             {os.getpid(): p, os.getppid(): p})
        self.assertEqual(out["processes"]["items"], [])

    def test_etime_parsing(self):
        self.assertEqual(sweep._etime_s("01:02"), 62)
        self.assertEqual(sweep._etime_s("1:02:03"), 3723)
        self.assertEqual(sweep._etime_s("2-00:00:01"), 172801)
        self.assertIsNone(sweep._etime_s("nonsense"))


class CliTest(SweepCase):
    def test_without_all_it_covers_the_current_project_only_and_always_exits_zero(self):
        p = self.repo()
        self.mk(os.path.join(self.proj("Dev"), "beta"), [("R-1", "open")])
        names = [x["name"] for x in self.data(project=p)["projects"]]
        self.assertEqual(names, ["alpha"])
        names = {x["name"] for x in self.data("--all")["projects"]}
        self.assertEqual(names, {"alpha", "beta"})

    def test_the_text_is_in_the_users_language_and_says_it_is_read_only(self):
        p = self.repo()
        text = self.sweep_cli("--all")
        self.assertIn("solo lectura", text)
        self.assertIn("Sesiones terminadas", text)
        self.assertIn("Procesos sueltos", text)


class ReadOnlyAndRobustTest(SweepCase):
    def test_a_cold_home_is_not_written_by_the_sweep(self):
        root = self.proj("Dev")
        self.mk(os.path.join(root, "alpha"), [("R-1", "open")])
        self.config(hub=os.path.join(root, "alpha"), root=root)
        self.assertFalse(os.path.exists(os.path.join(self.bhome, "portfolio.json")))
        before = sorted(os.listdir(self.bhome))
        self.sweep_cli("--all")
        self.assertFalse(os.path.exists(os.path.join(self.bhome, "portfolio.json")))
        self.assertEqual(sorted(os.listdir(self.bhome)), before)

    def test_an_unreadable_ledger_says_unknown_not_zero_stuck(self):
        p = self.repo()
        with open(sl._ledger_path(p), "w") as f:
            f.write("{broken")
        row = self.data(project=p)["projects"][0]
        self.assertEqual(row["claims_status"], "unknown")
        self.assertIn("desconocido", self.sweep_cli(project=p))

    def test_outside_a_project_it_still_answers_and_exits_zero(self):
        self.repo()
        nowhere = os.path.join(self.uhome, "nowhere")
        os.makedirs(nowhere)
        out = json.loads(self.sweep_cli("--json", cwd=nowhere))
        self.assertTrue(out["no_project"])
        self.assertEqual(out["projects"], [])
        self.assertIn("no es un proyecto", self.sweep_cli(cwd=nowhere))

    def test_one_budget_covers_the_whole_sweep(self):
        p = self.repo()
        self.wt(p, "done-lane")
        real = sweep.BUDGET_S
        sweep.BUDGET_S = -1
        try:
            out = sweep.collect(p)
        finally:
            sweep.BUDGET_S = real
        self.assertEqual(out["projects"][0]["worktrees_status"], "unknown")
        self.assertEqual(out["processes"]["status"], "unknown")
        self.assertIsNone(sweep._deadline)                       # never left set for the next call



class BudgetTest(SweepCase):
    def test_the_uncommitted_count_stops_with_the_sweep_s_time_budget(self):
        path = self.repo()
        with open(os.path.join(path, "x.txt"), "w") as f:
            f.write("x")
        self.assertEqual(sweep._dirty(path), 1)
        sweep._deadline = time.monotonic() - 1
        try:
            self.assertIsNone(sweep._dirty(path))                       # no git call once the budget is spent
        finally:
            sweep._deadline = None
