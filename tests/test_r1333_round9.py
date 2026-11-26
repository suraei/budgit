"""R-1333 round 9: type 3 needs on-screen evidence (an open ux finding, a visible area, or visible words in the user's own statements;
the bare words «text», «texto», «sale», «se ve» are not on-screen evidence), and «after R-n» is a time word, never a block.
Shapes copy the real ones: R-1119 (technician text, origin I-1660 a manual-source note) and R-1165 («TM 16 Nov after R-1135 round 5 HOLD»)."""
import unittest

import i18n
from test_r1333_one_order import Base, her, lo

R_1119_TEXT = "reuse _split_segments so quoted text is not a command"
R_1165_TEXT = "TM 16 Nov after R-1 round 5 HOLD: fresh design, not a round 6"


class R9(Base):
    def row(self, res, item_id):
        return self.rows(res)[item_id]


class AfterIsNotABlock(R9):
    def test_after_is_not_a_block(self):
        import ledgerplan as lp
        self.add(1)
        self.add(2, text=R_1165_TEXT)                                                  # R-1165's shape: a time word naming R-1
        self.add(3, text="despues de R-1 y tras R-1 vemos")
        self.add(4, text="blocked by R-1")
        self.add(5, text="bloqueada por R-1")
        self.add(6, text="depends on R-1")
        self.add(7, text="depende de R-1")
        live = {"R-%d" % i for i in range(1, 8)}
        for n, blocked in ((2, False), (3, False), (4, True), (5, True), (6, True), (7, True)):
            got = lp.blockers(self.data["items"][n - 1], live)
            self.assertEqual(got == ["R-1"], blocked, "R-%d" % n)
        self.assertEqual(self.row(self.order(), "R-1")["unblocks"], 4)                 # only the four real blocks


if __name__ == "__main__":
    unittest.main()
