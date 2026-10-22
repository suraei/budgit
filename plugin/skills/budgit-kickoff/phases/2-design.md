# Phase 2 — Design
- `docs/spec.md` with acceptance criteria in EARS form ("WHEN <trigger> THE SYSTEM SHALL <behaviour>")
  — they become the QA done-contract.
- Tag every EARS line with its test layer: `[func]` (executed test), `[perf]` (budget + synthetic data
  size, e.g. "WHEN the list holds 10k rows THE SYSTEM SHALL render in <200 ms" — measured, never assumed),
  `[design]` (UI: screenshots at 3 widths + the zoom viewport — 1280×720 at 80 % browser zoom — vs the approved mock). Each UI item needs ≥1 `[design]`;
  each list/data/import item needs ≥1 `[perf]`.
- `## Alternatives` in the spec for each pillar decision (see ground rule): options, why they lost, what
  would flip it.
- Load the lenses that apply (design, a11y, security, game, motion, data-safety, edge; `lenses/writing.md` for UI copy).
- Pillar decisions (art direction, core interaction) need multi-screen, interactive, animated
  prototypes per option — never a single static vignette. Before asking the user to
  JUDGE a style, check: does the artefact show the real subject at target fidelity (real shapes,
  light, motion)? If not, label it "NOT a style judgement" and do not ask the style question.
  UI: run `impeccable` itself (if not installed: lenses/design.md and the rubric; `design-check detect` stays UNVERIFIED).
- **UI design track** (lens design.md; the project documents its design its own way: any files, any
  format, listed in `.budgit/project.json` → `design.docs`, default `docs/design/*.md`):
  - 2a Direction (opus, the ONE design hand), written down: references (3–5 real products: what to copy, what
    not); register, tokens (colour, type, spacing) + rationale, do/don't; states (empty/loading/error/partial/
    zero-results per chart and table, each reachable by a URL param such as `?state=empty`). `budgit design-check
    docs` checks they are covered. ≤3 options for the user, inside the user's existing family.
  - 2b Mock (sonnet, same tokens): the densest real screen + an empty/error state, real or synthetic data, never
    lorem. Expose `?demo=1&now=<date>` (synthetic data, frozen clock) so screenshots are repeatable.
  - 2c Critique (fresh opus, not the builder): `budgit design-check shots`, open them, score
    reference/design-rubric.md, the three worst defects, one class of defect per round, max 3 rounds, then the user.
  - When the user approves a screen: `budgit design-check approve --user-word "<the user's words>"` (baselines for drift).
- **Art track** (games, illustrated or mascot UIs — lens game.md): research how comparable games organise art,
  write an art bible BEFORE any asset, build one key scene from it, fresh critic scores scenes against it; no
  sprite batch before bible + key scene. Characters = base body + part sets in slots, never edits of a flat comp.
- Never shrink the scope/granularity the user asked for to fit a budget without asking.
- Gate artefact: spec approved by the user, after `budgit:plan-critic` (fresh) reviewed its tags and Alternatives.
  UI projects add: `budgit design-check docs` clean, approved mock, rubric ≥8 (items 1–3 = 1),
  `budgit design-check tokens` clean.
