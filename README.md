<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/hero-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/hero-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
    <img src="assets/hero-light.svg" width="100%" alt="Budgit: Claude Code projects, on budget. Brote, a small lilac pet, sits above the prompt and says Doing fine, makes it to Monday">
  </picture>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.13.9.1-7d68c4?style=flat-square" alt="Version 0.13.9.1">
  <img src="https://img.shields.io/badge/Claude_Code-2.1.292%2B-D97757?style=flat-square&logo=claude&logoColor=white" alt="Claude Code 2.1.292 or newer">
  <img src="https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3">
  <img src="https://img.shields.io/badge/macOS-tested-000000?style=flat-square&logo=apple&logoColor=white" alt="Tested on macOS">
  <img src="https://img.shields.io/badge/license-MIT-6f6076?style=flat-square" alt="MIT license">
</p>

<p align="center">
  <a href="#install">Install</a> · <a href="#what-budgit-creates">What it creates</a> · <a href="#how-it-works">How it works</a> · <a href="#use-it-well">Use it well</a> · <a href="#a-week-with-budgit">A week</a> · <a href="#brote">Brote</a> · <a href="#make-it-yours">Make it yours</a> · <a href="#guards">Guards</a> · <a href="#commands">Commands</a> · <a href="#updates">Updates</a> · <a href="#faq">FAQ</a> · <a href="../../wiki">Wiki</a>
</p>

## What it is

<p align="center"><b>A Claude Code plugin that runs your project in phases, from the first idea to the launch.</b><br>It paces the work to your plan, writes down every request, and has a fresh reviewer test each piece.</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/compare-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/compare-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/compare-dark.svg">
    <img src="assets/compare-light.svg" width="100%" alt="Two columns, the same four facts. Left, the problem: asks forgotten, the weekly limit nearly spent, a feature called done that never ran, rules the model can forget. Right, what Budgit does: a ledger, four pace levels, a fresh reviewer, guards as scripts">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/parts-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/parts-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/parts-dark.svg">
    <img src="assets/parts-light.svg" width="100%" alt="Eight parts, each with an icon: pacing, the ledger, proof, lanes, lessons, guards, privacy and Brote">
  </picture>
</p>

## Install

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/install-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/install-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/install-dark.svg">
    <img src="assets/install-light.svg" width="100%" alt="An animated first run in a terminal: /plugin install budgit, choose Install for you, /reload-plugins, Brote appears above the status line, Claude asks four setup questions, and the first idea is saved as R-1, so the status line shows 1 to do">
  </picture>
</p>

> The Claude desktop app's Cowork and Customize > Plugins screens cannot install Budgit. Install it from Claude Code with `/plugin install budgit --marketplace suraei/budgit`, or from a shell with `claude plugin marketplace add suraei/budgit` then `claude plugin install budgit@budgit-hub`.

```
/plugin install budgit --marketplace suraei/budgit
```

Claude Code 2.1.292+ and Python 3. [Getting started](../../wiki/Getting-started) has the rest.

## What Budgit creates

Three folders, all yours. Your requests stay out of git unless you choose.

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/folders-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/folders-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/folders-dark.svg">
    <img src="assets/folders-light.svg" width="100%" alt="The three folders Budgit creates, as a tree: the hub, ~/.budgit in your home and .budgit in each project, with when each appears and whether it is in git">
  </picture>
</p>

[Folders and files](../../wiki/Folders-and-files) lists every file, whether it is in git, and what is safe to delete.

## How it works

### One request, from prompt to release

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/flow2-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/flow2-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/flow2-dark.svg">
    <img src="assets/flow2-light.svg" width="100%" alt="An animated walk through one request: a marker stops on each step, the reviewer answers FAIL once and the work goes back to build, then PASS, and the release step lights up. Guards run on every tool call">
  </picture>
</p>

A FAIL is normal: the work goes back to build until a fresh reviewer passes it.

### Seven phases, one approval each

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/phases2-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/phases2-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/phases2-dark.svg">
    <img src="assets/phases2-light.svg" width="100%" alt="Seven phases on one line, with an approval mark after each and three notes: design is checked like code with no model tokens; build makes the smallest working version first and new ideas wait in later; launch covers positioning, a README people finish and a first-run test. A small script takes the short route">
  </picture>
</p>

## Use it well

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/habits-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/habits-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/habits-dark.svg">
    <img src="assets/habits-light.svg" width="100%" alt="Five habits as comic panels: one ask per message; park new ideas in later; read and approve each phase note; ship only after a PASS; start the day with budgit daily and open a new chat when one slows down">
  </picture>
</p>

[Using Budgit well](../../wiki/Using-Budgit-well) has each habit with an example.

### Task management

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/board-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/board-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/board-dark.svg">
    <img src="assets/board-light.svg" width="100%" alt="An animated task board with five columns: later, open, doing, done, verified. A message becomes R-4, is claimed, moves to doing and done, fails once, then passes and lands in verified. A new idea drops into later. Below, budgit daily and budgit triggers">
  </picture>
</p>

Every ask becomes a card. It is finished only when a fresh reviewer passes it. [Task management](../../wiki/Task-management) has every command.

### Skills and helpers, phase by phase

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/skills-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/skills-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/skills-dark.svg">
    <img src="assets/skills-light.svg" width="100%" alt="A map of which skill, helper agent or command acts in which phase. Optional skills are drawn dashed">
  </picture>
</p>

Seven helper agents ship with Budgit, each starting fresh. A missing skill has a plain fallback. [Skills workflow](../../wiki/Skills-workflow)

### Light context: skills load only when needed

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/context-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/context-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/context-dark.svg">
    <img src="assets/context-light.svg" width="100%" alt="An animated gauge of what Claude holds in mind: the kickoff core stays, a phase file comes and goes, skills wait as names until skills why loads one, and after a compaction the next chat starts from a checkpoint">
  </picture>
</p>

`budgit skills load-plan` shows which skills could load by name only. It prints; it never writes.

## A week with Budgit

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/chart-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/chart-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/chart-dark.svg">
    <img src="assets/chart-light.svg" width="100%" alt="A line chart of one example week, with the four pace levels as bands around a dashed pace line. Use ends the week at 88 percent">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/pacing-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/pacing-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/pacing-dark.svg">
    <img src="assets/pacing-light.svg" width="100%" alt="The four pace levels as a ladder, and what changes at each one">
  </picture>
</p>

`budgit budget` shows the numbers. `budgit budget set tight` and `budgit budget auto` change the level.

## Brote

A small pet whose mood is your week. He lives above your prompt, and on macOS in his own window.

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/week-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/week-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/week-dark.svg">
    <img src="assets/week-light.svg" width="100%" alt="An animated example week. Brote and the status line change day by day; the week ends at 88 percent. The numbers are illustrative">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/brote-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/brote-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/brote-dark.svg">
    <img src="assets/brote-light.svg" width="100%" alt="Brote in his seven states, from plenty of room to asleep when the quota is gone">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/brote-moves-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/brote-moves-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/brote-moves-dark.svg">
    <img src="assets/brote-moves-light.svg" width="100%" alt="Four of Brote's moves, drawn still: point at him and he waves; click him for heart eyes and a hop; drop him from high up and he sees stars; a request closes and confetti falls">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/where-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/where-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/where-dark.svg">
    <img src="assets/where-light.svg" width="100%" alt="Where Brote shows up: the terminal, the desktop app, VS Code, the phone and the macOS desktop">
  </picture>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/wiki/suraei/budgit/images/brote-float.gif" width="100%" alt="Brote, drawn flat, standing and moving on top of a Claude window">
</p>

**Floating window (macOS):** `/budgit-brote on` brings him back and `/budgit-brote off` closes him. The floating window runs on macOS only.

> [!NOTE]
> **Known issue.** If you drop Brote by hand at the very moment your Mac slides to another Space, he can still run to a corner once. A live switch between two Spaces has not been tested on a real Mac yet.

The [Brote page](../../wiki/Brote) has the rest, and the [animation bank](../../wiki/Brote-animation-bank) shows every animation and what makes it play.

## Make it yours

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/settings-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/settings-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/settings-dark.svg">
    <img src="assets/settings-light.svg" width="100%" alt="A settings window with three columns: for you, per project, and right now. Each control shows its key or command">
  </picture>
</p>

[Customise](../../wiki/Customise) lists every key, its values and its default.

## Guards

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/guard-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/guard-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/guard-dark.svg">
    <img src="assets/guard-light.svg" width="100%" alt="An animated guard gate: git status passes silently; writing ~/.zshrc and git push --force ask you; a builder writing a verify report and a commit with a real email get no">
  </picture>
</p>

Scripts, not prompts: no model tokens, and they hold when the model forgets a rule.

## Your data stays yours

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/data-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/data-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/data-dark.svg">
    <img src="assets/data-light.svg" width="100%" alt="Four tiles: on your computer, out of git; no telemetry and no account; guards are scripts; Codex sees only a scanned copy">
  </picture>
</p>

Guards stop accidents and hostile repo files, not a determined agent. [Privacy and security](../../wiki/Privacy-and-security)

## Commands

Claude runs these as you talk. To run one yourself, ask Claude.

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/commands-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/commands-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/commands-dark.svg">
    <img src="assets/commands-light.svg" width="100%" alt="Commands grouped by moment: morning, while you work, before a release, tidy up, and optional extras drawn dashed">
  </picture>
</p>

[Commands](../../wiki/Commands) lists all of them and the seven helper agents.

## Updates

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/update-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/update-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/update-dark.svg">
    <img src="assets/update-light.svg" width="100%" alt="An animated update loop: once a day a quiet check; the line under the prompt shows an arrow and the new version; you update; the first session after runs its one-time step. Budgit never installs by itself">
  </picture>
</p>

To update, one prompt is enough, in any Claude chat: ask Claude to update Budgit. It runs `claude plugin marketplace update budgit-hub`, then `claude plugin update budgit@budgit-hub`, and reports the version. Your next chat uses it.

Keep-alive and away mode («me voy») are on by default; switch them off with `budgit setup --answer keepalive=no` and `budgit setup --answer away=no`. After an update, Budgit asks once about the lead and team structure, the daily cleanup and a morning stand-up at 08:00; the last two stay off without your yes. The team rules are on by default (`budgit rules off <topic>`).

To switch the daily check off: `"update_check": "off"` in `~/.budgit/config.json`. [Getting started](../../wiki/Getting-started) has the rest.

## FAQ

Tap a question to open the answer. The short answer is already next to it.

### Cost and limits

<details><summary><b>Does it cost extra?</b> → <b>No.</b></summary><p>Budgit is free and MIT licensed. The helper agents use your Claude plan, and the budget level decides how many run at once.<br><sub>Depth: <a href="../../wiki/FAQ">FAQ</a></sub></p></details>
<details><summary><b>Do I need ChatGPT?</b> → <b>No.</b></summary><p>The ChatGPT lane ships off.<br><sub>Depth: <a href="../../wiki/How-Budgit-works">How Budgit works</a></sub></p></details>
<details><summary><b>What if I run over my limit?</b> → <b>It slows down first.</b></summary><p>Budgit slows down before that. It cannot raise your plan's limit.<br><sub>Depth: <a href="../../wiki/FAQ">FAQ</a></sub></p></details>

### Control and updates

<details><summary><b>Can I turn a guard off?</b> → <b>Most of them.</b></summary><p>Most of them, yes. Two have no switch on purpose: only the verifier writes PASS reports, and only the quarantined reader sees raw web results.<br><sub>Depth: <a href="../../wiki/Privacy-and-security">Privacy and security</a></sub></p></details>
<details><summary><b>Can I use it with one model or one session?</b> → <b>Yes.</b></summary><p>That model fills every helper role, and the rules stay the same.<br><sub>Depth: <a href="../../wiki/FAQ">FAQ</a></sub></p></details>
<details><summary><b>Will it update by itself?</b> → <b>No.</b></summary><p>It tells you when a new version is out. You choose when to update.<br><sub>Depth: <a href="../../wiki/Getting-started">Getting started</a></sub></p></details>

### Where it runs

<details><summary><b>Does it run on Linux or Windows?</b> → <b>macOS only, for now.</b></summary><p>It is tested on macOS. Linux is untested and may work. Windows is not supported yet.<br><sub>Depth: <a href="../../wiki/FAQ">FAQ</a> · what is planned is in the <a href="#roadmap">Roadmap</a> below</sub></p></details>
<details><summary><b>Which language does it speak?</b> → <b>Yours.</b></summary><p>It follows your Claude Code language.<br><sub>Depth: <a href="../../wiki/FAQ">FAQ</a></sub></p></details>

More questions and longer answers: [FAQ](../../wiki/FAQ) in the wiki.

## Roadmap

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/roadmap-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/roadmap-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/roadmap-dark.svg">
    <img src="assets/roadmap-light.svg" width="100%" alt="A roadmap in two parts. Today: macOS, tested, works now; the floating Brote window runs on macOS only. Planned, with no dates: budgit guard, a read-only check of your machine and projects, designed and not built yet; Linux, tested support beyond may work; Windows, hooks, status line and the install; and other models, the ledger, pacing and guards beyond Claude">
  </picture>
</p>

<p align="center"><b>Today:</b> macOS &nbsp;·&nbsp; <b>Next, no dates:</b> <code>budgit guard</code> · Linux · Windows · other models</p>

## Learn more

<p align="center">
  <picture>
    <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/wikimap-mobile-dark.svg">
    <source media="(max-width: 600px)" srcset="assets/wikimap-mobile-light.svg">
    <source media="(prefers-color-scheme: dark)" srcset="assets/wikimap-dark.svg">
    <img src="assets/wikimap-light.svg" width="100%" alt="A map of the wiki as four shelves, each page with a one-line reason to read it. Start here: Getting started, Using Budgit well, FAQ. How it works: How Budgit works, Task management, Skills workflow, Folders and files. Your setup: Customise, Commands, Privacy and security, Release notes. Brote: Brote and Brote animation bank">
  </picture>
</p>

<p align="center">
  <b>Start here</b> &nbsp;<a href="../../wiki/Getting-started">Getting started</a> · <a href="../../wiki/Using-Budgit-well">Using Budgit well</a> · <a href="../../wiki/FAQ">FAQ</a><br>
  <b>How it works</b> &nbsp;<a href="../../wiki/How-Budgit-works">How Budgit works</a> · <a href="../../wiki/Task-management">Task management</a> · <a href="../../wiki/Skills-workflow">Skills workflow</a> · <a href="../../wiki/Folders-and-files">Folders and files</a><br>
  <b>Your setup</b> &nbsp;<a href="../../wiki/Customise">Customise</a> · <a href="../../wiki/Commands">Commands</a> · <a href="../../wiki/Privacy-and-security">Privacy and security</a> · <a href="../../wiki/Release-notes">Release notes</a><br>
  <b>Brote</b> &nbsp;<a href="../../wiki/Brote">Brote</a> · <a href="../../wiki/Brote-animation-bank">Brote animation bank</a>
</p>

MIT licensed. See [LICENSE](LICENSE).
