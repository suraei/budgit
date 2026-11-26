"""R-1294 slice 2 differential replay. Not a test module: run it by hand.

  env PYTHONPATH=<wt>/tests:<wt>/plugin/scripts python3 -W ignore <wt>/tests/r1294_replay.py <out file>

For every form of tests/fixtures/r1294_live_reads.txt it writes one line: the live-checkout guard's verdict (pass / refuse, with the
reason when refused) and the real-data exception's verdict (shared function with extra=None; n/a when the form is not a read verb of
that table). Run it on unmodified code to record today's verdicts, and again after the change to compare."""
import os
import re
import shlex
import shutil
import sys
from unittest import mock

import mergeguard
import studiolib as sl
from test_r1199_guards import make_repo

V = chr(103) + chr(105) + chr(116)
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "r1294_live_reads.txt")


def c(text):
    return text.replace("VCS", V).replace("\\n", "\n")


def rows(path=FIXTURE):
    """[(expected, form, reason)] in file order; comment and blank lines skipped."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.strip() and not line.startswith("#"):
                parts = line.split(" => ", 2)
                out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return out


def realdata_verdict(form):
    """The shared function with no extra table, for a form shaped `<vcs> [-C d] [--no-pager] <verb> args`; n/a otherwise."""
    try:
        toks = shlex.split(c(form))
    except ValueError:
        return "n/a"
    if not toks or toks[0] != V:
        return "n/a"
    i = 1
    while i < len(toks) and (toks[i] in ("--no-pager", "--no-optional-locks") or toks[i] == "-C"):
        i += 2 if toks[i] == "-C" else 1
    if i >= len(toks) or toks[i] not in sl._RO_SPECS:
        return "n/a"
    return "pass" if sl._git_reader_ok(toks[i], toks[i + 1:]) else "refuse"


def replay():
    """[(form, mergeguard verdict, reason, real-data verdict)]."""
    live = make_repo()
    out = []
    try:
        with mock.patch.object(mergeguard, "checkout_root", return_value=live):
            for _expected, form, _reason in rows():
                got = mergeguard.decision("Bash", {"command": c(form)}, live)
                out.append((form, "pass" if got is None else "refuse", "" if got is None else got[1][:110], realdata_verdict(form)))
    finally:
        shutil.rmtree(live, True)
    return out


if __name__ == "__main__":
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        for form, verdict, reason, rd in replay():
            fh.write("%s | %s | realdata=%s | %s\n" % (verdict, form, rd, reason))
    print("wrote", sys.argv[1], len(rows()), "forms")
