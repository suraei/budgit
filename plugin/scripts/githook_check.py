"""R-1042: the checks behind the git commit-msg and pre-push hooks that `budgit githooks install` puts in a repo.
Run by the hook stub as `python3 -I githook_check.py commit-msg <message file>` or `... pre-push <remote> <url>` (the pushed
ranges arrive on stdin). It rejects (exit 1, one clear line on stderr) a commit message that carries an AI attribution trailer,
by the SAME pattern as the PreToolUse guard: attribution.first_match (here anchored at a line start, so a message that only
quotes the phrase passes). It never needs the repo's cwd to import anything: the stub passes this file by absolute path and
this file puts its own folder first on the path. Any failure to read or to run git rejects (fail closed). Stdlib only.
Push cost (r2): all outgoing refs go through ONE `git log` and ONE `git cat-file --batch-check` (+ one `--batch` for annotated
tags), so 200 tags cost a handful of git calls, not 600."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ZERO = "0" * 40
SCISSORS = "# ------------------------ >8 ------------------------"   # git commit -v: the diff below it is not the message
FMT = "--format=%x01%H%n%B"


def _fail(text):
    sys.stderr.write("budgit: %s\n" % text)
    return 1


def _matcher():
    sys.path.insert(0, HERE)
    import attribution
    if not attribution._setting_on():                      # the user's own `budgit attribution off`: the same switch as the guard; unreadable = on
        return lambda text: None

    def match(text):
        """Two views, reject on either (r3): (a) the message as written and with folded lines unfolded, matched by the shared
        pattern; (b) what git's own trailer parser reads as trailers. "Generated with Claude Code" counts anywhere."""
        hit = attribution.first_match(text, anchored=True)
        if hit:
            return hit
        low = text.lower()
        if "co-authored-by" in low and ("claude" in low or "anthropic" in low):    # only then can git read such a trailer: saves a git call per commit
            return attribution.first_match(_trailers(text), anchored=True)
        return None
    return match


def _trailers(text):
    """What `git interpret-trailers --parse` reads as the trailers of the cleaned message (comments stripped). A missing
    git or a failing call raises, and main() turns that into a rejection (fail closed)."""
    clean = subprocess.run(["git", "stripspace", "--strip-comments"], input=text, capture_output=True, text=True, timeout=60)
    if clean.returncode:
        raise RuntimeError("git stripspace failed")
    parsed = subprocess.run(["git", "interpret-trailers", "--only-trailers", "--only-input", "--unfold"],
                            input=clean.stdout, capture_output=True, text=True, timeout=60)
    if parsed.returncode:
        raise RuntimeError("git interpret-trailers failed")
    return parsed.stdout


def _message_part(text):
    cut = text.find("\n" + SCISSORS)
    if text.startswith(SCISSORS):
        return ""
    return text if cut < 0 else text[:cut]


def commit_msg(path, match):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return _fail("commit blocked (fail closed): cannot read the message file (%s)." % exc)
    hit = match(_message_part(text))
    if hit:
        return _fail("commit rejected (R-1042): the message carries an AI attribution line: %r. "
                     "Remove it and commit again (your rule: no AI attribution in any commit)." % hit[:120])
    return 0


def _git(*args, input=None):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True, errors="replace", timeout=300, input=input)


def _commits(range_args):
    """-> [(sha, message)] of `git log` over range_args, or None when git could not answer."""
    r = _git("log", FMT, *range_args)
    if r.returncode:
        return None
    out = []
    for chunk in r.stdout.split("\x01")[1:]:
        sha, _, body = chunk.partition("\n")
        out.append((sha.strip(), body))
    return out


def _tag_texts(shas):
    """-> [(sha, text)] for the annotated tags among `shas` (a plain commit or a lightweight tag has no message of its own),
    or None when git could not answer. Two calls however many refs."""
    if not shas:
        return []
    kinds = _git("cat-file", "--batch-check=%(objecttype) %(objectname)", input="\n".join(shas) + "\n")
    if kinds.returncode:
        return None
    tags = []
    for line in kinds.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "tag":
            tags.append(parts[1])
        elif len(parts) != 2 or parts[1] == "missing" or parts[0] == "missing":
            return None
    if not tags:
        return []
    raw = subprocess.run(["git", "cat-file", "--batch"], input=("\n".join(tags) + "\n").encode(), capture_output=True, timeout=300)
    if raw.returncode:
        return None
    out, data, pos = [], raw.stdout, 0
    while pos < len(data):
        eol = data.find(b"\n", pos)
        if eol < 0:
            return None
        head = data[pos:eol].decode("utf-8", "replace").split()
        if len(head) != 3:
            return None
        size = int(head[2])
        out.append((head[0], data[eol + 1:eol + 1 + size].decode("utf-8", "replace")))
        pos = eol + 1 + size + 1
    return out


def pre_push(remote, stdin, match):
    refs = []
    for line in stdin.read().splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] != ZERO:          # a delete (local sha all zeros) sends no commit
            refs.append((parts[1], parts[3]))
    if not refs:
        return 0
    lshas = list(dict.fromkeys(l for l, _ in refs))
    rshas = list(dict.fromkeys(r for _, r in refs if r != ZERO))
    # one call: everything the pushed tips reach, minus what the remote tips we know of already hold, minus what ANY remote-tracking
    # ref has (an old commit another remote published is not new here). A repo with no remote-tracking ref checks everything.
    found = _commits(lshas + ["--not"] + rshas + ["--remotes"])
    if found is None:                                     # a remote tip we do not have locally: ask per ref, without it
        found = []
        for lsha, _r in refs:
            more = _commits([lsha, "--not", "--remotes"])
            if more is None:
                return _fail("push blocked (fail closed): git could not list the outgoing commits of %s." % lsha[:12])
            found.extend(more)
    for sha, body in found:
        hit = match(body)
        if hit:
            return _fail("push rejected (R-1042): commit %s carries an AI attribution line: %r. Reword that commit "
                         "(a new commit that fixes it is not enough, the old one still travels)." % (sha[:12], hit[:120]))
    texts = _tag_texts(lshas)
    if texts is None:
        return _fail("push blocked (fail closed): cannot read the pushed tags.")
    for sha, text in texts:
        hit = match(text)
        if hit:
            return _fail("push rejected (R-1042): the tag %s carries an AI attribution line: %r." % (sha[:12], hit[:120]))
    return 0


def main(argv):
    if len(argv) < 3 or argv[1] not in ("commit-msg", "pre-push"):
        return _fail("hook blocked (fail closed): bad call to githook_check.py.")
    try:
        match = _matcher()
    except Exception as exc:
        return _fail("hook blocked (fail closed): the Budgit pattern file could not be loaded (%s)." % exc)
    try:
        if argv[1] == "commit-msg":
            return commit_msg(argv[2], match)
        return pre_push(argv[2], sys.stdin, match)
    except Exception as exc:
        return _fail("hook blocked (fail closed): %s: %s" % (type(exc).__name__, exc))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
