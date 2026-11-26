"""R-882 synthetic fixture: a sibling that collects no test at all (see maska). The def line below is why the runner must
check ids and not file text: def test_ is present but nothing is collected."""
import unittest


class MaskB(unittest.TestCase):
    pass


def test_never_collected():
    pass
