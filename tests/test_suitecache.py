"""R-382: two suite slots on the machine, the suite at low priority, and a green result remembered by git tree.
Synthetic lock files and temp repos only."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import BudgitTestCase
from loadcal import load_factor
from test_maincheck import RepoCase
from test_suitequeue import HOLDER
import suitecache
import suitequeue


PRIO_MAX = 20 if sys.platform == "darwin" else 19                  # the lowest priority nice can reach on this system


class EnvCase(BudgitTestCase):
    """Sets the given BUDGIT_* variables for one test and puts the old values back."""
    ENV = {}

    def setUp(self):
        super().setUp()
        keys = set(self.ENV) | {"BUDGIT_SUITE_SLOTS", "BUDGIT_SUITE_PRIORITY", "BUDGIT_SUITE_CACHE", suitequeue.HELD_ENV}
        old = {k: os.environ.get(k) for k in keys}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(self.ENV)


class SlotCountTest(EnvCase):
    def test_two_slots_by_default_and_the_env_or_config_changes_it(self):
        self.assertEqual(suitequeue.slot_count(), 2)
        os.environ["BUDGIT_SUITE_SLOTS"] = "3"
        self.assertEqual(suitequeue.slot_count(), 3)
        os.environ["BUDGIT_SUITE_SLOTS"] = "1"
        self.assertEqual(suitequeue.slot_count(), 1)
        os.environ.pop("BUDGIT_SUITE_SLOTS")
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"admit": {"suite_slots": 4}}, f)
        self.assertEqual(suitequeue.slot_count(), 4)

    def test_junk_and_zero_fall_back_to_two(self):
        for junk in ("zero", "0", "-3", ""):
            os.environ["BUDGIT_SUITE_SLOTS"] = junk
            self.assertEqual(suitequeue.slot_count(), 2, junk)

    def test_slot_files_sit_beside_the_lock(self):
        os.environ["BUDGIT_SUITE_LOCK"] = "/x/suite.lock"
        self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOCK", None)
        os.environ["BUDGIT_SUITE_SLOTS"] = "3"
        self.assertEqual(suitequeue.slot_paths(), ["/x/suite.lock", "/x/suite.lock.1", "/x/suite.lock.2"])


class TwoSlotsTest(EnvCase):
    def setUp(self):
        self.ENV = {"BUDGIT_SUITE_LOAD_LIMIT": "100000", "BUDGIT_SUITE_SLOTS": "2"}
        super().setUp()
        self.lock = os.path.join(self.home, "q", "suite.lock")
        os.environ["BUDGIT_SUITE_LOCK"] = self.lock
        self.addCleanup(os.environ.pop, "BUDGIT_SUITE_LOCK", None)

    def hold(self, path, seconds):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        p = subprocess.Popen([sys.executable, "-c", HOLDER, path, str(seconds)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_a_second_suite_runs_beside_the_first(self):
        self.hold(self.lock, 30)
        began = time.time()
        with suitequeue.SuiteSlot("second", snapshot=lambda: "free", cpu=lambda: 0.1,
                              mem=lambda: {"pressure": 1, "free_true_mb": 8000}) as slot:       # no live reading decides
            self.assertEqual(slot.waited, 0)
            self.assertEqual(slot.path, self.lock + ".1")
        self.assertLess(time.time() - began, 3 * load_factor())     # real-deadline budget 3 s scaled by the same-run calibration (R-1282)

    def test_a_third_suite_waits_until_one_of_the_two_ends(self):
        self.hold(self.lock, 2)
        self.hold(self.lock + ".1", 30)
        heard = []
        with suitequeue.SuiteSlot("third", notify=lambda w, h: heard.append(h), snapshot=lambda: "free", cpu=lambda: 0.1,
                              mem=lambda: {"pressure": 1, "free_true_mb": 8000}) as slot:
            self.assertGreaterEqual(slot.waited, 1)
            self.assertEqual(slot.path, self.lock)                    # the slot that freed up
        self.assertEqual(heard[0]["what"], "other-project suite")

    def test_the_two_slots_are_two_different_files(self):
        os.makedirs(os.path.dirname(self.lock), exist_ok=True)
        first = suitequeue.SuiteSlot._take(suitequeue.slot_paths())
        second = suitequeue.SuiteSlot._take(suitequeue.slot_paths())
        self.addCleanup(lambda: [os.close(fd) for fd, _p in (first, second) if fd is not None])
        self.assertEqual({first[1], second[1]}, set(suitequeue.slot_paths()))
        self.assertEqual(suitequeue.SuiteSlot._take(suitequeue.slot_paths()), (None, None))


class PriorityTest(EnvCase):
    def run_cmd(self, command):
        return subprocess.run(suitequeue.lower_priority(command), shell=True, capture_output=True, text=True)

    def test_the_default_runs_the_suite_ten_nice_levels_lower_and_keeps_its_exit_code(self):
        r = self.run_cmd("%s -c 'import os; print(os.getpriority(os.PRIO_PROCESS, 0))'" % shlex.quote(sys.executable))
        self.assertEqual(int(r.stdout.strip()), min(os.getpriority(os.PRIO_PROCESS, 0) + suitequeue.NICE, PRIO_MAX))
        self.assertEqual(self.run_cmd("exit 7").returncode, 7)

    def test_off_leaves_the_command_alone(self):
        os.environ["BUDGIT_SUITE_PRIORITY"] = "off"
        self.assertEqual(suitequeue.lower_priority("echo hi"), "echo hi")

    def test_background_uses_taskpolicy_when_the_machine_has_it(self):
        os.environ["BUDGIT_SUITE_PRIORITY"] = "background"
        got = suitequeue.lower_priority("echo hi")
        if shutil.which("taskpolicy"):
            self.assertTrue(got.startswith("taskpolicy -b "))
        else:
            self.assertIn(" -n ", got)                                   # no taskpolicy here: nice instead
        self.assertEqual(self.run_cmd("exit 3").returncode, 3)

    def test_quotes_survive(self):
        self.assertEqual(self.run_cmd("printf '%s' \"a 'b' c\"").stdout, "a 'b' c")


class CleanTreeTest(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix="budgit-tree-")
        self.addCleanup(shutil.rmtree, self.top, True)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        self.git("init", "-q", "-b", "main")
        self.write("a.py", "x = 1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "start")

    def git(self, *args):
        subprocess.run(["git", "-C", self.top] + list(args), check=True, capture_output=True, env=self.env)

    def write(self, rel, text):
        path = os.path.join(self.top, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def test_a_clean_checkout_gives_its_tree(self):
        self.assertTrue(suitecache.tree_of(self.top))
        self.assertEqual(suitecache.clean_tree(self.top), suitecache.tree_of(self.top))

    def test_a_changed_tracked_file_or_a_new_untracked_file_is_not_clean(self):
        self.write("a.py", "x = 2\n")
        self.assertIsNone(suitecache.clean_tree(self.top))
        self.git("checkout", "--", "a.py")
        self.assertTrue(suitecache.clean_tree(self.top))
        self.write("test_new.py", "x\n")                                   # a test file nobody added yet would be skipped
        self.assertIsNone(suitecache.clean_tree(self.top))

    def test_project_state_folders_do_not_count(self):
        self.write(".studio/notes/n.md", "PASS\n")
        self.write(".budgit/ledger.json", "{}\n")
        self.assertTrue(suitecache.clean_tree(self.top))
        self.write(".studio-other/x", "x\n")                               # only those two folders are exempt
        self.assertIsNone(suitecache.clean_tree(self.top))

    def test_not_a_repository_gives_nothing(self):
        other = tempfile.mkdtemp(prefix="budgit-nogit-")
        self.addCleanup(shutil.rmtree, other, True)
        self.assertIsNone(suitecache.clean_tree(other))
        self.assertIsNone(suitecache.tree_of(other))

    def test_the_key_depends_on_tree_folder_and_command(self):
        k = suitecache.key("/a", "t1", "c")
        self.assertEqual(k, suitecache.key("/a", "t1", "c"))
        self.assertEqual(len({k, suitecache.key("/b", "t1", "c"), suitecache.key("/a", "t2", "c"), suitecache.key("/a", "t1", "d")}), 4)


class StoreTest(EnvCase):
    def test_a_corrupt_store_is_an_empty_one_and_record_repairs_it(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, suitecache.FILE), "w") as f:
            f.write("{not json")
        self.assertIsNone(suitecache.lookup("k"))
        suitecache.record("k", "/a", "t", "c", 1.5)
        self.assertEqual(suitecache.lookup("k")["tree"], "t")

    def test_a_junk_entry_in_valid_json_is_no_entry_and_never_crashes_record(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, suitecache.FILE), "w") as f:
            json.dump({"entries": {"a": "junk", "b": 3, "c": None}}, f)
        self.assertIsNone(suitecache.lookup("a"))
        suitecache.record("a", "/x", "t", "c", 1)
        self.assertEqual(suitecache.lookup("a")["tree"], "t")
        suitecache.forget("a")
        self.assertIsNone(suitecache.lookup("a"))
        suitecache.forget("never-there")

    def test_the_store_keeps_the_newest_entries_only(self):
        for i in range(suitecache.KEEP + 5):
            suitecache.record("k%d" % i, "/a", "t", "c", 1)
        with open(os.path.join(self.home, suitecache.FILE)) as f:
            self.assertEqual(len(json.load(f)["entries"]), suitecache.KEEP)

    def test_off_reads_and_writes_nothing(self):
        os.environ["BUDGIT_SUITE_CACHE"] = "off"
        suitecache.record("k", "/a", "t", "c", 1)
        self.assertIsNone(suitecache.lookup("k"))
        self.assertFalse(os.path.exists(os.path.join(self.home, suitecache.FILE)))


class CacheCliTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.env["BUDGIT_SUITE_LOCK"] = os.path.join(self.home, "suite.lock")
        self.env.pop(suitequeue.HELD_ENV, None)
        self.counter = os.path.join(self.home, "count")
        self.set_config(test_command="echo x >> '%s'" % self.counter)

    def runs(self):
        return len(self.read(self.counter).split()) if os.path.exists(self.counter) else 0

    def test_a_green_tree_is_not_run_twice_and_rerun_runs_it(self):
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 1)
        again = self.cli("suite", expect=0).stdout
        self.assertEqual(self.runs(), 1)
        self.assertIn("this exact tree already ran green at", again)
        self.assertNotIn("machine load average", again)
        self.cli("suite", "--rerun", expect=0)
        self.assertEqual(self.runs(), 2)

    def test_a_red_run_is_not_remembered(self):
        self.set_config(test_command="echo x >> '%s'; exit 1" % self.counter)
        self.cli("suite", expect=1)
        self.cli("suite", expect=1)
        self.assertEqual(self.runs(), 2)

    def test_a_dirty_tree_neither_reads_nor_writes_the_cache(self):
        self.cli("suite", expect=0)                                         # clean: recorded
        path = os.path.join(self.project, "tests", "test_t.py")
        self.write("tests/test_t.py", "# edited\n" + self.read(path))
        self.cli("suite", expect=0)
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 3)                                   # the dirty runs ran every time
        self.git("checkout", "--", "tests/test_t.py")
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 3)                                   # the clean tree is still the recorded one

    def test_a_run_that_changes_the_tree_is_not_recorded(self):
        self.set_config(test_command="echo x >> '%s'; echo junk >> tests/test_t.py" % self.counter)
        self.cli("suite", expect=0)
        self.git("checkout", "--", "tests/test_t.py")
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 2)

    def test_another_command_or_another_tree_runs(self):
        self.cli("suite", expect=0)
        self.set_config(test_command="echo y >> '%s'" % self.counter)
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 2)
        self.write("lib.py", "x = 1\n")
        self.commit("a new tree")
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 3)

    def test_an_empty_commit_keeps_the_tree_so_the_result_stands(self):
        self.cli("suite", expect=0)
        self.git("commit", "-q", "--allow-empty", "-m", "nothing changed")
        self.assertIn("already ran green", self.cli("suite", expect=0).stdout)
        self.assertEqual(self.runs(), 1)

    def test_off_switch(self):
        self.env["BUDGIT_SUITE_CACHE"] = "off"
        self.cli("suite", expect=0)
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 2)

    def test_main_check_reuses_what_suite_proved_and_suite_what_main_check_proved(self):
        self.cli("suite", expect=0)
        out = self.cli("main-check", expect=0).stdout
        self.assertEqual(self.runs(), 1)
        self.assertIn("already ran green", out)
        self.assertEqual(json.loads(self.cli("main-check", "--status", "--json", expect=0).stdout)["state"], "green")
        self.write("lib.py", "x = 1\n")
        self.commit("next")
        self.cli("main-check", expect=0)
        self.assertEqual(self.runs(), 2)
        self.cli("suite", expect=0)
        self.assertEqual(self.runs(), 2)

    def test_a_commit_with_a_known_green_tree_gets_its_own_green_result(self):
        self.cli("suite", expect=0)                                         # the tested tree
        self.git("commit", "-q", "--allow-empty", "-m", "merge-like")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        out = self.cli("main-check", expect=0).stdout
        self.assertEqual(self.runs(), 1)
        self.assertIn("already ran green", out)
        data = json.loads(self.cli("main-check", "--status", "--json", expect=0).stdout)
        self.assertEqual(data["sha"], sha)
        self.assertEqual(data["state"], "green")

    def test_main_check_rerun_runs_even_when_the_tree_is_known(self):
        self.cli("main-check", expect=0)
        self.cli("main-check", "--rerun", expect=0)
        self.assertEqual(self.runs(), 2)

    def test_a_red_run_on_a_recorded_tree_takes_the_record_back(self):
        self.cli("suite", expect=0)
        self.set_config(test_command="echo x >> '%s'; exit 1" % self.counter)
        self.cli("main-check", "--rerun", expect=3)                       # a different command: its own key, nothing to evict
        self.set_config(test_command="echo x >> '%s'" % self.counter)
        self.cli("suite", "--rerun", expect=0)
        self.set_config(test_command="echo x >> '%s'; test ! -e '%s'" % (self.counter, os.path.join(self.home, "flaky")))
        self.cli("suite", expect=0)                                         # records this command's key
        open(os.path.join(self.home, "flaky"), "w").close()
        self.cli("main-check", "--rerun", expect=3)                       # red on the same tree and command: the record goes
        self.git("commit", "-q", "--allow-empty", "-m", "same tree again")
        before = self.runs()
        self.cli("main-check", expect=3)
        self.assertEqual(self.runs(), before + 1)                           # it ran, because nothing vouched for the tree any more

    def test_a_reused_main_check_says_so_without_announcing_a_run_or_zero_tests(self):
        self.cli("suite", expect=0)
        out = self.cli("main-check", expect=0).stdout
        self.assertNotIn("running the full suite", out)
        self.assertNotIn("0 tests", out)
        self.assertIn("already ran green", out)
        fresh = self.cli("main-check", "--rerun", expect=0).stdout
        self.assertIn("running the full suite", fresh)

    def test_the_suite_runs_at_low_priority_by_default(self):
        out = os.path.join(self.home, "nice")
        self.set_config(test_command="%s -c \"import os; open('%s','w').write(str(os.getpriority(os.PRIO_PROCESS, 0)))\"" % (sys.executable, out))
        self.cli("suite", expect=0)
        self.assertEqual(int(self.read(out)), min(os.getpriority(os.PRIO_PROCESS, 0) + suitequeue.NICE, PRIO_MAX))


if __name__ == "__main__":
    unittest.main()
