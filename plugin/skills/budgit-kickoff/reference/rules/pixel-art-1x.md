---
id: rule/pixel-art-1x
kind: rule
status: draft
enabled: false
applies_to: {surface: [pixel-sprite, pixel-prop, pixel-icon, pixel-scene], host: [desktop-window, terminal-half-block, readme-svg, game]}
not_for: [vector UI, photos, web layout]
covers_gap: "no installed skill checks pixel art at its real size (r258 flow plan 3.2; research B found none)"
registry_entry: rules-registry.json
evidence_base: lessons/L-015, lessons/L-010, tools/brote/audit-sprites.mjs, tools/brote/audit-book.mjs, tools/brote/compare-frames.mjs, user corrections R-213 and I-123/I-126/I-128
---
# Pixel art at 1x (R-271, rule 1 of 5)

Draft. Wired into nothing. The selector attaches it when a job has `surface=pixel-*` and no skill has fit 2 (flow plan 3.2). Two tiers: **MUST** = a user correction or an existing audit enforces it; **SHOULD** = proposal, no evidence of a user asking and no script behind it yet.

## MUST (grounded)

| # | Rule | Check that exists | Source |
|---|---|---|---|
| PX1 | One art-pixel unit for every layer; screen scale is a whole number (Brote desktop 3 pt per art px, cards 4, zoom 8). Never a fractional scale. | `audit-sprites.mjs` lines 39-45: canvas is an integer multiple of 34x28, `image-rendering:pixelated`, even grid height | L-015 rule 1; user corrections #9, #15, #16 (a game project, 13 and 21 Oct) |
| PX2 | Write an object, real size, art px table before drawing; sizes come from the table, not from eye. Brote: body 18x15, canvas 34x28, signal bubbles 11x11. | partial: `audit-sprites.mjs` derives the body size; no check of prop sizes against a table | L-015 rule 2; r114 plan lines 32-34; catalog R-213 |
| PX3 | Nothing is cut by the canvas except particles (confetti, sparkles, stars). | `audit-sprites.mjs` (clipped body and accessory pixels fail, exit 1); `audit-book.mjs` | L-010, L-015 rule 3 |
| PX4 | Support rule: a grounded body rests on the shadow row; nothing below it; a hop shrinks the shadow; nothing floats unless airborne. | `audit-sprites.mjs` support rule; `audit-book.mjs` for the book | L-015 rule 3; user correction #16 on physics |
| PX5 | No holes in the silhouette; one outline colour, saturation at least 0.12, different from the body. | both audits | r114 plan decision 4 |
| PX6 | Every palette key resolves in every state. | both audits (`badKeys`) | audit header |
| PX7 | Parity: the shipped module draws the same frames as the approved mock. | `compare-frames.mjs` (any diff exits 1) | L-010 part 4, L-008 |
| PX8 | **Look at 1x before the user sees it**: render a 1x strip next to a 4x zoom and run one fresh critique pass; the report carries a one-line critique note. A judgement of "looks pixel" from zoomed screenshots alone is not a pass. | **none**: manual today (strip and zoom lived in a scratchpad `sheet.mjs`) | user corrections via coordinator; L-015 rule 4; R-213 review "not readable at real size" |
| PX9 | Judge a mock at the size the host draws it; a zoom is only a help. | none | I-123, I-126 (a book sprite that did not read at real size) |
| PX10 | Each distinct signal (question, permission, done, money, clock) has its own silhouette and colour; information is never only colour; no two signals share a shape. | none (catalog per-entry pass only) | catalog R-213 point 1; r114 plan line 163 |
| PX11 | Verify on every real host surface at its measured size; an unobserved surface is "needs the user's hand test", never PASS. | partial: kit test of the 15-row band | L-010 part 4-5; Brote R-114/R-148 |

## SHOULD (proposed; do not state as user rules)

| # | Rule | Why only SHOULD |
|---|---|---|
| PX12 | Features (eyes, mouth, small parts) at least 2 art px; cheeks never touch eye or heart-eye pixels. | The "2 px" number appears only as a proposal (flow plan line 64, D4). The cheek rule has shipped code (`sprites.js` line 134) and user, ledger I-123 text; the 2 px has no source. |
| PX13 | The silhouette stays visible on a light, a dark and a busy background. | One real case (pale steam vanished on the light stage, catalog line 111); no test. |

## How it is judged (the win test)

The test is the one proposed in the R-258 review page (my criterion, not a user statement; the user chose all five rules for 0.13.1): a rule is kept only if, in a real task, a sprite made with these rules plus a fresh critic at 1x beats the baseline "project rules + critic at 1x" on the brief's checks, and the user does not correct the style again. Until then the status stays `draft`.

## What is missing to make this checkable (for core-build; proposed, not built)

1. A script that writes the 1x strip beside the 4x zoom for any sprite sheet (PX8), so the step stops depending on a scratchpad file.
2. Prop sizes read from a table and compared in the audit (PX2).
3. A background-contrast pass on light, dark and busy backgrounds (PX13).

## Not verified

The audits were read by an agent (`ev-pixel-frames.md`) and not run. `tools/brote/float/director.test.mjs` and the Swift selftest were not read.
