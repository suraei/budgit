"""R-120: the guard and quarantine suites run a second time on the NEW names.

The originals keep their `.studio` fixtures (the legacy layout, which must keep working for one minor release). This
module re-reads their source, rewrites the data-folder name to `.budgit` and the agent namespace to `budgit:`, and
defines the same test classes here with BudgitTestCase as the base, so every bypass test covers both names without a
copy that can drift.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ROOT  # noqa: E402,F401

SOURCES = ("test_guards", "test_guards2", "test_guards_p6", "test_raw_store")
_HERE = os.path.dirname(os.path.abspath(__file__))


def _rewrite(src):
    src = re.sub(r"(?<![\w])\.studio\b", ".budgit", src)
    src = re.sub(r"\bstudio:(verifier|plan-critic|researcher|scout-reader|security-reviewer|persona-sim)\b",
                 r"budgit:\1", src)
    src = re.sub(r"\bstudio-(verifier|plan-critic|researcher|scout-reader|security-reviewer|persona-sim)\b",
                 r"budgit-\1", src)
    src = re.sub(r"\bStudioTestCase\b", "BudgitTestCase", src)
    # the rewrite above turned the glob list's `.studio` entry into a second `.budgit`: the list names both
    src = src.replace('!**/.budgit/research/raw/** !**/.budgit/research/raw/**',
                      '!**/.budgit/research/raw/** !**/.studio/research/raw/**')
    src = src.replace('"!**/.budgit/research/raw/**", "!**/.budgit/research/raw/**"',
                      '"!**/.budgit/research/raw/**", "!**/.studio/research/raw/**"')
    return src


def _load(name):
    path = os.path.join(_HERE, name + ".py")
    with open(path, encoding="utf-8") as f:
        src = _rewrite(f.read())
    ns = {"__name__": __name__, "__file__": path, "__builtins__": __builtins__}
    exec(compile(src, path + " [as .budgit]", "exec"), ns)
    return ns


for _name in SOURCES:
    for _key, _val in _load(_name).items():
        if isinstance(_val, type) and _key.endswith(("Test", "Case")) and _val.__module__ == __name__:
            globals()["%s__%s" % (_name, _key)] = _val


del _name, _key, _val
