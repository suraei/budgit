"""R-1149: a test run for a checkout imports and executes only that checkout's code.

`python -m unittest` puts the current folder first on sys.path, so a run started from another checkout would load
that checkout's modules. Python 3.11+ has -P (no cwd on sys.path); 3.9 has no such flag, so a runner there passes
cwd=<its own root> and puts its own tests and scripts folders first on PYTHONPATH.
"""
import os
import sys


def flags(version_info=None):
    """["-P"] when the interpreter that will run is 3.11 or newer (read from its version_info), else []."""
    vi = tuple(sys.version_info if version_info is None else version_info)
    return ["-P"] if vi[:2] >= (3, 11) else []


def own_dirs(root):
    return [os.path.join(root, "tests"), os.path.join(root, "plugin", "scripts")]


def own_pythonpath(root, existing=""):
    """Own tests and scripts dirs first, then whatever the caller already had."""
    parts = own_dirs(root) + ([existing] if existing else [])
    return os.pathsep.join(parts)
