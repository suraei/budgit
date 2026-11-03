# Portfolio: one Team Manager, one PM per project, lanes
Read this when you are a Team Manager or a Project Manager, or when you write a lane brief for a project that is not the hub.

## Levels
| Level | Which chat | Talks to | Decides | Never |
|---|---|---|---|---|
| **Team Manager** | `TM · Budgit`, opened in the hub folder | the user and the PMs | which projects get attention now, dependencies between projects, when a Budgit update reaches every project (after the user's click), sweeps and renewals across projects | does project work, keeps a copy of any project's tasks, talks to lanes directly |
| **Project Manager** | `PM · <project>`, opened in the project's own folder | the user (about its project), its lanes, the Team Manager | the order of its tasks, which lane starts next and on which task, how new input matches existing tasks, whether a lane's work is finished (then it archives the lane) | builds large work itself, touches another project, lets a lane run without a task |
| **Lane** | `<project> · <topic>`, started by its PM | its PM only | how to build its one task within the task's done-contract | asks the user directly, works without a claimed task, widens its scope on its own |

- A question goes lane → PM → Team Manager. Only a choice nobody below can make reaches the user, and the PM asks it in its own chat with a question card. The pick becomes a ledger record the same turn.
- A PM tells the Team Manager only cross-project facts: a Budgit feature it needs, an update done, a Budgit bug that blocks it, a renewal with the new chat id. Most of this travels through the project card that `budgit portfolio` reads, not through messages.
- A Team Manager's order to a PM (an update, a rule change, a sweep finding) becomes a ledger item in that project, so it survives the PM's renewal.

## Definition of Workflow
Every project, every count and every surface (status line, `budgit portfolio`, the desktop feed) uses these words:
- A **task** is a ledger item. Its states: in progress (`doing`), to do (`open`, not later), idea (`later`), done (`done` or `verified`).
- A **chat** is a Claude session. Its states: working, waiting (on the user), resting (idle). A chat is never "in progress"; only a task is.
- A **project** is a folder with a ledger.
- A lane is a chat with one claimed task. A PM is the chat that coordinates one project and has no task of its own.
- Every count names its project, or says it covers all projects. A status line starts with the project's name, not the product's.
- A project's work lives in its ledger. A side board or a notes file is imported into the ledger once by that project's PM, then retired or generated from the ledger.
- A project shows by its real name: the `name` in its `project.json`, then the one in the portfolio list, and the folder name only when neither exists. The PM asks the user for the name once and never guesses.
- A chat that works in a bound project without a claimed task, and is not that project's PM, is shown as "no task" and nudged once to claim one. It is never blocked.

## How work flows
- No weekly meeting and no fixed card. When a lane closes its task, its PM pulls the next task in the agreed order and starts the next lane.
- How many lanes run at once is computed, not fixed: the budget level sets the total across projects, the user's priority per project splits it, and stuck tasks reduce a project's share. A running lane is never stopped; only the next start waits. `budgit budget` gives the pace.
- New input is matched against open, in-progress and recently done tasks before it becomes a new task. A match is a change to that task, so every count shows it once.
- Only what stops work interrupts the user at once (a broken release, lost access, a risk to data). Everything else waits in one list across projects, with a link to the PM chat that asks, a default answer for each question, and a small daily limit. No answer means no change.

## Renewal
1. When a chat's context is running high, it writes its handoff (`budgit handoff write`): the tasks it claims, the user's decisions linked to them, commits, branch, uncommitted files, the test commands, then its own next steps and traps.
2. A fresh chat in the same folder reads the handoff and echoes the facts back.
3. `budgit handoff check <lane> --echo <file>` compares the echo with the facts. It passes only when nothing is missing.
4. `budgit ledger handover --from <old> --to <new>` moves the claims. The old chat is archived; a renewed PM tells the Team Manager its new chat id.

## Sweeps
`budgit sweep --all` lists, for every registered project:
- in-progress tasks with no commit or ledger change for a while;
- worktrees whose branch is merged and clean (removable), and old ones with unmerged or uncommitted work (ask the user, never delete);
- pointers of ended chats, stray data folders, uncommitted file counts;
- stray processes a Budgit chat started (dev servers, browsers, local web servers, test runs), with the chat that owns each;
- lanes that can be archived: the last report was checked, with no open question, pending permission or queued follow-up.

The PM archives those lanes at once and says so in one line. A sweep runs at every release, at every PM renewal, and whenever the Team Manager wakes.

## Daily read
`budgit daily [project] [--all] [--json]` is the morning read of a ledger. It lists and decides nothing, and it changes no ledger:
- the flow count of the last 24 hours and the rule line when more came in than went out;
- batches: items of the next version that name the same file or share two meaningful words (at most 8 in a batch);
- probable duplicates: open items whose titles are 80% alike, or that say "duplicate of / covered by R-n" for an open item;
- the order for the next version: Class A first, then items that look like Class A (a bug, slow, broken), then Class B, which waits for the user's yes;
- a preview of what housekeeping would clean, "not checked" when the machine is busy, git is slow or the folder is not a repo.

On the first startup session of a day in a repo with something to say, the SessionStart hook adds one line with three integers (batches, probable duplicates, items to clean). Offer to run `budgit daily` when you see it; with no answer nothing changes. A protected project shows only its masked id and card counts, "flow, batches and duplicates not computed"; its ledger is never opened, and a protected name written in another project's items is hidden.

Known limits of the hiding, on purpose: a protected name of fewer than 4 letters and digits is hidden only as a whole word (`ab` in `the ab project` and `ab.py`, not in `abcClient.py` or `ab2`), because hiding every word that holds it would wreck ordinary text; a name with no letter or digit (an emoji) and look-alike letters from another script are not caught; a protected folder that is in no `protected_dirs` list, no portfolio list and holds no `"private": true` cannot be known. When a config file cannot be read, `budgit daily` prints the code `config` and opens no ledger.

## Protected projects
A project marked protected shows only counts, versions and ids outside its own PM chat: no task titles, file names, branch names or chat titles, in any output. Its PM is the only chat that reads its tasks.
