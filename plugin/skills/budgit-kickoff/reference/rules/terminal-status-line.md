---
id: rule/terminal-status-line
kind: rule
status: draft
enabled: false
applies_to: {surface: [terminal-status-line, host-status-text], host: [terminal-cli, desktop-code-tab, vscode, mobile]}
not_for: [README hero pictures of the line (see rule/svg-light-dark)]
covers_gap: "no installed skill designs or checks a one-line terminal status; the rules live only in tests and user corrections"
registry_entry: rules-registry.json
evidence_base: plugin/scripts/studiolib.py status_line, plugin/bin/budgit _status_line, plugin/hooks/register.jsx, tests/test_brote_status.py, test_cli.py, test_colour_default.py, test_bar_colour_surface.py, test_i18n.py, a status-line colour review
---
# Terminal status line (R-271, rule 3 of 5)

Draft. Wired into nothing. This is the best-evidenced of the five: almost every MUST has a user correction and a test. The rules describe the line as shipped; they do not redesign it.

## MUST (a user correction, and a test that enforces it)

| # | Rule | Check that exists | Source |
|---|---|---|---|
| SL1 | The line starts with the product name, two spaces, and the plain text is at most 80 columns. | `test_brote_status.py` (12 doing, 58 open, stale, 100 % case), `test_cli.py:147,213` | `studiolib.py:364` |
| SL2 | No emoji; only text glyphs (en curso ✿, por hacer ⁘, ideas ✧, ┊, ▰▱). Check by regex over U+1F000-1FAFF and U+FE0F. | `test_brote_status.py` `EMOJI` regex | user, ledger R-132 (21 Oct), R-202 (23 Oct) |
| SL3 | Each segment has exactly one colour; the progress bar is a different colour from the ideas segment (bar sky blue 7cc4ff, ideas lilac c9b6ff). | `test_brote_status.py` pins each hex and "bar is never the ideas lilac" | user, ledger R-206 (23 Oct) |
| SL4 | The coloured line with its escapes removed equals the plain line, character for character; every coloured segment ends with a reset. | `test_colour_default.py:46`, `test_brote_status.py:134-141, 240-243` | `status_line` docstring |
| SL5 | Colour is emitted only on the terminal surface. Desktop, VS Code, mobile and any unknown surface get `--colour off`, and any escape that still arrives is stripped. | `test_bar_colour_surface.py` (5 surfaces plus terminal) | `register.jsx:48-58`; research 23 Oct (host docs: colour on desktop is "Unknown") |
| SL6 | The CLI alone prints plain text; colour needs `on` or `auto` with a saved setting not "off"; `budgit colour off` is the escape hatch; a garbled saved value counts as on. | `test_colour_default.py:29-75` | R-206 |
| SL7 | Outside a project show the account part only (a dim "no ledger here" head and the week bar), never an empty line; any error prints nothing and exits 0. | `test_cli.py:214-219`, `test_budget.py:310` | user, ledger R-238 |
| SL8 | Every visible word comes from the i18n catalog; English and Spanish have identical keys and placeholders; the default language is English; approved Spanish phrases are pinned. | `test_i18n.py:22-35, 205-222` | user, ledger R-118, R-204 ("en curso", "por hacer") |
| SL9 | The default line is exactly three counts, a 6-cell bar with the percent used, and one forecast tail ("fin <day> <hh>h" or "llegas"); no tail and no bar without live weekly data or too early to project. | `test_cli.py:195-201`, `test_budget.py:356` | a user pick (R-206); R-202, R-203, R-205 |
| SL10 | The stalled count and the 5-hour window stay out of the default line; they appear with `--extras` (the 5-hour part only at 70 % used or more). | `test_cli.py:203-212`, `test_brote_status.py` | user, ledger R-209 |

## SHOULD or OPEN (a gap, not a rule)

| # | Item | Status |
|---|---|---|
| SL11 | Honour `NO_COLOR`. | Nothing in `plugin/` or `tests/` mentions it; only `budgit colour off` and the surface gate exist. Needs a decision before it is a rule. |
| SL12 | The 80-column limit is measured in display cells, not code points, and in English as well as Spanish. | The tests use `len()` on the Spanish line; the glyphs U+273F, U+2058, U+2727, U+250A, U+25B0/1 may be wide in some fonts; "ends Wed 23h" is longer than "fin mie 23h" and is untested. |
| SL13 | Distinct colours by a measured distance, not by pinned hex values. | The test pins this palette only. |
| SL14 | A light-terminal variant. | The real ANSI pastels are about 1.75-2.2:1 on white (an agent's computation, not in the repo) and no variant exists. The README hero uses darker inks (`mk_preview.py:23-26`) that are still below 4.5 for several segments. |

## How it is judged

The 10 MUSTs already have tests; the win test of the R-258 review (proposed, not a user statement) applies only to SL11-SL14: a candidate rule is kept if it fixes a real user report. SL5's host claim (desktop prints escapes as text) is recorded only in a test docstring, with no source link in the repo.

## Not verified

The tests were read by an agent, not run. The host colour behaviour of the desktop Code tab is "Unknown" in the official docs read on 23 Oct.
