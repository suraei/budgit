"""Per-subagent tool-call ceiling (R-63). Briefs said "~60 calls" and agents ran 186-220; the hook
now counts every tool call a subagent makes (hook input carries agent_id only inside subagents). Zero model tokens.
cap-5 -> a note to wrap up; past cap+20 -> every tool but Write is denied so the agent saves its report and ends."""
import json
import os
import re

import studiolib as sl

SOFT_MARGIN, HARD_MARGIN = 5, 20
MAX_STATED = 500


def _path(agent_id):
    return os.path.join(sl.home_state(), "agent-calls", re.sub(r"[^A-Za-z0-9_-]", "_", str(agent_id))[:64])


# ONE accepted shape per wait (an allow list): `sleep N`, or cat/tail/head with at most `-n N` or `-N` and a single
# plain path ending /tasks/<name>.output. Any other character anywhere makes the call work.
_WAIT_CMD = re.compile(r"sleep [0-9]+|(cat|tail|head)( -n [0-9]+| -[0-9]+)? [A-Za-z0-9_./-]*/tasks/[A-Za-z0-9_.-]+\.output")


def is_wait(name, inp):
    """True for a call that only waits: a background-output read, a `sleep`, TaskOutput or BashOutput.
    The budget measures work, so these are counted apart (shared by the live cap and the transcript audit)."""
    if name in ("TaskOutput", "BashOutput"):
        return True
    cmd = (inp or {}).get("command") if isinstance(inp, dict) else None
    return name == "Bash" and isinstance(cmd, str) and _WAIT_CMD.fullmatch(cmd) is not None


def waits(agent_id):
    try:
        with open(_path(agent_id) + ".waits") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _bump(agent_id, suffix=""):
    path = _path(agent_id) + suffix
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            n = int(f.read().strip())        # an empty counter is corrupt too
    except FileNotFoundError:
        n = 0
    except (OSError, ValueError):
        n = 10 ** 4                          # unreadable counter: fail closed, never back to zero
    n += 1
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w") as f:
        f.write(str(n))
    os.replace(tmp, path)
    return n


def reset(agent_id):
    for suffix in ("", ".waits", ".budget", ".round"):
        try:
            os.remove(_path(agent_id) + suffix)
        except OSError:
            pass


_BRIEF_BUDGET = re.compile(r"(?im)^\W*call budget\W{0,3}\s*(\d{1,4})\b")


def _transcript(inp):
    """The subagent's own transcript: the hook's agent path when given, else next to the session transcript."""
    p = inp.get("agent_transcript_path")
    if isinstance(p, str) and p.endswith(".jsonl"):
        return p
    t = inp.get("transcript_path")
    if isinstance(t, str) and t.endswith(".jsonl") and inp.get("agent_id"):
        return "%s/subagents/agent-%s.jsonl" % (t[:-len(".jsonl")], inp["agent_id"])
    return None


def brief_budget(inp):
    """R-849: N from the `Call budget: N` line of the brief (the subagent's first user message), read once
    per agent and cached beside its counter. None when the brief states none."""
    agent_id = inp.get("agent_id")
    cache = _path(agent_id) + ".budget"
    try:
        with open(cache) as f:
            v = int(f.read().strip() or 0)
        return min(v, MAX_STATED) or None
    except (OSError, ValueError):
        pass
    path, found = _transcript(inp), 0
    try:
        with open(path, encoding="utf-8") as f:
            for _, line in zip(range(40), f):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                if rec.get("type") != "user" or not isinstance(msg, dict):
                    continue
                c = msg.get("content")
                if isinstance(c, list):
                    c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
                m = _BRIEF_BUDGET.search(c) if isinstance(c, str) else None
                found = int(m.group(1)) if m else 0
                break
    except (OSError, TypeError):
        return None                          # transcript not readable yet: ask again next call, do not cache
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w") as f:
            f.write(str(found))
    except OSError:
        pass
    return min(found, MAX_STATED) or None      # a stated number is capped: "Call budget: 99999" is no budget


def _user_text(rec):
    """The text of a real user turn (the brief, or a SendMessage resume); None for tool results and meta records."""
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return None
    origin = rec.get("origin")
    resume = isinstance(origin, dict) and origin.get("kind") == "coordinator"   # a SendMessage resume is recorded isMeta
    if any(rec.get(k) for k in ("isCompactSummary", "isVisibleInTranscriptOnly")):
        return None
    if rec.get("isMeta") and not resume:
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    c = msg.get("content")
    if isinstance(c, list):
        texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        c = "\n".join(texts) if texts else None
    if not isinstance(c, str) or c.startswith("[Request interrupted by user"):
        return None                          # tool results, interrupt markers: not a round
    return c


def _read_round(inp):
    """R-868: a round starts at each real user message in the subagent transcript (the brief, then every resume).
    Read incrementally (byte offset kept beside the counter). Returns the round budget stated by the LATEST
    message, or None. A new round zeroes the work and wait counters."""
    agent_id = inp.get("agent_id")
    path, state_path = _transcript(inp), _path(agent_id) + ".round"
    if not path:
        return None
    try:
        with open(state_path) as f:
            st = json.load(f)
        off, k, b = int(st["off"]), int(st["k"]), int(st["b"])
        if min(off, k, b) < 0:
            raise ValueError("negative")
    except FileNotFoundError:
        off, k, b = 0, 0, 0
    except (OSError, ValueError, KeyError, TypeError):
        return None                           # corrupt or unreadable state: fail closed, never a reset to zero
    try:
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read()
    except OSError:
        return min(b, MAX_STATED) if b else None
    end = data.rfind(b"\n") + 1               # a half-written last line waits for the next call
    new_k = k
    for line in data[:end].splitlines():
        try:
            text = _user_text(json.loads(line))
        except ValueError:
            continue
        if text is None:
            continue
        new_k += 1
        m = _BRIEF_BUDGET.search(text)
        b = int(m.group(1)) if m else 0       # the resume's own line applies; none means the original one
    if new_k > k and k > 0:                   # k == 0 is the first sight of the brief: nothing to zero
        for suffix in ("", ".waits"):
            try:
                os.remove(_path(agent_id) + suffix)
            except OSError:
                pass
    if new_k != k or end:
        try:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            tmp = state_path + ".tmp%d" % os.getpid()
            with open(tmp, "w") as f:
                json.dump({"off": off + end, "k": new_k, "b": b}, f)
            os.replace(tmp, state_path)
        except OSError:
            pass
    return min(b, MAX_STATED) if b else None


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _branch(wt):
    """The checked-out branch of a worktree (plain or linked), or None when detached or unreadable."""
    try:
        g = os.path.join(wt, ".git")
        if os.path.isfile(g):
            with open(g) as f:
                line = f.read().strip()
            gitdir = line[len("gitdir:"):].strip() if line.startswith("gitdir:") else ""
            gitdir = gitdir if os.path.isabs(gitdir) else os.path.join(wt, gitdir)
        else:
            gitdir = g
        with open(os.path.join(gitdir, "HEAD")) as f:
            head = f.read().strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


_FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _target(inp):
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    p = ti.get("file_path") or ti.get("notebook_path")
    return os.path.realpath(p) if isinstance(p, str) and p else None


def protected_write(inp):
    """R-868: a file tool may never touch the agent's own transcript, any subagents/ dir or the counter state.
    The transcript is thereby outside every path an agent may write, so a forged user line cannot start a round."""
    if inp.get("tool_name") not in _FILE_TOOLS:
        return False
    t = _target(inp)
    if t is None:
        return False
    state = os.path.realpath(os.path.join(sl.home_state(), "agent-calls"))
    mine = _transcript(inp)
    return (_inside(t, state) or (os.sep + "subagents" + os.sep) in t
            or (mine is not None and t == os.path.realpath(mine)))


def _allowed_past_limit(inp):
    """Past the limit only the final message and Write/Edit of <cwd>/progress*.md pass, and only when the cwd is a
    linked worktree root on a branch other than main or master. No git call: the caller commits."""
    if inp.get("tool_name") == "SubagentHandback":
        return True
    if inp.get("tool_name") in ("Write", "Edit"):
        t, cwd = _target(inp), inp.get("cwd")
        if not (t and isinstance(cwd, str) and cwd):
            return False
        cwd = os.path.realpath(cwd)
        base = os.path.basename(t)
        return (os.path.dirname(t) == cwd and base.startswith("progress") and base.endswith(".md")
                and os.path.isfile(os.path.join(cwd, ".git")) and _branch(cwd) not in (None, "main", "master")
                and not protected_write(inp))
    return False


_LIMIT_TEXT = "limit reached: save notes in progress*.md and hand back; your caller commits"


def check(inp, cap):
    """None, ("note", text) or ("deny", reason) for this tool call. An error inside the check fails closed for an
    agent already past its limit (refuse, one log line) and lets one under the limit through."""
    try:
        return _check(inp, cap)
    except Exception:
        sl.log_error("agent call cap")
        agent_id = inp.get("agent_id")
        try:
            with open(_path(agent_id)) as f:
                n = int(f.read().strip())
            stated = 0
            for suffix in (".round", ".budget"):         # the round's own budget first, then the brief's
                try:
                    with open(_path(agent_id) + suffix) as f:
                        raw = f.read()
                    stated = min(int(json.loads(raw)["b"] if suffix == ".round" else raw.strip()), MAX_STATED)
                except (OSError, ValueError, KeyError, TypeError):
                    stated = 0
                if stated:
                    break
            hard = stated + (stated + 3) // 4 if stated else cap + HARD_MARGIN
        except FileNotFoundError:
            n, hard = 0, cap + HARD_MARGIN
        except (OSError, ValueError):
            n, hard = 10 ** 4, cap + HARD_MARGIN
        if n >= hard and not _allowed_past_limit(inp):
            return ("deny", "budgit: call check failed past the limit: %s" % _LIMIT_TEXT)
        return None


def _check(inp, cap):
    agent_id = inp.get("agent_id")
    if not agent_id:
        return None
    if protected_write(inp):
        return ("deny", "budgit: the call counter and the transcripts are not yours to write; use your worktree.")
    round_b = _read_round(inp)           # R-868: a resume zeroes the counters before this call is counted
    if is_wait(inp.get("tool_name"), inp.get("tool_input")):
        _bump(agent_id, ".waits")          # a wait is free: it never moves the work count
        return None
    n = _bump(agent_id)
    stated = round_b or brief_budget(inp)
    if stated:                             # R-849: the brief's own number wins; report at N, refuse past N + 25 %
        hard = stated + (stated + 3) // 4
        if n > hard and not _allowed_past_limit(inp):
            return ("deny", "budgit: call budget %d (+25%% = %d) is used up (%d calls this round): %s; then send your "
                            "report (done, evidence, what is left) as your final message." % (stated, hard, n - 1, _LIMIT_TEXT))
        if n == stated or (stated < n <= hard and (n - stated) % 5 == 0):
            return ("note", "budgit: call budget %d reached (%d calls, work only): report now. Commit green work now as a "
                            "checkpoint (git add, then commit) and hand back. Refused from call %d."
                    % (stated, n, hard + 1))
        return None
    soft, hard = cap - SOFT_MARGIN, cap + HARD_MARGIN
    if n > hard and not _allowed_past_limit(inp):
        return ("deny", "budgit: tool-call ceiling reached (%d calls, budget ~%d): %s; then send your final report "
                        "(done, evidence, what is left) as your last message." % (n - 1, cap, _LIMIT_TEXT))
    if n == soft or (n > soft and (n - soft) % 10 == 0 and n <= hard):
        return ("note", "budgit: work %d, waits %d (budget ~%d, hard stop at %d): commit your green work now (a checkpoint) and "
                        "report what is left instead of starting new work." % (n, waits(agent_id), cap, hard))
    return None
