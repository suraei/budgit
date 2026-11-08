"""Detect a project's test and lint commands from its files, propose them, and record the approval from a real card answer.

Detection only reads files; it never runs anything. The approval is never written by a command the model runs: the
user's answer to an AskUserQuestion card arrives in the transcript (reconcile_transcript calls on_answer), and only an
answer that picks "Approve checks <digest>" for the exact proposed list moves it into .budgit/project.json."""
import hashlib
import json
import os
import re
import stat

import studiolib as sl

PROPOSAL = "proposed_checks.json"


def _read(path, limit=200000):
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, encoding="utf-8", errors="replace") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return ""
            return f.read(limit)
    except OSError:
        return ""


def _has(project, *names):
    return any(os.path.isfile(os.path.join(project, n)) for n in names)


def _test_files(project):
    out = []
    for sub in ("tests", "test"):
        d = os.path.join(project, sub)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        out += [os.path.join(d, n) for n in names if re.match(r"(test_.*|.*_test)\.py$", n)]
    return out


def detect(project):
    """-> the list of commands (strings) that look right for this project, from its files only."""
    cmds = []
    pyproject = _read(os.path.join(project, "pyproject.toml"))
    tests = _test_files(project)
    if (_has(project, "pytest.ini") or "[tool.pytest" in pyproject or "[tool:pytest]" in _read(os.path.join(project, "setup.cfg"))
            or "[pytest]" in _read(os.path.join(project, "tox.ini"))):
        cmds.append("python3 -m pytest")
    elif tests:
        if any(re.search(r"^\s*(import unittest|from unittest)", _read(t), re.M) for t in tests[:5]):
            cmds.append("python3 -m unittest discover -s " + os.path.basename(os.path.dirname(tests[0])))
        else:
            cmds.append("python3 -m pytest")
    if _has(project, "ruff.toml", ".ruff.toml") or "[tool.ruff" in pyproject:
        cmds.append("ruff check .")
    try:
        scripts = (json.loads(_read(os.path.join(project, "package.json"))) or {}).get("scripts") or {}
    except (ValueError, AttributeError):
        scripts = {}
    if isinstance(scripts, dict):
        test = scripts.get("test")
        if isinstance(test, str) and "no test specified" not in test:
            cmds.append("npm test")
        if isinstance(scripts.get("lint"), str):
            cmds.append("npm run lint")
    if _has(project, "go.mod"):
        cmds.append("go test ./...")
    if _has(project, "Cargo.toml"):
        cmds.append("cargo test")
    if re.search(r"^test\s*:", _read(os.path.join(project, "Makefile")) or _read(os.path.join(project, "makefile")), re.M):
        cmds.append("make test")
    return cmds


def _digest(project, name, checks):
    return hashlib.sha256(json.dumps([os.path.realpath(project), name, checks]).encode("utf-8")).hexdigest()


def _raw_config(project):
    try:
        data = json.loads(sl.read_text(os.path.join(sl.studio_dir(project), "project.json")).lstrip(chr(0xFEFF)))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def propose(project):
    """Write the proposal for a project whose checks list or name is empty. -> the proposal, or None when nothing is missing."""
    if os.path.islink(sl.studio_dir(project)):
        return None
    cfg = sl.load_project_config(project)
    have = (cfg.get("verify") or {}).get("checks") or []
    name = cfg.get("name") or ""
    if have and name:
        return None
    checks = list(have) or detect(project)
    if not checks and name:
        return None
    name = name or os.path.basename(os.path.realpath(project))
    prop = {"name": name, "checks": checks, "digest": _digest(project, name, checks)}
    path = os.path.join(sl.studio_dir(project), PROPOSAL)
    try:
        if _read_json(path).get("digest") != prop["digest"]:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            sl.atomic_write(path, json.dumps(prop, indent=2) + "\n")
    except OSError:
        return None
    return prop


def _read_json(path):
    try:
        data = json.loads(_read(path))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def offer_line(project):
    """What the session should do about a pending proposal: ask ONE card. Empty when nothing is pending."""
    prop = propose(project)
    if not prop:
        return ""
    short = prop["digest"][:12]
    return ("budgit: this project has no verifier checks%s. Ask the user ONE AskUserQuestion card (in their language) that lists "
            "these commands as the question text: %s. Options: label \"Approve checks %s\" and label \"Not now\". Do not run "
            "anything and do not write project.json yourself; the user's answer records it. If unanswered, it is offered again "
            "next session."
            % ("" if not prop["name"] else " (project name %s)" % prop["name"], "; ".join(prop["checks"]) or "(none found)", short))


def on_answer(project, result):
    """A card answer from the transcript. `result` is the structured toolUseResult the harness records beside the
    AskUserQuestion tool_result ({"questions": [...], "answers": {question: label}}). Records the checks only when some
    answer VALUE is exactly "Approve checks <digest12>" for the list now proposed; the question text is never read, and
    without the structured form nothing is approved. -> True when recorded."""
    if os.path.islink(sl.studio_dir(project)):
        return False
    prop = _read_json(os.path.join(sl.studio_dir(project), PROPOSAL))
    if not prop or prop.get("digest") != _digest(project, prop.get("name"), prop.get("checks")):
        return False
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        return False
    want = "Approve checks " + prop["digest"][:12]
    shown = {}
    for q in (result.get("questions") if isinstance(result.get("questions"), list) else []):
        if isinstance(q, dict) and isinstance(q.get("question"), str):
            shown[q["question"]] = q["question"]
    for key, answer in answers.items():
        if isinstance(answer, str) and answer == want:
            text = shown.get(key, "")
            cmds = prop.get("checks")
            if not isinstance(cmds, list) or any(not isinstance(c, str) or c not in text for c in cmds):
                continue                      # the card did not list what would be recorded
            cfg = _raw_config(project)
            verify = cfg.get("verify") if isinstance(cfg.get("verify"), dict) else {}
            verify["checks"] = list(prop["checks"])
            cfg["verify"] = verify
            if not cfg.get("name"):
                cfg["name"] = prop["name"]
            sl.atomic_write(os.path.join(sl.studio_dir(project), "project.json"), json.dumps(cfg, indent=2) + "\n")
            sl.audit(project, "verifier checks approved by card: %s" % "; ".join(prop["checks"]))
            return True
    return False


def scan_all(config=None):
    """Update step: every Budgit project under the projects root whose checks or name are empty gets a proposal file.
    -> the project folders that have one. Protected folders are skipped."""
    config = config or sl.load_config()
    root = config.get("projects_root")
    found = []
    if not root:
        return found
    root = os.path.realpath(os.path.expanduser(root))
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for n in names:
        p = os.path.join(root, n)
        if os.path.islink(sl.studio_dir(p)):
            continue
        if not os.path.isfile(os.path.join(sl.studio_dir(p), "project.json")) or sl.protected_dir(p):
            continue
        try:
            if propose(p):
                found.append(p)
        except Exception:
            continue
    return found
