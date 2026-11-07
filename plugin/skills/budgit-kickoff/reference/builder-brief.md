# Builder brief template

Copy this, fill every field, and start `budgit:builder` with it in the same turn as the build ask.
A coordinator never builds code itself. For rounds 2 to N, continue the same builder with SendMessage
and pass the user's text verbatim.

## Goal
One or two sentences: what exists when this is done, and for whom.

## Worktree and branch
- Worktree: <absolute path>
- Branch: <name>, based on <commit>
- Work only inside the worktree. Never touch main. Never merge or push.

## Files to read first
- <absolute path>, <why>
(Skip folders that are cloud-synced and may be evicted; copy what is needed into the worktree.)

## Format and input
- format: <the exact format or tool the user named, for example an Excel Office Script>. Required for a tool
  the user will run. Deliver exactly this, never another runtime in its place.
- input: <the user's current file or the columns that decide the result, and the user's real data size, for example
  80,000+ rows>. If missing, say so and ask once; do not build against an older template.

## Design and invariants
- Design: <the shape of the solution>. A tool with several views has ONE shared outcome function that every
  view calls.
- Invariants: <one per line>. The builder writes a test for each.

## Tests
- <test file>: <test name>, proves <invariant or SHALL line>
- Run command: <one plain command>

## Rules
- Edit and Write only; no heredocs or shell redirection into the repo. If an Edit fails, Read the lines again.
- One plain Bash command per call; no `$(...)`, no `$VAR` as the command, no long `&&` chains, never `cd`.
  A call that needs approval is skipped and marked UNTESTED.
- Commit only after green, with `tests && git commit`. Never amend. No attribution lines.
- Keep a progress file at <path> after each step.
- `ESCALATE: <why>` at the top of the hand-back if the design needs a judgement call.

## Call budget
<items x 8 plus renders> tool calls. Hand back before it runs out, even with parts UNTESTED.

## Hand-back
Commit ids; each SHALL line or invariant with its test; pasted test results; what was left undone or
UNTESTED; tool-call count.
