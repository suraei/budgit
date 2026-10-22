# Phase 3 — Plan, Phase 4 — Build
- Spikes answer the user's real question with a real case the user would run (sanity-check the example: geography, units,
  dates), never "does the plumbing answer".
- Plan: `superpowers:writing-plans` (if not installed: tasks with files, a failing test first, EARS checks) — WHAT + acceptance checks per task, not full function bodies;
  then `budgit:plan-critic` (fresh context) before building. Spikes before commitments; installs,
  migrations, purchases and pushes are explicit steps the user approves.
- The plan carries its own `## Alternatives` (approach options, why rejected) and, per task, the test
  layers it must produce: `[func]` test file, `[perf]` benchmark script + synthetic generator with the
  budget asserted, `[design]` screenshot step. A task with no executed check is not a task.
- If the plan touches a host surface you have not used, `## Surface contract` follows `## Assumptions`: one table (rows,
  columns, units, props honoured or ignored, redraw limit), each number quoted from the docs (file + line) or measured by a
  named probe. A number without a source is an assumption.
- Probes answer one question each, are designed from the docs, and write their result to a log file the session reads. Never
  ask the user to look at a probe. On a failed probe, read the log first and quote the line behind the theory before editing code.
- **MVP first:** right after `## Assumptions` the plan has `## MVP` — ≤5 tasks forming a walking skeleton (structure, one real
  end-to-end flow, the start command, the test harness), each mirrored as a ledger item tagged `mvp`; every other
  task sits under `## After the MVP` and its ledger items are tagged `later`. Task 1 is the runnable skeleton.
  The MVP gate: the user sees it running (command + screenshot/output) before any `later` item starts. Ideas that
  appear while building go to the ledger as `later`; they never extend the current task.
- The plan starts with `## Assumptions` (≤5; anything with 2+ readings is asked once, batched). Surgical
  diffs: every changed hunk traces to a plan task or ledger item; unrelated dead code is reported, not deleted.
- Build sessions run on sonnet (switch the session model, tell the user); bulk text/data (KB rows, fixtures, copy) is
  drafted through `budgit codex draft` and only reviewed by Claude.
- Build: you write and integrate, with `superpowers:test-driven-development` (if not installed: write the failing test first, watch it fail, then the code); subagents do read-only research
  or review. The one exception, opt-in: worktree implementer subagents, only when the plan marks tasks parallel AND each
  touches disjoint files, has an executable check and a tool-call ceiling; only the main session
  merges/commits to main. Never the agent-teams flag (~7x tokens). Commit small; push only as the project's CLAUDE.md says (default: ask the user first; a hook reminds you); commits must use the user's noreply
  identity (a guard blocks leaks in any session).
- Real data: list it in `.budgit/project.json` → `protected_paths`; writes to it ask the user first; work on
  synthetic copies in the scratchpad.
- The checks are the contract: a failing check is fixed in the code, never by editing an existing lint/test
  config, `verify.checks` or `--no-verify` (guards ask the user). Only `budgit:verifier` writes `.budgit/verify/`.
- Before enabling an MCP server or plugin: `budgit audit-mcp [--home]` (pinned vs floating, literal secrets,
  broad allow rules). Before installing a skill copied from elsewhere: `budgit lint <path>` (hidden characters).
- Ledger items close with `budgit ledger done R-n --evidence …` — the evidence names the command and
  its result for each layer, not "done".
