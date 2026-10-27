# Skill selection: the readable form of the registry

`budgit skills why --job <job> --traits <key=value,...>` runs this procedure on `reference/skills-registry.json` and logs one line per choice. This file explains it; the registry and the command are the source. If they disagree, the registry wins and this file is out of date. The shipped registry lists only skills a public user can get; the user's own skills are kept in a private overlay (`skills-registry.local.json` in the Budgit home folder) that `budgit skills build` fills and that is merged in at run time, never exported.

## The procedure

1. **Traits.** Job (for example `design/build`, `motion/hero`, `writing/public-prose`, `process/review`), surface (`web`, `pixel`, `terminal`, `native-macos`), stakes (`routine` or `pillar`), language, where (`local` or `pr`), the project's author skill, and the budget level (`ample`, `normal`, `tight`, `critical`).
2. **Hard filters.** A skill that fails one is listed under *Left out* with its reason, never dropped silently: not in the registry; turned off or limited to the first security layer; security verdict HOLD; installed files differ from the reviewed copy (drifted) or are missing; an update waits for review; a requirement is not met (Node, Chrome, a bundled program, the network, a browser tool, Codex, a pull request, a key); the surface is in its `not_for` list; it clashes with a project rule; it would be its own checker; it would push the plan over the agent cap; only its description was read.
3. **Continuity.** The author named in the project's design settings continues the author role (shape, build, polish). Checkers are always a different skill from the author.
4. **Score.** Skills with fit 2 or 3 for the job compete: `3·fit + S·(2·check + receipt) − B·cost − risk`. S is 2 for pillar stakes, else 1. B is 0, 1, 2, 3 at ample, normal, tight, critical. Cost counts length, agents, servers and bundled programs. Risk is 0 for a clean review, 1 for conditions or no trail, 2 for a program that cannot be audited or data that leaves the machine.
5. **Tie band.** Within one point: the continuity hand, then lower risk, then lower cost, then the higher score, then the name. If the best skills tie on evidence for a job marked for a bake-off, the answer is the provisional default from the registry's table and says `bakeoff: needed`.
6. **Fallback.** Every pick lists ranked fallbacks. The last is always project text that exists.
7. **Gap.** No survivor with fit 2 or more: the answer is a gap. The project rules named for that surface or job apply, then the lens. A gap is logged and opens a watch row in the project's `skills-gaps.json`.
8. **Plan.** For author jobs the plan adds checkers by level: ample two, normal one, tight and critical instruments only. The agent cap is 4, 3, 2, 0 and counts a skill's own sub-agents. A debate is recommended only at ample with pillar stakes. Cells of the budget table that need the debate runner are printed as recommendations; nothing runs them yet.

## Decision trees

### Design

```
surface?
├─ pixel sprite, frame motion, terminal text -> gap: project rules, a critic at 1x. Never impeccable.
├─ diagram or chart -> diagram-design (nine nodes, twelve arrows); README art in light and dark = two files.
├─ deck -> in a browser: frontend-slides; a .pptx is required: pptx.
├─ docs or README -> structure: the README rule, engineering:documentation; prose: the writing lens.
├─ native macOS panel -> gap: project rules and a real 1x screenshot.
└─ web
   ├─ project names an author -> that author continues.
   ├─ app, dashboard, settings -> bake-off needed; until it runs, the provisional default.
   ├─ landing -> small and routine: frontend-design; public or pillar: taste-v2.
   ├─ showpiece -> taste-v2; scroll-driven: gsap-scrolltrigger.
   └─ stage
      ├─ critique: routine -> design-critique template; pillar and budget not tight -> impeccable critique
      ├─ accessibility: no browser -> web-design-guidelines, then accessibility-review; browser -> impeccable audit
      ├─ lint: Node only -> taste-v2 lint; UX patterns -> web-design-guidelines
      ├─ polish: a critique exists -> impeccable polish; taste-v2 files exist -> its loop
      └─ tokens: code -> impeccable extract; quick audit -> design-system
```

### Motion

```
surface?
├─ web micro-interaction -> emil-design-eng; impeccable animate only for an authored hero moment
├─ timeline, scroll, SVG-heavy -> gsap-core and gsap-timeline (and scrolltrigger); a simple hover is CSS
├─ 3D -> threejs-animation
└─ sprite, frame, Swift -> gap: the frame-timing rule; the measured timing in the running app is the check
```

### Writing

```
├─ public English prose -> humanize gate, then stop-slop
├─ UI string, notice, error, empty state -> design:ux-copy; a whole flow: impeccable clarify
└─ Spanish text, legal or security wording -> project writing lens only
```

### Process (inside a Budgit phase the phase file leads)

```
size mini -> no process skill
spec -> the phase; brainstorming only outside a Budgit project
plan -> writing-plans; critique: plan-critic
execute -> under about eight tasks or shared interfaces: executing-plans; independent and costly to fail: subagent-driven-development
debug -> stubborn: systematic-debugging
verify -> every claim: verification-before-completion; closing an item: the verifier
review -> local diff: code-review, then the review toolkit by risk; pull request: the code-review plugin (blocked while the footer rule applies)
```

### Research, security, validate (static)

- Research: a web question goes to the researcher, one angle per call; public URLs to `budgit scout` with the reader; video to `watch`; a JavaScript-heavy page to `playwright-cli`; logged-in or visible work to the built-in browser. Paid or cloud scrapers are not selectable without the user's yes.
- Security: a third-party skill, plugin, MCP server or hook goes to the security reviewer before any copy; an own diff to the security review; a release to both plus `budgit secrets` and `budgit audit-mcp`; a new dependency to `budgit audit`. `budgit security plan` prints the reviewers for a trigger and the paths. The `security-guidance` plugin is a hook layer, not a reviewer.
- Validate: is it wanted -> the phase, the researcher, `budgit scout`; how will a user react -> persona-sim run 5, 3, 1 or 0 times at ample, normal, tight, critical (the plan prints the number), always reporting the spread and the disagreements; its claims go to `budgit hypotheses` and stay open until real data scores them (a hypothesis, never proof); predicting a market or people at scale -> not selectable.

## What the log line records

Time, job, traits, the pick and why, the skills it beat, what was left out and why, companions, the fallback used, the gap, the registry version, the budget level, the role, how it was run, whether a debate ran, the winner, whether the pick was provisional, whether a bake-off is needed, agent runs, and per node the calls and tokens (tokens stay empty unless the agent report gives them).
