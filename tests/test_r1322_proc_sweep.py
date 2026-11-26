"""R-1322: `budgit housekeeping procs` -- stale local test servers left behind by agents are found by pattern, age and
ownership, and stopped with SIGTERM only. A fake process table everywhere: nothing here signals a real process, and no project
name is shipped (the patterns and the protected ports come from the user's config, here a dict)."""
import json
import os
import signal
import subprocess
import sys
import unittest
from unittest import mock

from helpers import BudgitTestCase, ROOT
import daily
import procsweep

NOW = 1_800_000_000.0
H = 3600
BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")


def row(pid, ppid, command, age_h=10, tty="??", owner="sess1"):
    """owner: the Budgit session marker read from the process environment (R-1322 round 1: a process without one never qualifies)."""
    return {"pid": pid, "ppid": ppid, "start": NOW - age_h * H, "tty": tty, "command": command, "owner": owner}


def probe_of(info):
    """A fake lsof: {pid: (cwd, [ports])}; a pid not listed is unknown (None)."""
    def probe(pids):
        return {p: {"cwd": info[p][0], "ports": info[p][1]} for p in pids if p in info}
    return probe


def plan(table, info=None, live=(), config=None, holds=(), now=NOW, me=999999):
    info = info if info is not None else {r["pid"]: ("/work/demo", [8123]) for r in table}
    return procsweep.plan(table, now, probe_of(info), list(live) if live is not None else None, config or {}, holds=holds, me=me)


def pids(result):
    return sorted(c["pid"] for c in result["stop"])


DEMO = "python3 /plugin/bin/budgit serve --demo --port 8123"
HTTP = "/usr/bin/python3 -m http.server 8000"
SHELL = {"pid": 10, "ppid": 1, "start": NOW - 20 * H, "tty": "??", "command": "/bin/zsh -c eval 'x'"}


class Shapes(unittest.TestCase):
    """Today's shapes."""

    def test_orphan_demo_servers_are_candidates(self):
        r = plan([row(100, 1, DEMO, 140), row(101, 1, HTTP, 5)])
        self.assertEqual(pids(r), [100, 101])
        c = {x["pid"]: x for x in r["stop"]}[100]
        for key in ("pid", "ppid", "age_s", "ports", "cwd", "why", "command"):
            self.assertIn(key, c)
        self.assertEqual(c["ports"], [8123])
        self.assertEqual(c["cwd"], "/work/demo")
        self.assertIn("orphan", c["why"])

    def test_demo_server_under_live_parent_with_idle_cwd_is_candidate(self):
        table = [row(50, 1, "claude", 30), row(60, 50, "/bin/zsh -c run", 9), row(100, 60, DEMO, 9)]
        r = plan(table, info={100: ("/work/idle", [8123])}, live=["/work/other-session"])
        self.assertEqual(pids(r), [100])
        self.assertIn("idle cwd", r["stop"][0]["why"])

    def test_demo_server_under_live_parent_in_a_live_cwd_is_kept(self):
        table = [row(50, 1, "claude", 30), row(100, 50, DEMO, 9)]
        r = plan(table, info={100: ("/work/session/demo", [8123])}, live=["/work/session"])
        self.assertEqual(pids(r), [])

    def test_real_non_demo_server_is_kept(self):
        """INVARIANT: never a process with no test-server pattern match, however old and orphaned."""
        table = [row(100, 1, "node /srv/app/server.js --port 5000", 150), row(101, 1, "/usr/bin/python3 app.py", 150),
                 row(102, 1, "gunicorn app:app", 150)]
        r = plan(table)
        self.assertEqual(pids(r), [])

    def test_protected_port_is_kept(self):
        """INVARIANT: never a port of the protect list from config (her real app), from lsof or from the command line."""
        config = {"housekeeping": {"proc_protect_ports": [8123]}}
        self.assertEqual(pids(plan([row(100, 1, DEMO)], config=config)), [])          # lsof says 8123
        info = {100: ("/work/demo", [])}
        self.assertEqual(pids(plan([row(100, 1, DEMO)], info=info, config=config)), [])   # only the command says 8123
        other = {"housekeeping": {"proc_protect_ports": [9999]}}
        self.assertEqual(pids(plan([row(100, 1, DEMO)], config=other)), [100])

    def test_protect_list_with_an_unknown_port_fails_closed(self):
        config = {"housekeeping": {"proc_protect_ports": [8123]}}
        r = plan([row(100, 1, "python3 -m http.server")], info={}, config=config)    # no lsof answer, no port in the command
        self.assertEqual(pids(r), [])


class Invariants(unittest.TestCase):
    def test_interactive_terminal_shell_ancestor_is_kept(self):
        """INVARIANT: never a process whose ancestor is a shell she typed in (a terminal shell with a tty)."""
        table = [row(20, 1, "-zsh", 50, tty="ttys003"), row(100, 20, DEMO, 9)]
        self.assertEqual(pids(plan(table, live=["/elsewhere"])), [])
        deep = [row(20, 1, "/bin/bash", 50, tty="ttys001"), row(30, 20, "make run", 9, tty="ttys001"), row(100, 30, DEMO, 9)]
        self.assertEqual(pids(plan(deep, live=["/elsewhere"])), [])

    def test_agent_shell_without_a_tty_is_not_a_terminal(self):
        table = [row(20, 1, "/bin/zsh -c run", 50, tty="??"), row(100, 20, DEMO, 9)]
        self.assertEqual(pids(plan(table, live=["/elsewhere"])), [100])

    def test_claude_and_its_helpers_are_never_candidates(self):
        """INVARIANT: never the Claude app helpers or claude processes, even when their arguments quote a demo command."""
        table = [row(100, 1, "claude -p run serve --demo", 90),
                 row(101, 1, "/Applications/Claude.app/Contents/MacOS/Claude --serve --demo", 90),
                 row(102, 1, "/Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app/x -m http.server", 90),
                 row(103, 1, "/opt/tools/bin/claude --resume -m http.server", 90)]
        self.assertEqual(pids(plan(table)), [])

    def test_a_shell_wrapper_quoting_the_command_is_never_a_candidate(self):
        table = [row(100, 1, "/bin/zsh -c source snap && eval 'budgit serve --demo'", 90), row(101, 1, "grep serve --demo log", 90)]
        self.assertEqual(pids(plan(table)), [])

    def test_young_server_is_kept_and_the_age_is_configurable(self):
        young = [row(100, 1, DEMO, age_h=3)]
        self.assertEqual(pids(plan(young)), [])
        self.assertEqual(pids(plan(young, config={"housekeeping": {"proc_min_age_h": 2}})), [100])
        self.assertEqual(pids(plan([row(100, 1, DEMO, age_h=0.1)], config={"housekeeping": {"proc_min_age_h": 0.01}})), [])   # floor

    def test_patterns_from_config_extend_the_built_in_ones(self):
        table = [row(100, 1, "node preview-server.js", 9)]
        self.assertEqual(pids(plan(table)), [])
        config = {"housekeeping": {"proc_patterns": ["preview-server.js"]}}
        self.assertEqual(pids(plan(table, config=config)), [100])
        self.assertEqual(pids(plan([row(101, 1, DEMO, 9)], config=config)), [101])           # the built-in ones stay

    def test_no_project_name_is_shipped(self):
        self.assertEqual(procsweep.BUILTIN_PATTERNS, ("serve --demo", "-m http.server"))

    def test_the_caller_and_its_ancestors_are_never_candidates(self):
        table = [row(100, 1, DEMO, 9), row(200, 100, DEMO, 9)]
        self.assertEqual(pids(plan(table, me=200)), [])

    def test_a_hold_of_the_user_keeps_the_process(self):
        hold = {"kind": "pid", "value": 100, "start": NOW - 10 * H}
        self.assertEqual(pids(plan([row(100, 1, DEMO)], holds=[hold])), [])

    def test_unreadable_live_sessions_stop_nothing(self):
        """Round 1 (M3): the sessions folder unreadable means stop nothing, orphans included."""
        table = [row(50, 1, "claude", 30), row(100, 50, DEMO, 9), row(101, 1, DEMO, 9)]
        self.assertEqual(pids(plan(table, live=None)), [])

    def test_unknown_cwd_is_not_an_idle_cwd(self):
        table = [row(50, 1, "claude", 30), row(100, 50, DEMO, 9)]
        self.assertEqual(pids(plan(table, info={}, live=["/elsewhere"])), [])


class Stopping(unittest.TestCase):
    def run_sweep(self, stop, table, kill, fresh=None):
        tables = [table, fresh or table, fresh or table]
        reader = mock.Mock(side_effect=lambda: [dict(r) for r in tables.pop(0)] if tables else [dict(r) for r in table])
        return procsweep.run(stop, reader=reader, probe=probe_of({r["pid"]: ("/w", [8123]) for r in table}), live=[], config={},
                             now=NOW, kill=kill, sleep=lambda s: None, holds=[], me=999999)

    def test_dry_run_is_the_default_and_signals_nothing(self):
        kill = mock.Mock()
        rep = self.run_sweep(False, [row(100, 1, DEMO)], kill)
        kill.assert_not_called()
        self.assertEqual([c["pid"] for c in rep["stop"]], [100])
        self.assertFalse(rep["stopped"])
        import inspect
        self.assertIs(inspect.signature(procsweep.run).parameters["stop"].default, False)

    def test_stop_sends_sigterm_and_never_sigkill(self):
        calls = []
        rep = self.run_sweep(True, [row(100, 1, DEMO), row(101, 1, HTTP)], lambda pid, sig: calls.append((pid, sig)))
        self.assertEqual(sorted(calls), [(100, signal.SIGTERM), (101, signal.SIGTERM)])
        self.assertNotIn(signal.SIGKILL, [s for _, s in calls])
        self.assertEqual(sorted(rep["stopped"]), [100, 101])

    def test_a_survivor_is_reported_never_killed(self):
        calls = []
        rep = self.run_sweep(True, [row(100, 1, DEMO)], lambda pid, sig: calls.append((pid, sig)))
        self.assertEqual(calls, [(100, signal.SIGTERM)])
        self.assertEqual(rep["still_running"], [100])          # the fake table still lists it after the wait

    def test_a_reused_pid_is_not_signalled(self):
        calls = []
        fresh = [dict(row(100, 1, DEMO), start=NOW - 60)]        # same pid, started a minute ago: another process
        rep = self.run_sweep(True, [row(100, 1, DEMO)], lambda pid, sig: calls.append((pid, sig)), fresh=fresh)
        self.assertEqual(calls, [])
        self.assertEqual(rep["stopped"], [])

    def test_a_process_that_became_protected_between_reads_is_not_signalled(self):
        calls = []
        fresh = [row(100, 1, DEMO, tty="ttys004"), row(20, 1, "-zsh", 50, tty="ttys004")]
        fresh[0]["ppid"] = 20
        self.run_sweep(True, [row(100, 1, DEMO)], lambda pid, sig: calls.append((pid, sig)), fresh=fresh)
        self.assertEqual(calls, [])


class Config(BudgitTestCase):
    def test_settings_are_read_from_the_housekeeping_section(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"housekeeping": {"proc_patterns": ["x-srv"], "proc_protect_ports": [4321, "bad", True], "proc_min_age_h": 6}}, f)
        s = procsweep.settings()
        self.assertEqual(s["patterns"], procsweep.BUILTIN_PATTERNS + ("x-srv",))
        self.assertEqual(s["protect_ports"], {4321})
        self.assertEqual(s["min_age_s"], 6 * H)

    def test_a_bad_config_falls_back_to_the_defaults(self):
        s = procsweep.settings({"housekeeping": {"proc_min_age_h": "soon", "proc_patterns": "no", "proc_protect_ports": 5}})
        self.assertEqual((s["patterns"], s["protect_ports"], s["min_age_s"]), (procsweep.BUILTIN_PATTERNS, set(), 4 * H))


class Wiring(BudgitTestCase):
    def test_housekeeping_apply_stops_and_a_dry_run_does_not(self):
        rep = {"stop": [{"pid": 1, "ppid": 1, "age_s": 99999, "ports": [], "cwd": None, "why": ["orphan"], "command": "x"}],
               "kept": [], "stopped": [1], "still_running": [], "applied": True}
        with mock.patch.object(procsweep, "run", return_value=rep) as run:
            procsweep.housekeeping_text(True)
            procsweep.housekeeping_text(False)
        self.assertEqual([c.args[0] for c in run.call_args_list], [True, False])

    def test_the_cli_command_is_a_dry_run_by_default(self):
        env = dict(os.environ, STUDIO_HOME=self.home, BUDGIT_HOME=self.home)
        out = subprocess.run([sys.executable, BUDGIT, "housekeeping", "procs", "--json"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        rep = json.loads(out.stdout)
        self.assertFalse(rep["applied"])
        self.assertEqual(rep["stopped"], [])

    def test_the_housekeeping_help_mentions_procs(self):
        out = subprocess.run([sys.executable, BUDGIT, "housekeeping", "--help"], capture_output=True, text=True, timeout=120)
        self.assertIn("procs", out.stdout)

    def test_the_daily_preview_shows_the_count_next_to_the_worktree_line(self):
        out = {"projects": [{"id": "p", "protected": False, "counts": {"made": 1, "shut": 1, "pending": 0, "next": 0, "later": 0},
                             "rule": False, "batches": [], "duplicates": [], "order": [], "clean": {"remove": 2, "delete": 1}}],
               "no_project": False, "_names": frozenset(), "procs": 3}
        text = daily.render(out, "en")
        lines = text.splitlines()
        i = next(n for n, l in enumerate(lines) if "housekeeping would remove 2 worktree" in l)
        self.assertIn("3", lines[i + 1])
        self.assertIn("test server", lines[i + 1])
        out["procs"] = None
        self.assertIn("not checked", daily.render(out, "en").splitlines()[i + 1])

    def test_the_daily_counts_with_the_same_plan_as_housekeeping(self):
        with mock.patch.object(procsweep, "run", return_value={"stop": [{"pid": 1}, {"pid": 2}], "kept": []}) as run:
            self.assertEqual(procsweep.count(), 2)
        self.assertIs(run.call_args.args[0], False)


if __name__ == "__main__":
    unittest.main()
