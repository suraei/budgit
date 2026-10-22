# Phase 7 — Retro (each phase gate + project end)
Run `budgit comply --days 7 --project .` (offline, 0 tokens) and paste its table. Then run
`budgit retro-mine --days 7 --project .` (offline, 0 tokens): it writes the user's corrections (design, redo, bug,
clarity, should-have; what ran before each; asks the user made twice) to `.budgit/private/retro-mine-<date>.md`, which is
git-ignored. Paste only the COUNTS into retro.md, never the snippets. Any category with ≥ 2 hits, any "design
correction with no design skill before", or any ask said twice becomes a proposed lesson/memory in this same retro
(`<hub>/lessons/inbox/`, the user's OK first; nothing is written for the user). Append to `.budgit/retro.md`:
metrics (rule violations and hook model fills from comply, the `budgit self-check` lines that fired and what
changed after each, elapsed time, the user's corrections from the corrections log, bugs
found late, reopened ledger items, review findings actually fixed, the brief's go/kill call and probability
against what happened, the agents' notes in `.budgit/friction.md` — then clear that file), wins, mistakes, root cause, and
0–3 lessons, each tied to an observed failure or success ("would someone repeat this mistake without
it?"). Check `<hub>/lessons/INDEX.md` first (`budgit hub` prints `<hub>`): a repeat → propose promotion to a
rule/checklist/hook (the user approves). Never write Budgit files yourself: propose in
`<hub>/lessons/inbox/<date>-<project>-<slug>.md` and tell the user.
