"""R-1312: `budgit timelines silent` must not stall on iCloud-evicted (dataless) files and must stop at a time limit,
saying which projects it skipped. Stat and clock are injected; nothing here evicts a file or sleeps."""
import builtins
import contextlib
import os
import threading
import time
from unittest import mock

from helpers import StudioTestCase
import livework
import portfolio
import studiolib as sl
import timelines

DATALESS = 0x40000000


class FakeStat:
    """os.stat that reports SF_DATALESS for the given paths; `on_call` runs on every stat (to move a fake clock)."""

    def __init__(self, evicted=(), on_call=None):
        self.evicted = set(evicted)
        self.on_call = on_call

    def __call__(self, p, *a, **k):
        if self.on_call:
            self.on_call(p)
        real = os.stat(p, *a, **k)
        if p in self.evicted:
            return mock.Mock(st_flags=DATALESS, st_mtime=real.st_mtime)
        return real


class TimelinesLimitTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(livework, "_home", lambda: self.tmp)       # never read the real home configs
        patch.start()
        self.addCleanup(patch.stop)

    def make_project(self, name, with_item=False):
        p = os.path.join(self.tmp, name)
        os.makedirs(p)
        sl.ensure_studio_dir(p)
        data = sl.empty_ledger()
        if with_item:
            sl.add_item(data, "build it", "I-1", item_id="R-5")
        sl.save_ledger(p, data)
        return p

    def register(self, *paths):
        portfolio.save({"version": 1, "projects": [{"path": p, "name": None, "pm": None, "channel": "current", "reason": None}
                                                   for p in paths]})

    def projects(self, *names):
        out = [self.make_project(n, with_item=True) for n in names]
        self.register(*out)
        return out

    def test_evicted_ledger_is_skipped_and_never_opened(self):
        gone, ok = self.projects("gone", "ok")
        ledger = sl._ledger_path(gone)
        real_open = builtins.open

        def guarded(f, *a, **k):
            if str(f) == ledger:
                raise AssertionError("opened an evicted ledger")
            return real_open(f, *a, **k)

        skipped = []
        with mock.patch.object(builtins, "open", guarded), \
                mock.patch.object(sl, "load_ledger", wraps=sl.load_ledger) as ll:
            got = timelines.active_projects(skipped=skipped, stat=FakeStat([ledger]))
        self.assertEqual(got, [ok])
        self.assertEqual(skipped, [(gone, "evicted")])
        self.assertNotIn(gone, [c.args[0] for c in ll.call_args_list])

    def test_evicted_timeline_file_is_skipped_and_never_opened(self):
        gone, ok = self.projects("gone", "ok")
        tl = timelines.path(gone)
        with open(tl, "w") as f:
            f.write("{}\n")
        real_open = builtins.open

        def guarded(f, *a, **k):
            if str(f) == tl:
                raise AssertionError("opened an evicted timeline")
            return real_open(f, *a, **k)

        skipped = []
        with mock.patch.object(builtins, "open", guarded):
            lines = timelines.silent_lines([gone, ok], skipped=skipped, stat=FakeStat([tl]))
        self.assertEqual(lines, ["timelines: ok has no timeline line in the last 24 h"])
        self.assertEqual(skipped, [(gone, "evicted")])

    def test_time_limit_stops_early_and_lists_the_rest(self):
        a, b, c = self.projects("aa", "bb", "cc")
        now = [0.0]

        def tick(p):
            if p == sl._ledger_path(a):
                now[0] += 25                             # reading the first ledger "takes" 25 s
        with mock.patch.object(sl, "load_ledger", wraps=sl.load_ledger) as ll:
            out = timelines.cli_text("silent", limit_s=20, stat=FakeStat(on_call=tick), clock=lambda: now[0])
        self.assertNotIn(b, [c_.args[0] for c_ in ll.call_args_list])
        self.assertNotIn(c, [c_.args[0] for c_ in ll.call_args_list])
        last = out.splitlines()[-1]
        self.assertTrue(last.startswith("timelines: skipped "), last)
        self.assertIn("time limit:", last)              # past the limit no project.json is read, so the names stay hidden
        self.assertIn("skipped 3 projects", last)
        self.assertNotIn("bb", last)
        self.assertNotIn("cc", last)

    def test_summary_line_groups_reasons_by_basename(self):
        a, b, c = self.projects("aa", "bb", "cc")
        self.assertEqual(timelines.skipped_line([(a, "evicted"), (b, "evicted"), (c, "time limit")]),
                         "timelines: skipped 3 projects (evicted: aa, bb; time limit: cc)")

    def protect(self, *paths):
        """Real code path: the protected list comes from the config's protected_dirs (home folder pinned to the temp dir)."""
        return mock.patch.multiple(livework, _home=lambda: self.tmp), \
            mock.patch.object(sl, "load_config", return_value={"protected_dirs": list(paths)})

    def pj(self, project):
        return os.path.join(sl.studio_dir(project), "project.json")

    def no_open(self, *files):
        """Opening any of these files raises (a raising open proves a file was never read)."""
        real_open, real_os_open = builtins.open, os.open
        forbidden = {str(f) for f in files}

        def guarded(f, *a, **k):
            if str(f) in forbidden:
                raise AssertionError("opened " + str(f))
            return real_open(f, *a, **k)

        def guarded_os(f, *a, **k):
            if str(f) in forbidden:
                raise AssertionError("opened " + str(f))
            return real_os_open(f, *a, **k)
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(builtins, "open", guarded))
        stack.enter_context(mock.patch.object(os, "open", guarded_os))
        return stack

    def mark_private(self, project):
        with open(self.pj(project), "w") as f:
            f.write('{"private": true}')

    def test_project_json_marked_private_and_not_evicted_is_hidden(self):
        a, b = self.projects("aa", "marked")
        self.mark_private(b)
        self.assertEqual(timelines.skipped_line([(a, "evicted"), (b, "evicted")], timelines.private_checker(FakeStat())),
                         "timelines: skipped 2 projects (evicted: aa, private)")

    def test_evicted_project_json_is_hidden_and_never_opened(self):
        a, b = self.projects("aa", "marked")
        self.mark_private(b)
        with self.no_open(self.pj(b)):
            line = timelines.skipped_line([(b, "evicted")], timelines.private_checker(FakeStat([self.pj(b)])))
        self.assertEqual(line, "timelines: skipped 1 projects (evicted: private)")

    def test_project_json_read_after_the_limit_is_hidden_and_never_opened(self):
        a, b = self.projects("aa", "late")
        with self.no_open(self.pj(a), self.pj(b)):
            line = timelines.skipped_line([(a, "time limit")], timelines.private_checker(FakeStat(), lambda: 30.0, 20.0))
        self.assertEqual(line, "timelines: skipped 1 projects (time limit: private)")

    def test_oversize_project_json_is_hidden_and_never_opened(self):
        a, = self.projects("aa")
        with open(self.pj(a), "w") as f:
            f.write("{" + " " * (timelines.PROJECT_JSON_MAX + 1) + "}")
        with self.no_open(self.pj(a)):
            line = timelines.skipped_line([(a, "evicted")], timelines.private_checker(FakeStat()))
        self.assertEqual(line, "timelines: skipped 1 projects (evicted: private)")

    def test_private_project_is_never_named_and_no_project_json_is_opened(self):
        a, b = self.projects("aa", "secretname")
        tl = timelines.path(b)
        p1, p2 = self.protect(b)
        with p1, p2, self.no_open(self.pj(b)):
            out = timelines.cli_text("silent", limit_s=20, stat=FakeStat([sl._ledger_path(b), tl]), clock=time.monotonic)
        self.assertNotIn("secretname", out)
        self.assertIn("timelines: aa has no timeline line in the last 24 h", out)
        self.assertEqual(out.splitlines()[-1], "timelines: skipped 1 projects (evicted: private)")

    def test_unreadable_protected_list_hides_every_name(self):
        a, = self.projects("aa")
        with mock.patch.object(livework, "_config_dir_scan", side_effect=OSError("no list")):
            self.assertEqual(timelines.skipped_line([(a, "evicted")]), "timelines: skipped 1 projects (evicted: private)")

    def test_silent_line_hides_a_protected_project(self):
        a, b = self.projects("aa", "secretname")
        p1, p2 = self.protect(b)
        with p1, p2, self.no_open(self.pj(b)):
            lines = timelines.silent_lines([a, b])
        self.assertEqual(lines, ["timelines: aa has no timeline line in the last 24 h",
                                 "timelines: private has no timeline line in the last 24 h"])

    def test_nothing_skipped_output_equals_main(self):
        # main's cli_text("silent") body, copied here: the old code cannot be imported next to the new one.
        quiet, busy = self.projects("quiet", "busy")
        with open(timelines.path(busy), "w") as f:
            f.write('{"ts": "%s"}\n' % timelines._stamp(time.time() - 60))
        since = time.time() - timelines.DAY_S
        main_projects = [e["path"] for e in portfolio.load()["projects"]
                         if e.get("channel") != "off" and any(it.get("status") in ("open", "doing") for it in sl.load_ledger(e["path"])["items"])]
        main_out = "\n".join("timelines: %s has no timeline line in the last 24 h" % os.path.basename(os.path.realpath(p))
                             for p in main_projects if not timelines.read_lines(p, since)) or "timelines: no silent active project."
        self.assertEqual(main_out, "timelines: quiet has no timeline line in the last 24 h")
        self.assertEqual(timelines.cli_text("silent", limit_s=20, stat=FakeStat(), clock=time.monotonic), main_out)
        self.assertEqual(timelines.skipped_line([]), "")

    def within(self, seconds, fn):
        """-> (finished, result): fn runs in a daemon thread so a blocked open fails the test instead of hanging it."""
        box = []
        t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
        t.start()
        t.join(seconds)
        return (not t.is_alive()), (box[0] if box else None)

    def fifo_link(self, target_file):
        fifo = os.path.join(self.tmp, "fifo-%d" % len(os.listdir(self.tmp)))
        os.mkfifo(fifo)
        if os.path.lexists(target_file):
            os.remove(target_file)
        os.symlink(fifo, target_file)

    def test_project_json_symlinked_to_a_fifo_returns_fast_and_is_private(self):
        a, = self.projects("aa")
        self.fifo_link(self.pj(a))
        done, got = self.within(2, lambda: timelines.private_checker()(a))
        self.assertTrue(done, "the project.json read blocked on a FIFO")
        self.assertTrue(got)

    def test_timeline_file_symlinked_to_a_fifo_does_not_hang(self):
        a, = self.projects("aa")
        self.fifo_link(timelines.path(a))
        done, got = self.within(2, lambda: timelines.read_lines(a))
        self.assertTrue(done, "read_lines blocked on a FIFO")
        self.assertEqual(got, [])

    def test_newest_line_past_the_cap_still_counts_as_recent(self):
        a, = self.projects("aa")
        old = timelines._stamp(time.time() - 3 * 86400)
        with open(timelines.path(a), "w") as f:
            for i in range(20):
                f.write('{"ts": "%s", "n": %d}\n' % (old, i))
            f.write('{"ts": "%s", "n": 99}\n' % timelines._stamp(time.time() - 60))
        with mock.patch.object(timelines, "TIMELINE_MAX", 200):
            self.assertGreater(os.path.getsize(timelines.path(a)), 200)
            self.assertEqual(timelines.silent_lines([a]), [])
            self.assertEqual([r["n"] for r in timelines.read_lines(a, time.time() - 86400)], [99])

    def test_bad_portfolio_entries_are_skipped_not_fatal(self):
        a, = self.projects("aa")
        data = {"version": 1, "projects": [None, "junk", {"channel": "current"}, {"path": 5}, {"path": a, "channel": "current"}]}
        with mock.patch.object(portfolio, "load", return_value=data):
            self.assertEqual(timelines.active_projects(), [a])

    def test_unresolvable_protected_entry_masks_every_name(self):
        a, b = self.projects("aa", "bb")
        p1, p2 = self.protect(b, "~nouser-xyz/never")
        with p1, p2, mock.patch.object(sl, "protected_entry", side_effect=lambda s: None if "never" in s else os.path.realpath(s)):
            line = timelines.skipped_line([(a, "evicted")])
        self.assertEqual(line, "timelines: skipped 1 projects (evicted: private)")
