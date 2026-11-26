"""R-402: the machine shared fairly between projects. admit's fair-share rules (injected readings and shares), the lane count per
project (injected sessions, temp projects), and the suite queue that serves projects in turn with release suites first (real
processes on a temp Budgit home, one slot)."""
import fcntl
import json
import os
import subprocess
import sys
import textwrap
import time

from helpers import BudgitTestCase, ROOT
import admit
import fairshare
import studiolib as sl
import suiteorder

SCRIPTS = os.path.join(ROOT, "plugin", "scripts")
BUDGIT = os.path.join(ROOT, "plugin", "bin", "budgit")


def reading(load=1.0, free=8000, busy=1, cores=8):
    return {"cpu_load": load, "cores": cores, "free_mb": free, "busy": busy}


def share(lanes=0, starved=0, most=False, projects=3):
    return {"lanes": lanes, "pending_projects": projects, "starved": starved, "most": most}


class AdmitShareTest(BudgitTestCase):
    def setUp(self):
        super().setUp()
        os.environ["BUDGIT_LANG"] = "en"
        os.environ.pop("BUDGIT_SUITE_LOAD_LIMIT", None)

    def decide(self, m, s, kind="lane"):
        return admit.decide(m, kind, {}, s)

    def test_a_project_with_no_lane_always_gets_its_first_whatever_the_load_and_the_busy_count(self):
        got = self.decide(reading(load=30, busy=9), share(lanes=0))
        self.assertEqual((got["verdict"], got["exit"]), ("go", 0))
        self.assertIn("first one always runs", got["reason"])

    def test_the_first_lane_is_still_refused_when_the_machine_is_out_of_memory(self):
        self.assertEqual(self.decide(reading(free=100), share(lanes=0))["verdict"], "refuse")

    def test_above_the_waiting_load_the_project_with_the_most_lanes_yields_and_the_cap_is_one(self):
        got = self.decide(reading(load=9), share(lanes=3, most=True))
        self.assertEqual(got["verdict"], "wait")
        self.assertIn("holds the most lanes (3), so it yields first", got["reason"])
        self.assertEqual(got["lane_cap"], 1)
        other = self.decide(reading(load=9), share(lanes=1))
        self.assertEqual(other["verdict"], "wait")
        self.assertIn("above the waiting limit", other["reason"])
        self.assertNotIn("yields", other["reason"])

    def test_a_second_lane_needs_a_load_under_the_waiting_limit(self):
        self.assertEqual(self.decide(reading(load=6.8), share(lanes=1))["verdict"], "go")
        self.assertEqual(self.decide(reading(load=6.9), share(lanes=1))["verdict"], "wait")

    def test_a_third_lane_waits_while_another_project_with_work_has_none(self):
        got = self.decide(reading(load=1), share(lanes=2, starved=1, most=True))
        self.assertEqual(got["verdict"], "wait")
        self.assertIn("1 other project(s) with pending work hold none", got["reason"])
        self.assertEqual(got["lane_cap"], 2)
        again = self.decide(reading(load=1), share(lanes=2, starved=0, most=True))
        self.assertEqual((again["verdict"], again["lane_cap"]), ("go", None))

    def test_the_refusal_limits_still_hold_for_a_project_that_has_a_lane(self):
        self.assertEqual(self.decide(reading(load=17), share(lanes=1))["verdict"], "refuse")
        self.assertEqual(self.decide(reading(busy=6), share(lanes=1))["verdict"], "refuse")

    def test_without_a_share_nothing_changes_and_a_suite_ignores_it(self):
        self.assertEqual(self.decide(reading(load=30), None)["verdict"], "refuse")
        self.assertEqual(self.decide(reading(load=9), share(lanes=0), kind="suite")["verdict"], "wait")


class SurveyTest(BudgitTestCase):
    def make_project(self, name, pending=True):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(path, self.DATA))
        data = sl.empty_ledger()
        if pending:
            sl.add_item(data, "work", "user", phase="0.1")
        sl.save_ledger(path, data)
        return path

    def session(self, n, cwd, title="lane", status="busy", verified=True):
        return {"pid": 1000 + n, "session_id": "00000000-0000-4000-8000-%012d" % n, "cwd": cwd, "status": status, "since": 0,
                "name": title, "kind": "", "waiting_for": None, "verified": verified, "file": ""}

    def setUp(self):
        super().setUp()
        os.environ["BUDGIT_LANG"] = "en"
        old = os.environ.get("HOME")                                       # sessions are attributed to folders under the user's home
        os.environ["HOME"] = self.tmp
        self.addCleanup(lambda: os.environ.pop("HOME", None) if old is None else os.environ.update(HOME=old))
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"projects_root": self.tmp}, f)
        self.a, self.b, self.c = (self.make_project(n) for n in ("alpha", "beta", "gamma"))

    def test_lanes_count_busy_verified_sessions_that_are_not_coordinators(self):
        sessions = [self.session(1, self.a), self.session(2, self.a), self.session(3, self.a, title="PM · Alpha"),
                    self.session(4, self.a, title="TM - team"), self.session(5, self.a, status="idle"),
                    self.session(6, self.a, verified=False), self.session(7, self.b)]
        table = fairshare.survey(sessions)
        self.assertEqual(table[os.path.realpath(self.a)]["lanes"], 2)
        self.assertEqual(table[os.path.realpath(self.b)]["lanes"], 1)
        self.assertEqual(table[os.path.realpath(self.c)]["lanes"], 0)

    def test_a_busy_project_cannot_starve_the_two_others(self):
        busy = [self.session(i, self.a) for i in range(1, 5)]
        load = reading(load=30, busy=4)                                   # far above the refusal limit
        first = {name: admit.decide(load, "lane", {}, fairshare.share(path, busy)) for name, path in
                 (("a", self.a), ("b", self.b), ("c", self.c))}
        self.assertEqual(first["a"]["verdict"], "refuse")                 # four lanes already: it gets nothing more now
        self.assertEqual(first["b"]["verdict"], "go")                     # nothing else matters: B and C each get a first lane
        self.assertEqual(first["c"]["verdict"], "go")
        now = busy + [self.session(10, self.b), self.session(11, self.c)]
        calm = reading(load=1, busy=6 - 1)
        again = admit.decide(calm, "lane", {}, fairshare.share(self.a, now))
        self.assertEqual(again["verdict"], "go")                          # B and C have their lane, so A may take a fifth
        starved = admit.decide(calm, "lane", {}, fairshare.share(self.a, busy + [self.session(10, self.b)]))
        self.assertEqual(starved["verdict"], "wait")                      # C still has none
        self.assertIn("hold none", starved["reason"])

    def test_the_asking_project_counts_as_having_work_and_an_unknown_one_has_no_share(self):
        empty = self.make_project("empty", pending=False)
        got = fairshare.share(empty, [])
        self.assertEqual(got["lanes"], 0)
        self.assertEqual(got["pending_projects"], 4)
        self.assertIsNone(fairshare.share(os.path.join(self.tmp, "nowhere"), []))

    def test_a_project_with_a_ledger_that_the_survey_cannot_see_still_gets_its_first_lane(self):
        import shutil
        import tempfile
        away = tempfile.mkdtemp()                                           # outside the home and the projects root
        self.addCleanup(shutil.rmtree, away, True)
        os.makedirs(os.path.join(away, self.DATA))
        data = sl.empty_ledger()
        sl.add_item(data, "work", "user", phase="0.1")
        sl.save_ledger(away, data)
        got = fairshare.share(away, [])
        self.assertEqual((got["lanes"], got["pending_projects"], got["starved"]), (0, 4, 3))
        self.assertFalse(got["most"])

    def test_the_cli_takes_the_project_and_prints_the_share_in_json(self):
        r = subprocess.run([sys.executable, BUDGIT, "admit", "lane", "--json", "--project", self.a], capture_output=True, text=True,
                           env=dict(os.environ, STUDIO_HOME=self.home, BUDGIT_SUITE_LOAD_LIMIT="1000000",
                                    BUDGIT_TEST_MEM='{"pressure": 1, "free_true_mb": 8000, "total_mb": 16000, "compressor_mb": 100}'))
        self.assertIn(r.returncode, (0, 1, 2), r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("share", data)
        self.assertIn("lane_cap", data)


class OrderTest(BudgitTestCase):
    @staticmethod
    def ticket(i, project, since, release=False):
        return {"id": i, "project": project, "since": since, "release": release}

    def test_projects_take_turns_and_the_oldest_ticket_of_each_goes_first(self):
        waiting = [self.ticket("a1", "A", 1), self.ticket("a2", "A", 2), self.ticket("a3", "A", 3), self.ticket("b1", "B", 4),
                   self.ticket("c1", "C", 5)]
        self.assertEqual([t["id"] for t in suiteorder.order(waiting, {})], ["a1", "b1", "c1", "a2", "a3"])

    def test_the_project_served_last_goes_last(self):
        waiting = [self.ticket("a1", "A", 1), self.ticket("b1", "B", 2), self.ticket("c1", "C", 3)]
        self.assertEqual([t["id"] for t in suiteorder.order(waiting, {"A": 100, "B": 50})], ["c1", "b1", "a1"])

    def test_a_release_suite_goes_before_every_ordinary_one(self):
        waiting = [self.ticket("a1", "A", 1), self.ticket("b1", "B", 2), self.ticket("r1", "C", 9, release=True),
                   self.ticket("r0", "A", 8, release=True)]
        self.assertEqual([t["id"] for t in suiteorder.order(waiting, {})], ["r0", "r1", "a1", "b1"])

    def test_a_waiting_suite_from_the_same_checkout_is_refused_and_a_dead_ticket_is_dropped(self):
        suiteorder.join("t1", "A", "/x/a", False, "suite")
        data = suiteorder._load()
        data["waiting"][0]["pid"] = os.getppid()                          # another live process holds the place
        suiteorder._save(data)
        with self.assertRaises(suiteorder.AlreadyQueued):
            suiteorder.join("t2", "A", "/x/a", False, "suite")
        suiteorder.leave("t1")
        suiteorder.join("t3", "A", "/x/a", False, "suite")
        data = suiteorder._load()
        data["waiting"].append({"id": "ghost", "pid": 2 ** 22 + 12345, "project": "B", "top": "/x/b", "release": False, "since": 1})
        suiteorder._save(data)
        self.assertEqual([t["project"] for t in suiteorder.snapshot()], ["A"])

    def test_a_broken_queue_file_is_an_empty_queue(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, suiteorder.QUEUE), "w") as f:
            f.write('{"waiting": ["x", 3, {"id": 4}], "served": {"A": "late"}}')
        self.assertEqual(suiteorder.snapshot(), [])


WAITER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, %r)
    import suitequeue
    suitequeue.POLL = 0.05
    tag, project, release, log = sys.argv[1:5]
    with suitequeue.SuiteSlot(tag, project=project, release=(True if release == "1" else None),
                             snapshot=lambda: "free", cpu=lambda: 0.1,
                             mem=lambda: {"pressure": 1, "free_true_mb": 8000}):          # injected: no live reading decides
        with open(log, "a") as f:
            f.write(tag + "\\n")
        time.sleep(0.15)
""" % SCRIPTS)


class QueueProcessTest(BudgitTestCase):
    """One slot, held by the test; waiters are real processes; the log says who got the slot, in order."""

    def setUp(self):
        super().setUp()
        self.env = dict(os.environ, STUDIO_HOME=self.home, BUDGIT_SUITE_SLOTS="1", BUDGIT_SUITE_LOAD_LIMIT="1000000",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.x",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        self.env.pop("BUDGIT_SUITE_QUEUE_HELD", None)               # run inside `budgit suite`, the outer slot must not leak in
        self.log = os.path.join(self.tmp, "order.log")
        self.script = os.path.join(self.tmp, "waiter.py")
        with open(self.script, "w") as f:
            f.write(WAITER)
        os.makedirs(self.home, exist_ok=True)
        self.slot = os.open(os.path.join(self.home, "suite.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.slot, fcntl.LOCK_EX)
        self.procs = []
        self.addCleanup(self.stop)

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()

    def repo(self, name, trees=1):
        main = os.path.join(self.tmp, name)
        os.makedirs(main)
        for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", main] + args, env=self.env, check=True, capture_output=True)
        tops = [main]
        for i in range(1, trees):
            wt = os.path.join(self.tmp, "%s-wt%d" % (name, i))
            subprocess.run(["git", "-C", main, "worktree", "add", "-q", "-b", "b%d" % i, wt], env=self.env, check=True, capture_output=True)
            tops.append(wt)
        return tops

    def start(self, tag, top, release=False):
        self.procs.append(subprocess.Popen([sys.executable, self.script, tag, top, "1" if release else "0", self.log], env=self.env))
        want = len(self.procs)
        deadline = time.time() + 30
        while len(suiteorder.snapshot()) < want:
            self.assertLess(time.time(), deadline, "%s never queued" % tag)
            time.sleep(0.05)

    def run_to_the_end(self):
        fcntl.flock(self.slot, fcntl.LOCK_UN)
        os.close(self.slot)
        for p in self.procs:
            self.assertEqual(p.wait(timeout=60), 0)
        with open(self.log) as f:
            return f.read().split()

    def test_three_projects_take_turns_so_the_busy_one_cannot_starve_the_others(self):
        a1, a2, a3 = self.repo("alpha", 3)
        (b,), (c,) = self.repo("beta"), self.repo("gamma")
        for tag, top in (("a1", a1), ("a2", a2), ("a3", a3), ("b1", b), ("c1", c)):
            self.start(tag, top)
        self.assertEqual(self.run_to_the_end(), ["a1", "b1", "c1", "a2", "a3"])

    def test_a_release_suite_skips_ahead_of_everything_waiting(self):
        a1, a2 = self.repo("alpha", 2)
        (b,), (c,) = self.repo("beta"), self.repo("gamma")
        for tag, top, release in (("a1", a1, False), ("a2", a2, False), ("b1", b, False), ("rel", c, True)):
            self.start(tag, top, release)
        self.assertEqual(self.run_to_the_end()[0], "rel")

    def test_a_second_waiting_suite_from_the_same_checkout_does_not_queue(self):
        (a,) = self.repo("alpha")
        self.start("a1", a)
        r = subprocess.run([sys.executable, self.script, "again", a, "0", self.log], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already waiting", r.stderr)
        self.assertEqual(self.run_to_the_end(), ["a1"])
