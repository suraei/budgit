---
name: budgit-kickoff
description: Use when the user starts a NEW project or idea (app, videogame, script, prompt/instructions, tool) — e.g. "proyecto nuevo", "tengo una idea", "quiero hacer una app/juego" — or resumes a project that has a `.budgit/` folder. Runs Budgit's phased workflow (validate → design → plan → build → QA → launch → retro) with an enforced input ledger, lenses per role and retros.
---

# Budgit kickoff — v0.11.48 (2026-10-21)

Living process of Budgit (history in CHANGELOG.md). `<hub>` below = the folder `budgit hub` prints
(research/, lessons/, projects/, ideas/; not set up yet → reference/setup.md). Reply in the user's language; code/docs in English.
**Cloud sessions (claude.ai/code load no plugins):** a project vendored with `budgit vendor` carries this skill, the
agents and the hooks in `.claude/`; there the agents are `budgit-verifier`, `budgit-scout-reader`, `budgit-researcher`,
`budgit-plan-critic`, `budgit-security-reviewer`, `budgit-persona-sim` (dash, not `budgit:X`) and the CLI is
`.claude/budgit/bin/budgit`. The vendored guards accept both names; the plugin accepts `budgit:X` (`studio:X` too during the 0.12 compat window).
This file is the must-keep core. Load the phase file and the lenses you need, when you need them:
`phases/1-validate.md · 2-design.md · 3-plan-build.md · 5-qa.md · 6-launch.md · 7-retro.md`,
`lenses/{product,design,writing,a11y,security,marketing-devrel,game,motion,data-safety,edge}.md`,
`reference/ledger.md` (CLI), `reference/budget.md` (budget levels), `reference/brain.md` (typed [[links]] between
notes; before trusting an old note: `budgit brain current <note>`).

## First action of every session on a project
New project → `budgit new <slug> [--rigor mini|standard]` (folder in the projects root, git with noreply
identity, `.budgit/`, CLAUDE.md, hub card; binds this session). Just an idea for later →
`budgit idea "<text>"`. Existing project → `budgit activate <project dir>` (binding is per session) — then every input the user sends is captured and a
turn can't end while one is untracked; track each with `budgit ledger add|link|ack` (see
reference/ledger.md). Record `CORRECTION #n` + root cause whenever the user corrects you. Keep
`.budgit/progress.md` a ≤40-line snapshot (phase, done, next, blockers) rewritten in place.

## Ground rules (always)
- **MVP first (the usual failure is work that sprawls and never reaches an MVP).** Every project fixes its MVP early:
  the base/structure, ≤5 ledger items, runnable end to end (one real flow + one command to start it). Tag them
  `budgit ledger phase mvp R-n …`; build the walking skeleton FIRST and show it running to the user. Until the MVP
  ships, every new idea, research angle, polish or tool improvement — yours, an agent's or the user's — is parked:
  `budgit ledger add … --phase later` / `budgit ledger phase later R-n`, never started (the user's explicit "do it now" is the
  only override). `budgit status` and the session start show «MVP n/m»; `ledger set R-n doing` on a non-MVP item
  warns — stop and park it. Resuming a project without MVP tags → define them before any other work.
  Same for fixes: a review that keeps finding issues is closed after ≤3 rounds with the residual risk named to
  the user, not iterated forever.
- **Budgit-level topics are not yours:** backups, storage, tooling, process, plugins, settings →
  check `<hub>/research/` + `<hub>/docs/research/` + `<hub>/docs/decisions/`; not covered → write
  `<hub>/lessons/inbox/<date>-<project>-<slug>.md` and tell the user. Never research or
  ask the user about them in parallel. Never edit Budgit files yourself.
- **The user's weekly limit is finite:** research via `budgit:researcher` (sonnet), one agent per
  angle, reuse saved research first, announce heavy runs.
- **New host surface or platform API:** a mod band, a desktop pane, a vendor API you have not used. Before the first
  build, read its official docs end to end and run `budgit:researcher` for prior art; notes are saved with URL + date (project
  `.budgit/research/`, hub `<hub>/research/`). No build commit before the note exists; a lane that asks to iterate on the user's
  screen gets a probe that logs instead.
- **Verify online, never from memory** (versions, APIs, prices, "best tool"). Never call a source "blocked"
  until its technique ladder was tried (public JSON → maintained library → real browser on the user's machine → other
  resellers of the same data → mobile API) and ≥3 sources per category were checked. **Wide first, then deep;
  ≥2 angles agree before offering options on a decision-critical fact.** Persist every finding
  with URL + date in `.budgit/research/` — agent reports vanish.
- **Context is the main cost (measured: 69% of a week's spend at >400k context/call).** One topic
  per session; at each phase gate rewrite progress.md, then suggest `/compact <what to keep>` or a
  fresh session. Long outputs go to files (read the tail), never pasted whole. Each agent's model
  comes from the budget-level table below.
- **Subagents are where the limit goes (measured: one session = 318 agents, single agents ran 200–880
  calls at 250–570k context).** One brief = one deliverable (≤1 file or screen, within the level's calls
  per agent); give line ranges / grep targets, never "read app.js"; no twin A/B critics on the same
  artefact. A brief with >4 deliverables or an exhaustive per-row judgement is split into sequential agents
  (build → wire → verify), and every brief states a hard stop ("at 55 calls commit and report what is left")
  — agents in a pilot project ran 91–92 calls on 60-call briefs. Files >1500 lines that agents re-read are a cost bug → split them.
  Check with `python3 <hub>/tools/tokens/tokens.py --agents` when the hub has it.
- **Budget level (automatic; `budgit budget`):** ample · normal · tight · critical, from the user's plan limits'
  pace (weekly % used vs % of the week elapsed, 5-hour window, extra usage) — hooks announce changes, the user's
  word (`budgit budget set <level>` / `auto`) overrides. The level changes HOW you work (subagent count and
  call budget, research depth, checkpoint point, what waits for the reset, what goes to the ChatGPT lane, and
  which MODEL each agent gets: you (opus) are the brain; pass `model=` on EVERY Agent call per the announced
  table — judgement opus/sonnet, work sonnet, mechanical haiku — escalating one step only after the cheaper model
  failed twice),
  NEVER what you do: ledger, phase gates, verification, progress.md checkpoint, saved reports and the user's scope
  stay intact; if the budget cannot cover the user's scope, offer to defer or let the user choose — never shrink it
  silently. At a session start or phase gate, if the snapshot is >6 h old and `get_usage` exists, read
  it and run `budgit budget record` (details: reference/budget.md).
- **Only one model, or working alone:** the model table above names tiers (opus, sonnet, haiku); with a plan that has
  only one model, that model goes in every `model=`, the rules about calls, checks and reports stay the same, and you
  say so once. A project with one session needs nothing else: claims, the inbox and the ledger work the same.
- **Optional skills:** every skill named in a phase or lens is optional; the user may have none of them. If one is not
  installed, do the inline step written beside it and say so once, in one line. Never stop, and never ask the user to
  install anything to continue.
- **No Claude signature on commits or PRs:** the commit hook (`budgit watermark`, installed by `new`/`activate`) removes
  Claude's co-author and "Generated with" lines and adds one `Built-with: Budgit (<public repo>)` trailer. A PR body you
  write ends with that same Built-with line and never "Generated with Claude Code". `budgit watermark off` removes the
  hook for the project and it stays off; the user's own commit-msg hook or `core.hooksPath` is never touched.
- **Is this the best way?** Every gate artefact (spec, plan, QA report) has an `## Alternatives`
  section — ≥2 real options, why each lost, what evidence would flip the choice. A fresh-context critic judges
  it, never the author. Missing or straw-man → the gate does not pass.
- **No claim without evidence:** every factual claim you or an agent reports carries its proof (command +
  output, `file:line`, or URL + date); otherwise say UNVERIFIED. "The same model wrote and checked it" is
  not evidence. Before trusting a number, re-run it.
- **Continuous introspection:** when a `budgit self-check` line appears (same failure 3×, or a team
  rule broken since the last prompt) stop and answer it in one line: what is inefficient or wrong, and either
  fix it now or open a ledger item. Ask the same yourself after any FAIL verdict, any correction from the user, and
  at every gate. Never retry the same failing call a 4th time unchanged. `budgit retro-mine` (retro phase) is the
  same question asked of the user's side: where the user corrected or repeated a point, and what the team had used before.
- **Cheapest capable engine (measured 2026-10-20: main opus sessions = 55 % of spend, haiku 0.4 %,
  `budgit codex` 0 uses):** build sessions (executing a reviewed plan, tests, commits, releases) run on SONNET as
  the main model — switch the session model (desktop `set_session_model`, terminal `/model sonnet`) and tell the user;
  opus is for plan/design/QA judgement. Mechanical agents (run + read logs, probes, digests, formatting) get haiku.
  The ChatGPT lane (if set up; a ChatGPT login, not Claude quota; never secrets/real data): bulk drafts — KB rows, fixtures,
  copy variants — via `budgit codex draft --brief FILE`; `budgit codex review` on the diff BEFORE every opus
  reviewer/verifier round, its report passed to the reviewer as untrusted input (the hook reminds you). The call
  ceiling per agent is enforced by the hook: a wrap-up note at budget−5, hard stop at budget+20.
- **Design gate on anything visual the user will see:** README, SVG/art, UI, slides, mockups, in any phase
  and in hub tasks: load `impeccable` before drawing (if not installed: use lenses/design.md and the rubric); show 2 small rendered directions before a full build; one
  visual language per page; preview as the real host renders it (light + dark + narrow); the user's OK before anything
  public. A correction from the user on a design → re-run impeccable's critique (if not installed: the rubric) before the next round.
- **Ask in rounds:** ≤4 independent decisions per AskUserQuestion, recommended option first.
- **Never shrink the user's requested scope to fit a budget without asking.** Decision artefacts as
  rich as the decision; never ask the user to judge a style on placeholders.
- **Rigor scales:** mini (script/prompt: plan → build → verify → retro) or standard (apps/games: all
  phases). Say which; the user can override. No document theatre.
- Simple and robust. Noreply commits; push when the project's CLAUDE.md says so (default: ask the user first). Read and verify anything before installing it.
- **Knowledge:** Markdown in git is the source of truth; descriptive paths, one concept per file, YAML
  frontmatter, an `INDEX.md` per knowledge folder. No vector DB unless Budgit's benchmark says so.

## Reporting to the user: reply in the user's language
- Decision/result first, then at most 3 bullets; no recap or closer.
- Questions go last, batched and numbered (≤5), each with a recommended default the user can accept with one word.
- End with a state line (`phase n/m · MVP n/m · queue: next items`) and one next step: what you do now, or
  the one thing the user must do, with an estimate in minutes when it is the user's.
- Never ask what the repo or evidence can answer. Say plainly what was NOT verified.

## Phases (gate artefact → the user's approval to continue)
| Phase | Gate artefact | Detail |
|---|---|---|
| 1 Validate | `docs/brief.md` go/kill (+≥1 real-user signal) | phases/1-validate.md |
| 2 Design | `docs/spec.md` with EARS criteria | phases/2-design.md |
| 3 Plan · 4 Build | plan reviewed by `budgit:plan-critic`; ledger items done with evidence | phases/3-plan-build.md |
| 5 QA | `.budgit/verify/R-n.md` PASS by `budgit:verifier`, then `budgit ledger verify` | phases/5-qa.md |
| 6 Launch | `docs/launch.md` | phases/6-launch.md |
| 7 Retro | `.budgit/retro.md` + lessons proposed via the inbox | phases/7-retro.md |

**This process evolves while you work.** At EVERY phase gate re-read this file and
`<hub>/lessons/INDEX.md` (candidates included) and say which version you follow.
