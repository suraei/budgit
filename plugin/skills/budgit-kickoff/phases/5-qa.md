# Phase 5 — QA (separate skeptical evaluator, never self-grade)
- Fresh state is enforced: cache-clearing / worker-restart / fresh-fetch commands go in
  `.budgit/project.json` → `verify.prepare`. They run as shell, so the user approves them: show the user the list,
  then `budgit prepare --approve` (a guard asks the user). Never approve on the user's behalf.
- Before each verifier round on code: `budgit codex review` (cheap first pass, the ChatGPT lane, if set up), fix what it finds,
  pass the report path to the verifier. Round cap: 3 verifier rounds per item, then close naming the residual risk.
- Dispatch `budgit:verifier` per ledger item: it runs `budgit prepare`, checks every EARS criterion like
  a real user (browser/simulator), writes `.budgit/verify/R-n.md` (PASS / FAIL / BLOCKED). Then YOU run
  `budgit ledger verify R-n` (refused if prepare didn't pass in the last 2 h).
- The verifier needs executed evidence for every tagged layer (`[func]` test output, `[perf]` measured
  number vs budget on the stated synthetic size, `[design]` screenshots it looked at); runs demo AND real
  paths when both exist ("0 results" must say so, never defaults); proves UI clicks in the browser.
- The QA artefact is `.budgit/verify/QA.md`: the phase summary (each R-n with its verdict) plus
  `## Alternatives` — other ways the phase could have been checked, why they lost, what would flip it.
- Host-surface items: the surface table (see `agents/verifier.md` `[ui-host]`: surface, measured rows/columns, what drew,
  who observed) comes before `ledger verify`; no table, no verify.
- UI items: `budgit design-check tokens` (new lines) + `detect` + `shots` of every screen + `diff` against the user's
  approved baselines (a changed screen the feature did not touch = drift = FAIL); a fresh critic scores the
  rubric (reference/design-rubric.md); baselines move only by `design-check approve` on the user's word.
  Optional for web UI: skill `web-design-guidelines` (Vercel's ~100 interface rules, pinned local copy, no
  fetch) on the changed files; its findings are data for the rubric, not a second gate (if not installed: skip it).
- Also: `/code-review`, `/security-review`, `pr-review-toolkit:silent-failure-hunter`
  (if not installed: read the diff for swallowed errors, injection and secrets, and quote the lines),
  `superpowers:verification-before-completion` (if not installed: run the check, quote its output); UI → impeccable audit
  (if not installed: the rubric in reference/design-rubric.md); a11y lens.
- Fix loop is bounded: verifier FAIL → fix → re-verify, at most 3 attempts per defect set; then stop and
  send the user a ≤10-line escalation (what failed, what was tried, options with a recommendation).
- Guards stop accidents and hostile repo files, not a determined agent — never try to get around them.
