"""R-113 / P6: a per-repo `commit-msg` hook that removes Claude's co-author and "Generated with" lines from commit
messages and adds one `Built-with: Budgit (<url>)` trailer. The url is the plugin's public home (plugin.json
`homepage`, filled in by `budgit export`); without one the hook only strips. Same safety rules as the secrets hook:
never core.hooksPath, never an existing hook, never outside the repo's own .git. The hook is plain POSIX sh: it needs
neither python nor budgit on the PATH, and it is the user's to remove (`budgit watermark off`)."""
import json
import os
import re
import subprocess

import studiolib as sl

MARK = "# budgit-watermark"
TRAILER_KEY = "Built-with"
_URL = re.compile(r"^https://[A-Za-z0-9._~/-]+$")        # baked into a shell script: nothing that could quote or expand
_MODE = re.compile(r"^# budgit-watermark mode=(trailer|strip-only)$", re.M)


def _git(path, *args):
    r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def public_url():
    """The plugin's public home from its manifest, or "" (the private tree has none)."""
    import vendor
    try:
        with open(os.path.join(vendor.plugin_root(), ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            url = json.load(f).get("homepage")
    except (OSError, ValueError):
        return ""
    return url if isinstance(url, str) and _URL.fullmatch(url) else ""


def hook_text(url, mode="trailer"):
    """POSIX sh for .git/hooks/commit-msg. `url` must pass _URL, else the hook only strips."""
    if mode == "trailer" and not (isinstance(url, str) and _URL.fullmatch(url)):
        mode = "strip-only"
    lines = [
        "#!/bin/sh",
        "%s mode=%s" % (MARK, mode),
        "# Installed by `budgit watermark on`; remove it with `budgit watermark off`.",
        "# Deletes Claude's co-author and \"Generated with\" lines from the message"
        + (" and adds one Built-with line." if mode == "trailer" else "."),
        'f="$1"',
        '[ -f "$f" ] || exit 0',
        'tmp=$(mktemp "${f}.XXXXXX") || exit 0',
        "sed -e '/^[Cc][Oo]-[Aa][Uu][Tt][Hh][Oo][Rr][Ee][Dd]-[Bb][Yy]:[[:space:]]*[Cc][Ll][Aa][Uu][Dd][Ee]/d' "
        "-e '/Generated with \\[Claude Code\\]/d' \"$f\" > \"$tmp\" && cat \"$tmp\" > \"$f\"",
        'rm -f "$tmp"',
    ]
    if mode == "trailer":
        lines.append("git interpret-trailers --in-place --if-exists addIfDifferent "
                     "--trailer '%s: Budgit (%s)' \"$f\" || true" % (TRAILER_KEY, url))
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def _target(repo):
    """-> (top, hooks dir, commit-msg path, blocked reason or None)."""
    top = _git(os.path.realpath(repo), "rev-parse", "--show-toplevel")
    if not top:
        raise sl.StudioError("%s is not a git repository" % repo)
    hooks = _git(top, "rev-parse", "--git-path", "hooks")
    hooks = hooks if os.path.isabs(hooks) else os.path.join(top, hooks)
    blocked = "core.hooksPath is set" if _git(top, "config", "--get", "core.hooksPath") else None
    return top, hooks, os.path.join(hooks, "commit-msg"), blocked


def _ours(path):
    """The hook's text when `path` is a regular file Budgit wrote, else None (a symlink is never read through)."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    second = text.split("\n", 2)[1] if text.count("\n") >= 2 else ""
    return text if _MODE.match(second) else None             # the marker is line 2 of our own file, not a mention in someone else's


def install(repo, mode="trailer", url=None):
    """-> installed | already installed | refused: existing commit-msg hook | refused: core.hooksPath is set."""
    _top, hooks, target, blocked = _target(repo)
    if blocked:
        return "refused: " + blocked
    text = hook_text(public_url() if url is None else url, mode)
    if os.path.lexists(target):
        current = _ours(target)
        if current is None:
            return "refused: existing commit-msg hook"
        if current != text:
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
        return "already installed"
    os.makedirs(hooks, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(target, 0o755)
    return "installed"


def remove(repo):
    """-> removed | not installed | refused: not Budgit's hook."""
    _top, _hooks, target, _blocked = _target(repo)
    if not os.path.lexists(target):
        return "not installed"
    if _ours(target) is None:
        return "refused: not Budgit's hook"
    os.remove(target)
    return "removed"


def status(repo):
    """-> on (trailer) | on (strip-only) | off | blocked: core.hooksPath is set | blocked: another commit-msg hook."""
    _top, _hooks, target, blocked = _target(repo)
    if blocked:
        return "blocked: " + blocked
    if not os.path.lexists(target):
        return "off"
    text = _ours(target)
    if text is None:
        return "blocked: another commit-msg hook"
    m = _MODE.search(text)
    return "on (%s)" % (m.group(1) if m else "trailer")


def is_repo_root(project):
    """True when `project` is the top of its own git repository (a sub-folder of someone else's repo is not ours to hook)."""
    top = _git(os.path.realpath(project), "rev-parse", "--show-toplevel")
    return bool(top) and os.path.realpath(top) == os.path.realpath(project)
