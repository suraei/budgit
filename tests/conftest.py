"""R-318: no test may leave a real Brote window open on her screen.

A test that launches the real app (not the fake) must close the pid it launched, even when it fails. This sweep runs once at the end of the
session and fails if a Brote whose --plugin-root lies in a temporary folder started during the session and is still running. It never kills
anything (a process that is not ours to kill may belong to a run in another terminal) and ignores the ones that were already there at the start.
"""
import subprocess

import pytest

TEMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")


def _strays():
    try:
        out = subprocess.run(["/bin/ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        pid, _, args = line.strip().partition(" ")
        if "Contents/MacOS/Brote" in args and "--plugin-root" in args:
            root = args.split("--plugin-root", 1)[1].strip()
            if root.startswith(TEMP_PREFIXES):
                found[pid] = args
    return found


@pytest.fixture(scope="session", autouse=True)
def no_brote_left_behind():
    before = _strays()
    yield
    left = {p: a for p, a in _strays().items() if p not in before}
    assert not left, "a test left a real Brote window open: " + "; ".join(f"{p}: {a[-120:]}" for p, a in left.items())
