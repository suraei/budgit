# Coordination: the habits of the session that talks to the user and runs the lanes
Read this when you are the coordinating session (several lanes, or a long project). A lane reads only its own brief.

## Reports and evidence
- Relay a lane's success only with a quoted evidence line (command and result, `file:line`, or a log line); otherwise say "not checked". A lane's "criterion met" is a claim, and the same model that built it cannot be the proof.
- For a measurement, quote the median of the whole run, not the best sample, and the size it was measured on.
- When a bug shows on screen, read the self-check or probe log before the next attempt; quote the line that explains it, then change code.

## Your own shell
- Never `cd` the coordinating shell into a lane's folder. Use absolute paths or `git -C <dir>`, so the next command cannot run in the wrong place.
- Give every hand test step the session and folder it runs in. A test the user has to run by hand is walked by an agent first, so the user meets the real bugs only once.

## Lanes that go quiet or must stop
- When a lane ends a turn with a report you have not read, read it and send its next step in the same turn. An idle lane with a clear next step is lost time, and the user pays for it.
- A stop order: check the delivery state first. If the message is still queued, interrupt before anything else, and tell the user "stopped" only once the lane confirms it. A queued stop that nobody ran is the worst case.
- A lane that stops without finishing leaves its open items to you: re-assign each one to a live lane or say so to the user in the same turn.

## Before a release or push
- Map the user's request to every open and doing ledger item and show a short table: built, deferred, dropped. Ask for the yes to publish only after the table is shown, so nothing the user expected goes missing without being named.
- Items of a lane that died or was archived are re-owned that turn: give each a lane, move it to `later`, or tell the user.

## Before opening a lane: the running limit
Run `budgit capacity` first: it prints the portfolio limit T (set by the budget level), each project's share (priority weight, at least 1, never above what is ready) and what is free now. `ledger start` refuses a NEW start over the project's share or while the 5-hour window is at 70% or more; a running item is never stopped by a lower limit. Two stuck items (blocked, or past 1.5 x appetite since `doing_since`) stop new starts in that project: unblock them first. Pass `--expedite` for the one urgent slot of the whole portfolio (audited), or `--override-by I-n` with the input where the user said to go ahead (audited, the input must be typed by the user). Without budget data the limit uses the normal ceiling and says so; any error reading the portfolio lets the start through.

## Decision cards: `budgit triggers`
There is no fixed weekly card. Run `budgit triggers` after reading a lane report and at the start of a day: it lists the decisions that need the user now (age past appetite, blockers, an empty project, a release with its parked review, parked items that waited 30 or 90 days, a 7-day safety net), at most 3 a day, urgent first, at most one parked card a day with 5 items. Render each card as one short question with its default (what happens if nobody answers: keep going, keep parked, wait; nothing is ever dropped by silence), then record it: `budgit triggers shown KEY...` when put in front of the user, `budgit triggers answered KEY --input I-n` with the input where the user answered (typed by the user, not added by `ledger add`). A card shown and ignored comes back after 1 day, then 2, 4. A protected project's card shows only as "1 card in <id>, open its PM chat". The SessionStart line `Budgit: n decisions waiting` counts the current project's cards, or all of them in the hub.

## Sweeps
- A sweep (archive finished lanes, stale claims, loose branches, unanswered questions, stray processes) covers every project the user has registered, not only the one in front of you. `budgit portfolio` lists them; the steps are in `reference/portfolio.md`.

## The TM's running log
- `budgit tm log TEXT` appends one dated line (`YYYY-MM-DD HH:MM text`, local time) to `private/tm/status.md` of the project; `-` reads the text from stdin. Use it instead of `echo >> status.md`: one write per line, a symlinked log or a symlinked `private/` or `private/tm/` is refused, and `private/` is git-ignored. A text that starts with `-` goes after `--`, and `--project` before it: `budgit tm log --project DIR -- "-x done"`.
- `budgit housekeeping` is the daily tidy-up: the flow count of the last 24 hours for every project (in, out, pending, what waits in the next version) and the git worktrees and branches that are finished. A worktree goes only when its branch is merged into main, it has no uncommitted or untracked files, nothing in it changed for 24 hours and no live session works in it; the branch goes with it (`git branch -d`, never `-D`, never `--force`). Without `--apply` it only says what it would do. Projects under `protected_dirs` (or marked `"private": true`) are only counted. Above load 8 only the flow count runs. `--schedule on|off|status` sets up the daily run as a macOS launch agent (explicit, never automatic; run it again after a plugin update moves the path); `--last` shows the last applied report. Dirty or unmerged worktrees idle for more than 3 days are drop-list candidates: record them in the owning PM's ledger, do not delete them.

## Every choice is a record
- A choice the user makes in a question card or in chat becomes a ledger item the same turn, in their words. A decision that lives only in the conversation is lost at the next compaction.
- Previews are briefed on what they must show: the lane gets the question the mock answers, and the mock states which pick it supports.

## When the user must test something
- Spell each step out simply: first the action, then what the user should see on screen. One action per step.
- Do not refer to a test by a code, a row or a section ("T1-T9", "Step 4"), do not say "the file from before", and do not tell them to run it at any time without the steps. A hand-test file may keep its codes for the coordinator; the user receives the steps themselves.
- Say when to relaunch or reopen anything the test needs, and who types any command (not the user, unless they asked to).

## When the user must review a file
- Send the file as a clickable file card first, then ask in the same turn with an interactive question card, so the answer is one click in the chat. A file outside the project folders is not clickable: copy it into the project first.
- Each question names its file by the same short name or number as its card. Never put a markdown link inside a question: the card shows it as raw text and it cannot be clicked.
- One file per question. If there are several files, each question names its own.
- A mock shows only what the end user will see. Internal notes (budgets, owners, ledger ids) live in the spec, never in the page under review.

## Tools the user names
- When a message names a tool, repo, skill, plugin or site to look at, give each name a row the same turn, before the rest of the answer: an `unresolved` entry in the skills registry when you maintain it, otherwise a ledger item. A name that lives only in the conversation is lost at the next compaction, and the user has to say it twice.
- Before you pick a skill for a job, call `budgit skills why`; do not choose from memory.

## Pending picks
- Keep a dated list of the user's pending picks. Every checkpoint and every idle report lists them, oldest first.
- When you relay a pick to a lane, ask the lane to echo the pick back in its next report. A lane that reports it is waiting on something already relayed gets the pick re-sent at once.
