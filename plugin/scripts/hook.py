#!/usr/bin/env python3
"""Budgit hook dispatcher: hook.py <user-prompt-submit|post-tool-use|stop|session-start|session-end|post-compact>.
Acts only in sessions bound with `budgit activate`. Fails open: errors go to ~/.budgit/hooks.log."""
import contextlib
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import studiolib as sl  # noqa: E402


def _nullcontext():
    return contextlib.nullcontext()


_OUT = {}


def emit(obj):
    """Collect output; main() prints ONE JSON object (several handlers may speak in one event)."""
    for key, value in obj.items():
        if key == "systemMessage" and key in _OUT:
            _OUT[key] += " | " + value
        elif key == "hookSpecificOutput" and key in _OUT:
            old = _OUT[key].get("additionalContext")
            _OUT[key].update(value)
            if old and value.get("additionalContext"):
                _OUT[key]["additionalContext"] = old + "\n" + value["additionalContext"]
        else:
            _OUT[key] = value


_TEXT_KEYS = ("systemMessage", "permissionDecisionReason", "additionalContext")


def flush():
    if _OUT:
        for d in (_OUT, _OUT.get("hookSpecificOutput") or {}):   # never updatedInput: tool input stays as written
            for k in _TEXT_KEYS:
                if isinstance(d.get(k), str):
                    d[k] = sl.cloud_names(d[k])
        sys.stdout.write(json.dumps(_OUT, ensure_ascii=True))


def _context(event, text):
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def on_prompt(inp, project):
    session = inp.get("session_id")
    sl.append_inbox(project, inp.get("prompt") or "", session, "prompt")
    pending = sl.untracked(project, sl.load_ledger(project), session=session)
    if pending:
        _context("UserPromptSubmit", sl.untracked_notice(pending))
    ckpt = sl.checkpoint_due(session, inp.get("transcript_path"))
    if ckpt:
        _context("UserPromptSubmit", ckpt)


def _decision(event, decision, reason):
    emit({"hookSpecificOutput": {"hookEventName": event, "permissionDecision": decision,
                                 "permissionDecisionReason": reason}})


def _combine(decisions):
    """deny beats ask; reasons are joined so the user sees every guard that fired."""
    decisions = [d for d in decisions if d]
    if not decisions:
        return None
    kind = "deny" if any(d[0] == "deny" for d in decisions) else "ask"
    reason = " | ".join(d[1] for d in decisions)
    if kind == "ask" and "stop and report" not in reason:      # critic 9: an unanswered ask never means "retry"
        reason += sl._ASK_TAIL
    return kind, reason


def fill_agent_model(inp):
    """An Agent call without model= gets its role's model at the current budget level.
    "allow" is what the docs pair with updatedInput; it only skips the default prompt (Agent has none) — the user's deny/ask
    rules, e.g. Agent(model:opus), are still evaluated against the returned input (code.claude.com/docs/en/hooks,
    PreToolUse decision control, read 2026-10-19). "ask" would prompt the user on every call, against the point of the fill."""
    import budget
    tool_input = inp.get("tool_input") or {}
    if tool_input.get("model"):
        return
    level = budget.current()["level"]
    kind = tool_input.get("subagent_type")
    model = budget.model_for(kind, level)
    budget.record_fill(inp.get("session_id"), inp.get("tool_use_id"), model)
    emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "budgit: model filled from role", "updatedInput": dict(tool_input, model=model),
        "additionalContext": "budgit: no model= on this Agent call; filled model=%s (%s role, budget %s). "
                             "Pass it yourself next time." % (model, budget.role_for(kind), level)}})


from rawguard import RAW_REASON, SCOUT_READER, TREE_REASON, _fold, raw_glob_rewrite, raw_reason  # noqa: E402,F401
import rawguard  # noqa: E402


def __getattr__(name):            # tests and tools still reach the guard's helpers as hook.<name>
    return getattr(rawguard, name)


ASK_TAG = "guard-ask"


def summary_events(lines):
    """Stop summary: guard asks were already shown to the user as a prompt — do not report them twice. Denies and their
    false-positive marks are counted by `budgit denies`, not repeated at every Stop."""
    import denylog
    tags = [" %s " % t for t in (ASK_TAG, denylog.DENY_TAG, denylog.FP_TAG)]
    return [l for l in lines if not any(t in l for t in tags)]


def on_pre_tool_use(inp, project):
    tool = inp.get("tool_name")
    if tool in ("Agent", "Task"):
        fill_agent_model(inp)
        import codexlane
        note = codexlane.review_first_note(project, (inp.get("tool_input") or {}).get("subagent_type"))
        if note:
            _context("PreToolUse", note)
        return
    tool_input = inp.get("tool_input") or {}
    cwd = inp.get("cwd") or project or os.getcwd()
    found = [("verify-forgery", sl.verify_forgery_decision(tool, tool_input, inp.get("agent_type"), cwd))]
    if tool == "Bash":
        raw = raw_reason(inp)
        found.append(("raw-store", ("deny", raw) if raw else None))
        command = tool_input.get("command") or ""
        found.append(("commit-identity", sl.commit_identity_decision(command, cwd)))
        found.append(("approval", sl.approval_decision(command)))
        found.append(("hook-bypass", sl.hook_bypass_decision(command)))
    if project:
        found.append(("real-data", sl.real_data_decision(tool, tool_input, project, cwd=cwd)))
        found.append(("contract-lock", sl.contract_lock_decision(tool, tool_input, project, cwd=cwd)))
    result = _combine([d for _, d in found])
    if result:
        _decision("PreToolUse", result[0], result[1])
        if result[0] == "ask" and project:     # the user's answer lives in the prompt; the log keeps the trail
            sl.audit(project, "%s %s %s" % (ASK_TAG, tool, " ".join(result[1].split())[:200]))
        elif project:                          # R-55: denies are measured too (hash + masked head, never the command)
            import denylog
            denylog.log_denies(project, tool, tool_input, [(n, d) for n, d in found if d and d[0] == "deny"])


def on_post_tool_use_failure(inp, project):
    import introspect
    note = introspect.on_failure(inp.get("session_id"), inp.get("tool_name"), inp.get("tool_input"), inp.get("error"))
    if note:
        _context("PostToolUseFailure", note)


def on_subagent_stop(inp, project):
    sl.save_report(project, inp.get("agent_type"), inp.get("agent_id"), inp.get("last_assistant_message"))


def seal_verify_report(inp):
    """Round 2 ruling A: budgit:verifier's Write/Edit/MultiEdit of a .budgit/verify/*.md report is sealed (path +
    sha256 of the bytes on disk) in <BUDGIT_HOME>/verify-seals.jsonl; `ledger verify` accepts only sealed bytes.
    Machine-wide: runs for bound and unbound sessions alike."""
    if inp.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
        return
    path = (inp.get("tool_input") or {}).get("file_path") or ""
    if not path:
        return
    real = os.path.realpath(os.path.join(inp.get("cwd") or os.getcwd(), os.path.expanduser(path)))
    if not any(os.sep + n + os.sep + "verify" + os.sep in _fold(real) for n in sl.DATA_NAMES) or not _fold(real).endswith(".md"):
        return
    if sl.canon_agent(inp.get("agent_type")) != sl.VERIFIER:
        if not inp.get("agent_type"):     # visible, not silent: a PostToolUse payload without agent_type breaks sealing
            os.makedirs(sl.home_state(), exist_ok=True)
            sl.append_text(os.path.join(sl.home_state(), "hooks.log"),
                           "%s post-tool-use verify-seal: write to %s had no agent_type — not sealed\n" % (sl.now(), real))
        return
    if os.path.isfile(real):
        sl.write_seal(real)


def on_post_tool_use(inp, project):
    if inp.get("tool_name") == "SubagentHandback":   # the delivered report is the call's message
        sl.save_report(project, inp.get("agent_type"), inp.get("agent_id"),
                       (inp.get("tool_input") or {}).get("message"))
        return
    if inp.get("tool_name") == "Bash":
        response = inp.get("tool_response")
        output = response.get("stdout", "") if isinstance(response, dict) else str(response or "")
        note = sl.push_reminder((inp.get("tool_input") or {}).get("command") or "",
                                inp.get("cwd") or project, output)
        if note:
            _context("PostToolUse", note)
        return
    if inp.get("tool_name") in ("Edit", "Write", "MultiEdit"):
        seal_verify_report(inp)
        path = (inp.get("tool_input") or {}).get("file_path") or ""
        if path.endswith(".md"):            # brain.py is imported only for notes
            import brain
            note = brain.hook_errors(project, path)
            if note:
                _context("PostToolUse", note)
        return
    if inp.get("tool_name") != "AskUserQuestion":
        return
    response = inp.get("tool_response")
    text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
    new_id = sl.append_inbox(project, "AskUserQuestion answer: " + text, inp.get("session_id"),
                             "answer", ref=inp.get("tool_use_id"))
    if new_id:
        _context("PostToolUse", "budgit: the user's answer was captured as %s — track it "
                 "(`budgit ledger add --src %s`, `link` or `ack`)." % (new_id, new_id))


def on_stop(inp, project):
    session = inp.get("session_id")
    sl.reconcile_transcript(project, inp.get("transcript_path"), session)
    data = sl.load_ledger(project)
    state = sl.load_state(project)
    problems = sl.stop_problems(project, data, session)
    events = summary_events(sl.new_audit_lines(project, state))
    drop_note = ("budgit audit: " + " | ".join(l.split(" ", 1)[-1] for l in events)) if events else ""
    blocks = state["last_block"] if isinstance(state.get("last_block"), dict) else {}
    state["last_block"] = blocks
    if not problems:
        blocks.pop(session, None)
        sl.save_state(project, state)
        if drop_note:
            emit({"systemMessage": drop_note})
        return
    fingerprint = hashlib.sha1("\n".join(problems).encode("utf-8")).hexdigest()
    if inp.get("stop_hook_active") and blocks.get(session) == fingerprint:
        sl.save_state(project, state)
        emit({"systemMessage": ("budgit: ledger stalled — %d problem(s) left: %s %s"
                                % (len(problems), problems[0], drop_note)).strip()})
        return
    blocks[session] = fingerprint
    sl.save_state(project, state)
    out = {"decision": "block",
           "reason": "budgit ledger check failed:\n- " + "\n- ".join(problems)
           + "\nFix with the `budgit ledger` CLI, then finish your reply."}
    if drop_note:
        out["systemMessage"] = drop_note
    emit(out)


def _progress_line(project, text):
    try:
        sl.append_text(os.path.join(sl.studio_dir(project), "progress.md"), "\n- %s %s\n" % (sl.now()[:16], text))
    except sl.StudioError:
        sl.log_error("progress line")


def on_session_start(inp, project):
    import brain
    sid = inp.get("session_id")
    sl.touch_pointer(sid)
    try:                                         # R-115: claims of sessions that ended without releasing, and a resume's own
        data = sl.load_ledger(project)
        dead = sl.sweep_dead_claims(project, data)
        back = []
        if inp.get("source") == "resume":
            rec = sl.read_pointer(sid) or {}
            for item_id in rec.get("released") or []:
                try:
                    if sl.find_item(data, item_id)["status"] == "open":
                        sl.start_item(project, data, item_id, sid)
                        back.append(item_id)
                except sl.StudioError:
                    pass
        if dead or back:
            sl.save_ledger(project, data)
            if dead:
                _progress_line(project, "back to open (their session ended without releasing): %s" % ", ".join(dead))
            if back:
                _progress_line(project, "claimed again by the resumed session: %s" % ", ".join(back))
    except Exception:
        sl.log_error("session-start: claims step")
    try:
        sl.ensure_gitignore(project)          # projects activated before a line existed get it on their next session
    except Exception:                         # a bad .gitignore (Latin-1, a directory) must not cost the user the summary
        sl.log_error("session-start: gitignore step")
    text = sl.session_summary(project, inp.get("session_id"))
    pointer = brain.session_pointer(project)
    conflict = sl.data_dir_conflict(project)      # R-120: a stale session recreated .studio next to .budgit
    _context("SessionStart", text + ("\n" + pointer if pointer else "") + ("\n" + conflict if conflict else ""))


def on_session_end(inp, project):
    """R-115: the items this session was working on leave `doing` when it closes. clear/resume keep the claims (the
    resumed session re-claims what it released; after /clear the new session starts or takes what it needs)."""
    if inp.get("reason") in ("clear", "resume"):
        return
    sid = inp.get("session_id")
    data = sl.load_ledger(project)
    mine = sl.claimed_by(data, sid)
    sl.release_items(project, data, mine)
    if mine:
        sl.save_ledger(project, data)
    sl.end_pointer(sid, mine)


def on_post_compact(inp, project):
    sl.append_text(os.path.join(sl.studio_dir(project), "compactions.log"),
                   "%s trigger=%s\n%s\n\n" % (sl.now(), inp.get("trigger", ""),
                                             inp.get("compact_summary", "")))


def path_export_line(bindir):
    """The line appended to CLAUDE_ENV_FILE: nothing in the folder name may quote, expand or escape."""
    return 'export PATH="%s:$PATH"\n' % re.sub(r'["$`\\\r\n]', "", bindir)


def cloud_note():
    """Claude Code cloud sessions load no plugins: the vendored copy (`budgit vendor`, .claude/budgit/ with a VERSION
    file) is all there is. Put its CLI on PATH for later Bash calls when CLAUDE_ENV_FILE allows it, and tell the model
    where the CLI is and what the agents are called. "" outside a cloud session or an unvendored checkout."""
    root = sl.studio_root()
    if os.environ.get("CLAUDE_CODE_REMOTE") != "true" or not os.path.isfile(os.path.join(root, "VERSION")):
        return ""
    bindir = os.path.join(root, "bin")
    cli = os.path.join(bindir, "budgit")
    envfile = os.environ.get("CLAUDE_ENV_FILE")
    on_path = False
    if envfile:
        line = path_export_line(bindir)
        try:
            with open(envfile, "a+", encoding="utf-8") as f:
                f.seek(0)
                if line not in f.read():
                    f.write(line)
            on_path = True
        except OSError:
            sl.log_error("session-start: CLAUDE_ENV_FILE")
    where = "on PATH as `budgit`" if on_path else "not on PATH: run it as `python3 %s ...`" % cli
    return ("Budgit (cloud session, vendored runtime): the CLI is %s — %s. In this session the Budgit agents are named "
            "budgit-verifier, budgit-scout-reader, budgit-researcher, budgit-plan-critic, budgit-security-reviewer and "
            "budgit-persona-sim (not `budgit:X`). Bind this session with `budgit activate` to turn the ledger on; "
            "only pushed work survives this VM." % (cli, where))


HANDLERS = {"user-prompt-submit": on_prompt, "pre-tool-use": on_pre_tool_use,
            "post-tool-use": on_post_tool_use, "stop": on_stop, "subagent-stop": on_subagent_stop,
            "session-start": on_session_start, "post-compact": on_post_compact, "session-end": on_session_end,
            "post-tool-use-failure": on_post_tool_use_failure}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        inp = json.load(sys.stdin)
        if isinstance(inp, dict) and inp.get("agent_type"):
            inp["agent_type"] = sl.canon_agent(inp["agent_type"])   # cloud `studio-X` == plugin `studio:X` for every guard
        project = sl.project_for_session(inp.get("session_id"))
        if event in ("user-prompt-submit", "stop"):    # R-115 heartbeat: last_seen, at most once a minute
            sl.touch_pointer(inp.get("session_id"))
        if event == "user-prompt-submit":  # context nudge runs in EVERY session, zero model tokens
            nudge = sl.context_nudge(inp.get("session_id"), inp.get("transcript_path"))
            if nudge:
                emit({"systemMessage": "budgit: " + nudge})
            import budget
            note = budget.notice(inp.get("session_id"))   # once per level change, every session
            if note:
                emit({"systemMessage": note[0]})
                _context("UserPromptSubmit", note[1])
            import introspect   # self-check when a rule broke since the last prompt, every session
            check = introspect.live_check(inp.get("session_id"), inp.get("transcript_path"))
            if check:
                _context("UserPromptSubmit", check)
        if event == "session-start":
            note = cloud_note()
            if note:
                _context("SessionStart", note)
        if event == "session-start" and not project:   # model rules matter in every session, not only Budgit ones
            if not sl.vendored() and not os.path.exists(os.path.join(sl.home_state(), "config.json")):
                _context("SessionStart", sl.SETUP_NUDGE)   # R-52 E3: first run on a new machine
            import budget
            cur = budget.current()
            _context("SessionStart", budget.short_line(cur["level"], cur["reason"]))
        if event == "post-tool-use-failure":  # introspection runs in EVERY session
            on_post_tool_use_failure(inp, project)
            return 0
        if event == "subagent-stop" and inp.get("agent_id"):  # R-64: the counter goes too in unbound sessions
            import agentcap
            agentcap.reset(inp.get("agent_id"))
        if event == "pre-tool-use" and inp.get("agent_id"):   # R-63: per-subagent call ceiling, every session
            import agentcap
            import budget
            cap = agentcap.check(inp, budget.policy(budget.current()["level"])["agent_calls"])
            if cap and cap[0] == "deny":
                _decision("PreToolUse", "deny", cap[1])
                return 0
            if cap:
                _context("PreToolUse", cap[1])
        if event == "pre-tool-use" and inp.get("tool_name") in ("Read", "Grep", "Glob"):   # fires constantly: no lock
            raw = raw_reason(inp)
            if raw:
                _decision("PreToolUse", "deny", raw)
                return 0
            try:
                rewritten = raw_glob_rewrite(inp)
            except Exception:                   # fail closed, like raw_reason
                _decision("PreToolUse", "deny", RAW_REASON)
                return 0
            if rewritten:
                # "allow" is what the docs pair with updatedInput; Grep is read-only, and the user's deny/ask rules are still
                # evaluated against the returned input
                emit({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "allow",
                    "permissionDecisionReason": "budgit: raw/ exclusion appended to the Grep glob (P5 quarantine)",
                    "updatedInput": rewritten}})
            return 0
        if event == "pre-tool-use":  # guards run in EVERY session; real-data only when bound
            with (sl.locked(project) if project else _nullcontext()):
                on_pre_tool_use(inp, project)
            return 0
        if not project or event not in HANDLERS:
            if event == "post-tool-use":
                seal_verify_report(inp)         # seals are machine-wide: an unbound session's verifier still seals
            return 0
        with sl.locked(project):
            HANDLERS[event](inp, project)
    except Exception:  # fail open, but never silently: the user must know enforcement is off
        sl.log_error(event)
        if event in ("stop", "session-start", "user-prompt-submit", "pre-tool-use", "post-tool-use", "subagent-stop",
                     "post-tool-use-failure"):
            emit({"systemMessage": "budgit: hook error in %s — enforcement off this turn; "
                                   "see ~/.budgit/hooks.log" % event})
    finally:
        flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
