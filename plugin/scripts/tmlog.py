"""R-384: `budgit tm log TEXT` -- one dated line on the TM's running log, instead of a hand `echo >> status.md` (180 of those in
three days). The file is `<data dir>/private/tm/status.md` of the project; `private/` is git-ignored as retro-mine does.
One line per call: newlines in TEXT become spaces. The line is `YYYY-MM-DD HH:MM text` in the machine's local time, written with
one O_APPEND write, so concurrent loggers never interleave inside a line."""
import os
import re
import time

import studiolib as sl

NAME = "status.md"


def path(project):
    return os.path.join(sl.studio_dir(project), "private", "tm", NAME)


def line(text, when=None):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise sl.StudioError("nothing to log: give the text after `budgit tm log`")
    return "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M", when or time.localtime()), text)


def append(project, text, when=None):
    """-> the path written. Refuses an empty text; never follows a symlink."""
    entry = line(text, when)
    import retromine
    sl.ensure_studio_dir(project)
    retromine.ensure_ignored(project)
    target = path(project)
    folder = os.path.dirname(target)
    for part in (os.path.dirname(folder), folder):                      # private/ and private/tm/ must be real folders too
        if os.path.islink(part):
            raise sl.StudioError("refusing to log through a symlinked folder: %s" % part)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    sl.append_text(target, entry)
    return target
