"""R-283 S4: `budgit upgrade [<project>|--all] --check | --apply | --undo <step>` (plan 4.2).

Four numbered, idempotent steps bring a project's data and rules to the installed Budgit, in this order:
  u1 data folder  `.studio` -> `.budgit` (a stray `.budgit/verify` is merged first; `studiolib.migrate_dir` is reused)
  u2 CLAUDE.md    `studio activate`, `studio-kickoff`, `.studio/` -> the budgit names, on lines that name Budgit
  u3 project.json missing default fields are added, never overwritten; a missing name or empty checks only get flagged
  u4 card stamp   the card records the Budgit and rules versions
u2, u3 and u4 write inside the data folder or name it, so they wait for u1.

`--check` changes nothing. `--apply` is skip-not-force: a project with a chat open in it, activity in the last 5 minutes,
uncommitted or staged changes in a path a step touches, or a step that refuses, is skipped with a reason code and retried
next run; the steps after a skipped one are not run. Every step first saves the bytes it will touch to a record in the Budgit
home (0600, keyed by a hash, never inside the project), checks the result afterwards and undoes itself if the check fails,
and makes one commit with explicit paths when git tracks any of them (no hooks, no trailer). `--undo <project> <step>` puts
the saved bytes back and reverses the rename, so the project is byte-identical to before.

A protected project shows step ids and reason codes only, in every output.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

import card
import livework
import portfolio
import studiolib as sl
import updatelib

VERSION = 1
RULES_VERSION = "1"
IDS = ("u1", "u2", "u3", "u4")
QUIET_S = 300
TIMEOUT_S = 6
BUDGET_S = 60
# release-check's compat guard: what a release may stop supporting, and the step after which no project needs it.
# None = there is no per-project step (it cannot be guarded: dropping it needs a human decision).
COMPAT = {"dotstudio": "u1", "studio-names": "u2", "studio-env": None}
_RULES = ((re.compile(r"(?<![\w-])studio activate(?![\w-])"), "budgit activate"),  # compat: the old command name
          (re.compile(r"(?<![\w-])studio-kickoff(?![\w-])"), "budgit-kickoff"),
          (re.compile(r"(?<![\w./~-])\.studio/"), ".budgit/"))
# a line "names Budgit" when it says the product, one of its two old commands, or a file only Budgit keeps in `.studio/`
_NAMES = re.compile(r"budgit|studio activate|studio-kickoff|\.studio/(?:ledger|project\.json|inbox|state|progress|verify|reports|retro|private)",
                    re.I)
_deadline = None


class Refused(Exception):
    """A step that cannot run safely; `code` is the only text a protected project ever shows."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- small helpers

def _git(project, *args):
    left = TIMEOUT_S if _deadline is None else min(TIMEOUT_S, _deadline - time.monotonic())
    if left <= 0:
        return None
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        out = subprocess.run(["git", "-C", project, *args], capture_output=True, text=True, timeout=left, env=env,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return out.returncode, out.stdout


def _is_git(project):
    return os.path.isdir(os.path.join(project, ".git"))


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _write(path, text):
    """atomic_write, keeping the mode the file had (the temp file it renames over the target is 0600)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = None
    sl.atomic_write(path, text)
    if mode is not None:
        os.chmod(path, mode)


def key(project):
    return hashlib.sha256(os.path.realpath(project).encode("utf-8")).hexdigest()[:16]


def _record_path(project, step):
    return os.path.join(sl.home_state(), "upgrade", key(project), step + ".json")


def _running():
    return updatelib.running_version() or "unknown"


# ---------------------------------------------------------------- the steps: check

def _stray_verify(project):
    """Files of a stray `.budgit/verify` when `.budgit` holds nothing else (else None), as paths under verify/."""
    new = os.path.join(project, sl.DATA_NAME)
    files = []
    for root, dirs, names in os.walk(new):
        rel = os.path.relpath(root, new)
        if rel != "." and rel.split(os.sep)[0] != "verify":
            return None
        if rel == "." and any(n for n in names):
            return None
        for n in names:
            if os.path.islink(os.path.join(root, n)):
                return None
            files.append(os.path.relpath(os.path.join(root, n), os.path.join(new, "verify")))
        if rel == ".":
            dirs[:] = [d for d in dirs if d == "verify"]
            if any(os.path.islink(os.path.join(root, d)) for d in dirs):
                return None
    return sorted(files)


def check_u1(project):
    old, new = os.path.join(project, sl.OLD_DATA_NAME), os.path.join(project, sl.DATA_NAME)
    if not os.path.lexists(old):
        return {"state": "current", "changes": 0}
    if os.path.islink(old) or os.path.islink(new):
        return {"state": "refused", "reason": "symlink"}
    if not os.path.lexists(new):
        return {"state": "pending", "changes": 1}
    stray = _stray_verify(project)
    if stray is None:
        return {"state": "refused", "reason": "both"}
    for rel in stray:
        mine, theirs = os.path.join(new, "verify", rel), os.path.join(old, "verify", rel)
        if os.path.exists(theirs) and _read(theirs) != _read(mine):
            return {"state": "refused", "reason": "collision"}
    return {"state": "pending", "changes": len(stray) + 1}


def _claude_md(project):
    """(path, text); text is None when there is no CLAUDE.md. Raises Refused for a symlink or a file that is not UTF-8."""
    path = os.path.join(project, "CLAUDE.md")
    if os.path.islink(path):
        raise Refused("symlink")
    data = _read(path)
    if data is None:
        return path, None
    try:
        return path, data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("unreadable")


def rewrite_names(text):
    """(new text, number of lines changed): only lines that name Budgit change."""
    out, n = [], 0
    for line in text.split("\n"):
        new = line
        if _NAMES.search(line):
            for rx, repl in _RULES:
                new = rx.sub(repl, new)
        n += new != line
        out.append(new)
    return "\n".join(out), n


def check_u2(project):
    try:
        path, text = _claude_md(project)
    except Refused as exc:
        return {"state": "refused", "reason": exc.code}
    if text is None:
        return {"state": "current", "changes": 0}
    n = rewrite_names(text)[1]
    return {"state": "pending", "changes": n} if n else {"state": "current", "changes": 0}


def _project_json(project):
    return os.path.join(sl.studio_dir(project), "project.json")


BOM = b"\xef\xbb\xbf"


def _style(raw):
    """How the file is written (BOM, indent, escapes, separators, final newline), so u3 adds its keys and changes nothing else."""
    text = (raw[len(BOM):] if raw.startswith(BOM) else raw).decode("utf-8", "replace")
    indent = re.search(r'\n([ \t]+)"', text)
    compact = re.search(r'"\s*:\S', text) is not None
    return {"bom": raw.startswith(BOM),
            "indent": (indent.group(1) if indent else None) if "\n" in text.strip() else None,
            "ascii": re.search(r"\\u[0-9a-fA-F]{4}", text) is not None,
            "sep": (",", ":") if compact else None,
            "newline": text.endswith("\n")}


def _dump(cfg, raw):
    """cfg as JSON text in the style of `raw` (the bytes the file had), or the Budgit default style for a new file."""
    st = _style(raw) if raw else {"bom": False, "indent": "  ", "ascii": False, "sep": None, "newline": True}
    sep = st["sep"] if st["indent"] is None and st["sep"] else (",", ": ") if st["indent"] is not None else None
    text = json.dumps(cfg, indent=st["indent"], ensure_ascii=st["ascii"], separators=sep)
    return ("\ufeff" if st["bom"] else "") + text + ("\n" if st["newline"] else "")


def _merged_config(project):
    """(config with defaults filled in, number of fields added, attention codes) or raises Refused."""
    raw = _read(_project_json(project))
    if raw is None:
        cfg = {}
    else:
        try:
            cfg = json.loads((raw[len(BOM):] if raw.startswith(BOM) else raw).decode("utf-8"))
            json.dumps(cfg, ensure_ascii=False).encode("utf-8")     # a lone \ud800 escape parses but cannot be written back
        except (ValueError, RecursionError):                        # UnicodeError is a ValueError
            raise Refused("unreadable")
        if not isinstance(cfg, dict):
            raise Refused("unreadable")
    added = 0
    for k, v in json.loads(json.dumps(sl.DEFAULT_PROJECT)).items():
        if k not in cfg:
            cfg[k] = v
            added += 1
        elif isinstance(v, dict) and isinstance(cfg[k], dict):
            for kk, vv in v.items():
                if kk not in cfg[k]:
                    cfg[k][kk] = vv
                    added += 1
    attention = []
    if not (isinstance(cfg.get("verify"), dict) and cfg["verify"].get("checks")):
        attention.append("no_checks")
    if not (isinstance(cfg.get("name"), str) and cfg["name"].strip()):
        attention.append("no_name")
    return cfg, added, attention


def check_u3(project):
    try:
        cfg, added, attention = _merged_config(project)
    except Refused as exc:
        return {"state": "refused", "reason": exc.code}
    out = {"state": "pending" if added else "current", "changes": added}
    if attention:
        out["attention"] = attention
    return out


def check_u4(project):
    rec = card.read(project)
    want = (_running(), RULES_VERSION)
    if rec is not None and (rec.get("budgit_version"), rec.get("rules_version")) == want:
        return {"state": "current", "changes": 0}
    return {"state": "pending", "changes": 1}


CHECKS = {"u1": check_u1, "u2": check_u2, "u3": check_u3, "u4": check_u4}


def check(project):
    """[{id, state, changes, ...}] for the four steps: current | pending | refused | blocked (waiting for u1)."""
    steps, u1 = [], None
    for sid in IDS:
        if sid != "u1" and u1 is not None and u1["state"] != "current":
            steps.append({"id": sid, "state": "blocked", "changes": 0})
            continue
        res = dict(CHECKS[sid](project), id=sid)
        if sid == "u1":
            u1 = res
        steps.append(res)
    return steps


def behind(steps):
    return any(s["state"] in ("pending", "blocked", "refused") for s in steps)


# ---------------------------------------------------------------- what a step touches, and the saved record

def _pathspecs(project, sid):
    if sid == "u1":
        return [sl.OLD_DATA_NAME, sl.DATA_NAME, ".gitignore"]
    if sid == "u2":
        return ["CLAUDE.md"]
    data = os.path.basename(sl.studio_dir(project))
    if sid == "u3":
        return [data + "/project.json"]
    return [data + "/private/card.json", ".gitignore"]


def _files(project, sid):
    """Files whose bytes are saved (relative to the project): the ones a step writes outside a renamed folder."""
    if sid == "u1":
        return [".gitignore", ".git/info/exclude"]
    if sid == "u2":
        return ["CLAUDE.md"]
    data = os.path.basename(sl.studio_dir(project))
    if sid == "u3":
        return [data + "/project.json"]
    return [data + "/private/card.json", ".gitignore"]


def _snapshot(project, sid):
    snap = {}
    for rel in _files(project, sid):
        data = _read(os.path.join(project, rel))
        snap[rel] = None if data is None else base64.b64encode(data).decode("ascii")
    return snap


def _hashes(project, sid):
    return {rel: _sha(_read(os.path.join(project, rel))) for rel in _files(project, sid)}


def _save_record(project, sid, rec):
    path = _record_path(project, sid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    sl.atomic_write(path, json.dumps(rec, indent=1) + "\n")
    os.chmod(path, 0o600)


def _load_record(project, sid):
    try:
        with open(_record_path(project, sid), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("step") == sid else None


def _drop_lock(project, rec):
    if rec.get("lock_new"):
        try:
            os.remove(os.path.join(sl.studio_dir(project), ".lock"))
        except OSError:
            pass


def _restore(project, snap):
    for rel, enc in snap.items():
        path = os.path.join(project, rel)
        if enc is None:
            try:
                os.remove(path)
            except OSError:
                pass
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(base64.b64decode(enc))


# ---------------------------------------------------------------- the steps: apply and undo

def _apply_u1(project, rec):
    old, new = os.path.join(project, sl.OLD_DATA_NAME), os.path.join(project, sl.DATA_NAME)
    stray = _stray_verify(project) if os.path.lexists(new) else None
    plan = {"stray_dir": bool(stray is not None and os.path.isdir(new)), "moved": [], "dups": [], "dirs": {}}
    if stray is not None:
        for rel in stray:
            theirs = os.path.join(old, "verify", rel)
            plan["dups" if os.path.exists(theirs) else "moved"].append(rel)
        for root, dirs, _ in os.walk(new):
            plan["dirs"][os.path.relpath(root, new)] = os.stat(root).st_mode & 0o7777
    rec["u1"] = plan
    _save_record(project, "u1", rec)
    for rel in stray or []:
        src, dest = os.path.join(new, "verify", rel), os.path.join(old, "verify", rel)
        if rel in plan["moved"]:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
        os.remove(src)
    for root, dirs, files in sorted(os.walk(new), key=lambda t: -len(t[0])):
        os.rmdir(root)
    try:
        sl.migrate_dir(project, force=True, gitignore=True)           # quietness was checked above, ignoring our own lock
    except sl.StudioError:
        raise Refused("refused")                                      # the caller puts everything back from the record


def _undo_u1(project, rec):
    old, new = os.path.join(project, sl.OLD_DATA_NAME), os.path.join(project, sl.DATA_NAME)
    plan = rec.get("u1") or {}
    if os.path.isdir(new) and not os.path.lexists(old):
        os.rename(new, old)
    marker = os.path.join(old, sl.MIGRATED_MARKER)
    if os.path.isfile(marker):
        os.remove(marker)
    _restore(project, rec["snapshot"])
    _drop_lock(project, rec)
    if plan.get("stray_dir"):
        for rel in plan.get("moved", []) + plan.get("dups", []):
            src, dest = os.path.join(old, "verify", rel), os.path.join(new, "verify", rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if rel in plan["moved"]:
                shutil.move(src, dest)
            else:
                shutil.copy2(src, dest)
        os.makedirs(new, exist_ok=True)
        for rel, mode in sorted(plan.get("dirs", {}).items(), key=lambda kv: -len(kv[0])):
            try:
                os.chmod(os.path.join(new, rel), mode)
            except OSError:
                pass
        for rel in plan.get("moved", []):                       # folders the merge made under the old verify/ go again
            parent = os.path.dirname(os.path.join(old, "verify", rel))
            while parent.startswith(os.path.join(old, "verify")) and parent != os.path.join(old, "verify"):
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = os.path.dirname(parent)


def _apply_u2(project, rec):
    path, text = _claude_md(project)
    if text is None:
        raise Refused("unreadable")
    _write(path, rewrite_names(text)[0])


def _apply_u3(project, rec):
    cfg, added, _ = _merged_config(project)
    _write(_project_json(project), _dump(cfg, _read(_project_json(project))))


def _apply_u4(project, rec):
    private = os.path.join(sl.studio_dir(project), "private")
    rec["made_private"] = not os.path.isdir(private)
    _save_record(project, "u4", rec)
    if not card.stamp(project, budgit_version=_running(), rules_version=RULES_VERSION):
        raise Refused("unreadable")


def _undo_generic(project, rec):
    _restore(project, rec["snapshot"])
    _drop_lock(project, rec)
    if rec.get("made_private"):
        try:
            os.rmdir(os.path.join(sl.studio_dir(project), "private"))
        except OSError:
            pass


APPLY = {"u1": _apply_u1, "u2": _apply_u2, "u3": _apply_u3, "u4": _apply_u4}
UNDO = {"u1": _undo_u1, "u2": _undo_generic, "u3": _undo_generic, "u4": _undo_generic}


# ---------------------------------------------------------------- safety gates and the commit

def _chat_inside(project):
    real = livework.canon(project)
    for s in livework.live_sessions()["sessions"]:
        cwd = s.get("cwd")
        if isinstance(cwd, str) and (livework.canon(cwd) == real or os.path.realpath(cwd) == real):
            return True
    return False


def _recent_pointer(project, now):
    folder = os.path.join(sl.home_state(), "sessions")
    real = livework.canon(project)
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        if not name.endswith(".json"):
            continue
        rec = sl.read_pointer(name[:-5]) if sl.valid_session(name[:-5]) else None
        if not rec or rec.get("ended") or not isinstance(rec.get("project"), str):
            continue
        if livework.canon(rec["project"]) != real:
            continue
        seen = sl._epoch(rec.get("last_seen") or rec.get("ts"))
        if seen is not None and now - seen < QUIET_S:
            return True
    return False


def _busy(project, now):
    """True when a file of the old data folder (two levels, content folders skipped, the lock file ignored: taking
    the lock creates it) changed in the last QUIET_S seconds: a session is probably running."""
    old = os.path.join(project, sl.OLD_DATA_NAME)
    for root, dirs, files in os.walk(old):
        depth = root[len(old):].count(os.sep)
        dirs[:] = [] if depth >= 1 else [d for d in dirs if d not in sl._MIGRATE_SKIP]
        for name in files:
            if name == ".lock":
                continue
            try:
                if now - os.lstat(os.path.join(root, name)).st_mtime < QUIET_S:
                    return True
            except OSError:
                continue
    return False


def _status_lines(project, specs):
    out = _git(project, "status", "--porcelain", "-uall", "--", *specs)
    return None if out is None or out[0] != 0 else frozenset(l for l in out[1].splitlines() if l.strip())


def _gate(project, sid, now, baseline=frozenset()):
    """None when the step may run, else a reason code. `baseline`: status lines an earlier step of this run left behind
    because git had no identity to commit them; they are ours, not the user's work."""
    if _chat_inside(project):
        return "chat"
    if _recent_pointer(project, now) or (sid == "u1" and _busy(project, now)):
        return "recent"
    if _is_git(project):
        specs = _pathspecs(project, sid)
        lines = _status_lines(project, specs)
        if lines is None:
            return "git"
        if lines - baseline:
            return "dirty"
        staged = _git(project, "diff", "--cached", "--quiet", "--", *specs)
        if staged is None or staged[0] not in (0,):
            return "staged"
    return None


def _commit(project, sid, title):
    """One commit with explicit paths when git tracks something that changed; 'none' when nothing to commit,
    'noident' when git has no identity, 'failed' when the commit itself failed."""
    if not _is_git(project):
        return "none"
    specs = _pathspecs(project, sid)
    status = _git(project, "status", "--porcelain", "-uall", "--", *specs)
    if status is None or status[0] != 0:
        return "failed"
    paths = []
    for line in status[1].splitlines():
        p = line[3:].strip().strip('"')
        paths.extend(x.strip().strip('"') for x in p.split(" -> "))
    paths = sorted({p for p in paths if p})
    if not paths:
        return "none"
    who = _git(project, "-c", "user.useConfigOnly=true", "var", "GIT_COMMITTER_IDENT")
    if not (who and who[0] == 0 and who[1].strip()):
        return "noident"
    if _git(project, "add", "-A", "--", *paths) is None:
        return "failed"
    done = _git(project, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit", "--no-verify", "-q",
                "-m", "budgit upgrade %s: %s" % (sid, title), "--", *paths)
    return "committed" if done and done[0] == 0 else "failed"


TITLES = {"u1": "data folder .budgit", "u2": "CLAUDE.md names", "u3": "project.json defaults", "u4": "card stamp"}


# ---------------------------------------------------------------- a whole project

def apply_project(project, now=None):
    """Run the pending steps of one project, in order. Returns {'steps': [...], 'skipped': code or None}; `steps` is the
    state after the run (a step that ran says `applied`, `failed` one that was undone)."""
    now = now if now is not None else time.time()
    ran, skipped = {}, None
    for _ in IDS:
        steps = check(project)
        step = next((s for s in steps if s["state"] != "current"), None)
        if step is None:
            break
        sid = step["id"]
        if step["state"] != "pending":
            skipped = step.get("reason") or step["state"]
            break
        skipped = _run_step(project, sid, now, ran)
        if skipped:
            break
    final = []
    for s in check(project):
        final.append(dict(s, **ran[s["id"]]) if s["id"] in ran and (ran[s["id"]]["state"] == "failed" or s["state"] == "current") else s)
    return {"steps": final, "skipped": skipped}


def _run_step(project, sid, now, ran):
    """One step under the gate, the lock and the snapshot. Returns None when it is done, else the reason code."""
    code = _gate(project, sid, now, ran.get("_baseline", frozenset()))
    if code:
        return code
    started = False
    lock_new = not os.path.lexists(os.path.join(sl.studio_dir(project), ".lock"))    # taking the lock creates the file
    try:
        with sl.locked(project):
            if CHECKS[sid](project)["state"] != "pending":            # another run did it while we waited for the lock
                return None
            snap = _snapshot(project, sid)
            rec = {"step": sid, "project_key": key(project), "at": sl.now(), "state": "started", "snapshot": snap,
                   "lock_new": lock_new}
            _save_record(project, sid, rec)
            if _chat_inside(project) or _recent_pointer(project, now) or (sid == "u1" and _busy(project, now)):
                raise Refused("chat")                              # a chat opened while we were saving
            started = True
            APPLY[sid](project, rec)
    except Refused as exc:
        _rollback(project, sid) if started else _drop_record(project, sid)
        return exc.code
    except sl.StudioError:
        if not started:                                               # the lock could not be taken: the folder moved under us
            _drop_record(project, sid)
            return "busy"
        _rollback(project, sid)
        ran[sid] = {"state": "failed"}
        return "failed"
    except Exception:                                                # an OS error half way through: put the step back
        sl.log_error("upgrade step")
        _rollback(project, sid) if started else _drop_record(project, sid)
        ran[sid] = {"state": "failed"}
        return "failed"
    if CHECKS[sid](project)["state"] != "current":
        _rollback(project, sid)
        ran[sid] = {"state": "failed"}
        return "failed"
    committed = _commit(project, sid, TITLES[sid])
    if committed == "failed":
        _rollback(project, sid)
        ran[sid] = {"state": "failed"}
        return "commit"
    if committed == "noident":
        every = _status_lines(project, sorted({p for i in IDS for p in _pathspecs(project, i)}))
        ran["_baseline"] = every if every is not None else frozenset()
    rec = _load_record(project, sid) or {}
    rec.update(state="done", after=_hashes(project, sid), commit=committed)
    _save_record(project, sid, rec)
    ran[sid] = {"state": "applied", "commit": committed}
    return None


def _drop_record(project, sid):
    try:
        os.remove(_record_path(project, sid))
    except OSError:
        pass


def _rollback(project, sid):
    rec = _load_record(project, sid)
    if rec:
        try:
            UNDO[sid](project, rec)
        except Exception:
            sl.log_error("upgrade rollback")
        _drop_record(project, sid)


class _nolock:
    """u1 renames the folder that holds the lock file: its undo runs without the lock (the chat check above guards it)."""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def undo(project, sid):
    """Put a step back. Raises StudioError (code in the text) when there is nothing to undo or the files changed since."""
    if sid not in IDS:
        raise sl.StudioError("unknown step %r" % (sid,))
    rec = _load_record(project, sid)
    if rec is None or rec.get("state") == "undone":
        raise sl.StudioError("nothing-to-undo")
    if rec.get("state") == "done" and rec.get("after") != _hashes(project, sid):
        raise sl.StudioError("changed-since")
    for later in IDS[IDS.index(sid) + 1:]:
        other = _load_record(project, later)
        if other and other.get("state") == "done":
            raise sl.StudioError("undo-%s-first" % later)
    if _chat_inside(project):
        raise sl.StudioError("chat")
    with sl.locked(project) if sid != "u1" else _nolock():
        UNDO[sid](project, rec)
    if rec.get("commit") == "committed":
        _commit(project, sid, "undo " + TITLES[sid])
    rec["state"] = "undone"
    _save_record(project, sid, rec)
    return rec


# ---------------------------------------------------------------- many projects, and the output

def targets(only=None):
    """[(entry, id, name, protected)] for the registry (read only: a cold home is seeded in memory). `only` is one
    canonical project path; a folder the list does not hold yet is looked at as a current project."""
    data = portfolio.load() or portfolio.seed()
    entries = list(data["projects"])
    if only is not None and only not in {e["path"] for e in entries}:
        entries.append({"path": only, "channel": "current"})
    private = {e["path"] for e in entries if livework.is_private(e["path"])}
    ids = portfolio._ids(entries, private)
    rows = []
    for e in entries:
        path = e["path"]
        if only is not None and livework.canon(path) != only:
            continue
        rows.append((e, ids[path], portfolio.display_name(path, e, path in private), path in private))
    return rows


def run(mode, only=None, now=None):
    """check | apply over the registry (`only` = one canonical project path). Returns the data the CLI prints."""
    global _deadline
    stop = time.monotonic() + BUDGET_S
    _deadline = stop if mode == "check" else None     # --apply never cuts a git call short: that could undo a finished step
    try:
        rows = []
        for e, ident, name, prot in targets(only):
            path = e["path"]
            row = {"id": ident, "protected": prot, "channel": e.get("channel") or "current"}
            if not prot:
                row["name"] = name.encode("utf-8", "replace").decode("utf-8")      # a lone surrogate must not break the print
            if not os.path.isdir(path):
                row["status"] = "missing"
            elif row["channel"] == "off":
                row["status"] = "off"
            elif not livework.has_ledger(path):
                row["status"] = "unmanaged"
            elif mode == "check":
                steps = check(path)
                row["steps"] = steps
                row["status"] = "behind" if behind(steps) else "current"
            else:
                try:
                    res = apply_project(path, now) if time.monotonic() < stop else {"steps": check(path), "skipped": "time"}
                except Exception:                      # one project's surprise must not stop the others
                    sl.log_error("upgrade project")
                    res = {"steps": [], "skipped": "failed", "error": True}
                row["error"] = bool(res.get("error"))
                row["steps"] = res["steps"]
                row["skipped"] = res["skipped"]
                row["status"] = ("skipped" if res["skipped"] else "updated" if any(s["state"] == "applied" for s in res["steps"])
                                 else "current")
            if prot:                                   # step ids, states and reason codes only
                row["steps"] = [{k: v for k, v in s.items() if k in ("id", "state", "reason", "commit")}
                                for s in row.get("steps", [])]
            rows.append(row)
        code = 0
        if mode == "check":
            code = 2 if any(r["status"] == "behind" for r in rows) else 0
        else:
            code = 1 if any(s["state"] == "failed" for r in rows for s in r.get("steps", [])) or any(
                r.get("error") for r in rows) else 0   # a skip is not an error
        return {"version": VERSION, "mode": mode, "rows": rows, "exit": code}
    finally:
        _deadline = None


def diff(project):
    """The CLAUDE.md change u2 would make, as a unified diff (never for a protected project: the caller refuses)."""
    import difflib
    path, text = _claude_md(project)
    if text is None:
        return ""
    new = rewrite_names(text)[0]
    return "".join(difflib.unified_diff(text.splitlines(True), new.splitlines(True), "CLAUDE.md", "CLAUDE.md (after)"))


def render(out, lang=None):
    import i18n
    t = lambda k, **kw: i18n.t(k, lang, **kw)
    lines = [t("upgrade.title." + out["mode"])]
    for r in out["rows"]:
        name = r.get("name") or (t("portfolio.protected") + (" " + r["id"].split("~")[-1] if r["id"].startswith("private~") else ""))
        status = r["status"]
        if status in ("missing", "off", "unmanaged", "current"):
            lines.append("%s: %s" % (name, t("upgrade.status." + status)))
            continue
        parts = []
        for s in r.get("steps", []):
            if s["state"] == "current":
                continue
            what = s["id"]
            if not r["protected"] and s.get("changes"):
                what += " (%s)" % t("upgrade.changes", n=s["changes"])
            if s.get("reason"):                          # a protected project shows the code, never a sentence
                why = s["reason"] if r["protected"] else t("upgrade.reason." + s["reason"])
                parts.append("%s %s [%s]" % (what, t("upgrade.state." + s["state"]), why))
            else:
                parts.append("%s %s" % (what, t("upgrade.state." + s["state"])))
        reason = r.get("skipped")
        lines.append("%s: %s%s%s" % (name, t("upgrade.status." + status), ": " + ", ".join(parts) if parts else "",
                                      " [%s]" % (reason if r["protected"] else t("upgrade.reason." + reason)) if reason else ""))
        if not r["protected"]:
            for s in r.get("steps", []):
                for a in s.get("attention") or []:
                    lines.append("  %s: %s" % (s["id"], t("upgrade.attention." + a)))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- release-check's compat guard

def compat_dropped(root=None):
    path = os.path.join(root or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), "compat.json")
    raw = _read(path)
    if raw is None:
        return []
    try:
        dropped = json.loads(raw.decode("utf-8")).get("dropped") or []
    except (ValueError, AttributeError):
        raise sl.StudioError("compat.json is not readable: the release cannot say what it drops")
    if not isinstance(dropped, list) or not all(isinstance(d, str) for d in dropped):
        raise sl.StudioError("compat.json: `dropped` must be a list of names")
    return dropped


def compat_problems(dropped):
    """[{kind, name, step, projects, private}]: `unguarded` for a dropped name no step can guard (fails closed), `behind` for
    one a managed project that is not `off` (a skipped one too) still needs. A protected project is only counted."""
    problems, rows = [], None
    for name in dropped:
        step = COMPAT.get(name)
        if step is None:
            problems.append({"kind": "unguarded", "name": name, "step": None, "projects": [], "private": 0})
            continue
        if rows is None:
            rows = [(label, prot, check(e["path"])) for e, ident, label, prot in targets()
                    if e.get("channel") != "off" and os.path.isdir(e["path"]) and livework.has_ledger(e["path"])]
        behind_now = [(label, prot) for label, prot, steps in rows
                      if next(s for s in steps if s["id"] == step)["state"] != "current"]
        if behind_now:
            problems.append({"kind": "behind", "name": name, "step": step,
                             "projects": [label for label, prot in behind_now if not prot],
                             "private": sum(1 for label, prot in behind_now if prot)})
    return problems
