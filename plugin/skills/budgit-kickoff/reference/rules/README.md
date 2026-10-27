# Budgit-owned rules (R-271)

Five draft rules for 0.13.1, the user's choice of all five (ledger I-148), plus a sixth approved after reading break-ui (ledger I-154/I-155). Registry kind `rule`, `status: draft`, `enabled: false`, wired into nothing. The selector (plan section 15) may attach one to a job when no installed skill fits; nothing reads them yet.

| File | Rule | MUST | SHOULD / open |
|---|---|---|---|
| [pixel-art-1x.md](pixel-art-1x.md) | Pixel art checked at 1x | PX1-PX11 | PX12-PX13 |
| [frame-timing.md](frame-timing.md) | Frame-animation timing | FT1-FT10 | FT11-FT13, 3 conflicts |
| [terminal-status-line.md](terminal-status-line.md) | One-line terminal status | SL1-SL10 | SL11-SL14 |
| [readme-structure.md](readme-structure.md) | README and wiki structure | RM1-RM10 | RM11-RM19 |
| [svg-light-dark.md](svg-light-dark.md) | SVG in light and dark | SV1-SV8 | SV9-SV10 |
| [odd-data-probes.md](odd-data-probes.md) | Odd data on any host (Spanish names, accents, glyphs, empty and huge lists) | OD1-OD7 | OD8-OD13 |

Registry rows: [rules-registry.json](rules-registry.json).

## How to read the tiers

- **MUST**: a user correction, or an existing test or audit enforces it. The row names the check and the source.
- **SHOULD / open**: research or inference only, or a gap. Never state these as user rules.
- The win test ("a rule is kept only if it beats project rules + a critic at 1x in a real task") is the criterion proposed in the R-258 review. It is not a user statement.

## Not verified

Nothing here was run. Tests and audits were read by agents. Cross-lane flags (Brote ambient and fidget numbers, README art contrast, `NO_COLOR`, the 150-line rule) are reported, not changed.
