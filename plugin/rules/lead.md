## 6. Decisions, ledger and triage (lead)
- Record each decision the user states in the project ledger the same turn, with their words.
- Triage each note on arrival: do now (small), fold into an item naming the same deliverable, file with version and score, or drop naming its cover. An approved spec or mock is never folded or parked without a yes.
- Never first-in-first-out. Class A (a bug the user sees, a broken function, performance) leads the next release; Class B waits for a yes.
- Easy fixes go the same day to a cheap worker, with targeted tests. Exceptions: security, a user decision, a file conflict.

## 8. Urgent tools for the user (lead)
- A tool needed now ships once it passes the checked-first gate on a copy of the user's data; fixes follow as versions. The builder starts at once; later rounds go to the same builder.
- Intake on one card in the first turn: the user's file or headers, row count, format, expected numbers.
- Gate for multi-view tools: one shared outcome function; views add up and agree; every status mapped; no number stored as text; the user's numbers reproduced; an independent recompute matches.
- Reviewers never delay it. After the first wrong-number report, fix the class.
- The user's format is hard: exactly the named one (Excel script, shortcut, page), no other runtime even as an extra.
- Read the user's current file or ask once for it. Design and measure at 80,000 rows or more. Say what you could not run.

## 11. Release and docs (lead)
- Release small: ship each green set. A passing candidate ships at once; one unreleased after 6 hours is an incident. Hotfix within the hour from the last shipped tag.
- Only reviewed tips enter a candidate (verifier, and security, pass at that exact commit). Red main stops the line; only its fix merges.
- Public gates: verifier pass, full suite, security pass if security, leak scan, plugin validation, secrets scan, no attribution trailers, fresh-install check, every phase item with the user's words built or shown deferred, a docs step (README, wiki, changelog) per publish.
- Release cards say what the user sees on their data, with evidence, list priorities with status and target, and list unrun platforms as UNTESTED.
- Scheduled jobs survive updates: stable launcher path, never the versioned cache; re-registered at session start; post-update steps run once per version.
- Sync folders: before a commit, suite or release, count placeholder files in `.git` and download them; no worktrees in synced folders. [needs: a sync client | fallback: skip]

## 12. Retro and incidents (lead)
- Incidents: a fail after a pass; a repeat report of done work; done work missing from the user's build; red after a merge; a wrongly blocked call by a guard; a session that stalled; a long lock wait; a rule that has neither a tool nor a check.
- Filed the same turn: (1) evidence first, from ledger, git and reports; (2) root cause, asking why until the root and why no gate caught it; (3) prevention goes into the owning command, hook or sweep, proven by replay; (4) check it fixes what the user sees; (5) external check on raw evidence, findings VERIFIED, REFUTED or UNVERIFIED, disagreements shown to the user with both sides; (6) escalate causes beyond one project; (7) make it global.
- Done check: every hand-back carries one line each for `tested:`, `not tested:`, `enough:`, `cheaper:`, `angles:`. A missing field means not done.

## 15. Visual work (lead)
- Before showing a design, self-check spacing, symmetry, hierarchy, variety and repetition; map each of the user's words to the page.
- Complex generated views are validated at realistic multi-entity scale: bounded counts, filters isolate, order and pagination hold.
