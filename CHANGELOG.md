# Changelog

To update, ask Claude in any chat to update Budgit: it runs `claude plugin marketplace update budgit-hub`, then `claude plugin update budgit@budgit-hub`, and reports the version. Your next chat uses it. In a terminal session, `/reload-plugins` is an optional extra. The Release notes page of the wiki has the same notes.

## 0.13.9

- **The update says when it takes effect.** After an update, `budgit update` prints one line: "Budgit updated to X; your next chat uses it".
- **Keep-alive and away mode («me voy») are on by default.** Switch them off with `budgit setup --answer keepalive=no` (or `budgit job off keepalive`) and `budgit setup --answer away=no`.
- **After an update, Budgit asks once about the rest.** It asks whether to set up the lead and team structure, and whether to turn on the daily cleanup and a morning stand-up at 08:00; both stay off without your yes. Answer later with `budgit setup --answer cleanup=yes` (or `no`, `later`; keys are structure, cleanup, standup).
- **No checks card on an empty project.** A project with nothing in it no longer gets a card proposing checks.
- **The team rules are on by default.** Turn a topic off with `budgit rules off <topic>`, back on with `budgit rules on <topic>`; `budgit rules list` shows them.
- **New `budgit job status`, `budgit job on` and `budgit job off`** for the cleanup, keep-alive and stand-up jobs. A job checks first whether it has anything to do, and skips the run if not.
- **New `budgit busy`.** It says whether the machine is busy (CPU or memory) so heavy work can wait.
- **Known gap.** The rules block does not yet show your own settings (language, standing permissions, protected projects); that arrives in the next version.

## 0.13.8

- **Test and lint checks are detected and offered for you.** New projects and updated projects now get their test and lint commands detected and offered as one question in the chat; one click approves them.
- **A first design review waits instead of failing.** A design review with no approved baseline yet now waits for your approval of the captures instead of failing.

## 0.13.7

- **Brote no longer flies to the bottom-left corner after you switch tab, app or Space.** He waits for the window to settle (a short recheck after 0.6 s, another after 3 s) and then walks with the walking animation.
- **A plain script syntax check is no longer refused.** `bash -n` or `sh -n` with a system shell (from /bin, /usr/bin, /opt/homebrew/bin or /usr/local/bin) now runs without the home-folder guard stopping it.
- **New README and wiki.** The README shows how Budgit works in animated pictures. The details moved to the wiki, with new pages: Using Budgit well, Task management, Folders and files and Customise.

## 0.13.6

- **Brote stays where you put him, keeps off the middle of the Claude window, and climbs and flies more.** Drop him in a corner and he wanders only a little around it, then comes back after every outing. He keeps off the middle of the Claude window and its input box (and the same for terminals and editors), unless you put him there yourself. He now climbs up the side of the screen, flies up to a corner and hovers, and goes back home. This needs no new permission.
- **Known issue (Brote).** When you switch Space or app, Brote can fly to a corner and slide back, and his outings are rare, so the climbing and flying described above shows far less than it reads. The fix comes in 0.13.7.
- **The daily cleanup job now survives updates.** Budgit rewrites a small launcher at the first session after each update, with no command to run.
- **`budgit housekeeping --schedule on` is stricter.** It refuses when BUDGIT_HOME or CLAUDE_CONFIG_DIR is set, when the LaunchAgents folder is not yours, or when the state home is not the default. A rebuilt job keeps only its time, Nice, low-priority IO and BUDGIT_HOME, and bad values fall back to 09:30. Off macOS the step does nothing.
- **The README has a new "Folders Budgit creates" section.**
- **Budgit saves a checkpoint before Claude compacts a long chat.** Just before compaction it writes down where the work was: the open items, the last tool calls, the git state and the top of the progress file. When the next session starts after a compaction, it is pointed to the newest checkpoint so it can pick up where the work stopped. Checkpoints are kept in your per-user Budgit folder, never in the project, and the newest 20 per project are kept.
- **A new builder helper agent for briefed build work.** The builder takes a written brief (goal, files, tests, call budget) and builds it with the same rules every time: edit with the file tools, one plain command at a time, commit only after green tests, and hand back a clear list. A brief template comes with it.

## 0.13.5

- **Codex can review a protected project's code through a clean copy.** `budgit codex review --clean-copy` reads the allow list you write in the project's Budgit folder (`codex-allow.txt`, one git pathspec per line, tracked source and tests only), copies those files from the last commit into a random folder under `/private/tmp/codex-clean/`, leaves out data, keys, logs, reports and notes even if listed, scans the copy for secrets and personal data, runs Codex on it inside a deny-by-default macOS sandbox that can read only the copy and system files, write only its own scratch folder, and reach the network only on port 443, plus DNS name lookups through the system resolver, and deletes it every time. Any scan hit sends nothing. Known risk you accept by using it: Codex keeps its own shell tool and reads a per-run copy of your ChatGPT login, so hidden instructions in the copied code could make it send that login out; if you suspect that, run `codex logout` and then `codex login`, which ends the old token. Without an allow list it refuses and says how to write one. `budgit codex review` without the flag still refuses a protected folder. `budgit security plan` keeps the Codex reviewer on a protected path when an allow list exists. `budgit codex clean-copy-offer` prints the Claude Code auto-mode entry for clean copies and a command block you run yourself; Budgit never edits your settings.
- **Safer copy checks.**
- **`budgit export` no longer refuses ordinary words.** A private project whose folder name is a common word stops blocking the leak gate.

## 0.13.4

- **Budgit checks for a new release once a day.** It does a plain GET of the public `plugin.json` on `raw.githubusercontent.com`, from a background process, and the line under the prompt says when a newer release is waiting. The request sends only your IP address and a standard User-Agent. Turn it off with `BUDGIT_NO_UPDATE_CHECK=1` in your environment or `"update_check": "off"` in Budgit's `config.json`.

## 0.13.3

- **`budgit daily` is a morning read of your ledger.** It shows the counts of the day, batches of related items, probable duplicates (by id), the order of the next version with Class A first, and a preview of the housekeeping clean-up. It uses no network and no model tokens, and it never writes the ledger. `budgit daily NAME` reads one project of your list and `--all` reads every project; `--json` gives the same as JSON. If git was not checked, the preview says "not checked" instead of 0. A private project shows only a masked label and counts, and a private name never appears in any output. Once a day, the session start also offers it in one line.
- **`budgit housekeeping` tidies finished work.** It counts what is in flight, then lists the worktrees that are merged, clean and idle, and the merged branches. It changes nothing until you add `--apply`; then it runs only `git worktree remove` and `git branch -d`. Folders you list as protected are skipped.
- **One list of all your projects.** `budgit portfolio` shows every project in one read-only view (`add`, `pm`, `off`, `on` and `protect` keep the list), and a protected project shows counts only, under the label "private". `budgit capacity` shows how many items may run at once and how the limit is shared between projects. `budgit ledger start` now stops at a project's limit unless you use `--expedite` for the one urgent slot, or `--override-by` with the input where you said to go ahead. `budgit ledger match` tells which existing items a new input belongs to, so you match first and add second.
- **`budgit triggers` lists the decisions waiting for you.** Each card comes with a default that applies if nobody answers, at most three show a day, and the urgent ones come first. The session start says once a day that decisions are waiting.
- **Risky writes and destructive git commands always ask.** In every session, with or without protected data, Budgit asks before a command or a Write or Edit changes a place that runs code later: shell startup files, git hooks and git config, package-manager config such as `.npmrc`, launch agents, `.ssh` and Claude's own settings and hooks. It also asks before a git command that throws work away, such as `reset --hard`, `clean`, `checkout --` or `-f`, `restore`, `stash drop`, `branch -D`, `push --force` and `reflog expire`. A plain read, such as `git status` or `git log`, stays silent. These guards only ask; they never allow anything another guard refuses.
- **Tidier folders.** `budgit layout` lists documents saved outside a project's data folder and moves them in with `--move --yes`; `--undo` puts them back. `budgit hub --move-research` moves an older hub's research notes into the hub's data folder (a dry run unless you add `--yes`). `budgit relocate` plans, applies and undoes moving every project to one folder.
- **More helpers.** Each one only reports until you add `--apply`. `budgit sweep` lists stuck claims, removable worktrees, ended sessions and stray processes. `budgit upgrade --check` lists what a project needs to match this Budgit, and `--apply` does it, skipping a busy project and keeping an undo record. `budgit merge-close` closes the items whose work landed on main, as a dry run unless you add `--apply`. `budgit admit` says go, wait or refuse for one more lane or suite from the machine's load, and `budgit suite` runs a project's full test suite in a queue shared by the whole machine, and skips a tree that already passed.

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
