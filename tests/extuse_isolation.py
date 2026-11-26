"""R-1358: keep the outside-model use log out of the real project. A test that runs a review without a temp `cwd` would append
fake rows to the data folder of the folder the suite runs in, and skew the weekly stand-up count. `isolate(test)` sends every
use-log write of that test to a temp project; the temp project is returned so a test can read the rows."""
import tempfile
from unittest import mock

import extuse


def isolate(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    real = extuse.log
    patcher = mock.patch.object(extuse, "log", lambda project, *a, **k: real(tmp.name, *a, **k))
    patcher.start()
    test.addCleanup(patcher.stop)
    return tmp.name
