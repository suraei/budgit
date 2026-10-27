---
id: rule/svg-light-dark
kind: rule
status: draft
enabled: false
applies_to: {surface: [svg-diagram, svg-art, readme-picture, animated-svg], host: [github-readme, github-wiki, claude-page]}
not_for: [pixel sprites (rule/pixel-art-1x), choosing a diagram type (diagram-design)]
covers_gap: "diagram-design skins one theme at a time; nothing enforces a light and dark pair, contrast, or alt text on shipped README art"
registry_entry: rules-registry.json
evidence_base: share/README.md, share/*.svg (12 light/dark pairs), tools/readme/mk_docs_art.py, mk_preview.py, mk_first_run.py, mk_bank.py, the skills-flow diagrams and the generators used for them, lessons/L-009, L-015
---
# SVG that works in light and dark (R-271, rule 5 of 5)

Draft. Wired into nothing. Complements `diagram-design` (type choice, geometry); this rule only covers delivery, palette, contrast, text alternatives and preview.

## MUST (shipped pattern the user approved, or a user correction)

| # | Rule | Check that exists | Source |
|---|---|---|---|
| SV1 | Ship each README picture as a pair `name-light.svg` and `name-dark.svg`, delivered with `<picture><source media="(prefers-color-scheme: dark)" srcset="..-dark.svg"><img src="..-light.svg" width="100%" alt="..."></picture>`. Two files, not one SVG with `@media`. | `test_export.py:72` asserts the hero light path only; no test that every `-light` has a `-dark` twin | `share/README.md` (12 pairs); approved at I-124 |
| SV2 | A single-file SVG (animated, or any wiki image) draws its own opaque background card and never relies on the page colour. | none | `mk_docs_art.py frame()`, `mk_first_run.py`, `mk_bank.py:332` |
| SV3 | One palette dict per theme, reused everywhere; no literal colours in drawing code (light: bg #fffaf3, card #fffdf8, ink #3a2f3d, muted #6f6076, lilac #7d68c4, green #4f9672; dark: bg #26222d, card #2a2531, ink #efe8f3, muted #aa9cb4, lilac #b7a8e0, green #8fcfae). | none for docs art | `mk_docs_art.py:15-20` (`SKINS`), `mk_preview.py:15-22` |
| SV4 | Authored 960 wide, shown at `width="100%"`, pixel = 4 units, `shape-rendering="crispEdges"` on pixel groups, so the pixel scale matches across pictures. | none for docs art (sprites: `audit-sprites.mjs`) | `mk_docs_art.py` docstring; L-015 |
| SV5 | Fonts that exist on the reader's machine only (`ui-rounded, 'SF Pro Rounded', 'Segoe UI', system-ui` and `ui-monospace, 'SF Mono', Menlo, Consolas`); no web fonts, no external requests, no scripts; leave slack in text boxes because widths vary by platform. | none | `mk_docs_art.py:22-23`, `mk_preview.py:11-12` |
| SV6 | Alt text says what the picture says, including its numbers and labels, and is rewritten when the picture changes; decorative icons use `alt=""` and `aria-hidden="true"`. | none (R-258 D4 proposes a manual rubric item) | I-124 (four mismatched alt texts fixed); `mk_docs_art.py summary_icon()` |
| SV7 | Every animation respects `prefers-reduced-motion` and freezes on its last meaningful frame; classes and keyframes are namespaced per panel when several animations share one file. | none | `mk_first_run.py:140`, `mk_bank.py:328`, `mk_docs_art.py panel()` |
| SV8 | Preview every SVG as an `<img>` at the README column (about 836 px) and at 375 px, in light and dark, before the user sees it; one visual language per page. | manual | lessons/L-009 (user, ledger R-52 rounds) |

## SHOULD (a sound standard, not yet enforced in the shipped generators)

| # | Rule | Finding |
|---|---|---|
| SV9 | Text reaches 4.5:1 against its own background, and strokes or fills that carry meaning 3:1, in both themes, asserted in the generator so a bad colour fails the build. | The only implementation is the scratch `make_flow.py` (`ratio()` and nine text pairs). The shipped generators have no assertion. An agent's computation (the same formula) found the shipped light lilac at 4.44 on the card and 4.35 on the page, under 4.5 for the 15 px command text in `steps()`; the README hero's darker inks give 4.27, 4.46 and 3.72 on the terminal chip. Decorative card borders (1.37) are below 3 and are decoration. |
| SV10 | Informative SVGs carry `role="img"` and a text alternative: `aria-labelledby` to `<title>` and `<desc>` for diagrams; a long `aria-label` for README art. | Patterns differ across the generators. GitHub shows the `<img alt>` and, an agent infers, not the inner title; untested. The README `alt` is the effective text. |

## Open

1. `<picture>` in GitHub wiki pages is unverified, so the wiki keeps light-only images on a card (SV2). A throwaway wiki test page would settle it.
2. `tools/readme/check_art.py` is named in `mk_preview.py` and L-015 but does not exist. The real checkers are `audit-sprites.mjs` and `audit-book.mjs` for sprites; docs art has none.
3. The contrast near-miss in SV9 belongs to the docs lane; this rule reports it, it does not change the art.

## How it is judged

Per the R-258 review's proposed test (not a user statement): a picture made with these rules and checked by a fresh critic at column width in both themes beats the baseline "project rules + critic at 1x"; and SV9's assertion is added to a shipped generator before SV9 is called a rule.

## Not verified

Contrast figures are an agent's computation, not repo evidence. The SVG files and generators were read, not rendered by me.
