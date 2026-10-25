# Changelog

Update with `claude plugin marketplace update budgit-hub`, then `claude plugin update budgit@budgit-hub`, then `/reload-plugins` in Claude Code. The Release notes page of the wiki has the same notes.

## 0.13.0

- **A new status line.** One row under the prompt: items in progress, items to do, ideas, a six-cell bar of the week, and either "you make it" or the day and hour your week runs out. `/budgit-colour-probe` shows it in colour once.
- **The floating Brote follows Claude** (experimental, macOS). While Claude works he sits at a little desk and the laptop screen shows what Claude is doing. When Claude finishes, asks something or waits for a permission while you are in another app, he turns to you with a quiet badge. In free time he jumps rope, has tea or naps.
- **The floating Brote has a book and a notebook** (experimental, macOS). The book is a second little object you can drag and throw, and the notebook panel opens below Brote with your tasks.
- **Brote has moods** (experimental, macOS). Tap the floating Brote a few times in a row while he is drained: at the first tap he grumbles, at the second he gets annoyed, and at the third he gets angry, runs away from your pointer, calms down and comes back. He runs away at most three times an hour.
- **Your ledger stays private by default.** A project's ledger, notes and reports stay out of git unless you set `"track_ledger": true` in `project.json`.
- **Project text reaches Claude fenced in.** It arrives on one line inside a fenced block, so a stray line in a file cannot pose as an instruction.
- **Flags must be spelled out.** A shortened flag can no longer slip past a guard.
- **Approving a project's setup commands takes you at a terminal.** Budgit shows you the commands and asks you to confirm. From inside Claude it gives you the exact command to run in your Terminal instead.
- **A steadier budget level.** Weekly numbers from several sessions merge instead of overwriting each other.
- **`budgit setup --force` keeps what you do not pass.**
- **`exclude_dirs`** in `~/.budgit/config.json` lists transcript folders that `comply` and `retro-mine` skip.
- **Language.** With no Claude Code language set, Budgit follows your computer's preferred languages before English.
- **The kickoff names optional skills, each with a fallback.** stop-slop for public English text, taste-v2 and high-end-visual-design for landing pages, frontend-slides for decks, diagram-design and dataviz for diagrams and charts, threejs for 3D, and watch and playwright-cli for research. If one is not installed, the phase uses its plain fallback. Scrapers that need your own account or key are never defaults.
- **A new README and a wiki**, with getting started, how it works, Brote's animation bank, a FAQ and a privacy page.
- **Install steps from zero.** The README and the wiki now start with getting Claude Code, then give a one-line install for the terminal and the shell, a prompt to ask Claude to install it, the steps for VS Code and the desktop app, what each install error means, and how to update.

## 0.12.0

The first public version: Budgit under its new name, Brote above the prompt, the plain status line, items claimed by the session working on them, every message in English and Spanish, the `commit-msg` clean-up hook, a guided first run, and the floating Brote as an experimental macOS extra.
