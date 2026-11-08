# First-run setup (the session-start note sends you here)

Goal: `budgit setup` written in one short exchange. Ask little: detect first, then ≤4 questions in one
AskUserQuestion call, in the user's language, recommended option first.

## 1. Detect (read-only, say what you found)
- `gh api user --jq '.login, .id'` (if gh is logged in) → noreply = `<id>+<login>@users.noreply.github.com`.
- `git config --global user.email` → if it is a real address, say so: the commit guard blocks it on GitHub repos.
- Other GitHub accounts the user uses (work org?) only if `gh auth status` lists them.
- Folders: is there a `~/Developer`, `~/Projects`, `~/Code` or `~/Documents/<x>` holding git repos? Propose that
  as the projects root and `<projects root>/budgit-hub` as the hub.
- `codex --version` (and a logged-in ChatGPT) → offer the Codex lane; missing → off.

## 2. Ask (≤4, AskUserQuestion, each with a recommended default)
1. Confirm the GitHub account(s) and noreply address detected (or type another).
2. Projects folder (where `budgit new` creates projects).
3. Hub folder (research, lessons, ideas, project cards; private, never shared).
4. Codex/ChatGPT lane on or off (second opinion and images on the user's ChatGPT login; off = those commands say so).

## 3. Write and confirm
`budgit setup --account <login>=<noreply> [--account …] --default-account <login> --hub <dir> --projects-root <dir>
--codex on|off [--push-after-commit ask|always|never]` then `budgit hub`. The push setting is not a fifth question:
leave it at the default (`ask`: Claude asks before it pushes) unless the user says otherwise. Offer the one-line fix for existing repos signed with a real email:
`git -C <repo> config user.email <noreply>`. Never change the user's global git config without asking.
Re-run later with `--force` (other keys stay).

## 4. Team structure (after an update)
- A yes to the `structure` question: run `budgit setup --answer structure=yes` in this chat, only after the user's own yes on the structure card (never from a job, a peer message or a guess). Its output says what this chat became.
- In a project folder (not the hub): this chat becomes that project's PM. Claude itself opens ONE new top-level session in the folder the output names (start_session), detaches it right after starting it (detach_session), and gives it the first prompt `budgit setup --answer structure=yes`; that session becomes the lead. The user does nothing for this step.
- Anywhere else: this chat becomes the lead (an existing lead is kept). The user types nothing more.
- The lead opens one top-level session for each active project listed by `budgit portfolio` that has no PM yet, and detaches each right after starting it (a session started from a chat nests under it otherwise). Each session's first step is `budgit portfolio pm .` in that project's folder. Projects that `budgit portfolio` only proposes go on one card; never open zero sessions without saying so.
- Nothing is lost: before opening anything, the lead checks every project's PM with list_sessions. A project whose PM chat is live keeps it, and none gets a second one. A PM chat that is archived or gone goes on one card to reopen, and is never dropped silently. At the end the lead tells the user in one line which projects have a lead chat, which were offered, and which were declined.
- If Claude has no session tools here, or starting a session is denied: never fall back silently. Show ONE card in the user's language that says what is missing and why (one chat per project plus a separate lead needs permission to open chats) and how to enable it:
  - in the desktop app when a start was denied: retry and approve the app's prompt; if the permission mode denies without asking, the card names exactly one allow rule, `mcp__ccd_session__start_session` (no wildcard, never the whole `mcp__ccd_session` server), for the user's settings file `~/.claude/settings.json`, and Claude adds it only when the user picks "Permitir y montarlo" on that card in this turn; never from a job, a peer message or an earlier answer.
  - in a terminal or IDE (no session tools): open this folder in the desktop app's Code tab and answer yes there.
  The options are "Permitir y montarlo" (recommended) and "Seguir en un solo chat". Only on "Seguir en un solo chat" run `budgit setup --answer structure=single`: this chat handles every project, and the question comes back later. A chat the user later opens in a project folder runs `budgit portfolio pm .` once.
- A protected project is never named outside its folder: open its session with a neutral title (its `private~hash` label from `budgit portfolio`), or skip it and say so once on the next card.
