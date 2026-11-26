"""R-847: unused processes always close. Fake process tables replay the 9 Nov 2026 state; one test spawns a real sleep."""
import json, os, signal, subprocess, sys, time
from helpers import StudioTestCase
from loadcal import load_factor
import procclose as pc
import parentwatch

NOW = 1_800_000_000.0
ME, CLAUDE = 9000, 8000


def row(pid, ppid=1, pgid=None, age=10, owner=None, command="x"):
    return {"pid": pid, "ppid": ppid, "pgid": pid if pgid is None else pgid, "start": NOW - age, "command": command, "owner": owner}


def split_run(text):
    """A fake `ps`: from combined 'pid ppid pgid <5 date words> command' lines, the two calls the reader makes."""
    base_lines, cmd_lines = [], []
    for line in text.splitlines():
        p = line.split(None, 8)
        base_lines.append(" ".join(p[:8]))
        cmd_lines.append("%s %s" % (p[0], p[8] if len(p) > 8 else ""))
    return lambda argv: "\n".join(cmd_lines if any("command=" in a or "args=" in a for a in argv) else base_lines) + "\n"


class Machine:
    """A fake process table: kill and killpg remove rows, every call is recorded."""
    def __init__(self, rows, pid_reuse=None):
        self.rows, self.signals, self.reuse = rows, [], pid_reuse or {}

    def reader(self):
        out = [dict(r) for r in self.rows]
        for r in out:
            if r["pid"] in self.reuse:
                r["start"] = self.reuse[r["pid"]]
        return out

    stubborn = False                    # True: SIGTERM is ignored by every process

    def kill(self, pid, sig):
        self.signals.append(("pid", pid, sig))
        if sig == signal.SIGTERM and self.stubborn:
            return
        self.rows = [r for r in self.rows if r["pid"] != pid]

    def killpg(self, pgid, sig):
        self.signals.append(("pg", pgid, sig))
        if sig == signal.SIGTERM and self.stubborn:
            return
        self.rows = [r for r in self.rows if r["pgid"] != pgid]

    def pids(self):
        return {r["pid"] for r in self.rows}

    def kw(self):
        return dict(reader=self.reader, kill=self.kill, killpg=self.killpg, sleep=lambda s: None, wait_s=0.2, me=ME)


def base():
    return [row(CLAUDE, ppid=1, pgid=CLAUDE, age=50000, owner="S1", command="claude"),
            row(ME, ppid=CLAUDE, pgid=CLAUDE, age=1, owner="S1", command="python3 hook.py")]


class MarkerTest(StudioTestCase):
    def test_session_start_writes_the_marker_once_and_without_support_says_so(self):
        os.makedirs(self.home, exist_ok=True)
        env = os.path.join(self.home, "envfile")
        os.environ["CLAUDE_ENV_FILE"] = env
        try:
            self.assertTrue(pc.on_session_start({"session_id": "abc-1"}))
            self.assertTrue(pc.on_session_start({"session_id": "abc-1"}))
            self.assertEqual(open(env).read(), 'export BUDGIT_OWNER="abc-1"\n')
            del os.environ["CLAUDE_ENV_FILE"]
            self.assertFalse(pc.on_session_start({"session_id": "abc-2"}))
            self.assertIn("no CLAUDE_ENV_FILE support", open(os.path.join(self.home, "procclose.log")).read())
        finally:
            os.environ.pop("CLAUDE_ENV_FILE", None)

    def test_marker_line_keeps_a_plain_character_set(self):
        self.assertEqual(pc.marker_line('a"$(b)`\n'), 'export BUDGIT_OWNER="ab"\n')
        self.assertEqual(pc.marker_line(""), "")


class BlindTest(StudioTestCase):
    def test_a_reader_that_sees_no_environment_logs_and_says_inert(self):
        self.assertTrue(pc.check_blind([dict(row(1), env_seen=False), dict(row(2), env_seen=False)]))
        with open(os.path.join(self.home, "procclose.log")) as f:
            self.assertIn("procclose: env unreadable, closers inert", f.read())
        self.assertFalse(pc.check_blind([dict(row(1), env_seen=False), dict(row(2), env_seen=True)]))
        self.assertFalse(pc.check_blind([row(1)]))

    def test_darwin_reader_with_no_environment_anywhere_is_blind(self):
        text = "  5     1     5 Mon Nov  9 10:05:00 2026 /sbin/launchd\n"
        self.assertTrue(pc.check_blind(pc.read_darwin(run=split_run(text), environ=lambda pid: "")))

    def test_darwin_reader_with_an_environment_is_not_blind(self):
        text = "  5     1     5 Mon Nov  9 10:05:00 2026 /usr/local/bin/python3 x.py\n"
        rows = pc.read_darwin(run=split_run(text), environ=lambda pid: "PATH=/usr/bin\0BUDGIT_OWNER=s1\0")
        self.assertFalse(pc.check_blind(rows))
        self.assertEqual(rows[0]["owner"], "s1")


class ShellEnvFileTest(StudioTestCase):
    def test_a_process_started_from_a_shell_that_sourced_the_env_file_carries_the_marker(self):
        os.makedirs(self.home, exist_ok=True)
        envfile = os.path.join(self.home, "envfile")
        pc.write_marker(envfile, "sourced-shell-test")
        child = subprocess.Popen(["/bin/sh", "-c", '. "$1"; exec "$2" -c "import time; time.sleep(30)"', "sh", envfile, sys.executable],
                                 start_new_session=True)
        try:
            for _ in range(40):
                rows = [r for r in pc.read_table() if r["pid"] == child.pid and r["owner"]]
                if rows:
                    break
                time.sleep(0.1)
            self.assertTrue(rows, "no marker seen on a process started by a shell that sourced the env file")
            self.assertEqual(rows[0]["owner"], "sourced-shell-test")
        finally:
            child.terminate()
            child.wait()


class ReaderTest(StudioTestCase):
    def test_darwin_owner_comes_from_the_environment_never_from_argv_text(self):
        text = ("  501     1   501 Mon Nov  9 10:05:00 2026 python3 -m http.server 8000\n"
                "  502   501   501 Mon Nov  9 10:06:00 2026 /bin/zsh -c export BUDGIT_OWNER=S1 sleep 5\n")
        envs = {501: "TERM=x\0BUDGIT_OWNER=S1\0HOME=/h\0", 502: ""}
        rows = pc.read_darwin(run=split_run(text), environ=lambda pid: envs[pid])
        self.assertEqual([(r["pid"], r["ppid"], r["owner"]) for r in rows], [(501, 1, "S1"), (502, 501, None)])
        self.assertEqual(rows[0]["start"], time.mktime((2026, 11, 9, 10, 5, 0, 0, 0, -1)))

    def test_procargs2_buffer_skips_exec_path_and_argv(self):
        argc = (2).to_bytes(4, sys.byteorder)
        buf = argc + b"/bin/zsh\0\0\0zsh\0BUDGIT_OWNER=FAKE\0PATH=/bin\0BUDGIT_OWNER=REAL\0"
        self.assertEqual(pc.owner_from_env(pc.parse_procargs2(buf)), "REAL")
        self.assertEqual(pc.parse_procargs2(b""), "")

    def test_linux_reader_takes_the_owner_from_proc_environ(self):
        text = "  7     1     7 Mon Nov  9 10:05:00 2026 node server.js\n  8     7     7 Mon Nov  9 10:05:01 2026 sleep 9\n"
        rows = pc.read_linux(run=split_run(text), environ=lambda pid: "A=1\0BUDGIT_OWNER=S9\0B=2" if pid == 7 else "A=1", uid=1)
        self.assertEqual([r["owner"] for r in rows], ["S9", None])

    def test_windows_reads_no_table_so_nothing_is_ever_closed(self):
        self.assertEqual(pc.read_table("win32"), [])


class SessionEndTest(StudioTestCase):
    def test_session_end_closes_three_marked_processes_including_a_grandchild_in_a_group(self):
        m = Machine(base() + [row(100, ppid=CLAUDE, pgid=100, owner="S1", command="npm run dev"),
                              row(101, ppid=100, pgid=100, owner="S1", command="node vite"),
                              row(102, ppid=101, pgid=100, owner="S1", command="esbuild"),
                              row(200, ppid=1, owner="S2", command="other session server"),
                              row(300, ppid=1, owner=None, command="unmarked server")])
        closed = pc.on_session_end({"session_id": "S1", "reason": "other"}, **m.kw())
        self.assertEqual({r["pid"] for r in closed}, {100, 101, 102})
        self.assertEqual(m.pids(), {CLAUDE, ME, 200, 300})            # claude, the hook, another session and an unmarked one survive
        self.assertEqual(m.signals[0][:2], ("pg", 100))               # the group first

    def test_a_group_with_an_unrelated_unmarked_member_is_signalled_pid_by_pid(self):
        m = Machine(base() + [row(100, ppid=CLAUDE, pgid=100, owner="S1"), row(101, ppid=CLAUDE, pgid=100, owner=None, command="neighbour")])
        pc.on_session_end({"session_id": "S1"}, **m.kw())
        self.assertIn(101, m.pids())
        self.assertNotIn(100, m.pids())
        self.assertTrue(all(s[0] == "pid" for s in m.signals))

    def test_the_unmarked_subtree_of_a_marked_process_goes_with_it_deepest_first(self):
        m = Machine(base() + [row(100, ppid=CLAUDE, pgid=100, owner="S1", command="python server"),
                              row(110, ppid=100, pgid=110, owner=None, command="/bin/sh worker"),
                              row(111, ppid=110, pgid=111, owner=None, command="/bin/sleep 30")])
        pc.on_session_end({"session_id": "S1"}, **m.kw())
        self.assertEqual(m.pids(), {CLAUDE, ME})
        firsts = [s[1] for s in m.signals if s[2] == signal.SIGTERM]
        self.assertEqual(firsts, [111, 110, 100])

    def test_a_marked_orphans_fully_marked_subtree_is_swept_with_it(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python server"),
                              row(410, ppid=400, owner="S0", age=86400, command="python worker")])
        pc.sweep_orphans(True, now=NOW, min_age_s=3600, **m.kw())
        self.assertEqual(m.pids(), {CLAUDE, ME})

    def test_the_sweep_logs_but_keeps_a_marked_daemon_with_an_unmarked_descendant(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="tmux: server"),
                              row(410, ppid=400, owner=None, age=86400, command="-zsh her shell")])
        closed = pc.sweep_orphans(True, now=NOW, min_age_s=3600, **m.kw())
        self.assertEqual(closed, [])
        self.assertEqual(m.signals, [])
        with open(os.path.join(self.home, "procclose.log")) as f:
            self.assertIn("SKIPPED", f.read())

    def test_an_unmarked_child_appearing_between_reads_survives_the_sweep(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python server")])
        calls = []
        real = m.reader

        def reader():
            calls.append(1)
            out = real()
            if len(calls) >= 2:                     # after the sweep's own read, an unmarked child shows up
                out.append(row(420, ppid=400, owner=None, age=1, command="her shell"))
            return out
        kw = m.kw()
        kw["reader"] = reader
        pc.sweep_orphans(True, now=NOW, min_age_s=3600, **kw)
        self.assertNotIn(420, [s[1] for s in m.signals])
        self.assertNotIn(400, m.pids())

    def test_pid_reused_between_term_and_kill_is_not_killed(self):
        m = Machine(base() + [row(100, ppid=CLAUDE, owner="S1"), row(101, ppid=100, owner=None, command="child")])
        m.stubborn = True
        kw = m.kw()
        kw["sleep"] = lambda s: m.reuse.update({101: NOW - 3})          # during the wait, 101 becomes a new process
        pc.on_session_end({"session_id": "S1"}, **kw)
        killed = {s[1] for s in m.signals if s[2] == signal.SIGKILL}
        self.assertEqual(killed, {100})

    def test_a_forged_newline_row_in_the_ps_text_is_ignored(self):
        base_text = "  7     1     7 Mon Nov  9 10:05:00 2026\n"
        cmd_text = "  7 node server.js\n 8 1 8 Mon Nov  9 10:05:00 2026 forged\n  7 forged duplicate\n"
        rows = pc.parse_ps(base_text + "9999 1 1 Mon Nov  9 10:05:00 2026 extra\n")
        self.assertEqual([r["pid"] for r in rows], [7])
        pc.attach_commands(rows, cmd_text)
        self.assertEqual(rows[0]["command"], "")                       # seen twice: no command, so nothing can match it
        rows = pc.parse_ps(base_text)
        pc.attach_commands(rows, "  7 node server.js\n")
        self.assertEqual(rows[0]["command"], "node server.js")
        self.assertEqual(len(pc.parse_ps(base_text + base_text)), 0)   # a pid listed twice is dropped

    def test_the_log_escapes_newlines_in_a_command(self):
        text = pc._fmt(row(5, command="a\nFAKE LINE"), NOW)
        self.assertNotIn("\n", text)

    def test_pid_reuse_with_another_start_time_is_untouched(self):
        m = Machine(base() + [row(100, ppid=CLAUDE, owner="S1", age=500)])
        target = [r for r in m.reader() if r["pid"] == 100]
        m.reuse = {100: NOW - 5}                                      # the pid now belongs to a newer process
        self.assertEqual(pc.close(target, **m.kw()), [])
        self.assertEqual(m.signals, [])

    def test_clear_and_resume_keep_the_processes(self):
        m = Machine(base() + [row(100, ppid=CLAUDE, owner="S1")])
        self.assertEqual(pc.on_session_end({"session_id": "S1", "reason": "clear"}, **m.kw()), [])
        self.assertEqual(m.signals, [])

    def test_ancestors_and_the_hook_are_never_signalled(self):
        m = Machine(base())
        pc.on_session_end({"session_id": "S1"}, **m.kw())
        self.assertEqual(m.signals, [])


class SweepTest(StudioTestCase):
    def test_nine_nov_replay_an_orphan_of_3_8_days_closes_logged_and_an_unmarked_one_stays(self):
        age = int(3.8 * 86400)
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=age, command="python3 -m http.server 9000"),
                              row(401, ppid=1, owner=None, age=age, command="unmarked orphan"),
                              row(402, ppid=1, owner="S0", age=60, command="young orphan"),
                              row(403, ppid=CLAUDE, owner="S1", age=age, command="has a live parent")])
        closed = pc.sweep_orphans(True, now=NOW, min_age_s=3 * 3600, **m.kw())
        self.assertEqual([r["pid"] for r in closed], [400])
        self.assertEqual(m.pids(), {CLAUDE, ME, 401, 402, 403})
        text = open(os.path.join(self.home, "procclose.log")).read()
        self.assertIn("pid=400", text)
        self.assertIn("owner=S0", text)
        self.assertIn("http.server", text)
        self.assertIn("unused processes are always closed", text)

    def test_the_default_threshold_is_three_hours(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=3 * 3600 - 60, command="young"),
                              row(401, ppid=1, owner="S0", age=3 * 3600 + 60, command="old")])
        closed = pc.sweep_orphans(True, now=NOW, **m.kw())
        self.assertEqual([r["pid"] for r in closed], [401])
        self.assertIn(400, m.pids())

    def test_dry_run_lists_and_signals_nothing(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400)])
        found = pc.sweep_orphans(False, now=NOW, min_age_s=3600, **m.kw())
        self.assertEqual([r["pid"] for r in found], [400])
        self.assertEqual(m.signals, [])
        self.assertIn("dry-run would close", open(os.path.join(self.home, "procclose.log")).read())


BUDGIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")


class ProcsCliTest(StudioTestCase):
    """R-1095: `budgit procs` runs the sweep; the table and the kill are fakes, no real process is signalled."""
    def cli_module(self):
        import importlib.machinery, importlib.util
        loader = importlib.machinery.SourceFileLoader("budgit_cli_procs", BUDGIT)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli_procs", loader))
        loader.exec_module(mod)
        return mod

    def run_procs(self, m, **ns):
        import contextlib, io, types
        from unittest import mock
        real = pc.sweep_orphans
        args = types.SimpleNamespace(**dict({"apply": False, "min_age_s": None}, **ns))
        out = io.StringIO()
        with mock.patch.object(pc, "sweep_orphans", lambda apply, min_age_s=None, held_out=None, **kw: real(apply, now=NOW, min_age_s=min_age_s, held_out=held_out, **kw, **m.kw())), \
                contextlib.redirect_stdout(out):
            code = self.cli_module().cmd_procs(args)
        return code, out.getvalue()

    def test_procs_dry_run_prints_would_close_and_closes_nothing(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python3 -m http.server")])
        code, out = self.run_procs(m, min_age_s=3600)
        self.assertEqual(code, 0)
        self.assertIn("would close", out)
        self.assertIn("400", out)
        self.assertEqual(m.signals, [])

    def test_procs_apply_closes_only_marked_orphans(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400), row(401, ppid=1, owner=None, age=86400),
                              row(402, ppid=CLAUDE, owner="S1", age=86400)])
        code, out = self.run_procs(m, apply=True, min_age_s=3600)
        self.assertEqual(code, 0)
        self.assertIn("closed", out)
        self.assertEqual(m.pids(), {CLAUDE, ME, 401, 402})

    def test_housekeeping_process_lines_start_on_their_own_line(self):
        import contextlib, io, types
        from unittest import mock
        import housekeeping
        report = {"projects": [], "left_alone": [], "busy_reason": None, "requested_apply": False,
                  "totals": {"removed": 0, "deleted": 0, "failed": 0}}
        args = types.SimpleNamespace(schedule=None, last=False, only=None, apply=False, hours=None, json=False, quiet=False, at=None)
        procs = mock.Mock(sweep_orphans=lambda apply, **kw: [{"pid": 400, "owner": "S0"}])
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"STUDIO_HOME": self.home}), mock.patch.object(housekeeping, "run", return_value=report), \
                mock.patch.object(housekeeping, "render", return_value="(add --apply)"), \
                mock.patch.dict(sys.modules, {"procclose": procs}), contextlib.redirect_stdout(out):
            self.cli_module().cmd_housekeeping(args)
        self.assertIn("(add --apply)\nwould close orphaned process pid 400 (S0)", out.getvalue())


class HoldTest(StudioTestCase):
    """R-1095: her later, specific answer keeps a process; fake tables only, nothing real is signalled."""
    def until(self, days):
        import datetime
        return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

    def hold(self, kind, value, m=None, days=5):
        ok, res = pc.add_hold(kind, value, "Lo paro yo despues", "I-1", self.until(days), reader=m.reader if m else None)
        self.assertTrue(ok, res)
        return res

    def test_a_held_port_survives_apply(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python3 -m http.server --port 8123"),
                              row(401, ppid=1, owner="S0", age=86400, command="python3 -m http.server -p 9000"),
                              row(402, ppid=1, owner="S0", age=86400, command="python3 -m http.server --port=18123")])
        self.hold("port", "8123", m)
        held = []
        closed = pc.sweep_orphans(True, now=NOW, min_age_s=3600, held_out=held, **m.kw())
        self.assertEqual({r["pid"] for r in closed}, {401, 402})
        self.assertIn(400, m.pids())
        self.assertEqual([r["pid"] for r, h in held], [400])
        self.assertIn("HELD by the user (I-1", open(os.path.join(self.home, "procclose.log")).read())

    def test_a_held_pid_survives_but_a_reused_pid_is_not_matched(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python3 server")])
        self.hold("pid", 400, m)
        pc.sweep_orphans(True, now=NOW, min_age_s=3600, **m.kw())
        self.assertIn(400, m.pids())
        self.assertEqual(m.signals, [])
        m.reuse = {400: NOW - 80000}                     # the pid now belongs to a different process
        pc.sweep_orphans(True, now=NOW, min_age_s=3600, **m.kw())
        self.assertNotIn(400, m.pids())

    def test_a_descendant_of_a_held_process_survives(self):
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="npm run dev --port 8123"),
                              row(410, ppid=400, owner="S0", age=86400, command="node vite"),
                              row(500, ppid=1, owner="S0", age=86400, command="other server")])
        self.hold("port", "8123", m)
        pc.sweep_orphans(True, now=NOW, min_age_s=3600, **m.kw())
        self.assertEqual(m.pids(), {CLAUDE, ME, 400, 410})
        target = [r for r in m.reader() if r["pid"] == 410]
        self.assertEqual(pc.close(target, **m.kw()), [])   # the child alone is covered too
        self.assertEqual(m.pids(), {CLAUDE, ME, 400, 410})

    def test_an_expired_hold_is_ignored_and_reported_once(self):
        import contextlib, io
        from unittest import mock
        os.makedirs(self.home, exist_ok=True)
        with open(pc.holds_path(), "w") as f:
            json.dump([{"kind": "port", "value": 8123, "words": "w", "src": "I-1", "added": self.until(-3), "expires": self.until(-1), "id": "b2" * 16}], f)
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python3 -m http.server --port 8123")])
        real = pc.sweep_orphans
        fake = lambda apply, min_age_s=None, held_out=None, **kw: real(apply, now=NOW, min_age_s=3600, held_out=held_out, **kw, **m.kw())
        outs = []
        for _ in range(2):
            buf = io.StringIO()
            with mock.patch.object(pc, "sweep_orphans", fake), contextlib.redirect_stdout(buf):
                pc.main([])
            outs.append(buf.getvalue())
        self.assertIn("hold expired", outs[0])
        self.assertNotIn("hold expired", outs[1])
        self.assertIn("would close", outs[0])
        pc.sweep_orphans(True, now=NOW, min_age_s=3600, **m.kw())
        self.assertNotIn(400, m.pids())

    def test_an_entry_without_words_or_src_or_over_14_days_is_rejected(self):
        self.assertFalse(pc.add_hold("port", "8123", "", "I-1", self.until(3))[0])
        self.assertFalse(pc.add_hold("port", "8123", "words", "", self.until(3))[0])
        self.assertFalse(pc.add_hold("port", "8123", "words", "note", self.until(3))[0])
        ok, why = pc.add_hold("port", "8123", "words", "I-1", self.until(15))
        self.assertFalse(ok)
        self.assertIn("14", why)
        self.assertFalse(pc.add_hold("pid", "999999", "words", "I-1", self.until(3), reader=lambda: [])[0])
        self.assertFalse(os.path.exists(pc.holds_path()))

    def test_a_symlinked_holds_file_is_refused(self):
        os.makedirs(self.home, exist_ok=True)
        target = os.path.join(self.home, "elsewhere.json")
        with open(target, "w") as f:
            f.write("[]")
        os.symlink(target, pc.holds_path())
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python3 -m http.server --port 8123")])
        with self.assertRaises(OSError):
            pc.add_hold("port", "8123", "words", "I-1", self.until(3), reader=m.reader)
        self.assertEqual(open(target).read(), "[]")


class FailClosedTest(StudioTestCase):
    """R-1095 round 1: an untrusted holds file closes nothing; writes are atomic; ids are stable; entries are validated on read."""
    def until(self, days):
        import datetime
        return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

    def entry(self, **over):
        e = {"kind": "port", "value": "8123", "words": "Lo paro yo despues", "src": "I-1", "added": self.until(0),
             "expires": self.until(3), "id": "a1" * 16}
        e.update(over)
        return e

    def write(self, text):
        os.makedirs(self.home, exist_ok=True)
        with open(pc.holds_path(), "w") as f:
            f.write(text)

    def log_text(self):
        return open(os.path.join(self.home, "procclose.log")).read()

    def machine(self):
        return Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python3 -m http.server --port 8123")])

    def test_a_bad_holds_file_closes_nothing_under_apply_and_dry_run(self):
        def symlink():
            target = os.path.join(self.home, "elsewhere.json")
            with open(target, "w") as f:
                f.write("[]")
            os.symlink(target, pc.holds_path())
        cases = {"truncated": lambda: self.write('[{"kind": "port", "val'), "object": lambda: self.write('{"a":1}'),
                 "empty": lambda: self.write(""), "symlink": symlink}
        for name, make in cases.items():
            for apply in (True, False):
                with self.subTest(case=name, apply=apply):
                    if os.path.lexists(pc.holds_path()):
                        os.unlink(pc.holds_path())
                    os.makedirs(self.home, exist_ok=True)
                    make()
                    m = self.machine()
                    found = pc.sweep_orphans(apply, now=NOW, min_age_s=3600, **m.kw())
                    self.assertEqual(found, [])
                    self.assertEqual(m.signals, [])
                    self.assertIn("holds file unreadable; nothing closed", self.log_text())

    def test_a_file_that_goes_bad_between_term_and_kill_stops_the_kill(self):
        m = self.machine()
        m.stubborn = True
        calls = []

        def sleep(s):
            if not calls:
                calls.append(1)
                self.write("{ not json")
        kw = dict(m.kw(), sleep=sleep)
        pc.close([r for r in m.reader() if r["pid"] == 400], only_marked=True, **kw)
        self.assertEqual(calls, [1])
        self.assertEqual([s for s in m.signals if s[2] == signal.SIGKILL], [])
        self.assertTrue([s for s in m.signals if s[2] == signal.SIGTERM])
        self.assertIn("holds file unreadable; nothing closed", self.log_text())

    def test_an_entry_missing_words_or_src_or_over_14_days_makes_the_file_invalid(self):
        bad = {"no words": self.entry(words=""), "no words key": {k: v for k, v in self.entry().items() if k != "words"},
               "no src": self.entry(src=""), "src not I-n": self.entry(src="note"),
               "over 14 days after added": self.entry(added=self.until(-1), expires=self.until(15)),
               "no added": {k: v for k, v in self.entry().items() if k != "added"}}
        for name, e in bad.items():
            with self.subTest(case=name):
                self.write(json.dumps([e]))
                with self.assertRaises(pc.HoldsError):
                    pc._read_holds()
                m = self.machine()
                self.assertEqual(pc.sweep_orphans(True, now=NOW, min_age_s=3600, **m.kw()), [])
                self.assertEqual(m.signals, [])
        self.write(json.dumps([self.entry()]))
        self.assertEqual(len(pc._read_holds()), 1)

    def test_40_parallel_add_hold_calls_store_40_entries(self):
        import threading
        m = self.machine()
        results = []
        threads = [threading.Thread(target=lambda: results.append(pc.add_hold("port", "8123", "w%d" % i, "I-1", self.until(3), reader=m.reader)))
                   for i in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 40)
        self.assertTrue(all(ok for ok, _ in results), results)
        entries = pc._read_holds()
        self.assertEqual(len(entries), 40)
        self.assertEqual(len({e["id"] for e in entries}), 40)
        self.assertEqual(oct(os.stat(pc.holds_path()).st_mode & 0o777), oct(0o600))

    def test_unhold_by_id_requires_words_and_src_and_logs_the_removal(self):
        import contextlib, io
        ok, e = pc.add_hold("port", "8123", "Lo paro yo despues", "I-1", self.until(3), reader=self.machine().reader)
        self.assertTrue(ok)
        self.assertTrue(e["id"])
        for argv in (["--unhold", e["id"]], ["--unhold", e["id"], "--words", "w"], ["--unhold", e["id"], "--src", "I-2"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pc.main(argv), 1)
            self.assertEqual(len(pc._read_holds()), 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(pc.main(["--unhold", e["id"], "--words", "ya no", "--src", "I-2"]), 0)
        self.assertEqual(pc._read_holds(), [])
        self.assertIn("hold removed id=%s" % e["id"], self.log_text())
        self.assertIn("ya no", self.log_text())

    def test_an_expired_hold_is_reported_once_under_apply_and_in_housekeeping(self):
        import contextlib, io, types
        from unittest import mock
        import housekeeping
        self.write(json.dumps([self.entry(added=self.until(-3), expires=self.until(-1))]))
        m = self.machine()
        real = pc.sweep_orphans
        fake = lambda apply, min_age_s=None, held_out=None, **kw: real(apply, now=NOW, min_age_s=3600, held_out=held_out, **kw, **m.kw())
        buf = io.StringIO()
        with mock.patch.object(pc, "sweep_orphans", fake), contextlib.redirect_stdout(buf):
            pc.main(["--apply"])
        self.assertEqual(buf.getvalue().count("hold expired"), 1)
        self.assertEqual(self.log_text().count("hold expired"), 1)
        # housekeeping: a fresh expired hold is printed once, then not again
        self.write(json.dumps([self.entry(added=self.until(-3), expires=self.until(-1))]))
        report = {"projects": [], "left_alone": [], "busy_reason": None, "requested_apply": False,
                  "totals": {"removed": 0, "deleted": 0, "failed": 0}}
        args = types.SimpleNamespace(schedule=None, last=False, only=None, apply=False, hours=None, json=False, quiet=False, at=None)
        import importlib.machinery, importlib.util
        loader = importlib.machinery.SourceFileLoader("budgit_cli_hk", BUDGIT)
        cli = importlib.util.module_from_spec(importlib.util.spec_from_loader("budgit_cli_hk", loader))
        loader.exec_module(cli)
        outs = []
        for _ in range(2):
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"STUDIO_HOME": self.home}), mock.patch.object(housekeeping, "run", return_value=report), \
                    mock.patch.object(housekeeping, "render", return_value="(add --apply)"), \
                    mock.patch.object(pc, "sweep_orphans", fake), contextlib.redirect_stdout(out):
                cli.cmd_housekeeping(args)
            outs.append(out.getvalue())
        self.assertEqual(outs[0].count("hold expired"), 1)
        self.assertNotIn("hold expired", outs[1])

    def test_min_age_of_nan_inf_negative_or_599_is_rejected(self):
        import contextlib, io
        for v in ("nan", "inf", "-1", "599", "abc"):
            with self.subTest(value=v):
                with self.assertRaises(ValueError):
                    pc.check_min_age(v)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(pc.main(["--min-age-s=" + v]), 2)
                self.assertIn("--min-age-s", err.getvalue())
        self.assertEqual(pc.check_min_age("600"), 600.0)

    def test_a_bad_config_min_age_falls_back_to_the_default(self):
        from unittest import mock
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=5000), row(401, ppid=1, owner="S0", age=pc.DEFAULT_MIN_AGE_S + 100)])
        for bad in (5, "nan", "inf", -1):
            with self.subTest(value=bad), mock.patch.object(pc.sl, "load_config", return_value={"orphan_min_age_s": bad}):
                found = pc.sweep_orphans(False, now=NOW, **m.kw())
                self.assertEqual([r["pid"] for r in found], [401])
        self.assertIn("config orphan_min_age_s ignored", self.log_text())

    def test_pid_1_an_ancestor_pid_and_a_port_no_argv_names_are_refused(self):
        m = self.machine()
        w, s, u = "words", "I-1", self.until(3)
        self.assertFalse(pc.add_hold("pid", 1, w, s, u, reader=m.reader, me=ME)[0])
        self.assertFalse(pc.add_hold("pid", 0, w, s, u, reader=m.reader, me=ME)[0])
        ok, why = pc.add_hold("pid", CLAUDE, w, s, u, reader=m.reader, me=ME)
        self.assertFalse(ok)
        self.assertIn("ancestor", why)
        ok, why = pc.add_hold("port", "7777", w, s, u, reader=m.reader)
        self.assertFalse(ok)
        self.assertIn("no running process names this port", why)
        self.assertFalse(os.path.exists(pc.holds_path()))
        self.assertTrue(pc.add_hold("pid", 400, w, s, u, reader=m.reader, me=ME)[0])

    def test_control_characters_in_words_are_escaped_in_the_printout_and_the_log(self):
        import contextlib, io
        words = "a\nFORGED log line\x1b[31m\x00z"
        ok, e = pc.add_hold("port", "8123", words, "I-1", self.until(3), reader=self.machine().reader)
        self.assertTrue(ok)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pc.main(["--holds"])
        for text in (out.getvalue(), self.log_text()):
            self.assertNotIn("\x1b", text)
            self.assertNotIn("\x00", text)
            self.assertNotIn("\nFORGED", text)
            self.assertIn("\\n", text)
        self.assertEqual(len(out.getvalue().splitlines()), 1)

    def test_every_entry_field_is_validated_on_read_round_2(self):
        pid = {"kind": "pid", "value": 400, "start": NOW - 5}
        bad = {"expires with a forged tail": self.entry(expires=self.until(3) + "\nFORGED"),
               "added with a tail": self.entry(added=self.until(0) + "x"),
               "compact date": self.entry(expires=self.until(3).replace("-", "")),
               "added in the future": self.entry(added=self.until(5), expires=self.until(8)),
               "over 14 days after added": self.entry(added=self.until(-1), expires=self.until(14)),
               "no id": {k: v for k, v in self.entry().items() if k != "id"},
               "short id": self.entry(id="abc123"), "upper id": self.entry(id="A1" * 16),
               "pid start missing": self.entry(**pid, **{}) | {"start": None},
               "pid start nan": self.entry(**dict(pid, start=float("nan"))),
               "pid start inf": self.entry(**dict(pid, start=float("inf"))),
               "port 0": self.entry(value="0"), "port reversed": self.entry(value="9000-8000"),
               "port too high": self.entry(value="70000"), "port 101 wide": self.entry(value="8000-8100"),
               "src with a newline": self.entry(src="I-1\n")}
        for name, e in bad.items():
            with self.subTest(case=name):
                self.write(json.dumps([e], allow_nan=True))
                with self.assertRaises(pc.HoldsError):
                    pc._read_holds()
        for e in (self.entry(value="8000-8099"), self.entry(**pid)):
            self.write(json.dumps([e]))
            self.assertEqual(len(pc._read_holds()), 1)
        ok, why = pc.add_hold("port", "8000-8100", "w", "I-1", self.until(3), reader=self.machine().reader)
        self.assertFalse(ok)

    def test_unhold_requires_an_i_n_src_round_2(self):
        import contextlib, io
        self.write(json.dumps([self.entry()]))
        for src in ("note", "I-", "I-1x", "", None):
            with self.subTest(src=src):
                with self.assertRaises(ValueError):
                    pc.remove_hold("a1" * 16, "w", src)
        self.assertEqual(len(pc._read_holds()), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pc.main(["--unhold", "a1" * 16, "--words", "w", "--src", "I-7"]), 0)

    def test_a_newline_in_any_stored_field_cannot_forge_a_line_in_holds_or_the_log_round_2(self):
        import contextlib, io
        evil = "x\nFORGED line"
        e = self.entry(words=evil, src=evil, value=evil, expires=evil, added=evil, id=evil, kind="pid")
        self.write(json.dumps([e]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pc.main(["--holds"])          # an invalid file is refused, and the refusal is escaped too
        # force the printing paths past validation: each formatter gets the raw entry
        from unittest import mock
        e = dict(e, expires=self.until(2))
        with mock.patch.object(pc, "_read_holds", return_value=[e]), contextlib.redirect_stdout(out):
            pc.main(["--holds"])
        m = Machine(base() + [row(400, ppid=1, owner="S0", age=86400, command="python3 -m http.server --port 8123")])
        e2 = dict(self.entry(), words=evil, src=evil, expires=evil, value="8123", kind="port")
        with mock.patch.object(pc, "active_holds", return_value=([e2], [])), mock.patch.object(pc, "report_expired", return_value=[]), \
                contextlib.redirect_stdout(out):
            pc.sweep_orphans(False, now=NOW, min_age_s=3600, holds=[e2], **m.kw())
            held = []
            pc.sweep_orphans(False, now=NOW, min_age_s=3600, holds=[e2], held_out=held, **m.kw())
        for text in (out.getvalue(), self.log_text()):
            self.assertNotIn("\nFORGED", text)
            self.assertTrue(all(not ln.startswith("FORGED") for ln in text.splitlines()))
        self.write(json.dumps([self.entry(expires=self.until(-1), added=self.until(-3))]))
        # expired report with a raw hostile entry
        lines = []
        with mock.patch.object(pc, "_read_holds", return_value=[dict(self.entry(), words=evil, src=evil, expires=evil)]), \
                mock.patch.object(pc, "_split", return_value=([], [dict(self.entry(), words=evil, src=evil, expires=evil)])), \
                mock.patch.object(pc, "_locked_update", side_effect=lambda ch: ch([dict(self.entry(), words=evil, src=evil, expires=evil)])):
            lines = pc.report_expired()
        self.assertTrue(lines and all("\n" not in ln for ln in lines))

    def test_hook_paths_report_an_unreadable_holds_file_round_2(self):
        self.write("{ not json")
        m = Machine(base() + [row(100, ppid=CLAUDE, pgid=100, owner="S1", command="npm run dev"),
                              row(500, ppid=CLAUDE, owner="S1", age=300, command="python3 -m http.server 8123")])
        self.assertEqual(pc.on_session_end({"session_id": "S1", "reason": "other"}, **m.kw()), [])
        self.assertEqual(m.signals, [])
        self.assertIn("holds file unreadable; nothing closed", self.log_text())
        path = os.path.join(self.home, "agent.jsonl")
        started = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - 600))
        cmd = "python3 -m http.server 8123"
        lines = [{"type": "user", "timestamp": started, "message": {"content": "go"}},
                 {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                                "input": {"command": cmd, "run_in_background": True}}]}}]
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\n")
        out = pc.on_subagent_stop({"session_id": "S1", "agent_id": "agx", "stop_hook_active": True, "agent_transcript_path": path}, **m.kw())
        self.assertIn("FAIL", out["systemMessage"])
        self.assertIn("holds file unreadable", out["systemMessage"])
        self.assertEqual(m.signals, [])


class HandBackTest(StudioTestCase):
    def transcript(self, command="python3 -m http.server 8123"):
        os.makedirs(self.home, exist_ok=True)
        path = os.path.join(self.home, "agent.jsonl")
        started = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - 600))
        lines = [{"type": "user", "timestamp": started, "message": {"content": "go"}},
                 {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                                "input": {"command": command, "run_in_background": True}}]}}]
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\n")
        return path

    def test_stopped_agent_with_a_live_server_is_blocked_once_then_closed_on_the_second_stop(self):
        m = Machine(base() + [row(500, ppid=CLAUDE, owner="S1", age=300, command="python3 -m http.server 8123"),
                              row(501, ppid=CLAUDE, owner="S1", age=300, command="unrelated sibling work"),
                              row(502, ppid=CLAUDE, owner="S1", age=9000, command="python3 -m http.server 8123")])  # older than the agent
        inp = {"session_id": "S1", "agent_id": "ag1", "agent_transcript_path": self.transcript()}
        first = pc.on_subagent_stop(inp, **m.kw())
        self.assertEqual(first["decision"], "block")
        self.assertIn("stop 500", first["reason"])
        self.assertEqual(m.signals, [])
        second = pc.on_subagent_stop(inp, **m.kw())
        self.assertIn("FAIL", second["systemMessage"])
        self.assertEqual(m.pids(), {CLAUDE, ME, 501, 502})
        self.assertIn("FAIL agent ag1", open(os.path.join(self.home, "procclose.log")).read())

    def test_an_agent_that_cleaned_up_passes_and_a_continued_stop_closes_at_once(self):
        clean = Machine(base())
        self.assertIsNone(pc.on_subagent_stop({"session_id": "S1", "agent_id": "ag2", "agent_transcript_path": self.transcript()}, **clean.kw()))
        m = Machine(base() + [row(500, ppid=CLAUDE, owner="S1", age=300, command="python3 -m http.server 8123")])
        out = pc.on_subagent_stop({"session_id": "S1", "agent_id": "ag3", "stop_hook_active": True,
                                   "agent_transcript_path": self.transcript()}, **m.kw())
        self.assertIn("FAIL", out["systemMessage"])
        self.assertNotIn(500, m.pids())

    def test_without_a_transcript_nothing_is_claimed(self):
        m = Machine(base() + [row(500, ppid=CLAUDE, owner="S1", age=300)])
        self.assertIsNone(pc.on_subagent_stop({"session_id": "S1", "agent_id": "ag4"}, **m.kw()))
        self.assertIsNone(pc.on_subagent_stop({"session_id": "S1", "agent_id": "ag5", "transcript_path": self.transcript()}, **m.kw()))

    def test_a_sibling_python3_or_sleep_of_the_same_session_survives_the_gate(self):
        m = Machine(base() + [row(500, ppid=CLAUDE, owner="S1", age=300, command="python3 -m http.server 8123"),
                              row(510, ppid=CLAUDE, owner="S1", age=300, command="python3"),
                              row(511, ppid=CLAUDE, owner="S1", age=300, command="sleep 600"),
                              row(512, ppid=CLAUDE, owner="S1", age=300, command="python3 -m http.server")])
        inp = {"session_id": "S1", "agent_id": "ag6", "stop_hook_active": True,
               "agent_transcript_path": self.transcript("cd /x && python3 -m http.server 8123 > /dev/null 2>&1 &")}
        pc.on_subagent_stop(inp, **m.kw())
        self.assertEqual(m.pids(), {CLAUDE, ME, 510, 511, 512})

    def test_a_pipe_or_a_bare_program_is_never_claimed(self):
        sibs = [row(510, ppid=CLAUDE, owner="S1", age=300, command="python3"),
                row(511, ppid=CLAUDE, owner="S1", age=300, command="cat"),
                row(512, ppid=CLAUDE, owner="S1", age=300, command="sleep 600"),
                row(513, ppid=CLAUDE, owner="S1", age=300, command="x | python3")]
        for n, cmd in enumerate(["echo x | python3 &", "yes | cat &", "sleep 600 &", "a ; python3 &", "echo x && sleep 600 &"]):
            m = Machine(base() + list(sibs))
            inp = {"session_id": "S1", "agent_id": "pipe%d" % n, "stop_hook_active": True, "agent_transcript_path": self.transcript(cmd)}
            self.assertIsNone(pc.on_subagent_stop(inp, **m.kw()), cmd)
            self.assertEqual(m.pids(), {CLAUDE, ME, 510, 511, 512, 513}, cmd)
        with open(os.path.join(self.home, "procclose.log")) as f:
            self.assertIn("not claimable", f.read())

    def test_the_accepted_shape_strips_exactly_cd_nohup_redirections_and_the_ampersand(self):
        self.assertEqual(pc.claim_shape("cd /x && nohup python3 -m http.server 8123 > log 2>&1 &"), "python3 -m http.server 8123")
        self.assertEqual(pc.claim_shape("exec  node  --port 80   s.js"), "node --port 80 s.js")
        self.assertIsNone(pc.claim_shape("sleep 600 &"))
        self.assertIsNone(pc.claim_shape("python3"))
        self.assertIsNone(pc.claim_shape("python3 -m x $(id)"))
        self.assertIsNone(pc.claim_shape("cd /x && cd /y && python3 -m x"))
        self.assertIsNone(pc.claim_shape("echo x | python3 -m http.server 8123 &"))
        self.assertIsNone(pc.claim_shape("python3 a b | tee out.log &"))


class RealProcessTest(StudioTestCase):
    CODE = "import time; time.sleep(30)"           # a non-Apple sleeper: macOS shows no environment of /bin/sleep

    def spawn(self, code, marked=True, sid="real-test-session"):
        env = dict(os.environ, BUDGIT_OWNER=sid) if marked else dict(os.environ)
        env.pop("BUDGIT_OWNER", None) if not marked else None
        p = subprocess.Popen([sys.executable, "-c", code], env=env, start_new_session=True)
        self.addCleanup(lambda: (p.poll() is None) and (p.kill(), p.wait()))
        return p

    def wait_visible(self, pid, sid):
        for _ in range(40):
            if any(r["pid"] == pid and r["owner"] == sid for r in pc.read_table()):
                return
            time.sleep(0.1)
        self.fail("the marked process never showed its environment to the reader")

    def test_a_real_marked_sleep_is_gone_after_session_end_and_an_unmarked_one_survives(self):
        marked = self.spawn(self.CODE)
        plain = self.spawn(self.CODE, marked=False)
        self.wait_visible(marked.pid, "real-test-session")
        pc.on_session_end({"session_id": "real-test-session"})
        marked.wait(timeout=5)
        self.assertIsNotNone(marked.returncode)
        self.assertIsNone(plain.poll())

    def test_a_marked_python_parent_with_an_unmarked_bin_sleep_child_are_both_gone_inside_the_session_end_budget(self):
        code = "import subprocess,time; subprocess.Popen(['/bin/sleep','30']); time.sleep(30)"
        parent = self.spawn(code)
        self.wait_visible(parent.pid, "real-test-session")
        child = None
        for _ in range(40):
            kids = [r for r in pc.read_table() if r["ppid"] == parent.pid]
            if kids:
                child = kids[0]["pid"]
                break
            time.sleep(0.1)
        self.assertIsNotNone(child)
        factor = load_factor()
        started = time.monotonic()
        pc.on_session_end({"session_id": "real-test-session"})
        elapsed = time.monotonic() - started
        factor = max(factor, load_factor())     # the machine may have got busier during the run: the worse reading counts
        sys.stderr.write("session-end closer measured %.2f s (load factor %.1f)\n" % (elapsed, factor))
        parent.wait(timeout=5)
        time.sleep(0.2)
        self.assertRaises(OSError, os.kill, child, 0)
        self.assertLess(elapsed, 1.5 * factor)       # budget 1.5 s scaled by the same-run spawn calibration

    def test_a_process_that_ignores_term_is_killed_inside_the_budget(self):
        code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
        p = self.spawn(code)
        self.wait_visible(p.pid, "real-test-session")
        factor = load_factor()
        started = time.monotonic()
        pc.on_session_end({"session_id": "real-test-session"})
        elapsed = time.monotonic() - started
        factor = max(factor, load_factor())     # the worse of the before and after readings counts
        sys.stderr.write("session-end closer with TERM ignored measured %.2f s (load factor %.1f)\n" % (elapsed, factor))
        p.wait(timeout=5)
        self.assertIsNotNone(p.returncode)           # the product's outcome: the TERM-ignoring process is killed
        self.assertLess(elapsed, 1.5 * factor)       # budget 1.5 s scaled by the same-run spawn calibration


class ParentWatchTest(StudioTestCase):
    def test_exit_when_the_parent_changes_or_the_ttl_passes(self):
        self.assertEqual(parentwatch.should_exit(10, 0, 100, now=1, getppid=lambda: 1), "parent")
        self.assertEqual(parentwatch.should_exit(10, 0, 100, now=101, getppid=lambda: 10), "ttl")
        self.assertIsNone(parentwatch.should_exit(10, 0, 100, now=5, getppid=lambda: 10))
