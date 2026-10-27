# Phase 6 — Launch
- Lenses: marketing-devrel, edge (store rules, free tiers, legal), a11y, `lenses/writing.md` (every pitch/store/post text).
- Gate artefact: `docs/launch.md` — one-line pitch, landing/store copy, channels, first post, what to
  measure and when to look again (feeds the retro).
- Ship check: text assets (HTML/CSS/JS/JSON/SVG) are served compressed — brotli, gzip fallback; verify with
  `curl -sI -H 'Accept-Encoding: br, gzip' <url> | grep -i content-encoding` on the deployed URL, not assumed.
- Scope gate: before asking the user for the yes to publish, run `budgit release-check`. It exits non-zero while any item is open or in progress and not deferred, so the release waits until each one is built or the user parks it. Show the user its table (built, unbuilt, deferred) with the ask. It also prints a graph check, advice only (built items with no PASS receipt, items whose claiming session ended, built items that name an unbuilt one); add `--since <ref>` to list commits since the last release that name no item. Read it before the ask and say what you did about each line.
