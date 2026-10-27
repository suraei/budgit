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

## Sweeps
- A sweep (archive finished lanes, stale claims, loose branches, unanswered questions) covers every project the user has registered, not only the one in front of you. The projects folder under `budgit hub` lists them.

## Every choice is a record
- A choice the user makes in a question card or in chat becomes a ledger item the same turn, in their words. A decision that lives only in the conversation is lost at the next compaction.
- Previews are briefed on what they must show: the lane gets the question the mock answers, and the mock states which pick it supports.

## When the user must test something
- Write out each step in plain words: what to do, then what they should see. One action per step.
- Never point to a test by a code, a row or a section ("V1 to V14", "Stage 4"), never "the file from before", and never "do the test whenever you like" without the steps. A hand-test file may keep its codes for the coordinator; what reaches the user is the steps themselves.
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
