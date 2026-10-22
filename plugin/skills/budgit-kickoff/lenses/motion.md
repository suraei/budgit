# Lens: animation & motion
- `impeccable` animate mode + `anthropic-skills:emil-design-eng` for UI motion (if not installed: this lens' checklist below); gsap-skills (official)
  for web animation; hyperframes (official) for trailers/launch videos; Rive/Lottie only when a project
  needs vector assets. Measure on a mid Android + iPhone Low Power before committing.
- Image/keyframe generation: `budgit image "<prompt>" --name <file>` (Codex CLI, the ChatGPT lane, if set up, traced in MANIFEST.jsonl). The user is never the copy-paste proxy. Heavy batches → ask first (plan limits; API is the paid fallback).

## Motion rules
- Motion thesis first, one sentence before code — what moves, why, how often it plays. Frequent = short and subtle.
- Duration tokens in the project's design docs: `--dur-micro 120ms`, `--dur-ui 200-300ms`, `--dur-page 300-500ms`;
  exit ≈70% of enter; ease-out to enter, ease-in-out for on-screen moves, linear when scroll-synced. No
  bounce/elastic. UI animation over 300ms needs a written reason.
- Ship reduced motion with the animation: swap movement for opacity/instant, never drop the information
  (CSS media query or gsap `matchMedia`).
- Animate transform and opacity only (filter sparingly); no `transition: all`, no layout properties; hover
  effects behind `(hover: hover)`.
- Gate before QA on any animated screen: `budgit design-check motion <url>` (exit 1 on FAIL: layout props,
  `transition: all`, movement under reduced motion; WARN lines need a written reason or a fix).
