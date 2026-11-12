#!/usr/bin/env python3
"""Budgit hook dispatcher: hook.py <user-prompt-submit|post-tool-use|stop|session-start|session-end|post-compact>.
Acts only in sessions bound with `budgit activate`. Fails open: errors go to ~/.budgit/hooks.log."""
import contextlib
import hashlib
import json
import os
import time
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
        elif key == "reason" and key in _OUT and _OUT.get("decision") == "block":      # two Stop checks blocking in one event (R-373)
            _OUT[key] += "\n\n" + value
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
                    try:
                        d[k] = sl.cloud_names(d[k])
                    except Exception:            # a failed rename of text must never drop the decision it sits in
                        pass
        sys.stdout.write(json.dumps(_OUT, ensure_ascii=True))


def _context(event, text):
    emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


TRUST_PROMPTS = {"/budgit-trust work": "work", "/budgit-trust revoke": "revoke"}


def on_trust_prompt(inp):
    """R-335: the standing yes is set only by the user's own typed message, EXACTLY `/budgit-trust work` (or `revoke`).
    Exact equality on the trimmed prompt (the hook sees the raw typed text, before slash expansion): wrapped text, a
    longer message or anything from a subagent never matches."""
    op = TRUST_PROMPTS.get((inp.get("prompt") or "").strip()) if isinstance((inp.get("prompt") or ""), str) else None
    if not op or inp.get("agent_id") or inp.get("agent_type"):
        return
    import i18n
    project = sl.project_for_session(inp.get("session_id")) or sl.find_project(inp.get("cwd") or os.getcwd())
    if not project:
        emit({"systemMessage": i18n.t("cmd.no_project")})
        return
    if op == "work":
        # second factor: the transcript must END with this typed message (the transcript is written by Claude Code
        # itself; a payload piped into this hook has no such line). It may land a moment after the hook starts.
        granted = False
        for attempt in range(6):
            if sl.grant_trust(project, inp.get("transcript_path")):
                granted = True
                break
            time.sleep(0.2)
        if not granted:
            sl.note_pending_grant(inp.get("session_id"), project)   # the line may land later: the next tool call retries
        emit({"systemMessage": i18n.t("trust.on" if granted else "trust.unproven", project=project)})
    else:
        had = sl.revoke_trust(project)
        emit({"systemMessage": i18n.t("trust.revoked" if had else "trust.revoked_none", project=project)})


def on_prompt(inp, project):
    session = inp.get("session_id")
    sl.append_inbox(project, inp.get("prompt") or "", session, "prompt")
    data = sl.load_ledger(project)
    mine = _expire_claims(project, data, session)
    if mine:
        _context("UserPromptSubmit", "budgit: %s went back to open: nobody touched %s in the ledger for 24 hours. If this "
                 "chat still works on %s, claim %s again with `budgit ledger start R-n`." % (
                     ", ".join(mine), "it" if len(mine) == 1 else "them", "it" if len(mine) == 1 else "them",
                     "it" if len(mine) == 1 else "each"))
    pending = sl.untracked(project, data, session=session)
    if pending:
        _context("UserPromptSubmit", sl.untracked_notice(pending))
    ckpt = sl.checkpoint_due(session, inp.get("transcript_path"))
    if ckpt:
        _context("UserPromptSubmit", ckpt)


def _expire_claims(project, data, session=None):
    """R-477a: a claim nobody touched for a day goes back to open, with its note and a progress line. Checked at every
    prompt too, because a long-lived chat (a PM) never starts a session again; best effort. Returns the released ids
    that were this session's own, so the prompt can say so."""
    try:
        owners = {it["id"]: it.get("doing_by") for it in data["items"]}
        gone = sl.sweep_dead_claims(project, data)
        if gone:
            sl.save_ledger(project, data)
            _progress_line(project, "back to open (their claim went untouched or its session is gone): %s" % ", ".join(gone))
        return [i for i in gone if session and owners.get(i) == session]
    except Exception:
        sl.log_error("prompt: claims step")
        return []


RELAY_NOTE = (
    "budgit: this chat runs Budgit %(running)s; %(installed)s is installed (R-301). Answer the user's message first, "
    "here, as usual. Then, at the end of this turn and only if nothing is in flight (no background agent, session or "
    "command still running; otherwise wait for the user's next typed message), renew this chat: "
    "1) write a handoff file (what this chat was doing, open R-n and I-n ids, branch, last commit, next step) and run "
    "`budgit relay handoff --file <path> --title \"<this chat's title>\" --app-id <this chat's local_ id>`; "
    "2) start the successor with the app's session tools, with the same title and nested under the same parent; its "
    "brief says to first run `budgit relay echo %(sid)s --file <its echo>` restating every fact, then archive this "
    "chat and run `budgit relay done %(sid)s`; "
    "3) end your reply with one line in the user's language naming the new chat as a link, e.g. "
    "\"Este chat se ha renovado: sigue en [<título>](#local_…)\". "
    "If any step fails, run `budgit relay fail %(sid)s --reason \"<why>\"`, archive nothing, and end with one line, "
    "e.g. \"No he podido renovar este chat: <motivo>. Sigue igual.\" "
    "Never renew or archive any other chat. The user can also type /reload-plugins here to reload in place.")


def relay_note(inp):
    """R-301: the renewal steps, once per (chat, installed version), only for a prompt the user typed in a chat
    that runs a Budgit older than the installed one. Nothing is done to any chat here."""
    import relay
    import updatelib
    prompt = inp.get("prompt") or ""
    sid = inp.get("session_id")
    if not prompt.strip() or sl.is_harness(prompt) or not updatelib.chat_is_stale():
        return None
    installed = updatelib.installed_version()
    if relay.shown(sid, installed):
        return None
    return RELAY_NOTE % {"running": updatelib.running_version(), "installed": installed, "sid": sid}


def on_tm(event, inp):
    """True when this is the Team Manager chat; its prompt is kept as T-n in the Budgit home. Every prompt of every
    chat passes here, so only the list's `tm` key is read; the portfolio module loads in the TM chat alone."""
    try:
        with open(os.path.join(sl.home_state(), "portfolio.json"), encoding="utf-8") as f:
            tm = json.load(f).get("tm")
    except (OSError, ValueError, AttributeError):
        return False
    if not tm or tm != inp.get("session_id"):
        return False
    import portfolio
    if event == "user-prompt-submit":
        try:
            tm_id = portfolio.append_tm(inp.get("prompt") or "", inp.get("session_id"))
        except sl.StudioError:                  # the inbox lock cannot be taken: log it, skip this prompt, never crash the hook
            sl.log_error("tm inbox")
            tm_id = None
        if tm_id:
            _context("UserPromptSubmit", "budgit: kept in the TM inbox as %s. Route it to its project with "
                                         "`budgit portfolio route %s <project> [--item R-n]`." % (tm_id, tm_id))
    return True


def _away_now():
    try:
        import away
        return away.is_away()
    except Exception:
        return False


def _away_note(text, kind, session=None, project=None, cwd=None, fp=None):
    """R-641: queue a note for the user's return, under the same project key the CLI reads (`away.project_key`). A failed queue write
    (disk full, a directory planted at the queue path) is logged and swallowed: it must never stop the deny that goes with it
    from being printed (fail-open fix)."""
    try:
        import away
        away.note(text, kind, session, project=away.project_key(project, cwd), fp=fp)
    except Exception:
        try:
            sl.log_error("away note")
        except Exception:
            pass


def _decision(event, decision, reason):
    if decision == "ask" and event == "PreToolUse" and _away_now():      # R-641: nothing waits for the user while they are away
        import away
        decision, reason = "deny", away.REASON + " | " + reason[:200]
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
        return cap_agent_model(inp, tool_input)
    level = budget.current()["level"]
    kind = tool_input.get("subagent_type")
    model = budget.model_for(kind, level)
    budget.record_fill(inp.get("session_id"), inp.get("tool_use_id"), model)
    emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "budgit: model filled from role", "updatedInput": dict(tool_input, model=model),
        "additionalContext": "budgit: no model= on this Agent call; filled model=%s (%s role, budget %s). "
                             "Pass it yourself next time." % (model, budget.role_for(kind), level)}})


def cap_agent_model(inp, tool_input):
    """R-383: an explicit model= dearer than its role's policy at the current budget level is lowered to the policy, with one
    line saying so, unless the prompt carries `ESCALATE: <why>`. BUDGIT_MODEL_POLICY=advise only says it, =off does nothing.
    A model that names no known family (inherit, another vendor's id) is left alone."""
    import budget
    mode = budget.policy_mode()
    if mode == "off":
        return
    level = budget.current()["level"]
    kind = tool_input.get("subagent_type")
    given = tool_input.get("model")
    allowed = budget.above_policy(kind, level, given)
    if not allowed or budget.escalated(tool_input.get("prompt")):
        return
    role = budget.role_for(kind)
    if mode == "advise":
        _context("PreToolUse", "budgit: model=%s is above the %s policy (%s at budget %s). Put `ESCALATE: <why>` in the prompt "
                               "to keep it, or pass model=%s." % (given, role, allowed, level, allowed))
        return
    budget.record_rewrite(inp.get("session_id"), inp.get("tool_use_id"), given, allowed)
    emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "budgit: model lowered to the role policy", "updatedInput": dict(tool_input, model=allowed),
        "additionalContext": "budgit: model=%s lowered to %s (%s role, budget %s). To keep the dearer model put "
                             "`ESCALATE: <why>` in the prompt." % (given, allowed, role, level)}})


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


ARCHIVE_TOOL = "mcp__ccd_session_mgmt__archive_session"


def on_pre_tool_use(inp, project, defer=None):
    """The guards decide from the call and the project's files alone, never under the project lock. The bookkeeping
    (audit lines) runs at once, or is appended to `defer` for the caller to run under the lock."""
    tool = inp.get("tool_name")
    if tool == ARCHIVE_TOOL:            # R-301: a chat being renewed is archived only after the new chat's echo
        try:
            import relay
            reason = relay.archive_block(inp.get("session_id"), (inp.get("tool_input") or {}).get("session_id"))
        except Exception:                # fail closed: the archive is held when the check could not run
            sl.log_error("archive guard")
            reason = "budgit guard: the archive check could not run; stop and report it, do not retry."
        if reason:
            _decision("PreToolUse", "deny", reason)
        return
    if tool in ("Agent", "Task"):
        if project:                     # R-849: all-tools agents only with an ALL-TOOLS: line, in projects bound to Budgit
            try:
                import agentgate
                gate = agentgate.deny_reason(inp.get("tool_input"))
            except Exception:
                sl.log_error("agent gate")
                gate = None
                emit({"systemMessage": "budgit: the all-tools agent check could not run; enforcement did not run for this spawn "
                                       "(see ~/.budgit/hooks.log)"})
            if gate:
                _decision("PreToolUse", "deny", gate)
                return
        try:                            # bookkeeping and advice only; a failure here is logged, never a reason to skip the rest
            fill_agent_model(inp)
            import agentcap                      # R-904: brief budget above the agent type's maxTurns
            turns_note = agentcap.launch_warning(inp.get("tool_input"))
            if turns_note:
                _context("PreToolUse", turns_note)
            example_note = agentcap.example_only_warning(inp.get("tool_input"))   # R-929
            if example_note:
                _context("PreToolUse", example_note)
            import admit
            advice = admit.advice()
            if advice:
                _context("PreToolUse", advice)
            import codexlane
            note = codexlane.review_first_note(project, (inp.get("tool_input") or {}).get("subagent_type"))
            if note:
                _context("PreToolUse", note)
        except Exception:
            sl.log_error("agent start")
        return
    tool_input = inp.get("tool_input") or {}
    cwd = inp.get("cwd") or project or os.getcwd()
    if not inp.get("agent_id") and not inp.get("agent_type"):
        try:
            sl.finish_pending_grant(inp.get("session_id"), inp.get("transcript_path"))   # a typed yes whose line landed late
        except Exception:                            # a note about a grant must never cost the call its guard
            pass
    def closed(kind, fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except Exception:
            sl.log_error("guard " + fn.__name__)
            return (kind, "budgit guard: %s could not read this call; stop and report it, do not reword or retry." % fn.__name__)

    rd_keys = []
    def home_decision(*args):                    # R-473: an ask of a class the session said yes to passes; a deny never does
        decision, keys = sl.home_write_check(*args, session=inp.get("session_id"), subagent=bool(inp.get("agent_id")))
        rd_keys.extend(keys)
        return decision

    found = [("verify-forgery", closed("deny", sl.verify_forgery_decision, tool, tool_input, inp.get("agent_type"), cwd))]
    found.append(("budgit-home", closed("deny", home_decision, tool, tool_input, cwd, inp.get("agent_type"))))
    if str(tool).startswith("mcp__"):            # an MCP file tool is judged like Write for each path it is given
        found.append(("mcp-file", closed("deny", sl.mcp_file_decision, tool, tool_input, project, cwd, inp.get("agent_type"))))
    if tool == "Bash":
        raw = raw_reason(inp)
        found.append(("raw-store", ("deny", raw) if raw else None))
        command = tool_input.get("command") or ""
        import commitguard                       # R-900: a subagent commits only chained after a test run
        found.append(("commit-chain", closed("deny", commitguard.decision, tool, tool_input, inp.get("agent_id"))))
        found.append(("commit-identity", closed("ask", sl.commit_identity_decision, command, cwd)))
        found.append(("approval", closed("ask", sl.approval_decision, command, project, cwd)))
        found.append(("hook-bypass", closed("ask", sl.hook_bypass_decision, command)))
    try:                                         # R-433 / R-304: hold in every session; an error here must not stop the other guards
        import writeguard
        found.append(("persistence", writeguard.persistence_decision(tool, tool_input, cwd)))
        found.append(("destructive-git", writeguard.destructive_git_decision(tool, tool_input)))
    except Exception:
        found.append(("write-guard", ("ask", "budgit guard: the write and git guard could not run for this call.")))
    if project:
        def real_data_decision(*args, **kw):         # R-493: the keys of the ask are what a later yes is remembered for
            decision, keys = sl.real_data_check(*args, **kw)
            rd_keys.extend(keys)
            return decision
        found.append(("real-data", closed("ask", real_data_decision, tool, tool_input, project, cwd=cwd,
                                          session=inp.get("session_id"))))
        found.append(("contract-lock", closed("ask", sl.contract_lock_decision, tool, tool_input, project, cwd=cwd)))
    result = _combine([d for _, d in found])
    if result and result[0] == "ask" and inp.get("agent_id") and not _away_now():   # R-901 (while away, R-641 below does it): a subagent cannot answer a card: deny, and queue it for the coordinator
        import denylog
        _away_note("subagent-ask for %s: %s" % (tool, result[1][:300]), "subagent-ask", inp.get("session_id"), project, cwd,
                   fp=denylog.fingerprint(tool, tool_input))      # L4: one entry per session and command
        result = ("deny", "budgit guard: this call needs the user's yes, which a subagent cannot give. It is queued for your "
                          "coordinator: report it in your hand-back, do not reword or retry it. | " + result[1][:200])
        found = [(n, d) for n, d in found if not (d and d[0] == "ask")] + [("subagent-ask", result)]
    if result and result[0] == "ask" and _away_now():   # R-641: every guard ask becomes a deny while the user is away; a deny stays a deny
        import away
        _away_note("guard ask for %s: %s" % (tool, result[1][:300]), "guard", inp.get("session_id"), project, cwd)
        result = ("deny", away.REASON + " | " + result[1][:200])
        found = [(n, d) for n, d in found if not (d and d[0] == "ask")] + [("away", result)]
    try:                                           # R-493: which call asked, so only ITS PostToolUse can become a yes
        if result and result[0] == "ask" and rd_keys and any(n in ("real-data", "budgit-home") and d and d[0] == "ask" for n, d in found):
            sl.note_real_data_ask(inp.get("session_id"), tool, tool_input, rd_keys)
        else:
            sl.clear_real_data_ask(inp.get("session_id"), tool, tool_input)
    except Exception:
        sl.log_error("real-data memory")
    if result:
        _decision("PreToolUse", result[0], result[1])
        if result[0] == "ask" and project:     # the user's answer lives in the prompt; the log keeps the trail
            import denylog
            book = lambda: sl.audit(project, "%s %s %s %s" % (ASK_TAG, tool, " ".join(result[1].split())[:200],
                                                              denylog.fingerprint(tool, tool_input)))
        elif project:                          # R-55: denies are measured too (hash + masked head, never the command)
            import denylog
            denies = [(n, d) for n, d in found if d and d[0] == "deny"]
            def book():
                denylog.log_denies(project, tool, tool_input, denies)
                denylog.alarm(project)
        else:
            return
        if defer is None:
            book()
        else:
            defer.append(book)


def book_pre_tool_use(project, todo):
    """The bookkeeping of a PreToolUse decision under the project lock, waiting at most LOCK_WAIT_S. A busy lock never
    changes the decision, which is already out: the bookkeeping is skipped and the note says so."""
    with sl.try_locked(project, LOCK_WAIT_S) as got:
        if got:
            for book in todo:
                book()
            return
    os.makedirs(sl.home_state(), exist_ok=True)
    sl.append_text(os.path.join(sl.home_state(), "hooks.log"),
                   "%s pre-tool-use: project lock busy for %.0f s — the audit line was skipped, the guard decision stands\n"
                   % (sl.now(), LOCK_WAIT_S))
    emit({"systemMessage": "budgit: the project lock was busy; the guard decision stands, its audit line was skipped"})


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
                 "(`budgit ledger add --src %s --phase P`, `link` or `ack`)." % (new_id, new_id))


def on_stop(inp, project):
    session = inp.get("session_id")
    sl.reconcile_transcript(project, inp.get("transcript_path"), session)
    data = sl.load_ledger(project)
    state = sl.load_state(project)
    problems = sl.stop_problems(project, data, session)
    events = summary_events(sl.new_audit_lines(project, state))
    drop_note = ("budgit audit: " + " | ".join(l.split(" ", 1)[-1] for l in events)) if events else ""
    import general
    if not problems:
        general.forget_asked(state, session)
        sl.save_state(project, state)
        if drop_note:
            emit({"systemMessage": drop_note})
        return
    reason = general.stop_reason(state, session, problems)      # R-373: once per item, whatever the host reports about a continued Stop
    sl.save_state(project, state)
    if reason:
        out = {"decision": "block", "reason": reason}
        if drop_note:
            out["systemMessage"] = drop_note
        emit(out)
    elif inp.get("stop_hook_active"):                          # the model was asked and left it: say so, once, and let the turn end
        emit({"systemMessage": ("budgit: ledger stalled — %d problem(s) left: %s %s"
                                % (len(problems), problems[0], drop_note)).strip()})
    elif drop_note:
        emit({"systemMessage": drop_note})


def _progress_line(project, text):
    try:
        sl.append_text(os.path.join(sl.studio_dir(project), "progress.md"), "\n- %s %s\n" % (sl.now()[:16], text))
    except sl.StudioError:
        sl.log_error("progress line")


def _session_start_writes(inp, project, sid):
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
                _progress_line(project, "back to open (their session ended or went stale): %s" % ", ".join(dead))
            if back:
                _progress_line(project, "claimed again by the resumed session: %s" % ", ".join(back))
    except Exception:
        sl.log_error("session-start: claims step")
    try:
        sl.ensure_gitignore(project)          # projects activated before a line existed get it on their next session
    except Exception:                         # a bad .gitignore (Latin-1, a directory) must not cost the user the summary
        sl.log_error("session-start: gitignore step")


def _default_keepalive(postupdate):
    """R-583: keep-alive is ON by default. The first session after an update sets it up unless keepalive=no is stored, and says
    once that it is on, away mode is on, and how to switch each off. Stand-up and cleanup stay opt-in."""
    try:
        if not postupdate.default_keepalive_due():
            return
        import jobs
        postupdate.answer("keepalive", "yes")           # the default counts as the recorded yes
        budgit = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin", "budgit")
        code, _text = jobs.turn_on("keepalive", budgit)
        if code != 0:
            postupdate.answer("keepalive", "later")     # nothing was set up: try again next session
            return
        line = ("Tell the user once, in their language: keep-alive (a wake-up of idle project work every 2 hours) is on by default; "
                "`budgit setup --answer keepalive=no` or `budgit job off keepalive` switches it off.")
        if postupdate.away_enabled():
            line += (" Away mode («me voy») is on too; `budgit setup --answer away=no` switches it off. "
                     "Stand-up and cleanup stay off unless the user asks.")
        _context("SessionStart", line)
    except Exception:
        sl.log_error("session-start: keep-alive default")


def on_rules_and_questions(inp, project):
    """R-788: the team rules for this role (sent again after a compact) and, at most once per session, one question line
    for what an update still has to ask. Returns True when SETUP_NUDGE was merged into that one directive.
    Role: the coordinator when portfolio.is_tm(session), a project lead when a project is bound, else core only.
    There is no [design] line: the cards add no new UI of their own."""
    import postupdate
    import rules
    import portfolio
    sid = inp.get("session_id")
    state = postupdate.load()
    if state["rules"]["enabled"]:
        role = "coordinator" if portfolio.is_tm(sid) else ("lead" if project else "plain")
        text = rules.digest(role, off=state["rules"]["off"])
        version = __import__("updatelib").running_version(sl.studio_root()) or "0"
        if inp.get("source") != "compact" and state["rules"]["told"] != version:
            text += ("\nTell the user once, in their language: Budgit team rules are on; `budgit rules off <topic>` "
                     "turns a part off, `budgit rules list` shows the topics.")
            postupdate.mark_told(version)
        _context("SessionStart", text)
    if sl.vendored():
        return False
    _default_keepalive(postupdate)
    keys = postupdate.unanswered()
    if not keys or not postupdate.claim(sid or "", time.time()):
        return False
    need_nudge = not project and not os.path.exists(os.path.join(sl.home_state(), "config.json"))
    if need_nudge:                                 # one directive, not two: the setup call, then the questions
        _context("SessionStart", sl.SETUP_NUDGE + " " + postupdate.question_line(keys, with_setup_first=True))
        return True
    _context("SessionStart", postupdate.question_line(keys))
    return False


def on_session_start(inp, project, held=True):
    import brain
    sid = inp.get("session_id")
    sl.touch_pointer(sid)
    if held:                                      # with a busy lock the summary is read-only; the next start sweeps
        _session_start_writes(inp, project, sid)
    pointer = brain.session_pointer(project)
    conflict = sl.data_dir_conflict(project)      # R-120: a stale session recreated .studio next to .budgit
    _context("SessionStart", sl.session_summary(project, inp.get("session_id"), extra=(pointer, conflict),
                                                  full=inp.get("source") in ("compact", "resume")))
    if inp.get("source") == "compact":           # R-123: the resumed session is pointed at the checkpoint written before the cut
        try:
            line = checkpoint_line(project, inp.get("session_id"))
            if line:
                _context("SessionStart", line)
        except Exception:
            sl.log_error("session-start: checkpoint line")
    try:                                         # R-286 task 4: one line when a decision waits; a failure prints nothing
        import triggers
        due = triggers.session_line(project, source=inp.get("source"))
        if due:
            _context("SessionStart", due)
    except Exception:
        sl.log_error("session-start: triggers line")


def on_session_end(inp, project, held=True):
    """R-115: the items this session was working on leave `doing` when it closes. clear/resume keep the claims (the
    resumed session re-claims what it released; after /clear the new session starts or takes what it needs).
    Without the lock the pointer still ends and keeps the list; the ledger is left for the next session's sweep."""
    if inp.get("reason") in ("clear", "resume"):
        return
    sid = inp.get("session_id")
    data = sl.load_ledger(project)
    mine = sl.claimed_by(data, sid)
    if held:
        sl.release_items(project, data, mine, "session ended")
        if mine:
            sl.save_ledger(project, data)
    sl.end_pointer(sid, mine)


def on_post_compact(inp, project):
    sl.append_text(os.path.join(sl.studio_dir(project), "compactions.log"),
                   "%s trigger=%s\n%s\n\n" % (sl.now(), inp.get("trigger", ""),
                                             inp.get("compact_summary", "")))


def on_pre_compact(inp, project):
    """R-123: a mechanical checkpoint just before the context is cut. It never blocks or fails the compaction: any
    error goes to hooks.log and the hook still exits 0. Lock-free: it only reads the ledger and writes its own file."""
    import checkpoint
    sdir = sl.studio_dir(project)
    items = []
    try:                                         # a ledger that is a symlink or resolves outside the state folder is not read
        lp = sl._ledger_path(project)
        if os.path.lexists(lp) and not checkpoint.plain_file_in(lp, sdir):
            raise OSError("ledger.json is not a plain file inside the state folder")
        items = sl.load_ledger(project)["items"]
    except Exception as exc:
        _hook_note("pre-compact: ledger not readable (%s); the checkpoint lists no items" % exc.__class__.__name__)
    path, err = checkpoint.run(project, sdir, items, inp.get("transcript_path"),
                               inp.get("trigger") or "", inp.get("session_id") or "",
                               store=checkpoint.store_dir(sl.home_state(), project))
    if err:
        _hook_note("pre-compact: checkpoint failed — %s" % err)


def _hook_note(text):
    try:
        os.makedirs(sl.home_state(), exist_ok=True)
        sl.append_text(os.path.join(sl.home_state(), "hooks.log"), "%s %s\n" % (sl.now(), text))
    except Exception:
        pass


def checkpoint_line(project, session=None):
    """The SessionStart line after a compaction: where the newest checkpoint is (this session's first, and only a file
    this module wrote, under the per-user state folder). "" when there is none."""
    import checkpoint
    path = checkpoint.newest(checkpoint.store_dir(sl.home_state(), project), session)
    if not path:
        return ""
    return ("budgit: the context was just compacted. A mechanical checkpoint (open ledger items, last tool calls, git "
            "status, progress.md head) was written before the cut: read %s, then continue from the ledger." % path)


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
            "budgit-builder, budgit-verifier, budgit-scout-reader, budgit-researcher, budgit-plan-critic, budgit-security-reviewer and "
            "budgit-persona-sim (not `budgit:X`). Bind this session with `budgit activate` to turn the ledger on; "
            "only pushed work survives this VM." % (cli, where))


LOCK_WAIT_S = 2.0


def _busy_note(event, what):
    os.makedirs(sl.home_state(), exist_ok=True)
    sl.append_text(os.path.join(sl.home_state(), "hooks.log"),
                   "%s %s: project lock busy for %.0f s — %s\n" % (sl.now(), event, LOCK_WAIT_S, what))


def when_busy(event, inp, project):
    """The project lock stayed taken. What must not be lost is kept without it: the user's inputs go to the inbox spool
    (the next holder of the lock merges them), reports and the pointer are their own files. Advice and checks that the
    next call repeats are skipped, with a note in hooks.log."""
    if event == "user-prompt-submit":
        sl.spool_inbox(project, inp.get("prompt") or "", inp.get("session_id"), "prompt")
        _busy_note(event, "the prompt is in the inbox spool; the next holder of the lock merges it")
    elif event == "post-tool-use" and inp.get("tool_name") == "AskUserQuestion":
        response = inp.get("tool_response")
        text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        sl.spool_inbox(project, "AskUserQuestion answer: " + text, inp.get("session_id"), "answer",
                       ref=inp.get("tool_use_id"))
        _busy_note(event, "the answer is in the inbox spool; the next holder of the lock merges it")
    elif event == "post-tool-use" and inp.get("tool_name") == "SubagentHandback" or event == "subagent-stop":
        message = (inp.get("tool_input") or {}).get("message") if event == "post-tool-use" else inp.get("last_assistant_message")
        sl.save_report(project, inp.get("agent_type"), inp.get("agent_id"), message)
    elif event == "post-compact":
        on_post_compact(inp, project)           # an O_APPEND line: the lock was never needed
    elif event == "session-start":
        on_session_start(inp, project, held=False)
        _busy_note(event, "claims sweep and .gitignore skipped; the next session start repeats them")
    elif event == "session-end":
        on_session_end(inp, project, held=False)
        _busy_note(event, "the claims stay for the next session's sweep")
    else:
        _busy_note(event, "skipped")


def on_permission_request(inp, project=None):
    """R-641: Claude Code's own permission prompt; unchanged unless the user is away, then denied and queued."""
    if _away_now():
        import away
        try:
            shown = json.dumps(inp.get("tool_input"), ensure_ascii=False)[:300]
        except Exception:
            shown = "(unreadable)"
        _away_note("permission prompt for %s %s" % (inp.get("tool_name"), shown), "permission", inp.get("session_id"),
                   project, inp.get("cwd"))
        emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                     "decision": {"behavior": "deny", "message": away.REASON, "interrupt": False}}})


HANDLERS = {"permission-request": on_permission_request, "user-prompt-submit": on_prompt, "pre-tool-use": on_pre_tool_use,
            "post-tool-use": on_post_tool_use, "stop": on_stop, "subagent-stop": on_subagent_stop,
            "session-start": on_session_start, "post-compact": on_post_compact, "session-end": on_session_end,
            "pre-compact": on_pre_compact, "post-tool-use-failure": on_post_tool_use_failure}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        inp = json.load(sys.stdin)
        if os.environ.get("BUDGIT_HOOK_DEBUG") and isinstance(inp, dict) and inp.get("agent_id"):   # R-849: which fields a subagent payload carries
            _hook_note("payload-keys %s %s" % (event, ",".join(sorted(k for k in inp if "path" in k or k.startswith("agent")))))
        if isinstance(inp, dict) and inp.get("agent_type"):
            inp["agent_type"] = sl.canon_agent(inp["agent_type"])   # cloud `studio-X` == plugin `studio:X` for every guard
        try:                                           # a failed lookup must not take the guards down with it
            project = sl.project_for_session(inp.get("session_id"))
        except Exception:
            sl.log_error("project lookup")
            project = None
            emit({"systemMessage": "budgit: project lookup failed in %s — enforcement off this turn; "
                                   "see ~/.budgit/hooks.log" % event})
        if not project and event in ("session-start", "user-prompt-submit"):
            project = sl.auto_bind(inp.get("session_id"), inp.get("cwd"), inp)   # a chat in a project folder is bound
        if event in ("user-prompt-submit", "stop"):    # R-115 heartbeat: last_seen, at most once a minute
            sl.touch_pointer(inp.get("session_id"))
            if on_tm(event, inp):                      # R-283 S10: the TM's words stay out of every project inbox
                project = None
        if event == "user-prompt-submit":
            on_trust_prompt(inp)            # the user's own typed /budgit-trust work|revoke, in every session
            try:                            # R-641: «me voy» sets, the user's next real prompt clears; only from this real event
                import away
                import postupdate
                import i18n
                state = away.on_prompt(inp) if postupdate.away_enabled() else None     # R-583: away=no switches «me voy» off
                if state == "on":
                    emit({"systemMessage": i18n.t("away.on")})
                elif state == "off" and away.queue(project=away.project_key(project, inp.get("cwd"))):
                    _context("UserPromptSubmit", "budgit: the user is back. %d question(s) were queued while they were away: run `budgit "
                             "away off` to read and clear them, then bring them to the user in ONE card with a recommended default each."
                             % len(away.queue(project=away.project_key(project, inp.get("cwd")))))
            except Exception:
                sl.log_error("away prompt")
        if event == "permission-request":   # R-641: Claude Code's own permission prompt; unchanged unless the user is away
            on_permission_request(inp, project)
            return 0
        if event == "pre-tool-use" and inp.get("tool_name") == "AskUserQuestion":   # R-641: only while away; otherwise as before
            if _away_now():
                import away
                try:
                    asked = away.ask_text(inp.get("tool_input"))
                except Exception:
                    asked = "(unreadable question)"
                _away_note(asked, "ask", inp.get("session_id"), project, inp.get("cwd"))
                _decision("PreToolUse", "deny", away.REASON)
            return 0
        if event == "user-prompt-submit":  # context nudge runs in EVERY session, zero model tokens
            try:
                note = relay_note(inp)
                if note:
                    _context("UserPromptSubmit", note)
            except Exception:
                sl.log_error("relay note")
            nudge = sl.context_nudge(inp.get("session_id"), inp.get("transcript_path"))
            if nudge:
                emit({"systemMessage": "budgit: " + nudge})
            idle = sl.no_task_nudge(inp.get("session_id"))
            if idle:
                emit({"systemMessage": "budgit: " + idle})
            import budget
            note = budget.notice(inp.get("session_id"))   # once per level change, every session
            if note:
                emit({"systemMessage": note[0]})
                _context("UserPromptSubmit", note[1])
            import introspect   # self-check when a rule broke since the last prompt, every session
            check = introspect.live_check(inp.get("session_id"), inp.get("transcript_path"))
            if check:
                _context("UserPromptSubmit", check)
            import general   # R-373: a prompt asking for text for a person gets the humanize routing, project or not
            route = general.on_prompt(inp)
            if route:
                _context("UserPromptSubmit", route)
            if not project:
                project = general.bind(inp)      # a chat in a folder with no notebook gets the general one (an old session binds here)
        if event == "session-start":
            try:                                   # R-712: once per installed version, repairs the jobs Budgit owns; silent unless it fails
                import installstep
                line = installstep.run(sl.studio_root(), __import__("updatelib").running_version(sl.studio_root()), installstep.interpreter())
                if line:
                    emit({"systemMessage": line})
            except Exception:
                sl.log_error("session-start: install step")
            note = cloud_note()
            if note:
                _context("SessionStart", note)
            if project:
                try:                                   # R-785: an update brings the checks question to existing projects
                    import checksdetect
                    offer = checksdetect.offer_line(project)
                    if offer:
                        _context("SessionStart", offer)
                except Exception:
                    sl.log_error("session-start: verifier checks offer")
            try:                                 # R-399: the role block and the once-per-version reorganize
                import roles
                done = roles.apply_once()
                if done:
                    emit({"systemMessage": "budgit: " + done})
                for text in roles.session_start_texts(inp, project):
                    _context("SessionStart", text)
            except Exception:
                sl.log_error("session-start: roles")
            import general
            if not project:
                project = general.bind(inp)      # R-373: the general notebook
            found = general.offer(inp.get("cwd")) if general.offerable(project) else None
            if found:                            # R-373: one line and the command, once per folder; nothing is created
                emit({"systemMessage": found[0]})
                _context("SessionStart", "budgit: offered the user a notebook for this folder (%s). Create it only if they say yes." % found[1])
        nudged = False
        if event == "session-start":
            try:
                nudged = on_rules_and_questions(inp, project)
            except Exception:
                sl.log_error("session-start: rules and questions")
        if event == "session-start" and not project:   # model rules matter in every session, not only Budgit ones
            if not nudged and not sl.vendored() and not os.path.exists(os.path.join(sl.home_state(), "config.json")):
                _context("SessionStart", sl.SETUP_NUDGE)   # R-52 E3: first run on a new machine
            import budget
            cur = budget.current()
            _context("SessionStart", budget.short_line(cur["level"], cur["reason"]))
        if event == "post-tool-use-failure":  # introspection runs in EVERY session
            on_post_tool_use_failure(inp, project)
            return 0
        if event in ("session-start", "session-end", "subagent-stop"):   # R-847: machine-wide, hook-side, zero model calls
            try:
                import procclose
                if event == "session-start":
                    procclose.on_session_start(inp)
                elif event == "session-end":
                    procclose.on_session_end(inp)
                else:
                    out = procclose.on_subagent_stop(inp)
                    if out:
                        emit(out)
            except Exception:
                sl.log_error("procclose " + event)
        if event == "subagent-stop" and inp.get("agent_id"):  # R-64: the counter goes too in unbound sessions
            import agentcap
            agentcap.reset(inp.get("agent_id"))
        if event == "pre-tool-use" and inp.get("agent_id"):   # R-63: per-subagent call ceiling, every session
            try:                                # a failure of the call-count bookkeeping must not drop the guards that follow
                import agentcap
                import budget
                cap = agentcap.check(inp, budget.policy(budget.current()["level"])["agent_calls"])
            except Exception:
                sl.log_error("agent call cap")
                cap = None
                emit({"systemMessage": "budgit: the call budget check could not run; enforcement did not run for this call "
                                       "(see ~/.budgit/hooks.log)"})
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
            todo = []
            on_pre_tool_use(inp, project, todo)
            if todo:
                book_pre_tool_use(project, todo)
            return 0
        if event == "post-tool-use":
            seal_verify_report(inp)             # machine-wide and lock-free: a busy lock or an unbound session still seals
            try:                                # R-493: the tool ran after a real-data ask, so the user said yes to it
                sl.promote_real_data_yes(inp.get("session_id"), inp.get("tool_name"), inp.get("tool_input") or {})
            except Exception:
                sl.log_error("real-data yes")
            try:                                # R-520: text written between tool calls is on the user's screen; a nudge, never a deny
                import outguard
                interim = outguard.interim_note(inp)
                if interim:
                    _context("PostToolUse", interim)
            except Exception:
                sl.log_error("interim nudge")
            if inp.get("tool_name") in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                path = (inp.get("tool_input") or {}).get("file_path") or (inp.get("tool_input") or {}).get("notebook_path")
                if path:                        # the script scan reads what this chat wrote before it runs
                    sl.note_session_write(inp.get("session_id"), os.path.join(inp.get("cwd") or os.getcwd(), os.path.expanduser(path)))
        if event == "stop":                     # R-373: the humanize check runs in every session, once per request
            import general
            reason, note = general.stop_checks(inp, project)     # R-375: and the client-text gate
            if reason:
                emit({"decision": "block", "reason": reason})
            elif note:
                emit({"systemMessage": note})
        if event == "pre-compact":              # R-123: lock-free, and a failure is logged, never raised
            if project:
                on_pre_compact(inp, project)
            return 0
        if not project or event not in HANDLERS:
            return 0
        with sl.try_locked(project, LOCK_WAIT_S) as got:
            if got:
                HANDLERS[event](inp, project)
        if not got:
            when_busy(event, inp, project)
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
