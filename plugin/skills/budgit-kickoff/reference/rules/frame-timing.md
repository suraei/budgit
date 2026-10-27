---
id: rule/frame-timing
kind: rule
status: draft
enabled: false
applies_to: {surface: [sprite-animation, frame-sequence, readme-animated-svg], host: [desktop-window, terminal, readme-svg]}
not_for: [CSS or web UI motion (use emil-design-eng and impeccable animate)]
covers_gap: "no installed skill converts motion rules into frame counts and hold times; the r258 flow plan 3.3 says a person copies numbers into a table"
registry_entry: rules-registry.json
evidence_base: tools/brote/float/director.js, plugin sprites.js, personality.js, tools/brote/behaviour-check.mjs, perf-frame.mjs, the Brote personality catalog and three research notes on character animation
---
# Frame-animation timing (R-271, rule 2 of 5)

Draft. Wired into nothing. Do not copy the web rule "motion over 300 ms needs a written reason" (`lenses/motion.md`) to sprites: clips here run 1 to 6 s by design.

## MUST (grounded in shipped code, a test, or a user correction)

| # | Rule | Check that exists | Source |
|---|---|---|---|
| FT1 | The base tick is 100 ms (10 fps). A pose never changes faster than one tick; slowness comes from holds, not from a faster clock. | none for the rate; `perf-frame.mjs` checks cost per frame (median under 8 ms, 240 frames) | `director.js` lines 2, 13; research c line 10 |
| FT2 | A mood or palette change blends over exactly 10 ticks (1.0 s), once, never looped. | `behaviour-check.mjs` lines 14-21 | `sprites.js` line 184; r114 plan line 146 |
| FT3 | Every reaction has a written length in ticks (`ACTLEN`: hello 16, poke 16, grumpy 30, celebrate 32, land 5, dizzy 26, wake 28). A new reaction adds its row before it is drawn. | `audit-sprites.mjs` composes every frame of each act for grid and support; it does not check the length | `sprites.js` line 156 |
| FT4 | Pacing is data: fidget gap 150-300 ticks (15-30 s), `WORK_MIN` 80, `CALM_GAP` 60, ambient at most 600 ticks on Vivo and 1,200 on Sutil, none on Quieto. | `director.test.mjs` (pacing), no gate in the audits | `director.js` lines 48-49; window-physics plan line 44 |
| FT5 | Loops are declared ranges `[from, to)` in ticks, with a quick entry and a quick exit (work loops 32-64 ticks, call loops 72-120). | `director.test.mjs` asserts clips play | `director.js` lines 30-36, 319-321 |
| FT6 | Reduced motion: `dy`, `stretch` and feet are zeroed, free-time animation is off, calls become a fixed pose, and `/brote still` freezes and resumes. | `director.test.mjs` lines 159-162; `behaviour-check.mjs` lines 23-40 | `director.js` 78, 145, 318, 404; `brote.swift` (reads the macOS setting) |
| FT7 | A clip starts and ends at the same body offset, or it is a walking clip whose shift moves the window instead. | none in the repo (an ad hoc `drift.mjs` in a scratchpad) | animation-wiring plan line 8; `director.js` line 392 |
| FT8 | A sign that matters is held long enough to read: call poses at least 24 ticks fixed (2.4 s); attention steps at 0, 2 min and 10 min; at most 20 nudges per day. | `director.test.mjs` covers the ladder | `director.js` lines 52, 65-66, 318-328 |
| FT9 | Verify an animated screen with at least two differing frames showing the subject; one still frame is not a pass. | none generic (`compare-frames.mjs` compares every frame in Brote) | lessons/inbox/2026-10-21-a game project-agent-test-rewrite.md (not yet a lesson) |
| FT10 | Timings are measured from the running app against the table, not by eye. | **none**: no table and no script yet (flow plan 3.3 last row) | r258 flow plan 3.3 |

## SHOULD (proposed; no measurement behind them)

| # | Rule | Why only SHOULD |
|---|---|---|
| FT11 | Attention cues alternate no faster than 2 Hz (period at least 500 ms) and never above 3 flashes per second (WCAG 2.3.1). | The catalog states the limit and the call tick (3 ticks on, 3 off, 1.67 Hz) respects it, but nothing measures it, and some small sprites alternate every 1-2 ticks (5 Hz: sparkles, held-pose arms and feet, celebrate arms) with no written exception. |
| FT12 | Idle 2-4 frames at about 6 fps, walk 4-6 frames at about 8 fps, pixel standard 8-12 fps. | Tutorial advice in `research c` lines 50-55, marked "no measurement". |
| FT13 | Redraw only when the frame changed; rest mode at 5 fps while the picture is unchanged. | A plan (`brote-idle-cpu.md`); parts may not be built; idle CPU 2.25 % against a 1 % budget at the time. |

## Conflicts the Brote lane must settle before any number becomes a hard rule

1. Ambient rate: the catalog says Vivo is one thing per 20 s; `director.js` line 48 gives 600 ticks (60 s). The window-physics plan agrees with the code.
2. Fidget gap: the plugin `Pet` uses 5-11 s (`sprites.js` line 186); the director re-spaces to 15-30 s (line 49) and overrides it in the float app.
3. FT11: whether small-area 5 Hz alternations need a written exception.

## The table this rule asks every project to write (before drawing)

| Clip | Frames | Hold per frame (ticks) | Loop `[from, to)` | Total (ticks and s) | Reduced-motion form |
|---|---|---|---|---|---|

One row per clip, in the project's design doc. Brote's closest existing source is `ACTLEN` and `FIDGETS`; a game project has none. The final check compares the running app to this table (FT10).

## How it is judged

Same win test as the other four (proposed in the R-258 review, not a user statement): a clip built with this table, plus a fresh critic watching a GIF at the real size and fps, beats the baseline "project rules + critic at 1x" on the brief's checks, with no user correction afterwards.

## Not verified

Nothing here was run. `director.test.mjs` beyond lines 159-162, the Swift selftest and `scenario.py` were not read. The 1.67 Hz figure is derived from `alt(ft,3)` and is right only while the call sprite stays on the same tick base.
