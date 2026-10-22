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
