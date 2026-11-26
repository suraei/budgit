"""One safe read wrapper for helpers: `budgit repo-read [--repo <dir>] <verb> [flags] [-- paths]`.

One shared validator (parse, guard_words) is used by the command and by the guard. The program runs under a macOS
sandbox profile that denies every exec except the program itself, all writes except the null device and the network, so a
filter, driver, pager, fsmonitor, gpg or ssh program named in any config cannot run, whatever the config says. The env is
built from scratch and config overrides ride on GIT_CONFIG_COUNT (defence in depth). Nothing here reads repo config files.
"""
import fnmatch
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

import readdoor

PREFIX = readdoor.PREFIX
IS_MAC = sys.platform == "darwin"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
MAX_OUT = 2 * 1024 * 1024
TIMEOUT_S = 20.0
MIN_VERSION = (2, 44)
VERBS = readdoor.VERBS
_DIFFING = ("log", "diff", "show")
_BUILTIN_FORMATS = ("oneline", "short", "medium", "full", "fuller", "reference", "email", "raw")
_SAFE_CHARS = readdoor.SAFE_CHARS
_FLAGS = readdoor.FLAGS
_REVISION = readdoor.WORD
_SIGNATURE = readdoor.SIGNATURE
_TRAVERSAL = readdoor.TRAVERSAL


class Refused(Exception):
    """A refusal; the text starts with the rule name (R-1292 repo-read, or R-1360 read door for a protected path)."""
    def __init__(self, reason):
        Exception.__init__(self, reason if reason.startswith((PREFIX, readdoor.DOOR)) else PREFIX + reason)


def _clean(text):
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise Refused("a control character in an argument")
    return text


def _check_format(value):
    if value in _BUILTIN_FORMATS:
        return
    if "%" not in value:
        raise Refused("only a built-in format name or a format with %% placeholders: %r" % value[:40])
    if _SIGNATURE.search(value) or "%(" in value:
        raise Refused("a format with a signature (%G) or a %( placeholder is refused")


def _split_paths(verb, rest):
    """-> (words before the first `--`, words after it). For grep, the word after -e is a pattern and never the separator."""
    i = 0
    while i < len(rest):
        if rest[i] == "--":
            return rest[:i], rest[i + 1:]
        i += 2 if verb == "grep" and rest[i] == "-e" else 1
    return rest, []


def _revision(verb, word):
    if not _REVISION.fullmatch(word) or _TRAVERSAL.search(word) or ":" in word:       # rev:path names a path, past the exclude pathspecs
        raise Refused("%s: %r is not an allowed revision (paths go after --)" % (verb, word[:60]))


def _parse_grep(rest):
    """-e <pattern> (at least one; it always consumes the next word, so a pattern that starts with - is never a flag), the
    flags of the allowlist, and at most one revision."""
    flags, rev, patterns, i = [], None, 0, 0
    while i < len(rest):
        a = rest[i]
        if a == "-e":
            if i + 1 >= len(rest) or not rest[i + 1] or len(rest[i + 1]) > readdoor.MAX_PATTERN:
                raise Refused("grep: -e needs a pattern of 1 to %d characters" % readdoor.MAX_PATTERN)
            flags += ["-e", rest[i + 1]]
            patterns += 1
            i += 2
            continue
        if a.startswith("-"):
            if not _FLAGS["grep"].fullmatch(a):
                raise Refused("grep: the argument %r is not allowed" % a[:60])
        else:
            if rev is not None:
                raise Refused("grep takes at most one revision")
            _revision("grep", a)
            rev = a
        flags.append(a)
        i += 1
    if not patterns:
        raise Refused("grep needs -e <pattern>")
    return flags


def _parse_lines(rest):
    if rest[:1] == ["--count"]:
        if len(rest) != 2 or not rest[1]:
            raise Refused("lines --count takes one path")
        return rest
    if len(rest) != 3 or not rest[0] or rest[0].startswith("-") or not all(re.fullmatch(r"[1-9]\d{0,8}", n) for n in rest[1:]):
        raise Refused("lines takes <path> <start> <end>, or --count <path>; the numbers start at 1")
    if int(rest[2]) < int(rest[1]):
        raise Refused("lines: the end is before the start")
    return rest


def parse(args):
    """-> (repo or None, verb, flags, paths). Raises Refused. Everything outside the per-verb allowlist is refused."""
    args = [_clean(a) for a in args]
    repo = None
    if args[:1] == ["--repo"]:
        if len(args) < 2:
            raise Refused("--repo needs a folder")
        repo, args = args[1], args[2:]
    if not args or args[0] not in VERBS:
        raise Refused("the verb must be one of %s (--repo goes before the verb)" % ", ".join(VERBS))
    verb, rest = args[0], args[1:]
    rest, paths = _split_paths(verb, rest)
    if paths and verb in ("merge-base", "lines"):
        raise Refused("%s takes no paths" % verb)
    for p in paths:
        if p.startswith(":"):
            raise Refused("a path that starts with : (pathspec magic) is refused: %r" % p[:60])
    if verb == "lines":
        return repo, verb, _parse_lines(rest), []
    if verb == "grep":
        return repo, verb, _parse_grep(rest), paths
    if verb == "ls" and len([a for a in rest if not a.startswith("-")]) > 1:
        raise Refused("ls takes at most one revision")
    flags, i = [], 0
    while i < len(rest):
        a = rest[i]
        if verb == "log" and a == "-n" and i + 1 < len(rest) and re.fullmatch(r"\d+", rest[i + 1]):
            flags += [a, rest[i + 1]]
            i += 2
            continue
        if a.startswith("-"):
            if not _FLAGS[verb].fullmatch(a):
                raise Refused("%s: the argument %r is not allowed" % (verb, a[:60]))
            if a.startswith(("--format=", "--pretty=")):
                _check_format(a.split("=", 1)[1])
        elif verb == "status" or not _REVISION.fullmatch(a) or _TRAVERSAL.search(a) or (verb == "ls" and ":" in a):
            raise Refused("%s: %r is not an allowed revision (paths go after --)" % (verb, a[:60]))
        flags.append(a)
        i += 1
    return repo, verb, flags, paths


def guard_words(command):
    """The words of ONE simple command made only of bare words (letters, digits and -_./:=@^~,+%) and single-quoted words.
    Anything else outside single quotes (a double quote, $, backtick, backslash, !, newline, ; & | < > ( ) { } * ? # ...)
    is refused. A bare word may not start with ~ or =."""
    words, cur, i, n = [], None, 0, len(command)
    while i < n:
        c = command[i]
        if c in " \t":
            if cur is not None:
                words.append(cur)
                cur = None
        elif c == "'":
            end = command.find("'", i + 1)
            if end < 0:
                raise Refused("an unclosed single quote")
            cur = (cur or "") + _clean(command[i + 1:end])
            i = end
        elif c in _SAFE_CHARS:
            if cur is None and c in "~=":
                raise Refused("a word may not start with ~ or =")
            cur = (cur or "") + c
        else:
            raise Refused("the character %r is not allowed outside single quotes" % c)
        i += 1
    if cur is not None:
        words.append(cur)
    return words


def git_argv(program, verb, flags, paths=(), excludes=()):
    """grep and ls take pathspecs with magic (the excludes), so their own paths are marked :(literal) and the literal-pathspec
    env is left off for them (see verb_env). ls with a revision is ls-tree, which has no exclude magic: filter_listing does it."""
    if verb == "grep":
        return [program, "--no-pager", "grep", "--no-color", "--no-textconv", "--no-recurse-submodules"] + list(flags) + \
               ["--"] + [":(literal)" + p for p in paths] + list(excludes)
    if verb == "ls":
        if flags:
            return [program, "--no-pager", "ls-tree", "-r", "--name-only", "--full-name"] + list(flags) + ["--"] + list(paths)
        return [program, "--no-pager", "ls-files", "--"] + [":(literal)" + p for p in paths] + list(excludes)
    extra =["--no-ext-diff", "--no-textconv", "--ignore-submodules=all"] if verb in _DIFFING else []
    tail = ["--"] + list(paths) if paths or verb in _DIFFING else []       # an explicit `--`: the revisions are never read as paths
    return [program, "--no-pager", verb] + extra + list(flags) + tail


# --- the barrier --------------------------------------------------------------------------------------------------

_PROGRAM = [None]


def program():
    """The realpath of the program, resolved once through xcrun and cached; re-resolved when the file is gone."""
    if _PROGRAM[0] and os.path.isfile(_PROGRAM[0]):
        return _PROGRAM[0]
    p = subprocess.run(["/usr/bin/xcrun", "-f", "git"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, timeout=20)
    path = os.path.realpath(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else ""
    if not path or not os.path.isfile(path):
        raise Refused("the program could not be found")
    _PROGRAM[0] = path
    return path


def build_profile(prog):
    return "\n".join(["(version 1)", "(deny default)", "(allow file-read*)", '(allow process-exec (literal "%s"))' % prog,
                      "(allow process-fork)", '(allow file-write* (literal "%s"))' % os.devnull])


def base_env(home):
    return {"PATH": "/usr/bin:/bin", "HOME": home, "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_ATTR_NOSYSTEM": "1",
            "GIT_PAGER": "cat", "PAGER": "cat", "GIT_LITERAL_PATHSPECS": "1"}


def override_env():
    pairs = [("core.fsmonitor", "false"), ("core.hooksPath", os.devnull), ("core.pager", "cat"), ("protocol.allow", "never"),
             ("submodule.recurse", "false"), ("diff.submodule", "short"), ("diff.ignoreSubmodules", "all"),
             ("status.submoduleSummary", "false"), ("log.showSignature", "false"), ("log.diffMerges", "separate"),
             ("gpg.program", "/usr/bin/false"), ("gpg.openpgp.program", "/usr/bin/false"), ("gpg.x509.program", "/usr/bin/false"),
             ("gpg.ssh.program", "/usr/bin/false"), ("core.sshCommand", "/usr/bin/false"), ("credential.helper", ""),
             ("core.alternateRefsCommand", ""), ("diff.external", ""), ("core.attributesFile", os.devnull),
             ("core.excludesFile", os.devnull), ("mailmap.file", os.devnull), ("mailmap.blob", ""), ("safe.bareRepository", "explicit")]
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (k, v) in enumerate(pairs):
        env["GIT_CONFIG_KEY_%d" % i] = k
        env["GIT_CONFIG_VALUE_%d" % i] = v
    return env


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.wait()


def sandbox_run(argv, cwd, env, profile):
    """-> (rc, stdout, stderr) of argv under the profile. A timeout or an output over the cap kills the process group and refuses."""
    proc = subprocess.Popen([SANDBOX_EXEC, "-p", profile] + list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    sel, got = selectors.DefaultSelector(), {proc.stdout: [], proc.stderr: []}
    for f in got:
        sel.register(f, selectors.EVENT_READ)
    deadline, size = time.monotonic() + TIMEOUT_S, 0
    while sel.get_map():
        left = deadline - time.monotonic()
        if left <= 0:
            _kill(proc)
            raise Refused("timed out after %s s" % TIMEOUT_S)
        for key, _ in sel.select(left):
            data = os.read(key.fd, 65536)
            if not data:
                sel.unregister(key.fileobj)
                continue
            got[key.fileobj].append(data)
            size += len(data)
            if size > MAX_OUT:
                _kill(proc)
                raise Refused("output over the limit of %d bytes" % MAX_OUT)
    proc.wait()
    proc.stdout.close()
    proc.stderr.close()
    dec = lambda f: b"".join(got[f]).decode("utf-8", "replace")
    return proc.returncode, dec(proc.stdout), dec(proc.stderr)


def self_check():
    """Three probes on every call, then the version. -> {"program": ..., "version": ...}. Raises Refused when the sandbox cannot apply."""
    if not IS_MAC or not os.path.exists(SANDBOX_EXEC):
        raise Refused("this needs macOS and sandbox-exec; no repository was read")
    prog = program()
    cannot = Refused("sandbox cannot apply (outer sandbox?)")
    home = tempfile.mkdtemp(prefix="repo-read-")
    try:
        env = base_env(home)
        try:
            p1 = subprocess.run([SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"], capture_output=True, timeout=20)
            rc2, _o, err2 = sandbox_run(["/usr/bin/true"], home, env, build_profile(prog))
            subprocess.run([prog, "init", "-q", home + "/probe"], env=env, capture_output=True, timeout=20, check=True)
            rc3, _o, _e = sandbox_run([prog, "status", "--short"], home + "/probe", env, build_profile(prog))
        except (OSError, subprocess.SubprocessError):
            raise cannot
        if p1.returncode != 0 or rc2 != 71 or "execvp() of" not in err2 or "sandbox_apply" in err2 or rc3 != 0:
            raise cannot
        v = subprocess.run([prog, "--version"], capture_output=True, text=True, env=env, timeout=20).stdout
        m = re.search(r"(\d+)\.(\d+)", v)
        if not m or (int(m.group(1)), int(m.group(2))) < MIN_VERSION:
            raise Refused("the program version must be %d.%d or later (found: %s)" % (MIN_VERSION + (v.strip()[:40],)))
        return {"program": prog, "version": v.strip()}
    finally:
        shutil.rmtree(home, True)


def registered_roots():
    """The realpaths of the projects in budgit's own registry (the portfolio list). Empty when it cannot be read."""
    try:
        import portfolio
        return [os.path.realpath(p) for p in portfolio.entries_by_path()]
    except Exception:
        return []


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _in_registry(path):
    return any(_inside(path, root) for root in registered_roots())


def _linked_worktree_of_registered(top, gitdir, common):
    """A worktree whose common git dir sits inside a registered project, and which that repo lists back: its own
    worktrees/<name>/gitdir file names this toplevel's .git. A planted commondir cannot borrow a repo: it is not listed back."""
    wt_admin = os.path.realpath(gitdir)
    if os.path.dirname(wt_admin) != os.path.join(os.path.realpath(common), "worktrees") or not _in_registry(os.path.realpath(common)):
        return False
    try:
        with open(os.path.join(wt_admin, "gitdir"), encoding="utf-8") as f:
            return os.path.realpath(f.read().strip()) == os.path.join(top, ".git")
    except OSError:
        return False


# --- R-1360: the read door's protected reach, the file reader and the listing filter ---------------------------------

def verb_env(home, verb):
    """The scratch env with the overrides. grep and ls use pathspec magic (the excludes), so the literal-pathspec switch is off
    for them; their own paths are marked :(literal) in git_argv instead."""
    env = base_env(home)
    env.update(override_env())
    if verb in readdoor.READ_VERBS:
        env.pop("GIT_LITERAL_PATHSPECS", None)
    return env


def _fold(text):
    import studiolib
    return studiolib.fold_path(text)


def _private_in(parts):
    """True when a folded path holds <data folder name>/private (.studio or .budgit)."""
    return any(parts[i] in readdoor.DATA_NAMES and parts[i + 1] == "private" for i in range(len(parts) - 1))


def _protected_config():
    import studiolib
    cfg = studiolib.load_config()
    if not studiolib.protected_dirs_readable(cfg):
        raise Refused(readdoor.PROTECTED_TEXT)               # fail closed: a protected folder that cannot be read
    return studiolib, cfg


def check_not_protected(real, cwd):
    """Refuse a real path inside a data folder's private/, or inside a protected_dirs entry other than the one the call
    runs in (I-2042: the current protected project stays readable). Compared folded: macOS disks are case-blind."""
    if _private_in([p for p in _fold(real).split(os.sep) if p]):
        raise Refused(readdoor.PROTECTED_TEXT)
    sl, cfg = _protected_config()
    hit = sl.protected_dir(real, cfg)
    if hit and hit != sl.protected_dir(cwd, cfg):
        raise Refused(readdoor.PROTECTED_TEXT)


def protected_rels(top, cwd):
    """Top-relative folders of the other protected projects that sit inside the toplevel."""
    sl, cfg = _protected_config()
    current, rels = sl.protected_dir(cwd, cfg), []
    for entry in cfg.get("protected_dirs") or ():
        path = os.path.expanduser(entry.strip())
        real = os.path.realpath(path if os.path.isabs(path) else os.path.join(os.path.expanduser("~"), path))
        if entry != current and real != top and _inside(real, top):
            rels.append(os.path.relpath(real, top))
    return rels


def exclude_specs(rels):
    """Pathspecs that every grep and ls call carries: every <data>/private/ at any depth, and the other protected projects."""
    return [":(exclude,top,icase,glob)**/%s/private/**" % n for n in readdoor.DATA_NAMES] + \
           [":(exclude,top,icase,literal)" + r for r in rels]


def filter_listing(text, rels):
    """ls-tree has no exclude magic: drop the protected names from its output (folded compare)."""
    folded = [_fold(r).rstrip("/") for r in rels]
    keep = []
    for line in text.splitlines():
        f = _fold(line).lstrip('"')
        if _private_in(f.split("/")) or any(f == r or f.startswith(r + "/") for r in folded):
            continue
        keep.append(line)
    return "".join(l + "\n" for l in keep)


def open_regular(path):
    """Open a resolved path for reading without following a symlink and without blocking on a FIFO, then check the open file:
    a regular file, one link, and the same file the path names now."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise Refused("cannot open %r: %s" % (path[-60:], exc.strerror))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refused("%r is not a regular file" % path[-60:])
        if st.st_nlink != 1:
            raise Refused("%r has more than one link" % path[-60:])
        now = os.lstat(path)
        if os.path.realpath(path) != path or (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise Refused("the path changed while it was opened")
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


SENSITIVE_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "credentials*", "*secret*", ".netrc")


def _sensitive_name(real):
    name = os.path.basename(real).lower()
    return any(fnmatch.fnmatchcase(name, p) for p in SENSITIVE_NAMES)


def read_lines(top, gitdirs, folder, cwd, words, prog, env, profile):
    """`lines <path> <start> <end>` (at most 400 lines) or `lines --count <path>`. Reads no program but the tracked-file check:
    the file is opened here. Only a tracked file is read, and never one whose name looks like a secret."""
    count = words[0] == "--count"
    path = words[1] if count else words[0]
    real = os.path.realpath(os.path.join(folder, path))
    if not _inside(real, top):
        raise Refused("a path outside the repository: %r" % path[:60])
    if ".git" in _fold(os.path.relpath(real, top)).split(os.sep) or any(_inside(real, os.path.realpath(g)) for g in gitdirs):
        raise Refused("the repository's own internals are not readable here")
    check_not_protected(real, cwd)
    if _sensitive_name(real):
        raise Refused("lines does not read a file whose name looks like a secret (.env, *.pem, *.key, id_rsa, credentials...)")
    start = 1 if count else int(words[1])
    end = 0 if count else min(int(words[2]), start + readdoor.MAX_LINES - 1)
    out, n, size = [], 0, 0
    with open_regular(real) as f:
        rc, _, _ = sandbox_run([prog, "--no-pager", "ls-files", "--error-unmatch", "--", os.path.relpath(real, top)], top, env, profile)
        if rc != 0:
            raise Refused("lines reads tracked files only")
        for line in f:
            n += 1
            if not count and start <= n <= end:
                out.append(line)
                size += len(line)
                if size > MAX_OUT:
                    raise Refused("output over the limit of %d bytes" % MAX_OUT)
            if not count and n >= end:
                break
    return "%d\n" % n if count else b"".join(out).decode("utf-8", "replace")


def _revs(verb, flags):
    """The revision words among the parsed flags (grep: the word after -e is a pattern, never a revision)."""
    out, skip = [], False
    for a in flags:
        if skip:
            skip = False
        elif verb == "grep" and a == "-e":
            skip = True
        elif not a.startswith("-"):
            out.append(a)
    return out


def _refusal_text(rc, err, verb):
    first = (err.strip().splitlines() or ["the program failed (rc %d)" % rc])[0]
    if re.search(r"filter .*failed|filter-process", err):
        return PREFIX + "a required filter could not run (for example LFS)"
    return PREFIX + first


def execute(args, cwd=None):
    """-> (rc, stdout, stderr). A refusal is rc 2 with empty stdout and a text starting with the rule name."""
    try:
        return _execute(args, cwd or os.getcwd())
    except Refused as exc:
        return 2, "", str(exc)


def _execute(args, cwd):
    repo, verb, flags, paths = parse(args)
    prog = self_check()["program"]
    folder = os.path.realpath(repo or cwd)
    if not os.path.isdir(folder):
        raise Refused("the repository folder does not exist")
    home = tempfile.mkdtemp(prefix="repo-read-")
    try:
        env = verb_env(home, "status")
        profile = build_profile(prog)
        rc, out, err = sandbox_run([prog, "--no-pager", "rev-parse", "--path-format=absolute", "--show-toplevel", "--absolute-git-dir",
                                    "--git-common-dir"], folder, env, profile)
        lines = out.split("\n")
        if rc != 0 or len(lines) < 3 or not lines[0]:
            raise Refused((err.strip().splitlines() or ["no repository found here"])[0])
        top = os.path.realpath(lines[0])
        if not ((_in_registry(folder) and _in_registry(top)) or _linked_worktree_of_registered(top, lines[1], lines[2])):
            raise Refused("the repository is not inside a project budgit knows")
        reads = verb in readdoor.READ_VERBS
        if reads:
            check_not_protected(folder, cwd)
        if verb == "lines":
            return 0, read_lines(top, lines[1:3], folder, cwd, flags, prog, env, profile), ""
        for a in (_revs(verb, flags) if verb != "merge-base" else ()):
            if os.path.lexists(os.path.join(folder, a)):
                raise Refused("%r is also a path in this folder; name the revision unambiguously" % a[:60])
        for p in paths:
            real = os.path.realpath(os.path.join(folder, p))
            if not _inside(real, top):
                raise Refused("a path outside the repository: %r" % p[:60])
            if reads:
                check_not_protected(real, cwd)
        rels = protected_rels(top, cwd) if reads else []
        if reads:
            env = verb_env(home, verb)
        rc, out, err = sandbox_run(git_argv(prog, verb, flags, paths, exclude_specs(rels) if reads else ()), folder, env, profile)
    finally:
        shutil.rmtree(home, True)
    if verb == "ls" and rc == 0 and flags:
        out = filter_listing(out, rels)
    if verb in ("merge-base", "grep") and rc == 1 and not err.strip():
        return 1, "", ""                                  # not an ancestor, or no match: an answer, not a failure
    if rc != 0:
        return 2, "", _refusal_text(rc, err, verb)
    bad = [l for l in err.splitlines() if "cannot exec" in l or "Operation not permitted" in l]
    if bad:
        return 2, "", PREFIX + err.strip().splitlines()[0]
    return 0, out, err


def main(argv):
    rc, out, err = execute(argv)
    sys.stdout.write(out)
    sys.stderr.write(err if err.endswith("\n") or not err else err + "\n")
    return rc
