---
id: rule/odd-data-probes
kind: rule
status: draft
enabled: false
applies_to: {surface: [any-screen-that-renders-data, status-line, native-panel, pixel-game-ui, cli-output, web-ui], host: [web, swiftui, appkit, terminal-cli, pixel-font-game, readme-svg]}
not_for: [pure visual styling, animation (rule/frame-timing)]
covers_gap: "break-ui is web-only and writes toggle code into the project; nothing checks a native, terminal or pixel-font screen against odd data (long compound names, Spanish surnames, accents, empty and huge lists)"
registry_entry: rules-registry.json
evidence_base: a third-party break-ui skill review (its SKILL.md and CATALOG.md), tests/test_brote_feed2.py (ShortTitleTest), tests/test_brote_status.py, tests/test_cli.py, lessons/L-017, a user report about screens that only work on tidy data (relayed second-hand), and the user's approval to write this rule (ledger I-154/I-155)
---
# Odd data on any host (R-258 P6 follow-up, rule 6)

Draft. Wired into nothing. The method comes from `emilkowalski/break-ui` (MIT, pin 2d22863; read, not run, nothing copied). Its fixes are CSS and its toggle is web-only; this rule keeps the method and the data catalog and replaces the "how to look" column per host. Report first; fixing is a separate step.

## MUST (a user report, or a test in this repo already enforces it)

| # | Rule | Check that exists | Source |
|---|---|---|---|
| OD1 | A screen is not checked on tidy sample data alone. Before "done", it also runs on one odd dataset (OD6) and on Empty, One and Huge (OD4). A pass on the tidy data is reported as "tidy data only". | none generic; the status line has the pattern in tests | a user report of tidy-looking screens that break on real data (relayed second-hand; the exact cases are not in the repo) |
| OD2 | Odd data enters at the data boundary (fixture, mock, flag, test double), never by editing the drawn text or markup by hand. | none | break-ui rule 2 (SKILL.md:32) |
| OD3 | Every rendered value is listed in a field table (where it shows, source, type, limit or "unbounded", optional?). The list's own length is a value. A screen with an unlisted value is not checked. | none | break-ui SKILL.md:42-56 |
| OD4 | Four counts are always tried: 0, 1, a page boundary and page+1, and 1,000 or more. | `test_brote_status.py` (12 doing, 58 open, 100 % case), `test_cli.py` | existing status-line tests |
| OD5 | A long title never breaks inside a word; it is cut at a word boundary with one ellipsis, has a fixed maximum, and a title of exactly the maximum is left alone. | `test_brote_feed2.py` (`test_caps_at_46_at_a_word_boundary_with_an_ellipsis`, `test_exactly_46_is_left_alone`) | R-153 |
| OD6 | The odd dataset mixes the failures across rows (row 1 a very long name, row 2 one letter, row 3 missing, row 4 the same name as row 1), not one bad value per run. | none | break-ui rule 3 (SKILL.md:33) |
| OD7 | Numbers, amounts and dates are never truncated; text gives way first. A number that does not fit is a defect, reported as such. | none | break-ui SKILL.md:117-125 |

## SHOULD (the method of a third-party skill, not yet shown to win here)

| # | Rule | Note |
|---|---|---|
| OD8 | The worst value is plausible, or is the real limit of the schema, database or API. If no limit is found, that is a finding: "unbounded". Where the form allows 50 and the column 255, report the mismatch. | break-ui rule 1 (SKILL.md:31, 54). |
| OD9 | Probe set (host-neutral): names (OD10), emails and ids, labels and badges (one very long word, 12 tags, a pasted 2,000-character paragraph, a newline inside a label), numbers and money (0, 1, 1284, 1e6, negative, 0.1+0.2, 142 %, null, NaN, locale separators, a value that changes width live such as 99 to 100), dates (future, "0 seconds ago"), states (partial data, error, every enum value), collections (OD4, identical items, one item ten times longer than the rest). | Adapted from break-ui CATALOG.md categories; the categories not listed (images, environment) are web-only. |
| OD10 | Spanish and French names: compound given names ("María del Carmen", "Ma José"), double surnames with particles ("Pérez-Villalobos y Gómez de la Torre"), apostrophes ("O'Brien"), a name with only a surname, a one-letter name, all caps, a name that starts with a lower-case particle ("de la Vega"). Initials come from the first letter of the first and last word, not the first two letters. | Added by this rule; the break-ui catalog has none. Spanish text runs about 15-30 % longer than English (rule of thumb, unsourced). |
| OD11 | Glyph coverage: every character of this set draws as itself, with no empty box, no fallback font and no clipping, at the real size and in every font the host uses: `áéíóúü ñ ÁÉÍÓÚÜ Ñ ¿ ¡ « » € ç œ … – —` and an emoji. A pixel font or bitmap sprite font must have each glyph in its sheet, or the missing ones are listed and replaced by the fallback on purpose. | The status line already avoids emoji and tests Spanish phrases; no test covers the set. Pixel fonts are the biggest hole in the break-ui catalog. |
| OD12 | Width is display width, not code points: the glyphs may be wide or narrow, so line length (status line 80 columns, a fixed pixel grid, a bubble) is measured in cells or pixels, and long text wraps, clips with a mark or shrinks by a stated rule. | The status-line tests use `len()` (see rule/terminal-status-line SL12). |
| OD13 | The report is a short table: Broken, Ugly, Fragile, with one fix each, then "what held up", then decisions that belong to the user (for example truncate or wrap). A sturdy screen gets a short good result. The odd dataset stays as a regression test. | break-ui SKILL.md:127-162. |

## How to look, per host

| Host | Where odd data goes in | How to look |
|---|---|---|
| Web | a fixture or mock at the data boundary, a `?data=` flag | screenshot at the real widths, zoom 200 %, `Intl` for plurals and initials |
| SwiftUI / AppKit | a preview with the odd model, a launch argument or env var in debug only | `lineLimit`, `minimumScaleFactor`, `layoutPriority`, Dynamic Type; a real 1x screenshot of the real window |
| Terminal / CLI | a fixture ledger or `--demo` input file | display-width count, ANSI stripped; wide glyphs and emoji |
| Pixel-font game or sprite text | a test string table | render at 1x and 4x side by side (rule/pixel-art-1x PX8); check each glyph exists |
| README / SVG | the generator's input dict | render light and dark at 836 px and 375 px (rule/svg-light-dark SV8) |

## How it is judged

Same win test as the other rules (proposed in the R-258 review, not a user statement): kept only if a screen checked with this rule finds a defect that "project rules + critic at 1x" missed, or the same pass with fewer calls, and the user does not report the same kind of "false data" again. Until then `draft`.

## Not verified

break-ui was read, not run, and nothing from it is copied. Its catalog and fixes are not checked against Spanish or native hosts. I do not know which concrete case produced the user's report about screens that only work on tidy data: the report came second-hand. The 15-30 % Spanish length figure and the glyph set are my additions, unsourced. Existing tests were read by an agent, not run.
