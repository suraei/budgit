[budgit-job] Budgit keep-alive. You run headless: you have no session tools, so you cannot wake, message or close any session. Do ledger work only.

1. Run `budgit portfolio` to list the registered projects. For each project, run `budgit ledger list` in it and look for open items that are not blocked.
2. Find sessions by their title (use the session list your host gives you, if any); never rely on a typed session id. If a session for a project is live, leave that project alone.
3. For a project with an unblocked open item and no live session, do that one item through the project's normal Budgit flow, within the budget level `budgit budget` reports. Do at most one item per project per run.
4. Write nothing outside each project's own Budgit data folder. Stop when no project has an unblocked open item.

Waking project leads, closing quiet lanes and messaging leads are done only by the desktop scheduler, where a full session has the tools. They are not part of this run (UNTESTED).
