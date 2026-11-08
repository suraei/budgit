## 5. Structure and roles (coordinator, lead, worker)
- Coordinator: one top-level session for the whole machine. Project lead: one top-level session per project. Lanes (extra sessions for independent work) and workers (subagents or nested sessions) nest under their project lead.
- Coordinator and leads think: design, decide, brief, route, review evidence, talk to the user, record decisions. Workers do: code, pushes, merges, settings, repairs, cleanups, scans, suites. The coordinator writes no product code and briefs a builder in the same turn.
- Escalation goes up: worker to lead (results, blockers), lead to coordinator (decisions, causes beyond one project), coordinator to user. Goals and findings go down. Never chores or commands upward.
- Follow-ups on unfinished work, a failed block or a diagnosis go back to the same worker with the user's words and output verbatim.
- Infrastructure (disk, sync folders, moving folders, repairing the git store) goes to an infrastructure worker; a lead unblocks only its own repo and hands up the rest in the same turn.
- A new session starts only on a turn the user typed; later stages run in the lane or as subagents. A new lead starts detached at top level.
- Subagent: a result needed this turn. Nested session: work that outlives the turn or needs the user's steering.
- Keep building toward the goal through independent stages without asking to start each.
- Wait with background runs and notifications, never sleep loops; meanwhile do other unblocked work. Archive finished lanes yourself.
- Liveness: report minutes since the last transcript or output write, plus any pending prompt or live child. Quiet 15 minutes: check; a pending prompt means re-brief. Quiet 30 minutes and nothing running beneath it: treat it as stalled, stop it, and relaunch a smaller task with the partial work attached.
- Single-session mode [needs: session tools (start, detach, list, archive) | fallback: one session plays coordinator, lead and worker in turn, each turn naming the role it plays, using the same ledger and progress files; subagents do the worker part where the host has them]. The user is told once which mode runs.
- Evidence before "done". Test it yourself before asking the user.

### Worker brief checklist
- Edit and Write for files, not heredocs through the shell. One plain command per shell call; no `$(...)`, no long chains; if a call would need approval, skip it and mark the check UNTESTED.
- Budget: items times 8 tool calls plus renders; report before the cap.
- Commit only after green tests (`tests && git commit`); no amends.
- No destructive recovery: never run a hard `reset`, a forced push, `rm`, `stash`, `clean`, or a `checkout` with force. On red, stop and report. Use `--quit`, not `--abort`, for cherry-pick, merge, rebase and am. Merge briefs check HEAD against the expected commit first and each expected commit's ancestry at the end.
- Browser: open your own tab; never touch one you did not open; list the tabs used. Stop every server you started.
- Self-test on live source from a cold start; fixtures alone never suffice.

## 16. Background jobs (opt-in)
- Keep-alive: work never stops unless the user says so. Each round: skip if the budget is critical. Coordinator mode: wake idle project leads, close lanes quiet over 4 hours after saving their work. Single-session mode: run the project's top unblocked item.
- Daily stand-up (skipped when the plan budget is tight or critical): each lead batches same-area items, folds duplicates (never drops), orders bugs first, finishes before starting, retro-inspects yesterday's incidents. Protected projects report counts only. Messages are written to an outbox file first; anything that was refused gets sent on the following keep-alive round.
- Daily cleanup: tidy finished worktrees and branches; report first, apply only what is safe.
- Jobs never message the user, push, publish or change settings.
