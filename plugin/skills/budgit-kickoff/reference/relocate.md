# budgit relocate — all projects under one folder (R-332)

Moves every project to `<root>/<name>` (default `~/Dev`) one at a time, reversibly. The hub goes to `<root>/budgit`.
Nothing runs from a hook; every move is one command you start on purpose.

```
budgit relocate plan   [--root R] [--include NAME] [--exclude NAME]   # dry run, changes nothing
budgit relocate apply  --project NAME --yes                           # moves one project
budgit relocate finish --yes                                          # projects_root becomes R, after the last move
budgit relocate undo   --project NAME --yes                           # moves it back (NAME = _root for finish)
budgit relocate status                                                # every recorded move
```

## What the dry run lists
Per project: the old and new path, entries, linked worktrees, session pointers, hub cards, `~/.claude/projects` folders,
live sessions and small files that name the old path, plus a verdict. A project is skipped (never forced) when:
a process has a file open inside it (`lsof`; if `lsof` cannot answer, everything is skipped), a live session is bound to it,
it has uncommitted changes (or git cannot say, or its `.git` is a file), the target already exists or another project would
go to the same target, the move would cross a volume (that would be a copy), or it already sits under the root.
`lsof` only sees processes of the current user, so run the move with other users' sessions closed, and from a shell whose
folder is outside the project (the shell itself would count as having it open). Moving one project rewrites its hub card, which
makes the hub dirty: move the hub first, or commit it between moves. Unknown folders and loose files in the old projects folder stay where they are; `--include NAME`
names the ones that are projects.

## What a move does
1. Records a manifest in the Budgit home (`relocate/<stamp>-<name>.json`) before touching anything.
2. One atomic `os.rename` on the same volume. Never a copy.
3. `git worktree repair` for the linked worktrees (also those outside the project folder).
4. Rewrites the old path in Budgit's own files only: session pointers, hub cards, `config.json` (`hub`, `protected_dirs`).
5. On any failure after the rename (or Ctrl-C) it rolls back. A move that was cut off hard is left as `moved` and must be
   undone before that project is moved again.
6. `undo` replays the manifest and refuses, changing nothing, when the machine no longer matches (old path taken, files
   open, a pointer or a config value edited since). It reverses only what that move changed, in the opposite order.

## Protected projects
Moved as opaque folders: no file inside is read, no `git status` runs there, and only git's own `.git/worktrees/*/gitdir`
bookkeeping is touched. Output shows `#<hash>` labels and counts, never the name or path. The new path joins
`protected_dirs` before the rename and the old one leaves after it, so the folder is protected throughout. Commands that
mention a protected path go to a 0600 script under the Budgit home, never to the terminal.

## What stays by hand
Budgit never writes `~/.claude`. The `~/.claude/projects/<folder>` names encode the path, so each move prints a script
(0600, in the Budgit home) that renames them without overwriting anything; review it and run it once the chats are closed.
After the hub moves, if it is a plugin marketplace, run `claude plugin marketplace add <new hub folder>`: Claude re-points the marketplace of the same name, rewrites `settings.json` and `known_marketplaces.json` itself, and the installed plugins update from the new folder (tested with Claude Code 2.1.308 on a throwaway marketplace). Chats: `claude --resume <id>` finds a chat by id from any folder, but the chat list and `--continue` of the new folder only show the old history after the `~/.claude/projects` rename; if the new folder was opened in Claude before the rename, Claude already made a folder there and the rename script will not overwrite it (merge by hand).
Reopen Claude in the new folder afterwards.
