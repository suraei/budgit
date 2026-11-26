"""R-1198: the CI shard split (tools/ci_shard.py) and its Linux skip list.
  - the 8 slices cover every module exactly once, minus the skip list -> test_eight_slices_cover_every_module_once_minus_the_skip_list
  - the skip-list loader                                              -> test_loader_*"""
import os, sys, tempfile, unittest

from helpers import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import ci_shard as cs


class ShardTest(unittest.TestCase):
    def test_eight_slices_cover_every_module_once_minus_the_skip_list(self):
        mods = cs.discover()
        skip = cs.load_skip()
        self.assertTrue(set(skip) <= set(mods), "a skip-list entry names no module: %s" % sorted(set(skip) - set(mods)))
        got, skipped = [], None
        for k in range(1, 9):
            mine, skipped, _ = cs.pick(mods, k, 8, skip=skip, platform="linux")
            got += mine
        self.assertEqual(len(got), len(set(got)), "a module is in two slices")
        self.assertEqual(sorted(got), sorted(set(mods) - set(skip)))
        self.assertEqual(skipped, sorted(skip))

    def test_slices_are_repeatable_and_balanced_by_weight(self):
        w = {"a": 10.0, "b": 9.0, "c": 5.0, "d": 4.0, "e": 1.0}
        one = cs.split(list(w), w, 2)
        self.assertEqual(one, cs.split(list(reversed(list(w))), w, 2))
        self.assertEqual(sorted(sum(w[m] for m in s) for s in one), [14.0, 15.0])

    def test_nothing_is_skipped_off_linux(self):
        kept, skipped = cs.apply_skip(["a", "b"], {"a": "r"}, "darwin")
        self.assertEqual((kept, skipped), (["a", "b"], []))

    def test_no_timings_split_by_file_count(self):
        w = cs.load_weights(["a", "b", "c"], path=os.path.join(tempfile.gettempdir(), "no-such-durations.json"))
        self.assertEqual(w, {"a": 1.0, "b": 1.0, "c": 1.0})

    def test_shard_argument_is_checked(self):
        self.assertEqual(cs.parse_shard("3/8"), (3, 8))
        for bad in ("0/8", "9/8", "x", "3"):
            with self.assertRaises(ValueError):
                cs.parse_shard(bad)


class SkipListTest(unittest.TestCase):
    def load(self, text):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        path = os.path.join(d, "skip.txt")
        with open(path, "w") as f:
            f.write(text)
        return cs.load_skip(path)

    def test_loader_reads_module_and_reason_and_ignores_comments(self):
        got = self.load("# header\n\ntest_a  # needs macOS\ntest_b.py  # also macOS\n")
        self.assertEqual(got, {"test_a": "needs macOS", "test_b": "also macOS"})

    def test_loader_refuses_an_entry_without_a_reason(self):
        for bad in ("test_a\n", "test_a  #\n", "test_a extra  # why\n"):
            with self.assertRaises(ValueError):
                self.load(bad)

    def test_loader_refuses_a_module_listed_twice(self):
        with self.assertRaises(ValueError):
            self.load("test_a  # x\ntest_a  # y\n")

    def test_the_real_skip_list_loads_and_every_entry_has_a_reason(self):
        for mod, why in cs.load_skip().items():
            self.assertTrue(why, mod)


if __name__ == "__main__":
    unittest.main()
