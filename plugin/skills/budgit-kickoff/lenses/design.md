# Lens: design (UX/UI)
Must-obey rules live HERE (references often go unread). Unclear or ugly = useless (the user's rule).
- **Which skill:** call `budgit skills why --job design/<shape|build|polish|critique|a11y|lint|tokens> --traits surface=<web|pixel|terminal|native-macos>,stakes=<routine|pillar>` and follow its pick, its checker and its fallback chain; it logs the choice. A `gap` answer means the project rules apply: `reference/rules/` for pixel art at 1x, frame timing, terminal text, README structure and light/dark SVG, then this lens and the rubric. The skill names below are the last resort when the command is not available.
- **ONE design hand** (opus) owns tokens and all CSS. Feature, data and QA agents only consume tokens; they never
  add a literal colour, font size, radius, shadow or spacing (drift comes from many hands).
- **Register first:** product/dashboard = quiet, dense, tabular (the product register of the skill `budgit skills why` picks; if none: the rubric's product register); marketing = art
  directed (taste-skill per project). Write it down before any screen, in the project's own file and format;
  list the files in `.budgit/project.json` → `design.docs` (default `docs/design/*.md`).
- **Tokens:** spacing from one scale (4/8/12/16/24/32/48); ≤2 font families, hand-picked 5–6 step type scale,
  body ≥14 px; 8–10 tinted greys + 1 accent + 4 semantic, OKLCH ramps; ≤3 radii (nested radius = outer − gap);
  2-layer shadows only on floating layers. Positive spec beats ban lists: name the default to avoid AND the pick.
- **The user's look (a54):** a project that already has a look gets options only inside that family (soft-tint pills,
  solid severity chips, cards). Never propose a new family unasked. The user picks among ≤3 options.
- **Dashboards:** component base case by case: for React offer shadcn/ui + Recharts as one option, never assume.
  KPI = number + delta + comparison label, rounded (1.2M); `tabular-nums`, numbers right-aligned;
  one accent, colour only for status and never colour alone; same metric = same colour everywhere; every
  chart/table has empty, loading, error, partial and zero-results (empty ≠ zero-results); no zero rows, dots
  or grey placeholder bars (hide, or say "none"); a last-updated stamp; large synthetic data for perf.
- **Honest copy:** never invent metrics or testimonials; unknown = "—" + "metric to confirm".
- **Top 10 slop tells (fix on sight):** gradient text; glow shadows; nested cards; identical card grids; one radius
  everywhere; uniform grey shadows; tracked eyebrow labels on every section; fade-slide-up on every block;
  `transition: all` and bounce easing; Inter/purple-on-white defaults with no reason.
- **Safety rail:** before editing, list the files you will change or delete; touch nothing outside that list.
- **Gates are scripts (0 tokens):** `budgit design-check tokens` (new literal values, exit 1), `docs`, `shots`
  (3 widths + the zoom viewport), `diff` vs the user's approved baselines, `detect` (impeccable; if missing it says UNVERIFIED: use the rubric instead).
  Open every screenshot you take. Before any visual reaches the user, check it at its real 1x size next to a zoom with one critique pass; the bar is "clear at a glance without text". Scoring: reference/design-rubric.md (fresh critic, not the builder).
- Run the skill that `budgit skills why` picks for each stage (last resort `impeccable`: shape → craft → audit/polish; if not installed: this lens and the rubric); `design:ux-copy`, `design:user-research` (if not installed: plain sentence-case copy, no filler).
- Pillar decisions get multi-screen interactive prototypes per option.
- Images/keyframes: `budgit image "<prompt>" --name <file>` (Codex CLI, the ChatGPT lane, if set up, traced in MANIFEST.jsonl). The user is never the copy-paste proxy. Heavy batches → ask first.
- Last resort for landing pages: `taste-v2`, `high-end-visual-design` (optional; apps and dashboards stay on `impeccable`; if not installed: this lens and the rubric). Decks: `frontend-slides` (optional; self-host fonts before sending; if not installed: plain HTML sections). Last resort for diagrams and charts: `diagram-design` for diagrams, `dataviz` for charts and dashboards (optional; if not installed: a plain SVG or table with labelled axes). Anything else: install per project only, pinned, scripts read first.
