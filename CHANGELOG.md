# Changelog

To update, ask Claude in any chat to update Budgit: it runs `claude plugin marketplace update budgit-hub`, then `claude plugin update budgit@budgit-hub`, and reports the version. Your next chat uses it. In a terminal session, `/reload-plugins` is an optional extra. The Release notes page of the wiki has the same notes.

## 0.13.9.46

### Changed

- The budget line follows the account figure from the app, so it no longer shows a weekly percentage your account does not have.
- A serial full test run now tells you where the faster runner is.
- The test gate moves up to the full suite on its own when needed.
- A clean snapshot commit is made for the private CI push.

## 0.13.9.45

### Changed

- Read commands are no longer asked as writes.
- A full test suite no longer needs a click.
- Protected names are checked before a push, with reviewed exceptions.
- CI runs only nightly, on main and by hand.

## 0.13.9.44

### Changed

- R-1366: ledger, inbox and progress text replace private project names with their aliases on write; a strict pre-push names check (budgit names-check) refuses a push that holds one.

## 0.13.9.43

### Changed

- R-1313: a work folder is removed when its branch lands on main, and kept when it holds untracked files.

## 0.13.9.42

### Changed

- R-1358: skills are picked by task type, project and request words (1-3 per task, new types finance, market data, 3D, office docs); outside tools (Codex, Gemini, OpenRouter) get a use log, a health check and a quota fallback.

## 0.13.9.41

### Changed

- R-1359: long notes go in through a file instead of the command line: ledger add, finding add and link take --text-file; tm log --text-file; new progress add and friction add.

## 0.13.9.40

### Changed

- R-1333: One task order: urgent bugs that stop work first, then what is marked urgent, each row with its reason; pinned items in their own block.

## 0.13.9.39

### Changed

- R-1343: notes from the coordinator now reach the registered project manager session, which takes over automatically when the earlier one has ended.

## 0.13.9.38

### Changed

- R-1345: an item in a project with no git repository can now be closed using a sealed PASS report as evidence.

## 0.13.9.37

### Changed

- R-1309: the weekly budget figure now follows a plausible rise at once and only holds an outlier. A lower reading from a source that was not typed no longer replaces a correct figure.

## 0.13.9.36

### Changed

- R-1343: the TM's briefs reach the PM as notes shown on its next turn, so relays no longer need anyone to type them.

## 0.13.9.35

### Changed

- The weekly usage figure hold now ends (R-1309 follow-up): it lasts 6 hours after the app's own reading, or until two different sources both report a higher figure within 30 minutes, and the line says why. A row left by the previous build gets its 6 hours at the first reading after the update; a future or unknown time releases the hold; window kinds are cleaned and capped.
- New: `budgit housekeeping procs` lists stale agent test servers (dry run); `--stop` sends SIGTERM only, and only to processes with a session marker or in a session worktree (R-1322). Ports saved with `--protect-port` or `--protect-listening` are never touched. The daily cleanup job also stops them when it runs with --apply.
- One task-order module and a read-only dry run, with no view change (R-1333, first step).
- Stale tests and the rules preview are updated.

## 0.13.9.34

### Changed

- The weekly usage figure follows the app's usage panel (R-1309). A higher reading from the status line or the host no longer overwrites it; it is held and marked as possibly stale.
- Looking at status or changes in a protected repo asks unless the safe form is used (R-1294).
- Team rule text is corrected (R-1327).
- A pointer or link into a protected or private folder is not checked (R-644).

### Fixed

- A commit right after tests run with node or npm is no longer blocked (R-1326).
- A test table entry for the capacity set action is fixed (r901 test fix).

## 0.13.9.33

### Changed

- The weekly budget reset is now taken and not hidden. A stale reading says its age and source.
- The test gate always adds the leak checks when visible text changes, and it fails closed when it cannot list the changed files.
- The model cap honours a Fable request only with a written reason at the start of a line (`ESCALATE-FABLE: <why>`). It logs the requested and served model for each agent, and when the cap check fails it lowers to the cheapest model.

### Fixed

- The write guard closes more ways a shell line can hide a destructive command.

## 0.13.9.32

### Changed

- You can set how many tasks run at once per project, and it holds until your weekly reset.

### Fixed

- A refusal message from a hook is no longer saved as if you had typed it.

## 0.13.9.31

### Fixed

- The guard refuses a read command whose arguments hold a shell expansion (a variable, a backtick, a home tilde) or a glob that could spell an option.
- A home stand-in no longer hides the live checkout from the guard.
- A line the guard cannot lex safely counts as reaching the live checkout.

## 0.13.9.30

### Fixed

- The guard accepts a link to the user's own budgit.
- The pinned test runner may record test timings (`--record`) from the bound main checkout only.
- A failed Brote swap never starts a copy that failed its check, and turning Brote on keeps the version already tried.

## 0.13.9.29

- **An open Brote switches to the new version.** At the next session start after an update, a Brote that is already open closes (keeping its window place) and the new one opens, once per version; if the new one cannot start, the old one comes back.

## 0.13.9.28

- **Brote dresses up on its days.** Moustache and small hat on 19 Nov, party hat on 29 Mar, flag on 25 Nov; it looks normal on other days. The README explains how to turn on auto-update.

## 0.13.9.27

### Fixed

- Brote wears the moustache on its day; on other days it looks exactly as before.

## 0.13.9.26

### Fixed

- Headless Chrome for design checks uses a mock keychain, so macOS no longer shows a keychain pop-up.

## 0.13.9.25

### Added

- Skills tree: a small decision tree routes agents to skills, with a coverage check.

## 0.13.9.24

### Fixed

- Ledger retext removes a protected project name from an input or item text: the tool replaces the name itself, fails closed and keeps no free text for inputs.

## 0.13.9.23

### Fixed

- A subagent may read another project with `--project $HOME/...` when that read is the only command on the line; any other step on the line keeps the refusal, and a changed home variable never reaches a write target that it judged.

## 0.13.9.22

### Fixed

- A subagent may ask `ledger finding --help` again; only changing a finding stays with the coordinator.

## 0.13.9.21

### Added

- Ledger items keep structured findings from reviews (severity, class, state); `ledger finding add|set` and `ledger findings` record and list them, closing a finding needs a commit that exists, and an open high finding stops done and verified.

## 0.13.9.20

### Fixed

- The commit guard reads npm, pnpm and yarn option values (--prefix, --dir, --cwd, -w) before the test word, so a test run with a folder option followed by a commit is accepted; unknown options still fail closed.

## 0.13.9.19

### Fixed

- The guard's command tables list the new read-only `ledger timeline`, so the table tests pass again.

## 0.13.9.18

### Added

- Live run tracking: an agent launch marks the ledger items its brief names as in progress, with who and when; a hand-back records an event; `budgit ledger timeline` shows each item's history. The launch text is shielded and redacted, a tampered ledger is never written, and the brief scan is capped.

## 0.13.9.17

### Changed

- A test suite now checks memory like a lane but at the scheduled-job level: critical pressure, heavy swap or compressor use refuses it, warn alone does not; urgent mode reruns a known flaky test once, lets a step already in flight continue with fewer workers, and bisects a red merge group.

## 0.13.9.16

### Changed

- The merge guard refuses a read loop with any redirect, tee, subshell, newline or loop variable after done, and takes a reader step after the loop only from a fixed flag list per reader; the secret-scanner form must stand alone with no redirect.

## 0.13.9.15

### Changed

- The merge guard checks a read loop's variable strictly: a reassignment, source, dot, eval or any other variable refuses the line, and the guard checks the command as typed, before any home-path rewrite.

## 0.13.9.14

### Changed

- The merge guard no longer refuses a plain command only because it names an absolute path inside the project or one of its worktrees; relative, dot-dot, variable, glob, brace and link-making words still get the full scan.

## 0.13.9.13

### Added

- **Ledger items need an area.** `ledger add` now requires `--area`, and `ledger area` fills in the area of old items.
- **Ledger and rules are easier to search:** `rules show --grep` finds a rule by word, and `ledger list` has filters (including by area).
- **Every prompt carries the real local time** of your machine (clock and time zone), in English and Spanish, in every session.
- **Call caps come from similar past runs:** Budgit keeps a record of past runs, suggests a call cap from similar ones, and prints a note when a run reaches 80 % of its cap.

### Changed

- **Verifier reruns check only what changed.** Selective test runs take no suite lock, so they no longer wait behind a full run.
- **Advisory notes show once per session** instead of on every call, and each role gets one start budget; over-budget sections become short pointers.
- **`ledger done` refuses a proof sha that main cannot reach.**
- **A test run imports only its own checkout,** so a worktree can no longer test another copy's code by mistake.
- **A card about a file carries its path** (or link), including directories and Windows paths.
- With the language set to Spanish, job warnings and job messages now print in Spanish.

## 0.13.9.12

Update now if you run 0.13.9.10 or older.

This release also carries the 0.13.9.11 changes listed below, which were never published on their own.

### Fixed

- **Scheduled stand-up and keep-alive can run again** when neither Budgit nor your project sits in a folder macOS protects (Documents, Desktop, Downloads, iCloud Drive). Proven by tests; the first real scheduled run after you update is the live proof.
  - When macOS blocks Budgit's own folder, `budgit job health` and the session start say so in plain words, with the steps. Known limit: if only your project sits in a protected folder, the stand-up can come out empty without a warning (next hotfix).
  - They run in their own folder, outside Budgit's settings folder. Before, every call was refused there.
  - Stand-up and keep-alive also run on a normal busy laptop: before, a "warn" memory reading skipped them every day without a word. Proven by tests with simulated memory readings.
  - When a job does skip, `budgit job health` says why and when, and the job retries once later that day.
- **You see the stand-up when you open a session, once a day:** its date and first line, quoted.
- **A damaged ledger.json no longer turns Budgit's protections off.** You get one line naming the file and how to restore it.
- **If a Budgit guard crashes, or its library cannot load,** only a short list of read-only commands runs without asking: `git status`, `log`, `diff` and `show`, `ls`, `pwd`, and `cat`, `head` and `tail` inside the project. Everything else asks. You see a one-line warning each time.
- **Status shows whether each item's proof still holds:** PASS, FAIL, none, or superseded.
- With the language set to Spanish, the first-run commands (setup, new, status, budget, comply) now print in Spanish.

### Changed

- The search-root guard ships OFF by default. Turn it on in Budgit's own config. Its known gaps are fixed in the next hotfix.
- With the search guard on, a commit message written as a heredoc or `$(...)` that contains a word like find or grep is refused. Use a plain `git commit -m "..."`.

### Known limits

- If Claude Code sends the hook input it cannot parse, the hook lets the call through and prints "enforcement off".
- `git push --force` asks rather than refuses.
- A job that the fallback stopped is recorded as skipped after its run has ended.

### Also in this hotfix

- **The pre-commit line `budgit secrets --staged` never blocks a commit for a user without gitleaks.** It prints "staged: NOT scanned (gitleaks missing)" with the one install line and exits 0. A full `budgit secrets` with a skipped tool still exits 3, and Budgit's own release gate still counts a skipped scan as not clean. With `--staged`, the dependency scan is not part of the check and the output says so.
- **The search guard fails closed on a command it cannot read.** A search whose command name is hidden (quotes, variables, substitutions, globs, busybox, a shell fed from stdin) is refused; a search inside the project from any subfolder is allowed, `..` included. `search_guard` in Budgit's own config turns it off.

## 0.13.9.11

- **Sized ledger items get a due date from their size** (R-925). Small items are due today and rank first when overdue. A malformed due date is ignored instead of failing. `ledger add` carries `--appetite` in every example and caller, and refuses a commit trailer in item text (R-943, R-940).
- **Roles come from registration** (R-930, R-932). A session that was cleared and renewed resumes its role. A session with a parent never takes the coordinator role by itself, including on reorganize (R-939).
- **The protected-project barrier is outward only** and the rule texts agree with each other (R-944, R-942, R-948). The preview sections match the shipped rules (R-929).
- **`budgit attribution on|off`** sets whether commits with an attribution line are refused; the default is on (R-979, R-978). The check also reads a message piped in with `-F -`, `-F /dev/stdin`, a heredoc or a file (R-973, R-983, R-998, R-985).
- **Opt-in no-delete hook** (R-988). It is off by default. Turn it on with `budgit rm-guard on`. It refuses `rm` forms, including behind wrapper options, busybox and `git clean`, and names the Trash instead.
- **The verify-report guard judges write targets, not mentions** (R-980, R-990, R-993, R-995, R-996, R-997). It resolves bare and relative targets from any working directory, refuses glob, quoted and symlinked targets, asks on substituted targets, and no longer reacts to reads or to text in titles. Its ask text goes through the language files.
- **`ledger verify` ignores a report written for another commit** (R-991). A match needs at least 7 hex characters, and no match among several reports refuses.
- **The single-source warning is quieter and wider** (R-974, R-984, R-992, R-999). It skips your own rules, and it covers more source phrasings, in English and Spanish, from one shared connector list. It uses neutral possessives (my, your, our, their, its) and never assumes a gender.
- **One-line status notes no longer trip the client-text gate** (R-987).
- **Tests follow `--appetite`** and set up their own import path (R-976, R-985).

## 0.13.9.10

- **`budgit asks`** lists every ask you made with its state (read-only), and flags asks that got lost, such as a message that never reached an answer.
- **Away notes and helper commands are stricter.** Away notes are written by the coordinator only, and a helper agent's command is judged by what it writes, so a helper cannot change Budgit's own settings.
- **Outside review skips a tool that hit its usage limit** and falls back to the next one until the limit returns. Protected projects never go to an outside reviewer.
- **Done needs proof.** Marking an item done or verified needs a commit and a test name, and one verify folder serves ledger verify, the verifier and the guard.
- **A brief that quotes only your example is warned** so the general rule is stated first.
- Call caps in briefs also cover the verify-report write, and a warning shows above the turn limit.
- **Brote in the terminal shows its one-line text form** while a smaller pet is built; the floating Brote is unchanged.

## 0.13.9.9

- Slow tests now compare against a same-run ratio instead of a fixed second count, so they stop failing on a loaded computer.

## 0.13.9.8

- **The 5-hour gauge.** On Pro and Max the status line shows the 5-hour window next to the weekly one, and pacing levels (tight, critical) use both with hysteresis.
- **The Brote build cache is shared and keyed** by source, compiler version and flags, keeps the newest 4, and a warm build compiles nothing.
- **Test runs share one cache** and the pool halves on battery only under 30 % or in Low Power Mode.
- **One busy rule** judges the machine by kernel CPU counters and the memory that is truly free; the system load figure plays no part.
- **Scheduled-task runs no longer count as your messages.** A prompt that starts with `<scheduled-task` is treated as automated text, so it gets no inbox entry and does not switch off away mode.

## 0.13.9.7

- **The ext-review busy rule is shared**, and a helper that is busy no longer starts a second suite.
- **Pool home and test selection.** Shipped-file changes always select the leak, manifest and export tests.

## 0.13.9.6

- **The export leak gate stops flagging code and our own README text.** It still refuses the real phrases a person wrote, so a release is no longer blocked by wording that only describes shipped code.
- **`budgit ext-review`** asks an outside reviewer to check a change and now gives up after a timeout instead of waiting forever.

## 0.13.9.5

- **`budgit test --full` runs the whole suite in parallel.** It starts as many workers as your machine can spare, halves them on battery, waits outside the locks while the machine is busy, and cleans up its workers when it ends.
- **One busy rule everywhere.** The runner and the admission checks judge busy the same way: CPU above 85 % of all cores, or under 1 GB of free memory.
- **Plan and verify reviews check what varies** (0.13.9.4 notes below) now ship in this release together with the parallel runner.
- **Fewer tokens per session.** Each brief carries a call cap that is enforced, agents that carry every tool need a stated reason, and the text a session starts with shrinks from 22.9 KB to 4.0 KB on real-size data.
- **Leftover processes get closed.** A process a helper agent leaves behind is closed, its owner read from the environment only, and the daily sweep closes only fully-marked orphans.
- **`budgit export <folder> --check` on a missing or empty folder** now says to run `budgit export` first instead of crashing.
- The timing test for the status line now compares against a bare start on the same machine, so it no longer fails on a loaded computer.

## 0.13.9.4

- **Plan and verify reviews check what varies.** They list what changes per machine and user, walk 3 different users through the design, and flag local figures used as constants.
- **`budgit varies <file>`** checks a brief or plan file for a figure measured on one machine and used as a constant, and exits 1 when it finds one.

## 0.13.9.3

- **Read-only git commands pass the report-folder guard.** A plain git read (status, log, diff and similar) with known options is allowed; anything else is still refused.
- **`budgit --version`** prints the version of the installed plugin.
- **`budgit setup --pending`** lists the setup questions that are still unanswered, and prints nothing when none are left.
- **The structure answer honours `--project`.** With an explicit project folder the answer applies to that folder; without the flag it uses the current one.

## 0.13.9.2

- **Answering yes inside a project's chat now works.** Budgit makes it the lead for that project and opens a separate coordinator chat on its own; each chat it opens is top level, never nested in another.
- **No silent single chat.** If the app cannot open chats, Budgit asks first and says how to allow it; one chat for everything only if you pick it.

## 0.13.9.1

- **A yes to team structure now sets it up.** The chat where you answer becomes the lead; it opens one chat per project (or leads them all itself when it cannot open chats), and each project chat records itself as that project's lead. Nothing to type.

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
