"""R-1359: the text door. Free text (a ledger text, a finding, a directive, a TM note, a progress or friction line) goes through
a file (`--text-file PATH`) instead of the command line, so no word or backtick in it meets the shell guard or zsh.

One reader for every verb: `pick(inline, path, project, flag)`. The file is read as UTF-8, the trailing newline is stripped, an
empty file, a missing file, a directory, a text over CAP characters and a symlink pointing outside the scratch folder and the
project are refused. `--text` and `--text-file` together are refused. `dated_append` writes the progress and friction lines."""
import os
import re
import stat
import tempfile
import time

import studiolib as sl

CAP = 20000
FLAG = "--text-file"


def _inside(path, root):
    root = os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _roots(project):
    roots = [project, sl.studio_dir(project), tempfile.gettempdir(), "/tmp"]
    home = os.path.realpath(os.path.expanduser("~"))
    for var in ("TMPDIR", "CLAUDE_CODE_TMPDIR"):
        real = os.path.realpath(os.environ[var]) if os.environ.get(var) else None
        if real and real != os.sep and not _inside(home, real):      # never "/", the home folder or a folder above it
            roots.append(real)
    return roots


def _refuse_hidden(real, project):
    """A path inside the project may not run through a dot folder (.git, .ssh ...) other than the project's own data folder,
    nor through private/. Paths in the temp roots (outside the project) are not touched."""
    base = os.path.realpath(project)
    if not _inside(real, base) or real == base:
        return
    own = os.path.basename(sl.studio_dir(project))
    for part in os.path.relpath(real, base).split(os.sep):
        if part == "private" or (part.startswith(".") and part != own):
            raise sl.StudioError("%s: a path through %s inside the project is not read" % (FLAG, part))


def read(path, project, roots=None):
    """-> the text of the file, trailing newline stripped. Raises StudioError for each refusal. The real path of the whole
    argument (every symlink resolved) must sit inside one of the roots (default: the project, its data folder, the system
    temp folder and /tmp); the resolved file is then opened with O_NOFOLLOW and must be a regular file."""
    path = os.path.abspath(os.path.expanduser(str(path)))
    real = os.path.realpath(path)
    if not any(_inside(real, r) for r in (_roots(project) if roots is None else roots)):
        raise sl.StudioError("%s: %s is outside the project and the scratch folder" % (FLAG, path))
    _refuse_hidden(real, project)
    if os.path.isdir(real):
        raise sl.StudioError("%s: %s is a directory" % (FLAG, path))
    if not os.path.exists(real):
        raise sl.StudioError("%s: no such file: %s" % (FLAG, path))
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)      # NONBLOCK: a FIFO is refused below, never waited on
        with os.fdopen(fd, "rb") as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise sl.StudioError("%s: %s is not a regular file" % (FLAG, path))
            if info.st_uid != os.getuid():
                raise sl.StudioError("%s: %s is not owned by you" % (FLAG, path))
            raw = f.read(CAP * 4 + 1)                      # a UTF-8 character is at most 4 bytes
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise sl.StudioError("%s: cannot read %s as UTF-8 (%s)" % (FLAG, path, exc))
    text = text.rstrip("\r\n")
    if not text.strip():
        raise sl.StudioError("%s: %s is empty" % (FLAG, path))
    if len(text) > CAP:
        raise sl.StudioError("%s: %s is longer than %d characters" % (FLAG, path, CAP))
    return text


def pick(inline, path, project, inline_flag="--text"):
    """-> the inline text, or the file's text, or None when neither was given. Both together raise."""
    if path is None:
        return inline
    if inline is not None:
        raise sl.StudioError("give %s or %s, not both" % (inline_flag, FLAG))
    return read(path, project)


def dated_append(project, name, text, when=None):
    """Append `- YYYY-MM-DD HH:MM text` (newlines folded to spaces) to <data folder>/<name>; -> the path written.
    Same write as the TM log: one O_APPEND write, never through a symlink."""
    line = re.sub(r"\s+", " ", str(text or "")).strip()
    if not line:
        raise sl.StudioError("nothing to write: give --text or %s" % FLAG)
    sdir = sl.ensure_studio_dir(project)
    target = os.path.join(sdir, name)
    sl.append_text(target, "- %s %s\n" % (time.strftime("%Y-%m-%d %H:%M", when or time.localtime()), line))
    return target
