"""R-1074 phase 0: realdata collects every write target before it judges, and its redirect marker is typed.

The sink only collects: the decisions are proved unchanged by the corpus tests (test_guard_differential, test_guard_ask_counts).
"""
import os
import time
import unittest

import realdata
import test_guard_differential as d
from test_guard_trust import GuardCase

PATTERNS = ["data.json", "data/real/**", "Café/**"]

# (kind, command, the word as typed). None of the targets is protected: the sink collects before the protection filter.
ROWS = [
    ("redirect", "echo hi > out/a.txt", "out/a.txt"),
    ("tee", "echo hi | tee out/t.txt", "out/t.txt"),
    ("copy-dest", "cp src/app.py out/c.txt", "out/c.txt"),
    ("copy-src", "cp src/app.py out/c.txt", "src/app.py"),
    ("option-output", "sort -o out/s.txt in.txt", "out/s.txt"),
    ("dd", "dd if=in.bin of=out/d.img", "out/d.img"),
    ("rsync", "rsync -a src/ out/r/", "out/r/"),
    ("tar", "tar -czf out/a.tgz src", "out/a.tgz"),
    ("git", "git diff --output=out/g.txt", "out/g.txt"),
    ("other", "touch out/o.txt", "out/o.txt"),
]


class SinkTests(GuardCase):
    def collect(self, command):
        return realdata.collect(command, self.project, self.project, PATTERNS)

    def test_collect_returns_the_target_of_every_kind(self):
        for kind, command, word in ROWS:
            with self.subTest(kind=kind, command=command):
                targets, _doubts = self.collect(command)
                hits = [(p, k, w) for (p, k, w) in targets if k == kind and w.endswith(word)]
                self.assertTrue(hits, "no %s target %r in %r" % (kind, word, targets))

    def test_quoted_data_yields_no_write_target(self):
        for command in ('echo "a > verify/x"', "echo 'tee verify/x > y'", 'printf "%s" "cp a verify/b"'):
            with self.subTest(command=command):
                targets, _doubts = self.collect(command)
                self.assertEqual(targets, [])

    def test_an_unreadable_line_is_a_doubt_never_an_empty_clean_list(self):
        self.assertIsNone(realdata.segments("cat >(x)"))
        targets, doubts = self.collect("cat >(x)")
        self.assertEqual(targets, [])
        self.assertTrue(doubts, "an unreadable line must raise a doubt")

    def test_the_redirect_marker_is_typed_not_a_prefix(self):
        segs = realdata.segments("cat < in.txt > out.txt")
        self.assertEqual(segs, [(["cat"], [("in", "in.txt"), ("out", "out.txt")], "")])
        for _words, outs, _op in segs:
            for _side, word in outs:
                self.assertNotIn("\x00", word)

    def test_collecting_changes_no_decision(self):
        ctx = realdata.judge("echo x > data.json", self.project, PATTERNS, self.project)
        self.assertFalse(ctx.harmless)
        self.assertEqual(len(ctx.keys), 1)
        targets, _doubts = self.collect("echo x > data.json")
        self.assertIn("redirect", [k for (_p, k, _w) in targets])

    def test_the_link_and_the_extraction_folder_are_collected(self):
        targets, _doubts = self.collect("ln -s src/app.py out/l")
        self.assertTrue([1 for (_p, k, w) in targets if k == "link" and w == "out/l"], targets)
        targets, _doubts = self.collect("tar -xf a.tar -C out/x")
        self.assertTrue([1 for (p, k, _w) in targets if k == "tar" and str(p).rstrip("/").endswith("out/x")], targets)

    def test_touch_and_install_kinds_stay_apart(self):
        targets, _doubts = self.collect("install -m 644 src/app.py out/i")
        self.assertIn("copy-dest", [k for (_p, k, w) in targets if w.endswith("out/i")])
        targets, _doubts = self.collect("touch out/o.txt")
        self.assertEqual([k for (_p, k, w) in targets if w.endswith("out/o.txt")], ["other"])

    def test_a_direct_write_still_reaches_collect_as_a_doubt_or_a_target(self):
        for command in ("unknown-tool x > y", "unknown-tool data.json", "find data/real -delete", "rm -rf data/real"):
            with self.subTest(command=command):
                ctx = realdata.judge(command, self.project, PATTERNS, self.project)
                targets, doubts = self.collect(command)
                self.assertFalse(ctx.harmless, "the row must be one judge() asks about")
                self.assertTrue(targets or doubts, "an empty clean list for a command judge() asks about")
        _targets, doubts = self.collect("unknown-tool x > y")
        self.assertTrue([d for d in doubts if "unknown-tool" in d], doubts)


class PerfSink(d.DifferentialGuardTest):
    """[perf] judge() over the differential corpus, mean of 3 runs. Printed, not asserted (the machine varies)."""
    BEFORE = 0.9032                                   # seconds, measured on 040ea9f7 with the same helper

    def test_perf(self):
        cmds = d.corpus(self.root, d.CLI, self.drop, self.extra)
        runs = []
        for _ in range(4):                            # the first run warms caches and is dropped
            t0 = time.perf_counter()
            for cwd in (self.project, self.tmp):
                for cmd in cmds:
                    realdata.judge(cmd, self.project, PATTERNS, cwd)
            runs.append(time.perf_counter() - t0)
        runs = runs[1:]
        mean = sum(runs) / len(runs)
        print("\nPERF commands %d x 2 cwds, mean of 3 runs %.4f s, before %.4f s, change %+.1f %%"
              % (len(cmds), mean, self.BEFORE, 100.0 * (mean - self.BEFORE) / self.BEFORE))


    def test_prepass_ab(self):
        """A/B in one process: the same commands through the whole guard with the flag pre-pass on and forced off."""
        import studiolib as sl
        from unittest import mock
        cmds = d.corpus(self.root, d.CLI, self.drop, self.extra)

        def once():
            t0 = time.perf_counter()
            for cwd in (self.project, self.tmp):
                for cmd in cmds:
                    sl.real_data_check("Bash", {"command": cmd}, self.project, cwd=cwd)
            return time.perf_counter() - t0

        on, off = [], []
        once()                                         # warm caches
        for _ in range(5):
            on.append(once())
            with mock.patch.object(sl, "_flagless_read_ask", lambda command: None):
                off.append(once())
        print("\nPERF A/B commands %d x 2 cwds, best of 5: pre-pass on %.4f s, off %.4f s, ratio %.3f; means on %.4f off %.4f"
              % (len(cmds), min(on), min(off), min(on) / min(off), sum(on) / 5, sum(off) / 5))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(SinkTests))
    suite.addTest(PerfSink("test_perf"))               # not the inherited differential tests
    if os.environ.get("BUDGIT_AB_PERF") == "1":        # the A/B cost comparison runs only when asked for
        suite.addTest(PerfSink("test_prepass_ab"))
    return suite


if __name__ == "__main__":
    unittest.main()
