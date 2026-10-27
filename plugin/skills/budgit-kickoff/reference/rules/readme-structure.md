---
id: rule/readme-structure
kind: rule
status: draft
enabled: false
applies_to: {surface: [readme, wiki-page, getting-started, faq], host: [github-readme, github-wiki]}
not_for: [marketing landing pages (taste-v2, frontend-design), prose quality (rule/humanize, stop-slop)]
covers_gap: "no installed skill structures a README or wiki page; the rules came from the R-250 research and user corrections"
registry_entry: rules-registry.json
evidence_base: a prior-art review of README and wiki structure (12 rules), a docs audit, share/README.md, tests/test_export_public.py, lessons/L-009, user, ledger R-250, R-218, R-132, R-141, R-157, R-243, R-245
---
# README and wiki structure (R-271, rule 4 of 5)

Draft. Wired into nothing. R-250's own lane owns the shipped README and wiki; this file turns what that lane learned into a rule the selector can attach, and does not change those files. Prose quality is out of scope here (the humanize rules, R-267, cover it).

## MUST (a user correction, or a test enforces it)

| # | Rule | Check that exists | Source |
|---|---|---|---|
| RM1 | The README is for the user and sells the tool; leave out what a user does not care about (for example, the commit-signature paragraph). | `test_export_public.py::test_readme_does_not_talk_about_the_commit_signature` | user, ledger R-218 (23 Oct) |
| RM2 | Text is organised in sections with design; a wall of unformatted text without images is a defect. Take the Brote page as the model: image first, one or two sentences, then a table, list or code block; section titles that answer a question; one code block per thing to copy; a closing link to the deeper page. | none | user, ledger R-250 (25 Oct) |
| RM3 | Install comes first and is the shortest path; the other install methods fold; troubleshooting goes at the end or on its own page, never between steps. | `test_readme_has_required_sections` (headings only) | R-243/R-245 (friends could not install); audit line 30 |
| RM4 | Required README headings: Install, Your first run, What you get, Brote, How a project moves, Guards you will meet, Commands, Privacy, Learn more, plus the strings "**Platforms.**" and "MIT licensed. See [LICENSE](LICENSE)." | `test_readme_has_required_sections`, `test_readme_platform_and_float`, `test_readme_language_note` | R-250 layout |
| RM5 | Every number of two or more digits in README prose has a source row in `docs/design/2026-10-22-readme-facts.md`. | `test_every_number_in_readme_has_a_fact_row` | user, ledger R-157 |
| RM6 | README is English, with no emoji and no private data; the export leak gate and gitleaks pass. | `test_readme_english_and_clean`, `budgit export` leak gate | user, ledger R-118 |
| RM7 | README art is SVG in `share/` (the export copies only `share/*.svg`); wiki images live in `docs/wiki/images/` and are linked through `raw.githubusercontent.com/wiki/{repo}/images/...`. | `test_export_public.py::PublicExportBase` | `export.py` |
| RM8 | Every image has alt text that says what the picture says, and is rewritten when the picture changes. | none (R-258 D4 proposes a manual rubric item) | the user's acceptance of the I-124 fix for four alt texts that did not match |
| RM9 | Preview the README with GitHub's own CSS, in light and dark, at the real column (about 836 px) and at 375 px, and the user approves the preview before anything public. | none automated; mock builder `docs/design/r250/mk_mock.py` | lessons/L-009 (user, ledger R-52 rounds); R-141 |
| RM10 | Swap a README visual only on the coordinator's word that the user approved it, never on another lane's word. | none | docs lane note after the GIF revert (e9da761); user rejections, ledger R-262/R-264 |

## SHOULD (pattern-copying from 8 admired READMEs, not measured; no reader-outcome evidence)

| # | Rule | Note |
|---|---|---|
| RM11 | First screen: name, a pitch of at most 25 words, one visual, one nav line of at most 5 links. | The shipped README has two paragraphs for the pitch. |
| RM12 | At most 3 install command lines above the fold; other methods in `<details>`. | Shipped: one code line, then three `<details>`. |
| RM13 | Numbered steps, one action and one code block each, then the "it worked" check. | |
| RM14 | No paragraph longer than 4 lines; longer text becomes a list, table or sub-page. | The principle comes from the user (ledger R-250); the number 4 is the research doc's. |
| RM15 | Choices (install method, mode, platform) are a table with a "when to use" column. | |
| RM16 | At most 2 alerts per page, only for must-know items, one meaning per type, the tag alone on its line. | The only limit that is GitHub's own guidance. Whether alerts render in wikis is unverified, so the wiki uses bold-led blockquotes. |
| RM17 | Every `##` of a wiki page over about 80 lines is reachable from a contents line; `_Sidebar.md` has 3-5 bold groups of at most 7 links, Troubleshooting and FAQ last; FAQ grouped as What is / How do I / Common problems, each question a heading. | Three wiki pages are over 80 lines today (166, 95, 117). No wiki tests exist. |
| RM18 | Authoring sizes: every picture authored 960 wide and shown at `width="100%"`; one pixel is 4 units; collapsibles are `<summary><h3><img icon align="absmiddle"> label</h3></summary>`, icons 40 native shown at 20 px. | The docs lane's choice, not a user rule; the lane's notes say 28 px, the generator and README say 20. Use the README. |
| RM19 | GIF budget: at most 3 GIFs in the README, each 2 MB or less, 960 px wide, 6 s or less, 12-15 fps, 64 colours or fewer; record in a demo project with no personal data. | Set by the docs lane, not by the user; no tool measures it; the only GIF built was reverted. |

## Not a rule

"README at most 150 lines" (research rule 11): the shipped README is 301 lines and the user approved it (ledger I-124). Do not apply it unless the user asks.

## Open

Whether `<picture>` and alerts render in GitHub wiki pages (needs a throwaway wiki page and a look). Whether alt text or inner `title` is what GitHub exposes for an SVG used as `<img>` (an agent's inference, untested). No automated check covers RM2, RM3, RM8 or any wiki structure rule.

## How it is judged

Per the R-258 review's proposed test (not a user statement): a page structured with these rules plus a fresh critic on the rendered mock at 836 px and 375 px beats the baseline "project rules + critic at 1x", and the user does not report confusing text again.

## Not verified

The README tests and the research were read by an agent, not run. The docs lane's gates (leak check, gitleaks, `claude plugin validate --strict`) are described in its notes, not re-run here.
