# Changelog

Update with `claude plugin marketplace update budgit-hub`, then `claude plugin update budgit@budgit-hub`, then `/reload-plugins` in Claude Code. The Release notes page of the wiki has the same notes.

## 0.13.1

- **The line under the prompt shows in every folder.** Inside a project it shows that project's line; anywhere else it shows your plan limit. `/budgit-bar-probe` tells what your screen does with it.
- **The line under the prompt is plain text.** The host draws no colour there, so no colour codes are sent to it.
- **Budgit tells you when there is an update.** The line says when the Budgit loaded in this chat is older than the one on disk ("reopen") or when a newer release is waiting. `budgit update` prints the update commands and never runs them. `budgit update --check` asks the public repo once.
- **`budgit migrate-install`** moves an install from a live folder to the public marketplace. It shows the commands and runs them only after you answer yes at a terminal. It disables the old plugin and does not delete it.
- **Approval shows the commands safely.** Control characters appear as text, with a short digest of what you approve.
- **After you update, open new chats.** Chats opened before the update keep the old line until you reopen them.
- **`budgit lint` finds hidden characters.** It lists invisible, zero-width and look-alike characters in tracked files, file names and commit messages, one line per code point, and keeps the legitimate ones (emoji joiners, right-to-left marks). `budgit lint --fix` shows a diff first and removes only the rest. `budgit release-check` and the public export warn about leftovers.
- **`budgit release-check`** is the scope gate before a release: it stops while a request is open or in progress and not deferred.
- **Skills you can see before they load.** `budgit skills list`, `show`, `why`, `check` and `load-plan` describe which skill fits which job and what each one costs in context. Nothing changes your settings; every command only prints.
- **Reviewers chosen by trigger.** `budgit security plan --trigger T1` to `T4` prints which reviewers a change needs and how many, by budget level.
- **User-simulation runs scale with your budget.** Five runs when the budget is ample, three when normal, one when tight, and the spread is always shown. `budgit hypotheses` keeps each hypothesis and its score.
- **Private projects stay private in Brote's live feed.** A project under a `protected_dirs` folder, or with `"private": true`, shows counts only, under the name "private".
- **The floating Brote ships ready to run and opens by itself** (macOS). The next message you send in any chat after installing or updating (no new chat, no command) opens the floating Brote: the prebuilt app that comes inside the plugin is copied and opens with your Claude sessions, and it closes by itself about two minutes after the last one ends. `/budgit-brote off` closes him and keeps him off for good (no update turns him on again), `/budgit-brote on` brings him back, `/budgit-brote status` says whether he is running and `/budgit-brote uninstall` removes every file it wrote; the same words work in a terminal as `budgit brote on|off|status|stop|uninstall`. A chat that was already open when you updated keeps the old plugin until you reopen Claude, start a new chat or run `/reload-plugins`. It needs no build step and asks macOS for no permission. He paces calmer, walks while Claude works, rotates five reactions to a click, fetches a thrown book and wakes up in his bed. Tested on Apple chips; the Intel half is built but not yet run on an Intel Mac.
- **Where Brote shows up, said plainly.** Above the prompt in the terminal, when the folder you opened has a `.budgit` or `.studio` folder directly inside it. The desktop app shows the status line and `/brote`; there, Brote is the floating window, which opens by itself.

## 0.13.0

- **A new status line.** One row under the prompt: items in progress, items to do, ideas, a six-cell bar of the week, and either "you make it" or the day and hour your week runs out. `/budgit-colour-probe` shows it with colour codes once.
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
