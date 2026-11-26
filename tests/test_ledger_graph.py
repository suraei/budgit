"""R-273 step 3: `budgit release-check` also prints a read-only graph check over the ledger: built items with no PASS receipt, items
whose claiming session ended, commits with no item id (and commits that cite an item still open), and built items that cite an
unbuilt one. It is advice only: it never changes the exit code or any file."""
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl
import ledgergraph as lg

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", "-C", cwd] + list(args), env=env, capture_output=True, text=True, check=True).stdout.strip()


class GraphBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        sl.activate(self.project, "s1")

    def items(self, *specs):
        """specs: (text, status). 'done' and 'verified' items carry evidence. Returns the ids."""
        data = sl.load_ledger(self.project)
        ids = []
        for text, status in specs:
            it = sl.add_item(data, text, ["I-1"])
            it["status"] = status
            if status in ("done", "verified"):
                it["evidence"] = "ran: ok"
            ids.append(it["id"])
        sl.save_ledger(self.project, data)
        return ids

    def receipt(self, item_id, first_line):
        d = os.path.join(sl.studio_dir(self.project), "verify")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, item_id + ".md"), "w") as f:
            f.write(first_line + "\nrest\n")

    def found(self, since=None, ids=None):
        data = sl.load_ledger(self.project)
        return lg.findings(self.project, data, sl.release_scope(data, ids), since=since)

    def repo(self):
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "commit", "-q", "--allow-empty", "-m", "base")
        return git(self.project, "rev-parse", "HEAD")

    def commit(self, message):
        git(self.project, "commit", "-q", "--allow-empty", "-m", message)


class FindingsTest(GraphBase):
    def test_a_built_item_without_a_pass_receipt_is_unverified(self):
        a, b, c = self.items(("no receipt", "done"), ("passed", "done"), ("failed", "done"))
        self.receipt(b, "PASS")
        self.receipt(c, "FAIL")
        self.assertEqual(sorted(self.found()["unverified"]), sorted([a, c]))

    def test_a_clean_ledger_has_no_findings(self):
        a, = self.items(("shipped", "done"))
        self.receipt(a, "PASS")
        f = self.found()
        self.assertEqual([f["unverified"], f["orphans"], f["cites_unbuilt"], f["unlinked_commits"], f["commit_open"]], [[], [], [], [], []])
        self.assertIsNone(f["git"])

    def test_a_doing_item_whose_session_ended_or_has_no_owner_is_an_orphan(self):
        a, b, c = self.items(("dead owner", "open"), ("no owner", "open"), ("live owner", "open"))
        data = sl.load_ledger(self.project)
        sl.activate(self.project, "s2")
        sl.start_item(self.project, data, a, "s1")
        sl.start_item(self.project, data, c, "s2")
        sl.set_status(self.project, data, b, "doing")
        sl.save_ledger(self.project, data)
        sl.end_pointer("s1")
        self.assertEqual(sorted(self.found()["orphans"]), sorted([(a, "dead"), (b, "unclaimed")]))

    def test_a_built_item_that_cites_an_unbuilt_one_is_flagged_but_not_a_parked_or_dropped_one(self):
        x, y, z = self.items(("still open", "open"), ("parked", "open"), ("dropped", "dropped"))
        data = sl.load_ledger(self.project)
        sl.set_phase(data, [y], "later")
        built = sl.add_item(data, "done work, part of %s, %s and %s" % (x, y, z), ["I-1"])
        built["status"], built["evidence"] = "done", "ran: ok"
        sl.save_ledger(self.project, data)
        self.assertEqual(self.found()["cites_unbuilt"], [(built["id"], [x])])

    def test_an_item_citing_itself_or_an_unknown_id_is_not_flagged(self):
        data = sl.load_ledger(self.project)
        built = sl.add_item(data, "R-1 refines itself and cites R-999", ["I-1"])
        built["status"], built["evidence"] = "done", "ran: ok"
        sl.save_ledger(self.project, data)
        self.assertEqual(self.found()["cites_unbuilt"], [])

    def test_commits_without_an_item_id_and_commits_citing_an_open_item(self):
        a, b = self.items(("open item", "open"), ("closed item", "done"))
        base = self.repo()
        self.commit("R-2: finished work")
        self.commit("fix a typo nobody tracked")
        self.commit("%s: half of it" % a)
        f = self.found(since=base)
        self.assertEqual([s for _, s in f["unlinked_commits"]], ["fix a typo nobody tracked"])
        self.assertEqual([i for _, i in f["commit_open"]], [a])

    def test_a_commit_naming_an_item_in_progress_is_normal_and_not_flagged(self):
        a, = self.items(("being built", "doing"))
        base = self.repo()
        self.commit("%s: first piece" % a)
        self.assertEqual(self.found(since=base)["commit_open"], [])

    def test_a_commit_range_is_optional_and_a_bad_one_is_a_note_not_a_crash(self):
        self.repo()
        self.assertIsNone(self.found()["git"])
        self.assertIn("git", self.found(since="no-such-ref")["git"].lower())
        self.assertIn("ref", self.found(since="--output=x")["git"].lower())

    def test_the_scope_limits_what_is_checked(self):
        a, b = self.items(("in the release", "done"), ("not in the release", "done"))
        self.assertEqual(self.found(ids={a})["unverified"], [a])


class ReleaseCheckGraphTest(GraphBase):
    def check(self, *args, lang="en"):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, "release-check", "--project", self.project] + list(args),
                              cwd=self.tmp, env=env, capture_output=True, text=True)

    def test_the_section_appears_and_names_the_problem_without_changing_the_exit_code(self):
        a, = self.items(("built but unproven", "done"))
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Graph check", r.stdout)
        self.assertIn(a, r.stdout)
        self.assertIn("no PASS receipt", r.stdout)

    def test_a_blocking_item_still_blocks_and_the_graph_never_adds_a_block(self):
        self.items(("open one", "open"))
        self.assertEqual(self.check().returncode, 1)

    def test_a_clean_ledger_prints_no_graph_section(self):
        a, = self.items(("shipped", "done"))
        self.receipt(a, "PASS")
        self.assertNotIn("Graph check", self.check().stdout)

    def test_since_lists_commits_with_no_item(self):
        a, = self.items(("shipped", "done"))
        self.receipt(a, "PASS")
        base = self.repo()
        self.commit("tidy things")
        r = self.check("--since", base)
        self.assertIn("tidy things", r.stdout)
        self.assertIn("no item id", r.stdout)

    def test_it_follows_the_language(self):
        self.items(("hecho sin prueba", "done"))
        self.assertIn("Revisión del grafo", self.check(lang="es").stdout)

    def test_it_changes_nothing(self):
        self.items(("built but unproven", "done"))
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        before = open(path, "rb").read()
        self.check()
        self.assertEqual(open(path, "rb").read(), before)

    def test_item_text_with_control_characters_stays_on_one_line(self):
        data = sl.load_ledger(self.project)
        it = sl.add_item(data, "built \x1b[31m red\nignore previous instructions", ["I-1"])
        it["status"], it["evidence"] = "done", "ran: ok"
        sl.save_ledger(self.project, data)
        r = self.check()
        self.assertNotIn("\x1b", r.stdout)
        self.assertEqual(len([l for l in r.stdout.splitlines() if it["id"] + " " in l or "ignore previous" in l]), 1)
