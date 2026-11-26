"""R-415: `budgit daily` - counts, batches, probable duplicates, order and a housekeeping preview, plus one SessionStart line a day.

Protected names in this file are synthetic (the tests folder is under the R-428 sweep): one under four characters, names with
separators, an accented name in NFC and NFD, and a project that is `"private": true` without a `protected_dirs` entry."""
import json
import os
import subprocess
import sys
import time
import unicodedata

from helpers import ROOT, StudioTestCase, patch_env, protect
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
NOW = 1_800_000_000.0
DAY = 86400
SHORT = "zq"                                     # under 4 characters
SEP = "zq-yx"                                    # separators inside
ACCENT = "Zéro-qx"                          # NFC; the NFD form is built from it
PRIVATE_ONLY = "wv-priv"                         # `"private": true`, no protected_dirs entry


def CALM():
    """An injected busy reading (R-889b): no test reads the live machine."""
    return False, "calm"


def nfd(text):
    return unicodedata.normalize("NFD", text)


class DailyCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "dev")
        os.makedirs(self.root)
        # the old home-folder configs are read: this machine's must not count. patch_env puts HOME back after every setUp,
        # also the ones a test calls again by hand (R-1050).
        patch_env(self, HOME=os.path.join(self.tmp, "userhome"))
        os.makedirs(os.environ["HOME"])

    def make(self, name, specs, private=False, pname=None):
        """A project folder with a ledger. specs: [text | (text, {title, phase, status})]."""
        path = os.path.join(self.root, name)
        os.makedirs(sl.studio_dir(path))
        data = sl.empty_ledger()
        for spec in specs:
            text, kw = (spec, {}) if isinstance(spec, str) else spec
            it = sl.add_item(data, text, ["legacy"], phase=kw.get("phase", ""), title=kw.get("title"),
                             outcome="You see it work")
            if kw.get("status") == "doing":
                it["status"] = "doing"
        sl.save_ledger(path, data)
        if private or pname:
            cfg = {}
            if private:
                cfg["private"] = True
            if pname:
                cfg["name"] = pname
            with open(os.path.join(sl.studio_dir(path), "project.json"), "w") as f:
                json.dump(cfg, f)
        return path

    def cli(self, *args, project=None, lang="en"):
        env = dict(os.environ, STUDIO_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        cmd = [sys.executable, CLI, *args] + (["--project", project] if project else [])
        return subprocess.run(cmd, env=env, capture_output=True, text=True)

    def hook(self, project, source="startup"):
        env = dict(os.environ, STUDIO_HOME=self.home, CLAUDE_PROJECT_DIR=project, BUDGIT_LANG="en")
        p = subprocess.run([sys.executable, HOOK, "session-start"], input=json.dumps(
            {"session_id": "s-daily0001", "cwd": project, "source": source}), env=env, capture_output=True, text=True, cwd=project)
        return p.stdout + p.stderr

    def everything(self, *outputs):
        """All text a run produced: stdout, stderr and the two files it may leave in the Budgit home (its cache and the
        trigger state; the portfolio list is the user's own and is not an output)."""
        text = "\n".join(outputs)
        for name in ("daily.json", "triggers.json"):
            try:
                text += "\n" + open(os.path.join(self.home, name), encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                pass
        return text


def spelled(name):
    """The ways a name can be written in a text: case, separators and the NFD form."""
    parts = [p for p in name.replace("_", "-").split("-") if p]
    forms = {name, name.upper(), name.lower(), "_".join(parts), " ".join(parts), "".join(parts), ".".join(parts).upper()}
    return forms | {nfd(f) for f in forms}


def leaks(text, name):
    import leak
    return leak.hide(text, [name]) != text


# ---------------------------------------------------------------- leak.hide
class HideTest(DailyCase):
    def test_every_spelling_of_a_name_is_hidden(self):
        import leak
        for name in (SHORT, SEP, ACCENT, nfd(ACCENT)):
            for form in spelled(name):
                shown = leak.hide("about %s here" % form, [name])
                self.assertNotIn(unicodedata.normalize("NFC", form).casefold(), unicodedata.normalize("NFC", shown).casefold(),
                                 (name, form, shown))
                self.assertIn("about", shown)

    def test_a_short_name_is_only_a_whole_word(self):
        import leak
        self.assertEqual(leak.hide("zqx and xzq and ab2", [SHORT, "ab"]), "zqx and xzq and ab2")      # under 4 characters: whole words only
        self.assertEqual(leak.hide("table and cab", ["ab"]), "table and cab")

    def test_a_longer_name_is_hidden_inside_a_longer_word(self):
        import leak
        for text in ("QzVkClient.py", "myqzvk.py", "qz-vk2.py", "xqz-vk", "qz_vks"):
            shown = leak.hide(text, ["qz-vk"])
            self.assertNotIn("qzvk", shown.casefold().replace("-", "").replace("_", ""), (text, shown))
            self.assertIn("<hidden>", shown)

    def test_zero_width_characters_do_not_hide_a_name(self):
        import leak
        self.assertNotIn("yx", leak.hide("zq\u200b-y\u200bx!", [SEP]))

    def test_no_names_or_no_text_changes_nothing(self):
        import leak
        self.assertEqual(leak.hide("zq zq", []), "zq zq")
        self.assertEqual(leak.hide("", [SHORT]), "")
        self.assertEqual(leak.hide(None, [SHORT]), "")

    def test_names_that_overlap_leave_no_remnant(self):
        import leak
        shown = leak.hide("xabcdefgx and bcdefgabcd", ["abcd", "bcdefg"])
        self.assertNotIn("efg", shown)
        self.assertNotIn("abcd", shown)

    def test_invisible_fillers_do_not_hide_a_name(self):
        import leak
        for filler in ("\u3164", "\ufe0f", "\u2800", "\u034f", "\u115f", "\uffa0", "\u200b", "\u00ad"):
            shown = leak.hide("see q%szvk now" % filler, ["qzvk"])
            self.assertIn("<hidden>", shown, repr(filler))
            self.assertNotIn("zvk", shown, repr(filler))

    def test_private_names_add_the_listed_alias_the_legacy_config_and_the_children_of_a_container(self):
        import leak
        import portfolio
        held = self.make("held-plain", ["x"])
        protect(self.home, held)
        portfolio.add(held, name="Acme Portal Zz")                            # the alias the owner gave a protected project
        container = os.path.join(self.tmp, "box")
        child = os.path.join(container, "child-qq")
        os.makedirs(sl.studio_dir(child))
        sl.save_ledger(child, sl.empty_ledger())
        os.makedirs(os.path.join(container, "plain-docs"))                         # not a project: no name
        legacy = os.path.join(self.tmp, "elsewhere", "legacy-held")
        os.makedirs(legacy)
        os.makedirs(os.path.join(os.environ["HOME"], ".budgit"))
        with open(os.path.join(os.environ["HOME"], ".budgit", "config.json"), "w") as f:
            json.dump({"protected_dirs": [legacy]}, f)
        protect(self.home, held, container)
        names = {n.casefold() for n in leak.private_names()}
        self.assertTrue({"acme portal zz", "legacy-held", "child-qq"} <= names, names)
        self.assertNotIn("plain-docs", names)

    def test_the_project_json_name_of_an_unlisted_protected_project_is_a_name(self):
        import leak
        held = self.make("folder-one", ["x"], pname="Aardwolf Abcxyz")           # a protected_dirs entry, in no list
        container = os.path.join(self.tmp, "box2")
        child = os.path.join(container, "folder-two")
        os.makedirs(sl.studio_dir(child))
        sl.save_ledger(child, sl.empty_ledger())
        with open(os.path.join(sl.studio_dir(child), "project.json"), "w") as f:
            json.dump({"name": "Bonobo Defuvw"}, f)
        protect(self.home, held, container)
        names = {n.casefold() for n in leak.private_names()}
        self.assertTrue({"aardwolf abcxyz", "bonobo defuvw"} <= names, names)

    def test_a_single_word_name_is_hidden_when_the_text_splits_it(self):
        import leak
        for text in ("my-Qzvk_Portal-copy", "qzvk.portal", "Q-z-v-k-p-o-r-t-a-l"):
            self.assertIn("<hidden>", leak.hide(text, ["Qzvkportal"]), text)
        self.assertEqual(leak.hide("table and cab", ["ab"]), "table and cab")

    def test_the_children_of_a_protected_project_that_has_a_ledger_are_not_names(self):
        import leak
        held = self.make("held-two", ["x"])
        os.makedirs(os.path.join(held, "tests"))
        protect(self.home, held)
        names = {n.casefold() for n in leak.private_names()}
        self.assertIn("held-two", names)
        self.assertNotIn("tests", names)

    def test_private_names_cover_every_kind_of_protected_project(self):
        import leak
        import portfolio
        a = self.make("held-one", ["x"])                                    # under a protected_dirs entry
        b = self.make(PRIVATE_ONLY, ["x"], private=True)                    # `"private": true` only
        c = self.make("plain", ["x"], private=True, pname="Ledger-Name Zz")  # the ledger's own name too
        protect(self.home, a)
        for path in (a, b, c):
            portfolio.add(path)
        names = {n.casefold() for n in leak.private_names()}
        self.assertTrue({"held-one", PRIVATE_ONLY, "plain", "ledger-name zz"} <= names, names)
        d = self.make("open-one", ["x"])
        portfolio.add(d)
        self.assertNotIn("open-one", {n.casefold() for n in leak.private_names()})

    def test_a_generic_protected_folder_does_not_hide_every_word(self):
        import leak
        protect(self.home, os.path.join(self.tmp, "Dev"))
        self.assertNotIn("dev", {n.casefold() for n in leak.private_names()})


# ---------------------------------------------------------------- the pure parts
class ClassifyTest(DailyCase):
    def test_explicit_marker_wins_over_keywords(self):
        import daily
        self.assertEqual(daily.class_mark({"text": "Class B (process): the bug list is long"}), "B")
        self.assertEqual(daily.class_mark({"text": "Class A: tidy the docs"}), "A")
        self.assertEqual(daily.class_mark({"text": "class a and later Class B"}), "A")

    def test_keywords_without_a_marker_look_like_class_a(self):
        import daily
        for text in ("a bug in the bar", "the build is broken", "arranca lento", "el bot no funciona", "performance of the feed",
                     "app crashes on start", "el login esta roto"):
            self.assertEqual(daily.class_mark({"text": text}), "A?", text)

    def test_a_word_that_only_contains_a_keyword_is_class_b(self):
        import daily
        self.assertEqual(daily.class_mark({"text": "debug the cache and rename the slowly-named file"}), "B")

    def test_notes_do_not_count(self):
        import daily
        self.assertEqual(daily.class_mark({"text": "plain work", "notes": "this quotes Class A and a bug"}), "B")


class BatchTest(DailyCase):
    def items(self, *texts):
        data = sl.empty_ledger()
        for text in texts:
            sl.add_item(data, text, ["legacy"])
        return data["items"]

    def test_two_shared_words_make_a_batch(self):
        import daily
        its = self.items("improve the status line colours", "status line width on narrow screens", "unrelated hook rename")
        found = daily.batches(its)
        self.assertEqual([b["ids"] for b in found], [["R-1", "R-2"]])
        self.assertTrue({"status", "line"} <= set(found[0]["words"]))

    def test_one_shared_word_is_not_enough(self):
        import daily
        self.assertEqual(daily.batches(self.items("fix the status colours", "status of the hooks rename")), [])

    def test_a_shared_file_name_is_enough(self):
        import daily
        its = self.items("change the colours in plugin/scripts/studiolib.py", "add a guard to studiolib.py", "other thing")
        found = daily.batches(its)
        self.assertEqual([b["ids"] for b in found], [["R-1", "R-2"]])
        self.assertIn("studiolib.py", found[0]["files"])
        self.assertNotIn("/", json.dumps(found))                                    # a basename, never a path

    def test_a_word_in_most_items_names_no_batch(self):
        import daily
        its = self.items(*["budgit item number%d about thing%d" % (n, n * 7) for n in range(1, 13)])
        self.assertEqual(daily.batches(its), [])                                    # "budgit item" is in all twelve


class DuplicateTest(DailyCase):
    def items(self, *specs):
        data = sl.empty_ledger()
        for text, title in specs:
            sl.add_item(data, text, ["legacy"], title=title)
        return data["items"]

    def test_near_identical_titles_are_a_pair(self):
        import daily
        its = self.items(("a", "Colour the status line by load"), ("b", "Colour the status line by the load"), ("c", "Rename the hook"))
        self.assertEqual(daily.duplicates(its), [("R-1", "R-2")])

    def test_different_titles_are_not(self):
        import daily
        its = self.items(("a", "Colour the status line"), ("b", "Colour the sweep report output"))
        self.assertEqual(daily.duplicates(its), [])

    def test_titleless_items_match_by_their_text(self):
        import daily
        its = self.items(("make the portfolio list sort by priority first", None),
                         ("make the portfolio list sort by priority first please", None))
        self.assertEqual(daily.duplicates(its), [("R-1", "R-2")])

    def test_an_explicit_note_to_an_open_item_is_a_pair(self):
        import daily
        its = self.items(("Alpha beta gamma", None), ("covered by R-1, nothing more here", None), ("superseded by R-9", None))
        self.assertEqual(daily.duplicates(its), [("R-1", "R-2")])           # R-9 does not exist: no pair

    def test_a_closed_target_is_not_a_pair(self):
        import daily
        its = self.items(("Alpha beta gamma", None), ("duplicate of R-1", None))
        its[0]["status"] = "done"
        self.assertEqual(daily.duplicates([i for i in its if i["status"] in ("open", "doing")], all_items=its), [])


# ---------------------------------------------------------------- the report
class CollectTest(DailyCase):
    def test_counts_batches_duplicates_and_order_for_the_current_project(self):
        import daily
        path = self.make("proj", [
            ("fix the status line colours on narrow screens", {"phase": "v0.14", "title": "Status line colours"}),
            ("status line width bug on narrow screens", {"phase": "v0.14", "title": "Status line width"}),
            ("Class B (process): write the bug policy", {"phase": "v0.14"}),
            ("Colour the status line by load", {"phase": "v0.15", "title": "Colour the status line by load"}),
            ("Colour the status line by the load", {"phase": "v0.15", "title": "Colour the status line by the load"}),
            ("parked idea about hooks", {"phase": "later"})])
        out = daily.collect(path, now=time.time(), git=False)
        (row,) = out["projects"]
        self.assertEqual((row["counts"]["pending"], row["counts"]["next"], row["counts"]["later"]), (6, 3, 1))
        self.assertEqual([b["ids"] for b in row["batches"]], [["R-1", "R-2"]])
        self.assertEqual(row["duplicates"], [["R-4", "R-5"]])
        # R-1333: the order is the one order of the whole ledger (the same rows as `ledger top`), whatever the phase; class A first
        self.assertEqual([{"id": o["id"], "class": o["class"]} for o in row["order"][:2]],
                         [{"id": "R-2", "class": "A?"}, {"id": "R-1", "class": "B"}])
        self.assertIn("R-3", [o["id"] for o in row["order"]])
        self.assertEqual(row["order"][0]["class_source"], "inferred")             # R-2 is class A by its words only
        self.assertEqual(row["clean"], "not checked")
        self.assertTrue(row["rule"])                                         # six came in, none went out

    def test_without_a_numeric_phase_every_committed_item_counts(self):
        import daily
        path = self.make("proj", ["status line colours", "status line width", ("parked", {"phase": "later"}),
                                  ("mvp work", {"phase": "mvp"})])
        (row,) = daily.collect(path, git=False)["projects"]
        ids = [i["id"] for i in row["order"]]
        self.assertEqual(ids[:3], ["R-1", "R-2", "R-4"])               # the committed items first, as before
        self.assertLessEqual(len(ids), 4)                              # R-1333: the same rows as `ledger top`; a parked item may be in the head too

    def test_the_rule_is_off_when_more_went_out_than_came_in(self):
        import daily
        path = self.make("proj", ["one", "two"])
        data = sl.load_ledger(path)
        sl.set_status(path, data, "R-1", "done", evidence="ok")
        sl.set_status(path, data, "R-2", "done", evidence="ok")
        sl.save_ledger(path, data)
        (row,) = daily.collect(path, git=False)["projects"]
        self.assertFalse(row["rule"])

    def test_inside_a_class_the_one_order_breaks_a_tie_by_id(self):         # R-1333: was «the ledger order is kept»; the one order ties by id
        import daily
        data = sl.empty_ledger()
        for n in (30, 12, 20):
            sl.add_item(data, "plain work %d" % n, ["legacy"], item_id="R-%d" % n)
        path = self.make("proj", [])
        sl.save_ledger(path, data)
        (row,) = daily.collect(path, git=False)["projects"]
        self.assertEqual([o["id"] for o in row["order"]], ["R-12", "R-20", "R-30"])

    def test_the_ledger_is_left_exactly_as_it_was(self):
        import daily
        path = self.make("proj", ["status line colours", "status line width"])
        before = open(os.path.join(sl.studio_dir(path), "ledger.json"), "rb").read()
        daily.collect(path, git=False)
        self.assertEqual(open(os.path.join(sl.studio_dir(path), "ledger.json"), "rb").read(), before)

    def test_json_matches_the_schema(self):
        path = self.make("proj", ["status line colours", "status line width"], pname=None)
        out = self.cli("daily", "--json", project=path)
        self.assertEqual(out.returncode, 0, out.stderr)
        doc = json.loads(out.stdout)
        self.assertEqual(list(doc), ["projects"])
        (row,) = doc["projects"]
        self.assertEqual(set(row), {"id", "protected", "counts", "rule", "batches", "duplicates", "order", "order_line", "clean"})      # R-1333: the daily facts line of the one order
        self.assertEqual(set(row["counts"]), {"made", "shut", "pending", "next", "later"})
        self.assertTrue(all(isinstance(v, int) for v in row["counts"].values()))
        self.assertIsInstance(row["rule"], bool)
        self.assertTrue(all(set(b) == {"ids", "area"} for b in row["batches"]))
        self.assertTrue(all(len(p) == 2 for p in row["duplicates"]))
        self.assertTrue(all(set(o) <= {"id", "class", "class_source", "why", "pinned"} and {"id", "class", "class_source"} <= set(o) and o["class"] in ("A", "A?", "B")      # R-1333: «why» is the porque line
                            and o["class_source"] in ("flagged", "inferred") for o in row["order"]))
        self.assertTrue(row["clean"] == "not checked" or set(row["clean"]) == {"remove", "delete"})

    def test_five_hundred_items_take_under_a_second(self):
        import daily
        words = ["alpha", "beta", "gamma", "delta", "omega", "sigma", "kappa", "lambda", "theta", "zeta"]
        specs = [("%s %s %s thing number%d" % (words[n % 10], words[(n * 3) % 10], words[(n * 7) % 10], n),
                  {"phase": "v0.14", "title": "%s item %d" % (words[n % 10], n)}) for n in range(500)]
        path = self.make("big", specs)
        t0 = time.process_time()                                         # CPU time, not wall (R-1282)
        (row,) = daily.collect(path, git=False)["projects"]
        self.assertLess(time.process_time() - t0, 1.0)
        self.assertEqual(row["counts"]["pending"], 500)


class CleanPreviewTest(DailyCase):
    def repo(self):
        path = self.make("repo", ["status line colours", "status line width"])
        subprocess.run(["git", "-C", path, "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"],
                       check=True)
        return path

    def test_unreadable_live_sessions_make_the_preview_unknown_not_zero(self):
        import daily
        import housekeeping
        path = self.repo()
        real = housekeeping.live_cwds
        housekeeping.live_cwds = lambda: None
        self.addCleanup(lambda: setattr(housekeeping, "live_cwds", real))
        (row,) = daily.collect(path, now=time.time(), git=True, busy=CALM)["projects"]
        self.assertEqual(row["clean"], "not checked")

    def test_a_clean_repo_says_zero_and_writes_the_cache(self):
        import daily
        path = self.repo()
        (row,) = daily.collect(path, now=time.time(), git=True, busy=CALM)["projects"]
        self.assertEqual(row["clean"], {"remove": 0, "delete": 0})
        cache = json.load(open(os.path.join(self.home, "daily.json")))
        self.assertEqual(cache["day"], time.strftime("%Y-%m-%d"))
        self.assertEqual(list(cache["clean"].values()), [{"remove": 0, "delete": 0}])
        self.assertNotIn("repo", json.dumps(cache))                                  # no name, no path: a hash and two numbers

    def test_a_busy_machine_says_not_checked_never_zero(self):
        import daily
        path = self.repo()
        (row,) = daily.collect(path, git=True, busy=lambda: (True, "memory pressure warn"))["projects"]
        self.assertEqual(row["clean"], "not checked")

    def test_r889b_without_an_injected_reading_daily_uses_housekeeping_busy_now_with_a_short_window(self):
        import daily
        import housekeeping
        path = self.repo()
        seen = []
        real = housekeeping.busy_now
        housekeeping.busy_now = lambda window=None: seen.append(window) or (True, "x")
        self.addCleanup(setattr, housekeeping, "busy_now", real)
        (row,) = daily.collect(path, git=True)["projects"]
        self.assertEqual((seen, row["clean"]), ([housekeeping.DAILY_BUSY_WINDOW], "not checked"))
        self.assertEqual(housekeeping.DAILY_BUSY_WINDOW, 1)

    def test_git_past_its_deadline_says_not_checked(self):
        import daily
        import housekeeping
        path = self.repo()
        real = housekeeping.git

        def slow(p, *a):
            time.sleep(0.05)
            return real(p, *a)
        old = (housekeeping.git, daily.GIT_BUDGET_S)
        housekeeping.git, daily.GIT_BUDGET_S = slow, 0.01
        try:
            (row,) = daily.collect(path, git=True, busy=CALM)["projects"]
        finally:
            housekeeping.git, daily.GIT_BUDGET_S = old
        self.assertEqual(row["clean"], "not checked")

    def test_a_folder_that_is_not_a_repo_says_not_checked(self):
        import daily
        path = self.make("plain", ["one"])
        (row,) = daily.collect(path, git=True, busy=CALM)["projects"]
        self.assertEqual(row["clean"], "not checked")


# ---------------------------------------------------------------- protected projects and masking (the gate)
class MaskingTest(DailyCase):
    def run_all(self, *paths):
        import portfolio
        for p in paths:
            portfolio.add(p)
        txt = self.cli("daily", "--all")
        js = self.cli("daily", "--all", "--json")
        return txt, js

    def test_a_protected_project_shows_counts_only_and_its_ledger_stays_closed(self):
        import daily
        secret = "zq-yx"
        path = self.make(secret, [("Plan the %s launch" % secret, {"title": "Secret %s title" % secret, "phase": "v0.14"}),
                                  "second item", ("parked", {"phase": "later"})], private=True)
        real = sl.load_ledger

        def refuse(project, *a, **k):
            if os.path.realpath(project) == os.path.realpath(path):
                raise AssertionError("the protected ledger was opened")
            return real(project, *a, **k)
        sl.load_ledger = refuse
        try:
            out = daily.collect(path, git=True, busy=CALM)
        finally:
            sl.load_ledger = real
        (row,) = out["projects"]
        self.assertTrue(row["protected"])
        self.assertTrue(row["id"].startswith("private~"))
        self.assertEqual((row["batches"], row["duplicates"], row["order"]), ([], [], []))
        self.assertEqual((row["counts"]["made"], row["counts"]["shut"]), (None, None))
        self.assertEqual((row["counts"]["next"], row["counts"]["later"]), (2, 1))
        self.assertEqual(row["clean"], "not checked")
        text = daily.render(out, "en")
        self.assertIn("flow, batches and duplicates not computed", text)
        for needle in ("Secret", "Plan the", "launch", secret):
            self.assertNotIn(needle, text + json.dumps(daily.to_json(out)))

    def test_all_covers_a_protected_and_a_plain_project_without_a_leak(self):
        a = self.make("open-proj", ["status line colours", "status line width"])
        b = self.make("held-proj", ["secret thing held-proj"], private=True)
        protect(self.home, b)
        txt, js = self.run_all(a, b)
        self.assertEqual((txt.returncode, js.returncode), (0, 0), txt.stderr + js.stderr)
        doc = json.loads(js.stdout)
        rows = {r["id"]: r for r in doc["projects"]}
        self.assertEqual(len(rows), 2)
        mine = next(r for r in doc["projects"] if not r["protected"])
        self.assertTrue(all(i.startswith("open-proj/") for b_ in mine["batches"] for i in b_["ids"]))        # ids carry the label
        for text in (txt.stdout, js.stdout, txt.stderr, js.stderr):
            self.assertNotIn("held-proj", text)
            self.assertNotIn("secret thing", text)

    def test_the_current_project_is_protected_and_all_is_asked(self):
        a = self.make("open-proj", ["status line colours", "status line width"])
        b = self.make("held-proj", ["x"], private=True)
        import portfolio
        portfolio.add(a)
        portfolio.add(b)
        out = self.cli("daily", "--all", project=b)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("held-proj", self.everything(out.stdout, out.stderr))

    def test_a_protected_name_in_title_text_and_file_path_of_a_plain_item_never_leaves(self):
        import portfolio
        for name in (SHORT, SEP, ACCENT, nfd(ACCENT), PRIVATE_ONLY):
            self.setUp()
            held = self.make(name.replace(" ", "x"), ["held"], private=True)
            if name != PRIVATE_ONLY:
                protect(self.home, held)
            spell = " / ".join(sorted(spelled(name)))
            plain = self.make("plain-proj", [
                ("status line colours in plugin/%s/studiolib.py and tests/%s.py" % (name.lower(), "_".join(
                    p for p in name.lower().replace("-", " ").split())), {"title": "Colours near %s" % name, "phase": "v0.14"}),
                ("status line width in plugin/%s/studiolib.py: %s" % (name.upper(), spell), {"title": "Width near %s" % nfd(name),
                                                                                              "phase": "v0.14"}),
                ("Colour the line near %s" % name, {"title": "Colour near %s" % name}),
                ("Colour the line near %s too" % name, {"title": "Colour near %s too" % name})])
            portfolio.add(held)
            portfolio.add(plain)
            txt = self.cli("daily", "--all")
            js = self.cli("daily", "--all", "--json")
            one = self.cli("daily", project=plain)
            body = self.everything(txt.stdout, txt.stderr, js.stdout, js.stderr, one.stdout, one.stderr)
            for form in spelled(name):
                low = unicodedata.normalize("NFC", body).casefold()
                self.assertNotIn(unicodedata.normalize("NFC", form).casefold(), low, (name, form))
            self.assertFalse(leaks_in_parts(body, name), name)
            self.assertEqual(txt.returncode, 0, txt.stderr)

    def test_a_name_glued_to_other_characters_in_a_file_name_never_leaves(self):
        import portfolio
        held = self.make("qz-vk", ["held"], private=True)
        plain = self.make("plain-proj", ["client change in QzVkClient.py", "client guard in myqzvk.py and QzVkClient.py",
                                         "client tidy in qz-vk2.py"])
        portfolio.add(held)
        portfolio.add(plain)
        outs = [self.cli("daily", project=plain), self.cli("daily", "--json", project=plain), self.cli("daily", "--all"),
                self.cli("daily", "--all", "--json")]
        body = self.everything(*[o.stdout + o.stderr for o in outs])
        self.assertNotIn("qzvk", unicodedata.normalize("NFC", body).casefold().replace("-", "").replace("_", ""))

    def test_a_piece_of_a_protected_name_is_never_an_area_word(self):
        import daily
        import portfolio
        held = self.make("qz-vk", ["held"], private=True)
        plain = self.make("plain-proj", ["client change in qz-vk2.py layout", "client guard in qz-vk2.py layout"])
        portfolio.add(held)
        portfolio.add(plain)
        (row,) = daily.collect(plain, git=False)["projects"]
        area = json.dumps(row["batches"]).casefold()
        self.assertNotIn("vk2", area)
        self.assertNotIn("hidden", area)

    def test_an_unreadable_config_opens_no_ledger(self):
        import portfolio
        held = self.make("qzvk-main", ["SECRETWORD change in qzvk.py", "SECRETWORD guard in qzvk.py"])
        plain = self.make("plain-proj", ["one thing", "another thing"])
        portfolio.add(held)
        portfolio.add(plain)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            f.write('{"protected_dirs": ["%s"],}' % held)                    # a trailing comma: load_config reads nothing
        for args in (("daily", "--all"), ("daily", "--all", "--json"), ("daily",)):
            out = self.cli(*args, project=held)
            body = self.everything(out.stdout, out.stderr)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("SECRETWORD", body)
            self.assertNotIn("qzvk", body.casefold())
            self.assertNotIn("R-1", body)
            self.assertIn("config", body)
        import daily
        self.assertIsNone(daily.session_facts(held, now=NOW))
        self.assertIsNone(daily.session_facts(plain, now=NOW))

    def test_an_unreadable_project_json_counts_as_protected(self):
        import daily
        import portfolio
        held = self.make("qzvk-main", ["SECRETWORD change in qzvk.py", "SECRETWORD guard in qzvk.py"])
        with open(os.path.join(sl.studio_dir(held), "project.json"), "w") as f:
            f.write('{"private": true,}')                                     # a trailing comma
        portfolio.add(held)
        real = sl.load_ledger
        sl.load_ledger = lambda *a, **k: (_ for _ in ()).throw(AssertionError("the ledger was opened"))
        try:
            out = daily.collect(held, all_projects=True, git=False)
            self.assertIsNone(daily.session_facts(held, now=NOW))
        finally:
            sl.load_ledger = real
        (row,) = out["projects"]
        self.assertTrue(row["protected"])
        body = self.everything(self.cli("daily", "--all").stdout, self.cli("daily", "--all", "--json").stdout)
        self.assertNotIn("SECRETWORD", body)
        self.assertNotIn("qzvk", body.casefold())

    def test_a_piece_of_a_protected_name_is_never_a_file_in_the_area(self):
        import daily
        import portfolio
        held = self.make("qzvk-portal", ["held"], private=True)
        plain = self.make("plain-proj", ["fix qzvk.py layout", "fix qzvk.py colours", "fix portal.py colours"])
        portfolio.add(held)
        portfolio.add(plain)
        (row,) = daily.collect(plain, git=False)["projects"]
        self.assertNotIn("qzvk", json.dumps(row["batches"]).casefold())
        self.assertNotIn("portal.py", json.dumps(row["batches"]).casefold())

    def test_the_output_survives_a_lone_surrogate(self):
        import portfolio
        plain = self.make("plain-proj", ["bad text SURR here in a.py", "bad text SURR there in a.py"])
        ledger = os.path.join(sl.studio_dir(plain), "ledger.json")
        with open(ledger, encoding="utf-8") as f:
            raw = f.read()
        with open(ledger, "w", encoding="utf-8") as f:
            f.write(raw.replace("SURR", "\\ud800"))                                # a JSON escape of a lone surrogate
        portfolio.add(plain)
        out = self.cli("daily", project=plain)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("R-1", out.stdout)

    def test_a_long_unbroken_token_is_read_in_a_blink(self):
        import daily
        item = {"id": "R-1", "text": "x" * 60000 + " tail", "status": "open"}
        start = time.process_time()                                         # CPU time, not wall (R-1282)
        daily._files(item)
        self.assertLess(time.process_time() - start, 0.5)
        start = time.process_time()
        daily._files({"id": "R-2", "text": "-." * 400000})                  # megabytes of punctuation: only the first part is read
        self.assertLess(time.process_time() - start, 1.5)

    def test_the_names_never_serialise(self):
        import daily
        import portfolio
        held = self.make("qzvk-portal", ["held"], private=True)
        portfolio.add(held)
        out = daily.collect(held, all_projects=True, git=False)
        with self.assertRaises(TypeError):
            json.dumps(out)                                                   # the list of protected names cannot be dumped by mistake

    def test_a_protected_card_without_counts_says_no_data_never_zero(self):
        import daily
        import portfolio
        held = self.make("qz-vk", ["held one", "held two"], private=True)
        card_path = os.path.join(sl.studio_dir(held), "private", "card.json")
        self.assertTrue(os.path.exists(card_path), card_path)
        for body in ({"version": 1}, {"version": 1, "counts": {}}, {"version": 1, "counts": {"doing": 1}}):
            with open(card_path) as f:
                card = json.load(f)
            card.pop("counts", None)
            card.update(body)
            with open(card_path, "w") as f:
                json.dump(card, f)
            portfolio.add(held)
            out = daily.collect(held, all_projects=True, git=False)
            (row,) = out["projects"]
            self.assertTrue(row["protected"])
            self.assertEqual(row["counts"], {"made": None, "shut": None, "pending": None, "next": None, "later": None}, body)
            for lang in ("en", "es"):
                text = daily.render(out, lang)
                self.assertNotIn("0 next", text)
                self.assertNotIn("0 siguientes", text)
                self.assertIn("no data" if lang == "en" else "sin datos", text)

    def test_a_protected_card_with_counts_shows_them(self):
        import daily
        held = self.make("qz-vk", ["held one", "held two", ("parked", {"phase": "later"})], private=True)
        out = daily.collect(held, git=False)
        (row,) = out["projects"]
        self.assertEqual((row["counts"]["next"], row["counts"]["later"], row["counts"]["pending"]), (2, 1, 3))
        self.assertIn("2 next and 1 parked", daily.render(out, "en"))

    def test_an_unreadable_row_carries_null_counts(self):
        import daily
        import portfolio
        a = self.make("open-proj", ["one"])
        with open(os.path.join(sl.studio_dir(a), "ledger.json"), "w") as f:
            f.write('{"items": [ {"id": "R-1", ')
        gone = self.make("gone-proj", ["one"])
        portfolio.add(a)
        portfolio.add(gone)
        import shutil
        shutil.rmtree(gone)
        rows = {r["id"]: r for r in json.loads(self.cli("daily", "--all", "--json").stdout)["projects"]}
        self.assertEqual({r["error"] for r in rows.values()}, {"unreadable", "missing"})
        for row in rows.values():
            self.assertEqual(row["counts"], {"made": None, "shut": None, "pending": None, "next": None, "later": None})

    def test_filler_words_are_never_an_area(self):
        import daily
        items = [{"id": "R-1", "status": "open", "title": None, "text": "claude.md line rule both item auto"},
                 {"id": "R-2", "status": "open", "title": None, "text": "claude.md line rule both item auto, plus the cache"},
                 {"id": "R-3", "status": "open", "title": None, "text": "line rule both item auto thing task"}]
        (b,) = daily.batches(items)
        self.assertEqual(b["ids"], ["R-1", "R-2"])
        for word in ("rule", "both", "item", "auto", "thing", "task"):
            self.assertNotIn(word, b["words"])
        self.assertEqual(b["files"], ["claude.md"])

    def test_the_area_is_the_file_names_when_there_are_any(self):
        import daily
        path = self.make("proj", ["claude.md status line colours", "claude.md status line width", "hook rename tidy", "hook rename guard"])
        (row,) = daily.collect(path, git=False)["projects"]
        areas = {tuple(b["ids"]): b["area"] for b in row["batches"]}
        self.assertEqual(areas[("R-1", "R-2")], "claude.md")                      # no word beside a file name
        self.assertIn("hook", areas[("R-3", "R-4")])                              # no file: the words

    def test_an_unreadable_ledger_prints_a_code_never_its_text(self):
        import portfolio
        a = self.make("open-proj", ["one"])
        with open(os.path.join(sl.studio_dir(a), "ledger.json"), "w") as f:
            f.write('{"items": [ {"id": "R-1", SECRET-BODY-QQ')
        portfolio.add(a)
        out = self.cli("daily", "--all")
        self.assertEqual(out.returncode, 0)
        self.assertNotIn("SECRET-BODY-QQ", self.everything(out.stdout, out.stderr))


def leaks_in_parts(body, name):
    """True when the name still shows up in `body` in any separator spelling (the same test the gate uses)."""
    import leak
    return leak.hide(body, [name]) != body


# ---------------------------------------------------------------- the CLI
class CliTest(DailyCase):
    def test_text_report_in_english_and_spanish(self):
        path = self.make("proj", ["status line colours on narrow screens", "status line width bug on narrow screens"])
        en = self.cli("daily", project=path, lang="en")
        es = self.cli("daily", project=path, lang="es")
        self.assertEqual((en.returncode, es.returncode), (0, 0), en.stderr + es.stderr)
        self.assertIn("batch", en.stdout)
        self.assertIn("R-1", en.stdout)
        self.assertNotEqual(en.stdout, es.stdout)
        self.assertIn("lote", es.stdout)

    def test_outside_a_project_it_says_so_and_exits_zero(self):
        out = self.cli("daily", project=self.tmp)
        self.assertEqual(out.returncode, 0)

    def test_every_daily_key_exists_in_both_languages(self):
        import i18n
        keys = [k for k in i18n.CATALOG["en"] if k.startswith("daily.")]
        self.assertTrue(keys)
        for k in keys:
            self.assertIn(k, i18n.CATALOG["es"], k)

    def test_help_lists_the_command(self):
        out = self.cli("daily", "--help")
        self.assertIn("--all", out.stdout)
        self.assertIn("--json", out.stdout)

    def test_it_writes_nothing_but_its_own_cache(self):
        path = self.make("proj", ["status line colours", "status line width"])
        before = {os.path.join(b, n) for b, _d, fs in os.walk(self.home) for n in fs}
        self.cli("daily", project=path)
        after = {os.path.join(b, n) for b, _d, fs in os.walk(self.home) for n in fs}
        new = {os.path.basename(p) for p in after - before}
        self.assertLessEqual(new, {"daily.json", "portfolio.json"} | {os.path.basename(p) for p in before})


class ExistingOutputTest(DailyCase):
    """EARS 13: nothing that already exists changes."""

    def test_housekeeping_and_sweep_print_what_they_always_did(self):
        import housekeeping
        path = self.make("proj", ["one", "two"])
        report = {"projects": [{"name": "proj", "path": path, "flow": housekeeping.flow(path, time.time()), "flow_error": None,
                                "git": None, "done": None}], "left_alone": 0, "leftovers": None, "git_skipped": True,
                  "busy_reason": "median CPU 95 % is above 85 %", "applied": False, "hours": 24, "totals": {}}
        text = housekeeping.render(report, "en")
        self.assertIn("in 2, out 0", text)
        out = self.cli("sweep", project=path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Sweep (read only", out.stdout)


# ---------------------------------------------------------------- the SessionStart line
class SessionLineTest(DailyCase):
    def plain(self, extra=()):
        specs = ["status line colours on narrow screens", "status line width on narrow screens"] + list(extra)
        return self.make("proj", specs)

    def line(self, path, now, source="startup"):
        import triggers
        return triggers.session_line(path, now=now, lang="en", source=source)

    def test_the_first_startup_session_of_a_day_gets_one_integer_line(self):
        path = self.plain()
        got = self.line(path, NOW)
        self.assertIn("budgit daily", got)
        self.assertIn("1 batch", got)
        self.assertNotIn("R-1", got)
        self.assertNotIn("status", got.replace("budgit daily", ""))                  # no word of the ledger: integers only

    def test_a_second_session_the_same_day_and_other_sources_stay_silent(self):
        path = self.plain()
        self.assertNotIn("budgit daily", self.line(path, NOW, source="resume"))
        self.assertNotIn("budgit daily", self.line(path, NOW, source="clear"))
        self.assertIn("budgit daily", self.line(path, NOW))
        self.assertNotIn("budgit daily", self.line(path, NOW + 60))

    def test_a_worktree_of_the_same_repo_shares_the_day(self):
        path = self.plain()
        subprocess.run(["git", "-C", path, "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", path, "add", "-A"], check=True)
        subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"], check=True)
        other = os.path.join(self.tmp, "wt")
        subprocess.run(["git", "-C", path, "worktree", "add", "-q", "-b", "side", other], check=True)
        self.assertIn("budgit daily", self.line(path, NOW))
        self.assertNotIn("budgit daily", self.line(other, NOW + 5))

    def test_the_next_day_it_comes_back_with_the_existing_backoff(self):
        path = self.plain()
        self.assertIn("budgit daily", self.line(path, NOW))
        self.assertIn("budgit daily", self.line(path, NOW + DAY + 60))
        self.assertNotIn("budgit daily", self.line(path, NOW + 2 * DAY + 120))          # ignored twice: due after two days

    def test_two_sessions_at_once_show_it_once(self):
        path = self.plain()
        env = dict(os.environ, STUDIO_HOME=self.home, BUDGIT_LANG="en")
        code = ("import sys; sys.path.insert(0, %r); import triggers; print('SHOWN' if 'budgit daily' in "
                "triggers.session_line(%r, now=%r, lang='en', source='startup') else 'quiet')" % (
                    os.path.join(ROOT, "plugin", "scripts"), path, NOW))
        procs = [subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True) for _ in range(4)]
        outs = [p.communicate()[0].strip() for p in procs]
        self.assertEqual(outs.count("SHOWN"), 1, outs)

    def test_nothing_to_say_keeps_the_days_slot(self):
        path = self.make("proj", ["one thing", "another"])
        self.assertNotIn("budgit daily", self.line(path, NOW))
        data = sl.load_ledger(path)
        sl.add_item(data, "status line colours on narrow screens", ["legacy"])
        sl.add_item(data, "status line width on narrow screens", ["legacy"])
        sl.save_ledger(path, data)
        self.assertIn("budgit daily", self.line(path, NOW + 3600))                      # same day: the slot was not spent

    def test_the_line_and_the_report_agree_on_duplicates_with_a_parked_item(self):
        """A parked (`later`) item is never compared by title, but it is open, so an explicit "covered by R-n" may name it:
        the SessionStart facts and the report must count the same pairs."""
        import daily
        path = self.make("proj", [("Alpha parked idea about hooks", {"phase": "later"}), "covered by R-1, nothing more here",
                                  ("Beta parked idea about hooks", {"phase": "later"}), "rename the sweep column", "tune the colour palette"])
        (row,) = daily.collect(path, git=False)["projects"]
        self.assertEqual(row["duplicates"], [["R-1", "R-2"]])                # the parked R-1 is a target; R-1 and R-3 are not paired
        self.assertEqual(daily.session_facts(path, now=NOW)["duplicates"], len(row["duplicates"]))

    def test_a_cache_from_another_day_adds_no_to_clean_count(self):
        import daily
        path = self.plain()
        key = daily.repo_key(path)
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "daily.json"), "w") as f:
            json.dump({"day": "2001-01-01", "clean": {key: {"remove": 3, "delete": 4}}}, f)
        self.assertNotIn("to clean", self.line(path, NOW))
        self.setUp()
        path = self.plain()
        key = daily.repo_key(path)
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "daily.json"), "w") as f:
            json.dump({"day": time.strftime("%Y-%m-%d", time.localtime(NOW)), "clean": {key: {"remove": 3, "delete": 4}}}, f)
        self.assertIn("7 to clean", self.line(path, NOW))

    def test_the_repo_key_is_a_hash_and_never_printed(self):
        import daily
        path = self.plain()
        key = daily.repo_key(path)
        self.assertRegex(key, r"^[0-9a-f]{16}$")
        self.line(path, NOW)
        state = open(os.path.join(self.home, "triggers.json")).read()
        self.assertNotIn(os.path.basename(path), state)
        self.assertNotIn(self.root, state)

    def test_a_protected_project_gets_no_line_and_its_ledger_stays_closed(self):
        path = self.make("held-proj", ["status line colours", "status line width"], private=True)
        real = sl.load_ledger

        def refuse(project, *a, **k):
            if os.path.realpath(project) == os.path.realpath(path):
                raise AssertionError("the protected ledger was opened")
            return real(project, *a, **k)
        sl.load_ledger = refuse
        try:
            self.assertNotIn("budgit daily", self.line(path, NOW))
        finally:
            sl.load_ledger = real

    def test_the_card_takes_under_100_ms(self):
        specs = [("%s thing %d about %s" % (w, n, w), {"phase": "v0.14"}) for n, w in enumerate(["alpha", "beta", "gamma", "delta"] * 15)]
        path = self.make("busy", specs)
        import daily
        import retromine
        import triggers
        t0 = time.process_time()                                           # CPU time, not wall (R-1282)
        daily.session_facts(path, now=NOW)
        self.assertLess(time.process_time() - t0, 0.1)

    def test_over_budget_the_card_is_silent_and_keeps_the_slot(self):
        import daily
        path = self.plain()
        old = daily.SESSION_BUDGET_MS
        daily.SESSION_BUDGET_MS = 0
        try:
            self.assertNotIn("budgit daily", self.line(path, NOW))
        finally:
            daily.SESSION_BUDGET_MS = old
        self.assertIn("budgit daily", self.line(path, NOW + 10))

    def test_the_hook_prints_it_on_startup_only_and_never_names_a_protected_project(self):
        import portfolio
        path = self.plain()
        held = self.make("held-proj", ["x"], private=True)
        portfolio.add(path)
        portfolio.add(held)
        sl.activate(path, "s-daily0001")
        out = self.hook(path)
        self.assertIn("budgit daily", out)
        self.assertNotIn("held-proj", out)
        self.assertNotIn("budgit daily", self.hook(path, source="resume"))

    def test_spanish_line(self):
        import triggers
        path = self.plain()
        got = triggers.session_line(path, now=NOW, lang="es", source="startup")
        self.assertIn("budgit daily", got)
        self.assertIn("lote", got)
