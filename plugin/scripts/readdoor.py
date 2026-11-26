"""R-1360: the tables of the read door (`budgit repo-read`): the allowed flags per verb, the word regex and the shared limits.

Pure data and patterns, no I/O. repo_read.py (the command) and studiolib.py (the guard) import them, so there is one list.
"""
import re

COMMAND = "repo-read"
PREFIX = "R-1292 repo-read: "                       # the rule name every refusal of the command starts with
DOOR = "R-1360 read door: "                         # the rule name of the protected-path refusal
PROTECTED_TEXT = DOOR + "protected path"
GIT_VERBS = ("status", "log", "diff", "show", "merge-base")
READ_VERBS = ("grep", "ls", "lines")
VERBS = GIT_VERBS + READ_VERBS
MAX_LINES = 400
MAX_PATTERN = 500
SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./:=@^~,+%")
DATA_NAMES = (".studio", ".budgit")                 # the data folder names; a `private/` inside one is protected

_COMMON = (r"--stat|--name-only|--name-status|-p|--patch|-U\d+|--unified=\d+|--no-patch|--no-color|--numstat|--shortstat|-w|"
           r"--no-renames|--diff-filter=[A-Za-z]+|-[SG].+|--date=[A-Za-z0-9:-]+|--abbrev-commit|--word-diff(=[a-z]+)?|"
           r"--format=.*|--pretty=.*")
_RAW = {
    "status": r"-s|--short|-b|--branch|--porcelain(=v1|=v2)?|-u(no|normal|all)?|--untracked-files(=(no|normal|all))?|--ignored|--ahead-behind",
    "log": _COMMON + r"|--oneline|--graph|--decorate|--all|--first-parent|--no-merges|--merges|--follow|--reverse|--since=.+|--until=.+|"
                     r"--author=.+|--grep=.+|--max-count=\d+|--skip=\d+|-\d+|-n\d+",
    "diff": _COMMON + r"|--cached|--staged",
    "show": _COMMON + r"|--oneline|--cached|--staged",
    "merge-base": r"--is-ancestor|--all|--fork-point",
    "grep": r"-n|-i|-l|-c|-w|-F|-E",                # -e <pattern> is handled by the parser: it always consumes the next word
    "ls": r"(?!)",                                  # no flags at all
    "lines": r"--count",
}
FLAGS = {k: re.compile(v) for k, v in _RAW.items()}
WORD = re.compile(r"[A-Za-z0-9_@^~][A-Za-z0-9_.@{}/:^~,+=%-]*")      # a revision word
TRAVERSAL = re.compile(r"(^|[/:])\.\.(/|$)")                         # a revision never holds a `..` path segment
SIGNATURE = re.compile(r"%[-+ ]*G")

# ---- Task B: the closed list of budgit read verbs a helper may run, and the dry-run rows ----
NO_DRY_ROW = DOOR + "no dry-run row"
TOPIC = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")     # a word argument: [a-z0-9-]{1,40}, no leading `-` (it would read as a flag), no `/`, no `..`
RI_ID = re.compile(r"[RI]-\d+", re.ASCII)
I_ID = re.compile(r"I-\d+", re.ASCII)


class Refused(Exception):
    """A refusal of the read door; its text always starts with the rule name (DOOR)."""


# Each row pins argv exactly: the verb words, then the positionals (in order, each one a shape), then the flags in the order
# listed: (flag, shape of its value or None, required). A flag outside its row, a duplicate, the `--flag=value` form and a
# path-like argument are refused. A verb that is not in plugin/bin/budgit has no row (`budgit help` is not a verb).
READ_ROWS = (
    {"name": "status", "words": ("status",), "args": (), "flags": ()},
    {"name": "ledger list", "words": ("ledger", "list"), "args": (), "flags": (("--open", None, False),)},
    {"name": "ledger show", "words": ("ledger", "show"), "args": (RI_ID,), "flags": ()},
    {"name": "ledger top", "words": ("ledger", "top"), "args": (), "flags": ()},
    {"name": "rules show", "words": ("rules", "show"), "args": (TOPIC,), "flags": ()},
    {"name": "skills why", "words": ("skills", "why"), "args": (),
     "flags": (("--no-log", None, True), ("--job", TOPIC, False))},
    {"name": "help flag", "words": ("--help",), "args": (), "flags": ()},
)
NEVER = ("--text-file", "--out")


def _pathlike(word):
    return "/" in word or ".." in word or word.startswith(("~", ".")) or "\\" in word


def read_row(args):
    """-> the row name when `args` (the words after `budgit`) is exactly one of the read rows; None when the first words are no
    row's verb (the old judgement stands); raises Refused (text starts with DOOR) when the verb is a row's but the rest is not."""
    args = list(args)
    for row in READ_ROWS:
        n = len(row["words"])
        if tuple(args[:n]) != row["words"]:
            continue
        tail = args[n:]
        for word in tail:
            if word.split("=", 1)[0] in NEVER:
                raise Refused(DOOR + "%s is not allowed for %s" % (word.split("=", 1)[0], row["name"]))
        for shape in row["args"]:
            if not tail:
                raise Refused(DOOR + "%s needs its argument" % row["name"])
            word, tail = tail[0], tail[1:]
            if not shape.fullmatch(word):
                raise Refused(DOOR + ("%s: an argument that names a path" if _pathlike(word) else "%s: the argument has the wrong shape") % row["name"])
        seen = set()
        for flag, value, required in row["flags"]:
            if tail and tail[0] == flag:
                seen.add(flag)
                tail = tail[1:]
                if value is not None:
                    if not tail or not value.fullmatch(tail[0]):
                        raise Refused(DOOR + ("%s %s: an argument that names a path" if tail and _pathlike(tail[0]) else "%s %s: the value is missing or has the wrong shape") % (row["name"], flag))
                    tail = tail[1:]
            elif required:
                raise Refused(DOOR + "%s needs %s" % (row["name"], flag))
        if tail:
            word = tail[0]
            if _pathlike(word):
                raise Refused(DOOR + "%s: an argument that names a path" % row["name"])
            if word.startswith("-"):
                raise Refused(DOOR + "%s: the flag %s is not in its row (or is repeated)" % (row["name"], word.split("=", 1)[0]))
            raise Refused(DOOR + "%s: an extra argument" % row["name"])
        return row["name"]
    return None


# The dry-run rows: one per verb. {"argv": the exact argv of the dry form, "owner": who approves a change, "test": the test that
# proves the run changes nothing}. The dry form of a verb is the one that writes nothing: a verb with a --dry-run flag gets a row
# whose argv ends in it (none yet: the --dry-run verbs wait); a verb with no flag gets a row of its exact form. A row is added only
# after the PM Budgit approves it, with a fixture in tests/test_r1360_read_rows.py that puts the verb in a state where it would act,
# and a snapshot proof that the real CLI changes nothing in a temp HOME, data folder and project.
# housekeeping was left out because its status check refreshes a worktree index (under .git/worktrees), which is a write.
# relocate plan was left out because it touches the source repo's .git folder (its snapshot changed).
DRY_ROWS = ()


def dry_form_check(args):
    """The dry form of a verb is the one that writes nothing (a row whose argv holds no --dry-run). -> the row when `args` equals
    its argv exactly; None when `args` is not that verb's form (the old judgement stands); raises Refused (text starts with DOOR)
    when `args` begin with the row's argv and carry --apply or --yes, which stay refused."""
    args = list(args)
    for row in DRY_ROWS:
        argv = list(row["argv"])
        if "--dry-run" in argv:
            continue
        if args == argv:
            return row
        if args[:len(argv)] == argv and any(a in ("--apply", "--yes") or a.startswith(("--apply=", "--yes=")) for a in args[len(argv):]):
            raise Refused(DOOR + "%s: --apply and --yes are refused on a dry-run row" % " ".join(argv))
    return None


def has_dry_run(args):
    return any(a == "--dry-run" or a.startswith("--dry-run=") for a in args)


def dry_run_check(args):
    """-> the dry-run row that `args` equals exactly; raises Refused (text starts with DOOR) otherwise."""
    args = list(args)
    if any(a.startswith("--dry-run=") for a in args):
        raise Refused(DOOR + "--dry-run takes no value (--dry-run=false is refused)")
    if any(a in ("--apply", "--yes") or a.startswith(("--apply=", "--yes=")) for a in args):
        raise Refused(DOOR + "--dry-run together with --apply or --yes is refused")
    flagged = [row for row in DRY_ROWS if row["argv"][-1] == "--dry-run"]        # the rows that name the flag (the others are dry_form_check's)
    for row in flagged:
        if tuple(args) == tuple(row["argv"]):
            return row
    verb = [a for a in args if not a.startswith("-")][:1]
    for row in flagged:
        if verb and verb[0] == row["argv"][0] and args[:len(row["argv"]) - 1] == list(row["argv"][:-1]):
            raise Refused(NO_DRY_ROW + " for these arguments (the row pins its argv exactly)")
    raise Refused(NO_DRY_ROW)
