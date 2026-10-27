# Layer 2, English: word hygiene

<!-- humanizelint: off (this page quotes the patterns it forbids) -->

Run after layer 1. Style only: the IdeaLens study (arXiv 2610.06778) tests no word list, so none of this is claimed to matter to a
detector. It matters because these phrases tell the reader nothing. Source list: `stop-slop` (MIT, Hardik Pandya, pin 8da1f03)
and `lenses/writing.md`, reduced to the rules that do not clash with layer 1 or with UI copy.

## Kept (lint ids W1-W6)
| Id | What | Example | Fix |
|----|------|---------|-----|
| W1 | Throat-clearing openers | "Here's the thing:", "The truth is" | State the point |
| W2 | Intensifiers and emphasis crutches (a named list, not all adverbs) | "really", "literally", "at its core", "when it comes to" | Delete |
| W3 | Business jargon | "game-changer", "deep dive", "circle back", "delve", "landscape" | The plain word |
| W4 | "Not X, it's Y" contrast | "It's not a tool, it's a teammate." | Say Y |
| W5 | Meta-commentary | "In this section, we'll…" | Let the text move |
| W6 | Em dash | "fast — very fast" | Comma or full stop |

Judgement-only, no lint: forced triads (three items because three sounds complete); a closing one-liner that restates the
paragraph; quotable sayings; staccato fragments; naming the actor when a thing is given a human verb ("the decision emerges").

## Dropped from `stop-slop`, and why
| stop-slop rule | Why it is not here |
|----------------|--------------------|
| "Kill all adverbs" | Too blunt: "quickly" and "only" carry meaning. W2 keeps a named list of empty intensifiers. |
| "Kill all hedges, skip softening" | Clashes with H8, which wants the condition stated ("holds for vaults under 50k notes"). Vague hedging is still out; a stated condition is required. |
| No passive voice | Clashes with `design:ux-copy`'s own error pattern ("Your card was declined by your bank."). Keep passive only where the actor matters and is missing. |
| No sentence starting with What/When/Why/How | Arbitrary, and UI copy labels ("What happened", "How to fix") use them. |
| "Be specific" on its own | Without a source this pushes a model to invent detail. Replaced by H1-H4, which say where specifics come from. |
| "Rate 1-10 on five dimensions" | A self-score invites a model to grade its own homework. The lint and a reader replace it. |

## Where it does not apply
Legal and security wording; text the user wrote in their own voice (their sample wins); code, paths, commands; button and error
patterns, which belong to `design:ux-copy`.
