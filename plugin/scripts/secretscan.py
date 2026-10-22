"""`budgit secrets`: local secret scan (gitleaks) + offline dependency scan (osv-scanner).
Never installs anything, never sends code or findings anywhere, never verifies a secret against a live API.
Output passes through gitleaks --redact AND sl.redact before it is printed."""
import os
import re
import shutil
import subprocess

import studiolib as sl

LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
             "requirements.txt", "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "go.sum",
             "Cargo.lock", "Gemfile.lock", "composer.lock", "pubspec.lock", "mix.lock"}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".studio", ".budgit", "dist", "build"}
_MAX_LINES = 60
HOOK_MARK = "budgit secrets pre-commit"
CHAIN_LINE = "budgit secrets --staged || exit 1"


def _git(path, *args):
    r = subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def lockfiles(path):
    found = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        found += [os.path.join(root, f) for f in files if f in LOCKFILES]
    return found


def _run(argv, cwd):
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return 124, "timed out after 900 s"
    return r.returncode, sl.redact((r.stdout or "") + (r.stderr or ""))


def _show(text):
    lines = [l for l in text.splitlines() if l.strip()]
    for line in lines[:_MAX_LINES]:
        print("    " + line)
    if len(lines) > _MAX_LINES:
        print("    ... %d more lines (rerun the tool by hand to see them)" % (len(lines) - _MAX_LINES))


def _gitleaks(path, staged, in_git):
    if staged:
        argv = ["gitleaks", "git", "--pre-commit", "--staged"]
    else:
        argv = ["gitleaks", "git" if in_git else "dir"]
    code, out = _run(argv + ["--redact", "--no-banner", "--verbose", path], path)
    if code == 0:
        print("gitleaks: no leaks")
        return 0
    if code == 1:
        print("gitleaks: LEAKS FOUND (values redacted; fix or add a .gitleaksignore entry):")
        _show(out)
        return 1
    print("gitleaks: error (exit %d):" % code)
    _show(out)
    return 2


def _osv_major():
    _, out = _run(["osv-scanner", "--version"], None)
    m = re.search(r"(\d+)\.\d+", out)
    return int(m.group(1)) if m else 2


def _osv(path):
    flag = "--offline" if _osv_major() >= 2 else "--experimental-offline"
    code, out = _run(["osv-scanner", "scan", flag, "-r", path], path)
    if code in (0, 128):                    # 128 = no packages found
        print("osv-scanner: no known vulnerabilities (offline database)")
        return 0
    if code == 1:
        print("osv-scanner: VULNERABILITIES FOUND:")
        _show(out)
        return 1
    print("osv-scanner: error (exit %d) — the offline database may be missing; refreshing it downloads from "
          "osv.dev, so only when you choose to:" % code)
    _show(out)
    return 2


def scan(path, staged=False):
    """Returns 0 clean or skipped, 1 findings, 2 a tool failed. Missing tools never fail the run."""
    path = os.path.realpath(path)
    in_git = _git(path, "rev-parse", "--show-toplevel") is not None
    print("budgit secrets: %s (%s)" % (path, "staged changes" if staged else ("git history" if in_git else "directory")))
    missing, results, skipped = [], [], []
    if shutil.which("gitleaks"):
        results.append(_gitleaks(path, staged, in_git))
    else:
        print("gitleaks: skipped (not installed)")
        missing.append("gitleaks")
        skipped.append("gitleaks (not installed)")
    if staged:
        print("osv-scanner: skipped with --staged (run: budgit secrets)")
        skipped.append("osv-scanner (--staged)")
    elif not lockfiles(path):
        print("osv-scanner: skipped (no lockfiles)")
        skipped.append("osv-scanner (no lockfiles)")
    elif shutil.which("osv-scanner"):
        results.append(_osv(path))
    else:
        print("osv-scanner: skipped (not installed)")
        missing.append("osv-scanner")
        skipped.append("osv-scanner (not installed)")
    if skipped:
        print("skipped: %s \u2014 that part was NOT scanned" % ", ".join(skipped))
    if missing:
        print("install yourself (nothing was installed): brew install %s" % " ".join(missing))
    return max(results or [0])


def _hook_text():
    cli = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "bin", "budgit"))
    return ("#!/bin/sh\n# %s (installed by `budgit secrets --install-hook`; remove this file to opt out)\n"
            "if ! command -v gitleaks >/dev/null 2>&1; then\n"
            "  echo 'budgit secrets: BLOCKED \u2014 gitleaks is not installed, so staged changes were NOT scanned. "
            "Install it: brew install gitleaks (or remove .git/hooks/pre-commit to opt out)' >&2\n  exit 1\nfi\n"
            "BUDGIT=\"$(command -v budgit || command -v studio || true)\"\n[ -n \"$BUDGIT\" ] || BUDGIT='%s'\n"
            "if [ ! -x \"$BUDGIT\" ]; then echo 'budgit secrets: skipped (Budgit CLI not found)'; exit 0; fi\n"
            "exec \"$BUDGIT\" secrets --staged\n" % (HOOK_MARK, cli.replace("'", "")))


def install_hook(path):
    """Per-repo, opt-in: .git/hooks/pre-commit of a Budgit project. Never core.hooksPath, never overwrites."""
    top = _git(os.path.realpath(path), "rev-parse", "--show-toplevel")
    if not top:
        raise sl.StudioError("%s is not a git repository" % path)
    if not os.path.isfile(os.path.join(sl.studio_dir(top), "ledger.json")):
        raise sl.StudioError("%s is not a Budgit project (run: budgit activate %s)" % (top, top))
    hooks_path = _git(top, "config", "--get", "core.hooksPath")
    if hooks_path:
        print("budgit secrets: refused — core.hooksPath is set (%s), so a per-repo hook would not run. "
              "Add this line to a pre-commit hook there yourself: %s" % (hooks_path, CHAIN_LINE))
        return 1
    hooks = _git(top, "rev-parse", "--git-path", "hooks")
    hooks = os.path.join(top, hooks) if not os.path.isabs(hooks) else hooks
    target = os.path.join(hooks, "pre-commit")
    if os.path.lexists(target):
        print("budgit secrets: refused — %s already exists (left untouched). To chain, add this line to it "
              "before its last command: %s" % (target, CHAIN_LINE))
        return 1
    os.makedirs(hooks, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(_hook_text())
    os.chmod(target, 0o755)
    print("budgit secrets: installed %s (runs `budgit secrets --staged` before each commit in this repo only)"
          % target)
    return 0
