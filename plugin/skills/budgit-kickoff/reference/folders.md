# Where things are saved in a project

One structure in every project. The data folder (`.budgit/`) holds everything Budgit and its agents write about the work; the
project's own folders hold the product.

| What | Where |
|---|---|
| Ledger, progress, retro, project.json | `.budgit/` |
| Plans and specs written before building | `.budgit/plans/` |
| Research notes (URL + date) | `.budgit/research/` (raw scout files in `.budgit/research/raw/`, never read by hand) |
| Agent and verifier reports | `.budgit/reports/`, `.budgit/verify/` |
| Handovers between sessions | `.budgit/handover/` |
| The product (code, its own manual, assets) | the project's own folders, e.g. `src/`, `docs/` |

Rules:
- Save a plan, research note, report or handover in the data folder the first time. Never in `docs/plans/`, `research/`,
  `reports/` or `handoffs/` at the project root.
- One copy. To use a document elsewhere, link to it by path or `[[note]]`; do not copy it. Copying makes two versions that drift.
- Documents already in the wrong place: `budgit layout` lists them (a protected project: only how many), `budgit layout --move
  --yes` moves them with history and `budgit layout --undo` puts them back. A project that wants its own layout says
  `"layout": "keep"` in `.budgit/project.json`.
- The hub's research notes live in `<hub>/.budgit/research/`; an older hub moves them with `budgit hub --move-research`.
