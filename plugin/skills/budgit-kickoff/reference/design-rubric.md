# "Looks professional" rubric (P2)

Score each item 0 or 1. **pass ≥8, and items 1–3 must be 1.** Scored by a FRESH opus critic on screenshots
(`budgit design-check shots`: 375×812, 768×1024, 1440×900 and the zoom viewport), never by the agent that built the
screen. The user's eyes have the final say.

| # | Item | How it is checked |
|---|---|---|
| 1 | Token fidelity: no literal colour/size/radius/shadow outside the project's tokens | machine: `design-check tokens` exit 0 + `detect` clean |
| 2 | Hierarchy: in grayscale, the primary element and next action are clear in 2 s; ≤3 text sizes per card | critic |
| 3 | States: every chart/table has empty, loading, error, partial, zero-results; no zero rows, dots or grey bars | machine: shots of each state listed in the design docs (URL params) + critic |
| 4 | Type: ≤2 families, hand-picked scale, body ≥14 px, line length ≤~80 ch, numbers tabular and right-aligned | critic |
| 5 | Spacing/alignment: gaps from the scale; edges, baselines, icon centres align; same padding across screens | critic |
| 6 | Colour: one accent, colour only for status and never alone, contrast ≥4.5:1 (3:1 large), same metric = same colour | critic (+ detect contrast) |
| 7 | Density fit: dashboard primary zone dense, secondary quiet; consistent row height; KPI rounded + delta + label | critic |
| 8 | Consistency/drift: untouched screens diff clean vs the user's approved baselines; same component = same look | machine: `design-check diff` exit 0 |
| 9 | Responsive/robust: no overflow or clipping at 3 widths + the zoom viewport; long text, 0 and huge values; focus visible; reduced motion | machine shots + critic |
| 10 | Slop-free: none of the lens's top 10 tells; motion only transform/opacity; "would someone assume AI made this?" = no | detect + critic |

## Critique loop (phase 2c and any UI fix round)
- The critic returns: score per item, total, the top 3 defects with screen + spot, and ONE defect class to fix.
- **one defect class per round**, max 3 rounds (fixing one mismatch can break fine regions elsewhere); after
  round 3 the user decides. Re-shoot and re-score after each round; keep the scores in `.budgit/verify/`.
- A score with no screenshots opened is not a score.
