"""R-1360 fix: a home-variable read keeps passing when the checkout folder's name holds a changer word (`r1360b-read-rows`);
a changer used as a real shell word still keeps the refusal."""
import unittest

from helpers import StudioTestCase       # noqa: F401  (puts the plugin scripts on the path)
import studiolib as sl

CALL = "/wt/%s/plugin/bin/budgit ledger show R-1 --project $HOME/p"


class HomePathWordTest(unittest.TestCase):
    def test_a_folder_name_holding_a_changer_word_is_not_a_changer(self):
        for folder in ("r1360b-read-rows", "set-up", "print-me", "x.for.y", "a-export", "wait-list", "eval-tools"):
            self.assertTrue(sl._home_var_stays(CALL % folder), folder)
            self.assertFalse(sl._home_write_target_unsafe(CALL % folder + " # no write target"), folder)

    def test_a_changer_used_as_a_shell_word_still_refuses(self):
        for pre in ("read HOME; ", "export HOME=/x; ", "x; set -- ; ", "(eval x); ", "'read' x; ", "\\read x; ", "true && printf -v HOME x; "):
            line = pre + CALL % "plain"
            self.assertFalse(sl._home_var_stays(line), line)

    def test_a_write_target_with_a_real_changer_stays_unsafe(self):
        self.assertTrue(sl._home_write_target_unsafe("read HOME; tee $HOME/f"))
        self.assertTrue(sl._home_write_target_unsafe("export X=1; echo y > $HOME/f"))


if __name__ == "__main__":
    unittest.main()
