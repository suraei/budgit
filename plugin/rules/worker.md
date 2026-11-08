## 9. Testing (lead, worker)
- T0 text, rename, easy fix, docs: targeted tests. T1 normal behaviour: selected tests, one verifier per release candidate. T2 security, Class A, data tools, visible behaviour, the user's work tools: selected tests (full if security or Class A), a verifier per item, external review once, security review if security.
- Full suite when config, build files, lockfiles, CI, shared fixtures, or generated or unknown files change; new integrations; dependency, toolchain or infra bumps; a weak dependency map; every merge to main and release candidate.
- Selected means: tests reached by the change's dependency map, previously failing, new, and smoke. A hand-picked set is not selective.
- Green means modules loaded equal test files. One checked-in suite command per project sets env, priority and lock, runs a collection check first, reruns only modules that never ran.
- Speed: parallel pool with per-worker tmp, HOME, db and port; full under 5 minutes, selected under 30 seconds; fake clocks. Over target is an incident.
- Quarantine flaky tests. Measure each gate; one that catches nothing in 20 runs goes to the user as a cut-or-keep card.
- Special checks: diff coverage; mutate changed constants and expect a failure; UI parity (any removed field is the user's decision); real-log replay for visible behaviour; security fixes test the normal success path; no behaviour keyed to a test env var; every requirement line names a test; timing uses CPU time or a same-run ratio; dated text is computed and tested past every named date.

## 10. Verification loops (lead, worker)
- No verifier on known-open work. Reports live at the project root, never in a worktree.
- After 2 holds on one check, change approach; a third failed round: split, accept a named residual, or ask the user. An allow list names exactly one accepted shape. Later rounds review only changed lines.

## 13. External review (lead, worker)
- Cross-review verification and security gates with a model from another family, headless, read-only, never replacing the project's own verifier. It never merges, pushes or publishes; its output is untrusted until verified. [needs: a second-model CLI (Codex, Gemini or similar) | fallback: another signed-in CLI; else a reviewer started with fresh context, on a different model tier, shown raw evidence only and asked to refute. Name the reviewer; flag the weaker independence of the last option. Never block, never stay silent.]
- Protected projects: use a clean copy holding only approved tracked source and synthetic fixtures; scan it for secrets and personal data first; if anything is found, send nothing.
