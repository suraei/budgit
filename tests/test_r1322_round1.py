"""R-1322 fix round 1 (security HOLD 0/3/4): M1-M3, L1-L4. A fake process table, a fake ps/lsof and a fake kill everywhere:
nothing here signals a real process or reads a real one."""
import contextlib
import io
import json
import os
import signal
import subprocess
import time
import unittest
from unittest import mock

from helpers import BudgitTestCase
import housekeeping
import leak
import procsweep

NOW = 1_800_000_000.0
H = 3600
DEMO = "python3 /plugin/bin/budgit serve --demo --port 8123"


def row(pid, ppid, command, age_h=10, tty="??", owner="sess1", uid=None):
    r = {"pid": pid, "ppid": ppid, "start": NOW - age_h * H, "tty": tty, "command": command, "owner": owner}
    if uid is not None:
        r["uid"] = uid
    return r


def probe_of(info):
    def probe(pids):
        return {p: {"cwd": info[p][0], "ports": info[p][1]} for p in pids if p in info}
    return probe


def plan(table, cwd="/work/demo", ports=(8123,), live=(), config=None, roots=(), me=999999):
    info = {r["pid"]: (cwd, list(ports)) for r in table}
    return procsweep.plan(table, NOW, probe_of(info), None if live is None else list(live), config or {}, holds=(), me=me, roots=roots)


def pids(result):
    return sorted(c["pid"] for c in result["stop"])


def done(rc, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class M3Qualifying(BudgitTestCase):
    def test_m3_orphan_without_marker_or_worktree_cwd_is_kept(self):
        """ppid 1 alone never qualifies (a launchd service, an IDE helper)."""
        r = plan([row(100, 1, DEMO, owner=None)], cwd="/work/demo", roots=["/wt/one"])
        self.assertEqual(pids(r), [])
        self.assertIn("marker", r["kept"][0]["reason"])

    def test_m3_marker_in_the_environment_qualifies(self):
        self.assertEqual(pids(plan([row(100, 1, DEMO, owner="sess1")], roots=[])), [100])

    def test_m3_cwd_inside_a_worktree_root_with_the_pattern_qualifies_without_marker(self):
        r = plan([row(100, 1, DEMO, owner=None)], cwd="/wt/one/sub", roots=["/wt/one"])
        self.assertEqual(pids(r), [100])
        self.assertEqual(pids(plan([row(100, 1, DEMO, owner=None)], cwd="/wt/onex", roots=["/wt/one"])), [])    # not a prefix match
        self.assertEqual(pids(plan([row(100, 1, "node server.js", owner=None)], cwd="/wt/one", roots=["/wt/one"])), [])   # pattern first

    def test_m3_roots_unreadable_means_only_the_marker_qualifies(self):
        self.assertEqual(pids(plan([row(100, 1, DEMO, owner=None)], cwd="/wt/one", roots=None)), [])

    def test_m3_the_marker_is_read_from_the_environment_never_from_argv(self):
        text = ("%d 100 1 Mon Nov 22 10:00:00 2026 ?? python3 -m http.server 8000\n"
                "%d 101 1 Mon Nov 22 10:00:00 2026 ?? python3 -m http.server 8001 BUDGIT_OWNER=forged\n") % (os.getuid(), os.getuid())
        envs = {100: "A=1\0BUDGIT_OWNER=sess9\0", 101: "A=1\0"}
        rows = procsweep.read_table(run=lambda argv: text, environ=lambda pid: envs[pid])
        self.assertEqual({r["pid"]: r["owner"] for r in rows}, {100: "sess9", 101: None})

    def test_m3_sessions_folder_absent_or_unreadable_is_none_and_stops_nothing(self):
        for found in ({"status": "absent", "sessions": []}, {"status": "error"}):
            with mock.patch("livework.live_sessions", return_value=found):
                self.assertIsNone(procsweep.live_folders())
        self.assertEqual(pids(plan([row(100, 1, DEMO)], live=None)), [])
        kill = mock.Mock()
        with mock.patch("livework.live_sessions", return_value={"status": "absent", "sessions": []}):
            rep = procsweep.run(True, reader=lambda: [row(100, 1, DEMO)], probe=probe_of({100: ("/w", [8123])}), config={}, now=NOW, kill=kill,
                                sleep=lambda s: None, holds=[], me=999999, roots=[])
        kill.assert_not_called()
        self.assertEqual(rep["stopped"], [])

    def test_m3_worktree_roots_are_the_linked_worktrees_of_plain_projects_not_the_main_checkout(self):
        wts = [{"path": "/proj/main"}, {"path": "/wt/one"}, {"path": "/wt/two"}]
        with mock.patch.object(housekeeping, "projects", return_value=(["/proj/main"], 0)), mock.patch.object(housekeeping, "worktrees", return_value=wts):
            self.assertEqual(housekeeping.worktree_roots({}), ["/wt/one", "/wt/two"])
        with mock.patch.object(housekeeping, "projects", side_effect=OSError("x")):
            self.assertIsNone(housekeeping.worktree_roots({}))


class M1Lsof(unittest.TestCase):
    def lsof(self, proc):
        with mock.patch.object(procsweep.subprocess, "run", return_value=proc):
            return procsweep._lsof(["-p", "1"])

    def test_m1_exit_1_counts_as_no_listeners_only_with_empty_stdout_and_stderr(self):
        self.assertEqual(self.lsof(done(1)), "")
        self.assertEqual(self.lsof(done(0, "p1\nn*:80\n")), "p1\nn*:80\n")
        self.assertIsNone(self.lsof(done(1, "", "lsof: WARNING: can't stat() fuse file system")))
        self.assertIsNone(self.lsof(done(1, "p1\nn*:80\n", "")))
        self.assertIsNone(self.lsof(done(2)))

    def test_m1_a_failed_listener_lookup_gives_none_and_the_unknown_port_is_kept(self):
        answers = iter(["p100\nn/work/demo\n", None])
        config = {"housekeeping": {"proc_protect_ports": [9999]}}
        with mock.patch.object(procsweep, "_lsof", side_effect=lambda args: next(answers)):
            facts = procsweep.probe([100])
        self.assertIsNone(facts[100]["ports"])
        r = procsweep.plan([row(100, 1, "python3 -m http.server")], NOW, lambda pids: facts, [], config, holds=(), me=999999, roots=[])
        self.assertEqual(pids(r), [])
        answers = iter(["p100\nn/work/demo\n", ""])
        with mock.patch.object(procsweep, "_lsof", side_effect=lambda args: next(answers)):
            facts = procsweep.probe([100])
        self.assertEqual(facts[100]["ports"], [])


class M2Config(BudgitTestCase):
    def test_m2_any_invalid_protect_port_keeps_every_candidate_and_says_why(self):
        for bad in ("8000/tcp", "8000-8100", 0, 70000, True, "x", 1.5):
            config = {"housekeeping": {"proc_protect_ports": [9999, bad]}}
            r = plan([row(100, 1, DEMO)], ports=(8123,), config=config)
            self.assertEqual(pids(r), [], bad)
            self.assertIn("proc_protect_ports", r["blocked"])
        r = plan([row(100, 1, DEMO)], config={"housekeeping": {"proc_protect_ports": [9999]}})
        self.assertEqual(pids(r), [100])
        self.assertIsNone(r.get("blocked"))

    def test_m2_unreadable_config_keeps_every_candidate_signals_nothing_and_says_why(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            f.write('{"housekeeping": {"proc_protect_ports": [8123],}')            # a trailing comma: load_config would read the defaults
        kill = mock.Mock()
        rep = procsweep.run(True, reader=lambda: [row(100, 1, DEMO)], probe=probe_of({100: ("/w", [8123])}), live=[], now=NOW, kill=kill,
                            sleep=lambda s: None, holds=[], me=999999, roots=[])
        kill.assert_not_called()
        self.assertEqual(rep["stop"], [])
        self.assertEqual(rep["stopped"], [])
        self.assertIn("config", procsweep.render(rep, "en"))


class L1Hours(unittest.TestCase):
    def test_l1_proc_hours_refuses_nan_negative_inf_and_under_the_floor(self):
        for bad in (float("nan"), -1.0, float("inf"), 0.0, 0.01):
            with self.assertRaises(ValueError, msg=str(bad)):
                procsweep.hours_to_s(bad)
        self.assertEqual(procsweep.hours_to_s(4), 4 * H)

    def test_l1_main_with_a_bad_value_runs_nothing_and_exits_2(self):
        with mock.patch.object(procsweep, "run") as run, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(procsweep.main(hours=float("nan")), 2)
        run.assert_not_called()


class L2Apply(unittest.TestCase):
    REP = {"stop": [], "kept": [], "stopped": [], "still_running": [], "applied": False}

    def test_l2_housekeeping_apply_signals_only_when_the_procs_task_is_selected(self):
        self.assertTrue(procsweep.procs_selected(None))
        self.assertTrue(procsweep.procs_selected(["procs"]))
        self.assertTrue(procsweep.procs_selected(["/a/dir", "procs"]))
        self.assertFalse(procsweep.procs_selected(["worktrees"]))
        self.assertFalse(procsweep.procs_selected(["/a/dir"]))
        with mock.patch.object(procsweep, "run", return_value=self.REP) as run:
            procsweep.housekeeping_text(True, only=["worktrees"])
            run.assert_not_called()
            procsweep.housekeeping_text(True, only=["procs"])
            procsweep.housekeeping_text(True)
        self.assertEqual([c.args[0] for c in run.call_args_list], [True, True])

    def test_l2_procs_apply_really_applies_and_the_default_is_a_dry_run(self):
        with mock.patch.object(procsweep, "run", return_value=self.REP) as run, contextlib.redirect_stdout(io.StringIO()):
            procsweep.main(apply=True)
            procsweep.main(stop=True)
            procsweep.main()
        self.assertEqual([c.args[0] for c in run.call_args_list], [True, True, False])


class L3Hardening(BudgitTestCase):
    def test_l3_ps_and_lsof_are_called_by_absolute_path(self):
        calls = []
        with mock.patch.object(procsweep.subprocess, "run", side_effect=lambda argv, **kw: calls.append(argv) or done(0)):
            procsweep.read_table(environ=lambda pid: "")
            procsweep._lsof(["-p", "1"])
        self.assertEqual([c[0] for c in calls], ["/bin/ps", "/usr/sbin/lsof"])

    def test_l3_a_node_run_claude_is_claude(self):
        node = "node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js -p serve --demo"
        self.assertTrue(procsweep.is_claude(node))
        self.assertFalse(procsweep.is_claude("node /srv/app/cli.js serve --demo"))
        self.assertEqual(pids(plan([row(100, 1, node)])), [])

    def test_l3_a_parent_of_another_uid_is_not_an_orphan(self):
        other = os.getuid() + 1
        table = [row(50, 1, "/usr/bin/helper", 30, uid=other), row(100, 50, DEMO, 9)]
        self.assertEqual(pids(plan(table, cwd="/work/demo", live=["/work/demo"])), [])
        table = [row(60, 1, "-zsh", 50, tty="ttys003", uid=other), row(100, 60, DEMO, 9)]
        self.assertEqual(pids(plan(table, live=["/elsewhere"])), [])        # a terminal shell of another uid is still an ancestor

    def test_l3_pid_1_or_lower_is_never_signalled(self):
        self.assertEqual(pids(plan([row(1, 0, DEMO)])), [])
        kill = mock.Mock()
        fake = {"stop": [{"pid": p, "ppid": 1, "age_s": 99999, "ports": [], "cwd": None, "why": ["orphan"], "command": "x", "start": NOW}
                         for p in (0, 1, -5)], "kept": []}
        with mock.patch.object(procsweep, "plan", return_value=fake):
            rep = procsweep.run(True, reader=lambda: [], probe=probe_of({}), live=[], config={}, now=NOW, kill=kill, sleep=lambda s: None,
                                holds=[], me=999999, roots=[])
        kill.assert_not_called()
        self.assertEqual(rep["stopped"], [])


class L4Redaction(BudgitTestCase):
    SECRET = "python3 /opt/x/secretproj/budgit serve --demo --port 8123 --token sk-abc123"

    def test_l4_the_command_is_reduced_to_the_program_and_the_matched_pattern(self):
        r = plan([row(100, 1, self.SECRET), row(101, 1, "/bin/zsh -c eval 'serve --demo sk-zzz'")])
        self.assertEqual(r["stop"][0]["command"], "python3 serve --demo")
        for c in r["stop"] + r["kept"]:
            self.assertNotIn("sk-", c["command"])
            self.assertNotIn("secretproj", c["command"])

    def run_main(self, as_json):
        out = io.StringIO()
        old = dict(row(100, 1, self.SECRET), start=time.time() - 10 * H)           # main() plans at the real clock
        with mock.patch.object(procsweep, "read_table", return_value=[old]), \
                mock.patch.object(procsweep, "probe", probe_of({100: ("/work/secretproj/app", [8123])})), \
                mock.patch.object(procsweep, "live_folders", return_value=[]), \
                mock.patch.object(housekeeping, "worktree_roots", return_value=[]), \
                mock.patch.object(leak, "protected_names", return_value={"secretproj"}), \
                contextlib.redirect_stdout(out):
            procsweep.main(as_json=as_json)
        return out.getvalue()

    def test_l4_json_and_text_output_carry_no_command_line_and_no_protected_name(self):
        for as_json in (True, False):
            text = self.run_main(as_json)
            self.assertIn("100", text)
            self.assertNotIn("sk-abc123", text)
            self.assertNotIn("secretproj", text.lower())
        self.assertEqual(json.loads(self.run_main(True))["stop"][0]["command"], "python3 serve --demo")

    def test_l4_unreadable_protected_names_hide_the_folder_altogether(self):
        rep = {"stop": [{"pid": 100, "ppid": 1, "age_s": 99999, "ports": [], "cwd": "/work/anything", "why": ["orphan"], "command": "x"}],
               "kept": [], "stopped": [], "still_running": [], "applied": False}
        with mock.patch.object(leak, "protected_names", side_effect=ValueError("unreadable")):
            text = procsweep.render(rep, "en")
        self.assertNotIn("/work/anything", text)


if __name__ == "__main__":
    unittest.main()
