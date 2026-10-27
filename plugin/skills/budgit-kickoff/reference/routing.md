# Routing: which skill or agent for which kind of task
Read this when you write a lane brief or start a task. For design, motion, writing, review and research work, `budgit skills why --job <job> --traits <key=value,...>` names the skill, its checker and its fallback, and logs why; use its answer first; `reference/skill-selection.md` explains the rules. The table below is the default when the command is not available. Every skill named here is optional; if it is not installed, do the plain step beside it and say so once.

| The task | Use | If it is not installed |
|---|---|---|
| A new feature or change of behaviour, not yet defined | `superpowers:brainstorming` | ask the user for the goal, the non-goals and one concrete example, in one round |
| A plan for several steps | `superpowers:writing-plans`, then `budgit:plan-critic` | tasks with files, a failing test first and acceptance checks; critic still runs |
| Building from a reviewed plan | `superpowers:test-driven-development`, or `superpowers:executing-plans` for the whole plan inline | write the failing test, watch it fail, then the code |
| A bug, a failing test, unexpected behaviour | `superpowers:systematic-debugging` first | reproduce, read the log, name one cause, change one thing |
| Two or more independent tasks | `superpowers:dispatching-parallel-agents`, or one session per task | one lane per task, one writer per set of files |
| Work that must not touch the user's checkout | `superpowers:using-git-worktrees` | a separate folder and branch |
| About to say "done" | `superpowers:verification-before-completion`, then `budgit:verifier` | run the check and quote its output |
| A diff to review | `/code-review`, `/security-review` | read the diff for swallowed errors, injection and secrets, quote the lines |
| Research on the web | `budgit:researcher`, one angle per call | search wide first, save each finding with URL and date |
| A release, a new dependency, a third-party plugin or MCP | `budgit:security-reviewer` | `budgit secrets` and `budgit audit-mcp`, then read the code |
| Long prose people read as Budgit's voice (README, wiki, release note, landing, post) | `humanize/humanize.md` first, then `stop-slop` | `lenses/writing.md`; `budgit humanize-lint <file>` works without any skill |
| Short UI strings (a button, one error line, an empty state) | `design:ux-copy` first, `humanize` only with `--kind ui` | `lenses/writing.md` |
| Anything visual the user will see | the design lens, `impeccable` when installed | `lenses/design.md` and `reference/design-rubric.md` |

## In every lane brief
- A `Skills:` line with the rows above that apply (or "none"), so the lane does not have to guess.
- The order: `budgit activate --since-now <project dir>` first, then `budgit ledger start R-n`.
- A hard stop in tool calls, the one deliverable, and "send questions to the coordinator, never ask the user".
- A read-only or security brief forbids any command that raises an operating-system password or permission prompt (keychain reads, `sudo`, system settings).
