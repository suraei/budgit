# Phase 6 — Launch
- Lenses: marketing-devrel, edge (store rules, free tiers, legal), a11y, `lenses/writing.md` (every pitch/store/post text).
- Gate artefact: `docs/launch.md` — one-line pitch, landing/store copy, channels, first post, what to
  measure and when to look again (feeds the retro).
- Ship check: text assets (HTML/CSS/JS/JSON/SVG) are served compressed — brotli, gzip fallback; verify with
  `curl -sI -H 'Accept-Encoding: br, gzip' <url> | grep -i content-encoding` on the deployed URL, not assumed.
