"""R-1293: `away expire --kind subagent-ask` drops a helper's denied asks, never approves them, and keeps the user's notes.
Temp Budgit home (STUDIO_HOME of the fixture), fake project names only."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import away

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
BAD = "this line is not json {"


def entry(kind, project, text):
    return json.dumps({"t": "2026-11-01T00:00:00Z", "kind": kind, "session": "s", "project": project, "text": text})


class ExpireCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.home, exist_ok=True)
        self.a = os.path.realpath(os.path.join(self.home, "zorb-app"))
        self.b = os.path.realpath(os.path.join(self.home, "quux-lab"))

    def write(self, lines):
        with open(away.queue_path(), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def read(self):
        with open(away.queue_path(), "rb") as f:
            return f.read().decode("utf-8").split("\n")[:-1]

    def mixed(self):
        rows = []
        for p in (self.a, self.b):
            rows += [entry("note", p, "n"), entry("guard", p, "g"), entry("subagent-ask", p, "s1"), entry("subagent-ask", p, "s2")]
        rows.insert(3, BAD)
        self.write(rows)
        return rows

    def env(self):
        env = dict(os.environ, STUDIO_HOME=self.home, BUDGIT_LANG="en", CLAUDE_CODE_SESSION_ID="s1")
        env.pop("BUDGIT_HOME", None)
        return env


class ExpireTest(ExpireCase):
    def test_per_project_expire_removes_only_that_projects_asks(self):
        rows = self.mixed()
        self.assertEqual(away.expire_kind("subagent-ask", project=self.a), 2)
        want = [r for r in rows if not (r.startswith('{"t"') and '"subagent-ask"' in r and json.loads(r)["project"] == self.a)]
        self.assertEqual(self.read(), want)
        self.assertIn(BAD, self.read())

    def test_all_projects_removes_every_ask_and_keeps_the_rest(self):
        rows = self.mixed()
        self.assertEqual(away.expire_kind("subagent-ask", project=None), 4)
        self.assertEqual(self.read(), [r for r in rows if '"subagent-ask"' not in r])
        self.assertEqual(len(self.read()), 5)

    def test_only_asks_left_removes_the_file(self):
        self.write([entry("subagent-ask", self.a, "x"), entry("subagent-ask", self.b, "y")])
        self.assertEqual(away.expire_kind("subagent-ask"), 2)
        self.assertFalse(os.path.exists(away.queue_path()))

    def test_nothing_to_expire_returns_zero_and_keeps_the_bytes(self):
        self.write([entry("note", self.a, "n"), BAD])
        with open(away.queue_path(), "rb") as f:
            before = f.read()
        self.assertEqual(away.expire_kind("subagent-ask"), 0)
        with open(away.queue_path(), "rb") as f:
            self.assertEqual(f.read(), before)


class ExpireBytesTest(ExpireCase):
    def test_expire_keeps_every_other_line_byte_identical(self):
        parts = [
            entry("note", self.a, "n").encode("utf-8"),
            entry("subagent-ask", self.a, "s1").encode("utf-8"),
            b"\xff\xfe\x80 not utf-8",
            b"",
            entry("note", self.b, "cr").encode("utf-8") + b"\r",
            entry("subagent-ask", self.b, "s2").encode("utf-8"),
            b"plain text line",
        ]
        asks = {1, 5}
        original = b"\n".join(parts) + b"\n"
        expected = b"\n".join(p for i, p in enumerate(parts) if i not in asks) + b"\n"
        with open(away.queue_path(), "wb") as f:
            f.write(original)
        self.assertEqual(away.expire_kind("subagent-ask"), 2)
        with open(away.queue_path(), "rb") as f:
            self.assertEqual(f.read(), expected)

    def test_a_failed_rewrite_raises_and_leaves_the_queue_alone(self):
        from unittest import mock
        self.write([entry("subagent-ask", self.a, "s"), entry("note", self.a, "kept")])
        with open(away.queue_path(), "rb") as f:
            before = f.read()
        with mock.patch.object(away, "_write_bytes", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                away.expire_kind("subagent-ask")
        with open(away.queue_path(), "rb") as f:
            self.assertEqual(f.read(), before)


class ExpireNoteWaitTest(ExpireCase):
    def test_a_note_waits_at_most_a_second_for_a_held_queue_lock(self):
        import threading
        import time
        import studiolib as sl
        got = {}
        with sl.home_lock(away.LOCK_NAME):
            t0 = time.monotonic()
            t = threading.Thread(target=lambda: got.update(ok=away.note("held note", "note", "s2", project=self.a)))
            t.start()
            t.join(5)
            elapsed = time.monotonic() - t0
            waited = t.is_alive()
        t.join(10)
        self.assertFalse(waited, "the note waited on the held queue lock for more than 5 seconds")
        self.assertLess(elapsed, 3.0)
        self.assertTrue(got.get("ok"))
        self.assertIn("held note", [json.loads(r)["text"] for r in self.read()])


class ExpireCliFailTest(ExpireCase):
    def test_a_failed_rewrite_exits_1_and_says_nothing_was_expired(self):
        self.init_project()
        proj = os.path.realpath(self.project)
        self.write([entry("subagent-ask", proj, "mine"), entry("note", proj, "kept")])
        qdir = os.path.dirname(away.queue_path())
        os.chmod(qdir, 0o500)
        try:
            out = subprocess.run([sys.executable, CLI, "--project", self.project, "away", "expire", "--kind", "subagent-ask"],
                                 env=self.env(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        finally:
            os.chmod(qdir, 0o700)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("nothing was expired", out.stderr)


class ExpireLockTest(ExpireCase):
    def test_a_note_written_while_expire_waits_on_the_lock_survives(self):
        import threading
        import time
        import studiolib as sl
        self.write([entry("subagent-ask", self.a, "s"), entry("note", self.a, "kept")])
        got = {}
        with sl.home_lock(away.LOCK_NAME):
            t1 = threading.Thread(target=lambda: got.update(n=away.expire_kind("subagent-ask")))
            t2 = threading.Thread(target=lambda: away.note("late note", "note", "s2", project=self.a))
            t1.start()
            t2.start()
            time.sleep(0.4)
            self.assertTrue(t1.is_alive() and t2.is_alive())       # both wait for the lock
        t1.join(10)
        t2.join(10)
        self.assertEqual(got["n"], 1)
        texts = [json.loads(r)["text"] for r in self.read()]
        self.assertEqual(sorted(texts), ["kept", "late note"])


class ExpireCliTest(ExpireCase):
    def test_flag_is_byte_identical_and_two_projects_end_to_end(self):
        self.init_project()
        proj = os.path.realpath(self.project)
        away.set_away("s1")
        with open(away.flag_path(), "rb") as f:
            flag = f.read()
        self.write([entry("subagent-ask", proj, "mine"), entry("subagent-ask", self.b, "quux ask"),
                    entry("subagent-ask", self.a, "zorb ask"), entry("note", self.b, "q note")])
        out = self.run_cli("expire", "--kind", "subagent-ask")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(self.read()), 3)                      # only this project's ask went
        self.assertIn("quux ask", "\n".join(self.read()))
        out2 = self.run_cli("expire", "--kind", "subagent-ask", "--all-projects")
        self.assertEqual(out2.returncode, 0, out2.stderr)
        self.assertEqual([json.loads(r)["kind"] for r in self.read()], ["note"])
        for text in (out.stdout, out2.stdout):
            for name in ("zorb-app", "quux-lab", self.a, self.b, proj):
                self.assertNotIn(name, text)
        with open(away.flag_path(), "rb") as f:
            self.assertEqual(f.read(), flag)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, CLI, "--project", self.project, "away"] + list(args), env=self.env(),
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_cli_expires_this_project_and_names_nothing(self):
        self.init_project()
        proj = os.path.realpath(self.project)
        self.write([entry("note", proj, "n"), entry("subagent-ask", proj, "s"), entry("subagent-ask", self.b, "t")])
        out = self.run_cli("expire", "--kind", "subagent-ask")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "away: 1 helper asks expired, none approved (this project)")
        self.assertNotIn("zorb-app", out.stdout)
        self.assertNotIn("quux-lab", out.stdout)
        self.assertEqual(len(self.read()), 2)
        out = self.run_cli("expire", "--kind", "subagent-ask", "--all-projects")
        self.assertIn("(all projects)", out.stdout)
        self.assertEqual([json.loads(r)["kind"] for r in self.read()], ["note"])

    def test_kind_note_is_refused_and_missing_kind_exits_2(self):
        self.init_project()
        out = self.run_cli("expire", "--kind", "note")
        self.assertNotEqual(out.returncode, 0)
        out = self.run_cli("expire")
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
