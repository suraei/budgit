"""Release 2 RC (ext-review): on_tm reads portfolio.json whole, so a list past 4 KB still names the TM chat; the small stamp
read keeps its 4096 default."""
import json
import os
import tempfile
import unittest
import unittest.mock

import hook


class OnTmBigPortfolioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_on_tm_recognises_the_tm_when_portfolio_json_is_over_4_kb(self):
        path = os.path.join(self.tmp.name, "portfolio.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"projects": {"p%d" % n: {"path": "/x/%d" % n, "note": "n" * 40} for n in range(200)}, "tm": "sess-tm"}, fh)
        self.assertGreater(os.path.getsize(path), 5 * 1024)
        with unittest.mock.patch.object(hook.sl, "home_state", return_value=self.tmp.name):
            self.assertTrue(hook.on_tm("stop", {"session_id": "sess-tm"}))
            self.assertFalse(hook.on_tm("stop", {"session_id": "other"}))

    def test_read_regular_keeps_a_4096_byte_default_and_takes_a_limit(self):
        path = os.path.join(self.tmp.name, "big.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("a" * 6000)
        self.assertEqual(len(hook.read_regular(path)), 4096)
        self.assertEqual(len(hook.read_regular(path, 1 << 20)), 6000)


if __name__ == "__main__":
    unittest.main()
